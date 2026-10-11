# Frontend Dashboard

React SPA for autonomous-loop transparency: advised runs, evolve generations,
unified improvements timeline, system health, and alert triage.

> **At a glance:** 7-tab SPA (Advisor, Evolution, Models, Observable,
> Processes, Help, Jev) built with React + TypeScript + Vite. Live state via REST
> polling (3–10s, with exceptions below); the in-app alert engine runs
> client-side over the polled snapshots. Frontend code is domain-agnostic
> (the Jev tab is the exception, below) — it renders whatever
> JSON the backend sends. Unit tests run under
> vitest + jsdom (399 tests — 393 passing, 6 skipped — across 27 files).

## Purpose & Design

The dashboard mirrors how the project is actually used: autonomous
`/improve-bot-advised` and `/improve-bot-evolve` runs, with system monitoring
and alerts as support. The original 12-tab layout (one tab per phase added
during Phases 1–9) was trimmed in the dashboard refactor of 2026-04-29 — six
of the dropped tabs had never been used in practice and the
`RecentImprovements` / `AdvisedImprovements` / `RewardTrends` triple was
consolidated into the Models tab's Lineage timeline (`LineageView` /
`TimelineList`) with a source filter.

### Tab layout

| Tab | Component(s) | Data source | Refresh | Loop phase |
|-----|-------------|-------------|---------|---|
| **Advisor** | AdvisedControlPanel | `/api/advised/state` + `/api/advised/control` | 3s / 10s poll + on-demand | All advised-loop phases |
| **Evolution** | EvolutionTab | `/api/evolve/state` + `/api/evolve/control` + `/api/evolve/current-round` + `/api/evolve/pool` + `/api/evolve/results` + `/api/evolve/lineages` | Polled (per-endpoint) + on-demand | Self-play arena |
| **Models** | ModelsTab (LineageView / LiveRunsGrid / VersionInspector / CompareView / ForensicsView) | `/api/improvements/unified` (advised + evolve) via Lineage timeline mode | Refresh-on-demand | COMMIT (both loops) |
| **Observable** | ObservableTab | Exhibition / replay-stream surface (Phase L placeholder) | On-demand | — |
| **Processes** | ProcessMonitor + ResourceGauge + WslProcessesPanel + AlertsPanel | `/api/processes` + `/api/system/*` (separate router); alerts via the `useAlerts` hook | 5s poll | Cross-cutting (liveness + alerts) |
| **Help** | HelpTab | `/api/operator-commands` (reads `documentation/wiki/operator-commands.md` from disk) | One-time fetch | — |
| **Jev** | JevTab (JevGraph, JevPlanPanel) | `/api/jev/runs` + `/api/jev/runs/{run_id}` + `/api/jev/runs/{run_id}/policy` (read-only, served from `data/jev/runs`); with `?launch=<session_id>` also `GET /api/jev/launches/{session_id}` and the readiness receipt `POST /api/jev/launches/{session_id}/ready`, sent after the launched run is rendered and retried on each session poll until accepted (writes only `data/jev/launches/<session_id>/ready.json`; loopback client and Host, dashboard Origin `http://localhost:3000` or `http://127.0.0.1:3000` only) | 5s run-list poll + 1s selected-run poll; archived policy retried on the 1s poll until it first loads, then never refetched (immutable); 4s request timeout; with `?launch=`, the launch session polled every 1s from a Web Worker timer and again on visibilitychange | — (independent Jev player, outside both loops) |

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

**Models tab — Lineage timeline (`LineageView.tsx` / `TimelineList.tsx`):**
Unified table of advised + evolve improvements pulled
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
poll, auto-dismissing after a timeout. Its **View** button jumps to the Processes
tab, where AlertsPanel lives (there is no separate Alerts tab), and dismisses that
toast.

**HelpTab:** Renders `documentation/wiki/operator-commands.md` from disk via
`react-markdown` + `remark-gfm`. The backend re-reads the markdown on each
request, so an edit to the `.md` surfaces here on the next page load
without a frontend rebuild.

**JevTab:** Read-only browser for Jev runs: a run selector, the run's archived
policy shown as a graph, node details (the policy definition from the archived
policy, latest evaluations, command events, and tasks with deadlines), and the
recent trace, which labels each status change as a runtime transition. Each
snapshot is labeled live, stale, offline, or final, and only a live run
animates. Pulls from `useJevRun`. Opened by a dashboard-first launch
(`/?tab=jev&launch=<session_id>`, from `scripts/launch-jev.ps1` or
`scripts/benchmark_jev.py`), it follows that launch session's exact run: Preparing
until the session names its run, then (after the page has rendered that run and its
archived policy and acknowledged it) Starting, Live and Finished; a benchmark batch
moves to the next game in the same tab. Picking another run pauses following until
**Resume live**. A launcher that stops writing its session shows **Launcher not
responding**. An **Army decision** panel shows the configured provider and whether
each decision came from the Typesafe model or the scripted fallback. For a v2 run (a
policy with `start_plan` nodes or hosted v2 questions) a **Plans and decisions** panel
(`JevPlanPanel`) lists each plan's status, stage, blocker or outcome, holds and who
decided it (with the request id), each plan slot's open plan, next permitted kinds,
cooldown and turn, and each question family's decision source; v1 and older runs keep
the earlier layout. The node inspector names the node's lane and the request, fallback
or local decision behind it. All of this is derived by the pure `jevEvidence.ts`
(`graphActivity`, `plansEvidence`) from the latest state and recent events; nothing is
interpolated between polls.

The graph defaults to compact **Nodes**, with stable branch-prefixed IDs such as
`A1` (Army) and `R1` (Reserve); **Boxes** restores the descriptive tiles. Branch
cards toggle independently, **Add all** shows every branch, and **Collapse all**
clears the canvas so branches can be added one at a time. Independent branches
are arranged in columns. Node details appear below the full-width graph; run
metadata and graph help are collapsed disclosures. Selecting a plan stage adds
its branch without removing others. These controls change only the presentation,
not the archived policy or gameplay.

**Nodes** offers **Tree** and **Radial** layouts. Tree packs branches according to
their actual dimensions and the viewport aspect ratio, so a growing Army branch
does not force every other branch into equally tall rows. Radial allocates sectors
by subtree leaf count and separates overlapping labels. Compact tiles are 52 x 20
graph units, with short IDs; full details remain in the inspector. **Expand all**
followed by **Fit** includes every displayed node, even below the manual zoom floor.
The canvas uses more of the available window height; Fullscreen remains available.

**Dots 3D** projects a three-dimensional layout with branch colors and the same
recorded activity highlights. Drag empty space to rotate, Shift-drag to pan, and
scroll to zoom; keyboard-accessible rotation/tilt buttons are also available.
Hover supplies the full node tooltip; selecting a dot labels it and opens its
original node in the inspector. Expansion and selection persist across views.
There is no automatic camera motion. Last-known states remain labeled and muted;
reduced motion disables activity animation in this view too. The 3D view uses SVG
projection and adds no rendering dependency or service calls.

Shapes include Sphere, Hollow sphere, Branch faces and Cone. A visible Start hub
and permanent branch-root labels orient the forest; an optional inspector overlay
keeps selected-node details inside the graphic. These are presentation choices,
not new runtime graph nodes.

**JevGraph:** `d3-hierarchy` SVG tree of the archived policy forest with active
and waiting node status. Supports pan, zoom, and collapse, and is a
keyboard-navigable ARIA tree. Each root lane has a colour (a band, a node stripe,
and a legend with active/waiting counts). Only recorded activity is drawn: the latest
state's running path solid, and edges named by node events in the last
`JEV_TRAIL_SECONDS` (2) game seconds as a dashed, fading recent trail. A non-live
state is labeled last known, and reduced motion stops the animation. **Follow
activity** (off by default, live runs only) brings the newest active node into view
without changing the selection.

**ConnectionStatus:** Header connection dot + advised-run badge mounted at
the App shell, outside the tab switch.

**JevBattleView:** `?battle=1&run=<run_id>` selects a standalone Windows preview
instead of the normal tab shell. Reuses `useJevRun` and `JevGraph` with Branch faces,
all nodes initially expanded, decision summaries and a three-entry event log.
`scripts/launch-jev-battle.ps1` starts/reuses dashboard and isolated OBS, then
`scripts/probes/jev_battle_view.py --interactive` hosts Edge and SC2 as sibling
native child windows. The injected `battleAction` binding supplies recording,
camera lock and Stop battle; this is not a new HTTP API. `-AutoRecord` starts
recording when gameplay is ready. Both desktop launchers use Typesafe `jev-1.13.0` with the saved key;
only recording differs. The preview is fixed
1920×1080, with game-process audio and no microphone. See the
[operator guide](../operator/jev-battle-view.md) and
[native capture findings](../investigations/jev-battle-view-feasibility-2026-10-11.md).

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
| `useVersions()` | Version registry | `/api/versions` via `useApi`. Consumed by ModelsTab, VersionInspector, CompareView, ObservableTab. |
| `useVersionDetail(v)` | Per-version detail | `/api/versions/{v}/{config,training-history,actions,improvements,weight-dynamics}` via `useApi`. Consumed by VersionInspector, CompareView, ForensicsView. |
| `useLineage()` | Lineage DAG | `/api/lineage` via `useApi`. Consumed by LineageView. |
| `useRunsActive()` | In-flight runs | 2s poll of `/api/runs/active` via `useApi`. Consumed by LiveRunsGrid. |
| `useGameForensics()` | Per-game forensics | `/api/versions/{v}/forensics/{game_id}` via `useApi`. Consumed by ForensicsView. |
| `useJevRun()` | Jev run list + selected run + archived policy | 5s list poll, 1s selected-run poll, 4s abort timeout per request. The policy is retried on the 1s poll until it first loads, then never refetched. Selects the newest run when none is selected yet (the first non-empty list response) and keeps that selection until the user picks another, so it does not switch to runs started later. With `?run=<run_id>` it shows exactly that run; with `?launch=<session_id>` (which wins over `run`) it polls the session every 1s and follows its active run, never guessing the newest; it POSTs `/api/jev/launches/{id}/ready` once the run and policy are rendered, and an invalid ID selects nothing. It clears its timers and aborts requests on unmount. |

### Polling intervals

| Component | Interval | Method |
|-----------|----------|--------|
| AdvisedControlPanel (state) | 3000ms | `useAdvisedRun` / `useApi` poll |
| AdvisedControlPanel (control) | 10000ms | `useAdvisedRun` / `useApi` poll |
| EvolutionTab | per-endpoint inside `useEvolveRun` | `useApi` polls |
| ModelsTab (LineageView) | refresh-on-demand only | `useApi` (no `pollMs`) |
| ProcessMonitor / ResourceGauge / WslProcessesPanel | 5000ms | `useApi` / `useSystemInfo` |
| AlertToast / AlertsPanel (via `useAlerts`) | 5000ms | setInterval + fetch, rules evaluated client-side |
| HelpTab | one-time fetch on mount | `useApi` (no `pollMs`) |
| JevTab | 1000ms selected run / 5000ms run list / 1000ms launch session (`?launch=` only; Web Worker ticker plus visibilitychange, so a hidden page keeps following) | `useJevRun` (setInterval + fetch with AbortController) |
| ModelsTab (LiveRunsGrid) | 2000ms | `useRunsActive` (`useApi` with `pollMs: 2000`) |
| Everything else | One-time | useEffect fetch on mount |

---

## Implementation Notes

**Stack:** React 18 + TypeScript + Vite. Dev server on `:3000`, proxies to backend `:8765`.

**Routing:** Tab-based via `useState<Tab>(initialTab)` — no React Router, just conditional
rendering based on active tab. The `/?tab=<name>` deep link (case-insensitive, one of the 7
`TAB_NAMES`) picks the initial tab; an unknown or absent value falls back to `advisor`.
Operators open the Jev tab with `/?tab=jev`; `/?tab=jev&run=<run_id>` selects an exact
run and `/?tab=jev&launch=<session_id>` follows a dashboard-first launch session.

**Frontend is domain-agnostic:** Components render whatever JSON the API returns. Unit
type names, strategic states, and command vocabulary come from the backend. No SC2
concepts are hardcoded in the frontend. The exception is the Jev tab (an SC2 player's
viewer): its lane colours are keyed to v2's lane names (`KNOWN_LANE_TONES` in
`frontend/src/types/jev.ts`), it knows the hosted question families by name
(`JEV_DECISION_REASONS`), and its plan panel labels reserved resources as minerals and gas.

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
poll (auto-dismisses), while `AlertsPanel` is the full history view on the Processes
tab with filtering, per-alert ack/dismiss, and "mark all read". (`useAlerts` still
computes an unread count, but no tab button renders it since the Alerts tab was folded
into Processes.)

### Test infrastructure

Unit tests run via vitest with jsdom. Config lives in `frontend/vitest.config.ts`;
global setup is `frontend/src/test/setup.ts`; a smoke test lives at
`frontend/src/test/sanity.test.ts`. Per-component tests sit alongside their source
as `*.test.tsx` / `*.test.ts`. Run with `npm test -- --run` or `npm run test:run`.

| File | Purpose |
|------|---------|
| `frontend/src/App.tsx` | 7-tab routing (with the `/?tab=<name>` deep link) + top-level alert overlay + ConnectionStatus |
| `frontend/src/components/AdvisedControlPanel.tsx` | Advisor tab: live status, loop controls, hints, reward injection |
| `frontend/src/components/EvolutionTab.tsx` | Evolution tab: pool + current round + results feed |
| `frontend/src/components/ModelsTab.tsx` | Models tab shell: lineage, live runs, version inspector, compare, forensics |
| `frontend/src/components/ObservableTab.tsx` | Observable tab: exhibition / replay-stream surface |
| `frontend/src/components/LineageView.tsx` | Lineage timeline (absorbs the former Improvements timeline) |
| `frontend/src/components/TimelineList.tsx` | Improvements timeline row list (used by LineageView and VersionInspector) |
| `frontend/src/components/LiveRunsGrid.tsx` | Grid of in-flight runs |
| `frontend/src/components/VersionInspector.tsx` | Per-version detail inspector |
| `frontend/src/components/CompareView.tsx` | Side-by-side version comparison |
| `frontend/src/components/ForensicsView.tsx` | Post-hoc run forensics |
| `frontend/src/components/ProcessMonitor.tsx` | Live process inventory and health |
| `frontend/src/components/ResourceGauge.tsx` | Host CPU/memory/disk gauges |
| `frontend/src/components/WslProcessesPanel.tsx` | WSL-side process inventory |
| `frontend/src/components/AlertsPanel.tsx` | Full alert history + filter + ack |
| `frontend/src/components/AlertToast.tsx` | Transient new-alert overlay |
| `frontend/src/components/HelpTab.tsx` | Renders `operator-commands.md` via react-markdown |
| `frontend/src/components/JevTab.tsx` | Jev tab: read-only run browser |
| `frontend/src/components/JevGraph.tsx` | Jev policy graph view (d3-hierarchy SVG tree; colored root lanes, recorded edge activity, Follow activity) |
| `frontend/src/components/JevPlanPanel.tsx` | Jev tab: Plans and decisions panel (plans, plan slots, decision sources) for v2 runs |
| `frontend/src/components/jevEvidence.ts` | Pure derivations from a run's evidence: graph edge activity, node decision sources, plans/slots/decisions |
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
| `frontend/src/hooks/useJevRun.ts` | Jev run list + selected run + archived policy polling |
| `frontend/src/hooks/useVersions.ts` | Version registry (`/api/versions`) |
| `frontend/src/hooks/useVersionDetail.ts` | Per-version config / training history / actions / improvements / weight dynamics |
| `frontend/src/hooks/useLineage.ts` | Lineage DAG (`/api/lineage`) |
| `frontend/src/hooks/useRunsActive.ts` | In-flight runs, 2s poll (`/api/runs/active`) |
| `frontend/src/hooks/useGameForensics.ts` | Per-game forensics (`/api/versions/{v}/forensics/{game_id}`) |
| `frontend/src/hooks/useBuildOrders.ts` | (orphan — used only by the unmounted BuildOrderEditor) |
| `frontend/src/hooks/useGameState.ts` | (orphan — no consumer) |
| `frontend/src/hooks/useWebSocket.ts` | (orphan — used only by the unmounted CommandPanel and useGameState; 3s reconnect) |
| `frontend/src/lib/alertRules.ts` | Alert rule definitions and evaluator |
| `frontend/src/lib/alertStorage.ts` | `localStorage` persistence for alerts |
| `frontend/src/lib/idbCache.ts` | IndexedDB cache used by `useApi` |
| `frontend/vitest.config.ts` | Vitest config (jsdom + global setup) |
| `frontend/src/test/setup.ts` | Global test setup (matchers, mocks) |
| `frontend/src/test/sanity.test.ts` | Smoke test that the vitest stack loads |

#### Dots 3D shapes and node details

Dots 3D offers Sphere (default, nodes throughout the volume), Hollow sphere (nodes on the surface), Branch faces (one planar prism face per branch), and the original Cone. Sphere sectors scale with visible branch size. All shapes preserve the same node IDs, selection, recorded edges, and live/stale status; shape outlines are decorative. Rotate to expose dots hidden behind others.

Use **Node details ? In graph** for a scrollable inspector in the lower-right corner of the graphic, or **Below graph** for the full-width inspector. The overlay may cover dots; rotate/pan or switch it below when needed. Shape and placement choices last for the current run view.

Radial and Dots 3D include a **Start** orientation hub with dashed guides to visible roots. This synthetic hub is not an executable policy node and its guides are never animated as telemetry. Dots 3D always labels branch roots by code and name (for example, A1 ? Army). Hollow sphere now orders levels from the upper cap downward within branch sectors to make ancestry easier to follow.
