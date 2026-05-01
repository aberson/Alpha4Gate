"""Thin pointer package that aliases ``bots.current`` to the active bot version.

Reads the one-line version string from ``current.txt`` (stored **inside** this
package, NOT at ``bots/current.txt`` as the plan doc originally suggested), then
wires ``bots.current`` — including every submodule — to the target version tree
(currently ``bots.v0``). The aliasing guarantees that::

    from bots.current.learning.database import TrainingDB
    from bots.v0.learning.database       import TrainingDB as Direct
    assert TrainingDB is Direct   # same class object, not a re-import

which matters for isinstance checks and for any downstream code that stashes
the class in a registry keyed by identity.

Why ``current.txt`` lives inside the package, not one directory up:

* When ``pip install`` / ``uv pip install`` packages this project as a wheel,
  hatch only copies files that are part of a declared package directory.
  ``bots/current.txt`` sitting *next to* the package would silently fail to
  ship in the wheel, and the first ``import bots.current`` in a site-packages
  install would raise ``ImportError`` with no obvious cause.
* Keeping the pointer inside ``bots/current/`` makes the package self-contained
  and wheel-safe. The file is resolved relative to ``__file__`` (never
  cwd-relative — see ``memory/feedback_backend_wrong_cwd_silent.md``).

Why we install a ``MetaPathFinder`` on top of the simpler
``sys.modules[__name__] = target`` trick:

* ``sys.modules`` aliasing alone makes ``import bots.current`` return the
  target module object, but Python's submodule-import machinery still creates
  a **fresh** ``bots.current.learning`` module the first time it's imported
  (it walks ``target.__path__`` but stores the result under the
  ``bots.current.learning`` name). That means
  ``bots.current.learning.database.TrainingDB`` and
  ``bots.v0.learning.database.TrainingDB`` end up as two distinct class
  objects with the same source — ``isinstance`` checks across the alias
  boundary silently break.
* The finder below resolves any ``bots.current[.x.y.z]`` request to the
  corresponding ``bots.v0[.x.y.z]`` module and returns the *existing* module
  object, so identity is preserved.

The aliasing is the entire mechanism — no caching, no version enumeration, no
fallbacks. ``orchestrator.registry.current_version()`` (Step 1.7) will expose
the same pointer via a public API.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import CodeType, ModuleType


def _read_version() -> str:
    """Read and validate ``current.txt`` from next to this module.

    Resolves the path relative to ``__file__`` so the lookup is immune to the
    caller's current working directory. Raises :class:`ImportError` (not
    :class:`FileNotFoundError` / :class:`ValueError`) so a broken pointer
    surfaces as a normal import failure to any ``import bots.current`` site.
    """
    pointer = Path(__file__).resolve().parent / "current.txt"
    if not pointer.is_file():
        raise ImportError(f"bots/current/current.txt not found at {pointer}")
    name = pointer.read_text(encoding="utf-8").strip()
    if not name:
        raise ImportError("bots/current/current.txt is empty")
    # Validate the target version dir exists before attempting the import so
    # the error message names the offending value, not just a generic
    # "No module named bots.<name>".
    version_dir = Path(__file__).resolve().parent.parent / name
    if not version_dir.is_dir():
        raise ImportError(
            f'bots/current/current.txt names "{name}" but bots/{name}/ '
            "does not exist"
        )
    return name


class _CurrentAliasFinder(importlib.abc.MetaPathFinder):
    """Map ``bots.current[.x.y.z]`` import requests to ``bots.<version>[.x.y.z]``.

    Installed on :data:`sys.meta_path` once at import time. The finder does
    **not** load anything itself — it imports the real target module (so the
    real loader runs and caches the module under its canonical name) and then
    returns a spec whose loader simply returns that already-loaded object.
    """

    def __init__(self, version: str) -> None:
        self._version = version
        self._prefix = f"bots.{version}"

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname != "bots.current" and not fullname.startswith("bots.current."):
            return None
        # Do NOT intercept ``bots.current.__main__`` — ``python -m bots.current``
        # must run the ``__main__.py`` that lives in this package (it does the
        # runpy delegation to ``bots.<version>`` itself). Letting the normal
        # file-system loader handle it keeps ``get_code`` wired up for runpy.
        if fullname == "bots.current.__main__":
            return None
        # Translate bots.current[.x.y] -> bots.<version>[.x.y].
        suffix = fullname[len("bots.current"):]
        actual_name = self._prefix + suffix
        try:
            actual = importlib.import_module(actual_name)
        except ImportError:
            return None
        loader = _AliasLoader(actual)
        spec = importlib.util.spec_from_loader(fullname, loader)
        if spec is None:
            return None
        # Mark as a package when the target is one so submodule-of-submodule
        # imports (bots.current.learning.database) keep flowing through this
        # finder.
        if hasattr(actual, "__path__"):
            spec.submodule_search_locations = list(actual.__path__)
        return spec


class _AliasLoader(importlib.abc.Loader):
    """Loader that returns a pre-imported module instead of executing one."""

    def __init__(self, target: ModuleType) -> None:
        self._target = target

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        return self._target

    def exec_module(self, module: ModuleType) -> None:
        # Already fully initialized by the real import; nothing to do.
        return None

    def get_code(self, fullname: str) -> CodeType | None:
        # ``runpy._get_module_details`` calls ``loader.get_code(modname)`` to
        # fetch the code object for ``python -m`` execution. Without this,
        # ``python -m bots.current.runner`` (and any other submodule) raises
        # ``AttributeError: '_AliasLoader' object has no attribute 'get_code'``.
        # Delegate to the real target module's loader so the alias is
        # transparent to ``-m`` invocations.
        target_spec = getattr(self._target, "__spec__", None)
        if target_spec is None or target_spec.loader is None:
            return None
        loader = target_spec.loader
        if not hasattr(loader, "get_code"):
            return None
        return loader.get_code(self._target.__name__)  # type: ignore[no-any-return]


_version = _read_version()
# Install the finder BEFORE importing the target so that any transitive
# ``import bots.current.x`` triggered during the target's own import (there
# currently aren't any, but future bot versions might) goes through the alias.
sys.meta_path.insert(0, _CurrentAliasFinder(_version))
_target = importlib.import_module(f"bots.{_version}")

# Replace this module in ``sys.modules`` with the resolved target so that
# ``bots.current`` *is* ``bots.<version>`` for code that grabs the package
# object directly (e.g. ``import bots.current as bc; bc.some_attr``).
sys.modules[__name__] = _target
