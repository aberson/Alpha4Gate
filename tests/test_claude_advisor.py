"""Unit tests for Claude advisor: prompt format, rate limiter, response parsing."""

from __future__ import annotations

import json

from alpha4gate.claude_advisor import (
    AdvisorResponse,
    ClaudeAdvisor,
    RateLimiter,
    build_prompt,
    parse_response,
)


class TestBuildPrompt:
    def test_contains_game_time(self) -> None:
        prompt = build_prompt(
            game_time="5:30",
            strategic_state="expand",
            minerals=400,
            vespene=200,
            supply_used=30,
            supply_cap=46,
            army_composition="4 Stalker, 2 Zealot",
            enemy_composition="Unknown",
            recent_decisions="opening -> expand",
            build_order_name="4gate",
            build_step=9,
            total_steps=9,
        )
        assert "5:30" in prompt
        assert "expand" in prompt
        assert "400 minerals" in prompt
        assert "200 gas" in prompt
        assert "30/46" in prompt
        assert "4 Stalker, 2 Zealot" in prompt
        assert "4gate" in prompt

    def test_prompt_is_string(self) -> None:
        prompt = build_prompt(
            game_time="0:00",
            strategic_state="opening",
            minerals=50,
            vespene=0,
            supply_used=12,
            supply_cap=15,
            army_composition="None",
            enemy_composition="None",
            recent_decisions="None",
            build_order_name="4gate",
            build_step=0,
            total_steps=9,
        )
        assert isinstance(prompt, str)
        assert len(prompt) > 100


class TestParseResponse:
    def test_valid_json(self) -> None:
        text = json.dumps({
            "suggestion": "Build more gateways",
            "urgency": "medium",
            "reasoning": "Need more production",
        })
        resp = parse_response(text)
        assert resp.suggestion == "Build more gateways"
        assert resp.urgency == "medium"
        assert resp.reasoning == "Need more production"

    def test_json_in_code_block(self) -> None:
        text = '```json\n{"suggestion": "Expand now", "urgency": "high", "reasoning": "Safe"}\n```'
        resp = parse_response(text)
        assert resp.suggestion == "Expand now"
        assert resp.urgency == "high"

    def test_invalid_json_falls_back(self) -> None:
        text = "Just expand and take your natural"
        resp = parse_response(text)
        assert resp.suggestion == "Just expand and take your natural"
        assert resp.urgency == "low"
        assert "(unparseable response)" in resp.reasoning

    def test_missing_fields_default(self) -> None:
        text = json.dumps({"suggestion": "Attack now"})
        resp = parse_response(text)
        assert resp.suggestion == "Attack now"
        assert resp.urgency == "low"
        assert resp.reasoning == ""

    def test_raw_preserved(self) -> None:
        text = '{"suggestion": "test", "urgency": "low", "reasoning": "r"}'
        resp = parse_response(text)
        assert resp.raw == text


class TestAdvisorResponse:
    def test_to_dict(self) -> None:
        resp = AdvisorResponse(
            suggestion="Build more stalkers",
            urgency="high",
            reasoning="Enemy is massing air",
        )
        d = resp.to_dict()
        assert d["suggestion"] == "Build more stalkers"
        assert d["urgency"] == "high"
        assert d["reasoning"] == "Enemy is massing air"


class TestRateLimiter:
    def test_first_call_allowed(self) -> None:
        rl = RateLimiter(interval_game_seconds=30.0)
        assert rl.can_call(0.0)

    def test_blocked_within_interval(self) -> None:
        rl = RateLimiter(interval_game_seconds=30.0)
        rl.record_call(10.0)
        assert not rl.can_call(20.0)

    def test_allowed_after_interval(self) -> None:
        rl = RateLimiter(interval_game_seconds=30.0)
        rl.record_call(10.0)
        assert rl.can_call(40.0)

    def test_exactly_at_interval(self) -> None:
        rl = RateLimiter(interval_game_seconds=30.0)
        rl.record_call(0.0)
        assert rl.can_call(30.0)

    def test_interval_property(self) -> None:
        rl = RateLimiter(interval_game_seconds=60.0)
        assert rl.interval == 60.0


class TestClaudeAdvisor:
    def test_disabled_without_api_key(self) -> None:
        advisor = ClaudeAdvisor(api_key="")
        assert not advisor.enabled

    def test_enabled_with_api_key(self) -> None:
        advisor = ClaudeAdvisor(api_key="sk-test-key")
        assert advisor.enabled

    def test_request_rejected_when_disabled(self) -> None:
        advisor = ClaudeAdvisor(api_key="")
        assert not advisor.request_advice("test", 0.0)

    def test_no_pending_initially(self) -> None:
        advisor = ClaudeAdvisor(api_key="sk-test")
        assert not advisor.has_pending

    def test_last_response_initially_none(self) -> None:
        advisor = ClaudeAdvisor(api_key="sk-test")
        assert advisor.last_response is None

    def test_collect_response_none_without_pending(self) -> None:
        advisor = ClaudeAdvisor(api_key="sk-test")
        assert advisor.collect_response() is None

    def test_rate_limited_request(self) -> None:
        advisor = ClaudeAdvisor(api_key="sk-test", rate_limit_seconds=30.0)
        # Manually record a call
        advisor._rate_limiter.record_call(0.0)
        assert not advisor.request_advice("test", 15.0)  # Too soon
