"""Gymnasium environment wrapper: bridges burnysc2 async game loop with sync gym.Env."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import asdict
from typing import Any

import gymnasium
import numpy as np
from gymnasium import spaces
from numpy.typing import NDArray

from alpha4gate.decision_engine import (
    ACTION_TO_STATE as _ACTION_TO_STATE,
)
from alpha4gate.decision_engine import (
    NUM_ACTIONS,
    GameSnapshot,
    StrategicState,
)
from alpha4gate.learning.database import TrainingDB
from alpha4gate.learning.features import FEATURE_DIM, encode
from alpha4gate.learning.rewards import RewardCalculator

_log = logging.getLogger(__name__)

# How many game steps per env step (match bot.py observation frequency)
STEPS_PER_ACTION: int = 22

# Max game time in seconds before forcing a timeout (prevents 20+ min passive games)
MAX_GAME_TIME_SECONDS: float = 900.0  # 15 minutes

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
    ) -> None:
        super().__init__()
        self._map_name = map_name
        self._difficulty = difficulty
        self._reward_calc = reward_calculator or RewardCalculator()
        self._db = db
        self._game_id = game_id or "unnamed"
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

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[NDArray[np.float32], dict[str, Any]]:
        """Launch a new SC2 game and return the first observation."""
        super().reset(seed=seed, options=options)
        self.close()  # Kill any existing game

        self._step_index = 0
        self._total_reward = 0.0
        self._last_snapshot = None

        # Create FRESH queues for the new game. Issue #72: if the previous
        # game_thread was orphaned (for example because close() timed out
        # joining it), the zombie still holds a reference to the queues it
        # was created with. Creating new queue objects here guarantees that
        # the zombie's residual put()s go to a dead queue and cannot
        # contaminate the new game's observation stream.
        self._obs_queue = queue.Queue[_ObsTuple]()
        self._action_queue = queue.Queue[int | None]()

        # Launch game in background thread
        self._game_thread = threading.Thread(
            target=self._run_game_thread,
            args=(self._obs_queue, self._action_queue),
            daemon=True,
        )
        self._game_thread.start()

        # Wait for first observation
        obs, info, done, _ = self._obs_queue.get(timeout=300)
        return obs, info

    def step(
        self, action: int
    ) -> tuple[NDArray[np.float32], float, bool, bool, dict[str, Any]]:
        """Apply action, advance 22 game steps, return new observation."""
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
            _log.exception("Game thread crashed")
            # Send terminal observation so step() doesn't hang
            obs = np.zeros(FEATURE_DIM, dtype=np.float32)
            obs_queue.put((obs, {}, True, "loss"))
        finally:
            signal.signal = _orig_signal
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

        result = run_game(
            sc2.maps.get(self._map_name),
            [Bot(Race.Protoss, bot), Computer(Race.Random, diff)],
            realtime=self._realtime,
        )

        # Store game result in DB
        result_str = "win" if str(result) == "Result.Victory" else "loss"
        game_time = bot.time if hasattr(bot, "time") else 0.0

        if self._db is not None:
            self._db.store_game(
                game_id=self._game_id,
                map_name=self._map_name,
                difficulty=self._difficulty,
                result=result_str,
                duration_secs=game_time,
                total_reward=self._total_reward,
                model_version=self._model_version,
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
) -> Any:
    """Create a full training bot that inherits Alpha4GateBot.

    The bot runs full macro/micro/scouting from Alpha4GateBot but lets the
    gym action queue override the strategic state each decision step.
    """
    from alpha4gate.bot import Alpha4GateBot
    from alpha4gate.build_orders import default_4gate

    class _FullTrainingBot(Alpha4GateBot):
        """Alpha4GateBot subclass that bridges with gym queues for RL training."""

        def __init__(self) -> None:
            super().__init__(
                build_order=default_4gate(),
                logger=None,
                enable_console=False,
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
                obs = encode(snapshot)
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
