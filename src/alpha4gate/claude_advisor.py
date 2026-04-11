"""Claude CLI advisor: prompt construction, response parsing, rate limiting."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from alpha4gate.audit_log import record_decision
from alpha4gate.commands.primitives import (
    CommandAction,
    CommandPrimitive,
    CommandSource,
)

if TYPE_CHECKING:
    from alpha4gate.web_socket import ConnectionManager

_log = logging.getLogger(__name__)

PROMPT_TEMPLATE = (
    "You are a StarCraft II Protoss strategic advisor. "
    "Analyze the current game state and issue commands.\n\n"
    "Game time: {game_time}\n"
    "Strategic state: {strategic_state}\n"
    "Resources: {minerals} minerals, {vespene} gas, {supply_used}/{supply_cap} supply\n"
    "Army: {army_composition}\n"
    "Enemy (known): {enemy_composition}\n"
    "Recent decisions: {recent_decisions}\n"
    "Current build order: {build_order_name} (step {build_step}/{total_steps})\n\n"
    "Valid actions: build, expand, defend, attack, scout, tech, upgrade, rally\n"
    "Valid locations: main, natural, third, fourth, enemy_main, enemy_natural, enemy_third\n\n"
    "Respond with JSON only:\n"
    '{{"commands": [{{"action": "...", "target": "...", "location": "..." or null, '
    '"priority": 1-10}}], '
    '"suggestion": "one sentence, actionable", '
    '"urgency": "low|medium|high", '
    '"reasoning": "one sentence"}}'
)

_VALID_ACTIONS = {a.value for a in CommandAction}


@dataclass
class AdvisorResponse:
    """Parsed response from Claude advisor."""

    suggestion: str
    urgency: str  # "low", "medium", "high"
    reasoning: str
    raw: str = ""
    commands: list[CommandPrimitive] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "suggestion": self.suggestion,
            "urgency": self.urgency,
            "reasoning": self.reasoning,
        }


def build_prompt(
    game_time: str,
    strategic_state: str,
    minerals: int,
    vespene: int,
    supply_used: int,
    supply_cap: int,
    army_composition: str,
    enemy_composition: str,
    recent_decisions: str,
    build_order_name: str,
    build_step: int,
    total_steps: int,
) -> str:
    """Construct the Claude advisor prompt from game state.

    Args:
        game_time: Formatted game time string (e.g., "5:30").
        strategic_state: Current strategic state name.
        minerals: Current mineral count.
        vespene: Current vespene count.
        supply_used: Current supply used.
        supply_cap: Current supply cap.
        army_composition: Human-readable army composition string.
        enemy_composition: Human-readable enemy composition string.
        recent_decisions: Summary of recent decision engine transitions.
        build_order_name: Name of the active build order.
        build_step: Current step index in the build order.
        total_steps: Total steps in the build order.

    Returns:
        Formatted prompt string.
    """
    return PROMPT_TEMPLATE.format(
        game_time=game_time,
        strategic_state=strategic_state,
        minerals=minerals,
        vespene=vespene,
        supply_used=supply_used,
        supply_cap=supply_cap,
        army_composition=army_composition,
        enemy_composition=enemy_composition,
        recent_decisions=recent_decisions,
        build_order_name=build_order_name,
        build_step=build_step,
        total_steps=total_steps,
    )


def parse_response(text: str) -> AdvisorResponse:
    """Parse Claude's JSON response into an AdvisorResponse.

    Args:
        text: Raw text response from Claude.

    Returns:
        Parsed AdvisorResponse. Falls back to suggestion=text if JSON parsing fails.
    """
    cleaned = text.strip()

    # Try to extract JSON from the response
    # Claude sometimes wraps in ```json ... ```
    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                cleaned = part
                break

    try:
        data = json.loads(cleaned)
        commands = _extract_commands(data.get("commands", []))
        return AdvisorResponse(
            suggestion=data.get("suggestion", ""),
            urgency=data.get("urgency", "low"),
            reasoning=data.get("reasoning", ""),
            raw=text,
            commands=commands,
        )
    except (json.JSONDecodeError, AttributeError):
        _log.warning("Advisor: unparseable response: %.100s", text)
        return AdvisorResponse(
            suggestion=text.strip(),
            urgency="low",
            reasoning="(unparseable response)",
            raw=text,
        )


def _extract_commands(raw_commands: Any) -> list[CommandPrimitive]:
    """Extract CommandPrimitive list from parsed JSON commands array.

    Invalid entries are silently skipped.
    """
    if not isinstance(raw_commands, list):
        return []

    primitives: list[CommandPrimitive] = []
    for item in raw_commands:
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
                source=CommandSource.AI,
                id=str(uuid.uuid4()),
            )
        )
    return primitives


class RateLimiter:
    """Simple rate limiter: at most one call per interval (in game-seconds)."""

    def __init__(self, interval_game_seconds: float = 30.0) -> None:
        self._interval = interval_game_seconds
        self._last_call_game_time: float = -interval_game_seconds

    @property
    def interval(self) -> float:
        """The minimum interval between calls in game-seconds."""
        return self._interval

    def can_call(self, game_time_seconds: float) -> bool:
        """Check if enough game-time has passed for a new call.

        Args:
            game_time_seconds: Current game time in seconds.

        Returns:
            True if a call is allowed.
        """
        return game_time_seconds - self._last_call_game_time >= self._interval

    def set_interval(self, interval: float) -> None:
        """Update the minimum interval between calls.

        Args:
            interval: New interval in game-seconds.
        """
        self._interval = interval

    def record_call(self, game_time_seconds: float) -> None:
        """Record that a call was made at the given game time.

        Args:
            game_time_seconds: Game time when the call was made.
        """
        self._last_call_game_time = game_time_seconds


class ClaudeAdvisor:
    """Async Claude CLI advisor for strategic suggestions.

    Fires CLI calls as asyncio tasks (fire-and-forget). Results are consumed
    on the next on_step() iteration. If Claude is unavailable, the bot
    continues with rule-based decisions only.

    Auth is handled by the ``claude`` CLI itself (OAuth token or API key).
    """

    def __init__(
        self,
        model: str = "sonnet",
        rate_limit_seconds: float = 30.0,
        data_dir: Path | None = None,
        ws_manager: ConnectionManager | None = None,
    ) -> None:
        self._model = model
        self._rate_limiter = RateLimiter(rate_limit_seconds)
        self._pending_task: asyncio.Task[AdvisorResponse | None] | None = None
        self._last_response: AdvisorResponse | None = None
        self._enabled = True
        # Optional audit wiring: when both are set, successful responses are
        # recorded to ``data_dir/decision_audit.json`` and broadcast via
        # ``/ws/decisions``. Left unset in existing tests so they keep
        # passing without needing a temp dir.
        self._data_dir = data_dir
        self._ws_manager = ws_manager
        # Last request's game_time, captured in request_advice and consumed
        # when collect_response pairs the response with its request.
        self._pending_game_time: float | None = None
        _log.info("ClaudeAdvisor: enabled=%s model=%s", self._enabled, self._model)

    @property
    def enabled(self) -> bool:
        """Whether the advisor is enabled."""
        return self._enabled

    @property
    def last_response(self) -> AdvisorResponse | None:
        """The most recent advisor response."""
        return self._last_response

    @property
    def has_pending(self) -> bool:
        """Whether there's an in-flight API call."""
        return self._pending_task is not None and not self._pending_task.done()

    def request_advice(self, prompt: str, game_time_seconds: float) -> bool:
        """Fire an async advice request if rate limit allows.

        Args:
            prompt: The constructed prompt to send to Claude.
            game_time_seconds: Current game time for rate limiting.

        Returns:
            True if a request was fired, False if rate-limited or disabled.
        """
        if not self._enabled:
            _log.debug("Advisor: disabled")
            return False
        if not self._rate_limiter.can_call(game_time_seconds):
            _log.debug("Advisor: rate-limited at game_time=%.1f", game_time_seconds)
            return False
        if self.has_pending:
            _log.debug("Advisor: pending task in-flight")
            return False

        self._rate_limiter.record_call(game_time_seconds)
        self._pending_game_time = game_time_seconds
        self._pending_task = asyncio.create_task(self._call_api(prompt))
        _log.info("Advisor: request fired at game_time=%.1f", game_time_seconds)
        return True

    def collect_response(self) -> AdvisorResponse | None:
        """Check if the pending task is done and return the response.

        Returns:
            AdvisorResponse if available, None otherwise.
        """
        if self._pending_task is None:
            return None
        if not self._pending_task.done():
            return None

        try:
            result = self._pending_task.result()
        except Exception:
            _log.exception("Advisor: task raised")
            result = None

        self._pending_task = None
        pending_game_time = self._pending_game_time
        self._pending_game_time = None
        if result is not None:
            _log.debug("Advisor: collected %d commands", len(result.commands))
            self._last_response = result
            self._record_successful_decision(result, pending_game_time)
        return result

    def _record_successful_decision(
        self,
        response: AdvisorResponse,
        game_time: float | None,
    ) -> None:
        """Append a successful advisor response to the decision audit log.

        No-op unless both ``data_dir`` and ``ws_manager`` were passed to the
        constructor -- existing tests and call sites that don't wire audit
        remain unaffected.
        """
        if self._data_dir is None or self._ws_manager is None:
            return
        try:
            decision: dict[str, Any] = {
                "timestamp": datetime.now(UTC).isoformat(),
                "source": "claude_advisor",
                "model": self._model,
                "game_time": game_time,
                "request_summary": (
                    f"bot state at game_time={game_time:.1f}"
                    if game_time is not None
                    else "bot state (game_time unknown)"
                ),
                "response_commands": [
                    {
                        "action": cmd.action.value,
                        "target": cmd.target,
                        "location": cmd.location,
                        "priority": cmd.priority,
                        "id": cmd.id,
                    }
                    for cmd in response.commands
                ],
                "suggestion": response.suggestion,
                "urgency": response.urgency,
                "reasoning": response.reasoning,
            }
            record_decision(self._data_dir, self._ws_manager, decision)
        except Exception:
            # Audit logging must never break the game loop.
            _log.exception("Advisor: failed to record decision audit entry")

    async def _call_api(self, prompt: str) -> AdvisorResponse | None:
        """Call the Claude CLI in print mode.

        Args:
            prompt: The prompt to send.

        Returns:
            Parsed AdvisorResponse, or None on failure.
        """
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude",
                "-p",
                prompt,
                "--model",
                self._model,
                "--output-format",
                "text",
                "--no-session-persistence",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                _log.error(
                    "Advisor CLI failed (rc=%d): %s",
                    proc.returncode,
                    stderr.decode(errors="replace").strip(),
                )
                return None
            text = stdout.decode(errors="replace").strip()
            if not text:
                _log.warning("Advisor CLI returned empty response")
                return None
            response = parse_response(text)
            _log.info("Advisor: response received, %d commands", len(response.commands))
            return response
        except Exception:
            _log.exception("Advisor CLI call failed")
            return None
        finally:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
