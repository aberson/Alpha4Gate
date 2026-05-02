from __future__ import annotations

import uuid

from bots.v11.commands.primitives import CommandAction, CommandPrimitive, CommandSource

TECH_RECIPES: dict[str, list[str]] = {
    "voidrays": ["stargate"],
    "colossi": ["robotics_facility", "robotics_bay"],
    "high_templar": ["twilight_council", "templar_archives"],
    "dark_templar": ["twilight_council", "dark_shrine"],
    "blink": ["twilight_council"],
    "charge": ["twilight_council"],
    "phoenix": ["stargate"],
    "carrier": ["stargate", "fleet_beacon"],
    "tempest": ["stargate", "fleet_beacon"],
    "disruptor": ["robotics_facility", "robotics_bay"],
    "archon": ["twilight_council", "templar_archives"],
}


def expand_tech(
    target: str, source: CommandSource, game_time: float
) -> list[CommandPrimitive]:
    """Expand a tech target into prerequisite build commands plus the final tech/build command."""
    prerequisites = TECH_RECIPES.get(target, [])
    commands: list[CommandPrimitive] = []

    for prereq in prerequisites:
        commands.append(
            CommandPrimitive(
                action=CommandAction.BUILD,
                target=prereq,
                priority=6,
                source=source,
                id=str(uuid.uuid4()),
                timestamp=game_time,
            )
        )

    # Final command: build the target unit/research
    action = CommandAction.UPGRADE if target in ("blink", "charge") else CommandAction.BUILD
    commands.append(
        CommandPrimitive(
            action=action,
            target=target,
            priority=5,
            source=source,
            id=str(uuid.uuid4()),
            timestamp=game_time,
        )
    )

    return commands
