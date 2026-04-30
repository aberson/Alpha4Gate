# Dashboard refactor — trim to 5-6 tabs + unified Improvements view

**Date drafted:** 2026-04-29.
**Track:** Dashboard / operator UX.
**Prerequisites:** None — frontend-only refactor + small backend endpoint additions/retirements. Independent of Phase 8 Step 11 (the Linux soak running tonight).
**Slot:** Not a numbered phase. Dashboard hygiene PR.

---

## 1. What this feature does

Reduces the dashboard from 12 tabs to **5–6 tabs** matching how the project is actually used today (autonomous improve-bot-advised + improve-bot-evolve, with system monitoring as support). Replaces the conceptually-split `RecentImprovements` + `AdvisedImprovements` panels with a **single unified Improvements timeline** that pulls from both `data/improvement_log.json` (advised) and `data/evolve_results.jsonl` (evolve), tagged by source so each entry is filterable.

**Why this matters:**

The dashboard accumulated 12 tabs over Phases 1–9 because each phase added its own UI. The user's actual workflow is now:
- `/improve-bot-advised` for guided improvement loops
- `/improve-bot-evolve` (or `scripts/evolve.py`) for autonomous evolution
- Watch processes / errors when something seems stuck

Tabs that supported earlier-phase workflows (PPO daemon, manual training cycles, Elo ladder) are vestigial. Six of them have never been used in the user's routine. The split between "RecentImprovements" (advised) and "AdvisedImprovements" (more advised) is also confusing — they both serve the same operator question ("what changed and did it work").

This refactor cuts that cognitive load and makes the dashboard reflect the substrate-not-just-phase reality of where the project landed.

---

## 2. Existing context

A fresh-context model needs to know:

**Current 12 tabs and components.**

| Tab | Components rendered | Backend endpoints | Disposition |
|---|---|---|---|
| Live | LiveView | /api/status, /ws/game | DROP |
| Stats | Stats | /api/stats, /api/games | DROP |
| Decisions | DecisionQueue | /api/decision-log, /ws/decisions | DROP |
| Training | TrainingDashboard, ModelComparison, ImprovementTimeline, CheckpointList, RewardRuleEditor | /api/training/* (many) | DROP |
| Loop | LoopStatus, TriggerControls | /api/training/triggers, /api/training/daemon | DROP |
| Advisor | AdvisedControlPanel | /api/advised/state, /api/advised/control | KEEP |
| Improvements | RewardTrends, RecentImprovements, AdvisedImprovements | /api/improvements, /api/training/reward-trends | REBUILD as unified |
| Processes | ProcessMonitor, ResourceGauge, WslProcessesPanel | /api/processes, /api/resources | KEEP |
| Alerts | AlertsPanel | (alerts pipeline, error-log-buffer) | KEEP |
| Ladder | LadderTab | /api/ladder | DROP (never populated; `scripts/ladder.py --eval-only` is the populator and nobody runs it) |
| Evolution | EvolutionTab | /api/evolve/state, /api/evolve/control, /api/evolve/current-round, /api/evolve/pool | KEEP |
| Help | HelpTab | /api/operator-commands | KEEP (just added) |

**Data sources for unified Improvements view.**

- `data/improvement_log.json` — advised entries. Schema: `id`, `timestamp`, `run_id`, `iteration`, `title`, `type` (`training` | `dev`), `description`, `principles[]`, `result` (`pass` | `stopped` | `fail`), `metrics` (e.g. `observation_wins`, `validation_wins`), `files_changed[]`. Already served by `GET /api/improvements`.
- `data/evolve_results.jsonl` — JSONL, one line per phase outcome. Schema: `phase` (`fitness` | `stack_apply` | `regression`), `generation`, `parent`, `imp` (with `rank`, `title`, `type`, `description`, `principle_ids`, `files_touched`), `candidate`, `record[]`, `wins_cand`, `wins_parent`, `games`, `outcome` (`fitness-pass` | `fitness-fail` | `stack-apply-pass` | `stack-apply-commit-fail` | `regression-pass` | `regression-rollback` | `crash`), `reason`. Multiple rows per imp roll up to one logical improvement.

**Tab-related test files that exist.**

LadderTab.test.tsx, LoopStatus.test.tsx, RecentImprovements.test.tsx, RewardTrends.test.tsx, TriggerControls.test.tsx. These die with their components.

**Pre-existing test failures.**

Two vitest tests were already failing before the refactor:
- `AdvisedControlPanel.test.tsx::"renders idle state with guidance text"` — `getByText` finds two `/improve-bot-advised` matches in the panel template. Bug in the test, not the component. Fix while we're here.
- `TriggerControls.test.tsx::"calls POST /api/training/evaluate"` — assertion failure, root cause not investigated. Becomes moot when TriggerControls is deleted.

---

## 3. Scope

**In scope:**

- New `GET /api/improvements/unified` endpoint that merges advised + evolve sources into one timeline.
- New `ImprovementsTab.tsx` + test that consumes the unified endpoint with source tagging and a source filter.
- `App.tsx` cut down to 5–6 tabs + new Improvements wiring.
- Deletion of 14 unused frontend components and their 5 test files.
- Audit + deletion of backend endpoints exclusively used by deleted components.
- Backend tests in `tests/test_api*.py` updated to match the surviving endpoint surface.
- Fix to `AdvisedControlPanel.test.tsx` `getByText` issue (use `getAllByText` with length assertion or anchor to a more specific element).
- Verify dashboard works end-to-end (backend + frontend dev server, click every surviving tab).

**Explicitly out of scope:**

- Backend behavioural changes for the surviving tabs. Advisor / Evolution / Processes endpoints stay exactly as-is.
- WebSocket re-architecture. If `/ws/game` and `/ws/decisions` are only consumed by deleted tabs, retire them; otherwise leave alone.
- Re-adding any cut feature back into the surviving tabs (e.g. embedding live game state into the Evolution tab as a section). That's a separate plan if we want it later.
- Dashboard styling overhaul. Keep the look-and-feel of surviving tabs unchanged.
- New backend features / new metrics / new visualisations. The unified Improvements is the only new backend surface; everything else is removal.

---

## 4. Impact analysis

| File / module | Change type | Detail |
|---|---|---|
| `frontend/src/components/Stats.tsx` | DELETE | Plus any imports |
| `frontend/src/components/DecisionQueue.tsx` | DELETE | |
| `frontend/src/components/TrainingDashboard.tsx` | DELETE | |
| `frontend/src/components/ModelComparison.tsx` | DELETE | |
| `frontend/src/components/ImprovementTimeline.tsx` | DELETE | |
| `frontend/src/components/CheckpointList.tsx` | DELETE | |
| `frontend/src/components/RewardRuleEditor.tsx` | DELETE | |
| `frontend/src/components/LoopStatus.tsx` + `.test.tsx` | DELETE | |
| `frontend/src/components/TriggerControls.tsx` + `.test.tsx` | DELETE | Also kills one pre-existing test failure |
| `frontend/src/components/LadderTab.tsx` + `.test.tsx` | DELETE | |
| `frontend/src/components/LiveView.tsx` | DELETE | |
| `frontend/src/components/RewardTrends.tsx` + `.test.tsx` | DELETE | (Subsumed by ImprovementsTab) |
| `frontend/src/components/RecentImprovements.tsx` + `.test.tsx` | DELETE | (Subsumed) |
| `frontend/src/components/AdvisedImprovements.tsx` | DELETE | (Subsumed) |
| `frontend/src/components/AdvisedControlPanel.test.tsx` | Modify | Fix the `getByText` two-match bug |
| `frontend/src/components/ImprovementsTab.tsx` | NEW | Unified timeline with source tag + filter |
| `frontend/src/components/ImprovementsTab.test.tsx` | NEW | Vitest with mocked unified endpoint |
| `frontend/src/App.tsx` | Modify | Remove dropped tabs, add ImprovementsTab, simplify Tab union |
| `frontend/src/App.css` | Modify | Add `.improvements-tab` styles; remove orphaned classes |
| `bots/v0/api.py` | Modify | Add `GET /api/improvements/unified`; delete endpoints exclusively used by deleted components (audit in Step 1) |
| `tests/test_api*.py` | Modify | New test for unified endpoint; delete tests for removed endpoints |
| `frontend/package.json` | No change | No new deps |
| `documentation/wiki/operator-commands.md` | Modify | Update tab references (the Improvements row, remove Live/Ladder mentions) |

**No data migrations.** The data sources stay where they are; the unified endpoint reads from both at request time. No schema changes.

**No backwards-compat shims.** Killed endpoints can stay killed. If something we missed still calls them, it'll 404 and we fix forward.

---

## 5. New components

**`GET /api/improvements/unified`** (in `bots/v0/api.py`)

Merges `data/improvement_log.json` + `data/evolve_results.jsonl` into a list of `UnifiedImprovement` entries, newest first. Query params:

- `source` (optional): `advised` | `evolve` — filter by source. Default: both.
- `limit` (optional): max entries returned. Default: 50.

Response shape:

```json
{
  "improvements": [
    {
      "id": "advised-20260412-2007-iter1",
      "source": "advised",
      "timestamp": "2026-04-12T20:50:00Z",
      "title": "Stronger mineral floating penalties",
      "description": "...",
      "type": "training",
      "outcome": "promoted",
      "metric": "1/10 wins (validation)",
      "principles": ["§1 Core Strategic Objective", "§4.2 Resource Spending"],
      "files_changed": ["data/reward_rules.json"]
    },
    {
      "id": "evolve-gen2-cand_2e57ef46",
      "source": "evolve",
      "timestamp": "2026-04-29T21:34:32Z",
      "title": "Gas-dump warp priority when gas floods",
      "description": "...",
      "type": "dev",
      "outcome": "fitness-pass",
      "metric": "3-2 vs v3",
      "principles": ["4.2", "11.2", "24"],
      "files_changed": ["bots/v3/bot.py"]
    }
  ]
}
```

**Outcome normalisation across sources:**

- Advised `result: "pass"` → `outcome: "promoted"`.
- Advised `result: "stopped" | "fail"` → `outcome: "discarded"`.
- Evolve: take the LAST phase row for the imp (by candidate id). The phase row's `outcome` field passes through as-is so the UI can show `fitness-pass` / `regression-rollback` / `stack-apply-commit-fail` etc.
- "promoted" means: advised win-rate threshold met, OR evolve regression-pass.

**Evolve rollup logic:** group `evolve_results.jsonl` rows by `imp.title` + `generation`, sort each group by ordinal of phases (fitness < stack_apply < regression), take the last one as the canonical outcome. Multiple stack-apply rounds for the same imp during a generation roll up to whichever phase progressed furthest.

**`ImprovementsTab.tsx`** (in `frontend/src/components/`)

Single component, table-style rendering:

- Header: "Improvements" title + source filter (All / Advised / Evolve toggle pills) + entry count.
- Table columns: timestamp (relative + absolute on hover), source badge (colored), title, outcome badge, metric, principles (truncated), files (truncated).
- Click a row to expand: full description, full principle list, full files list, link to the GitHub commit if available.
- Empty state: "No improvements yet — run /improve-bot-advised or /improve-bot-evolve."
- Stale-data banner integration via `useApi` (consistent with LadderTab / EvolutionTab patterns).

No real-time polling — the data is append-only and changes on the order of minutes-to-hours. Refresh button instead.

**`ImprovementsTab.test.tsx`**

Vitest covering:
- Loading state before first fetch
- Renders entries when populated
- Source filter pills change displayed entries
- Empty state when both sources empty
- Stale-data banner appears on fetch error

---

## 6. Design decisions

**Single unified endpoint over a frontend-side merge.** Could ship the merge logic in the React component by hitting `/api/improvements` (advised) and `/api/evolve/state` (evolve summary) separately and joining client-side. Rejected because (a) the schemas are different enough that the projection logic shouldn't live in two places, and (b) evolve_results.jsonl can grow large and we don't want to ship the full file to the browser. Backend endpoint owns the projection; frontend just renders.

**Drop instead of hide.** Could ship Option A (hide nav buttons, keep components) as a stepping stone. Rejected because the user explicitly wants Option C — actual cleanup, not just nav hiding. Hidden components carry maintenance cost (lint, type-check, tests, dep upgrades, mental load) for zero benefit if they're truly unused.

**Keep Alerts as its own tab.** Considered folding into a corner notification or merging with Processes. Rejected because backend-error visibility is a "support" function operators reach for occasionally and it benefits from a dedicated surface. Keeping as a 5th or 6th tab.

**Keep the existing /api/improvements endpoint or retire it.** Decision: retire `/api/improvements` (line 975) and replace with `/api/improvements/unified`. Reason: we control all callers (just the deleted RecentImprovements component). Cleaner to land one new well-named endpoint than to keep two.

**Endpoint audit before deletion, not after.** Step 1 of the build is the endpoint audit — identify which `/api/training/*` and `/api/games` and `/api/decision-log` endpoints have NO surviving caller. Some may turn out to be referenced by Advisor or Evolution tabs (e.g. `/api/training/promotions/*` could be cross-cutting). Audit ensures we don't break a kept tab.

**Don't fold live game state into Advisor/Evolution tabs.** The user's question ("aren't they two ways of doing the same thing") implies they want simpler grouping, not richer per-tab views. Adding a "live game state" section inside Advisor + Evolution would re-add the cognitive load we're cutting. If the absence of Live is missed, we can revisit.

**Source-tag the unified entries, don't separate.** Advised and evolve entries share enough operator concerns (title, outcome, metric) that one sortable timeline is more useful than two adjacent panels. The source badge + filter pills give per-source views when needed.

**No new dependencies.** `react-markdown` was added for HelpTab; no further frontend deps needed for ImprovementsTab. Plain table.

---

## 7. Build steps

### Step 1: Endpoint audit

- **Problem:** Identify which backend endpoints in `bots/v0/api.py` are exclusively called by deleted-tab components vs which are also called by Advisor / Evolution / Processes / Alerts. Greppable from the frontend with `useApi("/api/...")` and `fetch("/api/...")` calls.
- **Produces:** `documentation/plans/dashboard-refactor-endpoint-audit.md` (temporary working doc, deleted after Step 7) listing every `/api/...` endpoint with its caller status (KEPT — used by surviving tab; DROP — only deleted-tab callers).
- **Done when:** every endpoint in `bots/v0/api.py` is classified KEPT or DROP, with grep evidence cited per endpoint.
- **Risk:** miscount a caller; miss a websocket subscription. Mitigation: full grep across `frontend/src/` for each endpoint path; cross-check with WS subscription list.
- **Status:** DONE (2026-04-29)

### Step 2: Build unified endpoint

- **Problem:** Implement `GET /api/improvements/unified` per §5 spec. Read `improvement_log.json` and `evolve_results.jsonl`, normalise into the unified schema, sort by timestamp desc, apply optional source / limit filters.
- **Produces:** Updated `bots/v0/api.py`; new test class in `tests/test_api.py` with cases: (a) only advised, (b) only evolve, (c) both, (d) source filter, (e) limit.
- **Done when:** New tests pass; mypy strict still 0 issues; ruff clean.
- **Status:** DONE (2026-04-29)

### Step 3: Build ImprovementsTab

- **Problem:** Implement `ImprovementsTab.tsx` per §5 spec. Source filter pills, table rendering, expandable rows, stale-data banner.
- **Produces:** `ImprovementsTab.tsx`, `ImprovementsTab.test.tsx`. Update `App.css` with new tab classes.
- **Done when:** Vitest passes for the new tab; manual render against a populated `improvement_log.json` shows correct table; manual filter pill toggles correct entries.
- **Status:** DONE (2026-04-29)

### Step 4: Wire into App.tsx

- **Problem:** Replace the current 12-tab nav with 5–6 tabs (final list in §5 of the conversation log: Advisor, Evolution, Improvements, Processes, Alerts, Help). Update `Tab` type union, button list, render dispatch.
- **Produces:** Updated `App.tsx`.
- **Done when:** App renders with 5–6 tab buttons; clicking each shows the correct component; Tab type union has only the kept names.
- **Status:** DONE (2026-04-29)

### Step 5: Delete frontend components

- **Problem:** Delete the 14 components listed in §4 + their 5 test files. Remove their imports from `App.tsx`. Remove orphaned CSS classes from `App.css`.
- **Produces:** Smaller `frontend/src/components/` tree; smaller `App.css`.
- **Done when:** `frontend/src/components/` no longer contains the deleted files; vitest still passes (only the surviving tabs' tests remain); ESLint reports no unused-import errors.
- **Status:** DONE (2026-04-29) — 19 files removed (14 components + 5 tests). Vitest 111/112 (only the pre-existing AdvisedControlPanel failure remains, slated for Step 7). ESLint surfaces 4 pre-existing problems in untouched files (AlertToast/useAlerts/useApi/useWebSocket); no unused-import errors. App.css orphan-class cleanup deferred — orphan CSS rules don't break anything; can sweep in a follow-up.

- **Problem:** Remove every endpoint marked DROP in Step 1's audit. Update or remove their backend tests in `tests/test_api*.py`.
- **Produces:** Smaller `bots/v0/api.py`; smaller backend test count.
- **Done when:** Pytest passes; mypy strict 0 issues; ruff clean.

### Step 7: Fix the AdvisedControlPanel test + delete audit doc

- **Problem:** Fix the `getByText` two-match bug in `AdvisedControlPanel.test.tsx` (use `getAllByText` with `expect(...).toHaveLength(2)`, OR anchor the text match to a specific role/element). Delete the temporary endpoint-audit doc from Step 1.
- **Produces:** Fixed test; cleaner working tree.
- **Done when:** Vitest is fully green (no pre-existing failures left).

### Step 8: Update operator-commands.md

- **Problem:** The cheat sheet mentions tabs that no longer exist. Update the "Run on Windows" + watching-task sections to reflect the new tab list.
- **Produces:** Updated `documentation/wiki/operator-commands.md`.
- **Done when:** No stale tab references in the doc.

### Step 9: Manual smoke test

- **Problem:** Start backend + frontend dev server. Click every tab. Confirm Advisor / Evolution flows still work end-to-end (start an advised run, check it appears in Improvements; same for an evolve quick run).
- **Type:** operator
- **Produces:** Pass/fail observation in the PR description; a small markdown record at `documentation/soak-test-runs/dashboard-refactor-smoke-<TS>.md`.
- **Done when:** Operator confirms the new dashboard renders, surviving features behave normally, and Improvements timeline shows real data from both sources.

---

## 8. Risks and open questions

| Item | Risk | Mitigation |
|---|---|---|
| Endpoint audit misses a cross-tab caller | Surviving tab breaks at runtime | Step 1 grep is exhaustive (every `/api/...` literal); manual smoke test in Step 9 catches what grep misses |
| `/api/improvements` (existing) callers we don't know about | External tooling may call it | `/api/improvements` is internal-only (no external API contract); fine to retire |
| WS endpoints `/ws/game`, `/ws/decisions` are subscribed by removed components only | Safe to retire; auditable | Step 1 audit includes WS endpoints; remove the subscription handlers in `bots/v0/api.py` if no surviving tab subscribes |
| Pre-existing TriggerControls test failure obscures real regressions during the refactor | Could miss a real test failure | Step 5 deletes TriggerControls + its test; the failure is cleared mid-refactor |
| Evolve rollup logic in unified endpoint becomes complex | More code to maintain | Spec the rollup tightly in §5 — group by (title, generation), take last by phase ordinal. Test cases cover edge cases (multiple stack-apply attempts, regression rollback) |
| `improvement_log.json` schema variations in old entries | Older entries may have missing fields | Unified projection treats missing fields as `null`; empty arrays for `principles` / `files_changed` |
| HelpTab references stale tab names after refactor | Operator-commands.md lists tabs that don't exist | Step 8 updates the doc explicitly |
| Frontend bundle size after deleting 14 components | None — bundle gets smaller | No mitigation needed; this is a benefit |

**Open questions (decide during execution, not now):**

1. Does `/api/training/promotions/*` get called by EvolutionTab or AdvisedControlPanel? If yes, KEEP. If no, DROP. (Answer in Step 1 audit.)
2. Does the alerts pipeline use any `/api/training/*` endpoint to surface training-phase errors? If yes, those endpoints stay even though Training tab is gone. (Answer in Step 1.)
3. Should the unified Improvements endpoint expose pagination (offset/limit) or just a `?limit=N` cap? Decision: just `?limit` for v1; revisit if the timeline grows past ~500 entries.

---

## 9. Testing strategy

**Unit tests:**

- `tests/test_api.py::TestImprovementsUnifiedEndpoint` — new class covering the cases enumerated in Step 2.
- `frontend/src/components/ImprovementsTab.test.tsx` — vitest covering filter pills, source badges, expand/collapse rows, empty state, stale-data banner.
- `frontend/src/components/AdvisedControlPanel.test.tsx` — fix the `getByText` two-match bug.

**Removed tests:**

- `LadderTab.test.tsx`, `LoopStatus.test.tsx`, `RecentImprovements.test.tsx`, `RewardTrends.test.tsx`, `TriggerControls.test.tsx` — gone with their components.
- `tests/test_api.py` test classes for each retired endpoint — gone with their endpoints.

**Integration / smoke:**

- Step 9 smoke test: start backend + frontend, click every tab, confirm surviving features behave. Record observations in a soak-test-runs markdown.

**No autonomous-behaviour observation** required — this is a UX refactor, not a behavioural change. The plan-feature skill rule for autonomous-behaviour observation phases doesn't apply here.

---

## 10. References

- `documentation/plans/alpha4gate-master-plan.md` — phase context.
- `documentation/wiki/operator-commands.md` — operator cheat sheet (will be updated in Step 8).
- `bots/v0/api.py` — backend endpoint surface.
- `frontend/src/App.tsx` — tab nav structure.
- Memory `feedback_useapi_cache_schema_break.md` — change cacheKey when response shape changes (relevant for the new unified endpoint replacing `/api/improvements`).
