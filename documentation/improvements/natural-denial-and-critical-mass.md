# Improvement: Natural denial with critical mass grouping

## Summary
Change the bot's attack behavior to deny the enemy natural expansion instead of
pushing up the ramp, and enforce a hard coherence gate so units never engage
piecemeal. Add a late-game condition to commit to the enemy main at high supply.

## Previous behavior (before this improvement)
- `_attack_target()` returned enemy main base or start location, sending the army
  up the ramp.
- The coherence system (army_coherence.py) only gated the *rally point* decision.
  Once `_run_micro()` fired, every unit that saw an enemy got an individual attack
  command via `MicroController.generate_commands()` — regardless of whether the
  army was grouped.
- `_rally_idle_army()` only moved units that were `is_idle` and far from rally, so
  newly produced units trickled forward independently.
- Staging timeout was 30s, after which the bot pushed even when ungrouped.

## Proposed changes

### Step 1: Compute enemy natural expansion location
- [x] Add a method `_enemy_natural()` to `Alpha4GateBot` that finds the closest
  expansion location to `self.enemy_start_locations[0]` (excluding the start
  location itself). Cache it like `_cached_staging_point`.
- [x] Use `self.expansion_locations_list` (burnysc2 BotAI built-in property) to find candidates.

### Step 2: Change attack target to enemy natural
- [x] Modify `_attack_target()` to return the enemy natural position instead of
  enemy main/start location.
- [x] Keep the enemy main as a fallback if natural can't be determined.

### Step 3: Late-game ramp push at high supply
- [x] Add a constant `PUSH_MAIN_SUPPLY` = 160 (or configurable) to
  `Alpha4GateBot`.
- [x] In `_attack_target()`, if `self.supply_used >= PUSH_MAIN_SUPPLY`, return
  the enemy main instead of the natural.
- [x] This gives a clear escalation path: deny natural early, kill main when maxed.

### Step 4: Hard coherence gate on micro — no piecemeal attacks
- [x] In `_run_micro()`, before calling `micro_controller.generate_commands()`,
  check `cm.is_coherent(army)`.
- [x] If NOT coherent: skip micro entirely. Instead, move all army units to the
  staging point (gather command). This prevents any unit from engaging solo.
- [x] If coherent AND `should_retreat()`: move all units to staging/defense rally
  (existing retreat logic).
- [x] If coherent AND NOT retreating: proceed with normal micro (focus fire, kite).
- [x] This replaces the current flow where micro fires unconditionally in ATTACK/DEFEND.

### Step 5: Increase staging timeout
- [x] Increase `STAGING_TIMEOUT_SECONDS` from 30 to 60. The army needs more time
  to group up, especially with the hard gate preventing early engagement.

### Step 6: Rally ALL army units, not just idle ones
- [x] In `_rally_idle_army()`, remove the `unit.is_idle` check. All non-engaged
  army units should move to the staging point when not in ATTACK/DEFEND state.
  This prevents produced units from standing at production buildings.
- [x] Keep the `distance > 10` check to avoid spamming move commands on units
  already near rally.

## Files to modify
| File | Changes |
|---|---|
| `src/alpha4gate/bot.py` | Steps 1-4, 6: new `_enemy_natural()`, modify `_attack_target()`, add coherence gate in `_run_micro()`, fix `_rally_idle_army()` |
| `src/alpha4gate/army_coherence.py` | Step 5: increase `STAGING_TIMEOUT_SECONDS` |
| `tests/test_bot_coherence.py` | Update tests for new attack target logic and coherence gate |
| `tests/test_army_coherence.py` | Update staging timeout test expectations |

## Testing plan
- **Unit tests:** Add tests for `_enemy_natural()` returning correct expansion.
  Update `test_bot_coherence.py` to verify:
  - Ungrouped army does NOT get attack commands (gather instead)
  - Grouped army gets normal micro commands
  - Attack target is enemy natural, not enemy main
  - At supply >= 160, attack target switches to enemy main
- **Game test:** `uv run python -m alpha4gate.runner --realtime --difficulty 3 --build-order 4gate`
  Watch for:
  - Army gathers at staging point before moving out
  - Army attack-moves to enemy natural, not up the ramp
  - Army retreats if losing the fight at the natural
  - No units trickle in solo
  - At high supply, army pushes into enemy main
- **Regression:** All 378 tests passing (371 original + 7 new).

## Risks and mitigations
- **Enemy natural detection:** `expansion_locations_list` may not always be sorted
  predictably. Mitigation: sort by distance to enemy start, pick closest non-start.
- **Hard coherence gate could cause army to sit idle too long** if units are
  spread out and never regroup. Mitigation: the 60s staging timeout is the safety
  valve — army pushes regardless after 60s.
- **DEFEND state also runs micro** — the coherence gate should NOT apply during
  defense (enemy is at our base, we must fight immediately even if ungrouped).
  Mitigation: only apply the hard gate in ATTACK state, not DEFEND.
- **Supply 160 threshold for main push** might be too high or too low depending
  on game state. Can be tuned after observing games.
