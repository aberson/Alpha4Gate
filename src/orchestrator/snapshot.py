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


_IMPORT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # `from bots.<old>.X import Y`  ->  `from bots.<new>.X import Y`
    (re.compile(r"^(\s*from\s+bots\.)([A-Za-z0-9_]+)(\.[^\s]+\s+import\b)"), r"\1__NEW__\3"),
    # `from bots.<old> import X`    ->  `from bots.<new> import X`
    (re.compile(r"^(\s*from\s+bots\.)([A-Za-z0-9_]+)(\s+import\b)"), r"\1__NEW__\3"),
    # `import bots.<old>.X`          ->  `import bots.<new>.X`
    (re.compile(r"^(\s*import\s+bots\.)([A-Za-z0-9_]+)(\.[^\s]+)"), r"\1__NEW__\3"),
    # `import bots.<old>`            ->  `import bots.<new>`
    (re.compile(r"^(\s*import\s+bots\.)([A-Za-z0-9_]+)(\s*(?:#.*)?$)"), r"\1__NEW__\3"),
)


def _rewrite_imports(target_dir: Path, old_pkg: str, new_pkg: str) -> int:
    """Rewrite ``bots.<old_pkg>`` imports to ``bots.<new_pkg>`` across *target_dir*.

    Without this, a snapshot at ``bots/cand_xyz/`` still has absolute
    imports like ``from bots.v0.army_coherence import X`` — at runtime
    those resolve to the ORIGINAL ``bots/v0/`` code, not the snapshot.
    Any change the sub-agent makes to ``bots/cand_xyz/*.py`` (other than
    the entrypoint ``__main__.py``'s bare module-level imports) is
    silently ignored because the in-process call graph flows through
    ``bots.v0.*``.

    Returns the number of files touched. Line-by-line regex rewrite
    (not AST) because:

    * The rewrite targets only four concrete ``import``/``from`` shapes
      at line starts. False positives would require a bots.<old> token
      appearing at column 0 inside a raw string, which is astronomically
      unlikely in this codebase.
    * AST-based rewrite is ~20× slower per file; per-snapshot it would
      add seconds while each round is already 30+ minutes of games.
    * Keeping the rewrite regex-shaped makes the matched shapes trivially
      inspectable in the diff if a future snapshot surfaces a miss.

    Only old_pkg matches are rewritten — an import of
    ``bots.other_version.*`` is left untouched, so a hand-crafted
    candidate that pulls from a sibling version keeps doing so.
    """
    touched = 0
    for py in target_dir.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new_lines: list[str] = []
        file_changed = False
        for line in text.splitlines(keepends=True):
            out = line
            for pat, repl in _IMPORT_PATTERNS:
                m = pat.match(out)
                if m and m.group(2) == old_pkg:
                    out = pat.sub(repl.replace("__NEW__", new_pkg), out)
                    file_changed = True
                    break
            new_lines.append(out)
        if file_changed:
            py.write_text("".join(new_lines), encoding="utf-8")
            touched += 1
    return touched


def snapshot_current(
    name: str | None = None, source: str | None = None
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

    # Copy the full source tree
    shutil.copytree(source_dir, target_dir)

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

    # Update current.txt to point at the new version
    pointer = _repo_root() / "bots" / "current" / "current.txt"
    pointer.write_text(new_name, encoding="utf-8")

    return target_dir
