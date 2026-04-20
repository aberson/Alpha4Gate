# Phase D Build Plan — Build-order z-statistic (reward refactor)

**Parent plan:** [alpha4gate-master-plan.md](alpha4gate-master-plan.md) — Phase D
**Track:** Capability
**Prerequisites:** Phase 5. B and D are orthogonal — D can run before, after, or in parallel with B.
**Effort estimate:** ~3 days (audit is most of it).
**Status:** Drafted, not yet started. Detail extracted from the master plan
on 2026-04-19 as part of the plan/build-doc cleanup.

## 1. What this feature does

Collapses implicit build-order reward rules into an explicit `z` target
vector with an edit-distance pseudo-reward. Today's
`bots/current/data/reward_rules.json` mixes build-order rules
(timed structure/tech goals) with tactical, economic, and other rules
in one flat list. That conflation makes the early-game reward signal
noisy because every rule fires regardless of which build the policy is
trying to follow.

By extracting build-order rules into named target sequences and
rewarding the policy on edit-distance to the chosen sequence, the
early-game reward variance should drop substantially while still letting
tactical/economic rules shape mid- and late-game.

## 2. Existing context

- **`bots/current/data/reward_rules.json`** — 48 active reward rules
  (per master plan baseline). Some are build-order timing rules
  (e.g. "Gateway by 1:30"), others are tactical (e.g. "engage when
  army_supply > enemy * 1.2") or economic (e.g. "saturate gas").
- **`bots/current/learning/`** — PPO training pipeline. Reward signal
  is computed per step, summed across an episode.
- **Curriculum** — current curriculum already has a notion of difficulty
  but no notion of "which build are we trying."

## 3. Scope (build steps)

| Step | Issue | Description |
|------|-------|-------------|
| D.1  | #164  | Audit `reward_rules.json` and tag rules |
| D.2  | #165  | Define `z` schema for `build_orders/<label>.json` |
| D.3  | #166  | Implement `build_order_reward.py` edit-distance |
| D.4  | #167  | Migrate (a)-tagged rules into build-order files |
| D.5  | #168  | Append `z` identifier as optional obs slot |
| D.6  | #169  | Backwards-compat: `use_build_order_reward` flag |
| D.7  | #170  | Train 3 cycles + measure early-game reward variance |
| D.8  | #171  | Snapshot to `vN+1` on promotion |

### Step D.1: Audit `reward_rules.json` and tag rules

**What to build.** Read every rule in
`bots/current/data/reward_rules.json` (currently 48 active rules) and
tag each as one of:
- (a) **build-order** — timed structure / tech goal (e.g. "Gateway by
  1:30", "Cybernetics Core by 2:00")
- (b) **tactical** — combat/engagement rules (e.g. "engage when army
  > enemy * 1.2")
- (c) **economy** — worker/saturation/resource rules (e.g. "saturate
  gas after 24 mineral workers")
- (d) **other** — everything else (default for ambiguous)

Edge cases tagged by primary purpose. Output: an annotated JSON file
or in-place modification adding a `category` field per rule.

**Existing context.**
- `bots/current/data/reward_rules.json` — flat array of rule objects.
- Per-rule schema today: `{"id": str, "trigger": ..., "amount": float,
  "description": str}`. The new field is `category: "a"|"b"|"c"|"d"`.
- Phase D's whole premise rests on this audit — get it right.

**Files to modify/create.**
- `bots/current/data/reward_rules.json` — add `category` field per
  rule.
- `documentation/plans/phase-d-audit.md` (NEW, optional) — narrative
  rationale for each tag, especially edge cases.

**Done when.**
- Every rule has a `category` field.
- Counts published in audit doc: e.g. "16 build-order, 22 tactical, 8
  economy, 2 other".
- Code that reads `reward_rules.json` (e.g. `rewards.py`) tolerates
  the new field (forward-compat).

**Flags (recommended).** `--reviewers code` (no isolation needed —
content audit, not code change).

**Depends on.** none.

**Produces.** Annotated `reward_rules.json`; optional audit doc.

### Step D.2: Define `z` schema for `build_orders/<label>.json`

**What to build.** Define + document the `z` (build-order target)
schema. Each file at `bots/current/data/build_orders/<label>.json`
encodes one named build order:

```json
{
  "name": "4-gate-aggression",
  "targets": [
    {"action": "build_pylon", "time_seconds": 18, "weight": 1.0},
    {"action": "build_gateway", "time_seconds": 30, "weight": 1.5},
    ...
  ],
  "tolerance_seconds": 30
}
```

Add at least 2 example files (e.g., `4-gate-aggression.json`,
`robo-colossus.json`) to anchor the schema.

**Existing context.**
- No existing `build_orders/` directory — Phase D creates it.
- "Action" vocabulary should align with rule trigger names so
  build-order reward + reward rules agree on naming.
- Tolerance window controls how strict the timing match is.

**Files to modify/create.**
- `bots/current/data/build_orders/` (NEW directory)
- `bots/current/data/build_orders/4-gate-aggression.json` (NEW)
- `bots/current/data/build_orders/robo-colossus.json` (NEW)
- `bots/current/data/build_orders/_schema.json` (NEW, JSON Schema for
  validation) — optional but recommended.

**Done when.**
- Schema documented (in build doc + JSON Schema file).
- Two example build-order files validate against the schema.
- Action vocabulary cross-checked against `reward_rules.json` rule
  triggers (no name mismatches).

**Flags (recommended).** `--reviewers code`

**Depends on.** Step D.1 (audit reveals action vocabulary used by
build-order rules).

**Produces.** New directory; 2 example files; optional schema file.

### Step D.3: Implement `build_order_reward.py` edit-distance

**What to build.** Create `bots/current/learning/build_order_reward.py`
with:
- `load_build_order(label) -> BuildOrder` — read JSON.
- `compute_progress(executed_actions, build_order) -> float` —
  edit-distance between executed action sequence and target sequence.
- `step_reward(prev_progress, curr_progress, alpha=1.0) -> float` —
  `-α * (curr - prev)` (rewards reducing edit-distance).

Edit-distance: standard Levenshtein with action substitution cost = 1,
optionally weighted by `weight` field per target.

**Existing context.**
- Existing reward computation in `bots/current/learning/rewards.py`
  consumes `reward_rules.json` and produces per-step reward. Phase D
  adds a parallel `build_order_reward.py` that hooks in alongside,
  not replaces.
- Per-step reward integration: rewards.py already sums multiple rule
  rewards; add build-order reward as another summand when
  `use_build_order_reward` flag is set (Step D.6).

**Files to modify/create.**
- `bots/current/learning/build_order_reward.py` (NEW)
- `tests/test_build_order_reward.py` (NEW)

**Done when.**
- `compute_progress` correct on synthetic sequences (perfect match =
  0; one missing step = 1; etc.).
- `step_reward` monotonic in progress (never positive when progress
  worsens).
- Empty target list handled (returns 0 reward, no division by zero).
- Weight field affects edit cost as documented.

**Flags (recommended).** `--reviewers code --isolation worktree`

**Depends on.** Step D.2.

**Produces.** New module; test file.

### Step D.4: Migrate (a)-tagged rules into build-order files

**What to build.** Take the (a)-tagged build-order rules from Step D.1
and re-express them as targets in build-order JSON files (Step D.2
schema). Preserve the timing intent. Keep (b)(c)(d) rules in
`reward_rules.json` as shaped rewards.

Migration test: pre/post total reward on a known game log agrees
within 5% (sanity check that the rewrite preserves intent).

**Existing context.**
- The 48 rules in reward_rules.json provide the source content.
- Build-order rules typically check "by time T, structure X exists".
  Express as target action with `time_seconds: T`.
- Multiple rules may map to the same build-order entry (e.g., "Gateway
  by 1:30" and "Gateway by 2:00" → one Gateway target with
  `tolerance_seconds: 30`).

**Files to modify/create.**
- `bots/current/data/build_orders/` — additional build-order files
  derived from (a)-tagged rules.
- `bots/current/data/reward_rules.json` — remove migrated (a) rules
  (or set `disabled: true` for backwards-compat).
- `bots/current/data/reward_rules.pre-phase-d.json` (BACKUP) — for
  rollback.
- `tests/test_reward_migration.py` (NEW) — pre/post reward parity on
  known game logs.

**Done when.**
- All (a)-tagged rules migrated.
- `reward_rules.pre-phase-d.json` backup exists.
- Migration test: replay a known game log through both old and new
  reward paths; total reward agrees within 5%.

**Flags (recommended).** `--reviewers code --isolation worktree`

**Depends on.** Step D.3.

**Produces.** Additional build-order JSON; updated reward_rules.json;
backup; test.

### Step D.5: Append `z` identifier as optional obs slot

**What to build.** Add a `z` (build-order identifier) one-hot slot to
`_FEATURE_SPEC` in `bots/current/learning/features.py`. The policy can
condition on which build it's trying to follow. One-hot over registered
build orders + a "none" bucket (fall-through if no z is selected).

**Existing context.**
- `bots/current/learning/features.py::_FEATURE_SPEC` — Phase B
  already extended this; established pattern for adding obs slots.
- `bots/current/learning/database.py` may need new column for `z` if
  it's part of state.
- Padding logic: policies trained without z must still load (z slot
  zeroed in input).

**Files to modify/create.**
- `bots/current/learning/features.py` — append z slots to
  `_FEATURE_SPEC`. Bump `BASE_GAME_FEATURE_DIM`.
- `bots/current/decision_engine.py::GameSnapshot` — add
  `current_build_order: str | None` field.
- `bots/current/learning/database.py` — possibly new column or store
  in existing `state_blob`.
- `tests/test_features_z.py` (NEW) — verify z slot encoded
  correctly.

**Done when.**
- `_FEATURE_SPEC` extended; `FEATURE_DIM` bumped.
- Padding test: old transitions (without z) round-trip via zeroed
  slot.
- z slot correctly encoded for synthetic snapshots.

**Flags (recommended).** `--reviewers code --isolation worktree`

**Depends on.** Step D.2 (need build-order labels to enumerate the
one-hot).

**Produces.** Updated features.py + decision_engine.py + database.py;
new test file.

### Step D.6: Backwards-compat: `use_build_order_reward` flag

**What to build.** Hyperparam flag `use_build_order_reward: bool`
(default `false`). When false, reward computation runs as before
(only `reward_rules.json`). When true, also adds per-step build-order
reward from Step D.3.

The flag enables A/B comparison: train one cycle with flag off
(baseline) and one with flag on, compare reward variance and win rate.

**Existing context.**
- `bots/current/data/hyperparams.json` is the canonical hyperparam
  config. Add the new flag with default false.
- `bots/current/learning/rewards.py` consumes both reward sources;
  add the build-order branch behind the flag.

**Files to modify/create.**
- `bots/current/data/hyperparams.json` — add `use_build_order_reward:
  false`.
- `bots/current/learning/rewards.py` — read the flag, add build-order
  reward summand when on.
- `tests/test_reward_flag.py` (NEW) — verify off path produces
  identical numbers to baseline; on path adds expected delta.

**Done when.**
- Flag-off path produces identical reward to baseline (regression
  test).
- Flag-on path adds build-order reward summand correctly.
- Flag documented in build doc + hyperparams comment.

**Flags (recommended).** `--reviewers code`

**Depends on.** Step D.5.

**Produces.** Hyperparams update; rewards.py extension; test file.

### Step D.7: Train 3 cycles + measure early-game reward variance

**What to build.** Operator step. Train 3 PPO cycles with
`use_build_order_reward: true` on the migrated DB. Measure:
- Early-game (first 5 min of each game) reward std-dev before vs
  after — target ≥ 30% drop.
- Win rate at difficulty 3 across 20 deterministic eval games — must
  hold (≥ baseline).

If variance drops but WR drops too, consider tuning `α` (build-order
reward scale) before snapshotting.

**Existing context.**
- Daemon-driven training: `python -m bots.v0.runner --serve --daemon
  --decision-mode hybrid` runs cycles. Or manual via
  `scripts/train_cycle.py` if exists.
- Reward logs: `bots/current/data/reward_logs/` contains per-game
  JSONL. Aggregate via `bots/current/learning/reward_aggregator.py`
  for the std-dev measurement.

**Files to modify/create.**
- None (operator step). Document results in this build doc as a
  per-step append.

**Done when.**
- 3 cycles complete; checkpoint saved.
- Reward variance measurement logged with before/after numbers.
- WR eval logged with confidence interval if available.
- Decision: snapshot (if gates pass) or kill (if not).

**Flags.** N/A (operator).

**Depends on.** Step D.6.

**Produces.** Training run + measurement results.

### Step D.8: Snapshot to `vN+1` on promotion

**What to build.** Operator step. If Step D.7 gates passed, snapshot
`bots/current/` → `bots/vN+1/` via `scripts/snapshot_bot.py`. Run
cross-version Elo self-play vs prior `vN`; promotion requires Elo
gain ≥ +10 over 20 games AND WR non-regression vs SC2 AI (Phase 4
gate).

**Existing context.**
- Phase 2's `snapshot_current()` + `scripts/snapshot_bot.py` handle
  the snapshot mechanics.
- Phase 4's `check_promotion()` handles the Elo + WR validation.

**Files to modify/create.**
- None (operator step).

**Done when.**
- `bots/vN+1/` snapshot exists with manifest lineage.
- `check_promotion()` passes (Elo gain ≥ +10 + WR hold).
- Ladder updated with new version's Elo.

**Flags.** N/A (operator).

**Depends on.** Step D.7.

**Produces.** New `bots/vN+1/` directory; promotion log entry; ladder
update.

## 4. Tests

- `tests/test_build_order_reward.py` — edit distance correct, reward
  monotonic in progress, empty target list handled.
- `tests/test_reward_migration.py` — pre/post migration reward totals on
  known game logs agree within 5%.

## 5. Validation

Early-game (first 5 min) reward std-dev drops ≥30% AND win-rate holds at
difficulty 3 AND Elo gain ≥ +10 over 20 games.

## 6. Gate

All three validation criteria simultaneously.

## 7. Kill criterion

Reward variance does not drop — old rules already captured it. Keep the
migration as cleanup; skip `z`-as-obs wiring; do not snapshot.

## 8. Rollback

Backup `reward_rules.pre-phase-d.json`; restore on kill. Delete the
promoted `vN` if already snapshotted.
