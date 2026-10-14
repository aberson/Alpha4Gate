[!] Detected non-blank Issue fields — repo-sync appears to have already run. Findings applied to plan.md will require corresponding `gh issue edit` updates (N+1 rework). See `feedback_plan_review_before_repo_sync.md`.
Reviewing as: feature plan. Sections 17–21 apply.

# JON dependency boundary review, October 14

Same-session plan-review of the prospective clarification, informed by a fresh independent necessity challenge from `jon225_dependency_challenge`. This is not acceptance of campaign code. Source: [canonical plan](../plans/jev-overnight-improvement-plan.md), D7 and Steps 225–227.

## Blockers

Resolved prospectively: Step 225's held implementation conflated integrity with authority. Its audit identified caller-authored process exits, rebinding prior archives and a copyable snapshot seal (`$env:USERPROFILE/dev/worktree_build-step-225-b0cc7700/.build-step/audit.md:7–24`). The independent reviewer confirmed the defect and recommended simplifying the step, rather than building the audit's proposed receipt/MAC machinery before the controller exists.

The coordinator adopts that recommendation: 225 provides immutable preparation and non-authoritative offline scoring; 227 must verify actual execution before any live verdict. No existing failed review changes status. No remaining plan blocker for this bounded offline candidate; live work remains blocked by unbuilt prerequisites.

## Significant gaps

Resolved prospectively: D7 now identifies which step owns execution facts and explicitly rejects caller claims as authority. Step 227's acceptance retains source/case/attempt/window/run binding, actual exits and output, critical-test validity, replay rejection and epoch invalidation. These preserve the evidence requirement without prescribing a new authentication service. Step 225 now requires deep review because persisted contracts cross an evidence producer/consumer boundary.

The simplification is an agent-selected mechanism within the approved dependency work, not a newly operator-required architecture. It gets one continuation attempt, round 4/10, two hours and one required review batch. Failure holds it; it cannot trigger another verifier layer or reset history.

## Missing items

Resolved: current held status, current ancestor context, and explicit shared JDI cumulative allowance. New campaign modules are still planned artifacts, not missing existing dependencies. They do not exist in the accepted source tree (`rg --files src scripts tests`, October 14).

## Nice-to-haves

None. No new service, listener, review tool or dashboard is needed.

## Check coverage

1. Persistence: D7 immutable manifests, atomic state and durable budget records; live records belong to 227.
2. Integrations: D1/D7 pinned provider and guarded dispatch remain mandatory; pricing is reverified in 226 before paid operation.
3. Secrets: existing launcher loading only; no credential reads during this review or offline build.
4. Concurrency: D7 OS-held lease and root launch lock; separate campaign-only checkout avoids JDI files.
5. Errors: unverified, incomplete, invalid and blocked evidence remain distinct from live success.
6. Toolchain: D9 supplies installation, lint, typing, tests and build commands.
7. Decisions: offline/live boundary and bounded correction are explicit agent choices.
8. Setup: D9 names Windows, SC2, viewer, browser and dependencies; no setup mutation here.
9. Idempotency: immutable manifest; 227 owns interrupted attempts and no completed-case replay.
10. Seams: existing `benchmark.CaseSpec:587`, `SourceSnapshot:866`, `capture_source:991`, `verify_snapshot:1112`, `RunExpectation:2340`, `score_run:2533`; reuse, no signature change for 225.
11. Scope: offline contracts precede budget and controller; automatic promotion/default changes remain excluded for JDI.
12. Security: content hashes prove identity, never execution authority; no caller-selectable verified flag.
13. Tests: production CLI must reject authoritative live requests until the controller exists; scenario scoring tests retain D2/D3 floors.
14. Operations: D9 prepare/run/resume/stop/report; bare invocation cannot launch.
15. Observation: 228/229/231 remain real native evidence gates.
15.5. Pipeline: 228 is still required before unattended campaign observation.
16. Fresh context: D7 inline ownership and schema table, D9 commands, explicit Step 225 hold.
17. Existing code: benchmark anchors above checked; no campaign module on current accepted branch.
18. Impact: 225's five declared files are new; shared benchmark/v2 tests cannot be edited concurrently with JDI.
19. Conflicts: original held checkout is read-only; no v1 recapture, J2 panel change or desktop pointer change.
20. Context: stale uncommitted-fix statement corrected after verifying ancestor `5eb0367`.
21. Sizing: contracts/scoring, reservation and controller remain distinct slices.
22. Step shape: code 225/226/227/230; operator 228 produces evidence; waits 229/231 produce observation.
23. Conditions: no conditional steps.
24. Review flags: 225 now deep; no runtime URL needed for this CLI/library change.
25. Format: seven numbered steps with required fields and existing issues.
26. Deployment seam: real smoke 228 retained; no mock result substitutes for it.
27. Stakes: deep routing applies to persisted evidence contracts; no model-tier override introduced by plan review.

## Applied changes and next action

Auto-applied 1 fix: stakes-aware reviewer escalation, Step 225. Separately, the coordinator made the documented prospective ownership clarification after the independent necessity challenge; this is not an autofix of operator-owned acceptance wording. No operator input is needed for this routine simplification under the current authorization. Plan-redline and plan-wrap follow before issue synchronization and isolated implementation.

Auto-applied 1 fixes. Plan is ready for `/plan-wrap` and `/repo-sync`.
