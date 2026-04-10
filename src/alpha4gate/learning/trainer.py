"""RL training orchestrator: collect → train → checkpoint cycle with curriculum."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Disk guard: stop training if data dir exceeds this size (bytes)
DEFAULT_DISK_LIMIT_GB: float = 200.0


class TrainingOrchestrator:
    """Manages the RL training loop: games → PPO update → checkpoint → repeat.

    Features:
        - Difficulty curriculum: auto-increase when win rate exceeds threshold
        - Crash recovery: resume from last complete cycle
        - Disk guard: stop when training data exceeds size limit
        - SC2 process cleanup between games
    """

    def __init__(
        self,
        checkpoint_dir: str | Path,
        db_path: str | Path,
        reward_rules_path: str | Path | None = None,
        hyperparams_path: str | Path | None = None,
        map_name: str = "Simple64",
        initial_difficulty: int = 1,
        max_difficulty: int = 10,
        win_rate_threshold: float = 0.8,
        disk_limit_gb: float = DEFAULT_DISK_LIMIT_GB,
    ) -> None:
        self._checkpoint_dir = Path(checkpoint_dir)
        self._db_path = Path(db_path)
        self._data_dir = self._db_path.parent
        self._reward_rules_path = reward_rules_path
        self._hyperparams_path = hyperparams_path
        self._map_name = map_name
        self._difficulty = initial_difficulty
        self._max_difficulty = max_difficulty
        self._win_rate_threshold = win_rate_threshold
        self._disk_limit_gb = disk_limit_gb

        # State tracking
        self._cycle: int = 0
        self._total_games: int = 0
        self._stopped: bool = False
        self._stop_reason: str = ""

    @property
    def difficulty(self) -> int:
        return self._difficulty

    @property
    def cycle(self) -> int:
        return self._cycle

    @property
    def total_games(self) -> int:
        return self._total_games

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def stop_reason(self) -> str:
        return self._stop_reason

    def should_increase_difficulty(self, win_rate: float) -> bool:
        """Check if difficulty should be increased based on recent win rate."""
        if win_rate >= self._win_rate_threshold and self._difficulty < self._max_difficulty:
            return True
        return False

    def increase_difficulty(self) -> int:
        """Increase difficulty by 1, up to max."""
        if self._difficulty < self._max_difficulty:
            self._difficulty += 1
            _log.info("Difficulty increased to %d", self._difficulty)
        return self._difficulty

    def check_disk_guard(self) -> bool:
        """Check if training data exceeds disk limit.

        Returns:
            True if under limit (safe to continue), False if over limit.
        """
        if self._db_path.exists():
            size_gb = self._db_path.stat().st_size / (1024**3)
            if size_gb >= self._disk_limit_gb:
                self._stopped = True
                self._stop_reason = (
                    f"Disk limit exceeded: {size_gb:.1f} GB >= {self._disk_limit_gb} GB"
                )
                _log.warning(self._stop_reason)
                return False
        return True

    def run(
        self,
        n_cycles: int,
        games_per_cycle: int,
        resume: bool = False,
    ) -> dict[str, Any]:
        """Run the full training loop.

        This is the main entry point. Each cycle:
        1. Play `games_per_cycle` games collecting transitions
        2. Train PPO on collected experience
        3. Save checkpoint
        4. Check curriculum advancement

        Args:
            n_cycles: Number of training cycles to run.
            games_per_cycle: Number of games per cycle.
            resume: If True, resume from last checkpoint.

        Returns:
            Training summary dict.
        """
        from alpha4gate.learning.checkpoints import prune_checkpoints, save_checkpoint
        from alpha4gate.learning.database import TrainingDB

        db = TrainingDB(self._db_path)

        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # Build or load model
        model = self._init_or_resume_model(resume)

        start_cycle = self._cycle
        results: list[dict[str, Any]] = []

        for cycle_idx in range(n_cycles):
            self._cycle = start_cycle + cycle_idx + 1
            _log.info(
                "=== Cycle %d/%d (difficulty=%d) ===",
                self._cycle,
                start_cycle + n_cycles,
                self._difficulty,
            )

            # Disk guard check
            if not self.check_disk_guard():
                break

            # Play games and train via model.learn() + SC2Env.
            # SB3 PPO is on-policy: model.learn() internally calls env.reset()
            # and env.step() to collect rollouts, then runs PPO updates.
            env = self._make_env(db)
            cycle_failed = False
            cycle_error: str | None = None
            try:
                model.set_env(env)
                # Estimate total timesteps: games_per_cycle games, each ~15
                # decisions (300 game-seconds / 22 steps-per-action ≈ 15).
                est_steps = games_per_cycle * 15
                _log.info(
                    "Training: %d games, ~%d timesteps",
                    games_per_cycle, est_steps,
                )
                model.learn(total_timesteps=est_steps, reset_num_timesteps=False)
                self._total_games += games_per_cycle
            except Exception as exc:
                _log.exception("Training cycle %d crashed", self._cycle)
                cycle_failed = True
                cycle_error = f"{type(exc).__name__}: {exc}"
            finally:
                env.close()

            # If the cycle crashed, skip the entire post-training block:
            # no win-rate read (would use stale DB data), no curriculum
            # advancement, no phantom checkpoint save, no fake "complete"
            # cycle_result. Record the failure honestly so the daemon and
            # promotion gate know to bail out.
            if cycle_failed:
                cycle_result = {
                    "cycle": self._cycle,
                    "difficulty": self._difficulty,
                    "status": "crashed",
                    "error": cycle_error,
                }
                results.append(cycle_result)
                _log.warning(
                    "Cycle %d skipped post-training block (crashed: %s)",
                    self._cycle, cycle_error,
                )
                continue

            # Check win rate for curriculum
            recent_win_rate = db.get_recent_win_rate(games_per_cycle * 2)
            if self.should_increase_difficulty(recent_win_rate):
                self.increase_difficulty()

            checkpoint_name = f"v{self._cycle}"
            _log.info("Cycle %d: win_rate=%.2f", self._cycle, recent_win_rate)

            # Run diagnostics on representative states
            self._log_diagnostics(model, self._cycle, recent_win_rate)

            # Save checkpoint — promotion gate decides best, not trainer
            save_checkpoint(
                model,
                self._checkpoint_dir,
                checkpoint_name,
                metadata={
                    "cycle": self._cycle,
                    "difficulty": self._difficulty,
                    "total_games": self._total_games,
                    "win_rate": recent_win_rate,
                },
                is_best=False,
            )

            # Prune old checkpoints
            prune_checkpoints(self._checkpoint_dir, keep=5)

            cycle_result = {
                "cycle": self._cycle,
                "difficulty": self._difficulty,
                "win_rate": recent_win_rate,
                "checkpoint": checkpoint_name,
            }
            results.append(cycle_result)
            _log.info("Cycle %d complete: %s", self._cycle, cycle_result)

        db.close()

        return {
            "cycles_completed": len(results),
            "total_games": self._total_games,
            "final_difficulty": self._difficulty,
            "stopped": self._stopped,
            "stop_reason": self._stop_reason,
            "cycle_results": results,
        }

    def _log_diagnostics(self, model: Any, cycle: int, win_rate: float) -> None:
        """Log action probabilities on diagnostic states after each cycle."""
        import json

        import numpy as np
        import torch

        diag_path = self._checkpoint_dir.parent / "diagnostic_states.json"
        if not diag_path.exists():
            return

        with open(diag_path) as f:
            diag_states = json.load(f)

        output_path = self._checkpoint_dir.parent / "training_diagnostics.json"
        existing: list[Any] = []
        if output_path.exists():
            with open(output_path) as f:
                existing = json.load(f)

        cycle_diag: dict[str, Any] = {
            "cycle": cycle,
            "win_rate": win_rate,
            "states": [],
        }
        for ds in diag_states:
            obs = np.array(ds["features"], dtype=np.float32)
            try:
                with torch.no_grad():
                    obs_t = torch.as_tensor(obs).unsqueeze(0).to(model.device)
                    dist = model.policy.get_distribution(obs_t)
                    probs = dist.distribution.probs[0].cpu().numpy()
                    action = int(probs.argmax())
                    cycle_diag["states"].append({
                        "name": ds["name"],
                        "action": action,
                        "probs": [round(float(p), 4) for p in probs],
                    })
            except Exception:
                _log.warning("Could not get diagnostics for %s", ds["name"])

        existing.append(cycle_diag)
        with open(output_path, "w") as f:
            json.dump(existing, f, indent=2)
        _log.info("Diagnostics: %s", cycle_diag)

    def _make_env(self, db: Any) -> Any:
        """Create an SC2Env for training with the current difficulty."""
        from alpha4gate.learning.environment import SC2Env
        from alpha4gate.learning.rewards import RewardCalculator

        log_dir = self._data_dir / "reward_logs"
        reward_calc = RewardCalculator(
            self._reward_rules_path if self._reward_rules_path else None,
            log_dir=log_dir,
        )
        game_id = f"rl_{uuid.uuid4().hex[:8]}"
        reward_calc.open_game_log(game_id)
        return SC2Env(
            map_name=self._map_name,
            difficulty=self._difficulty,
            reward_calculator=reward_calc,
            db=db,
            game_id=game_id,
            model_version=f"v{self._cycle}",
        )

    def _init_or_resume_model(self, resume: bool) -> Any:
        """Initialize a new PPO model or load from latest checkpoint."""
        import gymnasium
        from gymnasium import spaces
        from stable_baselines3 import PPO

        from alpha4gate.learning.checkpoints import get_best_name, load_checkpoint
        from alpha4gate.learning.features import FEATURE_DIM
        from alpha4gate.learning.hyperparams import load_hyperparams, to_ppo_kwargs

        if resume:
            best = get_best_name(self._checkpoint_dir)
            if best is not None:
                _log.info("Resuming from checkpoint: %s", best)
                return load_checkpoint(self._checkpoint_dir, best)

        # Create new model
        import numpy as np

        obs_space = spaces.Box(low=0.0, high=1.0, shape=(FEATURE_DIM,), dtype=np.float32)
        act_space: spaces.Discrete = spaces.Discrete(5)  # type: ignore[type-arg]
        dummy_env = gymnasium.make("CartPole-v1")
        dummy_env.observation_space = obs_space
        dummy_env.action_space = act_space

        ppo_kwargs: dict[str, Any] = {"policy_kwargs": {"net_arch": [128, 128]}}
        if self._hyperparams_path is not None:
            params = load_hyperparams(self._hyperparams_path)
            ppo_kwargs = to_ppo_kwargs(params)

        model = PPO("MlpPolicy", dummy_env, **ppo_kwargs)
        dummy_env.close()
        return model
