"""Claude NLP interpreter: parse free text into command primitives via Claude Haiku."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from alpha4gate.commands.primitives import (
    CommandAction,
    CommandPrimitive,
    CommandSource,
)

_INTERPRET_PROMPT = (
    "Parse this StarCraft II command into structured actions.\n\n"
    "Valid actions: build, expand, defend, attack, scout, tech, upgrade, rally\n"
    "Valid locations: main, natural, third, fourth, enemy_main, enemy_natural, enemy_third\n\n"
    'Input: "{text}"\n\n'
    "Respond with JSON only — a list of objects:\n"
    '[{{"action": "...", "target": "...", "location": "..." or null, "priority": 1-10}}]\n\n'
    "If the input is not a valid game command, respond with: []"
)

_VALID_ACTIONS = {a.value for a in CommandAction}


class CommandInterpreter:
    """Parse free text into command primitives using Claude Haiku."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-haiku-4-5-20251001",
    ) -> None:
        self._api_key = api_key
        self._model = model

    async def interpret(
        self,
        text: str,
        source: CommandSource,
    ) -> list[CommandPrimitive] | None:
        """Parse free text into primitives using Claude Haiku.

        Returns None on failure (timeout, unparseable, no API key).
        max_tokens=128, timeout=5 seconds.
        """
        if not self._api_key:
            return None

        prompt = _INTERPRET_PROMPT.format(text=text)

        try:
            raw = await asyncio.wait_for(self._call_api(prompt), timeout=5.0)
        except (TimeoutError, Exception):
            return None

        if raw is None:
            return None

        return self._parse_primitives(raw, source)

    async def _call_api(self, prompt: str) -> str | None:
        """Make the API call to Claude Haiku.

        Returns the raw text response, or None on failure.
        """
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=self._api_key)
            message = await client.messages.create(
                model=self._model,
                max_tokens=128,
                messages=[{"role": "user", "content": prompt}],
            )
            block = message.content[0]
            return block.text if hasattr(block, "text") else str(block)
        except Exception:
            return None

    def _parse_primitives(
        self,
        raw: str,
        source: CommandSource,
    ) -> list[CommandPrimitive] | None:
        """Parse raw JSON response into CommandPrimitive list.

        Returns None if JSON is invalid or contains no valid commands.
        """
        cleaned = raw.strip()
        # Strip markdown code fences if present
        if "```" in cleaned:
            parts = cleaned.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("["):
                    cleaned = part
                    break

        try:
            data: Any = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            return None

        if not isinstance(data, list):
            return None

        primitives: list[CommandPrimitive] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            action_str = str(item.get("action", "")).lower()
            if action_str not in _VALID_ACTIONS:
                continue
            target = str(item.get("target", ""))
            location = item.get("location")
            if location is not None:
                location = str(location)
            priority = item.get("priority", 5)
            if not isinstance(priority, int) or not (1 <= priority <= 10):
                priority = 5

            primitives.append(
                CommandPrimitive(
                    action=CommandAction(action_str),
                    target=target,
                    location=location,
                    priority=priority,
                    source=source,
                    id=str(uuid.uuid4()),
                )
            )

        return primitives if primitives else None
