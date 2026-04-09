# Army & Combat

Coherence, staging, engagement, retreat, and micro.

> **At a glance:** ArmyCoherenceManager gates attacks behind supply thresholds and army
> grouping checks — all parameters randomized per game for training diversity.
> MicroController generates per-unit commands: focus fire on highest-priority target,
> kiting for ranged units. Staging point computed as a fixed distance from the nearest
> enemy structure along the approach line.

## Purpose & Design

Two systems work together:

1. **Coherence** — decides *when* to attack, *where* to stage, and *when* to retreat.
   Prevents trickle attacks by requiring a critical mass of grouped units.
2. **Micro** — decides *how* each unit fights. Target selection, kiting, rally.

### Coherence flow

```
Every step during ATTACK/DEFEND:
  1. compute_centroid(army_units) → center of mass
  2. is_coherent(army_units) → are enough units near centroid?
  3. should_attack(own_supply, enemy_supply) → supply ratio + hysteresis check
  4. If coherent AND should_attack → send MicroController commands
  5. If should_retreat → pull back to staging point, notify_retreat()
  6. If staging too long (60s) → force attack anyway
```

### Randomized parameters

Every game, ArmyCoherenceManager rolls fresh parameters from uniform distributions:

| Parameter | Range | What it controls |
|-----------|-------|-----------------|
| attack_supply_ratio | 1.0 - 1.5 | How much stronger we need to be |
| attack_supply_floor | 15 - 25 | Minimum supply before any attack |
| retreat_supply_ratio | 0.4 - 0.7 | How weak triggers retreat |
| coherence_pct | 0.60 - 0.80 | % of army that must be grouped |
| coherence_distance | 6 - 10 | Distance threshold for "grouped" |
| staging_distance | 12 - 20 | How far from enemy to stage |
| fortify_trigger_ratio | 1.3 - 2.0 | Enemy advantage that triggers FORTIFY |
| max_defenses | 3 - 5 | Cap on static defense buildings |
| retreat_to_staging | 50% chance | Retreat to staging vs main base |

This creates training diversity — the bot explores different aggression levels
across games, generating varied training data for PPO.

### Micro system

MicroController generates one command per unit per step:

```
For each non-worker unit:
  If enemies nearby:
    target = select_target(enemies)  # highest priority, lowest HP at tie
    If kiting unit (Stalker, Sentry, Immortal, Colossus, Voidray) AND enemy close:
      → MOVE away (kite_position, 5.0 distance)
    Else:
      → ATTACK focus target
  Else:
    → MOVE to rally point
```

**Target priority** (higher = kill first):
- 9-10: Medivac, Sentry, WarpPrism, Infestor, Viper (support/high-value)
- 7-8: SiegeTank, Colossus, Disruptor, HighTemplar (damage dealers)
- 5-6: Stalker, Marauder, Hydralisk (core army)
- 2-3: Marine, Zealot, Zergling (fodder)
- 1: SCV, Probe, Drone (workers)

---

## Implementation Notes

**Hysteresis:** After a retreat, the attack threshold is multiplied by 1.2 to prevent
oscillation (attack → retreat → attack). Reset when attack is committed.

**Staging point:** Computed as a point `staging_distance` units from the nearest enemy
structure along the line from own base. Fallback: 70% of distance to enemy start
location if no structures known. Recalculated every 30 seconds.

| File | Purpose |
|------|---------|
| `src/alpha4gate/army_coherence.py` | ArmyCoherenceManager — parameters, staging, attack/retreat |
| `src/alpha4gate/micro.py` | MicroController — target selection, kiting, commands |
