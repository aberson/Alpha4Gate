"""Tests for FORTIFY state integration in Alpha4GateBot.

Tests fortification manager wiring, backlog draining, micro routing,
and notify_retreat wiring without requiring a live SC2 connection.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from sc2.ids.unit_typeid import UnitTypeId

from alpha4gate.army_coherence import ArmyCoherenceManager
from alpha4gate.bot import Alpha4GateBot
from alpha4gate.build_backlog import BuildBacklog
from alpha4gate.decision_engine import DecisionEngine, GameSnapshot
from alpha4gate.fortification import FortificationManager


def _mock_unit(x: float = 0.0, y: float = 0.0) -> MagicMock:
    u = MagicMock()
    u.position = MagicMock()
    u.position.x = x
    u.position.y = y
    u.type_id = UnitTypeId.STALKER
    u.is_structure = False
    u.is_idle = True
    u.tag = id(u)
    return u


def _make_bot(seed: int = 42) -> MagicMock:
    """Create a mock bot with real methods for fortification testing."""
    bot = MagicMock(spec=Alpha4GateBot)

    # Restore real methods
    bot._evaluate_fortification = Alpha4GateBot._evaluate_fortification.__get__(bot)
    bot._drain_backlog = Alpha4GateBot._drain_backlog.__get__(bot)
    bot._resolve_attack_rally = Alpha4GateBot._resolve_attack_rally.__get__(bot)
    bot._get_staging_point = Alpha4GateBot._get_staging_point.__get__(bot)
    bot._defense_rally = Alpha4GateBot._defense_rally.__get__(bot)
    bot._attack_target = Alpha4GateBot._attack_target.__get__(bot)
    bot._enemy_main = Alpha4GateBot._enemy_main.__get__(bot)
    bot._enemy_natural = Alpha4GateBot._enemy_natural.__get__(bot)
    bot._STAGING_RECALC_SECONDS = Alpha4GateBot._STAGING_RECALC_SECONDS
    bot.PUSH_MAIN_SUPPLY = Alpha4GateBot.PUSH_MAIN_SUPPLY

    # Coherence manager
    cm = ArmyCoherenceManager(seed=seed)
    bot.coherence_manager = cm

    # Decision engine
    bot.decision_engine = DecisionEngine(
        fortify_trigger_ratio=cm.fortify_trigger_ratio,
        attack_supply_ratio=cm.attack_supply_ratio,
    )

    # Fortification manager
    bot._fortification_manager = FortificationManager(
        defense_scaling_divisor=cm.defense_scaling_divisor,
        max_defenses=cm.max_defenses,
    )
    bot._build_backlog = BuildBacklog()
    bot._actions_this_step: list[dict[str, object]] = []

    # Mock BotAI attributes
    start = MagicMock()
    start.x = 10.0
    start.y = 10.0
    start.towards = MagicMock(return_value=MagicMock(x=12.0, y=10.0))
    start.distance_to = MagicMock(return_value=5.0)
    bot.start_location = start

    ramp = MagicMock()
    ramp.bottom_center = MagicMock()
    ramp.bottom_center.x = 15.0
    ramp.bottom_center.y = 10.0
    bot.main_base_ramp = ramp
    bot.townhalls = [MagicMock()]

    enemy_start = MagicMock()
    enemy_start.x = 90.0
    enemy_start.y = 90.0
    enemy_start.distance_to = MagicMock(return_value=0.0)
    bot.enemy_start_locations = [enemy_start]
    bot.enemy_structures = []
    bot.scout_manager = MagicMock()
    bot.scout_manager.enemy_base_locations = []

    nat_loc = MagicMock()
    nat_loc.x = 80.0
    nat_loc.y = 80.0
    nat_loc.distance_to = MagicMock(
        side_effect=lambda p: ((80.0 - p.x) ** 2 + (80.0 - p.y) ** 2) ** 0.5
    )
    bot.expansion_locations_list = [enemy_start, nat_loc]

    bot.time = 100.0
    bot.supply_used = 50
    bot._cached_staging_point = None
    bot._staging_point_time = -999.0
    bot._cached_enemy_natural = None

    # Mock structures() — return empty by default
    def _structures(uid: UnitTypeId) -> MagicMock:
        result = MagicMock()
        result.ready = MagicMock()
        result.ready.__len__ = MagicMock(return_value=0)
        result.ready.__iter__ = MagicMock(return_value=iter([]))
        result.not_ready = MagicMock()
        result.not_ready.__len__ = MagicMock(return_value=0)
        result.__len__ = MagicMock(return_value=0)
        return result

    bot.structures = _structures

    return bot


class TestFortificationManagerWiring:
    """Test that the bot correctly instantiates and calls FortificationManager."""

    def test_evaluate_fortification_returns_decisions(self) -> None:
        bot = _make_bot()

        # Set up structures: have cybernetics core, have forge
        def _structures(uid: UnitTypeId) -> MagicMock:
            result = MagicMock()
            if uid == UnitTypeId.CYBERNETICSCORE:
                result.ready = MagicMock()
                result.ready.__len__ = MagicMock(return_value=1)
            elif uid == UnitTypeId.FORGE:
                result.ready = MagicMock()
                result.ready.__len__ = MagicMock(return_value=1)
                result.not_ready = MagicMock()
                result.not_ready.__len__ = MagicMock(return_value=0)
            elif uid == UnitTypeId.PYLON:
                pylon = MagicMock()
                pylon.distance_to = MagicMock(return_value=5.0)
                result.ready = MagicMock()
                result.ready.__iter__ = MagicMock(return_value=iter([pylon]))
            else:
                result.ready = MagicMock()
                result.ready.__len__ = MagicMock(return_value=0)
                result.not_ready = MagicMock()
                result.not_ready.__len__ = MagicMock(return_value=0)
            result.__len__ = MagicMock(return_value=0)
            return result

        bot.structures = _structures
        snapshot = GameSnapshot(
            army_supply=20,
            enemy_army_supply_visible=60,
        )
        decisions = bot._evaluate_fortification(snapshot)
        targets = [d.target for d in decisions]
        assert "ShieldBattery" in targets or "PhotonCannon" in targets


class TestBacklogDraining:
    """Test that backlog is drained after macro execution."""

    def test_drain_backlog_retries_affordable(self) -> None:
        bot = _make_bot()
        bot._build_backlog.add("Pylon", (10.0, 10.0), "test", game_time=0.0)

        # Mock can_afford to return True for Pylon
        bot.can_afford = MagicMock(return_value=True)
        bot._build_structure = AsyncMock(return_value=True)

        snapshot = GameSnapshot(game_time_seconds=10.0)
        asyncio.run(bot._drain_backlog(snapshot))

        # Entry should be popped from backlog
        assert len(bot._build_backlog) == 0
        # Verify the build was actually issued
        bot._build_structure.assert_called_once_with(UnitTypeId.PYLON)

    def test_drain_backlog_skips_unaffordable(self) -> None:
        bot = _make_bot()
        bot._build_backlog.add("Pylon", (10.0, 10.0), "test", game_time=0.0)
        bot.can_afford = MagicMock(return_value=False)

        snapshot = GameSnapshot(game_time_seconds=10.0)
        asyncio.run(bot._drain_backlog(snapshot))

        # Entry should remain
        assert len(bot._build_backlog) == 1


class TestNotifyRetreatWiring:
    """Test that should_retreat triggers notify_retreat on decision engine."""

    def test_retreat_calls_notify_retreat(self) -> None:
        bot = _make_bot()
        bot.coherence_manager.retreat_supply_ratio = 0.5
        bot.coherence_manager.retreat_to_staging = False

        army = [_mock_unit(50, 50)]
        snap = GameSnapshot(army_supply=5, enemy_army_supply_visible=30)

        # Before the call, recently_retreated should be False
        assert bot.decision_engine._recently_retreated is False

        bot._resolve_attack_rally(army, snap, bot.coherence_manager)

        # notify_retreat should have been called, setting _recently_retreated
        assert bot.decision_engine._recently_retreated is True


