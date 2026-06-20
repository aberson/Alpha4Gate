"""CLI for the baseline-opponent registry (Phase EL Step 2).

Reads/writes ``data/baselines.json`` via ``orchestrator.baselines``.

Usage::

    uv run python scripts/baseline.py add v7-strong v7 --note "strong v7"
    uv run python scripts/baseline.py list
    uv run python scripts/baseline.py remove v7-strong
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure repo root's ``src`` is on sys.path so ``orchestrator`` is importable
# when the script is invoked directly (``python scripts/baseline.py``). The
# ``orchestrator.baselines`` import is deferred past this setup (E402 waiver).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from orchestrator.baselines import (  # noqa: E402
    default_baselines_path,
    load_baselines,
    register_baseline,
    write_baselines,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python scripts/baseline.py",
        description=(
            "Manage the evolve baseline-opponent registry "
            "(data/baselines.json)."
        ),
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help=(
            "Registry path (default: repo-root data/baselines.json)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Add or update a baseline opponent.")
    add.add_argument("name", help="Baseline slug (registry key).")
    add.add_argument("version", help="Bot version that plays the baseline.")
    add.add_argument(
        "--note",
        default="",
        help="Free-text annotation (why this baseline matters).",
    )

    sub.add_parser("list", help="List registered baselines.")

    remove = sub.add_parser("remove", help="Remove a baseline by name.")
    remove.add_argument("name", help="Baseline slug to remove.")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    path: Path = args.path if args.path is not None else default_baselines_path()

    if args.command == "add":
        try:
            baseline = register_baseline(
                path, args.name, args.version, note=args.note
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(
            f"registered {baseline.name} -> {baseline.version} "
            f"(added_at={baseline.added_at})"
        )
        return 0

    if args.command == "list":
        registry = load_baselines(path)
        if not registry:
            print(f"(no baselines registered at {path})")
            return 0
        for name, baseline in registry.items():
            note = f"  # {baseline.note}" if baseline.note else ""
            print(f"{name}\t{baseline.version}\t{baseline.added_at}{note}")
        return 0

    if args.command == "remove":
        registry = load_baselines(path)
        if args.name not in registry:
            print(
                f"error: no baseline named {args.name!r} in {path}",
                file=sys.stderr,
            )
            return 1
        del registry[args.name]
        write_baselines(path, registry)
        print(f"removed {args.name}")
        return 0

    parser.error(f"unknown command: {args.command!r}")
    return 2  # pragma: no cover — parser.error raises SystemExit


if __name__ == "__main__":
    sys.exit(main())
