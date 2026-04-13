---
name: a4g-dashboard-check
description: Screenshot every Alpha4Gate dashboard tab using Playwright and review for issues. Detects system state (fresh start, SC2 running, advised run active) and adapts review criteria accordingly. Invoke as "/a4g-dashboard-check".
user-invocable: true
argument: No arguments required. Optional: `--tabs <comma-separated>` to check specific tabs only (e.g. `--tabs stats,training`).
---

# Alpha4Gate Dashboard Check

Screenshots every dashboard tab using Playwright headless Chromium, then reviews each
screenshot for visual issues, errors, stale data, or offline indicators. Automatically
detects the current system state and applies the correct review criteria for that state.

## When to use

- After a machine restart to verify the dashboard is healthy
- During or after an `/improve-bot-advised` run to check all tabs
- When the user reports UI issues and you need to see what they see
- Quick visual smoke test before starting a long autonomous run

## Prerequisites

- Backend must be running on port 8765 (`uv run python -m alpha4gate.runner --serve`)
- Frontend dev server must be running on port 3000 (`cd frontend && npm run dev`)
- Playwright + Chromium installed (check: `npx playwright --version`)

## Procedure

### Step 1 — Detect system state

Before capturing screenshots, probe the system to determine what's running. This
determines which review tier to apply.

```bash
# Backend
curl -s http://localhost:8765/api/status 2>/dev/null  →  state: idle|in_game|...

# Frontend
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000 2>/dev/null  →  200

# SC2 process
powershell.exe -Command "Get-Process SC2_x64 -ErrorAction SilentlyContinue | Select-Object Id"

# Daemon
curl -s http://localhost:8765/api/training/daemon 2>/dev/null  →  daemon state

# Advised run
cat data/advised_run_state.json 2>/dev/null  →  status: running|completed|...
```

Map the results to a **review tier**:

| Tier | Conditions | Description |
|------|-----------|-------------|
| **T1: Fresh Start** | Backend UP + Frontend UP + SC2 not running + no active game | Just the dashboard, no game data flowing |
| **T2: SC2 Ready** | T1 + SC2_x64.exe running | SC2 available but no game in progress |
| **T3: Game Active** | T2 + backend state is `in_game` | Live game running, data streaming |
| **T4: Daemon Active** | T1 or T2 + daemon state is `running` | Training daemon spawning games autonomously |
| **T5: Advised Run** | T1-T4 + `advised_run_state.json` status is `running` | `/improve-bot-advised` loop is active |

If backend or frontend is DOWN, report which is missing and stop — do NOT attempt to start them.

### Step 2 — Capture screenshots

Run the capture script. It opens headless Chromium, clicks each tab, and saves a PNG.

```bash
node ./.claude/skills/a4g-dashboard-check/capture.mjs
```

To capture only specific tabs:

```bash
node ./.claude/skills/a4g-dashboard-check/capture.mjs processes stats training
```

**Note:** The script uses click-based navigation (not hash URLs) because Playwright doesn't reliably handle hash-based SPA routing via URL navigation.

Screenshots are saved to `.ui-dashboard-evidence/` (gitignored).

### Step 3 — Review each screenshot by tier

Read each screenshot using the Read tool and review against the tier-specific criteria below.

### Step 4 — Report

Output the detected tier and a summary table:

```
**System state:** T1 (Fresh Start) — backend + frontend only

| Tab | Status | Issues |
|-----|--------|--------|
| Live | ✓ | Connected, idle, "Waiting for game data..." |
| Stats | ✓ | 52 games displayed, 0% win rate |
| Processes | ⚠ | 2 duplicate BACKEND-SERVER entries |
| ... | ... | ... |
```

Flag any tab with issues as `⚠` (warning) or `✗` (broken) with a description.

---

## Tier-specific review criteria

### T1: Fresh Start (backend + frontend only)

The minimum viable state. Dashboard should load, connect via WebSocket, and display
historical data from the DB. No live game data expected.

| Tab | Expected | Failure indicators |
|-----|----------|-------------------|
| **Live** | Green "Live" dot. "Waiting for game data..." is normal. Command Panel visible with input field and Send button. | "Stale" indicator, "backend offline" banner, no nav tabs rendering, blank page |
| **Stats** | Game count, win rate tables, recent games list. Data should match training.db. "Last connected" should be recent (< 2 min). | "backend offline, showing cached data" banner, zero games when DB has data, stale "Last connected" timestamp |
| **Games** | Game history table with rows if DB has games. Filter dropdowns working. | Empty table when DB has games, missing columns (reward, duration), "No games" when games exist |
| **Decisions** | Decision log entries if any games have been played. May be empty on fresh DB. | Error messages, table rendering failures |
| **Training** | Current checkpoint name, total games, DB size, win rate windows. Checkpoint list. | Missing checkpoint data, "0 games" when DB has games, no model versions listed |
| **Loop** | Daemon state: "IDLE (stopped)". "Would Trigger?" evaluation. Config panel with editable fields. | Daemon showing "running" when not started, config fields not rendering, missing trigger evaluation |
| **Advisor** | Last run's iteration history if one completed. "COMPLETED" or no-run state. Control panel visible. | Crash, blank panel, "running" status when no run is active |
| **Improvements** | Reward trends chart (may need games to populate). Promote/Rollback section. | Chart not rendering, missing rule toggles |
| **Processes** | Backend ON (green), port 8765 bound (green). Process list showing backend + frontend. State files listed. | Backend OFF, port unbound, duplicate BACKEND-SERVER entries, orphan processes |
| **Alerts** | Alert list (may be empty). Ack/Dismiss buttons if alerts exist. | Page not rendering, error fetching alerts |

**Key T1 checks:**
- No "offline" or "stale" banners on any tab
- WebSocket connected (green "Live" dot, not yellow "Stale")
- Historical data renders correctly from DB
- No duplicate backend processes on Processes tab
- Port 8765 shows "bound" (green)

### T2: SC2 Ready (+ SC2 running)

Same as T1, plus:

| Check | Expected | Failure indicators |
|-------|----------|-------------------|
| **Processes tab** | SC2 process visible in process list | SC2 not detected when user confirmed it's running |
| **Live tab** | Still "Waiting for game data..." (no game started yet) | Showing stale game data from a prior session |

### T3: Game Active (+ live game in progress)

Live data should be streaming. This is the richest state to verify.

| Tab | Additional expectations beyond T1 |
|-----|----------------------------------|
| **Live** | Game time ticking, minerals/gas/supply updating, unit list populated, strategic state shown (not "Waiting for game data..."). Score increasing. |
| **Stats** | "Last connected" updating in real-time (< 30s). |
| **Decisions** | New decision entries appearing as strategy changes happen. Claude advisor entries if advisor is active. |
| **Processes** | BACKEND-RUNNER process visible (game runner), SC2 process running. |

**Key T3 checks:**
- Live tab shows real-time data (not frozen)
- Game time is advancing
- Supply/minerals/gas are non-zero
- Strategic state is one of: opening, expand, attack, defend, late_game, fortify

### T4: Daemon Active (+ training daemon running)

The daemon auto-spawns games every 60 seconds.

| Tab | Additional expectations beyond T1 |
|-----|----------------------------------|
| **Loop** | Daemon state: "RUNNING" or "CHECKING". Runs completed should increment. Last run timestamp should be recent. |
| **Training** | Total games count should be increasing over time. New checkpoints may appear. |
| **Processes** | Daemon process visible. May also see BACKEND-RUNNER (active game). |
| **Games** | New games appearing in history as daemon completes them. |

**Key T4 checks:**
- Daemon shows "running" (not "idle/stopped")
- Game count is increasing (compare with DB count)
- No "daemon crashed" or error banners
- Port 8765 still bound (daemon didn't kill the backend)

### T5: Advised Run Active (+ /improve-bot-advised running)

The most complex state. The advisor loop drives everything.

| Tab | Additional expectations beyond T1 |
|-----|----------------------------------|
| **Advisor** | Status: "RUNNING" (not "COMPLETED" or "STOPPED"). Current phase (1-7), iteration number, current improvement name. Progress bar showing elapsed/budget. Iteration history table with pass/fail results. |
| **Loop Controls** (on Advisor tab) | Games per cycle, difficulty, fail threshold should match run flags. |
| **Processes** | Backend-server ON. May see game runner during observation phases. No duplicate servers. |
| **Alerts** | "Advisor CLI failed" errors during replay-mode games are expected and harmless. Backend errors are NOT expected. |
| **Training** | If a training soak ran, game count should have increased. |
| **Stats** | Game count increasing as observation + validation games complete. |

**Key T5 checks:**
- Advisor shows "running" with correct phase/iteration
- Progress bar is advancing (elapsed < budget)
- No duplicate backend processes (the fix in `runner.py` should prevent this)
- Iteration history shows correct pass/fail results
- `advised_run_state.json` "Updated" timestamp is recent (< 5 min)
- `advised_run_control.json` shows `stop_run: false`

---

## Common issues across all tiers

| Issue | Symptom | Likely cause |
|-------|---------|-------------|
| "backend offline, showing cached data" | Yellow/orange banner on any tab | WebSocket disconnected. Backend may have restarted. Refresh browser. |
| "Stale (Xm ago)" | Yellow indicator next to nav | WebSocket not receiving heartbeats. Check backend process is alive. |
| Duplicate BACKEND-SERVER in Processes | Two python `--serve` entries | Multiple server processes spawned. Kill extras, keep one. |
| Empty game history when DB has data | Games tab shows "No games" | Backend not reading from correct `data/training.db` path. |
| Reward trends flatline after game N | Improvements tab chart drops to zero | RL training games using different reward path. Check `MAX_GAME_TIME_SECONDS`. |
| Advisor tab shows "running" but run finished | Stale `advised_run_state.json` | The advised run crashed without writing final state. Update manually. |
| Port 3000 "free" on Processes tab | Frontend column shows unbound | Frontend dev server not running. Start with `cd frontend && npm run dev`. |
