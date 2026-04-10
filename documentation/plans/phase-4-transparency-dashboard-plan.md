# Phase 4: Transparency Dashboard — Detailed Plan

## 1. What This Feature Does

Phase 4 makes the autonomous improvement loop fully visible and controllable from the
React dashboard. Phase 3 shipped a training daemon, evaluator, promotion gate, rollback
monitor, and curriculum auto-advancement — but they all run invisibly. The operator
cannot tell when training last ran, why it triggered or didn't, what got promoted, or
whether anything regressed without reading log files.

This phase closes that transparency gap by adding three new top-level dashboard tabs
(`Loop`, `Improvements`, `Alerts`), wiring them to existing daemon/promotion/rollback
endpoints, building one new aggregation endpoint for per-rule reward trends, and
introducing a fully self-contained in-app alert system with toast notifications and
localStorage-backed ack/dismiss state.

The phase is **frontend-heavy** — most backend capability already exists from Phase 3.
The only new backend work is one aggregation module, one new endpoint, and one extra
field on an existing endpoint.

## 2. Existing Context

**Project:** Alpha4Gate is a StarCraft II (SC2) Protoss bot reframed as an
"always-up autonomous improvement platform." The SC2 bot is a pluggable domain;
the real product is the play → evaluate → train → promote loop with full
transparency.

**Phase 3 (just completed) shipped:**
- `src/alpha4gate/learning/daemon.py` — `TrainingDaemon` background thread with
  trigger logic, curriculum persistence, observable state via `get_status()` and
  `get_trigger_state()`
- `src/alpha4gate/learning/evaluator.py` — `ModelEvaluator` for inference-only
  checkpoint evaluation with async job management
- `src/alpha4gate/learning/promotion.py` — `PromotionManager` + `PromotionLogger`,
  automated promote gate with JSON + wiki logging
- `src/alpha4gate/learning/rollback.py` — `RollbackMonitor` with regression detection,
  automatic revert, difficulty floor
- 15+ training-related API endpoints in `src/alpha4gate/api.py` covering daemon
  control, triggers, evaluation jobs, promotions, rollback, curriculum
- 661 tests passing, 0 type errors, 0 lint violations

**Frontend baseline (`frontend/`):**
- React 18 + TypeScript + Vite, dev server on `:3000` proxying `:8765`
- 6-tab SPA: `Live | Stats | Builds | Replays | Decisions | Training`
- Tab routing via `useState<Tab>()` in `frontend/src/App.tsx` — no React Router
- 11 components in `frontend/src/components/`, 3 hooks in `frontend/src/hooks/`
- Live game data flows over WebSocket (`/ws/game`, `/ws/decisions`, `/ws/commands`)
  via `useWebSocket.ts`. **Training data uses 5s polling**, not WebSocket.
- The existing `Training` tab stacks `TrainingDashboard`, `ModelComparison`,
  `ImprovementTimeline`, `CheckpointList`, `RewardRuleEditor` — read-only views
  of training state with no daemon control.
- **Frontend currently has no test infrastructure** — no vitest, no jest, no
  testing-library. Type checking via `npx tsc --noEmit` and visual inspection only.

**Backend endpoints Phase 4 will consume (all already exist):**

| Endpoint | Used by Phase 4 step |
|----------|----------------------|
| `GET /api/training/daemon` | LoopStatus (Step 5), Alerts (Step 9) |
| `GET /api/training/triggers` | LoopStatus (Step 5), Alerts (Step 9) |
| `POST /api/training/start` | TriggerControls (Step 6) |
| `POST /api/training/stop` | TriggerControls (Step 6) |
| `PUT /api/training/daemon/config` | TriggerControls (Step 6) |
| `POST /api/training/evaluate` | TriggerControls (Step 6) |
| `GET /api/training/checkpoints` | TriggerControls (Step 6) |
| `POST /api/training/promote` | TriggerControls (Step 6) |
| `POST /api/training/rollback` | TriggerControls (Step 6) |
| `GET /api/training/curriculum` | TriggerControls (Step 6) |
| `PUT /api/training/curriculum` | TriggerControls (Step 6) |
| `GET /api/training/promotions/history` | RecentImprovements (Step 7), Alerts (Step 9) |
| `GET /api/training/history` | Alerts (Step 9) |
| `GET /api/training/status` | Alerts (Step 9) — also extended in Step 1 |

**Backend response shapes (verified against current source):**

`GET /api/training/daemon` — from `TrainingDaemon.get_status()` in
`src/alpha4gate/learning/daemon.py`:
```ts
{
  running: boolean,
  state: "idle" | "checking" | "training",
  last_run: string | null,        // ISO timestamp
  next_check: string | null,      // ISO timestamp
  runs_completed: number,
  last_result: object | null,     // last training run summary, shape varies
  last_error: string | null,
  last_rollback: object | null,
  config: {                       // full DaemonConfig snapshot
    check_interval_seconds: number,
    min_transitions: number,
    min_hours_since_last: number,
    cycles_per_run: number,
    games_per_cycle: number,
    current_difficulty: number,
    max_difficulty: number,
    win_rate_threshold: number,
  }
}
```

`GET /api/training/triggers` — from `TrainingDaemon.get_trigger_state()`:
```ts
{
  transitions_since_last: number,
  hours_since_last: number,       // can be Infinity if never run
  would_trigger: boolean,
  reason: string,                 // human-readable explanation
}
```

`GET /api/training/status` — current shape, with the new field added in Step 1:
```ts
{
  training_active: boolean,
  current_checkpoint: string | null,
  total_checkpoints: number,
  total_games: number,
  total_transitions: number,
  db_size_bytes: number,
  reward_logs_size_bytes: number, // NEW in Step 1
}
```

`GET /api/training/history`:
```ts
{
  total_games: number,
  win_rates: {
    last_10: number,
    last_50: number,
    last_100: number,
    overall: number,
  }
}
```

`GET /api/training/checkpoints`:
```ts
{
  checkpoints: Array<{name: string, type: string, win_rate: number, ...}>,
  best: string | null,
}
```

`GET /api/training/curriculum` — from `TrainingDaemon.get_curriculum_status()`:
```ts
{
  current_difficulty: number,
  max_difficulty: number,
  win_rate_threshold: number,
  last_advancement: string | null,  // ISO timestamp
}
```

`PUT /api/training/curriculum` — request body accepts any subset of
`{current_difficulty, max_difficulty, win_rate_threshold}`. Response:
`{status: "updated", curriculum: <same shape as GET>}`.

`PUT /api/training/daemon/config` — request body accepts any subset of the
`config` fields above. Response: `{status: "updated", config: <full config>}`.

`GET /api/training/promotions/history` — from `PromotionLogger.get_history()`,
which reads `data/promotion_history.json`. Both promotions AND rollbacks are
appended to this file (rollbacks via `RollbackMonitor._log_rollback()`).
Distinguish them via `promoted: false` AND `reason.startsWith("rollback:")`:
```ts
{
  history: Array<{
    timestamp: string,                 // ISO timestamp
    new_checkpoint: string,            // for rollbacks: the checkpoint reverted TO
    old_best: string | null,           // for rollbacks: the checkpoint reverted FROM
    new_win_rate: number,
    old_win_rate: number | null,
    delta: number | null,              // new - old
    eval_games_played: number,
    promoted: boolean,                 // true=promoted, false=rejected OR rollback
    reason: string,                    // rollback entries start with "rollback:"
    difficulty: number,                // 0 for rollback entries
    action_distribution_shift: number | null,
  }>
}
```

`POST /api/training/start` → `{status: "started" | "already_running" | "error"}`.
`POST /api/training/stop` → `{status: "stopped" | "not_running" | "error"}`.
`POST /api/training/promote` — body `{checkpoint: string}` →
`{status: "promoted", checkpoint: string, old_best: string | null}`.
`POST /api/training/rollback` — body `{checkpoint: string}` →
`{status: "rolled_back", old_best: string, new_best: string}`.
`POST /api/training/evaluate` — body
`{checkpoint: string, games?: number, difficulty?: number}` → 202 with
`{job_id: string, status: "pending", checkpoint, games, difficulty}`.

**Reward log files:** `data/reward_logs/game_<id>.jsonl` — one JSONL per game,
written by `RewardCalculator` in `src/alpha4gate/learning/rewards.py`. Each line
is one decision step:
```json
{
  "game_time": 245.3,
  "total_reward": 0.18,
  "fired_rules": [
    {"id": "army_supply_growth", "reward": 0.05},
    {"id": "expansion_built", "reward": 0.10}
  ],
  "is_terminal": false,
  "result": null
}
```
On the terminal step `is_terminal: true` and `result` is `"win" | "loss" |
"timeout"`. **Important for the aggregator:** rule IDs are nested in the
`fired_rules` array, NOT at the top level. The aggregator must walk
`fired_rules` per line and sum `reward` per rule ID. Game ID comes from the
filename (`game_<id>.jsonl`), not from any field inside the file. Timestamp
must be derived from file mtime since lines do not include wall-clock time.

**Project conventions to follow:**
- Python: 3.12, `uv` (Astral's Python package manager — replaces pip + venv),
  ruff (line-length=100), mypy strict, pytest. See `AGENTS.md`.
- Frontend components are domain-agnostic — they render whatever JSON the backend
  sends, no SC2 concepts hardcoded.
- Existing components use self-fetching pattern (`useState` + `useEffect` +
  `setInterval` + `fetch`). New code should match unless a hook is more reusable.
- Existing styling: stat cards in `.status-grid`, `.stat-card` classes (see
  `TrainingDashboard.tsx`).
- Plan docs live in `documentation/plans/`. Wiki pages in `documentation/wiki/`.

## 3. Scope

### In scope
- Three new dashboard tabs: `Loop`, `Improvements`, `Alerts` (added to existing
  6-tab nav, total 9 tabs, single row)
- Live training cycle status component with daemon state, trigger evaluation,
  last run/error/result info — polled at 2s
- Full daemon control panel: start/stop, config edit, manual evaluate, manual
  promote/rollback (with confirmation dialogs), curriculum override
- Recent improvements view: last 20 promotions/rollbacks with filtering
- Per-rule reward trend visualization with Recharts line chart + summary table,
  windowed by last 50/100/200/500 games
- New backend endpoint `GET /api/training/reward-trends?games=N` and supporting
  `reward_aggregator.py` module
- New `reward_logs_size_bytes` field on existing `GET /api/training/status`
- In-app alert system: 6 alert rules, severity-coded panel, in-app toast at App
  root, unread count badge on Alerts tab, localStorage-backed ack/dismiss
- Frontend test infrastructure: vitest + @testing-library/react + jsdom
- Reusable `ConfirmDialog` modal component
- Wiki + plan doc updates to reflect post-Phase-4 state

### Out of scope (deferred)
- **WebSocket migration for training data** — polling at 2s/5s is sufficient.
  Reaffirms the Phase 3 deferral.
- **Browser Notification API / desktop popups** — alerts simulate notifications
  inside the dashboard tab via a toast component, no native browser APIs.
- **Email / Slack / push notification delivery** — single-operator dashboard only
- **Persistent alert database** — alert state derived from polled data, ack/dismiss
  in localStorage only
- **Multi-user / auth** — single operator
- **Mobile / responsive design** — desktop-only
- **Reward rule A/B testing UI** — `RewardRuleEditor` stays as-is
- **Domain abstraction (SC2 vs generic)** — Phase 5 work, untouched here
- **Caching layer for reward aggregator** — synchronous file read on every request
  is acceptable for v1 (last 100 games is finite)

## 4. Impact Analysis

### Files modified

| File | Nature of change | Reason |
|------|------------------|--------|
| `src/alpha4gate/api.py` | Modify (add ~10 lines to `/api/training/status`, add new endpoint ~25 lines) | Status endpoint gains `reward_logs_size_bytes`; new `/api/training/reward-trends` endpoint added |
| `tests/test_api.py` | Extend | Tests for new field + new endpoint |
| `frontend/src/App.tsx` | Modify | Add 3 new tabs to Tab union, nav, conditional render. Mount `<AlertToast />` at root. Lifted state for tab switching from toast "View" link. |
| `frontend/src/App.css` | Extend | Styles for confirm dialog, toast, severity colors, new stat cards |
| `frontend/package.json` | Modify | Add vitest, @testing-library/react, @testing-library/jest-dom, jsdom (devDeps); add recharts (dep); add `test`/`test:run` scripts |
| `documentation/wiki/frontend.md` | Update | New tabs, new components, new hooks, polling intervals, alert system section |
| `documentation/plans/always-up-plan.md` | Update | Mark Phase 4 steps DONE with issue numbers; update Current State; remove addressed items from "What's missing"; reaffirm WebSocket deferral |
| `CLAUDE.md` | Minor | Update test count if it changed |
| `memory/alpha4gate.md` (user memory) | Update | Phase 4 done, Phase 5 next |

### Files unchanged (despite seeming relevant)
- `src/alpha4gate/learning/daemon.py`, `evaluator.py`, `promotion.py`, `rollback.py`
  — already expose everything Phase 4 needs. **No backend changes for Steps 5-9.**
- `frontend/src/hooks/useWebSocket.ts` — Phase 4 uses polling exclusively
- All existing components in the Training tab — they stay where they are
- Database schema — alerts derived in-browser, not persisted server-side

## 5. New Components

### Backend
| File | Purpose | Approx size |
|------|---------|-------------|
| `src/alpha4gate/learning/reward_aggregator.py` | Reads `data/reward_logs/game_*.jsonl`, aggregates per-rule contributions over the last N games, returns JSON-serializable trend data with stable shape | ~80 lines |
| `tests/test_reward_aggregator.py` | Unit tests with synthetic JSONL fixtures: empty dir, single game, multi-game multi-rule, malformed lines, missing rule_id | ~120 lines |

### Frontend — infrastructure
| File | Purpose | Approx size |
|------|---------|-------------|
| `frontend/vitest.config.ts` | Vitest configuration: jsdom environment (browser-like DOM in Node, lets `@testing-library/react` mount components without a real browser), setup file | ~15 lines |
| `frontend/src/test/setup.ts` | Imports `@testing-library/jest-dom` matchers | ~3 lines |
| `frontend/src/test/sanity.test.ts` | Trivial assertion to verify runner works | ~5 lines |
| `frontend/src/components/ConfirmDialog.tsx` | Reusable modal: backdrop + centered card, escape/backdrop close, destructive variant for red confirm button | ~60 lines |
| `frontend/src/components/ConfirmDialog.test.tsx` | Tests: open/closed, onConfirm, onCancel, escape, backdrop click | ~80 lines |

### Frontend — feature components
| File | Purpose | Approx size |
|------|---------|-------------|
| `frontend/src/hooks/useDaemonStatus.ts` | Polls `/api/training/daemon` + `/api/training/triggers` in parallel every 2000ms | ~50 lines |
| `frontend/src/components/LoopStatus.tsx` | Daemon state badge, stat cards (runs_completed, last_run, next_check, transitions_since_last, hours_since_last), trigger evaluation card, last_error/last_result blocks | ~150 lines |
| `frontend/src/components/TriggerControls.tsx` | 5 panels: Daemon control (start/stop), Daemon config form, Manual evaluation, Manual promote/rollback (ConfirmDialog), Curriculum override (ConfirmDialog) | ~250 lines |
| `frontend/src/components/RecentImprovements.tsx` | Chronological list of last 20 promotion/rollback events, filter buttons (All/Promotions/Rollbacks), delta calculation with color | ~150 lines |
| `frontend/src/components/RewardTrends.tsx` | Summary table (sortable) + Recharts LineChart with per-rule lines, window selector (50/100/200/500) | ~180 lines |

### Frontend — alert system
| File | Purpose | Approx size |
|------|---------|-------------|
| `frontend/src/lib/alertRules.ts` | `Alert` type, `evaluateAlertRules(state)` → `Alert[]`, six rule functions, threshold constants at top of file | ~150 lines |
| `frontend/src/lib/alertStorage.ts` | localStorage wrapper, key `alpha4gate.alerts.state` storing `{acked: string[], dismissed: string[]}` | ~50 lines |
| `frontend/src/hooks/useAlerts.ts` | Polls all data sources, runs `evaluateAlertRules`, filters via storage, returns `{alerts, unreadCount, ackAlert, dismissAlert, markAllRead, clearHistory, newAlertsThisPoll}` | ~80 lines |
| `frontend/src/components/AlertToast.tsx` | Fixed top-right container, listens for `newAlertsThisPoll`, renders toast cards with 8-second auto-dismiss, "View" link switches to Alerts tab via lifted state | ~80 lines |
| `frontend/src/components/AlertsPanel.tsx` | Full alert list, severity filter (All/Errors/Warnings/Info), per-alert ack/dismiss buttons, "Mark all read" + "Clear history" buttons, sorted newest first | ~150 lines |
| Test files for above | `alertRules.test.ts`, `alertStorage.test.ts`, `AlertsPanel.test.tsx`, `AlertToast.test.tsx` | ~300 lines total |

## 6. Design Decisions

### D0. Polling vs WebSocket for training data
**Decision:** Stay with polling. New `LoopStatus` component polls at 2s; everything
else stays at the existing 5s.
**Rationale:** Daemon state changes are minutes-scale, not sub-second. Latency benefit
of WebSocket is negligible. Adding WS would require new broadcast plumbing in
`daemon.py`, `evaluator.py`, `promotion.py`, plus a new hook. Existing polling
pattern works and matches every other training component. Can upgrade later if
Phase 4 reveals an actual latency problem.
**Alternative considered:** WebSocket upgrade for training data — rejected as
premature. Reaffirms the deferral noted in `documentation/plans/always-up-plan.md`
line 441-442.

### D1. Tab structure: separate vs combined
**Decision:** Three new top-level tabs (`Loop`, `Improvements`, `Alerts`) inserted
after the existing `Training` tab. Final order: `Live | Stats | Builds | Replays
| Decisions | Training | Loop | Improvements | Alerts`. Single row, 9 tabs total.
**Rationale:** Easier to combine later than to split. Each tab has a distinct
purpose: Loop = control + live status, Improvements = historical promotions +
reward trends, Alerts = derived warnings. Stacking them all in the existing
Training tab would make it unscannable.
**Alternative considered:** Single new tab with sub-sections — rejected as too
crowded.

### D2. Alert rule set (v1)
**Decision:** Six initial rules:

| Rule | Severity | Source | Threshold |
|------|----------|--------|-----------|
| Win rate dropped | warning | `/api/training/history` | `last_10 < last_50 - 0.15` |
| Training failed | error | `/api/training/daemon` `last_error` | non-null since last ack |
| Daemon stopped unexpectedly | error | `/api/training/daemon` state delta | was training/checking, now idle with `last_error` set |
| Disk usage high | warning | extended `/api/training/status` | DB > 50 GB (200 GB hard guard) |
| Model regressed (rollback fired) | warning | `/api/training/promotions/history` | latest entry `action == "rollback"` since last ack |
| No training in N hours | info | `/api/training/triggers` `hours_since_last` | `> 24` while daemon running |

Thresholds defined as named constants at top of `alertRules.ts` for easy tuning.
**Rationale:** Covers the obvious failure modes (training broken, regression,
silence) and the obvious resource concern (disk). All derivable from existing
polled state — no new persistence.

### D3. Alert ID and dedup strategy
**Decision:** Each alert has a stable `id` of the form `{ruleId}:{stateHash}`,
e.g., `win_rate_drop:0.62→0.41`, `training_failed:2026-04-09T14:32:01Z`,
`rollback_fired:checkpoint_v23`. The `useAlerts` hook diffs current vs previous
alert IDs each poll. New IDs trigger a toast and bump unread count. Acked alerts
remain visible but de-emphasized. Dismissed alerts are filtered out forever (or
until "Clear history"). The same condition recurring with different state (e.g.,
a second rollback for a different checkpoint) produces a new alert.
**Rationale:** Stable IDs prevent toast spam. State-aware IDs ensure recurrences
aren't suppressed.

### D4. Notifications: in-app only, no browser API
**Decision:** Toast component is a normal React `<div>` rendered at the App root.
No `Notification` API, no permission prompt, no desktop popups. The toast
*simulates* a desktop notification's UX (top-right, severity color, auto-dismiss
after 8 seconds, click to view) but lives entirely inside the browser tab.
**Rationale:** Validates the alert UX without browser permission friction or
cross-environment quirks. If real desktop notifications are wanted later, it's a
small additive change on top of the in-app system.

### D5. Alert ack/dismiss persistence
**Decision:** localStorage. Key: `alpha4gate.alerts.state`. Value:
`{acked: string[], dismissed: string[]}`. Survives browser refresh, single-device
only (no sync).
**Rationale:** Three lines of code, no backend changes, matches single-operator
scope.

### D6. ConfirmDialog: custom modal vs `window.confirm()`
**Decision:** Custom modal in `frontend/src/components/ConfirmDialog.tsx`. Used
for ~4 destructive actions (rollback, promote, daemon stop, curriculum override).
Reusable, escape-key/backdrop dismiss, destructive variant with red confirm button.
**Rationale:** ~60 lines of code, no new deps, looks better than native confirm.
Worth it for the small number of touchpoints.

### D7. RewardTrends chart library
**Decision:** Add Recharts as a frontend dependency. First chart in the project.
**Rationale:** Will be reused across Phase 4 and Phase 5. Declarative React API,
~50 KB gzipped, acceptable for an internal-only dashboard. Per `AGENTS.md` "do
not add dependencies unless clearly justified": justification is "first chart
library, frontend-only, dashboard-only, will see reuse." Hand-rolled SVG was
considered but rejected for lower polish and per-chart maintenance cost.

### D8. Reward aggregator bucketing
**Decision:** Bucket per-game (one data point per game per rule). Output:
```
{
  rules: [
    {
      rule_id: str,
      total_contribution: float,
      contribution_per_game: float,
      points: [{game_id: str, timestamp: str, contribution: float}, ...]
    }
  ],
  n_games: int,
  generated_at: str (ISO timestamp)
}
```
**Rationale:** Matches how the data is generated (one JSONL per game). Simpler
than time-bucketing. Last 100 games is finite and bounded.

### D9. Reward aggregator lookback default
**Decision:** Default `games=100`, query param override `1 ≤ games ≤ 1000`.
**Rationale:** 100 games is a reasonable signal window without re-parsing huge
volumes per request. 1000 cap prevents abuse / accidental DoS.

### D10. Frontend test infrastructure
**Decision:** Add vitest + @testing-library/react + @testing-library/jest-dom +
jsdom in Step 3. Add unit tests for all new components, hooks, and lib files in
Phase 4. Existing components are not retroactively tested.
**Rationale:** Alert rules and storage logic deserve real tests. Will be reused
for Phase 5 frontend work. ~5 minutes of one-time setup. Splitting infra (Step 3)
from the first real component test (Step 4) isolates risk.

## 7. Build Steps

### Conventions used in this section

**Step format:** Each step uses the `/build-phase` orchestrator format. The
`/build-phase` skill reads this section, runs each step in order via the
`/build-step` skill, and posts progress to GitHub issues.

**`Issue: #` blank field:** Issue numbers are intentionally left blank in this
plan. The `/repo-sync` skill will diff the steps in this plan against existing
GitHub issues and create new ones, then update this file with the issue
numbers. Run `/repo-sync --plan documentation/plans/phase-4-transparency-dashboard-plan.md`
before `/build-phase`.

**`Flags` field — `/build-step` flags:** The `/build-step` skill takes flags
that control isolation, review depth, and evidence capture:
- `--reviewers code` — runs the 4-pass code review gauntlet (correctness,
  bugs, test quality, style). Default for backend-only steps that don't need
  runtime evidence.
- `--reviewers runtime` — runs evidence-based reviewers that verify behavior
  by actually running the code, not just reading it. Used when runtime
  behavior matters more than code quality.
- `--reviewers runtime --ui` — runtime reviewers PLUS Playwright screenshots
  of the rendered UI. The screenshots become evidence the reviewer compares
  against the problem statement. Used for all 5 frontend feature steps.
- `--isolation worktree` (default) — runs in a git worktree so the main repo
  stays clean. Auto-merged on success.
- `--tdd` — invokes `/build-step-tdd` instead, for strict test-first
  workflows. Not used in Phase 4.

**`Done when` field:** Concrete verification commands and observations that
prove the step is complete. The `/build-step` skill blocks merge until these
pass.

---

### Step 1: Status endpoint disk size + reward aggregator backend
- **Status:** DONE (2026-04-09)
- **Problem:** (1) Add `reward_logs_size_bytes` field to `GET /api/training/status`
  response in `src/alpha4gate/api.py`. Computed by walking `data/reward_logs/`
  directory and summing file sizes. Handle missing directory (return 0). The
  full updated response shape is shown in Section 2 "Backend response shapes".
  (2) Create new module `src/alpha4gate/learning/reward_aggregator.py` with a
  function `aggregate_reward_trends(reward_logs_dir: Path, n_games: int = 100)
  -> dict[str, Any]`. Behavior:
    - List `*.jsonl` files in `reward_logs_dir`, sort by mtime descending,
      take the first `n_games`. The game ID comes from the filename
      (`game_<id>.jsonl`); use `pathlib.Path.stem.removeprefix("game_")`.
    - For each file, parse it line-by-line (streaming, not `json.load`).
      Each line is a step record with shape:
      `{"game_time": float, "total_reward": float, "fired_rules": [{"id":
      str, "reward": float}, ...], "is_terminal": bool, "result": str|null}`.
    - **Rules are nested in the `fired_rules` array, NOT at the top level.**
      Walk `fired_rules` per line, sum `reward` per rule ID across all lines
      in the file. The result is one (rule_id, total_contribution) point per
      game per rule.
    - The per-game timestamp comes from the file mtime
      (`Path.stat().st_mtime`), formatted as ISO. Step records have no
      wall-clock timestamp.
    - Return shape: see design decision D8 below. `total_contribution` is the
      sum across all returned games for that rule; `contribution_per_game` is
      `total_contribution / n_games_with_data_for_that_rule`.
    - Skip empty/malformed lines with a warning log, do not crash on
      `json.JSONDecodeError`. Skip empty files entirely.
  (3) Add `tests/test_reward_aggregator.py` with synthetic JSONL fixtures
  covering: empty dir, single game with one rule firing once, multi-game with
  multiple rules, malformed lines, lines with empty `fired_rules`, lines
  missing `fired_rules` key entirely, files with no parseable lines. Use
  pytest `tmp_path` fixture. Construct fixtures by writing real JSONL files
  matching the documented schema.
  (4) Add test in `tests/test_api.py` for the new `reward_logs_size_bytes`
  field in `/api/training/status`. Verify it returns 0 when the directory is
  missing and a positive number when files exist.
- **Issue:** #50
- **Flags:** --reviewers code
- **Produces:** `src/alpha4gate/learning/reward_aggregator.py` (~80 lines),
  modified `src/alpha4gate/api.py` (+10 lines on status endpoint),
  `tests/test_reward_aggregator.py` (~120 lines), updated `tests/test_api.py`.
- **Done when:** `uv run pytest tests/test_reward_aggregator.py tests/test_api.py`
  passes; `uv run mypy src` clean; `uv run ruff check .` clean; existing 661
  tests still pass.
- **Depends on:** none

### Step 2: Reward trends API endpoint
- **Status:** DONE (2026-04-09)
- **Problem:** Add `GET /api/training/reward-trends?games=100` endpoint to
  `src/alpha4gate/api.py`. The endpoint:
  (1) Accepts an optional `games` query parameter (int, default 100, min 1,
  max 1000).
  (2) Resolves the reward logs directory from `_data_dir / "reward_logs"`.
  (3) Calls `reward_aggregator.aggregate_reward_trends(reward_logs_dir, games)`.
  (4) Returns the aggregator output as JSON.
  (5) Handles missing reward_logs directory by returning
  `{"rules": [], "n_games": 0, "generated_at": ...}` with HTTP 200 (empty
  state is normal).
  Add tests in `tests/test_api.py` covering: empty state, populated state with
  fixtures, query param validation (negative, too large, non-numeric).
- **Issue:** #51
- **Flags:** --reviewers code
- **Produces:** new endpoint in `src/alpha4gate/api.py` (~25 lines), tests in
  `tests/test_api.py`.
- **Done when:** `uv run pytest tests/test_api.py -k reward_trends` passes;
  `curl http://localhost:8765/api/training/reward-trends?games=10` returns
  valid JSON; full test suite still passes.
- **Depends on:** Step 1

### Step 3: Frontend test infrastructure (vitest + Recharts + CSS scaffolding)
- **Status:** DONE (2026-04-10)
- **Problem:** One-time frontend infra setup that unblocks all later steps.
  (1) Add to `frontend/package.json` devDependencies: `vitest`,
  `@testing-library/react`, `@testing-library/jest-dom`, `jsdom`. Configure via
  `frontend/vitest.config.ts` (jsdom environment, setup file for jest-dom
  matchers). Add `"test": "vitest"` and `"test:run": "vitest run"` scripts.
  Create `frontend/src/test/setup.ts` importing `@testing-library/jest-dom`.
  (2) Add a single trivial test `frontend/src/test/sanity.test.ts` that asserts
  `1 + 1 === 2`, just to prove the runner works.
  (3) Add `recharts` to `frontend/package.json` dependencies.
  (4) Add CSS scaffolding to `frontend/src/App.css` for classes that will be
  used by later steps: `.confirm-dialog`, `.confirm-dialog-backdrop`,
  `.confirm-dialog-card`, `.alert-toast`, `.alert-toast-enter`,
  `.alert-toast-exit`, `.severity-info`, `.severity-warning`, `.severity-error`,
  `.unread-badge`. Just the styles — components added in later steps.
- **Issue:** #52
- **Flags:** --reviewers code
- **Produces:** modified `frontend/package.json`, `frontend/vitest.config.ts`,
  `frontend/src/test/setup.ts`, `frontend/src/test/sanity.test.ts`, updated
  `frontend/src/App.css`.
- **Done when:** `cd frontend && npm run test:run` passes (sanity test);
  `cd frontend && npx tsc --noEmit` clean; `npm run build` succeeds (verifies
  Recharts bundles correctly).
- **Depends on:** none (can run in parallel with Steps 1-2)

### Step 4: ConfirmDialog component
- **Status:** DONE (2026-04-10)
- **Problem:** Build the reusable confirmation modal that destructive actions in
  Step 6 will use. Also serves as the first proof that the Step 3 vitest setup
  works for real components.
  (1) Create `frontend/src/components/ConfirmDialog.tsx` with props
  `{open: boolean, title: string, message: string, confirmLabel?: string,
  cancelLabel?: string, onConfirm: () => void, onCancel: () => void,
  destructive?: boolean}`. Renders a backdrop + centered card. When
  `destructive`, the confirm button uses red styling (`.destructive` class).
  Closes on Escape key and backdrop click (calls `onCancel`). Default labels:
  "Confirm" and "Cancel".
  (2) Add unit test `frontend/src/components/ConfirmDialog.test.tsx` covering:
  renders when `open=true`, hidden when `open=false`, calls `onConfirm` when
  confirm clicked, calls `onCancel` when cancel clicked, calls `onCancel` on
  Escape key, calls `onCancel` on backdrop click. Use
  `@testing-library/react`.
- **Issue:** #53
- **Flags:** --reviewers code
- **Produces:** `frontend/src/components/ConfirmDialog.tsx` (~60 lines),
  `frontend/src/components/ConfirmDialog.test.tsx` (~80 lines).
- **Done when:** `cd frontend && npm run test:run` passes (sanity +
  ConfirmDialog tests); `cd frontend && npx tsc --noEmit` clean.
- **Depends on:** Step 3

### Step 5: LoopStatus component (Phase 4 plan Step 1)
- **Status:** DONE (2026-04-10)
- **Problem:** Build the live training cycle status component and add a new
  "Loop" tab to the dashboard.
  (1) Create `frontend/src/hooks/useDaemonStatus.ts`. Polls
  `/api/training/daemon` and `/api/training/triggers` in parallel every 2000ms.
  Returns `{status, triggers, loading, error, refresh}`. Cleanup interval on
  unmount. Type the responses (`DaemonStatus`, `TriggerState` interfaces).
  (2) Create `frontend/src/components/LoopStatus.tsx`. Uses `useDaemonStatus`.
  Renders:
    - Daemon state badge (`idle` / `checking` / `training`) with color
      (gray / yellow / green)
    - Stat cards: `runs_completed`, `last_run` timestamp (formatted),
      `next_check` timestamp, `transitions_since_last`, `hours_since_last`
    - Trigger evaluation card: `would_trigger` badge (yes/no) + `reason` text
    - `last_error` text block (red, only if non-null)
    - `last_result` summary (only if present): cycles, win_rate,
      final_difficulty
  Match existing `TrainingDashboard.tsx` styling conventions (`.stat-card`,
  `.status-grid`).
  (3) Add a "Loop" tab to `frontend/src/App.tsx`. Insert after the "Training"
  tab. Update the `Tab` type union. Add nav button. Add conditional render
  `{tab === "loop" && <LoopStatus />}`.
  (4) Add unit test `frontend/src/components/LoopStatus.test.tsx` covering:
  loading state, error state, idle state, training state, error display.
  Mock `fetch` using vitest.
  (5) Manual verification: start the daemon via `POST /api/training/start`,
  visit the Loop tab, confirm state updates within 2 seconds.
- **Issue:** #54
- **Flags:** --reviewers runtime --ui --start-cmd "bash scripts/start-dev.sh" --url http://localhost:3000 --ready-url http://localhost:8765/api/status
- **Produces:** `frontend/src/hooks/useDaemonStatus.ts`,
  `frontend/src/components/LoopStatus.tsx`,
  `frontend/src/components/LoopStatus.test.tsx`, updated
  `frontend/src/App.tsx`.
- **Done when:** `npm run test:run` passes; `npx tsc --noEmit` clean;
  Playwright screenshot shows Loop tab rendering with daemon state.
- **Depends on:** Step 4

### Step 6: TriggerControls component (Phase 4 plan Step 5)
- **Status:** DONE (2026-04-10)
- **Problem:** Build the training trigger UI with full daemon control. Renders
  inside the Loop tab below `LoopStatus`.
  (1) Create `frontend/src/components/TriggerControls.tsx` with five panels:
    - **Daemon control:** Start/Stop buttons (`POST /api/training/start`,
      `POST /api/training/stop`). Disabled-state based on current daemon
      status from `useDaemonStatus`.
    - **Daemon config:** Editable form with number inputs for all
      `DaemonConfig` fields (`check_interval_seconds`, `min_transitions`,
      `min_hours_since_last`, `cycles_per_run`, `games_per_cycle`,
      `current_difficulty`, `max_difficulty`, `win_rate_threshold`). Save
      button calls `PUT /api/training/daemon/config`. Show "saved"
      confirmation briefly. Client-side validation (positive integers,
      `win_rate_threshold` between 0-1).
    - **Manual evaluation:** Checkpoint dropdown (populated from
      `GET /api/training/checkpoints`), games input (default 10), difficulty
      input (default 1). "Evaluate" button calls `POST /api/training/evaluate`.
      Show job ID after submission.
    - **Manual promote / rollback:** Two buttons. Both wrapped in
      `ConfirmDialog`. Promote calls `POST /api/training/promote`, rollback
      calls `POST /api/training/rollback`. After action, show result and
      refresh `useDaemonStatus`.
    - **Curriculum override:** Display current difficulty from
      `GET /api/training/curriculum`. Number input + "Set" button calls
      `PUT /api/training/curriculum`, wrapped in `ConfirmDialog` since this
      affects the autonomous loop.
  (2) Render `TriggerControls` inside the Loop tab, below `LoopStatus`.
  (3) Add unit test `TriggerControls.test.tsx` covering: form validation,
  start/stop button enable logic, confirmation dialog appears for destructive
  actions, mock POST endpoints.
  (4) Manual verification: start daemon, change `cycles_per_run` from 5 to 3,
  save, refresh, confirm value persisted. Trigger a manual rollback in a test
  environment, confirm dialog appears.
- **Issue:** #55
- **Flags:** --reviewers runtime --ui --start-cmd "bash scripts/start-dev.sh" --url http://localhost:3000 --ready-url http://localhost:8765/api/status
- **Produces:** `frontend/src/components/TriggerControls.tsx` (~250 lines),
  `frontend/src/components/TriggerControls.test.tsx`, updated Loop tab in
  `App.tsx`.
- **Done when:** `npm run test:run` passes; `npx tsc --noEmit` clean;
  Playwright screenshot shows controls rendered; manual smoke test of
  start/stop + config save works end-to-end.
- **Depends on:** Step 4, Step 5

### Step 7: RecentImprovements component (Phase 4 plan Step 2)
- **Status:** DONE (2026-04-10)
- **Problem:** Build the "Improvements" tab showing recent promotions and
  rollbacks with context.
  (1) Create `frontend/src/components/RecentImprovements.tsx`. Polls
  `GET /api/training/promotions/history` every 5000ms. Renders a chronological
  list of the last N events (default 20):
    - Each entry: timestamp, action (promote/rollback), checkpoint name,
      previous best, win rate at time of action, win rate delta vs prior best
      (color: green up arrow / red down arrow), reason text.
    - Filter buttons: All / Promotions / Rollbacks
    - Empty state when history is empty.
  (2) Add an "Improvements" tab to `App.tsx` after the Loop tab. Update Tab
  type union, add nav button, add conditional render.
  (3) Add unit test `RecentImprovements.test.tsx` covering: loading state,
  empty state, populated state, filter toggle, delta calculation correctness.
- **Issue:** #56
- **Flags:** --reviewers runtime --ui --start-cmd "bash scripts/start-dev.sh" --url http://localhost:3000 --ready-url http://localhost:8765/api/status
- **Produces:** `frontend/src/components/RecentImprovements.tsx` (~150 lines),
  test file, updated `App.tsx`.
- **Done when:** `npm run test:run` passes; `npx tsc --noEmit` clean;
  Playwright screenshot of Improvements tab rendering with mock or real
  promotion history.
- **Depends on:** Step 4

### Step 8: RewardTrends component (Phase 4 plan Step 3)
- **Status:** DONE (2026-04-10)
- **Problem:** Build the per-rule reward trend visualization using Recharts.
  Renders inside the Improvements tab below `RecentImprovements` (NOT a new
  tab — logically part of improvements).
  (1) Create `frontend/src/components/RewardTrends.tsx`. Polls
  `GET /api/training/reward-trends?games=100` every 5000ms. Renders:
    - Summary table at top: `rule_id`, `total_contribution`,
      `contribution_per_game` (sortable by clicking headers).
    - Recharts `LineChart` below: x-axis = game index (0..n_games),
      y-axis = contribution, one line per rule (legend with rule names,
      click to toggle visibility).
    - Empty state when no reward logs exist yet.
    - Selector for games window: 50 / 100 / 200 / 500.
  (2) Render `RewardTrends` inside the existing Improvements tab below
  `RecentImprovements`.
  (3) Add unit test `RewardTrends.test.tsx` covering: loading, empty,
  populated, sort toggle, window selector. Mock the `/api/training/reward-trends`
  endpoint.
- **Issue:** #57
- **Flags:** --reviewers runtime --ui --start-cmd "bash scripts/start-dev.sh" --url http://localhost:3000 --ready-url http://localhost:8765/api/status
- **Produces:** `frontend/src/components/RewardTrends.tsx` (~180 lines), test
  file, updated Improvements tab.
- **Done when:** `npm run test:run` passes; `npx tsc --noEmit` clean;
  `npm run build` succeeds (verifies Recharts bundles correctly); Playwright
  screenshot of `RewardTrends` with at least the empty state.
- **Depends on:** Step 2, Step 4, Step 7

### Step 9: Alerts — rules, panel, toast, badge, localStorage (Phase 4 plan Step 4)
- **Status:** DONE (2026-04-10)
- **Problem:** Build the full in-app alert system end-to-end. Kept as a single
  step because alert rules, storage, hook, panel, and toast share types and
  state — splitting forces them to be designed twice.
  (1) Create `frontend/src/lib/alertRules.ts`:
    - `Alert` type: `{id: string, ruleId: string, severity: "info" | "warning"
      | "error", title: string, message: string, timestamp: string}`
    - `evaluateAlertRules(state)` that runs all six rules from D2 and returns
      `Alert[]`. Each rule produces a stable ID per D3 (e.g.,
      `win_rate_drop:0.62→0.41`).
    - Threshold constants at top of file (`WIN_RATE_DROP_THRESHOLD = 0.15`,
      `DISK_USAGE_WARNING_GB = 50`, `NO_TRAINING_HOURS = 24`).
  (2) Create `frontend/src/lib/alertStorage.ts`:
    - localStorage wrapper with key `alpha4gate.alerts.state` storing
      `{acked: string[], dismissed: string[]}`.
    - Functions: `loadAlertState()`, `ackAlert(id)`, `dismissAlert(id)`,
      `clearHistory()`, `markAllRead(ids)`.
    - Acked alerts stay visible but de-emphasized; dismissed alerts are
      filtered out forever.
  (3) Create `frontend/src/hooks/useAlerts.ts`:
    - Polls all four data sources (`/api/training/daemon`, `/api/training/triggers`,
      `/api/training/history`, `/api/training/status`,
      `/api/training/promotions/history`) every 5000ms. Reuses
      `useDaemonStatus` where possible to avoid duplicate polling.
    - Calls `evaluateAlertRules`, filters via `alertStorage`, returns
      `{alerts, unreadCount, ackAlert, dismissAlert, markAllRead, clearHistory,
      newAlertsThisPoll}`.
    - `newAlertsThisPoll` is the diff of current vs previous alert IDs, used
      to fire toasts.
  (4) Create `frontend/src/components/AlertToast.tsx`:
    - Renders fixed-position container in top-right of viewport.
    - Listens to `useAlerts` for `newAlertsThisPoll`. For each new alert,
      renders a toast card with severity color, title, message, and a "View"
      button that switches to the Alerts tab via lifted state from `App.tsx`.
    - Auto-dismisses after 8000ms via `setTimeout`. CSS animation for
      enter/exit using the classes scaffolded in Step 3.
  (5) Create `frontend/src/components/AlertsPanel.tsx`:
    - Full alerts list with severity filter (All / Errors / Warnings / Info).
    - Per-alert ack and dismiss buttons.
    - "Mark all read" button, "Clear history" button.
    - Sorted newest first. Acked alerts visually de-emphasized (gray
      background).
  (6) Update `frontend/src/App.tsx`:
    - Add "Alerts" tab (last in nav). Show unread count badge:
      `Alerts (3)` when `unreadCount > 0`.
    - Render `<AlertToast />` at the App root, OUTSIDE the tab switcher, so
      it is always visible regardless of active tab.
    - Lifted state for tab switching from a toast "View" button.
  (7) Add unit tests:
    - `alertRules.test.ts` — each of the 6 rules with synthetic state, ID
      stability, threshold edges.
    - `alertStorage.test.ts` — load/save, ack, dismiss, `clearHistory`,
      localStorage isolation between tests.
    - `AlertsPanel.test.tsx` — render, filter, ack, dismiss, mark all read.
    - `AlertToast.test.tsx` — appears on new alert, auto-dismisses after
      timeout (use `vi.useFakeTimers`).
- **Issue:** #58
- **Flags:** --reviewers runtime --ui --start-cmd "bash scripts/start-dev.sh" --url http://localhost:3000 --ready-url http://localhost:8765/api/status
- **Produces:** `frontend/src/lib/alertRules.ts` (~150 lines),
  `frontend/src/lib/alertStorage.ts` (~50 lines),
  `frontend/src/hooks/useAlerts.ts` (~80 lines),
  `frontend/src/components/AlertToast.tsx` (~80 lines),
  `frontend/src/components/AlertsPanel.tsx` (~150 lines), 4 test files,
  updated `App.tsx`.
- **Done when:** all vitest tests pass; `npx tsc --noEmit` clean;
  `npm run build` succeeds; Playwright screenshot showing the AlertsPanel
  with at least one synthetic alert and a toast.
- **Depends on:** Step 4, Step 5

### Step 10: Wiki + plan doc updates
- **Problem:** Update documentation to reflect Phase 4 final state.
  (1) Update `documentation/wiki/frontend.md`:
    - Add new tabs (Loop, Improvements, Alerts) to the tab table.
    - Update the file inventory table with all new components, hooks, and lib
      files.
    - Update polling intervals table.
    - Add a short section describing the in-app alert system.
  (2) Update `documentation/plans/always-up-plan.md`:
    - Mark all of Phase 4 as DONE. The legacy Phase 4 outline in that file
      lists 5 high-level steps; map them to the 10 steps of THIS plan as
      follows: legacy Step 1 (Training cycle status) → Step 5 (LoopStatus);
      legacy Step 2 (Recent improvements view) → Step 7 (RecentImprovements);
      legacy Step 3 (Per-rule reward trends) → Steps 1, 2, 8 (backend
      aggregator, endpoint, RewardTrends component); legacy Step 4 (Alerting)
      → Step 9 (Alerts); legacy Step 5 (Training trigger UI) → Step 6
      (TriggerControls). Steps 3, 4, 10 of this plan are infrastructure /
      docs that the legacy outline did not enumerate.
    - Update "Current State" section with new components, the new backend
      module, and the new endpoint.
    - Update "What's missing" by removing items now addressed.
    - Add a note that WebSocket upgrade for training data remains deferred
      (originally Phase 4 decision, now reaffirmed).
  (3) Update `CLAUDE.md` test count if it changed.
  (4) Update memory file `memory/alpha4gate.md` to reflect Phase 4 done and
  Phase 5 next.
- **Issue:** #59
- **Flags:** --reviewers code
- **Produces:** updated `documentation/wiki/frontend.md`,
  `documentation/plans/always-up-plan.md`, `CLAUDE.md`, `memory/alpha4gate.md`.
- **Done when:** `/session-check` on `documentation/wiki/frontend.md` and the
  plan doc reports no gaps; manual diff review of plan doc shows Phase 4 is
  fully reflected.
- **Depends on:** Steps 1-9

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|------|------|------------|
| Reward log volume grows unbounded | Aggregator may slow down as `data/reward_logs/` grows past thousands of files | Default `games=100` limits read scope. Sort by mtime descending and stop after N. Hard cap of 1000 on the query param. Defer caching until measured slowdown. |
| Recharts bundle size | Adds ~50 KB gzipped to frontend bundle | Acceptable for an internal-only dashboard. Verify `npm run build` succeeds in Step 3. |
| Polling load on backend | 9 components × 2-5s polling could create load spikes | All endpoints are read-only and cheap (DB queries, file walks). 5s poll matches existing pattern. Browser only polls active tab in background-throttled mode. |
| localStorage data loss | Browser clearing localStorage drops alert ack state | Acceptable — alerts are derived, not authoritative. State will rebuild on next poll. |
| Alert rule false positives | Win-rate-drop rule may fire spuriously during normal training variance | 15-point drop threshold is conservative. User can ack to silence. Tune via constants in `alertRules.ts` if needed. |
| Tab row overflow at 9 tabs | Single-row nav may wrap on narrow viewports | Desktop-only scope per "out of scope". Add CSS `flex-wrap: nowrap` + horizontal scroll if it becomes a problem in practice. |
| Disk size walk on every status poll | `os.walk` over `data/reward_logs/` runs on every `/api/training/status` request (~5s) | Walk is fast for a few hundred files. Add caching only if measured slowness. |
| TriggerControls coupling to many endpoints | A schema change in any of 8 endpoints could break the form | Type the responses explicitly. Leave existing endpoints stable per AGENTS.md "Keep public behavior stable". |
| Vitest setup quirks on Windows | First-time vitest config on Windows may have path/jsdom edge cases | Step 3 isolates this risk before any feature work. Sanity test proves the runner works before Step 4 builds on it. |

### Open questions deferred to build time
- **Toast stack behavior:** if 5 alerts fire at once, do toasts stack or replace? Default to **stack**, max 3 visible, oldest auto-dismissed first. Decide concretely in Step 9 if it doesn't feel right.
- **Charts for `LoopStatus`:** should `runs_completed` have a small sparkline? Defer — start with cards only, add if it feels lacking.
- **Empty states everywhere:** copy text for each empty state. Decide in each step's review.

## 9. Testing Strategy

### Backend tests (Steps 1-2)
- **Unit:** `tests/test_reward_aggregator.py` with synthetic JSONL fixtures using
  pytest `tmp_path`. Cover: empty dir, single game, multi-game/multi-rule,
  malformed lines, missing `rule_id`, file count above and below cap.
- **Endpoint:** `tests/test_api.py` for the new `/api/training/reward-trends`
  endpoint and the new `reward_logs_size_bytes` field on `/api/training/status`.
- **Verification:** `uv run pytest`, `uv run mypy src`, `uv run ruff check .`,
  `uv run ruff format --check .`. All 661 existing tests must continue to pass.

### Frontend tests (Steps 3-9)
- **Sanity:** `frontend/src/test/sanity.test.ts` — proves vitest runs
  (Step 3).
- **Component tests:** Each new component has a `*.test.tsx` file in the same
  directory using `@testing-library/react`. Mock `fetch` via vitest. Cover
  loading / empty / populated / error states + critical interactions.
- **Lib tests:** `alertRules.test.ts` and `alertStorage.test.ts` are pure
  function / DOM API tests, no React rendering needed.
- **Hook tests:** Tested indirectly via component tests (mocked fetch returns
  drive the hook output).
- **Type checking:** `cd frontend && npx tsc --noEmit` after every step.
- **Bundle check:** `cd frontend && npm run build` after Steps 3, 8, 9 to catch
  Recharts bundling issues and any other build-time errors.

### UI evidence (Steps 5-9)
- All five frontend feature steps use `--reviewers runtime --ui` flags. The
  `build-step` skill captures Playwright screenshots of the rendered tab as
  evidence. The runtime reviewer verifies the screenshot matches the problem
  statement.
- Manual smoke tests called out in each step's "Done when" criteria.

### What might break
- **Existing tests:** `tests/test_api.py` for `/api/training/status` will need
  updating in Step 1 to expect the new `reward_logs_size_bytes` field.
- **Frontend build:** Adding Recharts in Step 3 changes the bundle. Verify
  `npm run build` in the same step.
- **Type checking:** New tabs in `App.tsx` change the `Tab` union — any switch
  statements over `Tab` elsewhere in the frontend will need updating (none
  expected based on current code, but check during Step 5).

### End-to-end verification
After Step 10:
1. `cd . && uv sync` — install any new Python deps
   (none expected for Phase 4, but cheap and idempotent)
2. `cd frontend && npm install` — install vitest, Recharts, and the testing
   library deps added in Step 3
3. `cd . && uv run pytest --tb=no -q` — full
   suite passes
4. `uv run mypy src` — clean
5. `uv run ruff check .` — clean
6. `cd frontend && npm run test:run && npx tsc --noEmit && npm run build` —
   clean
7. Start backend: `uv run python -m alpha4gate.runner --serve` (or use `bash scripts/start-dev.sh` to start both in one terminal)
8. Start frontend: `cd frontend && npm run dev`
9. Visit `http://localhost:3000`, verify all 9 tabs render
10. Start the daemon via the Loop tab, watch state update within 2 seconds
11. Edit a daemon config field, save, refresh, confirm value persisted
12. Visit Improvements tab, verify promotion history and reward trends render
13. Trigger a synthetic alert (e.g., set `min_hours_since_last` to 0 to fire
    a "no training" alert), verify toast appears + Alerts tab badge updates
14. Ack an alert, refresh browser, verify ack state persists via localStorage
