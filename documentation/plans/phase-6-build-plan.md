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

This phase is **operational**, not infrastructure. The two real code
deliverables are below; items 3–4 below are descriptions of the
ongoing regime and dashboard surfacing (no work item).

| Step | Issue | Description |
|------|-------|-------------|
| 6.1  | #178  | `/improve-bot-advised --opponent vN` curriculum flag |
| 6.2  | #179  | PFSP-lite pool sampling stretch |
| (3)  | n/a   | Operational mode — how B/D/E/F are driven autonomously once shipped (description, not work) |
| (4)  | n/a   | Dashboard surfacing — Ladder tab (from Phase 4) already shows cross-version progress (already done) |

### Step 6.1: `/improve-bot-advised --opponent vN` curriculum flag

**What to build.** Today `/improve-bot-advised --self-improve-code`
validates trainee runs against SC2 built-in AI. Phase 6 swaps in a
prior `bots/vN/` opponent via subprocess self-play. Operator passes
`--opponent v5` (or any registered version). Curriculum logic: pin the
opponent until trainee clears +N Elo, then advance to next opponent in
the registry. If `--opponent` is not specified, fall back to current
SC2-AI behavior (no breaking change).

**Existing context.**
- `.claude/skills/improve-bot-advised/SKILL.md` — Phase 4 dispatch loop
  picks an improvement and runs validation games. The current
  validation invocation uses SC2-AI difficulty.
- `src/orchestrator/selfplay.py::run_batch(p1, p2, games, map_name, …)`
  — Phase 3 primitive that boots two `python -m bots.vN` subprocesses
  per game.
- `src/orchestrator/ladder.py::get_elo(version)` + `LadderEntry`
  contract from Phase 4 — read current Elo to drive curriculum
  advancement.
- `src/orchestrator/registry.py::list_versions()` — enumerate
  registered opponents to walk the curriculum.

**Files to modify/create.**
- `.claude/skills/improve-bot-advised/SKILL.md` — accept `--opponent vN`
  flag in Phase 0 bootstrap; thread through to Phase 4 validation; fall
  through to SC2-AI if absent.
- `src/orchestrator/curriculum.py` (NEW) —
  `pick_opponent(current_elo, opponents, advance_threshold) -> str`.
- `tests/test_curriculum_opponent.py` (NEW) — synthetic Elo trajectory
  walks 3 opponents.

**Done when.**
- `--opponent v5` flag accepted; SKILL.md documents the flag.
- Curriculum advances correctly: trainee at Elo X+10 vs pinned opponent
  → next opponent gets pinned automatically.
- Backwards-compat: omitting `--opponent` runs the existing SC2-AI eval
  unchanged.
- `tests/test_curriculum_opponent.py` covers: pinning, advancement on
  threshold cross, exhaustion of opponent list (graceful stop).
- Dashboard "Loop" tab shows current opponent (read from
  `bots/current/data/advised_run_state.json`).

**Flags (recommended for `/build-step`).** `--reviewers code --isolation worktree`

**Depends on.** Phase 5 (sandbox); Phase 4 (ladder data).

**Produces.** Updated SKILL.md; new `curriculum.py`; new tests; small
addition to Loop dashboard tab.

### Step 6.2: PFSP-lite pool sampling (stretch)

**What to build.** Generalize Step 6.1's "pin one opponent" to
"sample from top-K opponents using PFSP-lite weights." Trainee plays
mixed-style validation games against a pool instead of single
opponent. AlphaStar-lite league at single-box scale.

**Existing context.**
- Phase 3 already shipped the PFSP-lite sampler in
  `src/orchestrator/selfplay.py` (`--sample pfsp --pool v0,v1,v2,v3`).
- Step 6.1's `pick_opponent` returns a single string; Step 6.2
  generalizes to `pick_opponent_pool` returning a sampler.
- Promotion gate must track WR per opponent in pool, not aggregate
  (so a strong-vs-weak pool doesn't artificially inflate a single
  number).

**Files to modify/create.**
- `.claude/skills/improve-bot-advised/SKILL.md` — accept
  `--opponent-pool top-K` flag (mutually exclusive with `--opponent`).
- `src/orchestrator/curriculum.py` — add `pick_opponent_pool(top_k,
  ladder, sampler="pfsp")` returning a callable that draws an
  opponent per game.
- `tests/test_curriculum_opponent.py` — extend with pool-sampling
  cases.

**Done when.**
- `--opponent-pool top-3` flag accepted; sampler draws across top-3
  weighted by PFSP-lite (lower WR vs trainee → higher weight).
- Per-opponent WR tracked in `advised_run_state.json` (not just
  aggregate).
- Promotion gate fires only if trainee beats EACH opponent in the
  pool by margin (configurable; default ≥ +5 Elo per opponent).

**Flags (recommended).** `--reviewers code --isolation worktree`

**Depends on.** Step 6.1 (curriculum module exists).

**Produces.** SKILL.md update; `curriculum.py` extension; test
coverage; possibly minor tweak to Phase 4 promotion gate to handle
pool-mode trainees.

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

## 9. Relationship to Phase 9 (improve-bot-evolve)

Phase 6 and Phase 9 are **orthogonal mechanisms** that both drive
cross-version improvement, both consume `run_batch` from
`src/orchestrator/selfplay.py`:

- **Phase 6** is PPO-training-driven: use H2H self-play results as the
  RL signal for the trainee version.
- **Phase 9** is improvement-pool-driven: discrete A/B selection between
  two siblings with Claude-generated improvement-pool items applied one
  per snapshot.

Both can run; they target different bottlenecks (RL signal vs discrete
search). Mutual-exclusion pre-flight prevents concurrent runs of either
skill against the same `bots/current/` working tree.
