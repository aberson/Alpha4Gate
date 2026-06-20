"""Diversity-driven extinction for the parallel-lineage evolve loop (EL.4).

Phase EL Step 1 gave the evolve loop *lineages* (parallel branches of the
version tree); Step 2 added a curated **baseline gauntlet** producing a
per-lineage-head fitness; Step 3 wrapped that gauntlet output into a
behavioral :class:`orchestrator.fingerprint.Fingerprint`. Step 4 closes the
loop: when the number of live lineages exceeds a cap, the *population
manager* decides which lineages to make extinct so the soak keeps exploring
breadth rather than maintaining an unbounded, increasingly-redundant set.

The decision is a **pure function** — :func:`decide_extinctions`. It reads
the current lineages, their head fingerprints, and their head fitnesses, and
returns a frozen :class:`PopulationVerdict` describing what to keep and what
to cull. It performs no I/O and mutates nothing: the evolve loop owns the
side effects (removing culled lineages from its in-memory set and appending
an ``"extinction"`` row to ``data/evolve_results.jsonl``).

Cull rule
---------

A lineage's *fitness* is its head version's baseline mean-win-rate (higher is
better); its *fingerprint* is its head's per-baseline win-rate vector.

1. **Under cap → keep all.** If ``len(lineages) <= cap`` nothing is culled.
2. **Over cap → cull the weakest *dominated* lineages.** A lineage ``L`` is
   *cull-eligible* iff some surviving lineage ``S`` **dominates** it:

       fitnesses[L.head] < fitnesses[S.head]   (S is strictly fitter)
       AND fingerprint_distance(fp_L, fp_S) < diversity_threshold
                                              (L is behaviorally redundant
                                               with the fitter S)

   i.e. ``L`` is a strictly-weaker near-duplicate of a fitter sibling, so
   removing it loses no breadth. A lineage with **no fingerprint OR no
   fitness** cannot be assessed for redundancy and is therefore **never
   cull-eligible** — we never cull what we cannot compare (the safe
   direction). A high-fitness lineage (no fitter, less-diverse neighbor)
   always survives; a unique-but-weak lineage (low fitness but distant from
   everyone — all distances ``>= threshold``) also survives.
3. **Cull weakest-first, only as many as the overage.** The overage is
   ``len(lineages) - cap``. We cull at most that many, taking the
   *lowest-fitness* cull-eligible lineages first. If fewer lineages are
   cull-eligible than the overage, only the eligible ones are culled and we
   log that the population stays above cap (the safe direction — we never
   cull a unique or incomparable lineage just to hit the number).

NaN sentinel (EL.3)
-------------------

:func:`orchestrator.fingerprint.fingerprint_distance` returns
``float("nan")`` for an *incomparable* pair (two heads sharing no baselines).
``nan < diversity_threshold`` is always ``False`` in Python, so an
incomparable pair is correctly **not** redundant — the ``< threshold`` test
already does the right thing. We rely on that: no crash on NaN, and NaN is
never treated as ``0.0`` / redundant. (``math.isnan`` is not needed here; the
comparison is sufficient.)

Repopulation (deferred)
-----------------------

The :class:`PopulationVerdict` carries an always-empty ``repopulate`` field
reserved for a future "fork the fittest survivor to refill below-cap slots"
behavior. v1 keeps the pure function extinction-only — repopulation is
**deferred**: forking a survivor is a side effect (it would snapshot a bot),
which does not belong in a pure decision function, and the in-cap-after-cull
case is rare enough not to warrant it yet. The field exists so a later step
can populate it without a verdict-shape migration.

Public surface
--------------

- :class:`CullDecision` — one culled lineage's record (which surviving
  lineage dominated it, and why).
- :class:`PopulationVerdict` — the frozen decision (``kept`` / ``culled`` /
  ``repopulate``); ``from_dict`` mirrors the established baselines.py /
  fingerprint.py pattern (raises ``ValueError`` on missing required fields).
- :func:`decide_extinctions` — the pure decision function.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from orchestrator.fingerprint import fingerprint_distance

if TYPE_CHECKING:
    from orchestrator.fingerprint import Fingerprint
    from orchestrator.lineages import Lineage

_log = logging.getLogger(__name__)

__all__ = [
    "CullDecision",
    "PopulationVerdict",
    "decide_extinctions",
]


@dataclass(frozen=True)
class CullDecision:
    """One culled lineage's extinction record.

    Fields
    ------
    lineage_id:
        The lineage being made extinct.
    head_version:
        That lineage's head version at extinction time (the version whose
        fitness / fingerprint were assessed).
    dominated_by:
        The surviving ``lineage_id`` that dominated this one (strictly
        fitter AND behaviorally redundant under ``diversity_threshold``).
    reason:
        Human-readable explanation, suitable for the extinction event row.
    """

    lineage_id: str
    head_version: str
    dominated_by: str
    reason: str

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_json(cls, data: str | bytes) -> CullDecision:
        return cls.from_dict(json.loads(data))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CullDecision:
        """Build a :class:`CullDecision` from a decoded JSON object.

        All four fields are required; a missing one raises ``ValueError``
        (mirroring :meth:`orchestrator.fingerprint.Fingerprint.from_dict`
        and :meth:`orchestrator.baselines.Baseline.from_dict`).
        """
        for required in ("lineage_id", "head_version", "dominated_by", "reason"):
            if required not in payload:
                raise ValueError(
                    f"cull decision is missing required field {required!r}"
                )
        return cls(
            lineage_id=payload["lineage_id"],
            head_version=payload["head_version"],
            dominated_by=payload["dominated_by"],
            reason=payload["reason"],
        )


@dataclass(frozen=True)
class PopulationVerdict:
    """The population manager's keep / cull decision (pure-function output).

    Fields
    ------
    kept:
        ``lineage_id`` of every surviving lineage, in the input registry's
        iteration order.
    culled:
        :class:`CullDecision` for every lineage made extinct, weakest-first
        (lowest head fitness culled first).
    repopulate:
        Reserved for a future "fork the fittest survivor" behavior; always
        empty in v1 (repopulation is deferred — see the module docstring).
    """

    kept: list[str] = field(default_factory=list)
    culled: list[CullDecision] = field(default_factory=list)
    repopulate: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_json(cls, data: str | bytes) -> PopulationVerdict:
        return cls.from_dict(json.loads(data))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PopulationVerdict:
        """Build a :class:`PopulationVerdict` from a decoded JSON object.

        All fields are optional and fall back to empty lists (a verdict with
        nothing kept and nothing culled is a valid no-op). ``culled`` entries
        are decoded via :meth:`CullDecision.from_dict`, which raises
        ``ValueError`` on a malformed entry.
        """
        raw_culled = payload.get("culled") or []
        if not isinstance(raw_culled, list):
            raise ValueError(
                f"population verdict 'culled' must be a JSON array; got "
                f"{type(raw_culled).__name__}"
            )
        return cls(
            kept=list(payload.get("kept") or []),
            culled=[CullDecision.from_dict(c) for c in raw_culled],
            repopulate=list(payload.get("repopulate") or []),
        )


def decide_extinctions(
    lineages: dict[str, Lineage],
    fingerprints: dict[str, Fingerprint],
    fitnesses: dict[str, float],
    *,
    cap: int,
    diversity_threshold: float,
) -> PopulationVerdict:
    """Decide which lineages to make extinct to bring the population to *cap*.

    Pure function — no I/O, no mutation. See the module docstring for the
    full cull rule. In brief:

    - ``len(lineages) <= cap`` → keep all (empty cull set).
    - Otherwise a lineage ``L`` is *cull-eligible* iff some surviving lineage
      ``S`` strictly out-fitnesses it AND is behaviorally redundant with it
      (``fingerprint_distance(fp_L, fp_S) < diversity_threshold``). A lineage
      with no fingerprint or no fitness is never cull-eligible. Incomparable
      fingerprint pairs (NaN distance) are not redundant (``nan < threshold``
      is ``False``).
    - Cull the weakest cull-eligible lineages first, at most ``len - cap`` of
      them; if fewer are eligible than the overage, cull only those and stay
      above cap.

    Args:
        lineages: ``{lineage_id: Lineage}`` — the live population. Each
            :class:`orchestrator.lineages.Lineage` exposes ``.head_version``.
        fingerprints: ``{version: Fingerprint}`` keyed by VERSION; a
            lineage's fingerprint is ``fingerprints.get(lineage.head_version)``
            (may be absent).
        fitnesses: ``{version: float}`` keyed by VERSION — the lineage head's
            baseline mean-win-rate (may be absent).
        cap: Maximum population size.
        diversity_threshold: Two heads with ``fingerprint_distance <
            diversity_threshold`` are behaviorally redundant.
    """
    kept_ids = list(lineages.keys())

    if len(lineages) <= cap:
        return PopulationVerdict(kept=kept_ids, culled=[], repopulate=[])

    overage = len(lineages) - cap

    # Resolve each lineage's head fitness + fingerprint once. A lineage that
    # lacks either signal is "unassessable" — it can never be cull-eligible,
    # but it CAN dominate another lineage only if it has both (it needs a
    # fitness to out-fitness and a fingerprint to be the redundancy anchor).
    fitness_of: dict[str, float | None] = {}
    fp_of: dict[str, Fingerprint | None] = {}
    for lid, lineage in lineages.items():
        head = lineage.head_version
        fitness_of[lid] = fitnesses.get(head)
        fp_of[lid] = fingerprints.get(head)

    # Find, for each candidate L, a dominating survivor S (strictly fitter +
    # redundant). We treat EVERY OTHER lineage as a potential dominator: a
    # lineage we might also cull can still witness another's redundancy, but
    # since we only ever cull the weaker of a redundant pair, the fitter
    # member of any such pair is itself never dominated by the weaker one.
    dominator_of: dict[str, str] = {}
    for lid in lineages:
        l_fit = fitness_of[lid]
        l_fp = fp_of[lid]
        if l_fit is None or l_fp is None:
            # Unassessable → never cull-eligible (safe direction).
            continue
        for sid in lineages:
            if sid == lid:
                continue
            s_fit = fitness_of[sid]
            s_fp = fp_of[sid]
            if s_fit is None or s_fp is None:
                continue
            if not (l_fit < s_fit):
                continue
            # NaN distance (incomparable / no shared baselines) makes this
            # comparison False, so incomparable pairs are NOT redundant.
            if fingerprint_distance(l_fp, s_fp) < diversity_threshold:
                dominator_of[lid] = sid
                break

    # Cull-eligible lineages, sorted weakest-first (lowest head fitness). The
    # fitness is guaranteed present for every eligible lineage above, so the
    # ``or 0.0`` is unreachable — it only narrows ``float | None`` to ``float``
    # for the type checker.
    def _eligible_fitness(lid: str) -> float:
        return fitness_of[lid] or 0.0

    eligible = sorted(
        dominator_of.keys(),
        key=lambda lid: (_eligible_fitness(lid), lid),
    )

    to_cull = eligible[:overage]
    cull_set = set(to_cull)

    culled: list[CullDecision] = []
    for lid in to_cull:
        lineage = lineages[lid]
        sid = dominator_of[lid]
        l_fit = _eligible_fitness(lid)
        s_fit = _eligible_fitness(sid)
        reason = (
            f"extinction: lineage {lid!r} (head {lineage.head_version}) "
            f"dominated by {sid!r} (head {lineages[sid].head_version}) — "
            f"fitness {l_fit:.3f} < {s_fit:.3f} and "
            f"fingerprint distance < diversity threshold {diversity_threshold}"
        )
        culled.append(
            CullDecision(
                lineage_id=lid,
                head_version=lineage.head_version,
                dominated_by=sid,
                reason=reason,
            )
        )

    kept = [lid for lid in kept_ids if lid not in cull_set]

    if len(to_cull) < overage:
        _log.warning(
            "population: over cap by %d but only %d lineage(s) cull-eligible; "
            "culling %d and staying above cap at %d (cap=%d). The remaining "
            "lineages are unique or incomparable — never culled to hit the "
            "number.",
            overage,
            len(eligible),
            len(to_cull),
            len(kept),
            cap,
        )
    else:
        _log.info(
            "population: over cap by %d; culling %d weakest dominated "
            "lineage(s) to reach cap=%d: %s",
            overage,
            len(to_cull),
            cap,
            ", ".join(to_cull),
        )

    return PopulationVerdict(kept=kept, culled=culled, repopulate=[])
