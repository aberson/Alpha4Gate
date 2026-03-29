"""Unit tests for micro controller: target priority, kiting, focus fire."""

from __future__ import annotations

from unittest.mock import MagicMock

from sc2.ids.unit_typeid import UnitTypeId

from alpha4gate.micro import (
    KITE_DISTANCE,
    MicroCommand,
    MicroController,
    kite_position,
    select_target,
    should_kite,
)


def _mock_unit(
    type_id: UnitTypeId,
    health: float = 100.0,
    tag: int = 1,
    x: float = 0.0,
    y: float = 0.0,
) -> MagicMock:
    """Create a mock unit for micro tests."""
    u = MagicMock()
    u.type_id = type_id
    u.health = health
    u.tag = tag
    u.position = MagicMock()
    u.position.x = x
    u.position.y = y
    return u


class TestSelectTarget:
    def test_empty_returns_none(self) -> None:
        assert select_target([]) is None

    def test_single_enemy(self) -> None:
        enemy = _mock_unit(UnitTypeId.MARINE, tag=1)
        assert select_target([enemy]) == enemy

    def test_prefers_high_priority(self) -> None:
        medivac = _mock_unit(UnitTypeId.MEDIVAC, tag=1)
        marine = _mock_unit(UnitTypeId.MARINE, tag=2)
        target = select_target([marine, medivac])
        assert target.tag == 1  # Medivac has higher priority

    def test_same_priority_prefers_lower_health(self) -> None:
        m1 = _mock_unit(UnitTypeId.MARINE, health=45.0, tag=1)
        m2 = _mock_unit(UnitTypeId.MARINE, health=10.0, tag=2)
        target = select_target([m1, m2])
        assert target.tag == 2  # Lower health

    def test_priority_beats_health(self) -> None:
        marine = _mock_unit(UnitTypeId.MARINE, health=1.0, tag=1)
        medivac = _mock_unit(UnitTypeId.MEDIVAC, health=100.0, tag=2)
        target = select_target([marine, medivac])
        assert target.tag == 2  # Medivac priority > Marine, despite health

    def test_custom_priorities(self) -> None:
        m = _mock_unit(UnitTypeId.MARINE, tag=1)
        z = _mock_unit(UnitTypeId.ZERGLING, tag=2)
        custom = {UnitTypeId.ZERGLING: 10, UnitTypeId.MARINE: 1}
        target = select_target([m, z], priorities=custom)
        assert target.tag == 2  # Zergling with custom high priority


class TestShouldKite:
    def test_stalker_should_kite(self) -> None:
        assert should_kite(UnitTypeId.STALKER) is True

    def test_zealot_should_not_kite(self) -> None:
        assert should_kite(UnitTypeId.ZEALOT) is False

    def test_sentry_should_kite(self) -> None:
        assert should_kite(UnitTypeId.SENTRY) is True

    def test_marine_should_not_kite(self) -> None:
        assert should_kite(UnitTypeId.MARINE) is False


class TestKitePosition:
    def test_moves_away_from_enemy(self) -> None:
        # Unit at (10, 10), enemy at (10, 5) → should move north (y increases)
        pos = kite_position((10.0, 10.0), (10.0, 5.0), distance=5.0)
        assert pos[1] > 10.0  # Moved away in y

    def test_distance_correct(self) -> None:
        pos = kite_position((0.0, 0.0), (5.0, 0.0), distance=3.0)
        # Should move left (away from enemy at x=5)
        expected_x = -3.0
        assert abs(pos[0] - expected_x) < 0.01
        assert abs(pos[1]) < 0.01

    def test_diagonal_direction(self) -> None:
        pos = kite_position((5.0, 5.0), (3.0, 3.0), distance=KITE_DISTANCE)
        # Should move up-right (away from enemy at bottom-left)
        assert pos[0] > 5.0
        assert pos[1] > 5.0

    def test_zero_distance_handles_overlap(self) -> None:
        pos = kite_position((5.0, 5.0), (5.0, 5.0), distance=3.0)
        # Should not crash; moves in arbitrary direction
        assert pos != (5.0, 5.0)


class TestMicroCommand:
    def test_to_dict_attack(self) -> None:
        cmd = MicroCommand(unit_tag=1, action="attack", target_tag=2)
        d = cmd.to_dict()
        assert d["unit_tag"] == 1
        assert d["action"] == "attack"
        assert d["target_tag"] == 2
        assert "target_position" not in d

    def test_to_dict_move(self) -> None:
        cmd = MicroCommand(unit_tag=1, action="move", target_position=(10.0, 20.0))
        d = cmd.to_dict()
        assert d["target_position"] == [10.0, 20.0]
        assert "target_tag" not in d


class TestMicroController:
    def test_no_units_returns_empty(self) -> None:
        ctrl = MicroController()
        cmds = ctrl.generate_commands([], [])
        assert cmds == []

    def test_attack_command_with_enemies(self) -> None:
        stalker = _mock_unit(UnitTypeId.STALKER, tag=1, x=20.0, y=20.0)
        enemy = _mock_unit(UnitTypeId.MARINE, tag=10, x=30.0, y=30.0)
        ctrl = MicroController()
        cmds = ctrl.generate_commands([stalker], [enemy])
        assert len(cmds) == 1
        assert cmds[0].action == "attack"
        assert cmds[0].target_tag == 10

    def test_kite_when_close(self) -> None:
        stalker = _mock_unit(UnitTypeId.STALKER, tag=1, x=10.0, y=10.0)
        zergling = _mock_unit(UnitTypeId.ZERGLING, tag=10, x=12.0, y=10.0)
        ctrl = MicroController()
        cmds = ctrl.generate_commands([stalker], [zergling])
        assert len(cmds) == 1
        assert cmds[0].action == "move"  # Kiting
        assert cmds[0].target_position is not None

    def test_rally_when_no_enemies(self) -> None:
        stalker = _mock_unit(UnitTypeId.STALKER, tag=1, x=10.0, y=10.0)
        ctrl = MicroController()
        cmds = ctrl.generate_commands([stalker], [], rally_point=(50.0, 50.0))
        assert len(cmds) == 1
        assert cmds[0].action == "move"
        assert cmds[0].target_position == (50.0, 50.0)

    def test_skips_workers(self) -> None:
        probe = _mock_unit(UnitTypeId.PROBE, tag=1, x=10.0, y=10.0)
        enemy = _mock_unit(UnitTypeId.MARINE, tag=10, x=30.0, y=30.0)
        ctrl = MicroController()
        cmds = ctrl.generate_commands([probe], [enemy])
        assert len(cmds) == 0

    def test_multiple_units(self) -> None:
        s1 = _mock_unit(UnitTypeId.ZEALOT, tag=1, x=10.0, y=10.0)
        s2 = _mock_unit(UnitTypeId.ZEALOT, tag=2, x=12.0, y=10.0)
        enemy = _mock_unit(UnitTypeId.MARINE, tag=10, x=30.0, y=30.0)
        ctrl = MicroController()
        cmds = ctrl.generate_commands([s1, s2], [enemy])
        assert len(cmds) == 2
        assert all(c.action == "attack" for c in cmds)
