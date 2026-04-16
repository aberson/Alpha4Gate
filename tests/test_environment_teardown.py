"""Phase 4.7 Step 3 (#84) — env teardown on normal game-end path.

Soak-2026-04-11b symptom: 6 of 12 eval games produced
``sc2.main Status.ended + Result for player 1: Defeat`` followed by
exactly 5 minutes of silence and then
``environment.py ERROR Timeout waiting for observation from game thread``.

Root cause: when SC2 transitioned to ``Status.ended`` outside the bot's
control, the bot's ``on_end`` hook ran but did NOT push a terminal
(``done=True``) tuple onto ``_obs_queue``; ``_sync_game`` finished its
DB/stats bookkeeping and returned; ``SC2Env.step()`` kept blocking on
``self._obs_queue.get(timeout=300)`` until the full 300-second timeout
fired.

Fix (in ``_run_game_thread``'s ``finally`` block): push an unconditional
``(zeros, {}, True, None)`` sentinel onto ``obs_queue`` so the consumer
``step()`` always sees a terminal tuple promptly. This path is DISTINCT
from ``_resign_and_mark_done`` (Phase 4.5 #72) which handles EARLY
termination where the bot voluntarily leaves the game.

These tests are unit-level only — no real SC2 launch, no burnysc2
monkey-patching. The ``_obs_queue`` is driven manually from the test
body.
"""

from __future__ import annotations

import queue
import time
from typing import Any
from unittest.mock import patch

import numpy as np
from bots.v0.learning.environment import FEATURE_DIM, SC2Env
from bots.v0.learning.rewards import RewardCalculator

# All consumer-side tests must complete in well under the real 300-second
# ``_obs_queue.get`` timeout. Five seconds is generous but still catches
# a regression that re-introduces the full stall.
_MAX_ELAPSED_SECONDS = 5.0


def _make_minimal_env() -> SC2Env:
    """Construct a bare ``SC2Env`` for consumer-side ``step()`` tests.

    Uses ``SC2Env.__new__`` to skip ``__init__`` (the same pattern used
    by ``TestResetFreshQueues`` and ``TestKillSwitchHygiene``) so no SC2
    process is launched. Wires up only the attributes ``step()`` reads:
    queues, reward calculator, no DB, a zeroed step/reward counter.
    """
    env = SC2Env.__new__(SC2Env)
    env._map_name = "Simple64"
    env._difficulty = 1
    env._reward_calc = RewardCalculator()
    env._db = None
    env._base_game_id = "rl_teardown"
    env._game_id = "rl_teardown_abc"
    env._model_version = "test"
    env._realtime = False
    env._obs_queue = queue.Queue()
    env._action_queue = queue.Queue()
    env._game_thread = None
    env._step_index = 0
    env._last_snapshot = None
    env._total_reward = 0.0
    env._game_start_time = 0.0
    env._game_store_failed_count = 0
    env._watchdog_soft_seconds = 1800.0
    env._watchdog_hard_seconds = 2700.0
    env._game_wall_start = time.monotonic()
    env._soft_cancel_sent = False
    return env


class TestObsQueueSentinel:
    """Consumer-side tests: ``step()`` exits promptly on the terminal sentinel.

    Drives the obs_queue directly so these tests are independent of any
    real game thread or burnysc2 internals.
    """

    def test_obs_queue_consumer_exits_on_sentinel(self) -> None:
        """A bare sentinel on an empty queue must short-circuit ``step()``.

        This is the exact symptom from soak-2026-04-11b: after
        ``Status.ended`` the real ``step()`` hung for the full
        300-second ``get(timeout=300)``. With the Step 3 fix the
        ``finally`` block pushes a ``(zeros, {}, True, None)`` tuple
        and ``step()`` returns ``done=True`` immediately.
        """
        env = _make_minimal_env()

        # The Step 3 sentinel shape — matches the ``finally`` push.
        env._obs_queue.put((np.zeros(FEATURE_DIM, dtype=np.float32), {}, True, None))

        start = time.monotonic()
        _obs, _reward, done, _truncated, _info = env.step(0)
        elapsed = time.monotonic() - start

        assert done is True, "sentinel must flip done=True"
        assert elapsed < _MAX_ELAPSED_SECONDS, (
            f"step() should return promptly on the sentinel, "
            f"not after a 300s stall (elapsed={elapsed:.3f}s)"
        )

    def test_obs_queue_consumer_exits_on_sentinel_after_real_obs(self) -> None:
        """Real mid-game observations then the sentinel: total time stays tiny.

        Pushes a short stream of non-terminal tuples followed by the
        terminal sentinel, then drives the consumer with repeated
        ``step()`` calls. The non-terminal steps must report
        ``done=False`` and the sentinel step must flip ``done=True``.
        """
        env = _make_minimal_env()

        non_terminal_count = 3
        for _ in range(non_terminal_count):
            env._obs_queue.put(
                (
                    np.zeros(FEATURE_DIM, dtype=np.float32),
                    {"strategic_state": "OPENING"},
                    False,
                    None,
                )
            )
        # Terminal sentinel — the Step 3 fix pushes this exact shape.
        env._obs_queue.put((np.zeros(FEATURE_DIM, dtype=np.float32), {}, True, None))

        start = time.monotonic()

        for _ in range(non_terminal_count):
            _obs, _reward, done, _truncated, _info = env.step(0)
            assert done is False, "non-terminal obs must not flip done"

        _obs, _reward, done, _truncated, _info = env.step(0)
        elapsed = time.monotonic() - start

        assert done is True, "sentinel must flip done=True"
        assert elapsed < _MAX_ELAPSED_SECONDS, (
            f"multi-step consumer should finish promptly, "
            f"not after a 300s stall (elapsed={elapsed:.3f}s)"
        )


class TestRunGameThreadTerminalSentinel:
    """Producer-side test: ``_run_game_thread``'s ``finally`` always pushes.

    Stubs ``_sync_game`` so no real SC2 is launched, then calls
    ``_run_game_thread`` directly and inspects the queue.
    """

    def test_run_game_thread_pushes_sentinel_in_finally(self) -> None:
        """Variant 1 (clean return) + Variant 2 (exception path).

        When ``_sync_game`` returns normally the ``finally`` block is
        the ONLY source of a terminal tuple, so the queue ends with
        exactly one item: the ``None``-result sentinel.

        When ``_sync_game`` raises, the exception branch pushes its
        ``"loss"`` tuple AND the ``finally`` still pushes the
        ``None``-result sentinel, so the queue ends with two terminal
        items in that order.
        """
        # Variant 1: clean return — exactly one sentinel on the queue.
        env_clean = _make_minimal_env()
        obs_q_clean: queue.Queue[Any] = queue.Queue()
        act_q_clean: queue.Queue[Any] = queue.Queue()

        with patch.object(env_clean, "_sync_game", return_value=None):
            env_clean._run_game_thread(obs_q_clean, act_q_clean)

        assert obs_q_clean.qsize() == 1, (
            "clean _sync_game return must leave exactly one terminal "
            "sentinel on the queue (pushed by the finally block)"
        )
        _obs, _info, done, result = obs_q_clean.get_nowait()
        assert done is True
        assert result is None, (
            "finally sentinel must carry result=None so "
            "compute_step_reward does not award a bogus terminal bonus"
        )

        # Variant 2: _sync_game raises — two terminal tuples.
        env_raise = _make_minimal_env()
        obs_q_raise: queue.Queue[Any] = queue.Queue()
        act_q_raise: queue.Queue[Any] = queue.Queue()

        def raising_sync_game(
            _obs_q: queue.Queue[Any],
            _act_q: queue.Queue[Any],
        ) -> None:
            raise RuntimeError("simulated sync_game failure")

        with patch.object(env_raise, "_sync_game", side_effect=raising_sync_game):
            env_raise._run_game_thread(obs_q_raise, act_q_raise)

        assert obs_q_raise.qsize() == 2, (
            "exception path: the except branch pushes a 'loss' tuple AND "
            "the finally pushes the None-result sentinel"
        )
        # Order: except first, finally second.
        _obs1, _info1, done1, result1 = obs_q_raise.get_nowait()
        assert done1 is True
        assert result1 == "loss"

        _obs2, _info2, done2, result2 = obs_q_raise.get_nowait()
        assert done2 is True
        assert result2 is None
