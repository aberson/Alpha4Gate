"""Thin entry point: ``python -m bots.jev.v1``.

Step 201 ships only ``--validate-policy``: it loads the packaged manifest and
policy through the production loader/validator, prints a one-line summary with
the canonical policy hash and exits 0, or prints every node-specific issue and
exits 1. Single-match execution (map/opponent/limits flags, SC2 launch) arrives
with the shared runner in Phase JV Step 202; until then a bare invocation fails
loudly instead of pretending to play.

This path imports only the standard library and ``jev`` (no burnysc2).
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from typing import NoReturn

from bots.jev.v1 import load_policy
from jev.contracts import render_lines, render_text
from jev.policy import PolicyError, describe_bundle

#: Nonzero, and distinct from argparse's usage-error code 2.
MATCH_UNAVAILABLE_EXIT = 1


class TerminalSafeArgumentParser(argparse.ArgumentParser):
    """ArgumentParser whose every emitted message is terminal-safe.

    argparse echoes argv in its errors (unrecognized arguments, invalid values),
    so ``error`` escapes the message on one line and ``exit`` renders whatever it
    prints. The usage-error exit code (2) is unchanged.
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {render_text(message)}\n")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if message:
            sys.stderr.write(render_lines(message))
        raise SystemExit(status)


def build_parser() -> argparse.ArgumentParser:
    parser = TerminalSafeArgumentParser(
        prog="python -m bots.jev.v1",
        description="Jev v1 decision-graph player.",
    )
    parser.add_argument(
        "--validate-policy",
        action="store_true",
        help="validate the packaged policy and manifest, print the policy hash, and exit",
    )
    parser.add_argument(
        "--policy-file",
        type=Path,
        default=None,
        help="with --validate-policy: validate this candidate policy document instead",
    )
    return parser


def _make_streams_encoding_safe() -> None:
    """Never let an unencodable character (e.g. a CJK path on cp1252) crash output."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")


def main(argv: list[str] | None = None) -> int:
    _make_streams_encoding_safe()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.policy_file is not None and not args.validate_policy:
        parser.error("--policy-file requires --validate-policy")
    if not args.validate_policy:
        print(
            render_text(
                "jev: match execution is not available yet (it arrives with the Phase JV "
                "Step 202 runner); use --validate-policy"
            ),
            file=sys.stderr,
        )
        return MATCH_UNAVAILABLE_EXIT
    try:
        bundle = load_policy(args.policy_file)
    except PolicyError as exc:
        print(render_lines(f"jev: {exc}"), file=sys.stderr)
        return 1
    print(render_text(describe_bundle(bundle)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
