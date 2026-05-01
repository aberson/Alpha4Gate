"""Model evaluator: run inference-only games and collect win rate + stats."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bots.v8.config import Settings
from bots.v8.learning.database import TrainingDB

_log = logging.getLogger(__name__)


@dataclass
class EvalResult:
    """Result of evaluating a checkpoint over N games.

    ``games_played`` counts ONLY valid games that produced a real outcome
    (wins + losses). Games whose inference loop raised or that completed
    without a result row in the DB are tracked separately in ``crashed``
    and are EXCLUDED from ``games_played``, ``win_rate``, ``avg_reward``,
    and ``avg_duration``. See Phase 4.5 blocker #67: crashed games used to
    be silently counted as losses, which corrupted promotion decisions.
    """

    checkpoint: str
    games_played: int
    wins: int
    losses: int
    crashed: int
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
    status: str  # "pending", "running", "completed", "failed", "cancelled"
    checkpoint: str
    n_games: int
    difficulty: int
    result: EvalResult | None = None
    error: str | None = None
    games_completed: int = 0
    cancel_requested: bool = False


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
        job_id: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> EvalResult:
        """Run N evaluation games with inference only and return aggregated stats.

        Args:
            checkpoint_name: Name of the checkpoint to load (e.g., "v5").
            n_games: Number of games to play.
            difficulty: SC2 AI difficulty level (1-10).
            job_id: Optional job id. When provided, the loop checks the job's
                ``cancel_requested`` flag between games and returns a partial
                EvalResult covering only games completed before the cancel.
            cancel_check: Optional callable returning True to signal cancel.
                Checked between games alongside the job_id flag; either signal
                breaks the loop. The daemon passes ``self._stop_event.is_set``
                so that ``POST /api/training/stop`` also halts in-flight
                promotion evals, not just training runs.

        Returns:
            EvalResult with win rate and statistics.
        """
        model = self._load_model(checkpoint_name)

        total_reward = 0.0
        total_duration = 0.0
        wins = 0
        losses = 0
        crashed = 0
        all_action_probs: list[list[float]] = []

        for game_idx in range(n_games):
            if job_id is not None:
                job = self._jobs.get(job_id)
                if job is not None and job.cancel_requested:
                    _log.info(
                        "Eval job %s cancelled before game %d/%d",
                        job_id,
                        game_idx + 1,
                        n_games,
                    )
                    break
            if cancel_check is not None and cancel_check():
                _log.info(
                    "Eval cancelled by cancel_check before game %d/%d "
                    "(checkpoint=%s)",
                    game_idx + 1,
                    n_games,
                    checkpoint_name,
                )
                break

            game_id = f"eval_{checkpoint_name}_{uuid.uuid4().hex[:8]}"
            _log.info(
                "Eval game %d/%d (checkpoint=%s, difficulty=%d)",
                game_idx + 1,
                n_games,
                checkpoint_name,
                difficulty,
            )

            result = self._run_single_game(
                model,
                game_id,
                checkpoint_name,
                difficulty,
                all_action_probs,
            )
            if result["outcome"] == "crashed":
                # Do NOT count crashed games' reward or duration in the
                # averages -- the partial reward from a crashed game is not
                # comparable to a full game's reward, and including it
                # skews the average. Crashes are surfaced via the separate
                # ``crashed`` counter and the promotion gate refuses to
                # promote when ``crashed > 0``.
                crashed += 1
                continue
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

        if crashed > 0:
            _log.warning(
                "Eval of %s on difficulty %d completed with %d/%d crashed "
                "games (%d valid). win_rate %.3f computed over valid games "
                "only.",
                checkpoint_name,
                difficulty,
                crashed,
                n_games,
                games_played,
                win_rate,
            )

        # Compute average action distribution across all games
        action_dist = self._average_action_probs(all_action_probs)

        return EvalResult(
            checkpoint=checkpoint_name,
            games_played=games_played,
            wins=wins,
            losses=losses,
            crashed=crashed,
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
            result = self.evaluate(
                job.checkpoint, job.n_games, job.difficulty, job_id=job_id
            )
            job.result = result
            job.games_completed = result.games_played
            job.status = "cancelled" if job.cancel_requested else "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            _log.exception("Evaluation job %s failed", job_id)

    def cancel_job(self, job_id: str) -> str:
        """Request cancellation of an eval job.

        The current in-flight game finishes (SC2 games can't be interrupted
        safely), then the loop exits and ``run_job`` marks the job
        ``cancelled``.

        Returns:
            One of: ``"cancellation_requested"`` (job was pending/running and
            now has the flag set), ``"already_completed"`` (terminal status),
            ``"not_found"`` (no such job).
        """
        job = self._jobs.get(job_id)
        if job is None:
            return "not_found"
        if job.status in ("completed", "failed", "cancelled"):
            return "already_completed"
        job.cancel_requested = True
        return "cancellation_requested"

    def get_job(self, job_id: str) -> EvalJob | None:
        """Get the status of an evaluation job."""
        return self._jobs.get(job_id)

    def _load_model(self, checkpoint_name: str) -> Any:
        """Load a model checkpoint. Separated for testability."""
        from bots.v8.learning.checkpoints import load_checkpoint

        return load_checkpoint(self._checkpoint_dir, checkpoint_name)

    def _create_env(
        self,
        game_id: str,
        checkpoint_name: str,
        difficulty: int,
    ) -> Any:
        """Create an SC2Env for evaluation. Separated for testability."""
        from bots.v8.learning.environment import SC2Env
        from bots.v8.learning.rewards import RewardCalculator

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
            Dict with keys: outcome ("win" | "loss" | "crashed"), reward,
            duration. The "crashed" outcome means the inference loop raised
            OR the game completed but no result row was recorded -- either
            way, the game's outcome is unknown and must NOT be silently
            counted as a loss by the caller. See Phase 4.5 blocker #67.
        """
        env = self._create_env(game_id, checkpoint_name, difficulty)

        total_reward = 0.0
        duration = 0.0
        crashed = False
        # Collect into a LOCAL list. We only merge into the shared
        # ``all_action_probs`` after we know the game completed cleanly
        # AND a result row was recorded -- otherwise we would poison the
        # eval's action_distribution with partial data from crashed
        # games. See Phase 4.5 blocker #67.
        local_action_probs: list[list[float]] = []
        # Phase 4.7 Step 1 (#82): ``SC2Env.reset()`` regenerates
        # ``_game_id`` by appending a per-reset uuid suffix (Phase 4.6
        # #75 collision protection). The id the env actually writes to
        # ``games.game_id`` is therefore NOT the base ``game_id`` this
        # method was called with — we MUST read ``env.game_id`` after
        # ``reset()`` succeeds and use that for the DB lookup, or
        # ``_get_game_result`` will miss every single eval row and
        # flag all eval games as "crashed" (soak-2026-04-11b).
        # We seed ``actual_id`` with the base id so the exception log
        # below still has SOMETHING to print if ``reset()`` itself
        # raises before we can re-read the env's post-reset id.
        actual_id = game_id
        try:
            obs, _info = env.reset()
            actual_id = env.game_id
            done = False
            info: dict[str, Any] = {}
            while not done:
                action, _states = model.predict(obs, deterministic=True)
                obs, reward, done, _truncated, info = env.step(int(action))
                total_reward += reward

                # Collect action probabilities if available
                action_probs = info.get("action_probs")
                if action_probs is not None:
                    local_action_probs.append(action_probs)

            duration = info.get("game_time", 0.0)
        except Exception:
            _log.exception("Eval game %s crashed", actual_id)
            crashed = True
        finally:
            env.close()

        if crashed:
            return {
                "outcome": "crashed",
                "reward": total_reward,
                "duration": duration,
            }

        outcome = self._get_game_result(actual_id)
        if outcome is None:
            # The game completed without raising, but no result row landed
            # in the DB. Treat this as crashed too -- we have no truthful
            # outcome to report and the silent-loss default is exactly what
            # #67 forbids.
            _log.error(
                "Eval game %s completed but no result row was recorded; treating as crashed",
                actual_id,
            )
            return {
                "outcome": "crashed",
                "reward": total_reward,
                "duration": duration,
            }

        # Game completed cleanly with a recorded result -- safe to merge
        # the collected action probs into the shared list.
        all_action_probs.extend(local_action_probs)
        return {
            "outcome": outcome,
            "reward": total_reward,
            "duration": duration,
        }

    def _get_game_result(self, game_id: str) -> str | None:
        """Look up a specific game's result from the database.

        Returns None if the game has no result row (caller must handle).
        Delegates to ``TrainingDB.get_game_result`` so the underlying
        SQLite access is properly locked -- see Phase 4.5 blocker #66.
        """
        return self._db.get_game_result(game_id)

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
