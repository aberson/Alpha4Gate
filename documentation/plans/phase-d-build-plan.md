# Phase D Build Plan — Build-order z-statistic (reward refactor)

**Parent plan:** [alpha4gate-master-plan.md](alpha4gate-master-plan.md) — Phase D
**Track:** Capability
**Prerequisites:** Phase 5. B and D are orthogonal — D can run before, after, or in parallel with B.
**Effort estimate:** ~3 days total. Automated section (D.1–D.6) ~1 day code. Manual section (M1–M2) ~2 days wall-clock.
**Status:** Drafted, reviewed 2026-05-13. Detail extracted from the master plan on 2026-04-19; refreshed against v13 baseline on 2026-05-13.

## 0. Path convention (read first)

The plan refers to `bots/current/...` as the edit target. `bots/current/` is a Python MetaPathFinder alias to whichever version `bots/current/current.txt` names (currently `v13`). When a step says "edit `bots/current/data/foo.json`", the build agent must:

1. Read `bots/current/current.txt` (one line, e.g. `v13`).
2. Resolve the path to `bots/<version>/data/foo.json`.
3. Edit there.

Edits land on the physical version directory. Evolve always snapshots from `bots/current` (resolved the same way) into the next promoted vN, so Phase D changes flow forward automatically. The MetaPathFinder also makes `from bots.current.learning.foo import ...` resolve transparently to the active version's module, so new tests can import through the alias and track future promotions.

## 0.5 Preflight (mandatory before /build-phase)

Phase D edits `reward_rules.json`, which the evolve daemon also patches via `[evo-auto]` commits. Concurrent edits race, and `[evo-auto]` will sweep any pre-staged Phase D content into its own commit (see memory `feedback_evo_auto_commits_sweep_staged`). Before launching the overnight run:

1. **Stop the evolve daemon.** Check `Get-Process python | Where-Object { $_.CommandLine -like "*evolve.py*" }`; terminate via `Stop-Process -Id <pid> -Force` if found. Do NOT kill `SC2_x64.exe`.
2. **Verify no active soak.** `uv run python scripts/evolve_round_state.py` reports idle.
3. **Verify clean tree.** `git status` shows no staged or unstaged changes under `bots/v13/`.
4. **Verify pointer.** `Get-Content bots/current/current.txt` returns the expected version (`v13` as of 2026-05-13).
5. **Verify tests pass on baseline.** `uv run pytest -x` clean before any Phase D step lands.
6. **uv sync** in any spawned worktree (per `worktree-hygiene.md` rule 1) before the first quality gate.

Any preflight failure halts the run. Surface to operator; do not proceed.

## 1. What this feature does

Collapses implicit build-order reward rules into an explicit `z` target vector with an edit-distance pseudo-reward. Today's `reward_rules.json` (63 active rules at v13) mixes build-order rules (timed structure/tech goals) with tactical, economic, and other rules in one flat list. That conflation makes the early-game reward signal noisy because every rule fires regardless of which build the policy is trying to follow.

By extracting build-order rules into named target sequences and rewarding the policy on edit-distance to the chosen sequence, the early-game reward variance should drop substantially while still letting tactical/economic rules shape mid- and late-game.

Phase D does NOT replace the evolve loop. After Phase D ships, evolve continues to patch `reward_rules.json` via `[evo-auto]` commits as before; Phase D adds a second reward summand wired in alongside.

## 2. Existing context

- **`reward_rules.json`** — 63 active rules at v13 (counted 2026-05-13 via `Select-String -Pattern '"active":\s*true' -Path bots/v13/data/reward_rules.json | Measure-Object | Select-Object -Expand Count`). Per-rule schema (verified from the file and from [bots/v13/learning/rewards.py:130-145](bots/v13/learning/rewards.py#L130-L145)):

  ```json
  {
    "id": "scout-early",
    "description": "...",
    "condition": {"field": "game_time_seconds", "op": "<", "value": 180},
    "requires": {"field": "has_scouted", "op": "==", "value": true},
    "reward": 0.01,
    "active": true
  }
  ```

  No `trigger` or `amount` fields exist — that was stale wording in the old plan. Some rules use `"value_field": <other_field>` instead of `"value": <literal>` for cross-field comparisons.

- **`bots/current/learning/rewards.py::RewardCalculator`** ([rewards.py:56](bots/v13/learning/rewards.py#L56)) — loads rules via `load_rules()`, evaluates each per step, accumulates `episode_total`. This is where the build-order reward summand wires in (D.6).

- **`bots/current/build_orders.py`** ([build_orders.py:11](bots/v13/build_orders.py#L11)) — already exists. Defines `BuildStep(supply: int, action: str, target: str)` ([build_orders.py:11-17](bots/v13/build_orders.py#L11-L17)) and `BuildOrder(id, name, source, steps[])`. The `BuildSequencer` class ([build_orders.py:58](bots/v13/build_orders.py#L58)) is constructed and consumed by `decision_engine.py` ([decision_engine.py:11, :144, :167](bots/v13/decision_engine.py#L11)) to drive in-game production decisions, gated by **current supply**, not time. `bot.py:19` imports `BuildOrder` (not the sequencer) for snapshot-time queries. Phase D's reward-target shape is different — gated by **game time** with tolerance windowing — so a parallel dataclass `BuildOrderTrajectory` lives in the new `build_order_reward.py` (D.3). The existing classes are not modified.

- **`bots/current/learning/features.py`** — `BASE_GAME_FEATURE_DIM=40`, `FEATURE_DIM=47` ([features.py:15-19](bots/v13/learning/features.py#L15-L19)). Phase B already extended `_FEATURE_SPEC`; D.5 follows the same pattern with a fixed-width 8-slot one-hot.

- **`bots/current/data/hyperparams.json`** — flat dict of PPO hyperparams ([hyperparams.json:1-16](bots/v13/data/hyperparams.json#L1-L16)). Current keys (14): `learning_rate`, `n_steps`, `batch_size`, `n_epochs`, `gamma`, `gae_lambda`, `clip_range`, `ent_coef`, `vf_coef`, `max_grad_norm`, `net_arch`, `policy_type`, `kl_rules_coef`, `use_imitation_init`. D.6 appends `use_build_order_reward` (bool, default false) and `build_order_reward_alpha` (float, default 1.0).

- **`bots/current/learning/reward_aggregator.py`** — aggregates per-game JSONL from `data/reward_logs/`. M1 (manual) uses it for variance measurement. Per-step JSONL line shape (written by [rewards.py:213-221](bots/v13/learning/rewards.py#L213-L221)):

  ```json
  {"game_time": 245.3, "total_reward": 0.187, "fired_rules": ["scout-early", "build-cybernetics"], "is_terminal": false, "result": null}
  ```

  Terminal lines have `is_terminal: true` and `result: "win" | "loss"`. D.6 adds an optional `build_order_reward` numeric field when the flag is on (`null` or absent when off).

- **`bots/current/learning/database.py`** — SQLite store. `transitions` table is tabular: `game_id` (TEXT), `step_index` (INT), `game_time` (REAL), 40 state columns matching `_STATE_COLS` (supply_used, supply_cap, minerals, ..., enemy_cloak_count), `action` (INT), `reward` (REAL), 40 next-state columns (`next_*`), `done` (INT), `action_probs` (TEXT JSON), `win_prob` (REAL). Columns added post-original-schema live in `_LATER_ADDED_COLS: list[tuple[str, str]]` ([database.py:158](bots/v13/learning/database.py#L158)) and are ALTER-TABLE-ADD-COLUMN'd at `__init__` time. D.5 appends `("current_build_order", "TEXT")` to that list — no other migration mechanism needed.

- **Curriculum** — current curriculum has a notion of difficulty but no notion of "which build are we trying."

## 3. Scope (build steps)

### 3.1 Automated section — `/build-phase` runs D.1–D.6 end-to-end

| Step | Issue | Type | Description |
|------|-------|------|-------------|
| D.1  | #164  | code | Audit `reward_rules.json` and tag rules |
| D.2  | #165  | code | Define `z` schema + 2 example trajectory files |
| D.3  | #166  | code | Implement `build_order_reward.py` edit-distance |
| D.4  | #167  | code | Migrate (a)-tagged rules into trajectory files |
| D.5  | #168  | code | Append `z` identifier as optional obs slot |
| D.6  | #169  | code | Backwards-compat: `use_build_order_reward` flag |
| D.6.5 | #270  | code | Smoke gate — env + model + RewardCalculator wired end-to-end |

### 3.2 Manual section — operator runs M1 → M2 after the automated run

| Step | Issue | Type | Description |
|------|-------|------|-------------|
| M1   | #170  | operator | Train 3 cycles + measure early-game reward variance |
| M2   | #171  | operator | Snapshot to `vN+1` on promotion |

---

### Step D.1: Audit `reward_rules.json` and tag rules

**Problem:** Read every rule in the resolved `bots/current/data/reward_rules.json` (63 active rules at v13) and annotate each in-place with a `category` field (`"a"` build-order, `"b"` tactical, `"c"` economy, `"d"` other), so D.4 can mechanically extract (a) rules into trajectory files.

**Type:** code
**Issue:** #164
**Flags:** --reviewers code --isolation worktree
**Status:** DONE (2026-05-19)

**What to build.** Tag rule based on its `condition.field` + `requires.field`:
- (a) **build-order** — `condition.field == "game_time_seconds"` AND `requires` checks structure/tech existence (e.g., `requires.field == "has_cybernetics_core"`).
- (b) **tactical** — predicates on `army_supply`, `enemy_army_supply_visible`, engagement-related fields.
- (c) **economy** — predicates on worker counts, gas/mineral saturation fields.
- (d) **other** — everything else; default for ambiguous.

The `category` field is additive. The existing rule loader at [rewards.py:130-145](bots/v13/learning/rewards.py#L130-L145) ignores unknown fields, so forward-compat is automatic — verify with a `pytest tests/test_rewards.py` pass.

**Existing context.**
- Real rule schema (verified 2026-05-13): see §2 above.
- D.4 mechanically reads `category == "a"` to drive migration — so the audit must be machine-readable, not narrative-only.

**Files to modify/create.**
- `bots/current/data/reward_rules.json` (resolves to `bots/v13/...`) — add `category` field per rule.
- `documentation/investigations/phase-d-audit.md` (NEW) — narrative rationale for edge cases. Required, not optional — D.4 relies on it to explain unmigratable (a) rules.

**Done when.**
- Every rule has a `category` field.
- Audit doc publishes per-category counts (e.g., "12 build-order, 35 tactical, 9 economy, 7 other") and an itemized rationale for any rule whose tagging was non-obvious.
- `uv run pytest tests/test_rewards.py` passes (loader tolerates the new field).

**Depends on.** none.

**Produces.** Annotated `reward_rules.json`; audit doc.

---

### Step D.2: Define `z` schema + 2 example trajectory files

**Problem:** Define the time-gated build-order target schema as JSON files at `bots/current/data/build_orders/<label>.json`, and write 2 anchoring examples (`4-gate-aggression.json`, `robo-colossus.json`) plus a JSON Schema. The Python dataclass `BuildOrderTrajectory` that consumes these files lives in `build_order_reward.py` (created in D.3) — not in the existing `build_orders.py`.

**Type:** code
**Issue:** #165
**Flags:** --reviewers code --isolation worktree
**Status:** DONE (2026-05-19)

**What to build.** Schema for `bots/current/data/build_orders/<label>.json`:

```json
{
  "name": "4-gate-aggression",
  "targets": [
    {"action": "build", "target": "pylon",   "time_seconds": 18, "weight": 1.0},
    {"action": "build", "target": "gateway", "time_seconds": 30, "weight": 1.5},
    {"action": "build", "target": "gateway", "time_seconds": 75, "weight": 1.0}
  ],
  "tolerance_seconds": 30
}
```

**Existing context.**
- The existing `bots/current/build_orders.py::BuildStep(supply, action, target)` ([build_orders.py:11-17](bots/v13/build_orders.py#L11-L17)) is supply-gated, used by the in-game `BuildSequencer`. **Do not modify it.** Phase D's reward target is time-gated; the semantics don't overlap, so a parallel `BuildOrderTrajectory` dataclass in `build_order_reward.py` (D.3) is the right call. Two distinct types, two distinct responsibilities.
- **Schema matches existing taxonomy.** `action` is one of `"build"`, `"train"`, `"research"` (verbs from `BuildStep.action`); `target` is the unit/structure/upgrade name (e.g., `"pylon"`, `"gateway"`, `"warp_gate_research"`). Edit-distance in D.3 compares `(action, target)` tuples for equality — a "match" requires both to align AND the timing window to be satisfied. Keeping the schema parallel to `BuildStep` avoids a second action vocabulary and makes mechanical migration in D.4 straightforward (the (a)-tagged rules' `requires.field` values like `has_gateway` map cleanly to `target="gateway"`).
- Tolerance window controls how strict the timing match is during edit-distance scoring (D.3).

**Files to modify/create.**
- `bots/current/data/build_orders/` (NEW directory).
- `bots/current/data/build_orders/4-gate-aggression.json` (NEW).
- `bots/current/data/build_orders/robo-colossus.json` (NEW).
- `bots/current/data/build_orders/_schema.json` (NEW) — JSON Schema for validation.

**Done when.**
- Both example files validate against `_schema.json` (use `jsonschema` or equivalent in the test).
- Every target's `action` field is one of `"build"`, `"train"`, `"research"` (verbs from `BuildStep.action`); every target has a non-empty `target` field. Asserted in the schema validator and spot-checked in the build-step writeup.
- New tests import via `bots.current.*` (MetaPathFinder alias).

**Depends on.** D.1 (the audit reveals which structures the (a)-tagged rules reference, which informs example trajectories).

**Produces.** New directory; 2 example trajectories; schema file.

---

### Step D.3: Implement `build_order_reward.py` edit-distance

**Problem:** Create `bots/current/learning/build_order_reward.py` defining the `BuildOrderTrajectory` dataclass (consumes the D.2 schema), a JSON loader, an edit-distance progress function, and a per-step reward function. The module is the primitive — D.6 wires it into `RewardCalculator` behind a flag.

**Type:** code
**Issue:** #166
**Flags:** --reviewers code --isolation worktree

**What to build.**

```python
@dataclass
class BuildOrderStepTarget:
    action: str   # "build" | "train" | "research"
    target: str   # unit/structure/upgrade name, e.g. "pylon", "gateway"
    time_seconds: int
    weight: float = 1.0

@dataclass
class BuildOrderTrajectory:
    name: str
    targets: list[BuildOrderStepTarget]
    tolerance_seconds: int = 30

def load_build_order(label: str, *, data_dir: Path | None = None) -> BuildOrderTrajectory: ...

def compute_progress(
    executed_actions: list[tuple[str, str, int]],  # (action, target, time_seconds_at_exec)
    trajectory: BuildOrderTrajectory,
) -> float:
    # Weighted Levenshtein between executed and trajectory.targets.
    # Match: (action, target) tuple equal AND |t_exec - t_target| <= trajectory.tolerance_seconds → 0 cost.
    # Substitution / insertion / deletion → cost = target.weight (or 1.0 for executed extras).

def step_reward(prev_progress: float, curr_progress: float, alpha: float = 1.0) -> float:
    return -alpha * (curr_progress - prev_progress)
```

**Existing context.**
- Existing reward in [rewards.py:RewardCalculator](bots/v13/learning/rewards.py#L56) computes per-step rule rewards and accumulates `episode_total`. The new module is a peer, not a replacement.
- D.6 wires `step_reward(...)` into `RewardCalculator` behind the `use_build_order_reward` flag.

**Files to modify/create.**
- `bots/current/learning/build_order_reward.py` (NEW).
- `tests/test_build_order_reward.py` (NEW) — imports via `from bots.current.learning.build_order_reward import ...`.

**Done when.**
- `compute_progress` correct on synthetic sequences:
  - Perfect match → 0 distance.
  - One missing step → distance = that target's `weight`.
  - One out-of-tolerance timing → distance = that target's `weight` (sub).
- `step_reward` is `<= 0` when progress worsens (distance grows), `> 0` when progress improves.
- Empty target list returns 0 reward, no exceptions.
- `load_build_order` resolves data dir via `bots/current/current.txt` (or accepts an explicit `data_dir`).
- All tests pass under `uv run pytest tests/test_build_order_reward.py`.

**Depends on.** D.2.

**Produces.** New module; test file.

---

### Step D.4: Migrate (a)-tagged rules into trajectory files

**Problem:** Take the (a)-tagged build-order rules from D.1's audit and re-express them as `BuildOrderTrajectory` JSON files. Disable (do not delete) the migrated rules in `reward_rules.json`. Add a parity test that replays a known game log through both old and new reward paths and asserts total reward agrees within 5%.

**Type:** code
**Issue:** #167
**Flags:** --reviewers code --isolation worktree

**What to build.** For each rule with `category: "a"`:
1. Read `condition.value` (the time threshold in seconds) and `requires.field` (the structure-present predicate, e.g., `has_gateway`).
2. Map the structure predicate to an `(action, target)` tuple by stripping the `has_` prefix and routing by structure class — e.g., `has_gateway` → `(action="build", target="gateway")`, `has_cybernetics_core` → `(action="build", target="cybernetics_core")`. Upgrades use `(action="research", target="<upgrade_name>")`; unit-presence predicates (rare in the (a) set) use `(action="train", target="<unit>")`.
3. Append `{"action": <mapped_action>, "target": <mapped_target>, "time_seconds": <threshold>, "weight": <derived from reward magnitude>}` to the appropriate trajectory file.
4. Set `"active": false` on the original rule. The existing loader at [rewards.py:130-145](bots/v13/learning/rewards.py#L130-L145) already reads `active` via `r.get("active", True)` and skips inactive rules — no loader change needed. Do NOT delete the rule; flipping `active` preserves the audit trail and lets the parity test compare on-the-fly.

Multiple rules referencing the same structure at different timings collapse into one target with `tolerance_seconds` widened to cover the spread. Rules that don't fit cleanly (no `requires` block, or non-structure predicate) go into a `notes` section in `phase-d-audit.md` with rationale.

**Existing context.**
- The loader already honors `active` — verified at [rewards.py:143](bots/v13/learning/rewards.py#L143). No new field, no loader change, no silent behavior.
- "Weight derived from reward magnitude": divide rule's `reward` by the median reward across (a)-tagged rules to normalize, then clamp to `[0.25, 4.0]`.

**Files to modify/create.**
- `bots/current/data/build_orders/*.json` — additional trajectory files derived from migrated rules.
- `bots/current/data/reward_rules.json` — flip `"active": false` on migrated (a) rules. Do NOT delete.
- `bots/current/data/reward_rules.pre-phase-d-<YYYYMMDD-HHMM>.json` (BACKUP) — copy of the file as it existed at the START of D.4. Timestamp matches the existing convention seen in `bots/v13/data/reward_rules.pre-advised-20260417-2351.json` and prevents collision on re-runs. The build agent generates the timestamp at D.4 start and uses the same value in §7 kill-criterion restore.
- `tests/test_reward_migration.py` (NEW) — replay a recorded `data/reward_logs/*.jsonl` game through both old and new reward paths.

**Done when.**
- All (a)-tagged rules either migrated to a trajectory file OR explicitly listed in `phase-d-audit.md` with a reason for non-migration.
- `reward_rules.pre-phase-d-<TS>.json` backup exists at the right path (filename includes a timestamp suffix like `20260519-1430` to match the existing `pre-advised-<TS>.json` convention and avoid collision on re-runs).
- Migration parity test passes: on a recorded game log, `sum(remaining_rule_rewards + build_order_reward at α=1.0)` agrees within 5% of `sum(all_original_rule_rewards)`.
- **Pre-audit of `tests/test_rewards.py`:** scan the file for any assertion that compares against a reward magnitude contributed by a now-`active: false` (a)-tagged rule. For each hit, update the assertion using **matched-state-delta comparison** (per memory `feedback_reward_test_baseline_drift`) — compare the same state under the same `RewardCalculator` instance with and without the migrated rule, assert the delta matches expectation. Do NOT update assertions that happen to pass by accident; the goal is to keep tests catching real regressions, not to silence drift. Audit output (which tests changed and why) lands in the `phase-d-audit.md` doc.
- `uv run pytest tests/test_rewards.py tests/test_reward_migration.py` both pass.

**Depends on.** D.3.

**Produces.** Trajectory JSONs; updated `reward_rules.json`; backup; migration test.

---

<!-- autofix-applied: 2026-05-19 -->
### Step D.5: Append `z` identifier as optional obs slot

**Problem:** Add a fixed-width 8-slot one-hot `z` (build-order identifier) to `_FEATURE_SPEC`, bump `BASE_GAME_FEATURE_DIM` 40→48 and `FEATURE_DIM` 47→55. Store the active build-order name on `GameSnapshot.current_build_order: str | None` and persist via a new `current_build_order TEXT` column on the `transitions` table, added through the project's standard `_LATER_ADDED_COLS` migration mechanism. Slot 0 is the "none" bucket; slots 1–7 are reserved for the first 7 registered trajectories (alphabetical by filename).

**Type:** code
**Issue:** #168
**Flags:** --reviewers code --isolation worktree

**What to build.**
- Append 8 entries to `_FEATURE_SPEC` named `("z_slot_<i>", 1.0)` for `i ∈ [0, 7]`.
- Bump `BASE_GAME_FEATURE_DIM` 40→48 and `FEATURE_DIM` 47→55 in `features.py`.
- Add `current_build_order: str | None = None` to `GameSnapshot` in `decision_engine.py`. Default value keeps existing constructor sites green.
- Add helper `_resolve_z_index(name: str | None, registry: list[str]) -> int`:
  - `None` → 0 (none bucket).
  - Unknown name → 0 (defensive default).
  - Known name → its index in `registry[:7]` + 1 (so index 0 in the registry → slot 1).
- Build the registry by `sorted(os.listdir(bots/current/data/build_orders))` filtered to `*.json`, excluding `_schema.json`.
- Encoder reads `GameSnapshot.current_build_order`, calls the helper, fills the one-hot.
- Persistence: add a new `current_build_order TEXT` column to `transitions` via the project's standard `_LATER_ADDED_COLS` migration mechanism ([bots/v13/learning/database.py:158](bots/v13/learning/database.py#L158)). Wire the new column through `database.py::store_transition()` ([bots/v13/learning/database.py:322-370](bots/v13/learning/database.py#L322-L370)) — add the parameter, append to `cols` and `values`, default `None`. Update [bots/v13/bot.py:539 (`_record_transition`)](bots/v13/bot.py#L539) to pass `snapshot.current_build_order` through. PPO write-site analog in `environment.py` gets the same treatment.

**Existing context.**
- `_FEATURE_SPEC` extension pattern from Phase B is the template ([features.py:35](bots/v13/learning/features.py#L35)).
- 8-slot fixed width: stable PPO input shape across future trajectory additions. Slots 1–7 take the first 7 alphabetical trajectory files; an 8th trajectory file is silently ignored by the encoder (logged as a warning). A future phase can bump width if the registry outgrows 7 entries.
- The "none" bucket at slot 0 is the load-bearing default: any old transition without a `current_build_order` decodes to a one-hot of `[1, 0, 0, 0, 0, 0, 0, 0]`, matching "no build active." This is consistent with `current_build_order=None` semantics.

**Files to modify/create.**
- `bots/current/learning/features.py` — append slots, bump dims, add `_resolve_z_index` helper, registry loader.
- `bots/current/decision_engine.py::GameSnapshot` — add `current_build_order: str | None = None` field.
- `bots/current/learning/database.py` — add `("current_build_order", "TEXT")` to `_LATER_ADDED_COLS`; extend `store_transition()` signature and `cols`/`values` assembly to thread the new column (default `None`).
- `bots/current/bot.py::_record_transition` — pass `snapshot.current_build_order` to `store_transition()`.
- `bots/current/learning/environment.py` — PPO write-site analog: thread `current_build_order` to `store_transition()`.
- `tests/test_features_z.py` (NEW).
- `tests/test_database.py` (UPDATE) — extend the existing `_LATER_ADDED_COLS` migration test to assert `current_build_order` is added on a synthetic legacy DB.

**Done when.**
- `assert FEATURE_DIM == 55` and `assert BASE_GAME_FEATURE_DIM == 48` (both in the test).
- None-bucket round-trip: `GameSnapshot(current_build_order=None)` encodes with slot 0 = 1.0 and slots 1–7 = 0.0.
- Known-label round-trip: `GameSnapshot(current_build_order="4-gate-aggression")` encodes with the right slot = 1.0 (depends on alphabetical position).
- Unknown-label decodes to slot 0 (defensive default).
- 8th alphabetical trajectory present in the registry → encoder logs a warning and decodes to slot 0.
- Existing tests that construct `GameSnapshot(...)` without `current_build_order` still pass.
- `_LATER_ADDED_COLS` migration is applied: opening a synthetic legacy DB (pre-Phase-D) and inserting one row works without exception; reading back shows `current_build_order = NULL` for legacy rows and the passed string for new rows.
- `uv run pytest tests/test_features_z.py tests/test_features.py tests/test_features_v2.py tests/test_database.py` all pass.

**Depends on.** D.2 (registry of trajectory files).

**Produces.** Updated `features.py` (8 new `_FEATURE_SPEC` slots, dims bumped, registry helper), `decision_engine.py::GameSnapshot` (new `current_build_order` field), `database.py` (new `_LATER_ADDED_COLS` entry + extended `store_transition()`), `bot.py::_record_transition` (threads new field), `environment.py` (PPO write-site analog); new `tests/test_features_z.py`; updated `tests/test_database.py` migration check.

---

### Step D.6: Backwards-compat: `use_build_order_reward` flag

**Problem:** Add hyperparams `use_build_order_reward: false` and `build_order_reward_alpha: 1.0` to `hyperparams.json`. When the flag is false, reward computation is byte-identical to baseline. When true, `RewardCalculator` also calls `build_order_reward.step_reward(...)` (scaled by `alpha`) and adds the result.

**Type:** code
**Issue:** #169
**Flags:** --reviewers code --isolation worktree

**What to build.**
- Add `"use_build_order_reward": false` and `"build_order_reward_alpha": 1.0` to `bots/current/data/hyperparams.json`.
- Extend `RewardCalculator.__init__` to read both values from a passed-in hyperparams dict (or load directly if rules_path's sibling `hyperparams.json` exists).
- Per-step path: if flag is true AND `GameSnapshot.current_build_order is not None`, compute `step_reward(prev_progress, curr_progress, alpha)` and add to the per-step total. `prev_progress` is initialized to `None` at game start; first step uses 0.
- `RewardCalculator` owns the progress state across steps (resets in `open_game_log`).
- Per-step JSONL log lines gain an optional `build_order_reward` numeric field when the flag is on (`null` or absent when off, to keep flag-off byte-identical).

**Existing context.**
- `hyperparams.json` is a flat dict ([hyperparams.json:1-16](bots/v13/data/hyperparams.json#L1-L16)). No nesting; defaults live alongside existing keys.
- `RewardCalculator` is constructed once per game in the runner; per-step state ownership is fine.

**Files to modify/create.**
- `bots/current/data/hyperparams.json` — add the two flags.
- `bots/current/learning/rewards.py` — read flags, wire summand. Update the inline header comment to mention the new flags.
- `tests/test_reward_flag.py` (NEW).

**Done when.**
- Flag-off path produces byte-identical per-step rewards to baseline on a recorded game log (use the same fixture as D.4's parity test).
- Flag-on path adds the build-order summand correctly on the same log (assert nonzero delta).
- `tests/test_rewards.py` still passes (no regression).
- `uv run pytest tests/test_reward_flag.py tests/test_rewards.py tests/test_reward_migration.py` all pass.

**Depends on.** D.5.

**Produces.** Hyperparams + rewards.py extension + test.

---

### Step D.6.5: Smoke gate — env + model + RewardCalculator wired end-to-end

**Problem:** D.5 bumps `FEATURE_DIM` 47→55 and adds a new `GameSnapshot.current_build_order` field that threads through encoder → PPO → DB write site. D.6 wires the new reward summand into `RewardCalculator`. None of these have been exercised together with the real producer-consumer chain. Add a 60-second smoke gate that instantiates env + model + RewardCalculator with `use_build_order_reward=true` and runs one full step, asserting no exception on tensor shape, missing-column, or missing-method.

**Type:** code
**Issue:** #270
**Flags:** --reviewers code --isolation worktree

**What to build.** `tests/test_phase_d_smoke.py` — a single integration test that:

1. Constructs an `SC2Env` (or its lightweight test double from the existing test suite — see `tests/test_environment.py` for the established pattern) with the v13 stack.
2. Loads the model with `FEATURE_DIM=55` observation space.
3. Constructs `RewardCalculator` with `use_build_order_reward=true` and `build_order_reward_alpha=1.0`, pointing at the actual `bots/current/data/build_orders/` registry.
4. Manually constructs a `GameSnapshot(current_build_order="4-gate-aggression")` and runs:
   - `features.encode(snapshot)` — asserts shape `(55,)` and the right slot one-hot.
   - `RewardCalculator.compute_step_reward(state)` — asserts no exception, returns a finite number.
   - `database.store_transition(..., current_build_order="4-gate-aggression")` against a fresh SQLite file — asserts INSERT succeeds and SELECT round-trips the value.
5. Repeats with `current_build_order=None` to confirm legacy-path is unaffected.

**Existing context.**
- Memory `feedback_duplicate_shape_constants` cites 4 instances of this same producer-consumer drift class in one Phase 4.5 debugging session, all invisible to 682 unit tests, all caught by a 4-minute smoke. The shape changes in D.5 (FEATURE_DIM bump + new GameSnapshot field + new DB column) hit exactly the same class.
- This step is the §15.5 smoke gate of plan-review — runs in seconds, catches the 3-line bug that would otherwise consume an M1 multi-hour-cycle window.

**Files to modify/create.**
- `tests/test_phase_d_smoke.py` (NEW).

**Done when.**
- `uv run pytest tests/test_phase_d_smoke.py -x` passes.
- Smoke covers: encoder shape, RewardCalculator no-exception, DB INSERT round-trip — all three flag-on AND flag-off.
- Total smoke runtime under 60 seconds (excludes SC2 client startup — uses lightweight test double).

**Depends on.** D.6.

**Produces.** Smoke test file.

---

## 3.3 Manual section — operator runs M1 → M2 after the automated run

These steps require multi-hour SC2 wall-clock work on a Windows machine with the game client running. `/build-phase` cannot execute them — they surface as a handoff prompt at the end of the automated run.

### Step M1 (= Phase D Step 7, issue #170): Train 3 cycles + measure early-game reward variance

**Problem:** Train 3 PPO cycles with `use_build_order_reward: true` against the current curriculum, then aggregate early-game reward variance and difficulty-3 win rate to test the Phase D premise.

**Type:** operator
**Issue:** #170

**Commands.**

```powershell
# Restart evolve daemon (paused during automated run, see preflight) — optional if M1 runs immediately:
# uv run python scripts/evolve.py --hours 0  # do NOT restart until after M1 finishes

# Train 3 cycles (foreground or `Start-Process` background):
uv run python -m bots.current.runner --serve --daemon --decision-mode hybrid
# ...wait for 3 cycles to complete (~2-4h depending on game length)...

# Early-game (first 5 min) reward std-dev — inline measurement, no script needed.
# Run BEFORE flipping the flag (baseline) and again AFTER 3 cycles with flag on:
uv run python -c @"
import json, statistics
from pathlib import Path
logs = Path('bots/v13/data/reward_logs').glob('game_*.jsonl')
rewards = []
for log in logs:
    for line in log.read_text().splitlines():
        if not line: continue
        rec = json.loads(line)
        if rec.get('game_time', 0) <= 300:
            rewards.append(rec['total_reward'])
print(f'n={len(rewards)} mean={statistics.mean(rewards):.4f} stdev={statistics.stdev(rewards):.4f}')
"@

# Deterministic eval (20 games at difficulty 3):
uv run python scripts/evaluate_model.py --difficulty 3 --games 20
```

**What to look for.**

| Metric | Pass | Fail |
|---|---|---|
| Early-game (first 5 min) reward std-dev | ≥ 30% drop vs pre-D.6 baseline | < 30% drop or no change |
| Difficulty-3 win rate (20 games) | ≥ baseline WR | regression |

If variance drops but WR regresses, tune `build_order_reward_alpha` down (try 0.5, then 0.25) and re-run before snapshotting. If variance does not drop after 3 cycles, kill per §7.

**Done when.** Results appended to `documentation/soak-test-runs/phase-d-M1-<TS>.md` with both metrics and a snapshot/kill decision. Pre-D.6 baseline numbers cited explicitly (rerun `reward_aggregator` on a recent pre-D.6 log for the comparison).

**Depends on.** D.6 + clean tree + evolve daemon paused.

### Step M2 (= Phase D Step 8, issue #171): Snapshot to `vN+1` on promotion

**Problem:** If M1 gates passed, snapshot the new stack and run cross-version Elo to validate promotion.

**Type:** operator
**Issue:** #171

**Commands.**

```powershell
# Snapshot from bots/current/ (= v13) into the next auto-numbered vN+1:
uv run python scripts/snapshot_bot.py --from current
# Note the new version name printed by the script (likely "v14"). Use it below.

# Update bots/current/current.txt to point at the new version IF this is the new baseline.
# Skip this if Phase D is staying behind the flag and current should keep pointing at v13:
Set-Content bots/current/current.txt 'v14'

# Cross-version Elo via the compare subcommand (20 games new-vs-v13):
uv run python scripts/ladder.py compare v14 v13 --games 20
```

**What to look for.**

| Metric | Pass | Fail |
|---|---|---|
| Elo gain vs v13 | ≥ +10 over 20 self-play games | < +10 or regression |
| WR vs SC2 AI diff 3 (sanity) | hold vs v13 | regression |

**Done when.** `bots/v14/` exists with manifest lineage; `check_promotion()` passes ([src/orchestrator/ladder.py](src/orchestrator/ladder.py)); ladder table shows new entry.

**Depends on.** M1 pass.

---

## 4. Tests (full list)

- `tests/test_build_order_reward.py` (D.3) — edit distance correct, reward monotonic in progress, empty target list handled.
- `tests/test_reward_migration.py` (D.4) — pre/post migration reward totals on known game logs agree within 5%.
- `tests/test_features_z.py` (D.5) — z slot encoding for None / known / unknown labels; padding round-trip; FEATURE_DIM assertion.
- `tests/test_reward_flag.py` (D.6) — flag-off byte-identical to baseline; flag-on adds expected delta.

All new test files import from `bots.current.<module>` (e.g., `from bots.current.learning.build_order_reward import ...`) via the MetaPathFinder alias, so the tests track future promotions automatically. Existing tests that import `bots.v0.*` continue as historical regression coverage.

## 5. Validation (M1 + M2)

Early-game (first 5 min) reward std-dev drops ≥ 30% AND win-rate holds at difficulty 3 AND Elo gain ≥ +10 over 20 games.

## 6. Gate

All three validation criteria simultaneously.

## 7. Kill criterion

Reward variance does not drop after 3 M1 cycles — the existing rules already captured the build-order shape implicitly. On kill:

1. **Restore D.4's rule-set backup.** `Copy-Item bots/current/data/reward_rules.pre-phase-d-<TS>.json bots/current/data/reward_rules.json -Force` — undoes the `active: false` flips. Without this restore, the (a)-tagged rules' contributions stay gone and the bot runs at a net reward regression vs baseline (flag default false means `build_order_reward` is not adding anything back).
2. Leave the flag default false; do not snapshot.
3. Keep the migration as cleanup at the documentation level only — D.1's audit (`phase-d-audit.md`) and D.4's trajectory files have standalone value as a record of what was tried.

D.6.5 smoke gate is responsible for catching shape mismatches before M1 — if M1 still hits a tensor-shape exception, treat that as a D.6.5 escape and add a regression case to `tests/test_phase_d_smoke.py` before re-running.

## 8. Rollback

- D.4's `reward_rules.pre-phase-d-<TS>.json` backup restores the rule set (copy over `reward_rules.json` and remove the `active: false` flips).
- Delete `bots/v14/` if M2 already snapshotted and then the promotion failed.
- Revert the Phase D commits on `bots/current/` to undo `features.py` / `rewards.py` / `decision_engine.py` / `hyperparams.json` edits.
- `bots/current/data/build_orders/` directory can stay (unreferenced if rolled back); delete for a clean state.

## 9. /build-phase invocation

After preflight passes:

```powershell
uv run claude /build-phase --plan documentation/plans/phase-d-build-plan.md
```

`/build-phase` walks D.1 → D.6 in order. M1 and M2 surface as a handoff prompt at the end of the automated run; the agent does not attempt to execute them.
