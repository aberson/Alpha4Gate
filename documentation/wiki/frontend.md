# Frontend Dashboard

React SPA for live game observation, training metrics, command input, and autonomous
loop transparency.

> **At a glance:** 10-tab SPA (Live, Stats, Decisions, Training, Loop,
> Advisor, Improvements, Processes, Alerts, Ladder) built with React + TypeScript + Vite. Live game data via
> WebSocket (real-time). Training and loop metrics via REST polling (5s, with exceptions below). Seven custom
> hooks handle WebSocket connections, API calls, and client-side alerting. All frontend
> code is domain-agnostic — it renders whatever JSON the backend sends. Unit tests run
> under vitest + jsdom.

## Purpose & Design

The dashboard provides transparency into what the bot is doing and how it's performing. The tab layout maps directly onto the autonomous loop phases from [improve-bot-advised-architecture.md](improve-bot-advised-architecture.md) + [monitoring.md](monitoring.md).

### Tab layout

| Tab | Component(s) | Data source | Refresh | Loop phase |
|-----|-------------|-------------|---------|---|
| **Live** | LiveView + CommandPanel | `/ws/game` + `/ws/commands` | Real-time WebSocket | THE TASK |
| **Stats** | Stats | `/api/stats` + `/api/games` (training.db) | 10s poll | PLAY + TEST |
| **Decisions** | DecisionQueue | `/api/decision-log` + `/ws/decisions` | Initial fetch + live | THINK + THE TASK |
| **Training** | TrainingDashboard + ModelComparison + CheckpointList + RewardRuleEditor | `/api/training/*` + `/api/reward-rules` | 5s poll + one-time | TRAIN |
| **Loop** | LoopStatus + TriggerControls | `/api/training/daemon` + `/api/training/status` + `/api/training/start`/`stop` | 5s poll + on-demand | TRAIN |
| **Advisor** | AdvisedControlPanel | `/api/advised/state` + `/api/advised/control` | 3s / 10s poll + on-demand | All 6 outer-loop phases |
| **Improvements** | AdvisedImprovements + RecentImprovements + RewardTrends | `/api/improvements` + `/api/training/promotions/history` + `/api/training/reward-trends` | 5-10s poll | COMMIT + TRAIN |
| **Processes** | ProcessMonitor | `/api/processes` | 5s poll | Cross-cutting (liveness) |
| **Alerts** | AlertsPanel (+ AlertToast overlay) | `useAlerts` hook (derives from `/api/training/*` polls) | 5s poll | Cross-cutting |
| **Ladder** | LadderTab | `/api/ladder` (reads `data/bot_ladder.json`) | 10s poll | Cross-cutting (Elo standings) |

The Advisor tab is the single source of truth for outer-loop state — it reads `bots/v0/data/advised_run_state.json` via `/api/advised/state` and writes `bots/v0/data/advised_run_control.json` via `/api/advised/control`.

### What each component shows

**LiveView:** Game time, minerals, gas, supply, score, strategic state. Unit and
structure lists. Claude advice (when present). Side panel: CommandPanel.

**CommandPanel:** Text input with autocomplete from primitives vocabulary. Command
history (last 20) with status badges (queued/executed/failed/rejected). Mode selector
(AI-Assisted/Human Only/Hybrid). Mute toggle. Collapsible settings (Claude interval,
lockout duration).

**TrainingDashboard:** Current checkpoint name, total games, total transitions, DB size,
checkpoint count. Win rate table: last 10/50/100/overall.

**CheckpointList:** Table of all checkpoints with metadata (type, agreement, win rate,
difficulty). Best checkpoint marked with indicator.

**RewardRuleEditor:** Editable table of reward rules — ID, description, reward value
(number input), active toggle. Save button persists via PUT.

**Stats:** Total wins/losses, per-difficulty win-rate table, and an expandable browsable game list from training.db. Click a game row to see a step-by-step reward timeline with fired rules (the former Games tab, consolidated into Stats in commit `b6c00c6`).

**DecisionQueue:** Last 20 state transitions: game step, from/to state, reason, Claude
advice.

**ModelComparison:** Per-checkpoint win rate table. Pulls `/api/training/models`.

**ImprovementTimeline:** Chronological model-version table with win-rate delta arrows — component exists at `frontend/src/components/ImprovementTimeline.tsx` but is not currently wired into a tab; `RecentImprovements` on the Improvements tab supersedes it.

**LoopStatus:** Daemon state (idle/checking/training), last run, next check, runs
completed, trigger preview, transitions-since-last counter, reward-log disk usage
(`reward_logs_size_bytes` from `/api/training/status`).

**TriggerControls:** Start/stop daemon buttons gated by a ConfirmDialog. Editable
daemon config (check interval, min transitions, min hours, cycles per run, games per
cycle) persisted via `PUT /api/training/daemon/config`.

**RecentImprovements:** Last N entries from `/api/training/promotions/history`,
separating promotions from rollbacks (a rollback is `promoted: false` with a
`reason` starting with `rollback:`). Shows win-rate delta, difficulty, timestamp.

**RewardTrends:** Per-rule reward contribution table derived from
`/api/training/reward-trends?games=N`. Shows total reward, fire count, average
per fire, and share of total reward across the window.

**AlertsPanel:** Full alert history with severity filter, ack/dismiss actions,
"mark all read", and "clear history". Persisted to `localStorage` via
`alertStorage.ts`.

**AlertToast:** Transient overlay that appears when a new alert fires this poll,
auto-dismissing after a timeout. Clicking it jumps to the Alerts tab.

---

## Key Interfaces

### Custom hooks

| Hook | Purpose | Details |
|------|---------|---------|
| `useWebSocket({url, onMessage, reconnectInterval})` | Generic WS client | Auto-reconnect (3s default), JSON parsing, cleanup on unmount |
| `useGameState()` | Live game state | Wraps useWebSocket for `/ws/game`, returns `{gameState, connected}` |
| `useBuildOrders()` | Build order CRUD | GET/POST/DELETE via `/api/build-orders`, returns `{orders, loading, createOrder, deleteOrder, refresh}` (backend API remains; no longer used by a visible tab) |
| `useDaemonStatus()` | Loop status polling | 5s poll of `/api/training/daemon` + `/api/training/status`, returns merged `{daemon, status, error}` |
| `useAlerts()` | Client-side alert engine | 5s poll of training endpoints, runs `alertRules.ts` over the snapshot, persists via `alertStorage.ts`, returns `{alerts, ackedIds, unreadCount, newAlertsThisPoll, ackAlert, dismissAlert, markAllRead, clearHistory}` |

### Polling intervals

| Component | Interval | Method |
|-----------|----------|--------|
| LiveView | Real-time | WebSocket |
| CommandPanel | Real-time | WebSocket + initial REST |
| AdvisedControlPanel (state) | 3000ms | `useAdvisedRun` / `useApi` poll |
| AdvisedControlPanel (control) | 10000ms | `useAdvisedRun` / `useApi` poll |
| TrainingDashboard | 5000ms | setInterval + fetch |
| ModelComparison | 5000ms | setInterval + fetch |
| LoopStatus (via `useDaemonStatus`) | 5000ms | setInterval + fetch |
| RecentImprovements / RewardTrends | 5000ms | setInterval + fetch |
| Stats | 10000ms | setInterval + fetch |
| ProcessMonitor | 5000ms | setInterval + fetch |
| AlertToast / AlertsPanel (via `useAlerts`) | 5000ms | setInterval + fetch, rules evaluated client-side |
| Everything else | One-time | useEffect fetch on mount |
| WebSocket reconnect | 3000ms | useWebSocket hook |

---

## Implementation Notes

**Stack:** React 18 + TypeScript + Vite. Dev server on `:3000`, proxies to backend `:8765`.

**Routing:** Tab-based via `useState<Tab>("live")` — no React Router, just conditional
rendering based on active tab.

**Frontend is domain-agnostic:** Components render whatever JSON the API returns. Unit
type names, strategic states, and command vocabulary come from the backend. No SC2
concepts are hardcoded in the frontend.

### In-app alert system

Alerts are generated client-side — there is no alert backend. The `useAlerts` hook
polls the training endpoints on the usual 5s interval, builds a `TrainingSnapshot`,
and evaluates the rules defined in `alertRules.ts` against it. Each rule has a stable
ID, a severity (`info`/`warning`/`critical`), and a threshold. Alerts are deduplicated
by ID over time.

State is persisted to `localStorage` via `alertStorage.ts`: the full alert history,
the set of acknowledged IDs, and a "cleared-before" watermark. Acks and dismissals
survive page reloads; "clear history" resets the watermark without losing the
underlying rule definitions.

Surface: `AlertToast` appears as a transient overlay whenever a new alert fires this
poll (auto-dismisses), while `AlertsPanel` is the full history view on the Alerts
tab with filtering, per-alert ack/dismiss, "mark all read", and an unread-count
badge on the tab button.

### Test infrastructure

Unit tests run via vitest with jsdom. Config lives in `frontend/vitest.config.ts`;
global setup is `frontend/src/test/setup.ts`; a smoke test lives at
`frontend/src/test/sanity.test.ts`. Per-component tests sit alongside their source
as `*.test.tsx` / `*.test.ts`. Run with `npm run test:run`.

| File | Purpose |
|------|---------|
| `frontend/src/App.tsx` | Tab routing + top-level alert overlay |
| `frontend/src/components/LiveView.tsx` | Live game display |
| `frontend/src/components/CommandPanel.tsx` | Command input + history |
| `frontend/src/components/TrainingDashboard.tsx` | Training metrics |
| `frontend/src/components/ModelComparison.tsx` | Per-checkpoint win rate table |
| `frontend/src/components/ImprovementTimeline.tsx` | Chronological win-rate delta table |
| `frontend/src/components/CheckpointList.tsx` | Model checkpoints |
| `frontend/src/components/RewardRuleEditor.tsx` | Reward rule editing |
| `frontend/src/components/Stats.tsx` | Per-difficulty win rates + expandable game history with reward timeline |
| `frontend/src/components/DecisionQueue.tsx` | Decision log |
| `frontend/src/components/ProcessMonitor.tsx` | Live process inventory and health |
| `frontend/src/components/AdvisedImprovements.tsx` | Advisor-driven improvement history |
| `frontend/src/components/ConfirmDialog.tsx` | Reusable confirm modal (used by TriggerControls) |
| `frontend/src/components/LoopStatus.tsx` | Daemon state + trigger preview + disk usage |
| `frontend/src/components/TriggerControls.tsx` | Start/stop daemon + editable config |
| `frontend/src/components/RecentImprovements.tsx` | Promotions + rollbacks timeline |
| `frontend/src/components/RewardTrends.tsx` | Per-rule reward contribution table |
| `frontend/src/components/AlertToast.tsx` | Transient new-alert overlay |
| `frontend/src/components/AlertsPanel.tsx` | Full alert history + filter + ack |
| `frontend/src/components/AdvisedControlPanel.tsx` | Advisor tab: live status, loop controls, hints, reward injection |
| `frontend/src/components/ConnectionStatus.tsx` | Header connection dot + advised-run badge |
| `frontend/src/components/LadderTab.tsx` | Elo standings table + head-to-head grid (Phase 4) |
| `frontend/src/components/StaleDataBanner.tsx` | Reusable stale-data warning banner |
| `frontend/src/hooks/useWebSocket.ts` | Generic WS hook |
| `frontend/src/hooks/useGameState.ts` | Game state WS hook |
| `frontend/src/hooks/useDaemonStatus.ts` | Daemon + training status polling |
| `frontend/src/hooks/useAlerts.ts` | Client-side alert engine + persistence |
| `frontend/src/hooks/useApi.ts` | Generic REST polling hook with stale detection |
| `frontend/src/hooks/useAdvisedRun.ts` | Advised run state polling + control mutations |
| `frontend/src/lib/alertRules.ts` | Alert rule definitions and evaluator |
| `frontend/src/lib/alertStorage.ts` | `localStorage` persistence for alerts |
| `frontend/vitest.config.ts` | Vitest config (jsdom + global setup) |
| `frontend/src/test/setup.ts` | Global test setup (matchers, mocks) |
| `frontend/src/test/sanity.test.ts` | Smoke test that the vitest stack loads |
