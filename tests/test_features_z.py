"""Tests for Phase D Step D.5 z-slot one-hot expansion in features.py.

Exercises the active-bot module via ``bots.current`` so the test follows
``bots/current/current.txt`` (currently → ``bots/v13``) and survives a
future re-pointer without code changes.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest
from bots.current.decision_engine import GameSnapshot
from bots.current.learning.features import (
    _DB_STATE_FEATURE_COUNT,
    _FEATURE_SPEC,
    BASE_GAME_FEATURE_DIM,
    FEATURE_DIM,
    Z_SLOT_COUNT,
    _load_z_registry,
    _resolve_z_index,
    encode,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_feature_dim_is_55() -> None:
    assert FEATURE_DIM == 55


def test_base_game_feature_dim_is_48() -> None:
    assert BASE_GAME_FEATURE_DIM == 48


def test_z_slot_count_is_8() -> None:
    assert Z_SLOT_COUNT == 8


def test_db_state_feature_count_is_40() -> None:
    """The 8 z slots ride on a separate TEXT column, not on _STATE_COLS."""
    assert _DB_STATE_FEATURE_COUNT == 40


def test_feature_spec_length_matches_base_dim() -> None:
    assert len(_FEATURE_SPEC) == BASE_GAME_FEATURE_DIM


def test_feature_spec_z_slot_names_are_contiguous() -> None:
    """The 8 z slots must be the last 8 entries of _FEATURE_SPEC, in order."""
    z_entries = _FEATURE_SPEC[-Z_SLOT_COUNT:]
    for i, (name, divisor) in enumerate(z_entries):
        assert name == f"z_slot_{i}"
        assert divisor == 1.0


# ---------------------------------------------------------------------------
# _resolve_z_index helper
# ---------------------------------------------------------------------------


class TestResolveZIndex:
    def test_none_maps_to_slot_zero(self) -> None:
        assert _resolve_z_index(None, ["4-gate-aggression", "robo-colossus"]) == 0

    def test_unknown_name_maps_to_slot_zero(self) -> None:
        """Defensive default — unknown names must not crash the encoder."""
        assert _resolve_z_index("not-a-real-build", ["4-gate-aggression"]) == 0

    def test_first_alphabetical_name_maps_to_slot_one(self) -> None:
        registry = ["4-gate-aggression", "robo-colossus"]
        assert _resolve_z_index("4-gate-aggression", registry) == 1

    def test_second_alphabetical_name_maps_to_slot_two(self) -> None:
        registry = ["4-gate-aggression", "robo-colossus"]
        assert _resolve_z_index("robo-colossus", registry) == 2

    def test_eighth_alphabetical_name_falls_back_to_slot_zero(self) -> None:
        """Only the first 7 registry entries are addressable (slots 1..7)."""
        registry = [f"t{i}" for i in range(10)]
        # registry[0..6] -> slots 1..7
        assert _resolve_z_index("t0", registry) == 1
        assert _resolve_z_index("t6", registry) == 7
        # registry[7..9] are not addressable, fall through to slot 0.
        assert _resolve_z_index("t7", registry) == 0
        assert _resolve_z_index("t9", registry) == 0

    def test_empty_registry_returns_zero_for_any_name(self) -> None:
        assert _resolve_z_index("anything", []) == 0


# ---------------------------------------------------------------------------
# _load_z_registry helper
# ---------------------------------------------------------------------------


class TestLoadZRegistry:
    def test_registry_contains_seeded_trajectories(self) -> None:
        """Phase D Step D.2 shipped ``4-gate-aggression`` and ``robo-colossus``."""
        registry = _load_z_registry()
        assert "4-gate-aggression" in registry
        assert "robo-colossus" in registry

    def test_registry_excludes_schema_file(self) -> None:
        """``_schema.json`` is the json-schema, not a trajectory."""
        registry = _load_z_registry()
        assert "_schema" not in registry

    def test_registry_is_alphabetically_sorted(self) -> None:
        registry = _load_z_registry()
        assert registry == sorted(registry)

    def test_registry_emits_warning_when_oversized(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An 8th+ entry should log a warning naming the overflow files."""
        # Build a fake build_orders dir with 10 trajectories.
        fake_dir = tmp_path / "build_orders"
        fake_dir.mkdir()
        (fake_dir / "_schema.json").write_text("{}", encoding="utf-8")
        for i in range(10):
            (fake_dir / f"traj_{i:02d}.json").write_text("{}", encoding="utf-8")

        # Patch resolve_data_path so _load_z_registry points at our fake dir.
        from bots.current.learning import features as features_mod

        from orchestrator import registry as registry_mod

        def fake_resolve_data_path(filename: str, version: str | None = None) -> Path:
            # ``_load_z_registry`` calls ``resolve_data_path("build_orders/_schema.json")``
            # and uses ``.parent``. Any path under ``fake_dir`` works.
            return fake_dir / "_schema.json"

        monkeypatch.setattr(
            registry_mod, "resolve_data_path", fake_resolve_data_path
        )
        # ``features._load_z_registry`` imports lazily — clear any cached
        # binding to the original by importing fresh through the patched
        # registry module if needed. The lazy import inside the function
        # picks up the patched symbol on next call.

        with caplog.at_level(logging.WARNING, logger=features_mod.__name__):
            registry = features_mod._load_z_registry()
        assert len(registry) == 10
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "expected a warning for oversized registry"
        # The warning should mention the overflow file(s).
        joined = " ".join(r.getMessage() for r in warnings)
        assert "traj_07" in joined or "traj_09" in joined


# ---------------------------------------------------------------------------
# Encoder integration
# ---------------------------------------------------------------------------


class TestEncodeZSlot:
    def test_encode_shape_is_55(self) -> None:
        vec = encode(GameSnapshot())
        assert vec.shape == (FEATURE_DIM,)
        assert vec.dtype == np.float32

    def test_none_bucket_round_trip(self) -> None:
        """``current_build_order=None`` -> slot 0 hot, slots 1..7 cold."""
        snap = GameSnapshot(current_build_order=None)
        vec = encode(snap)
        # Z block lives at indices [_DB_STATE_FEATURE_COUNT, _DB_STATE_FEATURE_COUNT + Z_SLOT_COUNT)
        z_start = _DB_STATE_FEATURE_COUNT
        z_end = z_start + Z_SLOT_COUNT
        assert vec[z_start] == pytest.approx(1.0)
        for i in range(z_start + 1, z_end):
            assert vec[i] == pytest.approx(0.0), f"slot {i - z_start} should be 0"

    def test_known_label_round_trip_4gate(self) -> None:
        """``4-gate-aggression`` is alphabetical-first -> slot 1 hot."""
        snap = GameSnapshot(current_build_order="4-gate-aggression")
        vec = encode(snap)
        z_start = _DB_STATE_FEATURE_COUNT
        # Slot 0 (none) is cold, slot 1 (4-gate-aggression) is hot, rest cold.
        assert vec[z_start + 0] == pytest.approx(0.0)
        assert vec[z_start + 1] == pytest.approx(1.0)
        for i in range(2, Z_SLOT_COUNT):
            assert vec[z_start + i] == pytest.approx(0.0)

    def test_known_label_round_trip_robo_colossus(self) -> None:
        """``robo-colossus`` is alphabetical-second -> slot 2 hot."""
        snap = GameSnapshot(current_build_order="robo-colossus")
        vec = encode(snap)
        z_start = _DB_STATE_FEATURE_COUNT
        assert vec[z_start + 0] == pytest.approx(0.0)
        assert vec[z_start + 1] == pytest.approx(0.0)
        assert vec[z_start + 2] == pytest.approx(1.0)
        for i in range(3, Z_SLOT_COUNT):
            assert vec[z_start + i] == pytest.approx(0.0)

    def test_unknown_label_decodes_to_slot_zero(self) -> None:
        """Defensive default — unknown names fall back to the ``none`` bucket."""
        snap = GameSnapshot(current_build_order="never-shipped-build")
        vec = encode(snap)
        z_start = _DB_STATE_FEATURE_COUNT
        assert vec[z_start + 0] == pytest.approx(1.0)
        for i in range(1, Z_SLOT_COUNT):
            assert vec[z_start + i] == pytest.approx(0.0)

    def test_legacy_snapshot_without_kwarg_still_works(self) -> None:
        """``GameSnapshot(...)`` without ``current_build_order`` must still encode.

        Captures the "default value keeps existing constructor sites green"
        requirement from the plan: any pre-D.5 caller that builds a
        snapshot positionally or with a subset of fields should continue
        to work and encode to the ``none`` bucket.
        """
        snap = GameSnapshot(
            supply_used=50,
            supply_cap=100,
            minerals=800,
            zealot_count=5,
        )
        vec = encode(snap)
        assert vec.shape == (FEATURE_DIM,)
        z_start = _DB_STATE_FEATURE_COUNT
        assert vec[z_start + 0] == pytest.approx(1.0)
        for i in range(1, Z_SLOT_COUNT):
            assert vec[z_start + i] == pytest.approx(0.0)
