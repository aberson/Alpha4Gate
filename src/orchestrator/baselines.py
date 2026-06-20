"""Baseline-opponent registry for the evolve fitness gauntlet (Phase EL).

Phase EL Step 1 gave the evolve loop *lineages* — parallel branches of the
version tree. Step 2 adds a second axis: a curated set of **baseline
opponents** a candidate can be measured against, independent of its
immediate parent. Where ``run_fitness_eval`` answers "is this imp better
than the version it was snapshotted from?", the baseline gauntlet answers
"how does the newly-promoted version stack up against a fixed panel of
reference bots?" — a more stable fitness signal than parent-relative wins
once a lineage has drifted several promotions away from its origin.

A *baseline* is a named reference opponent. Each carries:

- ``name`` — kebab/slug identifier, unique within a registry (e.g.
  ``"v7-strong"`` or ``"early-rush"``). This is the registry key.
- ``version`` — the bot version that plays the baseline role (e.g.
  ``"v7"``). Validated against :func:`orchestrator.registry.list_versions`
  at registration time.
- ``added_at`` — ISO-8601 UTC timestamp (seconds resolution).
- ``note`` — free-text operator annotation (why this baseline matters).

The registry is ``data/baselines.json`` — a JSON object keyed by ``name``.
It is cross-version evolve state, so it lives at repo-root ``data/`` (NOT
per-version ``bots/<v>/data/``); see ``.claude/rules/bot-runtime.md``. The
whole ``data/`` dir is gitignored.

Public surface
--------------

- :class:`Baseline` — one reference opponent's record (``to_json`` /
  ``from_json`` / ``from_dict`` mirror :class:`orchestrator.evolve.Improvement`
  and :class:`orchestrator.lineages.Lineage`).
- :func:`load_baselines` — read the registry from disk.
- :func:`write_baselines` — atomically persist the registry.
- :func:`register_baseline` — add/update one entry (validates the version).
- :func:`default_baselines_path` — the canonical ``data/baselines.json`` path.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchestrator.registry import _repo_root, list_versions

_log = logging.getLogger(__name__)

__all__ = [
    "Baseline",
    "default_baselines_path",
    "load_baselines",
    "register_baseline",
    "write_baselines",
]


# Mirrors the Windows ``os.replace`` retry-backoff used by
# ``orchestrator.lineages._atomic_write_json`` and
# ``orchestrator.evolve._restore_pointer``. Kept identical so the baseline
# registry survives the same ``--serve`` polling race. We mirror rather
# than import ``scripts/evolve_round_state`` so this ``src/`` module stays
# free of a ``scripts/``-on-sys.path dependency (the test harness only puts
# ``src/`` on the path).
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


def _now_iso() -> str:
    """Return an ISO-8601 UTC timestamp (seconds resolution)."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Baseline:
    """One reference opponent in the evolve fitness gauntlet.

    Fields
    ------
    name:
        Kebab/slug identifier, unique within a registry (e.g.
        ``"v7-strong"``). The registry key.
    version:
        The bot version that plays the baseline role (e.g. ``"v7"``).
    added_at:
        ISO-8601 UTC timestamp (seconds resolution). Defaults to "now".
    note:
        Free-text operator annotation. Defaults to empty.
    """

    name: str
    version: str
    added_at: str = dataclasses.field(default_factory=_now_iso)
    note: str = ""

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_json(cls, data: str | bytes) -> Baseline:
        payload = json.loads(data)
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Baseline:
        """Build a :class:`Baseline` from a decoded JSON object.

        Optional fields fall back to their dataclass defaults so a
        registry written by an older build (missing, say, ``note``) still
        loads. ``name`` and ``version`` are required.

        Raises:
            ValueError: if ``name`` or ``version`` is missing. (We raise
                ``ValueError`` rather than letting ``payload[...]`` raise
                ``KeyError`` so the contract matches the other malformed-
                entry errors in :func:`load_baselines`, and so the
                run-loop guard in ``scripts/evolve.py`` — which catches
                ``ValueError`` — degrades gracefully to parent-like
                instead of aborting the whole run.)
        """
        if "name" not in payload:
            raise ValueError(
                "baseline entry is missing required field 'name'"
            )
        if "version" not in payload:
            raise ValueError(
                f"baseline entry {payload['name']!r} is missing required "
                "field 'version'"
            )
        return cls(
            name=payload["name"],
            version=payload["version"],
            added_at=payload.get("added_at") or _now_iso(),
            note=payload.get("note", ""),
        )


def default_baselines_path() -> Path:
    """Return the canonical ``<repo_root>/data/baselines.json`` path.

    Cross-version evolve state lives at repo-root ``data/`` — NOT per-version
    ``bots/<v>/data/`` — per ``.claude/rules/bot-runtime.md``.
    """
    return _repo_root() / "data" / "baselines.json"


def load_baselines(path: Path) -> dict[str, Baseline]:
    """Load the baseline registry from *path*.

    Returns an empty dict when the file does not exist. The on-disk shape
    is a JSON object keyed by ``name`` (each value a serialized
    :class:`Baseline`). Insertion order from the JSON file is preserved
    (``json.loads`` keeps object key order, and ``dict`` is ordered).

    Raises:
        json.JSONDecodeError: if the file exists but is not valid JSON.
        ValueError: if the top-level payload or an entry is not an object,
            or if an entry is missing a required field (``version``;
            ``name`` is backfilled from the registry key below).
    """
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(
            f"baselines registry at {path} must be a JSON object keyed by "
            f"name; got {type(payload).__name__}"
        )
    registry: dict[str, Baseline] = {}
    for key, value in payload.items():
        if not isinstance(value, dict):
            raise ValueError(
                f"baselines registry entry {key!r} must be a JSON object; "
                f"got {type(value).__name__}"
            )
        # Tolerate a missing/blank name inside the value by trusting the
        # registry key (the key is authoritative).
        value.setdefault("name", key)
        registry[key] = Baseline.from_dict(value)
    return registry


def write_baselines(path: Path, registry: dict[str, Baseline]) -> None:
    """Atomically persist *registry* to *path* as a keyed JSON object.

    Uses the same write-``.tmp`` + ``os.replace``-with-retry-backoff
    pattern as ``orchestrator.lineages.write_lineages``, so the baseline
    registry survives the same Windows ``--serve`` polling race.
    """
    payload: dict[str, Any] = {
        name: dataclasses.asdict(baseline)
        for name, baseline in registry.items()
    }
    _atomic_write_json(path, payload)


def register_baseline(
    path: Path,
    name: str,
    version: str,
    note: str = "",
) -> Baseline:
    """Add or update one baseline entry in the registry at *path*.

    Validates that *version* exists via
    :func:`orchestrator.registry.list_versions` before writing. An
    existing entry with the same *name* is overwritten (its ``added_at``
    is refreshed). Returns the newly-registered :class:`Baseline`.

    Raises:
        ValueError: if *name* or *version* is empty, or *version* is not a
            registered version.
    """
    if not name:
        raise ValueError("register_baseline: name must be a non-empty string")
    if not version:
        raise ValueError(
            "register_baseline: version must be a non-empty string"
        )
    known = list_versions()
    if version not in known:
        raise ValueError(
            f"register_baseline: version {version!r} is not a registered "
            f"version; known versions are {known!r}"
        )
    registry = load_baselines(path)
    baseline = Baseline(name=name, version=version, note=note)
    registry[name] = baseline
    write_baselines(path, registry)
    _log.info(
        "registered baseline %r -> version %s (note=%r)",
        name,
        version,
        note,
    )
    return baseline
