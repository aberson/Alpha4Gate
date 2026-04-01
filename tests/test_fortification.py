"""Unit tests for the fortification manager."""

from __future__ import annotations

from alpha4gate.fortification import FortificationManager, _clamp


class TestClamp:
    def test_clamp_below(self) -> None:
        assert _clamp(-5, 1, 5) == 1

    def test_clamp_above(self) -> None:
        assert _clamp(10, 1, 5) == 5

    def test_clamp_within(self) -> None:
        assert _clamp(3, 1, 5) == 3

    def test_clamp_at_boundaries(self) -> None:
        assert _clamp(1, 1, 5) == 1
        assert _clamp(5, 1, 5) == 5


class TestScalingFormula:
    def test_no_advantage_returns_min(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=5)
        assert fm.desired_count(enemy_supply=20, own_supply=30) == 1

    def test_equal_supply_returns_min(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=5)
        assert fm.desired_count(enemy_supply=30, own_supply=30) == 1

    def test_moderate_advantage(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=5)
        # advantage=20, 20//10=2, clamp(2,1,5)=2
        assert fm.desired_count(enemy_supply=50, own_supply=30) == 2

    def test_large_advantage_capped_at_max(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=3)
        # advantage=100, 100//10=10, clamp(10,1,3)=3
        assert fm.desired_count(enemy_supply=130, own_supply=30) == 3

    def test_small_advantage_returns_min(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=15.0, max_defenses=5)
        # advantage=5, 5//15=0, clamp(0,1,5)=1
        assert fm.desired_count(enemy_supply=35, own_supply=30) == 1

    def test_custom_min_defenses(self) -> None:
        fm = FortificationManager(
            defense_scaling_divisor=10.0, max_defenses=5, min_defenses=2
        )
        assert fm.desired_count(enemy_supply=20, own_supply=20) == 2


class TestForgePrerequisite:
    def test_requests_forge_when_missing(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=3)
        decisions = fm.evaluate(
            enemy_supply=50,
            own_supply=20,
            existing_cannons=0,
            existing_batteries=0,
            has_forge=False,
            forge_building=False,
            has_cybernetics_core=True,
            has_pylon_near_natural=True,
        )
        targets = [d.target for d in decisions]
        assert "Forge" in targets

    def test_no_forge_request_when_building(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=3)
        decisions = fm.evaluate(
            enemy_supply=50,
            own_supply=20,
            existing_cannons=0,
            existing_batteries=0,
            has_forge=False,
            forge_building=True,
            has_cybernetics_core=True,
            has_pylon_near_natural=True,
        )
        targets = [d.target for d in decisions]
        assert "Forge" not in targets

    def test_no_cannons_without_forge(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=3)
        decisions = fm.evaluate(
            enemy_supply=50,
            own_supply=20,
            existing_cannons=0,
            existing_batteries=0,
            has_forge=False,
            forge_building=False,
            has_cybernetics_core=True,
            has_pylon_near_natural=True,
        )
        targets = [d.target for d in decisions]
        assert "PhotonCannon" not in targets


class TestBatteryBeforeCannon:
    def test_batteries_ordered_before_cannons(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=3)
        decisions = fm.evaluate(
            enemy_supply=60,
            own_supply=20,
            existing_cannons=0,
            existing_batteries=0,
            has_forge=True,
            forge_building=False,
            has_cybernetics_core=True,
            has_pylon_near_natural=True,
        )
        targets = [d.target for d in decisions]
        # All batteries should come before any cannon
        battery_indices = [i for i, t in enumerate(targets) if t == "ShieldBattery"]
        cannon_indices = [i for i, t in enumerate(targets) if t == "PhotonCannon"]
        assert battery_indices, "expected at least one ShieldBattery"
        assert cannon_indices, "expected at least one PhotonCannon"
        assert max(battery_indices) < min(cannon_indices)

    def test_batteries_without_cybernetics_core(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=3)
        decisions = fm.evaluate(
            enemy_supply=60,
            own_supply=20,
            existing_cannons=0,
            existing_batteries=0,
            has_forge=True,
            forge_building=False,
            has_cybernetics_core=False,
            has_pylon_near_natural=True,
        )
        targets = [d.target for d in decisions]
        assert "ShieldBattery" not in targets


class TestPylonPrerequisite:
    def test_requests_pylon_when_no_natural_coverage(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=3)
        decisions = fm.evaluate(
            enemy_supply=50,
            own_supply=20,
            existing_cannons=0,
            existing_batteries=0,
            has_forge=True,
            forge_building=False,
            has_cybernetics_core=True,
            has_pylon_near_natural=False,
        )
        targets = [d.target for d in decisions]
        assert targets[0] == "Pylon"

    def test_no_extra_pylon_when_covered(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=3)
        decisions = fm.evaluate(
            enemy_supply=50,
            own_supply=20,
            existing_cannons=0,
            existing_batteries=0,
            has_forge=True,
            forge_building=False,
            has_cybernetics_core=True,
            has_pylon_near_natural=True,
        )
        targets = [d.target for d in decisions]
        assert "Pylon" not in targets


class TestExistingStructures:
    def test_existing_batteries_reduce_count(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=5)
        # desired count = clamp(30//10, 1, 5) = 3
        decisions = fm.evaluate(
            enemy_supply=60,
            own_supply=30,
            existing_cannons=0,
            existing_batteries=2,
            has_forge=True,
            forge_building=False,
            has_cybernetics_core=True,
            has_pylon_near_natural=True,
        )
        battery_count = sum(1 for d in decisions if d.target == "ShieldBattery")
        assert battery_count == 1  # 3 - 2 = 1

    def test_existing_cannons_reduce_count(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=5)
        decisions = fm.evaluate(
            enemy_supply=60,
            own_supply=30,
            existing_cannons=3,
            existing_batteries=0,
            has_forge=True,
            forge_building=False,
            has_cybernetics_core=True,
            has_pylon_near_natural=True,
        )
        cannon_count = sum(1 for d in decisions if d.target == "PhotonCannon")
        assert cannon_count == 0  # 3 - 3 = 0

    def test_all_built_returns_empty(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=3)
        decisions = fm.evaluate(
            enemy_supply=60,
            own_supply=30,
            existing_cannons=3,
            existing_batteries=3,
            has_forge=True,
            forge_building=False,
            has_cybernetics_core=True,
            has_pylon_near_natural=True,
        )
        assert len(decisions) == 0


class TestEdgeCases:
    def test_zero_enemy_supply(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=3)
        decisions = fm.evaluate(
            enemy_supply=0,
            own_supply=30,
            existing_cannons=0,
            existing_batteries=0,
            has_forge=True,
            forge_building=False,
            has_cybernetics_core=True,
            has_pylon_near_natural=True,
        )
        # min_defenses=1, so still requests 1 of each
        battery_count = sum(1 for d in decisions if d.target == "ShieldBattery")
        cannon_count = sum(1 for d in decisions if d.target == "PhotonCannon")
        assert battery_count == 1
        assert cannon_count == 1

    def test_all_decisions_are_build_actions(self) -> None:
        fm = FortificationManager(defense_scaling_divisor=10.0, max_defenses=3)
        decisions = fm.evaluate(
            enemy_supply=50,
            own_supply=20,
            existing_cannons=0,
            existing_batteries=0,
            has_forge=True,
            forge_building=False,
            has_cybernetics_core=True,
            has_pylon_near_natural=True,
        )
        for d in decisions:
            assert d.action == "build"
