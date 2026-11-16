# Independent Typesafe Jev code review

Date: 2026-10-08
Verdict: PASS for the offline integration, with live validation pending.

Scope: Reviewed the current product diff, new `src/jev/decision.py` and
`tests/test_jev_decision.py`, the Phase JI plan, and the relevant runner,
controller, graph, runtime, telemetry, and Jev dashboard seams. This was one
independent code review with focused follow-up on fixes; it was not a build-phase
or review-deep protocol run.

The review found three correctness issues, all resolved in the reviewed candidate:

1. The provider originally offered `defend` for enemies near any own structure
   and for enemy structures, while the packaged defense graph only selects
   non-structure ground enemies near `start_location`. `context()` now applies
   the graph's location and entity filters. The regression covers a distant
   Pylon threat and an enemy structure near home.
2. The response parser originally accepted a chosen option with a lower
   probability than another offered option. It now requires the choice to share
   the highest probability, with an explicit tie test. This matches the
   documented TypeSafe Choice response contract.
3. A demoted defense target originally remained eligible for model `defend`
   requests even though the graph skipped it. The controller now passes the
   runtime's existing demoted target set to the coordinator. `context()` excludes
   those tags from eligible home threats and the choice signature while keeping
   the raw visible enemies in state. Tests cover answer invalidation, the next
   offered options, and controller forwarding.

The provider request shape, Bearer authorization, Choice response fields, and
model identifier match the [TypeSafe API reference](https://docs.typesafe.ai/api).
The inspected credential path reads `TYPESAFE_API_KEY` from the environment and
does not put it in recorded decision facts. The runner-to-provider mock transport
test checks an issued graph command and persisted model/source evidence. The
parent reported 30 focused decision tests passing; I did not rerun the broad
suites independently.

This verdict covers code and offline tests only. A real API key, hosted response,
SC2 match, and dashboard observation are still required for the plan's live
acceptance gate; none is claimed here.
