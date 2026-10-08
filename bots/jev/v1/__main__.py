"""Thin entry point: ``python -m bots.jev.v1``.

Runs the shared Jev runner (:mod:`jev.runner`) with this package's policy:
``--validate-policy`` validates the packaged manifest and policy and exits (no
SC2); otherwise one match against the built-in AI is played with the plan's D6
defaults. See :func:`jev.runner.main` for the flags and exit codes.

The validation path imports only the standard library and ``jev`` (no burnysc2).
"""

from __future__ import annotations

import sys

from bots.jev.v1 import load_policy
from jev import runner

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    return runner.main(argv, load_policy=load_policy, prog="python -m bots.jev.v1")


if __name__ == "__main__":
    sys.exit(main())
