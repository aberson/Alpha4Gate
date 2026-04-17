"""Snapshot the current bot version to a new ``bots/vN+1/`` directory.

Usage::

    uv run python scripts/snapshot_bot.py
    uv run python scripts/snapshot_bot.py --name v_experiment
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Snapshot the current bot version to a new bots/vN+1/ directory.",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Override the version name (default: auto-increment vN+1).",
    )
    args = parser.parse_args(argv)

    from orchestrator.snapshot import snapshot_current

    try:
        result = snapshot_current(name=args.name)
    except (FileNotFoundError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
