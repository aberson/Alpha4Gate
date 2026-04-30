"""Full-stack snapshot tool (Phase 2).

Copies the *source version directory* (resolved from ``bots/current/current.txt``)
to ``bots/vN+1/`` with a new ``VERSION`` file and a fresh ``manifest.json``
carrying the parent, git SHA, timestamp, Elo snapshot, and feature/action-space
fingerprints. Updates ``bots/current/current.txt`` to point at the new version.

The copy walks the full source tree (checkpoints, training.db, reward_logs,
everything) via ``_drvfs_safe_copytree`` — a ``shutil.copytree`` substitute
that skips ``chmod`` and ``copystat`` so evolve's candidate snapshots succeed
under WSL on a ``/mnt/c``-mounted repo. Each version is independently bootable
via ``python -m bots.vN``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.contracts import Manifest
from orchestrator.registry import (
    _repo_root,
    current_version,
    get_manifest,
    get_version_dir,
    list_versions,
)

__all__ = ["snapshot_current"]


def _drvfs_safe_copytree(src: Path, dst: Path) -> None:
    """Recursive copy that preserves only file *content* — no chmod/copystat.

    ``shutil.copytree`` defaults to ``copy2``, which calls ``chmod`` per file
    and ``copystat`` per directory.  Both raise ``[Errno 1] Operation not
    permitted`` on ``/mnt/c`` (NTFS-via-DrvFS) when running from WSL because
    DrvFS does not represent POSIX permissions.  evolve's per-eval candidate
    snapshots crash on this; see ``feedback_evolve_drvfs_copy2_fails``.

    Mode bits, atime, and mtime are not load-bearing for evolve's scratch
    ``cand_*/`` directories — Python's import system does not consult file
    mode, and SB3 checkpoint loading uses ``zipfile`` (also mode-agnostic).
    On Windows native and pure-Linux ext4, this helper is a strict subset
    of ``copytree``'s behavior; on DrvFS it is the only thing that works.
    """
    dst.mkdir(parents=True, exist_ok=False)
    for src_path in src.iterdir():
        dst_path = dst / src_path.name
        if src_path.is_dir():
            _drvfs_safe_copytree(src_path, dst_path)
        else:
            shutil.copyfile(src_path, dst_path)


def _drvfs_safe_rmtree(path: Path) -> None:
    """Recursive delete that survives DrvFS and Windows read-only files.

    ``shutil.rmtree``'s built-in retry path on a read-only file calls
    ``os.chmod`` to clear the bit before re-attempting ``unlink``.  On
    ``/mnt/c`` (NTFS-via-DrvFS) the chmod itself raises EPERM, so the
    retry fails and the whole rmtree aborts — leaving evolve's
    ``bots/cand_*/`` scratch dirs orphaned on disk after every rollback.

    This walks the tree depth-first calling ``unlink``/``rmdir`` directly.
    On PermissionError we attempt a single ``os.chmod(0o777)`` retry and
    swallow chmod failures (DrvFS will refuse, ext4 will accept).
    Symlinks are unlinked, never followed.

    Mirrors ``_drvfs_safe_copytree``: on Windows native and pure ext4 it
    is a strict subset of ``shutil.rmtree``; on DrvFS it is the only path
    that completes.
    """
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_dir():
        _drvfs_unlink(path)
        return
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            _drvfs_safe_rmtree(child)
        else:
            _drvfs_unlink(child)
    try:
        path.rmdir()
    except PermissionError:
        try:
            os.chmod(path, 0o777)
        except OSError:
            pass
        path.rmdir()


def _drvfs_unlink(path: Path) -> None:
    """``unlink`` with one chmod-and-retry, swallowing chmod failures."""
    try:
        path.unlink()
    except PermissionError:
        try:
            os.chmod(path, 0o777)
        except OSError:
            pass
        path.unlink()


def _next_version_name() -> str:
    """Return the next ``vN`` name by incrementing the highest existing version.

    Scans ``list_versions()`` for entries matching ``v<int>`` and returns
    ``v<max+1>``. Falls back to ``v1`` if no numeric versions exist.
    """
    versions = list_versions()
    max_n = -1
    for v in versions:
        m = re.fullmatch(r"v(\d+)", v)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"v{max_n + 1}"


def _git_sha() -> str:
    """Return the current HEAD git SHA (short form)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(_repo_root()),
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _rewrite_imports(target_dir: Path, old_pkg: str, new_pkg: str) -> int:
    """Rewrite every ``bots.<old_pkg>`` reference to ``bots.<new_pkg>`` across *target_dir*.

    "Aggressive" mode (issue #236, user direction 2026-04-28): rewrites
    ANY occurrence of ``bots.<old_pkg>`` in any ``.py`` file — imports,
    string literals (``"bots.v3.api:app"``), subprocess argv tuples,
    process-detection tags, log messages, comments, docstrings, prose.
    A snapshot is meant to be an independently-bootable copy of the
    source version, and stale string-literal self-references break:

    * Backend serving — ``uvicorn.run("bots.v3.api:app", ...)`` keeps
      the parent module loaded under the new name's directory.
    * Subprocess respawn — ``[sys.executable, "-m", "bots.v3.runner"]``
      launches the wrong version.
    * Process detection — ``_OUR_CMDLINE_TAGS = ("bots.v3", ...)``
      misses "our" processes after promotion.
    * Logger names — ``logging.getLogger("bots.v3.debug")`` writes to
      the wrong logger tree.

    Returns the number of files touched. Single-pass ``re.sub`` over
    each file's full text using a ``\\b``-anchored regex; this catches
    every documented victim shape without an AST walk.

    Cross-version references (``bots.other_version``, ``bots.current``,
    ``bots.v3`` when ``old_pkg == "v4"``) are preserved — the regex
    only matches the literal source token with both-side word
    boundaries, so ``bots.v3`` is not a substring of ``bots.v30``.
    """
    pattern = re.compile(rf"\bbots\.{re.escape(old_pkg)}\b")
    replacement = f"bots.{new_pkg}"
    touched = 0
    for py in target_dir.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new_text, n = pattern.subn(replacement, text)
        if n:
            py.write_text(new_text, encoding="utf-8")
            touched += 1
    return touched


def snapshot_current(
    name: str | None = None,
    source: str | None = None,
    *,
    update_pointer: bool = True,
) -> Path:
    """Snapshot a bot version to a new ``bots/<name>/`` directory.

    Args:
        name: Version name for the snapshot. Auto-increments from the highest
            existing ``vN`` if not provided.
        source: Source version name to snapshot from. Defaults to the current
            pointer (``bots/current/current.txt``). Pass an explicit version
            (e.g. ``"v0"``) to fold a non-current branch into a new version
            without first flipping the current pointer. The new manifest's
            ``parent`` field records this value verbatim.
        update_pointer: If True (default) write the new snapshot name to
            ``bots/current/current.txt`` so the new version becomes the
            active one. If False, do NOT touch the pointer. Used by
            :func:`orchestrator.evolve.run_fitness_eval` (and other
            parallel-evolve primitives) where the snapshot is ephemeral
            scratch and the caller does not want it to become the active
            version. See ``documentation/plans/evolve-parallelization-plan.md``
            decision D-2.

    Returns:
        Path to the newly created version directory.

    Raises:
        FileNotFoundError: If the source version directory does not exist.
        FileExistsError: If the target version directory already exists.
    """
    source_version = source if source is not None else current_version()
    source_dir = get_version_dir(source_version)

    if not source_dir.is_dir():
        raise FileNotFoundError(
            f"Source version directory does not exist: {source_dir}"
        )

    new_name = name if name is not None else _next_version_name()
    target_dir = get_version_dir(new_name)

    if target_dir.exists():
        raise FileExistsError(
            f"Target version directory already exists: {target_dir}"
        )

    # Copy the full source tree.  Uses _drvfs_safe_copytree (not
    # shutil.copytree) so evolve's per-eval candidate snapshots succeed
    # under WSL when the repo lives on /mnt/c — see the helper's docstring.
    _drvfs_safe_copytree(source_dir, target_dir)

    # Rewrite absolute imports `from bots.<source>.*` -> `from bots.<new>.*`
    # so the snapshot is actually self-contained at the Python level.
    # Without this, files in the snapshot re-import the SOURCE version's
    # code at runtime and any edit the caller makes to the snapshot is
    # silently ignored. See docstring of :func:`_rewrite_imports`.
    _rewrite_imports(target_dir, source_version, new_name)

    # Write new VERSION file
    (target_dir / "VERSION").write_text(new_name, encoding="utf-8")

    # Build fresh manifest inheriting from parent
    parent_manifest = get_manifest(source_version)
    new_manifest = Manifest(
        version=new_name,
        best=parent_manifest.best,
        previous_best=parent_manifest.previous_best,
        parent=source_version,
        git_sha=_git_sha(),
        timestamp=datetime.now(UTC).isoformat(),
        elo=parent_manifest.elo,
        fingerprint=parent_manifest.fingerprint,
    )
    (target_dir / "manifest.json").write_text(
        new_manifest.to_json(), encoding="utf-8"
    )

    # Update current.txt to point at the new version. Skipped when
    # ``update_pointer=False`` so callers (e.g. evolve fitness eval) can
    # produce ephemeral scratch snapshots without flipping the
    # process-global pointer that every other process in the repo shares.
    # See ``documentation/plans/evolve-parallelization-plan.md`` decision
    # D-2 for the rationale.
    if update_pointer:
        pointer = _repo_root() / "bots" / "current" / "current.txt"
        pointer.write_text(new_name, encoding="utf-8")

    return target_dir
