"""CLI wrapper for the Elo ladder (Phase 4).

Usage::

    python scripts/ladder.py update [--games 20] [--map Simple64] [--versions v0,v1]
    python scripts/ladder.py show [--json]
    python scripts/ladder.py compare v0 v1 [--games 20] [--map Simple64] [--dry-run]
    python scripts/ladder.py replay [--jsonl data/selfplay_results.jsonl]

See ``src/orchestrator/ladder.py`` for the implementation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path so ``orchestrator`` is importable.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Elo ladder — cross-version ranking + promotion gate",
    )
    sub = parser.add_subparsers(dest="command")

    # --- update ---
    p_update = sub.add_parser("update", help="Run round-robin and update ladder")
    p_update.add_argument("--games", type=int, default=20, help="Games per pair")
    p_update.add_argument("--map", default="Simple64", help="SC2 map name")
    p_update.add_argument(
        "--versions",
        default=None,
        help="Comma-separated version list (default: top-3 + current)",
    )

    # --- show ---
    p_show = sub.add_parser("show", help="Print current standings")
    p_show.add_argument("--json", action="store_true", dest="as_json", help="Output raw JSON")

    # --- compare ---
    p_compare = sub.add_parser("compare", help="Head-to-head promotion check")
    p_compare.add_argument("v1", help="Candidate version")
    p_compare.add_argument("v2", help="Parent version")
    p_compare.add_argument("--games", type=int, default=20, help="Games to play")
    p_compare.add_argument("--map", default="Simple64", help="SC2 map name")
    p_compare.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute from existing JSONL without running new games",
    )

    # --- replay ---
    p_replay = sub.add_parser("replay", help="Rebuild ladder from JSONL")
    p_replay.add_argument(
        "--jsonl",
        default=str(_REPO_ROOT / "data" / "selfplay_results.jsonl"),
        help="Path to JSONL results file",
    )

    return parser


def _print_standings_table(standings: dict[str, object]) -> None:
    """Print standings as a formatted table."""
    from orchestrator.contracts import LadderEntry

    entries: list[LadderEntry] = sorted(
        standings.values(),  # type: ignore[arg-type]
        key=lambda e: e.elo,  # type: ignore[union-attr]
        reverse=True,
    )
    if not entries:
        print("(no standings)")
        return

    print(f"{'Rank':<6}{'Version':<12}{'Elo':<10}{'Games':<8}{'Updated'}")
    print("-" * 56)
    for i, e in enumerate(entries, 1):
        print(f"{i:<6}{e.version:<12}{e.elo:<10.1f}{e.games_played:<8}{e.last_updated}")


def cmd_update(args: argparse.Namespace) -> int:
    """Run round-robin ladder update."""
    from orchestrator.ladder import ladder_update

    versions = args.versions.split(",") if args.versions else None
    standings = ladder_update(versions, args.games, args.map)
    print(f"\nLadder updated ({len(standings)} versions):\n")
    _print_standings_table(standings)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Show current ladder standings."""
    from orchestrator.ladder import load_ladder

    standings, h2h = load_ladder()
    if args.as_json:

        # Re-serialize to get the canonical JSON format.
        payload: dict[str, object] = {
            "standings": {
                v: {
                    "elo": round(e.elo, 1),
                    "games_played": e.games_played,
                    "last_updated": e.last_updated,
                }
                for v, e in standings.items()
            },
            "head_to_head": h2h,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_standings_table(standings)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Run promotion comparison between two versions."""
    if args.dry_run:
        # Replay only records matching these two versions from JSONL.
        from orchestrator.contracts import SelfPlayRecord
        from orchestrator.ladder import DEFAULT_ELO, update_elo

        jsonl_path = _REPO_ROOT / "data" / "selfplay_results.jsonl"
        if not jsonl_path.is_file():
            print(f"No JSONL file at {jsonl_path}", file=sys.stderr)
            return 1

        standings: dict[str, object] = {}
        h2h: dict[str, dict[str, dict[str, int]]] = {}
        text = jsonl_path.read_text(encoding="utf-8")
        count = 0
        for line in text.strip().splitlines():
            rec = SelfPlayRecord.from_json(line)
            if {rec.p1_version, rec.p2_version} == {args.v1, args.v2}:
                update_elo(standings, h2h, rec)  # type: ignore[arg-type]
                count += 1

        if count == 0:
            print(f"No games found between {args.v1} and {args.v2} in JSONL")
            return 1

        e1 = standings.get(args.v1)  # type: ignore[union-attr]
        e2 = standings.get(args.v2)  # type: ignore[union-attr]
        delta = (e1.elo if e1 else DEFAULT_ELO) - (e2.elo if e2 else DEFAULT_ELO)  # type: ignore[union-attr]
        print(f"Dry-run: {count} historical games between {args.v1} and {args.v2}")
        print(f"Elo delta: {delta:+.1f} (threshold: 10.0)")
        print(f"Would promote: {'yes' if delta >= 10.0 else 'no'}")
        return 0

    from orchestrator.ladder import check_promotion

    result = check_promotion(args.v1, args.v2, args.games, args.map)
    print("\nPromotion gate result:")
    print(f"  Candidate: {result.candidate}")
    print(f"  Parent:    {result.parent}")
    print(f"  Elo delta: {result.elo_delta:+.1f}")
    print(f"  Games:     {result.games_played}")
    print(f"  Promoted:  {result.promoted}")
    print(f"  Reason:    {result.reason}")
    return 0 if result.promoted else 1


def cmd_replay(args: argparse.Namespace) -> int:
    """Rebuild ladder from JSONL."""
    from orchestrator.ladder import ladder_replay

    jsonl_path = Path(args.jsonl)
    if not jsonl_path.is_file():
        print(f"JSONL file not found: {jsonl_path}", file=sys.stderr)
        return 1

    standings = ladder_replay(jsonl_path)
    print(f"\nLadder rebuilt from {jsonl_path} ({len(standings)} versions):\n")
    _print_standings_table(standings)
    return 0


def main() -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    dispatch = {
        "update": cmd_update,
        "show": cmd_show,
        "compare": cmd_compare,
        "replay": cmd_replay,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
