"""CLI for the parallel-lineage registry (Phase EH Step EH.1).

Reads/writes ``data/lineages.json`` via ``orchestrator.lineages``. This is
``write_lineages``' first production caller: before this script the only way
a registry could exist was an operator hand-authoring JSON, which made the
multi-lineage scheduler effectively unreachable.

Structural sibling of ``scripts/baseline.py`` — same ``_REPO_ROOT``
preamble, same sub-parser shape, same ``error: {exc}`` / exit-code
conventions — with two deliberate exceptions, both commented at their call
sites: ``remove`` on an absent id is idempotent (exit **0**, not 1), and
every advisory note -- ``list`` on an empty registry, ``add``'s
multi-lineage engagement warning -- goes to **stderr** so each command's
stdout stays machine-parseable. Validation and the registry write live in
``orchestrator.lineages.register_lineage``, the way ``baseline.py``
delegates to ``register_baseline``; in particular a re-``add`` of an
existing id updates ``head_version`` in place and preserves the fields this
CLI cannot express (``pool_path``, ``parent_chain``, ``created_at``,
``status``).

Note the plural: this script owns ``data/lineages.json`` (the evolve
lineage registry). It never touches ``data/lineage.json`` (**singular**),
which is the unrelated version DAG written by ``scripts/build_lineage.py``.

Usage::

    uv run python scripts/lineage.py add line-2 v10
    uv run python scripts/lineage.py list
    uv run python scripts/lineage.py remove line-2
    uv run python scripts/lineage.py --path other.json list   # before the verb
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

# Ensure repo root's ``src`` is on sys.path so ``orchestrator`` is importable
# when the script is invoked directly (``python scripts/lineage.py``). The
# ``orchestrator.lineages`` import is deferred past this setup (E402 waiver).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from orchestrator.lineages import (  # noqa: E402
    default_lineages_path,
    load_lineages,
    register_lineage,
    write_lineages,
)

# Every registry failure this CLI can surface. ``json.JSONDecodeError``
# subclasses ``ValueError``, so malformed JSON, a non-object payload, a
# non-object entry and a rejected ``register_lineage`` argument all land in
# the ``ValueError`` arm; ``KeyError`` covers an entry missing
# ``head_version``; ``OSError`` covers an unreadable or unwritable
# ``--path``. The sibling catches ``ValueError`` only, which is why a
# malformed registry tracebacks there.
_REGISTRY_ERRORS = (ValueError, KeyError, OSError)


def _force_utf8_streams() -> None:
    """Make stdout/stderr lossy-UTF-8 so a *diagnostic* can never fail.

    On Windows a redirected stdout is ``cp1252``
    (``.claude/rules/windows-shell.md``), so printing a non-ASCII character
    raises ``UnicodeEncodeError``. Both mutating branches print *after* the
    registry write has landed, which would report failure for a durable
    change — and a non-empty ``data/lineages.json`` engages multi-lineage
    scheduling for every later bare evolve run. ``register_lineage``'s slug
    guard closes the path where such an id gets in; this is the
    belt-and-braces half, and it also keeps ``list`` readable over a
    registry that was hand-authored before the guard existed.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):  # pragma: no cover - defensive
                pass


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python scripts/lineage.py",
        description=(
            "Manage the evolve parallel-lineage registry "
            "(data/lineages.json)."
        ),
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help=(
            "Registry path (default: repo-root data/lineages.json). "
            "Must precede the subcommand."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Add or update a lineage.")
    add.add_argument("lineage_id", help="Lineage slug (registry key).")
    add.add_argument(
        "head_version",
        help="Version the lineage's next generation snapshots from.",
    )

    sub.add_parser("list", help="List registered lineages.")

    remove = sub.add_parser(
        "remove", help="Remove a lineage by id (idempotent)."
    )
    remove.add_argument("lineage_id", help="Lineage slug to remove.")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    _force_utf8_streams()
    parser = build_parser()
    args = parser.parse_args(argv)

    path: Path = args.path if args.path is not None else default_lineages_path()

    if args.command == "add":
        try:
            # Probed before the write, and only for the default path, since
            # that is the only target the engagement note applies to.
            dormant = args.path is None and not load_lineages(path)
            lineage = register_lineage(path, args.lineage_id, args.head_version)
        except _REGISTRY_ERRORS as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(
            f"registered {lineage.lineage_id} -> {lineage.head_version} "
            f"(status={lineage.status}, created_at={lineage.created_at})"
        )
        if dormant:
            # stderr, per this file's advisory convention (same reason as
            # ``list``'s empty-registry note): it keeps ``add``'s stdout at
            # exactly one parseable ``registered ...`` line instead of 1 or
            # 4 depending on whether the registry was dormant.
            print(
                f"note: a non-empty {path} engages multi-lineage scheduling "
                "for every evolve run, including the launcher/observatory "
                "button; 'lineage.py remove <id>' for each id disengages it",
                file=sys.stderr,
            )
        return 0

    if args.command == "list":
        try:
            registry = load_lineages(path)
        except _REGISTRY_ERRORS as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if not registry:
            # Keep stdout empty so callers can parse it unconditionally; the
            # operator-facing note goes to stderr.
            print(f"(no lineages registered at {path})", file=sys.stderr)
            return 0
        for row_id, lineage in registry.items():
            print(
                f"{row_id}\t{lineage.head_version}\t{lineage.status}\t"
                f"{lineage.created_at}"
            )
        return 0

    if args.command == "remove":
        lineage_id: str = args.lineage_id
        try:
            registry = load_lineages(path)
            if lineage_id not in registry:
                # Idempotent by design: ``remove`` is the documented
                # ``--lineages`` off-switch / teardown escape hatch, so a
                # repeat call is a success, not an error. Nothing is
                # written, so a no-op remove never creates a registry file
                # as a side effect. ``registered:`` names what IS present so
                # a typo'd id is visible despite the rc 0.
                print(
                    f"(no lineage named {lineage_id!r} in {path}; "
                    f"registered: {sorted(registry)})",
                    file=sys.stderr,
                )
                return 0
            del registry[lineage_id]
            # Writing the (possibly empty) registry back is what makes
            # removing the LAST lineage disengage the scheduler.
            write_lineages(path, registry)
        except _REGISTRY_ERRORS as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        # Printed after the write, but ``_force_utf8_streams`` makes this
        # unable to raise, so it cannot report failure for a landed change.
        print(f"removed {lineage_id}")
        return 0

    parser.error(f"unknown command: {args.command!r}")
    return 2  # pragma: no cover — parser.error raises SystemExit


if __name__ == "__main__":
    sys.exit(main())
