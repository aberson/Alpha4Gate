# Frontend Dashboard

React SPA for autonomous-loop transparency: advised runs, evolve generations,
unified improvements timeline, system health, and alert triage.

> **At a glance:** 6-tab SPA (Advisor, Evolution, Improvements, Processes,
> Alerts, Help) built with React + TypeScript + Vite. Live state via REST
> polling (3–10s, with exceptions below); the in-app alert engine runs
> client-side over the polled snapshots. All frontend code is domain-agnostic
> — it renders whatever JSON the backend sends. Unit tests run under
> vitest + jsdom (112 passing).

## Purpose & Design

The dashboard mirrors how the project is actually used: autonomous
`/improve-bot-advised` and `/improve-bot-evolve` runs, with system monitoring
and alerts as support. The original 12-tab layout (one tab per phase added
during Phases 1–9) was trimmed in the dashboard refactor of 2026-04-29 — six
of the dropped tabs had never been used in practice and the
`RecentImprovements` / `AdvisedImprovements` / `RewardTrends` triple was
consolidated into a single `ImprovementsTab` with a source filter.

### Tab layout

| Tab | Component(s) | Data source | Refresh | Loop phase |
|-----|-------------|-------------|---------|---|
| **Advisor** | AdvisedControlPanel | `/api/advised/state` + `/api/advised/control` | 3s / 10s poll + on-demand | All advised-loop phases |
| **Evolution** | EvolutionTab | `/api/evolve/state` + `/api/evolve/control` + `/api/evolve/current-round` + `/api/evolve/pool` + `/api/evolve/results` | Polled (per-endpoint) + on-demand | Self-play arena |
| **Improvements** | ImprovementsTab | `/api/improvements/unified` (advised + evolve) | Refresh-on-demand | COMMIT (both loops) |
| **Processes** | ProcessMonitor + ResourceGauge + WslProcessesPanel | `/api/processes` + `/api/system/*` (separate router) | 5s poll | Cross-cutting (liveness) |
| **Alerts** | AlertsPanel (+ AlertToast overlay) | `useAlerts` hook (derives from polled training/advised endpoints) | 5s poll | Cross-cutting |
| **Help** | HelpTab | `/api/operator-commands` (reads `documentation/wiki/operator-commands.md` from disk) | One-time fetch | — |

The Advisor tab is the single source of truth for advised-loop state — it
reads `data/advised_run_state.json` via `/api/advised/state` and writes
`data/advised_run_control.json` via `/api/advised/control`. The Evolution tab
plays the same role for `data/evolve_run_state.json` and friends.

### What each component shows

**AdvisedControlPanel:** Live advised-run status (idle / running / paused),
current iteration / wall-clock budget, last result block (validation wins,
files changed, principles cited), strategic-hint injection form, and stop /
reset-loop buttons. Pulls from `useAdvisedRun`.

**EvolutionTab:** Live evolve-run status, current parent / generation,
fitness pool with per-imp rank + outcome, current-round game record,
generations-promoted counter, and a feed of recent results from
`evolve_results.jsonl`. Pulls from `useEvolveRun`.

**ImprovementsTab:** Unified table of advised + evolve improvements pulled
from `/api/improvements/unified`. Header has filter pills (All / Advised /
Evolve) + entry count + manual refresh button. Each row has timestamp,
source badge, title, outcome badge, metric blurb, and truncated principles
+ files-changed lists. Click a row to expand for full description, full
principle list, and full files list. Empty state and stale-data banner
handled via `useApi`.

**ProcessMonitor:** Live process inventory and health (Python, SC2, backend
listeners). Restart and kill-daemon controls.

**ResourceGauge:** Host CPU / memory / disk gauges sourced from the
`/api/system/*` endpoints in a separate FastAPI router.

**WslProcessesPanel:** WSL-side process inventory (relevant for Phase 8
Linux soaks); same separate-router source.

**AlertsPanel:** Full alert history with severity filter, ack/dismiss
actions, "mark all read", and "clear history". Persisted to `localStorage`
via `alertStorage.ts`.

**AlertToast:** Transient overlay that appears when a new alert fires this
poll, auto-dismissing after a timeout. Clicking it jumps to the Alerts tab.

**HelpTab:** Renders `documentation/wiki/operator-commands.md` from disk via
`react-markdown` + `remark-gfm`. The backend re-reads the markdown on each
request, so an edit to the `.md` surfaces here on the next page load
without a frontend rebuild.

**ConnectionStatus:** Header connection dot + advised-run badge mounted at
the App shell, outside the tab switch.

**StaleDataBanner:** Reusable stale-data warning shown by tabs whose
`useApi` hook reports a fetch error or staleness threshold exceeded.

**ConfirmDialog:** Reusable confirm modal (used by AdvisedControlPanel for
stop / reset-loop confirmations).

---

## Key Interfaces

### Custom hooks

| Hook | Purpose | Details |
|------|---------|---------|
| `useApi<T>(endpoint, opts)` | Generic REST polling hook | Optional `pollMs`, returns `{data, isLoading, isStale, lastSuccess, refetch}`. IndexedDB cache keyed by endpoint + schema version. |
| `useAdvisedRun()` | Advised run state polling + control mutations | 3s state poll + 10s control poll. Mutations via PUT. |
| `useEvolveRun()` | Evolve run state + pool + results polling | Per-endpoint polling cadences inside the hook. |
| `useDaemonStatus()` | Daemon + training-status polling | 5s poll of `/api/training/daemon` + `/api/training/status`. Currently only consumed by `useAlerts` for daemon-state alert rules — the dashboard refactor removed the Loop tab driver. |
| `useAlerts()` | Client-side alert engine | 5s poll of training + advised + promotions endpoints, runs `alertRules.ts` over the snapshot, persists via `alertStorage.ts`. |
| `useSystemInfo()` | Host resource snapshots | Backs ResourceGauge + WslProcessesPanel; reads the `/api/system/*` router. |

### Polling intervals

| Component | Interval | Method |
|-----------|----------|--------|
| AdvisedControlPanel (state) | 3000ms | `useAdvisedRun` / `useApi` poll |
| AdvisedControlPanel (control) | 10000ms | `useAdvisedRun` / `useApi` poll |
| EvolutionTab | per-endpoint inside `useEvolveRun` | `useApi` polls |
| ImprovementsTab | refresh-on-demand only | `useApi` (no `pollMs`) |
| ProcessMonitor / ResourceGauge / WslProcessesPanel | 5000ms | `useApi` / `useSystemInfo` |
| AlertToast / AlertsPanel (via `useAlerts`) | 5000ms | setInterval + fetch, rules evaluated client-side |
| HelpTab | one-time fetch on mount | `useApi` (no `pollMs`) |
| Everything else | One-time | useEffect fetch on mount |

---

## Implementation Notes

**Stack:** React 18 + TypeScript + Vite. Dev server on `:3000`, proxies to backend `:8765`.

**Routing:** Tab-based via `useState<Tab>("advisor")` — no React Router, just conditional
rendering based on active tab. Default tab is `advisor`.

**Frontend is domain-agnostic:** Components render whatever JSON the API returns. Unit
type names, strategic states, and command vocabulary come from the backend. No SC2
concepts are hardcoded in the frontend.

### In-app alert system

Alerts are generated client-side — there is no alert backend. The `useAlerts` hook
polls the training + advised + promotions endpoints on the usual 5s interval, builds
a snapshot, and evaluates the rules defined in `alertRules.ts` against it. Each rule
has a stable ID, a severity (`info`/`warning`/`critical`), and a threshold. Alerts are
deduplicated by ID over time.

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
as `*.test.tsx` / `*.test.ts`. Run with `npm test -- --run` or `npm run test:run`.

| File | Purpose |
|------|---------|
| `frontend/src/App.tsx` | 6-tab routing + top-level alert overlay + ConnectionStatus |
| `frontend/src/components/AdvisedControlPanel.tsx` | Advisor tab: live status, loop controls, hints, reward injection |
| `frontend/src/components/EvolutionTab.tsx` | Evolution tab: pool + current round + results feed |
| `frontend/src/components/ImprovementsTab.tsx` | Unified advised + evolve improvements timeline |
| `frontend/src/components/ProcessMonitor.tsx` | Live process inventory and health |
| `frontend/src/components/ResourceGauge.tsx` | Host CPU/memory/disk gauges |
| `frontend/src/components/WslProcessesPanel.tsx` | WSL-side process inventory |
| `frontend/src/components/AlertsPanel.tsx` | Full alert history + filter + ack |
| `frontend/src/components/AlertToast.tsx` | Transient new-alert overlay |
| `frontend/src/components/HelpTab.tsx` | Renders `operator-commands.md` via react-markdown |
| `frontend/src/components/ConnectionStatus.tsx` | Header connection dot + advised-run badge |
| `frontend/src/components/StaleDataBanner.tsx` | Reusable stale-data warning banner |
| `frontend/src/components/ConfirmDialog.tsx` | Reusable confirm modal |
| `frontend/src/components/CommandPanel.tsx` | (orphan — not currently mounted; predates refactor) |
| `frontend/src/components/BuildOrderEditor.tsx` | (orphan — not currently mounted; predates refactor) |
| `frontend/src/hooks/useApi.ts` | Generic REST polling hook with stale detection + IndexedDB cache |
| `frontend/src/hooks/useAdvisedRun.ts` | Advised run state polling + control mutations |
| `frontend/src/hooks/useEvolveRun.ts` | Evolve run state + pool + results polling |
| `frontend/src/hooks/useDaemonStatus.ts` | Daemon + training status polling (consumed only by `useAlerts`) |
| `frontend/src/hooks/useAlerts.ts` | Client-side alert engine + persistence |
| `frontend/src/hooks/useSystemInfo.ts` | Host resource snapshots backing the Processes tab |
| `frontend/src/lib/alertRules.ts` | Alert rule definitions and evaluator |
| `frontend/src/lib/alertStorage.ts` | `localStorage` persistence for alerts |
| `frontend/src/lib/idbCache.ts` | IndexedDB cache used by `useApi` |
| `frontend/vitest.config.ts` | Vitest config (jsdom + global setup) |
| `frontend/src/test/setup.ts` | Global test setup (matchers, mocks) |
| `frontend/src/test/sanity.test.ts` | Smoke test that the vitest stack loads |
