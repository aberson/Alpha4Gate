# Phase EL — Evolution Lines (parallel lineages + baseline DB + diversity-driven extinction)

**Track:** 9 (Capability research / evolve substrate). **Status:** Planned.
**Prerequisites:** Phase 9 (improve-bot-evolve) operational — this phase
generalizes its single-lineage loop. No other phase gates it.

> Slots into the master plan as a new **Phase EL** on Track 9, appended
> after Phase 9. Step IDs are `EL.1 … EL.7` (letter.number form, matching
> Phases B/D/E/G) so they never collide with the numeric versioning-track
> step numbering. Final phase letter to be confirmed at plan-merge time.

## 1. What This Feature Does

Today `improve-bot-evolve` (Phase 9) runs a **single** evolutionary
lineage: one `bots/current/current.txt` pointer, one parent chain
(`v0 → v1 → … → v13`), one `data/evolve_pool.json`. Every generation
fitness-tests improvements against the *immediate parent* and promotes a
single linear successor. This phase builds the population-genetics
substrate the 2026-04-27 plan-shape notes called for but that never
landed:

1. **Lines of evolution** — run **N parallel lineages** instead of one
   linear chain, each with its own head version, parent chain, and
   improvement pool, scheduled round-robin by a population manager.
2. **Baseline database** — a curated corpus of frozen opponent snapshots
   that candidates can be fitness-tested against (a *gauntlet*), instead
   of only the immediate parent. Gives a stable, shared yardstick across
   lineages and a stronger fitness signal than "beat your own parent."
3. **Diversity metric + diversity-driven extinction events** — a
   behavioral fingerprint per lineage head plus a population manager that
   **culls** lineages that are both low-fitness *and* low-diversity
   (redundant with a stronger sibling), keeping the population under a cap
   and preserving behavioral variety.

It is built because the single-lineage loop converges on one strategy and
re-proposes already-promoted ideas (see memory
`feedback_evolve_priors_diminishing_returns` — "Splash-readiness scored
7-2 then 4-5 once baked in"). Parallel diverse lineages with extinction
pressure is the standard escape from that local optimum.

## 2. Existing Context

- **`src/orchestrator/evolve.py`** — phase primitives: `Improvement`
  (frozen dataclass pool item), `run_fitness_eval(parent, imp, …)`
  (snapshots parent → applies one imp → plays `games` candidate-vs-parent
  → buckets pass/close/fail), `run_regression_eval(new_parent,
  prior_parent, …)`, `generate_pool(parent, …)` (mirror games + Claude
  advisor → orthogonal `Improvement` pool). The opponent in
  `run_fitness_eval` is **already a parameter** (`parent`) — the baseline
  gauntlet generalizes the *caller*, not this primitive.
- **`scripts/evolve.py`** — the generation loop: `generate_pool` →
  fitness each imp → `_stack_apply_and_promote` (snapshot → apply winners
  → import-check → `[evo-auto]` commit) → `run_regression_eval`. State in
  `data/evolve_pool.json`, `data/evolve_results.jsonl`,
  `data/evolve_run_state.json`. Single-flight at `--concurrency 1`
  (default); a parallel-fitness dispatcher exists for N>1 workers within a
  generation but the *lineage* count is still 1.
- **Pointer model** — `bots/current/current.txt` holds the active version
  name; `orchestrator.registry.current_version()` reads it. Consumed by
  `registry.py`, `snapshot.py`, `evolve.py`, `selfplay.py`, `ladder.py`,
  and every `bots/vN/{config,api}.py`. **This phase does not change that
  shape** — the scheduler flips the existing pointer between lineage heads
  before each generation, so all 40 consumers keep working unchanged.
- **Cross-version state** lives at repo-root `data/` (per
  `.claude/rules/bot-runtime.md` — per-version state is under
  `bots/<v>/data/`, orchestrator/evolve/ladder state is repo-root
  `data/`). New registries (`lineages.json`, `baselines.json`,
  `fingerprints.json`) land there, read via dedicated dir resolvers.
- **Dashboard** — `frontend/src/components/EvolutionTab.tsx` +
  `useEvolveRun.ts` render evolve run state from `/api/evolve/state`
  (served by the active version's `bots/current/api.py`). New lineage view
  extends this tab.
- **Adjacent backlog** — the already-scoped **PFSP-lineage regression
  gate** (`evolve-gate-reduction-plan.md` §follow-up) samples a
  distribution of ancestors for regression. The baseline DB (EL.2) is the
  opponent-corpus substrate that idea also needs; this phase delivers it.

## 3. Scope

**In:**
- N parallel lineages with per-lineage head + pool + parent chain, scheduled
  round-robin (sequential, single-flight — respects the SC2 2-client cap).
- Baseline opponent database + CLI + a fitness gauntlet mode.
- A behavioral diversity fingerprint + pairwise distance.
- A population manager: extinction (cull) + cap enforcement + optional
  repopulation by forking a strong, diverse survivor.
- Dashboard surfacing of lineages, diversity, and extinction events.
- A smoke gate and a multi-hour observation soak.

**Out (explicitly):**
- **Task-ordering / time-in-task species knobs** — DEFERRED. Per the Hydra
  investigation, the monolith species is "differentiated only by which
  mini-games it practiced"; these knobs are mini-game concepts and are
  **blocked on Track 7 (Phases H/I/J), currently investigation-blocked.**
  Revisit when a scored mini-game/task corpus exists. No build steps now.
- **Truly concurrent lineage execution** — lineages are scheduled
  round-robin, not run in parallel SC2 processes. Concurrency within a
  generation stays governed by the existing `--concurrency` dispatcher and
  the 2-client cap. True multi-lineage parallelism is a later optimization.
- **Hydra / mixture-of-experts** — that is Phase O, already on the plan.
- **Learned diversity objectives (novelty search / MAP-Elites archives).**
  The fingerprint is a measurement + cull signal, not a search objective.

## 4. Impact Analysis

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `scripts/evolve.py` | extend | wrap the generation loop in a lineage scheduler; add `--lineages`, `--fitness-mode`, `--population-cap` flags; per-lineage pool/state paths | read in full (lines 1-1601 + argparse `build_parser` at L120-293); single-lineage loop confirmed; pointer flips already isolated via `_restore_pointer` |
| `src/orchestrator/evolve.py` | extend | add `run_baseline_gauntlet()` caller wrapper around `run_fitness_eval` (opponent already parameterized) | read in full; `run_fitness_eval(parent, imp, …)` L522 takes opponent as `parent` arg — no signature change to the primitive |
| `.claude/skills/improve-bot-evolve/SKILL.md` | modify | document the new `--lineages` / `--fitness-mode` / `--population-cap` flags so the skill can drive multi-lineage runs (bare invocation stays single-lineage) | grep confirmed SKILL.md references `scripts/evolve.py`; flags additive, defaults preserve behavior |
| `bots/current/api.py` (active version, today `bots/v13/api.py`) | extend | new `GET /api/evolve/lineages` reading repo-root `data/lineages.json` via a dedicated `_evolve_dir` resolver | grep'd `/api/evolve` consumers: 13 `bots/vN/api.py` + `EvolutionTab.tsx` + `useEvolveRun.ts`; new endpoint is additive |
| `frontend/src/components/EvolutionTab.tsx` | extend | render lineage list, diversity matrix, extinction-event log | grep confirmed component + `EvolutionTab.test.tsx` exist |
| `frontend/src/hooks/useEvolveRun.ts` | modify | add lineages fetch; **bump `cacheKey`** per `feedback_useapi_cache_schema_break` | grep confirmed hook + `useEvolveRun.test.ts`; cacheKey bump required on shape change |
| `bots/current/current.txt` / `registry.current_version()` | **unchanged** | scheduler flips this pointer between lineage heads; shape identical | grep'd 40 consumers (`registry.py`, `snapshot.py`, `selfplay.py`, `ladder.py`, all `bots/vN/config.py`); none see a new shape |
| `data/lineages.json`, `data/baselines.json`, `data/fingerprints.json` | new (gitignored) | cross-version registries; atomic-replace writes (reuse the 5×50→800ms retry helper per `feedback_evolve_windows_atomic_replace_race`) | new files; no consumers to break |

## 5. New Components

- **`src/orchestrator/lineages.py`** — `Lineage` dataclass (`lineage_id`,
  `head_version`, `pool_path`, `parent_chain`, `created_at`, `status`),
  `lineages.json` read/write (atomic), and a round-robin `next_lineage()`
  scheduler. **Back-compat:** an empty/absent registry resolves to a single
  implicit `main` lineage whose head is `current_version()` — today's
  behavior, byte-identical default.
- **`src/orchestrator/baselines.py`** + **`scripts/baseline.py`** CLI —
  register/list/freeze a version as a named baseline; `data/baselines.json`
  registry. `run_baseline_gauntlet(candidate, baselines, games_each, …)` in
  `evolve.py` plays the candidate vs each baseline and aggregates to a
  fitness vector.
- **`src/orchestrator/fingerprint.py`** — `compute_fingerprint(version)` →
  behavioral vector, and `fingerprint_distance(a, b)`. **v1 fingerprint =
  the per-baseline win-rate vector** from the EL.2 gauntlet (needs no new
  in-game telemetry; two lineages that beat/lose the same baselines the
  same way are behaviorally redundant). Build-order/army-composition
  enrichment is a flagged later refinement, not v1.
- **`src/orchestrator/population.py`** — `decide_extinctions(lineages,
  fingerprints, fitnesses, cap)` → keep/cull/repopulate verdicts. A
  lineage is culled when the population exceeds `cap` **and** it is
  dominated: lower baseline-fitness **and** fingerprint distance to a
  survivor below a redundancy threshold. Optional repopulation forks a
  high-fitness, high-diversity survivor.
- **Dashboard lineage view** — extends `EvolutionTab.tsx`: lineage cards
  (head version, generations, baseline-fitness), a pairwise-diversity
  matrix, and an extinction-event timeline.

## 6. Design Decisions

- **Overlay, not rewrite, of the pointer model.** `current.txt` stays the
  single active-head pointer; lineages are an overlay registry and the
  scheduler flips the existing pointer before each generation. *Alternative
  considered:* per-lineage pointer files / removing the global pointer —
  rejected because it would touch all 40 `current_version()` consumers for
  no functional gain at single-flight scheduling.
- **Round-robin sequential scheduling, not concurrent lineages.** Honors
  the SC2 2-client cap and keeps the proven single-flight promote/commit
  path intact. *Alternative:* concurrent lineage processes — rejected as
  out-of-scope complexity (the parallelization plan already covers
  intra-generation concurrency).
- **Baseline-result vector as the v1 diversity fingerprint.** It is
  available for free from the EL.2 gauntlet, needs no new telemetry, and
  directly encodes "behaves differently against the same opponents."
  *Alternative:* build-order/army-composition fingerprint — deferred as an
  enrichment because `SelfPlayRecord` does not currently carry composition
  and harvesting it from logs/`training.db` is its own task.
- **Extinction requires BOTH low fitness AND low diversity.** A
  low-fitness-but-unique lineage survives (it explores a different niche);
  a high-fitness lineage always survives. *Alternative:* fitness-only
  culling — rejected because it collapses diversity, defeating the purpose.
- **Defer species knobs rather than reinterpret them.** Task-ordering and
  time-in-task are mini-game constructs; building a non-mini-game proxy now
  would codify the wrong abstraction. Captured as a blocked dependency on
  Track 7.

## 7. Build Steps

<!-- autofix-applied: 2026-06-19 -->
### Step EL.1: Lineage registry + round-robin scheduler
- **Problem:** Add `src/orchestrator/lineages.py` (`Lineage` dataclass,
  atomic `lineages.json` read/write, `next_lineage()` round-robin) and wrap
  the `scripts/evolve.py` generation loop so it schedules across N
  lineages, flipping `current.txt` to each lineage head before its
  generation. An empty/absent registry must resolve to a single implicit
  `main` lineage == today's behavior (byte-identical default). Add
  `--lineages N` (default 1).
- **Type:** code
- **Issue:** #273
- **Flags:** --reviewers code
- **Produces:** `src/orchestrator/lineages.py`; extended `scripts/evolve.py`
  loop + `--lineages` flag; per-lineage pool/state path derivation;
  `improve-bot-evolve/SKILL.md` note documenting the new flag.
- **Done when:** `tests/test_lineages.py` covers registry round-trip,
  round-robin order, and the implicit-`main` back-compat; existing
  `tests/test_evolve_cli.py` still passes unchanged with `--lineages 1`
  (default path is byte-identical).
- **Depends on:** none.

### Step EL.2: Baseline opponent database + fitness gauntlet
- **Problem:** Add `src/orchestrator/baselines.py` + `scripts/baseline.py`
  CLI (register/list/freeze a version as a named baseline; `data/baselines.json`),
  and `run_baseline_gauntlet(candidate, baselines, games_each)` in
  `src/orchestrator/evolve.py` that plays the candidate vs each baseline and
  aggregates to a per-baseline win-rate vector + scalar fitness. Add
  `--fitness-mode {parent,baseline,both}` (default `parent`, preserving
  current behavior) to `scripts/evolve.py`.
- **Type:** code
- **Issue:** #274
- **Flags:** --reviewers code
- **Produces:** `src/orchestrator/baselines.py`, `scripts/baseline.py`,
  `run_baseline_gauntlet()`, `--fitness-mode` flag.
- **Done when:** `tests/test_baselines.py` (registry + gauntlet aggregation,
  mocked `run_batch`) green; `--fitness-mode parent` reproduces current
  fitness behavior exactly.
- **Depends on:** none (independent of EL.1; can build in parallel).

### Step EL.3: Behavioral diversity fingerprint
- **Problem:** Add `src/orchestrator/fingerprint.py` with
  `compute_fingerprint(version)` (v1 = per-baseline win-rate vector from the
  EL.2 gauntlet), `fingerprint_distance(a, b)`, and atomic persistence to
  `data/fingerprints.json`. Document the build-order enrichment as a future
  refinement in the module docstring.
- **Type:** code
- **Issue:** #275
- **Flags:** --reviewers code
- **Produces:** `src/orchestrator/fingerprint.py`, `data/fingerprints.json`
  writer.
- **Done when:** `tests/test_fingerprint.py` asserts identical-result
  lineages → distance ≈ 0 and opposite-result lineages → max distance, on
  synthetic gauntlet vectors.
- **Depends on:** EL.2 (consumes the gauntlet output shape).

### Step EL.4: Population manager — diversity-driven extinction
- **Problem:** Add `src/orchestrator/population.py` with
  `decide_extinctions(lineages, fingerprints, fitnesses, cap)` returning
  keep/cull/repopulate verdicts (cull only when population > cap AND a
  lineage is dominated: lower fitness AND sub-threshold fingerprint distance
  to a survivor; optional repopulation forks a strong diverse survivor via
  `snapshot_bot.py`). Wire it into the EL.1 scheduler at each generation
  boundary; log extinction events to `data/evolve_results.jsonl` as a new
  `phase: "extinction"` row.
- **Type:** code
- **Issue:** #276
- **Flags:** --reviewers code
- **Produces:** `src/orchestrator/population.py`; scheduler hook;
  extinction-event result rows.
- **Done when:** `tests/test_population.py` covers: no cull under cap;
  dominated-lineage cull; unique-but-weak lineage survives; high-fitness
  lineage always survives. `--population-cap` flag honored.
- **Depends on:** EL.1, EL.2, EL.3.

### Step EL.5: Dashboard lineage + extinction surfacing
- **Problem:** Add `GET /api/evolve/lineages` to the active version's
  `bots/current/api.py` (reads `data/lineages.json` + `data/fingerprints.json`
  via a dedicated `_evolve_dir` resolver, per `.claude/rules/bot-runtime.md`),
  and extend `EvolutionTab.tsx` with a lineage list, pairwise-diversity
  matrix, and extinction-event timeline. Bump `useEvolveRun.ts` `cacheKey`.
- **Type:** code
- **Issue:** #277
- **Flags:** --reviewers runtime --ui --start-cmd "bash scripts/start-dev.sh" --url http://localhost:3000
- **Produces:** `/api/evolve/lineages` endpoint, extended `EvolutionTab.tsx`,
  bumped hook cacheKey, `tests/test_api_evolve_lineages.py`,
  `EvolutionTab.test.tsx` cases.
- **Done when:** vitest + pytest green; Playwright evidence shows the lineage
  view rendering ≥2 lineages with a diversity matrix and an extinction-event
  entry from seeded `data/*.json`.
- **Depends on:** EL.1, EL.4.

### Step EL.6: Multi-lineage smoke gate
- **Problem:** Run ONE real end-to-end multi-lineage round on SC2:
  `python scripts/evolve.py --lineages 2 --pool-size 2 --games-per-eval 3
  --fitness-mode both --population-cap 2 --generations 1 --no-commit`.
  Confirm both lineages schedule, the baseline gauntlet runs, fingerprints
  compute, the population manager evaluates extinction, no orphan
  `SC2_x64.exe`/python on port 8765, and the sandbox hook is intact.
- **Type:** operator
- **Issue:** #278
- **Produces:** a smoke-run record under
  `documentation/soak-test-runs/evolution-lines-smoke-<ts>.md`.
- **Done when:** the round completes without crash; both lineages produced a
  fitness result; `data/lineages.json` + `data/fingerprints.json` populated;
  no orphan processes; `git status` clean (`--no-commit`).
- **Depends on:** EL.1, EL.2, EL.3, EL.4, EL.5.

### Step EL.7: Multi-hour observation soak
- **Problem:** Run 2–3 lineages under a multi-hour budget
  (`--lineages 3 --population-cap 3 --hours 6 --fitness-mode both`) and
  observe the autonomous loop: lineages diverge on fingerprint, ≥1
  extinction event fires (or is justified as not-needed under cap),
  baseline-fitness trends are sane, no orphan SC2 processes, EVO_AUTO commit
  hygiene holds. This is the autonomous-behavior observation step — the loop
  runs unattended over wall-clock time, so time-dependent failures
  (scheduler drift, registry write races, extinction thrash) are invisible
  to unit tests.
- **Type:** wait
- **Issue:** #279
- **Produces:** soak record under
  `documentation/soak-test-runs/evolution-lines-soak-<ts>.md`.
- **Done when:** ≥2 generations per lineage complete; diversity matrix shows
  non-trivial separation; extinction logic exercised at least once or
  documented as not-triggered-by-design; zero orphan processes; commit log
  shows clean `[evo-auto]` rows.
- **Depends on:** EL.6.

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| Scheduler flips `current.txt` mid-run; a concurrent `--serve` reads it | Dashboard shows the "wrong" active version transiently | Reuse the atomic-replace retry helper (`feedback_evolve_windows_atomic_replace_race`); `/api/evolve/lineages` reports per-lineage heads so the operator isn't misled by the single active pointer |
| v1 fingerprint (baseline-result vector) is coarse | Two strategically-distinct lineages with the same baseline results look redundant | Accept for v1 (still catches the common "same results = same behavior" case); build-order enrichment flagged as the first refinement; extinction needs low fitness AND low diversity, so a coarse metric can't cull a strong lineage |
| Extinction thrash (cull → repopulate → cull) | Wasted wall-clock | `--population-cap` + redundancy threshold tuned conservatively; soak EL.7 watches for thrash; repopulation is optional and off by default |
| Round-robin N× slows wall-clock per lineage | Fewer generations per lineage per night | Documented; lineage count is operator-chosen; default `--lineages 1` is unchanged from today |
| Baseline corpus selection | A weak/stale baseline set makes the gauntlet uninformative | `scripts/baseline.py` lets the operator curate; seed with milestone versions (v0, v4, v7, v13) + archetypes |
| EVO_AUTO commits sweep the staged index across lineages | A stray staged file rides into a promote on any lineage | Pre-existing rule (`feedback_evo_auto_commits_sweep_staged`); EL.6/EL.7 records check `git diff --staged --stat` before launch |

## 9. Testing Strategy

- **Unit (pytest, mocked `run_batch`/Claude):** `test_lineages.py`,
  `test_baselines.py`, `test_fingerprint.py`, `test_population.py`,
  `test_api_evolve_lineages.py`. Each new module is unit-tested in isolation
  with the boundary mocked.
- **Back-compat assertions:** `--lineages 1` + `--fitness-mode parent` must
  reproduce current single-lineage behavior; existing `test_evolve.py` /
  `test_evolve_cli.py` pass unchanged.
- **Frontend (vitest):** `EvolutionTab.test.tsx` lineage-view cases;
  `useEvolveRun.test.ts` cacheKey bump.
- **Smoke gate (EL.6):** one real multi-lineage round, no mocks — the
  producer→consumer pipeline (`evolve.py` → `lineages.json`/`fingerprints.json`
  → `/api/evolve/lineages` → `EvolutionTab`) exercised end-to-end once before
  any soak, per the pipeline-smoke-gate rule.
- **Observation soak (EL.7):** the autonomous multi-hour run that surfaces
  time-dependent failures unit tests cannot.
- **Likely-to-break existing tests:** any that assert the exact shape of
  `evolve_results.jsonl` rows (new `phase: "extinction"` row) or
  `/api/evolve/state` response — audit and update those deliberately, not by
  reflexively matching the new shape (per
  `feedback_audit_wire_shape_on_storage_change`).

## Deferred (not in this plan)

- **Species knobs — task ordering, time-in-task.** Blocked on Track 7
  mini-games (Phases H/I/J, investigation-blocked). Revisit when a scored
  task/mini-game corpus exists; the monolith species is "differentiated by
  which mini-games it practiced" (Hydra investigation §1), so these knobs
  need that substrate first.
- **Build-order/army-composition fingerprint enrichment** — first
  refinement after the v1 baseline-result-vector fingerprint proves out.
- **Concurrent lineage execution** — later optimization; bounded by the SC2
  2-client cap and the existing intra-generation parallel dispatcher.
