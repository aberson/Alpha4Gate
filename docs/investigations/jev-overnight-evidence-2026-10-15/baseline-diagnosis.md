# Recovery test lifetime diagnosis

Baseline: `c0836c3ebb1df70968ebf71e9fceda5aca2c19e5` on `fix/jev-recovery-test-lifetime`.

## Reproduction

`uv run pytest "tests/test_jev_v2.py::test_each_strategic_choice_selects_its_graph_branch[recover]" -vv --tb=long` failed at the final `assert plans == ()`. The final runtime snapshot instead held one waiting `tech` plan attributed to `local_fallback`, with no request id, started at 11.428571 game seconds.

The test runs 40 mocked SC2 steps through `_hosted_match` -> `run_match` -> `JevController` and the mocked HTTP decision provider. I temporarily printed the relevant in-memory production events from that unchanged path, then removed the prints. No hosted network or SC2 process was used.

## Exact observed timeline

Instrumented run id: `8329fa04ffed48149d2b73219ea32f73`. Hosted strategy request id: `3`.

| Boundary | Event sequence | Game loop | Evidence |
|---|---:|---:|---|
| recover answer accepted | 1670 | 104 | Strategy diagnostic: `source=typesafe`, `choice=recover`, `in_force_request_id=3` |
| chosen branch passes | 1770 | 104 | `army.recover.chosen` succeeds with `source=typesafe`, `request_id=3` |
| recovery commands issued | 1776-1797 | 104 | Eight `army.recover.move` command events, one for each ready Zealot |
| active recovery first reported | 1816 | 112 | `status=regrouping`, 0/8 units at home, started at 5.0 game seconds |
| army crosses home threshold | 2050, 2164, 2278 | 128, 136, 144 | Units at home progress 3 -> 5 -> 8; status changes to `settling` at loop 144 |
| movement completes | 2846-3084 | 184-200 | All eight move tasks report `succeeded`; by loop 200 the graph also reports all eight actors already at the intended state |
| recovery completes and override releases | 3885 | 256 | Strategy diagnostic: `status=completed`, 8/8 home, settled 5.0 seconds, `source=local_fallback`, no in-force request; completed at 11.428571 game seconds |
| local macro fallback starts | 3934-3935 | 256 | `tech.start.go` records and succeeds after sequence 3885; the plan is `decided_by=local_fallback`, `decision_request_id=None` |

Before sequence 3885, every recovery strategy diagnostic remained attributed to Typesafe request 3, every successful `army.recover.chosen` guard was attributed to that request, and neither macro start node succeeded. The final test snapshot is at loop 312, well after recovery completion and the legal fallback start.

## Conclusion

This is an obsolete test lifetime assertion, not a runtime defect. The assertion was written when hosted army postures remained in force through the whole short match. Shipped recovery completion now intentionally releases `recover` after the army settles at home, allowing local macro fallback. The production timeline shows the required ordering and attribution.

The test-only repair preserves the original branch checks and additionally proves:

- no macro plan starts while the accepted recover request remains in force;
- the recover guard remains attributed to the accepted hosted request;
- the mocked SC2 army actually moves home and its movement tasks succeed;
- recovery reaches observable completion before any macro start;
- the subsequent plan is local `tech`, with no hosted request attribution.
