# Phase B — Unit-Type Histogram Observation Expansion

## 1. What This Feature Does

Adds ~23 unit-type histogram observation slots to the PPO feature vector so the
neural policy can see army composition — both own units by type and scouted enemy
units by threat class. Currently the model only sees aggregate `army_supply`,
which makes it blind to *what* units exist. This answers the question: "is
observation signal the binding constraint on win rate?"

## 2. Existing Context

The feature encoding pipeline flows through four files:

- **`bots/v0/decision_engine.py`** — `GameSnapshot` dataclass (17 fields today)
  captures the game state each decision tick.
- **`bots/v0/learning/features.py`** — `_FEATURE_SPEC` maps `GameSnapshot` fields
  to a normalized float vector. `encode()` iterates the spec dynamically (no
  hardcoded indices). Current dims: `BASE_GAME_FEATURE_DIM=17` + 7 advisor = `FEATURE_DIM=24`.
- **`bots/v0/learning/database.py`** — `training.db` stores transitions with
  explicit named columns per feature. `_LATER_ADDED_COLS` handles migration of
  existing DBs (new columns default to 0).
- **`bots/v0/bot.py`** — constructs `GameSnapshot` from SC2 game state and calls
  `_record_transition()` which reads `_FEATURE_SPEC` dynamically.

Downstream consumers (`environment.py`, `trainer.py`, `imitation.py`, all tests)
import `FEATURE_DIM` / `BASE_GAME_FEATURE_DIM` constants and auto-adapt — no
code changes needed there.

All trained checkpoints become incompatible when dimensions change (expected —
Step 5 retrains from scratch).

## 3. Scope

**In scope:**
- 15 own-army unit-type slots (full Protoss roster)
- 8 enemy threat-class slots (race-agnostic buckets)
- `GameSnapshot` field additions, `_FEATURE_SPEC` extension, DB schema + migration
- `bot.py` wiring to populate counts from SC2 unit data
- Diagnostic state fixtures for mid-game compositions
- Imitation padding path verification (old 17-width → new 40-width)
- Retrain from `v0_pretrain`, compare vs Phase A end-state
- Snapshot to `v1` on promotion

**Out of scope:**
- New reward rules or action space changes
- Strategy logic changes
- Multi-race support (Phase G)
- Build-order z-stats (Phase D)
- Any changes to advisor features (the 7 advisor slots are untouched)

## 4. Impact Analysis

| File | Nature | Details |
|------|--------|---------|
| `bots/v0/decision_engine.py` | Extend | Add ~23 unit-count fields to `GameSnapshot` (default `= 0`) |
| `bots/v0/learning/features.py` | Extend | Append ~23 entries to `_FEATURE_SPEC`, bump `BASE_GAME_FEATURE_DIM` 17→40, `FEATURE_DIM` 24→47 |
| `bots/v0/learning/database.py` | Extend | Add ~46 columns (state + next_state), extend `_STATE_COLS`, add to `_LATER_ADDED_COLS` |
| `bots/v0/bot.py` | Extend | Populate new `GameSnapshot` fields from `self.units` / `self.enemy_units` |
| `bots/v0/learning/imitation.py` | Comment | Update comment referencing `FEATURE_DIM=24` (code auto-adapts) |
| `tests/test_features_v2.py` | New | Synthetic snapshot encoding + old-width padding round-trip |
| Wiki docs | Update | Dimension references in training-pipeline.md, evaluation-pipeline.md, domain-coupling.md |

**Auto-adapt (no changes needed):** `environment.py`, `trainer.py`, `rules_policy.py`,
`neural_engine.py`, `policy_probe.py`, `contracts.py`, all 10 existing test files.

## 5. New Components

### Own-army unit-type slots (15 fields)

| Field | Unit | Normalization |
|-------|------|---------------|
| `zealot_count` | Zealot | /20 |
| `stalker_count` | Stalker | /20 |
| `sentry_count` | Sentry | /20 |
| `immortal_count` | Immortal | /20 |
| `colossus_count` | Colossus | /10 |
| `archon_count` | Archon | /20 |
| `high_templar_count` | High Templar | /20 |
| `dark_templar_count` | Dark Templar | /20 |
| `phoenix_count` | Phoenix | /20 |
| `void_ray_count` | Void Ray | /20 |
| `carrier_count` | Carrier | /10 |
| `tempest_count` | Tempest | /10 |
| `disruptor_count` | Disruptor | /10 |
| `warp_prism_count` | Warp Prism | /5 |
| `observer_count` | Observer | /5 |

### Enemy threat-class slots (8 fields)

| Field | Examples | Normalization |
|-------|----------|---------------|
| `enemy_light_count` | Marine, Zergling, Zealot | /20 |
| `enemy_armored_count` | Marauder, Roach, Stalker | /20 |
| `enemy_siege_count` | Siege Tank, Lurker, Colossus | /20 |
| `enemy_support_count` | Medivac, Overlord, Warp Prism | /20 |
| `enemy_air_harass_count` | Mutalisk, Phoenix, Oracle | /20 |
| `enemy_heavy_count` | Thor, Ultralisk, Archon | /20 |
| `enemy_capital_count` | Battlecruiser, Carrier, Brood Lord | /20 |
| `enemy_cloak_count` | Banshee, Dark Templar, Ghost | /20 |

### Dimension changes

| Constant | Before | After |
|----------|--------|-------|
| `BASE_GAME_FEATURE_DIM` | 17 | 40 (17 + 15 + 8) |
| `FEATURE_DIM` | 24 | 47 (40 + 7 advisor) |

## 6. Design Decisions

**Race-agnostic enemy threat classes over per-unit slots.** Per-unit slots for all
3 races would require ~30+ mostly-sparse entries. Bucketing by tactical role
(light swarm, armored ground, siege, etc.) is more sample-efficient — the model
learns "6 heavy armored units approaching" rather than needing separate experience
with Roaches vs Marauders vs Stalkers. Slots can be added/removed later; the only
cost is retraining (dimension change invalidates checkpoints).

**Normalization caps by unit cost tier.** Cheap units (Zealot, Stalker) use /20,
expensive units (Colossus, Carrier, Tempest) use /10, support (Warp Prism,
Observer) use /5. This keeps values in a similar [0, 1] range regardless of how
many of each unit a player realistically builds.

**Default-zero fields on GameSnapshot.** All new fields default to `= 0`, so
existing `GameSnapshot(...)` construction sites (tests, diagnostic states) continue
to work without modification. Only `bot.py` actively populates them.

## 7. Build Steps

### Automated section — `/build-phase` runs these end-to-end

#### Step 1: Own-army unit-type feature slots
- **Problem:** Add 15 own-army unit-type count fields to `GameSnapshot` in `decision_engine.py`, append 15 corresponding entries to `_FEATURE_SPEC` in `features.py`, bump `BASE_GAME_FEATURE_DIM` from 17 to 32 and `FEATURE_DIM` from 24 to 39, add 30 columns (state + next_state) to `database.py` schema + `_STATE_COLS` + `_LATER_ADDED_COLS`, and create `tests/test_features_v2.py` verifying new slots produce expected values for synthetic snapshots.
- **Issue:** #128
- **Flags:** --reviewers code --isolation worktree
- **Produces:** Modified `decision_engine.py`, `features.py`, `database.py`; new `tests/test_features_v2.py`
- **Done when:** All existing tests pass + new test_features_v2 tests pass, mypy strict clean, ruff clean
- **Depends on:** none
- **Status:** DONE (2026-04-17)

#### Step 2: Enemy threat-class feature slots
- **Problem:** Add 8 enemy threat-class count fields to `GameSnapshot`, append 8 entries to `_FEATURE_SPEC`, bump `BASE_GAME_FEATURE_DIM` from 32 to 40 and `FEATURE_DIM` from 39 to 47, add 16 DB columns + migration, add enemy-bucket test cases to `test_features_v2.py`, and verify `imitation.py` padding handles old 17-width DB rows → new 40-width (update the FEATURE_DIM=24 comment). Add a unit-type-to-threat-class mapping module or dict that maps SC2 unit type IDs to the 8 buckets.
- **Issue:** #129
- **Flags:** --reviewers code --isolation worktree
- **Produces:** Modified `decision_engine.py`, `features.py`, `database.py`, `imitation.py` (comment); extended `test_features_v2.py`; new threat-class mapping
- **Done when:** All tests pass, padding round-trip test covers 17→40 width, mypy strict clean
- **Depends on:** Step 1

#### Step 3: Wire unit counts from SC2 game state
- **Problem:** In `bot.py`, populate the 15 own-army unit-count fields and 8 enemy threat-class fields on `GameSnapshot` from SC2 unit data (`self.units`, `self.enemy_units`). Own-army: count units by type ID. Enemy: map visible enemy units through the threat-class bucketing from Step 2. Add unit tests covering snapshot population logic with mock unit lists.
- **Issue:** #130
- **Flags:** --reviewers code --isolation worktree
- **Produces:** Modified `bot.py`, new/extended test coverage for snapshot population
- **Done when:** All tests pass, snapshot correctly populated for synthetic unit lists
- **Depends on:** Step 2

#### Step 4: Diagnostic states + migration smoke gate
- **Problem:** Add diagnostic state fixtures in `test_features_v2.py` covering typical mid-game compositions (e.g., 4-gate rush, robo-colossus timing, late-game deathball). Smoke-gate: write a test that opens a real `training.db`, inserts 1 row with the new 40-column schema, reads it back, and verifies migration of a simulated old-format (17-column) row via `_LATER_ADDED_COLS` defaults. This is the pipeline integration check before training.
- **Issue:** #131
- **Flags:** --reviewers auto
- **Produces:** Extended `test_features_v2.py` with diagnostic fixtures and migration smoke test
- **Done when:** All tests pass, migration smoke test confirms old→new column compatibility
- **Depends on:** Step 3

### Manual section — `/build-phase` halts here, operator takes over

#### Step 5: Train and compare
- **Type:** operator
- **Problem:** Retrain from `v0_pretrain` for 2 cycles with the expanded feature vector. Compare win rate at difficulty 3 vs Phase A end-state across 20 deterministic eval games. Run Elo self-play vs prior `v0` across 20 games.
- **Issue:** #132
- **Depends on:** Step 4

**Commands to run:**
```powershell
cd .

# Start backend (if not running)
uv run python -m alpha4gate.runner --serve

# Run imitation pre-training with new features
uv run python -m bots.v0.learning.imitation

# Run 2 PPO training cycles
# (use the daemon or manual invocation per your preferred method)

# Deterministic eval: 20 games at difficulty 3
uv run python scripts/evaluate_model.py --difficulty 3 --games 20 --decision-mode hybrid

# Elo self-play: 20 games vs v0
uv run python scripts/ladder.py run --opponent v0 --games 20
```

**What to look for:**

| Check | Pass criterion | Fail action |
|-------|---------------|-------------|
| Imitation agreement | ≥ 95% (same as Phase A) | Investigate feature encoding bug |
| Win rate at diff 3 | ≥ Phase A end-state (72%) | Try 1 more cycle; if still below, kill criterion |
| Elo vs v0 | ≥ +10 | Try 1 more cycle; if still below, kill criterion |
| Training loss curve | Converging, not diverging | Check normalization caps, feature values |
| No NaN/Inf in features | Zero occurrences | Fix normalization divisor (likely /0) |

**Gate:** Both win-rate hold AND Elo gain. Either failure after 3 total cycles → kill Phase B, skip to Phase D or E.

#### Step 6: Snapshot to v1
- **Type:** conditional
- **Problem:** If Step 5 passes the gate, run `snapshot_current()` to create `bots/v1/` with the expanded feature spec. Verify the promotion gate accepts the new version. Update wiki docs with new dimension references.
- **Issue:** #133
- **Depends on:** Step 5 (only if gate passes)

**Commands to run:**
```powershell
cd .

# Snapshot current (v0) to v1
uv run python -c "from src.orchestrator.registry import snapshot_current; snapshot_current('v1')"

# Verify v1 exists and has correct feature dim
uv run python -c "from bots.v1.learning.features import FEATURE_DIM; print(f'v1 FEATURE_DIM={FEATURE_DIM}')"

# Check promotion gate
uv run python scripts/ladder.py check-promotion
```

**What to look for:**

| Check | Pass criterion |
|-------|---------------|
| `bots/v1/` directory | Exists with full module tree |
| v1 FEATURE_DIM | 47 |
| Promotion gate | PASS |
| `bots/current/current.txt` | Points to v1 |

## 8. Risks and Open Questions

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Observation expansion doesn't improve win rate | Medium | Kill criterion: 3 cycles max, then skip to Phase D/E |
| Enemy threat-class buckets are too coarse | Low | Can split buckets later; only cost is retraining |
| DB migration breaks existing training.db | Low | `_LATER_ADDED_COLS` tested in smoke gate (Step 4) |
| Normalization caps too high/low for some units | Low | Clip to [0,1] handles overflow; undershoot reduces signal but doesn't crash |
| Imitation pre-training slower with wider vector | Low | 47 dims is still tiny; no measurable impact expected |

## 9. Testing Strategy

**New tests:**
- `tests/test_features_v2.py` — synthetic snapshot encoding for all 23 new slots,
  old-width padding round-trip (17→40), diagnostic mid-game compositions,
  DB migration smoke test

**Existing tests that auto-adapt (no changes needed):**
- `tests/test_features.py` — uses `FEATURE_DIM` constant
- `tests/test_environment.py` — uses `FEATURE_DIM` from environment
- `tests/test_imitation.py` — uses constants, padding path already tested
- `tests/test_database.py` — uses `BASE_GAME_FEATURE_DIM` constant
- All other test files reference imported constants

**Comments to update:**
- `imitation.py` line 119: `FEATURE_DIM=24` → `FEATURE_DIM=47`
- `tests/test_imitation.py` lines 94-96: dimension references

**End-to-end verification:**
- Step 4 smoke gate: real DB write/read/migrate cycle
- Step 5 operator: 20-game eval at diff 3 + 20-game Elo self-play
