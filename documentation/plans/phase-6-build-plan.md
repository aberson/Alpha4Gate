# Phase 6 Build Plan — Self-play-driven improvement loop

**Parent plan:** [alpha4gate-master-plan.md](alpha4gate-master-plan.md) — Phase 6
**Track:** Operational
**Prerequisites:** Phase 5 + at least one of {B, D, E} promoted (so there's a non-trivial starting point).
**Effort estimate:** 2h to wire the skill flags; open-ended soak thereafter.
**Status:** Drafted, not yet started. Detail extracted from the master plan
on 2026-04-19 as part of the plan/build-doc cleanup.

## 1. What this feature does

Adds a **cross-version self-play layer** on top of the existing
intra-version training loop. The `TrainingDaemon` /
`PromotionManager` / `RollbackMonitor` (from always-up Phase 3) continues
to run inside `bots/current/` for intra-version WR-delta promotions; this
phase adds the operational regime that drives version-to-version Elo
improvements via `/improve-bot-advised --self-improve-code` against a
chosen prior version as opponent.

This is not a one-shot phase; it's an ongoing operational mode that drives
B / D / E / F autonomously once they ship.

## 2. Existing context

- **`TrainingDaemon`** — threaded daemon in API server, transition-count +
  time triggers, curriculum auto-advance, persistent config in
  `bots/current/data/daemon_config.json`.
- **`PromotionManager`** — intra-version promoter on WR delta.
- **`RollbackMonitor`** — intra-version regression detector.
- **`/improve-bot-advised`** (`.claude/skills/improve-bot-advised/SKILL.md`)
  — autonomous Claude-driven improvement loop, currently uses SC2 AI as
  the win-rate signal.
- **`src/orchestrator/selfplay.py`** + **`ladder.py`** — Phase 3/4 give
  the cross-version self-play primitive and Elo-based promotion gate.
- **PFSP-lite sampler** — already in `selfplay.py` from Phase 3.

## 3. Scope (operational steps)

1. `/improve-bot-advised --self-improve-code --opponent v5` — curriculum
   opponent selection (advance when +N Elo cleared).
2. Stretch: pool sampling from top-K versions for mixed-style validation
   (AlphaStar-lite league at single-box scale) using PFSP-lite sampler.
3. Operational mode — this is how B / D / E / F are driven autonomously
   once shipped. Not a one-shot phase; an ongoing regime.
4. Dashboard surfacing: Ladder tab (from Phase 4) shows cross-version
   progress; Improvements tab continues to show intra-version
   promotions/rollbacks from the existing daemon.

## 4. Tests

N/A — operational phase, not infrastructure. The underlying primitives
(`selfplay.py`, `ladder.py`, `improve-bot-advised` skill) are tested in
their own phases.

## 5. Validation

The first-cycle demonstration is the gate. Subsequent operation is
just running the skill on a recurring basis.

## 6. Gate (first-cycle demonstration)

- Multi-hour run produces `vN → vN+1 → vN+2` with monotonically rising Elo.
- At least one version beats SC2 AI at a higher difficulty than the
  Phase A baseline `v0`.

## 7. Kill criterion

Ladder exhibits cycling pathology (rock-paper-scissors, AlphaStar
Figure 3D) with no upward trend across 3 snapshots. Re-audit reward
rules and curriculum opponent selection before iterating further.

## 8. Rollback

Operational phase — rollback is "stop running the skill." No code to
revert. Promoted versions stay in `bots/vN/` and remain valid checkpoints.

## 9. Relationship to Phase 8 (improve-bot-evolve)

Phase 6 and Phase 8 are **orthogonal mechanisms** that both drive
cross-version improvement, both consume `run_batch` from
`src/orchestrator/selfplay.py`:

- **Phase 6** is PPO-training-driven: use H2H self-play results as the
  RL signal for the trainee version.
- **Phase 8** is improvement-pool-driven: discrete A/B selection between
  two siblings with Claude-generated improvement-pool items applied one
  per snapshot.

Both can run; they target different bottlenecks (RL signal vs discrete
search). Mutual-exclusion pre-flight prevents concurrent runs of either
skill against the same `bots/current/` working tree.
