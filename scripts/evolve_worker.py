"""Per-worker eval CLI for parallel evolve.

This is the one-shot child invoked by the parallel dispatcher
(``documentation/plans/evolve-parallelization-plan.md``). The worker
supports two modes — selected via ``--mode``:

* ``fitness`` (default) — original behavior. Reads an ``Improvement``
  payload from ``--imp-json`` and runs
  :func:`orchestrator.evolve.run_fitness_eval` against ``--parent``;
  serializes the resulting :class:`FitnessResult` to ``--result-path``.
* ``mirror`` — issue #250. Plays ``--games`` self-play matches between
  ``--p1`` and ``--p2`` via :func:`orchestrator.selfplay.run_batch`;
  serializes the list of :class:`SelfPlayRecord` to ``--result-path``.

Both modes:

1. Build a :class:`CurrentRoundPayload` stamped with the worker's
   ``--worker-id`` and ``--run-id`` so the dispatcher's stale-file filter
   can distinguish fresh writes from leftover state.
2. Atomically update ``<state-dir>/evolve_round_<worker_id>.json``
   between each game (live dashboard progress).
3. Write a JSON crash payload — the dispatcher's Decision-D-7 ``crash``
   bucket — and exit 1 on any exception.

The worker does NOT spawn its own SC2 subprocesses; it uses
``orchestrator.selfplay.run_batch`` (the same ``run_batch_fn`` the
single-flight evolve loop uses). SC2 lifecycle is handled inside that
function via its existing PortConfig + Proxy plumbing.

Logging goes to stdlib at INFO; a one-line run summary is also printed to
stdout for the dispatcher to capture.

Usage (fitness)::

    python scripts/evolve_worker.py \\
        --parent v3 \\
        --imp-json /tmp/imp.json \\
        --worker-id 0 \\
        --result-path /tmp/result_0.json \\
        --games-per-eval 5

Usage (mirror)::

    python scripts/evolve_worker.py --mode mirror \\
        --p1 v3 --p2 v3 --games 2 \\
        --worker-id 0 \\
        --result-path /tmp/mirror_0.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Ensure repo root is on sys.path so ``orchestrator`` is importable when
# the script is invoked directly (``python scripts/evolve_worker.py``).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from evolve_round_state import (  # noqa: E402
    CurrentRoundPayload,
    atomic_write_json,
    clear_current_round_state,
    write_current_round_state,
)

from orchestrator.evolve import (  # noqa: E402
    FitnessResult,
    Improvement,
    run_fitness_eval,
)
from orchestrator.evolve_dev_apply import spawn_dev_subagent  # noqa: E402

_log = logging.getLogger("evolve_worker")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/evolve_worker.py",
        description=(
            "One-shot eval worker for parallel evolve. Either: (fitness) "
            "loads an Improvement and runs run_fitness_eval, OR (mirror) "
            "plays --games parent-vs-parent matches via selfplay.run_batch."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("fitness", "mirror"),
        default="fitness",
        help=(
            "Worker mode (default: fitness). "
            "fitness: --parent + --imp-json -> FitnessResult. "
            "mirror: --p1 + --p2 + --games -> list[SelfPlayRecord] (issue #250)."
        ),
    )
    # --- shared args ---
    parser.add_argument(
        "--worker-id",
        required=True,
        type=int,
        help="Slot identifier (used for round-state file naming)",
    )
    parser.add_argument(
        "--result-path",
        required=True,
        type=Path,
        help=(
            "Where to atomically write the result JSON (fitness: a "
            "FitnessResult, mirror: ``{\"records\": [SelfPlayRecord, ...]}``, "
            "either: a crash payload)."
        ),
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help=(
            "Optional run identifier stamped into the round-state JSON; "
            "used by the dispatcher's stale-file filter. Defaults to a "
            "fresh 8-char hex uuid."
        ),
    )
    parser.add_argument(
        "--map",
        dest="map_name",
        default="Simple64",
        help="Map name (default: Simple64)",
    )
    parser.add_argument(
        "--game-time-limit",
        type=int,
        default=1800,
        help="Per-game soft time cap in seconds (default: 1800)",
    )
    parser.add_argument(
        "--hard-timeout",
        type=float,
        default=2700.0,
        help="Per-game hard timeout in seconds (default: 2700.0)",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=_REPO_ROOT / "data",
        help=(
            "Directory for evolve_round_<worker_id>.json (default: "
            "<repo>/data/)"
        ),
    )
    # --- fitness-mode args (issue #250: required only when --mode=fitness) ---
    parser.add_argument(
        "--parent",
        default=None,
        help="(fitness mode) Parent version this candidate evaluates against",
    )
    parser.add_argument(
        "--imp-json",
        type=Path,
        default=None,
        help="(fitness mode) Path to a file containing Improvement.to_json() output",
    )
    parser.add_argument(
        "--games-per-eval",
        type=int,
        default=5,
        help="(fitness mode) Games per fitness eval (default: 5)",
    )
    # --- mirror-mode args (issue #250) ---
    parser.add_argument(
        "--p1",
        default=None,
        help="(mirror mode) First version (e.g. v7)",
    )
    parser.add_argument(
        "--p2",
        default=None,
        help="(mirror mode) Second version (e.g. v7 for a mirror)",
    )
    parser.add_argument(
        "--games",
        type=int,
        default=None,
        help="(mirror mode) Number of games to play",
    )
    return parser


def _write_crash(
    result_path: Path,
    exc: BaseException,
    tb: str,
) -> None:
    """Write a crash payload to ``result_path`` for the dispatcher."""
    payload: dict[str, Any] = {
        "crash": True,
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "traceback": tb,
    }
    atomic_write_json(result_path, payload)


def _make_event_callback(
    state_path: Path, payload: CurrentRoundPayload
) -> Callable[[dict[str, Any]], None]:
    """Build the on_event callback that writes the round-state file.

    Mirrors the behavior of ``_on_fitness_event`` in
    ``scripts/evolve.py`` so the dashboard reads the same shape from a
    worker's state file as it does from the single-flight current-round
    file.
    """

    def _on_event(event: dict[str, Any]) -> None:
        etype = event.get("type")
        if etype == "fitness_start":
            payload.candidate = event.get("candidate")
            payload.games_total = event.get("total", payload.games_total)
            payload.games_played = 0
            payload.score_cand = 0
            payload.score_parent = 0
        elif etype == "fitness_game_end":
            payload.games_played += 1
            payload.score_cand = event.get("wins_cand", payload.score_cand)
            payload.score_parent = event.get(
                "wins_parent", payload.score_parent
            )
        write_current_round_state(state_path, payload)

    return _on_event


def _make_mirror_event_callback(
    state_path: Path, payload: CurrentRoundPayload
) -> Callable[[Any], None]:
    """Build the on_game_end callback for mirror mode.

    Issue #250: ``selfplay.run_batch`` only emits ``on_game_end`` (a
    :class:`SelfPlayRecord` per game), not the structured event-dict
    shape ``run_fitness_eval`` uses. We map each game-end into the
    same round-state file the dashboard already reads from fitness
    workers — phase=``mirror_games`` so the UI can switch the badge
    label without forking the schema.
    """

    def _on_game_end(_record: Any) -> None:
        payload.games_played += 1
        write_current_round_state(state_path, payload)

    return _on_game_end


def _run_mirror_mode(args: argparse.Namespace, run_id: str) -> int:
    """Mirror-mode worker entry. Issue #250.

    Plays ``--games`` matches between ``--p1`` and ``--p2`` via
    :func:`selfplay.run_batch`, writes
    ``{"mode": "mirror", "records": [SelfPlayRecord-as-dict, ...]}`` to
    ``--result-path``. Crash + state-file invariants mirror the fitness
    path.
    """
    if args.p1 is None or args.p2 is None or args.games is None:
        msg = (
            "evolve_worker --mode mirror requires --p1, --p2, and --games"
        )
        _log.error(msg)
        try:
            _write_crash(args.result_path, ValueError(msg), msg)
        except Exception:  # noqa: BLE001
            _log.exception("evolve_worker: also failed to write crash payload")
        return 1

    state_path: Path = args.state_dir / f"evolve_round_{args.worker_id}.json"
    result_path: Path = args.result_path

    payload = CurrentRoundPayload(
        generation=0,
        phase="mirror_games",
        worker_id=args.worker_id,
        run_id=run_id,
    )
    payload.reset_progress(args.games)
    payload.candidate = args.p2
    payload.imp_title = f"mirror {args.p1} vs {args.p2}"

    write_current_round_state(state_path, payload)

    on_game_end = _make_mirror_event_callback(state_path, payload)

    try:
        try:
            from orchestrator import selfplay

            records = selfplay.run_batch(
                args.p1,
                args.p2,
                args.games,
                args.map_name,
                game_time_limit=args.game_time_limit,
                hard_timeout=args.hard_timeout,
                on_game_end=on_game_end,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            tb = traceback.format_exc()
            _log.exception(
                "evolve_worker: mirror run_batch crashed (worker=%d)",
                args.worker_id,
            )
            try:
                _write_crash(result_path, exc, tb)
            except Exception:  # noqa: BLE001
                _log.exception(
                    "evolve_worker: also failed to write crash payload"
                )
            return 1

        try:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                result_path,
                {
                    "mode": "mirror",
                    "p1": args.p1,
                    "p2": args.p2,
                    "games_requested": args.games,
                    "records": [json.loads(r.to_json()) for r in records],
                },
            )
        except Exception as exc:
            tb = traceback.format_exc()
            _log.exception(
                "evolve_worker: failed to write mirror result to %s",
                result_path,
            )
            try:
                _write_crash(result_path, exc, tb)
            except Exception:  # noqa: BLE001
                _log.exception(
                    "evolve_worker: also failed to write crash payload"
                )
            return 1
    finally:
        try:
            clear_current_round_state(state_path)
        except Exception:  # noqa: BLE001
            _log.exception(
                "evolve_worker: failed to clear round-state on exit"
            )

    summary = (
        f"evolve_worker[mirror]: worker={args.worker_id} run_id={run_id} "
        f"p1={args.p1} p2={args.p2} games={len(records)}/{args.games}"
    )
    _log.info(summary)
    print(summary)
    return 0


def _run_fitness_mode(args: argparse.Namespace, run_id: str) -> int:
    """Fitness-mode worker entry (original behavior, factored for #250)."""
    if args.parent is None or args.imp_json is None:
        msg = (
            "evolve_worker --mode fitness requires --parent and --imp-json"
        )
        _log.error(msg)
        try:
            _write_crash(args.result_path, ValueError(msg), msg)
        except Exception:  # noqa: BLE001
            _log.exception("evolve_worker: also failed to write crash payload")
        return 1

    state_path: Path = args.state_dir / f"evolve_round_{args.worker_id}.json"
    result_path: Path = args.result_path

    # Read imp payload up-front so a missing/invalid file fails loudly
    # before we touch any state.
    try:
        imp_text = args.imp_json.read_text(encoding="utf-8")
        imp = Improvement.from_json(imp_text)
    except Exception as exc:
        tb = traceback.format_exc()
        _log.exception("evolve_worker: failed to load imp from %s", args.imp_json)
        # Best-effort crash write so the dispatcher can read the failure.
        try:
            _write_crash(result_path, exc, tb)
        except Exception:  # noqa: BLE001
            _log.exception("evolve_worker: also failed to write crash payload")
        return 1

    payload = CurrentRoundPayload(
        generation=0,
        phase="fitness",
        imp_title=imp.title,
        imp_rank=imp.rank,
        worker_id=args.worker_id,
        run_id=run_id,
    )
    payload.reset_progress(args.games_per_eval)

    on_event = _make_event_callback(state_path, payload)
    # Initial write so the dispatcher can see "this slot is alive" before
    # the first game finishes.
    write_current_round_state(state_path, payload)

    # Outer try/finally guarantees the round-state file is cleared on
    # every exit path: success, crash-write success, crash-write failure,
    # AND KeyboardInterrupt / SystemExit propagation. Otherwise an
    # exception in ``atomic_write_json`` (disk full, perm denied) would
    # leave ``active=True`` and the dispatcher would misclassify this
    # slot as alive forever.
    try:
        try:
            # ``dev_apply_fn=spawn_dev_subagent`` mirrors the serial path's
            # default at scripts/evolve.py::run_loop. Without it, dev-type
            # imps (Claude code-change proposals) hit
            # ``apply_improvement``'s ``NotImplementedError`` and the worker
            # exits 1 — Decision-D-7's `crash` bucket masks the real cause.
            result: FitnessResult = run_fitness_eval(
                args.parent,
                imp,
                games=args.games_per_eval,
                map_name=args.map_name,
                game_time_limit=args.game_time_limit,
                hard_timeout=args.hard_timeout,
                on_event=on_event,
                dev_apply_fn=spawn_dev_subagent,
            )
        except KeyboardInterrupt:
            # Let Ctrl+C / SIGINT propagate so the dispatcher's signal
            # handlers (Step 3) terminate the worker promptly. The outer
            # ``finally`` clears the round-state file before unwinding.
            raise
        except Exception as exc:
            tb = traceback.format_exc()
            _log.exception(
                "evolve_worker: run_fitness_eval crashed (worker=%d)",
                args.worker_id,
            )
            try:
                _write_crash(result_path, exc, tb)
            except Exception:  # noqa: BLE001
                _log.exception(
                    "evolve_worker: also failed to write crash payload"
                )
            return 1

        # Success: write the FitnessResult atomically. If this raises
        # (disk full, perm denied), the outer ``finally`` still clears
        # the round-state file.
        try:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(result_path, json.loads(result.to_json()))
        except Exception as exc:
            tb = traceback.format_exc()
            _log.exception(
                "evolve_worker: failed to write FitnessResult to %s",
                result_path,
            )
            # Best-effort crash payload so the dispatcher sees the failure.
            try:
                _write_crash(result_path, exc, tb)
            except Exception:  # noqa: BLE001
                _log.exception(
                    "evolve_worker: also failed to write crash payload"
                )
            return 1
    finally:
        try:
            clear_current_round_state(state_path)
        except Exception:  # noqa: BLE001
            _log.exception(
                "evolve_worker: failed to clear round-state on exit"
            )

    summary = (
        f"evolve_worker: worker={args.worker_id} run_id={run_id} "
        f"parent={result.parent} candidate={result.candidate} "
        f"imp={imp.title!r} bucket={result.bucket} "
        f"wins={result.wins_candidate}-{result.wins_parent}/{result.games}"
    )
    _log.info(summary)
    print(summary)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Execute one eval (fitness or mirror). Returns the process exit code."""
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Use explicit None-check so an empty-string ``--run-id ""`` is
    # preserved verbatim (not silently overridden by a falsy fallback).
    run_id: str = (
        args.run_id if args.run_id is not None else uuid.uuid4().hex[:8]
    )

    if args.mode == "mirror":
        return _run_mirror_mode(args, run_id)
    return _run_fitness_mode(args, run_id)


if __name__ == "__main__":
    sys.exit(main())
