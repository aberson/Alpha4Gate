---
name: a4g-ui-test
description: Alpha4Gate wrapper for ui-review-loop. Starts the bot + API + frontend, exercises the command panel via Playwright, and runs three evidence-based reviewers. Invoke as "/a4g-ui-test --problem '...' [--issue N]".
user-invocable: true
---

# Alpha4Gate UI Test

Wrapper around `/ui-review-loop` with Alpha4Gate-specific config: startup command,
dashboard URL, page list, exercise script, and domain context for reviewers.

## When to use

Use this when verifying Alpha4Gate UI behavior end-to-end:
- Command panel submit → WebSocket broadcast → history update
- Dashboard page rendering (Live, Stats, Build Orders, Replays, Decisions, Training)
- Frontend-backend integration bugs that need visual + log evidence

## Invocation

```
/a4g-ui-test --problem "Fix missing WS broadcast for inline-parsed commands" --issue 12
```

| Arg | Required | Default | Description |
|---|---|---|---|
| `--problem` | yes | — | What to fix or verify |
| `--issue` | no | — | GitHub issue number to close on PASS |
| `--max-iter` | no | 2 | Max developer-reviewer iterations |
| `--keep-evidence` | no | false | Preserve evidence directory on PASS |
| `--record-video` | no | false | Enable Playwright video recording |
| `--record-har` | no | false | Enable HAR network recording |

## How it works

### Step 0 — Resolve paths

```bash
PROJECT_DIR="$(cd "$(dirname "$0")/../../../.." && pwd)"   # Alpha4Gate root
SKILL_DIR="$PROJECT_DIR/.claude/skills/a4g-ui-test"
EXERCISE_PATH="$SKILL_DIR/exercises/test_commands.py"
```

All exercise paths are resolved to **absolute paths** before delegation.

### Step 1 — Ensure .gitignore entry

```bash
cd "$PROJECT_DIR"
grep -q '\.ui-review-evidence' .gitignore 2>/dev/null || echo '.ui-review-evidence/' >> .gitignore
```

### Step 2 — Delegate to ui-review-loop

Invoke `/ui-review-loop` with Alpha4Gate config:

```
/ui-review-loop \
  --problem "<user problem>\n\nALPHA4GATE CONTEXT:\n<domain context below>" \
  --start-cmd "bash scripts/live-test.sh" \
  --exercise-cmd "<EXERCISE_PATH absolute>" \
  --ready-url "http://localhost:8765/api/commands/mode" \
  --url "http://localhost:5173" \
  --pages '["http://localhost:5173", "http://localhost:5173/#/stats", "http://localhost:5173/#/build-orders", "http://localhost:5173/#/replays", "http://localhost:5173/#/decisions", "http://localhost:5173/#/training"]' \
  --viewport "1920x1080" \
  --stop-signal "process-group" \
  --issue <if provided> \
  --max-iter <if provided> \
  --keep-evidence <if provided> \
  --record-video <if provided> \
  --record-har <if provided>
```

### Domain context appended to problem statement

The following is appended to `--problem` so all three reviewers understand Alpha4Gate:

```
ALPHA4GATE CONTEXT:
- Dashboard at localhost:5173 has tabs: Live, Stats, Build Orders, Replays, Decisions, Training
- API server at localhost:8765 serves REST + WebSocket endpoints
- WebSocket /ws/game broadcasts game state every ~4 seconds
- WebSocket /ws/commands broadcasts command events (queued, executed, rejected)
- Command Panel: text input (placeholder "Type a command") + Send button, mode dropdown (AI-Assisted, Human Only, Hybrid), Mute Claude button
- Command History: shows submitted commands with status badges (queued, executed, rejected)
- "No commands yet." means the frontend hasn't received any command events
- Backend log patterns: "Cmd OK: <action> <target>" = command executed, "Cmd FAIL: <action> <target>" = execution failed
- Live page shows: game time, minerals, gas, supply, score, state, unit list
- SC2 game is started manually by the user — the skill only starts the bot + API + frontend
```

### Exercise scripts

Three exercise scripts in `exercises/`, each targeting a different UI area:

**`test_commands.py`** (default) — Command panel submit flow:
1. Waits for command input to be visible
2. Types "build stalkers", clicks Send
3. Waits for command to appear in `.command-history-list`
4. Waits 3s for WebSocket status badge updates

**`test_mode_switch.py`** — Mode dropdown cycling:
1. Switches to Human Only, waits 2s
2. Switches to Hybrid, waits 2s
3. Switches back to AI-Assisted, waits 2s

**`test_mute.py`** — Mute toggle verification:
1. Verifies initial button text is "Mute Claude"
2. Clicks to mute, verifies text changes to "Claude Muted"
3. Clicks to unmute, verifies text returns to "Mute Claude"

To use a specific exercise, pass `--exercise-cmd` with its absolute path.
Default (no `--exercise-cmd`) uses `test_commands.py`.

## Prerequisites

- SC2 must be running (the skill cannot start SC2 itself)
- Playwright installed: `uv tool install playwright && playwright install chromium`
- `scripts/live-test.sh` must exist in Alpha4Gate root
