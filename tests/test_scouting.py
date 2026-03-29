"""Unit tests for scouting and threat assessment."""

from __future__ import annotations

from unittest.mock import MagicMock

from sc2.ids.unit_typeid import UnitTypeId

from alpha4gate.scouting import (
    EnemyComposition,
    ScoutManager,
    assess_enemy,
    compute_threat_score,
    threat_level_from_score,
)


def _mock_unit(type_id: UnitTypeId, name: str = "Unit") -> MagicMock:
    """Create a mock unit with a type_id."""
    u = MagicMock()
    u.type_id = type_id
    u.name = name
    return u


class TestComputeThreatScore:
    def test_empty_list(self) -> None:
        assert compute_threat_score([]) == 0.0

    def test_single_marine(self) -> None:
        units = [_mock_unit(UnitTypeId.MARINE)]
        assert compute_threat_score(units) == 1.0

    def test_mixed_army(self) -> None:
        units = [
            _mock_unit(UnitTypeId.MARINE),
            _mock_unit(UnitTypeId.MARINE),
            _mock_unit(UnitTypeId.SIEGETANK),
        ]
        # 1.0 + 1.0 + 3.0 = 5.0
        assert compute_threat_score(units) == 5.0

    def test_unknown_unit_uses_default(self) -> None:
        units = [_mock_unit(UnitTypeId.OBSERVER)]  # Not in THREAT_WEIGHTS
        assert compute_threat_score(units) == 1.0

    def test_high_value_units(self) -> None:
        units = [
            _mock_unit(UnitTypeId.BATTLECRUISER),
            _mock_unit(UnitTypeId.CARRIER),
        ]
        assert compute_threat_score(units) == 16.0


class TestThreatLevel:
    def test_none(self) -> None:
        assert threat_level_from_score(0.0) == "none"

    def test_low(self) -> None:
        assert threat_level_from_score(3.0) == "low"

    def test_medium(self) -> None:
        assert threat_level_from_score(10.0) == "medium"

    def test_high(self) -> None:
        assert threat_level_from_score(25.0) == "high"

    def test_critical(self) -> None:
        assert threat_level_from_score(50.0) == "critical"

    def test_boundary_low(self) -> None:
        assert threat_level_from_score(5.0) == "low"

    def test_boundary_medium(self) -> None:
        assert threat_level_from_score(15.0) == "medium"

    def test_boundary_high(self) -> None:
        assert threat_level_from_score(30.0) == "high"


class TestAssessEnemy:
    def test_empty_enemy(self) -> None:
        bot = MagicMock()
        bot.enemy_units = []
        bot.enemy_structures = []
        comp = assess_enemy(bot)
        assert comp.threat_score == 0.0
        assert comp.threat_level == "none"
        assert comp.units == {}
        assert comp.structures == {}

    def test_counts_units(self) -> None:
        bot = MagicMock()
        bot.enemy_units = [
            _mock_unit(UnitTypeId.MARINE, "Marine"),
            _mock_unit(UnitTypeId.MARINE, "Marine"),
            _mock_unit(UnitTypeId.MARAUDER, "Marauder"),
        ]
        bot.enemy_structures = []
        comp = assess_enemy(bot)
        assert comp.units["Marine"] == 2
        assert comp.units["Marauder"] == 1

    def test_counts_structures(self) -> None:
        bot = MagicMock()
        bot.enemy_units = []
        s1 = MagicMock()
        s1.name = "Barracks"
        s2 = MagicMock()
        s2.name = "Barracks"
        bot.enemy_structures = [s1, s2]
        comp = assess_enemy(bot)
        assert comp.structures["Barracks"] == 2

    def test_threat_level_computed(self) -> None:
        bot = MagicMock()
        bot.enemy_units = [_mock_unit(UnitTypeId.BATTLECRUISER, "Battlecruiser")]
        bot.enemy_structures = []
        comp = assess_enemy(bot)
        assert comp.threat_level == "medium"  # 8.0 score


class TestEnemyComposition:
    def test_to_dict(self) -> None:
        comp = EnemyComposition(
            units={"Marine": 3},
            structures={"Barracks": 1},
            threat_score=5.5,
            threat_level="medium",
        )
        d = comp.to_dict()
        assert d["units"] == {"Marine": 3}
        assert d["threat_score"] == 5.5
        assert d["threat_level"] == "medium"


class TestScoutManager:
    def test_should_scout_before_first_time(self) -> None:
        mgr = ScoutManager()
        assert not mgr.should_scout(30.0)

    def test_should_scout_after_first_time(self) -> None:
        mgr = ScoutManager()
        assert mgr.should_scout(61.0)

    def test_should_not_scout_while_scouting(self) -> None:
        mgr = ScoutManager()
        mgr.assign_scout(123, 61.0)
        assert not mgr.should_scout(200.0)

    def test_should_scout_after_interval(self) -> None:
        mgr = ScoutManager()
        mgr.assign_scout(123, 61.0)
        mgr.clear_scout()
        assert mgr.should_scout(200.0)

    def test_should_not_scout_before_interval(self) -> None:
        mgr = ScoutManager()
        mgr.assign_scout(123, 61.0)
        mgr.clear_scout()
        assert not mgr.should_scout(100.0)

    def test_assign_and_clear(self) -> None:
        mgr = ScoutManager()
        mgr.assign_scout(42, 60.0)
        assert mgr.scout_tag == 42
        mgr.clear_scout()
        assert mgr.scout_tag is None

    def test_update_enemy_bases(self) -> None:
        mgr = ScoutManager()
        mgr.update_enemy_bases([(10, 20), (30, 40)])
        assert len(mgr.enemy_base_locations) == 2
