# Jev baseline and controlled-speed investigation

Started 2026-10-11 Pacific time (run timestamps cross into 2026-10-12 UTC).
This is development measurement before recovery or v13 behavior changes, not J2
acceptance, the deferred frozen-v1 benchmark, or a held-out evaluation.

## Preserved source

- Pre-pacing source: `4b7bfd1cc77f349e45b253bd57253d2d737eb4c052239214b43d6604f537ca15`.
- Pacing/instrumentation source: `ebdb5891f3f5e631d58fe53aefb40ace03aac6ce37fda7bbd9402322a26ef356`.
- Both use policy `a1c73ea8b3335a335a742031d5bd9990471fda536ce35cc541c1eeab7a2351d3`
  (v2, 292 nodes, nine branches). No recovery, targeting, production, confidence,
  model request cadence or freshness change was made between these captures.
- Snapshots: `data/jev/experiments/speed-2026-10-11/sources/`.
- Case records: sibling named directories, with source hashes and diagnostics.
- Per-run policy, trace, replay and diagnostics: `data/jev/runs/<run_id>/`.
- Original source files were verified byte-identical after its match. Generated
  Python caches were preserved outside the snapshot; later game children disable
  bytecode writes. This does not change the source fingerprint.

## Fixed experimental settings

Jev v2, Typesafe `jev-1.13.0`, Medium Terran, Simple64, seed 11. Maximum 450 requests,
900 game seconds and 1200 gameplay wall seconds per case. A time limit or operator
stop is not scored as a win. One game per setting is exploratory; the seed does
not make remote model answers deterministic.

The operator clarified during the preliminary 2x run that attended investigations
must use the combined viewer, now named **Observer Rush**. The earlier separate
React/SC2 runs remain useful raw evidence, but are not pooled with viewer results.
Observer Rush comparisons keep the camera/caster on and recording off. Their
launcher also records viewer file hashes independently of the frozen gameplay.

## Preliminary separate-window results

| Case | Result | Game duration | Measured gameplay wall time | Achieved speed | Accepted / answers | Stale | Low confidence | Median response |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Native realtime | Win | 402.72 s | Not instrumented | Not instrumented | 97 / 119 | 11 | 11 | 534.1 ms |
| Paced 2x | Win | 646.96 s | 386.66 s | 1.6732x | 104 / 145 | 11 | 30 | 481.2 ms |

Run IDs: native `7f1aa2516cc8416087ec34aa2fabae8e`; paced 2x
`df7248e4e68c480fbbaeba7b2bd8affb`. No service failures in either run. The 2x case
sent 146 requests, with one still pending at match end. Both completed naturally.

The army coordinator recorded a hosted choice in force for **71.88%** of the
native run's post-first-attack window, versus **49.34%** for preliminary 2x.
These are time-weighted trace-source fractions over different game trajectories,
not causal estimates of how many orders the model controlled. Accepted replies
alone would miss the fallback gaps. Reproduce with `scripts/report_jev_speed.py`.

These two games do not establish a strength or total time-to-victory improvement.
The faster simulation took more game time to win. Hardware throughput, model
decisions and different game trajectories all matter; native and paced scheduling
also differ. Use the matched Observer Rush series for the next comparison.

## Observer Rush series

Paced 1x, 2x and 4x use the same pacing-capable source snapshot. Recording is off.

| Requested cap | Result | Game duration | Gameplay wall time | Achieved speed | Accepted / answers | Stale | Low confidence | Median response | Hosted army-source time after first attack |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1x | Win | 803.04 s | 973.99 s | 0.8245x | 214 / 365 | 27 | 124 | 553.6 ms | 43.26% |
| 2x | Game-time cap | 900.00 s | 573.92 s | 1.5682x | 112 / 208 | 43 | 53 | 543.2 ms | 21.95% |
| 4x | Win | 715.36 s | 264.30 s | 2.7066x | 80 / 104 | 14 | 10 | 583.5 ms | 38.09% |

1x run: `2864335894684aea95d9abfaae3f9ebb`. It sent 368 requests: 365 answers,
one invalid response, one request failure, and one request pending at match end.
The army selector chose recovery for 263.57 game seconds after the first attack,
attack for 338.75 seconds and defense for 48.93 seconds. Selector choice is not
the same as unit idleness; existing orders and other branches can still operate.

2x run: `24279aee9fc84867b8ab7a7e36d71468`. All 208 calls returned answers, with
no service failures. The match reached the game-time cap; it is not a win.
Final worker count was two after 73 unit and 16 structure losses. The army selector
recorded 116.07 seconds of recovery, 224.64 of attack, and 245.71 of defense
after the first attack. Its snapshot verified unchanged after the run.

The 1x control completed naturally and all frozen source hashes verified after
the run. The new name, native SC2 fit, graph and controls were visually checked.

4x run: `41c59da87f574d76accc6ebaae9bcc83`. All 104 calls returned answers,
with no service failures or pending request at match end. The match won naturally
just before the operator requested a pause for RAM. Its source verified unchanged.
The army selector recorded 463.57 seconds of attack and 42.50 seconds of defense
after the first attack, with no recorded recovery branch time in that window.
This differs substantially from the 1x trajectory and prevents attributing the
better outcome or elapsed time to speed alone.

The 4x cap is a promising accelerated development setting, achieving 2.71x here,
but one game per setting does not establish reliable strength. Keep ordinary
launch defaults unchanged; repeat matched development seeds before standardizing
an accelerated evaluation speed. Preserve both 1x and 4x for subsequent comparisons.

## Operator pause and queued viewer tweaks

Paused at a natural match boundary at the operator's request to reclaim RAM.
SC2 and Observer Rush closed; the local caster model was unloaded and the dedicated
OBS instance was asked to close normally. No additional match is scheduled.
Before resuming gameplay work:

1. Rename the visible caster to **Archon Auto Caster**.
2. Make the node info box follow an end node of a newly highlighted path; choose
   randomly when several paths qualify. Investigate why current caster highlights
   do not update the inspector. Preserve manual inspection through the existing
   follow/highlight controls or a clear opt-out.

These two tweaks are recorded, not implemented during the pause.

## Implementation and checks

The optional `--game-speed` runner setting paces non-real-time observation steps
without changing SC2 step size or policy tick cadence. Sleep yields to asynchronous
service work. Stop/wall deadlines remain responsive, and slow frames cannot build
up credit for a later catch-up burst. No new setting changes default launches.

`metrics.timing` records game/wall time between the first and last controller steps;
startup, viewer readiness and replay saving are excluded. Historical archives
without this field remain readable. Formal fixed-panel benchmark settings are
unchanged; these development cases have their own manifests.

Focused runner/pacing tests: 151 passed (one real-SC2 test deselected). Observer
Rush launcher tests: six passed. Ruff and strict mypy passed for the pacing/runtime
and measurement command. Frontend build passed with the existing large-bundle
warning. Eight frontend viewer tests passed. The combined live window was visually checked,
including the SC2 fit,
graph and Observer Rush naming.

## Next work, in operator-approved order

The first exploratory baseline/speed series is complete. Resume with the queued
viewer tweaks, then recovery completion and attack
arbitration, then selected v13 grouping/re-engagement behaviors, then matched
before/after comparison. Choice flattening remains deferred. Do not infer any of
those behavior improvements from the new pacing control alone.

[Runbook](../operator/jev-speed-measurement.md) ·
[Observer Rush](../operator/jev-battle-view.md) ·
[Recovery diagnosis](jev-recovery-stall-2026-10-11.md)
