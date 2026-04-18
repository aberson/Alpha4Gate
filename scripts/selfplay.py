"""CLI wrapper for the subprocess self-play runner (Phase 3).

Usage::

    # Head-to-head mode:
    python scripts/selfplay.py --p1 v0 --p2 v0 --games 20 --map Simple64

    # PFSP-lite mode (trainee = current version):
    python scripts/selfplay.py --sample pfsp --pool v0,v1,v2 --games 40 --map Simple64

    # Disable the viewer (e.g. for batch jobs, CI, or non-Windows hosts):
    python scripts/selfplay.py --p1 v0 --p2 v1 --games 20 --no-viewer

See ``src/orchestrator/selfplay.py`` for the batch runner implementation.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestrator.contracts import SelfPlayRecord

# Ensure repo root is on sys.path so ``orchestrator`` is importable.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python scripts/selfplay.py",
        description="Alpha4Gate subprocess self-play runner.",
    )
    parser.add_argument(
        "--map",
        default="Simple64",
        help="SC2 map name (default: Simple64)",
    )
    parser.add_argument(
        "--games",
        type=int,
        required=True,
        help="Number of games to play",
    )
    parser.add_argument(
        "--game-time-limit",
        type=int,
        default=300,
        help="In-game time limit in seconds (default: 300)",
    )
    parser.add_argument(
        "--hard-timeout",
        type=float,
        default=600.0,
        help="Wall-clock timeout per game in seconds (default: 600)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional RNG seed for SC2",
    )
    parser.add_argument(
        "--results-path",
        type=Path,
        default=None,
        help="Path to JSONL results file (default: data/selfplay_results.jsonl)",
    )

    # Head-to-head mode.
    h2h = parser.add_argument_group("head-to-head mode")
    h2h.add_argument("--p1", default=None, help="Player 1 version (e.g. v0)")
    h2h.add_argument("--p2", default=None, help="Player 2 version (e.g. v1)")

    # PFSP mode.
    pfsp = parser.add_argument_group("PFSP mode")
    pfsp.add_argument(
        "--sample",
        choices=["pfsp"],
        default=None,
        help="Sampling strategy (currently only 'pfsp')",
    )
    pfsp.add_argument(
        "--pool",
        default=None,
        help="Comma-separated opponent pool (e.g. v0,v1,v2)",
    )
    pfsp.add_argument(
        "--win-rates",
        default=None,
        help="JSON file mapping version -> win rate (optional; cold-start if absent)",
    )
    pfsp.add_argument(
        "--temperature",
        type=float,
        default=1.0,
        help="PFSP temperature parameter (default: 1.0)",
    )

    # Viewer (Step 4).
    viewer = parser.add_argument_group("viewer")
    viewer.add_argument(
        "--no-viewer",
        action="store_true",
        help=(
            "Skip the pygame self-play viewer. Forced on non-Windows. "
            "Use for batch/CI jobs or when the viewer extras are absent."
        ),
    )
    viewer.add_argument(
        "--bar",
        choices=["top", "side"],
        default="top",
        help="Stats bar placement (default: top)",
    )
    viewer.add_argument(
        "--size",
        choices=["large", "small"],
        default="large",
        help="SC2 pane size preset (default: large)",
    )
    viewer.add_argument(
        "--background",
        type=_background_key,
        default="random",
        help=(
            "Background key from img_backgrounds/ or 'random' "
            "(default: random)"
        ),
    )

    return parser


def _background_key(value: str) -> str:
    """argparse ``type=`` validator for ``--background``.

    Accepts ``"random"`` plus any key from
    ``selfplay_viewer.backgrounds.list_backgrounds()``. When the viewer
    extras are not installed (``selfplay_viewer`` import fails), returns
    the value unchanged so non-Windows / no-extras callers can still
    parse the CLI for ``--no-viewer`` batch jobs.
    """
    try:
        from selfplay_viewer.backgrounds import list_backgrounds
    except Exception:
        # Viewer extras not installed; don't gate the CLI on them.
        return value

    if value == "random":
        return value
    valid = set(list_backgrounds())
    if not valid:
        # img_backgrounds/ not present in this checkout — accept any
        # value so --no-viewer / non-Windows callers are not blocked.
        return value
    if value not in valid:
        raise argparse.ArgumentTypeError(
            f"unknown --background key {value!r}; "
            f"valid: {sorted(valid) + ['random']}"
        )
    return value


def _viewer_enabled(no_viewer_flag: bool) -> bool:
    """Return True iff the pygame viewer should be launched.

    Forced off on non-Windows (D8 in the plan) with an INFO log so the
    operator knows why. Forced off when ``--no-viewer`` is passed.
    """
    log = logging.getLogger(__name__)
    if no_viewer_flag:
        return False
    if sys.platform != "win32":
        log.info("Viewer disabled on non-Windows platform")
        return False
    return True


def _warn_about_viewer_flags_when_disabled(args: argparse.Namespace) -> None:
    """INFO-log ignored viewer-only flags when ``--no-viewer`` is active.

    The viewer-only flags (``--bar``, ``--size``, ``--background``) are
    harmless when passed alongside ``--no-viewer`` but silently ignoring
    non-default values hides operator typos. Log once per offending
    flag and continue; never error.
    """
    log = logging.getLogger(__name__)
    parser = build_parser()
    defaults = vars(parser.parse_args(["--games", "1", "--no-viewer"]))

    for flag, attr in (("--bar", "bar"), ("--size", "size"), ("--background", "background")):
        if getattr(args, attr) != defaults[attr]:
            log.info("%s=%r ignored (viewer disabled)", flag, getattr(args, attr))


def _run_pfsp_mode(args: argparse.Namespace, use_viewer: bool) -> int:
    """Execute the PFSP sampling loop, optionally driving the viewer.

    One viewer instance is reused across every one-game ``run_batch``
    call so the user sees a single container window for the entire
    sweep, with SC2 pairs slotting in and out between games.
    """
    from orchestrator.registry import current_version
    from orchestrator.selfplay import pfsp_sample, run_batch

    pool = [v.strip() for v in args.pool.split(",")]
    trainee = current_version()
    win_rates: dict[str, float] = {}
    if args.win_rates:
        win_rates = json.loads(Path(args.win_rates).read_text())

    print(f"PFSP mode: trainee={trainee}, pool={pool}, games={args.games}")

    all_records: list[SelfPlayRecord] = []
    stop_event = threading.Event()

    def _sweep(
        on_game_start: Any | None = None,
        on_game_end: Any | None = None,
    ) -> list[SelfPlayRecord]:
        for i in range(args.games):
            # Cooperative PFSP-level cancellation. run_batch gets its own
            # check as well, but we additionally bail here so a viewer
            # close doesn't spend time picking the next opponent.
            if stop_event.is_set():
                break
            opponent = pfsp_sample(pool, win_rates, temperature=args.temperature)
            print(f"  game {i + 1}/{args.games}: {trainee} vs {opponent}")
            records = run_batch(
                trainee,
                opponent,
                1,
                args.map,
                game_time_limit=args.game_time_limit,
                hard_timeout=args.hard_timeout,
                seed=args.seed,
                results_path=args.results_path,
                on_game_start=on_game_start,
                on_game_end=on_game_end,
                stop_event=stop_event,
            )
            all_records.extend(records)
            if records:
                r = records[0]
                status = r.winner if r.winner else ("ERROR" if r.error else "draw")
                print(f"    -> {status}")
        return all_records

    if use_viewer:
        from selfplay_viewer import SelfPlayViewer

        viewer = SelfPlayViewer(
            bar=args.bar, size=args.size, background=args.background
        )
        viewer.run_with_batch(
            lambda: _sweep(
                on_game_start=viewer.on_game_start,
                on_game_end=viewer.on_game_end,
            ),
            stop_event=stop_event,
        )
    else:
        _sweep()

    _print_summary(all_records)
    return 0 if any(r.error is None for r in all_records) else 1


def _run_h2h_mode(args: argparse.Namespace, use_viewer: bool) -> int:
    """Execute head-to-head mode, optionally driving the viewer."""
    from orchestrator.selfplay import run_batch

    print(f"Head-to-head: {args.p1} vs {args.p2}, {args.games} games on {args.map}")

    records: list[SelfPlayRecord]

    if use_viewer:
        from selfplay_viewer import SelfPlayViewer

        viewer = SelfPlayViewer(
            bar=args.bar, size=args.size, background=args.background
        )
        stop_event = threading.Event()
        result = viewer.run_with_batch(
            lambda: run_batch(
                args.p1,
                args.p2,
                args.games,
                args.map,
                game_time_limit=args.game_time_limit,
                hard_timeout=args.hard_timeout,
                seed=args.seed,
                results_path=args.results_path,
                on_game_start=viewer.on_game_start,
                on_game_end=viewer.on_game_end,
                stop_event=stop_event,
            ),
            stop_event=stop_event,
        )
        # run_with_batch returns whatever batch_fn returned. If the user
        # closes the viewer before the batch even starts, result is None
        # (the batch thread never got scheduled to completion).
        records = result if result is not None else []
    else:
        records = run_batch(
            args.p1,
            args.p2,
            args.games,
            args.map,
            game_time_limit=args.game_time_limit,
            hard_timeout=args.hard_timeout,
            seed=args.seed,
            results_path=args.results_path,
        )

    for i, r in enumerate(records):
        status = r.winner if r.winner else ("ERROR" if r.error else "draw")
        swap_note = " (seats swapped)" if r.seat_swap else ""
        print(f"  game {i + 1}: {status}{swap_note}")

    _print_summary(records)
    return 0 if any(r.error is None for r in records) else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = build_parser()
    args = parser.parse_args(argv)

    use_viewer = _viewer_enabled(args.no_viewer)
    if not use_viewer:
        _warn_about_viewer_flags_when_disabled(args)

    if args.sample == "pfsp":
        if not args.pool:
            parser.error("--sample pfsp requires --pool")
        return _run_pfsp_mode(args, use_viewer)

    # Head-to-head mode.
    if not args.p1 or not args.p2:
        parser.error("head-to-head mode requires --p1 and --p2")
    return _run_h2h_mode(args, use_viewer)


def _print_summary(records: list[object]) -> None:
    """Print a win/loss/draw/error summary."""
    from orchestrator.contracts import SelfPlayRecord

    versions: set[str] = set()
    wins: dict[str, int] = {}
    draws = 0
    errors = 0

    for r in records:
        assert isinstance(r, SelfPlayRecord)
        versions.add(r.p1_version)
        versions.add(r.p2_version)
        if r.error:
            errors += 1
        elif r.winner:
            wins[r.winner] = wins.get(r.winner, 0) + 1
        else:
            draws += 1

    print("\n--- Summary ---")
    for v in sorted(versions):
        print(f"  {v}: {wins.get(v, 0)} wins")
    print(f"  draws: {draws}")
    if errors:
        print(f"  errors: {errors}")
    print(f"  total: {len(records)} games")


if __name__ == "__main__":
    sys.exit(main())
