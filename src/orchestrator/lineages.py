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
- :func:`register_lineage` — add/update one entry, validating the
  ``lineage_id`` slug and the ``head_version`` before any write.
- :func:`load_or_default_lineages` — read the registry, falling back to a
  single implicit ``main`` lineage when absent/empty.
- :func:`next_lineage` — deterministic round-robin scheduler over the
  ``status == "active"`` records only.
- :func:`default_lineages_path` — the canonical ``data/lineages.json`` path.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchestrator.registry import _repo_root, current_version, list_versions

_log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_LINEAGE_ID",
    "Lineage",
    "default_lineages_path",
    "load_lineages",
    "load_or_default_lineages",
    "next_lineage",
    "register_lineage",
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

    This helper diverges from its byte-identical siblings
    (``orchestrator.baselines._atomic_write_json``,
    ``orchestrator.fingerprint._atomic_write_json``) in exactly two ways:
    the scratch file carries the writer's pid, and the scratch is unlinked
    on the failure path (see the ``finally`` below). Each of those modules
    owns a private copy and ``write_lineages`` is this copy's only caller,
    so neither change reaches another registry. A shared ``<path>.tmp`` let
    two concurrent ``scripts/lineage.py`` invocations clobber each other's
    scratch — the loser's ``replace`` then raised an unretried
    ``FileNotFoundError`` while the winner reported success for an entry
    that never landed. Per-process naming removes that collision; it does
    not make the surrounding read-modify-write atomic, so concurrent
    registry edits remain the operator's to serialize.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    try:
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
    finally:
        # Second divergence from the siblings: drop the scratch on every
        # exit path. A ``replace`` that never succeeds (a ``--serve`` handle
        # outliving the backoff) used to leak the scratch file, and per-pid
        # naming turned that leak from one reusable ``<path>.tmp`` into one
        # accumulating file per invocation under ``data/``. After a
        # successful ``replace`` the scratch is already gone, so this is a
        # no-op; the unlink error is swallowed so it can never mask the
        # write/replace failure being propagated.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - defensive
            pass


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


# The slug shape :class:`Lineage` documents for ``lineage_id``
# ("Kebab/slug identifier", see the field docs above). Enforced by
# :func:`register_lineage` so a registry key can never carry a tab, a
# newline or a non-ASCII character: ``scripts/lineage.py list`` emits
# tab-separated rows, and on Windows a redirected stdout is ``cp1252``,
# where printing such a key raises ``UnicodeEncodeError``.
#
# Applied with ``fullmatch``, NOT ``match``: Python's ``$`` also matches
# immediately before a single trailing newline, so ``match`` accepted a
# valid slug carrying one. That key persists, keeps the registry non-empty
# and can never be named again by any shell-typeable ``remove`` argument,
# which latches multi-lineage scheduling on with no operator undo -- the
# exact teardown hatch Done-when (4) exists to provide. The anchors are
# kept because the pattern is quoted verbatim in the rejection message.
_LINEAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def register_lineage(
    path: Path,
    lineage_id: str,
    head_version: str,
) -> Lineage:
    """Add or update one lineage entry in the registry at *path*.

    Validates *lineage_id* against the slug shape :class:`Lineage`
    documents and *head_version* via
    :func:`orchestrator.registry.list_versions`, both **before** the
    registry is read or written, so a rejected registration leaves an
    existing registry byte-identical and creates no file. Returns the
    newly-registered :class:`Lineage`.

    An existing entry with the same *lineage_id* is **updated in place**
    through :func:`dataclasses.replace`, so only ``head_version`` changes
    and ``pool_path`` / ``parent_chain`` / ``created_at`` / ``status``
    survive. This is the one place the mirror of
    :func:`orchestrator.baselines.register_baseline` deliberately stops:
    ``Baseline`` has 3 fields and ``scripts/baseline.py`` expresses all 3,
    so reconstructing the whole record there is lossless. ``Lineage`` has 6
    and ``scripts/lineage.py`` expresses 2, so a fresh construct would
    silently zero ``pool_path`` / ``parent_chain``, restamp ``created_at``
    and revive a parked (``status != "active"``) lineage. Those are
    operator-authored fields, and the generation-boundary write-back
    (Phase EH Step EH.2) is specified to preserve them.

    Raises:
        ValueError: if *lineage_id* is empty or is not a slug, if
            *head_version* is empty, or if *head_version* is not a
            registered version.
        json.JSONDecodeError: if the registry at *path* exists but is not
            valid JSON (propagated from :func:`load_lineages`; a subclass
            of ``ValueError``).
        KeyError: if an existing registry entry is missing a required
            field (propagated from :func:`load_lineages`).
        OSError: if *path* cannot be read or written.
    """
    if not lineage_id:
        raise ValueError(
            "register_lineage: lineage_id must be a non-empty string"
        )
    if not _LINEAGE_ID_RE.fullmatch(lineage_id):
        raise ValueError(
            f"register_lineage: lineage_id {lineage_id!r} is not a valid "
            f"slug; expected {_LINEAGE_ID_RE.pattern} (lowercase letters, "
            "digits, '.', '_' and '-', e.g. 'main' or 'line-2')"
        )
    if not head_version:
        raise ValueError(
            "register_lineage: head_version must be a non-empty string"
        )
    known = list_versions()
    if head_version not in known:
        raise ValueError(
            f"register_lineage: head_version {head_version!r} is not a "
            f"registered version; known versions are {known!r}"
        )
    registry = load_lineages(path)
    existing = registry.get(lineage_id)
    if existing is not None:
        lineage = dataclasses.replace(existing, head_version=head_version)
    else:
        lineage = Lineage(lineage_id=lineage_id, head_version=head_version)
    registry[lineage_id] = lineage
    write_lineages(path, registry)
    _log.info(
        "registered lineage %r -> head %s (status=%s, created_at=%s)",
        lineage_id,
        head_version,
        lineage.status,
        lineage.created_at,
    )
    return lineage


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

    Only ``status == "active"`` records are scheduled. The evolve loop
    persists culled lineages as ``status="extinct"`` at the generation
    boundary (Phase EH Step EH.2), so without this filter a restart would
    resurrect an extinct lineage into the round-robin — see
    ``documentation/plans/evolve-operational-hardening-plan.md`` §6 D-3.

    Ordering is the iteration order of *registry* **restricted to its
    active records**, and that is NOT interchangeable with insertion
    order:

    - a registry read back from disk iterates **alphabetically** by
      ``lineage_id``, because :func:`write_lineages` serializes with
      ``json.dumps(..., sort_keys=True)`` and :func:`load_lineages`
      preserves the file's key order;
    - a registry built in memory iterates in that dict's **insertion**
      order.

    The two coincide only when the in-memory dict happens to already be
    sorted, so a caller holding a round-tripped registry must not reason
    about insertion order (the evolve loop's own lineage tests pin the
    alphabetical schedule this produces).

    The scheduler wraps: the successor of the last id is the first id.
    When *last_id* is ``None``, absent from *registry*, or present but not
    itself active, the first active lineage is returned.

    When *registry* is non-empty but holds no active record, **every** id
    is scheduled as a fallback and a WARNING is emitted — degrade, never
    crash, rather than raising the ``ValueError`` below on a caller that
    has no guard. That fallback is a guard for arbitrary callers,
    not the evolve loop's path: ``scripts/evolve.py`` partitions
    non-active records out of its registry BEFORE the generation loop and
    skips lineage scheduling entirely when nothing is active, so it never
    hands this function an all-extinct registry. Scheduling every id would
    otherwise be a resurrection, which is exactly what the active filter
    exists to prevent.

    Raises:
        ValueError: if *registry* is empty.
    """
    if not registry:
        raise ValueError("next_lineage: registry is empty")
    ids = [k for k, v in registry.items() if v.status == "active"]
    if not ids:
        _log.warning(
            "next_lineage: none of the %d registered lineage(s) is active "
            "(%s); scheduling all of them so the run can proceed",
            len(registry),
            ", ".join(
                f"{k}={v.status!r}" for k, v in registry.items()
            ),
        )
        ids = list(registry.keys())
    if last_id is None or last_id not in ids:
        return ids[0]
    pos = ids.index(last_id)
    return ids[(pos + 1) % len(ids)]
