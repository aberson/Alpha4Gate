"""CLI + orchestration loop for the evolve (sibling-tournament) skill.

Usage::

    # End-to-end overnight run (4h budget, pool of 10):
    python scripts/evolve.py

    # Short dev run (no commits, unlimited wall clock, tiny pool):
    python scripts/evolve.py --hours 0 --pool-size 2 --no-commit

    # Custom batch sizes + results path:
    python scripts/evolve.py --ab-games 6 --gate-games 5 \\
        --results-path data/my_evolve.jsonl

The CLI is a thin wrapper around :mod:`orchestrator.evolve`. Per round it:

1. Samples two improvements uniform-random from the remaining pool.
2. Calls :func:`orchestrator.evolve.run_round` with the configured batch
   sizes and map.
3. Appends the :class:`~orchestrator.evolve.RoundResult` to
   ``data/evolve_results.jsonl`` and updates ``data/evolve_pool.json`` +
   ``data/evolve_run_state.json``.
4. On promote, commits ``bots/<new_current>/`` with ``EVO_AUTO=1`` and a
   commit message containing ``[evo-auto]`` (unless ``--no-commit``).

The loop stops when any of three conditions trip:

* The wall-clock budget is exhausted (``--hours``; 0 disables the check).
* The pool has fewer than two remaining active items (pool exhausted).
* Three consecutive rounds discard without a promote (no-progress).

See ``documentation/plans/phase-9-build-plan.md`` Step 4 for the design
rationale.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import random
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestrator.contracts import SelfPlayRecord
    from orchestrator.evolve import Improvement, RoundResult

# Ensure repo root is on sys.path so ``orchestrator`` is importable when
# this script is invoked directly (``python scripts/evolve.py``). The
# parent repo layout mirrors scripts/ladder.py exactly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))


_log = logging.getLogger("evolve")


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python scripts/evolve.py",
        description=(
            "Alpha4Gate sibling-tournament (evolve) overnight runner. "
            "Generates a pool of improvements, plays them off in pairs, "
            "promotes winners past a parent safety gate."
        ),
    )
    parser.add_argument(
        "--pool-size",
        type=int,
        default=10,
        help="Number of improvements Claude generates (default: 10)",
    )
    parser.add_argument(
        "--ab-games",
        type=int,
        default=10,
        help="Games per A-vs-B batch (default: 10)",
    )
    parser.add_argument(
        "--gate-games",
        type=int,
        default=5,
        help="Games per parent-safety-gate batch (default: 5)",
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
            "30 min). Project-wide selfplay default is 300s, which cuts "
            "mirror matches short — evolve bumps it so games can resolve "
            "naturally."
        ),
    )
    parser.add_argument(
        "--hard-timeout",
        type=float,
        default=2700.0,
        help=(
            "Wall-clock timeout per game in seconds (default: 2700 = 45 "
            "min). Must be >= game-time-limit plus some buffer for SC2 "
            "spin-up and scoring."
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
        help="JSONL log of every RoundResult (default: data/evolve_results.jsonl)",
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
        "--run-log",
        type=Path,
        default=None,
        help=(
            "Human-readable markdown run log "
            "(default: documentation/soak-test-runs/evolve-<ts>.md)"
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed for improvement sampling (default: nondeterministic).",
    )
    parser.add_argument(
        "--return-loser",
        action="store_true",
        help=(
            "RESERVED for v2 — returns the AB loser to the pool. "
            "Raises NotImplementedError in v1."
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
    """Return ``(is_clean, dirty_paths)``.

    WARNs but does NOT abort when the tree is dirty — the operator sees the
    stashed file list and decides. Injected ``run`` for tests.
    """
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
    """Return True iff the SC2 install dir is present.

    Missing SC2 is a hard pre-flight failure (exit 1 from ``main``). The
    path is read from ``SC2PATH`` if set, else the project-standard
    Windows default.
    """
    sc2_path = os.environ.get(
        "SC2PATH", r"C:\Program Files (x86)\StarCraft II"
    )
    if Path(sc2_path).is_dir():
        return True
    _log.error(
        "SC2 install not found at %s. Set SC2PATH or install StarCraft II.",
        sc2_path,
    )
    return False


# ---------------------------------------------------------------------------
# State serialisation
# ---------------------------------------------------------------------------


PoolItemStatus = str  # "active" | "consumed-won" | "consumed-lost" | "consumed-tie"


def _imp_asdict(imp: Improvement) -> dict[str, Any]:
    """Return a JSON-serialisable dict for an Improvement."""
    return dataclasses.asdict(imp)


def _record_asdict(rec: SelfPlayRecord) -> dict[str, Any]:
    return dataclasses.asdict(rec)


def _round_asdict(result: RoundResult) -> dict[str, Any]:
    """Serialise a RoundResult to a JSON-compatible dict.

    ``RoundResult`` doesn't ship with its own ``to_json`` — the fields are
    all frozen dataclasses or primitives, so stdlib ``dataclasses.asdict``
    produces a pure JSON-compatible tree in one shot. We separate this
    helper from the write path so tests can assert the schema shape.
    """
    return dataclasses.asdict(result)


def _now_iso() -> str:
    """Return an ISO-8601 UTC timestamp (seconds resolution)."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write *payload* as pretty-printed, sorted JSON, atomically.

    Dashboard readers poll these files; we use a temp+rename so they never
    see a half-written partial document.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def write_pool_state(
    pool_path: Path,
    pool: list[Improvement],
    *,
    parent: str,
    statuses: dict[int, PoolItemStatus] | None = None,
    generated_at: str | None = None,
) -> None:
    """Write ``evolve_pool.json`` with per-item status flags.

    *statuses* maps an improvement's 0-based pool index to its current
    status string. Missing indexes default to ``"active"``. We index by
    position (not rank/title) because rank is not unique across a Claude
    response.
    """
    statuses = statuses or {}
    items: list[dict[str, Any]] = []
    for i, imp in enumerate(pool):
        entry = _imp_asdict(imp)
        entry["status"] = statuses.get(i, "active")
        items.append(entry)
    payload: dict[str, Any] = {
        "generated_at": generated_at or _now_iso(),
        "parent": parent,
        "pool": items,
    }
    _atomic_write_json(pool_path, payload)


def append_round_result(results_path: Path, result: RoundResult) -> None:
    """Append a single RoundResult as one JSON line."""
    results_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(_round_asdict(result))
    # Open in append text mode; one line per round.
    with results_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _last_result_snapshot(
    round_index: int,
    result: RoundResult,
    ab_wins_a: int,
    ab_wins_b: int,
    gate_wins_cand: int,
    gate_wins_parent: int,
    outcome: str,
) -> dict[str, Any]:
    """Build the ``last_result`` sub-object for the run-state file."""
    return {
        "round_index": round_index,
        "candidate_a": result.candidate_a,
        "candidate_b": result.candidate_b,
        "imp_a_title": result.imp_a.title,
        "imp_b_title": result.imp_b.title,
        "ab_score": [ab_wins_a, ab_wins_b],
        "gate_score": [gate_wins_cand, gate_wins_parent],
        "outcome": outcome,
        "reason": result.reason,
    }


def write_run_state(
    state_path: Path,
    *,
    status: str,
    parent_start: str,
    parent_current: str,
    started_at: str,
    wall_budget_hours: float,
    rounds_completed: int,
    rounds_promoted: int,
    no_progress_streak: int,
    pool_remaining_count: int,
    last_result: dict[str, Any] | None,
) -> None:
    """Write ``evolve_run_state.json`` — the dashboard-facing run state."""
    payload: dict[str, Any] = {
        "status": status,
        "parent_start": parent_start,
        "parent_current": parent_current,
        "started_at": started_at,
        "wall_budget_hours": wall_budget_hours,
        "rounds_completed": rounds_completed,
        "rounds_promoted": rounds_promoted,
        "no_progress_streak": no_progress_streak,
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
    rounds_completed: int,
    rounds_promoted: int,
    stop_reason: str,
    round_entries: list[dict[str, Any]],
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
        f"- Rounds completed: {rounds_completed}",
        f"- Rounds promoted: {rounds_promoted}",
        f"- Stop reason: {stop_reason}",
        "",
        "## Rounds",
        "",
    ]
    if not round_entries:
        lines.append("(no rounds completed)")
    else:
        lines.append(
            "| # | candidate A | candidate B | AB | gate | outcome | reason |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for entry in round_entries:
            ab = entry["ab_score"]
            gate = entry["gate_score"]
            lines.append(
                f"| {entry['round_index']} "
                f"| {entry['candidate_a']} "
                f"| {entry['candidate_b']} "
                f"| {ab[0]}-{ab[1]} "
                f"| {gate[0]}-{gate[1]} "
                f"| {entry['outcome']} "
                f"| {entry['reason']} |"
            )
    run_log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Commit-on-promote
# ---------------------------------------------------------------------------


def git_commit_evo_auto(
    new_version: str,
    round_index: int,
    imp_title: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    """Stage ``bots/<new_version>/`` and commit with EVO_AUTO=1.

    Returns True on success, False on failure (WARNING logged). A failure
    here must NOT crash the loop — the operator can reconcile the working
    tree out-of-band.
    """
    # Construct env with EVO_AUTO=1 so the sandbox pre-commit hook
    # (scripts/check_sandbox.py) permits commits under bots/.
    env = dict(os.environ)
    env["EVO_AUTO"] = "1"
    # Defensive: never hit the ADVISED_AUTO + EVO_AUTO conflict branch.
    env.pop("ADVISED_AUTO", None)

    # Build the commit message body. The [evo-auto] marker must appear on
    # its own line per the sandbox hook's expectations.
    msg = (
        f"evolve: round {round_index} promoted {imp_title}\n"
        "\n"
        "[evo-auto]\n"
    )

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
        return False
    if add_result.returncode != 0:
        _log.warning(
            "git add returned %d for bots/%s/: %s",
            add_result.returncode,
            new_version,
            add_result.stderr,
        )
        return False

    try:
        commit_result = run(
            ["git", "commit", "-m", msg],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(_REPO_ROOT),
            env=env,
        )
    except (FileNotFoundError, OSError) as exc:
        _log.warning("git commit failed for %s: %s", new_version, exc)
        return False
    if commit_result.returncode != 0:
        _log.warning(
            "git commit returned %d for round %d: %s",
            commit_result.returncode,
            round_index,
            commit_result.stderr,
        )
        return False

    _log.info(
        "evolve: committed round %d promote of %s (%s)",
        round_index,
        imp_title,
        new_version,
    )
    return True


# ---------------------------------------------------------------------------
# Sampling + loop helpers
# ---------------------------------------------------------------------------


def sample_two(
    active_indexes: list[int], rng: random.Random
) -> tuple[int, int]:
    """Return two distinct indexes sampled uniformly (without replacement).

    ``rng.sample`` is the idiomatic without-replacement primitive, so we
    don't have to hand-roll a reject loop. Caller guarantees
    ``len(active_indexes) >= 2``.
    """
    a, b = rng.sample(active_indexes, 2)
    return a, b


def _classify_outcome(result: RoundResult) -> str:
    """Collapse a RoundResult into a short outcome label.

    Labels:
      "promoted"       — gate passed
      "discarded-tie"  — A/B tied (reason string starts with "discarded")
      "discarded-gate" — gate failed
    """
    if result.promoted:
        return "promoted"
    if "lost to parent" in result.reason:
        return "discarded-gate"
    return "discarded-tie"


def _update_pool_statuses_after_round(
    statuses: dict[int, PoolItemStatus],
    imp_a_idx: int,
    imp_b_idx: int,
    result: RoundResult,
) -> None:
    """Mutate *statuses* to reflect the outcome of one round.

    - Promote: winner -> consumed-won, loser -> consumed-lost.
    - Gate failure: both -> consumed-lost (neither survived the parent gate,
      which matches the master-plan convention that a gate-failure consumes
      both improvements).
    - AB tie: both -> consumed-tie.
    """
    if result.promoted:
        if result.winner == result.candidate_a:
            statuses[imp_a_idx] = "consumed-won"
            statuses[imp_b_idx] = "consumed-lost"
        else:
            statuses[imp_a_idx] = "consumed-lost"
            statuses[imp_b_idx] = "consumed-won"
        return

    # Discard branch: distinguish AB-tie from gate-failure via the reason
    # string (set by orchestrator.evolve.run_round and stable across the
    # contract). AB-tie -> consumed-tie for both; gate-failure -> both
    # -lost (they lost to the parent collectively).
    if "lost to parent" in result.reason:
        statuses[imp_a_idx] = "consumed-lost"
        statuses[imp_b_idx] = "consumed-lost"
    else:
        statuses[imp_a_idx] = "consumed-tie"
        statuses[imp_b_idx] = "consumed-tie"


def _count_record_winners(
    records: Iterable[SelfPlayRecord], a: str, b: str
) -> tuple[int, int]:
    wins_a = 0
    wins_b = 0
    for rec in records:
        if rec.winner == a:
            wins_a += 1
        elif rec.winner == b:
            wins_b += 1
    return wins_a, wins_b


def _budget_exceeded(
    start_monotonic: float,
    hours: float,
    *,
    now_fn: Callable[[], float] = time.monotonic,
) -> bool:
    """Return True iff the wall-clock budget is up.

    ``hours == 0`` disables the check (always returns False). ``now_fn``
    is injectable so tests can force-trip the budget without sleeping.
    """
    if hours <= 0:
        return False
    elapsed_s = now_fn() - start_monotonic
    return elapsed_s >= hours * 3600.0


# ---------------------------------------------------------------------------
# Orchestration loop
# ---------------------------------------------------------------------------


def run_loop(
    args: argparse.Namespace,
    *,
    generate_pool_fn: Callable[..., list[Improvement]] | None = None,
    run_round_fn: Callable[..., RoundResult] | None = None,
    claude_fn: Callable[[str], str] | None = None,
    commit_fn: Callable[..., bool] | None = None,
    current_version_fn: Callable[[], str] | None = None,
    run_batch_fn: Callable[..., list[SelfPlayRecord]] | None = None,
    dev_apply_fn: Any = None,
    time_fn: Callable[[], float] = time.monotonic,
) -> int:
    """The actual evolve loop.

    Factored out of ``main()`` so tests can inject every expensive
    boundary. The injection points mirror the orchestrator module surface:
    ``generate_pool_fn`` (skipped in tests by returning a canned list),
    ``run_round_fn`` (the per-round primitive), and ``commit_fn`` (the
    git subprocess wrapper).
    """
    # Resolve defaults here so callers don't have to import the heavy
    # orchestrator modules just to construct an args Namespace.
    if generate_pool_fn is None:
        from orchestrator.evolve import generate_pool

        generate_pool_fn = generate_pool
    if run_round_fn is None:
        from orchestrator.evolve import run_round

        run_round_fn = run_round
    if commit_fn is None:
        commit_fn = git_commit_evo_auto
    if current_version_fn is None:
        from orchestrator.registry import current_version

        current_version_fn = current_version

    rng = random.Random(args.seed)

    # --- Pre-flight ---
    check_git_clean()
    if not check_sc2_installed():
        print(
            "evolve: SC2 not installed; aborting pre-flight.",
            file=sys.stderr,
        )
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

    # Write an initial "running" state so a watchdog can see us mid-startup.
    write_run_state(
        args.state_path,
        status="running",
        parent_start=parent_start,
        parent_current=parent_current,
        started_at=started_at_iso,
        wall_budget_hours=args.hours,
        rounds_completed=0,
        rounds_promoted=0,
        no_progress_streak=0,
        pool_remaining_count=0,
        last_result=None,
    )

    # --- Pool generation ---
    try:
        pool_kwargs: dict[str, Any] = {
            "pool_size": args.pool_size,
            "map_name": args.map,
            "game_time_limit": args.game_time_limit,
            "hard_timeout": args.hard_timeout,
        }
        if claude_fn is not None:
            pool_kwargs["claude_fn"] = claude_fn
        if run_batch_fn is not None:
            pool_kwargs["run_batch_fn"] = run_batch_fn
        pool = generate_pool_fn(parent_start, **pool_kwargs)
    except Exception as exc:
        _log.error("evolve: pool generation failed: %s", exc, exc_info=True)
        write_run_state(
            args.state_path,
            status="failed",
            parent_start=parent_start,
            parent_current=parent_current,
            started_at=started_at_iso,
            wall_budget_hours=args.hours,
            rounds_completed=0,
            rounds_promoted=0,
            no_progress_streak=0,
            pool_remaining_count=0,
            last_result=None,
        )
        return 1

    statuses: dict[int, PoolItemStatus] = {}
    pool_generated_at = _now_iso()
    write_pool_state(
        args.pool_path,
        pool,
        parent=parent_start,
        statuses=statuses,
        generated_at=pool_generated_at,
    )

    # --- Round loop ---
    rounds_completed = 0
    rounds_promoted = 0
    no_progress_streak = 0
    round_entries: list[dict[str, Any]] = []
    stop_reason = "pool-exhausted"
    last_result_snap: dict[str, Any] | None = None

    while True:
        # Exit conditions, checked in priority order. Budget first so a
        # timed-out run doesn't sneak one more round in.
        if _budget_exceeded(start_monotonic, args.hours, now_fn=time_fn):
            stop_reason = "wall-clock"
            _log.info("evolve: wall-clock budget exceeded; stopping")
            break
        active_indexes = [
            i for i in range(len(pool)) if statuses.get(i, "active") == "active"
        ]
        if len(active_indexes) < 2:
            stop_reason = "pool-exhausted"
            _log.info(
                "evolve: pool exhausted (%d active); stopping",
                len(active_indexes),
            )
            break
        if no_progress_streak >= 3:
            stop_reason = "no-progress"
            _log.info(
                "evolve: 3 consecutive discards; stopping for no-progress"
            )
            break

        imp_a_idx, imp_b_idx = sample_two(active_indexes, rng)
        imp_a = pool[imp_a_idx]
        imp_b = pool[imp_b_idx]

        round_index = rounds_completed + 1
        _log.info(
            "evolve: round %d — imp_a='%s' (#%d) vs imp_b='%s' (#%d)",
            round_index,
            imp_a.title,
            imp_a_idx,
            imp_b.title,
            imp_b_idx,
        )

        # --- Execute the round ---
        round_kwargs: dict[str, Any] = {
            "ab_games": args.ab_games,
            "gate_games": args.gate_games,
            "map_name": args.map,
            "game_time_limit": args.game_time_limit,
            "hard_timeout": args.hard_timeout,
        }
        if run_batch_fn is not None:
            round_kwargs["run_batch_fn"] = run_batch_fn
        if dev_apply_fn is not None:
            round_kwargs["dev_apply_fn"] = dev_apply_fn

        try:
            result = run_round_fn(parent_current, imp_a, imp_b, **round_kwargs)
        except Exception as exc:
            _log.error(
                "evolve: round %d crashed: %s", round_index, exc, exc_info=True
            )
            # Treat a crash as a consumed-tie so the loop keeps moving but
            # doesn't silently re-use the two improvements.
            statuses[imp_a_idx] = "consumed-tie"
            statuses[imp_b_idx] = "consumed-tie"
            write_pool_state(
                args.pool_path,
                pool,
                parent=parent_start,
                statuses=statuses,
                generated_at=pool_generated_at,
            )
            no_progress_streak += 1
            rounds_completed += 1
            continue

        # --- Score/outcome bookkeeping ---
        ab_wins_a, ab_wins_b = _count_record_winners(
            result.ab_record, result.candidate_a, result.candidate_b
        )
        if result.gate_record is not None and result.winner is not None:
            gate_wins_cand, gate_wins_parent = _count_record_winners(
                result.gate_record, result.winner, parent_current
            )
        else:
            gate_wins_cand, gate_wins_parent = 0, 0

        outcome = _classify_outcome(result)
        last_result_snap = _last_result_snapshot(
            round_index,
            result,
            ab_wins_a,
            ab_wins_b,
            gate_wins_cand,
            gate_wins_parent,
            outcome,
        )
        round_entries.append(last_result_snap)

        # Append JSONL first so even if the process crashes on the next
        # write the result itself is durable.
        append_round_result(args.results_path, result)

        _update_pool_statuses_after_round(
            statuses, imp_a_idx, imp_b_idx, result
        )
        write_pool_state(
            args.pool_path,
            pool,
            parent=parent_start,
            statuses=statuses,
            generated_at=pool_generated_at,
        )

        # --- Promote / commit ---
        if result.promoted:
            rounds_promoted += 1
            no_progress_streak = 0
            # A promote renames the pointer inside run_round; refresh.
            try:
                parent_current = current_version_fn()
            except Exception:
                parent_current = result.winner or parent_current
            if not args.no_commit:
                winning_imp = (
                    result.imp_a
                    if result.winner == result.candidate_a
                    else result.imp_b
                )
                winner_name = result.winner or parent_current
                commit_ok = commit_fn(
                    winner_name, round_index, winning_imp.title
                )
                if not commit_ok:
                    _log.warning(
                        "evolve: commit failed after promote; continuing loop "
                        "(operator can reconcile out-of-band)"
                    )
        else:
            no_progress_streak += 1

        rounds_completed += 1
        active_remaining = sum(
            1 for i in range(len(pool)) if statuses.get(i, "active") == "active"
        )
        write_run_state(
            args.state_path,
            status="running",
            parent_start=parent_start,
            parent_current=parent_current,
            started_at=started_at_iso,
            wall_budget_hours=args.hours,
            rounds_completed=rounds_completed,
            rounds_promoted=rounds_promoted,
            no_progress_streak=no_progress_streak,
            pool_remaining_count=active_remaining,
            last_result=last_result_snap,
        )

    # --- Finalise ---
    active_remaining = sum(
        1 for i in range(len(pool)) if statuses.get(i, "active") == "active"
    )
    write_run_state(
        args.state_path,
        status="completed",
        parent_start=parent_start,
        parent_current=parent_current,
        started_at=started_at_iso,
        wall_budget_hours=args.hours,
        rounds_completed=rounds_completed,
        rounds_promoted=rounds_promoted,
        no_progress_streak=no_progress_streak,
        pool_remaining_count=active_remaining,
        last_result=last_result_snap,
    )

    # Default run-log path uses the started_at timestamp, with colons
    # replaced so Windows-unsafe chars aren't in the filename.
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
        rounds_completed=rounds_completed,
        rounds_promoted=rounds_promoted,
        stop_reason=stop_reason,
        round_entries=round_entries,
    )

    _log.info(
        "evolve: run complete — %d rounds, %d promoted (%s)",
        rounds_completed,
        rounds_promoted,
        stop_reason,
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

    if args.return_loser:
        # Reserved for v2 — we emit on stderr AND raise so unit tests can
        # pick either signal. The CLI exit code is 2 (distinct from the
        # pre-flight-1 code in run_loop).
        print(
            "evolve: --return-loser is reserved for v2; not implemented.",
            file=sys.stderr,
        )
        raise NotImplementedError(
            "--return-loser is reserved for a future version of the evolve "
            "loop (return the AB loser to the pool)."
        )

    return run_loop(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except NotImplementedError as exc:
        # --return-loser: exit 2 distinct from pre-flight failures (exit 1).
        print(f"evolve: {exc}", file=sys.stderr)
        sys.exit(2)
