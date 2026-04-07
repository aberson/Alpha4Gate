# Improvement: Defensive Fortification (FORTIFY State)

## Summary
Add a new `FORTIFY` strategic state that triggers after the bot loses an engagement
or detects a sustained army supply disadvantage. The bot builds Shield Batteries and
Photon Cannons at the natural, retreats army to them, and holds until army supply
recovers. A new build backlog system retries failed build commands when resources
become available.

## Current behavior (pre-implementation)
- **DEFEND** (`decision_engine.py`, `_compute_next_state()`):
  Triggers only when `enemy_army_near_base` (within 40 units). Fights with existing
  army at the defense rally point. No structures are built.
- **Retreat** (`army_coherence.py`, `should_retreat()`):
  Triggers when `own_supply < enemy_supply * retreat_ratio`. Army retreats to staging
  or defense rally, then does nothing further.
- **Post-engagement** (`decision_engine.py`, `_compute_next_state()`):
  When army drops below `ATTACK_ARMY_SUPPLY` (20), bot transitions to EXPAND. No
  assessment of whether army is outmatched — just rebuilds and attacks again.
- **No defensive structures** were ever built. `PhotonCannon` and `ShieldBattery` were
  not in `_TARGET_MAP`. Forge was tracked but never auto-built.
- **Failed build commands** (`executor.py`):
  Returned `success=False` and were silently dropped. No retry mechanism.

## Proposed changes

### Step 1: Build backlog for failed commands

**Key types:**
- `BacklogEntry(structure_type: str, location: tuple[float, float], reason: str, enqueued_time: float)` — a single failed build request.
- `MacroDecision(action: str, target: str, reason: str)` — a build/train decision from `macro_manager.py`. Actions: `"build"`, `"train"`, `"expand"`. Targets match keys in `_TARGET_MAP` (e.g. `"PhotonCannon"`, `"ShieldBattery"`).

- [x] Add a `BuildBacklog` class (new file `src/alpha4gate/build_backlog.py`) that
  stores failed build requests (structure type, location, reason).
- [x] Each game step, the backlog checks affordability and retries the oldest entry.
- [x] Integrate into `bot.py:on_step()` — drain one backlog item per step when affordable.
- [x] Unit tests for backlog queue, retry, and expiry (items expire after 120 game-seconds).

### Step 2: FORTIFY strategic state + decision engine transitions
- [x] Add `FORTIFY = "fortify"` to `StrategicState` enum.
- [x] Add `GameSnapshot` fields: `cannon_count`, `battery_count` (for decision-making).
- [x] Add FORTIFY transitions to `_compute_next_state()`:
  - **Entry:** From any state when `enemy_visible_supply > own_supply * fortify_trigger_ratio`
    AND `_recently_retreated` is True (i.e., lost an engagement + enemy is bigger).
    Also enter FORTIFY from ATTACK when army supply drops below threshold after retreat.
  - **Exit:** When `own_supply >= enemy_visible_supply * attack_supply_ratio` again
    (army recovered), transition to EXPAND or ATTACK.
  - FORTIFY has lower priority than DEFEND (if enemy is at base, DEFEND takes over).
- [x] Add `fortify_trigger_ratio` as a randomized parameter in `ArmyCoherenceManager`
  (range 1.3–2.0) for training data diversity.
- [x] Add `_STATE_TO_ACTION` mapping for FORTIFY (action index 5).
- [x] Unit tests for all FORTIFY transitions.

### Step 3: Fortification manager — build defenses
- [x] Add `FortificationManager` class (new file `src/alpha4gate/fortification.py`):
  - Calculates how many cannons/batteries to build based on enemy supply advantage:
    `count = clamp(enemy_advantage // defense_scaling_divisor, min_defenses, max_defenses)`
  - `defense_scaling_divisor` randomized per game (range 8–15) for training.
  - `min_defenses` = 1, `max_defenses` randomized (range 3–5).
  - Ensures Forge exists (queues build if not). Batteries need only CyberneticsCore (already built by 4gate).
  - Returns `MacroDecision` list: Pylon (if needed) → Shield Batteries → Forge (if needed)
    → Photon Cannons. Batteries prioritized (CyberCore already exists in 4gate).
  - Build location: near the natural ramp/choke (use `main_base_ramp.bottom_center` or
    closest pylon to natural expansion).
- [x] Add `PhotonCannon` and `ShieldBattery` to `_TARGET_MAP` in `bot.py`.
- [x] Add `photon_cannon` and `shield_battery` to `_STRUCTURE_MAP` in `executor.py`.
- [x] Failed builds go into the build backlog (from Step 1).
- [x] Unit tests for scaling formula, Forge prerequisite logic, location selection.

### Step 4: Bot integration — FORTIFY behavior in on_step()
- [x] In `on_step()`, when state is FORTIFY:
  - Run `FortificationManager.evaluate()` to get defense build decisions.
  - Execute those decisions via `_execute_macro()` (failures go to backlog via return value check).
  - Continue army production (same as EXPAND — keep building units).
  - Rally army to defense rally point (near natural ramp) and hold.
  - Army engages enemies only when they come within range of defenses.
- [x] When state exits FORTIFY, stop queuing new defenses but keep existing ones.
- [x] Update `_run_micro()` to handle FORTIFY: fight at defense rally, don't chase.
- [x] Update neural engine `_ACTION_TO_STATE` / feature spec for new state.
- [x] Wire `notify_retreat()` from `_resolve_attack_rally()` when `should_retreat()` returns True.
- [x] Unit tests for FORTIFY behavior in on_step integration.

### Step 5: Wire up and integration test
- [x] Full test suite passes: `uv run pytest` — 578/578 passing
- [x] Lint passes: `uv run ruff check .` — all checks passed
- [x] Typecheck passes: `uv run mypy src` — no issues in 38 source files
- [x] Update `_FEATURE_SPEC` and training DB schema if new snapshot fields added.

## Files to modify

| File | Changes |
|------|---------|
| `src/alpha4gate/build_backlog.py` | **NEW** — BuildBacklog class with queue, retry, expiry |
| `src/alpha4gate/fortification.py` | **NEW** — FortificationManager: scaling, Forge prereq, build decisions |
| `src/alpha4gate/decision_engine.py` | Add FORTIFY state, transitions, snapshot fields |
| `src/alpha4gate/army_coherence.py` | Add `fortify_trigger_ratio`, `defense_scaling_divisor`, `max_defenses` params |
| `src/alpha4gate/bot.py` | Add PhotonCannon/ShieldBattery to `_TARGET_MAP`, FORTIFY handling in `on_step()`, backlog drain, `_STATE_TO_ACTION[FORTIFY]` |
| `src/alpha4gate/micro.py` | No changes needed (FORTIFY uses rally, not micro push) |
| `src/alpha4gate/macro_manager.py` | No changes (FORTIFY uses FortificationManager, not MacroManager) |
| `src/alpha4gate/commands/executor.py` | Add photon_cannon/shield_battery to `_STRUCTURE_MAP` |
| `src/alpha4gate/learning/features.py` | Add cannon_count, battery_count features |
| `src/alpha4gate/learning/environment.py` | Add FORTIFY action mapping |
| `src/alpha4gate/learning/database.py` | Add cannon_count, battery_count columns |
| `tests/test_build_backlog.py` | **NEW** — backlog unit tests |
| `tests/test_fortification.py` | **NEW** — fortification manager unit tests |
| `tests/test_decision_engine.py` | Add FORTIFY transition tests |
| `tests/test_bot_fortify.py` | **NEW** — integration tests for FORTIFY in on_step |

## Testing plan
- **Unit tests:** backlog queue/retry/expiry, fortification scaling formula, Forge prereq,
  FORTIFY state transitions (entry from retreat, exit on recovery, priority vs DEFEND)
- **Game test:** `uv run python -m alpha4gate.runner --difficulty 5 --realtime`
  - Expected: bot attacks, loses first engagement, retreats, enters FORTIFY, builds
    cannons + batteries at natural, holds position, rebuilds army, exits FORTIFY when
    supply recovers, resumes attacking
- **Regression:** all existing tests must pass (`uv run pytest`)

## Risks and mitigations
| Risk | Mitigation |
|------|-----------|
| Forge build delays cannons by 30s+ | Queue Forge early; batteries don't need Forge so they go up immediately |
| Resource starvation: building defenses starves army production | Cap total defense spending; army production continues in FORTIFY |
| FORTIFY loops: bot keeps entering/exiting FORTIFY | Hysteresis via `_recently_retreated` + `fortify_trigger_ratio` prevents rapid cycling |
| Build backlog grows unbounded | Expiry timer (120s) auto-removes stale entries; cap at 6 items max |
| Training DB schema migration | New columns have DEFAULT 0, backward compatible |
| Randomized params produce degenerate games | Ranges are bounded and validated (same pattern as existing coherence params) |

---

## Build Results — All 5 Steps Complete

**578/578 tests passing. Zero type errors. Zero lint violations.**

Built via two rwl-full runs (code-only mode, 4 reviewers each):
- **Run 1 (Steps 1-2):** PASS iteration 2/2. BuildBacklog + FORTIFY state.
- **Run 2 (Steps 3-4):** PASS iteration 2/2. FortificationManager + bot integration.

### Reviewer-caught bugs fixed

| Bug | How caught | Fix |
|-----|-----------|-----|
| `_drain_backlog` never issued build command | Correctness + Bug reviewers (Run 2) | Made async, calls `_build_structure()` |
| `_execute_macro` returned None, try/except dead code | Correctness + Bug reviewers (Run 2) | Returns `bool`, FORTIFY uses return value |
| Test name contradicted assertions | Test Quality reviewer (Run 1) | Renamed boundary test |
| Tautological enum/flag tests | Test Quality reviewer (Run 1 + 2) | Removed 4 tests |

### Files changed

| File | Change |
|------|--------|
| `src/alpha4gate/build_backlog.py` | **NEW** — BuildBacklog class with queue, retry, expiry |
| `src/alpha4gate/fortification.py` | **NEW** — FortificationManager: scaling, Forge prereq, build decisions |
| `src/alpha4gate/decision_engine.py` | FORTIFY state, transitions, GameSnapshot fields, notify_retreat |
| `src/alpha4gate/army_coherence.py` | fortify_trigger_ratio, defense_scaling_divisor, max_defenses params |
| `src/alpha4gate/bot.py` | _TARGET_MAP entries, FORTIFY on_step, backlog drain, _execute_macro→bool, notify_retreat wiring |
| `src/alpha4gate/commands/executor.py` | photon_cannon/shield_battery in _STRUCTURE_MAP |
| `src/alpha4gate/learning/features.py` | FEATURE_DIM 15→17, cannon_count/battery_count |
| `src/alpha4gate/learning/environment.py` | FORTIFY action (index 5), Discrete(6), _snapshot_to_raw |
| `src/alpha4gate/learning/database.py` | 4 new columns (cannon/battery + next_), _STATE_COLS updated |
| `tests/test_build_backlog.py` | **NEW** — 8 backlog unit tests |
| `tests/test_fortification.py` | **NEW** — 22 fortification manager tests |
| `tests/test_bot_fortify.py` | **NEW** — 7 FORTIFY integration tests |
| `tests/test_decision_engine.py` | 11 FORTIFY transition + snapshot field tests |
| `tests/test_army_coherence.py` | Updated for new params |
| `tests/test_bot_coherence.py` | Updated for param count 8→10 |
| `tests/test_features.py` | Updated for FEATURE_DIM=17 |
| `tests/test_environment.py` | Updated for 6 actions |
| `tests/test_database.py` | Updated for new columns |
| `tests/test_e2e_pipeline.py` | Updated for new feature dim |

### Next: game test

```bash
uv run python -m alpha4gate.runner --difficulty 5 --realtime
```

Expected: bot attacks, loses engagement, retreats, enters FORTIFY, builds cannons + batteries at natural, holds, rebuilds army, exits FORTIFY when supply recovers, resumes attacking.
