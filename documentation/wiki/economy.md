# Economy & Production

How the bot manages workers, supply, production, and expansion.

> **At a glance:** MacroManager runs five checks each step (supply, workers, expansion,
> production buildings, gas). Build orders drive the OPENING phase via a supply-threshold
> sequencer. FortificationManager adds static defense during FORTIFY. BuildBacklog
> retries failed builds with a 120s expiry. Constants like WORKERS_PER_BASE=16 and
> MAX_WORKERS=44 are hardcoded. Warp Gate research is auto-queued at the Cybernetics Core.

> **Framework position:** this doc covers internals of THE TASK — the SC2 bot itself. These subsystems run inside each game; the autonomous learning loop (PLAY/THINK/FIX/TEST/COMMIT/TRAIN) never modifies them at runtime, but the FIX phase can edit constants in this file via the `/improve-bot-advised` skill.

## Purpose & Design

Three systems handle economy:

1. **Build orders** — sequenced supply-threshold steps during OPENING
2. **MacroManager** — automated economy decisions post-OPENING
3. **FortificationManager** — static defense scaling during FORTIFY

### MacroManager checks (in order)

| Check | Trigger | Action |
|-------|---------|--------|
| Supply | supply_cap - supply_used <= 4 | Build pylon |
| Workers | worker_count < ideal | Train probe from idle nexus |
| Expansion | workers near saturation AND minerals >= 400 | Expand (skip during DEFEND, except when mineral-banked >= 1500 and base_count < 4 — "anti-float override") |
| Production | gateways < base_count * 2 | Build gateway (robo after 2nd base) |
| Gas | base has no assimilators | Build assimilator |

**Ideal worker count:** `min(base_count * 16 + gas_count * 3, MAX_WORKERS)` where `MAX_WORKERS=44` (`macro_manager.py:20`, capped at `_ideal_worker_count`).

### Build order sequencer

BuildSequencer tracks progress through a BuildOrder by supply thresholds:

```
4-Gate Timing Push (9 steps):
  @14 supply: build pylon
  @16 supply: build gateway
  @16 supply: build assimilator
  @19 supply: build cybernetics_core
  @21 supply: build pylon
  @23 supply: build gateway
  @25 supply: build gateway
  @25 supply: build pylon
  @27 supply: build twilight_council
```

`should_execute(current_supply)` returns True when supply >= step threshold.
`advance()` increments to next step. OPENING ends when sequencer is complete.

### Fortification scaling

During FORTIFY, FortificationManager calculates desired defense count:

```
advantage = enemy_supply - own_supply
desired = clamp(advantage // defense_scaling_divisor, min_defenses, max_defenses)
```

Build priority: Pylon (if needed) → Shield Batteries (need CyberneticsCore) →
Forge (if needed) → Photon Cannons.

### Build backlog

Failed builds (e.g., not enough minerals, no valid placement) go into BuildBacklog:
- Max 6 entries
- Each entry has 120s TTL
- `tick()` checks affordability and retries the oldest affordable entry
- Expired entries are silently dropped

---

## Implementation Notes

| Constant | Value | Where |
|----------|-------|-------|
| `WORKERS_PER_BASE_MINERALS` | 16 | macro_manager.py |
| `WORKERS_PER_GAS` | 3 | macro_manager.py |
| `MAX_WORKERS` | 44 | macro_manager.py |
| `SUPPLY_BUFFER` | 8 | macro_manager.py (scales with gateway count) |
| `GATEWAY_PER_BASE` | 2 | macro_manager.py |
| `ROBO_THRESHOLD_BASES` | 2 | macro_manager.py |
| `DEFAULT_EXPIRY_SECONDS` | 120.0 | build_backlog.py |
| `DEFAULT_MAX_SIZE` | 6 | build_backlog.py |

| File | Purpose |
|------|---------|
| `src/alpha4gate/macro_manager.py` | Economy automation |
| `src/alpha4gate/build_orders.py` | BuildOrder, BuildStep, BuildSequencer |
| `src/alpha4gate/build_backlog.py` | Retry queue for failed builds |
| `src/alpha4gate/fortification.py` | Static defense scaling |
| `data/build_orders.json` | Saved build orders (used by backend; no longer surfaced in the UI) |
