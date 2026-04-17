"""Version discovery + per-version data-path resolution.

Public surface (Step 1.7):

- :func:`current_version` — read ``bots/current/current.txt`` and return the
  active version string (e.g. ``"v0"``).
- :func:`get_version_dir` — map a version name to its ``bots/<v>/`` directory.
- :func:`resolve_data_path` — resolve a data-file name to either
  ``bots/<v>/data/<filename>`` (if it exists) or the legacy repo-root
  ``data/<filename>``, with a "write to per-version, read from either" bias for
  files that don't exist yet.
- :func:`get_manifest` — load ``bots/<v>/manifest.json`` into a
  :class:`orchestrator.contracts.Manifest`.

Implementation notes:

* ``registry.py`` does **NOT** import ``bots.current`` or ``bots.<version>``.
  Doing so would trigger the :class:`MetaPathFinder` installed by
  ``bots/current/__init__.py`` and could re-enter this module during version
  discovery. The pointer file is read directly via :mod:`pathlib`.
* :func:`_repo_root` is a private test seam — tests monkeypatch it to redirect
  the resolver at a temporary tree.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.contracts import Manifest

__all__ = [
    "current_version",
    "get_data_dir",
    "get_manifest",
    "get_version_dir",
    "resolve_data_path",
]


def _repo_root() -> Path:
    """Return the repository root directory.

    ``registry.py`` lives at ``src/orchestrator/registry.py``, so the repo root
    is three ``parent`` hops above this file. Exposed as a module-level
    function (rather than a module constant) so tests can
    ``monkeypatch.setattr("orchestrator.registry._repo_root", lambda: tmp_path)``
    to redirect every public function at a temporary tree in one call.
    """
    return Path(__file__).resolve().parent.parent.parent


def current_version() -> str:
    """Return the active bot version string from ``bots/current/current.txt``.

    The pointer lives INSIDE the ``bots/current/`` package (not at
    ``bots/current.txt``) so it ships inside the wheel when the project is
    installed via ``pip`` / ``uv pip install`` — see ``bots/current/__init__.py``
    for the full rationale.

    Raises:
        FileNotFoundError: if the pointer file is missing.
        ValueError: if the pointer file is present but empty / whitespace-only.
    """
    pointer = _repo_root() / "bots" / "current" / "current.txt"
    if not pointer.is_file():
        raise FileNotFoundError(f"bots/current/current.txt not found at {pointer}")
    name = pointer.read_text(encoding="utf-8").strip()
    if not name:
        raise ValueError(f"bots/current/current.txt is empty at {pointer}")
    return name


def get_version_dir(version: str) -> Path:
    """Return the absolute path to ``bots/<version>/``.

    Existence is NOT verified — the caller decides whether a missing directory
    is an error (e.g. a stale manifest) or expected (e.g. pre-seed).
    """
    return _repo_root() / "bots" / version


def resolve_data_path(filename: str, version: str | None = None) -> Path:
    """Resolve ``filename`` to a concrete data path.

    Priority:

    1. ``<repo_root>/bots/<v>/data/<filename>`` if that file already exists.
    2. ``<repo_root>/data/<filename>`` if the legacy repo-root file exists.
    3. ``<repo_root>/bots/<v>/data/<filename>`` if neither exists, so that
       callers which write a new file land under the per-version tree instead
       of the legacy root. This is what lets Step 1.8's migration roll in one
       file at a time without a flag day.

    ``version`` defaults to :func:`current_version` when omitted.
    """
    v = current_version() if version is None else version
    per_version = _repo_root() / "bots" / v / "data" / filename
    fallback = _repo_root() / "data" / filename
    if per_version.exists():
        return per_version
    if fallback.exists():
        return fallback
    return per_version


def get_data_dir(version: str | None = None) -> Path:
    """Return the data directory for ``version`` with legacy fallback.

    Priority:

    1. ``<repo_root>/bots/<v>/data/`` if that directory already exists.
    2. ``<repo_root>/data/`` otherwise (legacy location).

    Unlike :func:`resolve_data_path`, this returns a *directory* that callers
    typically pass wholesale to code that joins its own filenames inside
    (e.g. ``config.Settings.data_dir``). The legacy-first fallback lets Step 1.8
    migrate ``src/alpha4gate/config.py``'s default before any files physically
    move — production reads from ``data/`` until the per-version directory is
    seeded, then flips transparently.

    ``version`` defaults to :func:`current_version` when omitted.
    """
    v = current_version() if version is None else version
    per_version = _repo_root() / "bots" / v / "data"
    if per_version.is_dir():
        return per_version
    return _repo_root() / "data"


def get_manifest(version: str) -> Manifest:
    """Load and validate ``bots/<version>/manifest.json``.

    Raises:
        FileNotFoundError: if the manifest file is missing. Note that
            ``get_manifest("v0")`` is expected to raise until Step 1.8 seeds
            the initial manifest.

    Any error raised by :meth:`Manifest.from_json` (e.g. ``KeyError`` for a
    missing required field, ``json.JSONDecodeError`` for malformed input)
    propagates unchanged.
    """
    path = get_version_dir(version) / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"manifest.json not found for version {version!r} at {path}")
    return Manifest.from_json(path.read_text(encoding="utf-8"))
