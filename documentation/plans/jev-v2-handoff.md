# Jev v2 improvements build handoff

Ready to build **214-221 (#331-#338)** on `master-plan/phase-ev`, from this Alpha4Gate checkout. Stop before operator **222 (#339)**. The operator explicitly requested stopping testing and proceeding with improvements on 2026-10-09.

Read [plan](jev-v2-plan.md), [live validation](jev-v2-validation.md), [review](jev-v2-review.md), and [wrap](jev-v2-wrap.md). Steps 212 and 224 are DONE. Step 213 remains pending/deferred, not DONE; #330 stays open. The explicit resume flag prevents starting the deferred test again.

```text
/goal "Jev v2 Steps 214-221 are Status: DONE in documentation/plans/jev-v2-plan.md, issues #331-#338 are closed, and offline Python tests, strict mypy, ruff, frontend tests, lint and build pass. Preserve frozen v1. Step 213 is operator-deferred, not passed. STOP before operator Step 222 (#339); no paid matches in this goal."
/build-phase --plan documentation/plans/jev-v2-plan.md --resume 214
```

## Preserved baseline and measured evidence

Before runtime edits, verify `data/jev/benchmarks/baselines/v1.baseline.json` and snapshot `v1-b53039a693cadf75` with `jev.benchmark.verify_snapshot`. Expected fingerprint: `b53039a693cadf756e6cec4fc44c10534bcd9194079410a98b59a9e441bb1bfd`. This ignored evidence exists in the primary checkout, not automatically in a new clone/worktree. Preserve it intact; never silently recapture v1 from modified shared code.

Scripted staging passed at its 120-game-second cap. Hosted smoke won at 262.5 seconds, with 35 calls, 27 accepted replies, 7 stale replies discarded and no service failures. The operator confirmed dashboard-first launch and >=60 seconds of live Typesafe decisions. Model: `jev-1.13.0`. This is connectivity/execution evidence, not evidence of stronger play.

The operator stopped baseline batch `94ac2c35b17f4cbaa80020b1c859ba00` during its first match. One interrupted case, five unplayed, zero completed outcomes. The frozen v1 source is verified, which preserves future comparisons. Same-tab following across multiple games remains unverified. Do not restart the baseline or launch any paid game during this automated build span.

## Build scope and checks

Implement the planned v2 opening, scouting, gas, recoverable expansion, mixed Zealot/Stalker army, hosted strategic and execution choices, then live graph presentation. Keep the four-Gateway identity and four-Zealot first launch. All substantive action/recovery requirements and acceptance criteria remain in the plan. Do not change v1 policy bytes or legacy RL behavior.

Use the host's build-step developer/reviewer routing; the operator intends an Opus coordinator. Run meaningful offline Python and frontend checks and the UI evidence required by Step 221. Independent review remains required. No new gameplay implementation was performed in this validation/preparation session.

The host interrupt ended the baseline process tree before terminal evidence was written. Recovery used the production batch store to mark interruption, leaving the raw archive unchanged; no graceful-shutdown claim is made. Do not erase that limitation from the report. The tracked derived task-state file and untracked `dev.code-workspace` remain excluded from commits.
