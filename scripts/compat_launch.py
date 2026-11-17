"""Launch one bot version with the SC2 compatibility shim installed first.

Usage (built by ``orchestrator.selfplay._build_bot_process``)::

    python scripts/compat_launch.py <version> [bot args ...]

This is ``python -m bots.<version> [bot args ...]`` with one difference: it
calls :func:`orchestrator.sc2_compat.install` before the version's own
``bots.<version>.__main__`` entry runs. The frozen trees (``bots/v0`` through
``bots/v12``) predate the shim, so on SC2 build 98310 they crash about 45
game-seconds in; launching them through this wrapper lets them play there
without editing any frozen tree.

The wrapper lives in ``scripts/`` rather than ``src/orchestrator/`` because
``src/orchestrator/`` must not import ``bots.*`` (MetaPathFinder loop).

Import path: ``python -m`` would put the working directory (the repo root,
for self-play) at ``sys.path[0]``. Running a script puts the script's own
directory there instead, so the wrapper replaces it with the repo root,
derived from this file's location rather than the working directory, and
makes sure ``src`` (the ``orchestrator`` package) is importable too.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"

USAGE = "usage: compat_launch.py <version> [bot args ...]"


def _fix_import_path() -> None:
    """Make ``bots`` and ``orchestrator`` importable as ``-m bots.<v>`` would."""
    script_dir = str(Path(__file__).resolve().parent)
    if sys.path and Path(sys.path[0] or ".").resolve() == Path(script_dir):
        sys.path[0] = str(_REPO_ROOT)
    elif str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    if str(_SRC) not in sys.path:
        sys.path.insert(1, str(_SRC))


def _valid_version(version: str) -> bool:
    """A version is a single package name under ``bots/`` (``v0``, ``v13``)."""
    return version.isidentifier()


def main(argv: list[str]) -> None:
    """Install the shim, then run ``bots.<argv[0]>`` with ``argv[1:]``."""
    if not argv or not _valid_version(argv[0]):
        print(USAGE, file=sys.stderr)
        raise SystemExit(2)
    version, bot_args = argv[0], argv[1:]

    _fix_import_path()

    from orchestrator import sc2_compat

    sc2_compat.install()

    module = f"bots.{version}"
    # The entry sees the same argv shape as ``python -m bots.<version> ...``.
    sys.argv = [module, *bot_args]
    runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main(sys.argv[1:])
