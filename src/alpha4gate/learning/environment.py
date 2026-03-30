"""Gymnasium environment wrapper: bridges burnysc2 async game loop with sync gym.Env."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from dataclasses import asdict
from typing import Any

import gymnasium
import numpy as np
from gymnasium import spaces
from numpy.typing import NDArray

from alpha4gate.decision_engine import GameSnapshot, StrategicState
from alpha4gate.learning.database import TrainingDB
from alpha4gate.learning.features import FEATURE_DIM, encode
from alpha4gate.learning.rewards import RewardCalculator

_log = logging.getLogger(__name__)

# Map action index → StrategicState (same as neural_engine)
_ACTION_TO_STATE: list[StrategicState] = [
    StrategicState.OPENING,
    StrategicState.EXPAND,
    StrategicState.ATTACK,
    StrategicState.DEFEND,
    StrategicState.LATE_GAME,
]

# How many game steps per env step (match bot.py observation frequency)
STEPS_PER_ACTION: int = 22

# Max game time in seconds before forcing a timeout (prevents 20+ min passive games)
MAX_GAME_TIME_SECONDS: float = 900.0  # 15 minutes


class SC2Env(gymnasium.Env[NDArray[np.float32], int]):
    """Gymnasium environment wrapping a burnysc2 SC2 game.

    The async-to-sync bridge works as follows:
    - reset() launches a new SC2 game in a background thread running asyncio
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
    action_space: spaces.Discrete = spaces.Discrete(5)  # type: ignore[type-arg]

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
        _ObsTuple = tuple[NDArray[np.float32], dict[str, Any], bool, str | None]
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

        # Launch game in background thread
        self._game_thread = threading.Thread(
            target=self._run_game_thread, daemon=True
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

    def _run_game_thread(self) -> None:
        """Run the SC2 game in a new asyncio event loop (background thread)."""
        try:
            asyncio.run(self._async_game())
        except Exception:
            _log.exception("Game thread crashed")
            # Send terminal observation so step() doesn't hang
            obs = np.zeros(FEATURE_DIM, dtype=np.float32)
            self._obs_queue.put((obs, {}, True, "loss"))

    async def _async_game(self) -> None:
        """Run the actual SC2 game with a custom bot that bridges to the queues."""
        import sc2.maps
        from sc2.data import Difficulty, Race
        from sc2.main import run_game
        from sc2.player import Bot, Computer

        bot = _make_training_bot(
            obs_queue=self._obs_queue,
            action_queue=self._action_queue,
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

        result = await run_game(  # type: ignore[misc]
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
            "enemy_structure_count",
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

    def predict(self, snapshot: GameSnapshot) -> StrategicState:
        return self._state


def _make_training_bot(
    obs_queue: queue.Queue[tuple[NDArray[np.float32], dict[str, Any], bool, str | None]],
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

        async def on_step(self, iteration: int) -> None:
            """Run full Alpha4GateBot logic, but override strategic state from gym.

            On decision steps (every STEPS_PER_ACTION), sends an observation to
            the gym and receives a PPO action. The action is injected via a proxy
            neural engine so Alpha4GateBot.on_step() uses the gym-chosen state
            for all macro/micro decisions.
            """
            if iteration % STEPS_PER_ACTION == 0:
                snapshot = self._build_snapshot()
                obs = encode(snapshot)
                state_dict = asdict(snapshot)

                # Check for game time limit — send terminal timeout if exceeded
                timed_out = snapshot.game_time_seconds >= MAX_GAME_TIME_SECONDS
                info: dict[str, Any] = {
                    "snapshot": snapshot,
                    "snapshot_dict": state_dict,
                    "game_time": snapshot.game_time_seconds,
                    "strategic_state": (
                        self._gym_state.value
                        if self._gym_state
                        else self.decision_engine.state.value
                    ),
                }
                if timed_out:
                    _log.info(
                        "Game timeout at %.0fs — sending terminal observation",
                        snapshot.game_time_seconds,
                    )
                    self._obs_queue_train.put((obs, info, True, "timeout"))
                    return  # Stop playing — SC2Env will close the game

                self._obs_queue_train.put((obs, info, False, None))

                action = self._action_queue_train.get(timeout=120)
                if action is None:
                    return  # Shutdown signal
                if 0 <= action < len(_ACTION_TO_STATE):
                    self._gym_state = _ACTION_TO_STATE[action]
                    # Inject gym state via the neural engine proxy so
                    # Alpha4GateBot.on_step() uses it for macro/micro
                    self._neural_engine = _GymStateProxy(self._gym_state)  # type: ignore[assignment]

            await super().on_step(iteration)

    return _FullTrainingBot()
