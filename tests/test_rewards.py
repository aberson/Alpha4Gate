"""Tests for the reward shaping engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from alpha4gate.learning.rewards import (
    BASE_LOSS_REWARD,
    BASE_STEP_REWARD,
    BASE_WIN_REWARD,
    RewardCalculator,
)

RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "reward_rules.json"


@pytest.fixture()
def calc() -> RewardCalculator:
    return RewardCalculator(RULES_PATH)


def _state(**overrides: Any) -> dict[str, Any]:
    """Build a default game state dict with optional overrides."""
    base: dict[str, Any] = {
        "supply_used": 50,
        "supply_cap": 100,
        "minerals": 800,
        "vespene": 400,
        "army_supply": 30,
        "worker_count": 22,
        "base_count": 2,
        "enemy_army_near_base": False,
        "enemy_army_supply_visible": 0,
        "game_time_seconds": 200.0,
        "gateway_count": 3,
        "robo_count": 1,
        "forge_count": 1,
        "upgrade_count": 2,
        "enemy_structure_count": 0,
    }
    base.update(overrides)
    return base


class TestBaseReward:
    def test_step_reward(self, calc: RewardCalculator) -> None:
        reward = calc.compute_step_reward(_state())
        assert reward >= BASE_STEP_REWARD  # at least survival bonus

    def test_win_reward(self, calc: RewardCalculator) -> None:
        reward = calc.compute_step_reward(_state(), is_terminal=True, result="win")
        assert reward >= BASE_WIN_REWARD

    def test_loss_reward(self, calc: RewardCalculator) -> None:
        reward = calc.compute_step_reward(_state(), is_terminal=True, result="loss")
        assert reward <= BASE_LOSS_REWARD + BASE_STEP_REWARD + 1.0  # allow some rule bonuses


class TestRuleEvaluation:
    def test_scout_early_active(self, calc: RewardCalculator) -> None:
        """Scout rule fires when has_scouted=True and game_time < 180."""
        s = _state(game_time_seconds=120.0, has_scouted=True)
        reward = calc.compute_step_reward(s)
        assert reward > BASE_STEP_REWARD  # includes +0.1 from scout-early

    def test_scout_early_not_scouted(self, calc: RewardCalculator) -> None:
        """Scout rule does NOT fire when has_scouted=False."""
        s = _state(game_time_seconds=120.0, has_scouted=False)
        reward = calc.compute_step_reward(s)
        assert abs(reward - BASE_STEP_REWARD) < 0.001

    def test_scout_early_too_late(self, calc: RewardCalculator) -> None:
        """Scout rule does NOT fire when game_time >= 180."""
        s = _state(game_time_seconds=200.0, has_scouted=True)
        reward = calc.compute_step_reward(s)
        assert abs(reward - BASE_STEP_REWARD) < 0.001

    def test_supply_block_penalty(self, calc: RewardCalculator) -> None:
        """Supply block rule fires when supply_used == supply_cap."""
        s = _state(supply_used=100, supply_cap=100)
        reward = calc.compute_step_reward(s)
        expected = BASE_STEP_REWARD - 0.05
        assert abs(reward - expected) < 0.001

    def test_no_supply_block_no_penalty(self, calc: RewardCalculator) -> None:
        """Supply block rule does NOT fire when supply_used < supply_cap."""
        s = _state(supply_used=50, supply_cap=100)
        reward = calc.compute_step_reward(s)
        assert abs(reward - BASE_STEP_REWARD) < 0.001

    def test_defend_rush(self, calc: RewardCalculator) -> None:
        """Defend-rush fires when enemy structures near base early and army >= 6."""
        s = _state(
            enemy_structure_count=1,
            enemy_army_near_base=True,
            game_time_seconds=200.0,
            army_supply=10,
        )
        reward = calc.compute_step_reward(s)
        assert reward > BASE_STEP_REWARD + 0.2  # includes +0.3 from defend-rush


class TestOperators:
    def test_all_operators(self) -> None:
        calc = RewardCalculator()
        calc._rules = []
        ops_and_expected = [
            ("<", 5, 10, True),
            ("<", 10, 5, False),
            (">", 10, 5, True),
            (">", 5, 10, False),
            ("<=", 5, 5, True),
            (">=", 5, 5, True),
            ("==", 5, 5, True),
            ("!=", 5, 6, True),
            ("!=", 5, 5, False),
        ]
        for op_str, left, right, expected in ops_and_expected:
            result = calc._check_clause(
                {"field": "x", "op": op_str, "value": right},
                {"x": left},
            )
            assert result == expected, f"{left} {op_str} {right} should be {expected}"

    def test_value_field_comparison(self) -> None:
        calc = RewardCalculator()
        result = calc._check_clause(
            {"field": "supply_used", "op": "==", "value_field": "supply_cap"},
            {"supply_used": 100, "supply_cap": 100},
        )
        assert result is True


class TestActiveToggle:
    def test_inactive_rule_skipped(self) -> None:
        """Rules with active=false should not fire."""
        calc = RewardCalculator(RULES_PATH)
        # Disable all rules
        for rule in calc.rules:
            rule.active = False
        s = _state(supply_used=100, supply_cap=100)  # would trigger supply block
        reward = calc.compute_step_reward(s)
        assert abs(reward - BASE_STEP_REWARD) < 0.001


class TestEdgeCases:
    def test_empty_rules(self) -> None:
        calc = RewardCalculator()
        reward = calc.compute_step_reward(_state())
        assert abs(reward - BASE_STEP_REWARD) < 0.001

    def test_missing_field_in_state(self) -> None:
        calc = RewardCalculator(RULES_PATH)
        # State with no fields at all — rules should not crash
        reward = calc.compute_step_reward({})
        assert isinstance(reward, float)

    def test_unknown_operator(self) -> None:
        calc = RewardCalculator()
        result = calc._check_clause(
            {"field": "x", "op": "~=", "value": 5},
            {"x": 5},
        )
        assert result is False
