# Jev safety repair: bounded design, October 15

**Design only. Step 232 remains held at `00bc515`; no fourth implementation round is authorized or completed.** The next repair should remove duplicated decision logic and make success depend on actual orders. It should not add a general-purpose simulator, command authority service, or another eligibility framework. The existing [audit](jev-integration-safety-audit-2026-10-14.md) remains the governing failure record.

## What the additional investigation establishes

The previous reproduction covered recovery *starting* while the army was already home but still attacking: run `16fad34c92db4e5c8c5ea83ba3fcdcbf`, acceptance #1519, false-success #1611–1612. Cancellation and spatial proximity are not sufficient evidence of command replacement.

A new controlled offline run covers recovery *finishing*. Let the production path issue recovery MOVE tasks normally. Before the next observation, place the same eight actors at the rally point with ATTACK orders toward `(120.5, 120.5)`. This deliberately injected observation isolates an incompatible-order case; it does not estimate its frequency in real games. The harness uses `run_match`, mocked HTTP, the real JevBot/runtime/adapter and protobuf-backed simulated units. The real launcher is forbidden and the HTTP client requires `MockTransport`.

| Decisive tick, run `dcd67be496f048dcbc5e9ad219422e4f` | Evidence |
|---|---|
| Eight existing recovery tasks encounter incompatible orders at loop 112 | [Evidence JSON](jev-recovery-lifecycle-evidence-2026-10-15.json), `injected.tasks` |
| Those tasks are acknowledged and immediately marked succeeded | #1649–1664; success events #1650, 1652, 1654, 1656, 1658, 1660, 1662, 1664 |
| Recovery action and branch report success | #1754–1755 |
| No replacement recovery command is emitted; no army task remains | `commands_on_tick: 0`, `after_adapter.tasks: []` |
| All eight actors still have ATTACK orders toward the enemy | `after_adapter.orders`, tags 6000–6007 |

The reproducer is local `.build-step/overnight-2026-10-14/recovery_lifecycle_probe.py`; source stayed at `00bc515`. Three point-movement checks share the weakness: initial intent satisfaction at [operations.py:2522](https://github.com/aberson/Alpha4Gate/blob/00bc515/src/jev/operations.py#L2522), acknowledgement at [operations.py:3461](https://github.com/aberson/Alpha4Gate/blob/00bc515/src/jev/operations.py#L3461), and lifecycle completion at [operations.py:3484](https://github.com/aberson/Alpha4Gate/blob/00bc515/src/jev/operations.py#L3484). Fixing only the first would leave the latter two able to certify an incompatible state.

This is separate from the baseline test-lifetime failure: that test's normal simulated army moves home, settles, and legitimately releases the strategic override. Correcting that assertion does not resolve or accept Step 232.

## Proposed repair boundary

First, establish one order-aware completion rule for point movement and use it at intent construction, acknowledgement and task completion. Arrival with no remaining incompatible orders can complete. Arrival while attacking elsewhere cannot complete recovery. A compatible movement order can continue; any queued incompatible follow-up must also be accounted for. Use the existing graph MOVE command to replace incompatible orders, with existing ownership, preemption and command-budget checks. Do not add a new STOP command or make every satisfied action issue a redundant command. Gather/build/train have different completion contracts and remain outside this change.

Task cancellation remains bookkeeping. In [runtime.py:1802](https://github.com/aberson/Alpha4Gate/blob/00bc515/src/jev/runtime.py#L1802), `_finish` updates records and emits task events; it sends no SC2 command. Transition logic must not advertise successful recovery merely because it cancelled an attack task. The adapter's accepted command means “awaiting observation,” not observed execution ([sc2_adapter.py:937](https://github.com/aberson/Alpha4Gate/blob/00bc515/src/jev/sc2_adapter.py#L937)). A refused replacement must remain visibly unsuccessful, never become a completed recovery.

Second, remove the hand-maintained army selector mirror. Its current actor ordering is already different: `army_eligibility` prefers assigned attackers ([operations.py:779](https://github.com/aberson/Alpha4Gate/blob/00bc515/src/jev/operations.py#L779)); the policy tries the free home wave first ([policy.json:3829](https://github.com/aberson/Alpha4Gate/blob/00bc515/bots/jev/v2/policy.json#L3829)). More predicates in that mirror would preserve the underlying drift risk.

The bounded replacement should resolve the existing army branch's authored predicates and selectors through their existing operation implementations, producing its ordered actors, exact target and action intents. Both offered options and the chosen execution branch must consume this resolution. Keep the scope to the v2 army branches and their existing operations; reject unsupported effects during read-only resolution. Reuse the interpreter's sequence/selector ordering and binding rollback rather than writing another traversal with different rules. The launch latch must be staged until execution, and previews must not claim actors, mark visits, emit telemetry or spend resources. These are required design constraints, not a claim that such a resolver already exists.

Local emergency defense should be selected before consulting the model preference. An unavailable preferred branch must allow the graph's local fallback on the same tick. Retrying an old task must not consume the actor's command slot before an emergency replacement is selected: the current retry pass precedes graph evaluation ([runtime.py:1464](https://github.com/aberson/Alpha4Gate/blob/00bc515/src/jev/runtime.py#L1464)). Resolve this ordering with the same selected actor/action result, not a second cancellation predicate. Reserve ownership, command-budget exhaustion and adapter refusal need explicit outcomes; do not claim that a local planner can guarantee SC2 accepts an order.

## Decisive regression requirements

Use the existing production fixture path; capture immediately after adapter application and before simulated world advancement. For each affected actor correlate request id, chosen branch, task id, command outcome, ability and target. A successful unrelated unit or a later end-of-match snapshot is insufficient.

| Scenario | Required observation |
|---|---|
| Already-home recovery with ATTACK still active | Each affected actor gets a compatible replacement or an explicit failure; no false-success event |
| In-flight recovery arrives with a conflicting order | Acknowledgement/completion cannot certify recovery from position alone; reproduce #1649–1755 above |
| Idle or compatibly moving army already home | No unnecessary order spam; ordinary completion still releases the override after settling |
| Simultaneous free home wave and assigned attackers | Preview and execution choose the same ordered tags and exact target according to the policy |
| Ground/air threat arrives while an HTTP answer is pending or accepted | Exact capable actors defend immediately, irrespective of attack/regroup preference; incapable units are not assigned air attacks |
| Target/group disappears, or response is malformed, expired or out of set | Local fallback runs in the same tick; unrelated valid continuing tasks survive |
| Zero army, cross-lane owner, retry due, command limit or adapter refusal | Invalid options omitted; no empty-army recover; blocked work does not report execution success |
| Frozen v1 and ordinary non-army actions | Frozen policy bytes unchanged; existing behavior checks pass without recapture |

The first order-completion repair is a small independently testable prerequisite; it is not sufficient to accept the entire safety step. Until a concrete implementation boundary is approved, preserve `00bc515` and its three failed rounds. No playing-strength claim follows from these offline probes, and no hosted calls were made.

## Independent challenge and proposed clarification

The independent read-only challenge returned **NEEDS_DESIGN**. It confirmed the defect and rejected treating a stricter completion predicate as a complete repair: an already-running task bypasses acknowledgement checks and the existing-intent shortcut can leave it ineffective until the no-progress deadline (`runtime.py:2074`, `:2117`, `:2345`). It also required a concrete resolution/commit boundary. The clarification below is a proposal, not a passing review or authorization for another build round. Full review is preserved in local `.build-step/overnight-2026-10-14/safety-design-challenge.md`.

Use an order-state classifier shared by the three callers, with different acknowledgement and completion rules. For recovery MOVE to point P, let “arrived” retain the graph's `arrive_within` distance, and let a matching target retain the existing `_target_matches` tolerance. Inspect the entire observed queue. Unknown abilities are incompatible, not silently acceptable.

| Observed state | Acknowledgement/continuation | Completion | Required action |
|---|---|---|---|
| No orders; arrived; no incompatible holder/retry | May acknowledge an issued move | Complete | None |
| No orders; away | Not acknowledged | Not complete | Issue/reissue through existing bounded lifecycle |
| Queue contains only MOVE to P; away | Compatible continuation | Not complete | Preserve it |
| Queue contains only MOVE to P; arrived | Compatible continuation | Complete only with no incompatible future holder/retry | No redundant MOVE |
| ATTACK, different destination, unknown ability, or incompatible queued follow-up | Incompatible, even when arrived | Never complete | Reconcile before allowing old retry/continuation |
| Compatible observation but incompatible pending task owns actor | Observation alone is insufficient | Not complete as the new branch | Transfer only if graph preemption permits; otherwise block |

For attack-move, acknowledgement requires its matching ability/target (or observed idle arrival); completion additionally retains the existing target-clearance condition. This must not turn “still fighting at the point” into arrival success. Unit-target attack retains its separate target-liveness contract. Non-army gather/build/train are excluded. Before sharing changed behavior with v1 or scouting/expansion movement, explicitly demonstrate compatibility using their unchanged tests; if that cannot be done, keep the behavior opt-in on v2 army actions rather than changing those contracts implicitly.

| Existing task and selected action | Proposed reconciliation |
|---|---|
| Issued/running task, compatible observed order and same selected action | Preserve task and order |
| Issued/running task, incompatible observation, same selected action | Route immediately through the existing bounded reissue mechanism; no new retry counter; one dispatch per actor per tick; never wait for the 30-second movement deadline merely because the intent key exists |
| Different same-lane task and selected action permits preemption | Validate replacement first; suppress old retry, cancel old bookkeeping, create replacement; remain awaiting observation |
| Other-lane holder or no preemption permission | Explicit blocked actor; preserve legitimate owner; do not report that actor recovered |
| Budget unavailable or adapter refuses | Record blocked/refused actor; no completion claim; existing bounded retry/fallback policy applies |
| Mixed actor results | Report per-actor outcomes and partial running state; whole-group recovery cannot be certified from one successful actor |

The smallest credible graph change is a narrow extraction of the existing army traversal/action planning, with a read-only resolution mode, rather than a general runtime dry-run. Supported operations are the actual army subtree's predicates (`army_mode_is`, `strategy_is`, `strategy_local`, `latch_is_set`, `count_compare`, `threat_within`, `visit_count_compare`), selectors (`select_entities`, `select_point`, `select_locations`), and terminal `move`/`attack`; `set_latch` is the sole staged effect. Unsupported effects reject preview. These are a bounded allowlist to validate, not permission to clone their implementations.

Proposed ordering:

```text
Preview from one immutable observation plus current task/assignment/visit state.
  Use real ordered selectors and action planners; local defense wins first.
  Preference guards only compare the proposed choice; they never call eligibility.
  Stage set_latch in the preview frame; never mutate live runtime during preview.
At army-lane execution, resolve again after earlier lanes changed ownership/budgets.
  Revalidate a returned model choice against this resolution; fall back locally if invalid.
  Validate each actor's ownership and replacement budget.
  Suppress retries only for actors selected for a permitted replacement.
  Preserve unaffected compatible tasks; reconcile incompatible same-intent tasks.
  Commit staged launch latch with the first eligible attack dispatch or compatible
  continuation, never merely because a question preview reached the latch node.
  Dispatch through the existing adapter; retain awaiting-observation/refusal state.
On observation, classify order compatibility and arrival separately, then complete.
```

A resolution is tick-local, not a durable permit. Delayed responses never reuse old actor/target selections. Request construction may use a current preview; execution recomputes against current state. Retry suppression must use the same resolved per-actor actions, including explicitly authorized reserve transfers, and must not cancel unrelated cross-lane work. This clarification makes the open decisions reviewable, but has not been independently accepted; do not present it as a finished repair.
