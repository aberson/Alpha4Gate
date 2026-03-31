"""Unit tests for CommandExecutor and related integration."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from alpha4gate.build_orders import BuildOrder, BuildStep
from alpha4gate.commands.executor import CommandExecutor, ExecutionResult
from alpha4gate.commands.primitives import CommandAction, CommandPrimitive, CommandSource
from alpha4gate.decision_engine import DecisionEngine, GameSnapshot, StrategicState
from alpha4gate.scouting import ScoutManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_bot() -> MagicMock:
    """Create a minimal mock bot for executor tests."""
    bot = MagicMock()
    bot.start_location = Point2((50.0, 50.0))
    bot.enemy_start_locations = [Point2((150.0, 150.0))]
    bot._enemy_natural.return_value = (140.0, 130.0)
    bot._cached_staging_point = None

    # expansion_locations_list: main(50,50), natural(70,70), third(90,90), fourth(110,110)
    bot.expansion_locations_list = [
        Point2((50.0, 50.0)),  # main -- excluded by distance filter
        Point2((70.0, 70.0)),  # natural (closest)
        Point2((90.0, 90.0)),  # third
        Point2((110.0, 110.0)),  # fourth
    ]

    bot.can_afford.return_value = True
    bot.build = AsyncMock()
    bot.expand_now = AsyncMock()
    bot.research = AsyncMock()

    # Structures mock
    idle_gw = MagicMock()
    idle_gw.train = MagicMock()
    structures_result = MagicMock()
    structures_result.idle = [idle_gw]
    structures_result.ready = []
    structures_result.closest_to = MagicMock(
        return_value=MagicMock(position=Point2((55, 55)))
    )
    bot.structures.return_value = structures_result

    # Decision engine
    bot.decision_engine = DecisionEngine()
    # Scout manager
    bot.scout_manager = ScoutManager()

    return bot


def _cmd(
    action: CommandAction,
    target: str = "",
    location: str | None = None,
    source: CommandSource = CommandSource.HUMAN,
) -> CommandPrimitive:
    return CommandPrimitive(
        action=action,
        target=target,
        location=location,
        source=source,
        timestamp=10.0,
    )


def _run(coro: object) -> object:
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)  # type: ignore[arg-type]
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# resolve_location tests
# ---------------------------------------------------------------------------


class TestResolveLocation:
    def test_main(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        result = ex.resolve_location("main", CommandAction.BUILD)
        assert result == bot.start_location

    def test_natural(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        result = ex.resolve_location("natural", CommandAction.BUILD)
        assert result is not None
        assert result == Point2((70.0, 70.0))

    def test_third(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        result = ex.resolve_location("third", CommandAction.BUILD)
        assert result is not None
        assert result == Point2((90.0, 90.0))

    def test_fourth(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        result = ex.resolve_location("fourth", CommandAction.BUILD)
        assert result is not None
        assert result == Point2((110.0, 110.0))

    def test_enemy_main(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        result = ex.resolve_location("enemy_main", CommandAction.ATTACK)
        assert result == bot.enemy_start_locations[0]

    def test_enemy_natural(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        result = ex.resolve_location("enemy_natural", CommandAction.ATTACK)
        assert result is not None
        assert result == Point2((140.0, 130.0))

    def test_none_build_defaults_to_main(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        result = ex.resolve_location(None, CommandAction.BUILD)
        assert result == bot.start_location

    def test_none_attack_defaults_to_enemy_natural(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        result = ex.resolve_location(None, CommandAction.ATTACK)
        assert result is not None
        assert result == Point2((140.0, 130.0))

    def test_none_scout_defaults_to_none(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        result = ex.resolve_location(None, CommandAction.SCOUT)
        assert result is None

    def test_enemy_natural_none(self) -> None:
        bot = _mock_bot()
        bot._enemy_natural.return_value = None
        ex = CommandExecutor(bot)
        result = ex.resolve_location("enemy_natural", CommandAction.ATTACK)
        assert result is None


# ---------------------------------------------------------------------------
# BUILD unit tests
# ---------------------------------------------------------------------------


class TestExecuteBuildUnit:
    def test_train_stalker_from_idle_gateway(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.BUILD, "stalker")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is True
        assert result.primitives_executed == 1
        idle_gw = bot.structures.return_value.idle[0]
        idle_gw.train.assert_called_once_with(UnitTypeId.STALKER)

    def test_cannot_afford_unit(self) -> None:
        bot = _mock_bot()
        bot.can_afford.return_value = False
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.BUILD, "zealot")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is False

    def test_no_idle_production(self) -> None:
        bot = _mock_bot()
        bot.structures.return_value.idle = []
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.BUILD, "stalker")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is False

    def test_unknown_target(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.BUILD, "unknown_thing")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is False


# ---------------------------------------------------------------------------
# BUILD structure tests
# ---------------------------------------------------------------------------


class TestExecuteBuildStructure:
    def test_build_gateway(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.BUILD, "gateway", location="main")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is True
        bot.build.assert_called_once()

    def test_build_nexus_uses_expand(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.BUILD, "nexus")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is True


# ---------------------------------------------------------------------------
# DEFEND / ATTACK tests
# ---------------------------------------------------------------------------


class TestExecuteDefendAttack:
    def test_defend_sets_override(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.DEFEND, "main")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is True
        assert bot.decision_engine._override_state == StrategicState.DEFEND

    def test_attack_sets_override(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.ATTACK, "enemy_natural", location="enemy_natural")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is True
        assert bot.decision_engine._override_state == StrategicState.ATTACK

    def test_attack_updates_staging_point(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.ATTACK, "go", location="enemy_main")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is True
        assert bot._cached_staging_point == (150.0, 150.0)


# ---------------------------------------------------------------------------
# RALLY tests
# ---------------------------------------------------------------------------


class TestExecuteRally:
    def test_rally_updates_staging_point(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.RALLY, "natural")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is True
        assert bot._cached_staging_point == (70.0, 70.0)

    def test_rally_with_location(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.RALLY, "", location="third")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is True
        assert bot._cached_staging_point == (90.0, 90.0)


# ---------------------------------------------------------------------------
# SCOUT tests
# ---------------------------------------------------------------------------


class TestExecuteScout:
    def test_scout_sets_forced_target(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.SCOUT, "enemy_base", location="enemy_main")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is True
        assert bot.scout_manager.forced_target == (150.0, 150.0)

    def test_scout_default_target(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.SCOUT, "go")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is True
        assert bot.scout_manager.forced_target == (150.0, 150.0)


# ---------------------------------------------------------------------------
# UPGRADE tests
# ---------------------------------------------------------------------------


class TestExecuteUpgrade:
    def test_upgrade_blink(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.UPGRADE, "blink")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is True
        bot.research.assert_called_once()

    def test_unknown_upgrade(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.UPGRADE, "unknown_upgrade")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is False


# ---------------------------------------------------------------------------
# EXPAND tests
# ---------------------------------------------------------------------------


class TestExecuteExpand:
    def test_expand_with_location(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.EXPAND, "third", location="third")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is True
        bot.build.assert_called_once()

    def test_expand_no_location(self) -> None:
        bot = _mock_bot()
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.EXPAND, "")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is True
        bot.expand_now.assert_called_once()

    def test_expand_cannot_afford(self) -> None:
        bot = _mock_bot()
        bot.can_afford.return_value = False
        ex = CommandExecutor(bot)
        cmd = _cmd(CommandAction.EXPAND, "")
        result: ExecutionResult = _run(ex.execute(cmd))  # type: ignore[assignment]
        assert result.success is False


# ---------------------------------------------------------------------------
# DecisionEngine override tests
# ---------------------------------------------------------------------------


def _simple_order() -> BuildOrder:
    return BuildOrder(
        id="test",
        name="Test",
        steps=[BuildStep(supply=10, action="build", target="Pylon")],
    )


class TestCommandOverride:
    def test_override_forces_state(self) -> None:
        engine = DecisionEngine(build_order=_simple_order())
        engine._sequencer.advance()
        engine.evaluate(GameSnapshot(supply_used=20), game_step=0)
        assert engine.state == StrategicState.EXPAND

        engine.set_command_override(
            StrategicState.DEFEND, source="human", duration=60.0
        )
        result = engine.evaluate(
            GameSnapshot(supply_used=20, game_time_seconds=10.0), game_step=1
        )
        assert result == StrategicState.DEFEND
        assert engine.state == StrategicState.DEFEND

    def test_override_stays_active_within_duration(self) -> None:
        engine = DecisionEngine(build_order=_simple_order())
        engine._sequencer.advance()
        engine.evaluate(GameSnapshot(supply_used=20), game_step=0)

        engine.set_command_override(
            StrategicState.ATTACK, source="human", duration=60.0
        )
        engine.evaluate(
            GameSnapshot(supply_used=20, game_time_seconds=10.0), game_step=1
        )
        result = engine.evaluate(
            GameSnapshot(supply_used=20, game_time_seconds=50.0), game_step=2
        )
        assert result == StrategicState.ATTACK

    def test_override_expires(self) -> None:
        engine = DecisionEngine(build_order=_simple_order())
        engine._sequencer.advance()
        engine.evaluate(GameSnapshot(supply_used=20), game_step=0)

        engine.set_command_override(
            StrategicState.ATTACK, source="human", duration=60.0
        )
        engine.evaluate(
            GameSnapshot(supply_used=20, game_time_seconds=10.0), game_step=1
        )
        # After duration expires (10 + 60 = 70)
        result = engine.evaluate(
            GameSnapshot(supply_used=20, army_supply=5, game_time_seconds=80.0),
            game_step=3,
        )
        assert result != StrategicState.ATTACK
        assert engine._override_state is None

    def test_override_logged_with_reason(self) -> None:
        engine = DecisionEngine(build_order=_simple_order())
        engine._sequencer.advance()
        engine.evaluate(GameSnapshot(supply_used=20), game_step=0)

        engine.set_command_override(
            StrategicState.DEFEND, source="human", duration=60.0
        )
        engine.evaluate(
            GameSnapshot(supply_used=20, game_time_seconds=10.0), game_step=1
        )

        override_entries = [
            e for e in engine.decision_log if e.reason == "command_override"
        ]
        assert len(override_entries) == 1
        entry = override_entries[0]
        assert entry.to_state == "defend"
        assert entry.from_state == "expand"

    def test_override_expiry_logged(self) -> None:
        engine = DecisionEngine(build_order=_simple_order())
        engine._sequencer.advance()
        engine.evaluate(GameSnapshot(supply_used=20), game_step=0)

        engine.set_command_override(
            StrategicState.ATTACK, source="human", duration=10.0
        )
        engine.evaluate(
            GameSnapshot(supply_used=20, game_time_seconds=5.0), game_step=1
        )
        engine.evaluate(
            GameSnapshot(supply_used=20, army_supply=5, game_time_seconds=20.0),
            game_step=2,
        )

        expiry_entries = [
            e
            for e in engine.decision_log
            if "command_override_expired" in e.reason
        ]
        assert len(expiry_entries) >= 1

    def test_no_duplicate_log_when_already_in_state(self) -> None:
        engine = DecisionEngine(build_order=_simple_order())
        engine._sequencer.advance()
        engine.evaluate(
            GameSnapshot(supply_used=20, enemy_army_near_base=True), game_step=0
        )
        assert engine.state == StrategicState.DEFEND
        log_count_before = len(engine.decision_log)

        engine.set_command_override(
            StrategicState.DEFEND, source="human", duration=60.0
        )
        engine.evaluate(
            GameSnapshot(supply_used=20, game_time_seconds=10.0), game_step=1
        )
        assert len(engine.decision_log) == log_count_before


# ---------------------------------------------------------------------------
# ScoutManager.force_scout tests
# ---------------------------------------------------------------------------


class TestScoutManagerForceScout:
    def test_force_scout_sets_target(self) -> None:
        mgr = ScoutManager()
        mgr.force_scout((100.0, 200.0))
        assert mgr.forced_target == (100.0, 200.0)

    def test_consume_forced_target(self) -> None:
        mgr = ScoutManager()
        mgr.force_scout((100.0, 200.0))
        target = mgr.consume_forced_target()
        assert target == (100.0, 200.0)
        assert mgr.forced_target is None

    def test_consume_when_none(self) -> None:
        mgr = ScoutManager()
        assert mgr.consume_forced_target() is None

    def test_force_scout_overrides_previous(self) -> None:
        mgr = ScoutManager()
        mgr.force_scout((1.0, 2.0))
        mgr.force_scout((3.0, 4.0))
        assert mgr.forced_target == (3.0, 4.0)
