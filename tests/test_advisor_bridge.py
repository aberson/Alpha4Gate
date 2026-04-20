"""Tests for TrainingAdvisorBridge and PrinciplesLookup."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from bots.v0.learning.advisor_bridge import (
    PrinciplesLookup,
    TrainingAdvisorBridge,
    build_training_prompt,
)

# ---------------------------------------------------------------------------
# PrinciplesLookup
# ---------------------------------------------------------------------------


class TestPrinciplesLookup:
    def test_loads_sections(self, tmp_path: Path) -> None:
        md = tmp_path / "principles.md"
        md.write_text(
            textwrap.dedent("""\
            # Title

            ## 1. Economy
            Spend resources efficiently. Idle minerals are failure.

            ## 2. Army
            Build the army that beats what the opponent is doing.
            Production must keep pace with income.

            ## 3. Scouting
            Scout continuously and update the opponent model.
            """),
            encoding="utf-8",
        )
        lookup = PrinciplesLookup(md)
        assert lookup.section_count == 3

    def test_missing_file(self, tmp_path: Path) -> None:
        lookup = PrinciplesLookup(tmp_path / "nonexistent.md")
        assert lookup.section_count == 0
        assert lookup.lookup({}) == ""

    def test_lookup_high_minerals(self, tmp_path: Path) -> None:
        md = tmp_path / "principles.md"
        md.write_text(
            textwrap.dedent("""\
            ## 1. Economy
            Idle resources are failure. Spending is priority.

            ## 2. Army
            Build units and maintain production.
            """),
            encoding="utf-8",
        )
        lookup = PrinciplesLookup(md)
        result = lookup.lookup({"minerals": 1000, "game_time_seconds": 300})
        assert "resources" in result.lower() or "spending" in result.lower()

    def test_lookup_enemy_near_base(self, tmp_path: Path) -> None:
        md = tmp_path / "principles.md"
        md.write_text(
            textwrap.dedent("""\
            ## 1. Defense
            Survival is the top priority when under threat.

            ## 2. Economy
            Keep building workers.
            """),
            encoding="utf-8",
        )
        lookup = PrinciplesLookup(md)
        result = lookup.lookup({"enemy_army_near_base": True})
        assert "survival" in result.lower() or "threat" in result.lower()

    def test_lookup_no_conditions_met(self, tmp_path: Path) -> None:
        md = tmp_path / "principles.md"
        md.write_text(
            textwrap.dedent("""\
            ## 1. Economy
            Some text.
            """),
            encoding="utf-8",
        )
        lookup = PrinciplesLookup(md)
        # Normal state with nothing triggering
        result = lookup.lookup({
            "minerals": 200,
            "army_supply": 20,
            "game_time_seconds": 300,
            "base_count": 2,
            "supply_used": 50,
            "supply_cap": 100,
            "worker_count": 22,
            "enemy_army_near_base": False,
        })
        assert result == ""

    def test_lookup_caps_lines(self, tmp_path: Path) -> None:
        """Ensure output is capped at _MAX_PRINCIPLES_LINES."""
        md = tmp_path / "principles.md"
        # Create a section with 200 lines
        lines = "\n".join(f"Line {i} about idle resources." for i in range(200))
        md.write_text(f"## 1. Resource spending\n{lines}\n", encoding="utf-8")
        lookup = PrinciplesLookup(md)
        result = lookup.lookup({"minerals": 1000})
        assert len(result.split("\n")) <= 150


# ---------------------------------------------------------------------------
# TrainingAdvisorBridge
# ---------------------------------------------------------------------------


class TestTrainingAdvisorBridge:
    def test_rate_limiting(self, tmp_path: Path) -> None:
        bridge = TrainingAdvisorBridge(
            rate_limit_seconds=60.0, principles_path=tmp_path / "none.md"
        )
        try:
            # First call should be accepted
            assert bridge.submit_request("test prompt", 0.0)
            # Second call at same time should be rate-limited
            assert not bridge.submit_request("test prompt 2", 10.0)
            # Call after 60s should be accepted
            assert bridge.submit_request("test prompt 3", 61.0)
        finally:
            bridge.shutdown()

    def test_poll_empty(self, tmp_path: Path) -> None:
        bridge = TrainingAdvisorBridge(
            principles_path=tmp_path / "none.md"
        )
        try:
            assert bridge.poll_response() is None
            assert bridge.last_response is None
        finally:
            bridge.shutdown()

    def test_shutdown_idempotent(self, tmp_path: Path) -> None:
        bridge = TrainingAdvisorBridge(
            principles_path=tmp_path / "none.md"
        )
        bridge.shutdown()
        # Second shutdown should not raise
        bridge.shutdown()


# ---------------------------------------------------------------------------
# build_training_prompt
# ---------------------------------------------------------------------------


class TestBuildTrainingPrompt:
    def test_includes_game_state(self, tmp_path: Path) -> None:
        lookup = PrinciplesLookup(tmp_path / "none.md")
        state = {
            "minerals": 500,
            "vespene": 200,
            "supply_used": 40,
            "supply_cap": 60,
            "army_supply": 15,
            "game_time_seconds": 300.0,
            "current_state": "MIDGAME",
            "enemy_army_supply_visible": 10,
        }
        prompt = build_training_prompt(state, lookup)
        assert "500 minerals" in prompt
        assert "200 gas" in prompt
        assert "MIDGAME" in prompt

    def test_includes_principles_when_relevant(self, tmp_path: Path) -> None:
        md = tmp_path / "principles.md"
        md.write_text(
            textwrap.dedent("""\
            ## 1. Economy
            Idle resources are failure. Spending is the priority.
            """),
            encoding="utf-8",
        )
        lookup = PrinciplesLookup(md)
        state = {
            "minerals": 1200,
            "vespene": 0,
            "supply_used": 20,
            "supply_cap": 30,
            "army_supply": 5,
            "game_time_seconds": 300.0,
            "current_state": "MIDGAME",
            "enemy_army_supply_visible": 10,
        }
        prompt = build_training_prompt(state, lookup)
        assert "Relevant Protoss Guiding Principles" in prompt
        assert "Idle resources" in prompt

    def test_no_principles_when_no_conditions(self, tmp_path: Path) -> None:
        md = tmp_path / "principles.md"
        md.write_text(
            textwrap.dedent("""\
            ## 1. Obscure Topic
            Nothing matches any keyword.
            """),
            encoding="utf-8",
        )
        lookup = PrinciplesLookup(md)
        state = {
            "minerals": 200,
            "vespene": 100,
            "supply_used": 40,
            "supply_cap": 100,
            "army_supply": 20,
            "game_time_seconds": 400.0,
            "current_state": "MIDGAME",
            "enemy_army_supply_visible": 15,
            "base_count": 2,
            "worker_count": 22,
            "enemy_army_near_base": False,
        }
        prompt = build_training_prompt(state, lookup)
        assert "Relevant Protoss Guiding Principles" not in prompt

    def test_real_principles_file(self) -> None:
        """Integration test: use the actual principles file if it exists."""
        real_path = Path("documentation/sc2/protoss/guiding-principles.md")
        if not real_path.exists():
            pytest.skip("Principles file not found")
        lookup = PrinciplesLookup(real_path)
        assert lookup.section_count >= 30  # 33 sections in the file
        # High minerals should match something
        result = lookup.lookup({"minerals": 1500})
        assert len(result) > 0
