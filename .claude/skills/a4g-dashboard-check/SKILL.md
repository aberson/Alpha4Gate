---
name: a4g-dashboard-check
description: Screenshot every Alpha4Gate dashboard tab using Playwright and review for issues. Use for quick visual health checks after restarts, deploys, or advised runs. Invoke as "/a4g-dashboard-check".
user-invocable: true
argument: No arguments required. Optional: `--tabs <comma-separated>` to check specific tabs only (e.g. `--tabs stats,training`).
---

# Alpha4Gate Dashboard Check

Screenshots every dashboard tab using Playwright headless Chromium, then reviews each
screenshot for visual issues, errors, stale data, or offline indicators.

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

### Step 1 — Pre-flight

Verify backend and frontend are reachable before taking screenshots:

```bash
# Backend check
curl -s http://localhost:8765/api/status | python -c "import sys,json; d=json.load(sys.stdin); print(f'Backend: UP ({d[\"state\"]})')" 2>/dev/null || echo "Backend: DOWN"

# Frontend check
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000 2>/dev/null
```

If either is down, report which service is missing and stop. Do NOT attempt to start them — the user manages these processes from their own terminal.

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

The script captures these tabs (in order):

| Tab | URL | What to look for |
|-----|-----|-----------------|
| Live | `http://localhost:3000` | Connection status, game state, "Live" indicator |
| Stats | `http://localhost:3000/#/stats` | Win rates, game counts, reward data |
| Games | `http://localhost:3000/#/games` | Game history table populated |
| Decisions | `http://localhost:3000/#/decisions` | Decision log entries |
| Training | `http://localhost:3000/#/training` | Checkpoint info, model versions |
| Loop | `http://localhost:3000/#/loop` | Daemon state, trigger evaluation |
| Advisor | `http://localhost:3000/#/advisor` | Advised run state, iteration history |
| Improvements | `http://localhost:3000/#/improvements` | Reward trends chart |
| Processes | `http://localhost:3000/#/processes` | Active processes, port status |
| Alerts | `http://localhost:3000/#/alerts` | Error/warning list |

Screenshots are saved to `.ui-dashboard-evidence/` (gitignored).

### Step 3 — Review each screenshot

Read each screenshot using the Read tool and review for:

1. **Offline/stale banners** — "backend offline", "showing cached data", stale timestamps
2. **Error states** — red error badges, failed connections, empty tables that should have data
3. **Data freshness** — timestamps more than 10 minutes old, "Last connected: X ago" warnings
4. **Missing data** — tabs that should show content but are blank
5. **Process health** — on Processes tab, check that backend is ON and port 8765 is bound
6. **Duplicate processes** — multiple backend-server entries (indicates the port conflict bug)

### Step 4 — Report

Output a summary table:

```
| Tab | Status | Issues |
|-----|--------|--------|
| Live | ✓ | Connected, idle |
| Stats | ✓ | 52 games, 0% win rate |
| ... | ... | ... |
```

Flag any tab with issues as `⚠` or `✗` with a description of what's wrong.

### Optional: --tabs flag

If the user passes `--tabs stats,training,processes`, only capture and review those specific tabs instead of all 10.

## Tab-specific review guidance

- **Live**: Should show "Live" green dot. If "Stale" or disconnected, the WebSocket dropped.
- **Stats**: Check "Last connected" timestamp. Game counts should match Games tab.
- **Games**: All games should have result, duration, reward. Missing rewards = DB recording bug.
- **Training**: Current checkpoint should exist. Win rate windows should have data.
- **Loop**: Daemon state should be "idle" unless a training run is active.
- **Advisor**: Should show last run's iteration history if one has been run.
- **Processes**: Backend ON, port 8765 bound. No duplicate BACKEND-SERVER entries.
- **Alerts**: Review any errors. "Advisor CLI failed" during replay-mode runs is expected/harmless.
