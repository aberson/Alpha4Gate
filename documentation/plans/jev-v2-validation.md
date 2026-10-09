# Jev v2 validation: Step 213 baseline - 2026-10-09

Status: TESTING STOPPED AT OPERATOR REQUEST. Staging and hosted smoke passed. Step 213 (#330) remains pending/deferred; operator authorized Step 214 to start after the preserved v1 freeze.

Checkout: `115a9c5`. Runtime source clean. Policy validated with 84 nodes and hash `510a72dfb167b7200123cbc8436c912f02d3b0fb5798657db3c62e3f5f8f68ee`. Dry run resolves six cases, model `jev-1.13.0`, dashboard launch and source state `capture_pending`. Runtime fingerprint: `b53039a693cadf756e6cec4fc44c10534bcd9194079410a98b59a9e441bb1bfd`.

## Stages

1. Prerequisites and dry run: AUTO-PASS. No concurrent SC2 process; saved encrypted key exists; runtime source status clean; policy validation and baseline dry run exit 0.
2. Scripted staging: AUTO-PASS. Batch `fa10a65b5f5b4e009d725d4e6ae4c02e`, run `c2e43fbf2b3b4eb3ac8bd72555a905cf`. Benchmark exit 0; complete result `timeout` at the requested 120 game seconds; invalid cases empty. Diagnostics name frozen runtime `v1-b53039a693cadf75`, all 120 seconds covered, 13 accepted commands, zero rejected, zero hosted calls. Replay persisted.
3. Hosted smoke: AUTO-PASS for mechanical checks, operator-confirmed for the >=60-second live observation. Won at 262.545 game seconds, exit 0, 49 accepted commands / zero rejected. 35 hosted calls, 34 answers from `jev-1.13.0`: 27 accepted, 7 stale, zero service failures; latency median 559.1 ms; 66,296 input / 1,130 output tokens. First accepted answer at 190.580 game seconds, leaving 71.964 seconds of play. One request was pending at game end (not counted as an answer). Sampling reports 6.027 missed game seconds; do not treat missing coverage as zero. Separate Terran difficulty 1 / seed 1 match, option (a), launched through `scripts/launch-jev.ps1 -Version v1 -DecisionProvider typesafe -Difficulty 1 -Seed 1`. Run `538cdad40d2f4a4db28d5aa0681d3420`.
4. Baseline: INTERRUPTED at operator request. Zero completed cases; case 1 interrupted, five not started. Batch `94ac2c35b17f4cbaa80020b1c859ba00`; real probe passed, actual model `jev-1.13.0`, latency 865.8 ms. Session `744b6901610248c485cdaa938c6941bb`. Default limits unchanged.

## Dashboard-first evidence

Staging session `e1bf3e7b92cf40dc97604e64aa0eafbb`: exact run/policy readiness receipt at `2026-10-09T19:29:07.042+00:00`, launch release at `19:29:07.079+00:00`, SC2 client launched log at `12:29:25.718` Pacific (`19:29:25.718` UTC). Operator explicitly confirmed: ?Yes, it opened correctly and went Live?, without menu interaction. Mechanical receipt and visual observation are separate evidence.

Smoke session: `15f5356bb0f14b8682749f44fd6ed7c6`. Baseline same-tab following remains unverified.

## Findings and next build

No staging infrastructure failure. Baseline gameplay findings and metric coverage remain pending. Next authorized automated span is Steps 214-221 (#331-#338), stopping before operator Step 222 (#339), by explicit operator deferral of the remaining baseline. No v2 code changes during baseline validation.

## Stop, evidence limits and operator direction

Operator: ?Seems to be working great, the AI needs improvement but that's to be expected. you can stop the test and let's proceed with improvements.? This explicitly changes build sequencing; it does not certify harder-opponent performance.

Baseline session `744b6901610248c485cdaa938c6941bb` was interrupted in first case
`v1-typesafe-simple64-terran-3-11`, run `e28636a493f14fef847023e1b7c59e8d`,
last recorded game time 67.143 seconds. The host interrupt exited 1 and ended
the process tree without terminal archive/diagnostics persistence. Process
inspection confirmed no remaining benchmark, Jev match or SC2 process. No clean
Ctrl+C shutdown claim is made. The raw run archive is retained unchanged.

Using the production BatchStore lock and label_interrupted recovery, recorded
case 1 invalid/interrupted, five cases pending and batch interrupted. The known
run ID was attached from its launch session. The invocation conservatively
charges the full 450-request allowance plus its probe; actual interrupted
attempt spend is unverified (last recorded call count was zero). The launch
session is explicitly stopped. No service request or new game was made during
recovery. `--report` exits 0 and reports zero running cases.

Frozen baseline was finalized at `2026-10-09T19:35:20.778+00:00`, fingerprint
`b53039a693cadf756e6cec4fc44c10534bcd9194079410a98b59a9e441bb1bfd`. Preserve
`data/jev/benchmarks/baselines/v1.baseline.json` and its snapshot byte-for-byte.

Needs you: no action needed to start the authorized improvements build. Same-tab
multi-game following and the six-case harder-opponent screen remain unverified
and deferred; do not launch further paid games without a new request.

Verdict: implementation may proceed by operator direction. Stronger-play
acceptance remains pending. No ranked harder-opponent gameplay findings can be
claimed from this interrupted first case.
