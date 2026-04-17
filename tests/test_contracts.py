"""Round-trip tests for orchestrator.contracts dataclasses."""

from __future__ import annotations

import json

import pytest

from orchestrator.contracts import (
    BotSpawnArgs,
    Manifest,
    MatchResult,
    VersionFingerprint,
)


class TestBotSpawnArgs:
    def test_roundtrip_preserves_all_fields(self) -> None:
        original = BotSpawnArgs(
            role="p1",
            map_name="Simple64",
            sc2_connect=65481,
            start_port=65478,
            result_out="data/p1.json",
            seed=42,
        )
        restored = BotSpawnArgs.from_json(original.to_json())
        assert restored == original

    def test_non_default_optional_fields_roundtrip(self) -> None:
        original = BotSpawnArgs(
            role="p2",
            map_name="Simple64",
            sc2_connect=65482,
            start_port=65478,
            result_out="data/p2.json",
            seed=7,
            ladder_server="10.0.0.5",
            realtime=True,
        )
        restored = BotSpawnArgs.from_json(original.to_json())
        assert restored == original
        assert restored.ladder_server == "10.0.0.5"
        assert restored.realtime is True

    def test_instances_are_immutable(self) -> None:
        args = BotSpawnArgs(
            role="solo",
            map_name="Simple64",
            sc2_connect=0,
            start_port=0,
            result_out="x",
            seed=0,
        )
        with pytest.raises(AttributeError):
            args.seed = 99  # type: ignore[misc]


class TestMatchResult:
    def test_win_roundtrip(self) -> None:
        original = MatchResult(
            version="v0",
            match_id="abc123",
            outcome="win",
            duration_s=612.5,
        )
        restored = MatchResult.from_json(original.to_json())
        assert restored == original
        assert restored.error is None

    def test_crash_carries_error(self) -> None:
        original = MatchResult(
            version="v3",
            match_id="def456",
            outcome="crash",
            duration_s=2.1,
            error="SC2 failed to bind port",
        )
        restored = MatchResult.from_json(original.to_json())
        assert restored == original
        assert restored.error == "SC2 failed to bind port"


class TestManifest:
    def _sample_manifest(self) -> Manifest:
        return Manifest(
            version="v0",
            best="best.zip",
            previous_best="previous_best.zip",
            parent=None,
            git_sha="cfeeb99",
            timestamp="2026-04-15T22:00:00Z",
            elo=1500.0,
            fingerprint=VersionFingerprint(
                feature_dim=24,
                action_space_size=6,
                obs_spec_hash="sha256:abcd",
            ),
        )

    def test_roundtrip_preserves_nested_fingerprint(self) -> None:
        original = self._sample_manifest()
        restored = Manifest.from_json(original.to_json())
        assert restored == original
        assert restored.fingerprint.feature_dim == 24

    def test_json_output_is_indented_and_sorted(self) -> None:
        manifest = self._sample_manifest()
        raw = manifest.to_json()
        # indent=2 means re-parsing must produce an equivalent dict
        assert json.loads(raw)["version"] == "v0"
        # sort_keys=True means 'best' appears before 'version' in the raw text
        assert raw.index('"best"') < raw.index('"version"')

    def test_extra_field_roundtrips(self) -> None:
        base = self._sample_manifest()
        with_extra = Manifest(
            version=base.version,
            best=base.best,
            previous_best=base.previous_best,
            parent=base.parent,
            git_sha=base.git_sha,
            timestamp=base.timestamp,
            elo=base.elo,
            fingerprint=base.fingerprint,
            extra={"curriculum_difficulty": 3, "note": "post-phase-A baseline"},
        )
        restored = Manifest.from_json(with_extra.to_json())
        assert restored == with_extra
        assert restored.extra["curriculum_difficulty"] == 3


class TestVersionFingerprint:
    def test_is_hashable_when_frozen(self) -> None:
        fp_a = VersionFingerprint(24, 6, "hash-a")
        fp_b = VersionFingerprint(24, 6, "hash-a")
        fp_c = VersionFingerprint(40, 12, "hash-c")
        assert hash(fp_a) == hash(fp_b)
        assert {fp_a, fp_b, fp_c} == {fp_a, fp_c}
