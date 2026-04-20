# Phase G Build Plan — Multi-race support (Zerg, then Terran)

**Parent plan:** [alpha4gate-master-plan.md](alpha4gate-master-plan.md) — Phase G
**Track:** Capability (Multi-race)
**Status:** Future. **Prerequisites:** Phase 6 operational (the autonomous loop works end-to-end for Protoss first).
**Effort estimate:** ~7–10 weeks total across G.1–G.4.
**Detail extracted from the master plan on 2026-04-19** as part of the plan/build-doc cleanup.

## 1. What this feature does

Extends the bot from Protoss-only to all three SC2 races. Each race is
a separate `bots/` version line sharing infrastructure but with its own
gameplay code.

This is a large effort — the gameplay layer (macro, micro, production,
abilities, build orders, reward rules, feature encoding) is deeply
Protoss-specific today. The architecture (decision engine, command
system, PPO pipeline, dashboard, ladder, sandbox) is already
race-agnostic.

## 2. Existing context

- **`bots/v0/`** — Protoss-only stack. Production code is in 46 modules.
- **`src/orchestrator/`** — race-agnostic substrate (registry, snapshot,
  selfplay, ladder, sandbox).
- **Dashboard, daemon, evaluator, promoter, rollback monitor** — all
  race-agnostic.
- **`reward_rules.json`** (~48 rules in v0) — Protoss-specific.
- **`_FEATURE_SPEC`** — Protoss-specific unit-type slots after Phase B.

## 3. Phased approach

### G.1 — Race interface extraction

Before adding new races, extract a race-agnostic interface from the
Protoss code:

- `RaceConfig`: unit roster, production tree, ability set, macro
  mechanic (Chronoboost vs Inject vs MULE), worker type, supply
  structure
- `ProductionAdapter`: abstract over Warp Gate vs Larva vs Add-On
- `MicroAdapter`: abstract over race-specific abilities
- `FeatureSpec`: race-parameterized unit-type slots
- `RewardTemplate`: race-parameterized reward rules
- Refactor `bots/v0/` (Protoss) to use these interfaces — no behavior
  change, just structural extraction. All 1020+ tests must still pass.

**Effort:** 1–2 weeks.
**Gate:** All existing Protoss tests pass, interface coverage for 3 races.

### G.2 — Zerg

First new race (most different from Protoss — validates the interface
deeply):

- `bots/zerg_v0/`: Larva/Inject economy, Creep spread, Overlord supply,
  morph-based production (Roach, Hydra, Lurker, Mutalisk, Corruptor,
  Brood Lord, Viper, Infestor, Ultralisk, Baneling, Zergling)
- Zerg-specific micro: Bile, Fungal, Burrow, Abduct
- Zerg build orders (hatch-first, pool-first, 12-pool)
- Zerg reward rules (~40 rules, adapted from Protoss patterns)
- Zerg feature encoding (unit-type slots, larva count, inject timers)
- Train from scratch, promote via Elo ladder (vs SC2 AI, not vs Protoss)

**Effort:** 3–4 weeks.
**Gate:** Zerg wins ≥50% at difficulty 3 over 20 games.

### G.3 — Terran

Second new race:

- `bots/terran_v0/`: SCV economy, MULEs, Supply Depot walls, Add-On
  production (Reactor/Tech Lab), Siege mode, Stim, Medivac healing
- Terran-specific micro: Siege/Unsiege, Stim, Snipe, EMP, Nuke
- Terran build orders (1-1-1, 2-1-1, mech, bio)
- Terran reward rules and feature encoding
- Train from scratch, promote via Elo ladder

**Effort:** 2–3 weeks (interface proven).
**Gate:** Terran wins ≥50% at difficulty 3 over 20 games.

### G.4 — Cross-race ladder

Once all three races have promoted versions, enable cross-race self-play
in the ladder. Each race's version line competes within-race for
promotion; cross-race matches are informational (Elo tracked separately).

**Effort:** 2–3 days.
**Gate:** Cross-race Elo ladder produces stable rankings.

## 4. Phase 8 (improve-bot-evolve) interaction

When G.2 (Zerg) ships, the evolve skill needs:

- A `--race {protoss,zerg,terran}` flag selecting which race line to
  evolve.
- Per-race parent chains: a Zerg evolve round snapshots from
  `bots/zerg_vN/` and produces `bots/zerg_vN+1/` and `bots/zerg_vN+2/`,
  not Protoss versions.
- Mirror-seed games stay same-race (Zerg-vs-Zerg), so the seed-prompt
  data stays in-distribution for Claude.
- Optionally: a cross-race safety gate (winner must also not regress
  badly against a different-race opponent) — flagged as open question
  in `documentation/plans/improve-bot-evolve-plan.md` §8.

Sandbox hook: `EVO_AUTO=1` already permits writes under `bots/**`, so
no hook change is needed when new race directories appear.

## 5. Tests

Per sub-phase. Core requirement: G.1 must keep all 1020+ existing tests
green; G.2 / G.3 add per-race test suites; G.4 adds cross-race ladder
math + replay.

## 6. Effort total

| Sub-phase | Estimate |
|-----------|----------|
| G.1 (interface extraction) | 1–2 weeks |
| G.2 (Zerg) | 3–4 weeks |
| G.3 (Terran) | 2–3 weeks (interface proven) |
| G.4 (cross-race ladder) | 2–3 days |
| **Total** | **~7–10 weeks** |

## 7. Kill criterion

G.1 interface extraction proves too invasive (breaks >5% of tests or
requires >500 lines of adapter code). Indicates the gameplay layer is
more tightly coupled than expected — defer and revisit after more
capability phases mature the Protoss codebase.

## 8. Rollback

Each race is its own `bots/` directory — delete and ladder is unaffected.
