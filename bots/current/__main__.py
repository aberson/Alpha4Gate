"""Entry point for ``python -m bots.current``.

Reads the same ``current.txt`` pointer as :mod:`bots.current.__init__` and
delegates to ``python -m bots.<version>`` via :func:`runpy.run_module` with
``alter_sys=True`` so ``sys.argv`` and ``sys.path[0]`` are set up exactly as
they would be if the operator had invoked ``python -m bots.<version>``
directly.

The ``current.txt`` reader is duplicated from :mod:`bots.current.__init__`
rather than imported, because importing ``bots.current`` would trigger the
``sys.modules[__name__] = <target>`` aliasing in ``__init__.py`` — and mixing
that with :func:`runpy.run_module` risks the delegated module seeing a
partially-initialized parent package. The duplication is ~10 lines; keeping
``__main__.py`` self-sufficient avoids a subtle class of import-time ordering
bugs.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def _read_version() -> str:
    pointer = Path(__file__).resolve().parent / "current.txt"
    if not pointer.is_file():
        raise SystemExit(f"bots/current/current.txt not found at {pointer}")
    name = pointer.read_text(encoding="utf-8").strip()
    if not name:
        raise SystemExit("bots/current/current.txt is empty")
    version_dir = Path(__file__).resolve().parent.parent / name
    if not version_dir.is_dir():
        raise SystemExit(
            f'bots/current/current.txt names "{name}" but bots/{name}/ '
            "does not exist"
        )
    return name


def main() -> None:
    version = _read_version()
    # ``alter_sys=True`` replaces ``sys.modules["__main__"]`` with the target
    # module and fixes up ``sys.argv[0]`` so argparse's ``prog=`` and any
    # ``__file__`` references inside the delegated module behave correctly.
    runpy.run_module(f"bots.{version}", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
    # runpy.run_module doesn't exit, so propagate the target's exit code
    # semantics by doing nothing here — if the delegated module raised
    # SystemExit, Python's normal teardown handles it; otherwise exit 0.
    sys.exit(0)
