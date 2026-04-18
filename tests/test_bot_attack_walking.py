"""Regression tests for the attack-walking bug in Alpha4GateBot._run_micro.

Units in combat states (ATTACK / DEFEND / FORTIFY / LATE_GAME) must issue
attack-move when advancing toward a rally / staging / target point so they
engage enemies encountered along the way.  Plain .move() is reserved for
kiting (disengage / retreat) and non-combat scouting paths.

Previously the fix only covered ATTACK and LATE_GAME, leaving DEFEND and
FORTIFY units walking past enemies without engaging — reproducible in game
at low-ground ramps.  These tests lock in the ALL-combat-states behavior.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from bots.v0.bot import Alpha4GateBot
from bots.v0.decision_engine import StrategicState
from bots.v0.micro import MicroCommand
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2


# ---------------------------------------------------------------------------
# Minimal mock unit tracking attack / move calls separately
# ---------------------------------------------------------------------------
@dataclass
class _MockPosition:
    x: float = 0.0
    y: float = 0.0


class _MockUnit:
    """Stand-in for a burnysc2 Unit that records attack/move invocations."""

    __slots__ = (
        "tag",
        "type_id",
        "is_structure",
        "position",
        "attack_calls",
        "move_calls",
    )

    def __init__(
        self,
        tag: int,
        type_id: UnitTypeId = UnitTypeId.STALKER,
        *,
        is_structure: bool = False,
        position: tuple[float, float] = (0.0, 0.0),
    ) -> None:
        self.tag = tag
        self.type_id = type_id
        self.is_structure = is_structure
        self.position = _MockPosition(position[0], position[1])
        self.attack_calls: list[Any] = []
        self.move_calls: list[Any] = []

    def attack(self, target: Any) -> None:
        self.attack_calls.append(target)

    def move(self, target: Any) -> None:
        self.move_calls.append(target)

    def distance_to(self, _pos: Any) -> float:  # noqa: ANN401
        return 50.0  # far from staging by default


# ---------------------------------------------------------------------------
# Units collection with find_by_tag / filter helpers
# ---------------------------------------------------------------------------
class _UnitsCollection:
    """List-like that exposes .find_by_tag, matching burnysc2.Units."""

    def __init__(self, units: list[_MockUnit]) -> None:
        self._units = list(units)

    def __iter__(self) -> Any:
        return iter(self._units)

    def __len__(self) -> int:
        return len(self._units)

    def find_by_tag(self, tag: int) -> _MockUnit | None:
        for u in self._units:
            if u.tag == tag:
                return u
        return None


# ---------------------------------------------------------------------------
# Stub snapshot object: _run_micro only reads .army_supply
# ---------------------------------------------------------------------------
class _StubSnapshot:
    __slots__ = ("army_supply",)

    def __init__(self, army_supply: float = 30.0) -> None:
        self.army_supply = army_supply


# ---------------------------------------------------------------------------
# Stub bot — only wires what _run_micro actually touches
# ---------------------------------------------------------------------------
class _StubBot:
    """Strict stub that binds the real _run_micro method.

    Any unexpected attribute access raises AttributeError (via __slots__) so
    the test is explicit about which internals _run_micro depends on.
    """

    __slots__ = (
        "units",
        "enemy_units",
        "coherence_manager",
        "micro_controller",
        "_rally_point",
        "_enemy_main_pos",
        "_staging_pt",
        "_snapshot",
    )

    FINISHER_SUPPLY = Alpha4GateBot.FINISHER_SUPPLY

    def __init__(
        self,
        *,
        army: list[_MockUnit],
        enemies: list[_MockUnit],
        commands: list[MicroCommand],
        rally_point: tuple[float, float] = (50.0, 50.0),
        army_supply: float = 30.0,
        is_coherent: bool = True,
    ) -> None:
        self.units = _UnitsCollection(army + enemies)
        self.enemy_units = _UnitsCollection(enemies)

        # Coherence manager — _run_micro calls is_coherent(army)
        cm = MagicMock()
        cm.is_coherent.return_value = is_coherent
        self.coherence_manager = cm

        # Micro controller returns a pre-baked command list
        mc = MagicMock()
        mc.generate_commands.return_value = commands
        self.micro_controller = mc

        self._rally_point = rally_point
        self._enemy_main_pos: tuple[float, float] | None = (60.0, 60.0)
        self._staging_pt: tuple[float, float] | None = (40.0, 40.0)
        self._snapshot = _StubSnapshot(army_supply=army_supply)

    # --- methods that _run_micro calls on self --------------------------- #
    def _build_snapshot(self) -> _StubSnapshot:
        return self._snapshot

    def _resolve_attack_rally(
        self, _army: Any, _snap: Any, _cm: Any,
    ) -> tuple[float, float]:
        return self._rally_point

    def _defense_rally(self) -> tuple[float, float]:
        return self._rally_point

    def _get_staging_point(self) -> tuple[float, float] | None:
        return self._staging_pt

    def _enemy_main(self) -> tuple[float, float] | None:
        return self._enemy_main_pos

    # Bind the real production method
    _run_micro = Alpha4GateBot._run_micro  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _run(coro: Any) -> None:
    asyncio.run(coro)


def _move_cmd(unit_tag: int, pos: tuple[float, float] = (50.0, 50.0)) -> MicroCommand:
    return MicroCommand(unit_tag=unit_tag, action="move", target_position=pos)


def _kite_cmd(unit_tag: int, pos: tuple[float, float] = (30.0, 30.0)) -> MicroCommand:
    return MicroCommand(unit_tag=unit_tag, action="kite", target_position=pos)


def _attack_tag_cmd(unit_tag: int, target_tag: int) -> MicroCommand:
    return MicroCommand(unit_tag=unit_tag, action="attack", target_tag=target_tag)


# ===========================================================================
# move -> attack-move conversion, across every combat state
# ===========================================================================
class TestMoveConvertedToAttackInAllCombatStates:
    """The attack-walking regression: every combat state must attack-move on advance.

    Post-fix, all four states flow through the same unconditional
    ``unit.attack(Point2(...))`` branch in ``_run_micro``, so one parametrized
    test covers the full matrix. DEFEND is the regression target (previously
    broken); the other three lock in that the refactor didn't regress them.
    """

    @pytest.mark.parametrize(
        ("state", "unit_type"),
        [
            (StrategicState.DEFEND, UnitTypeId.STALKER),
            (StrategicState.FORTIFY, UnitTypeId.ZEALOT),
            (StrategicState.ATTACK, UnitTypeId.STALKER),
            (StrategicState.LATE_GAME, UnitTypeId.IMMORTAL),
        ],
    )
    def test_move_command_converts_to_attack(
        self, state: StrategicState, unit_type: UnitTypeId,
    ) -> None:
        unit = _MockUnit(tag=1, type_id=unit_type)
        bot = _StubBot(
            army=[unit],
            enemies=[],
            commands=[_move_cmd(unit.tag, (50.0, 50.0))],
            army_supply=30.0,  # below FINISHER_SUPPLY
            is_coherent=True,  # skip staging gate
        )
        _run(bot._run_micro(state))

        assert len(unit.attack_calls) == 1, (
            f"{state.name}: unit must issue attack-move (attack-walking fix)."
        )
        assert isinstance(unit.attack_calls[0], Point2)
        assert unit.attack_calls[0].x == 50.0
        assert unit.attack_calls[0].y == 50.0
        assert unit.move_calls == [], (
            f"{state.name}: unit must NOT issue plain move — attack-walking bug."
        )


# ===========================================================================
# Kiting must still use plain .move() — do NOT break the existing micro kite
# ===========================================================================
class TestKitePreservesPlainMove:
    def test_kite_issues_plain_move_in_defend(self) -> None:
        """Kiting disengage must stay plain move; attack-move would re-engage."""
        unit = _MockUnit(tag=10, type_id=UnitTypeId.STALKER)
        enemy = _MockUnit(tag=999, type_id=UnitTypeId.ZERGLING)
        bot = _StubBot(
            army=[unit],
            enemies=[enemy],
            commands=[_kite_cmd(unit.tag, (30.0, 30.0))],
        )
        _run(bot._run_micro(StrategicState.DEFEND))

        assert len(unit.move_calls) == 1, "Kite must stay plain move."
        assert isinstance(unit.move_calls[0], Point2)
        assert unit.attack_calls == [], "Kite must NOT be converted to attack-move."

    def test_kite_issues_plain_move_in_attack(self) -> None:
        """Same rule holds under ATTACK — kite != advance."""
        unit = _MockUnit(tag=11, type_id=UnitTypeId.STALKER)
        enemy = _MockUnit(tag=888, type_id=UnitTypeId.ZERGLING)
        bot = _StubBot(
            army=[unit],
            enemies=[enemy],
            commands=[_kite_cmd(unit.tag)],
            army_supply=30.0,
            is_coherent=True,
        )
        _run(bot._run_micro(StrategicState.ATTACK))

        assert len(unit.move_calls) == 1, "Kite must stay plain move."
        assert isinstance(unit.move_calls[0], Point2)
        assert unit.attack_calls == [], "Kite must NOT be converted to attack-move."


# ===========================================================================
# attack commands remain .attack() on the target unit
# ===========================================================================
class TestAttackCommandsUnchanged:
    def test_attack_with_target_tag_hits_enemy_unit(self) -> None:
        own = _MockUnit(tag=20, type_id=UnitTypeId.STALKER)
        enemy = _MockUnit(tag=500, type_id=UnitTypeId.ZERGLING)
        bot = _StubBot(
            army=[own],
            enemies=[enemy],
            commands=[_attack_tag_cmd(own.tag, enemy.tag)],
        )
        _run(bot._run_micro(StrategicState.DEFEND))

        assert len(own.attack_calls) == 1
        # Attack on a tagged unit passes the enemy unit object itself
        assert own.attack_calls[0] is enemy
        assert own.move_calls == []


# ===========================================================================
# Multiple units in one pass — all advancing units attack-move, kiters don't
# ===========================================================================
class TestMixedCommandsInSinglePass:
    def test_advancers_attack_kiters_move_under_defend(self) -> None:
        """Realistic mix: stalker kites, zealot advances."""
        kiter = _MockUnit(tag=100, type_id=UnitTypeId.STALKER)
        advancer = _MockUnit(tag=101, type_id=UnitTypeId.ZEALOT)
        enemy = _MockUnit(tag=300, type_id=UnitTypeId.MARINE)
        bot = _StubBot(
            army=[kiter, advancer],
            enemies=[enemy],
            commands=[
                _kite_cmd(kiter.tag, (20.0, 20.0)),
                _move_cmd(advancer.tag, (55.0, 55.0)),
            ],
        )
        _run(bot._run_micro(StrategicState.DEFEND))

        # Kiter: plain move, no attack
        assert kiter.move_calls and not kiter.attack_calls
        # Advancer: attack-move, no plain move
        assert advancer.attack_calls and not advancer.move_calls
