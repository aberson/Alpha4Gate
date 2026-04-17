from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class CommandAction(StrEnum):
    BUILD = "build"
    EXPAND = "expand"
    DEFEND = "defend"
    ATTACK = "attack"
    SCOUT = "scout"
    TECH = "tech"
    UPGRADE = "upgrade"
    RALLY = "rally"


class CommandSource(StrEnum):
    AI = "ai"
    HUMAN = "human"


class CommandMode(StrEnum):
    AI_ASSISTED = "ai_assisted"
    HUMAN_ONLY = "human_only"
    HYBRID_CMD = "hybrid_cmd"


@dataclass
class CommandPrimitive:
    action: CommandAction
    target: str
    location: str | None = None
    priority: int = 5
    source: CommandSource = CommandSource.HUMAN
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = 0.0
    ttl: float = 60.0


@dataclass
class CommandSettings:
    mode: CommandMode = CommandMode.AI_ASSISTED
    claude_interval: float = 30.0
    lockout_duration: float = 5.0
    muted: bool = False


_settings: CommandSettings | None = None


def get_command_settings() -> CommandSettings:
    global _settings
    if _settings is None:
        _settings = CommandSettings()
    return _settings


def filter_executable(
    commands: list[CommandPrimitive], mode: CommandMode
) -> list[CommandPrimitive]:
    """Return commands that should execute under the given mode.

    In HUMAN_ONLY mode, AI-sourced commands are dropped.
    All other modes pass every command through.
    """
    if mode != CommandMode.HUMAN_ONLY:
        return commands
    return [c for c in commands if c.source != CommandSource.AI]
