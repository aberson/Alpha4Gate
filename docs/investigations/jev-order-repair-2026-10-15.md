# Jev recovery repair and visible gameplay evidence — October 15

Status at 08:33 Pacific: further repairs authorized; two candidates rejected, third repair pass (round6/10) in progress. No new gameplay code has been accepted or applied to the default launcher. Whole master-plan Step232 remains open. [Watch the comparison](../jev-recovery-comparison.html).

The selected increment repairs observed movement-order lifecycle, not broader hosted-model choice or campaign authority. Original held candidates225/232 remain untouched. Current worktree is `worktree_jev-order-repair`, baseline `0f0753c`; first candidate source is frozen in `.build-step/jev-order-repair/review-r4/inputs.json` and `source/`. Root's unrelated documentation migration and source edits are preserved.

## Controlled real-SC2 comparison

These scenes use eight debug-created Zealots and a forced local-mock response through the production HTTP parser, controller, graph, runtime and SC2 adapter. The harness issues setup ATTACK orders, observes them, then releases the mock recovery answer; no direct setup orders follow release. They are labeled LOCAL MOCK / DEBUG SCENE in the recording, have zero external model requests, and are not evidence of hosted-model quality. Harness and source metadata: each run's `battle/local-demo.json`, `scene-ready.json`, `mock-decisions.jsonl`, `observed-orders.jsonl`; private harnesses in `.build-step/jev-order-repair/`.

| Measurement | Before: `eb8572e5937f4c429d37594ecb2cdcba` | First candidate: `5568a45d270a4d548deeb3a2ddcc5a09` |
|---|---|---|
| Accepted recover | #2589 at8.214286s | #2589 at8.214286s |
| Recovery action that tick | #2691: success, satisfied8, issued0 | #2715: running, satisfied0, issued8 |
| Raw SC2 orders at8.571429s | All8 retain ATTACK | Zero ATTACK:7idle,1MOVE |
| Delay to corrective MOVE | 0.714286–2.500000 game seconds (#2905/#2908 through#3443) | Same acceptance tick (#2693–2714) |
| Final recovery completion | #4113 at13.571429s | #4115 at13.571429s |

The improvement is prompt replacement of contradictory orders. Final completion was legitimate in both scenes and occurred at the same game time. The before observations bracket acceptance rather than sampling the exact acceptance instant. This is one controlled comparison, not a win-rate result. Full extracted event bodies and per-actor observations: `.build-step/jev-order-repair/before-analysis.json` and `after-analysis.json`.

## Ordinary harder match

Run `c7a98527acab4cd7a447e57f23207af4`: scripted Jev v2, Harder Terran (difficulty6), Simple64, seed11, game-speed1. No debug units or injected choices. Result: **loss**, no match error, zero model calls. Last metric sample586.428571s; terminal diagnostic586.607143s. Source was the frozen first candidate already imported before the second repair pass; this match cannot validate later source edits.

- Defense interrupted rallying at133.571429s: cancellation#35984, defensive ATTACK#35986.
- Local expansion issued Nexus construction at238.214286s: #70571.
- Three units fell below the four-unit regroup threshold at326.428571s: #103512; recovery MOVE#103518, accepted bySC2#103533.

The first launch attempt failed before a game began because OBS timed out changing video settings. The isolated portable OBS instance was restarted; the retry recorded and finished normally. Host receipts: `.build-step/jev-order-repair/harder-host*.log` and exit files. No SC2 process was killed.

## Review and next repair

First candidate: full Python **3411passed,13skipped,2deselected**; strict mypy833files and Ruff passed; frontend417passed/6skipped, build/lint passed. Two regressions failed on the baseline and passed with the candidate. Mechanical receipts are frozen in `review-r4/mechanical.json`.

Six independent lenses plus a strong final judge nevertheless rejected it: deterministic aggregate **7Block,1Nit**. Raw findings are retained, including two Blocks demoted toFYI by deduplication. Main defects: pending movement can retry before graph selection and duplicate a newly compatible order; pending recovery tasks can falsely settle; generic30-second posture expiry can release unfinished recovery. Tests missed true in-transit, acknowledgement, prolonged conflict and retry-limit cases. Evidence: `review-r4/raw-*.json`, `aggregate/2026-10-15T15-01-21.json`, `final-judge.md`.

The next pass reconciles pending tasks against fresh observations, leaves movement retries under current graph selection, blocks settlement on active recovery tasks, and gives executing recovery explicit completion/supersession semantics instead of generic30-second expiry. Defense priority, command ownership and retry budgets remain local. See [canonical Step232](../plans/master_plan.md#step-232-make-local-emergency-execution-independent-of-model-intent). No whole-step acceptance or broader model-benefit claim is implied.

Hosted spend: **$0**. No paid games, promotion, or frozen-v1 recapture. Typesafe credentials were untouched.

## Media provenance

- `../media/jev-recovery-before.mp4`: before raw OBS recording `battle/2026-10-15 07-29-04.mkv`, source seconds1–19.
- `../media/jev-recovery-after.mp4`: first-candidate raw recording `battle/2026-10-15 07-50-57.mkv`, source seconds1–19.
- `../media/jev-harder-terran.mp4`: ordinary Harder run recording `battle/2026-10-15 07-55-53.mkv`, source seconds570–590.
- Videos retain full graph/game layout, 1620×900, normal playback speed. Recovery excerpts are silent; harder excerpt retains game audio. Sampled frames visually checked Follow decisions and node details. Raw recordings remain local under their run IDs.

## Second repair pass checkpoint

Round5 passed3413 Python tests/13skips, strict mypy and Ruff, but six fresh lenses and strong final judge retained7Block findings. Invalid/failed refreshes still cleared recovery, and an accepted replacement could strand pending recovery ownership. New tests also needed decisive home-settlement, valid-replacement and due-retry defense timing assertions. Fresh source snapshot and corrected UTF-8 deterministic aggregate: `.build-step/jev-order-repair/review-r5/aggregate/2026-10-15T15-30-47.json`; original sidecar retained. No acceptance or root application.

A second controlled SC2 capture on round5, run`11112aca07534db6bd3df96ba0240cd9`, again acceptedrecover#2589 and issued8MOVE at#2715; raw8.571429s observation hadzeroATTACK. Completion#4200 at13.928571s. This confirms the immediate-order effect persists, not that the remaining lifecycle findings are fixed.
