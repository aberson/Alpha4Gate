"""Tests for ``orchestrator.fingerprint`` (Phase EL Step 3).

Covers:
- :class:`Fingerprint` json round-trip + ``from_dict`` optional-field defaults.
- :func:`compute_fingerprint` with a mocked ``run_gauntlet_fn`` (per_baseline
  carried through; empty baselines → empty vector), PLUS a production-caller
  integration test that drives the REAL ``run_baseline_gauntlet`` with a
  mocked ``run_batch_fn`` and proves the gauntlet output is mapped through.
- :func:`fingerprint_distance`: identical → 0.0; opposite → 1.0; partial
  overlap uses only shared keys; disjoint keys → documented NaN sentinel.
- Registry round-trip (``load_fingerprints`` / ``write_fingerprints``) and
  :func:`save_fingerprint` upsert.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from orchestrator.baselines import Baseline
from orchestrator.contracts import SelfPlayRecord
from orchestrator.evolve import GauntletResult
from orchestrator.fingerprint import (
    Fingerprint,
    compute_fingerprint,
    fingerprint_distance,
    load_fingerprints,
    save_fingerprint,
    write_fingerprints,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fp(
    version: str, per_baseline: dict[str, float]
) -> Fingerprint:
    return Fingerprint(
        version=version,
        per_baseline=per_baseline,
        computed_at="2026-06-19T00:00:00+00:00",
    )


def _make_baseline(name: str, version: str) -> Baseline:
    return Baseline(
        name=name,
        version=version,
        added_at="2026-06-19T00:00:00+00:00",
    )


def _rec(p1: str, p2: str, winner: str | None) -> SelfPlayRecord:
    return SelfPlayRecord(
        match_id="m",
        p1_version=p1,
        p2_version=p2,
        winner=winner,
        map_name="Simple64",
        duration_s=10.0,
        seat_swap=False,
        timestamp="2026-06-19T00:00:00+00:00",
        error=None,
    )


# ---------------------------------------------------------------------------
# Dataclass json helpers
# ---------------------------------------------------------------------------


def test_fingerprint_json_round_trip() -> None:
    fp = _fp("v8", {"b1": 0.75, "b2": 0.25})
    restored = Fingerprint.from_json(fp.to_json())
    assert restored == fp


def test_fingerprint_from_dict_fills_optional_defaults() -> None:
    fp = Fingerprint.from_dict({"version": "v8"})
    assert fp.version == "v8"
    assert fp.per_baseline == {}
    assert fp.computed_at  # default factory stamped a timestamp


def test_fingerprint_from_dict_coerces_win_rates_to_float() -> None:
    fp = Fingerprint.from_dict(
        {"version": "v8", "per_baseline": {"b1": 1, "b2": 0}}
    )
    assert fp.per_baseline == {"b1": 1.0, "b2": 0.0}
    assert all(isinstance(v, float) for v in fp.per_baseline.values())


def test_fingerprint_from_dict_missing_version_raises() -> None:
    with pytest.raises(
        ValueError, match="missing required field 'version'"
    ):
        Fingerprint.from_dict({"per_baseline": {}})


def test_fingerprint_from_dict_null_win_rate_raises_value_error() -> None:
    # A JSON null win-rate must raise ValueError (not TypeError from
    # float(None)) so load_fingerprints honors its ValueError-only contract.
    with pytest.raises(ValueError, match="non-numeric win-rate"):
        Fingerprint.from_dict(
            {"version": "v8", "per_baseline": {"b1": None}}
        )


def test_fingerprint_from_dict_non_numeric_win_rate_raises_value_error() -> None:
    with pytest.raises(ValueError, match="non-numeric win-rate"):
        Fingerprint.from_dict(
            {"version": "v8", "per_baseline": {"b1": "nope"}}
        )


def test_fingerprint_from_dict_non_dict_per_baseline_raises_value_error() -> None:
    # A non-dict per_baseline must raise ValueError (not AttributeError from
    # .items()) — proves load_fingerprints raises ValueError, not
    # AttributeError, so the EL.4 consumer's guard degrades gracefully.
    with pytest.raises(ValueError, match="must be a JSON object"):
        Fingerprint.from_dict(
            {"version": "v8", "per_baseline": [1, 2, 3]}
        )


def test_load_fingerprints_null_win_rate_raises_value_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fingerprints.json"
    path.write_text(
        json.dumps({"v8": {"per_baseline": {"b1": None}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-numeric win-rate"):
        load_fingerprints(path)


def test_load_fingerprints_non_dict_per_baseline_raises_value_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fingerprints.json"
    path.write_text(
        json.dumps({"v8": {"per_baseline": [1, 2, 3]}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must be a JSON object"):
        load_fingerprints(path)


# ---------------------------------------------------------------------------
# compute_fingerprint
# ---------------------------------------------------------------------------


def test_compute_fingerprint_carries_per_baseline_through() -> None:
    captured: dict[str, Any] = {}

    def _gauntlet(
        version: str, baselines: list[Baseline], **kwargs: Any
    ) -> GauntletResult:
        captured["version"] = version
        captured["baselines"] = baselines
        captured["kwargs"] = kwargs
        return GauntletResult(
            candidate=version,
            per_baseline={"b1": 0.75, "b2": 0.25},
            mean_win_rate=0.5,
            games_each=4,
            record=[],
        )

    baselines = [_make_baseline("b1", "v3"), _make_baseline("b2", "v5")]
    fp = compute_fingerprint(
        "v8", baselines, games_each=4, run_gauntlet_fn=_gauntlet
    )
    assert fp.version == "v8"
    assert fp.per_baseline == {"b1": 0.75, "b2": 0.25}
    # The gauntlet seam received the version, baselines, and games_each.
    assert captured["version"] == "v8"
    assert captured["baselines"] == baselines
    assert captured["kwargs"]["games_each"] == 4


def test_compute_fingerprint_empty_baselines_yields_empty_vector() -> None:
    def _gauntlet(
        version: str, baselines: list[Baseline], **kwargs: Any
    ) -> GauntletResult:
        return GauntletResult(
            candidate=version,
            per_baseline={},
            mean_win_rate=0.0,
            games_each=5,
            record=[],
        )

    fp = compute_fingerprint("v8", [], run_gauntlet_fn=_gauntlet)
    assert fp.per_baseline == {}


def test_compute_fingerprint_through_real_gauntlet() -> None:
    # Production-caller integration test: no run_gauntlet_fn, so the default
    # orchestrator.evolve.run_baseline_gauntlet actually runs. We only mock
    # the per-game seam (run_batch_fn) and prove compute_fingerprint maps the
    # gauntlet's per_baseline vector into the Fingerprint end-to-end.
    games = 4
    plan: dict[str, list[SelfPlayRecord]] = {
        "v3": [_rec("v8", "v3", "v8")] * 3 + [_rec("v8", "v3", "v3")] * 1,
        "v5": [_rec("v8", "v5", "v8")] * 1 + [_rec("v8", "v5", "v5")] * 3,
    }

    def _run_batch(
        p1: str, p2: str, n: int, map_name: str = "Simple64", **k: Any
    ) -> list[SelfPlayRecord]:
        return plan[p2]

    baselines = [_make_baseline("b1", "v3"), _make_baseline("b2", "v5")]
    fp = compute_fingerprint(
        "v8", baselines, games_each=games, run_batch_fn=_run_batch
    )
    # 3/4 vs v3, 1/4 vs v5 — straight from the real gauntlet aggregation.
    assert fp.per_baseline == {"b1": 0.75, "b2": 0.25}
    assert fp.version == "v8"


# ---------------------------------------------------------------------------
# fingerprint_distance
# ---------------------------------------------------------------------------


def test_distance_identical_is_zero() -> None:
    a = _fp("v8", {"b1": 0.75, "b2": 0.25})
    b = _fp("v9", {"b1": 0.75, "b2": 0.25})
    assert fingerprint_distance(a, b) == 0.0


def test_distance_opposite_is_one() -> None:
    a = _fp("v8", {"b1": 1.0, "b2": 1.0, "b3": 1.0})
    b = _fp("v9", {"b1": 0.0, "b2": 0.0, "b3": 0.0})
    assert fingerprint_distance(a, b) == 1.0


def test_distance_uses_only_shared_keys() -> None:
    # Shared keys are b1, b2. b3 (only in a) and b9 (only in b) are ignored.
    a = _fp("v8", {"b1": 1.0, "b2": 0.0, "b3": 0.5})
    b = _fp("v9", {"b1": 0.0, "b2": 0.0, "b9": 1.0})
    # |1-0| + |0-0| = 1.0 over 2 shared keys → 0.5
    d = fingerprint_distance(a, b)
    assert d == pytest.approx(0.5)
    # Comparable (shared-keys non-empty) case stays bounded in [0,1].
    assert 0.0 <= d <= 1.0


def test_distance_disjoint_keys_returns_nan_sentinel() -> None:
    a = _fp("v8", {"b1": 1.0})
    b = _fp("v9", {"b2": 0.0})
    # Documented sentinel: no shared baselines → incomparable → NaN (not 0.0,
    # which would collide with the identical-fingerprints result).
    result = fingerprint_distance(a, b)
    assert math.isnan(result)


# ---------------------------------------------------------------------------
# Registry persistence
# ---------------------------------------------------------------------------


def test_registry_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "fingerprints.json"
    registry = {
        "v8": _fp("v8", {"b1": 0.75, "b2": 0.25}),
        "v9": _fp("v9", {"b1": 0.5}),
    }
    write_fingerprints(path, registry)
    loaded = load_fingerprints(path)
    assert loaded == registry
    # On-disk shape is a JSON object keyed by version.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw.keys()) == {"v8", "v9"}
    assert raw["v8"]["per_baseline"]["b1"] == 0.75


def test_load_fingerprints_missing_file_returns_empty(
    tmp_path: Path,
) -> None:
    assert load_fingerprints(tmp_path / "nope.json") == {}


def test_load_fingerprints_blank_file_returns_empty(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fingerprints.json"
    path.write_text("   \n", encoding="utf-8")
    assert load_fingerprints(path) == {}


def test_load_fingerprints_rejects_non_object_top_level(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fingerprints.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        load_fingerprints(path)


def test_load_fingerprints_backfills_version_from_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fingerprints.json"
    path.write_text(
        json.dumps({"v8": {"per_baseline": {"b1": 1.0}}}),
        encoding="utf-8",
    )
    loaded = load_fingerprints(path)
    assert loaded["v8"].version == "v8"
    assert loaded["v8"].per_baseline == {"b1": 1.0}


def test_save_fingerprint_upsert_overwrites_same_version(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fingerprints.json"
    save_fingerprint(path, _fp("v8", {"b1": 0.5}))
    save_fingerprint(path, _fp("v9", {"b1": 0.2}))
    # Re-save v8 with a new vector — should overwrite, not duplicate.
    save_fingerprint(path, _fp("v8", {"b1": 0.9, "b2": 0.1}))
    loaded = load_fingerprints(path)
    assert set(loaded.keys()) == {"v8", "v9"}
    assert loaded["v8"].per_baseline == {"b1": 0.9, "b2": 0.1}
    assert loaded["v9"].per_baseline == {"b1": 0.2}
