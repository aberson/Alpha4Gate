# Alpha4Gate master plan

Status: outstanding work consolidated 2026-10-14. This is the single project execution inventory. Completed code is not operator acceptance. No gameplay, paid dispatch, publication, issue closure or implementation occurred during this migration.

## 1. Purpose and authority

Build an inspectable SC2 platform: frozen full-stack RL versions and a separate Jev graph player. Preserve empirical promotion/acceptance gates, honest measurement and local command legality. Workspace Toolkit scheduling/status remains authoritative where mapped: [shared program](../../../plan.md). This project plan owns requirements, not a replacement shared schedule. Source statements and original issue identities remain auditable in [source map](../plan-reconciliation/migration.json).

## 2. Current context

Read [CLAUDE.md](../../CLAUDE.md) and project rules before implementation. Current baseline at migration is c0836c3; de-facto branch master-plan/phase-ev. JDI0/9 accepted; 232 held after three reviews, candidate00bc515 unmerged. JON225 also held; no accepted campaign implementation. JDI $5 cumulative approval includes necessary JON prerequisite probes/smokes, no new process/stage resets it. The old overnight work window is history, not a recurring schedule. Spending remains gated on durable budget, safety and real smoke. Frozen v1 v1-b53039a693cadf75 must never be recaptured. JV207/208, J2 213/222/223 and Evolve gates are independently open. JI209–211 actual hosted acceptance is complete. The standalone Observer Rush preview/showcase is complete without closing JV/J2.

## 3. Scope and exclusions

Only outstanding leaf work appears below. Shipped phases A/0–5/8/9/B/N, D code/counts, EL1–6, EJ1–6, EV1–3, EH1–2, JV201–206, JI209–211, J2 code212/224/214–221 are excluded from estimates. Phase8 ceremonial validation was explicitly closed and cloud step12 cut; stale historical TODOs do not reopen it. PhaseF transformer, checkpoint-only opponent pool, domain abstraction and learned Hydra remain cut. No autonomous merges, recurring jobs or broader model gameplay authority is added here.

## 4. Architecture and dependencies

Python>=3.12, uv, burnysc2, FastAPI, React/TypeScript/Vite. RL versions are full frozen physical stacks; current is a pointer. src/orchestrator must not import bots.current/version modules. Jev owns its graph/runtime, no RL-stack dependency. Backend8765/frontend3000; verify actual health and IPv6 localhost behavior. Windows local CPU PPO; WSL Ubuntu-22.04 with SC2PATH and SC2_WSL_DETECT=0 for Linux work. Follow existing installed dependency locks, not historical test counts.

Serialize shared JON/JDI producers. 236 needs225–227;237 needs228. JDI232 is held independently. EH before EI shared-file edits; re-derive anchors after each landing. EH6 dependsEH8; do not enable long auto-committing launch while index scoping remains unfixed. Deep review remains required for trust/persistence boundaries; historical toolkit freeze has lifted, not a reason to downgrade. All Evolve/PhaseD/Phase6/7 live gates serialize machine-wide around one pointer and SC2 resources.

## 5. Operational corrections and contracts

Archived plans preserve original bytes and may contain superseded instructions. These corrections govern this master. Browser fixture repair is already complete at 03c552a: all nine opt-in browser tests passed (64 deselected), recorded in source-reviews/jev-v2-validation.md. It is excluded from outstanding forecasts. The interrupted Step222 run e06637818c1b4341a213e99082b91ffd is not a win despite raw Victory: the operator surrendered; no second Nexus or pending-request cleanup was proven. Remaining two development matches are held pending recovery/attack investigation, independently of deferred213. Register frozen baselines before gauntlet/floor gates; verify list output. --lineages N never creates N records: seed through scripts/lineage.py; EH2 persists only disk-loaded registries. Four seeded records/cap3 makes culling possible, not guaranteed: dominance plus fingerprint distance<0.15 still required. A single-lineage fallback cannot satisfy multi-lineage acceptance. Record not-triggered-by-design separately; >=2 generations per actual lineage and nontrivial diversity remain required. Remove seeded lineages after gates unless explicitly retained; baselines persist. EH has no extra six-hour acceptance step: EH10 is terminal by operator decision.

Use --generations0 for soaks; argparse default1 remains quick-test behavior. EV5 uses fixed production launcher only after EH6. Until EH4/EH5 land, dashboard Stop is unwired; viewer close only detaches. Never Ctrl+C the threaded evolve viewer or kill SC2 manually; use documented console closure/owned graceful mechanisms. Jev has its own graceful Stop battle/control contract; do not confuse the two. No live tests during docs migration. EVO_AUTO currently sweeps staged work; never run it over this migration.

PhaseD M1: take baseline before flag, three PPO cycles, >=30% early reward standard-deviation reduction and nonregressing difficulty3 WR, then M2 >=+10 Elo/20 games. Bare snapshot_bot.py resolves current version; --from current and hardcoded pointer v14 are invalid. Use actual emitted versions. Restore reward backup and leave flag off on failure. Phase7 is a skill invocation /improve-bot-advised, not a uv console script. PhaseE10-versus12 actions is unresolved E0; no implicit choice.

EI log identity is match_id, not PFSP game_index; one producer/consumer helper, newest-N byte cap and retention at every generation including serial/viewer/resume, never the concurrency-only stale sweeper. Trace fields: match_id/version/role/terminal marker/n_steps(nullable)/duration_s(nullable)/status_chunk_count/parse_errors; mechanical evidence only. Six flags preserve defaults, fitness flags cross worker argv. SelfPlayRecord adds two optional fields to nine existing keys (eleven total, correcting source arithmetic); all readers tolerate unknown fields from this release onward, not genuine pre-EI checkouts. PerItemState serializers and load_pool_state pop tuple change together; score pairs remain pairs. EI8 preserves existing clean-sweep tests and adds near-miss characterization; its late contradictory sentence is superseded by the detailed Done-when. EI14 co-tenants EL7 with trace-capture/capture-patch and optional static observe only, not enforce/stack-size/rule-stop; it claims no throughput or EL outcome, zero added games.

## 6. Design decisions

Operator-picked historical approvals retain their scope: EH pause wiring cut and checkbox disabled, resume clears pending stop with INFO, EH7/EH8 retained, no EH unattended-observation obligation, EH9 depends only shippedEH1/EH2, EV sibling correction #302. JDI integration/experiment choices approved with flexible single$5 cap; no stage subcap or renewed approval within that cap. Agent-defaulted cleanup: one master, source-preserving archive, unchanged IDs except explicit aliases, independent gates, conservative unresolved holds and active-hour estimates. EI product defaults remain unratified; J2 residual scope, E0 and branch landing remain decisions. Original append-only inventories are preserved in exact archives; this reconciliation appends to them rather than rewriting history.

## 7. Outstanding build and acceptance steps

Status PENDING can still have dependencies. HELD/DEFERRED are not dispatch-ready. Historical source bodies below retain exact requirements; canonical corrections above take precedence. Completed dependencies are listed in the provenance report rather than reintroduced as forecast work.

### Step 232: Make local emergency execution independent of model intent

- **Current increment (operator reopened repairs, 2026-10-15):** Independently review and demonstrate the shared recovery-order lifecycle repair on the shipped baseline. Its acceptance is limited to order-aware MOVE satisfaction/acknowledgement/completion, bounded reconciliation, no early recovery release, compatible-order preservation and production-entry regressions. This increment may land without declaring Step 232 complete; the full Done-when below remains required for whole-step acceptance. Defense precedence, branch-option eligibility and shared graph selection remain outstanding. Existing three-review history and rejected candidate stay preserved.

- **Recovery lifecycle decision (2026-10-15, current increment):** Once recovery is executing, retain it until observed completion or an explicit valid superseding choice/cancellation. The generic 30-second posture expiry must not release unfinished recovery. Preserve graph-owned emergency priority, bounded task retries/cooldowns, and ordinary posture expiry. Active recovery tasks block strategic settlement even when idle or moving compatibly but not yet complete. Exposed deadlines must match this behavior.
- **Status:** HELD after expanded repair rounds4–6; latest candidate unaccepted, whole-step requirements remain open
- **Latest checkpoint (2026-10-15):** Latest increment `f027bc2` on fix/jev-order-repair passed3416 Python tests/mypy/Ruff but review remains NEEDS-WORK: recovery-to-macro ownership handoff remains unresolved. Three controlled real-SC2 candidate captures show immediate order correction; ordinary HarderTerran loss does not prove stronger play. No root application, no paid calls. Stop/audit exit transitions before another patch. See [repair evidence](../investigations/jev-order-repair-2026-10-15.md). Original00bc515 untouched.
- **Type:** code
- **Issue:** #355
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [jev-decision-integration-plan.md](../archived-plans/jev-decision-integration-plan.md); original 232.

- **Problem:** Accepted attack/regroup can suppress defense; option eligibility omits executable-branch prerequisites.
- **Files:** bots/jev/v2/policy.json; src/jev/{operations,runtime,strategy}.py; tests/test_jev_{army,v2,decision}.py
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** Shared graph eligibility, defense precedence and same-tick fallback; production-entry regression tests.
- **Done when:** Inject every offered answer via `run_match`/mock HTTP; reproduce the cited suppression cases structurally, then verify defense tasks survive intent changes, ground/air-capable defense issues or preserves orders, no recover is offered with zero combat units, and attack with no eligible group/target falls back in the same tick. Include threat onset while a response is pending, malformed/out-of-set answers and expired answers. Existing v1 policy/hash and v1 behavior tests remain unchanged. Report safety changes separately from playing strength.

<!-- autofix-applied: 2026-10-13 -->


### Step 233: Record opportunities and compare production behavior offline

- **Status:** PENDING
- **Type:** code
- **Issue:** #356
- **Depends on:** 232
- **Source:** [jev-decision-integration-plan.md](../archived-plans/jev-decision-integration-plan.md); original 233.

- **Problem:** Selective trace predicates and overlapping held choices make historical label comparisons unreliable.
- **Files:** src/jev/{bot,strategy,operations,telemetry,benchmark}.py; scripts/check_jev_decision_scenarios.py; tests/test_jev_decision_integration.py; tests/fixtures/jev_decisions/
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** Versioned opportunity/application evidence and bounded complete-observation scenario driver.
- **Done when:** Production entry tests distinguish accepted from effective/executed, compare all-local and one-answer-removed scenarios with other held slots intact, detect command/plan differences and retain unknowns. Include the recovery/macro-block and constant-composition cases. Driver proves no real network/SC2 launcher is reachable. Legacy run parsing remains compatible; report storage growth against existing trace bounds. No exact-replay claim for old traces.

<!-- autofix-applied: 2026-10-13 -->


### Step 234: Ask timely army questions with bounded, richer context

- **Status:** PENDING
- **Type:** code
- **Issue:** #357
- **Depends on:** 233
- **Source:** [jev-decision-integration-plan.md](../archived-plans/jev-decision-integration-plan.md); original 234.

- **Problem:** Launch invalidates the first answer and snapshots omit trends and fight history.
- **Files:** src/jev/decision_context.py; src/jev/{strategy,bot,runner,launch,benchmark,operations,runtime}.py; bots/jev/v2/policy.json; scripts/{launch-jev-battle.ps1,probes/jev_battle_view.py,check_jev_decision_scenarios.py}; tests/test_jev_decision_context.py; tests/test_jev_decision_integration.py; affected MatchOptions tests
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** Profile plumbing, prepared-intent graph support in bots/jev/v2/policy.json and src/jev/{operations,runtime}.py, bounded context and semantic revalidation.
- **Done when:** Production scenarios at 0/0.5/1/2/5-second response delays prove pre-trigger application, exact 2-game-second wait limit, no early attack, immediate emergency preemption and no wait resets. Reinforcement/launch transitions preserve valid intent while loss/target/threat/slot changes invalidate it. History resets, stays bounded and never reports enemy vision loss as death/victory. Report measured offline timely/accepted/effective rates and latency-resampled estimates with coverage and unknowns. Default current profile and v1 regression checks pass.

<!-- autofix-applied: 2026-10-13 -->


### Step 235: Expose independent macro choices and scripted equivalents

- **Status:** PENDING
- **Type:** code
- **Issue:** #358
- **Depends on:** 234
- **Source:** [jev-decision-integration-plan.md](../archived-plans/jev-decision-integration-plan.md); original 235.

- **Problem:** Army strategy also owns the macro slot, restricting the model's meaningful choices.
- **Files:** src/jev/{strategy,plans,operations,runtime,runner,bot,launch,benchmark}.py; bots/jev/v2/policy.json; scripts/launch-jev-battle.ps1; scripts/probes/jev_battle_view.py; frontend/src/types/jev.ts; frontend/src/components/{jevEvidence.ts,JevPlanPanel.tsx,JevTab.tsx,JevBattleView.tsx}; focused backend/frontend tests
- **Flags:** --reviewers deep --isolation worktree --ui --start-cmd "npm --prefix frontend run dev -- --host 127.0.0.1 --port 3105 --strictPort" --url "http://127.0.0.1:3105/?tab=jev"
- **Start-cmd:** npm --prefix frontend run dev -- --host 127.0.0.1 --port 3105 --strictPort
- **URL:** http://127.0.0.1:3105/?tab=jev
- **Produces:** Improved-profile macro_priority, independent holds/expiry, fixed-mix scripted control and accurate decision display.
- **Done when:** Production tests cover recover plus expansion, pressure plus tech, explicit hold expiry, infeasible option exclusion, pending/expired/failing responses, priority/fairness and completed recovery. Every new option reaches a graph-executable branch and has a local comparator. Test both profiles on identical policy bytes, profile propagation and archived option records. Frontend fixtures and a Playwright dashboard smoke show safety overrides and independent slot sources, with screenshots. Offline report states exposure and behavioral influence without a strength conclusion.



### Step 236: Integrate the matched experiment into the existing benchmark/campaign tools

- **Status:** PENDING
- **Type:** code
- **Issue:** #359
- **Depends on:** 235, 225, 226, 227
- **Source:** [jev-decision-integration-plan.md](../archived-plans/jev-decision-integration-plan.md); original 236.

- **Problem:** Official J2 panels cannot encode this comparison, and a parallel harness would duplicate budget and resume behavior.
- **Files:** src/jev/decision_experiment.py; src/jev/benchmark.py; JON campaign modules as integration requires; scripts/benchmark_jev.py; tests/test_jev_decision_experiment.py; tests/test_jev_benchmark.py; docs/operator/jev-decision-experiment.md
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** Additive `benchmark_jev.py --decision-experiment` prepare/dry-run/run/resume/report mode, sealed manifests, paired reports and operator runbook.
- **Done when:** Network-disabled CLI smoke resolves the 75-game baseline and one cumulative authorization with adjustable stage allocations; source equality, profiles, seed ordering, realtime settings, declared counts, cumulative cost cap and no-default-value legacy drift are tested. Resume retains ledger/identities; corrupt evidence, wrong model/source/map/profile and exhausted budget stop dispatch. Fixture reports exercise wins, ties, missing games, infrastructure errors, safety failures, fixed-mix control and inadequate macro exposure. Official J2 panels and frozen v1 snapshot remain untouched. No paid call during this step.



### Step 237: Approve and observe the real integration smoke

- **Status:** PENDING
- **Type:** operator
- **Issue:** #360
- **Depends on:** 236, 228
- **Source:** [jev-decision-integration-plan.md](../archived-plans/jev-decision-integration-plan.md); original 237.

- **Problem:** Mocked transport cannot prove real SC2/provider/viewer propagation and budget enforcement.
- **Files:** generated smoke evidence and approval receipt under data/jev/decision-experiments/
- **Flags:** none; attended Observer Rush
- **Produces:** Archived 2026-10-14 authorization, chosen allocation and three smoke run records, or a blocked gate record.
- **Done when:** After JON 228 passes, bind the existing approval to the cumulative ledger and run one scripted and one per hosted profile on development seed 11. Verify pinned source/model/profile, native realtime, question/application evidence, functioning local fallback and Stop control. Inspect actual usage/ledger reconciliation before proceeding. If no meaningful decisions occur within 900 game seconds, the smoke is inconclusive; diagnose before allocating any additional smoke within the same allowance. These games are excluded from evaluation scores.



### Step 238: Run and review the predeclared first matched block

- **Status:** PENDING
- **Type:** wait
- **Issue:** #361
- **Depends on:** 237
- **Source:** [jev-decision-integration-plan.md](../archived-plans/jev-decision-integration-plan.md); original 238.

- **Problem:** Live integration needs a small controlled screen before committing the remaining runtime.
- **Files:** generated first-block evidence/report
- **Flags:** none; approved sequential native-realtime campaign
- **Produces:** Three matched cases across all four arms, 12 games/6 hosted, within the shared cumulative $5 cap; no separate $1 subcap.
- **Done when:** Approved block completes or stops with explicit reason; report all attempts, paired outcomes, safety and macro exposure, latency, cost and operating-window use. Stop and report, then assess remaining resources for the planned 60 games/30 hosted under the existing authorization. A failed screen is evidence, not permission to tune and reuse held-out cases.



### Step 239: Complete the sealed panel within the cumulative allowance

- **Status:** PENDING
- **Type:** wait
- **Issue:** #362
- **Depends on:** 238
- **Source:** [jev-decision-integration-plan.md](../archived-plans/jev-decision-integration-plan.md); original 239.

- **Problem:** Three cases cannot support the pre-registered 18-case comparison.
- **Files:** generated remaining-panel evidence/report and allocation record referencing the existing approval
- **Flags:** none; approved sequential native-realtime campaign
- **Produces:** Remaining 15 cases per arm (60 games/30 hosted), or a budget/window/safety-stopped partial panel.
- **Done when:** Stage review and a recorded allocation precede dispatch under the 2026-10-14 approval. Run the unchanged sealed matrix; no automatic replays of failed games, no pool of substitute seeds and no resetting the $5 ledger. Report partial evidence honestly if the operator stops work or the cap/window stops the panel. No second-stage budget approval is required within $5.



### Step 240: Recommend the model's role from the paired evidence

- **Status:** PENDING
- **Type:** code
- **Issue:** #363
- **Depends on:** 239
- **Source:** [jev-decision-integration-plan.md](../archived-plans/jev-decision-integration-plan.md); original 240.

- **Problem:** Acceptance/exposure improvements alone do not prove better play.
- **Files:** docs/investigations/jev-decision-experiment-results.md; docs/archived-plans/jev-decision-integration-plan.md; docs/archived-plans/master_plan.md
- **Flags:** --reviewers code --isolation worktree
- **Produces:** Cited report, status updates and more/same/less recommendation; no automatic promotion.
- **Done when:** Offline report generation applies the sealed decision rule to every attempted game, distinguishes integration evidence from strength, shows S/F/H/I comparisons and confidence limitations, and cites run/event evidence. Partial or inadequate-exposure panels remain inconclusive. Family-specific benefit can justify selective delegation; a constant-rule win cannot be described as adaptive model value. J2 gates remain separately adjudicated.



### Step 225: Freeze campaign contracts and weighted evaluation

- **Status:** HELD — original three-review authenticity/execution-authority failure; bounded prospective round only after reorientation
- **Latest checkpoint (2026-10-15):** Baseline test repair `e41e4b9` passed independent review; candidate now `324d5f0`, full Python 3407 passed/13 skipped, mypy/Ruff passed. Its single authorized six-lens review plus final judge returned NEEDS-WORK for preparation/scoring contracts and missing regression coverage. Held, pushed, unmerged; no further loop authorized. See [overnight results](../investigations/jev-overnight-results-2026-10-15.md). Steps 226-227 remain unstarted.
- **Type:** code
- **Issue:** #347
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [jev-overnight-improvement-plan.md](../archived-plans/jev-overnight-improvement-plan.md); original 225.

- **Problem:** Implement schema-versioned manifests, champion references, exact case matrices and deterministic scoring described in D2/D3.
- **Files:** src/jev/campaign.py; src/jev/campaign_evaluation.py; scripts/run_jev_campaign.py; tests/test_jev_campaign.py; tests/test_jev_campaign_evaluation.py
- **Existing context:** Sections 2 and 6 define the production boundaries; D7 assigns offline contracts/scoring here and execution authority to 227. Preserve the held original attempt and official J2 panels/desktop defaults.
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** `campaign_evaluation.py`, manifest portions of `campaign.py`, focused tests, dry-run/report entrypoint skeleton.
- **Done when:** Dry-run writes/validates an immutable no-network manifest; tests cover duplicate anchors, missing strata, unchanged candidate, weighted improvement masking race regression, invalid evidence and J2 panel isolation. The production CLI labels offline scores unverified and refuses live verdicts/promotion until 227 supplies verified execution facts; caller claims cannot enable an authoritative report.

<!-- autofix-applied: 2026-10-13 -->


### Step 226: Enforce the managed $5 ceiling

- **Status:** PENDING
- **Type:** code
- **Issue:** #348
- **Depends on:** 225
- **Source:** [jev-overnight-improvement-plan.md](../archived-plans/jev-overnight-improvement-plan.md); original 226.

- **Problem:** Add durable reservations at every campaign-paid request boundary, including probe and failure paths; validate vendor bounds before enabling paid mode.
- **Files:** src/jev/campaign_budget.py; src/jev/decision.py; src/jev/runner.py; tests/test_jev_campaign_budget.py; tests/test_jev_decision.py; tests/test_jev_benchmark.py
- **Existing context:** Sections 2 and 6 define the production boundaries; implement only this step, preserving official J2 panels and desktop defaults.
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** `campaign_budget.py`, optional provider integration, budget descriptor plumbing, focused boundary/crash tests.
- **Done when:** Concurrent/restarted reservation attempts cannot overspend; missing usage retains reservation; probes/retries are charged; unsupported pricing blocks before HTTP; unflagged desktop tests remain valid. Document verified pricing/billing assumptions and any account-side cap availability without inventing it.

<!-- autofix-applied: 2026-10-13 -->


### Step 227: Sequential Observer Rush controller and launcher

- **Status:** PENDING
- **Type:** code
- **Issue:** #349
- **Depends on:** 226
- **Source:** [jev-overnight-improvement-plan.md](../archived-plans/jev-overnight-improvement-plan.md); original 227.

- **Problem:** Run frozen parent/candidate cases through the combined viewer with explicit race, speed, limits, budget identity and graceful lifecycle.
- **Files:** src/jev/campaign.py; scripts/run_jev_campaign.py; scripts/launch-jev-overnight.ps1; scripts/launch-jev-battle.ps1; scripts/probes/jev_battle_view.py; tests/test_jev_campaign.py; tests/test_jev_battle_launcher.py; docs/operator/jev-overnight.md
- **Existing context:** Sections 2 and 6 define the production boundaries; implement only this step, preserving official J2 panels and desktop defaults.
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** `campaign.py`, `run_jev_campaign.py`, `launch-jev-overnight.ps1`, additive native-wrapper forwarding, operator guide.
- **Done when:** Offline lifecycle tests cover pause/restart/no duplicate cases, stale leases, deadline/budget refusal, wrong snapshot and viewer failure. CLI default is dry-run; real runs require explicit start. Code validates exact run attachment and preserves existing desktop defaults. Controller-owned execution records bind actual process exits and retained output to source/case/attempt/window/run identity; caller wrappers or prior archives cannot be rebound into valid evidence. Missing, skipped, xfailed or zero-collected critical tests cannot pass. Control disagreement invalidates epoch cache reuse; only verified facts can produce authoritative D2/D3 reports.

<!-- autofix-applied: 2026-10-13 -->


### Step 228: Real pipeline smoke before unattended work

- **Status:** PENDING
- **Type:** operator
- **Issue:** #350
- **Depends on:** 227
- **Source:** [jev-overnight-improvement-plan.md](../archived-plans/jev-overnight-improvement-plan.md); original 228.

- **Problem:** Prove a real Typesafe -> budget -> Jev -> Observer Rush -> report cycle before a long soak.
- **Files:** Read-only: docs/operator/jev-overnight.md; data/jev/campaigns/<campaign-id>/; data/jev/runs/<run-id>/
- **Existing context:** Sections 2 and 6 define the production boundaries; implement only this step, preserving official J2 panels and desktop defaults.
- **Produces:** Recorded smoke evidence and operator acceptance, no code artifacts.
- **Done when:** At least 60 seconds after first accepted hosted response, exact game is visible and ledger/trace/report agree; stop-after-game and resume are exercised across two cases without duplicate results or budget reset. Stay inside one shared $5 campaign ceiling; record spend. Any required code fixes return to 226/227 before retry.

<!-- autofix-applied: 2026-10-13 -->


### Step 229: Observe the first fixed-candidate overnight campaign

- **Status:** PENDING
- **Type:** wait
- **Issue:** #351
- **Depends on:** 228
- **Source:** [jev-overnight-improvement-plan.md](../archived-plans/jev-overnight-improvement-plan.md); original 229.

- **Problem:** Exercise six hours of realistic unattended lifecycle, or terminate earlier for budget/exhausted work/failure, and preserve the outcome honestly.
- **Files:** Read-only: campaign manifest/ledger/report and run archives; no shipped source edits
- **Existing context:** Sections 2 and 6 define the production boundaries; implement only this step, preserving official J2 panels and desktop defaults.
- **Produces:** Morning report, process/heartbeat evidence, source-verified case archives and operational verdict.
- **Done when:** Real campaign is monitored at start and completion; multiple case transitions occur, terminal state reconciles reservations, no orphaned match dispatch or overwritten evidence, and a resume test preserves unfinished work. Early failure is recorded as failure, not passed overnight acceptance. Strength improvement is not required for operational success.

<!-- autofix-applied: 2026-10-13 -->


### Step 230: Add bounded one-change advised iterations

- **Status:** PENDING
- **Type:** code
- **Issue:** #352
- **Depends on:** 229
- **Source:** [jev-overnight-improvement-plan.md](../archived-plans/jev-overnight-improvement-plan.md); original 230.

- **Problem:** Adapt the advised pattern to isolated Jev candidates and the tested campaign controller, within the same total spending ceiling.
- **Files:** src/jev/campaign_advisor.py; src/jev/campaign.py; scripts/run_jev_campaign.py; tests/test_jev_campaign_advisor.py; docs/operator/jev-overnight.md
- **Existing context:** Sections 2 and 6 define the production boundaries; implement only this step, preserving official J2 panels and desktop defaults.
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** `campaign_advisor.py`, allowlist and billing adapter, CLI advised mode, report extensions and isolation tests.
- **Done when:** One proposal yields one exact frozen artifact; policy/graph correctness tests precede games; rejected/malformed/out-of-scope/no-op proposals do not launch evaluations; every paid advisor action has a conservative bound. Unknown billing mode disables advised dispatch while fixed-candidate evaluation remains available.

<!-- autofix-applied: 2026-10-13 -->


### Step 231: Observe a complete advised cycle

- **Status:** PENDING
- **Type:** wait
- **Issue:** #353
- **Depends on:** 230
- **Source:** [jev-overnight-improvement-plan.md](../archived-plans/jev-overnight-improvement-plan.md); original 231.

- **Problem:** Verify automatic diagnosis, candidate construction, evaluation and checkpointing with real components and budgets.
- **Files:** Read-only: advised campaign evidence and isolated candidate artifacts; no shipped source edits
- **Existing context:** Sections 2 and 6 define the production boundaries; implement only this step, preserving official J2 panels and desktop defaults.
- **Produces:** Real advised campaign evidence and measured verdict; no automatic default-player switch.
- **Done when:** At least one full cycle reaches improved/regressed/inconclusive with exact artifact provenance, followed by a safe next-cycle boundary; overnight duration tests reservation/lifecycle behavior. Failure remains visible and blocks evolution-pool rollout.



### Step 207: Observe the live pipeline smoke

- **Status:** PENDING
- **Type:** operator
- **Issue:** #324
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [jev-player-plan.md](../archived-plans/jev-player-plan.md); original 207.

- **Problem:** Prove SC2 observation through Jev execution, persistence, API and browser completes a real cycle before longer matches.
- **Files:** Existing procedure from Step 206; no authored code in this step.
- **Produces:** Smoke evidence: run ID, policy hash, command/result event, verifier output and dashboard screenshot.
- **Done when:** After SC2 startup, observe 60 seconds of actual gameplay with graph-driven commands and live dashboard updates; disk/API/browser agree on run/hash/node IDs; close/reopen the tab and recover current state; stop cleanly and verify terminal stopped state. No mocks or replayed fixtures. Failures leave this gate incomplete.



### Step 208: Accept the basic player and viewer

- **Status:** PENDING
- **Type:** operator
- **Issue:** #325
- **Depends on:** 207
- **Source:** [jev-player-plan.md](../archived-plans/jev-player-plan.md); original 208.

- **Problem:** Establish whether Jev reliably plays the intended rush through full matches with useful inspection evidence.
- **Files:** Existing procedure from Step 206; no authored code in this step.
- **Produces:** Three sequential match evidence bundles and observed findings classified as functional blockers, strategy tuning or later enhancements.
- **Done when:** Run seeds 1, 2 and 3 against Terran difficulty 1 on Simple64, 900 game-second / 1800 wall-second limits per match. All three terminate normally with SC2 outcomes and complete trace provenance; no unbounded loop, orphaned run or duplicate-spend failure. At least one match visibly reaches four Gateways and sends the first four-Zealot attack, with reinforcement evidence. Inspect one waiting task and one completed command/result in browser; record observed recovery when it occurs, and explicitly mark unobserved recovery cases as scenario-tested only. A timeout/crash or absent rush behavior is a functional blocker; losses alone are tuning findings. Report wins without an estimated general win rate. Phase remains incomplete until blockers are fixed and affected gates rerun.



### Step 213: Observe the harder-opponent baseline

- **Status:** DEFERRED — operator stopped baseline; new testing request required
- **Type:** operator
- **Issue:** #330
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [jev-v2-plan.md](../archived-plans/jev-v2-plan.md); original 213.

- **Problem:** Identify the current rush's failure modes against stronger opponents.
- **Files:** data/jev/benchmarks/ and data/jev/runs/ (generated evidence), docs/plan-reconciliation/source-reviews/jev-v2-validation.md (observation report).
- **Produces:** Six baseline outcomes, replay/run IDs, metric coverage and ranked gameplay findings.
- **Done when:** Dashboard opens on the exact starting run before SC2 launches, visibly changes to Live as play begins, and follows the next case without menu interaction; real service probe and >=60-second production smoke pass before completing the six-case baseline in D6; all outcomes and interruptions are recorded; failures are classified as execution, information, composition, strategy or infrastructure. Missing prerequisites keep this step pending.
- **Operator decision (2026-10-09):** "you can stop the test and let's proceed with improvements." Staging and hosted smoke passed; v1 frozen as `v1-b53039a693cadf75`. Baseline batch `94ac2c35b17f4cbaa80020b1c859ba00` has one interrupted case and five pending, zero completed outcomes. Same-tab multi-game following and harder-opponent findings remain unverified. Do not resume paid testing without a new request. See [validation](../plan-reconciliation/source-reviews/jev-v2-validation.md).

<!-- autofix-applied: 2026-10-08 -->


### Step 222: Prove the full v2 decision cycle in SC2

- **Status:** HELD — remaining development matches paused for recovery/attack investigation
- **Type:** operator
- **Issue:** #339
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [jev-v2-plan.md](../archived-plans/jev-v2-plan.md); original 222.

- **Problem:** Validate real strategic and execution decisions through service, graph, commands and viewer.
- **Files:** data/jev/runs/, data/jev/benchmarks/ (generated), docs/plan-reconciliation/source-reviews/jev-v2-validation.md (observation report).
- **Produces:** Live screenshots, replays, accepted strategic/execution request IDs correlated with commands, latency/staleness and cleanup evidence.
- **Done when:** The prepared procedure observes >=60 seconds after a v2 question becomes eligible and at least one full match; a real strategy answer and at least one execution-level answer affect commands; actual second Nexus and first Stalker are observed; at least one worker/site recovery and pending-stop cleanup are exercised. Use at most three development matches (Terran, difficulties 3, 4, 5, seed 11, in order) within the per-invocation time/request cap. If natural play omits required evidence, record that gate unverified and pending; do not invent a scenario override or count offline fixtures. No mocks satisfy this gate.



### Step 223: Evaluate harder-opponent improvement

- **Status:** PENDING
- **Type:** wait
- **Issue:** #340
- **Depends on:** 222, J2.0
- **Source:** [jev-v2-plan.md](../archived-plans/jev-v2-plan.md); original 223.

- **Problem:** Determine whether v2 earns the bounded stronger-play claim.
- **Files:** data/jev/benchmarks/, data/jev/runs/ (generated), docs/plan-reconciliation/source-reviews/jev-v2-validation.md (findings), docs/jev-v2-progress.html (status).
- **Produces:** Held-out paired comparison, scripted attribution panel, explicit performance verdict and prioritized next findings.
- **Done when:** All 18 D6 evaluation games have valid provenance/outcomes or the exact remaining/invalid cases are reported with a hold; performance acceptance is DONE only if the pre-registered target passes. Negative completed evaluation remains recorded as target unmet, not silently promoted. Report execution, information, composition and strategy findings; no unlimited fix/retest campaign.



### Step J2.0: Decide ownership of unbuilt J2 scope

- **Status:** HELD — explicit scope decision
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [jev-v2-plan.md](../archived-plans/jev-v2-plan.md); original J2.0.

The J2 build outcome explicitly leaves max health/shield facts and 40% durability regroup, available_abilities and mineral_contents facts, saturation-based workers beyond probe_target 16, and an Assimilator at the natural without an owning step. Decide descope versus separate owning implementation before 223; preserve each requirement. Existing code completion is not evidence these shipped.


### Step EH.3: Fail loudly when the gauntlet is inert

- **Status:** PENDING
- **Type:** code
- **Issue:** #308
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EH.3.

- **Problem:** With `data/baselines.json` absent, `--fitness-mode baseline|both` degrades to `parent` (`scripts/evolve.py:3941-3947`) and `--panel-floor` is INERT (`:3955-3967`) — each with a startup WARNING but **no error and no failed run**, which `../operator-gate-runbook.md`:72-80 notes is "easy to miss in a long log". The operator-visible cost is a six-hour soak that produces a green-looking record for capabilities that never executed. Add `--require-baselines` (default OFF, preserving the byte-identical bare invocation) which converts the degradation into a **two-second non-zero exit** naming the missing registry and the exact `scripts/baseline.py add` command from `../operator-gate-runbook.md`:85-92. Keep the existing warn-and-proceed as the default so no queued gate command changes behaviour unless it opts in.
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/evolve.py`; extended `tests/test_evolve_cli.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7); a test asserts `--fitness-mode both --require-baselines` with an empty registry exits non-zero **before any game is dispatched** and the message names `scripts/baseline.py`; a paired negative test asserts the same invocation **without** the flag still warns and proceeds, pinning the default as byte-identical.



### Step EH.4: Runner-side control-file reader

- **Status:** PENDING
- **Type:** code
- **Issue:** #309
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EH.4.

- **Problem:** The dashboard's Stop Run button drives five hops — `EvolutionTab.tsx:1620` → `handleStopConfirm` `:1401` → `useEvolveRun.ts:383` `PUT /api/evolve/control` → `bots/v13/api.py:1460-1502` → atomic write of `data/evolve_run_control.json` — and terminates in a file **no runner opens**; grepping `scripts/` and `src/` yields exactly one hit, and it is a comment in `launch-evolve.ps1:31` stating that nothing reads it. `pause_after_round` (`EvolutionTab.tsx:1411`, checkbox `:1634-1641`) is a **second silent no-op on the identical file** — **cut from this step by operator decision P-1** (§3 Explicitly out): `read_run_control` still parses the key, because the API writes it and a reader must not choke on it, but nothing acts on it, and EH.5 disables the checkbox so the UI stops promising it. Add `--control-path` beside `--state-path` (`:265-273`) read via `getattr(args, "control_path", None)` so the ~40 tests built by `_build_args` stay green; add `read_run_control(path)` beside `write_run_state` (~`:1412`) returning both-False on missing/`OSError`/`JSONDecodeError`/**non-dict payload**/**non-bool key value** (§5) and **not** requiring the phantom `updated_at` field; **clear the file to both-False at the initial running-state write (`:4022`)** — load-bearing, because a `stop_run: true` is armed on disk right now and a reader without the clear would abort the next run at generation 0 (§6 D-4). Per operator decision **P-6** the clear is **unconditional, including on `--resume`** (an explicit resume is a fresher operator intent than the stale stop, and honouring the old stop would make resume appear to do nothing) — but it must **emit an INFO line naming the pending stop it discarded**, so a superseded stop is visible in the run log rather than silently dropped. Then poll at the loop head (`:4284`, before the wall-clock check) setting `stop_reason = "dashboard-stop"`, at the serial fitness loop (`:4441`), and via `halt_state["stop_dispatching"]` in the parallel dispatcher (`:2785-2793`) — **two shapes, because the cancel machinery is parallel-path-only while `--viewer` forces serial** (§6 D-5). **Drain in-flight games, never kill** the SC2 subprocess. Update the three stale `SKILL.md` claims (`:63`, `:359`, `:462`), `wiki/operator-commands.md:335` and the `launch-evolve.ps1:31` comment **in this step, after the reader lands** — `tests/test_evolve_cli.py:5142-5157` parses stop reasons out of SKILL.md and asserts each appears in evolve.py source, so the doc edit only passes once the code emits the string. **Do not touch `docs/archived-plans/evolve-viewer-plan.md`** — its five now-false "not wired" claims (`:657, :665, :684, :803, :936`) are tracked in a sibling issue per operator decision **P-7**, because that plan has two open operator gates.
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `scripts/evolve.py`; modified `.claude/skills/improve-bot-evolve/SKILL.md`; modified `docs/wiki/operator-commands.md`; modified `scripts/launch-evolve.ps1` (comment only, ASCII/no-BOM); extended `tests/test_evolve_cli.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7) and (1) an integration test drives real `run_loop` with `hours=0.0` and **`generations=0` (unbounded)** — so the control file is the only thing that *could* stop the loop — where the fitness stub arms `stop_run` on its first call, asserting `rc == 0`, `state["status"] == "completed"`, `state["generations_completed"] == 1`, and `"- Stop reason: dashboard-stop"` in the run log; (2) a stale-flag test arms `stop_run: true` **before** `run_loop` and asserts at least one generation still executes, proving the startup clear fired; (3) a corrupt-file test parameterised over `"{not json"`, `"[]"`, `"null"` and `'"stop"'` asserts the run completes normally in all four cases, plus a non-bool test writing `'{"stop_run": "yes"}'` asserting the run does **not** stop early (non-bool coerces to False), pinning the coercion decision rather than a crash; (4) a cross-file pin asserts evolve.py's `--control-path` basename equals `bots/v13/api.py:1369`'s `_EVOLVE_CONTROL_FILE`; (5) a resume test arms `stop_run: true`, runs with `--resume`, and asserts the run **proceeds** and the run log carries the INFO line naming the discarded pending stop (P-6); (6) a test writing `'{"pause_after_round": true}'` asserts the run is **unaffected** — pinning P-1's cut, so a later re-wiring is a deliberate change rather than an accident.



### Step EH.5: Frontend stop honesty

- **Status:** PENDING
- **Type:** code
- **Issue:** #310
- **Depends on:** EH.4
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EH.5.

- **Problem:** `useEvolveRun.ts:381-391` never binds the `fetch` Response, so `res.ok` genuinely cannot be checked and a 400/500 still produces a success toast; `EvolutionTab.tsx:1401-1407` fires `showMessage("Stop requested — run will end at the next generation boundary")` unconditionally after the PUT, with no try/catch and no error branch. Even after EH.4 makes the stop real, the UI has no way to show a request that failed to reach the API. Bind and check the Response; replace the fire-and-forget toast with a persistent "Stop requested — pending" badge driven by `control.data.stop_run` and cleared when the runner clears the flag at the next run start; and fix the `:1330` empty-state copy, which currently blames the pre-EL single-lineage loop for an empty lineage panel that was actually caused by the missing writer. **Disable the "Pause after current generation" checkbox (`:1634-1641`) and label it not-implemented** — operator decision **P-1** cuts the runtime work for `pause_after_round`, and cutting the work must not leave the control lying. Use `disabled` plus a short title/helper string; do not remove the markup, so re-arming it later is a one-line change.
- **Flags:** --reviewers code --isolation worktree --ui --start-cmd "bash scripts/start-dev.sh" --url http://localhost:3000
- **Produces:** modified `frontend/src/hooks/useEvolveRun.ts`; modified `frontend/src/components/EvolutionTab.tsx`; extended `frontend/src/components/EvolutionTab.test.tsx`
- **Done when:** `cd frontend && npm run test:run` green at ≥234 (228 passing, 6 skipped baseline) with a new case asserting the pending badge renders from `control.stop_run` rather than from a toast, a case asserting a non-ok PUT surfaces an error instead of a success message, and a case asserting the pause checkbox renders **disabled** with its not-implemented label (P-1); `npm run build` clean; backend suite untouched at ≥ the EH.1-recorded pytest baseline (§7).



### Step EH.6: Launcher `--generations` passthrough

- **Status:** PENDING
- **Type:** code
- **Issue:** #311
- **Depends on:** EH.8
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EH.6.

- **Problem:** `scripts/evolve.py` declares `--generations` with `default=1` (`:217`) and the loop breaks with `stop_reason = "generations-reached"` as soon as the cap is met (`:4290-4297`). `scripts/launch-evolve.ps1` has exactly one parameter, `[double]$Hours = 4` (`:45`), and its spawn line (`:72`) forwards only `--hours` and `--viewer` — so the observatory `run-evolution` button and every `.\scripts\launch-evolve.ps1 -Hours N` promise an N-hour soak and deliver **one generation** (~20 min), then exit cleanly with nothing anywhere saying so. `../operator-gate-runbook.md`:40-45 rates this **fatal to EV.5, EJ.8 and EL.7**. Add `[int]$Generations = 0` at `:45` and `--generations $Generations` to `:72`; correct the false header comment at `:27-29` (which claims the run "keeps going headless to its `--hours` budget"); add `--generations 0` plus a flag-table row to the **second independent caller**, `.claude/skills/improve-bot-evolve/SKILL.md:216-222` and `:34-53`; and add a non-fatal startup WARNING in `scripts/evolve.py` when `--hours > 0` and `--generations` was left at its default of 1, naming the contradiction. **Do not flip the argparse default** (§3 Explicitly out) and leave `tests/test_evolve_cli.py:457` `assert args.generations == 1` untouched — it is the tripwire that forces the docstring and wiki to move together if a future change ever does flip it. `scripts/launch-evolve.ps1` is pinned ASCII-only with no BOM by `tests/test_evolve_cli.py:5167-5172`. Sequenced after EH.8 so the unattended multi-hour run this step unblocks cannot sweep the operator's staged index into an `[evo-auto]` commit (operator decision P-4, § Decision Inventory).
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/launch-evolve.ps1`; modified `scripts/evolve.py`; modified `.claude/skills/improve-bot-evolve/SKILL.md`; extended `tests/test_evolve_cli.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7) and (1) `test_launch_evolve_ps1_pins_the_viewer_spawn_contract` (`:5160`) gains an assertion that `--generations` appears in the parsed `$evolveCmd` line, mirroring its existing `assert "--viewer" in spawn` at `:5192`, plus a separate assertion that the param block declares a default of 0; (2) a new `caplog` test asserts a WARNING naming both `--hours` and `--generations` fires for `hours=4.0, generations=1`; (3) a paired negative test with an explicitly-passed `--generations 1` and `hours=0.75` asserts **no** warning, so the guard cannot regress into noise; (4) the ASCII/no-BOM launcher test still passes.



### Step EH.7: Single-run lock + stale run-state reconciliation

- **Status:** PENDING
- **Type:** code
- **Issue:** #312
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EH.7.

- **Problem:** *"One evolve run at a time, machine-wide"* is enforced **by convention, not code** (`../operator-gate-runbook.md`:174-183): "There is no lock file, no pid file." Two runs collide on `data/evolve_*.json`, both flip `bots/current/current.txt`, and — worst — a second run starting mid-soak calls `_clear_fresh_run_state` (`scripts/evolve.py:1888-1899`), which **truncates the first run's `evolve_results.jsonl`**, destroying the evidence of a soak in progress. The only guard is a Windows CIM probe in `launch-evolve.ps1:54-75` that is launcher-scoped, Windows-only, blind to a WSL run, and explicitly fails open — and **every gate command in the runbook is a direct `python scripts/evolve.py` that bypasses it entirely.** Stamp pid + start timestamp into the already-existing `data/evolve_run_state.json` at the `:4022` write and check it at startup. **Read the pre-existing file strictly before the `:4022` write** — that write is a whole-file `_atomic_write_json` (`:1472`) and would otherwise clobber the very record being checked; both precede `_clear_fresh_run_state` (`:4096`), which is what truncates `evolve_results.jsonl`. **Liveness is `psutil.pid_exists(pid)`, never `os.kill(pid, 0)`:** on Windows (the sole declared platform, `pyproject.toml:16`) `os.kill` resolves to `OpenProcess` + `TerminateProcess(handle, 0)`, so the POSIX idiom *kills* the soak this lock exists to protect — and would kill the pytest process in this step's own Done-when (1). `psutil` is guaranteed importable with no fallback branch: it is an **unconditional transitive dependency of the core `portpicker>=1.6`** (`pyproject.toml:34`; `uv.lock` portpicker block is `dependencies = [{ name = "psutil" }]`, distinct from the `viewer`-extra declaration at `uv.lock:171`), and `bots/v*/system_info.py:35` already imports it at module scope inside the current baseline — add no lazy import and no degrade-on-`ImportError` path, because a lock that fails open is not a lock. Guard pid reuse by comparing the stamped start timestamp against `psutil.Process(pid).create_time()`: a process younger than the stamp means the pid was recycled and the record is stale. Then: if the recorded pid is alive and the state is `running`, refuse to start with a non-zero exit naming the live pid; and — generalizing — refusal requires a **positively confirmed** live pid, so any `running` record from which liveness cannot be established (pid key absent, non-integer, or the probe itself raising — the pid-less shape is the ONLY shape on every pre-EH.7 machine, including the 17-key record on disk at HEAD) is treated as stale: reap the stale record and proceed, emitting an INFO line naming the reaped `run_id` and `started_at` so a mixed-version collision is diagnosable after the fact — which also fixes the write-only staleness defect (`:180`, `:190-193`), where the file has claimed `"status": "running"` since a 2026-09-14 launcher run (`started_at: 2026-09-14T04:19:28+00:00`, re-verified 2026-10-05) that never completed a generation, making the dashboard render a phantom soak indefinitely. Provide `--force` to override for the genuine recovery case.
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `scripts/evolve.py`; extended `tests/test_evolve_cli.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7) and (1) a test with a state file naming **this** process's own live pid asserts a second `run_loop` refuses with a non-zero exit and the message names the pid, and asserts `evolve_results.jsonl` is **byte-unchanged** — the truncation is the actual harm; (2) a test with a state file naming a dead pid asserts the run proceeds and the stale record is reaped; (3) `--force` overrides (1); (4) a fresh checkout with no state file is unaffected; (5) a test seeded with the **real pre-EH.7 record shape** — `status: "running"`, a past `started_at`, and NO `pid` key, i.e. exactly what `write_run_state` (`:1412`) produces today — asserts the run **proceeds** (rc 0, not a refusal) and that the emitted INFO line names the reaped `run_id` and `started_at`; a sibling case with `"pid": "not-an-int"` takes the same path; (6) a recycled-pid test where the state file names a live pid whose `create_time()` is newer than the stamped start timestamp asserts the run proceeds and reaps; (7) a source-level pin asserts the lock code contains no `os.kill`, mirroring the cross-file pin at `tests/test_evolve_cli.py:5142-5157`. Clause (1) additionally asserts the probing process is **still alive** after the refusal — the canary that catches a `TerminateProcess`-shaped probe.



### Step EH.8: Path-scope the `[evo-auto]` commit

- **Status:** PENDING
- **Type:** code
- **Issue:** #313
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EH.8.

- **Problem:** `EVO_AUTO=1` commits **sweep the entire git index**, not just `bots/<v>/` (`../operator-gate-runbook.md`:208; `.claude/rules/evolve.md` § Pre-launch hygiene), so anything an operator happened to stage rides into the next `[evo-auto]` commit — on whatever branch is checked out, during an unattended six-hour soak. The only mitigation today is a pre-flight `git diff --staged --stat` the operator has to remember. **The `git add` is already path-scoped** — `scripts/evolve.py:1564` is `["git", "add", f"bots/{new_version}/", "bots/current/current.txt"]`, the only `git add` call site in the repo — so there is nothing to replace there, and it must be **kept**: a fresh `bots/<vN>/` is untracked, so the add is what makes those files committable at all. The sweep is the **bare commit** at `:1592`, `["git", "commit", "--no-verify", "-m", msg]`, which carries no pathspec and no `-a` and therefore commits everything already in the index — evolve.py's own docstrings say exactly this at `:1646` and `:1678-1681`. Fix **four** sites, not one: (a) make the promote commit partial — `git commit --no-verify --only -m msg -- bots/<new_version>/ bots/current/current.txt`; (b) immediately before it, read `git diff --cached --name-only` and log an **ERROR naming any staged path outside that set — log, never abort**, because a refusal produces no commit at all, fails this step's own Done-when (1), and would silently drop a promotion on every generation of an unattended soak; (c) `git_revert_evo_auto` carries the identical bare commit at `:1750` and is live via `scripts/evolve.py:3825` and `scripts/evolve_inject_one.py:287` — scope it too, but verify the shape in a scratch repo first, since `git revert --no-commit` leaves sequencer state and a partial commit there may be refused (fall back to `git revert --quit` after the scoped commit, or an isolated `GIT_INDEX_FILE`); (d) `_reset_staged_promote` (`:1657`) and `_reset_staged_revert` (`:1687`) both run `git reset HEAD -- .`, which unstages the operator's unrelated work on every commit-failure path — scope both resets to the same paths (the revert path derives its set from `git show --name-only --pretty= <promote_sha>`, since a pre-EH.8 promote commit may itself have swept foreign paths). Correct the stale rationale comment at `:1585-1586` in the same edit — `--no-verify` skips `scripts/check_sandbox.py` entirely, and per ``.claude/rules/evolve.md`:31 that hook "blocks invalid paths but doesn't enforce ONLY these paths" — but keep `--no-verify` itself; the WSL shebang rationale below it is real. Note the partial-commit gotcha: `git commit -- <paths>` commits the WORKING-TREE state of those paths, identical to the staged state here only because the `git add` immediately precedes it. This is the workspace's standing rule for git-touching automation (`dev/.claude/rules/working-directory.md`: "Prefer path-scoped `git add <paths>`").
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `scripts/evolve.py` (`git_commit_evo_auto`, `git_revert_evo_auto`, `_reset_staged_promote`, `_reset_staged_revert`); extended `tests/test_evolve_cli.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7) and a test that stages an unrelated file in a temp git repo, runs the promotion commit path, and asserts (1) the resulting commit contains **only** the promotion paths and (2) the unrelated staged file is still staged and uncommitted afterwards; (3) the same two assertions hold for `git_revert_evo_auto` — an unrelated staged file survives the rollback commit still staged; and (4) a forced commit failure leaves the unrelated file staged, proving the `_reset_staged_*` cleanups no longer run `git reset HEAD -- .`.

<!-- autofix-applied: 2026-09-05 -->


### Step EH.9: Producer→consumer smoke gate

- **Status:** PENDING
- **Type:** code
- **Issue:** #314
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EH.9.

- **Problem:** Every existing lineage test either calls `write_lineages` directly as a fixture seed (`tests/test_evolve_cli.py:2156` and five siblings) or hand-writes `lineages.json` as JSON (`tests/test_api_evolve_lineages.py:84` and four siblings). **That is precisely why this defect survived a 2,026-test suite**: both endpoints are asserted, the relationship between them never is. **Premise narrowed 2026-10-05 — EH.1 and EH.2 each shipped part of what this step was written to add.** EH.1's `tests/test_lineage_cli.py:354` (`test_cli_engages_and_disengages_the_evolve_consumer`) already seeds via `scripts/lineage.py`'s `main()` at the default path and asserts the real `_load_lineage_registry_if_engaged` reads it back, and EH.2's `tests/test_evolve_cli.py:2668` (`test_lineage_write_back_persists_advanced_head_and_logs`) already re-reads through `load_lineages` and asserts `created_at`/`pool_path`/`parent_chain`/`status` survive for both records. **What remains genuinely unbuilt is the `run_loop` → disk → API half of the chain, and the UNSCHEDULED-third-record case** — scope this step to those two and do not re-add coverage that already exists. Add one chain smoke that wires the real components: seed by calling `scripts/lineage.py`'s `main()` **in-process** under the same `monkeypatch.setattr(lineages_mod, "_repo_root", lambda: tmp_path)` harness §4 already designates (`tests/test_evolve_cli.py:2133-2156`), additionally passing `--path tmp_path / "data" / "lineages.json"`; the seeded path and the path `run_loop` reads MUST be the same file, because `_load_lineage_registry_if_engaged` hardcodes `default_lineages_path()` (`:3736`) with no path parameter and an unmonkeypatched seed would read — or write — the **real** repo-root registry. Drive real `run_loop` with `--lineages 3` for two generations, assert `_load_lineage_registry_if_engaged` returned `len(registry) == 3` (unpacking the `(registry, from_disk)` tuple EH.2 introduces, and asserting `from_disk is True`) rather than the single implicit `main` from `:3759-3768`, then assert the on-disk file, re-read via `load_lineages`, still contains **all three** records, that the **two** lineages which took a round-robin turn have `head_version` differing from their seed, and that the **unscheduled** third record is present with its seed `head_version` and seed `created_at` intact — a generation is one lineage's turn (`:4310-4333`), so three lineages over two generations leaves one unscheduled by construction, and that survivor assertion is the only one in the phase that catches a persist hop which writes only the live `_lineage_heads` dict, then serve `GET /api/evolve/lineages` against that same directory and assert the response body carries a non-empty `lineages` array with the **advanced** `head_version`. **Point the API at that directory explicitly** — `configure(tmp_path/"data", tmp_path/"logs", tmp_path/"replays", evolve_dir=tmp_path/"data")` before driving it through `fastapi.testclient.TestClient(app)`, reusing the `evolve_dir`/`client` fixture pair at `tests/test_api_evolve_lineages.py:26-41`. Monkeypatching `lineages_mod._repo_root` repoints the **writer only**; the endpoint resolves its directory from the module-level `_evolve_dir` that `bots.v13.api.configure(...)` sets, so without this call the consumer half of the chain reads the real repo-root registry and the smoke gate silently proves nothing. No SC2 process and no real games — the fitness function is stubbed; everything else is production code on its real import path.
- **Flags:** --reviewers code --isolation worktree
- **Produces:** new chain-smoke test in `tests/test_evolve_cli.py` (or `tests/test_evolve_chain_smoke.py`); extended `tests/test_api_evolve_lineages.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7); the smoke completes in under 60 seconds; it **fails** when run against the pre-EH.2 persist hop (verify by reverting the hop locally and observing red — record that red-first evidence in the step checkpoint); and it asserts the API response, not just the file.



### Step EH.10: Operator verification — Gate 0 plus one armed short run

- **Status:** PENDING
- **Type:** operator
- **Issue:** #315
- **Depends on:** EH.3, EH.4, EH.5, EH.6, EH.7, EH.9
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EH.10.

- **Problem:** Six of the nine preceding steps change behaviour that only a real run can demonstrate, and three of them (EH.4, EH.6, EH.7) are deployment-seam changes — a launcher script, a control file the dashboard writes, and a startup guard — which unit tests structurally cannot exercise against the real environment. Perform the runbook's **Gate 0** first: `uv run python scripts/baseline.py add v10 v10 --note "frozen anchor"`, again for `v13`, then `list` (`../operator-gate-runbook.md`:85-92, :253-256). Then seed a 2-lineage registry with the new `scripts/lineage.py`, start a short run through the **launcher** (the real production entry point for the observatory button), and confirm on live substrate: the run does not stop after one generation; the dashboard's Lineages panel shows non-empty data with advancing heads; pressing **Stop Run** ends the run at the next generation boundary and the badge reflects a pending state until it does; and a second `python scripts/evolve.py` launched mid-run is refused rather than truncating `evolve_results.jsonl`.

  **Pre-flight, run from the repo root:**

  ```powershell
  cd $env:USERPROFILE\dev\Alpha4Gate
  git branch --show-current          # expect master-plan/phase-ev
  git diff --staged --stat           # MUST be empty - EH.8 has landed by now (EH.10 -> EH.6 -> EH.8), so this is belt-and-braces
  uv run python scripts/baseline.py list
  uv run python scripts/lineage.py list
  uv run python scripts/lineage.py add main v13
  uv run python scripts/lineage.py add line-2 v10
  uv run python scripts/lineage.py list      # expect 2 active records
  .\scripts\launch-evolve.ps1 -Hours 1 -Generations 0
  ```

  Do **not** run the launcher bare — post-EH.6 its defaults are `-Hours 4 -Generations 0`, an unbounded
  four-hour soak, which is exactly the long slot this step declines to consume. `-Hours 1` is a ceiling,
  not a plan: check (3), pressing **Stop Run**, is the intended terminator, pressed once the second
  generation boundary has been observed. Budget ~45-60 min of machine time at ~20 min/generation,
  serialized machine-wide against every other pending soak. The existing pid-less
  `data/evolve_run_state.json` is **expected** to be reaped by the first launch and must NOT be
  hand-deleted beforehand — deleting it converts this step's most valuable real-world check into a re-run
  of EH.7's Done-when (4).

  **Post-flight teardown, same shell:**

  ```powershell
  uv run python scripts/lineage.py list      # take the seeded ids from here, do not assume names
  uv run python scripts/lineage.py remove main
  uv run python scripts/lineage.py remove line-2
  uv run python scripts/lineage.py list      # expect empty
  git log --oneline -20                      # capture every [evo-auto] commit this run produced
  ```
- **Produces:** a verification record under `docs/soak-test-runs/` with the pre-flight state, the observed outcome of each of the four checks, and the run log excerpt showing `Stop reason: dashboard-stop`
- **Done when:** all four checks are observed and recorded **from the live environment's own evidence** (dashboard screenshot, run log, `data/lineages.json` before/after) rather than inferred from tests; Gate 0's `baseline.py list` output is captured; **the seeded registry is torn down before the record is filed** — remove every lineage id the run's own `scripts/lineage.py list` reports (`main`/`line-2` are only the runbook's example at `../operator-gate-runbook.md`:148-151), then capture a post-flight `list` showing none; `remove` routes through `write_lineages`, so the file normally remains on disk as `{}`, which is the pass condition, not a failure. If the operator deliberately keeps the registry live to feed EL.7 (#279), the record must say so **in writing** — the default is teardown, because a surviving non-empty registry engages multi-lineage for every later bare invocation (`scripts/evolve.py:3756-3757`), including EJ.7 #288, EJ.8 #289, EV.5 #295, Phase 7 Step 6 #280 and EI.14. **Do not tear down `data/baselines.json`** — Gate 0's frozen anchors are meant to persist (§3) and EH.3 depends on them. Commit posture is also recorded: the launcher cannot pass `--no-commit`, so re-confirm `git diff --staged --stat` is empty and `git branch --show-current` is `master-plan/phase-ev` immediately before launching, then capture `git log --oneline` for every `[evo-auto]` commit the run produced — or record explicitly that none were — plus `git show --stat` for each, confirming only `bots/<vN>/` and `bots/current/current.txt` paths appear. **Claims survival and control-correctness only** — this is not a throughput or fitness verdict, and it deliberately does **not** consume a long soak slot. **No step in this phase owns the multi-hour tier** — operator decision **P-2 as overridden** cut the co-tenant `Type: wait` step the draft carried (§3), so EH.10 is the phase's terminal verification and there is nothing downstream to hand off to. EH.10 claims nothing about time-dependent behaviour; whatever EL.7 (#279) or EI.14 happens to show is opportunistic evidence this plan neither owes nor collects.



### Step EI.1: Per-game log identity + retention

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.0, EH.10
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.1.

- **Problem:** `src/orchestrator/selfplay.py:292` writes `logs/selfplay_{version}_{role}.log`, which burnysc2 opens `"w+"`, so every game truncates the previous game's only trace. Four coupled changes. **(a) Filename.** Use the literal template `selfplay_{version}_{role}_{match_id}.log`, keying on the `match_id` already minted at `selfplay.py:684`. **Keep the `selfplay_{version}_{role}` prefix** — the only machine consumer globs on the parent version (`src/orchestrator/evolve.py:1381`), so a name that drops the version can never match and the function silently returns its fallback string straight into the proposer prompt. Do NOT key on `game_index`: it is always 0 in PFSP mode (D-1). Threading `match_id` to the producer adds one parameter to **each** of `_build_match` (`selfplay.py:296-303`) and `_build_bot_process` (`:260`) — neither has a test caller, so the signature change is contained. **(b) One source of truth.** Expose the shape as a single importable format helper (not a raw string) that BOTH producer and consumer import, replacing the inline f-string at `:292` and the two duplicate glob patterns at `evolve.py:1381` and `:1383`. **(c) Reader budget.** Re-anchor `_read_log_tails` and bound it to a newest-N set — its 4,000-byte cap is applied **per file** inside the loop, so per-game naming turns an ~8 KB prompt section into games × 4 KB and then generations × games × 4 KB (D-2). **(d) Retention.** Add a dedicated `_prune_selfplay_logs(logs_dir, keep_n)` called at the **generation boundary inside `run_loop`**. Do **NOT** put it in `_cleanup_stale_round_files`: that function's sole caller sits behind `if concurrency > 1` at `scripts/evolve.py:3873`, so retention placed there never runs at the default `--concurrency 1` — which is also mandatory for every `--viewer` run — and even at higher concurrency it runs once at dispatcher startup, not per generation. Retention must never unlink a file belonging to the live run, and must not delete a prior run's retained logs on `--resume`.
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `src/orchestrator/selfplay.py` (log-name helper; `_build_match` and `_build_bot_process` signatures), `src/orchestrator/evolve.py` (`_read_log_tails` re-anchor + newest-N bound), `scripts/evolve.py` (`_prune_selfplay_logs` + its generation-boundary call site); a new producer→consumer round-trip test
- **Done when:** full `uv run pytest` green at or above the 2,007/2,024 baseline, ruff clean, mypy clean in both scopes (packages, and the `scripts/` gate at or below its 3-error baseline), with new tests asserting (1) two games in one batch produce two distinct log files, neither truncating the other; (2) an **integration test through the production caller** per [`code-quality.md`](../../../.claude/rules/code-quality.md) — generate logs via the real producer, then read them back via the real `_read_log_tails`, asserting non-empty content for a known match, because a pattern-only unit test passes while production silently degrades to the empty-glob fallback string; (3) the shared name helper is asserted `is`-identical between producer and consumer, per `code-quality.md` § "One source of truth for data-shape constants"; (4) the newest-N bound holds the tails section under a fixed byte ceiling given 50 files; (5) **retention actually fires through `run_loop` at `--concurrency 1`** — a direct call to the retention helper does NOT satisfy this clause, because that is exactly the silent-wiring shape this step exists to avoid; (6) retention never unlinks a file whose mtime is inside the live run's window, and a `--resume` does not delete the prior run's retained logs. The two existing prompt assertions at `tests/test_evolve.py:1841` and `:1872` still pass unmodified.



### Step EI.2: `src/orchestrator/trace.py` — stdlib trace parser

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.0, EH.10
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.2.

- **Problem:** Add a leaf module that parses one game's console log into a structured record and extracts **mechanical** features only (terminal-result marker present, step count, duration, status-chunk counts). No `bots.*` import, no `evolve.py` import, no play-quality judgement — round 2 measured that whole class at chance. Pure stdlib so it is unit-testable in isolation and cannot break a run by existing.
- **Flags:** --reviewers code --isolation worktree
- **Produces:** new `src/orchestrator/trace.py`; `tests/test_trace.py`
- **Done when:** full suite green, mypy clean (this module IS in mypy's scope), with tests covering a complete log, a truncated log, an empty file, and a log that predates the status-log line entirely — the last case matters because 36.7% of the existing candidate corpus has no status line at all, and the parser must report absence rather than inferring a value.



### Step EI.3: Trace capture at the two existing game-end seams

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.1, EI.2
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.3.

- **Problem:** Wire `trace.py` into the fitness and regression game-end callbacks (`src/orchestrator/evolve.py:626` and `:827`), writing one parsed trace per game, fail-open so any parse error logs and continues rather than aborting a soak. **Capture is gated on a new `--trace-capture` flag, default OFF**, so the bare invocation stays byte-identical; thread it through the worker argv as well as the serial path, since the fitness callback runs inside the worker at `--concurrency > 1`. Traces are written to `logs/traces/` under a filename shape exported as a single importable constant from `trace.py`, mirroring EI.1's treatment of the log name, and pruned by the same `_prune_selfplay_logs`-style generation-boundary helper with its own `keep_n`. Sequenced after EI.1 so the per-worker splice cause is already removed; do NOT disable under concurrency, which would make the layer inert on the most recently recorded soak configuration.
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `src/orchestrator/evolve.py`, `scripts/evolve.py` (flag + generation-boundary prune), `scripts/evolve_worker.py` (argv), `src/orchestrator/trace.py` (output-name constant); capture tests
- **Done when:** full suite green; a test drives a fake batch through the **production** callback path and asserts a trace lands per game; a test asserts a raising parser does not propagate and does not prevent the caller's own callback from running; an assertion that with `--trace-capture` absent the callback chain is byte-identical; and an assertion that the flag reaches the worker argv.



### Step EI.4: Proposer evidence provenance

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.1
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.4.

- **Problem:** The proposer is handed log tails with nothing saying which games they are. Label each tail with its file, seat, opponent and outcome. **Join on the `match_id` that EI.1 puts into the filename, against the persisted `data/selfplay_results.jsonl`** — NOT against the in-scope mirror `records` at `src/orchestrator/evolve.py:1823`, which cover mirror games only while most tails are fitness games and would therefore go unlabelled. Specify the degraded label for a tail whose `match_id` has no matching row (e.g. `(unjoined — no result row for this match)`) so a missing join fails honestly rather than silently. **Do not** add a "no mirror games this generation" line: shipped code already prints `Mirror games run (parent vs parent): 0` on refresh generations, so round 1's framing of this as a false label was wrong; the defect is missing provenance only. **This step depends on EI.1** — before per-game log identity exists there is exactly one truncated file per (version, seat) and nothing to join a tail to, so labelling it would stamp false provenance onto the most expensive call in the loop.
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `src/orchestrator/evolve.py` prompt assembly
- **Done when:** full suite green; a test asserts the assembled prompt names the seat and outcome for a known record; a test asserts the degraded label is emitted for an unjoinable tail rather than a wrong one; the two existing prompt assertions still pass. There are **no prompt goldens** in this repo (verified: zero test references to the prompt builder or log-tail reader), so this step has no golden-update cost.

<!-- autofix-applied: 2026-09-02 -->


### Step EI.5: `SelfPlayRecord` evaluation id + bidirectional compat

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.0, EH.10
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.5.

- **Problem:** Add `fitness_eval_id: str | None = None` and `eval_game_index: int | None = None` to `SelfPlayRecord` as **optional with defaults**, stamp them at the single production construction site (`src/orchestrator/selfplay.py:730`), thread the id through `run_batch` as a keyword-only argument and pass it at the **four** `run_batch_fn(` call sites in `src/orchestrator/evolve.py` — `:650` (fitness), `:858` (regression), `:1017` (baseline gauntlet), `:1819` (mirror). **`fitness_eval_id` is a `str(uuid.uuid4())` minted once per `run_batch` invocation by the CALLER**, mirroring how `match_id` is minted per game at `selfplay.py:684`; `eval_game_index` is the zero-based index of the game within that batch. Callers that do not supply an id leave both fields `None`, which is what every historical row already means. Consumers: the two replay loops (`src/orchestrator/ladder.py:359`, `scripts/ladder.py:142`), the parallel mirror rehydration in `scripts/evolve.py`, the worker result path, and any later analysis that needs to group a 5-game evaluation without timestamp clustering — which is the whole point of the field. and harden `from_json` to **filter unknown keys** rather than only supplying defaults (D-4). Apply the same tolerance at `src/orchestrator/evolve.py:205`, which bypasses `from_json` entirely (D-5), and in lockstep at both replay loops (`src/orchestrator/ladder.py:359` and `scripts/ladder.py:142`) and the parallel mirror rehydration.

  **`SelfPlayRecord` current shape** (`src/orchestrator/contracts.py:121-145`, verified 2026-09-02) — the nine keys every one of the 1,792 existing rows carries:

  | Field | Type | Note |
  |---|---|---|
  | `match_id` | `str` | `str(uuid.uuid4())`, minted per game at `selfplay.py:684`; the EI.1 filename key and the EI.4 join key |
  | `p1_version` | `str` | |
  | `p2_version` | `str` | |
  | `winner` | `str \| None` | version string of the winning side; `None` for draw/crash |
  | `map_name` | `str` | |
  | `duration_s` | `float` | |
  | `seat_swap` | `bool` | always `False` in PFSP mode (D-1 corollary) |
  | `timestamp` | `str` | ISO 8601 |
  | `error` | `str \| None = None` | the only field carrying a default today |

  Frozen dataclass. `to_json` is `dataclasses.asdict`; `from_json` is a strict `cls(**json.loads(data))` — which is what D-4 hardens. The two new fields are additive optional-with-default, making an eleven-key row (nine plus two).

- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `src/orchestrator/contracts.py`, `selfplay.py`, `evolve.py`, `ladder.py`; `scripts/ladder.py`, `scripts/evolve.py`, `scripts/evolve_worker.py`; a NEW tracked fixture `tests/fixtures/selfplay_results_legacy.jsonl` (~50 rows copied verbatim from the live file); extended `tests/test_selfplay.py`, new `SelfPlayRecord` coverage in `tests/test_contracts.py`
- **Done when:** full suite green; tests assert (1) a legacy nine-key line loads and yields defaults; (2) a dataclass constructed **without** the new fields accepts a wider eleven-key payload — this simulates an older build and is the honest form of the roll-back claim (see D-4); (3) a round trip that **sets the new field to a non-default value** — dataclass equality passes trivially when both sides hold equal defaults, so a default-valued round trip cannot detect the drift; (4) the **tracked fixture** replays without error through **both** replay loops. Do NOT assert against `data/selfplay_results.jsonl`: it is gitignored (`.gitignore:42`) and this step runs `--isolation worktree`, so the file does not exist there and the assertion would be silently skipped or fabricated. The full 1,792-row replay moves to EI.14's operator checklist, where the file exists. (5) the id is actually stamped by `run_batch` and groups a 5-game evaluation.

<!-- autofix-applied: 2026-09-02 -->


### Step EI.6: Persist `wins_parent` and games-played

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.0, EH.10
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.6.

- **Problem:** `PerItemState` records `fitness_score = [wins_candidate, games]` and drops the parent's win count, and the denominator is the **declared** games rather than games played. Persist both **as NEW keys (`wins_parent`, `games_played`)** — do **not** change the arity of `fitness_score` or of the results-row `score`. Both are rendered as fixed-arity pairs by the dashboard (`frontend/src/hooks/useEvolveRun.ts:29` types `fitness_score` as `[number, number] | null`; `EvolutionTab.tsx:886` renders `{item.fitness_score[0]}/{item.fitness_score[1]}`), so widening either list in place silently mislabels wins and games in the Evolution tab — the wire-shape failure class in [`code-quality.md`](../../../.claude/rules/code-quality.md) § "Audit wire shape when storage representation changes". **All four copies of the pool-state field list must change in the same commit** — the dataclass, `to_json`, `from_json`, and the hardcoded pop-tuple in `load_pool_state` (`scripts/evolve.py:928-935`) — or every `--resume` of a run started after this change dies at startup with a single swallowed log line (D-6).

  **`PerItemState` current field list** (`scripts/evolve.py:827-838`, verified 2026-09-02) — the six keys that must stay in lockstep across all four copies: `status` (default `_ACTIVE`), `fitness_score` (`list[int] | None`, the fixed-arity `[wins_candidate, games]` pair), `retry_count` (`int`), `first_evaluated_against` (`str | None`), `last_evaluated_against` (`str | None`), `consecutive_null_diffs` (`int`, added by Phase EJ.2 — the in-repo precedent for an additive defaulted field here). The fourth copy is the hardcoded pop-tuple in `load_pool_state` (`scripts/evolve.py:928-935`), which pops exactly these six and splats whatever remains into the frozen `Improvement`.

- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `scripts/evolve.py`; pool-state round-trip tests; a frontend render assertion in `frontend/src/components/EvolutionTab.test.tsx`
- **Done when:** full suite green; a **full write-then-load round-trip test** (not isolated `to_json`/`from_json` unit tests, which cannot see this failure) proves a pool file written by the new build loads; a pool file written by the **old** build still loads; a fitness row carries both win counts plus games played alongside declared games; and `cd frontend && npm run test:run` green at or above 234 with an assertion that the Evolution tab's wins/games render is **unchanged** — the new values are additive keys, not a widened pair.



### Step EI.7: Patch capture, log-only

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.0, EH.10
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.7.

- **Problem:** Capture the fitness-time patch and the stack-time patch as a **log-only artifact**, behind a new `--capture-patch` flag (default OFF). **The pre-edit content is NOT collected on the default path.** `_collect_candidate_py_content` is guarded at `src/orchestrator/evolve_dev_apply.py:323-325` on `screen_null_diff`, which defaults OFF — so today `content_before` is an empty dict and there is nothing to diff against. Widen that guard to `if screen_null_diff or capture_patch`, so patch capture does not silently depend on an unrelated Phase EJ flag being armed. Dev-apply runs **inside the worker**, so `--capture-patch` genuinely crosses the subprocess boundary and must be threaded through the worker argv, not only bound serially. Store the patch in an applicable form so deterministic replay becomes a follow-on rather than a rebuild. **This is not a decision input**: round 2 deleted round 1's pre-registered threshold rule, because both of its branches point the same way and a clean-apply check cannot separate "same idea, differently implemented" from "different idea". If the captured value crosses the worker boundary it must be added to **both** hand-written `FitnessResult` serializers (D-11).
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `src/orchestrator/evolve_dev_apply.py` (widened guard), `src/orchestrator/evolve.py`, `scripts/evolve.py` (`--capture-patch` argparse + serial binding), `scripts/evolve_worker.py` (argv parse + forward)
- **Done when:** full suite green; a test asserts a patch is captured **with `--capture-patch` ON and `--screen-null-diff` OFF** — the flag-off-sibling case is the one that matters, because a test that arms both would pass over a production no-op; a test asserts the capture is absent-not-crashing for a candidate that changed no files; a test asserts the default path is byte-identical with the flag absent; a test asserts the flag reaches the worker argv; and if the value is ferried, a round-trip test sets it to a non-default value and asserts it survives the worker result file.



### Step EI.8: `--fitness-stop rule|majority`

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.6
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.8.

- **Problem:** The gate-1 early stop is hardcoded to a strict majority and is **not** bucket-neutral (D-8). Add a `played` counter to the fitness callback — it does not exist, and the regression sibling's arithmetic cannot be ported without it (D-9) — then extract a pure `_fitness_stop_locked(...)` helper (the sibling of `_regression_stop_locked` at `src/orchestrator/evolve.py:689`) and add `--fitness-stop rule|majority` defaulting to `majority`, which must be byte-identical to today. Thread it through the **worker argv** as well as the serial path, or it is a silent no-op under concurrency. Correct the false in-source comment claiming the stop saves 2-3 games with no effect on bucket assignment, and the copies of that claim in the viewer wrapper docstring (`scripts/evolve.py:600`) and `docs/archived-plans/evolve-viewer-plan.md:249`.
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `src/orchestrator/evolve.py`, `scripts/evolve.py`, `scripts/evolve_worker.py`, `.claude/skills/improve-bot-evolve/SKILL.md`, `docs/archived-plans/evolve-viewer-plan.md`
- **Done when:** full suite green, which requires the three clauses below to be **mutually satisfiable** — they are only if the property is scoped as stated. (1) An exhaustive soundness property parametrized over **`--fitness-stop rule` ONLY** (not over `majority`), asserting the rule-derived stop never changes a bucket versus full play. Run it against the extracted `_fitness_stop_locked` **helper**, not against `run_fitness_eval` — the primitive does a real `snapshot_current()` tree copy per call, measured at ~34 ms against the regression twin's ~56 µs, so a literal mirror of that test's parametrization would add roughly 25 minutes to the gate that flips every later step DONE. Budget: the new property adds **under 30 seconds**. Pair it with a handful of end-to-end `run_fitness_eval` cases proving the helper is actually wired. (2) A separate **characterization** test pinning the majority stop's known non-neutrality by enumerating the five-game sequences where it collapses the near-miss bucket into fail — so the defect stays codified as a known deviation rather than being silently re-blessed or silently fixed. (3) A test asserting `--fitness-stop majority` is byte-identical to today, and one asserting the flag reaches the worker argv, which the existing `--regression-rule` test pair does **not** cover. **The two existing early-stop tests are clean sweeps and remain valid unmodified** — neither stops one win short, so neither codifies the collapse; this step ADDS a near-miss case they cannot reach. Any diff that modifies either existing test is suspect and must be justified, per [`code-quality.md`](../../../.claude/rules/code-quality.md) § "Audit wire shape".



### Step EI.9: `--stack-size K`

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.6
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.9.

- **Problem:** Cap how many gate-1 winners are stacked into one promotion (`winner_idxs`, `scripts/evolve.py:4559-4562`), default 0 = unlimited = today. Three changes are mandatory together (D-7): exempt deferred winners from `_RETRY_CAP` so a winner that passed gate 1 is not evicted after two more evaluations; stop `promoted_imp_idxs = list(winner_idxs)` at `:4676` from stamping deferred winners as promoted, which would poison the promoted-titles exclusion and the rollback path; and keep deferred winners visible to `_count_active` so the pool does not top up past `--pool-size` every generation.
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/evolve.py`, `.claude/skills/improve-bot-evolve/SKILL.md`
- **Done when:** full suite green; tests assert (1) at `K=0` behaviour is byte-identical; (2) at `K=1` only the rank-1 winner is stacked and the rest are deferred, **not** evicted and **not** marked promoted; (3) a deferred winner survives more than `_RETRY_CAP` generations; (4) `_count_active` includes deferred winners so `delta` is unchanged; (5) an **integration test through `run_loop`** proving the cap reaches the real stack-apply call rather than being unit-tested in isolation.



### Step EI.10: `--stack-model MODEL`

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.0, EH.10
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.10.

- **Problem:** The operator's idea (i), literally. `spawn_dev_subagent` takes a `model` keyword that **no production caller sets**. Bind a `functools.partial` at the single `stack_apply_fn(` call site (`scripts/evolve.py:4609`), leaving the fitness binding at `:4434` untouched. State explicitly in the step whether `scripts/evolve_inject_one.py:211` — the second production caller, invisible to the obvious grep — is in or out of the flag's reach (D-10).
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/evolve.py`, `.claude/skills/improve-bot-evolve/SKILL.md`
- **Done when:** full suite green; an integration test through `run_loop` (template: the existing null-diff integration test) proves the escalated model reaches the **stack** dev-apply and **not** the fitness one; a test asserts the **fitness** dev-apply callable at `scripts/evolve.py:4434` stays the bare identity when the flag is absent, preserving the two existing byte-identical-default assertions. Note `scripts/evolve_worker.py` has **zero** stack involvement (`rg -n "stack"` returns nothing), so this step needs no worker edit and no worker-argv assertion.



### Step EI.11: `--stack-static-gate`

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.9
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.11.

- **Problem:** Per-improvement mypy validates changed files only, so cross-improvement signature drift is invisible. Add a whole-package `mypy --strict` on the new version **before** the `[evo-auto]` commit. Ship it with **two modes**: `--stack-static-gate=observe` records the verdict and the outcome token but never blocks a promotion or drops a rank, and `--stack-static-gate=enforce` reuses the existing cleanup path on failure (never the fragile revert path) with a drop-lowest-rank rule so a repeated static failure cannot livelock the retry ladder. Default is OFF. The observe mode exists because a blocking gate changes stack composition and can suppress a promotion — which suppresses the regression evaluation gated on `promoted_imp_idxs` at `scripts/evolve.py:4857`, removing both a promotion and `--games-per-eval` games from a generation. That is a real perturbation and must not be armed inside another phase's measurement (see EI.14). The gate takes an **injectable callable defaulted off**, mirroring the existing import-check seam, because an unconditional gate would fire in the debug tool and change the operator's ~10-minute iteration cycle (D-10). **The new outcome token literal is `stack-apply-static-fail`**, joining the ten existing members of the `PhaseOutcome` literal at `scripts/evolve.py:957-968` alongside its `stack-apply-import-fail` and `stack-apply-commit-fail` siblings. That exact string must appear identically in `scripts/evolve.py`, the frontend union, `SKILL.md` and the wiki token list, or a graded skill eval fails (D-12).
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/evolve.py`, `src/orchestrator/evolve_dev_apply.py`, `.claude/skills/improve-bot-evolve/SKILL.md`, `docs/wiki/operator-commands.md`
- **Done when:** full suite green; a primitive test mirrors the existing import-fail rollback test's assertion set for a static failure **in `enforce` mode**; a test asserts `observe` mode records a verdict and leaves `promoted_imp_idxs`, the commit and the regression phase untouched; a test asserts the gate does not run when the flag is off, so the four existing stack-apply primitive tests do not shell out to a real mypy against a synthetic tree; the outcome token is present in the skill flag table and the wiki token list, and the two live SKILL.md assertion tests still pass.



### Step EI.12: Frontend token repair + dashboard file counts

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.1, EI.3, EI.11
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.12.

- **Problem:** The `EvolveOutcome` union in `frontend/src/hooks/useEvolveRun.ts` is a hand-maintained duplicate of the Python outcome literal and is **already** missing `screen-null-diff` from Phase EJ.2 — the drift is invisible because the value is consumed as a widened type. Add `stack-apply-static-fail`, repair the existing drift, and add the token to `WARNING_OUTCOMES` in `TimelineList.tsx` beside its import-fail and commit-fail siblings. **Also extend `get_temp_file_counts()`** (`bots/v13/process_registry.py:268-285`): its loop is suffix-filtered to `.jsonl` and `.SC2Replay`, so the dashboard cannot even count the `.log` and trace files this phase creates. Add rows for `logs/*.log` and `logs/traces/`, so the operator can see growth on the Processes tab rather than discovering it on disk.
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `frontend/src/hooks/useEvolveRun.ts`, `frontend/src/components/TimelineList.tsx`, `bots/v13/process_registry.py`
- **Done when:** `cd frontend && npm run test:run` green at or above 234; a test asserts the union matches the Python `PhaseOutcome` literal member-for-member, so this drift cannot recur silently; `npm run build` clean; full `uv run pytest` green with a test asserting the new file-count rows appear in `get_temp_file_counts()`.



### Step EI.13: Producer→consumer smoke gate

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.1, EI.2, EI.3, EI.4, EI.5, EI.6, EI.7
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.13.

- **Problem:** An end-to-end run of the evidence chain with real components, exercising producer→consumer drift that mock-bounded unit tests cannot see — each such test mocks the boundary it would have asserted on. **The ONLY substitution permitted is the `GameMatch` runner inside `_build_match`**, replaced by a fake that writes a real recorded console log to the production path via the production log-name helper. Everything downstream runs unmocked: the log write, the trace parse, `_read_log_tails`, prompt assembly with provenance, the pool-state write-then-load round trip, and both `SelfPlayRecord` replay loops. **No SC2 process launches and no games are played**, which is what keeps this step inside the phase's zero-added-games claim and runnable in a worktree. Do not reach for a real batch: the log file is written only by burnysc2's bot process, so "real batch" would mean a real StarCraft II game, which cannot run in the default suite (`addopts = "-m 'not sc2'"`), cannot finish in seconds, and would add games.
- **Flags:** --reviewers code --isolation worktree
- **Produces:** a chain-smoke test in the standard suite exercising the full evidence chain; recorded output
- **Done when:** the chain completes with no exception; the log files produced are distinct per game and carry the production name shape; the trace parser reads every one; the proposer evidence block is non-empty and carries provenance including the degraded label for an unjoinable tail; the pool file round-trips; and the **tracked fixture** `tests/fixtures/selfplay_results_legacy.jsonl` replays clean through both replay loops. Do NOT assert against `data/selfplay_results.jsonl` — it is gitignored and absent from a worktree. Run it **before** EI.14.



### Step EI.14: Shadow-half observation soak

- **Status:** PENDING
- **Type:** wait
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.11, EI.12, EI.13, EL.7
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.14.

- **Problem:** The evolve loop is unattended, multi-hour, wall-clock-dependent behaviour, and this phase adds capture that runs inside it. Time-dependent failures — unbounded log growth, retention deleting a live file, prompt-budget creep across generations, a fail-open path that silently swallows every trace — are invisible to the EI.13 chain smoke.

  **Host: EL.7 (#279), the 6-hour multi-lineage soak.** Round 1's "ride the flag-OFF shadow half of a queued soak" is not runnable as written — none of EJ.7, EJ.8 or EL.7 has a multi-hour flag-off half. EJ.7's only flags-off leg is a 15-minute smoke, and EJ.8 and EL.7 are each a single flags-on run compared against *historical* controls. EL.7 is chosen because it is the longest queued gate and its recorded observations (generations per lineage, diversity matrix, extinction, orphan processes, commit hygiene) do not include any metric this phase's capture can move.

  **Arming set — log-only, and nothing else.** Arm exactly `--trace-capture`, `--capture-patch`, and the EI.1 log naming and retention, which are unconditional. Do **NOT** arm `--stack-static-gate` in `enforce`, `--stack-size`, or `--fitness-stop rule` on a host run: each changes stack composition or games per generation. `observe` mode of the static gate is permitted because it records without blocking. **EI claims nothing from EL.7's own result table**, and EL.7's record must note the co-tenancy.

  **The command.** EL.7's own invocation with this phase's log-only flags appended. Read the
  runbook's EL.7 section first — it carries two blocking preconditions (register frozen baselines,
  and decide the lineage-registry path, because `--lineages N` does not create N lineages):

  ```powershell
  cd $env:USERPROFILE\dev\Alpha4Gate
  git branch --show-current          # expect master-plan/phase-ev
  git diff --staged --stat           # MUST be empty - evo-auto commits sweep the whole index
  uv run python scripts/evolve.py --lineages 4 --population-cap 3 --hours 6 --generations 0 --fitness-mode both --trace-capture --capture-patch --stack-static-gate observe
  ```

  **Zero added SC2 games**, and that claim is now true rather than assumed: the armed set writes files and records rows, and touches neither gating nor composition. Serialize against the other pending soaks — one evolve run at a time, machine-wide. See [`../operator-gate-runbook.md`](../operator-gate-runbook.md), which also carries EL.7's own two blocking preconditions (the absent baselines registry, and the fact that `--lineages N` does not create N lineages).
- **Produces:** an observation report under `docs/soak-test-runs/`
- **Done when:** the soak completes and the report records: one trace file per game and worker, not one per version and seat; a bounded `logs/` directory after retention, with no file from the live run unlinked; the proposer evidence block naming each tail's game, seat, opponent and outcome for at least one refresh generation; every fitness row carrying a non-null parent win count and evaluation id; at least one whole-package type-check verdict recorded in **observe** mode (advisory, non-blocking — not a blocked promotion); the gate-1 accept rate reported with an exact binomial p-value against the 0.5 null; and the full `data/selfplay_results.jsonl` replaying clean through both replay loops in the main project tree, where the file exists. **Claims survival and instrumentation only** — no throughput verdict, which would need a paired control.

---


### Step EI.0: Approve evidence-layer defaults and synchronize issues

- **Status:** HELD — product decisions and issue synchronization pending
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.0.

EI remains planned, not product-approved by this cleanup. Review/redline the existing choices and mint its fourteen missing issue IDs before implementation. The historical mesh freeze was lifted; it is not a present blocker. Do not downgrade EI.1/EI.5/EI.6 deep review. Resolve reach of stack-model into evolve_inject_one explicitly. This planning decision is independent of the truncated idea (ii).


### Step EL.7: Multi-hour observation soak

- **Status:** PENDING
- **Type:** wait
- **Issue:** #279
- **Depends on:** EH.10
- **Source:** [evolution-lines-plan.md](../archived-plans/evolution-lines-plan.md); original EL.7.

- **Problem:** Run 2–3 lineages under a multi-hour budget
  (`--lineages 4 --population-cap 3 --hours 6 --generations 0 --fitness-mode both`, after seeding four actual registry records) and
  observe the autonomous loop: lineages diverge on fingerprint, ≥1
  extinction event fires (or is explicitly documented as not-triggered-by-design; multi-lineage scheduling must still be observed),
  baseline-fitness trends are sane, no orphan SC2 processes, EVO_AUTO commit
  hygiene holds. This is the autonomous-behavior observation step — the loop
  runs unattended over wall-clock time, so time-dependent failures
  (scheduler drift, registry write races, extinction thrash) are invisible
  to unit tests.
- **Produces:** soak record under
  `docs/soak-test-runs/evolution-lines-soak-<ts>.md`.
- **Done when:** ≥2 generations per lineage complete; diversity matrix shows
  non-trivial separation; extinction logic exercised at least once or
  documented as not-triggered-by-design; zero orphan processes; commit log
  shows clean `[evo-auto]` rows.



### Step EJ.7: Flags-on smoke gate (real SC2)

- **Status:** PENDING
- **Type:** operator
- **Issue:** #288
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [evolve-judging-plan.md](../archived-plans/evolve-judging-plan.md); original EJ.7.

- **Problem:** Verify the six flags compose end-to-end on real infrastructure before an overnight soak. Register 2 frozen anchors (e.g. v10, v13) in `data/baselines.json` via the EL baselines CLI, then run one short flags-on evolve: `uv run python scripts/evolve.py --pool-size 3 --games-per-eval 3 --hours 0.75 --generations 0 --priors-exclude-promoted --screen-null-diff --regression-rule one-sided --panel-floor --refresh-dedup --budget-fit --fitness-mode both`. Inspect `data/evolve_results.jsonl` for the new row types where triggered, confirm no crash, no orphaned `bots/cand_*` dirs, and pointer integrity. Then run one 15-minute defaults-off smoke and confirm behavior matches a pre-EJ run (no new row types, no flag effects).
- **Produces:** smoke evidence appended to the run log under `docs/soak-test-runs/`; pass/fail verdict per checklist item
- **Done when:** flags-on run completes without crash; every triggered EJ mechanism left its audit row; defaults-off run shows zero behavior change; verdicts recorded.



### Step EJ.8: Overnight flags-on A/B validation soak

- **Status:** PENDING
- **Type:** wait
- **Issue:** #289
- **Depends on:** EJ.7, EH.6, EH.7
- **Source:** [evolve-judging-plan.md](../archived-plans/evolve-judging-plan.md); original EJ.8.

- **Problem:** The investigation's numbers (null-rollback 50%→18.75%, ~5.9 games saved per avoided re-proposal, reclaimed final-generation games) are analytic; validate them against a real overnight run before any default flips. Run one standard 4h flags-on soak (`/improve-bot-evolve` flags as in EJ.7 minus the reduced sizes) and compare against the most recent flags-off soaks on: regression-rollback rate, `screen-null-diff`/`pool_dedup` catch counts, games per generation, generations completed, and promotions. Record the comparison table in the soak log; recommend (do not apply) default flips.
- **Produces:** soak report under `docs/soak-test-runs/` with the A/B comparison table + default-flip recommendation
- **Done when:** soak completes; comparison table filled from `evolve_results.jsonl` + run state; recommendation recorded. NOT gated on the soak showing improvement — a negative result is a valid outcome that blocks default flips.



### Step EV.4: Real-SC2 smoke gate

- **Status:** PENDING
- **Type:** operator
- **Issue:** #294
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [evolve-viewer-plan.md](../archived-plans/evolve-viewer-plan.md); original EV.4.

- **Problem:** Prove the wiring works against real SC2 on Windows before any long run. On a Windows box with `uv sync --extra viewer` and SC2 installed, run a minimal evolve: `uv run --extra viewer python scripts/evolve.py --pool-size 2 --games-per-eval 3 --hours 0.5 --no-commit --viewer`. Confirm (a) the themed container opens with a background and stats bar; (b) during the fitness phase both SC2 clients reparent into the two panes and the overlay shows `cand_<hash>` vs the parent version; (c) the container idles gracefully (placeholders, no freeze) during the Claude pool-generation and stack-apply phases; (d) closing the container mid-run leaves the evolve process alive and the run continuing headlessly to its budget — verify via `data/evolve_results.jsonl` gaining rows *after* the window closed. Then run the same command **without** `--viewer` for 10 minutes and confirm behavior matches a pre-EV run (raw SC2 windows, identical row shapes in `evolve_results.jsonl`). Finally confirm `uv run python scripts/evolve.py --viewer --concurrency 2` exits immediately with the guidance message, and that on a machine without the extra (`uv run python ... --viewer`, no `--extra viewer`) the run starts headless with a WARNING rather than crashing. **(e) Exercise the operator acceptance condition end-to-end (§1).** This item is the phase's acceptance gate, not a spot-check. Launch from the dev-observatory `run-evolution` button itself if the control plane is up; otherwise run the identical command the registry holds, `.\scripts\launch-evolve.ps1` (bare — let it use its own `-Hours 4` default, exactly as the button invokes it). Confirm: (e1) a **real SC2 match renders inside the themed container** — not merely that the window opened; wait for it, because pool generation runs a Claude prompt before the first mirror-calibration game, so first paint can be several minutes out and a short `-Hours` override may end the run before a game ever starts; (e2) the dashboard opens on the Evolution tab and shows the run; (e3) the already-running-evolve guard fires on a second invocation instead of starting a competing run; (e4) closing the container leaves both the run and the dashboard alive. **(e) is a real, un-flagged run.** The launcher passes no `--no-commit` (`$evolveCmd` at [`scripts/launch-evolve.ps1:72`](../../scripts/launch-evolve.ps1#L72) is `uv run --extra viewer python scripts/evolve.py --hours $Hours --viewer`), and its own header cautions that evolve "creates new bot versions, flips `bots/current`, and AUTO-COMMITS `[evo-auto]` promotions". So this run **can promote a new version and auto-commit `[evo-auto]` to whatever branch is checked out — here `master-plan/phase-ev`**, which §0 says cannot merge to `master` until `onbrand-pilot` lands. Expect that commit; do not "fix" it by adding `--no-commit`, because the whole point of (e) is to exercise the dev-observatory button exactly as the registry invokes it, with no arguments (D16, changed 2026-08-10). Stop the run once (e1)-(e4) are observed — the full four hours is EV.5's job, not this one. **Stop it by closing the evolve CONSOLE window; that is the only working stop gesture.** Closing the themed container only DETACHES the display and the run keeps going headless (D-1); the dashboard's Stop button is NOT wired to the runner (§8, no reader for `data/evolve_run_control.json`). And **never press Ctrl+C on a `--viewer` run**: the evolution loop runs off the main thread, so burnysc2's SIGINT kill-switch is never armed and Ctrl+C can leave ORPHANED SC2 processes that [`.claude/rules/bot-runtime.md`](../../.claude/rules/bot-runtime.md) forbids killing by hand. Authoritative wording: `scripts/launch-evolve.ps1` header ("HOW TO STOP THE RUN", L25-40) and `scripts/evolve.py::_VIEWER_CTRL_C_WARNING`.
- **Produces:** smoke evidence appended to the run log under `docs/soak-test-runs/`; pass/fail verdict per checklist item (a)-(e) plus the two negative cases (concurrency rejection, missing-extra degradation)
- **Done when:** all five positive observations confirmed, **with (e1) — a real match visibly rendered in the container from the launcher — as the hard gate**; the defaults-off run shows zero behavior change; both rejection/degradation cases behave as specified; and verdicts are recorded. A failure here blocks EV.5. If (e1) does not happen, the phase has not met its acceptance condition regardless of how many unit tests pass.



### Step EV.5: Attended-then-detached observation run

- **Status:** PENDING
- **Type:** wait
- **Issue:** #295
- **Depends on:** EV.4, EH.6, EH.7, EV.6
- **Source:** [evolve-viewer-plan.md](../archived-plans/evolve-viewer-plan.md); original EV.5.

- **Problem:** Time-dependent failures in a pygame loop + daemon thread riding a multi-hour run are invisible to EV.4's 30 minutes (D-7). Launch a standard 4-hour run via the production launcher (`.\scripts\launch-evolve.ps1 -Hours 4`), watch the first generation attended, then close the container and let the remainder run detached. **Same real-run caveat as EV.4 item (e):** the launcher passes no `--no-commit`, so this run may promote a new version and auto-commit `[evo-auto]` to the current branch (`master-plan/phase-ev`) — expected, not a defect (D16). If the run must be ended before its 4-hour budget, close the evolve CONSOLE window: closing the container only detaches, the dashboard Stop button is not wired, and Ctrl+C is forbidden under `--viewer` (full rationale in EV.4 item (e)). Afterwards compare against the most recent pre-EV soak on: generations completed, promotions, games per generation, and crash rows in `data/evolve_crashes.jsonl`. Specifically look for viewer-attributable divergence — a lower generation count, HWND-attach stalls (the 15s `GAME_START_HWND_TIMEOUT_SECONDS` blocks the pygame thread per game, [`container.py:94`](../../src/selfplay_viewer/container.py#L94)), unbounded process memory growth, or any evidence the post-close latch failed.
- **Produces:** observation report under `docs/soak-test-runs/` with the survival verdict and, if a paired control was run, the throughput comparison
- **Done when:** the 4-hour run completes; the run continued past the container close; and the report records a **survival verdict** — no crash, no dead run, no unbounded memory growth, no evidence the post-close latch failed. **Throughput is NOT claimed from an unpaired comparison.** Comparing against "the most recent pre-EV soak" confounds viewer overhead with a different bot version, a different pool, and different machine load; per [`.claude/rules/measurement-validity.md`](../../../.claude/rules/measurement-validity.md) (match measurement scope to decision scope), that comparison cannot answer "did `--viewer` cost throughput". To claim throughput, run the paired control: 2 hours `--viewer` then 2 hours headless resumed from the same state in the same session, and compare those. Absent the paired control, record generations/promotions as **context, explicitly not a verdict**.

---


### Step EV.6: Refresh viewer stop instructions after EH reader lands

- **Status:** PENDING
- **Type:** code
- **Issue:** #302
- **Depends on:** EH.4, EH.5, EH.6
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EV.6.

Sibling issue #302 owns the five legacy not-wired claims. Update live operator/wiki instructions to the implemented stop behavior only after EH.4/EH.5; preserve archived pre-change evidence. EV.5 requires EH.6. Viewer close still detaches, and never use Ctrl+C for the threaded evolve viewer. EH P-7 prevents rewriting old gate evidence.


### Step M1: Train 3 cycles + measure early-game reward variance

- **Status:** PENDING
- **Type:** operator
- **Issue:** #170
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [phase-d-build-plan.md](../archived-plans/phase-d-build-plan.md); original M1.

**Problem:** Train 3 PPO cycles with `use_build_order_reward: true` against the current curriculum, then aggregate early-game reward variance and difficulty-3 win rate to test the Phase D premise.

**Commands.**

```powershell
# Restart evolve daemon (paused during automated run, see preflight) — optional if M1 runs immediately:
# uv run python scripts/evolve.py --hours 0  # do NOT restart until after M1 finishes

# Train 3 cycles (foreground or `Start-Process` background):
uv run python -m bots.current.runner --serve --daemon --decision-mode hybrid
# ...wait for 3 cycles to complete (~2-4h depending on game length)...

# Early-game (first 5 min) reward std-dev — inline measurement, no script needed.
# Run BEFORE flipping the flag (baseline) and again AFTER 3 cycles with flag on:
uv run python -c @"
import json, statistics
from pathlib import Path
logs = Path('bots/v13/data/reward_logs').glob('game_*.jsonl')
rewards = []
for log in logs:
    for line in log.read_text().splitlines():
        if not line: continue
        rec = json.loads(line)
        if rec.get('game_time', 0) <= 300:
            rewards.append(rec['total_reward'])
print(f'n={len(rewards)} mean={statistics.mean(rewards):.4f} stdev={statistics.stdev(rewards):.4f}')
"@

# Deterministic eval (20 games at difficulty 3):
uv run python scripts/evaluate_model.py --difficulty 3 --games 20
```

**What to look for.**

| Metric | Pass | Fail |
|---|---|---|
| Early-game (first 5 min) reward std-dev | ≥ 30% drop vs pre-D.6 baseline | < 30% drop or no change |
| Difficulty-3 win rate (20 games) | ≥ baseline WR | regression |

If variance drops but WR regresses, tune `build_order_reward_alpha` down (try 0.5, then 0.25) and re-run before snapshotting. If variance does not drop after 3 cycles, kill per §7.

**Done when.** Results appended to `docs/soak-test-runs/phase-d-M1-<TS>.md` with both metrics and a snapshot/kill decision. Pre-D.6 baseline numbers cited explicitly (rerun `reward_aggregator` on a recent pre-D.6 log for the comparison).




### Step M2: Snapshot to `vN+1` on promotion

- **Status:** PENDING
- **Type:** operator
- **Issue:** #171
- **Depends on:** M1
- **Source:** [phase-d-build-plan.md](../archived-plans/phase-d-build-plan.md); original M2.

**Problem:** If M1 gates passed, snapshot the new stack and run cross-version Elo to validate promotion.

**Commands.**

```powershell
# Snapshot from bots/current/ (= v13) into the next auto-numbered vN+1:
uv run python scripts/snapshot_bot.py
# Note the new version name printed by the script (likely "v14"). Use it below.

# Update bots/current/current.txt to point at the new version IF this is the new baseline.
# Skip this if Phase D is staying behind the flag and current should keep pointing at v13:
# Do not manually repoint current: bare snapshot resolves the actual version and performs its pointer update.

# Cross-version Elo via the compare subcommand (20 games new-vs-v13):
uv run python scripts/ladder.py compare v14 v13 --games 20
```

**What to look for.**

| Metric | Pass | Fail |
|---|---|---|
| Elo gain vs v13 | ≥ +10 over 20 self-play games | < +10 or regression |
| WR vs SC2 AI diff 3 (sanity) | hold vs v13 | regression |

**Done when.** `bots/v14/` exists with manifest lineage; `check_promotion()` passes ([src/orchestrator/ladder.py](../../src/orchestrator/ladder.py)); ladder table shows new entry.


---

Canonical correction from the operator runbook: use the actual emitted new version and previous version in the ladder command; v14/v13 are historical examples, never reserved identities. Never pass --from current (that is the four-file shim). Snapshot only after M1 passes; preserve source stack and reward backup.


### Step 6.1: `/improve-bot-advised --opponent vN` curriculum flag

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [phase-6-build-plan.md](../archived-plans/phase-6-build-plan.md); original 6.1.

**What to build.** Today `/improve-bot-advised --self-improve-code`
validates trainee runs against SC2 built-in AI. Phase 6 swaps in a
prior `bots/vN/` opponent via subprocess self-play. Operator passes
`--opponent v5` (or any registered version). Curriculum logic: pin the
opponent until trainee clears +N Elo, then advance to next opponent in
the registry. If `--opponent` is not specified, fall back to current
SC2-AI behavior (no breaking change).

**Existing context.**
- `.claude/skills/improve-bot-advised/SKILL.md` — Phase 4 dispatch loop
  picks an improvement and runs validation games. The current
  validation invocation uses SC2-AI difficulty.
- `src/orchestrator/selfplay.py::run_batch(p1, p2, games, map_name, …)`
  — Phase 3 primitive that boots two `python -m bots.vN` subprocesses
  per game.
- `src/orchestrator/ladder.py::get_elo(version)` + `LadderEntry`
  contract from Phase 4 — read current Elo to drive curriculum
  advancement.
- `src/orchestrator/registry.py::list_versions()` — enumerate
  registered opponents to walk the curriculum.

**Files to modify/create.**
- `.claude/skills/improve-bot-advised/SKILL.md` — accept `--opponent vN`
  flag in Phase 0 bootstrap; thread through to Phase 4 validation; fall
  through to SC2-AI if absent.
- `src/orchestrator/curriculum.py` (NEW) —
  `pick_opponent(current_elo, opponents, advance_threshold) -> str`.
- `tests/test_curriculum_opponent.py` (NEW) — synthetic Elo trajectory
  walks 3 opponents.

**Done when.**
- `--opponent v5` flag accepted; SKILL.md documents the flag.
- Curriculum advances correctly: trainee at Elo X+10 vs pinned opponent
  → next opponent gets pinned automatically.
- Backwards-compat: omitting `--opponent` runs the existing SC2-AI eval
  unchanged.
- `tests/test_curriculum_opponent.py` covers: pinning, advancement on
  threshold cross, exhaustion of opponent list (graceful stop).
- Dashboard "Loop" tab shows current opponent (read from
  `bots/current/data/advised_run_state.json`).

**Flags (recommended for `/build-step`).** `--reviewers code --isolation worktree`


**Produces.** Updated SKILL.md; new `curriculum.py`; new tests; small
addition to Loop dashboard tab.


### Step 6.2: PFSP-lite pool sampling (stretch)

- **Status:** DEFERRED — optional PFSP stretch
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** 6.1
- **Source:** [phase-6-build-plan.md](../archived-plans/phase-6-build-plan.md); original 6.2.

**What to build.** Generalize Step 6.1's "pin one opponent" to
"sample from top-K opponents using PFSP-lite weights." Trainee plays
mixed-style validation games against a pool instead of single
opponent. AlphaStar-lite league at single-box scale.

**Existing context.**
- Phase 3 already shipped the PFSP-lite sampler in
  `src/orchestrator/selfplay.py` (`--sample pfsp --pool v0,v1,v2,v3`).
- Step 6.1's `pick_opponent` returns a single string; Step 6.2
  generalizes to `pick_opponent_pool` returning a sampler.
- Promotion gate must track WR per opponent in pool, not aggregate
  (so a strong-vs-weak pool doesn't artificially inflate a single
  number).

**Files to modify/create.**
- `.claude/skills/improve-bot-advised/SKILL.md` — accept
  `--opponent-pool top-K` flag (mutually exclusive with `--opponent`).
- `src/orchestrator/curriculum.py` — add `pick_opponent_pool(top_k,
  ladder, sampler="pfsp")` returning a callable that draws an
  opponent per game.
- `tests/test_curriculum_opponent.py` — extend with pool-sampling
  cases.

**Done when.**
- `--opponent-pool top-3` flag accepted; sampler draws across top-3
  weighted by PFSP-lite (lower WR vs trainee → higher weight).
- Per-opponent WR tracked in `advised_run_state.json` (not just
  aggregate).
- Promotion gate fires only if trainee beats EACH opponent in the
  pool by margin (configurable; default ≥ +5 Elo per opponent).

**Flags (recommended).** `--reviewers code --isolation worktree`


**Produces.** SKILL.md update; `curriculum.py` extension; test
coverage; possibly minor tweak to Phase 4 promotion gate to handle
pool-mode trainees.


### Step 6.3: Demonstrate cross-version PPO improvement

- **Status:** PENDING
- **Type:** wait
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** 6.1
- **Source:** [phase-6-build-plan.md](../archived-plans/phase-6-build-plan.md); original 6.3.

After 6.1 (and 6.2 only if chosen), run the multi-hour first cycle: vN → vN+1 → vN+2 with increasing Elo and at least one higher SC2-AI difficulty than v0. Stop/re-audit curriculum and reward if ladder cycles across three snapshots. This is PPO-driven and independent of discrete evolve; exclusive use of current working tree. Phase7 establishes there is no advised_run_state.json; re-anchor any future telemetry writer, do not pretend the older Phase6 path is implemented.


### Step 7.6: End-to-end advised staleness/soak validation

- **Status:** PENDING
- **Type:** operator
- **Issue:** #280
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [phase-7-build-plan.md](../archived-plans/phase-7-build-plan.md); original Phase7 Step6.

- **Problem:** Run the full loop unattended to confirm staleness detection → soak scheduling →
  recovery works end-to-end against the live system. This is operator/wall-clock work (a multi-hour
  soak), not a `/build-step` code change — hence a separate `Type: operator` step (per §22; the
  original draft buried a 1h live soak inside a code step's Done-when).
- **Done when (operator runs + reports):**
  1. With reward rules freshly edited, run `/improve-bot-advised --self-improve-code --hours 8`.
  2. Staleness detected on iteration 1 (reward edit newer than newest checkpoint).
  3. A `soak` improvement is emitted and runs to completion (2–4h, clamped ≤ 50% of budget).
  4. Post-soak deterministic eval shows a measurable Elo/WR delta (sign doesn't matter — proves
     the path works end-to-end).
  5. Loop resumes and finishes the remaining budget without starving other iterations.

**Operator commands.**
```powershell
# (edit a reward rule first so the policy is provably stale, then:)
uv run python -m orchestrator.staleness <current-version>   # sanity: is_stale should be true
# then the full run:
/improve-bot-advised --self-improve-code --hours 8
```

**What to look for.**

| Check | Expected |
|---|---|
| Iteration 1 staleness | `is_stale: true`, reason cites reward-edit-after-checkpoint |
| Soak emitted + clamped | One `type: soak`, duration ≤ min(remaining/2, 4h) |
| Daemon recovery | API-only backend restarts cleanly after the soak; `data/` intact |
| Budget | Soak-type time ≤ 50% of total `--hours` |
| End-to-end | Post-soak eval delta is non-zero; loop completes remaining budget |


### Step E.0: Resolve action-space cardinality before implementation

- **Status:** HELD — conflicting action-space specification
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original E.0.

The source enumerates ATTACK main/natural/third (3), EXPAND own_natural/own_third/own_fourth (3), and DEFEND/FORTIFY/OPENING/LATE_GAME singleton states (4), while requiring Discrete(12), 12 ActionTarget entries and range 0..11. These total ten legal pairs. Decide ten legal actions versus twelve with explicitly defined extra/reserved slots and masking; then update migration/teacher/checkpoint contracts together. No additional actions are invented by reconciliation. Phase F is cut; target collapse means revisit signal or defer, not reactivate F.


### Step E.1: Structured `(strategic_state, target)` action space

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** E.0
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original E.1.

**What to build.** Replace flat `Discrete(6)` action space with
structured `(strategic_state, target)` pairs. Mapping:
- `ATTACK` → {main, natural, third}
- `EXPAND` → {own_natural, own_third, own_fourth}
- `DEFEND`, `FORTIFY`, `OPENING`, `LATE_GAME` → no target dim (use sentinel)

Effective space = 12 actions. Update `ACTION_TO_STATE` in
`decision_engine.py` to a new `ActionTarget` enum that captures both
dimensions, and update `SC2Env.action_space` to `Discrete(12)`.

**Existing context.**
- `bots/current/decision_engine.py` — `ACTION_TO_STATE: list[StrategicState]`
  is the canonical 6-way mapping. Used by tests, training, and the
  rules policy.
- `bots/current/learning/environment.py` — `SC2Env.action_space =
  Discrete(6)` today. Action index is fed through `ACTION_TO_STATE`.
- `bots/current/learning/rules_policy.py` — `rule_actions_for_batch`
  returns 6-way action indices for the KL teacher.
- `bots/current/data/diagnostic_states.json` — fixed snapshots with
  expected actions (currently 6-way ints).

**Files to modify/create.**
- `bots/current/decision_engine.py` — new `ActionTarget` enum, new
  `ACTION_TO_STATE` (12-way) and helper to decompose into
  `(strategic, target)`.
- `bots/current/learning/environment.py` — `Discrete(12)` action
  space.
- `bots/current/data/diagnostic_states.json` — expand expected actions
  to 12-way (or migrate per Step E.5).

**Done when.**
- `ActionTarget` enum has 12 entries with stable ordinal indices.
- `decision_engine.action_to_target(idx)` returns
  `(StrategicState, TargetEnum | None)`.
- `SC2Env.action_space.n == 12`.
- All existing tests still pass (action-space size assertions updated
  where needed).

**Flags (recommended).** `--reviewers code --isolation worktree`


**Produces.** Updated `decision_engine.py`, `environment.py`,
`diagnostic_states.json`.


### Step E.2: Custom `ActorCriticPolicy` with two sequential softmax heads

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** E.1
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original E.2.

**What to build.** Custom SB3 `ActorCriticPolicy` subclass with two
softmax heads:
- `p(strategic)`: 6-way distribution over strategic states.
- `p(target | strategic)`: conditional 3-way distribution over targets
  (gated by which strategic state was sampled).

Sample order: strategic head first, then target head conditioned on
the sampled strategic. Effective joint distribution covers all 12
actions but with explicit factorization (autoregressive).

**Existing context.**
- SB3 `ActorCriticPolicy` is the parent class; current bot uses
  `MlpPolicy` (default). Custom subclass goes in
  `bots/current/learning/autoreg_policy.py` (NEW).
- Phase A uses `MlpLstmPolicy` for some configs — autoreg subclass
  must work alongside or as alternative; configurable via
  `hyperparams.json::policy_type: "autoreg"`.

**Files to modify/create.**
- `bots/current/learning/autoreg_policy.py` (NEW) — custom policy
  subclass with two heads.
- `bots/current/learning/trainer.py` — pick `autoreg_policy` when
  `hyperparams.policy_type == "autoreg"`.
- `bots/current/data/hyperparams.json` — add example
  `policy_type: "autoreg"` (off by default).

**Done when.**
- `autoreg_policy.AutoregPolicy` instantiates without error.
- Forward pass returns valid joint distribution over 12 actions.
- Gradients flow to both heads (verified in unit test).
- Target head correctly gated by strategic head choice (target probs
  are zero for invalid combos).

**Flags (recommended).** `--reviewers code --isolation worktree`


**Produces.** New policy module; trainer hook; hyperparam example.


### Step E.3: DB column `action_space_version`

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** E.1
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original E.3.

**What to build.** Add `action_space_version INT` column to
`bots/current/data/training.db` schema (transitions table). Values:
`1` = legacy 6-way, `2` = Phase E 12-way. Migration script that flags
all existing rows as version 1.

**Existing context.**
- `bots/current/learning/database.py` — `TrainingDB` class, `_STATE_COLS`,
  `_LATER_ADDED_COLS` for schema migration. Phase B already
  established this pattern.

**Files to modify/create.**
- `bots/current/learning/database.py` — add column + migration; add
  helper `get_action_space_version(transition_id)`.
- `tests/test_database_action_space_version.py` (NEW) — verify
  migration adds default `1`, new inserts can use `2`.

**Done when.**
- Existing transitions table gains `action_space_version` defaulting
  to `1`.
- New inserts respect provided version.
- `tests/test_database_action_space_version.py` covers fresh DB,
  migrated DB, and version filtering on read.

**Flags (recommended).** `--reviewers code --isolation worktree`


**Produces.** Updated schema + migration; new test file.


### Step E.4: Cascade target picking into `_compute_next_state` + rules KL teacher

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** E.1, E.2
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original E.4.

**What to build.** Update the rule engine to pick BOTH a strategic
state AND a target (matching the new 12-way action space). Cascade
into the KL teacher so `rule_actions_for_batch` returns 12-way.

**Existing context.**
- `bots/current/decision_engine.py::_compute_next_state` — currently
  returns `StrategicState`. Phase E needs it to also return `(state,
  target)`.
- `bots/current/learning/rules_policy.py::rule_actions_for_batch` —
  consumes `_compute_next_state`'s output, returns action indices for
  the KL teacher loss.
- Target picking heuristics: ATTACK → enemy main if scouted, else
  natural; EXPAND → own_natural if not built, else own_third, etc.

**Files to modify/create.**
- `bots/current/decision_engine.py` — extend `_compute_next_state` to
  return `ActionTarget`; add target-picking heuristics.
- `bots/current/learning/rules_policy.py` — return 12-way action
  indices.
- `tests/test_decision_engine_targets.py` (NEW) — verify
  target-picking heuristics for each strategic state under various
  game snapshots.

**Done when.**
- `_compute_next_state(snap)` returns `ActionTarget` enum value.
- `rule_actions_for_batch(batch)` returns ints in `[0, 11]`.
- Target heuristics covered by tests (own_natural picked when
  available, else own_third, etc.).

**Flags (recommended).** `--reviewers code --isolation worktree`


**Produces.** Updated decision engine + rules policy; new test file.


### Step E.5: 6-way → 12-way DB migration with target inference

- **Status:** PENDING
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** E.3, E.4
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original E.5.

**What to build.** Migrate existing 6-way DB rows to 12-way: for each
old transition, infer target from game log when possible, else
default to `*_main`. Reject loading old checkpoints that were trained
on 6-way unless `--force` is passed (mismatched action-space loads
would silently produce garbage).

**Existing context.**
- `bots/current/data/training.db` has thousands of legacy
  transitions. Game logs in `logs/` may have target info via the
  ATTACK target position; otherwise default.
- `bots/current/learning/checkpoints.py` loads SB3 checkpoints; needs
  action-space compatibility check.

**Files to modify/create.**
- `bots/current/learning/database.py` — `migrate_action_space(db,
  log_dir)` function.
- `bots/current/learning/checkpoints.py` — reject mismatched-action-space
  loads unless `--force`.
- `scripts/migrate_phase_e.py` (NEW) — operator-run script that
  invokes the migration.
- `tests/test_action_migration.py` (NEW) — synthetic 6-way DB
  migrates cleanly; old checkpoints rejected explicitly.

**Done when.**
- `scripts/migrate_phase_e.py --db bots/current/data/training.db
  --logs logs/` produces a migrated DB with all rows at version 2.
- Inferred targets match game-log evidence where available; default
  `*_main` elsewhere.
- Loading a 6-way checkpoint into a 12-way trainer raises clear
  error unless `--force`.

**Flags (recommended).** `--reviewers code --isolation worktree`

exists).

**Produces.** Migration function; checkpoint compat check;
operator script; test file.


### Step E.6: Snapshot to `vN+1` on promotion

- **Status:** PENDING
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** E.5
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original E.6.

**What to build.** Operator step: train 2-3 cycles with the autoreg
policy on the migrated DB, run deterministic eval at difficulty 3,
run cross-version Elo self-play vs prior `vN`. If gates pass, snapshot
`bots/current/` → `bots/vN+1/`.

**Existing context.**
- `scripts/snapshot_bot.py` from Phase 2 handles the snapshot
  mechanics.
- Phase 4 promotion gate (`check_promotion`) handles the Elo + WR
  validation.

**Files to modify/create.**
- None (operator step). Validation per "Done when".

**Done when.**
- Replay inspection shows meaningful target differentiation (not
  always `enemy_main`); document a sample.
- WR holds at difficulty 3 (≥ baseline).
- Elo gain ≥ +10 over 20 self-play games vs prior `vN`.
- `bots/vN+1/` snapshot created via `snapshot_bot.py`.

**Flags.** N/A (operator).


**Produces.** New `bots/vN+1/` directory; promotion log entry.


### Step K.1: Pool metadata schema + file

- **Status:** PENDING
- **Type:** code
- **Issue:** #223
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [phase-k-build-plan.md](../archived-plans/phase-k-build-plan.md); original K.1.

- **Problem:** Define `data/pool_metadata.json` schema (§5). Write the
  schema as Python TypedDict in `src/orchestrator/pool_metadata.py`.
  Add load/save helpers with file-locking (concurrent evolve runs
  could write simultaneously).
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** `src/orchestrator/pool_metadata.py`,
  `tests/test_pool_metadata.py`, `data/pool_metadata.json` (empty
  initial).
- **Done when:** Round-trip load/save tests pass; file-locking test
  passes (two writers serialize correctly); mypy strict + ruff
  clean.


### Step K.2: Manifest extension

- **Status:** PENDING
- **Type:** code
- **Issue:** #224
- **Depends on:** K.1
- **Source:** [phase-k-build-plan.md](../archived-plans/phase-k-build-plan.md); original K.2.

- **Problem:** Add `themes` and `notable_moments` to manifest schema.
  `snapshot_current()` writes `themes: []` and `notable_moments: []`
  by default. Add `set_themes()` and `add_notable_moment()` helpers
  in `registry.py`.
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** Updates to `src/orchestrator/registry.py`,
  `src/orchestrator/snapshot.py`, `tests/test_registry.py`,
  `tests/test_snapshot.py`.
- **Done when:** Snapshotting `v0 → v1` produces a manifest with the
  new fields; reading an OLD manifest (no `themes` field) doesn't
  crash (default `[]`).



### Step K.3: Build-order signature

- **Status:** PENDING
- **Type:** code
- **Issue:** #225
- **Depends on:** K.2
- **Source:** [phase-k-build-plan.md](../archived-plans/phase-k-build-plan.md); original K.3.

- **Problem:** `extract_build_order_signature(game_id) ->
  list[tuple[str, int]]` — returns the first 5 minutes of unit/structure
  production as `[(unit_name, count), ...]` ranked by frequency. Read
  from `transitions` or `decision_audit.json`. Auto-theme function:
  `infer_themes(signature) -> list[str]` with 3-5 hardcoded rules
  (skytoss, ground, cheese-rush, defensive, tech-rush).
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** `src/orchestrator/build_order_signature.py`,
  `src/orchestrator/auto_theme.py`,
  `tests/test_build_order_signature.py`,
  `tests/test_auto_theme.py`.
- **Done when:** Signature extraction tests pass on a fixture game
  ID; auto-theme produces sensible labels on 5 hand-crafted
  signatures.



### Step K.4: API endpoint + dashboard surface

- **Status:** PENDING
- **Type:** code
- **Issue:** #226
- **Depends on:** K.3
- **Source:** [phase-k-build-plan.md](../archived-plans/phase-k-build-plan.md); original K.4.

- **Problem:** `GET /api/pool/metadata` returns merged
  registry+ladder+metadata view. Decide tab-vs-extend during this
  step (read LadderTab; if extension is clean, extend; otherwise
  new Pool tab as 11th tab). Frontend hook `usePoolMetadata.ts` with
  `cacheKey: 'pool-v1'`.
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** Backend endpoint, `frontend/src/hooks/usePoolMetadata.ts`,
  dashboard surface (tab or extension).
- **Done when:** Endpoint returns valid JSON for `v0`-only pool;
  frontend renders without console errors;
  `npm --prefix frontend run test` passes; `npm --prefix frontend
  run build` passes.



### Step K.5: Operator smoke gate

- **Status:** PENDING
- **Type:** operator
- **Issue:** #227
- **Depends on:** K.4
- **Source:** [phase-k-build-plan.md](../archived-plans/phase-k-build-plan.md); original K.5.

- **Problem:** Run `python -m orchestrator list`, verify themes +
  notable_moments fields surface; manually tag `v0` with
  `["baseline"]` theme via `set_themes("v0",
  ["baseline"])`; verify dashboard updates; verify NL-prompt-shaped
  query (`themes contains "baseline"`) returns `v0`. No real
  matchups yet — that's Phase L.
- **Produces:** Manual verification.
- **Done when:** All smoke checks green.



### Step G.1: Extract race interfaces

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** 6.3
- **Source:** [phase-g-build-plan.md](../archived-plans/phase-g-build-plan.md); original G.1.

RaceConfig, ProductionAdapter, MicroAdapter, FeatureSpec and RewardTemplate; no Protoss behavior regression. Reconcile original v0-refactor proposal with frozen-version rule: build on a new working version, never rewrite frozen history. Kill/defer if >5% tests break or >500 adapter lines needed.


### Step G.2: Implement and validate Zerg

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** G.1
- **Source:** [phase-g-build-plan.md](../archived-plans/phase-g-build-plan.md); original G.2.

Larva/inject, creep, Overlords, morph roster, Bile/Fungal/Burrow/Abduct, hatch/pool/12-pool builds, rewards/features. Add evolve --race and per-race parent chain, same-race mirrors. Train and demonstrate >=50% at difficulty3 over20 games.


### Step G.3: Implement and validate Terran

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** G.2
- **Source:** [phase-g-build-plan.md](../archived-plans/phase-g-build-plan.md); original G.3.

SCVs/MULEs, depot walls, addons, siege/stim/medivacs, snipe/EMP/nuke, 1-1-1/2-1-1/mech/bio, rewards/features. Train and demonstrate >=50% at difficulty3 over20 games.


### Step G.4: Enable cross-race informational ladder

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** G.3
- **Source:** [phase-g-build-plan.md](../archived-plans/phase-g-build-plan.md); original G.4.

Within-race promotion; separate cross-race informational Elo with stable rankings. Optional cross-race safety gate remains a future decision, not silently required.


### Step H.0: Conclude mini-game role investigation

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [phase-h-build-plan.md](../archived-plans/phase-h-build-plan.md); original H.0.

Resolve scorecard versus gate/reward and Windows PySC2 feasibility before activating H/I/J. Defer all if scorecard-only value cannot justify substrate cost.


### Step H.1: Pin PySC2 and verify imports

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.0
- **Source:** [phase-h-build-plan.md](../archived-plans/phase-h-build-plan.md); original H.1.

Pinned dependency; import-clean with version stack.


### Step H.2: Define MinigameResult and storage

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.1
- **Source:** [phase-h-build-plan.md](../archived-plans/phase-h-build-plan.md); original H.2.

Finite score/result contracts, per-version training.db minigame_results migration and round-trip compatibility.


### Step H.3: Implement minigame runner

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.2
- **Source:** [phase-h-build-plan.md](../archived-plans/phase-h-build-plan.md); original H.3.

Run PySC2 path for selected version/map, structured score, bounded parallel-safe SC2 lifetime.


### Step H.4: Expose minigame CLI

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.3
- **Source:** [phase-h-build-plan.md](../archived-plans/phase-h-build-plan.md); original H.4.

scripts/run_minigame.py --version v0 --map MoveToBeacon; argument/errors/output tests.


### Step H.5: Observe mini-game end-to-end smoke

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.4
- **Source:** [phase-h-build-plan.md](../archived-plans/phase-h-build-plan.md); original H.5.

MoveToBeacon finite numeric score and stored row, compare published baseline at implementation time; no invented minimum guarantee.


### Step I.1: Build custom Protoss mini-game corpus

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.5
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original I.1.

Two to five focused maps from the investigation priority list: blink-stalker micro, attack-concave, force-field choke, storm-clump and phoenix-vs-mutas, scoped by H investigation; score reliability before further maps. Preserve per-race subdirectories for G extensions; kill if custom-map cost is unjustified versus PySC2 alone.


### Step J.1: Wire approved mini-game role

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.5, H.0
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original J.1.

Investigation selects gate/reward/scorecard; wire only the chosen role(s), with cost and empirical validation: scorecard into per-version manifest, gate into fitness/pre-gate, reward into curriculum pretraining. If none is warranted, collapse into H.


### Step L.0: Prove replay-tail observer spike

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original L.0.

Spike C: third SC2 process loads an actively written replay, not a third API PlayerBot. Keep normal fog/full observation limitations explicit; >2 days failure kills viewer path.


### Step L.1: Implement replay exhibition pipeline

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** L.0
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original L.1.

After spike success: replay tailing, game-state bridge/viewer, finite callback/process lifetime, own-process cleanup and recorded fixture. Preserve shipped EV two-pane mode; do not refactor it blindly to a single pane.


### Step L.2: Observe exhibition fidelity

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** L.1
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original L.2.

Live visual matchup, reliable follow/termination and bounded latency; compare recorded fixture, no gameplay authority for observer.


### Step M.1: Build NL matchup selector

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** K.5, L.2
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original M.1.

Use K metadata and L exhibitions, parse NL prompts into tagged pool matchups with explicit fallback. Validate three sample prompts; kill/defer if fallback >50%.


### Step O.0: Prove four-way evolve soak reliability

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** wait
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original O.0.

Parallelization is code-complete but the scripted Hydra prerequisite is actual four-way soak reliability; record complete evidence before multi-expert architecture.


### Step O.1: Train themed Hydra experts

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** O.0
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original O.1.

Separate themed experts (Skytoss, Ground, Cheese-rush) with appropriate training distributions, manifests and reproducible checkpoints. Learned Hydra remains cut.


### Step O.2: Implement scripted expert switching

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** O.1
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original O.2.

Threshold/hysteresis controller, inference integration and dashboard expert trace; preserve same observation/action interfaces.


### Step O.3: Validate expert use and Elo

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** wait
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** O.2
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original O.3.

20 self-play games, >=+10 Elo, no expert collapse: each active >=10% steps in >=50% games. Observe comparable replay/use; defer if switching is effectively one expert.


### Step P.1: Investigate licensed replay pretraining

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original P.1.

Corpus/license/feasibility investigation before distillation or foundational pretraining. No unlicensed corpus assumption; kill if unavailable or no value at current scale.


### Step Q.0: Conclude harvest skill scope investigation

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [phase-q-build-plan.md](../archived-plans/phase-q-build-plan.md); original Q.0.

Compare paper intake versus existing advised workflow; docs-only default, auto-commit only explicit later decision. Collapse to advised extension if indistinguishable.


### Step Q.1: Draft Mode A paper intake

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** Q.0
- **Source:** [phase-q-build-plan.md](../archived-plans/phase-q-build-plan.md); original Q.1.

Paper paste/URL/localPDF -> scoped investigation with context, questions, method, findings, criteria, constraints, downstream decisions and references; docs-only, no code commit.


### Step Q.2: Wire conditional harvest sandbox

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** Q.0, Q.1
- **Source:** [phase-q-build-plan.md](../archived-plans/phase-q-build-plan.md); original Q.2.

Only if scope investigation explicitly selects auto-commit. Otherwise retain as not-applicable conditional, not work silently authorized.


### Step Q.3: Review three retroactive harvest cases

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** Q.1
- **Source:** [phase-q-build-plan.md](../archived-plans/phase-q-build-plan.md); original Q.3.

Compare outputs to three prior investigations using source evidence.


### Step Q.4: Observe one fresh paper intake

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** Q.1
- **Source:** [phase-q-build-plan.md](../archived-plans/phase-q-build-plan.md); original Q.4.

Fresh input yields useful investigation passing review; no cron or auto-PR scope.


### Step Q.5: Consider Mode B suggestion wrapper

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** Q.4
- **Source:** [phase-q-build-plan.md](../archived-plans/phase-q-build-plan.md); original Q.5.

Optional extension only after ModeA live acceptance; keep v1.1 if not a thin natural wrapper.


### Step R.1: Rescope Wilson confidence reporting

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EJ.8
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original R.1.

SPRT was explicitly rejected by July5 EJ investigation and must not be revived. Preserve Wilson-CI reporting as remaining work, additive to shipped one-sided posterior.


### Step S.1: Investigate build-order entropy metric

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** R.1
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original S.1.

Deferred build-order entropy measurement; define a diagnostic metric and scoped validation before any policy/reward change.


### Step S.2: Investigate time-to-win signal

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** R.1
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original S.2.

Deferred signal study; separate wins from speed effects, avoid rewarding invalid/short matches.


### Step S.3: Measure higher-n per-difficulty evaluations

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** R.1
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original S.3.

Deferred higher sample count with per-difficulty breakdown and real machine-time cost.


### Step S.4: Investigate informative priors

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** R.1
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original S.4.

Deferred prior calibration; retain current Beta(1,1) until evidence justifies change.


### Step ER.1: Clarify truncated restructure idea ii

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original ER.1.

Operator seed has six possible readings of SC2 version judge. Obtain intended meaning; do not treat EI as gated by this answer.


### Step ER.2: Plan deterministic replay of tested patches

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.7, EI.9, EI.14
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original ER.2.

Declared successor to EI patch capture: stack cap plus replay addresses candidate scored versus regenerated code shipped. Design exact artifact/replay acceptance before implementation.


### Step ER.3: Consider rule-derived fitness bar after evidence

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.8, EI.14
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original ER.3.

Binomial-alpha or posterior conditioning only after selectable early-stop and post-change corpus; no current gate change.


### Step ER.4: Retain PFSP ancestor regression follow-up

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EL.7
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original ER.4.

Sample ancestor distribution for regression beyond immediate parent; baseline DB supplies corpus but does not implement ancestral sampling.


### Step ER.5: Enrich fingerprints after v1 evidence

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EL.7
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original ER.5.

Build-order/army-composition refinement remains deferred until baseline-result fingerprint proves useful.


### Step ER.6: Revisit species knobs after scored mini-games

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.5, J.1
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original ER.6.

Task ordering/time-in-task are mini-game species concepts, not proxy knobs to invent now.


### Step ER.7: Consider true concurrent lineage execution

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EL.7, EH.7, EH.8
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original ER.7.

Deferred separate from intra-generation worker concurrency; preserve global pointer/commit ownership and SC2 two-client constraints.


### Step ER.8: Repair PFSP seat alternation when activated

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** 6.2
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original ER.8.

game_index always0 in one-game PFSP batches means seat_swap alwaysFalse. Retain as latent correctness follow-up with actual caller test.


### Step ER.9: Repair result-out collision when consumer exists

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.1
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original ER.9.

Concurrent workers collide on logs/selfplay_tmp/game{i}_p*.json, but result-out has no reader at source audit. Conditional follow-up when real consumer is introduced; do not invent usage.


### Step JV.9: Complete production Observer Rush lifecycle

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** 222
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original JV.9.

Preview delivered; production lifecycle integration, multi-monitor/DPI behavior and long-session validation remain separate. Preserve exact before/after source, graceful owned-client stop, no gameplay/camera authority added.


### Step JV.10: Consider v13 grouping adaptation and decision flattening

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** 232
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original JV.10.

v13 centroid/staging/hysteresis not yet copied. Preserve frozen before/after, avoid unseen-enemy=attack/random thresholds. Flattening explicitly deferred; JDI safety controls precede broader changes.


### Step BL.1: Decide and execute branch landing when authorized

- **Status:** DEFERRED — future or investigation-gated scope
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EV.4
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original BL.1.

onbrand-pilot already contained; no mechanical blocker requiring its separate merge. Decide landing with EV acceptance open versus after EV.4. Recheck branches/CI, prefer PR for Linux/Docker signal; merge commit preserves step history, tag actual merged commit, retain phase branch until gates close. No publication authorized by this cleanup.


### Step T.1: Soften max-supply ATTACK override (PARTIAL)

- **Status:** DEFERRED — historical residual; revalidate current state
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.1.

`MAX_SUPPLY_ATTACK_THRESHOLD=180` forces ATTACK regardless of
DEFEND/FORTIFY/EXPAND/OPENING state. **Deliberately heavy-handed for now —
get it working, tune later.**

**Problem with the current fix:** At higher difficulties (4-5), a legitimate
defensive stand (e.g., enemy doom-drop into main while own army is across
the map at 180+ supply) would be incorrectly preempted into ATTACK, losing
the defender's advantage. The 180-supply check fires even when defending
is the correct play.

**Softer solution candidates (pick one when tuning):**
- Require `army_supply >= enemy_army_supply_visible * k` before ATTACK
  fires, so max-supply doesn't override when outgunned in the engagement
  area.
- Add a cooldown: override fires only if `supply_used >= 180` has held
  for N seconds, preventing flip-flop with a visible enemy raid.
- Scale the threshold by difficulty (180 at diff 3, 190 at diff 4, 195
  at diff 5).
- Replace with a "production saturation" signal: override when no new
  units can be produced (all warp-gates on cooldown + all production
  full) AND supply at cap — captures the actual "waste" condition
  without false-firing.

**When to tune:** After re-eval at diff 3 confirms win-rate hold, before
pushing to diff 4-5. The hard fix is safe for diff 1-3 where opponents
rarely doom-drop.



Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


### Step T.3: Tech structures placed on low ground

- **Status:** DEFERRED — historical residual; revalidate current state
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.3.


**Observed (2026-04-17):** Twilight Council and Forge placed on low
ground outside the main base ramp, with no expansion or defensive
structures nearby. Creates free-kill targets for enemy raids — losing a
Twilight Council resets the tech path and is game-ending at higher
difficulties.

**Rule of thumb the bot should follow:**
- **Pylons** — low ground is fine (cheap, power grid coverage, vision).
- **Tech structures** (Twilight, Robotics Bay, Cybernetics Core, Forge,
  Templar Archives, Fleet Beacon) — place inside the main base perimeter
  OR adjacent to an established expansion with cannons. Never alone on
  low ground.
- **Gateways / Robotics / Stargates** — generally inside main,
  occasionally at natural for warp-in proxy, but only when defended.

**Candidate fixes:**
- Add a `structure_placement_priority` map: each building type gets a
  preferred placement zone (main / natural / proxy / any-powered).
  Default placement logic respects the zone.
- Placement query: before committing to a low-ground pylon-powered spot
  for a tech structure, check if any townhall is within N tiles. Reject
  if not.
- Look at `build_manager.py` / wherever `build_structure` or
  pylon-adjacent placement search lives.

**Related observation:** Repeated "red placement location" error
messages in the log suggest the placement search is retrying invalid
spots. May be a symptom of the same bug (trying to place tech on an
already-taken low-ground spot) or a separate retry-storm issue worth
grepping for.

**Re-observed (2026-04-18, 6:23 game time):** Screenshot shows entire
production complex (multiple Gateways + CyberCore + Robo) built on the
low ground *below* the ramp. User note: "the main buildings you want to
protect on the high ground or next to the natural." This confirms T.3 is
still live post T.2/T.5/T.6/T.7 fixes. Three stacked "Can't find
placement location" errors visible → retry-storm likely firing again.



Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


### Step T.4: Target priority: engage producers, not their spawns

- **Status:** DEFERRED — historical residual; revalidate current state
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.4.


**Observed (2026-04-17):** Against Broodlord composition, bot units
engage the ground-spawned Broodlings instead of the flying Broodlords
that produce them. Broodlings respawn continuously as long as the
Broodlord lives; killing the Broodlord collapses the entire threat.
Wasting DPS on Broodlings is a positive-feedback loss (more time spent
on Broodlings = more Broodlings spawn).

**General principle:** For "producer + spawn" enemy unit pairs, target
the producer. Cases:
- Broodlord (air) → produces Broodlings (ground). Kill Broodlord.
- Carrier → produces Interceptors. Kill Carrier.
- Swarm Host (burrowed) → spawns Locusts. Kill Swarm Host.
- Warp Prism (air) + Zealots (warped in) → kill Warp Prism first.

**Candidate fixes:**
- Target priority table in `micro_controller.py` or `tactics/`:
  `{UnitTypeId.BROODLORD: priority_high, UnitTypeId.BROODLING: priority_low}`,
  `{UnitTypeId.CARRIER: priority_high, UnitTypeId.INTERCEPTOR: priority_low}`.
- When selecting attack targets, filter the enemy list by
  `priority_high` first; fall back to closest-enemy only if no
  high-priority targets are in range.
- Unit capability constraint: ground-only units (Zealot, Stalker without
  blink, Immortal) can still hit Broodlord since it's low-ground air —
  but ensure anti-air units (Stalker, Phoenix, Void Ray) prioritize
  flying producers.



Revalidate current code and recorded evidence before implementation: this is a historical unresolved backlog, not a new claim every symptom persists. Capability correction: ground-only Zealots/Immortals cannot attack flying producers; use actual anti-air capability, never the source assertion about low-ground air.


### Step T.7: Units in melee don't deal damage proportional to count

- **Status:** DEFERRED — historical residual; revalidate current state
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.7.


**Observed (2026-04-17, game time 13:16):** Large Protoss army
intermixed with Zerg in full melee. Units visibly engaged but the
engagement drags — bot loses what should be winning fights. User
described it as "move, attack move, don't really attack."

**Candidate root causes (need log evidence to diagnose):**
1. **Target priority (duplicate of T.4):** units engage nearest trash
   (Roaches, Zerglings, Banelings) instead of
   Broodlords/Ravagers/priority targets.
2. **Command churn:** `_run_micro` runs every tick, issues attack-move
   every tick, interrupting the unit's current attack animation before
   damage lands. Check: does DispatchGuard (shipped 2026-04-14) apply to
   unit commands or only build commands?
3. **Target switching:** units chase closer enemies mid-attack, never
   finishing a kill.

**Next step:** grab log snippet around game time 13:16 from an eval run
to see which code path is firing. Likely needs T.4 fix + possibly
command-rate-limit on attack commands.



Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


### Step T.8: Production structures placed inside the mineral line

- **Status:** DEFERRED — historical residual; revalidate current state
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.8.


**Observed (2026-04-18, ~6:10 game time):** Screenshot shows a Robotics
Facility, Stargate, Forge, and Gateway warp-gated directly on top of
mineral patches. Probes are trying to path through the buildings to
mine. A "Can't find placement location" error is visible. Nexus is
hidden behind the production complex, and the main Robotics Facility
(selected) is sitting where Probe pathing expects crystals.

**Impact:** worker mining throttled, pylon/production placement futures
constrained, cannons/batteries have no room when they're actually
needed, worker rally confused.

**Rule:** only defensive structures belong in the mineral line —
**PhotonCannon** and **ShieldBattery**. Everything else (Gateway,
CyberneticsCore, Forge, Robotics Facility, Stargate, Nexus, Pylon except
worker-rally Pylons) must be placed in the back of the main or at
natural choke points / behind the Nexus.

**Candidate root causes:**
1. `bot._build_structure()` falls back to "any powered Pylon radius"
   when primary placement fails — includes the Nexus-adjacent Pylon
   that covers the mineral line.
2. No keep-out zone around mineral patches / vespene geysers for
   non-defensive structures.

**Next step:** find the placement routine, add a filter that excludes
build positions within N tiles of any `self.mineral_field` or
`self.vespene_geyser` for structures ∉ {PhotonCannon, ShieldBattery,
Pylon-for-expansion}. Probably in `bot.py._build_structure` or
`macro_manager` → `MacroDecision` execution path.



Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


### Step T.9: Natural expansion undefended; fall-back point softer than main

- **Status:** DEFERRED — historical residual; revalidate current state
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.9.


**Observed (2026-04-18, ~5:48 game time):** Screenshot shows main with a
cluster of production structures (acceptable fortification given the
stacked gateways/robo/stargate near the Nexus), while the natural
(bottom-right Nexus) has only probes mining. No cannons, no shield
batteries, no units parked. If the bot is pushed off the main or needs
a pull-back, the natural is the logical fall-back — but it's softer
than the main.

**Impact:** a timing attack that forces a retreat has nowhere safe to
retreat to. User called out that the natural is "usually a better
fall-back point than the main" — Protoss tradition is to fortify the
natural because the main ramp funnels but the natural is where the army
lives.

**Rule:** once the natural Nexus is up, queue **at least one**
ShieldBattery next to it, and add a PhotonCannon covering the mineral
line / rally path when Forge is ready. Don't touch the main's existing
fortification logic — per the user, it's "sort of fine for now." Scope
is purely: seed a minimum defense at base #2.

**Candidate touch points:**
1. `fortification.py` — if it runs per-base, extend to include natural
   with a lower target count than main.
2. `macro_manager._check_shield_batteries` — if it only considers main,
   widen to include any Nexus owned ≥ 45 seconds.

**Next step:** read `fortification.py` +
`macro_manager._check_shield_batteries` to see whether the current
logic is base-indexed or hard-coded to main. Simplest fix: add a
"natural defense" check that fires once the 2nd Nexus has been standing
for ~45s and no ShieldBattery is within 10 tiles of it.



Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


### Step T.10: Army idles inside enemy base instead of finishing the game

- **Status:** DEFERRED — historical residual; revalidate current state
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.10.


**Observed (2026-04-18, 12:42 game time):** Screenshot shows a full
Protoss army (Stalkers + Zealots + at least 2 Archons visible) standing
dispersed among enemy Zerg structures in the Zerg main base. Supply
85/86, 4270 minerals / 478 gas floating. Three "Can't find placement
location" errors stacked on the left — bot is still trying to place
buildings (probably a Pylon to break supply cap) instead of ordering
the army to clean up.

User note: "It could win in a few seconds if it would attack walk
around."

**Relationship to T.5:** T.5 (`e0ae944`) made `_run_micro` always
attack-move in combat states. This screenshot suggests the problem is
not "move vs attack" — the units are simply **not being issued any
order this tick**. They are inside attack range of buildings/units but
stand idle. Either:
1. `_run_micro` is not running (strategic_state not in
   ATTACK/DEFEND/FORTIFY/LATE_GAME despite visually being in combat),
   or
2. `_run_micro` runs but targets are filtered out (e.g., T.4-style
   priority picks an out-of-range target), or
3. DispatchGuard is rate-limiting attack re-issues from T.7 too
   aggressively and no new command arrives when the current target
   dies — the unit goes idle.

**Test path:** pull the game log around 12:42 and grep for
`Hybrid override:`, `strategic_state`, and the unit-command dispatch
lines. Compare to a winning game's log at the same phase.

**Rule:** if units are in vision of enemy structures AND have not
received a command in the last N ticks AND are idle, issue attack-move
on the nearest enemy structure — regardless of strategic_state.

**Next step:** identify which of the 3 root causes above fires, fix
accordingly. Candidate is (3) — DispatchGuard window may be too wide
when the held target has just died.



Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


### Step T.11: Split army: engaged force + still-rallying force in parallel

- **Status:** DEFERRED — historical residual; revalidate current state
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.11.


**Observed (2026-04-18, 7:52 game time):** Screenshot shows a Protoss
force (Stalkers + Sentries) engaging Zerg Roaches at the top-right,
while a second group (Zealots) is standing on a rally point
lower-middle, not moving. Game shows 14 in F2 (probably the engaged
squad). Two forces fight at partial strength against the same enemy, so
trades are worse than they need to be.

**Rule (guiding principles §12 coherence, §10 engagement):** when any
owned army unit is in combat, subsequent unit production from Gateway /
WarpGate / Robo must **join the engagement**, not continue to a stale
rally point. Rally targets should be invalidated by a combat event.

**Candidate touch points:**
1. `army_coherence.py` — probably already computes a center-of-mass;
   see whether rally points derive from it.
2. `commands/executor.py` — when a new unit warps in during combat, is
   it issued an attack-move toward the fight, or just inherits a stale
   rally?
3. `bot.py._produce_army` after `wg.warp_in(...)` — maybe issue an
   immediate `newest_unit.attack(engagement_target)` before returning.

**Next step:** read `army_coherence.py` and the warp-in path; determine
whether stragglers get an explicit "move to fight" order or rely on the
WarpGate rally. If the latter, add a post-warp hook that routes new
units to the nearest ally-in-combat.


Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


### Step T.12: Re-validate Archon morph fix in hybrid mode (DEBUG ONLY)

- **Status:** DEFERRED — historical residual; revalidate current state
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.12.

`bot.py::_produce_army` morph branch. First diagnostic run was done in
**rules** mode at diff 3 (simpler, removes the policy variable while
isolating the engine-level morph dispatch).

Once the root cause is identified and the fix lands, the morph behaviour
must be re-validated in **hybrid mode** (`--decision-mode hybrid
--model-path bots/v0/data/checkpoints/v3.zip --no-claude`) to confirm
the PPO policy doesn't suppress HT warp-ins or starve the gas economy
enough to skip the morph branch in realistic deployment. Archon count
must be non-zero across a 5-game hybrid sample before we consider the
fix shipped.



Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


### Step T.2.1: Revisit proactive elevation-aware rally placement

- **Status:** DEFERRED — reactive low-ground-bleeding fix shipped; proactive alternative remains future
- **Type:** code
- **Issue:** not assigned
- **Depends on:** none outstanding
- **Source:** [tactical backlog](../archived-plans/tactical-bugs.md), original T.2 deferred tail.

Reactive centroid/HP-loss detection shipped c5bd90d/#138. The separately deferred proactive elevation-aware rally-point selection remains a distinct requirement: investigate whether current placement avoids low-ground bleeding before proposing it. Do not reopen the shipped reactive fix. Accept only actual terrain-aware placement with regression evidence; no new gameplay work in this migration.

### Step SV.1: Resolve shared self-play detach and orphan cleanup

- **Status:** HELD — historical residual; current closure or activation decision required
- **Type:** code
- **Issue:** #147 (legacy provenance)
- **Depends on:** none outstanding
- **Source:** [selfplay-viewer-2-screen-plan.md](../archived-plans/legacy/selfplay-viewer-2-screen-plan.md); also [April18 soak](../soak-test-runs/soak-2026-04-18-selfplay-viewer.md).

The April18 #147 soak had only7records after Esc atgame8, an orphan, and partial detach-continuation. EV intentionally did not change scripts/selfplay.py/shared run_with_batch contract, which still sets stop_event. Preserve the deferred detach_only API improvement and remove misleading orphan warning only with real owned-lifetime proof. Revalidate against current implementation before change; never reproduce historical manual SC2 killing.

### Step SV.2: Repair threaded viewer interrupt ownership

- **Status:** HELD — historical residual; current closure or activation decision required
- **Type:** code
- **Issue:** #147 (legacy provenance)
- **Depends on:** SV.1
- **Source:** [selfplay-viewer-2-screen-plan.md](../archived-plans/legacy/selfplay-viewer-2-screen-plan.md); also [April18 soak](../soak-test-runs/soak-2026-04-18-selfplay-viewer.md).

EV explicitly leaves worker-thread Ctrl+C/SC2 cleanup unsafe; proper fix requires orchestrator selfplay outside its original read-only scope. Design owned graceful interruption without installing main-thread signals from workers or killing arbitrary SC2. Test actual lifetime boundary; keep current documented no-Ctrl+C rule until proven.

### Step SV.3: Revisit viewer sizing on the operator display

- **Status:** HELD — historical residual; current closure or activation decision required
- **Type:** code
- **Issue:** #147 (legacy provenance)
- **Depends on:** none outstanding
- **Source:** [selfplay-viewer-2-screen-plan.md](../archived-plans/legacy/selfplay-viewer-2-screen-plan.md); also [April18 soak](../soak-test-runs/soak-2026-04-18-selfplay-viewer.md).

April18 operator found even small preset overwhelming. Current presets still have1024x768panes. Preserve smaller/parametric sizing discussion, layout/input/reparenting validation; no automatic geometry preference selected by migration.

### Step SV.4: Audit viewer surrender-label cosmetic leak

- **Status:** HELD — historical residual; current closure or activation decision required
- **Type:** code
- **Issue:** #147 (legacy provenance)
- **Depends on:** none outstanding
- **Source:** [selfplay-viewer-2-screen-plan.md](../archived-plans/legacy/selfplay-viewer-2-screen-plan.md); also [April18 soak](../soak-test-runs/soak-2026-04-18-selfplay-viewer.md).

April18 screenshot/text records V0 surrendered broodwar. Find current message producer and source enum; remove leaked implementation label if still present, preserve flavor text. Close only with current evidence or explicit supersession.

### Step SV.5: Consider nonblocking HWND discovery

- **Status:** HELD — historical residual; current closure or activation decision required
- **Type:** code
- **Issue:** #147 (legacy provenance)
- **Depends on:** SV.1
- **Source:** [selfplay-viewer-2-screen-plan.md](../archived-plans/legacy/selfplay-viewer-2-screen-plan.md); also [April18 soak](../soak-test-runs/soak-2026-04-18-selfplay-viewer.md).

The 15s GAME_START_HWND_TIMEOUT_SECONDS hotfix blocks pygame per game. Deferred per-frame lookup must preserve bounded discovery, pane ownership, callback chaining and exit cleanup; verify no UI stall and no stale HWND attachment.

### Step SV.6: Resolve legacy self-play viewer acceptance residuals

- **Status:** HELD — historical residual; current closure or activation decision required
- **Type:** operator
- **Issue:** #147 (legacy provenance)
- **Depends on:** SV.1, SV.2
- **Source:** [selfplay-viewer-2-screen-plan.md](../archived-plans/legacy/selfplay-viewer-2-screen-plan.md); also [April18 soak](../soak-test-runs/soak-2026-04-18-selfplay-viewer.md).

Original2screenStep9/#147 was superseded as a design, but April18 evidence is4PASS/1PARTIAL/1FAIL/1N/A, not seven passes. Map current detach/orphan fixes and observe HWND count over10reparent cycles (+/-20UserObjects), resumed batch completion and no orphan; preserve actual prior pass evidence for panes/overlay/resize/placeholder. A triage record with follow-up identities is an allowed source completion alternative. Do not invent a new requirement to kill SC2.

### Step UI.7: Resolve historical live-smoke acceptance provenance

- **Status:** HELD — historical residual; current closure or activation decision required
- **Type:** operator
- **Issue:** not assigned
- **Depends on:** none outstanding
- **Source:** [ui-live-test-plan.md](../archived-plans/legacy/ui-live-test-plan.md); also [April18 soak](../soak-test-runs/soak-2026-04-18-selfplay-viewer.md).

Old ui-live-test plan Step7 records real commands/live-data verification and runtime reviews but no final per-check smoke verdict. Locate durable PASS or explicit retirement under later dashboard redesign; otherwise preserve the unresolved end-to-end gate against current equivalent surfaces. Do not resurrect removed frontend components or old5173port.

### Step UI.8: Resolve historical visual-check acceptance provenance

- **Status:** HELD — historical residual; current closure or activation decision required
- **Type:** operator
- **Issue:** not assigned
- **Depends on:** UI.7
- **Source:** [ui-live-test-plan.md](../archived-plans/legacy/ui-live-test-plan.md); also [April18 soak](../soak-test-runs/soak-2026-04-18-selfplay-viewer.md).

Old ui-live-test Step8 checklist includes stats, command history, build/attack execution, human_only and ai_assisted switching, mute. Some actual live fixes are recorded, but not a complete final checklist. Resolve each through later evidence or explicit supersession; remaining genuine human checks stay pending rather than inferred from521unit tests.

## 8. Jev campaign contracts

JON: freeze exact source bytes before evaluation, no second authoring on promotion. One atomic durable ledger reserves worst-case cost before every gameplay/probe/advice/coder request, counts missing usage at maximum, no hidden retry, unknown price/model blocks dispatch. Rates in sources are dated evidence, reverify before dispatch; managed cap is not a claim about account taxes/provider changes. Integer nanodollars, cumulative5e9. Offline225 structures cannot authorize execution: 227 issues and verifies real controller evidence binding campaign/window/attempt/case/run/source/result/critical test execution. Caller labels, booleans, hashes or seals alone are not authority. Preserve original three-round hold; prospective round4/10 bounded<=2h plus one review batch after reorientation, not field-by-field patching or unrelated225 scope.

Weighted JON references parent50/original25/distinct25 (coalesce duplicate original to50). Freeze before testing. Shared built-in-AI cases, not direct Jev H2H. Three-race Medium paired screen6games total; candidate wins>=parent admits only. Promotion18cases/version: Simple64 Medium/MediumHard,3races3new sealed seeds, alternate order; candidate>parent, weightedreference>=.10,no lower race/difficulty wins, full critical suite. New promotion panel after prior exposure, exclude11/101/202 and lineage exposed seeds. One assessment per candidate fingerprint, no result-driven reruns. Nightly parent controls one per race, disagreement invalidates window/cache, not equivalence claim. Missing/incomplete/invalid evidence is no promotion. Critical suite includes recovery completion/renewal, defense, production while pending, stale answers and reservation cancellation; skipped/xfail/zero-collected invalid. First campaign six hours, fixed2xcap,900game/1200wall/450requests, enough remaining time before dispatch, graceful stop and honest overshoot; camera/caster on,recordingoff. Advisor one hypothesis in isolated allowlisted worktree; budget/evaluator/tests/credentials protected, no merge/push/default switch. Prepare performs no network/process; explicit run/resume retains ledger; report read-only. UUID4 lowerhex identities,SHA256bytes,UTCpersisted/monotonicelapsed,atomic records/exclusive ownership; failed retries retain conservative allowance. JON planned commands/modules must not be represented as shipped.

JDI: graph owns shared feasibility and same-tick fallback; emergency defense first, strategic recovery second, army intent third, local fallback last. No recover without living eligible combat unit, no attack without group/target. Outcome proof must inspect exact actor post-adapter order, not merely selected branch or any command. 232 audit shows recovery success while eight priorATTACKorders survive; do not infer fixed from cancelled bookkeeping. Current/improved profiles, one global request budget; prepared army intent at3readyZealots but no earlyattack, at4 wait<=2gameSeconds once, defense immediate, semantic signatures preserve harmless reinforcement transitions and revalidate current eligibility. History31samples<=1/gameSecond,30s count/durability proxy, sighting ages/coverage; vision loss is not death. Independent macro priority tech/expand/hold10s, own slot/expiry, fair question scheduling after2nonemergencyarmyrequests.

JDI arms S scripted-local, F scripted-fixedStalkerwhenfeasible, H hostedcurrent, I hostedimproved use identical repairedsource/policy. Simple64 three races Medium/MediumHard seeds303/404/505 unless exposed (nextunused>=606),18cases/arm,72evaluationgames36hosted;3development smokes yield75baselinegames38hosted. Native realtime, no game-speed override;900game/1200wall/450requests. First3Mediumcases12games then60remaining; stop/report/allocation within same$5, prerequisiteJONcalls also count. Positive conclusion needs I>=2wins/18 above bothS/F,no race regression,no defense suppression/avoidableemptyarmyfailure,zeroinfrastructurefailure; macroclaim>=10appliedchoices across>=3cases includingtech+expand. All attempts visible;missingpanelinconclusive;draw/timeouts/infra0 in intention-to-test, completed-only separately, discordant paired counts+descriptiveClopperPearson95% interval,no population/causalbreadthclaim. If source/prompts change, new sealedexperiment/seeds. More/same/less model role allowed, no automaticpromotion.

J2 remains separate: six-case deferredv1baseline races3/4seed11;222<=3Terrandevelopmentmatches3/4/5seed11;223freezev2once then hostedv1/v2 races atMediumHard seeds101/202 (12games) +6scriptedv2attribution. Positive gatev2>=4/6,moretotal thanv1,no lower race count;v1=6/6 ceilinginconclusive. Realacceptedhosteddecisions/provenance required, no fallback-as-hostedwin. Perinvocation6games/7200wall/2700requests, firstinfra/auth/provenancefailurestops; allcasesretained, explicitinterruptedretry. 222 needssecondNexus,firstStalker,actualstrategy+execution influence,worker/site recovery,pendingstop; absentnatural evidence remainspending. Browserexactsession/run ready beforeSC2, no newest-run guessing; input readiness separate from fullrealgameacceptance. JV requires its own three seeds1/2/3Terrandiff1completeoutcomes,rushandtraceevidence; JI historicalservicepass does not substitute.

## 9. Tests and validation

Future code steps retain original review flags, production-caller integration, negative/default-preservation tests and exact Done-when. Re-measure baseline on the actual worktree; historical counts are evidence, not timeless assertions. Run scoped checks while iterating and required full suites at implementation acceptance: uv run pytest, uv run ruff check ., uv run mypy src bots --strict; scripts checks explicitly where EH/EI/JON own them, report pre-existing errors. Frontend npm run test:run (not watch), lint,typecheck,build; actual browser tests separately. Offline proof never closes live gates. Migration validation/source hashes are in ../plan-reconciliation/validation.json.

## 10. Risks and holds

Reference-only exception: this authorized documentation migration repairs the shared-doc resource paths in frozen RL bots/v0 through v12 as well as current v13. Original bytes remain in Git history; gameplay logic, stored data, Jev captured source snapshots and policy bytes are untouched. Historical source coordinates still need the migration map. The earlier alpha4gate.runner skill cleanup is already absent from current skills; it is not reopened.


Held225/232 are structural review failures, not unfinished paperwork. E0,J2.0,EI.0,BL1 need product decisions. Investigation-gated future work remains disabled. No command here should bypass current local safety rules. Historical code anchors must be rederived. Archives preserve links verbatim for provenance, so historical relative links may be stale; use migration.json to resolve old paths.

## 11. Estimates

[Build estimates](build-estimates.md) are prospective model ranges for every outstanding leaf, active worker and operator attention hours. They exclude unattendedSC2 wall duration, separately named in gate requirements; no measured calibration history exists. Held work is forecast-only, not approved dispatch. Raw master SHA256 binds exact plan bytes; Observatory binds its own evidence work-item fingerprints.

## 12. Completion and rollback

A phase closes only when its original acceptance gates and actual evidence pass; negative experiments remain recorded with target-unmet status when specified. Completed original plans reside under ../completed/, mixed or superseded originals under ../archived-plans/. Source bytes/hash history are preserved, and migration.json records all file paths. Reconciliation is reversible file moves plus references; no runtime data or frozen baseline changed. Implementation rollback follows phase-specific backups/version isolation and never deletes a real promoted version merely because it appears untracked.

## 13. Quickstart for a fresh session

Read project instructions, this master and the exact source for the selected leaf. Inspect live git status/branch, preserved pending worktrees and issue evidence. Choose an unheld ready leaf, re-anchor producer/consumer paths, execute its normal review workflow. Do not use stale overnight/session goals as new authorization. For operator work read docs/operator guides and docs/operator-gate-runbook.md after applying section5 corrections. Stop/report at actual gate boundaries.

## 14. Source reconciliation

[Merge mapping and review](../plan-reconciliation/merge-decisions.md) describes duplicates, partial overlap, sequencing and intentionally retained scope. [Immutable migration map](../plan-reconciliation/migration.json) includes source hashes and targets. Source reviews/proposals/validation reports are reference evidence under ../plan-reconciliation/source-reviews/, not competing plans. All original append-only decisions and build history remain in full archived sources.

## 15. Appended decision inventory (2026-10-14)

| ID | Origin | Decision | Status |
|---|---|---|---|
| RC1 | Operator | All active-project docs consolidated; one master; preserve distinct requirements | Applied |
| RC2 | Operator | Shared Toolkit scheduling authority retained | Applied |
| RC3 | Agent default | Keep JDI/JON and every live gate separate; source hashes preserve history | Applied |
| RC4 | Agent default | Hold contradictory E cardinality and unowned J2 residual scope | Unresolved product choices |
| RC5 | Agent default | Forecast ranges are model estimates; no invented measured history | Applied |
