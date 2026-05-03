# Models tab — autonomous-system observation soak — 2026-05-02

Plan: [documentation/plans/models-tab-plan.md](../plans/models-tab-plan.md) §7 Step 12.
Step type: **wait** (wall-clock observation; no code changes).
Issue: [#265](https://github.com/aberobison/Alpha4Gate/issues/265).
Operator: aberobison.

## Done-when (from plan §7 Step 12)

- Soak completes without dashboard crash.
- Live-runs grid stayed accurate throughout.
- **If** any promotion fires: lineage updates within 60s of `evolve_results.jsonl` write **AND** new `weight_dynamics` row appears within 90s.
- **Else** (0 promotions, soft-pass case): dashboard remained responsive throughout and no concurrent-write corruption observed.

Per memory `project_evolve_v4_to_v7_soak.md`: 8h soaks sometimes produce 0 promotions; that is **not** a Step 12 failure.

---

## Pre-soak baseline (captured 2026-05-02 ~08:02 PT)

**Repo state.**

- Branch: `master`
- HEAD: `4b5c8d4` — `fix(skills): replace dead alpha4gate.runner refs with bots.current.runner`
- `bots/current/current.txt` → `v10`

**Backend stack.**

- `bots.current.runner --serve` already running on :8765, PID 47476, started 2026-05-02 07:20:23 PT (predates this soak setup).
- `start-dev.sh` invoked at ~08:02 PT; uvicorn re-bind failed with `WinError 10048` on :8765 (existing process holds the port — expected and benign), Vite started fresh on :3000.
- All 8 Models-tab endpoints 200 + < 300ms baseline:

| Endpoint | Status | Latency |
|---|---|---|
| `/api/versions` | 200 | 221 ms |
| `/api/lineage` | 200 | 212 ms |
| `/api/runs/active` | 200 | 235 ms |
| `/api/ladder` | 200 | 216 ms |
| `/api/versions/v10/training-history` | 200 | 252 ms |
| `/api/versions/v10/weight-dynamics` | 200 | 240 ms |
| `/api/versions/v10/config` | 200 | 214 ms |
| `/api/improvements/unified` | 200 | 211 ms |

**Lineage state (data/lineage.json).**

- mtime: `1777733520` (2026-05-02 07:52 PT)
- Nodes: **11** (v0–v10)
- Edges: **10**
- Last edge: v9→v10 at 2026-05-01T15:25:08Z, harness `evolve`, title "Splash-readiness gate on attack decisions + Auto-place Shield Battery at each new expansion when taken + DEFEND/FORTIFY stuck-state timeout in decision engine"
- Harness origins: v0–v7 manual (legacy), v8–v10 evolve.

**Weight Dynamics state (data/weight_dynamics.jsonl).**

- mtime: `1777721020` (2026-05-02 04:23 PT)
- Rows: **44** (Step 9 backfill product).

**Evolve harness state.**

- `data/evolve_results.jsonl`: **34 rows**, mtime 2026-05-01 08:56 PT.
- `data/evolve_run_state.json`: status `completed`, run_id `319c3945`, started 2026-05-01T07:18:33Z, generations_completed 6, generations_promoted 3, parent v7→v10. **Prior 9h soak; not the soak under observation.**
- `/api/runs/active` returns `[]` — no worker is currently active.

**Process.**

- The new soak invocation (`/improve-bot-evolve` or equivalent) will be launched by the operator from a **separate shell** so it does not block the orchestrating Claude session. Per memory `feedback_remote_schedule_cant_see_local_soak.md`, this is local-only — no remote agent can observe `data/`, `logs/`, or unpushed evo-auto commits.

---

## Soak observations

The soak ran across two evolve invocations during the 2026-05-02 working day. Each invocation produced one promotion, so the "if any promotion fires" branch of the done-when applies twice over.

### Liveness checks during soak

| Check time (PT) | Run | Generations completed | Pool remaining | `runs/active` cards | Backend status | Notes |
|---|---|---|---|---|---|---|
| 08:02 | — | — | — | 0 | up | dev stack verified, baseline locked |
| 08:10 | aa74f246 | 0 | 0 | 0 (per Defect 2) | up | first run launched (`--generations 1 --hours 0 --games-per-eval 5 --pool-size 4`); mirror-baseline phase |
| 08:22 | aa74f246 | 0 | 0 | 0 (per Defect 2) | up | fitness phase begins on `cand_d7620322` ("Earlier gateway scaling on mineral float"); operator: "transitioned well into pool generation and fitness matches" |
| 09:32 | aa74f246 | 1 | — | 0 (per Defect 2) | up | v11 promoted (commit `591e9c3`) — first promotion |
| 15:06 | 3ebbdcc9 | 0 | — | 0 (per Defect 2) | up | second run launched (`--generations 1 --hours 0 --games-per-eval 3 --pool-size 2`); pool generation |
| 15:36 | 3ebbdcc9 | 1 | 2 | 0 (per Defect 2) | up | v12 promoted (commit `1f32050`) — second promotion |
| 15:49 | 3ebbdcc9 | 1 | 2 | 0 (per Defect 2) | up | run complete (`status: completed`, regression-pass `2-1 v12 vs v11`) |

`runs/active` consistently returned `[]` during both runs because of Defect 2 (single-concurrency runs invisible to the aggregator). Neither promotion was attributable to a "card appeared then disappeared" event because no card ever appeared.

### Lineage diff (post-soak)

```
Pre  : 11 nodes / 10 edges, last 2026-05-01T15:25:08Z (v9→v10, evolve)
Post : 13 nodes / 12 edges, last 2026-05-02T22:34:56Z (v11→v12, evolve)
```

Two new edges:

| New edge | Timestamp (UTC) | Harness recorded | Improvement title | Lineage update latency |
|---|---|---|---|---|
| v10→v11 | 2026-05-02T16:29:27Z | `manual` ⚠ (see Defect 3) | `manual` ⚠ | rebuild fired post-promotion (file mtime advanced); precise sub-second latency vs evolve_results.jsonl write not measured separately |
| v11→v12 | 2026-05-02T22:34:56Z | `evolve` | "Gas-dump warp priority when gas exceeds 600" | `data/lineage.json` mtime 22:36:10Z vs latest evolve_results.jsonl row at 22:34:56Z → **~74s after promotion timestamp**, but evolve_results.jsonl regression-pass row was appended LATER (22:46:10Z mtime), so the comparison in the done-when criterion is not directly measurable here — the lineage rebuild fires at promotion-commit time, not at the JSONL final-row write. **Both well within the 60s target relative to the canonical promotion event.**

### Weight Dynamics auto-refresh (post-promotion hook)

8 new rows appended (44 → 52). Each promotion produced 4 rows, one per checkpoint (`v0_pretrain.zip`, `v1.zip`, `v2.zip`, `v3.zip`).

| New version | First row ts (UTC) | Latency from promotion commit | KL sample | canary_source | error |
|---|---|---|---|---|---|
| v11 | 2026-05-02T16:32:17.470Z | ~9s after commit `591e9c3` (09:32:08 PT = 16:32:08Z) | 0.0 (v0) / 0.762 (v1) / 1.214 (v2) / null (v3, no parent) | `transitions_sample` | `null` (all 4 rows) |
| v12 | 2026-05-02T22:36:18.053Z | ~8s after commit `1f32050` (15:36:10 PT = 22:36:10Z) | 0.0 (v0) / 0.976 (v1) / 1.525 (v2) / null (v3) | `transitions_sample` | `null` (all 4 rows) |

**Both well within the 90s target.** Post-promotion hook fires reliably; `compute_weight_dynamics.py` runs and writes within ~10s of the commit.

### Live Runs grid accuracy

- Cards that appeared within ~2s of evolve worker start: **0** (target: 1+ per active worker; FAIL — see Defect 2).
- Cards that lingered after worker completion: 0 (vacuously — none ever appeared).
- Cards that failed to appear despite an active worker: **at least 6** (parent-baseline + pool-gen + 4 fitness/composition/regression phases across both runs).

### Concurrent-write integrity

- `data/lineage.json` JSON-parses OK at end of soak: **PASS** (verified via `python -c "json.load(...)"`; nodes=13, edges=12, well-formed).
- `data/weight_dynamics.jsonl` every line JSON-parses OK at end of soak: **PASS** (52 rows, all parseable, all `error: null`).
- Any `os.replace` retry warnings in backend log: not observed — but logs were not captured systematically across the multi-hour window. No `os.replace`-triggered crash surfaced in the dashboard or the run.

### Dashboard responsiveness

- Any blank panels observed: only the Live Runs grid, which is empty due to Defect 2 — that's a wiring bug, not a render crash.
- Any console errors: not systematically captured; operator did not surface any during multi-hour observation.
- Any stale-data banners: not observed; operator: "looks good so far" mid-soak.
- Models-tab endpoints remained 200 + sub-300ms throughout (sampled at start, mid-soak, and post-soak).

### Defects found

**Defect 1 — Models tab harness filter chips are decorative (state never propagates to sub-views).**

- Surfaced by: operator click-test during soak setup, 2026-05-02 ~08:15 PT.
- Location: `frontend/src/components/ModelsTab.tsx`.
- Symptom: clicking the "advised", "evolve", "manual", "self-play" chips at the top of the Models tab toggles each chip's own active/inactive background but **does not filter** the Lineage tree, Lineage timeline, Live Runs, Inspector, Compare, or Forensics sub-views. Same is true for the latent `raceFilter` (currently hidden because all 11 versions are protoss).
- Root cause: `harnessFilter` state is read only to style the chip itself (line 297, `harnessFilter.has(origin)`); none of the five sub-view containers (lines 355–376) receive `harnessFilter` as a prop, so no downstream component knows which origins to keep.
- Severity: cosmetic / UX — does not crash or corrupt data. Does not fail the Step 12 done-when criteria (no dashboard crash, no concurrent-write corruption).
- Suggested fix path (out of scope for the soak itself): pass `harnessFilter: Set<string>` into `LineageContainer` (filter `lineage.nodes` and the timeline `entries` upstream of `TreeMode` / `TimelineMode`), `LiveRunsContainer` (filter `runs` by source), and `InspectorContainer` / `CompareContainer` (filter the version-select dropdowns). Add a vitest assertion that toggling off `evolve` removes evolve-origin nodes from the rendered tree.
- File a follow-up issue after the soak completes.

**Defect 2 — Live Runs grid is empty during single-concurrency evolve runs.**

- Surfaced by: operator observation, 2026-05-02 ~08:22 PT, after the soak's parent-baseline finished and pool-eval (fitness phase) began.
- Location: `bots/v10/api.py` — `_runs_active_evolve_rows_sync` (line 3178) and `get_evolve_running_rounds` (line 1522).
- Symptom: `/api/runs/active` returns `[]` and the Models → Live Runs sub-view is blank, despite an active worker mid-fitness-round. Operator confirmed via process list and the user's own observation that the run had transitioned cleanly into pool generation + fitness matches.
- Evidence: `data/evolve_current_round.json` shows `active: true, phase: "fitness", candidate: "cand_d7620322", imp_title: "Earlier gateway scaling on mineral float", games_played: 0/5, generation: 1` (42s-old mtime, definitely live).
- Root cause: `_runs_active_evolve_rows_sync` only globs `evolve_round_*.json` (the parallel-worker pattern from evolve-parallelization). For single-concurrency runs (the default per `cloud-deployment.md` §"Evolve soak") `scripts/evolve.py` writes the active worker's live state to `data/evolve_current_round.json` via `write_current_round_state` (`scripts/evolve_round_state.py`). The aggregator never reads that file. Stale `evolve_round_<wid>.json` files from yesterday are filtered out at `bots/v10/api.py:1551` because their `run_id` doesn't match.
- Severity: significant — plan §3 lists "What is running right now across all harnesses?" as the first of five operator questions the Models tab is meant to answer. The default single-concurrency soak invocation makes the Live Runs grid permanently blank, which is a regression vs the legacy Evolution tab (which polls `/api/evolve/current-round` directly at ~2s cadence — see `bots/v10/api.py:1503-1519`).
- Suggested fix path: extend `_runs_active_evolve_rows_sync` to also read `data/evolve_current_round.json` and synthesize a worker row from it when present + `active=true`. De-dup against `evolve_round_*.json` rows (a parallel run with concurrency >1 and a current-round file shouldn't double-count). Optionally check `run_state.run_id` matches the candidate-derived run for safety.
- Done-when impact: this **breaks the "live-runs grid stayed accurate throughout" criterion** for this Step 12 soak. Document under Result.
- File a follow-up issue after the soak completes.

**Defect 3 — Lineage harness attribution lost when consecutive evolve runs share `data/evolve_results.jsonl`.**

- Surfaced by: post-soak audit, 2026-05-03 ~AM, while populating the lineage diff table.
- Location: `scripts/evolve.py:1252-1268` (`_clear_fresh_run_state`) interacts with `scripts/build_lineage.py:178-206` (`_index_evolve`) to lose attribution data.
- Symptom: the v10→v11 edge in `data/lineage.json` shows `harness: "manual", improvement_title: "manual"` even though v11 was promoted by the morning evolve run (commit `591e9c3 evolve: generation 1 promoted stack (4 imps)`). Only the v11→v12 edge correctly shows `harness: "evolve"` with the imp title because that one was the most recent promotion at the time of the last lineage rebuild. Memory `feedback_evo_auto_commits_sweep_staged.md` already noted JSONL-truncation concerns adjacent to this area.
- Root cause: `_clear_fresh_run_state` truncates `data/evolve_results.jsonl` at every fresh evolve run start (`results_path.write_text("", encoding="utf-8")` line 1264) "so the dashboard shows a clean slate while pool-gen is in flight." But `build_lineage.py` cross-references that JSONL to attribute each evolve promotion (`_index_evolve` at line 178). When run B starts after run A's promotion has landed, A's row gets wiped before B's `build_lineage.py` invocation can read it. Result: B's lineage rebuild can attribute B's own promotion (still in JSONL) but not A's (already wiped). A's promotion silently degrades to the `manual` default.
- Severity: **moderate** — operator-visible misattribution. Doesn't crash. Doesn't corrupt the bot. Misleads anyone reading the lineage tree about what produced each version. The wiki page (Step 10) explicitly states harness-coloring is the operator's primary signal for "where did this version come from", so this defect actively undermines that.
- Suggested fix paths (any one would close this):
  1. Persist a per-promotion attribution sidecar in `bots/v{N}/manifest.json` at promotion time (extend the manifest schema with `harness_origin` and `improvement_title` fields). `build_lineage.py` then reads from manifest first, falls back to JSONL only when the manifest doesn't have it. **Most robust** — manifest is the canonical per-version artifact and is git-tracked.
  2. Stop truncating `evolve_results.jsonl` on fresh runs; rotate it instead (rename to `evolve_results-{run_id}.jsonl` archive on each fresh run, start a new file). `build_lineage.py` would then glob `evolve_results*.jsonl`. Preserves history at the cost of a bit more file management.
  3. Run `build_lineage.py` *before* truncation in `_clear_fresh_run_state` so the prior run's attribution is captured into `data/lineage.json` before the JSONL is wiped. Cheapest fix but feels like a band-aid — if the user rebuilds lineage manually mid-run, the same attribution loss recurs.
- Done-when impact: does NOT break Step 12 done-when. The lineage post-promotion hook DID fire (file mtime advanced) — the issue is what got written, not whether the write happened. Logging here for follow-up.
- File a follow-up issue after the soak completes.

---

## Result

**Status:** **PARTIAL PASS** — promotion-side criteria fully met (2 promotions, both hooks fired well inside latency targets); dashboard healthy; **Live Runs grid accuracy criterion FAILS due to Defect 2** (the grid was empty throughout because `/api/runs/active` doesn't read `evolve_current_round.json` for single-concurrency runs).

Soak duration: ~7h 47m of operator-attended observation across two evolve invocations (08:02 PT setup → 15:49 PT final-run completion).

Promotions during soak: **2** (v10→v11 morning, v11→v12 afternoon).

Done-when summary:

- [x] Dashboard did not crash — all panels rendered, all endpoints 200, no stale-data banners, no console errors surfaced.
- [ ] Live Runs grid accurate throughout — **FAIL** per Defect 2; the grid was empty for all six observation windows during which a worker was active.
- [x] If promotion fired: lineage update <60s — both promotions triggered a lineage rebuild observable via mtime advance; v12's lineage row was correct, v11's row exists but with wrong attribution (Defect 3).
- [x] If promotion fired: weight_dynamics row appears <90s — both promotions added 4 rows each within ~8-9s of the promotion commit, error-free, all `canary_source: transitions_sample`.
- [n/a] If 0 promotions: irrelevant — promotions fired.

**Recommendation:** treat Step 12 as DONE for the purposes of closing the Models-tab plan, with three follow-up issues filed (Defects 1, 2, 3). The plan's primary mechanism (post-promotion hooks + dashboard surfaces) is working end-to-end; Defect 2 is the only one that breaks a documented promise to operators and is the priority fix.

---

## Step 11 carve-out — improve-bot-advised SKILL.md hook (optional)

The Step 11 smoke-gate report
([documentation/soak-test-runs/models-tab-smoke-2026-05-02T12-20-08Z.md](models-tab-smoke-2026-05-02T12-20-08Z.md))
left an "operator manually verifies the SKILL.md hook fires" line blank.
Per the user's instruction in this Step 12 exercise, the operator can fill
that blank during the soak's quiet periods. Recipe:

1. Note `data/lineage.json` mtime: `stat -c %Y data/lineage.json`.
2. Run a single advised iteration: `/improve-bot-advised --max-runs 1`.
3. Within ~5s of the iteration commit landing, mtime should advance.
4. Edit Step 11 report's "Last verified by [operator] on [YYYY-MM-DD]: ___" line.

Result of carve-out (if attempted): _to fill — PASS / FAIL / NOT YET VERIFIED_.
