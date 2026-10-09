Reviewing as: feature plan. Sections 17–21 apply.

# Jev v2 technical plan review

Date: 2026-10-08, revision 3 (expedite check). Source: [jev-v2-plan.md](jev-v2-plan.md).
Same-session plan review under the plan-review Codex adapter, not independent
implementation review. No populated Issue fields; issue sync has not run.

## Blockers

None remaining.

## Significant gaps

Resolved: Step 214 originally declared `--reviewers code` while modifying shared
runtime execution/reservation behavior. Escalated only that flag to `deep`, per
the stakes-routing trigger owner in review-deep/core.md. Other shared-contract
steps were already deep; visible full-stack Step 221 retains full/UI review.

During authoring, clarified baseline freeze after observation-only instrumentation,
the GamePort query signatures, and the point at which maximum-durability facts
become available. Replaced an undefined live scenario override with at most three
named development matches and an honest pending gate if required behavior is absent.
These refinements are now explicit in plan sections 5, D1, D3 and Step 222.

## Missing items

None at planning scope. New v2 source, benchmark script and operator procedure
are explicitly future build deliverables, not claimed existing files.

## Nice-to-haves

Issue fields are intentionally blank until repo-sync. Existing dashboard ports
3000/8765 are reused, so this phase adds no port allocation. Wider statistical
panels and additional maps may follow demonstrated improvement; neither is required
to claim the deliberately narrow six-case result.

## Coverage and evidence

| Check | Result and plan evidence |
|---|---|
| 1 Persistence | D5 preserves atomic RunRecorder snapshots; D6 defines locked resumable manifests, corruption handling and immutable completed cases. |
| 2 Integrations | D2 defines one typed Choice request, cadence, timeout, HTTP failure behavior; D6 pins/verifies model. Official Typesafe API checked on review date. |
| 3 Secrets | D2/section 9 use environment-only key, same-account encrypted local file, no key output/archive. No refresh protocol assumed. |
| 4 Async/concurrency | One in-flight request globally, bounded per-question answers, plan ownership, one batch writer; SC2 query preparation kept outside synchronous graph evaluation. |
| 5 Errors | D2/D3/D5 define fallback, expiry, progress deadlines and user-visible blockers. |
| 6 Toolchain | Section 9 covers install/dev/build/test/lint/typecheck for backend and frontend, verified against pyproject.toml and frontend/package.json. |
| 7 Decisions | No unresolved TBD, placeholder or alternative architecture. Parameter ranges, enum choices and failure alternatives are deliberate runtime behavior. |
| 8 Setup | Section 9 names SC2/map/runtime prerequisites, exact commands and encrypted-key load. Future commands labeled not yet implemented. |
| 9 Idempotency | Existing intent-key tasks plus single plan ownership; benchmark resume skips complete cases and records attempts. |
| 10 Seams | Section 5 defines observations, sightings, costs, plan records, questions, IDs and query methods. D5 retains the run wire envelope; D7 specifies separate launch/ready API contracts. |
| 11 Scope | One map, two bases, modest composition, no evolution/full tech tree. Each new behavior has an acceptance step. |
| 12 Security | Enum-only service answers, no runtime code/graph mutation, no hidden enemy state, sanitized diagnostics and no credential copying to source snapshots. |
| 13 Testing | Production-caller tests, old archive compatibility, live service smoke and paired evaluation are separate requirements. |
| 14 Operations | Finite match/batch limits, one-client sequential runs, Ctrl+C propagation, owned cleanup and immutable batch recovery. |
| 15 Observation | Steps 213, 222 and 223 require actual matches and observable outcomes; not unit-green acceptance. |
| 15.5 Pipeline smoke | Real service/SC2/graph/viewer smoke before longer panels, with minimum 60 seconds and explicit unanswered gates. |
| 16 Clean context | Inline architecture/schema/ID/route/process/quickstart summaries. |
| 17 Existing code | Read contracts.py Observation/Entity, operations.py ability/cost tables, runtime.py budgets/reservations, adapter.observe/_issue_one, runner._play, decision.py and UI producers. Nineteen named producer paths existence-checked, zero missing. |
| 18 Impact | Section 4 enumerates symbol consumers including duck-typed test ports; old wire/installed-wheel regression gates retained. |
| 19 Conflicts | Active branch master-plan/phase-ev at 4ea560e; existing JI working-tree changes acknowledged. One active worktree. No edits to legacy RL or EV implementation. |
| 20 Context sufficiency | Package separation, shared runtime, archived graph and Typesafe's limited v1 scope described explicitly. |
| 21 Step sizing | Benchmark, execution, scouting, gas, expansion, mixed army, strategy, execution questions and viewer each have a production-verifiable slice. Live work separated. |
| 22 Operator/code split | 213/222 produce observations only. 223 is a bounded wait/evaluation. Operator procedure and tools authored by code steps. |
| 23 Conditional predicates | N/A: no conditional step. |
| 24 Review URLs | Steps 221/224 have start command and Jev URL; other code steps use deep code review without a fabricated browser requirement. |
| 25 Step format | Mechanical scan: 13 unique headings 212-224, ordered 212,224,213,214-223; required fields present; no collision with active sibling plans. |
| 26 Substrate | 213/222 explicitly exercise real service and SC2 environment. |
| 27 Stakes routing | Step 214 escalated to deep; other schema/HTTP/ownership changes already deep. |

Control-plane checks: master_plan.md now exposes Phase J2, objective and reserved
step range. New J2 progress view has 0/10 code and 0/3 live gates, never counting
this review as gameplay acceptance. No new listener ports.

Revision 2 review: verified App tab parsing, useJevRun null-only initial selection,
launch-a4g startup/browser/prompt sequence and run_match recorder-before-play
ordering. D7 defines bounded session/ready records, loopback/origin enforcement,
exact run/hash matching, no arbitrary path or game-command endpoint, child-only
key injection, startup deadlines and no silent headless fallback. Step 224
precedes baseline 213; baseline source finalization follows launch instrumentation.
API route-table, hook race/cancellation, launcher compatibility and actual UI-first
SC2 observation are acceptance requirements. Manual history pauses following
explicitly. All 27 checks revisited; no unresolved findings from this addition.
Existing gameplay-scope evidence is reused where unaffected; this is still a
same-session plan review, not independent implementation review.

Commands used: repository-wide `rg` symbol/path searches, source reads,
`git status --short`, `git log -12 --oneline`, `git worktree list`, installed
burnysc2 Difficulty enum inspection, Python structural/existence checks and
`git diff --check`. Planning only: no product test suite rerun and no paid games.

Auto-applied 1 fixes:
  - Stakes-aware reviewer escalation: Step 214 (`code` to `deep`).

Auto-applied 1 fixes. Plan is ready for `/plan-wrap` and `/repo-sync`.


## Expedite recheck (2026-10-08)

Reviewing as: feature plan. Sections 17?21 apply.

Blockers: none. Significant gaps: one resolved launch-error parser seam.
Missing items: none remaining. Nice-to-haves: no new findings.

Producer proof: contracts.py:152 defines a closed run ErrorCode union and
frontend/src/types/jev.ts:577 parses API failures through the strict run-error
reader. D7 previously specified HTTP statuses without separate code vocabulary.
Defined LaunchError codes and a dedicated frontend parser; archived run errors
remain unchanged. Step 224 carries the autofix marker. Source paths, field shapes,
step dependencies, live gates, issue-field state and conflict checks were rerun;
reviewer routing and unchanged gameplay-design findings remain as recorded above.

Auto-applied 1 fixes:
  - Missing launch error contract: D7 / Step 224.

Auto-applied 1 fixes. Plan is ready for `/plan-wrap` and `/repo-sync`.

## 2026-10-09 continuation review (revision 4)

[!] Detected non-blank Issue fields ? repo-sync appears to have already run. Findings applied to plan.md will require corresponding `gh issue edit` updates (N+1 rework). See `feedback_plan_review_before_repo_sync.md`.

Reviewing as: feature plan. Sections 17?21 apply.

Rechecked the 27-check coverage above against the changed continuation. Source
checkpoint `115a9c5`: `contracts.py:767/812` still defines Entity/Observation,
`decision.py:250` ArmyDecisions, `bot.py:567` JevController and `runner.py:433`
run_match. Existing benchmark and launch modules now supply the previously
planned prerequisites. No new operation, API or schema design was introduced
by the operator's sequencing change. The remaining shared-runtime steps retain
deep review; Step 221 retains full/UI review and launch/URL fields.

All 13 step records have Problem, Type, Status, Issue, Files, Produces, Done when
and Depends on. IDs unique; no TBD. No conditional step or mixed code/operator
acceptance was introduced. Runtime source is clean, policy validates, and
production verify_snapshot rehashed the frozen v1 to
`b53039a693cadf756e6cec4fc44c10534bcd9194079410a98b59a9e441bb1bfd`.

Blockers: None for the operator-authorized 214-221 span.
Significant gaps: No new design gap; incomplete baseline and multi-game browser
observation remain explicitly deferred evidence, not accepted performance.
Missing items: None beyond the listed unbuilt deliverables.
Nice-to-haves: None added.

The one sequencing change is operator-authored P7, not an agent autofix.
Step 213 stays pending; Step 214 depends on completed 224 and verified freeze.
Auto-applied 0 fixes. Plan is ready for `/plan-wrap` and `/repo-sync`.
