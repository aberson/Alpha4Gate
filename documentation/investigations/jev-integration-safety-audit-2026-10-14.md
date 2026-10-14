# Jev integration safety audit, 2026-10-14

**Disposition: Step 232 held after three review rounds; no fourth patch round.** The candidate is preserved and pushed as `build-step-232-jdi` at `00bc515`, not merged into the working branch. The accepted JDI count remains 0/9 and Typesafe spending remains $0. The investigation and experiment design remain in the [main report](jev-model-involvement-2026-10-14.md). This audit explains the remaining implementation gate, not playing strength.

## One structural finding

The integration still approximates graph execution separately from the graph, and its tests sometimes infer effective behavior from a selected branch or any command rather than the exact actor's resulting order. This permits a choice to appear executable and successful while incompatible earlier work survives. Round 1 exposed target ordering and ownership drift; round 2 exposed recovery priority, posture and readiness drift; round 3 still exposes actor ordering and recovery order replacement. Full raw reviews and immutable input inventories remain under local `.build-step/jev-jdi-overnight/review-232-r3-final/`; the final independent verdict is `NEEDS-WORK_STOP_FOR_AUDIT` in `final.json:2`.

The concrete round-3 defect spans four sites at the preserved candidate:

1. [runtime.py:1432](https://github.com/aberson/Alpha4Gate/blob/00bc515/src/jev/runtime.py#L1432) excludes recovery from cancellation of incompatible army tasks.
2. [operations.py:2522](https://github.com/aberson/Alpha4Gate/blob/00bc515/src/jev/operations.py#L2522) treats point movement as satisfied based on position, even if an incompatible order remains.
3. [runtime.py:2350](https://github.com/aberson/Alpha4Gate/blob/00bc515/src/jev/runtime.py#L2350) accepts satisfied intents before checking/preempting the current holder at line 2395.
4. [runtime.py:1802](https://github.com/aberson/Alpha4Gate/blob/00bc515/src/jev/runtime.py#L1802) finishes task bookkeeping without sending a replacement SC2 order. Cancellation alone therefore cannot fix the defect.

## Controlled reproduction

An offline run through `run_match`, mocked HTTP, the real provider/parser, JevBot, graph runtime and adapter reproduces the defect. This is a simulated world, **not a hosted model call or live SC2 match**. The real launcher was forbidden, HTTP required `MockTransport`, and only the fixture's dummy credential was supplied. The [portable evidence](jev-integration-recovery-audit-2026-10-14.json:1) records the exact source, setup, events and post-adapter actor state.

Start with the existing eight-Zealot world and let the army attack. Return `recover` with confidence 0.9 for the first strategic request. Before delivery, place the eight units at the home rally point while retaining their attack orders. Capture state immediately after `JevBot.on_step`, before the simulated world's next advance. The fixture entry points are [test_jev_v2.py:1279](https://github.com/aberson/Alpha4Gate/blob/00bc515/tests/test_jev_v2.py#L1279) and [test_jev_plans.py:353](https://github.com/aberson/Alpha4Gate/blob/00bc515/tests/test_jev_plans.py#L353).

| Observation | Evidence |
|---|---|
| Recovery accepted at loop 104 | Offline run `16fad34c92db4e5c8c5ea83ba3fcdcbf`, event #1519, request 3 |
| Recovery move and branch report success | Same run, #1611 and #1612 |
| No recovery movement command on that tick | Evidence `recovery_commands_on_tick: 0` |
| Eight attack tasks remain running after adapter application | Evidence `after_adapter.tasks`, actors 6000–6007 |
| Those same eight actors retain ATTACK toward (120.5, 120.5) | Evidence `after_adapter.orders` |

Position placement is deliberate fault construction, not a claim about how frequently this state occurs in live games. The result demonstrates a reachable state-handling defect; it does not measure win rate. The local reproducer is `.build-step/jev-jdi-overnight/audit_recovery_repro.py`; its initial unused-import error was corrected before execution, with no product source change.

**This defect also exists before the candidate.** The same controlled setup against working-branch gameplay source `3f89a4f` accepted recovery at loop 104 and retained all eight attack tasks/orders with zero recovery commands: offline run `ec14a829a8874a498b2289635fe59e69`, acceptance #1603, move-success #1695, branch-success #1696. The evidence JSON's `baseline_comparison` contains the corresponding post-adapter actor state. The older fixture lacks an after-step hook, so the audit wrapped the original `JevBot.on_step` only to observe its return before world advancement. This establishes an unresolved integration defect, not a newly introduced regression or evidence that the candidate makes play worse. Root already uses position-only satisfaction (`operations.py:2276`) and its early satisfied-intent path (`runtime.py:2275`).

## Complete site inventory for this bug shape

Search scope was `src/`, `tests/` and `bots/`, using `army_eligibility(`, `effective_army_mode(`, `satisfied=`, `.satisfied`, `_finish(`, and the packaged graph's move/attack/gather actions. Line references below are at **00bc515**. Exposure is not proof of a defect at every site.

| Surface | Sites and disposition |
|---|---|
| Shared feasibility producer | `operations.py:713` (`army_eligibility`), `:683` (`effective_army_mode`). Still hand-maintains selectors. |
| Graph guard consumer | `operations.py:1734`, effective selection at `:1753`, reported group size at `:1763`. |
| Runtime consumers | `runtime.py:1175`/`:1191` assemble ownership/visits; `:1426`/`:1430` choose effective mode; `:1432` transition tasks. |
| Question/context consumers | `strategy.py:676`, `:712`, `:954`, `:963`, `:979`. Ordinary runtime and direct-context fallback paths must agree. |
| Actor-order mismatch | `operations.py:779` prefers assigned attackers; `policy.json:3829`/`:3833` tries a full free home wave first. Option availability may still agree while reported group size and actor identity differ. |
| Shared satisfaction/ownership consumer | `runtime.py:2345` existing-intent shortcut, `:2350` satisfied shortcut, `:2422` refusal, `:2450` assignment; `:1802` task finish. Review compatibility and actual orders, not only task IDs. |
| Point-intent producer | `operations.py:2522`, exposed by move and attack at `:2544`/`:2548`. Confirmed recovery case above. |
| Other satisfied-intent producers | `operations.py:2269` gather checks current harvest ability; `:3255` expansion build checks an existing structure; `:3305` worker transfer checks current gather targets. Their completion conditions differ; do not blindly force replacement commands for every satisfied intent. |

The complete packaged action inventory is:

| Policy | Node IDs (policy.json line) | Assessment |
|---|---|---|
| v2 | `expansion.site_safe.retreat` (2347), `expansion.move` (2417), `scouting.retreat.go` (2706), `scouting.move` (2948) | Shared point-move path; audit incompatible holder/order combinations. |
| v2 | `army.recover.move` (3682), `army.rally.move` (4123), `reserve.gather` (4314) | Recovery reproduced; rally/reserve are sibling exposures. Reserve explicitly does not preempt. |
| v2 | `army.defend.air.attack` (3386), `army.defend.attack` (3463), `army.attack.go` (4034) | Unit-target defense compares ability/target; attack can resolve to a point and shares point semantics. |
| v2 | `economy.assign` (152) | Gather satisfaction uses order state, a distinct condition. |
| v1 | `economy.assign` (97), `army.defend.attack` (1032), `army.attack.go` (1282), `army.rally.move` (1355) | Shared-runtime compatibility exposure only. No frozen-v1 edit or baseline recapture is authorized. |

## Remaining production-test gaps

The final reviewer confirmed that `tests/test_jev_v2.py:1436` tests pending-response threat onset through a direct in-process service rather than `run_match`/mock HTTP. Assertions at `:1580` and `:2021` accept an order on any combat unit without tying the continuing task to its actor, ability and target. Defense at `:1671` checks final orders instead of the acceptance tick; recovery at `:1774` and target loss at `:1943` stop at runtime events instead of the exact post-adapter orders. These cannot establish Step 232's Done-when contract. See [candidate tests](https://github.com/aberson/Alpha4Gate/blob/00bc515/tests/test_jev_v2.py#L1436) and [Step 232](../plans/jev-decision-integration-plan.md#step-232-make-local-emergency-execution-independent-of-model-intent).

One suggested test repair was rejected by the final judge: requiring a nonempty army request in every invalid-option scenario is wrong when only one legal option remains. The coordinator deliberately skips questions with fewer than two options (`strategy.py:1497`). Verify omission using decision/context evidence, or construct a genuinely multi-option case. Do not invent an unnecessary model request to satisfy a test.

## Minimal next design and regression proposal

Before another implementation attempt, define one side-effect-free representation of the graph's ordered branch resolution: effective branch, ordered actor tags, exact target and whether an incompatible command must be replaced. Option construction and execution should consume that same result. The model must still only select graph options; the graph retains legality and commands. Avoid a second general execution engine or another hand-copied eligibility predicate.

Specify the transition invariant explicitly: a branch may claim success only if the actor's existing order is compatible, or the graph has issued the required replacement. Spatial arrival and task cancellation alone are insufficient. Review the shared move path's v1 compatibility before choosing repair scope.

Use a focused table-driven production regression through `run_match` and mock HTTP. For each decisive tick, correlate request/rejection → selected guard → task ID → actor tag → exact ability/target after adapter application. Cover: simultaneous free wave and assigned group; recovery while already home but still attacking; ground/air threat onset during pending HTTP; group/target loss; malformed/out-of-set/expired answers; newly started fallback versus valid continuation. Differentially compare offered/effective resolution with the real graph for these cases. Keep v1's existing behavior assertions and frozen bytes unchanged. This is a proposed repair/test design, not an implemented or accepted test suite.

## Gates and preservation

The final candidate passed full Python **3419 passed, 13 skipped, 2 deselected**; focused safety tests **253 passed**; strict mypy **832 files**, Ruff and installed lint prepass passed. Unchanged frontend evidence is **417 passed, 6 skipped**, build/lint passed. Local receipts are `worktree_build-step-232-jdi/.build-step/round3-{pytest,mypy,ruff,prepass}.log`; frontend receipt reuse is recorded in `review-selection-232-r3.md`. Green mechanical checks do not override the confirmed defect.

Six fresh lenses and the required final judge completed. Correctness and test-quality rejected the candidate; security/style passed. The canonical reducer separately failed closed because correctness and bugs used invalid `model_tier: logical-sonnet` metadata. No aggregate PASS was produced or fabricated; raw reports remain unchanged. The final judge confirmed the substantive hold independently. Root/worktree byte inventories were unchanged throughout review. Twenty-one review calls across three rounds and one diagnostic consultation were used; the original round budget was not reset.

The applicable build-step stop rule says: **“Third instance: STOP and audit.”** This audit satisfies the required sibling inventory and structural regression proposal; it does not itself authorize a fourth patch or accept the candidate. Keep issue #355 open. JON's partial offline contract work remains separately preserved at `23c5d8b`; its expired attempt is not restarted. Steps 233 onward and paid experiments remain pending.
