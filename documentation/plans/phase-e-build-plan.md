# Phase E Build Plan — Autoregressive action head

**Parent plan:** [alpha4gate-master-plan.md](alpha4gate-master-plan.md) — Phase E
**Track:** Capability
**Prerequisites:** Phase 5. Ideally Phase B done first (more interesting ATTACK decisions to differentiate).
**Effort estimate:** ~1 week.
**Status:** Drafted, not yet started. Detail extracted from the master plan
on 2026-04-19 as part of the plan/build-doc cleanup.

## 1. What this feature does

Unlocks tactical variety by making ATTACK / EXPAND conditional on
**target choice** rather than a flat `Discrete(6)` action space. The
current policy can only output one of six strategic states; it cannot
distinguish "attack the enemy main" from "attack the enemy natural" or
"expand to my third base" from "expand to my fourth." This phase
restructures the action space into `(strategic_state, target)` pairs
and gives the policy two sequential softmax heads so it can learn
target-conditional behavior.

## 2. Existing context

- **Action space today:** `Discrete(6)` — strategic states only.
- **`ACTION_TO_STATE`** in `bots/current/decision_engine.py` — canonical
  mapping from action index to `StrategicState`.
- **`SC2Env.action_space`** in `bots/current/learning/environment.py`
  — currently `Discrete(6)`.
- **`rules_policy.py`** — KL teacher for `--decision-mode hybrid`. Must
  be updated to emit 12-way targets.
- **Diagnostic states** at `bots/current/data/diagnostic_states.json`
  hold expected-action per fixed snapshot for canary regression.

## 3. Scope (build steps)

| Step | Issue | Description |
|------|-------|-------------|
| E.1  | #172  | Structured `(strategic_state, target)` action space (Discrete(12)) |
| E.2  | #173  | Custom `ActorCriticPolicy` with two sequential softmax heads |
| E.3  | #174  | DB column `action_space_version` |
| E.4  | #175  | Cascade target into `_compute_next_state` + rules KL teacher |
| E.5  | #176  | 6-way → 12-way DB migration with target inference |
| E.6  | #177  | Snapshot to `vN+1` on promotion |

### Step E.1: Structured `(strategic_state, target)` action space

**What to build.** Replace flat `Discrete(6)` action space with
structured `(strategic_state, target)` pairs. Mapping:
- `ATTACK` → {main, natural, third}
- `EXPAND` → {own_natural, own_third, own_fourth}
- `DEFEND`, `FORTIFY`, `OPENING`, `LATE_GAME` → no target dim (use sentinel)

Effective space = 12 actions. Update `ACTION_TO_STATE` in
`decision_engine.py` to a new `ActionTarget` enum that captures both
dimensions, and update `SC2Env.action_space` to `Discrete(12)`.

**Existing context.**
- `bots/current/decision_engine.py` — `ACTION_TO_STATE: list[StrategicState]`
  is the canonical 6-way mapping. Used by tests, training, and the
  rules policy.
- `bots/current/learning/environment.py` — `SC2Env.action_space =
  Discrete(6)` today. Action index is fed through `ACTION_TO_STATE`.
- `bots/current/learning/rules_policy.py` — `rule_actions_for_batch`
  returns 6-way action indices for the KL teacher.
- `bots/current/data/diagnostic_states.json` — fixed snapshots with
  expected actions (currently 6-way ints).

**Files to modify/create.**
- `bots/current/decision_engine.py` — new `ActionTarget` enum, new
  `ACTION_TO_STATE` (12-way) and helper to decompose into
  `(strategic, target)`.
- `bots/current/learning/environment.py` — `Discrete(12)` action
  space.
- `bots/current/data/diagnostic_states.json` — expand expected actions
  to 12-way (or migrate per Step E.5).

**Done when.**
- `ActionTarget` enum has 12 entries with stable ordinal indices.
- `decision_engine.action_to_target(idx)` returns
  `(StrategicState, TargetEnum | None)`.
- `SC2Env.action_space.n == 12`.
- All existing tests still pass (action-space size assertions updated
  where needed).

**Flags (recommended).** `--reviewers code --isolation worktree`

**Depends on.** none.

**Produces.** Updated `decision_engine.py`, `environment.py`,
`diagnostic_states.json`.

### Step E.2: Custom `ActorCriticPolicy` with two sequential softmax heads

**What to build.** Custom SB3 `ActorCriticPolicy` subclass with two
softmax heads:
- `p(strategic)`: 6-way distribution over strategic states.
- `p(target | strategic)`: conditional 3-way distribution over targets
  (gated by which strategic state was sampled).

Sample order: strategic head first, then target head conditioned on
the sampled strategic. Effective joint distribution covers all 12
actions but with explicit factorization (autoregressive).

**Existing context.**
- SB3 `ActorCriticPolicy` is the parent class; current bot uses
  `MlpPolicy` (default). Custom subclass goes in
  `bots/current/learning/autoreg_policy.py` (NEW).
- Phase A uses `MlpLstmPolicy` for some configs — autoreg subclass
  must work alongside or as alternative; configurable via
  `hyperparams.json::policy_type: "autoreg"`.

**Files to modify/create.**
- `bots/current/learning/autoreg_policy.py` (NEW) — custom policy
  subclass with two heads.
- `bots/current/learning/trainer.py` — pick `autoreg_policy` when
  `hyperparams.policy_type == "autoreg"`.
- `bots/current/data/hyperparams.json` — add example
  `policy_type: "autoreg"` (off by default).

**Done when.**
- `autoreg_policy.AutoregPolicy` instantiates without error.
- Forward pass returns valid joint distribution over 12 actions.
- Gradients flow to both heads (verified in unit test).
- Target head correctly gated by strategic head choice (target probs
  are zero for invalid combos).

**Flags (recommended).** `--reviewers code --isolation worktree`

**Depends on.** Step E.1.

**Produces.** New policy module; trainer hook; hyperparam example.

### Step E.3: DB column `action_space_version`

**What to build.** Add `action_space_version INT` column to
`bots/current/data/training.db` schema (transitions table). Values:
`1` = legacy 6-way, `2` = Phase E 12-way. Migration script that flags
all existing rows as version 1.

**Existing context.**
- `bots/current/learning/database.py` — `TrainingDB` class, `_STATE_COLS`,
  `_LATER_ADDED_COLS` for schema migration. Phase B already
  established this pattern.

**Files to modify/create.**
- `bots/current/learning/database.py` — add column + migration; add
  helper `get_action_space_version(transition_id)`.
- `tests/test_database_action_space_version.py` (NEW) — verify
  migration adds default `1`, new inserts can use `2`.

**Done when.**
- Existing transitions table gains `action_space_version` defaulting
  to `1`.
- New inserts respect provided version.
- `tests/test_database_action_space_version.py` covers fresh DB,
  migrated DB, and version filtering on read.

**Flags (recommended).** `--reviewers code --isolation worktree`

**Depends on.** Step E.1.

**Produces.** Updated schema + migration; new test file.

### Step E.4: Cascade target picking into `_compute_next_state` + rules KL teacher

**What to build.** Update the rule engine to pick BOTH a strategic
state AND a target (matching the new 12-way action space). Cascade
into the KL teacher so `rule_actions_for_batch` returns 12-way.

**Existing context.**
- `bots/current/decision_engine.py::_compute_next_state` — currently
  returns `StrategicState`. Phase E needs it to also return `(state,
  target)`.
- `bots/current/learning/rules_policy.py::rule_actions_for_batch` —
  consumes `_compute_next_state`'s output, returns action indices for
  the KL teacher loss.
- Target picking heuristics: ATTACK → enemy main if scouted, else
  natural; EXPAND → own_natural if not built, else own_third, etc.

**Files to modify/create.**
- `bots/current/decision_engine.py` — extend `_compute_next_state` to
  return `ActionTarget`; add target-picking heuristics.
- `bots/current/learning/rules_policy.py` — return 12-way action
  indices.
- `tests/test_decision_engine_targets.py` (NEW) — verify
  target-picking heuristics for each strategic state under various
  game snapshots.

**Done when.**
- `_compute_next_state(snap)` returns `ActionTarget` enum value.
- `rule_actions_for_batch(batch)` returns ints in `[0, 11]`.
- Target heuristics covered by tests (own_natural picked when
  available, else own_third, etc.).

**Flags (recommended).** `--reviewers code --isolation worktree`

**Depends on.** Step E.1, Step E.2.

**Produces.** Updated decision engine + rules policy; new test file.

### Step E.5: 6-way → 12-way DB migration with target inference

**What to build.** Migrate existing 6-way DB rows to 12-way: for each
old transition, infer target from game log when possible, else
default to `*_main`. Reject loading old checkpoints that were trained
on 6-way unless `--force` is passed (mismatched action-space loads
would silently produce garbage).

**Existing context.**
- `bots/current/data/training.db` has thousands of legacy
  transitions. Game logs in `logs/` may have target info via the
  ATTACK target position; otherwise default.
- `bots/current/learning/checkpoints.py` loads SB3 checkpoints; needs
  action-space compatibility check.

**Files to modify/create.**
- `bots/current/learning/database.py` — `migrate_action_space(db,
  log_dir)` function.
- `bots/current/learning/checkpoints.py` — reject mismatched-action-space
  loads unless `--force`.
- `scripts/migrate_phase_e.py` (NEW) — operator-run script that
  invokes the migration.
- `tests/test_action_migration.py` (NEW) — synthetic 6-way DB
  migrates cleanly; old checkpoints rejected explicitly.

**Done when.**
- `scripts/migrate_phase_e.py --db bots/current/data/training.db
  --logs logs/` produces a migrated DB with all rows at version 2.
- Inferred targets match game-log evidence where available; default
  `*_main` elsewhere.
- Loading a 6-way checkpoint into a 12-way trainer raises clear
  error unless `--force`.

**Flags (recommended).** `--reviewers code --isolation worktree`

**Depends on.** Step E.3 (DB column exists), Step E.4 (target enum
exists).

**Produces.** Migration function; checkpoint compat check;
operator script; test file.

### Step E.6: Snapshot to `vN+1` on promotion

**What to build.** Operator step: train 2-3 cycles with the autoreg
policy on the migrated DB, run deterministic eval at difficulty 3,
run cross-version Elo self-play vs prior `vN`. If gates pass, snapshot
`bots/current/` → `bots/vN+1/`.

**Existing context.**
- `scripts/snapshot_bot.py` from Phase 2 handles the snapshot
  mechanics.
- Phase 4 promotion gate (`check_promotion`) handles the Elo + WR
  validation.

**Files to modify/create.**
- None (operator step). Validation per "Done when".

**Done when.**
- Replay inspection shows meaningful target differentiation (not
  always `enemy_main`); document a sample.
- WR holds at difficulty 3 (≥ baseline).
- Elo gain ≥ +10 over 20 self-play games vs prior `vN`.
- `bots/vN+1/` snapshot created via `snapshot_bot.py`.

**Flags.** N/A (operator).

**Depends on.** Steps E.1–E.5.

**Produces.** New `bots/vN+1/` directory; promotion log entry.

## 4. Impact matrix (within `bots/current/**`)

| Module | Change |
|--------|--------|
| `decision_engine.py` | `ACTION_TO_STATE` → new 12-way `ActionTarget` |
| `learning/environment.py` | `SC2Env.action_space` = `Discrete(12)` |
| `learning/rules_policy.py` | `rule_actions_for_batch` returns 12-way |
| `learning/features.py` | No change |
| `learning/imitation.py` | DB re-labeling migration |
| `learning/database.py` | `action_space_version` column |
| `learning/checkpoints.py` | Reject mismatched-action-space loads unless `--force` |
| `data/diagnostic_states.json` | Expected-action expands to 12-way |

Cross-version concern is gone: `v0` / `v1` keep their 6-way space
forever; the new `vN+1` uses 12-way. Self-play just works because each
subprocess loads its own stack.

## 5. Tests

- `tests/test_autoreg_policy.py` — two-head forward pass, target head
  gated by strategic head, gradients flow to both.
- `tests/test_action_migration.py` — 6-way DB migrates cleanly; old
  checkpoints rejected explicitly.

## 6. Validation

Replay inspection shows meaningful target differentiation (not always
`enemy_main`) AND win rate holds AND Elo gain ≥ +10 over 20 games.

## 7. Gate

All three validation criteria.

## 8. Kill criterion

Target head collapses to single mode. May indicate insufficient signal —
Phase F might be needed first. Mark phase "deferred pending F".

## 9. Rollback

Delete promoted `vN`; prior versions unaffected by design.
