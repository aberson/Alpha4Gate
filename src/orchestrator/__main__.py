"""CLI entry point for ``python -m orchestrator.registry``.

Subcommands:
    list  — print all registered bot versions, one per line.
    show  — print the manifest JSON for a specific version.

Usage::

    python -m orchestrator.registry list
    python -m orchestrator.registry show v0
"""

from __future__ import annotations

import argparse
import sys

from orchestrator.registry import get_manifest, list_versions


def _cmd_list(args: argparse.Namespace) -> int:
    """Print registered versions, one per line."""
    for v in list_versions():
        print(v)
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    """Print manifest JSON for the given version."""
    try:
        manifest = get_manifest(args.version)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(manifest.to_json())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.registry",
        description="Alpha4Gate bot version registry CLI.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List all registered bot versions.")

    show_parser = sub.add_parser("show", help="Show manifest for a version.")
    show_parser.add_argument("version", help="Version name (e.g. v0)")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    handlers = {"list": _cmd_list, "show": _cmd_show}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
