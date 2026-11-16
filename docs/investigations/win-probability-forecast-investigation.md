# Win-probability forecast: investigation report

**Phase 4.6, Step 7 — investigation only, no production code.**

- Branch: worktree off master @ `39cdabe` (post-Steps 1-5 of Phase 4.6).
- Date: 2026-04-10.
- Baseline: 799 Python tests passing, mypy clean (45 source files), ruff clean.
- Primary data: `~/soak-artifacts/2026-04-11/training.db`
  (captured during the soak-2026-04-11 run).

## 1. Problem statement

The only in-game signal that "the bot is doing well" today is the post-game
`Result.Victory` / `Result.Defeat` row written by `SC2Env._sync_game` into
`games.result`. There is no per-step estimate of P(win). A forecast that
scored each decision step with a probability would enable three things in
follow-up phases:

1. **Debug** — show the operator exactly when a game started going wrong
   rather than only that it did.
2. **Train** — replace / augment `learning.rewards.RewardCalculator` with a
   learned value-head signal (AlphaZero-style), which may address Finding 1
   (PPO win-rate degradation across cycles).
3. **Model-quality proxy** — distinguish "the bot thought it was winning
   right up to the GG" from "the bot knew it was losing at minute 6" —
   richer signal than a binary outcome.

## 2. Existing feature inventory

The observation vector the trainer feeds PPO is defined by
`src/alpha4gate/learning/features.py::_FEATURE_SPEC` (`FEATURE_DIM = 17`).
Each value is normalized to `[0, 1]` by a hard-coded divisor and
`GameSnapshot` (`src/alpha4gate/decision_engine.py`) is the upstream struct.

Current 17 features:

- `supply_used` (÷200), `supply_cap` (÷200) — supply consumption.
- `minerals` (÷2000), `vespene` (÷2000) — current bank.
- `army_supply` (÷200) — own army supply.
- `worker_count` (÷80) — economy size.
- `base_count` (÷5) — expansions.
- `enemy_army_near_base` (bool) — defensive alarm.
- `enemy_army_supply_visible` (÷200) — visible enemy threat.
- `game_time_seconds` (÷1200) — game phase proxy.
- `gateway_count`, `robo_count`, `forge_count` — production building counts.
- `upgrade_count` — research completed.
- `enemy_structure_count` — scouted enemy buildings.
- `cannon_count`, `battery_count` — static defense.

**Gaps vs what a strong win-probability model would want:**

- No **income-rate** features (minerals/min, vespene/min). Only current
  bank is captured; a bot sitting on 3000 minerals is not the same signal
  as one earning 1500/min.
- No **opponent composition** beyond a single `enemy_army_supply_visible`
  scalar — Zealots vs Immortals vs Carriers is indistinguishable.
- No **map position / engagement location** — there's no "is my army in
  the middle of the map or defending my natural" bit.
- No **tech tier** scalar — you can infer it from `robo_count > 0` plus
  `upgrade_count` but there's no "has Blink / has Storm" feature.
- No **scouting freshness** — `enemy_army_supply_visible` drops to 0
  the moment you lose vision, which the model cannot distinguish from
  "they genuinely have no army".
- No **supply differential** — only `army_supply` and
  `enemy_army_supply_visible` as separate scalars; a single
  `army_ratio` feature would likely carry more signal than either alone.
- No **lost-units-this-game** counter — a rolling casualty total is a
  strong losing signal and is easy to compute from bot state.
- `action_probs` column exists in `transitions` but is **100% NULL** in
  the soak artifact — no PPO policy distribution is being persisted
  yet, which blocks any distillation-style training that would reuse
  the policy head's features.

The existing 17 features are **sufficient for a first-pass baseline**
(logistic regression / tiny MLP) but would leave meaningful accuracy
on the table versus a richer feature set.

## 3. Transitions table: state of the soak artifact

Ran against `~/soak-artifacts/2026-04-11/training.db`:

```
games:                  8 rows
transitions:            1650 rows
distinct game_ids in transitions:  10
result distribution:    loss=6, win=2
```

**Critical: only 8 of the 10 game_ids in the transitions table have a
matching row in `games`.** 262 transitions are orphaned — their game
thread crashed after storing transitions but before `store_game` ran.
This is the exact Bug B pattern that Step 1 of Phase 4.6 (commit
`b27c6cc`) fixed going forward, but the soak artifact was captured
before the fix landed, so the unlabeled rows are permanent.

Labeled transitions (what we can train on):

```
win transitions:  323
loss transitions: 1065
total labeled:    1388 (baseline-rate P(win) = 0.233)
unlabeled:        262 (~16% of rows discarded)
```

Per-game transition counts: min=76, max=191, mean=165 — most games ran
to the 15-minute timeout (~191 steps at `STEPS_PER_ACTION=22`, ~900s).

**Label propagation:** the `games.result` label is NOT denormalized onto
`transitions`. Supervised training currently requires a JOIN on
`game_id`. For win-prob work, either:

- add a `result` (or `win` INTEGER 0/1) column to `transitions` during
  `store_game`, OR
- do the JOIN in the training script and pay the cost at data-load time.

Either is fine; the JOIN is trivial on a 1.4k-row table and not
noticeably slower on a 140k-row table.

**Schema observation:** the schema has both `game_time` (in seconds,
populated) and `game_time_secs` (also populated, slightly off due to
rounding). This is a cosmetic redundancy, not a blocker.

**Also: `action_probs` is 100% NULL in the soak artifact** — a second
separate issue; the environment plumbs `action_probs` through
`info["action_probs"]` (see `environment.py:659-665`) but the soak run
never had PPO's `last_probabilities` populated on the `_GymStateProxy`.
Worth raising as a follow-up issue independent of win-prob.

## 4. Architectural options — recommendation

### Option (a) — New value head on the PPO net

Add a second output head to the PPO actor-critic network (already a
critic head exists in SB3's PPO). Could either bias the critic toward
win-probability calibration or add a sibling head trained with the
`win=1/loss=0` label.

- Pros: cleanest training-loop integration; gradient-sharing with
  the policy; no second model to deploy.
- Cons: couples tightly with Finding 1 (PPO win-rate is degrading);
  touches `neural_engine.py` and `trainer.py` which are explicitly
  **out of scope for this investigation**; cannot validate offline on
  existing data without at least one new training run.

### Option (b) — Separate small classifier trained offline

Standalone model (pure-numpy logistic regression or a tiny torch MLP)
trained on transitions from `training.db`. Loads at bot startup and is
called per decision step.

- Pros: fully decoupled from PPO; can be validated offline on the soak
  artifact; easy to A/B against heuristic baselines; no PPO retraining
  required; sklearn is NOT in the env so would use pure numpy or
  torch.nn.Linear (both already deps).
- Cons: needs a second training pipeline and a second checkpoint to
  version; trailing indicator of policy quality, not a training signal
  for PPO unless piped back into the reward function (which is a
  follow-up).

### Option (c) — Hand-rolled heuristic

Weighted feature score, e.g.
`0.25*army_vs_enemy + 0.25*econ + 0.15*supply_ratio + 0.15*production + ...`.
Ships immediately as a debug surface.

- Pros: zero training; zero checkpoint; zero deps; runs in < 1 ms;
  useful immediately even if (a) or (b) is the long-term answer;
  interpretable to the operator.
- Cons: will never be as accurate as a learned model on a mature
  feature set; hand-weights drift out of date as the bot evolves.

### Recommendation: **Option (c) first, then Option (b)**

Rationale anchored in the data from the soak artifact (Section 5):

1. **Eight labeled games is not enough to train anything honestly.**
   A pure-numpy logistic regression with a game-level train/test split
   (to avoid within-game leakage) showed 95.7% train accuracy and
   50.3% test accuracy — identical to the always-loss baseline on a
   test fold that happened to be 50/50 by chance of the split. That is
   pure overfitting on the single won training game. Option (b)
   should not ship until the labeled-game pool is at least an order of
   magnitude larger.
2. **Option (c) is the only option that can ship today.** It has no
   training curve, no checkpoint lifecycle, no risk of stale
   calibration, and doubles as the "null hypothesis" that a future
   Option (b) must beat.
3. **Option (a) is blocked by the out-of-scope constraint** and should
   only be reopened after Option (b) has validated that a simple
   model can do better than heuristic on real data at scale.

The heuristic prototype (Section 5) shows 0.342 mean P(win) on won-game
transitions vs 0.197 on lost-game transitions — a 0.145 absolute
separation, which is not enough to classify but is already enough to
render a meaningful debug curve in the Live tab: operators would see
the curve dip during disadvantageous windows even if the 0.5 threshold
doesn't move.

## 5. Prototype results (investigation only — NOT committed)

A throwaway prototype was run in-process against the soak artifact.
**No script was committed to `scripts/`** because (1) the data size
makes the learned path dishonest and (2) the only honest output
(heuristic) is small enough to fit in a bot module once the debug
path is designed.

### Heuristic baseline (Option c)

```
Rows labeled:    1388 (win=323 loss=1065, base rate P(win)=0.233)
Heuristic formula:
    0.25 * army_vs_enemy_ratio +
    0.25 * (0.5*workers/50 + 0.5*bases/3) +
    0.15 * supply_used/200 +
    0.15 * (gateways + 2*robo)/6 +
    0.10 * upgrades/4 +
    0.10 * (cannons + batteries)/4 (static defense) -
    0.30 * enemy_army_near_base

Mean P(win) on WIN  transitions: 0.342
Mean P(win) on LOSS transitions: 0.197
Accuracy @ 0.5 threshold:        0.782
Always-loss baseline accuracy:   0.767
```

The heuristic is barely above the majority-class baseline on
accuracy (+1.5 points) but does cleanly separate the mean — i.e. it
is useful as a **debug indicator**, not a classifier. This is the
right property for sink #1 (operator debug view).

### Pure-numpy logistic regression (Option b)

```
Train: 1008 transitions from 6 games (base rate = 0.133)
Test:  380  transitions from 2 games (base rate = 0.497 — luck of split)

Train metrics:  acc=0.957  brier=0.031  log_loss=0.117
Test metrics:   acc=0.503  brier=0.443  log_loss=2.119
Test (late, step>=100):    acc=0.514  brier=0.481  log_loss=2.585
```

Classic overfit-to-one-training-game pattern. On 8 total games, there
is no way to train an honest win-prob classifier — the model memorizes
the won training game's feature trajectory and extrapolates badly to
unseen games. **Option (b) must wait for ≥50 labeled games** (rough
rule of thumb: 10x the feature count after game-level aggregation).

## 6. Output sinks — in-scope for initial implementation

Three candidate sinks were listed; recommendation for a follow-up
**Phase 5 issue**:

- **(yes) Backend log line** — emit `winprob=0.42 state=attack`
  every 10 decision steps at INFO, piped through `alpha4gate.logging`.
  Cheap, observable in `backend.log`, survives without frontend work.
- **(yes) New column in `transitions` table** — add `win_prob REAL`
  that the environment writes alongside the existing state. Enables
  post-hoc analysis and backfilling the heuristic with a learned model
  later. Pair with migration in `_LATER_ADDED_COLS` per the existing
  Phase 4.5 migration pattern.
- **(no) WebSocket event on `/ws/game` for a live curve** — defer to
  Phase 5.1. Requires frontend component work (explicitly out of
  scope per the brief) and is the most invasive sink.

## 7. Connection to Finding 1 (PPO win-rate degradation)

Finding 1 observed the cycle win-rate sequence
`0.333 → 0.250 → 0.200 → 0.167 → 0.286` across soak-2026-04-11 cycles
1-5 — a monotone drop until the last cycle. A learned win-probability
signal **could plausibly help** if the current hand-crafted reward
function is rewarding the wrong things (e.g., paying out for building
cannons when cannons don't actually correlate with winning) and a
value head would short-circuit that by propagating win/loss labels
back as the reward. But this is speculation: we would need (a) enough
labeled data to train a value head honestly and (b) a controlled A/B
to show the value-head reward actually reverses the degradation. Do
not over-promise on this linkage in a follow-up plan.

## 8. Next-step recommendation

Open a Phase 5 follow-up issue titled **"Win-probability heuristic +
debug surface"** that does:

1. Add `win_prob` column to `transitions` via `_LATER_ADDED_COLS`.
2. Add `winprob_heuristic.py` under `src/alpha4gate/learning/` with
   a single pure function
   `score(snapshot: GameSnapshot) -> float` matching the Section 5
   formula. Unit test on synthetic snapshots.
3. Wire it into `SC2Env.step` so every transition row writes the
   heuristic P(win) next to the state it describes.
4. Log `winprob=%.2f` every 10 decision steps from the bot's
   existing logger.
5. **Do NOT** wire it into the reward function yet. **Do NOT** add a
   frontend live curve yet.
6. Open a sibling issue "Capture action_probs in training.db" for the
   independent bug that the `action_probs` column is 100% NULL in the
   soak artifact — a prerequisite for any future Option (a) work.
7. Open a third sibling issue "Grow labeled-game pool to ≥50 for
   win-prob supervised training" — this is the gate that unblocks
   Option (b).

After the heuristic has been running for a few soak cycles and has
produced ≥50 labeled games, revisit Option (b) with a proper
game-level CV split and decide whether to promote the learned model
over the heuristic. Option (a) (value head on PPO) is the longest-lead
path and should not be attempted until (b) has validated the feature
set on real data at scale.

## Appendix: files touched by this investigation

- **Read**: `src/alpha4gate/learning/features.py`,
  `src/alpha4gate/learning/environment.py`,
  `src/alpha4gate/learning/database.py`,
  `src/alpha4gate/decision_engine.py`.
- **Queried**: `~/soak-artifacts/2026-04-11/training.db`.
- **Written**: this report only.
- **Baseline gates**: `uv run mypy src` PASS, `uv run ruff check .` PASS,
  `uv run pytest` 799 passed / 1 deselected.
