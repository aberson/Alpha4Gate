# Dashboard refactor — endpoint audit

**Generated:** 2026-04-29
**Source:** `bots/v0/api.py` (lines 252–1898 — 53 REST + 3 WebSocket decorators)
**Method:** Grep `frontend/src/` for every endpoint literal (`useApi(...)`, `fetch(...)`, `useWebSocket(...)`). Component-to-tab map taken from `frontend/src/App.tsx` (current 12-tab dispatch).
**Classification:** **KEPT** — at least one caller is a surviving tab (Advisor / Improvements-rebuild / Processes / Alerts / Evolution / Help) or the App shell. **DROP** — every caller is a doomed tab/component, OR there are no callers at all.

Surviving-tab component map (per plan §2):

- **Advisor** → `AdvisedControlPanel.tsx` (uses `useAdvisedRun`)
- **Improvements (rebuilt)** → new `ImprovementsTab.tsx` will hit only `/api/improvements/unified` (built in Step 2). No legacy endpoint here is "saved" by the rebuild — the rebuild is a fresh consumer of a new endpoint.
- **Processes** → `ProcessMonitor.tsx`, `ResourceGauge.tsx`, `WslProcessesPanel.tsx`
- **Alerts** → `AlertsPanel.tsx` (data via `useAlerts.ts`)
- **Evolution** → `EvolutionTab.tsx` (uses `useEvolveRun`)
- **Help** → `HelpTab.tsx`
- **App shell** → `App.tsx`, `ConnectionStatus.tsx`

Doomed components (per plan §3, §4): LiveView, Stats, DecisionQueue, TrainingDashboard, ModelComparison, ImprovementTimeline, CheckpointList, RewardRuleEditor, LoopStatus, TriggerControls, LadderTab, RewardTrends, RecentImprovements, AdvisedImprovements, BuildOrderEditor (not currently mounted in App.tsx — orphan), CommandPanel (only mounted inside LiveView).

---

## REST endpoints

| Endpoint | Method | Classification | Caller files (grep evidence) |
|---|---|---|---|
| `/api/status` | GET | **DROP** | No callers in `frontend/src/`. (Note: `ConnectionStatus.tsx:46` polls `/api/training/status`, not `/api/status` — different path.) |
| `/api/operator-commands` | GET | **KEPT** | `frontend/src/components/HelpTab.tsx:23` — `useApi("/api/operator-commands", ...)` (Help tab) |
| `/api/stats` | GET | **DROP** | `frontend/src/components/Stats.tsx:175` — Stats component is doomed |
| `/api/build-orders` | GET | **DROP** | `frontend/src/hooks/useBuildOrders.ts:10` — only consumer is `BuildOrderEditor.tsx`, which is not imported by `App.tsx` (orphan; effectively dead) |
| `/api/build-orders` | POST | **DROP** | `frontend/src/hooks/useBuildOrders.ts:25` — same as above |
| `/api/build-orders/{order_id}` | DELETE | **DROP** | `frontend/src/hooks/useBuildOrders.ts:36` — same as above |
| `/api/games` | GET | **DROP** | `frontend/src/components/Stats.tsx:192` — Stats is doomed |
| `/api/games/{game_id}` | GET | **DROP** | `frontend/src/components/Stats.tsx:109` — Stats is doomed |
| `/api/decision-log` | GET | **DROP** | `frontend/src/components/DecisionQueue.tsx:21` — DecisionQueue is doomed |
| `/api/game/start` | POST | **DROP** | No callers found in `frontend/src/` |
| `/api/game/batch` | POST | **DROP** | No callers found in `frontend/src/` |
| `/api/training/status` | GET | **KEPT** | `frontend/src/hooks/useAlerts.ts:86` (Alerts tab data pipeline) AND `frontend/src/components/ConnectionStatus.tsx:46` (App.tsx shell, mounted regardless of active tab). Also `TrainingDashboard.tsx:31` (doomed) — but the surviving callers carry it. |
| `/api/debug/raise_error` | POST | **DROP** | No fetch callers in `frontend/src/`; only documented in `lib/alertRules.ts:261` as a comment. (Was a manual debug trigger; orphan.) |
| `/api/training/reward-trends` | GET | **DROP** | `frontend/src/components/RewardTrends.tsx:172` — RewardTrends is subsumed/doomed |
| `/api/training/reset` | POST | **DROP** | `frontend/src/components/RewardTrends.tsx:226` — doomed |
| `/api/training/history` | GET | **KEPT** | `frontend/src/hooks/useAlerts.ts:85` (Alerts tab) — also called by doomed `TrainingDashboard.tsx:37` but Alerts pipeline keeps it alive |
| `/api/training/models` | GET | **DROP** | `frontend/src/components/ModelComparison.tsx:25`, `frontend/src/components/ImprovementTimeline.tsx:21` — both doomed |
| `/api/training/checkpoints` | GET | **DROP** | `frontend/src/components/CheckpointList.tsx:25`, `frontend/src/components/ModelComparison.tsx:27`, `frontend/src/components/TriggerControls.tsx:277` — all three doomed |
| `/api/training/start` | POST | **DROP** | `frontend/src/components/TriggerControls.tsx:250` — doomed |
| `/api/training/stop` | POST | **DROP** | `frontend/src/components/TriggerControls.tsx:261` — doomed |
| `/api/training/daemon` | GET | **DROP** | `frontend/src/hooks/useDaemonStatus.ts:95` — only consumed by `LoopStatus.tsx:112` (doomed) and `TriggerControls.tsx:183` (doomed). `useAlerts.ts:64` re-uses `useDaemonStatus` but only reads triggers/status to drive Loop-tab alerts; with Loop gone there is no surviving consumer. **See ambiguity note below.** |
| `/api/training/triggers` | GET | **DROP** | `frontend/src/hooks/useDaemonStatus.ts:96` — same chain as `/api/training/daemon` above; same ambiguity note |
| `/api/training/daemon/config` | PUT | **DROP** | `frontend/src/components/TriggerControls.tsx:231` — doomed |
| `/api/training/evaluate` | POST | **DROP** | `frontend/src/components/TriggerControls.tsx:318` — doomed |
| `/api/training/evaluate/{job_id}` | GET | **DROP** | No active callers in `frontend/src/`. Tested in `tests/test_api.py` but tests don't keep production endpoints alive. |
| `/api/training/evaluate/{job_id}/stop` | POST | **DROP** | No active callers in `frontend/src/`. Same note as above. |
| `/api/training/promotions` | GET | **DROP** | No callers found in `frontend/src/` |
| `/api/training/promotions/history` | GET | **KEPT** | `frontend/src/hooks/useAlerts.ts:87` — Alerts tab pipeline. Also called by doomed `RecentImprovements.tsx:176`; Alerts keeps it. |
| `/api/training/promotions/latest` | GET | **DROP** | No callers found in `frontend/src/` |
| `/api/improvements` | GET | **DROP** | `frontend/src/components/AdvisedImprovements.tsx:60` — doomed; per plan §6 design decision the new `/api/improvements/unified` endpoint replaces this and `/api/improvements` retires |
| `/api/training/promote` | POST | **DROP** | `frontend/src/components/TriggerControls.tsx:347` — doomed |
| `/api/training/rollback` | POST | **DROP** | `frontend/src/components/TriggerControls.tsx:368` — doomed |
| `/api/training/curriculum` | GET | **DROP** | `frontend/src/components/TriggerControls.tsx:388` — doomed |
| `/api/training/curriculum` | PUT | **DROP** | `frontend/src/components/TriggerControls.tsx:408` — doomed |
| `/api/advised/state` | GET | **KEPT** | `frontend/src/hooks/useAdvisedRun.ts:69` (Advisor tab) AND `frontend/src/hooks/useAlerts.ts:65` (Alerts tab) AND `frontend/src/components/ConnectionStatus.tsx:50` (App shell) |
| `/api/advised/control` | GET | **KEPT** | `frontend/src/hooks/useAdvisedRun.ts:70` — Advisor tab |
| `/api/advised/control` | PUT | **KEPT** | `frontend/src/hooks/useAdvisedRun.ts:73` — Advisor tab |
| `/api/evolve/state` | GET | **KEPT** | `frontend/src/hooks/useEvolveRun.ts:233` — Evolution tab |
| `/api/evolve/control` | GET | **KEPT** | `frontend/src/hooks/useEvolveRun.ts:237` — Evolution tab |
| `/api/evolve/control` | PUT | **KEPT** | `frontend/src/hooks/useEvolveRun.ts:259` — Evolution tab |
| `/api/evolve/current-round` | GET | **KEPT** | `frontend/src/hooks/useEvolveRun.ts:250` — Evolution tab |
| `/api/evolve/pool` | GET | **KEPT** | `frontend/src/hooks/useEvolveRun.ts:241` — Evolution tab |
| `/api/evolve/results` | GET | **KEPT** | `frontend/src/hooks/useEvolveRun.ts:245` — Evolution tab |
| `/api/processes` | GET | **KEPT** | `frontend/src/components/ProcessMonitor.tsx:81` — Processes tab |
| `/api/cleanup/stop-run` | POST | **KEPT** | `frontend/src/components/AdvisedControlPanel.tsx:225` — Advisor tab |
| `/api/cleanup/reset-loop` | POST | **KEPT** | `frontend/src/components/AdvisedControlPanel.tsx:241` — Advisor tab |
| `/api/kill-daemons` | POST | **KEPT** | `frontend/src/components/ProcessMonitor.tsx:98` — Processes tab |
| `/api/shutdown` | POST | **DROP** | No callers found in `frontend/src/` |
| `/api/restart` | POST | **KEPT** | `frontend/src/components/ProcessMonitor.tsx:88` — Processes tab |
| `/api/reward-rules` | GET | **DROP** | `frontend/src/components/RewardRuleEditor.tsx:26` — doomed |
| `/api/reward-rules` | PUT | **DROP** | `frontend/src/components/RewardRuleEditor.tsx:57` — doomed |
| `/api/commands` | POST | **DROP** | `frontend/src/components/CommandPanel.tsx:153` — CommandPanel is only rendered inside `LiveView.tsx` (3 mount sites at LiveView.tsx:11,20,69), and LiveView is doomed |
| `/api/commands/history` | GET | **DROP** | `frontend/src/components/CommandPanel.tsx:42` — same chain |
| `/api/commands/mode` | GET | **DROP** | `frontend/src/components/CommandPanel.tsx:34` — same chain |
| `/api/commands/mode` | PUT | **DROP** | `frontend/src/components/CommandPanel.tsx:219` — same chain |
| `/api/commands/settings` | GET | **DROP** | `frontend/src/components/CommandPanel.tsx:52` — same chain |
| `/api/commands/settings` | PUT | **DROP** | `frontend/src/components/CommandPanel.tsx:237` (and `:253`) — same chain |
| `/api/commands/primitives` | GET | **DROP** | `frontend/src/components/CommandPanel.tsx:47` — same chain |
| `/api/ladder` | GET | **DROP** | `frontend/src/components/LadderTab.tsx:28` — LadderTab is doomed |

---

## WebSocket endpoints

| Endpoint | Classification | Caller files |
|---|---|---|
| `/ws/game` | **DROP** | `frontend/src/hooks/useGameState.ts:13` — only consumer is `LiveView.tsx:5` (`useGameState()`); LiveView is doomed |
| `/ws/commands` | **DROP** | `frontend/src/components/CommandPanel.tsx:96` — CommandPanel is only mounted inside `LiveView.tsx`, doomed |
| `/ws/decisions` | **DROP** | `frontend/src/components/DecisionQueue.tsx:44` — DecisionQueue is doomed |

All three WebSocket endpoints can be retired alongside their tabs.

---

## Summary

- **Total endpoints in `bots/v0/api.py`:** 56 (53 REST + 3 WebSocket)
- **KEPT:** 19 (16 REST + 0 WebSocket; 3 endpoints are app-shell/cross-cutting via `useAlerts` + `ConnectionStatus`)
- **DROP:** 37 (34 REST + 3 WebSocket)

### KEPT endpoints (final surface)

REST:
- `/api/operator-commands` (Help)
- `/api/training/status` (Alerts + ConnectionStatus shell)
- `/api/training/history` (Alerts)
- `/api/training/promotions/history` (Alerts)
- `/api/advised/state`, `/api/advised/control` GET, `/api/advised/control` PUT (Advisor + cross-cut)
- `/api/cleanup/stop-run`, `/api/cleanup/reset-loop` (Advisor)
- `/api/evolve/state`, `/api/evolve/control` GET, `/api/evolve/control` PUT, `/api/evolve/current-round`, `/api/evolve/pool`, `/api/evolve/results` (Evolution)
- `/api/processes`, `/api/kill-daemons`, `/api/restart` (Processes)

WebSocket: none.

### Notes / edge cases / ambiguities

1. **`/api/training/daemon` and `/api/training/triggers` (`useDaemonStatus` chain).** Both are listed DROP. The hook `useDaemonStatus` is invoked from three places: `LoopStatus.tsx` (doomed), `TriggerControls.tsx` (doomed), and indirectly from `useAlerts.ts:64` (`const { status: daemon, triggers } = useDaemonStatus()`). The Alerts pipeline imports the hook to derive Loop-specific alert rules from the daemon/triggers payload. **With the Loop tab gone, the alert rules that depend on `daemon`/`triggers` become moot** and Step 6 should remove them along with the endpoints. If during Step 6 we find `useAlerts` still references the daemon/triggers values to produce a non-Loop alert, those endpoints become KEPT; verify in Step 6 implementation. Marking DROP under the assumption that loop-status alerting dies with the Loop tab.

2. **`/api/improvements`.** Doomed per plan §6 ("retire `/api/improvements` and replace with `/api/improvements/unified`"). The new endpoint is added in Step 2 — not in scope of this audit, which is strictly the existing surface.

3. **`/api/system/substrate`, `/api/system/wsl-processes`, `/api/system/resources`.** Referenced by `frontend/src/hooks/useSystemInfo.ts` (consumed by `ResourceGauge` and `WslProcessesPanel` in the Processes tab — both surviving). **These endpoints are NOT defined in `bots/v0/api.py`** (no decorator hits in the grep). They are served from a separate FastAPI router not found by the audit grep. Out of scope for this audit per the brief ("every endpoint in `bots/v0/api.py`"), but flagging because they are critical to the surviving Processes tab. Step 6 must NOT delete the source file that serves them; locate and confirm before any deletion sweep.

4. **Build orders endpoints (3).** `BuildOrderEditor.tsx` exists but is not imported by `App.tsx` — it is already an orphan today. All three `/api/build-orders*` endpoints are DROP regardless of refactor; they have been dead for some time.

5. **`/api/status`, `/api/game/start`, `/api/game/batch`, `/api/debug/raise_error`, `/api/training/promotions`, `/api/training/promotions/latest`, `/api/shutdown`, `/api/training/evaluate/{job_id}` GET, `/api/training/evaluate/{job_id}/stop` POST.** Nine endpoints with **no frontend callers at all**. All DROP. Some may still have backend test coverage in `tests/test_api*.py` — those tests should be removed alongside the endpoint per Step 6 policy ("tests don't keep production endpoints alive on their own").

6. **`/api/training/checkpoints`** (single GET) is called from three doomed components (`CheckpointList`, `ModelComparison`, `TriggerControls`) — three independent confirmations of DROP, no surviving caller.

7. **App-shell vs tab classification.** `ConnectionStatus.tsx` is rendered by `App.tsx:151` outside the tab switch, so its `/api/training/status` and `/api/advised/state` calls are app-shell-level — they keep those endpoints alive even if no tab consumes them. This is consistent with the brief's "App.tsx itself calls an endpoint at the app shell level → KEPT" rule.
