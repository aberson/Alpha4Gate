"""Unit tests for macro manager: worker count, expansion triggers, supply math."""

from __future__ import annotations

from unittest.mock import MagicMock

from bots.v0.decision_engine import StrategicState
from bots.v0.macro_manager import (
    MAX_WORKERS,
    WORKERS_PER_BASE_MINERALS,
    WORKERS_PER_GAS,
    MacroDecision,
    MacroManager,
)
from sc2.ids.unit_typeid import UnitTypeId


def _mock_bot(
    supply_used: int = 30,
    supply_cap: int = 38,
    minerals: int = 300,
    vespene: int = 100,
    worker_count: int = 16,
    base_count: int = 1,
    gateway_count: int = 2,
    warpgate_count: int = 0,
    robo_count: int = 0,
    gas_building_count: int = 1,
    idle_nexus_count: int = 1,
    pending_pylon: int = 0,
    pending_probe: int = 0,
    pending_nexus: int = 0,
    pending_gateway: int = 0,
    pending_robo: int = 0,
    pending_gas: int = 0,
) -> MagicMock:
    """Create a mock BotAI for macro manager testing."""
    bot = MagicMock()
    bot.supply_used = float(supply_used)
    bot.supply_cap = float(supply_cap)
    bot.minerals = minerals
    bot.vespene = vespene

    # Units
    probes = [MagicMock() for _ in range(worker_count)]
    bot.units.return_value = probes
    bot.units.side_effect = lambda uid: probes if uid == UnitTypeId.PROBE else []

    # Townhalls
    townhalls = [MagicMock() for _ in range(base_count)]
    bot.townhalls = MagicMock()
    bot.townhalls.__len__ = lambda self: base_count
    bot.townhalls.__iter__ = lambda self: iter(townhalls)
    idle_nexuses = townhalls[:idle_nexus_count]
    bot.townhalls.idle = MagicMock()
    bot.townhalls.idle.__iter__ = lambda self: iter(idle_nexuses)
    bot.townhalls.ready = MagicMock()
    bot.townhalls.ready.__iter__ = lambda self: iter(townhalls)

    # Structures
    gateways = [MagicMock() for _ in range(gateway_count)]
    warpgates = [MagicMock() for _ in range(warpgate_count)]
    robos = [MagicMock() for _ in range(robo_count)]

    def structures_side_effect(uid: UnitTypeId) -> list[MagicMock]:
        if uid == UnitTypeId.GATEWAY:
            return gateways
        if uid == UnitTypeId.WARPGATE:
            return warpgates
        if uid == UnitTypeId.ROBOTICSFACILITY:
            return robos
        return []

    bot.structures = MagicMock(side_effect=structures_side_effect)

    # Gas buildings
    bot.gas_buildings = MagicMock()
    bot.gas_buildings.__len__ = lambda self: gas_building_count
    bot.gas_buildings.closer_than = MagicMock(return_value=[])

    # Vespene geysers (empty by default)
    bot.vespene_geyser = MagicMock()
    bot.vespene_geyser.closer_than = MagicMock(return_value=[])

    # Already pending
    def already_pending_side_effect(uid: UnitTypeId) -> int:
        mapping = {
            UnitTypeId.PYLON: pending_pylon,
            UnitTypeId.PROBE: pending_probe,
            UnitTypeId.NEXUS: pending_nexus,
            UnitTypeId.GATEWAY: pending_gateway,
            UnitTypeId.ROBOTICSFACILITY: pending_robo,
            UnitTypeId.ASSIMILATOR: pending_gas,
        }
        return mapping.get(uid, 0)

    bot.already_pending = MagicMock(side_effect=already_pending_side_effect)

    return bot


class TestMacroDecision:
    def test_to_dict(self) -> None:
        d = MacroDecision(action="build", target="Pylon", reason="Supply tight").to_dict()
        assert d["action"] == "build"
        assert d["target"] == "Pylon"
        assert d["reason"] == "Supply tight"


class TestSupplyManagement:
    def test_builds_pylon_when_supply_tight(self) -> None:
        bot = _mock_bot(supply_used=34, supply_cap=38)
        mgr = MacroManager()
        decisions = mgr.evaluate(bot, StrategicState.EXPAND)
        pylon_decisions = [d for d in decisions if d.target == "Pylon"]
        assert len(pylon_decisions) >= 1

    def test_no_pylon_when_supply_comfortable(self) -> None:
        bot = _mock_bot(supply_used=20, supply_cap=38)
        mgr = MacroManager()
        decisions = mgr.evaluate(bot, StrategicState.EXPAND)
        pylon_decisions = [d for d in decisions if d.target == "Pylon"]
        assert len(pylon_decisions) == 0

    def test_no_pylon_when_already_pending(self) -> None:
        bot = _mock_bot(supply_used=34, supply_cap=38, pending_pylon=1)
        mgr = MacroManager()
        decisions = mgr.evaluate(bot, StrategicState.EXPAND)
        pylon_decisions = [d for d in decisions if d.target == "Pylon"]
        assert len(pylon_decisions) == 0

    def test_no_pylon_at_200_cap(self) -> None:
        bot = _mock_bot(supply_used=196, supply_cap=200)
        mgr = MacroManager()
        decisions = mgr.evaluate(bot, StrategicState.EXPAND)
        pylon_decisions = [d for d in decisions if d.target == "Pylon"]
        assert len(pylon_decisions) == 0


class TestWorkerProduction:
    def test_trains_probe_when_below_saturation(self) -> None:
        bot = _mock_bot(worker_count=10, base_count=1)
        mgr = MacroManager()
        decisions = mgr.evaluate(bot, StrategicState.EXPAND)
        probe_decisions = [d for d in decisions if d.target == "Probe"]
        assert len(probe_decisions) >= 1

    def test_no_probe_when_saturated(self) -> None:
        bot = _mock_bot(worker_count=19, base_count=1, gas_building_count=1)
        mgr = MacroManager()
        decisions = mgr.evaluate(bot, StrategicState.EXPAND)
        probe_decisions = [d for d in decisions if d.target == "Probe"]
        assert len(probe_decisions) == 0


class TestExpansion:
    def test_expands_when_saturated_and_rich(self) -> None:
        bot = _mock_bot(worker_count=15, base_count=1, minerals=450)
        mgr = MacroManager()
        decisions = mgr.evaluate(bot, StrategicState.EXPAND)
        expand_decisions = [d for d in decisions if d.target == "Nexus"]
        assert len(expand_decisions) == 1

    def test_no_expand_when_defending(self) -> None:
        bot = _mock_bot(worker_count=15, base_count=1, minerals=450)
        mgr = MacroManager()
        decisions = mgr.evaluate(bot, StrategicState.DEFEND)
        expand_decisions = [d for d in decisions if d.target == "Nexus"]
        assert len(expand_decisions) == 0

    def test_no_expand_when_poor(self) -> None:
        bot = _mock_bot(worker_count=15, base_count=1, minerals=200)
        mgr = MacroManager()
        decisions = mgr.evaluate(bot, StrategicState.EXPAND)
        expand_decisions = [d for d in decisions if d.target == "Nexus"]
        assert len(expand_decisions) == 0


class TestProductionBuildings:
    def test_builds_gateway_when_below_target(self) -> None:
        # 1 base * 2 gateways/base = 2, currently have 1
        bot = _mock_bot(gateway_count=1, base_count=1)
        mgr = MacroManager()
        decisions = mgr.evaluate(bot, StrategicState.EXPAND)
        gw_decisions = [d for d in decisions if d.target == "Gateway"]
        assert len(gw_decisions) >= 1

    def test_no_gateway_when_at_target(self) -> None:
        bot = _mock_bot(gateway_count=2, base_count=1)
        mgr = MacroManager()
        decisions = mgr.evaluate(bot, StrategicState.EXPAND)
        gw_decisions = [d for d in decisions if d.target == "Gateway"]
        assert len(gw_decisions) == 0

    def test_builds_robo_after_2_bases(self) -> None:
        bot = _mock_bot(base_count=2, gateway_count=4, robo_count=0)
        mgr = MacroManager()
        decisions = mgr.evaluate(bot, StrategicState.EXPAND)
        robo_decisions = [d for d in decisions if d.target == "RoboticsFacility"]
        assert len(robo_decisions) == 1

    def test_no_robo_before_2_bases(self) -> None:
        bot = _mock_bot(base_count=1, robo_count=0)
        mgr = MacroManager()
        decisions = mgr.evaluate(bot, StrategicState.EXPAND)
        robo_decisions = [d for d in decisions if d.target == "RoboticsFacility"]
        assert len(robo_decisions) == 0


class TestOpeningSkip:
    def test_no_decisions_during_opening(self) -> None:
        bot = _mock_bot()
        mgr = MacroManager()
        decisions = mgr.evaluate(bot, StrategicState.OPENING)
        assert len(decisions) == 0


class TestIdealWorkerCount:
    def test_one_base_one_gas(self) -> None:
        bot = _mock_bot(base_count=1, gas_building_count=1)
        ideal = MacroManager._ideal_worker_count(bot)
        assert ideal == WORKERS_PER_BASE_MINERALS + WORKERS_PER_GAS

    def test_two_bases_two_gas(self) -> None:
        bot = _mock_bot(base_count=2, gas_building_count=2)
        ideal = MacroManager._ideal_worker_count(bot)
        assert ideal == 2 * WORKERS_PER_BASE_MINERALS + 2 * WORKERS_PER_GAS

    def test_capped_at_max(self) -> None:
        bot = _mock_bot(base_count=5, gas_building_count=10)
        ideal = MacroManager._ideal_worker_count(bot)
        assert ideal == MAX_WORKERS
