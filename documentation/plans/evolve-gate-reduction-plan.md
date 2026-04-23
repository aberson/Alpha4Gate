# Evolve Gate Reduction + Rollback-Order Fix Plan

## 1. What This Feature Does

Reduces the `/improve-bot-evolve` self-play pipeline from 3 noise-dominated gates (fitness + composition + regression) to 2 gates (fitness + regression) and fixes a latent rollback-order bug that prevents `git revert` from cleaning up rolled-back promote commits. Together these changes raise the expected real-imp promotion rate per generation from ~0.39 (three compound 73% gates at p=0.60) to ~0.53 (two gates), cut roughly 18 games per generation (~50 minutes at current pace) by eliminating both the composition test batch and the composition-fallback batch, and keep master's commit history consistent with the live bot state after regression rollbacks.

The driving evidence is run `20260422-0824`: 10.33h wall-clock, 4 generations, 0 net promotions. Gens 1 and 3 produced stack-promotions that were correctly rolled back at the regression gate; gens 2 and 4 produced stack-fail + fallback-fail outcomes. In both rolled-back generations the runtime `git revert` failed with exit 128 ("local changes would be overwritten by merge") because `current.txt` was written to the prior parent before the revert call, leaving the working tree dirty. The composition gate added a third Bernoulli filter without providing unique detection capability — regression already catches bad imp-stacks as "new parent loses to prior parent."

Self-play evolution remains the long-term mechanism for bot improvement; `/improve-bot-advised` overfits to Blizzard's stock SC2 AI and cannot compound. This plan narrows the evolve pipeline to the minimum viable set of gates so the mechanism can start producing real promotions on overnight runs.

## 2. Existing Context

Alpha4Gate is a Protoss SC2 bot with three control layers (rule-based strategy, PPO neural policy, Claude advisor) and a FastAPI + React+Vite dashboard. Production bot code is at [bots/v0/](bots/v0/); cross-version orchestration lives in [src/orchestrator/](src/orchestrator/). The master plan ([documentation/plans/alpha4gate-master-plan.md](documentation/plans/alpha4gate-master-plan.md)) has Phases 0-5 complete and Phase 9 (`/improve-bot-evolve`) Steps 1-9 complete with post-Step-8 enhancements shipped.

The relevant evolve primitives are:
- [src/orchestrator/evolve.py](src/orchestrator/evolve.py) — `FitnessResult`, `CompositionResult`, `RegressionResult` dataclasses and `run_fitness_eval`, `run_composition_eval`, `run_regression_eval` functions. This is where the gate semantics are defined.
- [scripts/evolve.py](scripts/evolve.py) — CLI orchestrator. `run_loop` at line 1155 drives generations; the per-phase call sites are around 1484 (fitness), 1606 (composition), 1889 (regression). The rollback block with the bug is at 1937-1950.
- [tests/test_evolve_cli.py](tests/test_evolve_cli.py) — orchestration tests with `_ScriptedFitness`, `_ScriptedComposition`, `_ScriptedRegression` fixtures. 29 tests currently.
- [tests/test_evolve.py](tests/test_evolve.py) — primitive tests.
- [frontend/src/hooks/useEvolveRun.ts](frontend/src/hooks/useEvolveRun.ts) — TypeScript schema mirror for `data/evolve_run_state.json`, `data/evolve_pool.json`, `data/evolve_results.jsonl`.

Memory (all in `~/.claude\projects\alpha4gate-project\memory\`) that shapes this plan:
- [feedback_evolve_revert_fails_dirty_tree.md](feedback_evolve_revert_fails_dirty_tree.md) — the bug this plan fixes, evidence from run `20260422-0824`.
- [feedback_evolve_fitness_5game_noise_floor.md](feedback_evolve_fitness_5game_noise_floor.md) — null-hit table confirming strict-majority is always 50% regardless of `n`; the design decision here is to reduce gate count, not tighten thresholds.
- [feedback_evolve_composition_stack_crash.md](feedback_evolve_composition_stack_crash.md) — mitigation kept (pre-composition import gate) but relocated: the new pipeline applies this check at stack-apply time, not at composition time.
- [feedback_useapi_cache_schema_break.md](feedback_useapi_cache_schema_break.md) — frontend `cacheKey` bump mandatory whenever the `data/evolve_*.json` schema changes.
- [project_evolve_plan.md](project_evolve_plan.md) and [project_evolve_redesigned.md](project_evolve_redesigned.md) — current evolve architecture context.

Constraints from `./CLAUDE.md`:
- Windows 11, Python 3.14 in `.venv`, uv-managed.
- SC2 install at `C:\Program Files (x86)\StarCraft II\`.
- Never kill `SC2_x64.exe`; only restart Python processes.
- `[advised-auto]` + `[evo-auto]` commits respect sandbox hook scopes.

## 3. Scope

**In scope:**
- Swap rollback order in `scripts/evolve.py` so `git revert` runs before the pointer reset.
- Add test coverage asserting rollback-revert completes cleanly, master has a revert commit, and `current.txt` ends at the prior parent.
- Remove the composition phase from the evolve pipeline. Delete `run_composition_eval`, `CompositionResult`, `_composition_outcome`, composition-specific pool statuses (`promoted-stack`, `promoted-single`, `is_fallback`), and all composition-specific test infrastructure.
- Replace the composition phase with a direct "apply all fitness-pass imps to a fresh snapshot → promote to `vN+1` → regression test" path (Option B).
- Update `frontend/src/hooks/useEvolveRun.ts` schema + bump `cacheKey` to match the simplified state vocabulary.
- Add a mandatory smoke-gate step: a 60-second end-to-end run that exercises the new pipeline with real components and confirms state files are written in the new schema.
- Run a 5-6h validation soak on the new pipeline and write a soak report; success = ≥1 net promotion.

**Out of scope (explicitly deferred to a follow-up plan):**
- PFSP-lineage regression gate (wiring `pfsp_sample` from [src/orchestrator/selfplay.py:181](src/orchestrator/selfplay.py#L181) into the regression phase to test `new_parent` against a sampled distribution of prior parents rather than immediate prior).
- Supermajority / higher-`n` threshold tweaks (the null-hit ceiling problem).
- Pool-quality improvements (Claude-side prompt engineering, prior-run feedback injection).
- Rewriting the composition-import-check as a separate validation gate; it migrates in-place into the stack-apply step.

**Prerequisites (operator, before invoking `/build-phase`):**
- `git revert e180dd9 3c672d6` in order (revert the newer gen-3 promote first, then gen-1) under `EVO_AUTO=1` so the sandbox hook permits the `bots/` edits. Commit message format: `evolve: post-hoc rollback revert for gen N` with `[evo-auto]` marker. Push master. Expected result: `bots/v1/` and `bots/v2/` directories removed, `bots/current/current.txt` returns to `v0`, working tree clean.

## 4. Impact Analysis

| Path | Nature of change | Notes |
|---|---|---|
| [scripts/evolve.py](scripts/evolve.py) | Modify + refactor | Fix rollback order at lines 1937-1950; delete composition phase block roughly at lines 1553-1790; delete `_composition_outcome` helper around line 441; delete `CompositionResult`-typed parameters in `run_loop` and helpers. The delta is large (likely -300 lines net). |
| [src/orchestrator/evolve.py](src/orchestrator/evolve.py) | Delete + refactor | Delete `CompositionResult` class (lines 165-~200), `run_composition_eval` function (lines 637-~840), and supporting helpers. Keep `FitnessResult`, `RegressionResult`, `run_fitness_eval`, `run_regression_eval`. `apply_improvement` stays — moves to being called directly from `scripts/evolve.py` in the new promote path. |
| [tests/test_evolve_cli.py](tests/test_evolve_cli.py) | Modify + delete | Delete composition-specific tests: `test_stack_fail_fallback_single_promotes`, `test_stack_crash_skipped_rotates_through_ranks_until_pass`, `test_stack_crash_skipped_all_ranks_crash_no_promotion`, `test_stack_lost_by_games_does_not_rotate_beyond_top1`, `test_stack_fail_fallback_picks_highest_fitness_not_lowest_rank`, `test_stack_fail_and_fallback_fail_no_promotion`, `test_git_commit_evo_auto_builds_fallback_body`. Delete `_ScriptedComposition` fixture. Add new tests: `test_regression_rollback_creates_revert_commit_cleanly`, `test_all_fitness_pass_imps_stacked_into_new_version`, `test_fitness_all_fail_no_promotion`. Expected test count delta: -5 to -8 tests. |
| [tests/test_evolve.py](tests/test_evolve.py) | Delete | Delete all `run_composition_eval` primitive tests. Keep fitness + regression primitive tests. |
| [frontend/src/hooks/useEvolveRun.ts](frontend/src/hooks/useEvolveRun.ts) | Modify | Remove `"promoted-stack"`, `"promoted-single"`, `"composition-pass"`, `"composition-fail"`, `"composition"` (phase), and `is_fallback` fields from union types. Add `"promoted"` status. Bump `cacheKey`. |
| [frontend/src/components/dashboard/EvolutionTab.tsx](frontend/src/components/dashboard/EvolutionTab.tsx) and related | Modify | Adjust any rendering that branches on `is_fallback`, `promoted-stack` vs `promoted-single`, or displays composition rows. |
| [.claude/skills/improve-bot-evolve/SKILL.md](.claude/skills/improve-bot-evolve/SKILL.md) | Modify | Rewrite Phase 2b (composition) section; simplify Phase 2 structure to just Fitness + Regression; update commit-message formats to drop fallback variant. Update § "What NOT to do" ("DO NOT lower --games-per-eval below 5" stays; delete composition-failure troubleshooting since the phase is gone). |
| `bots/current/current.txt` | No change | Should be `v0` after prerequisite cleanup; this plan does not touch it. |
| `documentation/plans/alpha4gate-master-plan.md` | Modify | Add a "Phase 9 — post-Step-10 enhancement: gate reduction" entry cross-linking this plan. |

## 5. New Components

No new modules are introduced. The plan is net-subtractive: one subsystem (composition phase) is removed and its unique-value behaviors (apply all fitness-pass imps + import-check) migrate into the surrounding call flow.

The one new helper in `scripts/evolve.py` is an inline "stack-apply-and-promote" function (roughly 30-50 lines) that replaces the composition-phase call site. It composes existing primitives:

```python
def _stack_apply_and_promote(
    parent: str,
    winning_imps: list[Improvement],
    dev_apply_fn: Callable[..., None],
    # ... args threading
) -> tuple[str, str] | None:
    """Apply all winning imps to a new snapshot and promote it to vN+1.

    Returns (new_version, promote_sha) on success, None on apply failure.
    Does NOT run any evaluation — the caller invokes run_regression_eval.
    """
```

The pre-composition import-gate check (currently at `run_composition_eval` entry) moves into this helper as a step-1 validation: `python -c "import bots.<new_version>.bot"` under a 30s timeout. Failure → rollback the snapshot and return None without promoting.

## 6. Design Decisions

**Decision 1: Option B (stack all fitness-pass imps), not Option A (top-fitness only).**

Considered: (A) promote only the single top-wins fitness-pass imp, skipping compounding entirely; (B) apply all fitness-pass imps to the new version directly, trust regression to catch bad interactions. Chose B. Option A is simpler but eliminates the compounding upside that makes multi-imp generations valuable. Option B keeps compounding and relies on regression to catch bad-stacks as "new parent loses to prior parent." The failure mode regression would newly be responsible for — bad-interaction stacks — is exactly what regression is designed to catch generically, and the ~9 games it costs to run regression is less than the ~9 games composition would have cost to catch the same failure earlier.

**Decision 2: Delete `run_composition_eval` and `CompositionResult`, don't just orphan them.**

Considered: keep the functions for future re-enablement, just stop calling them. Rejected. Dead code rots; the signal `composition_result` in event schemas would confuse future-maintainers reading the codebase. If composition is ever desired again, it reappears in a future plan as a new primitive with updated semantics (e.g., PFSP-weighted composition). Cleaner to remove and resurrect than to carry around.

**Decision 3: Smoke-gate BEFORE soak, as a separate step.**

Per plan-feature's data-pipeline rule (evolve.py writes `data/evolve_*.json` + `data/evolve_results.jsonl` consumed by the frontend, and by `/improve-bot-evolve`'s post-run hooks). The smoke gate — a short (~60s) end-to-end run with real `run_fitness_eval` and `run_regression_eval`, using a single generation with pool-size 2 games-per-eval 3 — catches producer/consumer drift in state files that unit tests with `_ScriptedFitness` fixtures cannot. Without this gate, the first time the schema change is exposed is in the 5h soak, where a crash means re-running.

**Decision 4: Frontend schema update ships in the same build step as the backend composition removal.**

Considered: a separate step for frontend. Rejected. Splitting introduces a window where `/api/evolve/state` returns the new schema and the frontend expects the old — dashboard crashes. Keeping them in one step ensures atomicity. The `cacheKey` bump rule in memory applies.

**Decision 5: The prerequisite master cleanup is outside `/build-phase`.**

The user explicitly scoped it as a prerequisite. `/build-phase` executes `/build-step` on each step, which uses worktree isolation — running `git revert` inside a build step would revert inside the worktree, not on master. Cleanup must happen on master before `/build-phase` starts. A human-readable note in §3 and a pre-flight check in Step 1 (verify master is clean and `bots/current/current.txt` = `v0`) enforce this.

## 7. Build Steps

### Step 1: Fix rollback-order bug in scripts/evolve.py
- **Status:** DONE (2026-04-23)
- **Problem:** In [scripts/evolve.py:1937-1950](scripts/evolve.py#L1937-L1950), the regression-rollback branch writes `bots/current/current.txt` to the prior parent (via `parent_current = prior_parent` and its side-effect on `snapshot_current`) before calling `revert_fn(promote_sha, ...)`. `git revert --no-commit` refuses to operate on a dirty working tree and exits 128. Swap the order: call `revert_fn` first (the revert commit itself writes `current.txt` back via its reverse diff), then update in-memory `parent_current`. Also add a pre-flight assertion: master is at the expected commit and `bots/current/current.txt` is `v0` before the run starts (new check in `run_loop` startup).
- **Issue:** (leave blank — created later by /repo-sync)
- **Flags:** `--reviewers code`
- **Produces:**
  - Modified [scripts/evolve.py](scripts/evolve.py) rollback block (~10 line swap).
  - New test in [tests/test_evolve_cli.py](tests/test_evolve_cli.py): `test_regression_rollback_creates_revert_commit_cleanly` — uses a `_ScriptedRevertFn` fixture that records calls and asserts pointer reset happens AFTER revert succeeds, not before.
  - New test: `test_run_loop_aborts_if_master_has_phantom_promote_at_startup` — asserts the new pre-flight assertion fires when startup sees a stale rolled-back-but-not-reverted state.
- **Done when:**
  - `uv run pytest tests/test_evolve_cli.py -q` passes with the 2 new tests green.
  - `uv run pytest` full suite passes (expect same count as today plus 2 new tests).
  - `uv run mypy scripts/evolve.py --strict` clean.
  - `uv run ruff check scripts/evolve.py tests/test_evolve_cli.py` clean.
- **Depends on:** none (prerequisite git revert must be done first by operator, but that's outside the build-phase scope).

### Step 2: Remove composition phase — 3 gates → 2 gates
- **Status:** DONE (2026-04-23)
- **Problem:** Remove the composition phase from the evolve pipeline. Delete `run_composition_eval` + `CompositionResult` + `_composition_outcome` + composition-specific pool-status values (`promoted-stack`, `promoted-single`, `is_fallback`) + `_ScriptedComposition` test fixture. Replace the composition phase in `run_loop` with a direct `_stack_apply_and_promote` helper that applies all fitness-pass imps to a fresh snapshot, runs the import-check that used to live at the composition-eval entry, and promotes on success. Regression phase is unchanged. Update `frontend/src/hooks/useEvolveRun.ts` to match the simplified schema (drop `composition*` union members, drop `is_fallback`, collapse `promoted-stack`/`promoted-single` to `promoted`) and bump its `cacheKey`. Update [.claude/skills/improve-bot-evolve/SKILL.md](.claude/skills/improve-bot-evolve/SKILL.md) sections describing Phase 2.
- **Issue:** (leave blank)
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:**
  - Modified [scripts/evolve.py](scripts/evolve.py) (~200-300 lines net removed).
  - Modified [src/orchestrator/evolve.py](src/orchestrator/evolve.py) (~200 lines removed — full `run_composition_eval` + `CompositionResult` deleted).
  - Modified [tests/test_evolve_cli.py](tests/test_evolve_cli.py) — composition tests removed, 2-3 new tests added (`test_all_fitness_pass_imps_stacked_into_new_version`, `test_fitness_all_fail_no_promotion_no_regression`, `test_stack_apply_import_check_failure_rolls_back_snapshot`).
  - Modified [tests/test_evolve.py](tests/test_evolve.py) — all `run_composition_eval` primitive tests removed.
  - Modified [frontend/src/hooks/useEvolveRun.ts](frontend/src/hooks/useEvolveRun.ts) with new schema + bumped `cacheKey`.
  - Modified [frontend/src/components/dashboard/EvolutionTab.tsx](frontend/src/components/dashboard/EvolutionTab.tsx) with composition-row rendering removed.
  - Modified [.claude/skills/improve-bot-evolve/SKILL.md](.claude/skills/improve-bot-evolve/SKILL.md) rewriting Phase 2.
  - Modified [documentation/plans/alpha4gate-master-plan.md](documentation/plans/alpha4gate-master-plan.md) with cross-link to this plan.
- **Done when:**
  - `uv run pytest` full suite passes. Expected count: current 1326 minus ~10 deleted composition tests plus ~3 new = ~1319.
  - `uv run mypy src bots --strict` clean (count unchanged on `src/`, may gain/lose modules).
  - `uv run ruff check .` clean.
  - `npm --prefix frontend run test` all vitest tests pass (129 today).
  - `npm --prefix frontend run build` succeeds (catches TypeScript type errors).
  - Grep confirms zero remaining references to `CompositionResult`, `run_composition_eval`, `_composition_outcome`, `is_fallback`, `promoted-stack`, `promoted-single`, `"composition"` phase name across `src/`, `scripts/`, `tests/`, `frontend/`.
- **Depends on:** Step 1.

### Step 3: Smoke gate — minimal 2-gate evolve run
- **Problem:** Run the modified pipeline end-to-end with real components for one generation to catch producer/consumer drift in state files, dashboard schema, or subprocess plumbing that unit tests with scripted fixtures miss. Configure for minimum-viable execution: `--pool-size 2 --games-per-eval 3 --hours 0 --game-time-limit 300 --hard-timeout 360 --no-commit`. `--hours 0` disables wall-clock; run exits via pool-exhaustion when all 2 imps either pass or fail fitness.
- **Type:** operator
- **Issue:** (leave blank)
- **Produces:**
  - A successful minimal evolve run with `--no-commit` so it doesn't mutate master.
  - Confirmation that `data/evolve_run_state.json`, `data/evolve_pool.json`, `data/evolve_results.jsonl` are written in the new schema.
  - Confirmation that `/api/evolve/state` endpoint returns valid JSON matching the new `useEvolveRun.ts` types.
  - Confirmation that the Evolution dashboard tab renders without errors.
- **Done when:**
  - `scripts/evolve.py` exits with rc=0 and `status: "completed"` in the state file.
  - `data/evolve_results.jsonl` contains only `phase: "fitness"` and optionally `phase: "regression"` rows (zero `phase: "composition"` rows).
  - Dashboard loads without JS console errors; Evolution tab shows the completed run.
  - SC2 processes cleaned up (no orphan `SC2_x64.exe` beyond what was there pre-run).
- **Depends on:** Step 2.

### Step 4: Validation soak — 5-6h evolve run
- **Problem:** Run a production-shaped evolve soak on the 2-gate pipeline to measure real-world promotion rate. Launch `/improve-bot-evolve --hours 6 --games-per-eval 9 --pool-size 4 --post-training-cycles 0 --game-time-limit 1800 --hard-timeout 2100` (matching the 20260422-0824 baseline except `--hours` halved and `--post-training-cycles 0` to avoid confounding the comparison). Monitor via scheduled wake-ups. At run end, write a morning report to `documentation/soak-test-runs/evolve-<RUN_TS>.md` comparing outcomes to the 20260422-0824 baseline (0 net promotions in 10h).
- **Type:** wait
- **Issue:** (leave blank)
- **Produces:**
  - A 5-6h soak under the new pipeline.
  - A `documentation/soak-test-runs/evolve-<RUN_TS>.md` report with: generations completed, promotions, rollbacks, fitness pass/fail distribution, and a comparison table vs the 20260422-0824 baseline.
  - A git tag `evolve/run/<RUN_TS>/final` on master.
- **Done when:**
  - Soak report is written and committed.
  - **Primary success criterion:** at least 1 net promotion (promoted AND survived regression) over the 5-6h window. This is the core validation hypothesis — that removing the composition gate raises the real-imp promotion rate.
  - **Secondary success criterion:** zero rollback-revert failures (the rollback-order fix from Step 1 holds up under real SC2 subprocess execution).
  - **Acceptable failure mode:** 0 promotions but zero crashes and clean state-file schema. This is a weaker outcome than hoped but not a build-step failure — it indicates the gate-reduction change was insufficient and the PFSP-lineage follow-up plan should move to the top of the queue. Document explicitly in the soak report.
- **Depends on:** Step 3.

## 8. Risks and Open Questions

| Risk | Severity | Mitigation |
|---|---|---|
| Step 2 scope is large (~500 lines net removed across 4 files + frontend). A single-worktree build-step agent may thrash or miss refs. | Medium | `--reviewers code` runs 4 parallel reviewers after the diff. Grep-assertion in Done-when forces zero references remaining. If the agent stalls, split into 2a (backend only) + 2b (frontend + SKILL.md) as a recovery move. |
| Frontend schema change lands in the same step as backend; if Step 2 partially lands (backend only), dashboard breaks. | Medium | Worktree isolation means partial landings are only visible if the worktree merges. Build-phase verifies each step's Done-when before merging. `npm --prefix frontend run build` in Done-when catches TypeScript drift. |
| Step 4 soak might hit 0 promotions anyway. | Medium-High | Per §7 Step 4, this is a documented acceptable failure mode, not a crash. The follow-up plan (PFSP-lineage) is already scoped. |
| Removed tests include patterns that other tests copy from; refactoring may cascade. | Low | Grep `_ScriptedComposition` across tests before deleting; any dependent test suites flagged in Step 2 Produces. |
| The import-gate check that currently lives at `run_composition_eval` entry migrates into the new `_stack_apply_and_promote` helper. If it's omitted during the refactor, a broken-stack promotion won't be detected until regression games run — wasting 9 games. | Low | Explicit line in Step 2 Produces; new test `test_stack_apply_import_check_failure_rolls_back_snapshot` enforces it. |
| The prerequisite `git revert` might leave bots/v1 and bots/v2 dirs undeleted if the promote commits didn't add them atomically. | Low | Verification step at end of prerequisite: `ls bots/v* | grep -v v0` returns empty. Manual `rm -rf bots/v{1,2}` if it doesn't. |
| **Open question:** Should the soak in Step 4 also launch `--post-training-cycles N` on promotion, matching the 20260422-0824 baseline exactly? | Low | Deliberately set to 0 in Step 4 spec to isolate the gate-reduction variable. Revisit after the first soak. |
| **Open question:** Is there a minimum `--games-per-eval` below which the 2-gate pipeline is too noisy to be useful? | Medium | Out of scope for this plan; the validation soak uses `--games-per-eval 9` matching prior runs. Address in the supermajority / n-tuning follow-up. |

## 9. Testing Strategy

**Unit (pytest):**
- Rollback-revert order assertion (new in Step 1).
- Startup pre-flight phantom-promote detection (new in Step 1).
- Stack-apply-all-fitness-pass-imps promotes to vN+1 (new in Step 2).
- Stack-apply import-check failure rolls back the snapshot cleanly (new in Step 2).
- Fitness-all-fail skips regression entirely and produces no promotion (new in Step 2).
- Existing fitness-primitive and regression-primitive tests in `tests/test_evolve.py` are preserved.
- Expected final test count: ~1319 (1326 current - ~10 composition tests + ~3 new).

**Integration / frontend:**
- `npm --prefix frontend run test` (vitest) — 129 current.
- `npm --prefix frontend run build` (TypeScript type-check) — must pass after schema simplification.

**Smoke (Step 3):**
- End-to-end `scripts/evolve.py --pool-size 2 --games-per-eval 3 --hours 0 --no-commit` produces a completed run with valid new-schema state files.
- Dashboard loads the result without JS console errors.

**Acceptance (Step 4):**
- 5-6h soak with production flags. Primary: ≥1 net promotion. Secondary: zero rollback-revert failures.
- Soak report comparing to 20260422-0824 baseline.

**What might break in existing tests:**
- Any test that imports `CompositionResult` or `run_composition_eval`.
- Any test asserting `is_fallback` or `"composition"` as a phase name.
- Any snapshot / golden-file test in the frontend that includes `composition` rows.

All must be surfaced in Step 2's grep-zero-remaining-references assertion. No backwards-compat shims — if a test references the deleted symbols, it's either updated or deleted.

**What this plan does NOT test:**
- Whether `pfsp_sample` from `src/orchestrator/selfplay.py` integrates cleanly with the regression phase (deferred to follow-up plan).
- Whether supermajority thresholds (e.g., `ceil(0.7 * games)`) produce better promotion rates (deferred).
- Whether pool size other than 4 changes the dynamics (deferred).

---

## Appendix: Prerequisite operator checklist (run before `/build-phase`)

```powershell
# 1. Verify current state
git log --oneline -3
# Expect: HEAD is 95c3f12 (fallback-fix) on top of e180dd9 + 3c672d6

cat bots/current/current.txt
# Expect: v0

git status --short
# Expect: empty OR just `M bots/current/current.txt` depending on prior session

# 2. Clean the dirty current.txt so revert can run
git checkout bots/current/current.txt

# 3. Revert the two phantom promote commits (newer first)
$env:EVO_AUTO = "1"
git revert --no-commit e180dd9
git commit -m "evolve: post-hoc rollback revert for gen 3 (run 20260422-0824)`n`n[evo-auto]"
git revert --no-commit 3c672d6
git commit -m "evolve: post-hoc rollback revert for gen 1 (run 20260422-0824)`n`n[evo-auto]"
Remove-Item Env:EVO_AUTO

# 4. Verify final state
cat bots/current/current.txt   # Expect: v0
ls bots/v*                     # Expect: only v0 (v1 and v2 removed by reverts)
git status --short             # Expect: empty

# 5. Push
git push origin master

# 6. Ready to invoke /build-phase --plan documentation/plans/evolve-gate-reduction-plan.md
```
