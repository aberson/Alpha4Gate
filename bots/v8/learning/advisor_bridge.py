"""Training advisor bridge: thread-safe Claude advisor for multi-game training.

Runs the Claude CLI in a dedicated daemon thread with its own asyncio event
loop, communicating with game threads via thread-safe queues.  This avoids
the CancelledError crash that occurs when asyncio tasks created in one game
thread's event loop are accessed from a subsequent game thread's loop.

Also provides situational lookup into the Protoss guiding principles file,
injecting only relevant strategic sections into each advisor prompt.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bots.v8.claude_advisor import (
    AdvisorResponse,
    RateLimiter,
    build_prompt,
    parse_response,
)

_log = logging.getLogger(__name__)

# Default path to the principles file (relative to project root).
_DEFAULT_PRINCIPLES_PATH = Path("documentation/sc2/protoss/guiding-principles.md")

# Maximum lines of principles text injected into a single prompt.
_MAX_PRINCIPLES_LINES = 150


# ---------------------------------------------------------------------------
# Situational principles lookup
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Condition:
    """A game-state condition that maps to keyword searches."""

    check: str  # callable-style descriptor for debugging
    keywords: tuple[str, ...]


# Each condition is a (lambda over state_dict, keywords) pair.  The lambda
# receives the full snapshot-as-dict and returns True when the condition
# holds.  Keywords are case-insensitive substrings matched against section
# text.
_CONDITIONS: list[tuple[Any, tuple[str, ...]]] = [
    # High minerals — spending problem
    (lambda s: s.get("minerals", 0) > 800, ("resource", "spending", "idle")),
    # Low army late — production problem
    (
        lambda s: s.get("army_supply", 0) < 5 and s.get("game_time_seconds", 0) > 180,
        ("army", "production", "build"),
    ),
    # Enemy at the door
    (lambda s: s.get("enemy_army_near_base", False), ("defend", "survival", "threat")),
    # No expansion yet mid-game
    (
        lambda s: s.get("base_count", 1) < 2 and s.get("game_time_seconds", 0) > 240,
        ("expand", "economy", "base"),
    ),
    # Supply blocked
    (
        lambda s: (
            s.get("supply_cap", 200) > 0
            and s.get("supply_used", 0) / max(s.get("supply_cap", 200), 1) > 0.9
        ),
        ("supply", "block"),
    ),
    # Low worker count
    (lambda s: s.get("worker_count", 16) < 16, ("worker", "probe", "economy")),
    # Large army, could attack
    (
        lambda s: (
            s.get("army_supply", 0) > 20
            and s.get("army_supply", 0)
            > s.get("enemy_army_supply_visible", 0) * 1.3
        ),
        ("attack", "timing", "fight"),
    ),
    # Early game
    (
        lambda s: s.get("game_time_seconds", 0) < 180,
        ("opening", "scout", "build order"),
    ),
]


class PrinciplesLookup:
    """In-memory index over the Protoss guiding principles document.

    Splits the file into sections by ``## N.`` headers, then matches
    sections by keyword when queried with a game-state dict.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._sections: list[tuple[str, str]] = []  # (header, body)
        file_path = path or _DEFAULT_PRINCIPLES_PATH
        if not file_path.exists():
            _log.warning("Principles file not found at %s", file_path)
            return
        text = file_path.read_text(encoding="utf-8")
        self._parse_sections(text)
        _log.info(
            "PrinciplesLookup: loaded %d sections from %s",
            len(self._sections),
            file_path,
        )

    def _parse_sections(self, text: str) -> None:
        """Split markdown into (header, body) tuples by ``## N.`` pattern."""
        # Match lines like "## 1. Core Strategic Objective"
        pattern = re.compile(r"^(## \d+\..*)$", re.MULTILINE)
        parts = pattern.split(text)
        # parts alternates: [preamble, header1, body1, header2, body2, ...]
        i = 1  # skip preamble
        while i < len(parts) - 1:
            header = parts[i].strip()
            body = parts[i + 1].strip()
            self._sections.append((header, body))
            i += 2

    def lookup(self, state: dict[str, Any]) -> str:
        """Return relevant principle excerpts for the current game state.

        Scans ``state`` against condition checks, collects matching keyword
        sets, greps sections for those keywords, and returns a deduplicated
        block of text capped at ``_MAX_PRINCIPLES_LINES`` lines.
        """
        if not self._sections:
            return ""

        # Collect keywords from all matching conditions
        keywords: set[str] = set()
        for check_fn, kws in _CONDITIONS:
            try:
                if check_fn(state):
                    keywords.update(kws)
            except Exception:
                continue  # malformed state — skip this condition

        if not keywords:
            return ""

        # Match sections that contain any keyword (case-insensitive)
        matched: list[str] = []
        seen_headers: set[str] = set()
        for header, body in self._sections:
            combined = (header + "\n" + body).lower()
            if any(kw.lower() in combined for kw in keywords):
                if header not in seen_headers:
                    seen_headers.add(header)
                    matched.append(f"{header}\n{body}")

        if not matched:
            return ""

        # Cap at _MAX_PRINCIPLES_LINES
        result_lines: list[str] = []
        for block in matched:
            for line in block.split("\n"):
                if len(result_lines) >= _MAX_PRINCIPLES_LINES:
                    break
                result_lines.append(line)
            if len(result_lines) >= _MAX_PRINCIPLES_LINES:
                break

        return "\n".join(result_lines)

    @property
    def section_count(self) -> int:
        """Number of parsed sections."""
        return len(self._sections)


# ---------------------------------------------------------------------------
# Training advisor bridge
# ---------------------------------------------------------------------------


@dataclass
class _AdvisorRequest:
    """Internal request envelope."""

    prompt: str
    game_time: float


class TrainingAdvisorBridge:
    """Thread-safe Claude advisor for multi-game RL training.

    Runs the Claude CLI subprocess in a dedicated daemon thread with its
    own asyncio event loop.  Game threads submit prompts via
    ``submit_request()`` and poll results via ``poll_response()``.
    The bridge's event loop never dies between games, avoiding the
    CancelledError that plagued the original ClaudeAdvisor in training.
    """

    def __init__(
        self,
        model: str = "sonnet",
        rate_limit_seconds: float = 60.0,
        principles_path: Path | None = None,
    ) -> None:
        self._model = model
        self._rate_limiter = RateLimiter(rate_limit_seconds)
        self._principles = PrinciplesLookup(principles_path)
        self._last_response: AdvisorResponse | None = None

        # Thread-safe communication
        self._request_queue: queue.Queue[_AdvisorRequest | None] = queue.Queue()
        self._response_queue: queue.Queue[AdvisorResponse] = queue.Queue()

        # Start the advisor thread
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(
            target=self._run_loop, name="advisor-bridge", daemon=True
        )
        self._thread.start()
        _log.info(
            "TrainingAdvisorBridge: started (model=%s, rate_limit=%.0fs)",
            model,
            rate_limit_seconds,
        )

    @property
    def last_response(self) -> AdvisorResponse | None:
        """Most recent successful advisor response."""
        return self._last_response

    @property
    def principles(self) -> PrinciplesLookup:
        """The principles lookup instance (for prompt construction)."""
        return self._principles

    def submit_request(self, prompt: str, game_time: float) -> bool:
        """Submit an advice request (non-blocking).

        Returns True if the request was queued, False if rate-limited.
        """
        if not self._rate_limiter.can_call(game_time):
            return False
        self._rate_limiter.record_call(game_time)
        self._request_queue.put(_AdvisorRequest(prompt=prompt, game_time=game_time))
        _log.info("Advisor bridge: request queued at game_time=%.1f", game_time)
        return True

    def poll_response(self) -> AdvisorResponse | None:
        """Check for a completed response (non-blocking).

        Returns the AdvisorResponse if one is ready, None otherwise.
        Updates ``last_response`` on success.
        """
        try:
            response = self._response_queue.get_nowait()
            self._last_response = response
            _log.info(
                "Advisor bridge: response collected, %d commands",
                len(response.commands),
            )
            return response
        except queue.Empty:
            return None

    def shutdown(self) -> None:
        """Stop the advisor thread cleanly."""
        _log.info("Advisor bridge: shutting down")
        self._request_queue.put(None)  # poison pill
        self._thread.join(timeout=30)
        if self._thread.is_alive():
            _log.warning("Advisor bridge: thread did not exit within 30s")

    # -- internal --

    def _run_loop(self) -> None:
        """Entry point for the advisor daemon thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._process_requests())
        except Exception:
            _log.exception("Advisor bridge: event loop crashed")
        finally:
            self._loop.close()

    async def _process_requests(self) -> None:
        """Process requests from the queue until shutdown."""
        while True:
            # Use run_in_executor to do blocking queue.get without
            # stalling the event loop for other tasks.
            request = await self._loop.run_in_executor(  # type: ignore[union-attr]
                None, self._request_queue.get
            )
            if request is None:
                _log.info("Advisor bridge: received shutdown signal")
                break

            response = await self._call_api(request.prompt)
            if response is not None:
                self._response_queue.put(response)

    async def _call_api(self, prompt: str) -> AdvisorResponse | None:
        """Call the Claude CLI in print mode.

        Mirrors ``ClaudeAdvisor._call_api()`` but runs in the bridge's
        own event loop, isolated from game threads.
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
                    "Advisor bridge CLI failed (rc=%d): %s",
                    proc.returncode,
                    stderr.decode(errors="replace").strip(),
                )
                return None
            text = stdout.decode(errors="replace").strip()
            if not text:
                _log.warning("Advisor bridge CLI returned empty response")
                return None
            response = parse_response(text)
            _log.info(
                "Advisor bridge: response received, %d commands",
                len(response.commands),
            )
            return response
        except Exception:
            _log.exception("Advisor bridge CLI call failed")
            return None
        finally:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()


def build_training_prompt(
    state: dict[str, Any],
    principles: PrinciplesLookup,
) -> str:
    """Build a training advisor prompt with situational principles.

    Wraps ``claude_advisor.build_prompt()`` but prepends relevant
    principles excerpts based on the current game state.
    """
    game_time_seconds = state.get("game_time_seconds", 0.0)
    game_time_str = (
        f"{int(game_time_seconds // 60)}:{int(game_time_seconds % 60):02d}"
    )

    base_prompt = build_prompt(
        game_time=game_time_str,
        strategic_state=state.get("current_state", "UNKNOWN"),
        minerals=int(state.get("minerals", 0)),
        vespene=int(state.get("vespene", 0)),
        supply_used=int(state.get("supply_used", 0)),
        supply_cap=int(state.get("supply_cap", 0)),
        army_composition=f"{state.get('army_supply', 0)} supply",
        enemy_composition=f"{state.get('enemy_army_supply_visible', 0)} supply visible",
        recent_decisions=state.get("current_state", ""),
        build_order_name="4gate",
        build_step=0,
        total_steps=1,
    )

    # Inject relevant principles
    principles_text = principles.lookup(state)
    if principles_text:
        return (
            "## Relevant Protoss Guiding Principles\n"
            f"{principles_text}\n\n"
            f"{base_prompt}"
        )
    return base_prompt
