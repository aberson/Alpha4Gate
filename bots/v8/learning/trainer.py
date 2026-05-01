"""RL training orchestrator: collect → train → checkpoint cycle with curriculum."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Disk guard: stop training if data dir exceeds this size (bytes)
DEFAULT_DISK_LIMIT_GB: float = 200.0


def compute_adjusted_win_rate(
    db: Any,
    expected_games: int,
    failed_games: int,
) -> float:
    """Shrink the tail window passed to ``db.get_recent_win_rate``.

    Subtracts ``failed_games`` from ``expected_games`` before querying,
    so the ``failed_games`` rows that never made it into the DB do not
    silently pull an equal number of even older rows into the tail
    window.

    NOTE: this does NOT guarantee the remaining window contains only
    current-cycle live games. ``TrainingOrchestrator.run`` calls this
    helper with ``expected_games = games_per_cycle * 2`` — a two-cycle
    smoothing window — so the tail still absorbs prior-cycle rows
    whenever the current cycle is short. That behavior is intentional
    and carried over from the pre-fix code; it matches the existing
    2x-cycle smoothing convention in the trainer. All this helper
    corrects is the "failed games silently replaced by older games"
    drift, not the underlying 2x smoothing approximation.
    If every game in the cycle failed, or the adjusted window is empty,
    returns 0.0 — the caller should not advance the difficulty
    curriculum on a zero-signal cycle.

    Args:
        db: A ``TrainingDB``-like object exposing ``get_recent_win_rate``.
        expected_games: Number of games the cycle was supposed to
            produce (passed directly to ``get_recent_win_rate`` when
            nothing failed). In the trainer this is ``games_per_cycle * 2``.
        failed_games: Count of games whose ``store_game`` raised (read
            from ``SC2Env.game_store_failed_count``).

    Returns:
        Adjusted win rate in ``[0.0, 1.0]``.
    """
    live_games = expected_games - failed_games
    if live_games <= 0:
        return 0.0
    return float(db.get_recent_win_rate(live_games))


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
        replay_dir: str | Path | None = None,
        advisor_bridge: Any | None = None,
    ) -> None:
        self._checkpoint_dir = Path(checkpoint_dir)
        self._db_path = Path(db_path)
        self._data_dir = self._db_path.parent
        # Phase 4.6 Step 2: the replay directory is passed in explicitly
        # so the dashboard Replays tab sees trainer games. Defaults to a
        # sibling ``replays/`` next to the data dir (matches the default
        # in ``config.Settings`` where ``DATA_DIR`` and ``REPLAY_DIR``
        # both live in the project root). Call sites that load
        # ``Settings`` should pass ``settings.replay_dir`` explicitly
        # so the configured path wins over the default.
        self._replay_dir = (
            Path(replay_dir) if replay_dir is not None
            else self._data_dir.parent / "replays"
        )
        self._reward_rules_path = reward_rules_path
        self._hyperparams_path = hyperparams_path
        # Phase 4.8 Approach B: TrainingAdvisorBridge for training-time
        # observation enrichment. When set, the bridge fires Claude CLI
        # calls in its own thread and recommendations appear in the
        # observation vector.
        self._advisor_bridge = advisor_bridge
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
        from bots.v8.learning.checkpoints import prune_checkpoints, save_checkpoint
        from bots.v8.learning.database import TrainingDB

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
            cycle_failed_games: int = 0
            try:
                model.set_env(env)
                # Estimate total timesteps: games_per_cycle games, each ~15
                # decisions (300 game-seconds / 22 steps-per-action ≈ 15).
                # NOTE: this is an UPPER BOUND on how many games might
                # actually be played. Under ``realtime=False`` a single
                # game can consume the whole budget before it finishes,
                # so the cycle runs closer to "1 game" than
                # "games_per_cycle games". Phase 4.7 Step 5 (#86) renamed
                # the per-cycle log below from "Training: N games, ~M
                # timesteps" to a PPO-budget framing so operators do not
                # sit watching for N game completions per cycle that will
                # not arrive. ``games_per_cycle`` still governs the
                # win-rate window downstream (``get_recent_win_rate``)
                # which is why the field is retained.
                # Phase 4.8: increased from 15 to 150 steps per game.
                # The old estimate (15) was calibrated for a regime where
                # one RL game consumed the entire budget (~192 steps).
                # After Fix C (terminal rewards 10x up), games can be
                # much shorter and the budget fragments across many
                # micro-games that never reach a terminal state. 150
                # steps/game × 10 games = 1500 total timesteps gives
                # enough budget for full-length games to receive the
                # +100/-100 terminal reward signal.
                est_steps = games_per_cycle * 150
                _log.info(
                    "Training cycle %d: PPO.learn(total_timesteps=%d)",
                    self._cycle, est_steps,
                )
                model.learn(total_timesteps=est_steps, reset_num_timesteps=False)
                self._total_games += games_per_cycle
            except Exception as exc:
                _log.exception("Training cycle %d crashed", self._cycle)
                cycle_failed = True
                cycle_error = f"{type(exc).__name__}: {exc}"
            finally:
                # Bug B (soak-2026-04-11): read the per-env failure
                # counter BEFORE ``env.close()`` so the cycle code can
                # adjust the win-rate denominator. The counter lives on
                # the SC2Env instance so it survives until we close the
                # env here. If any games crashed inside the game thread
                # they'll be visible as ``cycle_failed_games > 0`` and
                # will be logged loudly below. We accept only a real
                # int and fall back to 0 when tests hand us a
                # ``MagicMock`` env whose attribute access returns a
                # ``MagicMock`` instead of a real count.
                raw_failed = getattr(env, "game_store_failed_count", 0)
                cycle_failed_games = (
                    raw_failed if isinstance(raw_failed, int) else 0
                )
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

            # Check win rate for curriculum. Bug B (soak-2026-04-11):
            # games whose ``store_game`` raised were excluded from the
            # ``games`` table, so a plain ``get_recent_win_rate(N)`` call
            # would silently absorb older games into its tail window.
            # ``compute_adjusted_win_rate`` shrinks the window so only
            # live (DB-written) games count toward the denominator.
            if cycle_failed_games > 0:
                _log.error(
                    "Cycle %d: %d games failed to store — "
                    "shrinking win-rate window by %d "
                    "(base window=%d)",
                    self._cycle,
                    cycle_failed_games,
                    cycle_failed_games,
                    games_per_cycle * 2,
                )
            recent_win_rate = compute_adjusted_win_rate(
                db,
                expected_games=games_per_cycle * 2,
                failed_games=cycle_failed_games,
            )
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
                "failed_games": cycle_failed_games,
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

        from bots.v8.learning.policy_probe import get_action_probs

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
            probs = get_action_probs(model, obs)
            if probs.size == 0:
                _log.warning("Could not get diagnostics for %s", ds["name"])
                continue
            action = int(probs.argmax())
            cycle_diag["states"].append({
                "name": ds["name"],
                "action": action,
                "probs": [round(float(p), 4) for p in probs],
            })

        existing.append(cycle_diag)
        with open(output_path, "w") as f:
            json.dump(existing, f, indent=2)
        _log.info("Diagnostics: %s", cycle_diag)

    def _make_env(self, db: Any) -> Any:
        """Create an SC2Env for training with the current difficulty.

        Phase 4.6 Step 2 wiring: the env is given the replay dir and
        ``stats.json`` path so every trainer game lands on the same
        legacy dashboard surfaces (Stats tab, Replays tab, Reward
        Trends) that the manual ``--batch`` path produces. Before this
        wiring, the dashboard was blind to trainer activity because
        those producers only lived in ``connection.run_bot`` and
        ``batch_runner.save_stats`` — neither of which the trainer
        called. See Phase 4.6 fix plan and ``documentation/soak-*``
        for the full soak findings.

        NOTE: ``reward_calc.open_game_log`` is no longer called here.
        ``SC2Env.reset()`` now rotates the log file on every game so
        each trainer game produces its own ``game_<id>.jsonl`` (and
        the Reward Trends aggregator can count one file per game).
        Previously this helper called ``open_game_log`` once with the
        base game_id and every game in the cycle appended to that
        single file, producing the "Scanned 8 games" discrepancy in
        soak-2026-04-11.
        """
        from bots.v8.learning.environment import SC2Env
        from bots.v8.learning.rewards import RewardCalculator

        log_dir = self._data_dir / "reward_logs"
        reward_calc = RewardCalculator(
            self._reward_rules_path if self._reward_rules_path else None,
            log_dir=log_dir,
        )
        game_id = f"rl_{uuid.uuid4().hex[:8]}"
        replay_dir = self._replay_dir
        stats_path = self._data_dir / "stats.json"
        return SC2Env(
            map_name=self._map_name,
            difficulty=self._difficulty,
            reward_calculator=reward_calc,
            db=db,
            game_id=game_id,
            model_version=f"v{self._cycle}",
            replay_dir=replay_dir,
            stats_path=stats_path,
            build_order_label="4gate",
            advisor_bridge=self._advisor_bridge,
        )

    def _init_or_resume_model(self, resume: bool) -> Any:
        """Initialize or load a PPO model, dispatching on ``policy_type``.

        Picks one of ``{PPO, PPOWithKL, RecurrentPPO, RecurrentPPOWithKL}``
        based on hyperparams ``policy_type`` and ``kl_rules_coef``. When
        ``use_imitation_init`` is set and a ``v0_pretrain`` checkpoint
        exists, loads that as the starting point (AlphaStar-style
        supervised initialization). Resume takes priority over the
        imitation path.

        The dummy env's observation and action spaces are read directly
        from ``SC2Env`` so the model cannot silently drift from what the
        real env will hand it later. Hardcoding spaces here was the
        root cause of two Phase 4.5 findings (F1: obs space drift
        15->17, F6: action space drift 5->6).
        """
        import gymnasium

        from bots.v8.learning.environment import SC2Env
        from bots.v8.learning.hyperparams import load_hyperparams, to_ppo_kwargs

        params: dict[str, Any] = {}
        if self._hyperparams_path is not None:
            params = load_hyperparams(self._hyperparams_path)
        policy_type = str(params.get("policy_type", "MlpPolicy"))
        kl_coef = float(params.get("kl_rules_coef", 0.0))
        use_imitation = bool(params.get("use_imitation_init", False))

        model_cls = self._pick_model_class(policy_type, kl_coef)

        if resume:
            best = self._resume_checkpoint_name()
            if best is not None:
                _log.info("Resuming from checkpoint: %s (class=%s)",
                          best, model_cls.__name__)
                return model_cls.load(str(self._checkpoint_dir / best))

        if use_imitation:
            pretrain = self._checkpoint_dir / "v0_pretrain.zip"
            if pretrain.exists():
                _log.info("Loading imitation-pretrained checkpoint v0_pretrain (class=%s)",
                          model_cls.__name__)
                return model_cls.load(str(pretrain.with_suffix("")))
            _log.warning(
                "use_imitation_init=true but %s not found. "
                "Run imitation training first (--train imitation) or pass "
                "--ensure-pretrain. Falling through to a fresh model.",
                pretrain,
            )

        dummy_env = gymnasium.make("CartPole-v1")
        dummy_env.observation_space = SC2Env.observation_space
        dummy_env.action_space = SC2Env.action_space
        ppo_kwargs: dict[str, Any] = to_ppo_kwargs(params) if params else {
            "policy_kwargs": {"net_arch": [128, 128]},
        }
        if kl_coef > 0.0:
            ppo_kwargs["kl_rules_coef"] = kl_coef
        model = model_cls(policy_type, dummy_env, **ppo_kwargs)
        dummy_env.close()
        return model

    @staticmethod
    def _pick_model_class(policy_type: str, kl_coef: float) -> Any:
        """Dispatch: (policy_type, kl_coef>0) -> concrete SB3 model class."""
        from sb3_contrib import RecurrentPPO
        from stable_baselines3 import PPO

        from bots.v8.learning.ppo_kl import PPOWithKL, RecurrentPPOWithKL

        use_kl = kl_coef > 0.0
        if policy_type == "MlpLstmPolicy":
            return RecurrentPPOWithKL if use_kl else RecurrentPPO
        return PPOWithKL if use_kl else PPO

    def _resume_checkpoint_name(self) -> str | None:
        """Return best checkpoint name if one exists, else None."""
        from bots.v8.learning.checkpoints import get_best_name

        name = get_best_name(self._checkpoint_dir)
        if name is None:
            return None
        if not (self._checkpoint_dir / f"{name}.zip").exists():
            return None
        return name
