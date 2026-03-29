"""Tests for feature encoding and decoding."""

import numpy as np

from alpha4gate.decision_engine import GameSnapshot
from alpha4gate.learning.features import FEATURE_DIM, decode, encode


class TestEncode:
    def test_output_shape(self) -> None:
        snap = GameSnapshot()
        vec = encode(snap)
        assert vec.shape == (FEATURE_DIM,)
        assert vec.dtype == np.float32

    def test_all_zeros_for_default_snapshot(self) -> None:
        snap = GameSnapshot()
        vec = encode(snap)
        assert np.all(vec >= 0.0)
        assert np.all(vec <= 1.0)

    def test_normalization_bounds(self) -> None:
        snap = GameSnapshot(
            supply_used=200,
            supply_cap=200,
            minerals=2000,
            vespene=2000,
            army_supply=200,
            worker_count=80,
            base_count=5,
            enemy_army_near_base=True,
            enemy_army_supply_visible=200,
            game_time_seconds=1200.0,
            gateway_count=10,
            robo_count=4,
            forge_count=2,
            upgrade_count=10,
        )
        vec = encode(snap)
        np.testing.assert_allclose(vec, np.ones(FEATURE_DIM, dtype=np.float32), atol=1e-6)

    def test_clipping_above_max(self) -> None:
        snap = GameSnapshot(minerals=5000, vespene=5000)
        vec = encode(snap)
        assert vec[2] == 1.0  # minerals clipped
        assert vec[3] == 1.0  # vespene clipped

    def test_bool_field_encoding(self) -> None:
        snap_false = GameSnapshot(enemy_army_near_base=False)
        snap_true = GameSnapshot(enemy_army_near_base=True)
        assert encode(snap_false)[7] == 0.0
        assert encode(snap_true)[7] == 1.0

    def test_partial_values(self) -> None:
        snap = GameSnapshot(supply_used=100, supply_cap=200, minerals=1000)
        vec = encode(snap)
        assert abs(vec[0] - 0.5) < 1e-6  # 100/200
        assert abs(vec[1] - 1.0) < 1e-6  # 200/200
        assert abs(vec[2] - 0.5) < 1e-6  # 1000/2000


class TestDecode:
    def test_round_trip(self) -> None:
        snap = GameSnapshot(
            supply_used=50,
            supply_cap=100,
            minerals=800,
            vespene=400,
            army_supply=30,
            worker_count=22,
            base_count=2,
            enemy_army_near_base=True,
            enemy_army_supply_visible=15,
            game_time_seconds=300.0,
            gateway_count=3,
            robo_count=1,
            forge_count=1,
            upgrade_count=2,
        )
        vec = encode(snap)
        restored = decode(vec)
        assert restored.supply_used == snap.supply_used
        assert restored.supply_cap == snap.supply_cap
        assert restored.minerals == snap.minerals
        assert restored.vespene == snap.vespene
        assert restored.army_supply == snap.army_supply
        assert restored.worker_count == snap.worker_count
        assert restored.base_count == snap.base_count
        assert restored.enemy_army_near_base == snap.enemy_army_near_base
        assert restored.enemy_army_supply_visible == snap.enemy_army_supply_visible
        assert restored.gateway_count == snap.gateway_count
        assert restored.robo_count == snap.robo_count
        assert restored.forge_count == snap.forge_count
        assert restored.upgrade_count == snap.upgrade_count

    def test_wrong_shape_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Expected shape"):
            decode(np.zeros(5, dtype=np.float32))

    def test_decode_zeros(self) -> None:
        vec = np.zeros(FEATURE_DIM, dtype=np.float32)
        snap = decode(vec)
        assert snap.supply_used == 0
        assert snap.enemy_army_near_base is False
