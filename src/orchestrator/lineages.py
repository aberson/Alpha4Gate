"""Parallel-lineage registry + round-robin scheduler for evolve (Phase EL).

The evolve loop (``scripts/evolve.py``) historically advances a single
lineage: every generation snapshots ``current_version()``, fitness-tests a
pool of imps, and (on promotion) flips ``bots/current/current.txt`` to the
new ``vN+1``. Phase EL lets the loop interleave generations across N
independent lineages, each with its own head version and pool, so the
overnight soak can explore divergent branches rather than one chain.

A *lineage* is a named branch of the version tree. Each carries:

- ``lineage_id`` — kebab/slug identifier (e.g. ``"main"``, ``"line-2"``).
- ``head_version`` — the version the next generation snapshots from
  (e.g. ``"v13"``); the lineage's live parent.
- ``pool_path`` — repo-relative or absolute path to this lineage's pool file.
- ``parent_chain`` — ordered ancestry (oldest first), for lineage display.
- ``created_at`` — ISO-8601 UTC, seconds resolution.
- ``status`` — ``"active"`` by default; the scheduler may later park a
  lineage (e.g. ``"exhausted"``) without removing it from the registry.

The registry is ``data/lineages.json`` — a JSON object keyed by
``lineage_id``. It is cross-version evolve state, so it lives at repo-root
``data/`` (NOT per-version ``bots/<v>/data/``); see
``.claude/rules/bot-runtime.md``. The whole ``data/`` dir is gitignored.

Back-compat
-----------

When ``data/lineages.json`` is absent or empty, the project behaves
exactly as before: a single implicit lineage ``main`` whose
``head_version`` is :func:`orchestrator.registry.current_version`. The
evolve loop only engages the multi-lineage scheduling path when
``--lineages > 1`` OR a non-empty ``data/lineages.json`` exists.

Public surface
--------------

- :class:`Lineage` — one branch's record (``to_json`` / ``from_json``
  mirror :class:`orchestrator.evolve.Improvement`).
- :func:`load_lineages` — read the registry from disk.
- :func:`write_lineages` — atomically persist the registry.
- :func:`load_or_default_lineages` — read the registry, falling back to a
  single implicit ``main`` lineage when absent/empty.
- :func:`next_lineage` — deterministic round-robin scheduler.
- :func:`default_lineages_path` — the canonical ``data/lineages.json`` path.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchestrator.registry import _repo_root, current_version

_log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_LINEAGE_ID",
    "Lineage",
    "default_lineages_path",
    "load_lineages",
    "load_or_default_lineages",
    "next_lineage",
    "write_lineages",
]


# Mirrors the Windows ``os.replace`` retry-backoff used by
# ``orchestrator.evolve._restore_pointer`` and
# ``scripts/evolve_round_state.atomic_write_json``. Kept identical so the
# lineage registry survives the same ``--serve`` polling race. We mirror
# rather than import ``scripts/evolve_round_state`` so this ``src/`` module
# stays free of a ``scripts/``-on-sys.path dependency (the test harness only
# puts ``src/`` on the path).
_ATOMIC_REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write *payload* as pretty, sorted JSON, atomically with retries.

    On Windows ``os.replace`` raises ``PermissionError`` when the backend
    ``--serve`` holds an open handle on the target; retry with backoff
    before the final (raising) attempt.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for delay in _ATOMIC_REPLACE_RETRY_DELAYS:
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(delay)
    tmp.replace(path)


DEFAULT_LINEAGE_ID = "main"


def _now_iso() -> str:
    """Return an ISO-8601 UTC timestamp (seconds resolution)."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Lineage:
    """One branch of the version tree scheduled by the evolve loop.

    Fields
    ------
    lineage_id:
        Kebab/slug identifier, unique within a registry (e.g. ``"main"``).
    head_version:
        Version the next generation of this lineage snapshots from
        (e.g. ``"v13"``).
    pool_path:
        Path (repo-relative or absolute, caller's choice) to this lineage's
        evolve pool file.
    parent_chain:
        Ordered ancestry of ``head_version`` (oldest first). Display-only.
    created_at:
        ISO-8601 UTC timestamp (seconds resolution). Defaults to "now".
    status:
        Scheduler status; ``"active"`` by default.
    """

    lineage_id: str
    head_version: str
    pool_path: str = ""
    parent_chain: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    status: str = "active"

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_json(cls, data: str | bytes) -> Lineage:
        payload = json.loads(data)
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Lineage:
        """Build a :class:`Lineage` from a decoded JSON object.

        Optional fields fall back to their dataclass defaults so a
        registry written by an older build (missing, say, ``parent_chain``)
        still loads. ``lineage_id`` and ``head_version`` are required.
        """
        return cls(
            lineage_id=payload["lineage_id"],
            head_version=payload["head_version"],
            pool_path=payload.get("pool_path", ""),
            parent_chain=list(payload.get("parent_chain", [])),
            created_at=payload.get("created_at") or _now_iso(),
            status=payload.get("status", "active"),
        )


def default_lineages_path() -> Path:
    """Return the canonical ``<repo_root>/data/lineages.json`` path.

    Cross-version evolve state lives at repo-root ``data/`` — NOT per-version
    ``bots/<v>/data/`` — per ``.claude/rules/bot-runtime.md``.
    """
    return _repo_root() / "data" / "lineages.json"


def load_lineages(path: Path) -> dict[str, Lineage]:
    """Load the lineage registry from *path*.

    Returns an empty dict when the file does not exist. The on-disk shape
    is a JSON object keyed by ``lineage_id`` (each value a serialized
    :class:`Lineage`). Insertion order from the JSON file is preserved
    (``json.loads`` keeps object key order, and ``dict`` is ordered).

    Raises:
        json.JSONDecodeError: if the file exists but is not valid JSON.
        KeyError: if an entry is missing a required field.
    """
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(
            f"lineages registry at {path} must be a JSON object keyed by "
            f"lineage_id; got {type(payload).__name__}"
        )
    registry: dict[str, Lineage] = {}
    for key, value in payload.items():
        if not isinstance(value, dict):
            raise ValueError(
                f"lineages registry entry {key!r} must be a JSON object; "
                f"got {type(value).__name__}"
            )
        # Tolerate a missing/blank lineage_id inside the value by trusting
        # the registry key (the key is authoritative).
        value.setdefault("lineage_id", key)
        registry[key] = Lineage.from_dict(value)
    return registry


def write_lineages(path: Path, registry: dict[str, Lineage]) -> None:
    """Atomically persist *registry* to *path* as a keyed JSON object.

    Uses the same write-``.tmp`` + ``os.replace``-with-retry-backoff pattern
    as ``orchestrator.evolve._restore_pointer`` and the evolve state-file
    writers, so the lineage registry survives the same Windows ``--serve``
    polling race.
    """
    payload: dict[str, Any] = {
        lineage_id: dataclasses.asdict(lineage)
        for lineage_id, lineage in registry.items()
    }
    _atomic_write_json(path, payload)


def load_or_default_lineages(path: Path) -> dict[str, Lineage]:
    """Load the registry, falling back to a single implicit ``main`` lineage.

    Back-compat helper: when ``data/lineages.json`` is absent or empty,
    return ``{"main": Lineage(lineage_id="main", head_version=<current>)}``
    where ``<current>`` is :func:`orchestrator.registry.current_version`.
    This is the single-lineage behavior the evolve loop had before
    Phase EL — the loop only diverges from it when a non-empty registry
    exists.
    """
    registry = load_lineages(path)
    if registry:
        return registry
    head = current_version()
    _log.info(
        "lineages: no registry at %s; using implicit single lineage %r "
        "at head %s",
        path,
        DEFAULT_LINEAGE_ID,
        head,
    )
    return {
        DEFAULT_LINEAGE_ID: Lineage(
            lineage_id=DEFAULT_LINEAGE_ID,
            head_version=head,
        )
    }


def next_lineage(
    registry: dict[str, Lineage], last_id: str | None
) -> str:
    """Return the lineage_id to schedule after *last_id* (round-robin).

    Ordering is the registry's insertion order (``dict`` iteration order,
    which mirrors the on-disk JSON key order). The scheduler wraps: the
    successor of the last id is the first id. When *last_id* is ``None`` or
    not present in the registry, the first lineage is returned.

    Raises:
        ValueError: if *registry* is empty.
    """
    ids = list(registry.keys())
    if not ids:
        raise ValueError("next_lineage: registry is empty")
    if last_id is None or last_id not in registry:
        return ids[0]
    pos = ids.index(last_id)
    return ids[(pos + 1) % len(ids)]
