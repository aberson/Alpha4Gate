# Improvement: Army Coherence and Favorable Engagement System

## Implementation status

**Steps 1–4 complete (2026-03-29). 353/353 tests passing. Zero type errors. Zero lint violations.**

Step 5 (live game test) is manual — run `uv run python -m alpha4gate.runner --difficulty 5 --realtime`.

| Step | Status | Details |
|------|--------|---------|
| Step 1: ArmyCoherenceManager class | DONE | `src/alpha4gate/army_coherence.py` — 32 unit tests |
| Step 2: Integrate into bot.py | DONE | `_resolve_attack_rally`, `_get_staging_point`, coherence param logging |
| Step 3: Staging point recalculation | DONE | 30s cache with auto-recalc on enemy structure discovery |
| Step 4: Unit tests | DONE | 13 bot integration tests in `tests/test_bot_coherence.py` |
| Step 5: Live game test | PENDING | Requires SC2 running, manual observation |

## Summary
Stop the bot from trickling units into fights one at a time. Add an army grouping system
that stages units outside enemy range, checks coherence before pushing, and retreats
intelligently based on relative army strength. All thresholds are randomized per game
and logged for training data collection.

## Current behavior
- Bot enters ATTACK when `army_supply >= 20` (hardcoded in `decision_engine.py:75`)
- During ATTACK, every army unit is individually sent toward the enemy base (`bot.py:451-453`)
- No staging, no grouping — units trickle in as they're produced and die piecemeal
- Retreat is binary: army drops below 20 → EXPAND state, units rally near natural
- No comparison to enemy army strength when deciding to attack or retreat

## Proposed changes

### Step 1: Add `ArmyCoherenceManager` class
**New file:** `src/alpha4gate/army_coherence.py`

Responsibilities:
- Per-game randomized parameter generation with ranges
- Army centroid calculation and coherence check
- Staging point calculation (distance from enemy structures)
- Attack readiness evaluation (supply ratio + coherence)
- Retreat evaluation (supply ratio check)

**Randomized parameters (generated once per game, logged):**

| Parameter | Range | Purpose |
|-----------|-------|---------|
| `attack_supply_ratio` | 1.0–1.5 | Attack when `our_supply >= enemy_visible * ratio` |
| `attack_supply_floor` | 15–25 | Minimum army supply to attack regardless of ratio |
| `retreat_supply_ratio` | 0.4–0.7 | Retreat when `our_supply < enemy_visible * ratio` |
| `coherence_pct` | 0.60–0.80 | Fraction of army that must be near centroid to push |
| `coherence_distance` | 6.0–10.0 | Max distance from centroid to count as "grouped" |
| `staging_distance` | 12.0–20.0 | Distance from nearest enemy structure to stage at |
| `retreat_to_staging` | true/false | 50/50: retreat to staging point vs defense rally |

**Key methods:**
- `__init__()` — roll all random params, return them as a dict for logging
- `compute_centroid(units) -> Point2` — average position of army units
- `is_coherent(units) -> bool` — check if `coherence_pct` of units are within `coherence_distance` of centroid
- `compute_staging_point(own_base, enemy_structures, enemy_start) -> Point2` — point along path to enemy that is `staging_distance` from nearest known enemy structure
- `should_attack(own_supply, enemy_visible_supply) -> bool` — supply ratio check with floor
- `should_retreat(own_supply, enemy_visible_supply) -> bool` — retreat ratio check
- `get_params_dict() -> dict` — all rolled values for logging

### Step 2: Integrate into `bot.py`

Modify `Alpha4GateBot`:

1. **`__init__`**: Create `ArmyCoherenceManager` instance. Log its params via `GameLogger`.

2. **`_run_micro` (ATTACK state)**: Replace current "send everyone to enemy base" with:
   - If `should_retreat()` → set rally to staging point or defense rally (per rolled param), move all units there
   - Else if not `is_coherent()` → set rally to staging point, move units there (gathering phase)
   - Else if `is_coherent()` and `should_attack()` → push: attack-move toward enemy base together
   - Else → hold at staging point (army grouped but not strong enough yet)

3. **`_rally_idle_army`**: When in EXPAND/LATE_GAME, rally to staging point instead of just defense rally, so units pre-stage.

4. **`_build_snapshot` or observer log**: Include the coherence manager's rolled params in the first log entry each game.

### Step 3: Add staging point recalculation
- Recalculate staging point periodically (every ~30 seconds) as scouting reveals new enemy structures
- Cache the point so it's not computed every frame
- Fallback: if no enemy structures known, use 70% of the distance along the path from our base to enemy start location

### Step 4: Unit tests
- `test_army_coherence.py`:
  - `test_centroid_calculation` — verify centroid math with mock unit positions
  - `test_coherence_check` — grouped units pass, scattered units fail
  - `test_staging_point_distance` — staging point is correct distance from enemy structures
  - `test_should_attack_ratio` — supply ratio logic with various own/enemy values
  - `test_should_retreat_ratio` — retreat triggers correctly
  - `test_param_randomization` — params fall within defined ranges over many rolls
  - `test_retreat_destination` — both staging and defense rally paths work

### Step 5: Integration test via game
- Run: `uv run python -m alpha4gate.runner --difficulty 5 --realtime`
- Observe: units should gather at staging point before pushing as a group
- Check logs: randomized params should appear in game log output
- Run batch: `uv run python -m alpha4gate.runner --difficulty 5 --batch 10` to collect varied training data

## Files to modify
| File | Changes |
|------|---------|
| `src/alpha4gate/army_coherence.py` | **NEW** — ArmyCoherenceManager class (7 randomized params, centroid, coherence, staging, attack/retreat) |
| `src/alpha4gate/bot.py` | Import coherence manager; `__init__` creates instance; `_run_micro` uses `_resolve_attack_rally`; `_rally_idle_army` pre-stages; `_get_staging_point` caches with 30s recalc; coherence params logged on first observer entry |
| `src/alpha4gate/micro.py` | No changes |
| `src/alpha4gate/decision_engine.py` | No changes |
| `tests/test_army_coherence.py` | **NEW** — 32 unit tests for ArmyCoherenceManager |
| `tests/test_bot_coherence.py` | **NEW** — 13 integration tests for bot coherence logic |

## Testing plan
- **Unit tests:** All Step 4 tests above — run with `uv run pytest tests/test_army_coherence.py -v`
- **Existing tests:** `uv run pytest` must still pass (no behavior changes to existing modules)
- **Game test:** `uv run python -m alpha4gate.runner --difficulty 5 --realtime` — visually confirm grouping
- **Batch test:** `uv run python -m alpha4gate.runner --difficulty 5 --batch 10` — check logs for param variance and win/loss correlation

## Risks and mitigations
| Risk | Mitigation |
|------|------------|
| Units sit at staging point forever if coherence threshold too strict | Floor: if staged for >30s without reaching coherence, push anyway |
| Staging point inside enemy range on small maps | Minimum staging distance of 12 ensures safe buffer; recalculate as structures discovered |
| Retreat oscillation (retreat → regroup → push → retreat loop) | Add hysteresis: after retreating, require higher supply ratio (attack_ratio * 1.2) before re-engaging |
| Randomized params produce unplayable games (e.g., ratio 1.5 + floor 15 = never attacks) | Validate param combinations at roll time; re-roll if contradictory |
| Observer/sentry counted in army supply inflates coherence | Already excluded in `_run_micro` army filter; keep consistent |
