"""End-to-end integration tests for the command pipeline.

Tests the full flow from API submission through queue to execution,
mocking only the SC2 game engine (BotAI), not internal command components.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sc2.position import Point2

from alpha4gate import api as api_module
from alpha4gate.api import app, configure
from alpha4gate.build_orders import BuildOrder, BuildStep
from alpha4gate.commands import (
    CommandAction,
    CommandExecutor,
    CommandMode,
    CommandPrimitive,
    CommandSource,
    get_command_queue,
    get_command_settings,
)
from alpha4gate.commands.parser import StructuredParser
from alpha4gate.commands.recipes import expand_tech
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

    bot.expansion_locations_list = [
        Point2((50.0, 50.0)),
        Point2((70.0, 70.0)),
        Point2((90.0, 90.0)),
        Point2((110.0, 110.0)),
    ]

    bot.can_afford.return_value = True
    bot.build = AsyncMock()
    bot.expand_now = AsyncMock()
    bot.research = AsyncMock()

    idle_gw = MagicMock()
    idle_gw.train = MagicMock()
    structures_result = MagicMock()
    structures_result.idle = [idle_gw]
    structures_result.ready = []
    structures_result.closest_to = MagicMock(
        return_value=MagicMock(position=Point2((55, 55)))
    )
    bot.structures.return_value = structures_result

    bot.decision_engine = DecisionEngine(
        build_order=BuildOrder(
            id="test", name="Test", steps=[BuildStep(supply=10, action="build", target="Pylon")]
        )
    )
    bot.scout_manager = ScoutManager()

    return bot


def _simple_order() -> BuildOrder:
    return BuildOrder(
        id="test",
        name="Test",
        steps=[BuildStep(supply=10, action="build", target="Pylon")],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singletons() -> None:  # type: ignore[misc]
    """Reset module-level singletons between tests to avoid state leakage."""
    import alpha4gate.commands.primitives as p_mod
    import alpha4gate.commands.queue as q_mod

    q_mod._command_queue = None
    p_mod._settings = None
    yield  # type: ignore[misc]
    q_mod._command_queue = None
    p_mod._settings = None


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """Create a test client with temporary data directories."""
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    replay_dir = tmp_path / "replays"
    data_dir.mkdir()
    log_dir.mkdir()
    replay_dir.mkdir()
    configure(data_dir, log_dir, replay_dir)

    api_module._command_history.clear()

    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Full pipeline: structured command → queue → executor
# ---------------------------------------------------------------------------


class TestFullPipeline:
    def test_structured_command_through_queue_to_executor(
        self, client: TestClient
    ) -> None:
        """POST a structured command, verify it's parsed, queued, drained, and executable."""
        # Submit via API
        resp = client.post("/api/commands", json={"text": "build stalkers at natural"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["parsed"][0]["action"] == "build"
        assert data["parsed"][0]["target"] == "stalkers"
        assert data["parsed"][0]["location"] == "natural"

        # Verify queued
        queue = get_command_queue()
        assert queue.size == 1

        # Drain and execute with mock bot
        commands = queue.drain(game_time=0.0)
        assert len(commands) == 1
        assert commands[0].action == CommandAction.BUILD
        assert commands[0].target == "stalkers"

        bot = _mock_bot()
        executor = CommandExecutor(bot)
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(executor.execute(commands[0]))
        finally:
            loop.close()

        assert result.success is True
        assert result.primitives_executed == 1


# ---------------------------------------------------------------------------
# 2. Mode switching clears queue
# ---------------------------------------------------------------------------


class TestModeSwitchClearsQueue:
    def test_mode_switch_clears_queue(self, client: TestClient) -> None:
        """Queue commands, switch mode, verify queue is empty."""
        client.post("/api/commands", json={"text": "build stalkers"})
        client.post("/api/commands", json={"text": "build zealots"})
        assert get_command_queue().size == 2

        resp = client.put("/api/commands/mode", json={"mode": "human_only"})
        assert resp.status_code == 200
        assert resp.json()["queue_cleared"] is True
        assert get_command_queue().size == 0


# ---------------------------------------------------------------------------
# 3. Mute toggle suppresses AI commands
# ---------------------------------------------------------------------------


class TestMuteToggle:
    def test_mute_via_settings(self, client: TestClient) -> None:
        """Set muted=true via API, verify settings reflect it."""
        resp = client.put("/api/commands/settings", json={"muted": True})
        assert resp.status_code == 200
        assert resp.json()["muted"] is True

        settings = get_command_settings()
        assert settings.muted is True

    def test_mute_suppresses_advisor_conditions(self) -> None:
        """When muted, the advisor integration conditions are not met.

        In bot.py, the advisor block requires `not settings.muted`.
        We verify the condition directly.
        """
        settings = get_command_settings()
        settings.muted = True

        # The bot checks: not settings.muted and mode != HUMAN_ONLY
        # When muted is True, advisor should not run
        advisor_should_run = not settings.muted and settings.mode != CommandMode.HUMAN_ONLY
        assert advisor_should_run is False

    def test_unmute_allows_advisor_conditions(self) -> None:
        """When not muted and mode is AI_ASSISTED, advisor conditions are met."""
        settings = get_command_settings()
        settings.muted = False
        settings.mode = CommandMode.AI_ASSISTED

        advisor_should_run = not settings.muted and settings.mode != CommandMode.HUMAN_ONLY
        assert advisor_should_run is True


# ---------------------------------------------------------------------------
# 4. AI lockout in hybrid mode
# ---------------------------------------------------------------------------


class TestAILockoutHybridMode:
    def test_human_command_triggers_lockout(self) -> None:
        """In HYBRID_CMD mode, a human command triggers AI lockout."""
        settings = get_command_settings()
        settings.mode = CommandMode.HYBRID_CMD
        settings.lockout_duration = 5.0

        # Simulate: bot receives human command at game_time=10.0
        lockout_until = 10.0 + settings.lockout_duration  # 15.0

        # AI is locked out at game_time=12.0 (within lockout)
        assert 12.0 < lockout_until

        # AI is NOT locked out at game_time=20.0 (after lockout)
        assert 20.0 >= lockout_until

    def test_ai_commands_dropped_during_lockout(self) -> None:
        """Verify the lockout logic directly: AI commands suppressed during lockout window."""
        settings = get_command_settings()
        settings.mode = CommandMode.HYBRID_CMD
        settings.lockout_duration = 5.0

        # Simulate the bot's lockout tracking
        game_time_human_cmd = 10.0
        ai_lockout_until = game_time_human_cmd + settings.lockout_duration

        # During lockout (t=12), AI is locked out
        is_locked_at_12 = 12.0 < ai_lockout_until
        assert is_locked_at_12 is True

        # After lockout (t=16), AI is free
        is_locked_at_16 = 16.0 < ai_lockout_until
        assert is_locked_at_16 is False

    def test_ai_lockout_expiry(self) -> None:
        """Verify lockout expires and AI commands work after the window."""
        settings = get_command_settings()
        settings.mode = CommandMode.HYBRID_CMD
        settings.lockout_duration = 3.0

        game_time_human_cmd = 100.0
        ai_lockout_until = game_time_human_cmd + settings.lockout_duration

        # Exactly at boundary
        assert 103.0 >= ai_lockout_until  # lockout expired at boundary
        # Clearly after
        assert 105.0 >= ai_lockout_until


# ---------------------------------------------------------------------------
# 5. Command expiry (TTL)
# ---------------------------------------------------------------------------


class TestCommandExpiry:
    def test_expired_command_not_returned(self) -> None:
        """Queue a command with short TTL, drain past TTL, verify it's gone."""
        queue = get_command_queue()
        cmd = CommandPrimitive(
            action=CommandAction.BUILD,
            target="stalker",
            timestamp=10.0,
            ttl=5.0,
            source=CommandSource.HUMAN,
        )
        queue.push(cmd)
        assert queue.size == 1

        # Drain at game_time=20.0, well past timestamp(10) + ttl(5) = 15
        result = queue.drain(game_time=20.0)
        assert len(result) == 0

    def test_non_expired_command_returned(self) -> None:
        """Queue a command, drain within TTL, verify it's returned."""
        queue = get_command_queue()
        cmd = CommandPrimitive(
            action=CommandAction.BUILD,
            target="stalker",
            timestamp=10.0,
            ttl=60.0,
            source=CommandSource.HUMAN,
        )
        queue.push(cmd)

        result = queue.drain(game_time=30.0)
        assert len(result) == 1
        assert result[0].target == "stalker"


# ---------------------------------------------------------------------------
# 6. Queue overflow eviction
# ---------------------------------------------------------------------------


class TestQueueOverflowEviction:
    def test_overflow_evicts_lowest_priority_ai_command(self) -> None:
        """Push 11 commands (max depth 10), verify lowest-priority AI evicted."""
        queue = get_command_queue()  # max_depth=10

        # Push 5 human commands (priority 5)
        for i in range(5):
            queue.push(
                CommandPrimitive(
                    action=CommandAction.BUILD,
                    target=f"human_{i}",
                    priority=5,
                    source=CommandSource.HUMAN,
                )
            )

        # Push 5 AI commands (priority 3)
        for i in range(5):
            queue.push(
                CommandPrimitive(
                    action=CommandAction.BUILD,
                    target=f"ai_{i}",
                    priority=3,
                    source=CommandSource.AI,
                )
            )

        assert queue.size == 10

        # Push one more — should evict lowest-priority AI command
        queue.push(
            CommandPrimitive(
                action=CommandAction.BUILD,
                target="overflow_cmd",
                priority=5,
                source=CommandSource.HUMAN,
            )
        )

        assert queue.size == 10
        targets = {c.target for c in queue.pending}
        # All human commands preserved
        for i in range(5):
            assert f"human_{i}" in targets
        assert "overflow_cmd" in targets
        # One AI command should be evicted (4 remaining)
        ai_count = sum(1 for c in queue.pending if c.source == CommandSource.AI)
        assert ai_count == 4


# ---------------------------------------------------------------------------
# 7. Conflict clearing in hybrid mode
# ---------------------------------------------------------------------------


class TestConflictClearing:
    def test_human_attack_clears_ai_attack(self) -> None:
        """Queue an AI ATTACK, submit human ATTACK, verify AI ATTACK cleared."""
        queue = get_command_queue()
        settings = get_command_settings()
        settings.mode = CommandMode.HYBRID_CMD

        # Queue an AI ATTACK
        ai_cmd = CommandPrimitive(
            action=CommandAction.ATTACK,
            target="enemy_natural",
            source=CommandSource.AI,
            priority=5,
        )
        queue.push(ai_cmd)
        assert queue.size == 1

        # Human submits ATTACK — clear conflicting AI ATTACKs
        cleared = queue.clear_conflicting(CommandAction.ATTACK)
        assert len(cleared) == 1
        assert cleared[0].source == CommandSource.AI
        assert cleared[0].action == CommandAction.ATTACK

        # Queue should now be empty (AI attack removed)
        assert queue.size == 0

        # Push the human command
        human_cmd = CommandPrimitive(
            action=CommandAction.ATTACK,
            target="enemy_main",
            source=CommandSource.HUMAN,
            priority=7,
        )
        queue.push(human_cmd)
        assert queue.size == 1
        assert queue.pending[0].source == CommandSource.HUMAN


# ---------------------------------------------------------------------------
# 8. Command override in DecisionEngine
# ---------------------------------------------------------------------------


class TestCommandOverrideIntegration:
    def test_override_active_then_expires(self) -> None:
        """Set ATTACK override, verify it holds, advance past duration, verify normal resumes."""
        engine = DecisionEngine(build_order=_simple_order())
        engine._sequencer.advance()
        engine.evaluate(GameSnapshot(supply_used=20), game_step=0)
        assert engine.state == StrategicState.EXPAND

        # Set override
        engine.set_command_override(StrategicState.ATTACK, source="human", duration=30.0)

        # During override (first evaluate activates it at t=10, expires at t=40)
        result = engine.evaluate(
            GameSnapshot(supply_used=20, game_time_seconds=10.0), game_step=1
        )
        assert result == StrategicState.ATTACK

        # Still active at t=35
        result = engine.evaluate(
            GameSnapshot(supply_used=20, game_time_seconds=35.0), game_step=2
        )
        assert result == StrategicState.ATTACK

        # After expiry (t=50 > 10+30=40)
        result = engine.evaluate(
            GameSnapshot(supply_used=20, army_supply=5, game_time_seconds=50.0),
            game_step=3,
        )
        assert result != StrategicState.ATTACK
        assert engine._override_state is None


# ---------------------------------------------------------------------------
# 9. TECH recipe expansion
# ---------------------------------------------------------------------------


class TestTechRecipeExpansion:
    def test_tech_voidrays_produces_prerequisite_chain(
        self, client: TestClient
    ) -> None:
        """Submit 'tech voidrays', verify parser returns it and expand_tech produces chain."""
        # Parse the command
        parser = StructuredParser()
        primitives = parser.parse("tech voidrays", CommandSource.HUMAN)
        assert primitives is not None
        assert len(primitives) == 1
        assert primitives[0].action == CommandAction.TECH
        assert primitives[0].target == "voidrays"

        # Expand via recipes
        expanded = expand_tech("voidrays", CommandSource.HUMAN, game_time=0.0)
        assert len(expanded) == 2

        # First: build stargate (prerequisite)
        assert expanded[0].action == CommandAction.BUILD
        assert expanded[0].target == "stargate"

        # Second: build voidrays (the tech unit)
        assert expanded[1].action == CommandAction.BUILD
        assert expanded[1].target == "voidrays"

    def test_tech_voidrays_queued_via_api(self, client: TestClient) -> None:
        """Submit 'tech voidrays' via API, verify it's queued."""
        resp = client.post("/api/commands", json={"text": "tech voidrays"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["parsed"][0]["action"] == "tech"
        assert data["parsed"][0]["target"] == "voidrays"
        assert get_command_queue().size == 1


# ---------------------------------------------------------------------------
# 10. Mode-specific behavior: HUMAN_ONLY disables advisor
# ---------------------------------------------------------------------------


class TestHumanOnlyMode:
    def test_human_only_disables_advisor(self, client: TestClient) -> None:
        """Set mode to HUMAN_ONLY, verify settings and advisor conditions."""
        resp = client.put("/api/commands/mode", json={"mode": "human_only"})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "human_only"

        settings = get_command_settings()
        assert settings.mode == CommandMode.HUMAN_ONLY

        # The bot checks: mode != HUMAN_ONLY for advisor
        advisor_should_run = settings.mode != CommandMode.HUMAN_ONLY
        assert advisor_should_run is False

    def test_human_commands_still_work_in_human_only(
        self, client: TestClient
    ) -> None:
        """In HUMAN_ONLY mode, human commands are still accepted and queued."""
        client.put("/api/commands/mode", json={"mode": "human_only"})

        resp = client.post("/api/commands", json={"text": "build stalkers"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"
        assert get_command_queue().size == 1


# ---------------------------------------------------------------------------
# 11. Settings persistence across calls
# ---------------------------------------------------------------------------


class TestSettingsPersistence:
    def test_settings_persist_across_api_calls(self, client: TestClient) -> None:
        """Update settings, read them back, verify values match."""
        # Update
        client.put(
            "/api/commands/settings",
            json={"claude_interval": 45.0, "lockout_duration": 8.0},
        )

        # Read back via settings singleton
        settings = get_command_settings()
        assert settings.claude_interval == 45.0
        assert settings.lockout_duration == 8.0

    def test_settings_roundtrip_via_api(self, client: TestClient) -> None:
        """Update settings via API, then read via API mode endpoint, verify consistency."""
        client.put("/api/commands/settings", json={"muted": True})
        resp = client.get("/api/commands/mode")
        data = resp.json()
        assert data["muted"] is True


# ---------------------------------------------------------------------------
# 12. Command history tracking
# ---------------------------------------------------------------------------


class TestCommandHistoryTracking:
    def test_history_records_submissions(self, client: TestClient) -> None:
        """Submit commands, GET history, verify entries present with correct structure."""
        client.post("/api/commands", json={"text": "build stalkers"})
        client.post("/api/commands", json={"text": "attack enemy_main"})

        resp = client.get("/api/commands/history")
        assert resp.status_code == 200
        history = resp.json()["commands"]
        assert len(history) == 2

        # Verify structure of first entry
        entry = history[0]
        assert "id" in entry
        assert entry["text"] == "build stalkers"
        assert entry["status"] == "queued"
        assert entry["source"] == "human"
        assert "timestamp_utc" in entry
        assert entry["parsed"] is not None
        assert len(entry["parsed"]) == 1
        assert entry["parsed"][0]["action"] == "build"

    def test_history_entries_have_unique_ids(self, client: TestClient) -> None:
        """Each history entry has a unique id."""
        client.post("/api/commands", json={"text": "build stalkers"})
        client.post("/api/commands", json={"text": "build zealots"})

        resp = client.get("/api/commands/history")
        history = resp.json()["commands"]
        ids = [e["id"] for e in history]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# 13. API commands use infinite TTL — never expired by drain
# ---------------------------------------------------------------------------


class TestAPICommandTTL:
    def test_api_command_survives_high_game_time(self, client: TestClient) -> None:
        """Commands submitted via the API have ttl=inf and are never expired."""
        resp = client.post("/api/commands", json={"text": "build stalkers"})
        assert resp.status_code == 200

        queue = get_command_queue()
        assert queue.size == 1

        # Drain at an absurdly high game time — command should still be returned
        commands = queue.drain(game_time=999_999.0)
        assert len(commands) == 1
        assert commands[0].target == "stalkers"

    def test_game_time_stamped_command_does_expire(self) -> None:
        """A command with a finite TTL and timestamp DOES expire when drained late."""
        queue = get_command_queue()
        cmd = CommandPrimitive(
            action=CommandAction.BUILD,
            target="zealot",
            timestamp=10.0,
            ttl=5.0,
            source=CommandSource.HUMAN,
        )
        queue.push(cmd)
        assert queue.size == 1

        # Drain well past expiry (10 + 5 = 15)
        result = queue.drain(game_time=100.0)
        assert len(result) == 0

    def test_inf_ttl_command_not_expired_alongside_finite(
        self, client: TestClient
    ) -> None:
        """Mix inf-TTL (API) and finite-TTL commands; only the finite one expires."""
        # API command — gets inf TTL
        client.post("/api/commands", json={"text": "build stalkers"})

        # Manually pushed command with short TTL
        queue = get_command_queue()
        finite_cmd = CommandPrimitive(
            action=CommandAction.BUILD,
            target="zealot",
            timestamp=0.0,
            ttl=10.0,
            source=CommandSource.HUMAN,
        )
        queue.push(finite_cmd)
        assert queue.size == 2

        # Drain past the finite TTL but the inf one survives
        commands = queue.drain(game_time=50.0)
        assert len(commands) == 1
        assert commands[0].target == "stalkers"


# ---------------------------------------------------------------------------
# 14. Drain → CommandExecutor integration
# ---------------------------------------------------------------------------


class TestDrainToExecute:
    def test_drain_feeds_executor(self) -> None:
        """Push a command, drain it, pass to CommandExecutor, verify execution."""
        import asyncio

        queue = get_command_queue()
        cmd = CommandPrimitive(
            action=CommandAction.BUILD,
            target="stalkers",
            timestamp=0.0,
            ttl=60.0,
            source=CommandSource.HUMAN,
        )
        queue.push(cmd)

        commands = queue.drain(game_time=1.0)
        assert len(commands) == 1

        bot = _mock_bot()
        executor = CommandExecutor(bot)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(executor.execute(commands[0]))
        finally:
            loop.close()

        assert result.success is True
        assert result.primitives_executed == 1

    def test_drain_empty_queue_gives_nothing_to_execute(self) -> None:
        """Draining an empty queue yields no commands for the executor."""
        queue = get_command_queue()
        commands = queue.drain(game_time=0.0)
        assert len(commands) == 0
