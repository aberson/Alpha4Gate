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
from orchestrator.registry import resolve_data_path

# Resolve via registry so the test follows the hot-data move from
# ``data/`` -> ``bots/v0/data/`` without knowing which location is active.
RULES_PATH = resolve_data_path("reward_rules.json")


@pytest.fixture()
def calc() -> RewardCalculator:
    return RewardCalculator(RULES_PATH)


def _state(**overrides: Any) -> dict[str, Any]:
    """Build a minimal game state dict that triggers no shaped rules by default."""
    base: dict[str, Any] = {
        "supply_used": 20,
        "supply_cap": 30,
        "minerals": 200,
        "vespene": 50,
        "army_supply": 5,
        "worker_count": 12,
        "base_count": 1,
        "enemy_army_near_base": False,
        "enemy_army_supply_visible": 10,
        "game_time_seconds": 150.0,
        "gateway_count": 1,
        "robo_count": 0,
        "forge_count": 0,
        "upgrade_count": 0,
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

    def test_timeout_reward(self, calc: RewardCalculator) -> None:
        """Timeout is milder than loss but still negative, with gradient."""
        reward = calc.compute_step_reward(_state(), is_terminal=True, result="timeout")
        loss_reward = calc.compute_step_reward(_state(), is_terminal=True, result="loss")
        assert reward < 0  # still negative
        assert reward > loss_reward  # but milder than loss

    def test_timeout_gradient_big_army(self, calc: RewardCalculator) -> None:
        """Bot with big idle army gets harsher timeout penalty."""
        idle = calc.compute_step_reward(
            _state(army_supply=30, enemy_army_supply_visible=2),
            is_terminal=True, result="timeout",
        )
        weak = calc.compute_step_reward(
            _state(army_supply=2, enemy_army_supply_visible=30),
            is_terminal=True, result="timeout",
        )
        assert idle < weak  # big idle army punished more


class TestRuleEvaluation:
    def test_scout_early_active(self, calc: RewardCalculator) -> None:
        """Scout rule fires when has_scouted=True and game_time < 180."""
        s = _state(game_time_seconds=120.0, has_scouted=True)
        reward = calc.compute_step_reward(s)
        assert reward > BASE_STEP_REWARD  # includes +0.1 from scout-early

    def test_scout_early_not_scouted(self, calc: RewardCalculator) -> None:
        """Scout rule does NOT fire when has_scouted=False."""
        base = calc.compute_step_reward(_state(game_time_seconds=120.0))
        s = _state(game_time_seconds=120.0, has_scouted=False)
        reward = calc.compute_step_reward(s)
        assert abs(reward - base) < 0.001

    def test_scout_early_too_late(self, calc: RewardCalculator) -> None:
        """Scout rule does NOT fire when game_time >= 180."""
        base = calc.compute_step_reward(_state(game_time_seconds=200.0))
        s = _state(game_time_seconds=200.0, has_scouted=True)
        reward = calc.compute_step_reward(s)
        assert abs(reward - base) < 0.001

    def test_supply_block_penalty(self, calc: RewardCalculator) -> None:
        """Supply block rule fires when supply_used == supply_cap."""
        base = calc.compute_step_reward(_state())
        s = _state(supply_used=30, supply_cap=30)
        reward = calc.compute_step_reward(s)
        assert reward < base  # supply block penalty

    def test_no_supply_block_no_penalty(self, calc: RewardCalculator) -> None:
        """Supply block rule does NOT fire when supply_used < supply_cap."""
        s = _state()
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
        assert reward > BASE_STEP_REWARD + 0.02  # includes defend-rush (scaled 10x down in Fix C)


class TestEconomyRewards:
    """Tests for economy reward rules (Step 9)."""

    def test_worker_saturation_fires(self, calc: RewardCalculator) -> None:
        base = calc.compute_step_reward(_state())
        s = _state(worker_count=22)
        reward = calc.compute_step_reward(s)
        assert reward > base  # +0.05 from worker-saturation

    def test_worker_saturation_below_threshold(self, calc: RewardCalculator) -> None:
        s = _state(worker_count=20)
        reward = calc.compute_step_reward(s)
        assert abs(reward - BASE_STEP_REWARD) < 0.001

    def test_expand_on_time_fires(self, calc: RewardCalculator) -> None:
        # Compare matching game_time so time-gated penalties cancel; delta isolates expand-on-time.
        base_s = _state(game_time_seconds=250.0)
        expand_s = _state(base_count=2, game_time_seconds=250.0)
        base = calc.compute_step_reward(base_s)
        reward = calc.compute_step_reward(expand_s)
        assert reward > base  # +0.2 from expand-on-time

    def test_expand_on_time_too_late(self, calc: RewardCalculator) -> None:
        # At t=350 the expand-on-time window has closed; base_count=2 should not add reward.
        # gateway_count=5 avoids too-few-gateways-5 firing on the base_count>=2 state only.
        base_s = _state(game_time_seconds=350.0, gateway_count=5)
        too_late_s = _state(base_count=2, game_time_seconds=350.0, gateway_count=5)
        base = calc.compute_step_reward(base_s)
        reward = calc.compute_step_reward(too_late_s)
        assert abs(reward - base) < 0.001

    def test_mineral_floating_penalty(self, calc: RewardCalculator) -> None:
        base = calc.compute_step_reward(_state())
        s = _state(minerals=1500)
        reward = calc.compute_step_reward(s)
        assert reward < base  # -0.02 from mineral-floating

    def test_mineral_floating_not_triggered(self, calc: RewardCalculator) -> None:
        # minerals=400 is below the lowest floating threshold (500).
        s = _state(minerals=400)
        reward = calc.compute_step_reward(s)
        assert abs(reward - BASE_STEP_REWARD) < 0.001

    def test_worker_production_early(self, calc: RewardCalculator) -> None:
        base = calc.compute_step_reward(_state())
        s = _state(worker_count=16, game_time_seconds=100.0)
        reward = calc.compute_step_reward(s)
        assert reward > base  # +0.03 from worker-production


class TestMilitaryRewards:
    """Tests for military reward rules (Step 10)."""

    def test_army_buildup_fires(self, calc: RewardCalculator) -> None:
        base = calc.compute_step_reward(_state())
        s = _state(army_supply=15)
        reward = calc.compute_step_reward(s)
        assert reward > base  # +0.05 from army-buildup

    def test_army_buildup_below_threshold(self, calc: RewardCalculator) -> None:
        s = _state(army_supply=10)
        reward = calc.compute_step_reward(s)
        assert abs(reward - BASE_STEP_REWARD) < 0.001

    def test_army_ratio_fires(self, calc: RewardCalculator) -> None:
        base = calc.compute_step_reward(_state())
        s = _state(army_supply=20, enemy_army_supply_visible=10)
        reward = calc.compute_step_reward(s)
        assert reward > base  # +0.1 from army-ratio

    def test_army_ratio_weaker(self, calc: RewardCalculator) -> None:
        s = _state(army_supply=5, enemy_army_supply_visible=20)
        reward = calc.compute_step_reward(s)
        assert abs(reward - BASE_STEP_REWARD) < 0.001

    def test_tech_progress_fires(self, calc: RewardCalculator) -> None:
        # Compare matching game_time so time-gated penalties cancel; delta isolates tech-progress.
        base_s = _state(game_time_seconds=300.0)
        tech_s = _state(robo_count=1, game_time_seconds=300.0)
        base = calc.compute_step_reward(base_s)
        reward = calc.compute_step_reward(tech_s)
        assert reward > base  # +0.05 from tech-progress

    def test_gateway_efficiency_fires(self, calc: RewardCalculator) -> None:
        base = calc.compute_step_reward(_state())
        s = _state(gateway_count=3)
        reward = calc.compute_step_reward(s)
        assert reward > base  # +0.03 from gateway-efficiency


class TestScoutingRewards:
    """Tests for scouting and information reward rules (Step 11)."""

    def test_early_scout_tight_fires(self, calc: RewardCalculator) -> None:
        base = calc.compute_step_reward(_state())
        s = _state(game_time_seconds=90.0, has_scouted=True)
        reward = calc.compute_step_reward(s)
        assert reward > base  # +0.15 from early-scout-tight + 0.1 from scout-early

    def test_react_to_rush_fires(self, calc: RewardCalculator) -> None:
        base = calc.compute_step_reward(_state())
        s = _state(
            enemy_army_near_base=True,
            current_state="defend",
            army_supply=20,
            enemy_army_supply_visible=5,
        )
        reward = calc.compute_step_reward(s)
        assert reward > base  # +0.3 from react-to-rush

    def test_react_to_rush_not_defending(self, calc: RewardCalculator) -> None:
        """Does NOT fire when not in DEFEND state."""
        s = _state(
            enemy_army_near_base=True,
            current_state="attack",
        )
        # react-to-rush should not fire (is_defending_rush = False)
        reward = calc.compute_step_reward(s)
        # Should not include react-to-rush bonus
        s2 = _state(enemy_army_near_base=True, current_state="defend")
        reward2 = calc.compute_step_reward(s2)
        assert reward < reward2

    def test_map_awareness_fires(self, calc: RewardCalculator) -> None:
        base = calc.compute_step_reward(_state())
        s = _state(enemy_structure_count=3)
        reward = calc.compute_step_reward(s)
        assert reward > base  # +0.05 from map-awareness



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


class TestRewardLogging:
    """Tests for always-on JSONL reward logging and per-game log files."""

    def test_per_game_log_creates_file(self, tmp_path: Path) -> None:
        """open_game_log creates a JSONL file in log_dir."""
        calc = RewardCalculator(log_dir=tmp_path / "reward_logs")
        calc.open_game_log("abc123")
        calc.compute_step_reward(_state())
        calc.close_game_log()
        log_file = tmp_path / "reward_logs" / "game_abc123.jsonl"
        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        import json

        entry = json.loads(lines[0])
        assert "total_reward" in entry
        assert "fired_rules" in entry

    def test_multiple_games_separate_files(self, tmp_path: Path) -> None:
        """Each game gets its own log file."""
        calc = RewardCalculator(log_dir=tmp_path / "logs")
        for gid in ("game1", "game2", "game3"):
            calc.open_game_log(gid)
            calc.compute_step_reward(_state())
            calc.close_game_log()
        assert (tmp_path / "logs" / "game_game1.jsonl").exists()
        assert (tmp_path / "logs" / "game_game2.jsonl").exists()
        assert (tmp_path / "logs" / "game_game3.jsonl").exists()

    def test_close_flushes_log(self, tmp_path: Path) -> None:
        """close() flushes and closes the log file."""
        calc = RewardCalculator(log_dir=tmp_path / "logs")
        calc.open_game_log("flush_test")
        calc.compute_step_reward(_state())
        calc.compute_step_reward(_state(), is_terminal=True, result="win")
        calc.close()
        log_file = tmp_path / "logs" / "game_flush_test.jsonl"
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_context_manager(self, tmp_path: Path) -> None:
        """RewardCalculator works as a context manager."""
        with RewardCalculator(log_dir=tmp_path / "logs") as calc:
            calc.open_game_log("ctx_test")
            calc.compute_step_reward(_state())
        log_file = tmp_path / "logs" / "game_ctx_test.jsonl"
        assert log_file.exists()
        assert len(log_file.read_text().strip().split("\n")) == 1

    def test_no_log_when_log_dir_none(self) -> None:
        """No log file created when log_dir is None."""
        calc = RewardCalculator()
        calc.open_game_log("should_not_exist")
        reward = calc.compute_step_reward(_state())
        assert isinstance(reward, float)
        calc.close()

    def test_close_idempotent(self, tmp_path: Path) -> None:
        """Calling close() multiple times does not raise."""
        calc = RewardCalculator(log_dir=tmp_path / "logs")
        calc.open_game_log("idem")
        calc.compute_step_reward(_state())
        calc.close()
        calc.close()  # should not raise

    def test_legacy_log_path_still_works(self, tmp_path: Path) -> None:
        """Legacy log_path= parameter still creates a single log file."""
        log_file = tmp_path / "legacy.jsonl"
        calc = RewardCalculator(log_path=log_file)
        calc.compute_step_reward(_state())
        calc.close()
        assert log_file.exists()
        assert len(log_file.read_text().strip().split("\n")) == 1

    def test_log_dir_created_if_missing(self, tmp_path: Path) -> None:
        """log_dir is auto-created if it doesn't exist."""
        deep_dir = tmp_path / "a" / "b" / "c"
        calc = RewardCalculator(log_dir=deep_dir)
        assert deep_dir.exists()
        calc.close()
