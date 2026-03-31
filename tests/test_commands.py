from __future__ import annotations

from alpha4gate.commands.parser import StructuredParser
from alpha4gate.commands.primitives import (
    CommandAction,
    CommandMode,
    CommandPrimitive,
    CommandSource,
    get_command_settings,
)
from alpha4gate.commands.queue import CommandQueue
from alpha4gate.commands.recipes import TECH_RECIPES, expand_tech

# --- CommandPrimitive defaults ---


class TestCommandPrimitive:
    def test_defaults(self) -> None:
        cmd = CommandPrimitive(action=CommandAction.BUILD, target="stalker")
        assert cmd.priority == 5
        assert cmd.source == CommandSource.HUMAN
        assert cmd.ttl == 60.0
        assert cmd.timestamp == 0.0
        assert cmd.location is None
        assert cmd.id != ""

    def test_uuid_unique(self) -> None:
        a = CommandPrimitive(action=CommandAction.BUILD, target="zealot")
        b = CommandPrimitive(action=CommandAction.BUILD, target="zealot")
        assert a.id != b.id


# --- CommandSettings singleton ---


class TestCommandSettings:
    def test_singleton(self) -> None:
        s1 = get_command_settings()
        s2 = get_command_settings()
        assert s1 is s2

    def test_defaults(self) -> None:
        s = get_command_settings()
        assert s.mode == CommandMode.AI_ASSISTED
        assert s.claude_interval == 30.0
        assert s.lockout_duration == 5.0
        assert s.muted is False


# --- StructuredParser ---


class TestStructuredParser:
    def setup_method(self) -> None:
        self.parser = StructuredParser()

    def test_build_command(self) -> None:
        result = self.parser.parse("build stalkers at natural", CommandSource.HUMAN)
        assert result is not None
        assert len(result) == 1
        assert result[0].action == CommandAction.BUILD
        assert result[0].target == "stalkers"
        assert result[0].location == "natural"

    def test_attack_command(self) -> None:
        result = self.parser.parse("attack enemy_natural", CommandSource.HUMAN)
        assert result is not None
        assert result[0].action == CommandAction.ATTACK
        assert result[0].target == "enemy_natural"

    def test_expand_command(self) -> None:
        result = self.parser.parse("expand third", CommandSource.HUMAN)
        assert result is not None
        assert result[0].action == CommandAction.EXPAND
        assert result[0].target == "third"

    def test_tech_command(self) -> None:
        result = self.parser.parse("tech voidrays", CommandSource.AI)
        assert result is not None
        assert result[0].action == CommandAction.TECH
        assert result[0].source == CommandSource.AI

    def test_defend_command(self) -> None:
        result = self.parser.parse("defend main", CommandSource.HUMAN)
        assert result is not None
        assert result[0].action == CommandAction.DEFEND
        assert result[0].target == "main"

    def test_scout_command(self) -> None:
        result = self.parser.parse("scout enemy_base", CommandSource.HUMAN)
        assert result is not None
        assert result[0].action == CommandAction.SCOUT
        assert result[0].target == "enemy_base"

    def test_upgrade_command(self) -> None:
        result = self.parser.parse("upgrade blink", CommandSource.HUMAN)
        assert result is not None
        assert result[0].action == CommandAction.UPGRADE

    def test_rally_command(self) -> None:
        result = self.parser.parse("rally natural", CommandSource.HUMAN)
        assert result is not None
        assert result[0].action == CommandAction.RALLY
        assert result[0].target == "natural"

    def test_unrecognized_returns_none(self) -> None:
        assert self.parser.parse("please make some units", CommandSource.HUMAN) is None

    def test_empty_returns_none(self) -> None:
        assert self.parser.parse("", CommandSource.HUMAN) is None

    def test_location_with_to(self) -> None:
        result = self.parser.parse("rally to natural", CommandSource.HUMAN)
        assert result is not None
        assert result[0].location == "natural"
        assert result[0].target == ""

    def test_source_preserved(self) -> None:
        result = self.parser.parse("build zealot", CommandSource.AI)
        assert result is not None
        assert result[0].source == CommandSource.AI


# --- CommandQueue ---


class TestCommandQueue:
    def test_push_and_size(self) -> None:
        q = CommandQueue(max_depth=5)
        q.push(CommandPrimitive(action=CommandAction.BUILD, target="zealot"))
        assert q.size == 1

    def test_drain_priority_order(self) -> None:
        q = CommandQueue(max_depth=10)
        q.push(CommandPrimitive(action=CommandAction.BUILD, target="low", priority=1, timestamp=0))
        q.push(
            CommandPrimitive(action=CommandAction.BUILD, target="high", priority=9, timestamp=0)
        )
        q.push(CommandPrimitive(action=CommandAction.BUILD, target="mid", priority=5, timestamp=0))
        result = q.drain(game_time=30.0)
        assert [c.target for c in result] == ["high", "mid", "low"]
        assert q.size == 0

    def test_drain_ttl_expiry(self) -> None:
        q = CommandQueue(max_depth=10)
        q.push(
            CommandPrimitive(
                action=CommandAction.BUILD, target="old", timestamp=0.0, ttl=10.0
            )
        )
        q.push(
            CommandPrimitive(
                action=CommandAction.BUILD, target="new", timestamp=50.0, ttl=60.0
            )
        )
        result = q.drain(game_time=30.0)
        assert len(result) == 1
        assert result[0].target == "new"

    def test_overflow_eviction_ai_before_human(self) -> None:
        q = CommandQueue(max_depth=2)
        q.push(
            CommandPrimitive(
                action=CommandAction.BUILD,
                target="human_cmd",
                priority=3,
                source=CommandSource.HUMAN,
            )
        )
        q.push(
            CommandPrimitive(
                action=CommandAction.BUILD,
                target="ai_cmd",
                priority=3,
                source=CommandSource.AI,
            )
        )
        # Queue full (2). Push a new one — should evict AI (same priority, AI evicted first).
        q.push(
            CommandPrimitive(
                action=CommandAction.BUILD,
                target="new_cmd",
                priority=5,
                source=CommandSource.HUMAN,
            )
        )
        assert q.size == 2
        targets = {c.target for c in q.pending}
        assert "ai_cmd" not in targets
        assert "human_cmd" in targets
        assert "new_cmd" in targets

    def test_overflow_eviction_lowest_priority(self) -> None:
        q = CommandQueue(max_depth=2)
        q.push(
            CommandPrimitive(
                action=CommandAction.BUILD, target="high", priority=9, source=CommandSource.AI
            )
        )
        q.push(
            CommandPrimitive(
                action=CommandAction.BUILD, target="low", priority=1, source=CommandSource.AI
            )
        )
        q.push(
            CommandPrimitive(
                action=CommandAction.BUILD, target="mid", priority=5, source=CommandSource.AI
            )
        )
        targets = {c.target for c in q.pending}
        assert "low" not in targets

    def test_clear_all(self) -> None:
        q = CommandQueue(max_depth=10)
        q.push(CommandPrimitive(action=CommandAction.BUILD, target="a"))
        q.push(CommandPrimitive(action=CommandAction.BUILD, target="b"))
        cleared = q.clear()
        assert len(cleared) == 2
        assert q.size == 0

    def test_clear_by_source(self) -> None:
        q = CommandQueue(max_depth=10)
        q.push(
            CommandPrimitive(
                action=CommandAction.BUILD, target="ai", source=CommandSource.AI
            )
        )
        q.push(
            CommandPrimitive(
                action=CommandAction.BUILD, target="human", source=CommandSource.HUMAN
            )
        )
        cleared = q.clear(source=CommandSource.AI)
        assert len(cleared) == 1
        assert cleared[0].target == "ai"
        assert q.size == 1
        assert q.pending[0].target == "human"

    def test_clear_conflicting(self) -> None:
        q = CommandQueue(max_depth=10)
        q.push(
            CommandPrimitive(
                action=CommandAction.BUILD, target="ai_build", source=CommandSource.AI
            )
        )
        q.push(
            CommandPrimitive(
                action=CommandAction.ATTACK, target="ai_attack", source=CommandSource.AI
            )
        )
        q.push(
            CommandPrimitive(
                action=CommandAction.BUILD, target="human_build", source=CommandSource.HUMAN
            )
        )
        cleared = q.clear_conflicting(CommandAction.BUILD)
        assert len(cleared) == 1
        assert cleared[0].target == "ai_build"
        assert q.size == 2

    def test_pending_snapshot(self) -> None:
        q = CommandQueue(max_depth=10)
        q.push(CommandPrimitive(action=CommandAction.BUILD, target="x"))
        snap = q.pending
        q.push(CommandPrimitive(action=CommandAction.BUILD, target="y"))
        assert len(snap) == 1  # snapshot not affected by later push


# --- Tech recipes ---


class TestTechRecipes:
    def test_recipe_keys(self) -> None:
        assert "voidrays" in TECH_RECIPES
        assert "colossi" in TECH_RECIPES
        assert "blink" in TECH_RECIPES

    def test_expand_tech_voidrays(self) -> None:
        cmds = expand_tech("voidrays", CommandSource.HUMAN, game_time=100.0)
        assert len(cmds) == 2
        assert cmds[0].action == CommandAction.BUILD
        assert cmds[0].target == "stargate"
        assert cmds[1].action == CommandAction.BUILD
        assert cmds[1].target == "voidrays"

    def test_expand_tech_colossi(self) -> None:
        cmds = expand_tech("colossi", CommandSource.AI, game_time=50.0)
        assert len(cmds) == 3
        targets = [c.target for c in cmds]
        assert targets == ["robotics_facility", "robotics_bay", "colossi"]

    def test_expand_tech_upgrade(self) -> None:
        cmds = expand_tech("blink", CommandSource.HUMAN, game_time=0.0)
        assert len(cmds) == 2
        assert cmds[0].action == CommandAction.BUILD
        assert cmds[0].target == "twilight_council"
        assert cmds[1].action == CommandAction.UPGRADE
        assert cmds[1].target == "blink"

    def test_expand_tech_unknown_target(self) -> None:
        cmds = expand_tech("unknown_unit", CommandSource.HUMAN, game_time=0.0)
        assert len(cmds) == 1
        assert cmds[0].target == "unknown_unit"

    def test_expand_tech_timestamps(self) -> None:
        cmds = expand_tech("voidrays", CommandSource.HUMAN, game_time=42.0)
        for cmd in cmds:
            assert cmd.timestamp == 42.0

    def test_expand_tech_prereq_higher_priority(self) -> None:
        cmds = expand_tech("voidrays", CommandSource.HUMAN, game_time=0.0)
        assert cmds[0].priority > cmds[1].priority
