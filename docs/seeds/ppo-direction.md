# Seed: PPO direction — what should the neural policy do?

**Status:** SEED, ACTIVE exploration (2026-11-11). Not parked. Owner step: PPO.0 in
[master_plan.md](../plans/master_plan.md).
**Operator framing (2026-11-11):** PPO is likely the strongest path forward, but its role is
undefined and the design space is large.
**Goal tie-in:** PPO earns its place only through the Phase ES scoreboard
([evolve-for-strength](evolve-for-strength.md)). A PPO-driven tree must beat the same tree
in rules mode on that scoreboard.

## What exists today (verified 2026-11-11)

- **Decision modes** (`bots/v13/learning/neural_engine.py`): `rules` (default), `neural`,
  `hybrid`. In the default `rules` mode PPO is never consulted.
- **Control surface:** PPO chooses one of six strategic states (`OPENING`, `EXPAND`, `ATTACK`,
  `DEFEND`, `LATE_GAME`, `FORTIFY`). The rules engine, commands, tactics and micro carry it out.
- **Observation:** v13 has 55 dimensions (48 base plus an 8-slot build-order one-hot); v0 has 47.
- **Reward:** 63 shaped JSON rules, plus Phase D's build-order trajectory reward (flag off) and
  Phase N's win-probability heuristic.
- **Training:** Stable Baselines 3 PPO on CPU through `TrainingDaemon`, warm-started by
  imitation of the rules engine (`v0_pretrain.zip`).
- **Evidence so far:**
  - 2026-04-15: pure-PPO soaks reached 36% training win rate at difficulty 3 over 421 games
    and were called a dead end.
  - 2026-05-28: a 15-cycle soak produced `bots/v13/data/checkpoints/v15.zip` (training win
    rate 0.55 → 0.20 → 0.70). It has never had a deterministic evaluation.
  - Changing the observation size made every older checkpoint unloadable.
- Reward and hyperparameter edits do not change games played in `rules` mode.

## Why it has not paid off yet (hypotheses to test, not conclusions)

1. **Small control surface:** choosing among six states over a strong rules engine leaves
   little room to beat that engine.
2. **Wrong yardstick:** progress was judged by noisy training win rate at difficulty 3, never
   by a deterministic evaluation against a fixed panel.
3. **Reward imitates the rules:** 63 hand-shaped rewards may teach the rules back instead of
   winning.
4. **Compute:** CPU PPO limits games per hour. Headless Linux ran 4-way parallel in Phase 8
   Spike 3.

## Design space — the questions to answer

### 1. What should PPO decide?

- a. Strategic state, as today.
- b. State plus target: Phase E's structured action space (E.0 cardinality is unresolved).
- c. Separate heads for macro (tech / expand / hold) and army intent (Jev harvest H5).
- d. Opening or build-order choice per game: a cheap bandit over Phase D's build orders.
- e. Engagement thresholds: when to fight and when to retreat.
- f. A choice among legal branches only: the Jev decision graph owns legality, and PPO picks
  with action masking (Jev harvest H19).

### 2. How should it learn?

Imitation of the rules engine, then RL fine-tuning with a KL penalty toward the rules teacher
(E.4). Reward: win/loss plus win-probability change, against the existing shaped rules
(keep, trim or replace).

### 3. Who should it train against?

A built-in AI curriculum tied to the ES stages, frozen versions (6.1 `--opponent vN`), or
PFSP-lite (6.2).

### 4. How is it judged?

The ES scoreboard: PPO on against PPO off on the same tree, deterministic evaluation, paired
seeds.

### 5. How does it combine with evolve?

Evolve edits rules code. Policies are trained per version, and a post-promotion training hook
exists (`--post-training-cycles`). Observation changes invalidate checkpoints, so retraining
after a promotion needs a policy.

### 6. Where does it train?

Windows CPU or headless Linux (an older SC2 package; see the ES substrate question).

**Found during Phase ES planning (2026-11-11):**
- The runner's `--batch` path ignores `--decision-mode` and `--model-path`
  (`bots/v13/runner.py:482-490`), so any earlier "hybrid" batch silently played the rules
  engine. P1 must use the Phase ES scoreboard worker, which honors both (ES.2).
- Training and measurement run headless on WSL (D16).

## First exploration steps (cheapest evidence first)

1. **P1 — measure what exists.** On a small Stage 0 scoreboard: v13 `rules` against v13
   `hybrid` with `v15.zip`, and with `v0_pretrain.zip`. If hybrid ≈ rules, today's PPO adds
   nothing, and the real question becomes the control surface (question 1).
2. **P2 — survey the design space** (`/user-brainstorm` or `/deep-research`): what comparable
   single-machine SC2 bots use RL for.
3. **P3 — pick one control surface** for a spike with a pre-registered claim and a kill
   criterion.

## Existing steps this exploration will keep, reshape or retire

These are HELD under PPO.0, not parked.

| Steps | Question | Note |
|---|---|---|
| M1, M2 (Phase D build-order reward) | 2 | Test only if the build-order reward survives P3 |
| E.0–E.6 (structured action space) | 1b | E.0's 10-vs-12 cardinality decision folds into P3 |
| 6.1–6.3, ER.8 (curriculum against versions, PFSP) | 3 | Reframe the curriculum around ES stages |
| 7.6 (advised staleness soak) | 5 | Keep only if the advised loop remains part of the PPO cycle |
