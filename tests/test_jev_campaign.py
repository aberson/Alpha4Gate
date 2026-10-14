from __future__ import annotations

import json
from pathlib import Path

import pytest

from jev.campaign import (
    J2_SEEDS,
    CampaignCase,
    CampaignSchemaError,
    SourceReference,
    build_manifest,
    coalesce_champion_weights,
    prepare_campaign,
    read_campaign_manifest,
)


def _source(letter: str) -> SourceReference:
    return SourceReference(2, f"C:/snapshots/{letter}", letter.lower() * 64, letter.lower() * 64)


@pytest.fixture(autouse=True)
def _offline_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SourceReference, "verify", lambda self: None)


def _manifest(*, unchanged: bool = False):
    parent = _source("A")
    candidate = parent if unchanged else _source("B")
    return build_manifest(
        parent=parent,
        candidate=candidate,
        original=_source("C"),
        distinct=_source("D"),
        critical_test_ids=("tests/test_jev_plans.py::test_recovery",),
        screen_seeds=(301, 302, 303),
        promotion_seeds=(401, 402, 403),
        control_seeds=(501, 502, 503),
        campaign_id="123456781234423482341234567890ab",
    )


def test_duplicate_anchor_weights_are_coalesced() -> None:
    parent = _source("A")
    weights = coalesce_champion_weights(parent, parent, None)

    assert len(weights) == 1
    assert weights[0].weight_millionths == 1_000_000
    assert weights[0].roles == (
        "current_parent",
        "original_anchor",
        "distinct_champion",
    )


def test_unchanged_candidate_has_no_strength_cases() -> None:
    manifest = _manifest(unchanged=True)

    assert manifest.no_change is True
    assert manifest.cases == ()


def test_matrix_is_exact_and_excludes_j2_seeds() -> None:
    manifest = _manifest()
    promotion = [case for case in manifest.cases if case.role == "promotion"]

    assert len([case for case in manifest.cases if case.role == "screen"]) == 6
    assert len(promotion) == 72
    assert len([case for case in manifest.cases if case.role == "control"]) == 3
    assert {case.seed for case in manifest.cases}.isdisjoint(J2_SEEDS)
    assert len({(case.race, case.difficulty, case.seed) for case in promotion}) == 18


def test_manifest_validation_rejects_j2_seed_after_parse() -> None:
    document = _manifest().to_dict()
    cases = document["cases"]
    assert isinstance(cases, list)
    first = cases[0]
    assert isinstance(first, dict)
    first["seed"] = 11
    first["case_id"] = CampaignCase.make(
        first["role"],
        first["source_fingerprint"],
        first["race"],
        first["difficulty"],
        first["seed"],
        first["order"],
    ).case_id

    with pytest.raises(CampaignSchemaError, match="exclude J2 panel seeds"):
        type(_manifest()).from_dict(document)


def test_preparation_is_write_once_and_detects_tampering(tmp_path: Path) -> None:
    campaign_dir = prepare_campaign(tmp_path, _manifest())

    assert read_campaign_manifest(campaign_dir) == _manifest()
    with pytest.raises(CampaignSchemaError, match="already exists"):
        prepare_campaign(tmp_path, _manifest())

    manifest_path = campaign_dir / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["requested_speed"] = "4"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CampaignSchemaError, match="canonical|changed"):
        read_campaign_manifest(campaign_dir)


def test_production_cli_prepares_offline_and_refuses_live_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import run_jev_campaign

    paths = {name: tmp_path / name for name in ("parent", "candidate", "original")}
    sources = {
        paths["parent"]: _source("A"),
        paths["candidate"]: _source("B"),
        paths["original"]: _source("C"),
    }
    monkeypatch.setattr(
        SourceReference,
        "from_snapshot",
        classmethod(lambda cls, path: sources[path]),
    )
    root = tmp_path / "campaigns"
    rc = run_jev_campaign.main(
        [
            "prepare",
            "--parent",
            str(paths["parent"]),
            "--candidate",
            str(paths["candidate"]),
            "--original",
            str(paths["original"]),
            "--root",
            str(root),
            "--critical-test",
            "tests/test_jev_plans.py::test_recovery",
            "--dry-run",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert output["evidence_scope"] == "unverified/offline"
    assert output["authoritative"] is False
    assert output["promotion_authorized"] is False
    campaign_dir = Path(output["campaign_directory"])
    assert campaign_dir.is_dir()
    assert {case.seed for case in read_campaign_manifest(campaign_dir).cases}.isdisjoint(J2_SEEDS)

    assert (
        run_jev_campaign.main(
            ["run", "--campaign", output["campaign_directory"], "--authoritative", "--promote"]
        )
        == 2
    )
    assert "unavailable in Step 225" in capsys.readouterr().err
