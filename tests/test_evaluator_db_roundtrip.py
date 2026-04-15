"""Phase 4.7 Step 1 (#82): evaluator <-> env <-> DB round-trip test.

Exercises the full ``ModelEvaluator._run_single_game`` path against a
real ``TrainingDB`` and a real ``SC2Env`` (with the SC2 launch mocked
at the thread boundary). The whole point of this test is to hit the
real ``TrainingDB.get_game_result`` lookup so any future regression
that breaks the evaluator/env ``game_id`` agreement (the soak-2026-04-11b
blocker) is caught pre-merge.

Explicitly does NOT patch ``evaluator._get_game_result`` -- if you're
reading this comment considering adding such a patch, STOP: the real
lookup IS the test.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from alpha4gate.config import Settings
from alpha4gate.learning.database import TrainingDB
from alpha4gate.learning.environment import SC2Env
from alpha4gate.learning.evaluator import ModelEvaluator
from alpha4gate.learning.features import FEATURE_DIM


def _make_settings(tmp_path: Path) -> Settings:
    """Create a Settings instance pointing at ``tmp_path``."""
    return Settings(
        sc2_path=Path("."),
        log_dir=tmp_path / "logs",
        replay_dir=tmp_path / "replays",
        data_dir=tmp_path / "data",
        web_ui_port=0,
        anthropic_api_key="",
        spawning_tool_api_key="",
    )


@pytest.fixture()
def db(tmp_path: Path) -> TrainingDB:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    return TrainingDB(data_dir / "training.db")


@pytest.fixture()
def evaluator(tmp_path: Path, db: TrainingDB) -> ModelEvaluator:
    settings = _make_settings(tmp_path)
    return ModelEvaluator(settings, db)


def _make_fake_thread_start(
    env_ref: dict[str, SC2Env],
    db: TrainingDB,
    *,
    result: str,
) -> Any:
    """Return a ``Thread.start`` replacement that fakes one SC2 game.

    The real ``SC2Env._run_game_thread`` runs in a background thread
    and (via ``_sync_game``) eventually calls ``self._db.store_game``
    with ``self._game_id`` (the suffixed post-reset id). Reproducing
    that flow faithfully is the whole point of this test: it's the
    exact seam that soak-2026-04-11b broke.

    The replacement:
      1. Pushes one non-terminal observation so ``env.reset()``
         returns (reset blocks on ``obs_queue.get``).
      2. Writes a real row to ``training.db`` using ``env._game_id``
         — the SAME id the env just assigned in ``reset()``.
      3. Pushes a terminal observation so the very next ``env.step()``
         sees ``done=True`` and the inference loop exits.
    """

    def fake_start(thread: threading.Thread) -> None:
        if not getattr(thread, "_args", ()):  # ignore hard-watchdog thread
            return
        env = env_ref["env"]
        obs_q = thread._args[0]  # type: ignore[attr-defined]

        # 1. First observation so reset() unblocks.
        obs_q.put(
            (
                np.zeros(FEATURE_DIM, dtype=np.float32),
                {"strategic_state": "OPENING"},
                False,
                None,
            )
        )

        # 2. Write the DB row using the env's post-reset game_id.
        # This is exactly what the real ``_sync_game`` does at the
        # end of a game, modulo real reward/duration numbers.
        db.store_game(
            game_id=env._game_id,
            map_name="Simple64",
            difficulty=1,
            result=result,
            duration_secs=150.0,
            total_reward=0.5,
            model_version="v-unit",
        )

        # 3. Terminal observation so the next step() returns done=True.
        obs_q.put(
            (
                np.zeros(FEATURE_DIM, dtype=np.float32),
                {"game_time": 150.0},
                True,
                result,
            )
        )

    return fake_start


class TestEvaluatorDbRoundTrip:
    """Phase 4.7 Step 1 (#82): the full evaluator <-> env <-> DB path.

    Would have caught the Phase 4.6 Step 1 regression that went
    undetected until soak-2026-04-11b flagged every eval game as
    "crashed".
    """

    def _run_one_game(
        self,
        evaluator: ModelEvaluator,
        db: TrainingDB,
        *,
        base_id: str,
        result: str,
    ) -> tuple[dict[str, Any], SC2Env]:
        """Run exactly one eval game and return (outcome dict, env).

        The env is constructed by the real ``_create_env`` so the
        evaluator and env are wired through the production seam. The
        background game thread is faked via ``Thread.start``.
        """
        model = MagicMock()
        # Deterministic: always action 0 — only one step runs anyway
        # because the fake thread pushes a terminal observation next.
        model.predict.return_value = (np.array(0), None)

        env_ref: dict[str, SC2Env] = {}

        real_create_env = evaluator._create_env

        def wrapped_create_env(*args: Any, **kwargs: Any) -> SC2Env:
            env = real_create_env(*args, **kwargs)
            env_ref["env"] = env
            return env

        fake_start = _make_fake_thread_start(env_ref, db, result=result)

        with (
            patch.object(evaluator, "_create_env", side_effect=wrapped_create_env),
            patch.object(threading.Thread, "start", fake_start),
        ):
            outcome = evaluator._run_single_game(
                model,
                game_id=base_id,
                checkpoint_name="v-unit",
                difficulty=1,
                all_action_probs=[],
            )

        return outcome, env_ref["env"]

    def test_roundtrip_win_flows_through_post_reset_id(
        self,
        evaluator: ModelEvaluator,
        db: TrainingDB,
    ) -> None:
        """A normal win flows through ``env.game_id`` to the real DB lookup.

        Assertions (the four the plan calls out):

        1. ``db.get_game_result(env.game_id)`` returns non-None after
           ``_run_single_game`` returns.
        2. The returned value equals the outcome the fake game produced.
        3. ``outcome`` from ``_run_single_game`` is ``"win"`` (not
           ``"crashed"`` — this is the whole regression we care about).
        4. The id used for the DB lookup is NOT the pre-reset base id.
           Anyone who un-fixes the evaluator by passing ``game_id``
           instead of ``env.game_id`` hits this assertion.
        """
        base_id = "eval_roundtrip_win"
        result, env = self._run_one_game(evaluator, db, base_id=base_id, result="win")

        # 1. The row is findable by the env's post-reset id.
        looked_up = db.get_game_result(env.game_id)
        assert looked_up is not None

        # 2. The stored outcome matches.
        assert looked_up == "win"

        # 3. _run_single_game reports "win", NOT "crashed".
        #    This is the exact regression from soak-2026-04-11b.
        assert result["outcome"] == "win"

        # 4. Regression guard: the id used for the lookup is NOT the
        #    base id — if someone un-fixes #82 by reverting to
        #    ``self._get_game_result(game_id)`` this assertion fails.
        assert env.game_id != base_id
        assert env.game_id.startswith(f"{base_id}_")
        # And the base id would NOT find the row on its own, proving
        # the lookup MUST go through the post-reset property.
        assert db.get_game_result(base_id) is None

    def test_roundtrip_loss_flows_through_post_reset_id(
        self,
        evaluator: ModelEvaluator,
        db: TrainingDB,
    ) -> None:
        """Same path, loss outcome — guards against a win-only shortcut."""
        base_id = "eval_roundtrip_loss"
        result, env = self._run_one_game(evaluator, db, base_id=base_id, result="loss")

        looked_up = db.get_game_result(env.game_id)
        assert looked_up == "loss"
        assert result["outcome"] == "loss"
        assert env.game_id != base_id
