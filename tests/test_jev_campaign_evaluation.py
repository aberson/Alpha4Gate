from __future__ import annotations

import pytest

from jev.campaign import CampaignSchemaError, SourceReference, build_manifest
from jev.campaign_evaluation import (
    OfflineCaseFact,
    OfflineCriticalFact,
    OfflineEvidence,
    evaluate_offline,
)


def _source(letter: str) -> SourceReference:
    return SourceReference(2, f"C:/snapshots/{letter}", letter.lower() * 64, letter.lower() * 64)


@pytest.fixture(autouse=True)
def _offline_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SourceReference, "verify", lambda self: None)


def _manifest(*, unchanged: bool = False):
    parent = _source("A")
    return build_manifest(
        parent=parent,
        candidate=parent if unchanged else _source("B"),
        original=_source("C"),
        distinct=_source("D"),
        critical_test_ids=("critical::recovery", "critical::reservations"),
        screen_seeds=(301, 302, 303),
        promotion_seeds=(401, 402, 403),
        control_seeds=(501, 502, 503),
        campaign_id="123456781234423482341234567890ab",
    )


def _evidence(manifest, outcome_for):
    return OfflineEvidence(
        tuple(
            OfflineCaseFact(case.case_id, case.source_fingerprint, outcome_for(case))
            for case in manifest.cases
        ),
        tuple(OfflineCriticalFact(test_id, True, True) for test_id in manifest.critical_test_ids),
    )


def test_missing_stratum_stays_incomplete() -> None:
    manifest = _manifest()
    evidence = _evidence(manifest, lambda case: "loss")
    missing_id = next(case.case_id for case in manifest.cases if case.role == "promotion")
    evidence = OfflineEvidence(
        tuple(fact for fact in evidence.case_facts if fact.case_id != missing_id),
        evidence.critical_facts,
    )

    report = evaluate_offline(manifest, evidence)

    assert report["projected_verdict"] == "incomplete"
    assert report["complete"] is False
    assert report["promotion_authorized"] is False


def test_weighted_improvement_cannot_hide_race_regression() -> None:
    manifest = _manifest()

    def outcome(case):
        if case.role == "screen":
            return "win"
        if case.role == "control":
            return "loss"
        if case.source_fingerprint == manifest.candidate.fingerprint:
            return "loss" if case.race == "Terran" else "win"
        if case.source_fingerprint == manifest.parent.fingerprint:
            return "win" if case.race == "Terran" else "loss"
        return "loss"

    report = evaluate_offline(manifest, _evidence(manifest, outcome))

    assert report["candidate_reference_delta_millionths"] >= 100_000
    assert report["promotion_candidate_wins"] > report["promotion_parent_wins"]
    assert report["race_regressions"] == ["Terran"]
    assert report["projected_verdict"] == "regressed"
    assert report["evidence_scope"] == "unverified/offline"
    assert report["authoritative"] is False
    assert report["promotion_authorized"] is False


def test_malformed_or_authority_claiming_evidence_is_rejected() -> None:
    with pytest.raises(CampaignSchemaError, match="fields differ"):
        OfflineEvidence.from_dict({"case_facts": [], "critical_facts": [], "authoritative": True})

    manifest = _manifest()
    evidence = _evidence(manifest, lambda case: "loss")
    first = evidence.case_facts[0]
    wrong = OfflineCaseFact(first.case_id, "f" * 64, first.outcome)
    with pytest.raises(CampaignSchemaError, match="wrong source"):
        evaluate_offline(
            manifest, OfflineEvidence((wrong, *evidence.case_facts[1:]), evidence.critical_facts)
        )


def test_infrastructure_fact_is_not_scored_as_a_loss() -> None:
    manifest = _manifest()
    evidence = _evidence(
        manifest,
        lambda case: "infrastructure_error" if case.order == 0 else "loss",
    )

    report = evaluate_offline(manifest, evidence)

    assert report["projected_verdict"] == "incomplete"
    assert report["interrupted_case_ids"] == [manifest.cases[0].case_id]


def test_unchanged_candidate_reports_no_change_without_facts() -> None:
    report = evaluate_offline(_manifest(unchanged=True), OfflineEvidence((), ()))

    assert report["projected_verdict"] == "no_change"
    assert report["promotion_authorized"] is False
