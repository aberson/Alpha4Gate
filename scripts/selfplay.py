"""CLI wrapper for the subprocess self-play runner (Phase 3).

Usage::

    # Head-to-head mode:
    python scripts/selfplay.py --p1 v0 --p2 v0 --games 20 --map Simple64

    # PFSP-lite mode (trainee = current version):
    python scripts/selfplay.py --sample pfsp --pool v0,v1,v2 --games 40 --map Simple64

See ``src/orchestrator/selfplay.py`` for the batch runner implementation.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

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

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = build_parser()
    args = parser.parse_args(argv)

    from orchestrator.selfplay import pfsp_sample, run_batch

    if args.sample == "pfsp":
        # PFSP mode.
        if not args.pool:
            parser.error("--sample pfsp requires --pool")

        from orchestrator.registry import current_version

        pool = [v.strip() for v in args.pool.split(",")]
        trainee = current_version()
        win_rates: dict[str, float] = {}
        if args.win_rates:
            win_rates = json.loads(Path(args.win_rates).read_text())

        print(f"PFSP mode: trainee={trainee}, pool={pool}, games={args.games}")

        all_records = []
        for i in range(args.games):
            opponent = pfsp_sample(
                pool, win_rates, temperature=args.temperature
            )
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
            )
            all_records.extend(records)
            if records:
                r = records[0]
                status = r.winner if r.winner else ("ERROR" if r.error else "draw")
                print(f"    -> {status}")

        _print_summary(all_records)
        return 0 if any(r.error is None for r in all_records) else 1

    # Head-to-head mode.
    if not args.p1 or not args.p2:
        parser.error("head-to-head mode requires --p1 and --p2")

    print(f"Head-to-head: {args.p1} vs {args.p2}, {args.games} games on {args.map}")

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
