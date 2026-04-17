"""Full-stack snapshot tool (Phase 2).

Copies the *source version directory* (resolved from ``bots/current/current.txt``)
to ``bots/vN+1/`` with a new ``VERSION`` file and a fresh ``manifest.json``
carrying the parent, git SHA, timestamp, Elo snapshot, and feature/action-space
fingerprints. Updates ``bots/current/current.txt`` to point at the new version.

The copy is a full ``shutil.copytree`` — checkpoints, training.db, reward_logs,
everything. Each version is independently bootable via ``python -m bots.vN``.
"""

from __future__ import annotations

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


def snapshot_current(name: str | None = None) -> Path:
    """Snapshot the current bot version to a new ``bots/<name>/`` directory.

    Args:
        name: Version name for the snapshot. Auto-increments from the highest
            existing ``vN`` if not provided.

    Returns:
        Path to the newly created version directory.

    Raises:
        FileNotFoundError: If the source version directory does not exist.
        FileExistsError: If the target version directory already exists.
    """
    source_version = current_version()
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

    # Copy the full source tree
    shutil.copytree(source_dir, target_dir)

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

    # Update current.txt to point at the new version
    pointer = _repo_root() / "bots" / "current" / "current.txt"
    pointer.write_text(new_name, encoding="utf-8")

    return target_dir
