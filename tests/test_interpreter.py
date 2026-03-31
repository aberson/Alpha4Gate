"""Unit tests for the Claude NLP interpreter and Step 3 advisor updates."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

from alpha4gate.claude_advisor import (
    AdvisorResponse,
    RateLimiter,
    parse_response,
)
from alpha4gate.commands.interpreter import CommandInterpreter
from alpha4gate.commands.primitives import (
    CommandAction,
    CommandPrimitive,
    CommandSettings,
    CommandSource,
)

# ------------------------------------------------------------------ #
#  Helper to run async with mocked _call_api
# ------------------------------------------------------------------ #


def _run_interpret(
    api_key: str,
    text: str,
    source: CommandSource,
    api_return: str | None = None,
    api_side_effect: Exception | None = None,
) -> list[CommandPrimitive] | None:
    """Run interpreter.interpret() with _call_api mocked."""
    interp = CommandInterpreter(api_key=api_key)

    if api_side_effect is not None:
        interp._call_api = AsyncMock(side_effect=api_side_effect)  # type: ignore[method-assign]
    elif api_return is not None:
        interp._call_api = AsyncMock(return_value=api_return)  # type: ignore[method-assign]
    else:
        interp._call_api = AsyncMock(return_value=None)  # type: ignore[method-assign]

    return asyncio.run(interp.interpret(text, source))


# ------------------------------------------------------------------ #
#  CommandInterpreter tests
# ------------------------------------------------------------------ #


class TestInterpreterValidResponse:
    """Test interpreter with a mocked valid JSON response."""

    def test_valid_json_returns_primitives(self) -> None:
        response_json = json.dumps([
            {"action": "build", "target": "gateway", "location": "main", "priority": 7},
            {"action": "attack", "target": "army", "location": "enemy_natural", "priority": 9},
        ])
        result = _run_interpret("sk-test", "build and attack", CommandSource.HUMAN, response_json)

        assert result is not None
        assert len(result) == 2
        assert result[0].action == CommandAction.BUILD
        assert result[0].target == "gateway"
        assert result[0].location == "main"
        assert result[0].priority == 7
        assert result[0].source == CommandSource.HUMAN
        assert result[1].action == CommandAction.ATTACK
        assert result[1].priority == 9

    def test_single_command_response(self) -> None:
        response_json = json.dumps([
            {"action": "scout", "target": "probe", "location": None, "priority": 5},
        ])
        result = _run_interpret("sk-test", "scout with a probe", CommandSource.AI, response_json)

        assert result is not None
        assert len(result) == 1
        assert result[0].action == CommandAction.SCOUT
        assert result[0].source == CommandSource.AI


class TestInterpreterTimeout:
    """Test interpreter with timeout."""

    def test_timeout_returns_none(self) -> None:
        interp = CommandInterpreter(api_key="sk-test")

        async def slow_api(prompt: str) -> str:
            await asyncio.sleep(10)
            return "[]"

        interp._call_api = slow_api  # type: ignore[method-assign]
        result = asyncio.run(interp.interpret("build something", CommandSource.HUMAN))
        assert result is None


class TestInterpreterInvalidJson:
    """Test interpreter with invalid JSON response."""

    def test_invalid_json_returns_none(self) -> None:
        result = _run_interpret(
            "sk-test", "asdf", CommandSource.HUMAN, "This is not JSON at all"
        )
        assert result is None

    def test_empty_list_returns_none(self) -> None:
        result = _run_interpret("sk-test", "do nothing", CommandSource.HUMAN, "[]")
        assert result is None

    def test_invalid_action_skipped(self) -> None:
        response_json = json.dumps([
            {"action": "fly", "target": "sky", "priority": 5},
            {"action": "build", "target": "pylon", "priority": 5},
        ])
        result = _run_interpret(
            "sk-test", "fly and build", CommandSource.HUMAN, response_json
        )

        assert result is not None
        assert len(result) == 1
        assert result[0].action == CommandAction.BUILD


class TestInterpreterNoApiKey:
    """Test interpreter with empty API key."""

    def test_empty_api_key_returns_none(self) -> None:
        result = _run_interpret("", "build a gateway", CommandSource.HUMAN)
        assert result is None


class TestInterpreterPriorityValidation:
    """Test that out-of-range priorities default to 5."""

    def test_out_of_range_priority_defaults(self) -> None:
        response_json = json.dumps([
            {"action": "build", "target": "pylon", "priority": 99},
        ])
        result = _run_interpret(
            "sk-test", "build a pylon", CommandSource.HUMAN, response_json
        )

        assert result is not None
        assert result[0].priority == 5


class TestInterpreterCodeFence:
    """Test interpreter strips markdown code fences."""

    def test_json_in_code_fence(self) -> None:
        raw = '```json\n[{"action": "build", "target": "pylon", "priority": 5}]\n```'
        result = _run_interpret("sk-test", "build", CommandSource.HUMAN, raw)

        assert result is not None
        assert len(result) == 1
        assert result[0].action == CommandAction.BUILD


# ------------------------------------------------------------------ #
#  Updated AdvisorResponse tests
# ------------------------------------------------------------------ #


class TestAdvisorResponseCommands:
    """Test AdvisorResponse with new commands field."""

    def test_default_commands_empty(self) -> None:
        resp = AdvisorResponse(
            suggestion="Build more pylons",
            urgency="medium",
            reasoning="Supply blocked",
        )
        assert resp.commands == []

    def test_commands_populated(self) -> None:
        cmd = CommandPrimitive(
            action=CommandAction.BUILD,
            target="gateway",
            source=CommandSource.AI,
        )
        resp = AdvisorResponse(
            suggestion="Build gateway",
            urgency="high",
            reasoning="Need production",
            commands=[cmd],
        )
        assert len(resp.commands) == 1
        assert resp.commands[0].action == CommandAction.BUILD


# ------------------------------------------------------------------ #
#  Updated parse_response tests
# ------------------------------------------------------------------ #


class TestParseResponseWithCommands:
    """Test that parse_response extracts commands from JSON."""

    def test_commands_extracted(self) -> None:
        text = json.dumps({
            "commands": [
                {"action": "build", "target": "gateway", "location": "main", "priority": 7},
                {"action": "expand", "target": "nexus", "priority": 5},
            ],
            "suggestion": "Build and expand",
            "urgency": "high",
            "reasoning": "Need production and economy",
        })
        resp = parse_response(text)
        assert resp.suggestion == "Build and expand"
        assert resp.urgency == "high"
        assert len(resp.commands) == 2
        assert resp.commands[0].action == CommandAction.BUILD
        assert resp.commands[0].target == "gateway"
        assert resp.commands[0].location == "main"
        assert resp.commands[0].priority == 7
        assert resp.commands[0].source == CommandSource.AI
        assert resp.commands[1].action == CommandAction.EXPAND

    def test_no_commands_field(self) -> None:
        text = json.dumps({
            "suggestion": "Just macro",
            "urgency": "low",
            "reasoning": "Safe",
        })
        resp = parse_response(text)
        assert resp.commands == []

    def test_invalid_commands_skipped(self) -> None:
        text = json.dumps({
            "commands": [
                {"action": "fly", "target": "sky"},
                {"action": "build", "target": "pylon", "priority": 5},
            ],
            "suggestion": "Build stuff",
            "urgency": "medium",
            "reasoning": "Production",
        })
        resp = parse_response(text)
        assert len(resp.commands) == 1
        assert resp.commands[0].action == CommandAction.BUILD

    def test_unparseable_response_no_commands(self) -> None:
        text = "Just do whatever"
        resp = parse_response(text)
        assert resp.commands == []


# ------------------------------------------------------------------ #
#  RateLimiter.set_interval tests
# ------------------------------------------------------------------ #


class TestRateLimiterSetInterval:
    """Test the new set_interval method."""

    def test_set_interval_changes_interval(self) -> None:
        rl = RateLimiter(interval_game_seconds=30.0)
        assert rl.interval == 30.0
        rl.set_interval(15.0)
        assert rl.interval == 15.0

    def test_set_interval_affects_can_call(self) -> None:
        rl = RateLimiter(interval_game_seconds=30.0)
        rl.record_call(0.0)
        assert not rl.can_call(20.0)  # 20 < 30

        rl.set_interval(15.0)
        assert rl.can_call(20.0)  # 20 >= 15


# ------------------------------------------------------------------ #
#  Lockout logic tests
# ------------------------------------------------------------------ #


class TestLockoutLogic:
    """Test the AI lockout mechanism in bot.py (tested via unit logic)."""

    def _make_bot_with_lockout(self) -> Any:
        """Create a mock bot with lockout methods wired in."""
        from alpha4gate.bot import Alpha4GateBot

        # We test the lockout methods directly using a minimal approach:
        # use object.__new__ to avoid sc2 __init__ dependency.
        bot = object.__new__(Alpha4GateBot)
        bot._ai_lockout_until = 0.0
        return bot  # type: ignore[return-value]

    def test_not_locked_out_initially(self) -> None:
        bot = self._make_bot_with_lockout()
        assert not bot._is_ai_locked_out(0.0)
        assert not bot._is_ai_locked_out(100.0)

    def test_lockout_active_within_duration(self) -> None:
        bot = self._make_bot_with_lockout()
        with patch(
            "alpha4gate.bot.get_command_settings",
            return_value=CommandSettings(lockout_duration=5.0),
        ):
            bot.set_ai_lockout(10.0)
        assert bot._is_ai_locked_out(12.0)
        assert bot._is_ai_locked_out(14.9)

    def test_lockout_expired(self) -> None:
        bot = self._make_bot_with_lockout()
        with patch(
            "alpha4gate.bot.get_command_settings",
            return_value=CommandSettings(lockout_duration=5.0),
        ):
            bot.set_ai_lockout(10.0)
        assert not bot._is_ai_locked_out(15.0)
        assert not bot._is_ai_locked_out(20.0)

    def test_lockout_resets_on_new_command(self) -> None:
        bot = self._make_bot_with_lockout()
        with patch(
            "alpha4gate.bot.get_command_settings",
            return_value=CommandSettings(lockout_duration=5.0),
        ):
            bot.set_ai_lockout(10.0)
        assert bot._is_ai_locked_out(14.0)

        with patch(
            "alpha4gate.bot.get_command_settings",
            return_value=CommandSettings(lockout_duration=5.0),
        ):
            bot.set_ai_lockout(14.0)
        assert bot._is_ai_locked_out(18.0)
        assert not bot._is_ai_locked_out(19.0)


# ------------------------------------------------------------------ #
#  Interpreter API error handling
# ------------------------------------------------------------------ #


class TestInterpreterApiError:
    """Test interpreter when API call raises an exception."""

    def test_api_exception_returns_none(self) -> None:
        result = _run_interpret(
            "sk-test",
            "build something",
            CommandSource.HUMAN,
            api_side_effect=RuntimeError("API down"),
        )
        assert result is None

    def test_api_returns_none(self) -> None:
        result = _run_interpret(
            "sk-test", "build something", CommandSource.HUMAN, api_return=None
        )
        assert result is None
