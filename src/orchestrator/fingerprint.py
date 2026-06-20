"""Behavioral diversity fingerprint for evolve versions (Phase EL Step 3).

Phase EL Step 1 gave the evolve loop *lineages*; Step 2 added a curated
panel of **baseline opponents** and ``run_baseline_gauntlet`` to measure a
candidate against them. Step 3 turns the gauntlet output into a behavioral
*fingerprint* — the signal the population manager (EL.4) needs to decide
whether two versions are behaviorally redundant.

v1 fingerprint = per-baseline win-rate vector
----------------------------------------------

The v1 fingerprint of a version is exactly the per-baseline win-rate vector
``run_baseline_gauntlet`` already produces (``GauntletResult.per_baseline``).
Two versions that beat and lose to the same baselines the same way share
the same vector and are behaviorally redundant; two versions with divergent
vectors explore different parts of the strategy space. We reuse the gauntlet
output rather than invent new telemetry — the gauntlet already runs as part
of the ``--fitness-mode both`` path.

Deferred future refinement
--------------------------

A richer v2 fingerprint would enrich the vector with build-order timings and
army-composition mixes (e.g. how fast the version reached its third base, its
gateway:robo:stargate ratio). That enrichment is **deferred**: the gauntlet's
underlying :class:`orchestrator.contracts.SelfPlayRecord` does not yet carry
composition or build-order telemetry, so there is nothing to fold in. v1
stands on the baseline-result vector alone, and the json shape leaves room
for the future fields without a migration (``from_dict`` defaults).

Public surface
--------------

- :class:`Fingerprint` — one version's per-baseline win-rate vector
  (``to_json`` / ``from_json`` / ``from_dict`` mirror
  :class:`orchestrator.baselines.Baseline` and
  :class:`orchestrator.lineages.Lineage`).
- :func:`compute_fingerprint` — run the baseline gauntlet for a version and
  wrap the result into a :class:`Fingerprint`.
- :func:`fingerprint_distance` — normalized [0,1] distance between two
  fingerprints over their shared baselines.
- :func:`load_fingerprints` / :func:`write_fingerprints` /
  :func:`save_fingerprint` — registry persistence (``data/fingerprints.json``).
- :func:`default_fingerprints_path` — the canonical registry path.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.baselines import Baseline
from orchestrator.registry import _repo_root

if TYPE_CHECKING:
    from orchestrator.contracts import SelfPlayRecord
    from orchestrator.evolve import GauntletResult

_log = logging.getLogger(__name__)

__all__ = [
    "Fingerprint",
    "compute_fingerprint",
    "default_fingerprints_path",
    "fingerprint_distance",
    "load_fingerprints",
    "save_fingerprint",
    "write_fingerprints",
]


# Mirrors the Windows ``os.replace`` retry-backoff used by
# ``orchestrator.baselines._atomic_write_json`` and
# ``orchestrator.lineages._atomic_write_json``. Kept identical so the
# fingerprint registry survives the same ``--serve`` polling race. We mirror
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


def _now_iso() -> str:
    """Return an ISO-8601 UTC timestamp (seconds resolution)."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Fingerprint:
    """One version's behavioral fingerprint — its per-baseline win-rate vector.

    Fields
    ------
    version:
        The bot version this fingerprint describes (e.g. ``"v8"``). The
        registry key.
    per_baseline:
        ``{baseline_name: win_rate}`` — the version's candidate win rate (a
        float in ``[0.0, 1.0]``) against each baseline, straight from
        :attr:`orchestrator.evolve.GauntletResult.per_baseline`. An empty
        dict means the version was fingerprinted with no baselines.
    computed_at:
        ISO-8601 UTC timestamp (seconds resolution). Defaults to "now".
    """

    version: str
    per_baseline: dict[str, float] = field(default_factory=dict)
    computed_at: str = field(default_factory=_now_iso)

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_json(cls, data: str | bytes) -> Fingerprint:
        payload = json.loads(data)
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Fingerprint:
        """Build a :class:`Fingerprint` from a decoded JSON object.

        Optional fields fall back to their dataclass defaults so a registry
        written by an older build (missing, say, ``computed_at``) still
        loads. ``version`` is required.

        Raises:
            ValueError: if ``version`` is missing; if ``per_baseline`` is
                present but is not a JSON object (e.g. a list or string); or
                if any ``per_baseline`` win-rate value is non-numeric / null
                (cannot be coerced to ``float``). (We raise ``ValueError``
                rather than letting ``payload[...]`` raise ``KeyError`` or
                the coercion raise ``TypeError``/``AttributeError`` so the
                contract matches :meth:`Baseline.from_dict` and the run-loop
                guards that catch ``ValueError``.)
        """
        if "version" not in payload:
            raise ValueError(
                "fingerprint entry is missing required field 'version'"
            )
        version = payload["version"]
        raw_vec = payload.get("per_baseline") or {}
        if not isinstance(raw_vec, dict):
            raise ValueError(
                f"fingerprint entry {version!r} field 'per_baseline' must be "
                f"a JSON object; got {type(raw_vec).__name__}"
            )
        per_baseline = {}
        for k, v in raw_vec.items():
            try:
                per_baseline[str(k)] = float(v)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"fingerprint entry {version!r} baseline {k!r} has "
                    f"non-numeric win-rate {v!r}"
                ) from exc
        return cls(
            version=payload["version"],
            per_baseline=per_baseline,
            computed_at=payload.get("computed_at") or _now_iso(),
        )


def compute_fingerprint(
    version: str,
    baselines: list[Baseline],
    *,
    games_each: int = 5,
    map_name: str = "Simple64",
    game_time_limit: int = 1800,
    hard_timeout: float = 2700.0,
    run_batch_fn: Callable[..., list[SelfPlayRecord]] | None = None,
    run_gauntlet_fn: Callable[..., GauntletResult] | None = None,
) -> Fingerprint:
    """Fingerprint *version* by running the baseline gauntlet and wrapping it.

    Runs ``run_baseline_gauntlet(version, baselines, ...)`` (default:
    :func:`orchestrator.evolve.run_baseline_gauntlet`, injectable as
    *run_gauntlet_fn* for tests) and folds the resulting ``per_baseline``
    win-rate vector into a :class:`Fingerprint`. The gauntlet is the single
    source of behavioral telemetry — this function adds no new measurement.

    *run_batch_fn* is forwarded to the gauntlet (so the per-game seam stays
    injectable through this wrapper); it is ignored when *run_gauntlet_fn*
    is supplied directly.

    Empty *baselines* → an empty ``per_baseline`` vector (the gauntlet
    returns ``per_baseline={}`` for no baselines, which we carry through).
    """
    if run_gauntlet_fn is None:
        from orchestrator.evolve import run_baseline_gauntlet

        run_gauntlet_fn = run_baseline_gauntlet

    result = run_gauntlet_fn(
        version,
        baselines,
        games_each=games_each,
        map_name=map_name,
        game_time_limit=game_time_limit,
        hard_timeout=hard_timeout,
        run_batch_fn=run_batch_fn,
    )
    fp = Fingerprint(version=version, per_baseline=dict(result.per_baseline))
    _log.info(
        "fingerprint: %s over %d baseline(s)",
        version,
        len(fp.per_baseline),
    )
    return fp


def fingerprint_distance(a: Fingerprint, b: Fingerprint) -> float:
    """Return a normalized [0,1] distance between two fingerprints.

    Distance metric: **mean absolute difference of win-rates over the
    baselines present in BOTH vectors** (L1 / n over the shared keys). Each
    component is in ``[0,1]``, so the mean is bounded in ``[0,1]``:
    identical vectors → ``0.0``; maximally-opposite (one all ``1.0``, the
    other all ``0.0`` on the shared keys) → ``1.0``. We compare only the
    *intersection* of baselines because a baseline only one version was
    measured against carries no comparative signal.

    Disjoint-keys sentinel: if the two fingerprints share **no** baselines
    the vectors are incomparable. We return ``float("nan")`` and log a
    warning (chosen over raising so a population-manager scan over a
    heterogeneous registry degrades gracefully rather than aborting). NaN —
    not ``0.0`` — unambiguously signals "incomparable: no shared baselines",
    avoiding an in-band collision with the identical-fingerprints result
    (``0.0``). A consumer's ``distance < threshold`` cull test naturally
    evaluates ``False`` for NaN (every comparison with NaN is ``False``), so
    incomparable pairs are never culled for redundancy — the safe direction:
    never cull on unprovable redundancy. EL.4/EL.5 consumers should treat
    NaN as "incomparable / N-A" (e.g. via :func:`math.isnan`).
    """
    shared = a.per_baseline.keys() & b.per_baseline.keys()
    if not shared:
        _log.warning(
            "fingerprint_distance: %s and %s share no baselines; returning "
            "NaN (incomparable sentinel)",
            a.version,
            b.version,
        )
        return float("nan")
    total = sum(
        abs(a.per_baseline[name] - b.per_baseline[name]) for name in shared
    )
    return total / len(shared)


def default_fingerprints_path() -> Path:
    """Return the canonical ``<repo_root>/data/fingerprints.json`` path.

    Cross-version evolve state lives at repo-root ``data/`` — NOT per-version
    ``bots/<v>/data/`` — per ``.claude/rules/bot-runtime.md``.
    """
    return _repo_root() / "data" / "fingerprints.json"


def load_fingerprints(path: Path) -> dict[str, Fingerprint]:
    """Load the fingerprint registry from *path*.

    Returns an empty dict when the file does not exist. The on-disk shape is
    a JSON object keyed by ``version`` (each value a serialized
    :class:`Fingerprint`). Insertion order from the JSON file is preserved.

    Raises:
        json.JSONDecodeError: if the file exists but is not valid JSON.
        ValueError: if the top-level payload or an entry is not an object;
            if an entry is missing its required ``version`` field
            (``version`` is backfilled from the registry key below); if an
            entry's ``per_baseline`` is present but not a JSON object; or if
            any ``per_baseline`` win-rate value is non-numeric / null. (All
            malformed-entry cases raise ``ValueError`` — never ``TypeError``
            or ``AttributeError`` — so EL.4/EL.5 consumers' guards degrade
            gracefully.)
    """
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(
            f"fingerprints registry at {path} must be a JSON object keyed by "
            f"version; got {type(payload).__name__}"
        )
    registry: dict[str, Fingerprint] = {}
    for key, value in payload.items():
        if not isinstance(value, dict):
            raise ValueError(
                f"fingerprints registry entry {key!r} must be a JSON object; "
                f"got {type(value).__name__}"
            )
        # Tolerate a missing/blank version inside the value by trusting the
        # registry key (the key is authoritative).
        value.setdefault("version", key)
        registry[key] = Fingerprint.from_dict(value)
    return registry


def write_fingerprints(
    path: Path, registry: dict[str, Fingerprint]
) -> None:
    """Atomically persist *registry* to *path* as a keyed JSON object.

    Uses the same write-``.tmp`` + ``os.replace``-with-retry-backoff pattern
    as ``orchestrator.baselines.write_baselines`` /
    ``orchestrator.lineages.write_lineages``, so the fingerprint registry
    survives the same Windows ``--serve`` polling race.
    """
    payload: dict[str, Any] = {
        version: dataclasses.asdict(fp)
        for version, fp in registry.items()
    }
    _atomic_write_json(path, payload)


def save_fingerprint(path: Path, fp: Fingerprint) -> None:
    """Upsert *fp* into the registry at *path* (keyed by version) and persist.

    Loads the existing registry, overwrites any entry with the same
    ``version``, and atomically writes it back.
    """
    registry = load_fingerprints(path)
    registry[fp.version] = fp
    write_fingerprints(path, registry)
    _log.info(
        "saved fingerprint for %s (%d baseline(s)) -> %s",
        fp.version,
        len(fp.per_baseline),
        path,
    )
