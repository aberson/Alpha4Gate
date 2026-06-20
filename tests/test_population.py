"""Tests for ``orchestrator.population`` (Phase EL Step 4).

Pure-function coverage for :func:`decide_extinctions`:
- (a) no cull when ``len(lineages) <= cap``;
- (b) a dominated lineage (lower fitness AND distance < threshold to a fitter
  survivor) is culled;
- (c) a unique-but-weak lineage (low fitness but all distances >= threshold)
  survives;
- (d) a high-fitness lineage always survives;
- (e) a lineage with no fingerprint / no fitness is not cull-eligible;
- (f) incomparable (NaN distance — no shared baselines) pairs are NOT
  treated as redundant;
- (g) only ``len - cap`` are culled (weakest-first) when more are eligible
  than the overage.

Plus the :class:`PopulationVerdict` / :class:`CullDecision` json round-trip
and ``from_dict`` contract.
"""

from __future__ import annotations

import json

from orchestrator.fingerprint import Fingerprint
from orchestrator.lineages import Lineage
from orchestrator.population import (
    CullDecision,
    PopulationVerdict,
    decide_extinctions,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lin(lineage_id: str, head: str) -> Lineage:
    return Lineage(lineage_id=lineage_id, head_version=head)


def _fp(version: str, vec: dict[str, float]) -> Fingerprint:
    return Fingerprint(version=version, per_baseline=vec)


# ---------------------------------------------------------------------------
# (a) under cap → keep all
# ---------------------------------------------------------------------------


def test_no_cull_when_at_or_under_cap() -> None:
    lineages = {
        "a": _lin("a", "v1"),
        "b": _lin("b", "v2"),
    }
    fps = {
        "v1": _fp("v1", {"base": 0.9}),
        "v2": _fp("v2", {"base": 0.91}),  # redundant + fitter, but under cap
    }
    fits = {"v1": 0.5, "v2": 0.9}

    verdict = decide_extinctions(
        lineages, fps, fits, cap=2, diversity_threshold=0.15
    )
    assert verdict.culled == []
    assert verdict.kept == ["a", "b"]
    assert verdict.repopulate == []


# ---------------------------------------------------------------------------
# (b) dominated lineage culled
# ---------------------------------------------------------------------------


def test_dominated_lineage_is_culled() -> None:
    # 'weak' is redundant with (distance ~0.05 < 0.15) AND less fit than
    # 'strong'. Over cap (3 > 2) so the overage is 1 — 'weak' must go.
    lineages = {
        "strong": _lin("strong", "v10"),
        "weak": _lin("weak", "v11"),
        "unique": _lin("unique", "v12"),
    }
    fps = {
        "v10": _fp("v10", {"b1": 0.9, "b2": 0.8}),
        "v11": _fp("v11", {"b1": 0.85, "b2": 0.8}),  # distance 0.025 from v10
        "v12": _fp("v12", {"b1": 0.1, "b2": 0.1}),  # far from both
    }
    fits = {"v10": 0.85, "v11": 0.50, "v12": 0.40}

    verdict = decide_extinctions(
        lineages, fps, fits, cap=2, diversity_threshold=0.15
    )
    assert [c.lineage_id for c in verdict.culled] == ["weak"]
    cull = verdict.culled[0]
    assert cull.head_version == "v11"
    assert cull.dominated_by == "strong"
    assert "extinction" in cull.reason
    assert set(verdict.kept) == {"strong", "unique"}


# ---------------------------------------------------------------------------
# (c) unique-but-weak survives
# ---------------------------------------------------------------------------


def test_unique_but_weak_survives() -> None:
    # 'weak' is the LEAST fit but its fingerprint is distant from every other
    # head (all distances >= threshold), so it is NOT redundant → survives.
    # The redundant pair (strong/mid) provides the only cull-eligible victim.
    lineages = {
        "strong": _lin("strong", "v1"),
        "mid": _lin("mid", "v2"),
        "weak": _lin("weak", "v3"),
    }
    fps = {
        "v1": _fp("v1", {"b": 0.9}),
        "v2": _fp("v2", {"b": 0.88}),  # distance 0.02 from v1 → redundant
        "v3": _fp("v3", {"b": 0.0}),  # distance 0.9 from v1, 0.88 from v2
    }
    fits = {"v1": 0.9, "v2": 0.6, "v3": 0.1}  # weak has the LOWEST fitness

    verdict = decide_extinctions(
        lineages, fps, fits, cap=2, diversity_threshold=0.15
    )
    # Despite being least fit, 'weak' is unique → not culled. 'mid' is the
    # dominated near-duplicate of 'strong' and is the one culled.
    assert [c.lineage_id for c in verdict.culled] == ["mid"]
    assert "weak" in verdict.kept


# ---------------------------------------------------------------------------
# (d) high-fitness always survives
# ---------------------------------------------------------------------------


def test_high_fitness_always_survives() -> None:
    # 'top' is fitter than everyone, so no S out-fitnesses it → never
    # cull-eligible even though it is redundant with 'near'.
    lineages = {
        "top": _lin("top", "v1"),
        "near": _lin("near", "v2"),
        "other": _lin("other", "v3"),
    }
    fps = {
        "v1": _fp("v1", {"b": 0.95}),
        "v2": _fp("v2", {"b": 0.93}),  # redundant with v1
        "v3": _fp("v3", {"b": 0.2}),
    }
    fits = {"v1": 0.95, "v2": 0.50, "v3": 0.60}

    verdict = decide_extinctions(
        lineages, fps, fits, cap=2, diversity_threshold=0.15
    )
    assert "top" in verdict.kept
    # The weaker redundant member ('near') is the victim, not 'top'.
    assert [c.lineage_id for c in verdict.culled] == ["near"]


# ---------------------------------------------------------------------------
# (e) no fingerprint / no fitness → not cull-eligible
# ---------------------------------------------------------------------------


def test_lineage_without_fingerprint_or_fitness_not_cull_eligible() -> None:
    # 'nofp' has a fitness but NO fingerprint; 'nofit' has a fingerprint but
    # NO fitness. Neither can be assessed for redundancy → neither cull-
    # eligible, even though both are weaker than 'strong'. Over cap (3 > 2)
    # but only redundant-assessable victims may be culled; here there are
    # none, so the population stays above cap (nothing culled).
    lineages = {
        "strong": _lin("strong", "v1"),
        "nofp": _lin("nofp", "v2"),
        "nofit": _lin("nofit", "v3"),
    }
    fps = {
        "v1": _fp("v1", {"b": 0.9}),
        # v2 (nofp) deliberately absent
        "v3": _fp("v3", {"b": 0.88}),  # redundant with v1 by distance
    }
    fits = {
        "v1": 0.9,
        "v2": 0.4,
        # v3 (nofit) deliberately absent
    }

    verdict = decide_extinctions(
        lineages, fps, fits, cap=2, diversity_threshold=0.15
    )
    assert verdict.culled == []  # neither victim is assessable → safe
    assert set(verdict.kept) == {"strong", "nofp", "nofit"}


# ---------------------------------------------------------------------------
# (f) incomparable (NaN distance) pairs are NOT redundant
# ---------------------------------------------------------------------------


def test_incomparable_nan_distance_not_redundant() -> None:
    # 'weak' and 'strong' share NO baselines → fingerprint_distance is NaN →
    # ``nan < threshold`` is False → not redundant → not culled, even though
    # 'weak' is less fit. Over cap but no eligible victim → nothing culled.
    lineages = {
        "strong": _lin("strong", "v1"),
        "weak": _lin("weak", "v2"),
        "third": _lin("third", "v3"),
    }
    fps = {
        "v1": _fp("v1", {"alpha": 0.9}),
        "v2": _fp("v2", {"beta": 0.1}),  # disjoint keys from v1 and v3
        "v3": _fp("v3", {"alpha": 0.85}),  # shares 'alpha' with v1 only
    }
    fits = {"v1": 0.9, "v2": 0.1, "v3": 0.4}

    verdict = decide_extinctions(
        lineages, fps, fits, cap=2, diversity_threshold=0.15
    )
    # 'weak' is incomparable to everyone → never culled. 'third' IS comparable
    # to 'strong' (shares 'alpha', distance 0.05) and less fit → it is the
    # cull victim.
    assert "weak" in verdict.kept
    assert [c.lineage_id for c in verdict.culled] == ["third"]


# ---------------------------------------------------------------------------
# (g) only len-cap culled, weakest-first
# ---------------------------------------------------------------------------


def test_only_overage_culled_weakest_first() -> None:
    # Four lineages, cap 2 → overage 2. Three are dominated redundant
    # duplicates of 'top'; only the TWO weakest of those three may be culled.
    lineages = {
        "top": _lin("top", "v1"),
        "d1": _lin("d1", "v2"),
        "d2": _lin("d2", "v3"),
        "d3": _lin("d3", "v4"),
    }
    fps = {
        "v1": _fp("v1", {"b": 0.90}),
        "v2": _fp("v2", {"b": 0.89}),  # redundant
        "v3": _fp("v3", {"b": 0.88}),  # redundant
        "v4": _fp("v4", {"b": 0.87}),  # redundant
    }
    fits = {"v1": 0.90, "v2": 0.50, "v3": 0.30, "v4": 0.10}

    verdict = decide_extinctions(
        lineages, fps, fits, cap=2, diversity_threshold=0.15
    )
    # Overage is 2 → the two WEAKEST eligible victims (d3 @ 0.10, d2 @ 0.30)
    # are culled, weakest-first. d1 (0.50) survives so the population lands
    # exactly at cap (top + d1).
    assert [c.lineage_id for c in verdict.culled] == ["d3", "d2"]
    assert set(verdict.kept) == {"top", "d1"}


def test_fewer_eligible_than_overage_stays_above_cap() -> None:
    # Over cap by 2 but only ONE lineage is cull-eligible (the lone redundant
    # near-duplicate). The other over-cap lineages are unique. We cull only
    # the one eligible and remain above cap (3 kept, cap 2).
    lineages = {
        "top": _lin("top", "v1"),
        "dup": _lin("dup", "v2"),
        "uniqA": _lin("uniqA", "v3"),
        "uniqB": _lin("uniqB", "v4"),
    }
    fps = {
        "v1": _fp("v1", {"b": 0.90}),
        "v2": _fp("v2", {"b": 0.89}),  # redundant with top, less fit
        "v3": _fp("v3", {"b": 0.50}),  # unique: far from top AND from v4
        "v4": _fp("v4", {"b": 0.05}),  # unique: far from top AND from v3
    }
    fits = {"v1": 0.90, "v2": 0.40, "v3": 0.30, "v4": 0.20}

    verdict = decide_extinctions(
        lineages, fps, fits, cap=2, diversity_threshold=0.15
    )
    assert [c.lineage_id for c in verdict.culled] == ["dup"]
    # Stayed above cap: 3 kept though cap is 2.
    assert len(verdict.kept) == 3


# ---------------------------------------------------------------------------
# (h) equal-fitness redundant pair — neither strictly dominates → both survive
# ---------------------------------------------------------------------------


def test_equal_fitness_redundant_pair_both_survive() -> None:
    # Two behaviorally-redundant lineages (distance 0.01 < 0.15) with EQUAL
    # head fitness. The cull rule is strict ``l_fit < s_fit``, so NEITHER
    # strictly dominates the other → neither is cull-eligible. Over cap by 1,
    # but there is no eligible victim, so nothing is culled and the population
    # stays above cap. This pins the strict-`<` boundary against a `<=` typo
    # that would silently cull one of two equal-fitness peers.
    lineages = {
        "twinA": _lin("twinA", "v1"),
        "twinB": _lin("twinB", "v2"),
    }
    fps = {
        "v1": _fp("v1", {"b": 0.90}),
        "v2": _fp("v2", {"b": 0.89}),  # distance 0.01 < 0.15 → redundant
    }
    fits = {"v1": 0.70, "v2": 0.70}  # EQUAL fitness

    verdict = decide_extinctions(
        lineages, fps, fits, cap=1, diversity_threshold=0.15
    )
    assert verdict.culled == []  # neither strictly dominates → no cull
    assert set(verdict.kept) == {"twinA", "twinB"}  # stays above cap (2 > 1)


# ---------------------------------------------------------------------------
# (i) a legitimate 0.0-fitness eligible lineage is culled (and sorted first)
# ---------------------------------------------------------------------------


def test_zero_fitness_dominated_lineage_is_culled_first() -> None:
    # 'zero' has a REAL 0.0 head fitness (distinct from None/absent) and is
    # redundant with the fitter 'strong' → it IS cull-eligible. 'mid' is also
    # dominated by 'strong' but is fitter than 'zero'. Over cap by 2 (4 > 2),
    # so the two weakest eligible victims are culled weakest-first: 'zero'
    # (0.0) sorts ahead of 'mid' (0.30). This pins that a 0.0 fitness is
    # handled as a real, weakest value (not conflated with None) and that the
    # ``or 0.0`` sort-key narrowing keeps 0.0 in its correct weakest slot.
    lineages = {
        "strong": _lin("strong", "v1"),
        "mid": _lin("mid", "v2"),
        "zero": _lin("zero", "v3"),
        "unique": _lin("unique", "v4"),
    }
    fps = {
        "v1": _fp("v1", {"b": 0.90}),
        "v2": _fp("v2", {"b": 0.89}),  # redundant with strong
        "v3": _fp("v3", {"b": 0.88}),  # redundant with strong
        "v4": _fp("v4", {"b": 0.05}),  # unique → not cull-eligible
    }
    fits = {"v1": 0.90, "v2": 0.30, "v3": 0.0, "v4": 0.40}

    verdict = decide_extinctions(
        lineages, fps, fits, cap=2, diversity_threshold=0.15
    )
    # Weakest-first: 'zero' (0.0) culled before 'mid' (0.30).
    assert [c.lineage_id for c in verdict.culled] == ["zero", "mid"]
    assert verdict.culled[0].lineage_id == "zero"  # 0.0 sorts weakest-first
    assert verdict.culled[0].dominated_by == "strong"
    assert set(verdict.kept) == {"strong", "unique"}


# ---------------------------------------------------------------------------
# Verdict / CullDecision json contract
# ---------------------------------------------------------------------------


def test_verdict_json_round_trip() -> None:
    verdict = PopulationVerdict(
        kept=["a", "b"],
        culled=[
            CullDecision(
                lineage_id="c",
                head_version="v9",
                dominated_by="a",
                reason="extinction: c dominated by a",
            )
        ],
    )
    restored = PopulationVerdict.from_json(verdict.to_json())
    assert restored.kept == ["a", "b"]
    assert restored.repopulate == []
    assert len(restored.culled) == 1
    assert restored.culled[0].dominated_by == "a"


def test_cull_decision_from_dict_missing_field_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="dominated_by"):
        CullDecision.from_dict(
            {
                "lineage_id": "c",
                "head_version": "v9",
                "reason": "x",
            }
        )


def test_verdict_from_dict_defaults_to_empty() -> None:
    verdict = PopulationVerdict.from_dict(json.loads("{}"))
    assert verdict.kept == []
    assert verdict.culled == []
    assert verdict.repopulate == []
