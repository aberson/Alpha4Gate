"""Model evaluator: run inference-only games and collect win rate + stats."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from alpha4gate.config import Settings
from alpha4gate.learning.database import TrainingDB

_log = logging.getLogger(__name__)


@dataclass
class EvalResult:
    """Result of evaluating a checkpoint over N games."""

    checkpoint: str
    games_played: int
    wins: int
    losses: int
    win_rate: float
    avg_reward: float
    avg_duration: float
    difficulty: int
    action_distribution: list[float] | None


@dataclass
class ComparisonResult:
    """Result of comparing two checkpoints."""

    a: EvalResult
    b: EvalResult
    winner: str
    win_rate_delta: float
    significant: bool


@dataclass
class EvalJob:
    """Tracks an in-progress or completed evaluation job."""

    job_id: str
    status: str  # "pending", "running", "completed", "failed"
    checkpoint: str
    n_games: int
    difficulty: int
    result: EvalResult | None = None
    error: str | None = None
    games_completed: int = 0


class ModelEvaluator:
    """Evaluates model checkpoints by running inference-only games.

    Each game loads the specified checkpoint, runs a predict-only loop
    (no gradient updates), and records results to the training database.
    """

    def __init__(self, settings: Settings, db: TrainingDB) -> None:
        self._settings = settings
        self._db = db
        self._checkpoint_dir = settings.data_dir / "checkpoints"
        self._jobs: dict[str, EvalJob] = {}

    def evaluate(
        self,
        checkpoint_name: str,
        n_games: int,
        difficulty: int,
    ) -> EvalResult:
        """Run N evaluation games with inference only and return aggregated stats.

        Args:
            checkpoint_name: Name of the checkpoint to load (e.g., "v5").
            n_games: Number of games to play.
            difficulty: SC2 AI difficulty level (1-10).

        Returns:
            EvalResult with win rate and statistics.
        """
        model = self._load_model(checkpoint_name)

        total_reward = 0.0
        total_duration = 0.0
        wins = 0
        losses = 0
        all_action_probs: list[list[float]] = []

        for game_idx in range(n_games):
            game_id = f"eval_{checkpoint_name}_{uuid.uuid4().hex[:8]}"
            _log.info(
                "Eval game %d/%d (checkpoint=%s, difficulty=%d)",
                game_idx + 1, n_games, checkpoint_name, difficulty,
            )

            result = self._run_single_game(
                model, game_id, checkpoint_name, difficulty, all_action_probs,
            )
            total_reward += result["reward"]
            total_duration += result["duration"]
            if result["outcome"] == "win":
                wins += 1
            else:
                losses += 1

        games_played = wins + losses
        avg_reward = total_reward / games_played if games_played > 0 else 0.0
        avg_duration = total_duration / games_played if games_played > 0 else 0.0
        win_rate = wins / games_played if games_played > 0 else 0.0

        # Compute average action distribution across all games
        action_dist = self._average_action_probs(all_action_probs)

        return EvalResult(
            checkpoint=checkpoint_name,
            games_played=games_played,
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            avg_reward=avg_reward,
            avg_duration=avg_duration,
            difficulty=difficulty,
            action_distribution=action_dist,
        )

    def compare(
        self,
        checkpoint_a: str,
        checkpoint_b: str,
        n_games: int,
        difficulty: int,
    ) -> ComparisonResult:
        """Evaluate both checkpoints and compare their performance.

        Args:
            checkpoint_a: First checkpoint name.
            checkpoint_b: Second checkpoint name.
            n_games: Number of games per checkpoint.
            difficulty: SC2 AI difficulty level.

        Returns:
            ComparisonResult with winner determination.
        """
        result_a = self.evaluate(checkpoint_a, n_games, difficulty)
        result_b = self.evaluate(checkpoint_b, n_games, difficulty)

        delta = result_a.win_rate - result_b.win_rate
        # Simple significance: >5% better with >=10 games total
        total_games = result_a.games_played + result_b.games_played
        significant = abs(delta) > 0.05 and total_games >= 10

        if delta > 0:
            winner = checkpoint_a
        elif delta < 0:
            winner = checkpoint_b
        else:
            winner = "tie"

        return ComparisonResult(
            a=result_a,
            b=result_b,
            winner=winner,
            win_rate_delta=delta,
            significant=significant,
        )

    def submit_job(
        self,
        checkpoint: str,
        n_games: int,
        difficulty: int,
    ) -> str:
        """Create a pending evaluation job and return its ID.

        The actual execution must be triggered separately (e.g., in a background
        thread via ``run_job``).
        """
        job_id = uuid.uuid4().hex[:12]
        job = EvalJob(
            job_id=job_id,
            status="pending",
            checkpoint=checkpoint,
            n_games=n_games,
            difficulty=difficulty,
        )
        self._jobs[job_id] = job
        return job_id

    def run_job(self, job_id: str) -> None:
        """Execute an evaluation job (blocking). Call from a background thread."""
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.status = "running"
        try:
            result = self.evaluate(job.checkpoint, job.n_games, job.difficulty)
            job.result = result
            job.games_completed = result.games_played
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            _log.exception("Evaluation job %s failed", job_id)

    def get_job(self, job_id: str) -> EvalJob | None:
        """Get the status of an evaluation job."""
        return self._jobs.get(job_id)

    def _load_model(self, checkpoint_name: str) -> Any:
        """Load a model checkpoint. Separated for testability."""
        from alpha4gate.learning.checkpoints import load_checkpoint

        return load_checkpoint(self._checkpoint_dir, checkpoint_name)

    def _create_env(
        self,
        game_id: str,
        checkpoint_name: str,
        difficulty: int,
    ) -> Any:
        """Create an SC2Env for evaluation. Separated for testability."""
        from alpha4gate.learning.environment import SC2Env
        from alpha4gate.learning.rewards import RewardCalculator

        reward_calc = RewardCalculator(
            log_dir=self._settings.data_dir / "reward_logs",
        )
        reward_calc.open_game_log(game_id)

        return SC2Env(
            map_name="Simple64",
            difficulty=difficulty,
            reward_calculator=reward_calc,
            db=self._db,
            game_id=game_id,
            model_version=checkpoint_name,
        )

    def _run_single_game(
        self,
        model: Any,
        game_id: str,
        checkpoint_name: str,
        difficulty: int,
        all_action_probs: list[list[float]],
    ) -> dict[str, Any]:
        """Run a single inference-only game and return outcome + stats.

        Returns:
            Dict with keys: outcome ("win"/"loss"), reward, duration.
        """
        env = self._create_env(game_id, checkpoint_name, difficulty)

        total_reward = 0.0
        duration = 0.0
        try:
            obs, _info = env.reset()
            done = False
            info: dict[str, Any] = {}
            while not done:
                action, _states = model.predict(obs, deterministic=True)
                obs, reward, done, _truncated, info = env.step(int(action))
                total_reward += reward

                # Collect action probabilities if available
                action_probs = info.get("action_probs")
                if action_probs is not None:
                    all_action_probs.append(action_probs)

            duration = info.get("game_time", 0.0)
        except Exception:
            _log.exception("Eval game %s crashed", game_id)
        finally:
            env.close()

        outcome = self._get_game_result(game_id)

        return {
            "outcome": outcome,
            "reward": total_reward,
            "duration": duration,
        }

    def _get_game_result(self, game_id: str) -> str:
        """Look up a specific game's result from the database."""
        row = self._db._conn.execute(
            "SELECT result FROM games WHERE game_id = ?", (game_id,)
        ).fetchone()
        if row is not None:
            return str(row[0])
        return "loss"  # default if game crashed before recording

    @staticmethod
    def _average_action_probs(
        all_probs: list[list[float]],
    ) -> list[float] | None:
        """Average action probability vectors across all steps."""
        if not all_probs:
            return None
        n = len(all_probs)
        dim = len(all_probs[0])
        sums = [0.0] * dim
        for probs in all_probs:
            for i, p in enumerate(probs):
                if i < dim:
                    sums[i] += p
        return [s / n for s in sums]
