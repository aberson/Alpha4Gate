# Phase JDI: Repair Jev integration, then measure broader decisions

Status: APPROVED / BUILDING, 2026-10-14. Operator approved the proposal and **$5 cumulative maximum**, with allocation delegated across stages. Step 232 is in implementation and review; no build step is accepted yet. Paid dispatch still requires the implementation, safety and budget prerequisites below. Umbrella #354 and step issues #355–363 are synchronized.

**Active build authorization:** On 2026-10-14 the operator requested an overnight build, ending **09:00 America/Los_Angeles (16:00 UTC)**. Start checkpoint: 07:43 UTC. Continue ready work within this window, preserve phase reports, and do not treat a completed batch or blocked dependency as permission to abandon independent work. No additional spending approval is needed within the cumulative $5.

**Objective:** Repair the integration, then test broader choices against scripted decisions on the same improved graph. Success can mean more, the same, or less hosted-model involvement.

## 1. What This Feature Does

Make Jev's local decision graph a reliable executor of bounded strategic choices, then measure whether Typesafe's hosted Jev model makes those choices better than local rules. “Graph optimization” here means clear ownership, feasible options, timely questions and reliable fallbacks. Node-count reduction is not a success metric.

Proposal: [documentation/plans/jev-decision-integration-proposal.html](jev-decision-integration-proposal.html).

Progress: [JDI inventory and gate history](../jev-decision-integration-progress.html). This records JDI's nine units; earlier Jev progress pages remain historical or scoped to their own phases.

First milestone: existing Zealot/Stalker, recovery, expansion and tech capabilities. New units and build orders require a later proposal. This agent-originated default was accepted with the proposal on 2026-10-14. Jev names both the player and the hosted model; this plan calls them **graph** and **model** where that distinction matters. Observer Rush is the combined StarCraft II (SC2), graph and commentary viewer.

## 2. Existing Context

Read `CLAUDE.md` first. Planning anchor: branch `master-plan/phase-ev`, HEAD `e1a8e026ca01822cb53e47a44e255d3d4614ed66`; preceding `5eb0367` includes recovery/pacing changes. Re-read producers before building: anchors will move.

| Evidence | Implication for this plan |
|---|---|
| `src/jev/bot.py:770` observes, polls hosted questions, installs decisions, then ticks the runtime; `src/jev/strategy.py:639` constructs army context | Test through the controller/runner, including timing and issued commands |
| `src/jev/runtime.py:1374` cancels tasks on army-mode change; `src/jev/operations.py:1525` filters defense by the selected mode | A valid model label can still break execution; repair arbitration first |
| `24279aee #211147–211259`; `3c5f624b #52107–52222` | Accepted attack answers suppress local defense; the latter produces an army-root failure |
| `3c5f624b #53315` then `#53698` | Next army command follows after 4.06 game seconds; defense resumes after 5.58. This is not proof that SC2 cleared existing unit orders |
| `0eb8a3ce #40844/#41002`, plus 16 other first-answer cases in the investigation ledger | First request and local launch share a tick; launch invalidates the request signature |
| `src/jev/strategy.py:1273`, `:1504`; `3c5f624b #56525` | A held army posture also suppresses new macro plans. Independent strategic choices need independent slots |
| `24279aee #243110`; `src/jev/strategy.py:327` | Empty-army recovery was offered and accepted, but its wording includes rebuilding. Treat the filter as a requested contract change, not proven model-caused harm |
| All 189 accepted production answers in the evidence CSV choose Stalker-heavy | Compare with a constant local preference before claiming adaptive model value |
| Adversarial review: 38 unknown strategy comparisons are all recovery; only 2/72 recovery answers enter the comparable denominator | Historical label disagreement cannot establish marginal command influence or playing strength |

Sources: [Phase 1 report](../investigations/jev-model-involvement-2026-10-13.md), [adversarial review](../investigations/jev-model-involvement-2026-10-13-adversarial-review.md), [event ledger](../investigations/jev-model-involvement-2026-10-13-evidence.csv), [incident appendix](../investigations/jev-model-involvement-2026-10-13-incidents.md). These documents distinguish measured control failures from hypotheses; preserve that distinction. Old traces omit full observations and unchanged predicate facts, so they are not exact replay inputs (`src/jev/telemetry.py:734`).

Reuse [Phase JON](jev-overnight-improvement-plan.md), umbrella [#346](https://github.com/aberson/Alpha4Gate/issues/346), for durable budgets, frozen sources, sequential Observer Rush launches and resume. JON Steps 225–227 are dependencies of the experiment adapter, not new work to duplicate here. Step 225 is currently **BLOCKED** (`jev-overnight-improvement-plan.md:386`); no campaign implementation is assumed available. JDI offline Steps 232–235 can proceed independently. Serialize shared-file edits with JON. JON's live smoke Step 228 must pass before the JDI paid smoke. Any prerequisite smoke/probe undertaken for this investigation also consumes its approved $5 total; the approval does not authorize an unrelated JON improvement campaign.

Existing J2 Steps 222/223 remain open. JDI evidence can be linked into those gates only if their original criteria are separately satisfied; this plan does not close or replace them. The frozen v1 baseline `v1-b53039a693cadf75` must never be recaptured.

## 3. Scope

In: emergency arbitration, executable option contracts, deterministic production-entry scenarios, bounded observation history, earlier army requests, semantic staleness checks, independent macro decisions, explicit experiment profiles, matched comparisons, attribution and a recommendation.

Out: model-issued commands, model weights/training, new combat units, new build orders, tactical micro delegation, retreat/trade authority, graph flattening, autonomous graph evolution, automatic promotion, merges or desktop-default changes. Hosted calls, availability probes and matches for this investigation are authorized within the single cumulative $5 allowance after prerequisites pass. Never inspect `.env`, credential files or the DPAPI-protected key; retain the existing operator launcher as the key-loading boundary.

## 4. Impact Analysis

Paths below are existing unless marked new. Callsite searches covered `src/jev`, `scripts`, `tests` and `frontend/src`; repeat them at implementation time.

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `bots/jev/v2/policy.json` | modify | Defense precedence, feasible recovery/attack, prepared launch and independent macro branches | Army branch order and recovery/attack guards read, including lines 3194, 3563–3608, 3798 |
| `src/jev/operations.py`, `runtime.py` | modify | Keep safety/feasibility in graph operations and arbitration; expose side-effect-free eligibility using the same predicates | `_army_mode_is`, `_strategy_is`, runtime task cancellation and strategy reach inspected; consumers include policy validation, strategy, bot and army/v2/macro/plans tests |
| `src/jev/strategy.py`, `plans.py` | modify | Question profiles, extra `macro_priority` family, slot ownership, eligibility and bounded history inputs | `QuestionId`, `EXECUTION_QUESTIONS`, `StrategySlot`, `_apply`, `_slots`, `_schedule` inspected. Keep macro priority a strategic-slot question, not an execution-plan question |
| `src/jev/bot.py` | modify | Feed observation history and profile into the production coordinator; record choice/application outcomes | Controller production tick path and `tests/test_jev_v2.py:1266–1317` mocked-HTTP `run_match` seam read |
| `src/jev/runner.py`, `launch.py`, `benchmark.py` | extend | Optional profile/mix-control fields, validation, recording, child arguments and experiment dispatch | `MatchOptions`, `match_options_record`, benchmark `main`, `capture_source`, `score_run` inspected; preserve legacy serialized defaults |
| `scripts/benchmark_jev.py`, `scripts/launch-jev-battle.ps1`, `scripts/probes/jev_battle_view.py` | extend | Benchmark CLI delegates experiment execution to JON; viewer forwards settings unchanged | Benchmark script is a thin `main` wrapper; battle probe and launcher are production callers. `scripts/measure_jev_speed.py` and `scripts/probes/jev_recovery_comparison.py` are compatibility consumers |
| `src/jev/telemetry.py`, `bot.py`, `benchmark.py`, `api.py`, `scripts/validate_jev.py` | extend/check | Add versioned optional evidence inside event facts; preserve outer event schema and old-run reading | Diagnostics are produced by `bot.py::MatchMetrics` and consumed by `benchmark.py::_read_diagnostics`; validation script/API compatibility covered by tests below |
| `frontend/src/types/jev.ts`, `components/jevEvidence.ts`, `JevPlanPanel.tsx`, `JevTab.tsx`, `JevBattleView.tsx` | extend/check | Show question profile, independent macro source and safety override without misleading accepted-as-executed labels | Search found consumers and tests `jevEvidence.test.ts`, `JevTab.test.tsx`, `JevBattleView.test.tsx`, `JevGraph.test.tsx` |
| `tests/test_jev_{army,v2,plans,macro,decision,telemetry,benchmark,launch,api,validation,live_graph,pacing,economy,sc2}.py` | extend/regress | `MatchOptions`, strategy facts and graph behavior consumers | File-level callsite search; optional fields must not break existing callers or archived option fingerprints |
| JON `campaign.py`, `campaign_budget.py`, `campaign_evaluation.py` | future dependency/reuse | One budget ledger, lifecycle and source-verification owner | Planned in JON, not present at planning time; re-anchor their APIs after landing |
| `documentation/master_plan.md` | modify now | Register phase and reserve 232–240 | No existing numeric-step collision found |

Exact shared-contract consumer inventory from `rg -l`: `QuestionId`, `HostedQuestions`, `StrategySlot`, `scripted_strategy_facts` and `EXECUTION_QUESTIONS` occur in `src/jev/{strategy,plans,runtime,operations,runner,bot}.py` and `tests/test_jev_{v2,decision}.py`. `MatchOptions`/`match_options_record` additionally occur in `src/jev/{benchmark,launch}.py`, `scripts/probes/{jev_battle_view,jev_recovery_comparison}.py`, and `tests/test_jev_{army,api,benchmark,economy,launch,live_graph,pacing,macro,plans,sc2,telemetry,validation}.py`. Update construction, serialization, parsing, fingerprint comparison and subprocess forwarding together. Frontend consumers are enumerated in the table above.

Keep `Entity` and `Observation` unchanged in this milestone. Their existing counts, health, shields, positions, attack-target flags and sighting ages suffice for an explicitly limited strength proxy (`src/jev/contracts.py:827`, `:931`). No new HTTP routes, storage service or dependency is needed; reuse Python, httpx, pytest and the existing React/TypeScript display.

## 5. New Components

- `src/jev/decision_context.py`: bounded observation history and transparent strength/trend summaries.
- `src/jev/decision_experiment.py`: experiment manifest validation, arm construction and paired reports; delegates lifecycle/budget to JON and source/scoring primitives to benchmark.
- `scripts/check_jev_decision_scenarios.py`: offline production-entry scenarios and latency replay reports, using the same fixture driver as focused tests. It must never construct a real HTTP transport or SC2 launcher.
- `tests/test_jev_decision_context.py`, `tests/test_jev_decision_integration.py`, `tests/test_jev_decision_experiment.py` and `tests/fixtures/jev_decisions/`: new bounded fixtures/tests.
- `documentation/operator/jev-decision-experiment.md`: runbook authored with the adapter, including literal dry-run, approve, stop, resume and report commands.
- `data/jev/decision-experiments/`: generated manifests/reports referencing existing run and campaign evidence; no copied budget ledger or separate game scheduler.

### Data contracts

| New record | Fields and constraints |
|---|---|
| Decision opportunity, version 1 | `run_id`, monotonic `opportunity_id`, `question_id`, `profile`, game/wall timestamps, offered option IDs, local default/reason, relevant slot state, semantic signature, feature snapshot, request ID if sent; local default uses shared graph eligibility, never a trace-based guess |
| Decision application, version 1 | Opportunity/request IDs; answered choice/confidence; acceptance reason; effective choice/source; safety override reason; affected slot; branch/task/command event-seq references. Accepted, held, overridden and executed are separate states |
| Context summary, version 1 | Game time; own/visible-enemy combat count and health+shield totals split by ground/air capability; sighting ages; own losses over 5/30 seconds; count/durability/bank trends over 30 seconds; history coverage seconds; last-fight observed outcome/time/reason; enemy losses nullable |
| Scenario record, version 1 | Named initial graph/plan state, complete normalized Observation sequence, scheduled wall/game time, injected responses/delays, expected safety/command/slot invariants. Two independently initialized production controllers consume the same observations; this is a controlled scenario, not a predicted alternate game |
| Experiment manifest, version 1 | Experiment/campaign IDs; source/policy hashes; arm settings; ordered cases and seeds; pinned model/rates; SC2/map/viewer configuration; time/request limits; preregistered metrics/rules; approval receipts/counts/cap; hashes of offline gate reports |
| Experiment result, version 1 | All attempted case/arm/run IDs, outcome/validity/reason, usage/latency/exposure, paired scores and intervals, stop reason, remaining budget, recommendation with evidence links |

Identifiers: existing `run_id` is a runner-generated 32-character lowercase UUID hex; event `seq` is the existing per-run integer. New `opportunity_id` is a positive counter scoped to run; `question_id` is an enumerated family string. `experiment_id` is UUID hex generated once by the adapter; JON owns `campaign_id` and its format. `case_id` is `race-difficulty-seed` (e.g. `terran-3-303`), and arm ID is one of `S`, `F`, `H`, `I`. Source hashes are SHA-256 of captured content. Resume validates immutable hashes and retains identities.

Wire types: IDs/choices/reasons are strings, counters and event sequences nonnegative integers, game/wall timestamps numeric seconds, counts integers, durability/confidence numeric values, uncertainty explicitly JSON null. Option lists and event references are arrays. Store opportunities/applications in existing versioned event facts; in-memory history is per controller. Atomically write immutable experiment JSON via temporary-file replacement, validate on read, and reject conflicting concurrent ownership through JON's existing lease. Reports are derived/rebuildable; a corrupt manifest is never silently repaired into a new campaign. External response text is data and cannot change allowed options, graph code, budget or dispatch authority.

## 6. Design Decisions

### D1. Local defense and feasible choices are invariants

Defense preempts strategic intent whenever the existing local threat predicate finds an attackable home threat. No model choice may cancel a defending task merely to change posture. When a threat cannot be attacked by available units, record `no_capable_defender` and take an available local rally response; never mislabel this as defended. Retreat/trade is excluded from this milestone.

Offer `recover` only with at least one living eligible combat unit; local rebuilding continues with no army. Check the complete attack branch, including eligible group/actors and target, not just army count. If conditions change before application, discard the answer and execute the local feasible branch in the same tick. Also exercise unsolicited/invalid answer labels through the real response parser. No network wait blocks a controller tick.

The graph owns feasibility predicates and commands. Extract reusable, side-effect-free eligibility calculations from its operations; do not introduce a second rule set in the prompt coordinator. For the current profile, preserve question meanings/timing except these mandatory safety/feasibility repairs.

### D2. Measure behavior, not just answer labels

Add actual local-default and application evidence at each opportunity. Test the same complete scenario with scripted choices, every offered injected model choice and adversarial delays. Compare issued commands, plan changes and safety overrides, including removing army intent while retaining strategic recovery. Read-only eligibility has no task/plan side effects.

Old-run analysis remains descriptive. Latency resampling from recorded requests can estimate opportunities; it cannot supply missing observations, predict new answers or prove wins. Report unknown/censored cases explicitly. General runtime serialization and counterfactual whole-game simulation are out of scope.

### D3. Prepare army intent before launch; revalidate semantics

In the improved profile, begin the first army question at three ready Zealots. Its `attack` criterion means **prepare to launch once the local four-Zealot gate is met**. The graph gains an executable prepare-intent branch, so this is a valid present action with locally enforced future launch conditions. Never issue an early three-unit attack.

At four ready Zealots allow at most **2 game seconds** of nonblocking wait for the pending first answer, once per opening. Defense interrupts immediately; deadline expiry launches the scripted branch. No repeated reset or renewed wait after a failed/low-confidence answer. Both scripted controls use the same improved launch rules, but never incur an artificial model wait.

Signature fields: decision-contract version, slot generation, offered-option set, relevant threat class (none/attackable/unattackable), target identity where a target is bound, and semantic readiness class. Three-to-four readiness and a harmless reinforcement tag change do not invalidate a prepared launch. Losing all eligible actors, losing a bound target, changing a controlling plan, new home threat or a removed option does. Revalidate exact current eligibility at application even if the signature matches; preserve maximum answer age and confidence floor 0.5.

Report latency-resampled timely/accepted/effective rates separately, by family and denominator, with historical answers held fixed only for the explicitly labeled estimate. A broader new option set has no defensible historical confidence estimate. Do not promise an acceptance-rate increase before Step 234 measures it.

### D4. Richer but bounded context

Sample normalized observations at most once per game second into a 31-sample ring. Include history coverage and ages. Strength proxy is the pair **combat unit count plus total health+shield**, split by attack capability, for own and currently visible enemy forces. Label it a durability proxy, not DPS, combat odds or full enemy army strength. Remembered enemies are separate aged sightings.

Own Zealot/Stalker tags disappearing from complete own observations count as losses; no enemy disappears-as-dead inference. Enemy losses remain unknown without positive evidence. A fight begins with nearby armed enemies and observed damage/own combat loss; ends after five game seconds without that evidence. Outcome labels are `own_army_lost`, `enemy_left_vision`, `disengaged`, `unknown`, with supporting counts; `enemy_left_vision` is never described as a victory. Capture 5/30-second own losses and 30-second count, durability and resource trends. Reset between games. Limit the added summary to 16 KiB of UTF-8 JSON; aggregate counts over all observed units, and retain at most 16 individual remembered sightings ordered by recency then tag. If it still exceeds the bound, omit individual sightings and record the omission; never truncate JSON. Keep uncertainty in prompts.

### D5. Broader decisions through independent slots

Add opt-in `--decision-question-profile current|improved`, default `current`, plus `--scripted-production-mix local|stalker_heavy`, default `local` (reject the override with a hosted provider). They become optional `MatchOptions` fields, propagate through every launcher, and enter experiment fingerprints. Omit default-valued fields in legacy `match_options_record`; old records load as current/local. v1 rejects nondefault values and remains unchanged.

Current profile keeps the four existing question families on the repaired graph. Improved profile keeps `strategy` for `pressure|recover`, keeps `army_mode` for executable intent, and introduces `macro_priority` for feasible `tech|expand|hold`. `hold` explicitly delays **new** macro commitments for at most 10 game seconds; it does not stop an existing plan or military production. It uses the existing macro slot with no selected plan plus an expiry, not a new command. Do not ask when there is only one meaningful choice.

Improved strategic posture changes only the army slot; macro priority changes only the macro slot. A held recover/pressure answer must not block macro by implication. Expansion-response and production-mix remain supported. Existing local priorities are the scripted default for every broader choice; graph predicates select local eligible tech/expansion, otherwise hold. Macro hold expiry restores local fallback; it must not renew without a new accepted answer. Preserve the existing completed-recovery boundary.

Resolve remaining army overlap explicitly: emergency defense first, an active strategic recovery second, army-mode intent third, local fallback last. While recovery owns the army slot, attack is not eligible in the army question. Applying a new recovery invalidates conflicting held army intent within the same controller tick; completing/expiring recovery restores local eligibility. This priority is implemented in shared graph eligibility and tested, not merely described in model instructions.

All five families share one in-flight request and the same request cap. Army/expansion urgency wins, but nonurgent questions receive bounded fairness: after two consecutive nonemergency army requests, service an overdue eligible macro question next. Emergency defense is always local. Offline tests exercise starvation, timeouts, mixed holds and invalidation. Display each slot's source, question profile and safety override; do not describe the hosted answer as the effective action when overridden.

### D6. Same graph, four controls, bounded claim

All arms run identical repaired source/policy bytes, local safety and observation collection. Differences are explicitly recorded settings, never different graph snapshots:

| Arm | Choice source | What it controls |
|---|---|---|
| S | Scripted, local production preference | Value of the model on the repaired graph |
| F | Scripted, fixed Stalker-heavy preference when feasible; local otherwise | Value beyond a constant composition rule |
| H | Hosted, current question profile | Current interface on the repaired graph; not historical buggy behavior |
| I | Hosted, improved question profile | Earlier, richer, independent broader decisions |

I versus S/F tests model value; I versus H tests the combined question-design change. It does **not** isolate whether timing, context or added macro choice caused a difference. A causal claim about breadth alone needs a later ablation; do not claim it from this panel.

Use Simple64, built-in Terran/Protoss/Zerg at Medium (3) and MediumHard (4), seeds **303, 404, 505**: 18 matched cases per arm, 72 evaluation games, 36 hosted. These seeds exclude J2's 11/101/202; before sealing, reject prior evaluation exposure in local runs and deterministically choose the next unused integer starting at 606 if any proposed seed was used. Seal before outcomes. Cycle arm order by case to limit temporal bias. All games are native realtime (`realtime=true`, no `game_speed`), sequential in Observer Rush with fixed recording settings. Pin `jev-1.13.0`, limits 900 game / 1200 wall seconds, max 450 requests, and exact source/map/SC2 versions. Shared seeds reduce case differences; they do not make a stochastic hosted model deterministic.

Pre-registered claim: **on this fixed panel, improved hosted choices produce a material gain over both scripted controls without weakening emergency execution**. Win scores 1; loss, draw, game timeout and infrastructure failure score 0 in the intention-to-test table. Unstarted games remain missing, not losses; no full-panel verdict while any are missing. Also report completed-game results separately. No silent replacement of failures, no result-based retries, no prompt tuning on held-out cases. Report paired win differences and discordant win/loss counts; give a two-sided 95% Clopper–Pearson interval for I's win fraction among discordant pairs (undefined when there are none). These are descriptive, with no significance-based claim; 18 cases cannot establish broad superiority.

Decision rule: I must beat each of S and F by at least **2 wins/18 (11.1 percentage points)**; have no lower win count within any race across both difficulties; have no new observed defense suppression or preventable empty army-lane failure under a home threat; and have zero infrastructure failures for a positive strength conclusion. To conclude anything about broader macro authority, require at least 10 applied macro choices across at least three cases, including both tech and expansion opportunities; report unmet exposure as inconclusive. These are agent-default screening thresholds, not statistical significance. Otherwise recommend same, narrower family-specific use, or more measurement, according to evidence. Never auto-promote.

Run a predeclared first block of three Medium cases (one per race, seed 303): 12 games / 6 hosted. Continue the same sealed panel after the stage review; the 2026-10-14 authorization covers both stages without another budget approval. Early stop may follow safety, provenance, budget or missing-exposure defects, never an apparent winning score; stopped panels are inconclusive. If code/prompts change, freeze a new experiment and new held-out seeds rather than combine results.

### D7. Cost and approval gates

Reuse JON's reservation-before-HTTP ledger. Its budget guard must be implemented and reviewed before any call; JON's live substrate smoke must pass before JDI evaluation. That prerequisite smoke itself runs under the same guard and allowance. Resume/retries/probes share the ledger; a new process, stage or experiment never resets the approved cumulative allowance.

**Operator authorization, 2026-10-14:** approved `jev-decision-integration-proposal.html` and stated: “Approved \"**$5 cumulative maximum**\", you can allocate however you like within that. If you need more than you suggest in stage one, that's fine.” This supersedes the proposed $1 first-stage subcap and separate second-stage approval. Do not ask again for spending already covered by this authorization.

**Approved allowance: $5 total maximum**, including necessary prerequisite smokes, probes, retries and both evaluation stages. The working schedule remains three smoke games plus the first block (**15 games, 8 hosted**), then **60 games, 30 hosted**; baseline total **75 games, 38 hosted, 37 scripted**. Counts and stage allocation may be adjusted within $5, with changes recorded before dispatch. Extra development smokes/probes must be recorded separately from evaluation; no result-based retries, substitutions or post-outcome panel changes. Necessary JON prerequisite smoke calls are additional to these baseline game counts but included in the same $5. Keep phase stop/report boundaries and safety prerequisites; they are evidence gates, not renewed budget-permission requests. Further spending above $5 requires new approval.

Official [Typesafe pricing](https://docs.typesafe.ai/models), checked 2026-10-13: `jev-1.13.0` costs $0.042 per million input tokens, output free. Eight completed native-realtime hosted v2 runs in the audit used 2,533,484 input tokens total, mean 316,685.5 (~$0.0133/game at that price). Reproducible source: evidence JSON `runs[].metrics.input_tokens`, realtime true, version 2, provider typesafe, result win; run prefixes `0eb8a3ce`, `3c5f624b`, `61658d31`, `7f1aa251`, `8575bdd7`, `a434eef7`, `ab2a7eed`, `e0663781`. This selected sample is a planning estimate, not a price bound.

Budget estimate: roughly **$0.60–$1.20** for 38 hosted games, allowing richer requests; recompute after smoke usage. Conservative 65,536-token reservation is ~$0.002753/request; 38 × 450 full-size calls could cost **$47.07**, so completing every game is not guaranteed under $5. The ledger must stop first. Missing usage retains reservations; unknown price/model blocks dispatch. Record partially completed panels without a strength verdict.

Expected native-realtime wall time at the operator's 5–10 min/game estimate: first stage 75–150 min, full 75 games 6.25–12.5 hours, plus launch overhead. The per-game wall ceiling permits up to 25 hours total, plus overhead. Record the planned batch duration before dispatch, respect any operator-specified operating window, and stop cleanly at its end. This approval installs no recurring schedule. Resuming authorized work retains the same ledger and sealed panel.

## 7. Build Steps

Stop and report after each stage: safety (232), offline design/evidence (233–235), experiment preparation (236), live smoke (237), first block (238), remaining panel (239), conclusion (240). Steps 232–236 are code/offline work only. The overnight authorization permits continuing after stage reports when required gates pass; it does not waive failed reviews, local safety or budget enforcement.

### Step 232: Make local emergency execution independent of model intent
- **Problem:** Accepted attack/regroup can suppress defense; option eligibility omits executable-branch prerequisites.
- **Type:** code
- **Status:** PENDING
- **Files:** bots/jev/v2/policy.json; src/jev/{operations,runtime,strategy}.py; tests/test_jev_{army,v2,decision}.py
- **Issue:** #355
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** Shared graph eligibility, defense precedence and same-tick fallback; production-entry regression tests.
- **Done when:** Inject every offered answer via `run_match`/mock HTTP; reproduce the cited suppression cases structurally, then verify defense tasks survive intent changes, ground/air-capable defense issues or preserves orders, no recover is offered with zero combat units, and attack with no eligible group/target falls back in the same tick. Include threat onset while a response is pending, malformed/out-of-set answers and expired answers. Existing v1 policy/hash and v1 behavior tests remain unchanged. Report safety changes separately from playing strength.
- **Depends on:** none

<!-- autofix-applied: 2026-10-13 -->
### Step 233: Record opportunities and compare production behavior offline
- **Problem:** Selective trace predicates and overlapping held choices make historical label comparisons unreliable.
- **Type:** code
- **Status:** PENDING
- **Files:** src/jev/{bot,strategy,operations,telemetry,benchmark}.py; scripts/check_jev_decision_scenarios.py; tests/test_jev_decision_integration.py; tests/fixtures/jev_decisions/
- **Issue:** #356
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** Versioned opportunity/application evidence and bounded complete-observation scenario driver.
- **Done when:** Production entry tests distinguish accepted from effective/executed, compare all-local and one-answer-removed scenarios with other held slots intact, detect command/plan differences and retain unknowns. Include the recovery/macro-block and constant-composition cases. Driver proves no real network/SC2 launcher is reachable. Legacy run parsing remains compatible; report storage growth against existing trace bounds. No exact-replay claim for old traces.
- **Depends on:** 232

<!-- autofix-applied: 2026-10-13 -->
### Step 234: Ask timely army questions with bounded, richer context
- **Problem:** Launch invalidates the first answer and snapshots omit trends and fight history.
- **Type:** code
- **Status:** PENDING
- **Files:** src/jev/decision_context.py; src/jev/{strategy,bot,runner,launch,benchmark,operations,runtime}.py; bots/jev/v2/policy.json; scripts/{launch-jev-battle.ps1,probes/jev_battle_view.py,check_jev_decision_scenarios.py}; tests/test_jev_decision_context.py; tests/test_jev_decision_integration.py; affected MatchOptions tests
- **Issue:** #357
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** Profile plumbing, prepared-intent graph support in bots/jev/v2/policy.json and src/jev/{operations,runtime}.py, bounded context and semantic revalidation.
- **Done when:** Production scenarios at 0/0.5/1/2/5-second response delays prove pre-trigger application, exact 2-game-second wait limit, no early attack, immediate emergency preemption and no wait resets. Reinforcement/launch transitions preserve valid intent while loss/target/threat/slot changes invalidate it. History resets, stays bounded and never reports enemy vision loss as death/victory. Report measured offline timely/accepted/effective rates and latency-resampled estimates with coverage and unknowns. Default current profile and v1 regression checks pass.
- **Depends on:** 233

<!-- autofix-applied: 2026-10-13 -->
### Step 235: Expose independent macro choices and scripted equivalents
- **Problem:** Army strategy also owns the macro slot, restricting the model's meaningful choices.
- **Type:** code
- **Status:** PENDING
- **Files:** src/jev/{strategy,plans,operations,runtime,runner,bot,launch,benchmark}.py; bots/jev/v2/policy.json; scripts/launch-jev-battle.ps1; scripts/probes/jev_battle_view.py; frontend/src/types/jev.ts; frontend/src/components/{jevEvidence.ts,JevPlanPanel.tsx,JevTab.tsx,JevBattleView.tsx}; focused backend/frontend tests
- **Issue:** #358
- **Flags:** --reviewers deep --isolation worktree --ui --start-cmd "npm --prefix frontend run dev -- --host 127.0.0.1 --port 3105 --strictPort" --url "http://127.0.0.1:3105/?tab=jev"
- **Start-cmd:** npm --prefix frontend run dev -- --host 127.0.0.1 --port 3105 --strictPort
- **URL:** http://127.0.0.1:3105/?tab=jev
- **Produces:** Improved-profile macro_priority, independent holds/expiry, fixed-mix scripted control and accurate decision display.
- **Done when:** Production tests cover recover plus expansion, pressure plus tech, explicit hold expiry, infeasible option exclusion, pending/expired/failing responses, priority/fairness and completed recovery. Every new option reaches a graph-executable branch and has a local comparator. Test both profiles on identical policy bytes, profile propagation and archived option records. Frontend fixtures and a Playwright dashboard smoke show safety overrides and independent slot sources, with screenshots. Offline report states exposure and behavioral influence without a strength conclusion.
- **Depends on:** 234

### Step 236: Integrate the matched experiment into the existing benchmark/campaign tools
- **Problem:** Official J2 panels cannot encode this comparison, and a parallel harness would duplicate budget and resume behavior.
- **Type:** code
- **Status:** PENDING
- **Files:** src/jev/decision_experiment.py; src/jev/benchmark.py; JON campaign modules as integration requires; scripts/benchmark_jev.py; tests/test_jev_decision_experiment.py; tests/test_jev_benchmark.py; documentation/operator/jev-decision-experiment.md
- **Issue:** #359
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** Additive `benchmark_jev.py --decision-experiment` prepare/dry-run/run/resume/report mode, sealed manifests, paired reports and operator runbook.
- **Done when:** Network-disabled CLI smoke resolves the 75-game baseline and one cumulative authorization with adjustable stage allocations; source equality, profiles, seed ordering, realtime settings, declared counts, cumulative cost cap and no-default-value legacy drift are tested. Resume retains ledger/identities; corrupt evidence, wrong model/source/map/profile and exhausted budget stop dispatch. Fixture reports exercise wins, ties, missing games, infrastructure errors, safety failures, fixed-mix control and inadequate macro exposure. Official J2 panels and frozen v1 snapshot remain untouched. No paid call during this step.
- **Depends on:** 235; JON 225–227 implemented and reviewed

### Step 237: Approve and observe the real integration smoke
- **Problem:** Mocked transport cannot prove real SC2/provider/viewer propagation and budget enforcement.
- **Type:** operator
- **Status:** PENDING
- **Files:** generated smoke evidence and approval receipt under data/jev/decision-experiments/
- **Issue:** #360
- **Flags:** none; attended Observer Rush
- **Produces:** Archived 2026-10-14 authorization, chosen allocation and three smoke run records, or a blocked gate record.
- **Done when:** After JON 228 passes, bind the existing approval to the cumulative ledger and run one scripted and one per hosted profile on development seed 11. Verify pinned source/model/profile, native realtime, question/application evidence, functioning local fallback and Stop control. Inspect actual usage/ledger reconciliation before proceeding. If no meaningful decisions occur within 900 game seconds, the smoke is inconclusive; diagnose before allocating any additional smoke within the same allowance. These games are excluded from evaluation scores.
- **Depends on:** 236; JON 228 accepted; existing authorization bound to the ledger

### Step 238: Run and review the predeclared first matched block
- **Problem:** Live integration needs a small controlled screen before committing the remaining runtime.
- **Type:** wait
- **Status:** PENDING
- **Files:** generated first-block evidence/report
- **Issue:** #361
- **Flags:** none; approved sequential native-realtime campaign
- **Produces:** Three matched cases across all four arms, 12 games/6 hosted, within the shared cumulative $5 cap; no separate $1 subcap.
- **Done when:** Approved block completes or stops with explicit reason; report all attempts, paired outcomes, safety and macro exposure, latency, cost and operating-window use. Stop and report, then assess remaining resources for the planned 60 games/30 hosted under the existing authorization. A failed screen is evidence, not permission to tune and reuse held-out cases.
- **Depends on:** 237

### Step 239: Complete the sealed panel within the cumulative allowance
- **Problem:** Three cases cannot support the pre-registered 18-case comparison.
- **Type:** wait
- **Status:** PENDING
- **Files:** generated remaining-panel evidence/report and allocation record referencing the existing approval
- **Issue:** #362
- **Flags:** none; approved sequential native-realtime campaign
- **Produces:** Remaining 15 cases per arm (60 games/30 hosted), or a budget/window/safety-stopped partial panel.
- **Done when:** Stage review and a recorded allocation precede dispatch under the 2026-10-14 approval. Run the unchanged sealed matrix; no automatic replays of failed games, no pool of substitute seeds and no resetting the $5 ledger. Report partial evidence honestly if the operator stops work or the cap/window stops the panel. No second-stage budget approval is required within $5.
- **Depends on:** 238; safety/provenance gates and remaining cumulative allowance

### Step 240: Recommend the model's role from the paired evidence
- **Problem:** Acceptance/exposure improvements alone do not prove better play.
- **Type:** code
- **Status:** PENDING
- **Files:** documentation/investigations/jev-decision-experiment-results.md; documentation/plans/jev-decision-integration-plan.md; documentation/master_plan.md
- **Issue:** #363
- **Flags:** --reviewers code --isolation worktree
- **Produces:** Cited report, status updates and more/same/less recommendation; no automatic promotion.
- **Done when:** Offline report generation applies the sealed decision rule to every attempted game, distinguishes integration evidence from strength, shows S/F/H/I comparisons and confidence limitations, and cites run/event evidence. Partial or inadequate-exposure panels remain inconclusive. Family-specific benefit can justify selective delegation; a constant-rule win cannot be described as adaptive model value. J2 gates remain separately adjudicated.
- **Depends on:** 239 completed or explicitly concluded partial/declined after 238

## 8. Risks and Open Questions

| Risk / question | Resolution or gate |
|---|---|
| Model might handle more than these choices | This plan tests a bounded first milestone. Expand only after reliable execution/exposure; no capability ceiling inferred from old traces |
| Same graph does not isolate each improved component | I–H measures the bundle; a later ablation is required for a breadth-only claim |
| Defense predicate itself may miss threats | Preserve its scope in metrics and add attackable/unattackable scenarios; do not claim universal defense |
| Durability is a weak combat predictor | State its limitation and uncertainty; no hidden-information features or invented win probabilities |
| Shared scheduler may starve macro questions | Explicit fairness tests and live exposure gate; no conclusion about untested choices |
| $5 may not finish the panel | Durable reservations stop it; incompleteness is an allowed outcome, not permission to spend more |
| JON Step 225 blocked / review host unavailable | Offline JDI work may proceed; paid adapter/live gates wait for real prerequisites. Never relabel an unavailable independent review as passed |
| Small/stochastic sample or provider drift | Pin returned model, interleave arms, record provenance, use paired reporting and limit claims to the fixed panel |
| Shared-file work conflicts | Build JON and JDI sequentially at their common producer boundaries; re-read signatures/consumers after each landing |

No unresolved implementation alternatives remain. D1–D6 were accepted with the proposal on 2026-10-14. D7's stage subcap and renewed-approval requirement were superseded by the operator's flexible $5 cumulative authorization (P5); implementation and safety gates remain pending.

## 9. Testing Strategy and Quickstart

Planning changed documentation only; the commands below are **future build checks**, not tests claimed to have passed now.

1. Read `CLAUDE.md`, this plan, the investigation/adversarial review and JON dependency state. Preserve existing local changes. Prerequisites: Windows, Python >=3.12, uv, Node/npm matching the existing frontend lockfile; SC2 and Simple64 installed for live gates only. Install with `uv sync`; frontend dependencies with `npm ci` in `frontend`. Offline tests use fake transport credentials only, never the real key. For display development, start `uv run python -m bots.current.runner --serve` (backend :8765), then `npm run dev` in `frontend` (:3000); these reuse existing ports and do not authorize launching a game. Python runs from the installed project without a separate compile step; frontend build is below.
2. Validate both policies: `uv run python -m bots.jev.v1 --validate-policy` and `uv run python -m bots.jev.v2 --validate-policy`. Run scoped tests for each step, then the existing Jev regression suite with `uv run pytest tests -k jev` (real-SC2 markers remain deselected by default). Run `uv run ruff check src/jev scripts tests` and `uv run mypy src/jev --strict`; distinguish existing failures from new ones.
3. After Step 233: `uv run python scripts/check_jev_decision_scenarios.py --report data/jev/decision-experiments/offline.json`. This command is planned, not present today. Include production runner, actual policy, parser/coordinator, task scheduler and adapter command records. Fixtures are synthetic complete scenarios; old trace windows only ground the scenarios and timing distributions.
4. After Step 235, in `frontend`: `npm run test -- --run`, `npm run build`, `npx tsc --noEmit`, `npm run lint`. Exercise archived events and optional new fields. No UI visual acceptance is hidden inside a code step.
5. Step 236 implements `--decision-experiment prepare|run|resume|report`, with required `--experiment-dir` and, for dispatch, `--stage smoke|screen|panel`. The new mode is mutually exclusive with official `--panel`/legacy resume flags. Planned offline example: `uv run python scripts/benchmark_jev.py --decision-experiment prepare --experiment-dir data/jev/decision-experiments/prepared --dry-run`. Dry-run prints the resolved manifest without writes or network; prepare without dry-run writes it once. Run/resume use the sealed manifest, stage approval and JON ledger; report only reads evidence. The code step authors the full literal operator commands, including the landed JON approval/allowance interface, in the runbook. These paths do not exist yet. Existing verification control: `uv run python scripts/benchmark_jev.py --panel baseline --dry-run` must still be read-only and must not recapture v1.
6. Real smoke precedes the longer native-realtime comparison. Live Steps 237–239 use the recorded 2026-10-14 authorization after prerequisites pass; stage reallocations within $5 need no renewed permission. Offline acceptance-rate estimates and deterministic command safety are not substitutes for matched play.

Implementation workflow: scoped worktree per code step, producer/consumer tests, declared review lane, then report at the stage boundary. Issue synchronization follows review/redline/wrap. The implementation request was received on 2026-10-14; first useful slice is Step 232. Installed skill notation names a workflow, not a shell executable.

## Appendix

### Decision Inventory

| ID | P/D | Choice | Status |
|---|---|---|---|
| P1 | P | Repair integration, then test broader choices against scripted on the same improved graph | Selected by operator |
| P2 | P | Local command legality; no key/credential access; frozen v1 immutable | Selected by operator |
| P3 | P | Explicit paid authorization; native realtime; build on JON/#346 | Selected by operator; allocation clarified by P5 |
| P4 | P | More, same or less involvement are acceptable; decide from measurements | Selected by operator |
| D1 | D | Local defense wins; zero-army recovery excluded; same-tick feasible fallback | Accepted by operator 2026-10-14; contract details in §6 D1 |
| D2 | D | Existing capabilities first; bounded production scenarios and explicit unknowns | Accepted by operator 2026-10-14 |
| D3 | D | Three-Zealot prepare, maximum two-game-second first-launch wait, semantic signatures | Accepted by operator 2026-10-14 |
| D4 | D | 30-second history with count/durability proxy and conservative fight labels | Accepted by operator 2026-10-14 |
| D5 | D | Independent macro priority with ten-second hold; current/improved profiles | Accepted by operator 2026-10-14 |
| D6 | D | Four arms, 18 matched cases, fixed-mix control, two-win/no-race-regression rule | Accepted by operator 2026-10-14 |
| D7 | D | Working schedule: 15 then 60 games, 38 hosted baseline; stage allocation adjustable | Changed 2026-10-14: $1 subcap and second approval superseded by P5 |
| P5 | P | Approve the proposal and $5 cumulative maximum; agent may allocate across stages without another approval | Explicit operator authorization 2026-10-14; includes more stage-one spending if useful |
| P6 | P | Build through the night until 09:00 Pacific on 2026-10-14 | Explicit operator authorization; preserve required quality/safety gates |

Review and handoff evidence: [planning checks](jev-decision-integration-planning-checks.md). Proposal is a view; this Markdown document remains authoritative.
