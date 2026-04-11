"""Tests for the Gymnasium SC2 environment wrapper.

These tests mock the SC2 game loop to test the environment logic without
needing a running SC2 instance.
"""

from __future__ import annotations

import asyncio
import queue
import sqlite3
import threading
import time
from dataclasses import asdict
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from alpha4gate.decision_engine import GameSnapshot, StrategicState
from alpha4gate.learning.environment import (
    _ACTION_TO_STATE,
    FEATURE_DIM,
    MAX_GAME_TIME_SECONDS,
    STEPS_PER_ACTION,
    SC2Env,
    _GymStateProxy,
    _make_training_bot,
)
from alpha4gate.learning.features import encode
from alpha4gate.learning.rewards import RewardCalculator


def _default_snapshot(**overrides: Any) -> GameSnapshot:
    base = GameSnapshot(
        supply_used=50,
        supply_cap=100,
        minerals=800,
        vespene=400,
        army_supply=30,
        worker_count=22,
        base_count=2,
        game_time_seconds=200.0,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


class TestObservationSpace:
    def test_obs_shape(self) -> None:
        """Encoded observations should match FEATURE_DIM."""
        snap = _default_snapshot()
        obs = encode(snap)
        assert obs.shape == (FEATURE_DIM,)
        assert obs.dtype == np.float32

    def test_obs_bounds(self) -> None:
        snap = _default_snapshot()
        obs = encode(snap)
        assert np.all(obs >= 0.0)
        assert np.all(obs <= 1.0)


class TestGymStateProxy:
    """Test the _GymStateProxy used to inject gym actions into Alpha4GateBot."""

    def test_proxy_returns_correct_state(self) -> None:
        proxy = _GymStateProxy(StrategicState.ATTACK)
        snap = _default_snapshot()
        assert proxy.predict(snap) == StrategicState.ATTACK

    def test_proxy_all_states(self) -> None:
        for _action_idx, expected_state in enumerate(_ACTION_TO_STATE):
            proxy = _GymStateProxy(expected_state)
            assert proxy.predict(_default_snapshot()) == expected_state

    def test_proxy_last_probabilities_default_empty(self) -> None:
        proxy = _GymStateProxy(StrategicState.ATTACK)
        assert proxy.last_probabilities == []

    def test_proxy_last_probabilities_settable(self) -> None:
        proxy = _GymStateProxy(StrategicState.ATTACK)
        proxy._probabilities = [0.1, 0.2, 0.3, 0.25, 0.15]
        assert proxy.last_probabilities == [0.1, 0.2, 0.3, 0.25, 0.15]


class TestRewardComputation:
    """Test that reward calculator integrates correctly with env logic."""

    def test_step_reward_positive(self) -> None:
        calc = RewardCalculator()
        state = asdict(_default_snapshot())
        reward = calc.compute_step_reward(state)
        assert reward > 0  # survival bonus

    def test_terminal_win_reward(self) -> None:
        calc = RewardCalculator()
        state = asdict(_default_snapshot())
        reward = calc.compute_step_reward(state, is_terminal=True, result="win")
        assert reward > 5.0

    def test_terminal_loss_reward(self) -> None:
        calc = RewardCalculator()
        state = asdict(_default_snapshot())
        reward = calc.compute_step_reward(state, is_terminal=True, result="loss")
        assert reward < -5.0


class TestSnapshotToRaw:
    """Test the snapshot-to-raw conversion for DB storage."""

    def test_raw_vector_length(self) -> None:
        env = SC2Env.__new__(SC2Env)
        snap = _default_snapshot()
        # Need to bind the method — use the class method directly
        raw = SC2Env._snapshot_to_raw(env, snap)
        assert raw.shape == (FEATURE_DIM,)

    def test_raw_values_match_snapshot(self) -> None:
        env = SC2Env.__new__(SC2Env)
        snap = _default_snapshot(supply_used=75, minerals=1200)
        raw = SC2Env._snapshot_to_raw(env, snap)
        assert raw[0] == 75.0  # supply_used
        assert raw[2] == 1200.0  # minerals

    def test_bool_converted_to_int(self) -> None:
        env = SC2Env.__new__(SC2Env)
        snap = _default_snapshot(enemy_army_near_base=True)
        raw = SC2Env._snapshot_to_raw(env, snap)
        assert raw[7] == 1.0  # enemy_army_near_base as int


def _run(coro: Any) -> Any:
    """Helper: run an async coroutine synchronously for a test step.

    Owns the event loop lifecycle explicitly (create -> run -> close)
    so Windows Proactor IOCP handles don't leak across tests. Prefer
    this over ``asyncio.run`` when the individual ``run_until_complete``
    semantics matter for step-by-step orchestration.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _install_mock_bot_runtime(
    bot: Any, *, game_time: float = 120.0
) -> MagicMock:
    """Wire just enough of ``Alpha4GateBot`` state so ``on_step`` can run.

    The training bot's decision path calls ``_build_snapshot`` (which
    reads heavy live-game state from ``BotAI``) and ``self.client`` (to
    surrender on timeout / shutdown). For unit-level repros we replace
    both with lightweight stubs so the control-flow paths that drove
    the #72 crash — the timeout branch and the shutdown-signal branch —
    can be exercised without a real SC2 process.
    """
    mock_client = MagicMock()
    # Use side_effect so each call to leave() produces a FRESH coroutine.
    # A shared return_value coroutine would raise
    # ``RuntimeError: cannot reuse already awaited coroutine`` if a
    # test awaited leave() twice (e.g. shutdown-after-timeout paths).
    mock_client.leave = MagicMock(side_effect=lambda: _async_none())
    bot.client = mock_client
    bot._build_snapshot = MagicMock(
        return_value=_default_snapshot(game_time_seconds=game_time)
    )
    return mock_client


async def _async_none() -> None:
    return None


class TestTrainingBotTimeoutTeardown:
    """Issue #72 repro: timeout path MUST mark the episode done and leave.

    Before the fix, ``_FullTrainingBot.on_step`` would push a terminal
    observation then ``return`` without surrendering the game. Because
    burnysc2 in non-realtime mode keeps ticking, the next decision step
    would re-enter the timeout branch and push another terminal
    observation — flooding the queue and orphaning the game thread.
    Eventually ``KillSwitch._to_kill`` would collect enough stale
    ``SC2Process`` references that a later ``kill_all()`` (from a
    sibling game thread's ``__aexit__``) would terminate the live
    game's SC2 binary mid-request and crash the whole cycle with
    ``WSMessageTypeError(257, None)``.
    """

    def test_timeout_marks_episode_done_and_calls_leave(self) -> None:
        obs_q: queue.Queue[Any] = queue.Queue()
        act_q: queue.Queue[Any] = queue.Queue()
        bot = _make_training_bot(obs_q, act_q, RewardCalculator())
        mock_client = _install_mock_bot_runtime(
            bot, game_time=MAX_GAME_TIME_SECONDS + 1.0
        )
        # A decision iteration (multiple of STEPS_PER_ACTION) triggers
        # the gym decision path; game_time is past the limit so we hit
        # the timeout branch.
        _run(bot.on_step(0))

        assert bot._episode_done is True
        assert mock_client.leave.called, (
            "timeout path must surrender via client.leave() so burnysc2's "
            "game loop actually exits — otherwise the thread orphans and "
            "KillSwitch state leaks across games (#72)."
        )
        # Exactly one terminal observation was produced (not a flood).
        assert obs_q.qsize() == 1
        _obs, _info, done, result = obs_q.get_nowait()
        assert done is True
        assert result == "timeout"

    def test_subsequent_on_step_after_timeout_is_noop(self) -> None:
        """Second decision-step after a timeout must not push again.

        This is the specific behaviour that produced the ``Game timeout
        at 1257s, 1261s, 1265s, ...`` rapid-fire log cadence observed in
        ``soak-2026-04-10c/backend.log``: every 22 game iterations the
        bot re-entered the timeout branch and pushed another terminal
        observation. The guard must prevent that.
        """
        obs_q: queue.Queue[Any] = queue.Queue()
        act_q: queue.Queue[Any] = queue.Queue()
        bot = _make_training_bot(obs_q, act_q, RewardCalculator())
        _install_mock_bot_runtime(bot, game_time=MAX_GAME_TIME_SECONDS + 5.0)

        _run(bot.on_step(0))
        assert obs_q.qsize() == 1

        # Simulate several more iterations as the SC2 loop keeps
        # ticking before it notices in_game is false. Queue must NOT
        # grow past the single terminal observation.
        for it in (STEPS_PER_ACTION, 2 * STEPS_PER_ACTION, 3 * STEPS_PER_ACTION):
            _run(bot.on_step(it))

        assert obs_q.qsize() == 1, (
            "on_step after episode teardown must be a no-op; "
            f"queue grew to {obs_q.qsize()}"
        )

    def test_shutdown_signal_triggers_leave(self) -> None:
        """Gym-side shutdown (action=None) must surrender the game.

        ``SC2Env.close()`` enqueues ``None`` on the action queue. The
        bot must consume it, surrender via ``client.leave()``, and
        stop producing observations so ``_play_game_ai`` can exit
        cleanly instead of orphaning the thread.
        """
        obs_q: queue.Queue[Any] = queue.Queue()
        act_q: queue.Queue[Any] = queue.Queue()
        bot = _make_training_bot(obs_q, act_q, RewardCalculator())
        mock_client = _install_mock_bot_runtime(bot, game_time=60.0)

        # Pre-load the shutdown sentinel so on_step sees action=None.
        act_q.put(None)
        _run(bot.on_step(0))

        assert bot._episode_done is True
        assert mock_client.leave.called
        # One observation (the pre-shutdown obs) was produced, then the
        # bot received the shutdown sentinel and stopped.
        assert obs_q.qsize() == 1
        _obs, _info, done, _result = obs_q.get_nowait()
        assert done is False  # obs was pre-shutdown, not a terminal

        # And further iterations are no-ops.
        _run(bot.on_step(STEPS_PER_ACTION))
        assert obs_q.qsize() == 0

    def test_action_queue_timeout_flips_episode_done(self) -> None:
        """``action_queue.get(timeout=120)`` raising ``queue.Empty`` must
        exit the same way a shutdown or a game timeout does: flip
        ``_episode_done``, call ``client.leave()``, and stop.

        Before the fix the ``queue.Empty`` path propagated out of
        ``on_step`` uncaught, so ``_episode_done`` was never set and
        the next decision tick would re-enter the decision branch.
        The contract is: every on_step exit that ends the episode
        surrenders via ``client.leave()``.
        """
        obs_q: queue.Queue[Any] = queue.Queue()
        act_q: queue.Queue[Any] = queue.Queue()
        bot = _make_training_bot(obs_q, act_q, RewardCalculator())
        mock_client = _install_mock_bot_runtime(bot, game_time=60.0)

        # Shrink the wait so the test does not block for 120s. The
        # bot reads from ``self._action_queue_train`` which is the
        # queue passed into ``_make_training_bot`` above, so wrapping
        # its ``get`` with a 0-second timeout produces ``queue.Empty``
        # immediately on the very first decision-step call.
        original_get = act_q.get
        act_q.get = lambda *_a, **_kw: original_get(timeout=0)  # type: ignore[assignment,method-assign]

        _run(bot.on_step(0))

        assert bot._episode_done is True, (
            "action_queue.Empty path must flip _episode_done so later "
            "on_step ticks are no-ops; otherwise the bot re-enters the "
            "decision branch indefinitely (issue #72 reviewer M2)."
        )
        assert mock_client.leave.called, (
            "action_queue.Empty path must surrender via client.leave() "
            "so burnysc2's game loop actually exits."
        )
        # The pre-shutdown observation was produced (and then the
        # queue.Empty on the action side forced teardown).
        assert obs_q.qsize() == 1
        _obs, _info, done, _result = obs_q.get_nowait()
        assert done is False  # pre-shutdown observation, not a terminal


class TestResetFreshQueues:
    """Issue #72 repro: zombie threads must not contaminate a new game.

    Before the fix the ``_obs_queue``/``_action_queue`` were created
    once in ``__init__`` and reused across every ``reset()``. If
    ``close()``'s 30-second join timed out (because the bot was stuck
    in the re-entering-timeout loop above), ``reset()`` would launch a
    new game thread sharing queues with the still-running zombie.
    Observations from the zombie would interleave with the new game's
    real observations and break the PPO rollout.
    """

    def test_reset_swaps_queues_so_zombie_writes_are_isolated(self) -> None:
        env = SC2Env.__new__(SC2Env)
        env._obs_queue = queue.Queue()
        env._action_queue = queue.Queue()
        env._game_thread = None
        env._step_index = 0
        env._last_snapshot = None
        env._total_reward = 0.0
        env._game_start_time = 0.0

        zombie_obs_q = env._obs_queue
        zombie_act_q = env._action_queue

        # Patch threading.Thread so reset() does not actually spawn a
        # real game; put a pre-fab observation on the NEW queue so the
        # 300s queue.get returns immediately.
        def fake_start(self: threading.Thread) -> None:
            # The obs queue the thread was told to write to is in args[0].
            new_obs_q = self._args[0]  # type: ignore[attr-defined]
            new_obs_q.put(
                (
                    np.zeros(FEATURE_DIM, dtype=np.float32),
                    {"strategic_state": "OPENING"},
                    False,
                    None,
                )
            )

        with patch.object(threading.Thread, "start", fake_start):
            _obs, _info = env.reset()

        # Queues were swapped: env now holds FRESH queue objects, not
        # the ones any lingering zombie thread captured at creation.
        assert env._obs_queue is not zombie_obs_q
        assert env._action_queue is not zombie_act_q

        # Simulate a zombie thread pushing a garbage terminal
        # observation into the OLD queue after reset() returned.
        zombie_obs_q.put(
            (np.zeros(FEATURE_DIM, dtype=np.float32), {}, True, "zombie")
        )

        # The new env's observation queue is unaffected by the zombie.
        assert env._obs_queue.qsize() == 0
        assert zombie_obs_q.qsize() == 1


class TestKillSwitchHygiene:
    """Issue #72 regression guard: ``_run_game_thread`` must clear the
    burnysc2 ``KillSwitch._to_kill`` class-level list when it finishes.

    burnysc2 never prunes that list on ``SC2Process.__aexit__``, so
    across many sequential games it grows monotonically with dead
    references. Eventually a ``kill_all()`` (triggered by one game's
    normal end) iterates the list and calls ``_clean()`` on a sibling
    game's SC2 binary — exactly the ``kill_switch: Process cleanup for
    N processes`` / ``WSMessageTypeError(257, None)`` pattern observed
    in soak-2.
    """

    @pytest.mark.parametrize("sync_game_raises", [False, True])
    def test_run_game_thread_drains_killswitch(
        self,
        sync_game_raises: bool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from sc2.sc2process import KillSwitch

        env = SC2Env.__new__(SC2Env)
        env._map_name = "Simple64"
        env._difficulty = 1
        env._reward_calc = RewardCalculator()
        env._db = None
        env._game_id = "test"
        env._model_version = "test"
        env._realtime = False
        env._total_reward = 0.0

        # monkeypatch auto-restores the original class attribute after
        # the test, so test isolation does not depend on us remembering
        # to rebind it by hand.
        monkeypatch.setattr(
            KillSwitch, "_to_kill", [object(), object(), object()]
        )

        obs_q: queue.Queue[Any] = queue.Queue()
        act_q: queue.Queue[Any] = queue.Queue()

        def fake_sync_game(
            _obs_q: queue.Queue[Any],
            _act_q: queue.Queue[Any],
        ) -> None:
            if sync_game_raises:
                raise RuntimeError("simulated WSMessageTypeError")
            return None  # pretend the game finished cleanly

        env._sync_game = fake_sync_game  # type: ignore[assignment,method-assign]
        env._run_game_thread(obs_q, act_q)

        if sync_game_raises:
            # The crash-path terminal observation reached the caller queue.
            assert obs_q.qsize() == 1
            _obs, _info, done, result = obs_q.get_nowait()
            assert done is True
            assert result == "loss"

        assert KillSwitch._to_kill == [], (
            "KillSwitch._to_kill must be drained after a game thread "
            "exits, or sibling game threads will cross-kill each other."
        )


class TestGameIdUniquenessAcrossResets:
    """Bug A (soak-2026-04-11 cycle 5): two sequential ``reset()`` calls
    on the same ``SC2Env`` instance MUST produce different ``_game_id``
    values. The old code reused the constructor-supplied ``game_id``
    for every game in the cycle, so the second game's ``store_game``
    always collided with the first on ``games.game_id``'s UNIQUE
    constraint, raising ``sqlite3.IntegrityError`` and crashing the
    cycle 14 seconds after an unrelated episode timeout.
    """

    def _bare_env(self, base: str = "rl_abc") -> SC2Env:
        """Build an SC2Env without running any real game — we only
        exercise ``reset()``'s id-allocation path, not the live
        SC2 subprocess.
        """
        env = SC2Env.__new__(SC2Env)
        env._map_name = "Simple64"
        env._difficulty = 1
        env._reward_calc = RewardCalculator()
        env._db = None
        env._base_game_id = base
        env._game_id = base
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
        return env

    def test_two_resets_produce_distinct_game_ids(self) -> None:
        env = self._bare_env(base="rl_cycle5")

        def fake_start(thread: threading.Thread) -> None:
            new_obs_q = thread._args[0]  # type: ignore[attr-defined]
            new_obs_q.put(
                (
                    np.zeros(FEATURE_DIM, dtype=np.float32),
                    {"strategic_state": "OPENING"},
                    False,
                    None,
                )
            )

        with patch.object(threading.Thread, "start", fake_start):
            env.reset()
            first_id = env._game_id
            first_base = env._base_game_id
            env.reset()
            second_id = env._game_id
            second_base = env._base_game_id

        assert first_id != second_id, (
            "reset() must allocate a FRESH per-game id; the old code "
            "reused the constructor game_id across every reset on the "
            "same env and hit `UNIQUE constraint failed: games.game_id` "
            "as soon as two games ran in one cycle."
        )
        # Both should share the base label so operators can correlate
        # the per-cycle reward log with the per-game DB rows.
        assert first_id.startswith("rl_cycle5_"), first_id
        assert second_id.startswith("rl_cycle5_"), second_id
        # Neither should be exactly equal to the base — a fresh suffix
        # must have been added.
        assert first_id != "rl_cycle5"
        # Regression signal: the base label must stay put across resets.
        # If someone accidentally reassigns ``_base_game_id = self._game_id``
        # in ``reset()``, the base would accumulate suffixes
        # (``rl_cycle5_AAA_BBB``) and the trainer's correlation between
        # the per-cycle reward log and per-game DB rows would break.
        assert first_base == "rl_cycle5"
        assert second_base == "rl_cycle5"

    def test_store_game_under_real_db_does_not_collide_across_resets(
        self,
        tmp_path: Any,
    ) -> None:
        """End-to-end repro with a REAL ``TrainingDB``.

        Before Bug A was fixed, calling ``reset()`` twice on a single
        ``SC2Env`` and then manually calling ``store_game`` with
        ``env._game_id`` would raise ``sqlite3.IntegrityError`` on the
        second call because both calls used the identical
        constructor-supplied id. With the fix, each ``reset()``
        regenerates the id so both stores succeed.
        """
        from alpha4gate.learning.database import TrainingDB

        db = TrainingDB(tmp_path / "train.db")
        env = self._bare_env(base="rl_integration")
        env._db = db

        def fake_start(thread: threading.Thread) -> None:
            new_obs_q = thread._args[0]  # type: ignore[attr-defined]
            new_obs_q.put(
                (
                    np.zeros(FEATURE_DIM, dtype=np.float32),
                    {"strategic_state": "OPENING"},
                    False,
                    None,
                )
            )

        with patch.object(threading.Thread, "start", fake_start):
            env.reset()
            first_id = env._game_id
            db.store_game(
                game_id=first_id,
                map_name="Simple64",
                difficulty=1,
                result="win",
                duration_secs=300.0,
                total_reward=10.0,
                model_version="v1",
            )

            env.reset()
            second_id = env._game_id
            # This is the line that used to raise IntegrityError.
            db.store_game(
                game_id=second_id,
                map_name="Simple64",
                difficulty=1,
                result="loss",
                duration_secs=250.0,
                total_reward=-5.0,
                model_version="v1",
            )

        assert db.get_game_count() == 2
        db.close()


class TestSyncGameStoreFailureObservable:
    """Bug B (soak-2026-04-11 cycle 5): when ``store_game`` inside
    ``_sync_game`` raises, the failure MUST be observable.

    We chose Option 2 (mark-and-continue): log at ERROR and bump
    ``_game_store_failed_count``. The trainer reads that counter
    after ``model.learn()`` returns and excludes failed games from
    the cycle win-rate denominator via ``compute_adjusted_win_rate``.
    Documented on the ``_sync_game`` try/except block.
    """

    def _env_with_mock_db(self, raising: Exception | None) -> SC2Env:
        env = SC2Env.__new__(SC2Env)
        env._map_name = "Simple64"
        env._difficulty = 1
        env._reward_calc = RewardCalculator()
        env._base_game_id = "rl_test"
        env._game_id = "rl_test_deadbeef0000"
        env._model_version = "test"
        env._realtime = False
        env._total_reward = 5.0
        env._game_store_failed_count = 0

        mock_db = MagicMock()
        if raising is not None:
            mock_db.store_game.side_effect = raising
        env._db = mock_db  # type: ignore[assignment]
        return env

    def _patch_run_game(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stub the ``sc2`` imports that ``_sync_game`` pulls in so the
        method runs without launching a real SC2 process.

        ``_sync_game`` imports ``sc2.maps``, ``sc2.player.Bot``,
        ``sc2.player.Computer``, and ``sc2.main.run_game`` lazily on
        every call. We replace each with a harmless stub so the method
        reaches the ``store_game`` call site — which is the line under
        test.
        """
        import sc2.main
        import sc2.maps
        import sc2.player

        # ``_make_training_bot`` pulls in ``alpha4gate.bot`` which itself
        # reaches deep into burnysc2 internals. Swap it for a lightweight
        # fake whose ``time`` attribute is all ``_sync_game`` needs.
        fake_bot = MagicMock()
        fake_bot.time = 123.4
        import alpha4gate.learning.environment as env_mod

        monkeypatch.setattr(
            env_mod, "_make_training_bot", lambda *a, **kw: fake_bot
        )

        # Stub the sc2.player.Bot constructor to accept any ai arg
        # (the real one asserts ``isinstance(ai, BotAI)``).
        monkeypatch.setattr(
            sc2.player, "Bot", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr(
            sc2.player, "Computer", lambda *a, **kw: MagicMock()
        )
        monkeypatch.setattr(
            sc2.maps, "get", lambda *a, **kw: MagicMock()
        )

        # ``run_game`` returns a result whose ``str(result)`` either
        # matches ``"Result.Victory"`` (→ "win") or something else
        # (→ "loss"). A plain string works because ``str(s) == s``.
        monkeypatch.setattr(
            sc2.main, "run_game", lambda *a, **kw: "Result.Defeat"
        )

    @pytest.mark.parametrize(
        "exc",
        [
            sqlite3.IntegrityError("UNIQUE constraint failed: games.game_id"),
            sqlite3.OperationalError("database is locked"),
        ],
        ids=["IntegrityError", "OperationalError"],
    )
    def test_store_game_sqlite_error_is_caught_and_counted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        exc: Exception,
    ) -> None:
        """The soak-2026-04-11 cycle 5 crash path, exactly reproduced.

        ``store_game`` raises ``sqlite3.IntegrityError`` (the actual
        cycle 5 crash) OR ``sqlite3.OperationalError`` (the parent we
        catch for runtime hazards like ``database is locked`` and
        ``disk I/O error``). In either case ``_sync_game`` must NOT
        propagate the exception; it must log at ERROR and bump
        ``_game_store_failed_count`` so the trainer can exclude this
        game from the win-rate denominator.

        Both branches are exercised in one method to cover the
        ``except IntegrityError`` and ``except OperationalError`` arms
        without 30 lines of duplicated scaffolding.
        """
        env = self._env_with_mock_db(exc)
        self._patch_run_game(monkeypatch)

        obs_q: queue.Queue[Any] = queue.Queue()
        act_q: queue.Queue[Any] = queue.Queue()

        # No exception should escape — mark-and-continue semantics.
        env._sync_game(obs_q, act_q)

        assert env._game_store_failed_count == 1, (
            "store_game error must bump the failure counter so the "
            "cycle win-rate denominator can exclude it"
        )

    def test_store_game_success_leaves_counter_at_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Happy path: ``store_game`` returns cleanly, counter stays 0."""
        env = self._env_with_mock_db(raising=None)
        self._patch_run_game(monkeypatch)

        obs_q: queue.Queue[Any] = queue.Queue()
        act_q: queue.Queue[Any] = queue.Queue()
        env._sync_game(obs_q, act_q)

        assert env._game_store_failed_count == 0
        assert env._db.store_game.called  # type: ignore[attr-defined]

    def test_run_game_thread_broad_catch_also_bumps_counter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If ``_sync_game`` raises something NOT caught by its inner
        try/except (e.g. a burnysc2 ``WSMessageTypeError``), the
        broad catch in ``_run_game_thread`` must still bump the
        failure counter so the cycle code sees a non-zero count.
        """
        from sc2.sc2process import KillSwitch

        env = SC2Env.__new__(SC2Env)
        env._map_name = "Simple64"
        env._difficulty = 1
        env._reward_calc = RewardCalculator()
        env._db = None
        env._base_game_id = "rl_test"
        env._game_id = "rl_test_abc"
        env._model_version = "test"
        env._realtime = False
        env._total_reward = 0.0
        env._game_store_failed_count = 0

        monkeypatch.setattr(KillSwitch, "_to_kill", [])

        def raising_sync_game(
            _obs_q: queue.Queue[Any],
            _act_q: queue.Queue[Any],
        ) -> None:
            raise RuntimeError("simulated WSMessageTypeError")

        env._sync_game = raising_sync_game  # type: ignore[assignment,method-assign]
        env._run_game_thread(queue.Queue(), queue.Queue())

        assert env._game_store_failed_count == 1, (
            "broad exception catch in _run_game_thread must also "
            "bump the failure counter"
        )


@pytest.mark.sc2
class TestTimeoutLeavesGameSc2Live:
    """SC2-live integration test: run a real game past the timeout and
    confirm ``_play_game_ai`` exits cleanly (rather than orphaning the
    thread). Runs only when StarCraft II is installed; skipped by the
    default ``pytest -m 'not sc2'`` run so CI is unaffected.
    """

    def test_training_env_single_episode_timeout_surrenders(self) -> None:
        from alpha4gate.learning.environment import SC2Env

        env = SC2Env(map_name="Simple64", difficulty=1, realtime=False)
        try:
            _obs, _info = env.reset()
            start = time.monotonic()
            done = False
            steps = 0
            # Bound the test at 2 minutes wall clock (should finish far
            # sooner as non-realtime MAX_GAME_TIME_SECONDS hits quickly).
            while not done and time.monotonic() - start < 120:
                _obs, _reward, done, _trunc, _info = env.step(0)
                steps += 1
            assert done, "env did not terminate — bot never surrendered"
            assert steps > 0
        finally:
            env.close()
