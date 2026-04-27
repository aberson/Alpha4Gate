"""Snapshot a bot version to a new ``bots/vN+1/`` directory.

Usage::

    uv run python scripts/snapshot_bot.py
    uv run python scripts/snapshot_bot.py --name v_experiment
    uv run python scripts/snapshot_bot.py --from v0           # fold v0 into next vN+1
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Snapshot a bot version to a new bots/vN+1/ directory.",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Override the version name (default: auto-increment vN+1).",
    )
    parser.add_argument(
        "--from",
        dest="source",
        default=None,
        help=(
            "Snapshot from this source version instead of the current "
            "pointer (e.g. 'v0' to fold v0 into the next version without "
            "first flipping bots/current/current.txt)."
        ),
    )
    args = parser.parse_args(argv)

    from orchestrator.snapshot import snapshot_current

    try:
        result = snapshot_current(name=args.name, source=args.source)
    except (FileNotFoundError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
