"""Tests for _build_snapshot() own-army and enemy threat-class population."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bots.v0.bot import _OWN_UNIT_MAP, Alpha4GateBot
from sc2.ids.unit_typeid import UnitTypeId


# ---------------------------------------------------------------------------
# Lightweight mock unit
# ---------------------------------------------------------------------------
@dataclass
class _MockUnit:
    """Minimal stand-in for a burnysc2 Unit object."""

    type_id: UnitTypeId
    is_structure: bool = False

    def distance_to(self, _pos: Any) -> float:  # noqa: ANN401
        return 100.0  # far from base by default


# ---------------------------------------------------------------------------
# Stub state object for self.state.upgrades
# ---------------------------------------------------------------------------
class _StubState:
    __slots__ = ("upgrades",)

    def __init__(self) -> None:
        self.upgrades: list[Any] = []


# ---------------------------------------------------------------------------
# Strict stub bot — raises AttributeError for unexpected attributes
# ---------------------------------------------------------------------------
class _StubBot:
    """Only defines the attributes _build_snapshot() actually uses.

    Any unexpected attribute access raises AttributeError, unlike MagicMock
    which silently returns new MagicMock objects.
    """

    __slots__ = (
        "units",
        "enemy_units",
        "supply_used",
        "supply_cap",
        "minerals",
        "vespene",
        "townhalls",
        "time",
        "enemy_structures",
        "start_location",
        "state",
        "_structures_map",
    )

    def __init__(self) -> None:
        self.units: list[_MockUnit] = []
        self.enemy_units: list[_MockUnit] = []
        self.supply_used: int = 50
        self.supply_cap: int = 100
        self.minerals: int = 400
        self.vespene: int = 200
        self.townhalls: list[Any] = []
        self.time: float = 120.0
        self.enemy_structures: list[Any] = []
        self.start_location: object = object()
        self.state: _StubState = _StubState()
        self._structures_map: dict[UnitTypeId, list[Any]] = {}

    def structures(self, uid: UnitTypeId) -> list[Any]:
        return self._structures_map.get(uid, [])

    # Bind the real _build_snapshot method
    _build_snapshot = Alpha4GateBot._build_snapshot  # type: ignore[assignment]


def _make_bot(
    own_units: list[_MockUnit] | None = None,
    enemy_units: list[_MockUnit] | None = None,
) -> _StubBot:
    """Create a strict stub bot wired to call the real _build_snapshot."""
    bot = _StubBot()
    if own_units is not None:
        bot.units = own_units
    if enemy_units is not None:
        bot.enemy_units = enemy_units
    return bot


# ---------------------------------------------------------------------------
# Own-army counting tests
# ---------------------------------------------------------------------------
class TestOwnArmyCounts:
    def test_zealots_and_stalkers(self) -> None:
        units = [
            _MockUnit(UnitTypeId.ZEALOT),
            _MockUnit(UnitTypeId.ZEALOT),
            _MockUnit(UnitTypeId.ZEALOT),
            _MockUnit(UnitTypeId.STALKER),
            _MockUnit(UnitTypeId.STALKER),
        ]
        bot = _make_bot(own_units=units)
        snap = bot._build_snapshot()

        assert snap.zealot_count == 3
        assert snap.stalker_count == 2

    def test_all_own_unit_types(self) -> None:
        """Every mapped own-unit type increments the right field."""
        for uid, field_name in _OWN_UNIT_MAP.items():
            units = [_MockUnit(uid), _MockUnit(uid)]
            bot = _make_bot(own_units=units)
            snap = bot._build_snapshot()
            assert getattr(snap, field_name) == 2, (
                f"{field_name} should be 2 for two {uid.name}"
            )

    def test_probes_not_in_own_counts(self) -> None:
        """Probes are workers, not combat -- they shouldn't appear in any count."""
        units = [_MockUnit(UnitTypeId.PROBE)] * 5
        bot = _make_bot(own_units=units)
        snap = bot._build_snapshot()

        for field_name in set(_OWN_UNIT_MAP.values()):
            assert getattr(snap, field_name) == 0

        assert snap.worker_count == 5

    def test_unknown_own_unit_no_crash(self) -> None:
        """Units not in the map don't crash and don't populate any count."""
        units = [_MockUnit(UnitTypeId.MOTHERSHIP)]  # not in _OWN_UNIT_MAP
        bot = _make_bot(own_units=units)
        snap = bot._build_snapshot()

        for field_name in set(_OWN_UNIT_MAP.values()):
            assert getattr(snap, field_name) == 0

    def test_empty_own_units(self) -> None:
        bot = _make_bot(own_units=[])
        snap = bot._build_snapshot()

        for field_name in set(_OWN_UNIT_MAP.values()):
            assert getattr(snap, field_name) == 0

    def test_warp_prism_phasing_counted(self) -> None:
        """A deployed (phasing) Warp Prism should still count."""
        units = [
            _MockUnit(UnitTypeId.WARPPRISM),
            _MockUnit(UnitTypeId.WARPPRISMPHASING),
        ]
        bot = _make_bot(own_units=units)
        snap = bot._build_snapshot()

        assert snap.warp_prism_count == 2


# ---------------------------------------------------------------------------
# Enemy threat-class counting tests
# ---------------------------------------------------------------------------
class TestEnemyThreatCounts:
    def test_marines_and_roaches(self) -> None:
        enemies = [
            _MockUnit(UnitTypeId.MARINE),
            _MockUnit(UnitTypeId.MARINE),
            _MockUnit(UnitTypeId.MARINE),
            _MockUnit(UnitTypeId.ROACH),
            _MockUnit(UnitTypeId.ROACH),
        ]
        bot = _make_bot(enemy_units=enemies)
        snap = bot._build_snapshot()

        assert snap.enemy_light_count == 3
        assert snap.enemy_armored_count == 2

    def test_all_threat_classes_populated(self) -> None:
        """At least one unit from each threat class maps correctly."""
        # Pick one representative per class
        reps: dict[str, UnitTypeId] = {
            "enemy_light_count": UnitTypeId.MARINE,
            "enemy_armored_count": UnitTypeId.ROACH,
            "enemy_siege_count": UnitTypeId.SIEGETANK,
            "enemy_support_count": UnitTypeId.MEDIVAC,
            "enemy_air_harass_count": UnitTypeId.MUTALISK,
            "enemy_heavy_count": UnitTypeId.ULTRALISK,
            "enemy_capital_count": UnitTypeId.BATTLECRUISER,
            "enemy_cloak_count": UnitTypeId.GHOST,
        }
        enemies = [_MockUnit(uid) for uid in reps.values()]
        bot = _make_bot(enemy_units=enemies)
        snap = bot._build_snapshot()

        for field_name in reps:
            assert getattr(snap, field_name) == 1, (
                f"{field_name} should be 1"
            )

    def test_unknown_enemy_unit_no_crash(self) -> None:
        """Enemy units not in THREAT_CLASS_MAP don't crash, counts stay 0."""
        # SCV is in _SUPPLY_COST but not in THREAT_CLASS_MAP
        enemies = [_MockUnit(UnitTypeId.SCV)]
        bot = _make_bot(enemy_units=enemies)
        snap = bot._build_snapshot()

        threat_fields = [
            "enemy_light_count",
            "enemy_armored_count",
            "enemy_siege_count",
            "enemy_support_count",
            "enemy_air_harass_count",
            "enemy_heavy_count",
            "enemy_capital_count",
            "enemy_cloak_count",
        ]
        for f in threat_fields:
            assert getattr(snap, f) == 0

    def test_empty_enemy_units(self) -> None:
        bot = _make_bot(enemy_units=[])
        snap = bot._build_snapshot()

        threat_fields = [
            "enemy_light_count",
            "enemy_armored_count",
            "enemy_siege_count",
            "enemy_support_count",
            "enemy_air_harass_count",
            "enemy_heavy_count",
            "enemy_capital_count",
            "enemy_cloak_count",
        ]
        for f in threat_fields:
            assert getattr(snap, f) == 0


# ---------------------------------------------------------------------------
# Combined own + enemy
# ---------------------------------------------------------------------------
class TestCombinedSnapshot:
    def test_own_and_enemy_together(self) -> None:
        own = [
            _MockUnit(UnitTypeId.IMMORTAL),
            _MockUnit(UnitTypeId.IMMORTAL),
            _MockUnit(UnitTypeId.OBSERVER),
        ]
        enemies = [
            _MockUnit(UnitTypeId.ZERGLING),
            _MockUnit(UnitTypeId.ZERGLING),
            _MockUnit(UnitTypeId.HYDRALISK),
            _MockUnit(UnitTypeId.BROODLORD),
        ]
        bot = _make_bot(own_units=own, enemy_units=enemies)
        snap = bot._build_snapshot()

        assert snap.immortal_count == 2
        assert snap.observer_count == 1
        assert snap.enemy_light_count == 3  # 2 lings + 1 hydra
        assert snap.enemy_capital_count == 1  # broodlord
