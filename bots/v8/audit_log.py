"""Decision audit log writer: persists decisions and broadcasts to /ws/decisions.

This module exists because the Decision Log feature (frontend
``DecisionQueue.tsx`` + ``GET /api/decision-log`` + ``/ws/decisions``) was dead
on arrival in Phase 4.5: the endpoint and WebSocket existed, but nothing in
``src/`` ever wrote ``data/decision_audit.json`` or called
``broadcast_decision``. Phase 4.6 Step 3 starts narrow -- only the Claude
advisor's successful responses feed the audit log; rule-based decision engine
and PPO action audits are deferred to follow-up issues.

The JSON schema matches what ``api.py`` reads: ``{"entries": [decision, ...]}``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bots.v8.web_socket import ConnectionManager

_log = logging.getLogger(__name__)

DECISION_AUDIT_FILENAME = "decision_audit.json"

# Strong references to in-flight broadcast tasks so the event loop (which only
# holds weak references to tasks) can't GC them mid-execution. Per the asyncio
# docs: "Save a reference to the result of this function, to avoid a task
# disappearing mid-execution." The set is the textbook pattern -- tasks are
# added on creation and removed via ``add_done_callback`` so it never grows
# unbounded.
_pending_broadcasts: set[asyncio.Task[None]] = set()


def _on_broadcast_done(task: asyncio.Task[None]) -> None:
    """Done-callback for broadcast tasks.

    Releases the strong reference in ``_pending_broadcasts`` and surfaces any
    exception raised by ``broadcast_decision`` via ``_log.error``. Without the
    explicit ``task.exception()`` call, failures would only appear as a
    confusing "Task exception was never retrieved" warning at GC time.
    """
    _pending_broadcasts.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _log.error("broadcast_decision task failed: %s", exc, exc_info=exc)


def _read_entries(path: Path) -> list[dict[str, Any]]:
    """Read existing audit entries from JSON file, or return empty list.

    Self-heals if the file is corrupt (invalid JSON from a partial write,
    concurrent-writer race, or manual edit gone wrong). Mirrors the pattern
    in ``PromotionLogger._read_history`` (Phase 4.5 Step 4 iter-2): without
    this, a single corrupt byte would silently swallow every subsequent
    ``record_decision`` call -- every new entry would be lost without signal.

    On corruption we rotate the bad file out of the way (preserving it for
    forensics) and return an empty list so the next write starts fresh.
    """
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        corrupt_path = path.with_name(f"{path.stem}.corrupt.{suffix}.json")
        try:
            path.rename(corrupt_path)
        except OSError:
            _log.exception(
                "Failed to rotate corrupt decision audit file %s", path
            )
        _log.warning(
            "decision_audit.json at %s was corrupt (%s); "
            "rotated to %s and starting fresh",
            path,
            exc,
            corrupt_path,
        )
        return []

    if not isinstance(data, dict):
        _log.warning(
            "decision_audit.json at %s had unexpected top-level type %s; "
            "starting fresh",
            path,
            type(data).__name__,
        )
        return []
    entries = data.get("entries", [])
    if not isinstance(entries, list):
        _log.warning(
            "decision_audit.json at %s had non-list 'entries'; starting fresh",
            path,
        )
        return []
    return entries


def _write_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    """Write entries list to JSON file (pretty-printed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": entries}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _apply_ui_aliases(decision: dict[str, Any]) -> dict[str, Any]:
    """Add UI-expected field aliases to a decision dict (Phase 4.8 Fix A).

    The Decision Log table in ``frontend/src/components/DecisionQueue.tsx``
    was designed against an earlier schema built for DecisionEngine state
    transitions: ``{game_step, from_state, to_state, reason, claude_advice}``.
    Phase 4.6 #77 wired the Claude advisor to call ``record_decision`` but
    the advisor's dict has a completely different shape: ``{timestamp,
    source, model, game_time, request_summary, response_commands,
    suggestion, urgency, reasoning}``. Zero field-name overlap means the UI
    rendered each advisor broadcast as a row with all columns blank (see
    aberson/Alpha4Gate#90 root cause analysis on 2026-04-11).

    This helper adds the UI-expected keys as aliases, mapped from the
    advisor fields. Original keys are preserved so any future consumer that
    wants the rich advisor payload (full reasoning, all response commands,
    urgency, etc.) still has it. Existing aliases are NOT overwritten --
    when a real state-transition producer eventually writes proper
    ``game_step``/``from_state`` values, they pass through unchanged.

    Mapping, where each right-hand-side key is the aliased source:

    - ``game_step``    <- int(game_time) truncated to integer seconds
    - ``from_state``   <- source (e.g. ``"claude_advisor"``)
    - ``to_state``     <- first ``response_commands[0].action`` if present,
                         else ``urgency``, else ``"advise"``
    - ``reason``       <- ``reasoning`` > ``request_summary`` > ``""``
    - ``claude_advice`` <- ``suggestion`` > ``""``

    Returns the same dict (mutated in place) for caller convenience.
    """
    # game_step: truncate game_time seconds to an integer tick count.
    if "game_step" not in decision:
        game_time = decision.get("game_time")
        if isinstance(game_time, int | float):
            decision["game_step"] = int(game_time)

    # from_state: use the producer source as the "from" label.
    if "from_state" not in decision:
        source = decision.get("source")
        if isinstance(source, str) and source:
            decision["from_state"] = source

    # to_state: prefer the first command's action, then urgency, then
    # a constant "advise" fallback so the column is never empty when the
    # producer is the advisor.
    if "to_state" not in decision:
        commands = decision.get("response_commands")
        first_action: str | None = None
        if isinstance(commands, list) and commands:
            first = commands[0]
            if isinstance(first, dict):
                action = first.get("action")
                if isinstance(action, str) and action:
                    first_action = action
        if first_action is not None:
            decision["to_state"] = first_action
        else:
            urgency = decision.get("urgency")
            if isinstance(urgency, str) and urgency:
                decision["to_state"] = urgency
            else:
                decision["to_state"] = "advise"

    # reason: prefer the structured reasoning, fall back to request_summary.
    if "reason" not in decision:
        reasoning = decision.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            decision["reason"] = reasoning
        else:
            request_summary = decision.get("request_summary")
            if isinstance(request_summary, str) and request_summary:
                decision["reason"] = request_summary
            else:
                decision["reason"] = ""

    # claude_advice: the short human-readable suggestion string.
    if "claude_advice" not in decision:
        suggestion = decision.get("suggestion")
        if isinstance(suggestion, str) and suggestion:
            decision["claude_advice"] = suggestion
        else:
            decision["claude_advice"] = ""

    return decision


def record_decision(
    data_dir: Path,
    ws_manager: ConnectionManager | None,
    decision: dict[str, Any],
) -> None:
    """Persist a decision record and broadcast it via ``/ws/decisions``.

    Args:
        data_dir: Root data directory. The audit file is written to
            ``data_dir / decision_audit.json`` -- the same path
            ``api.py:get_decision_log`` reads, so API and producer agree.
        ws_manager: Optional WebSocket connection manager. When provided,
            the decision is scheduled for broadcast to live dashboard
            clients via ``ws_manager.broadcast_decision``. When ``None``,
            only the file write occurs (useful in tests or when running
            without the API server).
        decision: Serialisable decision dict. The file writer appends it
            to the ``entries`` list verbatim.

    Behavior:
        - Bootstraps ``decision_audit.json`` if it does not exist.
        - Self-heals corrupt JSON (rotates to
          ``decision_audit.corrupt.<ts>.json`` and starts fresh) using the
          same pattern as ``PromotionLogger._read_history``.
        - Schedules ``broadcast_decision`` as an asyncio task when a running
          event loop is available. If no loop is running (e.g. sync test
          context), the broadcast is skipped silently -- the file write is
          still durable and the next API poll will surface the entry.
    """
    # Phase 4.8 Fix A: add UI-expected aliases (game_step/from_state/
    # to_state/reason/claude_advice) so DecisionQueue.tsx can render the
    # Claude advisor entries without a UI schema change. Original fields
    # are preserved; see ``_apply_ui_aliases`` for the full mapping.
    _apply_ui_aliases(decision)

    path = data_dir / DECISION_AUDIT_FILENAME
    entries = _read_entries(path)
    entries.append(decision)
    _write_entries(path, entries)

    if ws_manager is None:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop -- file write already succeeded, skip broadcast.
        return
    task = loop.create_task(ws_manager.broadcast_decision(decision))
    # Hold a strong reference so the task isn't GC'd mid-execution (the loop
    # only weakly references tasks). ``_on_broadcast_done`` clears it and
    # surfaces any exception via ``_log.error``.
    _pending_broadcasts.add(task)
    task.add_done_callback(_on_broadcast_done)
