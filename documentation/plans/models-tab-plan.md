# Models insight tab — Utility-stack introspection + Observable shell

**Date drafted:** 2026-05-01.
**Track:** Dashboard / operator UX. Cross-cuts Utility (Phases 6/7/8/9/N/O) and Observable (Phase L) stacks.
**Prerequisites:** None hard. Builds on the 2026-04-29 dashboard refactor (`dashboard-refactor-plan.md`) and the existing `/api/improvements/unified` endpoint. Compatible with evolve-parallelization (which adds `/api/evolve/running-rounds`).
**Slot:** Not a numbered phase. Operator UX work that prepares the dashboard for upcoming phases without committing to any one of them.
**Review status:** v3 — incorporates plan-review fixes from 2026-05-01 round 1 (5 blockers + 7 significant + 6 missing) and round 2 (2 blockers + 7 significant + 7 missing + 3 nice-to-haves).

---

## 1. What this feature does

Replaces the current Improvements tab with a **Models** tab that answers five operator questions through five drill-down sub-views, and adds a sibling **Observable** tab as a wired-up shell for Phase L's exhibition mode.

Today the dashboard shows model evolution as a flat append-only timeline (Improvements tab) and a single-version Evolution status panel. There is no way to ask:

- *What is running right now across all harnesses?* (advised + evolve + training daemon + Linux soak can all run concurrently)
- *How did this lineage evolve?* (ancestry tree, branches, regressions)
- *What does v_N look like internally?* (config, training trajectory, action distribution, applied improvements)
- *How does v_X compare to v_Y?* (Elo delta, hyperparams diff, weight-derived KL divergence)
- *What is the bot thinking right now or in this past game?* (winprob trajectory, give-up firings, expert dispatch)

The 2026-04-29 refactor deleted 14 components, including a per-rule reward-contribution time-series the operator remembers fondly. The Improvements tab that replaced it shows changes-in-time, not model-dynamics-in-time. This plan ships the model-dynamics view the operator was actually asking for, plus a "Weight Dynamics" capability that did not previously exist anywhere — offline-computed L2 layer norms and KL divergence over a fixed canary state set.

The plan also accommodates the master plan's two-stack split (lines 56-86): Utility stack (rated training, evolution, headless) gets the Models tab; Observable stack (exhibition matches, Phase L replay-stream-as-live) gets its own tab as a shell. Both are designed for multi-track, multi-harness, multi-goal, and (eventual) multi-race operation from day one.

**Why this matters now:** the master plan has Phases 6/7/8/N already producing data the dashboard cannot surface, and Phases L/O/G coming with new data axes that the current 6-tab structure cannot accommodate without another disruptive refactor. Building this once, broadly, is cheaper than three more incremental tab additions.

---

## 2. Existing context

A fresh-context model needs to know:

**Current 6 tabs.** `App.tsx` defines `Advisor | Evolution | Improvements | Processes | Alerts | Help`. This plan removes `Improvements` (function preserved in Lineage timeline mode), adds `Models` and `Observable`. Net: 7 tabs.

**Two-stack split.** Master plan §"Two stacks, one platform" (lines 56-86): Utility stack (training, evolution, intra/cross-version promotion, ladder, headless Linux/Docker) and Observable stack (exhibition viewer; Phase L = replay-stream-as-live). Disjoint code paths, shared dashboard.

**Per-version state.** Each version under `bots/v0/` … `bots/v10/` has its own `data/` dir with `training.db` (40+ columns of state-action-reward + `win_prob` from Phase N), `checkpoints/manifest.json` (SB3 .zip blobs + minimal metadata), `hyperparams.json`, `reward_rules.json`, `daemon_config.json`, plus version-root `manifest.json` (registry data — see schema below). `bots/current/current.txt` holds the active version string (e.g. `v10`).

**Manifest schema (verified on disk 2026-05-01).** Each `bots/vN/manifest.json` contains: `version, parent, git_sha, timestamp, best, previous_best, elo, fingerprint, extra`. Notably absent: `race`, `harness_origin`, `promoted_at`. This plan **derives** race + harness_origin at query time from cross-version logs; treats existing `timestamp` as the promoted_at signal.

**Cross-version state.** Repo-root `data/` has `improvement_log.json` (advised), `evolve_results.jsonl` (evolve phases), `evolve_run_state.json` (live evolve metadata), `evolve_round_<worker_id>.json` (per-worker progress, evolve-parallelization), `evolve_pool.json`, `selfplay_results.jsonl`, ladder data, `decision_audit.json`.

**`improvement_log.json` schema.** Per `dashboard-refactor-plan.md` §2: `id, timestamp, run_id, iteration, title, type, description, principles[], result, metrics, files_changed[]`. **No `target_version` field** — this plan derives target version by parsing `files_changed[]` for `bots/vN/` path prefixes (entries that touch `bots/current/...` resolve via `current.txt` value at the iteration's commit time, looked up via `git show <sha>:bots/current/current.txt`).

**Existing endpoints we extend or aggregate.** `/api/improvements/unified` (advised + evolve timeline, source-tagged, working), `/api/evolve/state`, `/api/evolve/running-rounds` (per-worker, evolve-parallelization), `/api/evolve/current-round`, `/api/advised/state`, `/api/training/daemon` (in-memory daemon state — there is no on-disk `daemon_state.json`), `/api/training/triggers`, `/api/training/history` (rolling WR, wired but no consumer), `/api/training/promotions/history`, `/api/processes`, `/api/ladder`.

**Endpoint code-shipping target.** Production runtime imports from `bots.current` (currently aliases to `bots.v10` per `current.txt`). New endpoints land in **`bots/v10/api.py`** (the current-version snapshot). Historical versions (`v0`…`v9`) are dormant in production; intra-version edits to their `api.py` snapshots do not propagate. See memory: "Feature work in non-current vN is dormant in production." Future evolve generations re-snapshot from current and inherit these endpoints automatically.

**Data-dir resolver gotcha (recorded in memory).** Backend has a per-version vs cross-version data_dir issue — the same env var was being used for both, which silently broke either side. New endpoints in this plan must use **separate resolvers**: `_per_version_data_dir(version)` and `_cross_version_data_dir()`. Smoke gate (Step 11) verifies both.

**Diagnostic states canary set.** Master-plan glossary defines `diagnostic_states.json` as a fixed obs-vector set logged each training cycle for within-version regression detection. **Verified absent on disk for all 11 versions on 2026-05-01.** `trainer.py` references the name. Either (a) trainer writes them but they're gitignored, or (b) production hasn't started writing them yet. This plan provides a fallback (see §6.7) so Step 9 (Weight Dynamics) doesn't block on a separate instrumentation phase. A small follow-up issue should investigate whether trainer.py *should* be writing this file.

**Orphaned components on disk.** `frontend/src/components/` still contains `TrainingDashboard.tsx`, `RewardTrends.tsx`, `ModelComparison.tsx`, `CheckpointList.tsx`, `CommandPanel.tsx`, `BuildOrderEditor.tsx` and others — not imported by `App.tsx`, but their patterns are fair game for cannibalisation. **`recharts ^3.8.1` is still listed in `frontend/package.json`** (verified 2026-05-01 — the "removed in refactor" claim in some memory entries is stale). No re-add needed.

**Test layout.** Flat under `tests/` — `test_api.py`, `test_*.py`, no nested `tests/api/` directory. New test files follow the same flat pattern: `test_api_versions.py`, `test_api_runs_active.py`, `test_api_lineage.py`, etc.

**Phase status.** Phase N (winprob heuristic + give-up trigger) live and writes `transitions.win_prob`. Phase O (Hydra meta-controller) and Phase G (multi-race) and Phase L (replay-stream viewer) planned but not built — this plan stubs the surfaces they will eventually fill.

**Promotion-creating call sites (verified on disk 2026-05-01).** Versions are created from three entry points: `scripts/evolve.py` (autonomous evolve), `scripts/snapshot_bot.py` (manual fold of non-current vN work — added 2026-04-27 per memory), and improve-bot-advised iteration commits (advised loop). **`scripts/bootstrap_promotion.py` does not exist** despite a test file referencing it (likely orphan from earlier refactor). Post-promotion hooks must fire from each — this plan centralises the hook in a helper invoked from all three sites (§5). Note: improve-bot-advised is a Claude **skill** (Markdown at `.claude/skills/improve-bot-advised/SKILL.md`), not Python code — wiring is an *added instruction* in `SKILL.md` telling Claude to invoke the helper, not a code edit. The other two sites are Python.

---

## 3. Scope

**In scope:**
- Models tab with 5 sub-views: Lineage, Live Runs, Version Inspector, Compare, Forensics
- Lineage view subsumes the current Improvements tab (timeline mode) + adds tree mode
- Observable tab as a wired-up shell (real version-pool selector, Phase L stub)
- 9 new backend endpoints split across 3 build steps for reviewability (see §7)
- Offline `compute_weight_dynamics.py` script + persisted JSONL + post-promotion hook
- Offline `build_lineage.py` script + persisted DAG + post-promotion hook + lazy-init fallback (wired in Step 2, after script exists)
- Centralised post-promotion hook helper (`bots/v0/learning/post_promotion_hooks.py`) called from evolve.py, snapshot_bot.py, and (via SKILL.md instruction) improve-bot-advised
- Multi-race readiness: `race` field derived (default "protoss") in version metadata, hidden race-filter when ≤1 race
- Operator-facing wiki page (`documentation/wiki/models-tab.md`)
- Smoke gate (60s end-to-end with real data) + observation soak (overnight evolve, dashboard watched)
- Input-validation hardening on all path params and subprocess invocations (see §6.11)

**Out of scope:**
- Phase L replay-stream-as-live viewer (Observable tab is shell only — pool selector + placeholder card)
- Phase O Hydra expert-dispatch viz (Forensics has stub panel that auto-fills when Phase O writes `expert_id` to `transitions`)
- Phase G multi-race actually populated (only the readiness scaffolding ships now)
- PFSP-lineage regression visualization (defer until that mechanism ships)
- Real-time WebSocket pushes (all new surfaces use HTTP polling like the rest of the dashboard)
- Resurrecting per-rule reward-contribution time-series (RewardTrends original) — its data pipeline (`reward_aggregator.py`) is orphaned; flagged as a follow-up in §8
- Modifying `manifest.json` schema to add `race` or `harness_origin` fields (Phase G can introduce that later if useful — derivation strategy works fine for v0..v10)
- Optimization-goal filter (e.g., "WR @ diff N" vs. "Elo" vs. "winprob calibration") — only one goal is operationally meaningful per version today; revisit when there's a need to discriminate

**Bundle-size constraint.** Current `frontend/dist/assets/index-*.js` baseline measured at Step 1a (record gz size). Hard budget: total gz growth ≤120KB across all steps. Recharts already present, so chart additions are free; new tree library (d3-hierarchy, ~20KB gz) competes against this budget.

---

## 4. Impact analysis

| Path | Change | Notes |
|---|---|---|
| `frontend/src/App.tsx` | Modify | Remove `improvements` tab; add `models` + `observable` tabs. Default tab stays `advisor`. |
| `frontend/src/components/ImprovementsTab.tsx` | Delete | Behavior preserved in `LineageView` timeline mode (Step 4). |
| `frontend/src/components/ImprovementsTab.test.tsx` | Delete | Replaced by `LineageView.test.tsx` timeline-mode coverage. |
| `frontend/src/components/EvolutionTab.tsx` | Keep | Untouched. Live Runs grid reuses its hooks but does not replace the tab. |
| `frontend/src/hooks/useEvolveRun.ts` | Reuse | Backend source for `useRunsActive` aggregator. |
| `bots/v10/api.py` (current snapshot) | Extend | 9 new endpoints (§5). Dual data-dir resolvers. **Production target.** |
| `bots/v10/runner.py` | Possibly modify | If runner owns endpoint registration. |
| `bots/v10/learning/database.py` | Read-only | Action histogram + winprob trajectory queries. No schema change. |
| `bots/v10/learning/checkpoints.py` | Read-only | Manifest reading for version registry. |
| `bots/v0/learning/post_promotion_hooks.py` | New | Centralised helper invoked from all promotion sites. v0 is the canonical source; `snapshot_current` will copy to future versions. |
| `bots/v10/learning/post_promotion_hooks.py` | New | Same file landed in current snapshot via Step 2's edit pattern. |
| `scripts/evolve.py` | Modify | Post-promotion hook call site (after `git_commit_evo_auto`). |
| `scripts/snapshot_bot.py` | Modify | Post-promotion hook call site (after manual snapshot). |
| `.claude/skills/improve-bot-advised/SKILL.md` | Modify | **Instruction added** telling Claude to invoke `post_promotion_hooks.run_post_promotion_hooks(<version>)` after each iteration commit. Not a code edit — a markdown change. |
| `scripts/build_lineage.py` | New | Builds `data/lineage.json`. Atomic-replace pattern. Idempotent. |
| `scripts/compute_weight_dynamics.py` | New | Loads SB3 .zip checkpoints, emits `data/weight_dynamics.jsonl`. Idempotent (hash-keyed). Diagnostic-states fallback. Per-checkpoint failure → error row. |
| `data/lineage.json` | New (gitignored) | Persisted DAG. Atomic-replace writes. |
| `data/weight_dynamics.jsonl` | New (gitignored) | Append-only. Single-writer-at-a-time via advisory lockfile. |
| `frontend/src/components/ModelsTab.tsx` | New | Top-level shell with sub-view router. |
| `frontend/src/components/LineageView.tsx` | New | Family tree + timeline modes. |
| `frontend/src/components/LiveRunsGrid.tsx` | New | Multi-worker card grid. |
| `frontend/src/components/VersionInspector.tsx` | New | Per-version drill-down. |
| `frontend/src/components/CompareView.tsx` | New | A vs B side-by-side. |
| `frontend/src/components/ForensicsView.tsx` | New | Per-game replay-style insight. |
| `frontend/src/components/ObservableTab.tsx` | New | Top-level shell, Phase L placeholder. |
| `frontend/src/hooks/useVersions.ts` | New | Version registry fetch. |
| `frontend/src/hooks/useRunsActive.ts` | New | Unified live-runs aggregator client. |
| `frontend/src/hooks/useLineage.ts` | New | Family-tree DAG fetch. |
| `frontend/src/hooks/useVersionDetail.ts` | New | Per-version inspector data. |
| `documentation/wiki/index.md` | Modify | Update tab inventory + add Models tab page link. |
| `documentation/wiki/models-tab.md` | New | Operator-facing reference (content outline in §5). |
| `tests/test_api_versions.py` | New | Contract tests for /api/versions, /api/versions/{v}/config + input-validation rejects. |
| `tests/test_api_versions_data.py` | New | /api/versions/{v}/training-history, /actions, /improvements. |
| `tests/test_api_runs_active.py` | New | /api/runs/active aggregator. |
| `tests/test_api_lineage.py` | New | /api/lineage + lazy-init behaviour. |
| `tests/test_api_forensics.py` | New | /api/versions/{v}/forensics/{game_id} + input-validation rejects. |
| `tests/test_build_lineage.py` | New | Script unit tests. |
| `tests/test_compute_weight_dynamics.py` | New | Script unit tests including diagnostic-states fallback + per-checkpoint failure error-row behaviour. |
| `tests/test_post_promotion_hooks.py` | New | Hook helper, including failure-doesn't-block-promotion behaviour. |

---

## 5. New components

### Backend endpoints (in `bots/v10/api.py` — current snapshot target)

All endpoints declared `async def` and use `await asyncio.to_thread(...)` for any blocking subprocess or filesystem-heavy operation (so the event loop is never starved). All path params are validated against strict regex before use (see §6.11) — invalid input → 400 with brief reason.

- `GET /api/versions` → `[{name, race, parent, harness_origin, timestamp, sha, fingerprint, current: bool}, ...]`
  Reads each `bots/v*/manifest.json`. **Derives `race`** (currently always `"protoss"` — single-race project). **Derives `harness_origin`** ∈ `"advised" | "evolve" | "manual" | "self-play"` by cross-referencing the version's `git_sha` with `improvement_log.json` (advised commits) and `evolve_results.jsonl` (evolve `[evo-auto]` promotions); falls back to `"manual"` when no match found.

- `GET /api/versions/{v}/config` → `{hyperparams, reward_rules, daemon_config}`. Reads three per-version JSON files via `_per_version_data_dir(v)`. Returns `{}` for any missing file (does not 500).

- `GET /api/versions/{v}/training-history` → `{rolling_10: [{game_id, ts, wr}], rolling_50: [...], rolling_overall: [...]}`. Aggregates `bots/v{v}/data/training.db` `games` table filtered by `model_version == v`. Uses existing `idx_games_model` index.

- `GET /api/versions/{v}/actions` → `[{action_id, name, count, pct}, ...]`. Aggregates `transitions` table joined with `games.model_version == v`. Bot's `learning/features.py` provides action-id-to-name map.

- `GET /api/versions/{v}/improvements` → filtered improvement timeline. Reuses `/api/improvements/unified` shape, **filtered by deriving target version from each entry's `files_changed[]`**: any path matching `bots/vN/` → `vN`; paths matching `bots/current/...` resolved via `git show <commit_sha>:bots/current/current.txt` (cached per commit; SHA is validated against `^[0-9a-f]{7,40}$` before subprocess invocation — see §6.11). Returns `[]` for versions with no matched entries.

- `GET /api/versions/{v}/weight-dynamics` → `[{checkpoint, ts, l2_per_layer: {...} | null, kl_from_parent: float | null, canary_source: "diagnostic_states" | "transitions_sample" | null, error: string | null}, ...]`. Reads `data/weight_dynamics.jsonl` filtered by `version == v`. **Per-row error indicator:** when `error` is non-null, the row represents a failed compute attempt (l2 + KL + canary fields are null). Inspector renders such rows as "Compute failed — `<error>` — re-run `python scripts/compute_weight_dynamics.py --version vN`."

- `GET /api/versions/{v}/forensics/{game_id}` → `{trajectory: [{step, win_prob, ...}], give_up_fired: bool, give_up_step: int | null, expert_dispatch: null}`. Reads `transitions` filtered by `(model_version == v, game_id == game_id)`. Both path params validated against strict regex (see §6.11). **`expert_dispatch` is always `null` until Phase O writes `expert_id` column to `transitions`** — single shape for TypeScript clients (see §6.8).

- `GET /api/runs/active` → `[{harness, version, phase, current_imp, games_played, games_total, score_cand, score_parent, started_at, updated_at}, ...]`. Aggregates **the existing `/api/training/daemon` endpoint** (in-memory daemon state — there is no on-disk per-version daemon_state file; daemon is per-version-current and at most one is active), `/api/advised/state`, `/api/evolve/running-rounds`, plus a scan of `data/evolve_round_<worker_id>.json` files for per-worker rows. Returns `[]` if nothing active.

- `GET /api/lineage` → `{nodes: [{id, version, race, harness_origin, parent}], edges: [{from, to, harness, improvement_title, ts, outcome}]}`. **Lazy-init wired in Step 2** (after `scripts/build_lineage.py` exists): if `data/lineage.json` is missing, the endpoint acquires an `asyncio.Lock` (process-wide, so only one rebuild runs at a time across concurrent requests), invokes `build_lineage.py` via `asyncio.to_thread(subprocess.run, [...], shell=False)`, then persists + returns. Subsequent requests read the cached file directly (~10ms). **Until Step 2 lands, Step 1a's endpoint returns `{nodes: [], edges: []}` for missing file.** Node `id` equals `version` (e.g. `"v3"`) — no separate surrogate. Edge `improvement_title` is `"manual"` for `harness_origin == "manual"` promotions (no improvement record exists) and `"—"` for any other unresolvable case.

### Frontend components

**Refresh cadence (general rule):** live surfaces poll at 2s; everything else loads-once on mount with a manual refresh button. Per-component below.

- `ModelsTab.tsx` — top-level shell. Holds the 5 sub-view router (`lineage` | `live` | `inspector` | `compare` | `forensics`), the version selector strip (drop-down + race filter that hides when `len({v.race or "protoss" for v in versions}) <= 1` — see §6.6 for the coercion rule), and a **harness filter** (chips: `advised | evolve | manual | self-play`, all-on by default). Default sub-view is `lineage`. Refresh: load-once for version registry; manual refresh button in header re-fetches everything.

- `LineageView.tsx` — two modes:
  - **Tree mode**: family-tree visualization using **`d3-hierarchy` cluster layout** rendered to native SVG (decision §6.5 — recharts is already present but its tree primitives are limited; d3-hierarchy adds ~20KB gz and gives full control). Nodes coloured by `harness_origin`; edges labelled with `improvement_title` (or `"manual"` / `"—"` per the rules above). Accepts `onNodeSelect: (version: string) => void` callback prop — invoked when user clicks a node. Parent (`ModelsTab`) handles the selection by setting selected-version state and switching sub-view to `inspector`.
  - **Timeline mode**: same content as today's `ImprovementsTab` (table of unified improvements, source badges, expandable rows). This is the subsumption surface.
  - Mode toggle is a single button; default is Tree mode.
  - Refresh: load-once on mount; manual refresh button.

- `LiveRunsGrid.tsx` — N-card grid, one card per active harness. **Card layout** (1-line-per-row dense view; expandable):
  ```
  [harness icon] [HARNESS NAME @ version]              [updated 3s ago]
  Phase: regression  ·  Imp: "Splash readiness"        [▶ expand]
  ████████░░░░░░  6/10 games  ·  cand 4 vs parent 2
  ```
  Each card has header (icon + harness + version), one-line metric strip (phase, current imp), progress bar (games_played/games_total), score line (cand vs parent), and an "expand" affordance for full state JSON. Empty state: single muted card "No active runs."
  Refresh: poll at 2s (matches existing live surfaces).

- `VersionInspector.tsx` — drill-down panel with five sub-panels (collapsible accordion):
  - **Config**: hyperparams + reward rules + daemon config (collapsible JSON).
  - **Training curve**: line chart of rolling WR (recharts).
  - **Actions**: bar chart of action frequencies (recharts).
  - **Improvements applied**: filtered timeline (reuse Lineage timeline-mode component).
  - **Weight Dynamics**: line chart of L2 layer norms over checkpoints (added in Step 9; before Step 9 ships, this panel shows "Pending — run `scripts/compute_weight_dynamics.py`"). When a row has `error` set, that checkpoint renders as a red dot with the error string on hover.
  - "Compare with parent" quick-link button at top → switches to Compare sub-view with A=current, B=parent prefilled.
  - Refresh: load-once on selected-version change; manual refresh button.

- `CompareView.tsx` — A vs B selector at top. Diff panels:
  - **Elo delta** (from ladder).
  - **Hyperparams diff** (deep-diff renderer with green/red highlighting).
  - **Reward rules diff** (rule-by-rule add/modify/remove).
  - **Weight KL divergence** (single number from `weight_dynamics.jsonl`, added in Step 9; semantics: parent → child only — sibling comparison shows "no direct lineage" placeholder).
  - Refresh: load-once on A/B selection change.

- `ForensicsView.tsx` — per-game replay-style. Game-id selector (default: most recent in current version). Renders winprob trajectory line chart (recharts) with give-up trigger marked as a vertical reference line, plus expert-dispatch panel (always shows "Expert dispatch — Phase O pending" until Phase O writes the column). Refresh: load-once on game-id change.

- `ObservableTab.tsx` — top-level. Pool selector (queries real `/api/versions`, lets operator pick two seeds for a hypothetical exhibition), placeholder card "Exhibition mode awaits Phase L (replay-stream-as-live)" with a wiki link. Refresh: load-once on mount.

**`useApi` cache-key bump.** Per memory (`feedback_useapi_cache_schema_break.md`), when an API response shape changes, the IDB cache key must change to avoid old cached data crashing React. `/api/improvements/unified` is consumed differently in Lineage timeline mode (still same shape, so likely fine — but verify in Step 4 and bump the cache key if any field shape shifts). New endpoints get fresh cache keys; no migration concern.

### Data files

- `data/lineage.json` — persisted DAG. Schema in `/api/lineage` description above. Rebuilt by `scripts/build_lineage.py` on every promotion via the centralised hook helper. Atomic-replace writes (write to `.tmp`, fsync, `os.replace` with retry-with-backoff helper). Lazy-init on first request if missing (Step 2 onward).

- `data/weight_dynamics.jsonl` — append-only, one row per `(version, checkpoint)` tuple, including failure rows. **Success-row schema:** `{version, checkpoint, ts, l2_per_layer: {policy_net.0.weight: 12.3, ...}, kl_from_parent: 0.087 | null, hash, canary_source: "diagnostic_states" | "transitions_sample", error: null}`. **Failure-row schema:** `{version, checkpoint, ts, l2_per_layer: null, kl_from_parent: null, hash, canary_source: null, error: "<exception class>: <message>"}`. Idempotent — `compute_weight_dynamics.py` skips already-computed `(version, checkpoint, hash)` tuples, but **retries failure rows** (a failure row with the same `hash` is treated as "needs retry" and re-attempted; succeeds → success row appended; new failure → updated failure row appended). Single-writer-at-a-time via `data/.weight_dynamics.lock` advisory lockfile (see §6.4). **Recovery from corruption:** `mv data/weight_dynamics.jsonl data/weight_dynamics.jsonl.bak` then `python scripts/compute_weight_dynamics.py --all`.

### Offline scripts

- `scripts/build_lineage.py [--out path]` — rebuilds `data/lineage.json` from `bots/v*/manifest.json` + `improvement_log.json` + `evolve_results.jsonl`. Pure-Python, ~1s for 11 versions. Atomic-replace. Always invoked with list-form args (no shell).

- `scripts/compute_weight_dynamics.py [--version vN] [--all] [--canary-source {diagnostic_states,transitions_sample,auto}]` — loads each new SB3 `.zip` checkpoint with `stable_baselines3.PPO.load`, computes `torch.linalg.norm(p)` per parameter, plus action-distribution KL divergence from parent. Per-checkpoint failure → emit failure-row to JSONL with exception details, log warning, continue to next checkpoint. **Canary source resolution (auto mode):** if `bots/v{v}/data/diagnostic_states.json` exists, use it; else fall back to a deterministic hashed sample of 100 random transitions from that version's `training.db` (seeded with `version`-string for reproducibility). Records which source was used in `canary_source` field. Appends to JSONL under advisory lock. **Backfill timing target: ~30s for 11 existing checkpoints (hard fail at >60s).**

### Centralised hook helper

- `bots/v0/learning/post_promotion_hooks.py` — exposes `run_post_promotion_hooks(version: str) -> None`. Validates `version` against `^v\d+$` before any subprocess call. Internally invokes `scripts/build_lineage.py` and `scripts/compute_weight_dynamics.py --version <v>` as subprocesses with timeout (60s each), list-form args, `shell=False`. Failures log a warning but never raise — promotion path is never blocked.
- **Three call sites:**
  - `scripts/evolve.py` (after `git_commit_evo_auto`) — Python import + call
  - `scripts/snapshot_bot.py` (after snapshot completes) — Python import + call
  - improve-bot-advised skill (`.claude/skills/improve-bot-advised/SKILL.md`) — instruction added to the skill body telling Claude to run `python -c "from bots.v0.learning.post_promotion_hooks import run_post_promotion_hooks; run_post_promotion_hooks('<version>')"` after each iteration commit. **This is a markdown edit, not a code change.** Its "test" is manual verification during Step 11 smoke gate (run an advised iteration, confirm lineage updates).
- v0 is the canonical source; `snapshot_current` (regex-rewriting copier) propagates the file to future versions on next promotion.

### Wiki page outline (`documentation/wiki/models-tab.md`)

- **What this tab is for.** One paragraph framing of "five questions answered."
- **Sub-view guide.** One paragraph each on Lineage, Live Runs, Version Inspector, Compare, Forensics — what each shows and when to use it.
- **How lineage is computed.** Brief description of the DAG sources (manifest + improvement_log + evolve_results) so operators understand what edges mean.
- **What "Weight Dynamics" measures.** Layer L2 norms = "is the model's parameter magnitude growing/shrinking?"; KL divergence = "do the policy outputs diverge over a fixed canary?". Includes note about diagnostic-states fallback.
- **First-run / refresh.** "If charts say 'Pending', run `python scripts/compute_weight_dynamics.py --all`." "If lineage tree is empty, refresh — it self-builds on first request."
- **Recovery procedures.** How to rebuild `data/weight_dynamics.jsonl` if corrupted; how to force-rebuild lineage.json.
- **Phase L / Phase O / Phase G placeholders.** What each waiting placeholder means, with links to relevant master-plan sections.
- **Operator commands cheatsheet.** Quick `/improve-bot-advised`, `/improve-bot-evolve`, `scripts/snapshot_bot.py` invocations that produce data the tab visualizes.

---

## 6. Design decisions

**6.1. Hard-cut Improvements → Lineage (timeline mode).** One tab less. Function preserved bit-for-bit in Lineage's timeline mode (`ImprovementsTab.tsx` content moves to `LineageView.tsx` under a mode toggle). Operator muscle-memory for `/improvements`-style work lands on Models tab default sub-view, which is Lineage with timeline-mode toggleable.
*Alternatives considered:* feature-flag co-existence (rejected — churn for no gain); rename Improvements to Lineage (rejected — Lineage is a sub-view of a broader tab).

**6.2. Persisted lineage on disk + lazy-init + post-promotion hook.** `data/lineage.json` rebuilt on every promotion. **Lazy-init wired in Step 2 (not Step 1a)**: if file missing on first request, endpoint acquires a process-wide `asyncio.Lock`, invokes `build_lineage.py` via `await asyncio.to_thread(subprocess.run, [...], shell=False)`, persists, returns. The lock prevents two concurrent first-time requests from double-building. First-paint cost ~1s; subsequent reads ~10ms. **Step 1a returns `{nodes:[], edges:[]}` on missing file** as a graceful fallback until Step 2 ships `build_lineage.py`.
*Rationale:* user-experience-first — sub-100ms steady-state matters more than cold-start; lazy-init resolves the fresh-checkout / data-wipe failure mode without a separate "did you run the script?" UX. Lock prevents the double-build race.

**6.3. Live Runs grid covers all four harnesses.** Training daemon, advised iteration, evolve workers (1..N), Linux soak instances. Single chrome with per-source state shape.
*Source resolution:* the aggregator endpoint calls existing `/api/training/daemon` (in-memory daemon state — there is no on-disk per-version state file), `/api/advised/state`, `/api/evolve/running-rounds`, plus scans `data/evolve_round_<worker_id>.json` files. **Resolved bug from v1 of this plan:** v1 mistakenly proposed scanning a fictional `bots/v*/data/daemon_state.json` — that file doesn't exist; daemon state lives in memory and is exposed only via the existing endpoint.

**6.4. Concurrent-write safety on `data/lineage.json` and `data/weight_dynamics.jsonl`.** Hooks fire from three call sites (evolve.py, snapshot_bot.py, improve-bot-advised); concurrent invocation is rare but possible.
- `lineage.json`: full-file rewrite via atomic-replace — write to `data/lineage.json.tmp`, fsync, `os.replace`. The Windows `os.replace` race already documented in memory (memory: "Evolve Windows atomic-replace race FIXED 08fa85c") applies here — reuse the same retry-with-backoff helper (5x, 50ms→800ms).
- `weight_dynamics.jsonl`: append-only, single-writer-at-a-time enforced via `data/.weight_dynamics.lock` advisory lockfile (`fcntl.flock` on Linux/WSL2 ext4, `msvcrt.locking` on Windows). **WSL/DrvFS note:** if `data/` ever lives on `/mnt/c/...` (DrvFS), fcntl semantics are subtly different — repo convention per memory is to keep `data/` and venvs on ext4 (`~/...`) when running from WSL. This plan inherits that constraint without changes.

**6.5. Tree library: `d3-hierarchy` + native SVG (not new recharts components).** Recharts is already present (`^3.8.1`) and is used for line/bar charts in Inspector + Forensics — no re-add required. But recharts' tree primitives are limited; we add `d3-hierarchy` (~20KB gz, no deps) for cluster layout and render to native SVG. Total bundle delta from this plan: ~30KB gz (well under the +120KB budget).
*Alternatives considered:* recharts treemap (rejected — different visual model than family tree); `react-d3-tree` (rejected — heavier, opinionated); `visx` (rejected — much larger surface area than needed).

**6.6. Multi-race readiness from day 1.** Backend hardcodes `race: "protoss"` in `/api/versions` responses (no manifest schema change). UI race-filter dropdown is hidden when `len({v.race or "protoss" for v in versions}) <= 1` — coerces missing-race to default before counting, so the filter stays hidden until a non-protoss race actually appears.
*Rationale:* zero migration cost now; Phase G can either continue deriving in backend (preferred) or add `race` to manifest and migrate. UI doesn't change.

**6.7. Offline weight-dynamics + persisted JSONL + diagnostic-states fallback + per-checkpoint failure rows.** `compute_weight_dynamics.py` runs as post-promotion hook. Dashboard reads JSONL only — never opens .zip files synchronously. Manual backfill flag for first-time run. **If `bots/v{v}/data/diagnostic_states.json` is missing** (currently true for all 11 versions), fall back to a deterministic hashed 100-row sample from `training.db` transitions (seed = version string). `canary_source` field on each row records which path was taken. **Per-checkpoint failures** (torch crash, OOM, missing .zip, etc.) emit a failure row instead of silently skipping — `error` field on the row carries the exception message; the next backfill run treats failure rows as "retry candidates." Inspector renders failure rows as red dots with hover-tooltip + retry hint.
*Follow-up:* small issue to investigate whether trainer.py was supposed to emit `diagnostic_states.json` and quietly stopped, or never started — out of scope for this plan.

**6.8. Stub for expert-dispatch (always null).** `ForensicsView` shows "Expert dispatch — Phase O pending" placard. Schema returns **`expert_dispatch: null`** (single shape, never `[]`). When Phase O writes `expert_id` column to `transitions`, the API populates the array and the UI auto-renders (the placard becomes a real panel).
*Rationale:* TypeScript clients handle a single shape, not a union. Decouples Phase O delivery from Models tab delivery.

**6.9. Endpoint-shipping target is `bots/v10/api.py` (current snapshot).** Production runtime imports from `bots.current` which aliases to `bots.v10`. Edits to v0…v9 historical snapshots do not propagate. Hook helpers (e.g. `post_promotion_hooks.py`) live in `bots/v0/` as canonical source plus copied to `bots/v10/` for current-runtime — `snapshot_current` propagates v10 → next on the next evolve generation.
*Rationale:* per memory ("Feature work in non-current vN is dormant in production"), shipping into the current snapshot is the only way to reach production without a manual `snapshot_bot.py --from v0` fold.

**6.10. Centralised post-promotion hook helper across three call sites.** All three promotion paths invoke the same `bots/v0/learning/post_promotion_hooks.run_post_promotion_hooks(version)`:
- `scripts/evolve.py` and `scripts/snapshot_bot.py` are Python — direct import + call.
- improve-bot-advised is a Claude **skill** (`SKILL.md`) — wiring is an *added instruction* in the skill body telling Claude to invoke the helper after each iteration commit. Verification is manual (Step 11 smoke gate runs an advised iteration and confirms lineage updates), not unit-testable.
Subprocess invocations within the helper have 60s timeout each; failures log warning but never block promotion. This avoids drift between call sites and ensures `snapshot_bot.py` doesn't silently skip the lineage rebuild.
*Note:* `scripts/bootstrap_promotion.py` was mentioned in v2 of this plan as a possible 4th call site — verified **does not exist** on 2026-05-01 (only an orphan test file references it). Dropped from this plan.

**6.11. Input validation on subprocess invocations and path params.**
All FastAPI path params and subprocess inputs are validated against strict regex before use. All subprocess invocations use list-form args with `shell=False`.
- `version` ∈ path / function arg: validate against `^v\d+$`. Invalid → 400 (API) or `ValueError` (helper).
- `game_id` ∈ path: validate against existing game-id format from `database.py` (UUID-like; check `_is_valid_game_id` helper or add one). Invalid → 400.
- `sha` for `git show <sha>:bots/current/current.txt`: validate against `^[0-9a-f]{7,40}$`. Invalid → skip the entry, log warning, continue (the improvement_log is operator-trusted but a malformed entry shouldn't crash the endpoint or open a shell-injection vector).
- All subprocess invocations explicitly use `subprocess.run([prog, *args], shell=False, timeout=...)` — never string-form, never `shell=True`.
*Tests for this:* `tests/test_api_versions.py` and `tests/test_api_forensics.py` include "rejects malformed version param with 400" and "rejects malformed game_id with 400" cases. `tests/test_post_promotion_hooks.py` includes "rejects invalid version with ValueError, never invokes subprocess."

---

## 7. Build steps

12 steps. Step 1 is split into 1a/1b/1c (three sub-batches of endpoints, ~3 endpoints each) for reviewability. Step 11 is the data-pipeline smoke gate (required by quality bar). Step 12 is the autonomous-system observation soak.

### Step 1a: Backend foundation — version registry, lineage endpoint (no lazy-init), config endpoint
- **Problem:** Add three endpoints to `bots/v10/api.py` (current snapshot, the production import target): `GET /api/versions` (with derived `race` and `harness_origin`, both validated against §6.11 rules), `GET /api/versions/{v}/config`, `GET /api/lineage` **returning `{nodes:[], edges:[]}` for missing-file case** (lazy-init wired in Step 2 once `build_lineage.py` exists). Use separate `_per_version_data_dir(version)` and `_cross_version_data_dir()` resolvers. Add input-validation helpers (regex-checked path params). All endpoints `async def`. Return empty list/object on missing data files; never 500.
- **Issue:** #252
- **Status:** DONE (2026-05-01)
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** 3 endpoints; dual resolver functions; input-validation helpers; pytest contract tests `tests/test_api_versions.py` and `tests/test_api_lineage.py` (including malformed-input rejection tests). Bundle-size baseline measurement recorded in PR description (`npm run build`; gz size of `dist/assets/index-*.js`).
- **Done when:** `pytest tests/test_api_versions.py tests/test_api_lineage.py` pass; manual `curl http://localhost:8765/api/versions` returns 11 entries with derived race + harness_origin populated; `curl http://localhost:8765/api/versions/v3@bad/config` returns 400; `curl http://localhost:8765/api/lineage` with missing `data/lineage.json` returns `{nodes:[], edges:[]}` (not an error).
- **Depends on:** none

### Step 1b: Backend training-data endpoints
- **Problem:** Add three per-version data-read endpoints to `bots/v10/api.py`: `GET /api/versions/{v}/training-history`, `GET /api/versions/{v}/actions`, `GET /api/versions/{v}/improvements` (with files_changed-path target-version derivation, including `bots/current/...` SHA lookup via `git show <sha>:bots/current/current.txt` with SHA regex-validated per §6.11). All `async def` + `await asyncio.to_thread` for the git subprocess.
- **Issue:** #253
- **Status:** DONE (2026-05-01)
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** 3 endpoints; pytest contract tests `tests/test_api_versions_data.py` (including malformed-SHA-skipped behaviour).
- **Done when:** All three return correctly-shaped data for v3 (a version known to have games + improvements); bots/current SHA-lookup-derivation correctly resolves a known historical advised commit to its target version; injecting a malformed SHA into a fixture entry causes that entry to be skipped (logged warning), not crash the endpoint.
- **Depends on:** 1a

### Step 1c: Backend aggregator + forensics + weight-dynamics-read endpoints
- **Problem:** Add three endpoints: `GET /api/runs/active` (aggregates existing `/api/training/daemon`, `/api/advised/state`, `/api/evolve/running-rounds`, plus `data/evolve_round_<worker_id>.json` glob), `GET /api/versions/{v}/forensics/{game_id}` (returns `expert_dispatch: null` always; both path params validated per §6.11), `GET /api/versions/{v}/weight-dynamics` (reads JSONL; returns `[]` if absent; surfaces failure-rows with non-null `error` field).
- **Issue:** #254
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** 3 endpoints; pytest contract tests `tests/test_api_runs_active.py` and `tests/test_api_forensics.py` (including malformed-game_id and malformed-version rejection tests).
- **Done when:** Tests pass; manual: with no harness running, `/api/runs/active` returns `[]`; with an evolve worker running, `/api/runs/active` returns a card-row for it; `/api/versions/v3/forensics/<recent_game_id>` returns trajectory with `win_prob` populated (Phase N is live); malformed-input requests return 400.
- **Depends on:** 1a

### Step 2: Lineage builder script + centralised hook helper + lazy-init wiring
- **Problem:** Build `scripts/build_lineage.py` that walks `bots/v*/manifest.json`, `data/improvement_log.json`, `data/evolve_results.jsonl` and writes `data/lineage.json` with the DAG schema in §5. Atomic-replace writes (write to `.tmp`, fsync, `os.replace` with retry-with-backoff helper). Idempotent. Build `bots/v0/learning/post_promotion_hooks.py` exposing `run_post_promotion_hooks(version)` with input validation (§6.11) and centralised subprocess-invocation pattern. Wire from `scripts/evolve.py` (after `git_commit_evo_auto`) and `scripts/snapshot_bot.py` (after snapshot). Add the SKILL.md instruction in `.claude/skills/improve-bot-advised/SKILL.md` (or wherever the skill body lives) telling Claude to invoke the helper after each iteration commit. **Wire `/api/lineage` lazy-init** (replaces Step 1a's empty-fallback): on missing file, acquire process-wide `asyncio.Lock`, invoke `build_lineage.py` via `asyncio.to_thread(subprocess.run, ...)`, return result. Failures in any hook-invoked subprocess log warning, do not break promotion.
- **Issue:** #255
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** `scripts/build_lineage.py`; `bots/v0/learning/post_promotion_hooks.py` (canonical source); `bots/v10/learning/post_promotion_hooks.py` (current snapshot copy); modified `scripts/evolve.py`, `scripts/snapshot_bot.py`; modified `.claude/skills/improve-bot-advised/SKILL.md`; modified `bots/v10/api.py` `/api/lineage` endpoint with lazy-init; regenerated `data/lineage.json` with 11 versions + valid edges; pytest tests `tests/test_build_lineage.py`, `tests/test_post_promotion_hooks.py` (including invalid-version rejection + concurrent-lazy-init lock test).
- **Done when:** Running script on existing repo state produces valid DAG with all 11 versions and all promotion edges; injecting a mock evolve promotion regenerates file with new edge; injecting a script crash inside the hook does not break the calling promotion path (verified via test); deleting `data/lineage.json` and hitting `/api/lineage` triggers lazy-init and returns valid DAG; invoking helper with version `"v3; rm -rf"` raises `ValueError` and never invokes subprocess (verified via test).
- **Depends on:** 1a

### Step 3: Models tab shell + sub-view router
- **Problem:** Add `Models` tab to `App.tsx`, remove `Improvements` tab (port content into Step 4's `LineageView`). Build `ModelsTab.tsx` shell with version selector strip, race filter (hidden when `len({v.race or "protoss" for v in versions}) <= 1`), harness filter chips (`advised | evolve | manual | self-play`), 5-way sub-view router, and shared selected-version state. Pass `onNodeSelect` callback prop to LineageView (will be filled in Step 4) that sets selected-version state and switches sub-view to `inspector`. Default sub-view: `lineage`.
- **Issue:** #256
- **Flags:** `--reviewers code --isolation worktree --ui`
- **Produces:** `ModelsTab.tsx`, `useVersions.ts`, vitest tests; `ImprovementsTab.tsx` + test deleted (after Step 4 confirms Lineage timeline mode parity); `App.tsx` updated.
- **Done when:** Tab renders, version dropdown populated from real registry (11 versions), race filter is hidden, harness filter chips render and filter the version list, sub-view router switches between 5 placeholder panels, `onNodeSelect` callback prop defined and passed down (no-op consumer until Step 4).
- **Depends on:** 1a

### Step 4: Lineage view (tree + timeline modes; subsume Improvements)
- **Problem:** Build `LineageView.tsx` with tree mode (d3-hierarchy cluster layout, native SVG, harness-coloured nodes, edge labels per §5 rules) + timeline mode (port `ImprovementsTab.tsx` content verbatim, source badges, expandable rows). Mode toggle button. Default Tree. Tree node click invokes `onNodeSelect(version)` prop (wired by Step 3). Add `d3-hierarchy` to `frontend/package.json`. Verify no `useApi` cache-key bump needed for `/api/improvements/unified` (shape unchanged); if any field shape shifts, bump cache key. Delete `ImprovementsTab.tsx` + test only after timeline-mode test parity is verified.
- **Issue:** #257
- **Flags:** `--reviewers code --isolation worktree --ui`
- **Produces:** `LineageView.tsx`, `useLineage.ts`, vitest tests covering tree render + timeline render + mode toggle + node click + onNodeSelect propagation; `ImprovementsTab.tsx` + test deleted; bundle-size delta recorded in PR description.
- **Done when:** Tab renders family tree with 11 versions and promotion edges; timeline mode shows existing improvements identical to old Improvements tab; clicking a tree node fires `onNodeSelect` (verified via vitest mock); UI screenshot test green; bundle delta ≤30KB gz.
- **Depends on:** 1a, 2, 3

### Step 5: Live Runs grid
- **Problem:** Build `LiveRunsGrid.tsx` consuming `/api/runs/active`. One card per active harness using the layout described in §5 (header + metric strip + progress bar + score + expand). Empty state: "No active runs." Polls at 2s like other live surfaces.
- **Issue:** #258
- **Flags:** `--reviewers code --isolation worktree --ui`
- **Produces:** `LiveRunsGrid.tsx`, `useRunsActive.ts`, vitest tests covering empty / single / multi-card / expand states.
- **Done when:** With evolve worker active, card appears within 2s; with advised iteration also active, both cards visible; empty state visible when nothing running; expand reveals full state JSON.
- **Depends on:** 1c, 3

### Step 6: Version Inspector
- **Problem:** Build `VersionInspector.tsx` with five collapsible sub-panels (Config, Training curve, Actions, Improvements applied, Weight Dynamics). Use recharts (already present) for line + bar charts. Drill-in trigger from Step 3's `onNodeSelect` handler now resolves to populated Inspector. Weight Dynamics panel shows "Pending — run scripts/compute_weight_dynamics.py" until Step 9 ships data; failure-rows render as red dots with hover error tooltips. Add "Compare with parent" quick-link button that switches to Compare sub-view with A=current, B=parent prefilled.
- **Issue:** #259
- **Flags:** `--reviewers code --isolation worktree --ui`
- **Produces:** `VersionInspector.tsx`, `useVersionDetail.ts`, vitest tests for each sub-panel including failure-row rendering.
- **Done when:** Picking v3 in Lineage opens Inspector with all five panels rendering (4 populated from real data, 1 placeholder); "Compare with parent" navigates to Compare sub-view with v3 + v3.parent preselected.
- **Depends on:** 1a, 1b, 4

### Step 7: Compare view
- **Problem:** Build `CompareView.tsx` with A/B selector (initial state from URL state or "Compare with parent" handoff), hyperparams deep-diff (red/green highlighting), reward-rules diff (rule add/modify/remove), Elo delta from `/api/ladder`. Weight KL placeholder until Step 9.
- **Issue:** #260
- **Flags:** `--reviewers code --isolation worktree --ui`
- **Produces:** `CompareView.tsx`, diff utilities (`utils/deepDiff.ts`), vitest tests.
- **Done when:** Picking v2 + v4 shows three populated diff panels and Elo delta; "Compare with parent" prefill from Inspector works; sibling-comparison shows "no direct lineage" placeholder.
- **Depends on:** 1a, 1b, 4

### Step 8: Forensics view
- **Problem:** Build `ForensicsView.tsx` with game-id selector (default: most recent game in current version), winprob trajectory line chart with give-up trigger marked as a vertical reference line (recharts), expert-dispatch placeholder card "Phase O pending." Reads `/api/versions/{v}/forensics/{game_id}`.
- **Issue:** #261
- **Flags:** `--reviewers code --isolation worktree --ui`
- **Produces:** `ForensicsView.tsx`, vitest test with seeded transitions data.
- **Done when:** Picking a recent game shows winprob curve; if game had give-up trigger, vertical line marks it; placeholder card visible.
- **Depends on:** 1c, 4

### Step 9: Weight Dynamics — offline script + chart integration
- **Problem:** Build `scripts/compute_weight_dynamics.py` per §5 spec (loads SB3 .zip, computes layer L2 norms + KL divergence; auto-resolves canary source: diagnostic_states.json if present, else hashed 100-row transition sample; per-checkpoint failure → emit failure-row with `error` field, never crash). Append rows to `data/weight_dynamics.jsonl` under advisory lockfile. Wire post-promotion hook (already centralised in Step 2's helper — just add the script invocation). Backfill 11 existing checkpoints. Wire chart to Inspector (Weight Dynamics panel) + KL metric to Compare view. Open small follow-up issue: investigate whether trainer.py should be writing `diagnostic_states.json`.
- **Issue:** #262
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** `scripts/compute_weight_dynamics.py`; populated `data/weight_dynamics.jsonl` (≥11 success-rows after backfill, possibly some failure-rows if torch crashes); chart in `VersionInspector` + KL number in `CompareView`; updated `bots/v0/learning/post_promotion_hooks.py` (and v10 copy) to invoke the script; pytest tests `tests/test_compute_weight_dynamics.py` (including diagnostic-states-present + fallback paths AND per-checkpoint failure → error-row behaviour); follow-up issue link in plan §8.
- **Done when:** Backfill completes ~30s (hard-fail at >60s); Inspector shows L2 line chart for v3; Compare shows KL number for v2 vs v4 (parent → child) and "no direct lineage" for v3 vs v5; running an evolve generation triggers a new JSONL row; injecting a torch crash into one checkpoint produces a failure-row, the rest of the backfill continues, and Inspector renders that checkpoint as a red dot with error tooltip.
- **Depends on:** 2, 6, 7

### Step 10: Observable tab shell + wiki page
- **Problem:** Add `Observable` top-level tab to `App.tsx`. Build `ObservableTab.tsx` with two-version pool selector (queries real `/api/versions`) and Phase L placeholder card. No exhibition controls. Write `documentation/wiki/models-tab.md` content per §5 outline (including recovery procedures). Update `documentation/wiki/index.md` tab inventory.
- **Issue:** #263
- **Flags:** `--reviewers code --isolation worktree --ui`
- **Produces:** `ObservableTab.tsx`, modified `App.tsx`, vitest test, `documentation/wiki/models-tab.md`, modified `documentation/wiki/index.md`.
- **Done when:** Observable tab renders with two version dropdowns populated from real registry; placeholder card visible with link to wiki page; wiki page renders correctly via the operator-commands rendering path.
- **Depends on:** 1a

### Step 11: Smoke gate — pipeline end-to-end
- **Problem:** 60-second end-to-end run with REAL backend + REAL frontend + REAL data dir (not mocks). Start backend, mount Models tab, navigate each of the 5 sub-views, mount Observable tab, exercise pool selector. Verify: every new endpoint returns non-error responses with real data; every sub-view mounts without exception; lineage tree shows ≥10 nodes; live runs grid shows current state (likely empty or 1 card); inspector populates for current version; compare works for two distinct versions; forensics shows a real recent game; weight dynamics chart has ≥1 point. Verify per-version vs cross-version data-dir resolvers both work (read from `bots/v3/data/training.db` AND `data/improvement_log.json` in same request flow). Verify lazy-init: delete `data/lineage.json`, hit `/api/lineage`, assert it self-rebuilds. Verify input-validation: hit `/api/versions/v3@bad/config`, assert 400. **Verify improve-bot-advised SKILL.md instruction is effective:** run a single advised iteration (mock if needed), confirm `data/lineage.json` mtime updates from the post-iteration hook invocation. No mocks for the dashboard surfaces.
- **Type:** code (automated where possible; SKILL.md verification may require manual run)
- **Issue:** #264
- **Flags:** `--reviewers runtime --isolation worktree --ui`
- **Produces:** `scripts/smoke_models_tab.sh`, vitest e2e suite invoking real backend, smoke-gate report file.
- **Done when:** All assertions green within 60s wall clock (excluding the SKILL.md verification, which can run separately); report file lists every endpoint hit + status code + payload-size sanity check; lazy-init self-rebuild verified; per-version + cross-version reads both confirmed; input-validation rejects confirmed; SKILL.md hook confirmed manually.
- **Depends on:** 1a, 1b, 1c, 2, 3, 4, 5, 6, 7, 8, 9, 10

### Step 12: Observation soak — autonomous behavior watch
- **Problem:** Run an overnight evolve soak (8h+) with the dashboard open and Models tab actively monitored. Watch for: lineage tree updates if a promotion fires; Live Runs grid stays accurate as workers cycle; Weight Dynamics auto-refreshes via post-promotion hook (success-rows or failure-rows as appropriate); no UI crashes; no stale-data banners; per-version vs cross-version resolver does not regress (memory: this has bitten before); concurrent-write safety on lineage.json + weight_dynamics.jsonl holds under real load. Document any issues found in a soak-run markdown file.
- **Type:** wait
- **Issue:** #265
- **Flags:** (no build-step flags; this is wall-clock observation work)
- **Produces:** `documentation/soak-test-runs/models-tab-observation-<date>.md` with lineage diff (before vs after), live-runs accuracy report, weight-dynamics auto-refresh confirmation, screenshots, and any defects found.
- **Done when:** Soak completes without dashboard crash; live-runs grid stayed accurate throughout; **if** any promotion fires during the soak, lineage updates within 60s of `evolve_results.jsonl` write AND new weight_dynamics row appears within 90s — **else** dashboard remained responsive throughout and no concurrent-write corruption observed (per memory: 8h soaks sometimes produce 0 promotions; that's not a Step 12 failure).
- **Depends on:** 11

---

## 8. Risks and open questions

| Item | Risk | Mitigation |
|---|---|---|
| Per-version vs cross-version data-dir confusion | Per memory, this has bitten before — backend reads from wrong dir, endpoint returns idle skeleton silently | Explicit separate resolver functions; smoke gate (Step 11) verifies both; observation soak (Step 12) catches under real concurrent load |
| Concurrent writes (lineage.json + weight_dynamics.jsonl + Windows os.replace race + lazy-init double-build) | Hooks fire from 3 call sites; rare-but-real concurrency on multiple files | Atomic-replace + retry-with-backoff (matches existing evolve fix `08fa85c`) for lineage.json; advisory lockfile for JSONL append; process-wide `asyncio.Lock` around lazy-init to prevent double-build; Step 12 stresses under real load |
| Endpoint changes only land in `bots/v10/api.py` (current snapshot) | Historical versions don't get the endpoints — but they're dormant in production, so this is fine | §6.9 documents the policy; v0 keeps canonical source for `post_promotion_hooks.py`; future evolve generations re-snapshot from current and inherit |
| Command-injection in subprocess invocations | Path params and improvement_log SHAs flow into subprocess calls | §6.11 mandates strict regex validation + list-form `subprocess.run(shell=False)`; Steps 1a/1c/2 each include rejection tests |
| `useApi` IDB cache-key on schema shifts | Per memory, old cached data crashes React when shape changes | Step 4 verifies `/api/improvements/unified` shape unchanged; bumps cache key if any new endpoint reuses a shape that already had a key |
| `training.db` query latency on large versions | Action histogram or training-history endpoint could be slow on multi-GB DB | Pre-aggregate to per-version `summary.json` if observed >500ms in Step 11; uses existing `idx_games_model` index |
| Lineage tree layout for many versions | Tree rendering may degrade past ~30 versions | Default collapsed view + lazy expand; tested with 20+ mock versions in Step 4 |
| Weight-dynamics .zip loading impact | Each checkpoint is 10-50MB | Idempotent + hash-keyed; only backfills once; post-promotion hook touches 1 file at a time |
| Bundle size growth | d3-hierarchy + new components could bloat | Hard +120KB gz budget tracked in PR descriptions; baseline measured in Step 1a |
| Operator muscle-memory for Improvements tab | Operators may not realise Improvements moved | Models tab default sub-view is Lineage timeline mode (identical content); wiki page (Step 10) explains |
| Reward Contributions time-series resurrection (the original "weights graphic") | User remembers it but `reward_aggregator.py` pipeline is orphaned; needs new instrumentation | Out of scope this plan; flagged as a follow-up — would be a per-version Inspector sub-panel powered by re-wiring reward_aggregator into game completion |
| Weight KL divergence semantics | Sibling comparison ambiguous (which to call "parent"?) | Plan documents: KL is parent→child only; sibling shows "no direct lineage" placeholder |
| `compute_weight_dynamics.py` per-checkpoint failures | Torch crash, OOM, missing .zip — silent regression risk | Failure-row pattern (§6.7): row with `error` field, no l2/KL data; Inspector renders red dot with error tooltip and retry hint; backfill retries on next run |
| `diagnostic_states.json` missing for all versions | Step 9 KL compute would fail without canary set | Fallback to hashed transition sample (§6.7); records `canary_source` field for post-fix re-compute; small follow-up issue tracks the underlying instrumentation question |
| improve-bot-advised SKILL.md instruction may not be followed by Claude reliably | Skill-based wiring is non-deterministic compared to Python code call | Step 11 smoke gate manually verifies a single advised iteration triggers the hook; if reliability is poor, future plan can add a wrapper script that the skill invokes (deterministic) |
| `--ui` flag screenshot reliability | Playwright tests have flaked historically | Use `--reviewers code --ui` (not full); accept manual screenshot review for ambiguous frames |
| `bots/current/...` SHA-lookup performance | `/api/versions/{v}/improvements` derives target version via `git show <sha>:bots/current/current.txt` per advised entry — could be slow | Cache resolution per commit-SHA in-process; if observed >300ms p95 in Step 11, persist to a `data/.target_version_cache.json` |
| **Open question — Phase G manifest schema migration** | If Phase G prefers manifest-resident `race`, this plan's derivation strategy needs revisiting | Phase G plan owns the call; this plan's race derivation is one-line-replaceable when Phase G ships |
| **Open question — diagnostic_states instrumentation** | Whether trainer.py should be writing diagnostic_states.json | Filed as small follow-up issue; out of this plan's scope |

---

## 9. Testing strategy

**Unit (per step):**
- API contract tests (Steps 1a/1b/1c) using seeded fixtures in flat `tests/test_api_*.py` files. Verify shape, derived race/harness_origin populated, target-version derivation handles `bots/vN/`-path and `bots/current/...`-path entries, empty-list-on-missing-file behaviour, lazy-init on missing `data/lineage.json` (post Step 2). **Input-validation tests:** malformed `version` rejected with 400; malformed `game_id` rejected with 400; malformed SHA in fixture causes that improvement entry to be skipped (logged warning), not endpoint crash.
- vitest tests for each new component, covering loading / empty / populated / error states. Race-filter coercion edge case (mixed missing + present race fields → filter still hidden).
- Hook tests for fetch + cache-key + race-filter coercion behaviour.
- `tests/test_build_lineage.py` (Step 2) — fixture data, atomic-replace race test (mock `os.replace` to fail twice then succeed; assert retry-with-backoff completes).
- `tests/test_compute_weight_dynamics.py` (Step 9) — covers diagnostic-states-present path AND fallback path; verifies `canary_source` field correctly recorded; verifies advisory lockfile prevents concurrent writes; verifies per-checkpoint failure produces an error-row (with `error` field, null l2/KL fields) and the script continues to next checkpoint.
- `tests/test_post_promotion_hooks.py` (Step 2) — verifies failure-doesn't-block-promotion; verifies invalid-version raises ValueError without invoking subprocess; verifies 60s subprocess timeout; verifies concurrent-call lazy-init lock test (two coroutines hit `/api/lineage` with file missing — only one subprocess runs).

**Integration:**
- Step 11 smoke gate — 60s end-to-end with REAL backend + REAL data dir (not mocks). Required because this feature touches producer/consumer chains across SQLite, JSONL, JSON, and `.zip` model files. Mocks would mask exactly the schema-drift bugs the smoke gate is designed to catch. Specifically: per-version + cross-version resolver both exercised in the same request flow; lazy-init self-rebuild verified; input-validation rejects verified live; SKILL.md instruction effectiveness verified by running an advised iteration manually.
- Step 12 observation soak — overnight evolve run with dashboard watched. Required because this is observability infrastructure for an autonomous system; component unit tests cannot exercise time-dependent failures (e.g., post-promotion hook race conditions, lineage rebuild concurrency, JSONL append under live polling). Soft pass when soak yields zero promotions (per memory, that's a real outcome) — only fails on UI crash or concurrent-write corruption.

**Existing tests at risk:**
- `frontend/src/components/ImprovementsTab.test.tsx` — deleted in Step 4 (replaced by `LineageView.test.tsx` timeline-mode coverage).
- No other existing tests should break.

**Quickstart (fresh-context, fresh-checkout enablement):**

*Linux / macOS / WSL (ext4 path):*
1. Pull this branch.
2. `cd frontend && npm install && cd ..` (recharts already present; d3-hierarchy added by Step 4).
3. `uv sync` — installs Python deps if any new ones from Step 9 (likely none — torch + SB3 already present).
4. `python scripts/build_lineage.py` — builds `data/lineage.json`. (Optional — endpoint lazy-inits.)
5. `python scripts/compute_weight_dynamics.py --all` — backfills weight-dynamics for 11 existing checkpoints (~30s; hard-fail at 60s).
6. `bash scripts/start-dev.sh` — starts backend + frontend.
7. Open http://localhost:3000 → click "Models" tab → see family tree.

*Windows (PowerShell — user's preferred shell per memory):*
1. Pull this branch.
2. `cd frontend; npm install; cd ..`
3. `uv sync`
4. `python scripts\build_lineage.py`
5. `python scripts\compute_weight_dynamics.py --all`
6. `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-dev.ps1` *(if a .ps1 launcher exists; else run uvicorn + vite manually per `documentation/wiki/operator-commands.md`)*.
7. Open http://localhost:3000 → click "Models" tab.

**End-to-end verification (post-deployment):**
- Mount Models tab, drill into one version via tree click, exit Inspector via Compare-with-parent button, mount Compare view with two distinct versions, mount Forensics view on a recent game, mount Live Runs grid — all without exception.
- Run `scripts/compute_weight_dynamics.py --all` — verify JSONL has entries for all 11 existing checkpoints with `canary_source: "transitions_sample"` (since diagnostic_states.json absent) and no `error` rows.
- Trigger an evolve generation — watch lineage update + live-runs grid update + new weight-dynamics row appear (all via the centralised hook).
- Mount Observable tab, select two versions in pool dropdowns, see placeholder card.
- Confirm race-filter dropdown is hidden in Models tab (only protoss in registry today).

**Recovery procedures:**
- **Corrupted `data/lineage.json`:** `rm data/lineage.json` — endpoint lazy-inits on next request.
- **Corrupted `data/weight_dynamics.jsonl`:** `mv data/weight_dynamics.jsonl data/weight_dynamics.jsonl.bak` then `python scripts/compute_weight_dynamics.py --all`. Originals retained as `.bak` until manually deleted.
- **Stuck advisory lockfile (`data/.weight_dynamics.lock`) after a crash:** `rm data/.weight_dynamics.lock`. The next script run re-acquires cleanly.
- **Endpoint returns stale data after manual SQLite edits:** dashboard reads on each request (no backend cache); just refresh.

**Regression guards:**
- pytest full suite green: `uv run pytest`
- mypy strict green: `uv run mypy src bots --strict`
- ruff clean: `uv run ruff check .`
- vitest green: `cd frontend && npm test`
- Bundle size delta ≤120KB gz vs baseline recorded in Step 1a
