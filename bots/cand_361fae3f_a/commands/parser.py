from __future__ import annotations

import re
import uuid

from bots.cand_361fae3f_a.commands.primitives import CommandAction, CommandPrimitive, CommandSource

# Pattern: <action> [target] [at|to|from <location>]
_PATTERN = re.compile(
    r"^(?P<action>build|expand|defend|attack|scout|tech|upgrade|rally)"
    r"(?:\s+(?P<target>[a-z0-9_]+))?"
    r"(?:\s+(?:at|to|from)\s+(?P<location>[a-z0-9_]+))?$",
    re.IGNORECASE,
)


class StructuredParser:
    """Parse simple structured commands into CommandPrimitives."""

    def parse(self, text: str, source: CommandSource) -> list[CommandPrimitive] | None:
        """Parse a text command into primitives. Returns None if no match."""
        text = text.strip()
        if not text:
            return None

        match = _PATTERN.match(text)
        if not match:
            return None

        action_str = match.group("action").lower()
        target = match.group("target") or ""
        location = match.group("location")

        try:
            action = CommandAction(action_str)
        except ValueError:
            return None

        cmd = CommandPrimitive(
            action=action,
            target=target,
            location=location,
            source=source,
            id=str(uuid.uuid4()),
        )
        return [cmd]
