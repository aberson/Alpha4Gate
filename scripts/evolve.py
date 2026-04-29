"""CLI + orchestration loop for the evolve skill (generation-phase algorithm).

Per generation the script:

1. **Fitness phase** — every active imp is individually snapshotted + applied
   and plays the parent for ``--games-per-eval`` games. Buckets:
   ``>= games//2 + 1`` wins → ``fitness-pass``; one win short of majority
   → ``fitness-close`` (resurrection-eligible); anything lower → ``evicted``.
2. **Stack-apply + promote** — if ≥1 fitness-pass imp, snapshot the parent
   into a fresh ``vN+1`` directory, apply every fitness-pass imp in rank
   order, run a ``python -c "import bots.vN+1.bot"`` gate, and (on pass)
   commit the promotion as one ``[evo-auto]`` commit. Import-check failures
   roll back the snapshot and skip regression.
3. **Regression phase** — if anything was promoted, play the new parent
   vs the prior parent. On rollback, ``git revert`` the promote commit
   (also under ``EVO_AUTO=1``) and restore the pointer.
4. **Pool refresh** — end-of-generation bookkeeping: close-loss and
   benched-pass imps get ``retry_count += 1``; any at ``retry_count >= 3``
   evict, the rest flip back to ``active``. Claude is asked for enough
   new imps to top the active pool up to ``--pool-size``.

The composition phase (a third Bernoulli filter that tested the stacked
candidate against the parent before promotion) was removed 2026-04-23;
see ``documentation/plans/evolve-gate-reduction-plan.md``. Regression
already catches bad-interaction stacks, so the composition gate added
cost without adding unique detection capability.

Usage::

    # Overnight run with defaults (pool=10, 5 games per eval, 4h budget):
    python scripts/evolve.py

    # Short dev run (no commits, tiny pool):
    python scripts/evolve.py --hours 0 --pool-size 2 --no-commit
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from orchestrator.contracts import SelfPlayRecord
    from orchestrator.evolve import (
        FitnessResult,
        Improvement,
        RegressionResult,
    )

# Ensure repo root is on sys.path so ``orchestrator`` is importable when the
# script is invoked directly (``python scripts/evolve.py``). The
# ``orchestrator.evolve`` import below is deferred past this ``sys.path``
# setup (hence the E402 waivers).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from orchestrator.evolve import (  # noqa: E402
    _restore_pointer as _primitive_restore_pointer,
)
from orchestrator.paths import resolve_sc2_path  # noqa: E402

_log = logging.getLogger("evolve")


# Per-imp status vocabulary (also used by the dashboard). See
# documentation/investigations/evolve-algorithm-redesign-investigation.md
# and documentation/plans/evolve-gate-reduction-plan.md for the post-
# gate-reduction vocabulary — the two legacy promotion statuses were
# collapsed into a single ``promoted`` status.
PoolItemStatus = str
_ACTIVE: PoolItemStatus = "active"
_FITNESS_PASS: PoolItemStatus = "fitness-pass"
_FITNESS_CLOSE: PoolItemStatus = "fitness-close"
_EVICTED: PoolItemStatus = "evicted"
_PROMOTED: PoolItemStatus = "promoted"
_REGRESSION_ROLLBACK: PoolItemStatus = "regression-rollback"

# Upper bound on total fitness evaluations per imp (original + 2 retries).
_RETRY_CAP = 3


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python scripts/evolve.py",
        description=(
            "Alpha4Gate evolve overnight runner (generation-phase algorithm). "
            "Generates a pool of improvements, fitness-tests each vs the "
            "parent, promotes the winning stack, and regression-checks."
        ),
    )
    parser.add_argument(
        "--pool-size",
        type=int,
        default=10,
        help="Number of improvements Claude generates (default: 10)",
    )
    parser.add_argument(
        "--games-per-eval",
        type=int,
        default=5,
        help=(
            "Games in each phase evaluation (fitness / regression). "
            "Default: 5. Threshold for pass = strict majority "
            "(>= games//2 + 1); close-loss = one win short of majority; "
            "anything else = fail."
        ),
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=4.0,
        help=(
            "Wall-clock budget in hours (default: 4.0). "
            "0 disables the wall-clock check (useful for test runs)."
        ),
    )
    parser.add_argument(
        "--map",
        default="Simple64",
        help="SC2 map name (default: Simple64)",
    )
    parser.add_argument(
        "--game-time-limit",
        type=int,
        default=1800,
        help=(
            "SC2 in-game time limit per game, in seconds (default: 1800 = "
            "30 min)."
        ),
    )
    parser.add_argument(
        "--hard-timeout",
        type=float,
        default=2700.0,
        help=(
            "Wall-clock timeout per game in seconds (default: 2700 = 45 "
            "min). Must be >= game-time-limit plus buffer."
        ),
    )
    parser.add_argument(
        "--no-commit",
        action="store_true",
        help="Skip the auto-commit on promote (dev / test use).",
    )
    parser.add_argument(
        "--results-path",
        type=Path,
        default=_REPO_ROOT / "data" / "evolve_results.jsonl",
        help="JSONL log of every phase outcome (default: data/evolve_results.jsonl)",
    )
    parser.add_argument(
        "--pool-path",
        type=Path,
        default=_REPO_ROOT / "data" / "evolve_pool.json",
        help="Pool state file (default: data/evolve_pool.json)",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=_REPO_ROOT / "data" / "evolve_run_state.json",
        help=(
            "Dashboard-reading run state file "
            "(default: data/evolve_run_state.json)"
        ),
    )
    parser.add_argument(
        "--current-round-path",
        type=Path,
        default=_REPO_ROOT / "data" / "evolve_current_round.json",
        help=(
            "Live per-game progress file (default: data/evolve_current_round.json)."
        ),
    )
    parser.add_argument(
        "--crash-log-path",
        type=Path,
        default=_REPO_ROOT / "data" / "evolve_crashes.jsonl",
        help=(
            "JSONL log of crashed phases with full tracebacks "
            "(default: data/evolve_crashes.jsonl)."
        ),
    )
    parser.add_argument(
        "--run-log",
        type=Path,
        default=None,
        help=(
            "Human-readable markdown run log "
            "(default: documentation/soak-test-runs/evolve-<ts>.md)"
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip initial pool generation and reload pool + per-item statuses "
            "from --pool-path. The on-disk parent must equal current_version()."
        ),
    )
    parser.add_argument(
        "--post-training-cycles",
        type=int,
        default=0,
        help=(
            "If a run completes with at least one promotion, start the "
            "training daemon on the newly-promoted parent for exactly N "
            "cycles (bounded via DaemonConfig.max_runs). Default 0 = disabled."
        ),
    )
    parser.add_argument(
        "--backend-url",
        default="http://localhost:8765",
        help=(
            "Base URL of the Alpha4Gate backend API. Used by the "
            "--post-training-cycles hook. Default: http://localhost:8765."
        ),
    )
    return parser


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------


def check_git_clean(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[bool, list[str]]:
    """Return ``(is_clean, dirty_paths)``."""
    try:
        result = run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(_REPO_ROOT),
        )
    except (FileNotFoundError, OSError) as exc:
        _log.warning("git status failed (%s); assuming clean", exc)
        return True, []

    if result.returncode != 0:
        _log.warning(
            "git status returned %d: %s", result.returncode, result.stderr
        )
        return True, []

    dirty = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if dirty:
        _log.warning(
            "Working tree is dirty (%d paths). Evolve will still run but "
            "any commits from other sources will interleave with promote "
            "commits:",
            len(dirty),
        )
        for line in dirty[:20]:
            _log.warning("  %s", line)
        if len(dirty) > 20:
            _log.warning("  ... and %d more", len(dirty) - 20)
    return not dirty, dirty


def check_sc2_installed() -> bool:
    """Return True iff the SC2 install dir is present."""
    sc2_path = resolve_sc2_path()
    if sc2_path.is_dir():
        return True
    _log.error(
        "SC2 install not found at %s. Set SC2PATH or install StarCraft II.",
        sc2_path,
    )
    return False


def _restore_current_pointer(parent_name: str) -> None:
    """Write ``bots/current/current.txt`` to *parent_name*.

    Thin script-side wrapper around ``orchestrator.evolve._restore_pointer``
    so the atomic-replace retry loop lives in exactly one place. Used by
    the regression-rollback path when ``git revert`` is skipped or fails —
    in those cases the primitive deliberately leaves the pointer untouched
    so this caller can order operations correctly.
    """
    _primitive_restore_pointer(parent_name)


def check_no_phantom_promote(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[bool, str | None, str | None]:
    """Return ``(ok, head_version, disk_version)``.

    A "phantom promote" state is one where ``bots/current/current.txt``
    on disk differs from ``git show HEAD:bots/current/current.txt``. This
    happens when a prior run rolled back a promote on disk but failed to
    revert the promote commit in git — the scenario from run
    20260422-0824 that motivated the rollback-order fix. Starting a new
    evolve run from such a state is unsafe: the primitives trust the
    filesystem pointer but the working tree is dirty relative to HEAD.

    ``ok=True`` means both values match (or the pointer file is missing,
    which is handled by other pre-flight checks). ``ok=False`` means the
    caller must bail with a recovery message naming both values.
    """
    pointer = _REPO_ROOT / "bots" / "current" / "current.txt"
    if not pointer.is_file():
        # No pointer file on disk; other pre-flight steps will surface this.
        return True, None, None

    disk_version = pointer.read_text(encoding="utf-8").strip()

    try:
        result = run(
            ["git", "show", "HEAD:bots/current/current.txt"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(_REPO_ROOT),
            encoding="utf-8",
        )
    except (FileNotFoundError, OSError) as exc:
        _log.warning(
            "git show HEAD:bots/current/current.txt failed (%s); "
            "skipping phantom-promote check",
            exc,
        )
        return True, None, disk_version

    if result.returncode != 0:
        _log.warning(
            "git show HEAD:bots/current/current.txt returned %d: %s; "
            "skipping phantom-promote check",
            result.returncode,
            (result.stderr or "").strip(),
        )
        return True, None, disk_version

    head_version = result.stdout.strip()
    if head_version != disk_version:
        return False, head_version, disk_version
    return True, head_version, disk_version


# ---------------------------------------------------------------------------
# Per-item state + serialisation
# ---------------------------------------------------------------------------


@dataclass
class PerItemState:
    """End-of-generation status payload stored alongside each pool item."""

    status: PoolItemStatus = _ACTIVE
    fitness_score: list[int] | None = None
    retry_count: int = 0
    first_evaluated_against: str | None = None
    last_evaluated_against: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "fitness_score": self.fitness_score,
            "retry_count": self.retry_count,
            "first_evaluated_against": self.first_evaluated_against,
            "last_evaluated_against": self.last_evaluated_against,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PerItemState:
        return cls(
            status=data.get("status", _ACTIVE),
            fitness_score=data.get("fitness_score"),
            retry_count=int(data.get("retry_count") or 0),
            first_evaluated_against=data.get("first_evaluated_against"),
            last_evaluated_against=data.get("last_evaluated_against"),
        )


def _imp_asdict(imp: Improvement) -> dict[str, Any]:
    """Return a JSON-serialisable dict for an Improvement."""
    return dataclasses.asdict(imp)


def _record_asdict(rec: SelfPlayRecord) -> dict[str, Any]:
    return dataclasses.asdict(rec)


def _now_iso() -> str:
    """Return an ISO-8601 UTC timestamp (seconds resolution)."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


_ATOMIC_REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write *payload* as pretty-printed, sorted JSON, atomically.

    On Windows, ``os.replace`` fails with ``PermissionError`` if any process
    holds an open handle on the target — which happens whenever the backend
    ``--serve`` is polling these state files. Retry a few times with backoff
    before giving up; the races are short-lived.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for delay in _ATOMIC_REPLACE_RETRY_DELAYS:
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(delay)
    tmp.replace(path)


def write_pool_state(
    pool_path: Path,
    pool: list[Improvement],
    *,
    parent: str,
    per_item_state: dict[int, PerItemState] | None = None,
    generated_at: str | None = None,
    generation: int = 0,
) -> None:
    """Write ``evolve_pool.json`` with per-item status + retry bookkeeping."""
    per_item_state = per_item_state or {}
    items: list[dict[str, Any]] = []
    for i, imp in enumerate(pool):
        entry = _imp_asdict(imp)
        st = per_item_state.get(i, PerItemState())
        entry.update(st.to_json())
        items.append(entry)
    payload: dict[str, Any] = {
        "generated_at": generated_at or _now_iso(),
        "parent": parent,
        "generation": generation,
        "pool": items,
    }
    _atomic_write_json(pool_path, payload)


def load_pool_state(
    pool_path: Path,
) -> tuple[list[Improvement], dict[int, PerItemState], str, str, int]:
    """Reload a pool file written by :func:`write_pool_state`.

    Returns ``(pool, per_item_state, parent, generated_at, generation)``.
    """
    from orchestrator.evolve import Improvement as _Improvement

    payload = json.loads(pool_path.read_text(encoding="utf-8"))
    items = payload["pool"]
    pool: list[_Improvement] = []
    per_item_state: dict[int, PerItemState] = {}
    for i, entry in enumerate(items):
        state_fields = {
            k: entry.pop(k, None)
            for k in (
                "status",
                "fitness_score",
                "retry_count",
                "first_evaluated_against",
                "last_evaluated_against",
            )
        }
        # Back-compat: old pool files without these fields get defaults.
        state_fields = {k: v for k, v in state_fields.items() if v is not None}
        per_item_state[i] = PerItemState.from_json(state_fields)
        # Improvement.files_touched is optional — default empty list if absent.
        entry.setdefault("files_touched", [])
        pool.append(_Improvement(**entry))
    return (
        pool,
        per_item_state,
        payload["parent"],
        payload.get("generated_at") or _now_iso(),
        int(payload.get("generation") or 0),
    )


# ---------------------------------------------------------------------------
# Phase-result rows (appended to evolve_results.jsonl)
# ---------------------------------------------------------------------------


PhaseOutcome = Literal[
    "fitness-pass",
    "fitness-close",
    "fitness-fail",
    "stack-apply-pass",
    "stack-apply-import-fail",
    "stack-apply-commit-fail",
    "regression-pass",
    "regression-rollback",
    "crash",
]


def _fitness_row(
    generation: int,
    parent: str,
    result: FitnessResult,
) -> dict[str, Any]:
    outcome_map: dict[str, PhaseOutcome] = {
        "pass": "fitness-pass",
        "close": "fitness-close",
        "fail": "fitness-fail",
    }
    return {
        "phase": "fitness",
        "generation": generation,
        "parent": parent,
        "imp": _imp_asdict(result.imp),
        "candidate": result.candidate,
        "record": [_record_asdict(r) for r in result.record],
        "wins_cand": result.wins_candidate,
        "wins_parent": result.wins_parent,
        "games": result.games,
        "outcome": outcome_map[result.bucket],
        "reason": result.reason,
    }


def _stack_apply_row(
    generation: int,
    parent: str,
    new_version: str,
    stacked_imps: list[Improvement],
    *,
    outcome: PhaseOutcome,
    reason: str,
) -> dict[str, Any]:
    """Build a results-row for the stack-apply step.

    ``outcome`` is ``stack-apply-pass`` when the import check succeeded,
    the snapshot was promoted to *new_version*, and the promote commit
    landed (if ``--no-commit`` was not set); ``stack-apply-import-fail``
    when the import check failed and the snapshot was rolled back; or
    ``stack-apply-commit-fail`` when import passed but the git commit
    step failed and the snapshot was rolled back.
    """
    return {
        "phase": "stack_apply",
        "generation": generation,
        "parent": parent,
        "new_version": new_version,
        "stacked_imps": [_imp_asdict(imp) for imp in stacked_imps],
        "stacked_titles": [imp.title for imp in stacked_imps],
        "outcome": outcome,
        "reason": reason,
    }


def _regression_row(
    generation: int,
    result: RegressionResult,
) -> dict[str, Any]:
    return {
        "phase": "regression",
        "generation": generation,
        "new_parent": result.new_parent,
        "prior_parent": result.prior_parent,
        "record": [_record_asdict(r) for r in result.record],
        "wins_new": result.wins_new,
        "wins_prior": result.wins_prior,
        "games": result.games,
        "rolled_back": result.rolled_back,
        "outcome": (
            "regression-rollback" if result.rolled_back else "regression-pass"
        ),
        "reason": result.reason,
    }


def _crash_row(
    generation: int,
    phase: str,
    parent: str,
    imp: Improvement | None,
    exc: BaseException,
    traceback_str: str,
) -> dict[str, Any]:
    """Build a phase-row for a crashed primitive call."""
    return {
        "phase": phase,
        "generation": generation,
        "parent": parent,
        "imp": _imp_asdict(imp) if imp is not None else None,
        "outcome": "crash",
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "error": (traceback_str.splitlines() or [str(exc)])[-1],
        "reason": f"crashed: {type(exc).__name__}: {exc}",
    }


def append_phase_result(results_path: Path, row: dict[str, Any]) -> None:
    """Append one row to ``evolve_results.jsonl``."""
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def append_crash_log(
    crash_log_path: Path,
    *,
    generation: int,
    phase: str,
    parent: str,
    imp: Improvement | None,
    exc: BaseException,
    traceback_str: str,
) -> None:
    """Append a full-traceback JSON line to ``data/evolve_crashes.jsonl``."""
    entry: dict[str, Any] = {
        "timestamp": _now_iso(),
        "generation": generation,
        "phase": phase,
        "parent": parent,
        "imp_title": imp.title if imp is not None else None,
        "imp_type": imp.type if imp is not None else None,
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "traceback": traceback_str,
    }
    crash_log_path.parent.mkdir(parents=True, exist_ok=True)
    with crash_log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Current-round JSON (live per-game progress)
# ---------------------------------------------------------------------------


@dataclass
class CurrentRoundPayload:
    """Mutable payload used to update ``evolve_current_round.json``.

    One instance per generation, updated in place as phases progress. The
    ``to_dict`` output is the exact shape read by
    :meth:`frontend/src/components/EvolutionTab.tsx` — keep in lock-step.
    """

    generation: int = 0
    # Phases: starting / mirror_games / claude_prompt / fitness /
    # stack_apply / regression / pool_refresh.
    phase: str = "starting"
    imp_title: str | None = None
    imp_rank: int | None = None
    imp_index: int | None = None
    candidate: str | None = None
    stacked_titles: list[str] = field(default_factory=list)
    new_parent: str | None = None
    prior_parent: str | None = None
    games_played: int = 0
    games_total: int = 0
    score_cand: int = 0
    score_parent: int = 0

    def reset_progress(self, total: int) -> None:
        self.games_played = 0
        self.games_total = total
        self.score_cand = 0
        self.score_parent = 0

    def to_dict(self, *, active: bool = True) -> dict[str, Any]:
        return {
            "active": active,
            "generation": self.generation,
            "phase": self.phase,
            "imp_title": self.imp_title,
            "imp_rank": self.imp_rank,
            "imp_index": self.imp_index,
            "candidate": self.candidate,
            "stacked_titles": list(self.stacked_titles),
            "new_parent": self.new_parent,
            "prior_parent": self.prior_parent,
            "games_played": self.games_played,
            "games_total": self.games_total,
            "score_cand": self.score_cand,
            "score_parent": self.score_parent,
            "updated_at": _now_iso(),
        }


def write_current_round_state(path: Path, payload: CurrentRoundPayload) -> None:
    """Write the live per-game progress file (active=True)."""
    _atomic_write_json(path, payload.to_dict(active=True))


def clear_current_round_state(path: Path) -> None:
    """Mark the current-round file inactive between phases."""
    _atomic_write_json(
        path, {"active": False, "updated_at": _now_iso()}
    )


# ---------------------------------------------------------------------------
# Run state (dashboard-facing)
# ---------------------------------------------------------------------------


def _last_result_snapshot_fitness(
    generation: int, result: FitnessResult
) -> dict[str, Any]:
    outcome_map = {
        "pass": "fitness-pass",
        "close": "fitness-close",
        "fail": "fitness-fail",
    }
    return {
        "generation_index": generation,
        "phase": "fitness",
        "imp_title": result.imp.title,
        "stacked_titles": None,
        "score": [result.wins_candidate, result.games],
        "outcome": outcome_map[result.bucket],
        "reason": result.reason,
    }


def _last_result_snapshot_stack_apply(
    generation: int,
    new_version: str,
    stacked_imps: list[Improvement],
    *,
    outcome: PhaseOutcome,
    reason: str,
) -> dict[str, Any]:
    return {
        "generation_index": generation,
        "phase": "stack_apply",
        "imp_title": None,
        "stacked_titles": [imp.title for imp in stacked_imps],
        "new_version": new_version,
        "score": [0, 0],
        "outcome": outcome,
        "reason": reason,
    }


def _last_result_snapshot_regression(
    generation: int, result: RegressionResult
) -> dict[str, Any]:
    return {
        "generation_index": generation,
        "phase": "regression",
        "imp_title": None,
        "stacked_titles": None,
        "score": [result.wins_new, result.games],
        "outcome": (
            "regression-rollback" if result.rolled_back else "regression-pass"
        ),
        "reason": result.reason,
    }


def _last_result_snapshot_crash(
    generation: int,
    phase: str,
    imp: Improvement | None,
    exc: BaseException,
) -> dict[str, Any]:
    return {
        "generation_index": generation,
        "phase": phase,
        "imp_title": imp.title if imp is not None else None,
        "stacked_titles": None,
        "score": [0, 0],
        "outcome": "crash",
        "reason": f"crashed: {type(exc).__name__}: {exc}",
    }


def write_run_state(
    state_path: Path,
    *,
    status: str,
    parent_start: str,
    parent_current: str,
    started_at: str,
    wall_budget_hours: float,
    generations_completed: int,
    generations_promoted: int,
    evictions: int,
    resurrections_remaining: int,
    pool_remaining_count: int,
    last_result: dict[str, Any] | None,
    generation_index: int = 0,
) -> None:
    """Write ``evolve_run_state.json`` — dashboard run state."""
    payload: dict[str, Any] = {
        "status": status,
        "parent_start": parent_start,
        "parent_current": parent_current,
        "started_at": started_at,
        "wall_budget_hours": wall_budget_hours,
        "generation_index": generation_index,
        "generations_completed": generations_completed,
        "generations_promoted": generations_promoted,
        "evictions": evictions,
        "resurrections_remaining": resurrections_remaining,
        "pool_remaining_count": pool_remaining_count,
        "last_result": last_result,
    }
    _atomic_write_json(state_path, payload)


def write_run_log(
    run_log_path: Path,
    *,
    started_at: str,
    finished_at: str,
    parent_start: str,
    parent_current: str,
    wall_budget_hours: float,
    generations_completed: int,
    generations_promoted: int,
    evictions: int,
    stop_reason: str,
    generation_entries: list[dict[str, Any]],
) -> None:
    """Write a human-readable markdown summary of the full run."""
    run_log_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        f"# Evolve run — {started_at}",
        "",
        f"- Parent (start): `{parent_start}`",
        f"- Parent (end): `{parent_current}`",
        f"- Wall-clock budget: {wall_budget_hours}h",
        f"- Started: {started_at}",
        f"- Finished: {finished_at}",
        f"- Generations completed: {generations_completed}",
        f"- Generations promoted: {generations_promoted}",
        f"- Total evictions: {evictions}",
        f"- Stop reason: {stop_reason}",
        "",
        "## Generations",
        "",
    ]
    if not generation_entries:
        lines.append("(no generations completed)")
    else:
        lines.append(
            "| gen | fitness pass/close/fail | stack-apply | regression | outcome |"
        )
        lines.append("|---|---|---|---|---|")
        for entry in generation_entries:
            lines.append(
                f"| {entry['generation']} "
                f"| {entry['fitness_counts']} "
                f"| {entry.get('stack_outcome', '—')} "
                f"| {entry.get('regression_outcome', '—')} "
                f"| {entry.get('summary', '—')} |"
            )
    run_log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Commit / revert (EVO_AUTO=1)
# ---------------------------------------------------------------------------


def _git_env_evo_auto() -> dict[str, str]:
    env = dict(os.environ)
    env["EVO_AUTO"] = "1"
    env.pop("ADVISED_AUTO", None)
    return env


def git_commit_evo_auto(
    new_version: str,
    generation: int,
    stacked_titles: list[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[bool, str | None]:
    """Stage ``bots/<new_version>/`` + ``current.txt`` and commit.

    Returns ``(success, sha)``. On failure, logs WARNING and returns
    ``(False, None)`` — the caller continues the loop and the operator
    reconciles out-of-band.
    """
    env = _git_env_evo_auto()

    header = (
        f"evolve: generation {generation} promoted "
        f"stack ({len(stacked_titles)} imps)"
    )
    body_lines = [header, ""]
    for title in stacked_titles:
        body_lines.append(f"- {title}")
    body_lines.extend(["", "[evo-auto]", ""])
    msg = "\n".join(body_lines)

    try:
        add_result = run(
            ["git", "add", f"bots/{new_version}/", "bots/current/current.txt"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(_REPO_ROOT),
            env=env,
        )
    except (FileNotFoundError, OSError) as exc:
        _log.warning("git add failed for %s: %s", new_version, exc)
        return False, None
    if add_result.returncode != 0:
        _log.warning(
            "git add returned %d for bots/%s/: %s",
            add_result.returncode,
            new_version,
            add_result.stderr,
        )
        return False, None

    # --no-verify: EVO_AUTO already restricts staged paths to bots/<vN>/*
    # via check_sandbox.py, so the pre-commit hook is duplicate enforcement.
    # On WSL the hook can't run (Git-for-Windows generated /bin/sh shebang
    # over bash-array syntax + Windows-only INSTALL_PYTHON path), and
    # without skipping it every WSL evolve commit fails.
    try:
        commit_result = run(
            ["git", "commit", "--no-verify", "-m", msg],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(_REPO_ROOT),
            env=env,
        )
    except (FileNotFoundError, OSError) as exc:
        _log.warning("git commit failed for %s: %s", new_version, exc)
        return False, None
    if commit_result.returncode != 0:
        _log.warning(
            "git commit returned %d for generation %d: %s",
            commit_result.returncode,
            generation,
            commit_result.stderr,
        )
        return False, None

    # Capture the promote commit SHA so a subsequent regression rollback
    # can revert the exact commit.
    try:
        sha_result = run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(_REPO_ROOT),
            env=env,
        )
        sha = sha_result.stdout.strip() if sha_result.returncode == 0 else None
    except (FileNotFoundError, OSError):
        sha = None

    _log.info(
        "evolve: committed generation %d promote (%s; %d imps; sha=%s)",
        generation,
        new_version,
        len(stacked_titles),
        sha,
    )
    return True, sha


def _reset_staged_revert(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Clear any staged changes left behind by a failed revert commit.

    ``git revert --no-commit`` stages the reverse diff in the index; if
    the follow-up ``git commit`` then fails, the staged diff lingers and
    will leak into the NEXT generation's commit (``git_commit_evo_auto``
    does a plain ``git commit -m`` with no pathspec and no ``-a``, which
    commits everything currently staged). Run ``git reset HEAD -- .`` so
    the revert primitive cleans up its own mess on the failure path.
    Never raises — this is best-effort.
    """
    try:
        run(
            ["git", "reset", "HEAD", "--", "."],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(_REPO_ROOT),
        )
    except (FileNotFoundError, OSError) as exc:
        _log.warning(
            "git reset (post-failed-revert cleanup) failed: %s; "
            "index may still carry staged revert diff",
            exc,
        )


def git_revert_evo_auto(
    promote_sha: str,
    generation: int,
    reason: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    """Revert the promote commit at *promote_sha* under EVO_AUTO=1.

    Uses ``git revert --no-commit`` so we can provide our own commit
    message containing the ``[evo-auto]`` marker. Returns True on success;
    a failure logs WARNING and returns False (operator reconciles). On
    any failure path after ``git revert --no-commit`` has staged its
    reverse diff, ``_reset_staged_revert`` is called to drop the staged
    changes so they do not leak into the next generation's commit.
    """
    env = _git_env_evo_auto()

    try:
        revert_result = run(
            ["git", "revert", "--no-commit", promote_sha],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(_REPO_ROOT),
            env=env,
        )
    except (FileNotFoundError, OSError) as exc:
        _log.warning("git revert failed for %s: %s", promote_sha, exc)
        return False
    if revert_result.returncode != 0:
        _log.warning(
            "git revert --no-commit returned %d for %s: %s",
            revert_result.returncode,
            promote_sha,
            revert_result.stderr,
        )
        return False

    msg = (
        f"evolve: generation {generation} regression rollback\n"
        "\n"
        f"Reverts {promote_sha[:12]}. {reason}\n"
        "\n"
        "[evo-auto]\n"
    )
    # --no-verify: same rationale as git_commit_evo_auto above.
    try:
        commit_result = run(
            ["git", "commit", "--no-verify", "-m", msg],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(_REPO_ROOT),
            env=env,
        )
    except (FileNotFoundError, OSError) as exc:
        _log.warning("git commit (revert) failed for %s: %s", promote_sha, exc)
        _reset_staged_revert(run=run)
        return False
    if commit_result.returncode != 0:
        _log.warning(
            "git commit (revert) returned %d for generation %d: %s",
            commit_result.returncode,
            generation,
            commit_result.stderr,
        )
        _reset_staged_revert(run=run)
        return False
    _log.info(
        "evolve: reverted promotion %s (generation %d regression rollback)",
        promote_sha,
        generation,
    )
    return True


# ---------------------------------------------------------------------------
# Post-training hook (unchanged)
# ---------------------------------------------------------------------------


def start_post_training_daemon(
    *,
    cycles: int,
    backend_url: str,
    new_parent: str,
) -> dict[str, Any]:
    """Start the training daemon for exactly *cycles* runs on *new_parent*."""
    import httpx

    result: dict[str, Any] = {
        "new_parent": new_parent,
        "cycles": cycles,
        "backend_url": backend_url,
        "config_status": None,
        "start_status": None,
        "error": None,
    }
    try:
        cfg_resp = httpx.put(
            f"{backend_url}/api/training/daemon/config",
            json={"max_runs": cycles},
            timeout=10.0,
        )
        result["config_status"] = cfg_resp.status_code
        start_resp = httpx.post(
            f"{backend_url}/api/training/start",
            timeout=10.0,
        )
        result["start_status"] = start_resp.status_code
        if cfg_resp.status_code == 200 and start_resp.status_code == 200:
            _log.info(
                "post-training: daemon started for %d cycles on %s "
                "(config rc=%d, start rc=%d)",
                cycles,
                new_parent,
                cfg_resp.status_code,
                start_resp.status_code,
            )
        else:
            _log.warning(
                "post-training: daemon did NOT start on %s — config rc=%d, "
                "start rc=%d.",
                new_parent,
                cfg_resp.status_code,
                start_resp.status_code,
            )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        _log.warning(
            "post-training: failed to auto-start daemon on %s: %s",
            new_parent,
            exc,
        )
    return result


# ---------------------------------------------------------------------------
# Loop helpers
# ---------------------------------------------------------------------------


def _budget_exceeded(
    start_monotonic: float,
    hours: float,
    *,
    now_fn: Callable[[], float] = time.monotonic,
) -> bool:
    if hours <= 0:
        return False
    elapsed_s = now_fn() - start_monotonic
    return elapsed_s >= hours * 3600.0


def _count_active(per_item_state: dict[int, PerItemState]) -> int:
    return sum(
        1 for st in per_item_state.values() if st.status == _ACTIVE
    )


def _count_evicted(per_item_state: dict[int, PerItemState]) -> int:
    return sum(
        1 for st in per_item_state.values() if st.status == _EVICTED
    )


def _count_resurrections_remaining(per_item_state: dict[int, PerItemState]) -> int:
    """Count close-loss + benched-pass imps that still have retries left."""
    return sum(
        1
        for st in per_item_state.values()
        if st.status in {_FITNESS_CLOSE, _FITNESS_PASS}
        and st.retry_count < _RETRY_CAP
    )


def _clear_fresh_run_state(
    *,
    results_path: Path,
    pool_path: Path,
    current_round_path: Path | None,
    parent: str,
) -> None:
    """Wipe leftover per-run state files at the start of a fresh (non-resume)
    run so the dashboard shows a clean slate while pool-gen is in flight.
    """
    try:
        if results_path.exists():
            results_path.write_text("", encoding="utf-8")
    except OSError as exc:
        _log.warning(
            "evolve: failed to truncate %s on fresh run: %s", results_path, exc
        )

    _atomic_write_json(
        pool_path,
        {"parent": parent, "generated_at": _now_iso(), "generation": 0, "pool": []},
    )

    if current_round_path is not None:
        clear_current_round_state(current_round_path)


def _apply_retry_bookkeeping(
    per_item_state: dict[int, PerItemState],
) -> None:
    """End-of-generation: flip benched imps back to active (or evict at cap).

    Benched = status in {fitness-pass, fitness-close}. Both statuses mean
    "this imp survived but wasn't promoted; needs another look next gen."
    Cap is enforced BEFORE flipping so we never emit ``active`` for an imp
    that's already hit the cap.
    """
    for st in per_item_state.values():
        if st.status in {_FITNESS_PASS, _FITNESS_CLOSE}:
            if st.retry_count >= _RETRY_CAP:
                st.status = _EVICTED
            else:
                st.status = _ACTIVE


def _apply_fitness_outcome(
    per_item_state: dict[int, PerItemState],
    idx: int,
    result: FitnessResult,
) -> None:
    """Mutate state[idx] to reflect one fitness-eval outcome."""
    st = per_item_state[idx]
    st.retry_count += 1
    st.fitness_score = [result.wins_candidate, result.games]
    st.last_evaluated_against = result.parent
    if st.first_evaluated_against is None:
        st.first_evaluated_against = result.parent
    if result.bucket == "pass":
        st.status = _FITNESS_PASS
    elif result.bucket == "close":
        st.status = _FITNESS_CLOSE
    else:
        st.status = _EVICTED


# ---------------------------------------------------------------------------
# Stack-apply + promote (gate-reduction refactor, 2026-04-23)
# ---------------------------------------------------------------------------


@dataclass
class StackApplyOutcome:
    """Outcome of :func:`_stack_apply_and_promote`.

    ``promoted=True`` means a new ``vN+1`` dir now exists, the pointer
    has been flipped to it, the commit (if ``--no-commit`` was not set)
    landed as ``promote_sha``, and the caller should proceed to
    regression. ``promoted=False`` means one of:

    * the pre-promote import check failed, OR
    * the post-promote git commit step failed, OR
    * the apply step raised.

    In every ``promoted=False`` case the candidate directory has been
    removed and the pointer is back at *parent*.

    ``stacked_imps`` is always populated (input echo). ``new_version``
    and ``promote_sha`` are only meaningful when ``promoted=True``.
    """

    parent: str
    stacked_imps: list[Improvement]
    new_version: str | None
    promote_sha: str | None
    promoted: bool
    outcome: PhaseOutcome
    reason: str


def _default_import_check(
    new_version: str,
    *,
    timeout: float = 30.0,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    """Run ``python -c "import bots.<new_version>.bot"`` under *timeout* seconds.

    Returns ``None`` on success. On failure returns a trimmed error
    string (last ~500 chars of stderr/stdout) suitable for surfacing in
    the results row's ``reason`` field.

    Migrated from the deleted ``orchestrator.evolve._default_import_check``
    when the composition phase was removed 2026-04-23; see
    ``documentation/plans/evolve-gate-reduction-plan.md``. The gate now
    runs AFTER the snapshot + apply + promote-to-``vN+1`` step, so a
    failure here rolls back the snapshot and skips regression.
    """
    argv = [sys.executable, "-c", f"import bots.{new_version}.bot"]
    try:
        result = run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            cwd=str(_REPO_ROOT),
        )
    except subprocess.TimeoutExpired:
        return f"import-check timed out after {timeout}s"
    if result.returncode == 0:
        return None
    stderr = (result.stderr or "").strip()
    if not stderr:
        stderr = (result.stdout or "").strip()
    tail = stderr[-500:] if len(stderr) > 500 else stderr
    return tail or f"import-check exited rc={result.returncode} (no stderr)"


def _stack_apply_and_promote(
    parent: str,
    winning_imps: list[Improvement],
    *,
    dev_apply_fn: Any,
    snapshot_fn: Callable[[], str] | None = None,
    import_check_fn: Callable[[str], str | None] | None = None,
    apply_fn: Callable[..., None] | None = None,
    commit_fn: Callable[..., tuple[bool, str | None]] | None = None,
    generation: int = 0,
) -> StackApplyOutcome:
    """Snapshot *parent* → apply each winning imp → import-check → commit.

    Steps, in order:

    1. Compute the next ``vN+1`` name and pre-assign ``new_version_dir``
       BEFORE calling ``snapshot_fn`` — this guarantees any partial-copy
       leak (e.g. manifest write raises mid-snapshot) is cleanable.
       Then call ``snapshot_fn(new_version)`` (default:
       ``orchestrator.snapshot.snapshot_current`` with that name) to
       produce a fresh ``bots/<new_version>/`` directory. ``snapshot_current``
       also flips the pointer to the new version as a side effect.
    2. Apply each winning imp to the snapshot in the order given (the
       caller sorts by rank before calling). ``dev``-type imps are
       dispatched to *dev_apply_fn*; ``training`` imps are patched
       directly.
    3. Run ``python -c "import bots.<new_version>.bot"`` under a 30s
       timeout. On failure: rmtree the new version directory, restore
       the pointer to *parent*, and return ``promoted=False`` with
       ``outcome="stack-apply-import-fail"``.
    4. Rewrite the manifest's parent field to *parent*
       (``snapshot_current`` writes the last-pointer value there, which
       may drift).
    5. If ``commit_fn`` is provided (caller suppresses with ``--no-commit``),
       invoke it to create the ``[evolve-promote]`` commit. On commit
       failure: rmtree the new version dir, restore the pointer to
       *parent*, and return ``promoted=False`` with
       ``outcome="stack-apply-commit-fail"``. This keeps in-process
       state (pointer / parent_current / imp statuses) aligned with
       what's actually tracked in git: if there's no commit, there's
       no promotion.
    6. On success: return ``promoted=True`` with
       ``outcome="stack-apply-pass"``. The caller proceeds to regression.

    On any apply failure (e.g. malformed training patch, dev sub-agent
    raised), the snapshot is rolled back and the exception is re-raised
    to the caller, which wraps it as a crash row. Rollback uses a
    nested try/except around ``_primitive_restore_pointer`` so a
    restore-pointer error (e.g. Windows PermissionError after retries)
    does NOT mask the original apply/snapshot exception — the pointer
    inconsistency is surfaced on the next run by the phantom-promote
    guard.
    """
    from orchestrator import snapshot as _snapshot_mod
    from orchestrator.evolve import (
        _rewrite_manifest_parent,
        _safe_rmtree,
        apply_improvement,
    )

    if import_check_fn is None:
        import_check_fn = _default_import_check
    if apply_fn is None:
        apply_fn = apply_improvement

    # H1 fix: compute target name FIRST so the candidate dir path is
    # known even if snapshot_fn raises partway through shutil.copytree
    # or during the manifest/pointer writes that follow the copy.
    new_version = _snapshot_mod._next_version_name()
    new_version_dir = _REPO_ROOT / "bots" / new_version

    def _cleanup_on_error() -> None:
        """Rmtree candidate dir + restore pointer; swallow restore errors.

        Called from every rollback branch. The pointer-restore failure
        mode is Windows-specific (PermissionError after retries); we
        log it and let the original exception propagate or the
        helper's intended return value land — the phantom-promote
        guard on the next run will catch any lingering inconsistency.
        """
        if new_version_dir.exists():
            _safe_rmtree(new_version_dir)
        try:
            _primitive_restore_pointer(parent)
        except Exception as restore_exc:  # noqa: BLE001
            _log.error(
                "failed to restore pointer during cleanup: %s",
                restore_exc,
            )

    try:
        if snapshot_fn is not None:
            snapshot_fn()
        else:
            _snapshot_mod.snapshot_current(new_version)
        for imp in winning_imps:
            apply_fn(new_version_dir, imp, dev_apply_fn=dev_apply_fn)
    except Exception:
        # H2 fix: wrap pointer-restore in its own try/except (inside
        # _cleanup_on_error) so a restore failure does not mask the
        # original apply/snapshot exception reported to the caller.
        _cleanup_on_error()
        raise

    # Import gate: the snapshot exists on disk, the pointer is already
    # flipped to it, apply succeeded — now verify the module imports.
    import_error = import_check_fn(new_version)
    if import_error is not None:
        _cleanup_on_error()
        reason = (
            f"stack-apply import-fail: {new_version} ({len(winning_imps)} "
            f"imps) failed import check; stderr tail: {import_error}"
        )
        _log.warning("stack-apply outcome: %s", reason)
        return StackApplyOutcome(
            parent=parent,
            stacked_imps=list(winning_imps),
            new_version=None,
            promote_sha=None,
            promoted=False,
            outcome="stack-apply-import-fail",
            reason=reason,
        )

    # Rewrite manifest parent to the real parent (snapshot_current
    # records the then-current pointer, which in our case IS the parent,
    # so this is a no-op most of the time but keeps lineage honest).
    _rewrite_manifest_parent(new_version_dir, parent)

    # H3 fix: commit BEFORE claiming promotion. If the commit fails,
    # rollback the snapshot and report stack-apply-commit-fail so
    # parent_current / per_item_state never advance into a state that
    # disagrees with git HEAD.
    promote_sha: str | None = None
    if commit_fn is not None:
        commit_ok, sha = commit_fn(
            new_version,
            generation,
            [imp.title for imp in winning_imps],
        )
        if not commit_ok:
            _cleanup_on_error()
            reason = (
                f"stack-apply commit-fail: {new_version} "
                f"({len(winning_imps)} imps) imported cleanly but git "
                f"commit failed; rolled back to {parent}"
            )
            _log.warning("stack-apply outcome: %s", reason)
            return StackApplyOutcome(
                parent=parent,
                stacked_imps=list(winning_imps),
                new_version=None,
                promote_sha=None,
                promoted=False,
                outcome="stack-apply-commit-fail",
                reason=reason,
            )
        promote_sha = sha

    reason = (
        f"stack-apply pass: promoted {new_version} "
        f"({len(winning_imps)} imps) from parent {parent}"
    )
    _log.info("stack-apply outcome: %s", reason)
    return StackApplyOutcome(
        parent=parent,
        stacked_imps=list(winning_imps),
        new_version=new_version,
        promote_sha=promote_sha,
        promoted=True,
        outcome="stack-apply-pass",
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Orchestration loop
# ---------------------------------------------------------------------------


def run_loop(
    args: argparse.Namespace,
    *,
    generate_pool_fn: Callable[..., list[Improvement]] | None = None,
    run_fitness_fn: Callable[..., FitnessResult] | None = None,
    stack_apply_fn: Callable[..., StackApplyOutcome] | None = None,
    run_regression_fn: Callable[..., RegressionResult] | None = None,
    claude_fn: Callable[[str], str] | None = None,
    commit_fn: Callable[..., tuple[bool, str | None]] | None = None,
    revert_fn: Callable[..., bool] | None = None,
    current_version_fn: Callable[[], str] | None = None,
    run_batch_fn: Callable[..., list[SelfPlayRecord]] | None = None,
    dev_apply_fn: Any = None,
    time_fn: Callable[[], float] = time.monotonic,
    post_training_fn: Callable[..., dict[str, Any]] | None = None,
) -> int:
    """Evolve orchestration loop — generation-phase algorithm.

    Every heavy boundary is injectable so tests can exercise the full
    control flow with canned results. See ``tests/test_evolve_cli.py`` for
    the mock shapes.
    """
    if generate_pool_fn is None:
        from orchestrator.evolve import generate_pool

        generate_pool_fn = generate_pool
    if run_fitness_fn is None:
        from orchestrator.evolve import run_fitness_eval

        run_fitness_fn = run_fitness_eval
    if stack_apply_fn is None:
        stack_apply_fn = _stack_apply_and_promote
    if run_regression_fn is None:
        from orchestrator.evolve import run_regression_eval

        run_regression_fn = run_regression_eval
    if commit_fn is None:
        commit_fn = git_commit_evo_auto
    if revert_fn is None:
        revert_fn = git_revert_evo_auto
    if current_version_fn is None:
        from orchestrator.registry import current_version

        current_version_fn = current_version
    if dev_apply_fn is None:
        from orchestrator.evolve_dev_apply import spawn_dev_subagent

        dev_apply_fn = spawn_dev_subagent

    # --- Pre-flight ---
    check_git_clean()
    if not check_sc2_installed():
        print(
            "evolve: SC2 not installed; aborting pre-flight.",
            file=sys.stderr,
        )
        return 1

    # Phantom-promote guard: catch the failure mode from run 20260422-0824
    # where a prior rollback dirtied ``bots/current/current.txt`` but
    # ``git revert`` failed on the dirty tree, leaving HEAD still carrying
    # the promote commit. Starting a new run from that state is unsafe.
    phantom_ok, head_version, disk_version = check_no_phantom_promote()
    if not phantom_ok:
        msg = (
            "evolve: phantom-promote state detected — "
            f"bots/current/current.txt on disk is {disk_version!r} but "
            f"git HEAD has {head_version!r}. A prior rollback left the "
            "pointer inconsistent with git. Recover with:\n"
            "  git checkout bots/current/current.txt   "
            "# accept HEAD's version\n"
            "or\n"
            "  git revert <promote-sha>                "
            "# revert the phantom promote commit"
        )
        _log.error(msg)
        print(msg, file=sys.stderr)
        return 1

    parent_start = current_version_fn()
    parent_current = parent_start
    started_at_iso = _now_iso()
    start_monotonic = time_fn()
    _log.info(
        "evolve: starting run (parent=%s, pool_size=%d, budget=%sh)",
        parent_start,
        args.pool_size,
        args.hours,
    )

    def _write_state(
        *,
        status: str,
        pool: list[Improvement],
        per_item_state: dict[int, PerItemState],
        generation_index: int,
        generations_completed: int,
        generations_promoted: int,
        last_result: dict[str, Any] | None,
    ) -> None:
        write_run_state(
            args.state_path,
            status=status,
            parent_start=parent_start,
            parent_current=parent_current,
            started_at=started_at_iso,
            wall_budget_hours=args.hours,
            generation_index=generation_index,
            generations_completed=generations_completed,
            generations_promoted=generations_promoted,
            evictions=_count_evicted(per_item_state),
            resurrections_remaining=_count_resurrections_remaining(
                per_item_state
            ),
            pool_remaining_count=_count_active(per_item_state),
            last_result=last_result,
        )

    # Write an initial "running" state so a watchdog can see us mid-startup.
    _write_state(
        status="running",
        pool=[],
        per_item_state={},
        generation_index=0,
        generations_completed=0,
        generations_promoted=0,
        last_result=None,
    )

    current_round_path = getattr(args, "current_round_path", None)

    def _write_failed_and_return() -> int:
        _write_state(
            status="failed",
            pool=[],
            per_item_state={},
            generation_index=0,
            generations_completed=0,
            generations_promoted=0,
            last_result=None,
        )
        return 1

    # --- Pool generation (or resume) ---
    pool: list[Improvement]
    per_item_state: dict[int, PerItemState]
    pool_generated_at: str
    generation_index: int

    resume_loaded = False
    if getattr(args, "resume", False) and args.pool_path.exists():
        try:
            (
                pool,
                per_item_state,
                pool_parent,
                pool_generated_at,
                generation_index,
            ) = load_pool_state(args.pool_path)
        except Exception as exc:
            _log.error(
                "evolve: --resume failed to read %s: %s; aborting",
                args.pool_path,
                exc,
            )
            return _write_failed_and_return()
        if pool_parent != parent_start:
            _log.error(
                "evolve: --resume parent mismatch (pool file says %r, "
                "current_version() is %r); refusing to continue",
                pool_parent,
                parent_start,
            )
            return _write_failed_and_return()
        active = _count_active(per_item_state)
        _log.info(
            "evolve: resumed from %s (%d items, %d active, generation %d)",
            args.pool_path,
            len(pool),
            active,
            generation_index,
        )
        resume_loaded = True
    else:
        _clear_fresh_run_state(
            results_path=args.results_path,
            pool_path=args.pool_path,
            current_round_path=current_round_path,
            parent=parent_start,
        )

        pool_gen_payload = CurrentRoundPayload(
            generation=0,
            phase="mirror_games",
            imp_title="parent-vs-parent mirror games",
            candidate=parent_start,
            games_total=3,
        )

        def _on_pool_gen_event(
            event: dict[str, Any],
            _payload: CurrentRoundPayload = pool_gen_payload,
            _path: Path | None = current_round_path,
        ) -> None:
            etype = event.get("type")
            if etype == "mirror_start":
                _payload.phase = "mirror_games"
                _payload.games_played = 0
                _payload.games_total = event.get("total", _payload.games_total)
            elif etype == "mirror_game_end":
                _payload.games_played = event.get(
                    "games_played", _payload.games_played + 1
                )
                if "total" in event:
                    _payload.games_total = event["total"]
            elif etype == "claude_start":
                _payload.phase = "claude_prompt"
                _payload.games_played = 0
                _payload.games_total = event.get("pool_size", 0)
            elif etype == "pool_ready":
                pass
            if _path is not None:
                write_current_round_state(_path, _payload)

        if current_round_path is not None:
            write_current_round_state(current_round_path, pool_gen_payload)

        try:
            pool_kwargs: dict[str, Any] = {
                "pool_size": args.pool_size,
                "map_name": args.map,
                "game_time_limit": args.game_time_limit,
                "hard_timeout": args.hard_timeout,
                "on_pool_gen_event": _on_pool_gen_event,
            }
            if claude_fn is not None:
                pool_kwargs["claude_fn"] = claude_fn
            if run_batch_fn is not None:
                pool_kwargs["run_batch_fn"] = run_batch_fn
            pool = generate_pool_fn(parent_start, **pool_kwargs)
        except Exception as exc:
            _log.error("evolve: pool generation failed: %s", exc, exc_info=True)
            if current_round_path is not None:
                clear_current_round_state(current_round_path)
            return _write_failed_and_return()

        per_item_state = {i: PerItemState() for i in range(len(pool))}
        pool_generated_at = _now_iso()
        generation_index = 0

    if not resume_loaded:
        write_pool_state(
            args.pool_path,
            pool,
            parent=parent_start,
            per_item_state=per_item_state,
            generated_at=pool_generated_at,
            generation=generation_index,
        )

    # --- Generation loop ---
    generations_completed = 0
    generations_promoted = 0
    generation_entries: list[dict[str, Any]] = []
    stop_reason = "pool-exhausted"
    last_result_snap: dict[str, Any] | None = None

    while True:
        if _budget_exceeded(start_monotonic, args.hours, now_fn=time_fn):
            stop_reason = "wall-clock"
            _log.info("evolve: wall-clock budget exceeded; stopping")
            break

        active_idxs = [
            i for i, st in per_item_state.items() if st.status == _ACTIVE
        ]
        if not active_idxs:
            stop_reason = "pool-exhausted"
            _log.info(
                "evolve: pool exhausted (0 active); stopping"
            )
            break

        generation_index += 1
        _log.info(
            "evolve: generation %d — %d active imps, parent=%s",
            generation_index,
            len(active_idxs),
            parent_current,
        )

        gen_payload = CurrentRoundPayload(generation=generation_index)

        def _current_round_writer(
            p: CurrentRoundPayload,
            _path: Path | None = current_round_path,
        ) -> None:
            if _path is not None:
                write_current_round_state(_path, p)

        # ---------- FITNESS PHASE ----------
        fitness_results: dict[int, FitnessResult] = {}
        fitness_counts = {"pass": 0, "close": 0, "fail": 0, "crash": 0}
        for idx in sorted(active_idxs):
            if _budget_exceeded(start_monotonic, args.hours, now_fn=time_fn):
                stop_reason = "wall-clock"
                _log.info(
                    "evolve: wall-clock budget exceeded mid-fitness; "
                    "breaking out of fitness phase"
                )
                break

            imp = pool[idx]
            gen_payload.phase = "fitness"
            gen_payload.imp_title = imp.title
            gen_payload.imp_rank = imp.rank
            gen_payload.imp_index = idx
            gen_payload.candidate = None
            gen_payload.stacked_titles = []
            gen_payload.new_parent = None
            gen_payload.prior_parent = None
            gen_payload.reset_progress(args.games_per_eval)
            _current_round_writer(gen_payload)

            def _on_fitness_event(
                event: dict[str, Any],
                _p: CurrentRoundPayload = gen_payload,
            ) -> None:
                etype = event.get("type")
                if etype == "fitness_start":
                    _p.candidate = event.get("candidate")
                    _p.games_total = event.get("total", _p.games_total)
                    _p.games_played = 0
                    _p.score_cand = 0
                    _p.score_parent = 0
                elif etype == "fitness_game_end":
                    _p.games_played += 1
                    _p.score_cand = event.get("wins_cand", _p.score_cand)
                    _p.score_parent = event.get("wins_parent", _p.score_parent)
                _current_round_writer(_p)

            try:
                result = run_fitness_fn(
                    parent_current,
                    imp,
                    games=args.games_per_eval,
                    map_name=args.map,
                    game_time_limit=args.game_time_limit,
                    hard_timeout=args.hard_timeout,
                    run_batch_fn=run_batch_fn,
                    dev_apply_fn=dev_apply_fn,
                    on_event=_on_fitness_event,
                )
            except Exception as exc:
                tb = traceback.format_exc()
                _log.error(
                    "evolve: fitness crash on generation %d imp %r: %s",
                    generation_index,
                    imp.title,
                    exc,
                    exc_info=True,
                )
                fitness_counts["crash"] += 1
                per_item_state[idx].status = _EVICTED
                per_item_state[idx].retry_count += 1
                per_item_state[idx].last_evaluated_against = parent_current
                append_phase_result(
                    args.results_path,
                    _crash_row(
                        generation_index, "fitness", parent_current, imp, exc, tb
                    ),
                )
                append_crash_log(
                    args.crash_log_path,
                    generation=generation_index,
                    phase="fitness",
                    parent=parent_current,
                    imp=imp,
                    exc=exc,
                    traceback_str=tb,
                )
                last_result_snap = _last_result_snapshot_crash(
                    generation_index, "fitness", imp, exc
                )
                continue

            fitness_results[idx] = result
            _apply_fitness_outcome(per_item_state, idx, result)
            fitness_counts[result.bucket] += 1
            append_phase_result(
                args.results_path, _fitness_row(generation_index, parent_current, result)
            )
            last_result_snap = _last_result_snapshot_fitness(
                generation_index, result
            )
            write_pool_state(
                args.pool_path,
                pool,
                parent=parent_start,
                per_item_state=per_item_state,
                generated_at=pool_generated_at,
                generation=generation_index,
            )
            _write_state(
                status="running",
                pool=pool,
                per_item_state=per_item_state,
                generation_index=generation_index,
                generations_completed=generations_completed,
                generations_promoted=generations_promoted,
                last_result=last_result_snap,
            )

        winner_idxs = sorted(
            (idx for idx, r in fitness_results.items() if r.bucket == "pass"),
            key=lambda i: pool[i].rank,
        )

        # ---------- STACK-APPLY + PROMOTE PHASE ----------
        # All fitness-pass imps are applied in rank order to a fresh
        # snapshot of the parent. An import-check gate runs AFTER apply
        # but BEFORE regression; import failure rolls back the snapshot
        # and skips regression. Pre-2026-04-23 this was a full
        # composition phase with its own 5-game Bernoulli gate — removed
        # per documentation/plans/evolve-gate-reduction-plan.md.
        prior_parent = parent_current
        stack_outcome_label: str = "none"
        promote_sha: str | None = None
        promoted_imp_idxs: list[int] = []

        if not winner_idxs:
            _log.info(
                "evolve: generation %d — no fitness passes; skipping "
                "stack-apply",
                generation_index,
            )
            stack_outcome_label = "no winners"
        else:
            winning_imps = [pool[i] for i in winner_idxs]
            gen_payload.phase = "stack_apply"
            gen_payload.imp_title = None
            gen_payload.imp_rank = None
            gen_payload.imp_index = None
            gen_payload.candidate = None
            gen_payload.stacked_titles = [imp.title for imp in winning_imps]
            gen_payload.new_parent = None
            gen_payload.prior_parent = None
            gen_payload.reset_progress(0)
            _current_round_writer(gen_payload)

            try:
                # H3 fix: commit is now part of the helper's contract.
                # If --no-commit is set, pass commit_fn=None so the
                # helper skips the commit step entirely (tests + CI
                # dry-runs rely on this). Otherwise the helper's own
                # rollback handles commit-failure cleanup.
                stack_result = stack_apply_fn(
                    parent_current,
                    winning_imps,
                    dev_apply_fn=dev_apply_fn,
                    commit_fn=None if args.no_commit else commit_fn,
                    generation=generation_index,
                )
            except Exception as exc:
                tb = traceback.format_exc()
                _log.error(
                    "evolve: stack-apply crash on generation %d: %s",
                    generation_index,
                    exc,
                    exc_info=True,
                )
                append_phase_result(
                    args.results_path,
                    _crash_row(
                        generation_index,
                        "stack_apply",
                        parent_current,
                        None,
                        exc,
                        tb,
                    ),
                )
                append_crash_log(
                    args.crash_log_path,
                    generation=generation_index,
                    phase="stack_apply",
                    parent=parent_current,
                    imp=None,
                    exc=exc,
                    traceback_str=tb,
                )
                last_result_snap = _last_result_snapshot_crash(
                    generation_index, "stack_apply", None, exc
                )
                stack_outcome_label = "crash"
            else:
                append_phase_result(
                    args.results_path,
                    _stack_apply_row(
                        generation_index,
                        parent_current,
                        stack_result.new_version or "",
                        list(stack_result.stacked_imps),
                        outcome=stack_result.outcome,
                        reason=stack_result.reason,
                    ),
                )
                last_result_snap = _last_result_snapshot_stack_apply(
                    generation_index,
                    stack_result.new_version or "",
                    list(stack_result.stacked_imps),
                    outcome=stack_result.outcome,
                    reason=stack_result.reason,
                )

                if stack_result.promoted and stack_result.new_version:
                    # Helper already did the commit (or skipped it when
                    # --no-commit was honored). State mutations ONLY
                    # happen on the success branch — a commit failure
                    # returns promoted=False with the rollback already
                    # done inside the helper, so parent_current stays
                    # put and the phantom-promote guard never fires.
                    stack_outcome_label = "stack-apply-pass"
                    promoted_imp_idxs = list(winner_idxs)
                    parent_current = stack_result.new_version
                    promote_sha = stack_result.promote_sha
                    for idx in promoted_imp_idxs:
                        per_item_state[idx].status = _PROMOTED
                else:
                    # Import-check or commit failed; snapshot rolled
                    # back inside the helper. No promotion this
                    # generation. The outcome label carries the
                    # specific failure flavor for the run state.
                    stack_outcome_label = stack_result.outcome

            write_pool_state(
                args.pool_path,
                pool,
                parent=parent_start,
                per_item_state=per_item_state,
                generated_at=pool_generated_at,
                generation=generation_index,
            )
            _write_state(
                status="running",
                pool=pool,
                per_item_state=per_item_state,
                generation_index=generation_index,
                generations_completed=generations_completed,
                generations_promoted=generations_promoted,
                last_result=last_result_snap,
            )

        # ---------- REGRESSION PHASE ----------
        regression_outcome_label = "none"
        if promoted_imp_idxs and parent_current != prior_parent:
            gen_payload.phase = "regression"
            gen_payload.imp_title = None
            gen_payload.imp_rank = None
            gen_payload.imp_index = None
            gen_payload.candidate = None
            gen_payload.stacked_titles = []
            gen_payload.new_parent = parent_current
            gen_payload.prior_parent = prior_parent
            gen_payload.reset_progress(args.games_per_eval)
            _current_round_writer(gen_payload)

            def _on_regression_event(
                event: dict[str, Any],
                _p: CurrentRoundPayload = gen_payload,
            ) -> None:
                etype = event.get("type")
                if etype == "regression_start":
                    _p.games_total = event.get("total", _p.games_total)
                    _p.games_played = 0
                    _p.score_cand = 0
                    _p.score_parent = 0
                elif etype == "regression_game_end":
                    _p.games_played += 1
                    _p.score_cand = event.get("wins_new", _p.score_cand)
                    _p.score_parent = event.get("wins_prior", _p.score_parent)
                _current_round_writer(_p)

            try:
                regression_result = run_regression_fn(
                    parent_current,
                    prior_parent,
                    games=args.games_per_eval,
                    map_name=args.map,
                    game_time_limit=args.game_time_limit,
                    hard_timeout=args.hard_timeout,
                    run_batch_fn=run_batch_fn,
                    on_event=_on_regression_event,
                )
            except Exception as exc:
                tb = traceback.format_exc()
                _log.error(
                    "evolve: regression crash on generation %d: %s",
                    generation_index,
                    exc,
                    exc_info=True,
                )
                append_phase_result(
                    args.results_path,
                    _crash_row(
                        generation_index,
                        "regression",
                        parent_current,
                        None,
                        exc,
                        tb,
                    ),
                )
                append_crash_log(
                    args.crash_log_path,
                    generation=generation_index,
                    phase="regression",
                    parent=parent_current,
                    imp=None,
                    exc=exc,
                    traceback_str=tb,
                )
                last_result_snap = _last_result_snapshot_crash(
                    generation_index, "regression", None, exc
                )
                regression_outcome_label = "crash"
            else:
                append_phase_result(
                    args.results_path,
                    _regression_row(generation_index, regression_result),
                )
                last_result_snap = _last_result_snapshot_regression(
                    generation_index, regression_result
                )
                if regression_result.rolled_back:
                    regression_outcome_label = "rollback"
                    # Flip promoted imps to regression-rollback.
                    for idx in promoted_imp_idxs:
                        per_item_state[idx].status = _REGRESSION_ROLLBACK
                    # Rollback order is load-bearing: run ``git revert`` on
                    # a CLEAN working tree first — the revert commit's
                    # reverse diff restores bots/current/current.txt to
                    # prior_parent as a side effect. The primitive
                    # deliberately leaves the pointer alone so this works;
                    # see run_regression_eval's docstring. If the revert is
                    # skipped (--no-commit) or fails, we fall back to
                    # writing the pointer explicitly so the in-process
                    # state and on-disk state still agree.
                    revert_ok = False
                    if promote_sha and not args.no_commit:
                        revert_ok = revert_fn(
                            promote_sha,
                            generation_index,
                            regression_result.reason,
                        )
                        if not revert_ok:
                            _log.warning(
                                "evolve: git revert failed on gen %d; "
                                "operator must reconcile manually",
                                generation_index,
                            )
                    if not revert_ok:
                        # No revert commit landed (either --no-commit dev
                        # run, missing sha, or the revert failed). Restore
                        # the pointer explicitly so subsequent generations
                        # see prior_parent as the live parent.
                        _restore_current_pointer(prior_parent)
                    parent_current = prior_parent
                else:
                    regression_outcome_label = "pass"
                    generations_promoted += 1

            write_pool_state(
                args.pool_path,
                pool,
                parent=parent_start,
                per_item_state=per_item_state,
                generated_at=pool_generated_at,
                generation=generation_index,
            )

        # ---------- POOL REFRESH ----------
        _apply_retry_bookkeeping(per_item_state)

        active_after_refresh = _count_active(per_item_state)
        delta = args.pool_size - active_after_refresh
        if delta > 0:
            gen_payload.phase = "pool_refresh"
            gen_payload.imp_title = f"generating {delta} replacement imps"
            gen_payload.imp_rank = None
            gen_payload.imp_index = None
            gen_payload.candidate = None
            gen_payload.stacked_titles = []
            gen_payload.new_parent = None
            gen_payload.prior_parent = None
            gen_payload.reset_progress(delta)
            _current_round_writer(gen_payload)

            def _on_refresh_event(
                event: dict[str, Any],
                _p: CurrentRoundPayload = gen_payload,
            ) -> None:
                etype = event.get("type")
                if etype == "claude_start":
                    _p.games_total = event.get("pool_size", _p.games_total)
                _current_round_writer(_p)

            try:
                fresh_kwargs: dict[str, Any] = {
                    "pool_size": delta,
                    "map_name": args.map,
                    "game_time_limit": args.game_time_limit,
                    "hard_timeout": args.hard_timeout,
                    "on_pool_gen_event": _on_refresh_event,
                    "skip_mirror": True,
                }
                if claude_fn is not None:
                    fresh_kwargs["claude_fn"] = claude_fn
                if run_batch_fn is not None:
                    fresh_kwargs["run_batch_fn"] = run_batch_fn
                fresh_imps = generate_pool_fn(parent_current, **fresh_kwargs)
            except Exception as exc:
                _log.warning(
                    "evolve: pool refresh failed on gen %d (%s); continuing "
                    "without topping up — may stop on pool-exhausted next gen",
                    generation_index,
                    exc,
                )
                fresh_imps = []

            # Append fresh imps at fresh indexes with active status.
            start_idx = len(pool)
            for offset, imp in enumerate(fresh_imps):
                pool.append(imp)
                per_item_state[start_idx + offset] = PerItemState()

        generations_completed += 1

        # Summarise the generation for the run-log markdown.
        stack_summary = (
            f"{stack_outcome_label}"
            + (
                f" → promoted {parent_current}"
                if promoted_imp_idxs and regression_outcome_label != "rollback"
                else ""
            )
            + (" → ROLLBACK" if regression_outcome_label == "rollback" else "")
        )
        generation_entries.append(
            {
                "generation": generation_index,
                "fitness_counts": (
                    f"{fitness_counts['pass']}/{fitness_counts['close']}/"
                    f"{fitness_counts['fail']}"
                    + (
                        f" (+{fitness_counts['crash']} crash)"
                        if fitness_counts["crash"]
                        else ""
                    )
                ),
                "stack_outcome": stack_outcome_label,
                "regression_outcome": regression_outcome_label,
                "summary": stack_summary,
            }
        )

        write_pool_state(
            args.pool_path,
            pool,
            parent=parent_start,
            per_item_state=per_item_state,
            generated_at=pool_generated_at,
            generation=generation_index,
        )
        _write_state(
            status="running",
            pool=pool,
            per_item_state=per_item_state,
            generation_index=generation_index,
            generations_completed=generations_completed,
            generations_promoted=generations_promoted,
            last_result=last_result_snap,
        )
        if current_round_path is not None:
            clear_current_round_state(current_round_path)

    # --- Finalise ---
    _write_state(
        status="completed",
        pool=pool,
        per_item_state=per_item_state,
        generation_index=generation_index,
        generations_completed=generations_completed,
        generations_promoted=generations_promoted,
        last_result=last_result_snap,
    )
    if current_round_path is not None:
        clear_current_round_state(current_round_path)

    if args.run_log is None:
        safe_ts = started_at_iso.replace(":", "-")
        run_log_path = (
            _REPO_ROOT
            / "documentation"
            / "soak-test-runs"
            / f"evolve-{safe_ts}.md"
        )
    else:
        run_log_path = args.run_log

    write_run_log(
        run_log_path,
        started_at=started_at_iso,
        finished_at=_now_iso(),
        parent_start=parent_start,
        parent_current=parent_current,
        wall_budget_hours=args.hours,
        generations_completed=generations_completed,
        generations_promoted=generations_promoted,
        evictions=_count_evicted(per_item_state),
        stop_reason=stop_reason,
        generation_entries=generation_entries,
    )

    _log.info(
        "evolve: run complete — %d generations, %d promoted (%s)",
        generations_completed,
        generations_promoted,
        stop_reason,
    )

    post_cycles = getattr(args, "post_training_cycles", 0)
    if generations_promoted >= 1 and post_cycles > 0:
        fn = post_training_fn or start_post_training_daemon
        fn(
            cycles=post_cycles,
            backend_url=getattr(args, "backend_url", "http://localhost:8765"),
            new_parent=parent_current,
        )
    elif generations_promoted >= 1:
        _log.info(
            "evolve: run promoted to %s; --post-training-cycles not set so "
            "no daemon was started.",
            parent_current,
        )

    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = build_parser()
    args = parser.parse_args(argv)

    return run_loop(args)


if __name__ == "__main__":
    sys.exit(main())
