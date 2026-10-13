# Jev overnight preparation: gate history and historical opponents

Investigated 2026-10-12. Preparation only; no paid matches launched.

## What the old gates actually did

| Stage | Evidence | Meaning |
|---|---|---|
| Advised loop | `.claude/skills/improve-bot-advised/SKILL.md`, Validation Philosophy and `--fail-threshold` | One fix at a time; default 10 observation games and a configurable 30% win-rate-drop rejection rule. This is regression tolerance, not proof of improvement. |
| Individual fitness plus composition | `e63cb46` (2026-04-21), `aa9190e` (2026-04-22) | Candidate-versus-parent testing plus a separate combined-candidate gate. |
| Composition removed | `f4daa7a` (2026-04-23), `documentation/archived/evolve-gate-reduction-plan.md` | Removed a redundant empirical gate; retained import validation and post-combination regression. |
| Current default | `src/orchestrator/evolve.py`, `run_fitness_eval` and `run_regression_eval`; evolve skill | Five games per evaluation; 3/5 strict-majority pass. A neutral, independent, decisive matchup passes an odd-sized majority test half the time. More games reduce uncertainty but do not change that null pass probability. |
| Optional posterior rollback | `becf306` (2026-07-06), `src/orchestrator/gate_stats.py` | Roll back on posterior probability of harm >=0.85, with at least four decisive games. At five decisive games, 0-5 and 1-4 roll back; 2-3 survives. Surviving does not prove stronger play. |
| Optional historical floor | `d69ae6a` (2026-07-06), `scripts/evolve.py` | A sweep loss against a registered frozen baseline can roll back promotion. Flags are optional, not silently active for every run. |

The remembered small gate is real: 3 wins out of 5. Three-game smoke configurations
also exist; they were pipeline checks, not convincing strength measurements.
The July investigation explicitly discusses noisy promotions and repeated testing:
[evolve judging alternatives](evolve-judging-alternatives.md).

## What exists for history weighting

`src/orchestrator/evolve.py::run_baseline_gauntlet` runs head-to-head against named
historical bots and computes an **equal arithmetic mean** of per-baseline win rates.
`src/orchestrator/baselines.py` stores name/version/note, not explicit weights.
`src/orchestrator/selfplay.py::pfsp_sample` separately samples opponents using
`(1 - win_rate) ** temperature`; fully beaten opponents receive zero weight.
Sampling hard opponents and computing an evaluation score are different operations.
PFSP alone cannot preserve coverage of previously mastered opponents.

For Jev, retain immutable champions and define weights before candidate testing.
Initially compare their performance on the same built-in-AI case matrix, because
the current Jev runner/Observer Rush path is not a Jev-versus-Jev self-play adapter.
This is a weighted **reference score**, not head-to-head evidence. Direct historical
opponents are a later adapter milestone. Do not claim that adapter already exists.

Use per-race and per-difficulty regression floors in addition to the weighted total.
A moving average of successive winner scores by itself would still allow drift.
Keep a permanent original anchor and rotate held-out cases between assessment epochs.
Repeatedly exposed cases become development data, never remain labelled held-out.

## Spending evidence

The operator reports about $0.20 spent to date; this investigation did not inspect
the account balance or invoices.

Verified 2026-10-12 against the official [Typesafe model documentation](https://docs.typesafe.ai/models):
`jev-1.13.0` costs $0.042 per million input tokens; output is free. The documented
request context ceiling is 64k tokens. Thus $5 corresponds to roughly 119 million
input tokens at that price. This is a token conversion, not a nightly cost forecast.

Current `TypesafeProvider.decide` reports usage after responses; it does not enforce
a dollar budget. Timeouts may have been billed despite returning no usable usage.
Existing benchmark request caps are useful but are not dollar accounting.

The new controller must reserve worst-case cost before sending, include probes and
failed attempts, persist reservations across crashes, and stop when pricing or
charge bounds cannot be established. Use integer currency units, not float dollars.
The advertised $5 control is for the managed run under verified billing terms;
it cannot limit unrelated clients using the account or unilateral provider changes.
An account-side spending limit would provide an additional boundary if available;
availability has not been verified. No new billing setup is needed for this prep.

See the [build plan](../plans/jev-overnight-improvement-plan.md).
