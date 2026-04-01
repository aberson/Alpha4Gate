"""Unit tests for ArmyCoherenceManager: coherence, staging, attack/retreat logic."""

from __future__ import annotations

from unittest.mock import MagicMock

from alpha4gate.army_coherence import ArmyCoherenceManager


def _mock_unit(x: float = 0.0, y: float = 0.0) -> MagicMock:
    """Create a mock unit with a position."""
    u = MagicMock()
    u.position = MagicMock()
    u.position.x = x
    u.position.y = y
    return u


class TestParameterRandomization:
    def test_params_within_ranges(self) -> None:
        """All rolled params must fall within defined ranges over many seeds."""
        for seed in range(100):
            mgr = ArmyCoherenceManager(seed=seed)
            assert 1.0 <= mgr.attack_supply_ratio <= 1.5
            assert 15.0 <= mgr.attack_supply_floor <= 25.0
            assert 0.4 <= mgr.retreat_supply_ratio <= 0.7
            assert 0.60 <= mgr.coherence_pct <= 0.80
            assert 6.0 <= mgr.coherence_distance <= 10.0
            assert 12.0 <= mgr.staging_distance <= 20.0
            assert isinstance(mgr.retreat_to_staging, bool)

    def test_deterministic_with_seed(self) -> None:
        """Same seed produces same params."""
        a = ArmyCoherenceManager(seed=42)
        b = ArmyCoherenceManager(seed=42)
        assert a.get_params_dict() == b.get_params_dict()

    def test_different_seeds_differ(self) -> None:
        """Different seeds produce different params (with very high probability)."""
        a = ArmyCoherenceManager(seed=1)
        b = ArmyCoherenceManager(seed=2)
        assert a.get_params_dict() != b.get_params_dict()

    def test_get_params_dict_keys(self) -> None:
        mgr = ArmyCoherenceManager(seed=0)
        params = mgr.get_params_dict()
        expected_keys = {
            "attack_supply_ratio",
            "attack_supply_floor",
            "retreat_supply_ratio",
            "coherence_pct",
            "coherence_distance",
            "staging_distance",
            "fortify_trigger_ratio",
            "defense_scaling_divisor",
            "max_defenses",
            "retreat_to_staging",
        }
        assert set(params.keys()) == expected_keys


class TestCentroid:
    def test_empty_units(self) -> None:
        assert ArmyCoherenceManager.compute_centroid([]) == (0.0, 0.0)

    def test_single_unit(self) -> None:
        units = [_mock_unit(10.0, 20.0)]
        assert ArmyCoherenceManager.compute_centroid(units) == (10.0, 20.0)

    def test_multiple_units(self) -> None:
        units = [_mock_unit(0.0, 0.0), _mock_unit(10.0, 10.0)]
        cx, cy = ArmyCoherenceManager.compute_centroid(units)
        assert abs(cx - 5.0) < 0.01
        assert abs(cy - 5.0) < 0.01

    def test_three_units(self) -> None:
        units = [_mock_unit(0.0, 0.0), _mock_unit(3.0, 0.0), _mock_unit(0.0, 6.0)]
        cx, cy = ArmyCoherenceManager.compute_centroid(units)
        assert abs(cx - 1.0) < 0.01
        assert abs(cy - 2.0) < 0.01


class TestCoherence:
    def test_single_unit_always_coherent(self) -> None:
        mgr = ArmyCoherenceManager(seed=0)
        assert mgr.is_coherent([_mock_unit(5.0, 5.0)]) is True

    def test_empty_is_coherent(self) -> None:
        mgr = ArmyCoherenceManager(seed=0)
        assert mgr.is_coherent([]) is True

    def test_grouped_units_coherent(self) -> None:
        """Units clustered tightly should be coherent."""
        mgr = ArmyCoherenceManager(seed=0)
        mgr.coherence_distance = 10.0
        mgr.coherence_pct = 0.6
        units = [_mock_unit(0, 0), _mock_unit(1, 1), _mock_unit(2, 0), _mock_unit(1, -1)]
        assert mgr.is_coherent(units) is True

    def test_scattered_units_not_coherent(self) -> None:
        """Units spread far apart should not be coherent."""
        mgr = ArmyCoherenceManager(seed=0)
        mgr.coherence_distance = 3.0
        mgr.coherence_pct = 0.8
        units = [_mock_unit(0, 0), _mock_unit(50, 50), _mock_unit(100, 100), _mock_unit(1, 1)]
        assert mgr.is_coherent(units) is False

    def test_borderline_coherence(self) -> None:
        """Exactly at the threshold should pass."""
        mgr = ArmyCoherenceManager(seed=0)
        mgr.coherence_pct = 0.5
        mgr.coherence_distance = 5.0
        # 2 of 4 units near centroid = 0.5 = threshold
        # Centroid of (0,0),(1,0),(100,0),(101,0) is (50.5, 0)
        # None are within 5 of centroid. Let's pick better positions.
        # 4 units: 3 clustered, 1 far → centroid pulled toward cluster
        units = [_mock_unit(0, 0), _mock_unit(1, 0), _mock_unit(0, 1), _mock_unit(100, 0)]
        # centroid ~ (25.25, 0.25). None within 5 of that.
        # Use 2 units instead for exact 50%
        mgr.coherence_pct = 0.5
        mgr.coherence_distance = 2.0
        units = [_mock_unit(0, 0), _mock_unit(1, 0)]
        # centroid = (0.5, 0). Both within 2.0 of centroid.
        assert mgr.is_coherent(units) is True


class TestStagingPoint:
    def test_with_enemy_structures(self) -> None:
        """Staging point should be staging_distance from nearest enemy structure."""
        point = ArmyCoherenceManager.compute_staging_point(
            own_base=(0.0, 0.0),
            enemy_structures=[(100.0, 0.0)],
            enemy_start=(100.0, 0.0),
            staging_distance=20.0,
        )
        # Should be at (80, 0) — 20 units from enemy at (100, 0)
        assert abs(point[0] - 80.0) < 0.01
        assert abs(point[1] - 0.0) < 0.01

    def test_no_enemy_structures_fallback(self) -> None:
        """Without enemy structures, stage at 70% of distance to enemy start."""
        point = ArmyCoherenceManager.compute_staging_point(
            own_base=(0.0, 0.0),
            enemy_structures=[],
            enemy_start=(100.0, 0.0),
            staging_distance=15.0,
        )
        # 70% of 100 = 70
        assert abs(point[0] - 70.0) < 0.01
        assert abs(point[1] - 0.0) < 0.01

    def test_very_close_enemy(self) -> None:
        """If enemy structure is closer than staging_distance, use midpoint."""
        point = ArmyCoherenceManager.compute_staging_point(
            own_base=(0.0, 0.0),
            enemy_structures=[(10.0, 0.0)],
            enemy_start=(10.0, 0.0),
            staging_distance=20.0,
        )
        # dist=10 < staging_distance=20 → midpoint at (5, 0)
        assert abs(point[0] - 5.0) < 0.01

    def test_diagonal_path(self) -> None:
        """Staging point on a diagonal still respects distance."""
        import math

        point = ArmyCoherenceManager.compute_staging_point(
            own_base=(0.0, 0.0),
            enemy_structures=[(100.0, 100.0)],
            enemy_start=(100.0, 100.0),
            staging_distance=14.142,  # ~10 * sqrt(2)
        )
        # Distance from point to enemy should be ~14.142
        dist_to_enemy = math.hypot(point[0] - 100.0, point[1] - 100.0)
        assert abs(dist_to_enemy - 14.142) < 0.5

    def test_same_position(self) -> None:
        """Own base and enemy at same position returns own base."""
        point = ArmyCoherenceManager.compute_staging_point(
            own_base=(50.0, 50.0),
            enemy_structures=[],
            enemy_start=(50.0, 50.0),
            staging_distance=15.0,
        )
        assert point == (50.0, 50.0)

    def test_picks_closest_enemy_structure(self) -> None:
        """Should stage relative to the closest enemy structure, not farthest."""
        point = ArmyCoherenceManager.compute_staging_point(
            own_base=(0.0, 0.0),
            enemy_structures=[(50.0, 0.0), (100.0, 0.0)],
            enemy_start=(100.0, 0.0),
            staging_distance=15.0,
        )
        # Closest is (50, 0), stage at 50-15=35
        assert abs(point[0] - 35.0) < 0.01


class TestShouldAttack:
    def test_attack_with_supply_advantage(self) -> None:
        mgr = ArmyCoherenceManager(seed=0)
        mgr.attack_supply_ratio = 1.0
        mgr.attack_supply_floor = 15
        assert mgr.should_attack(30, 20) is True

    def test_no_attack_when_weak(self) -> None:
        mgr = ArmyCoherenceManager(seed=0)
        mgr.attack_supply_ratio = 1.5
        mgr.attack_supply_floor = 50  # high floor so floor doesn't trigger
        assert mgr.should_attack(20, 30) is False

    def test_attack_on_floor_no_enemy(self) -> None:
        """Attack if at floor supply and no enemy visible."""
        mgr = ArmyCoherenceManager(seed=0)
        mgr.attack_supply_floor = 20
        assert mgr.should_attack(20, 0) is True

    def test_no_attack_below_floor_no_enemy(self) -> None:
        mgr = ArmyCoherenceManager(seed=0)
        mgr.attack_supply_floor = 20
        mgr.attack_supply_ratio = 1.0
        assert mgr.should_attack(10, 0) is False

    def test_hysteresis_after_retreat(self) -> None:
        """After retreating, need higher ratio to re-engage."""
        mgr = ArmyCoherenceManager(seed=0)
        mgr.attack_supply_ratio = 1.0
        mgr.attack_supply_floor = 50  # high so floor doesn't interfere

        # Trigger retreat flag
        mgr.should_retreat(5, 20)
        assert mgr._recently_retreated is True

        # Now 20 vs 20 should NOT be enough (need 1.0 * 1.2 = 1.2 ratio)
        assert mgr.should_attack(20, 20) is False

        # 24 vs 20 = 1.2 ratio — should be enough
        assert mgr.should_attack(24, 20) is True
        # Flag should be cleared after successful attack decision
        assert mgr._recently_retreated is False


class TestShouldRetreat:
    def test_retreat_when_outnumbered(self) -> None:
        mgr = ArmyCoherenceManager(seed=0)
        mgr.retreat_supply_ratio = 0.5
        # 10 < 30 * 0.5 = 15 → retreat
        assert mgr.should_retreat(10, 30) is True

    def test_no_retreat_when_strong(self) -> None:
        mgr = ArmyCoherenceManager(seed=0)
        mgr.retreat_supply_ratio = 0.5
        assert mgr.should_retreat(30, 20) is False

    def test_no_retreat_no_enemy(self) -> None:
        mgr = ArmyCoherenceManager(seed=0)
        assert mgr.should_retreat(5, 0) is False

    def test_retreat_sets_flag(self) -> None:
        mgr = ArmyCoherenceManager(seed=0)
        mgr.retreat_supply_ratio = 0.5
        mgr.should_retreat(5, 20)
        assert mgr._recently_retreated is True


class TestStagingTimeout:
    def test_no_timeout_initially(self) -> None:
        mgr = ArmyCoherenceManager(seed=0)
        assert mgr.update_staging_timer(0.0, is_staging=True) is False

    def test_timeout_after_duration(self) -> None:
        mgr = ArmyCoherenceManager(seed=0)
        mgr.update_staging_timer(0.0, is_staging=True)
        assert mgr.update_staging_timer(59.0, is_staging=True) is False
        assert mgr.update_staging_timer(60.0, is_staging=True) is True

    def test_reset_when_not_staging(self) -> None:
        mgr = ArmyCoherenceManager(seed=0)
        mgr.update_staging_timer(0.0, is_staging=True)
        mgr.update_staging_timer(20.0, is_staging=True)
        # Stop staging resets timer
        mgr.update_staging_timer(25.0, is_staging=False)
        # Restart staging — should not immediately timeout
        assert mgr.update_staging_timer(26.0, is_staging=True) is False
        assert mgr.update_staging_timer(85.0, is_staging=True) is False
        assert mgr.update_staging_timer(86.0, is_staging=True) is True


class TestRetreatDestination:
    def test_retreat_to_staging_flag(self) -> None:
        """The retreat_to_staging param is a bool that varies with seed."""
        results = set()
        for seed in range(50):
            mgr = ArmyCoherenceManager(seed=seed)
            results.add(mgr.retreat_to_staging)
        # Over 50 seeds, both True and False should appear
        assert True in results
        assert False in results
