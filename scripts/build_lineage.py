"""Build ``data/lineage.json`` — the persisted version DAG.

Models tab Step 2 deliverable. Walks ``bots/v*/manifest.json``,
cross-references ``data/improvement_log.json`` (advised) and
``data/evolve_results.jsonl`` (evolve) and ``data/selfplay_results.jsonl``
(self-play, forward-compat), and writes a single ``data/lineage.json``
with the schema documented in ``documentation/plans/models-tab-plan.md``::

    {
      "nodes": [
        {"id": "vN", "version": "vN", "race": "protoss",
         "harness_origin": "...", "parent": "vN-1"},
        ...
      ],
      "edges": [
        {"from": "vN-1", "to": "vN", "harness": "...",
         "improvement_title": "...", "ts": "...", "outcome": "promoted"},
        ...
      ]
    }

Pure Python; no third-party dependencies; ~1s for 11 versions.

Atomic-replace writes via ``<out>.tmp`` + fsync + ``os.replace``, with
bounded retry-with-backoff on Windows ``PermissionError`` (matches the
``feedback_evolve_windows_atomic_replace_race.md`` recipe used in
``src/orchestrator/evolve.py``'s ``_restore_pointer``).

Usage::

    python scripts/build_lineage.py [--out path]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger("build_lineage")

_VERSION_RE: re.Pattern[str] = re.compile(r"^v(\d+)$")

# Matches the retry recipe in ``src/orchestrator/evolve.py``
# (``_ATOMIC_REPLACE_RETRY_DELAYS``). Reused here verbatim — the helper
# itself is module-local in ``orchestrator.evolve`` and not exported,
# so we duplicate the small constant + retry loop rather than reach
# across module boundaries (this script must remain importable from
# tests without dragging in the orchestrator's heavier deps).
_ATOMIC_REPLACE_RETRY_DELAYS: tuple[float, ...] = (0.05, 0.1, 0.2, 0.4, 0.8)


def _repo_root() -> Path:
    """Resolve the repo root from this script's location.

    ``scripts/build_lineage.py`` lives one level under the repo root, so
    ``parent.parent`` lands at the repo root.
    """
    return Path(__file__).resolve().parent.parent


def _version_sort_key(name: str) -> tuple[int, str]:
    """Sort key that orders ``vN`` strings by integer N ascending.

    Returns ``(N, name)`` for ``v\\d+`` matches; falls back to
    ``(maxsize, name)`` for anything that does not match so foreign
    directory names (``current``, etc.) are stably sorted at the end.
    """
    m = _VERSION_RE.match(name)
    if m:
        return (int(m.group(1)), name)
    return (sys.maxsize, name)


def _read_json_file(path: Path) -> dict[str, Any] | None:
    """Read a JSON object from ``path``; return None on missing/invalid.

    Mirrors the helper in ``bots/v10/api.py`` (returns None for missing
    files, malformed JSON, and non-dict top-level values) so the build
    script and the API endpoint agree on what counts as 'present'.
    """
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file; return a list of dict rows (skipping malformed).

    Returns an empty list if the file is missing or unreadable. Lines
    that fail to parse or whose top-level value isn't a dict are
    silently skipped — matches ``_scan_versions_sync`` in api.py.
    """
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _collect_advised_index(
    cross_dir: Path,
) -> tuple[set[str], set[str], dict[str, str]]:
    """Index ``improvement_log.json`` for advised attribution.

    Returns ``(shas, versions, version_to_title)``. The first two are
    the same sets the api.py helper builds (so harness-origin derivation
    matches). The third maps a version name → first matching title so
    edges can be labeled.

    Today's improvement_log entries don't carry a ``git_sha`` or a
    ``new_version`` — the only reliable signal is ``files_changed``
    paths under ``bots/vN/``. We use that to populate
    ``version_to_title``.
    """
    shas: set[str] = set()
    versions: set[str] = set()
    version_to_title: dict[str, str] = {}

    payload = _read_json_file(cross_dir / "improvement_log.json")
    if payload is None:
        return shas, versions, version_to_title

    entries = payload.get("improvements", [])
    if not isinstance(entries, list):
        return shas, versions, version_to_title

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        sha = entry.get("git_sha") or entry.get("sha") or entry.get("commit")
        if isinstance(sha, str) and sha:
            shas.add(sha)
        title_raw = entry.get("title")
        title = title_raw if isinstance(title_raw, str) else None
        files = entry.get("files_changed", [])
        if isinstance(files, list):
            for fp in files:
                if not isinstance(fp, str):
                    continue
                m = re.match(r"^bots/(v\d+)/", fp)
                if m:
                    v = m.group(1)
                    versions.add(v)
                    if title and v not in version_to_title:
                        version_to_title[v] = title
    return shas, versions, version_to_title


def _collect_evolve_index(
    cross_dir: Path,
) -> tuple[set[str], set[str], dict[str, str]]:
    """Index ``evolve_results.jsonl`` for evolve attribution.

    Returns ``(shas, versions, version_to_title)``. ``version_to_title``
    is populated from the matching ``stack-apply-pass`` row — multiple
    stacked imps are joined with ``" + "`` so the edge label conveys
    the full set without dropping information.
    """
    shas: set[str] = set()
    versions: set[str] = set()
    version_to_title: dict[str, str] = {}

    for row in _read_jsonl_rows(cross_dir / "evolve_results.jsonl"):
        new_v = row.get("new_version")
        if isinstance(new_v, str) and _VERSION_RE.match(new_v):
            versions.add(new_v)
            outcome = row.get("outcome")
            if outcome == "stack-apply-pass" and new_v not in version_to_title:
                titles_raw = row.get("stacked_titles")
                if isinstance(titles_raw, list):
                    titles = [t for t in titles_raw if isinstance(t, str)]
                    if titles:
                        version_to_title[new_v] = " + ".join(titles)
        sha = row.get("git_sha") or row.get("sha") or row.get("commit")
        if isinstance(sha, str) and sha:
            shas.add(sha)
    return shas, versions, version_to_title


def _collect_selfplay_index(cross_dir: Path) -> tuple[set[str], set[str]]:
    """Index ``selfplay_results.jsonl`` for self-play attribution.

    Today's schema (``SelfPlayRecord``) only logs matches, not
    promotions — see api.py for the full rationale. We still scan
    forward-compat keys (``new_version`` / ``version`` / sha-shaped
    values) so a future promotion-emitting harness lights up without
    a script change.
    """
    shas: set[str] = set()
    versions: set[str] = set()
    for row in _read_jsonl_rows(cross_dir / "selfplay_results.jsonl"):
        new_v = row.get("new_version") or row.get("version")
        if isinstance(new_v, str) and _VERSION_RE.match(new_v):
            versions.add(new_v)
        sha = row.get("git_sha") or row.get("sha") or row.get("commit")
        if isinstance(sha, str) and sha:
            shas.add(sha)
    return shas, versions


def _derive_harness_origin(
    *,
    version: str,
    sha: str | None,
    advised_shas: set[str],
    advised_versions: set[str],
    evolve_shas: set[str],
    evolve_versions: set[str],
    selfplay_shas: set[str],
    selfplay_versions: set[str],
) -> str:
    """Pick a harness origin string for ``version``.

    Precedence matches ``_scan_versions_sync`` in api.py: evolve →
    advised → self-play → manual. Keep the two implementations in sync
    if either changes.
    """
    if (sha and sha in evolve_shas) or version in evolve_versions:
        return "evolve"
    if (sha and sha in advised_shas) or version in advised_versions:
        return "advised"
    if (sha and sha in selfplay_shas) or version in selfplay_versions:
        return "self-play"
    return "manual"


def build_lineage(repo_root: Path) -> dict[str, Any]:
    """Pure-function lineage build. Returns the JSON payload as a dict.

    No I/O writes; tests can call this directly to assert the structure
    matches expectations without setting up tempfiles.
    """
    bots_dir = repo_root / "bots"
    cross_dir = repo_root / "data"

    advised_shas, advised_versions, advised_titles = _collect_advised_index(cross_dir)
    evolve_shas, evolve_versions, evolve_titles = _collect_evolve_index(cross_dir)
    selfplay_shas, selfplay_versions = _collect_selfplay_index(cross_dir)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    if not bots_dir.is_dir():
        return {"nodes": nodes, "edges": edges}

    # Walk version directories in version-number order so the output is
    # deterministic even when the filesystem returns directories in
    # creation-time order.
    for child in sorted(bots_dir.iterdir(), key=lambda p: _version_sort_key(p.name)):
        if not child.is_dir() or child.name == "current":
            continue
        if not _VERSION_RE.match(child.name):
            continue
        manifest = _read_json_file(child / "manifest.json")
        if manifest is None:
            continue

        version_name = child.name
        sha_raw = manifest.get("git_sha")
        sha = sha_raw if isinstance(sha_raw, str) and sha_raw else None
        parent_raw = manifest.get("parent")
        parent = parent_raw if isinstance(parent_raw, str) and parent_raw else None
        ts_raw = manifest.get("timestamp")
        ts = ts_raw if isinstance(ts_raw, str) else ""

        # #269: prefer ``manifest.extra.harness_origin`` /
        # ``manifest.extra.improvement_title`` when present. Stamped at
        # promotion time by ``_rewrite_manifest_parent`` (and similar
        # hooks for advised/self-play in future). The manifest is
        # git-tracked, so attribution survives fresh-run truncation of
        # ``data/evolve_results.jsonl``. Falls through to the existing
        # JSONL-derived attribution when ``extra`` is absent or carries
        # an unrecognized harness_origin (so legacy manifests behave as
        # they do today).
        extra_raw = manifest.get("extra")
        extra = extra_raw if isinstance(extra_raw, dict) else {}
        manifest_harness_raw = extra.get("harness_origin")
        manifest_harness = (
            manifest_harness_raw
            if manifest_harness_raw in {"evolve", "advised", "manual", "self-play"}
            else None
        )
        manifest_imp_raw = extra.get("improvement_title")
        manifest_imp = (
            manifest_imp_raw
            if isinstance(manifest_imp_raw, str) and manifest_imp_raw
            else None
        )

        if manifest_harness is not None:
            harness_origin = manifest_harness
        else:
            harness_origin = _derive_harness_origin(
                version=version_name,
                sha=sha,
                advised_shas=advised_shas,
                advised_versions=advised_versions,
                evolve_shas=evolve_shas,
                evolve_versions=evolve_versions,
                selfplay_shas=selfplay_shas,
                selfplay_versions=selfplay_versions,
            )

        nodes.append({
            "id": version_name,
            "version": version_name,
            "race": "protoss",
            "harness_origin": harness_origin,
            "parent": parent,
        })

        # Skip the synthetic root edge for v0 (parent is null) and for
        # any orphaned manifest whose parent field is missing/blank.
        if parent is None:
            continue

        if manifest_imp is not None:
            improvement_title = manifest_imp
        elif harness_origin == "manual":
            improvement_title = "manual"
        elif harness_origin == "evolve":
            improvement_title = evolve_titles.get(version_name, "—")
        elif harness_origin == "advised":
            improvement_title = advised_titles.get(version_name, "—")
        else:
            improvement_title = "—"

        edges.append({
            "from": parent,
            "to": version_name,
            "harness": harness_origin,
            "improvement_title": improvement_title,
            "ts": ts,
            "outcome": "promoted",
        })

    # Sort edges by timestamp then by ``to`` so re-runs are
    # byte-identical regardless of directory iteration order.
    edges.sort(key=lambda e: (e["ts"], e["to"]))

    return {"nodes": nodes, "edges": edges}


def _atomic_write_json(payload: dict[str, Any], out_path: Path) -> None:
    """Write ``payload`` to ``out_path`` via tempfile + fsync + os.replace.

    Implements the retry-with-backoff recipe documented in
    ``feedback_evolve_windows_atomic_replace_race.md``. On Windows the
    backend ``--serve`` polls files in ``data/`` so a direct write can
    race against an open read handle and surface as ``PermissionError``;
    we retry up to 5 times with the standard backoff sequence and then
    propagate the original error so the caller can decide how to react.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")

    # Stable JSON output — sort_keys + 2-space indent + trailing newline
    # so re-running on identical state produces byte-identical output
    # (the idempotency contract relies on this).
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"

    # Write to a temp sibling, fsync the file before the rename so a
    # crash mid-rename leaves a complete temp file rather than a
    # truncated final file.
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            # Some filesystems (notably DrvFS under WSL) reject fsync
            # on plain files. Best-effort only.
            pass

    # Retry-with-backoff on Windows PermissionError (open read handle
    # race; see the matching retry in src/orchestrator/evolve.py).
    last_exc: PermissionError | None = None
    for delay in _ATOMIC_REPLACE_RETRY_DELAYS:
        try:
            os.replace(tmp, out_path)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(delay)
    # Final attempt: surface the PermissionError to the caller if it
    # still fails after the bounded retries.
    try:
        os.replace(tmp, out_path)
    except PermissionError:
        if last_exc is not None:
            raise last_exc from None
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the lineage DAG from manifests and harness logs.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path (default: <repo>/data/lineage.json)",
    )
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    out_path = (
        Path(args.out).resolve()
        if args.out is not None
        else repo_root / "data" / "lineage.json"
    )

    payload = build_lineage(repo_root)
    _atomic_write_json(payload, out_path)

    print(
        f"build_lineage: wrote {len(payload['nodes'])} nodes / "
        f"{len(payload['edges'])} edges to {out_path}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
