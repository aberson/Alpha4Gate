# UI Verification Phase — Alpha4Gate

## Overview

Autonomous verification of Alpha4Gate's frontend UI during a live SC2 game.
Each step uses `/a4g-ui-test` as the gate: developer agent writes code (if needed),
Playwright captures evidence, and three reviewers (UI, Backend, Frontend) evaluate.

If the UI already works correctly for a given step, the loop passes on iteration 1
with no code changes. If a reviewer finds a bug, the developer agent fixes it and
the loop re-runs (up to `--max-iter`).

## Prerequisites

- SC2 client running (game loaded or in lobby)
- `scripts/live-test.sh` starts bot + API + frontend correctly
- Playwright installed: `uv tool install playwright && playwright install chromium`
- All Phase 1-2 tests passing (`uv run pytest`)

## Orchestration

Run each step sequentially via `/a4g-ui-test`. Stop on BLOCKED.
After all steps PASS, run `/git-update` to commit and push.

```
For each step below:
  1. Invoke /a4g-ui-test --problem "<step problem>" --keep-evidence
  2. If PASS → move to next step
  3. If BLOCKED → stop, report findings, wait for user
  4. After all steps PASS → run quality gates, then /git-update
```

## Quality gates (run after all steps PASS)

```bash
cd Alpha4Gate
uv run pytest --tb=no -q
uv run ruff check .
uv run mypy src
```

---

## Step 1: LiveView renders real-time game data

**Problem:**
Verify that the LiveView component displays live game stats from the WebSocket
/ws/game feed. The page at http://localhost:5173 should show minerals, vespene,
supply_used, supply_cap, game_time, strategic_state, and a unit list — NOT
"Waiting for game data..." or empty placeholders.

If the LiveView shows stale or missing data, fix the WebSocket connection logic
in the frontend. The backend broadcasts game state every ~4 seconds via
`drain_broadcast_queue()` → `/ws/game`.

**Exercise:** Default (test_commands.py exercises the command panel, but
screenshots of all 6 pages capture LiveView state too).

**Acceptance:**
- Initial screenshot of root page shows numeric game stats (minerals > 0)
- No "Waiting for game data..." text visible
- Console log shows WebSocket connected to /ws/game
- Backend log shows no errors

---

## Step 2: Command Panel submit and history update

**Problem:**
Verify the full command submit flow: type "build stalkers" in the command input,
click Send, and confirm the command appears in the Command History feed with a
status badge (queued, parsing, or executed).

The exercise script already does this (fill input → click Send → wait for
`.command-history-list li`). Reviewers should verify:
- The input field accepts text and the Send button is clickable
- After submit, the command appears in history (not "No commands yet.")
- The status badge shows a valid state (queued/parsing/executed)
- The POST /api/commands request succeeds (200 in console/HAR)

If the command doesn't appear in history, fix the WebSocket /ws/commands
broadcast path in the backend or the `onWsMessage` handler in CommandPanel.tsx.

**Acceptance:**
- After-exercise screenshot shows at least one entry in command history
- Status badge is visible (queued, executed, or parsing)
- No JavaScript errors in console log
- Backend log shows command received

---

## Step 3: Command execution round-trip

**Problem:**
Verify that a submitted command progresses through the full pipeline:
queued → executed (or queued → rejected with a reason). The after-exercise
screenshots should show the status badge updated from its initial state.

Focus on the WebSocket /ws/commands event stream. The backend should broadcast
a "queued" event when the command is accepted, then an "executed" or "rejected"
event after the bot processes it. The frontend's `onWsMessage` handler updates
the history entry's status in place.

If status badges stay stuck on "queued" and never transition to "executed",
the likely fix is in `api.py` — ensure `broadcast_command_event()` is called
after command execution with the correct event type.

**Acceptance:**
- Command history shows at least one entry with "executed" status (green badge)
  OR "rejected" with a reason — not permanently stuck on "queued"
- Backend log shows "Cmd OK:" or "Cmd FAIL:" line for the submitted command
- Console log shows /ws/commands messages received
- No unhandled promise rejections

---

## Step 4: Mode switching

**Problem:**
Verify that the command mode dropdown (AI-Assisted / Human Only / Hybrid)
works correctly.

**Exercise:** Use `exercises/test_mode_switch.py` (already written). It cycles
through all three modes: Human Only → Hybrid → AI-Assisted. Pass its absolute
path as `--exercise-cmd` to `/a4g-ui-test`.

The PUT /api/commands/mode endpoint should return the new mode. The frontend's
`handleModeChange` updates local state on success.

**Acceptance:**
- Mode dropdown is visible in screenshots with current mode selected
- After-exercise screenshot shows mode reverted to AI-Assisted
- No console errors from PUT /api/commands/mode
- Backend log shows no errors on mode change

---

## Step 5: Mute toggle and settings panel

**Problem:**
Verify that the "Mute Claude" button toggles correctly and the Settings panel
(Claude interval slider, lockout duration slider) is accessible.

**Exercise:** Use `exercises/test_mute.py` (already written). It clicks Mute,
verifies "Claude Muted" label, clicks again, verifies "Mute Claude" label.
Pass its absolute path as `--exercise-cmd` to `/a4g-ui-test`.

The PUT /api/commands/settings endpoint handles mute state. The frontend's
`toggleMute` updates local state on 200 response.

**Acceptance:**
- Mute button visible in screenshots with correct label
- If exercise written: after-exercise shows toggle state changed
- Settings button visible and functional
- No errors in console or backend logs

---

## Completion

After all 5 steps PASS:

```
UI Verification Phase — Complete
  Step 1 (LiveView data):      PASS
  Step 2 (Command submit):     PASS
  Step 3 (Execution pipeline): PASS
  Step 4 (Mode switching):     PASS
  Step 5 (Mute toggle):        PASS

Quality gates:
  pytest:    PASS
  ruff:      PASS
  mypy:      PASS

Next: /git-update to commit any fixes and push.
```
