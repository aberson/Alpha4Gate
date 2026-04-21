"""Gymnasium environment wrapper: bridges burnysc2 async game loop with sync gym.Env."""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import gymnasium
import numpy as np
from gymnasium import spaces
from numpy.typing import NDArray

from bots.cand_313752e4_b.decision_engine import (
    ACTION_TO_STATE as _ACTION_TO_STATE,
)
from bots.cand_313752e4_b.decision_engine import (
    NUM_ACTIONS,
    GameSnapshot,
    StrategicState,
)
from bots.cand_313752e4_b.learning.database import TrainingDB
from bots.cand_313752e4_b.learning.features import FEATURE_DIM, encode
from bots.cand_313752e4_b.learning.rewards import RewardCalculator

_log = logging.getLogger(__name__)

# How many game steps per env step (match bot.py observation frequency)
STEPS_PER_ACTION: int = 22

# Phase 4.8 warmup: force OPENING state for the first N seconds of game
# time before letting PPO choose freely. This prevents PPO's random initial
# policy from choosing ATTACK at step 0 (which sends probes to die and
# produces an instant loss with no learning signal). During warmup the
# rule-based bot builds economy and army; PPO observes but doesn't control.
# The warmup threshold should be relaxed as PPO matures — set to 0 to
# disable entirely once the policy has learned basic macro.
WARMUP_GAME_SECONDS: float = 0.0  # disabled — PPO learns faster without forced OPENING

# Max game time in seconds before forcing a timeout (prevents passive stalls)
MAX_GAME_TIME_SECONDS: float = 18000.0  # 5 hours — let games end naturally so all reward rules fire

# Per-game wall-clock watchdog (soak-2026-04-15 postmortem).
# The SC2-side MAX_GAME_TIME_SECONDS is 5h of game-time, which an idle
# passive game can burn in real wall time (observations keep flowing but
# neither player pushes). One stuck eval game in the 04-15 soak ate 5h
# of real wall time and blocked the whole orchestrator. Two gates:
#   - soft: push the shutdown sentinel on the action queue so
#     ``_sync_game``'s ``_resign_and_mark_done`` ends the game cleanly
#     and training/eval moves to the next game.
#   - hard: synthesize a terminal observation directly and abandon the
#     game thread when the soft path didn't take (game thread wedged
#     on burnysc2 I/O and not polling the action queue).
WATCHDOG_SOFT_SECONDS: float = 1800.0  # 30 min — resign-via-sentinel
WATCHDOG_HARD_SECONDS: float = 2700.0  # 45 min — synthesize terminal and move on

# Single source of truth for the bridge observation tuple shape:
# (obs_vector, info_dict, done_flag, result_string).
# All queue annotations in this module reference this alias; see
# feedback_duplicate_shape_constants.md on why we don't inline it.
type _ObsTuple = tuple[NDArray[np.float32], dict[str, Any], bool, str | None]


class SC2Env(gymnasium.Env[NDArray[np.float32], int]):
    """Gymnasium environment wrapping a burnysc2 SC2 game.

    The async-to-sync bridge works as follows:
    - reset() launches a new SC2 game in a background thread
    - The bot's on_step() is overridden to put observations into a queue and
      wait for actions from another queue
    - step() puts an action, waits for the next observation
    - close() kills the background thread and SC2 process

    This avoids nesting event loops and keeps the gym.Env interface clean.
    """

    metadata: dict[str, Any] = {"render_modes": []}
    observation_space: spaces.Box = spaces.Box(
        low=0.0, high=1.0, shape=(FEATURE_DIM,), dtype=np.float32
    )
    action_space: spaces.Discrete = spaces.Discrete(NUM_ACTIONS)  # type: ignore[type-arg]

    def __init__(
        self,
        map_name: str = "Simple64",
        difficulty: int = 1,
        reward_calculator: RewardCalculator | None = None,
        db: TrainingDB | None = None,
        game_id: str | None = None,
        model_version: str = "unknown",
        realtime: bool = False,
        replay_dir: Path | None = None,
        stats_path: Path | None = None,
        build_order_label: str = "4gate",
        advisor_bridge: Any | None = None,
        watchdog_soft_seconds: float = WATCHDOG_SOFT_SECONDS,
        watchdog_hard_seconds: float = WATCHDOG_HARD_SECONDS,
    ) -> None:
        super().__init__()
        self._map_name = map_name
        self._difficulty = difficulty
        self._reward_calc = reward_calculator or RewardCalculator()
        # Phase 4.8 Approach B: when provided, the TrainingAdvisorBridge
        # fires during training games and its recommendations are encoded
        # into the observation vector so PPO can learn from Claude's
        # strategic guidance.  When None, advisor features are all zeros.
        # The bridge runs in its own thread with its own event loop,
        # avoiding the CancelledError that the old ClaudeAdvisor caused
        # when shared across game threads.
        self._advisor_bridge = advisor_bridge
        self._db = db
        # Phase 4.6 Step 2: wire legacy dashboard producers into the
        # trainer. Both paths are optional so existing call sites and
        # unit tests that instantiate ``SC2Env`` without a replay dir
        # or stats path keep working unchanged.
        #
        # ``replay_dir`` is where burnysc2 should write the per-game
        # ``.SC2Replay`` file (see ``connection.build_replay_path``).
        # ``stats_path`` is the ``data/stats.json`` file that the
        # legacy Stats tab reads — the trainer appends one game at a
        # time via ``batch_runner.append_stats_game``.
        # ``build_order_label`` feeds into the stats.json ``GameRecord``
        # so aggregations by build order stay consistent with the
        # batch path.
        self._replay_dir = Path(replay_dir) if replay_dir is not None else None
        self._stats_path = Path(stats_path) if stats_path is not None else None
        self._build_order_label = build_order_label
        # Path to the replay file for the CURRENT game (rebuilt in
        # every ``reset()`` via ``build_replay_path``). ``None`` means
        # ``replay_dir`` was not set so no replay will be written.
        self._current_replay_path: str | None = None
        # ``_base_game_id`` is the human-readable label supplied by the
        # trainer (e.g. ``"rl_ab12cd34"``). The per-game ``_game_id`` that
        # actually lands in ``training.db`` is derived from it in every
        # ``reset()`` by appending a fresh UUID suffix so sequential games
        # within a single ``SC2Env`` instance can never collide on the
        # ``games.game_id`` UNIQUE constraint (soak-2026-04-11 cycle 5).
        self._base_game_id = game_id or "unnamed"
        self._game_id = self._base_game_id
        self._model_version = model_version
        self._realtime = realtime

        # Communication queues between gym thread and game thread
        self._obs_queue: queue.Queue[_ObsTuple] = queue.Queue()
        self._action_queue: queue.Queue[int | None] = queue.Queue()  # None = shutdown

        self._game_thread: threading.Thread | None = None
        self._step_index: int = 0
        self._last_snapshot: GameSnapshot | None = None
        self._total_reward: float = 0.0
        self._game_start_time: float = 0.0
        # Soak-2026-04-11: when ``_sync_game`` or ``_run_game_thread``
        # catches an exception from ``store_game`` (or any unexpected
        # error in the game thread) this counter is incremented. The
        # trainer reads it after ``model.learn()`` returns so the cycle
        # win-rate calculation can exclude games whose rows never made
        # it into the DB, and so the number of failed games is logged
        # loudly instead of silently dropping out of ``get_recent_win_rate``.
        # See Bug B in the soak-2026-04-11 cycle 5 postmortem.
        self._game_store_failed_count: int = 0

        # Per-game wall-clock watchdog state (soak-2026-04-15).
        # ``_game_wall_start`` is set in ``reset()`` right before the game
        # thread spawns and read by ``step()`` on every iteration.
        # ``_soft_cancel_sent`` ensures the shutdown sentinel is pushed
        # at most once per game — PPO may call ``step()`` many times
        # between soft-gate trigger and the game thread actually draining
        # the action queue.
        self._watchdog_soft_seconds: float = watchdog_soft_seconds
        self._watchdog_hard_seconds: float = watchdog_hard_seconds
        self._game_wall_start: float = 0.0
        self._soft_cancel_sent: bool = False

        # Thread-level hard watchdog (post-soak-2026-04-15 fix). The
        # step()-level watchdog only fires when step() is called; if
        # _sync_game itself wedges (burnysc2 websocket stall, hung SC2
        # frame, SQLite-blocked store_game), step() never returns and the
        # check is never evaluated. This timer thread runs independently
        # and force-closes SC2 via KillSwitch.kill_all() when the hard
        # gate elapses, unblocking _sync_game with a websocket error
        # that its exception path handles cleanly.
        self._hard_watchdog_cancel: threading.Event | None = None
        self._hard_watchdog_thread: threading.Thread | None = None

    @property
    def game_store_failed_count(self) -> int:
        """Number of games in this env whose row never made it into ``training.db``.

        Incremented whenever ``_sync_game`` catches an exception from
        ``store_game`` or ``_run_game_thread`` catches an unexpected
        exception from ``_sync_game``. The trainer reads this after
        ``model.learn()`` returns to adjust the cycle's win-rate
        denominator (see Bug B in soak-2026-04-11 cycle 5).
        """
        return self._game_store_failed_count

    @property
    def game_id(self) -> str:
        """The current post-reset game id this env will write to ``games.game_id``.

        Callers that constructed the env with a base id MUST read this
        property after ``reset()`` before querying
        ``TrainingDB.get_game_result``, because the env appends a
        per-reset uuid suffix.
        """
        return self._game_id

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[NDArray[np.float32], dict[str, Any]]:
        """Launch a new SC2 game and return the first observation."""
        super().reset(seed=seed, options=options)
        self.close()  # Kill any existing game

        # Bug A fix (soak-2026-04-11 cycle 5): allocate a fresh per-game
        # id so the new game's row cannot collide with a still-written
        # row from the prior game on this env. The old code reused
        # ``self._game_id`` across every reset on the same env instance,
        # so the second ``store_game`` would raise
        # ``sqlite3.IntegrityError: UNIQUE constraint failed: games.game_id``
        # as soon as two games ran in one cycle. UUID4 hex guarantees
        # uniqueness without relying on a global counter or wall clock.
        # Fallback to ``self._game_id`` or the literal ``"unnamed"`` when
        # tests instantiate via ``SC2Env.__new__`` without running
        # __init__, so ``reset()`` stays usable in unit-level repros.
        base = (
            getattr(self, "_base_game_id", None)
            or getattr(self, "_game_id", None)
            or "unnamed"
        )
        self._base_game_id = base
        self._game_id = f"{base}_{uuid.uuid4().hex[:12]}"

        # Phase 4.6 Step 2: rotate the reward log on every reset so each
        # trainer game produces its own ``game_<id>.jsonl`` file. Before
        # this fix, ``TrainingOrchestrator._make_env`` called
        # ``reward_calc.open_game_log`` exactly once per cycle and every
        # game in the cycle appended to the same file — the reward
        # aggregator counts one file per game, so a cycle of N games
        # looked like a single game on the Reward Trends chart. This is
        # the root cause of the "Scanned 8 games" vs ~50 trainer-games
        # discrepancy seen in soak-2026-04-11.
        #
        # Use ``getattr`` so unit tests that instantiate ``SC2Env`` via
        # ``__new__`` without running ``__init__`` (there are several in
        # ``tests/test_environment.py``) can still call ``reset()``
        # without having to wire up a real ``RewardCalculator``.
        _rc = getattr(self, "_reward_calc", None)
        if _rc is not None:
            _rc.open_game_log(self._game_id)

        # Phase 4.6 Step 2: allocate a unique replay path for this game
        # so the Replays tab has one entry per trainer game. The path
        # is threaded into ``run_game`` via ``save_replay_as`` in
        # ``_sync_game``. ``build_replay_path`` embeds a timestamp so
        # concurrent/sequential games on the same map do not collide
        # (see Step 5: ``connection.build_replay_path``).
        _replay_dir = getattr(self, "_replay_dir", None)
        if _replay_dir is not None:
            from bots.cand_313752e4_b.connection import build_replay_path

            _replay_dir.mkdir(parents=True, exist_ok=True)
            self._current_replay_path = str(
                build_replay_path(_replay_dir, self._map_name)
            )
        else:
            self._current_replay_path = None

        self._step_index = 0
        self._total_reward = 0.0
        self._last_snapshot = None
        self._game_wall_start = time.monotonic()
        self._soft_cancel_sent = False

        # Create FRESH queues for the new game. Issue #72: if the previous
        # game_thread was orphaned (for example because close() timed out
        # joining it), the zombie still holds a reference to the queues it
        # was created with. Creating new queue objects here guarantees that
        # the zombie's residual put()s go to a dead queue and cannot
        # contaminate the new game's observation stream.
        self._obs_queue = queue.Queue[_ObsTuple]()
        self._action_queue = queue.Queue[int | None]()

        # Cancel any prior hard-watchdog before starting a new game.
        # If the prior game thread was abandoned but its SC2 still lives,
        # the old watchdog is what will eventually reclaim it, so only
        # cancel once we know a fresh watchdog is about to take over.
        self._cancel_hard_watchdog()

        # Launch game in background thread
        self._game_thread = threading.Thread(
            target=self._run_game_thread,
            args=(self._obs_queue, self._action_queue),
            daemon=True,
        )
        self._game_thread.start()

        # Start thread-level hard watchdog (force-closes SC2 if the game
        # thread is still alive after hard_seconds, independent of step()).
        self._start_hard_watchdog(self._game_thread)

        # Wait for first observation
        obs, info, done, _ = self._obs_queue.get(timeout=300)
        return obs, info

    def step(
        self, action: int
    ) -> tuple[NDArray[np.float32], float, bool, bool, dict[str, Any]]:
        """Apply action, advance 22 game steps, return new observation."""
        # Per-game wall-clock watchdog (soak-2026-04-15). Checked BEFORE
        # pushing an action so a wedged game can't burn another step's
        # latency before being ended. Two gates:
        #   - Hard gate: abandon the game thread outright and return a
        #     synthetic terminal so PPO/eval moves to the next game. The
        #     thread becomes a zombie; #72's queue-swap in ``reset()``
        #     keeps its residual writes isolated.
        #   - Soft gate: push the None sentinel to ``_action_queue`` so
        #     ``_sync_game``'s ``_resign_and_mark_done`` fires and the
        #     game ends cleanly. Fire at most once per game.
        elapsed = time.monotonic() - self._game_wall_start
        if elapsed > self._watchdog_hard_seconds:
            _log.error(
                "Watchdog HARD timeout: game %s ran %.0fs real-time "
                "(>%.0fs); abandoning game thread and forcing terminal.",
                self._game_id, elapsed, self._watchdog_hard_seconds,
            )
            self._game_store_failed_count += 1
            obs = np.zeros(FEATURE_DIM, dtype=np.float32)
            return obs, -10.0, True, False, {"error": "watchdog_hard_timeout"}
        if elapsed > self._watchdog_soft_seconds and not self._soft_cancel_sent:
            _log.error(
                "Watchdog SOFT timeout: game %s ran %.0fs real-time "
                "(>%.0fs); sending shutdown sentinel to game thread.",
                self._game_id, elapsed, self._watchdog_soft_seconds,
            )
            self._action_queue.put(None)
            self._soft_cancel_sent = True

        # Send action to game thread
        self._action_queue.put(action)

        # Wait for next observation
        try:
            obs, info, done, result = self._obs_queue.get(timeout=300)
        except queue.Empty:
            # Game thread died or timed out
            _log.error("Timeout waiting for observation from game thread")
            obs = np.zeros(FEATURE_DIM, dtype=np.float32)
            return obs, -10.0, True, False, {"error": "timeout"}

        # Compute reward (include current_state for scouting reward rules)
        state_dict = info.get("snapshot_dict", {})
        state_dict["current_state"] = info.get("strategic_state", "")
        reward = self._reward_calc.compute_step_reward(
            state_dict, is_terminal=done, result=result
        )
        self._total_reward += reward

        # Store transition in DB
        if self._db is not None and self._last_snapshot is not None:
            prev_raw = self._snapshot_to_raw(self._last_snapshot)
            curr_raw = self._snapshot_to_raw(
                info.get("snapshot", GameSnapshot())
            )
            self._db.store_transition(
                game_id=self._game_id,
                step_index=self._step_index,
                game_time=info.get("game_time", 0.0),
                state=prev_raw,
                action=action,
                reward=reward,
                next_state=curr_raw if not done else None,
                done=done,
                action_probs=info.get("action_probs"),
            )

        self._last_snapshot = info.get("snapshot")
        self._step_index += 1

        return obs, reward, done, False, info

    def close(self) -> None:
        """Shut down the game thread and kill SC2."""
        self._cancel_hard_watchdog()
        if self._game_thread is not None and self._game_thread.is_alive():
            # Signal shutdown
            self._action_queue.put(None)
            self._game_thread.join(timeout=30)
            self._game_thread = None

        # Drain queues
        for q in (self._obs_queue, self._action_queue):
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

    def _start_hard_watchdog(self, watched_thread: threading.Thread) -> None:
        """Arm a timer thread that force-closes SC2 if the game wedges.

        The step()-level watchdog only fires when step() is called; this
        guards against _sync_game wedging entirely. On expiry, terminates
        the specific SC2 process(es) spawned by this env's game thread,
        unblocking the burnysc2 websocket so _sync_game's exception path
        can clean up.

        Two deliberate differences from calling burnysc2's
        ``KillSwitch.kill_all()`` directly:
          1. ``KillSwitch._to_kill`` is process-global — naively calling
             ``kill_all()`` would terminate SC2 binaries owned by other
             concurrent envs/evaluators in the same Python process. We
             snapshot ``_to_kill`` at arm-time and only touch entries
             added AFTER that snapshot.
          2. burnysc2's ``SC2Process._clean`` tries ``terminate()`` x3
             with no fallback to ``kill()`` if terminate is ignored.
             On a hung SC2 this can silently fail. We escalate
             terminate → wait → kill → wait ourselves.
        """
        # __new__-constructed test envs skip __init__ and don't set the
        # watchdog config; treat that as "no watchdog" so tests that
        # exercise reset() without wiring the watchdog attrs still pass.
        hard_seconds = getattr(self, "_watchdog_hard_seconds", None)
        if hard_seconds is None:
            return
        cancel = threading.Event()
        self._hard_watchdog_cancel = cancel

        # Snapshot the process-global KillSwitch._to_kill by identity so
        # we can tell which SC2Process instances are owned by THIS game.
        try:
            from sc2.sc2process import KillSwitch

            preexisting_ids = {id(p) for p in KillSwitch._to_kill}
        except Exception:  # pragma: no cover - defensive only
            preexisting_ids = set()

        def _worker() -> None:
            # wait() returns True if cancelled, False on timeout.
            if cancel.wait(timeout=hard_seconds):
                return
            # Race guard: the cancel event may have been set between the
            # wait returning False and this check. Without this we'd
            # still kill SC2 on a game that just finished cleanly.
            if cancel.is_set():
                return
            if not watched_thread.is_alive():
                return
            _log.error(
                "Thread watchdog: game %s still alive after %.0fs; "
                "force-terminating SC2 process(es) owned by this game.",
                self._game_id, hard_seconds,
            )
            self._force_terminate_sc2(preexisting_ids)

        self._hard_watchdog_thread = threading.Thread(
            target=_worker, daemon=True, name="a4g-hard-watchdog",
        )
        self._hard_watchdog_thread.start()

    def _force_terminate_sc2(self, preexisting_ids: set[int]) -> None:
        """Terminate SC2 Popen handles added during this game only.

        Escalates terminate → wait(3s) → kill → wait(3s) so a hung
        SC2 binary that ignores terminate still dies. ``preexisting_ids``
        scopes the kill to SC2Process instances registered AFTER the
        watchdog was armed, leaving sibling envs' SC2 processes intact.
        """
        try:
            from sc2.sc2process import KillSwitch
        except Exception:  # pragma: no cover - defensive only
            _log.exception(
                "Watchdog: cannot import KillSwitch; SC2 not terminated"
            )
            return

        targets = [
            p for p in KillSwitch._to_kill if id(p) not in preexisting_ids
        ]
        if not targets:
            _log.warning(
                "Watchdog fired but found no SC2 processes owned by this "
                "game; SC2 may already be gone or was never registered."
            )
            return

        for sc2_proc in targets:
            popen = getattr(sc2_proc, "_process", None)
            if popen is None:
                continue
            if popen.poll() is not None:
                continue  # already exited
            pid = getattr(popen, "pid", "?")
            try:
                popen.terminate()
                try:
                    popen.wait(timeout=3)
                except Exception:  # pragma: no cover - timeout path
                    pass
                if popen.poll() is None:
                    popen.kill()
                    try:
                        popen.wait(timeout=3)
                    except Exception:  # pragma: no cover - timeout path
                        pass
                _log.info(
                    "Watchdog terminated SC2 pid=%s (exit=%s)",
                    pid, popen.poll(),
                )
            except Exception:  # pragma: no cover - defensive only
                _log.exception(
                    "Watchdog failed to terminate SC2 pid=%s", pid,
                )

    def _cancel_hard_watchdog(self) -> None:
        """Signal the hard watchdog to stop without waiting for it.

        Tolerates missing attributes because several tests construct
        SC2Env via ``__new__`` without invoking ``__init__`` and this is
        called unconditionally from ``close()`` and ``reset()``.
        """
        cancel = getattr(self, "_hard_watchdog_cancel", None)
        if cancel is not None:
            cancel.set()
        self._hard_watchdog_cancel = None
        self._hard_watchdog_thread = None

    def _run_game_thread(
        self,
        obs_queue: queue.Queue[_ObsTuple],
        action_queue: queue.Queue[int | None],
    ) -> None:
        """Run the SC2 game in a background thread.

        sc2.main.run_game() is synchronous and calls asyncio.run() internally,
        so we must NOT wrap it in another asyncio.run().

        burnysc2's SC2Process sets signal handlers which only work in the main
        thread, so we monkey-patch signal.signal to a no-op here.

        The queues are passed in explicitly (rather than read from ``self``)
        so that this thread — and the bot it spawns — keeps writing to the
        same queue pair for its entire lifetime. If ``SC2Env.reset()``
        swaps in fresh queues mid-run (because an earlier game thread was
        orphaned), this zombie thread still writes to its own dead queue
        and cannot contaminate the new game's observation stream (#72).
        """
        import signal

        _orig_signal = signal.signal
        signal.signal = lambda *_args, **_kw: signal.SIG_DFL  # noqa: E731
        try:
            self._sync_game(obs_queue, action_queue)
        except Exception:
            # Bug B (Option 2 — mark-and-continue) from soak-2026-04-11:
            # the gauntlet requires an *observable* failure when the game
            # thread crashes. Logging at ERROR (via ``log.exception``)
            # already satisfies the #73 watchdog, but we ALSO bump
            # ``_game_store_failed_count`` so the trainer's cycle code
            # can subtract this game from the win-rate denominator
            # after ``model.learn()`` returns. Without this counter,
            # the failed game silently drops out of
            # ``db.get_recent_win_rate`` (which pulls from the tail of
            # the ``games`` table) and an older game is used instead,
            # drifting the displayed win-rate away from the cycle that
            # actually ran.
            prior = getattr(self, "_game_store_failed_count", 0)
            self._game_store_failed_count = prior + 1
            _log.exception(
                "Game thread crashed — marking failed, "
                "running total failed=%d",
                self._game_store_failed_count,
            )
            # Send terminal observation so step() doesn't hang
            obs = np.zeros(FEATURE_DIM, dtype=np.float32)
            obs_queue.put((obs, {}, True, "loss"))
        finally:
            signal.signal = _orig_signal
            # Game thread finished (normally or via exception) — cancel the
            # thread-level hard watchdog so it doesn't fire KillSwitch.kill_all()
            # on the next game's SC2 process.
            self._cancel_hard_watchdog()
            # Phase 4.7 Step 3 (#84): unconditional terminal sentinel on
            # ``_obs_queue`` so ``SC2Env.step()`` never stalls for the
            # full 300-second timeout after a normal game end.
            #
            # Soak-2026-04-11b symptom: 6 of 12 eval games produced
            # ``sc2.main Status.ended + Result for player 1: Defeat``
            # followed by exactly 5 minutes of silence and then
            # ``environment.py ERROR Timeout waiting for observation
            # from game thread``. Root cause: when SC2 transitions to
            # ``Status.ended`` outside the bot's control, the bot's
            # ``on_end`` hook runs but does NOT push a terminal
            # (done=True) tuple onto ``_obs_queue``; ``_sync_game``
            # finishes its DB/stats bookkeeping and returns; the
            # consumer ``step()`` keeps blocking on ``get(timeout=300)``
            # until the timeout fires.
            #
            # This is a DIFFERENT path from ``_resign_and_mark_done``
            # (Phase 4.5 #72): that helper covers EARLY termination
            # where the bot voluntarily leaves the game (timeout,
            # queue.Empty, shutdown-sentinel). Step 3 covers the
            # NORMAL end-of-game where sc2 ends the game on its own
            # and the bot's ``on_end`` is not one of the
            # ``_resign_and_mark_done`` call sites.
            #
            # Sentinel shape matches the exception-path tuple below
            # (``done=True``) but uses ``result=None`` rather than
            # ``"loss"`` because the real result — if any — has
            # already been written to the DB by ``_sync_game``'s
            # ``store_game`` call and the bot's terminal reward push
            # (if it happened) has already settled the reward total.
            # ``result=None`` means "terminal, no outcome attached"
            # and ``RewardCalculator.compute_step_reward`` handles
            # that path gracefully (no terminal bonus is added).
            #
            # This push is unconditional: on the exception branch
            # above we already put a ``("loss", done=True)`` tuple
            # on the queue, so the consumer will see TWO terminal
            # tuples in that case. The consumer processes the first
            # and exits its loop via ``done=True``; the second sits
            # unused and is drained by ``SC2Env.close()``.
            try:
                obs_queue.put((np.zeros(FEATURE_DIM, dtype=np.float32), {}, True, None))
            except Exception:  # pragma: no cover - defensive only
                _log.debug(
                    "Could not push terminal sentinel to obs_queue",
                    exc_info=True,
                )

            # Defensive cleanup: even after _sync_game returns normally,
            # burnysc2's ``KillSwitch._to_kill`` class-level list still
            # holds references to every SC2Process it has ever seen
            # (it is never pruned on ``__aexit__``). If another game
            # thread starts later in this process, that thread's end-of-
            # game ``KillSwitch.kill_all()`` will iterate the stale
            # entries and happily terminate any still-live SC2 binary it
            # finds — closing its websocket mid-request and crashing the
            # live game with ``WSMessageTypeError(257, None)``. Clearing
            # the list when this thread's game is done means the next
            # thread starts from a clean slate. See issue #72.
            try:
                from sc2.sc2process import KillSwitch

                KillSwitch._to_kill.clear()
            except Exception:  # pragma: no cover - defensive only
                _log.debug("Could not clear KillSwitch._to_kill", exc_info=True)

    def _sync_game(
        self,
        obs_queue: queue.Queue[_ObsTuple],
        action_queue: queue.Queue[int | None],
    ) -> None:
        """Run the actual SC2 game with a custom bot that bridges to the queues."""
        import sc2.maps
        from sc2.data import Difficulty, Race
        from sc2.main import run_game
        from sc2.player import Bot, Computer

        bot = _make_training_bot(
            obs_queue=obs_queue,
            action_queue=action_queue,
            reward_calc=self._reward_calc,
            advisor_bridge=getattr(self, "_advisor_bridge", None),
        )

        difficulty_map: dict[int, Difficulty] = {
            1: Difficulty.Easy,
            2: Difficulty.Medium,
            3: Difficulty.MediumHard,
            4: Difficulty.Hard,
            5: Difficulty.Harder,
            6: Difficulty.VeryHard,
            7: Difficulty.CheatVision,
            8: Difficulty.CheatMoney,
            9: Difficulty.CheatInsane,
            10: Difficulty.CheatInsane,
        }
        diff = difficulty_map.get(self._difficulty, Difficulty.Easy)

        # Phase 4.6 Step 2: thread the replay path into burnysc2 so the
        # Replays tab sees trainer games. Before this fix, the trainer
        # path called ``run_game`` with no ``save_replay_as`` kwarg
        # (unlike ``connection.run_bot`` which defaults to ``save_replay=
        # True``) so no replay file was ever written. The path is
        # allocated in ``reset()`` via ``connection.build_replay_path``
        # and left as ``None`` when ``replay_dir`` is not configured so
        # existing unit-level uses of SC2Env stay unaffected. ``getattr``
        # guards against unit-level repros that instantiate SC2Env via
        # ``__new__`` and call ``_sync_game`` directly (without reset()).
        _replay_path = getattr(self, "_current_replay_path", None)
        result = run_game(
            sc2.maps.get(self._map_name),
            [Bot(Race.Protoss, bot), Computer(Race.Random, diff)],
            realtime=self._realtime,
            save_replay_as=_replay_path,
        )

        # Store game result in DB
        result_str = "win" if str(result) == "Result.Victory" else "loss"
        game_time = bot.time if hasattr(bot, "time") else 0.0

        # Phase 4.6 Step 2: flush and close the per-game reward log so
        # ``reward_aggregator.aggregate_reward_trends`` sees a complete
        # ``game_<id>.jsonl`` on disk before the next reset rotates it.
        # ``close_game_log`` is idempotent: calling it on an already-
        # closed file is a no-op, so the pairing with ``open_game_log``
        # in ``reset()`` stays one-to-one even if the game errored before
        # reaching this line — the ``_run_game_thread`` finally block
        # would still let the next ``reset()`` open a fresh file.
        _rc = getattr(self, "_reward_calc", None)
        if _rc is not None:
            try:
                _rc.close_game_log()
            except Exception:  # pragma: no cover - defensive
                _log.debug(
                    "close_game_log raised during teardown", exc_info=True
                )

        # Phase 4.6 Step 2: guard flag so the stats.json append only
        # happens on the DB-write-success path. Without this, any
        # exception in ``store_game`` would still be followed by
        # ``append_stats_game`` and the two surfaces would drift
        # (training.db excludes the failed game but stats.json
        # includes it). Using an explicit flag rather than checking
        # ``_game_store_failed_count`` delta makes the intent
        # obvious to future readers of ``_sync_game``.
        _db_write_ok = False

        if self._db is not None:
            # Bug B (Option 2 — mark-and-continue) from soak-2026-04-11:
            # wrap the ``store_game`` call so an ``IntegrityError`` (or
            # any other DB failure) does NOT propagate out of the game
            # thread as a generic "Game thread crashed" — instead we log
            # the precise reason at ERROR level (the #73 watchdog will
            # see it) and bump ``_game_store_failed_count`` so the
            # trainer can exclude this game from the cycle win-rate
            # denominator. We chose mark-and-continue over propagate
            # (Option 1) because ERROR-level logs are already the
            # primary signal for the soak watchdog and an accurate
            # adjusted win-rate is more useful to the cycle bookkeeping
            # than a re-raised exception that forces the whole cycle
            # to abort. If future work needs harder failure semantics,
            # swap the ``continue``-equivalent below for a ``raise``.
            try:
                self._db.store_game(
                    game_id=self._game_id,
                    map_name=self._map_name,
                    difficulty=self._difficulty,
                    result=result_str,
                    duration_secs=game_time,
                    total_reward=self._total_reward,
                    model_version=self._model_version,
                )
                _db_write_ok = True
            # We deliberately do NOT catch ``sqlite3.DatabaseError`` here.
            # ``DatabaseError`` is a broad parent that also catches
            # ``sqlite3.ProgrammingError`` ("Cannot operate on a closed
            # database", misuse of cursors, etc.) — those are programming
            # bugs, not runtime hazards, and silently swallowing them would
            # mask real defects. ``OperationalError`` is the specific
            # superclass for runtime hazards we want to tolerate under load
            # (``database is locked``, ``disk I/O error``, ``disk full``).
            except sqlite3.IntegrityError:
                self._game_store_failed_count += 1
                _log.exception(
                    "store_game failed with IntegrityError for "
                    "game_id=%s (base=%s) — likely a stale row from a "
                    "prior game on this env. Marking failed, running "
                    "total failed=%d",
                    self._game_id,
                    self._base_game_id,
                    self._game_store_failed_count,
                )
            except sqlite3.OperationalError:
                self._game_store_failed_count += 1
                _log.exception(
                    "store_game failed with OperationalError for "
                    "game_id=%s — running total failed=%d "
                    "(disk full / database locked / disk I/O error)",
                    self._game_id,
                    self._game_store_failed_count,
                )
        else:
            # ``_db`` is None — tests / unit fixtures. There is no row
            # to record anywhere, so there is nothing for the dashboard
            # Stats tab to show either. Leave ``_db_write_ok=False``
            # so the stats append below is skipped too.
            pass

        # Phase 4.6 Step 2: append this game to the legacy ``stats.json``
        # file that the Stats tab reads. We only run this when the DB
        # write succeeded so ``training.db`` and ``stats.json`` stay in
        # sync — a failed DB write already bumps
        # ``_game_store_failed_count`` and will be excluded from the
        # cycle win-rate denominator, so appending it here would drift
        # the two surfaces. The append is best-effort: a write failure
        # is logged at ERROR (the #73 watchdog will see it) but does
        # NOT re-raise or bump the failure counter, because stats.json
        # is a secondary dashboard surface and the DB row is the
        # canonical record of the game. If future work wants harder
        # semantics, swap the ``_log.exception`` for a ``raise``.
        _stats_path = getattr(self, "_stats_path", None)
        if _db_write_ok and _stats_path is not None:
            try:
                from bots.cand_313752e4_b.batch_runner import GameRecord, append_stats_game

                record = GameRecord(
                    timestamp=datetime.now(UTC).isoformat(),
                    map_name=self._map_name,
                    opponent=f"built-in-{self._difficulty}",
                    result=result_str,
                    duration_seconds=game_time,
                    build_order_used=getattr(
                        self, "_build_order_label", "4gate"
                    ),
                    score=0,
                )
                append_stats_game(_stats_path, record)
            except Exception:
                _log.exception(
                    "append_stats_game failed for game_id=%s — "
                    "stats.json may be stale but training.db is "
                    "authoritative",
                    self._game_id,
                )

    def _snapshot_to_raw(self, snap: GameSnapshot) -> NDArray[np.float32]:
        """Convert snapshot to raw (un-normalized) feature vector for DB storage."""
        d = asdict(snap)
        fields = [
            "supply_used", "supply_cap", "minerals", "vespene", "army_supply",
            "worker_count", "base_count", "enemy_army_near_base",
            "enemy_army_supply_visible", "game_time_seconds",
            "gateway_count", "robo_count", "forge_count", "upgrade_count",
            "enemy_structure_count", "cannon_count", "battery_count",
            "zealot_count", "stalker_count", "sentry_count", "immortal_count",
            "colossus_count", "archon_count", "high_templar_count",
            "dark_templar_count", "phoenix_count", "void_ray_count",
            "carrier_count", "tempest_count", "disruptor_count",
            "warp_prism_count", "observer_count",
            "enemy_light_count", "enemy_armored_count", "enemy_siege_count",
            "enemy_support_count", "enemy_air_harass_count", "enemy_heavy_count",
            "enemy_capital_count", "enemy_cloak_count",
        ]
        values = []
        for f in fields:
            v = d.get(f, 0)
            if isinstance(v, bool):
                v = int(v)
            values.append(float(v))
        return np.array(values, dtype=np.float32)


class _GymStateProxy:
    """Proxy that mimics NeuralDecisionEngine.predict() for gym state injection.

    Alpha4GateBot.on_step() checks if self._neural_engine is not None, then
    calls predict(snapshot). This proxy always returns the gym-chosen state.
    """

    def __init__(self, state: StrategicState) -> None:
        self._state = state
        self._probabilities: list[float] = []

    @property
    def last_probabilities(self) -> list[float]:
        """Action probabilities carried over from the gym caller."""
        return self._probabilities

    def predict(self, snapshot: GameSnapshot) -> StrategicState:
        return self._state


def _make_training_bot(
    obs_queue: queue.Queue[_ObsTuple],
    action_queue: queue.Queue[int | None],
    reward_calc: RewardCalculator,
    advisor_bridge: Any | None = None,
) -> Any:
    """Create a full training bot that inherits Alpha4GateBot.

    The bot runs full macro/micro/scouting from Alpha4GateBot but lets the
    gym action queue override the strategic state each decision step.

    Args:
        advisor_bridge: Optional ``TrainingAdvisorBridge`` instance. When
            provided, the bridge fires Claude CLI calls in its own thread
            (rate-limited at ~60s game-time intervals) and its
            recommendations are encoded into the observation vector so PPO
            can learn to follow Claude's strategic guidance (Approach B
            from #89). When ``None``, advisor features are all zeros.
    """
    from bots.cand_313752e4_b.bot import Alpha4GateBot
    from bots.cand_313752e4_b.build_orders import default_4gate

    class _FullTrainingBot(Alpha4GateBot):
        """Alpha4GateBot subclass that bridges with gym queues for RL training."""

        def __init__(self) -> None:
            super().__init__(
                build_order=default_4gate(),
                logger=None,
                enable_console=False,
                claude_advisor=None,  # live advisor disabled; bridge handles it
            )
            self._obs_queue_train = obs_queue
            self._action_queue_train = action_queue
            self._reward_calc_train = reward_calc
            self._gym_state: StrategicState | None = None
            # Once the episode is torn down (timeout, gym shutdown, or the
            # bot has already resigned) this flag is flipped true and every
            # subsequent ``on_step`` becomes a no-op until ``_play_game_ai``
            # notices ``client.in_game`` is false and returns. Without this,
            # non-realtime SC2 keeps ticking game-time forward while the
            # bot re-enters the timeout branch every 22 iterations and
            # floods the obs queue with phantom terminal observations —
            # exactly the crash pattern logged in issue #72.
            self._episode_done: bool = False

        async def _resign_and_mark_done(self) -> None:
            """Leave the SC2 game and mark the episode as terminated.

            Calls ``self.client.leave()`` so burnysc2's ``_play_game_ai``
            loop observes ``client.in_game is False`` on its next tick and
            returns, which lets ``SC2Process.__aexit__`` run and frees the
            SC2 binary. Errors during ``leave()`` are logged but swallowed:
            the game is already in a broken state, so we just want to
            stop pumping more observations into the queue.
            """
            self._episode_done = True
            try:
                await self.client.leave()
            except Exception:  # pragma: no cover - defensive
                _log.debug("client.leave() raised during teardown", exc_info=True)

        async def on_step(self, iteration: int) -> None:
            """Run full Alpha4GateBot logic, but override strategic state from gym.

            On decision steps (every STEPS_PER_ACTION), sends an observation to
            the gym and receives a PPO action. The action is injected via a proxy
            neural engine so Alpha4GateBot.on_step() uses the gym-chosen state
            for all macro/micro decisions.
            """
            if self._episode_done:
                # Episode is torn down; do nothing until burnysc2's game
                # loop notices and exits. Crucially, do NOT run the parent
                # on_step (it assumes a live game) and do NOT push more
                # observations onto the queue.
                return

            if iteration % STEPS_PER_ACTION == 0:
                snapshot = self._build_snapshot()

                # -- Advisor bridge: poll + submit -----------------
                # Poll for a completed response from the bridge thread.
                # Then submit a new request (rate-limited by the bridge).
                _adv_commands: list[dict[str, str]] | None = None
                _adv_urgency: str | None = None
                if advisor_bridge is not None:
                    advisor_bridge.poll_response()
                    if advisor_bridge.last_response is not None:
                        resp = advisor_bridge.last_response
                        _adv_commands = [
                            {"action": cmd.action}
                            for cmd in resp.commands
                        ]
                        _adv_urgency = resp.urgency
                    # Build a training prompt with situational principles
                    from bots.cand_313752e4_b.learning.advisor_bridge import (
                        build_training_prompt,
                    )
                    state_for_prompt = asdict(snapshot)
                    state_for_prompt["current_state"] = (
                        self._gym_state.value
                        if self._gym_state
                        else self.decision_engine.state.value
                    )
                    prompt = build_training_prompt(
                        state_for_prompt, advisor_bridge.principles
                    )
                    advisor_bridge.submit_request(
                        prompt, snapshot.game_time_seconds
                    )

                obs = encode(
                    snapshot,
                    advisor_commands=_adv_commands,
                    advisor_urgency=_adv_urgency,
                )
                state_dict = asdict(snapshot)

                # Check for game time limit — send terminal timeout if exceeded
                timed_out = snapshot.game_time_seconds >= MAX_GAME_TIME_SECONDS
                # Capture action_probs from neural engine proxy if available
                _action_probs: list[float] | None = None
                if (
                    self._neural_engine is not None
                    and hasattr(self._neural_engine, "last_probabilities")
                    and self._neural_engine.last_probabilities
                ):
                    _action_probs = self._neural_engine.last_probabilities

                info: dict[str, Any] = {
                    "snapshot": snapshot,
                    "snapshot_dict": state_dict,
                    "game_time": snapshot.game_time_seconds,
                    "strategic_state": (
                        self._gym_state.value
                        if self._gym_state
                        else self.decision_engine.state.value
                    ),
                    "action_probs": _action_probs,
                }
                if timed_out:
                    _log.info(
                        "Game timeout at %.0fs — sending terminal observation",
                        snapshot.game_time_seconds,
                    )
                    self._obs_queue_train.put((obs, info, True, "timeout"))
                    # Surrender so the game actually ends. Without this,
                    # non-realtime SC2 would keep ticking and on_step would
                    # re-fire indefinitely until something external killed
                    # the SC2 subprocess (see issue #72).
                    await self._resign_and_mark_done()
                    return

                self._obs_queue_train.put((obs, info, False, None))

                try:
                    action = self._action_queue_train.get(timeout=120)
                except queue.Empty:
                    # Gym side never delivered an action within the
                    # window. This is the same "episode must exit via
                    # client.leave()" invariant as the shutdown and
                    # timeout branches: surrender, flip _episode_done,
                    # and stop pumping observations. Without this the
                    # bot could re-enter the decision path on the next
                    # STEPS_PER_ACTION tick and re-fire the same timeout
                    # branch indefinitely (issue #72 reviewer M2).
                    _log.warning(
                        "action_queue.get timed out after 120s — "
                        "surrendering episode"
                    )
                    await self._resign_and_mark_done()
                    return
                if action is None:
                    # Gym signalled shutdown (SC2Env.close()). Leave the
                    # game so the thread exits cleanly rather than
                    # becoming an orphaned zombie that keeps SC2 alive.
                    await self._resign_and_mark_done()
                    return
                if 0 <= action < len(_ACTION_TO_STATE):
                    # Phase 4.8 warmup: during the first WARMUP_GAME_SECONDS
                    # of game time, override PPO's action with OPENING so
                    # the rule-based bot builds economy/army safely. PPO
                    # still sees observations and receives rewards (it
                    # learns from the warmup trajectory) but can't make
                    # suicidal choices like ATTACK at step 0 with no units.
                    if snapshot.game_time_seconds < WARMUP_GAME_SECONDS:
                        action = 0  # OPENING
                    self._gym_state = _ACTION_TO_STATE[action]
                    # Inject gym state via the neural engine proxy so
                    # Alpha4GateBot.on_step() uses it for macro/micro
                    proxy = _GymStateProxy(self._gym_state)
                    # Carry over last_probabilities from previous neural engine
                    if (
                        self._neural_engine is not None
                        and hasattr(self._neural_engine, "last_probabilities")
                    ):
                        proxy._probabilities = self._neural_engine.last_probabilities
                    self._neural_engine = proxy  # type: ignore[assignment]

            await super().on_step(iteration)

    return _FullTrainingBot()
