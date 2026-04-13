# Domain Coupling

What's SC2-specific vs what could work with any game or domain.

> **At a glance:** 16 of 23 top-level Python modules (70%) have zero SC2/burnysc2 imports
> and are fully domain-agnostic (46 .py files total including subdirectories). The entire
> learning pipeline (trainer, rewards, features, neural engine, database, checkpoints,
> imitation) is generic. SC2 coupling concentrates in 7 modules: `bot.py`,
> `connection.py`, `observer.py`, `macro_manager.py`, `micro.py`,
> `scouting.py`, `commands/executor.py`, and `learning/environment.py` (hybrid). The
> `SC2Env` gymnasium wrapper is the key bridge — its public interface is domain-agnostic,
> with SC2 isolated in one private method.

## Purpose & Design

This page maps the boundary between SC2-specific code and domain-agnostic infrastructure.
It exists to support two goals:

1. **Phase 5 (Domain Abstraction)** — when we extract the training/eval/monitoring loop
   into something that works with any domain, this map shows exactly what to change.
2. **Day-to-day work** — when modifying the learning pipeline, know that you're in
   domain-agnostic territory and shouldn't introduce SC2 imports.

### The coupling spectrum

```
Tier 0: Pure Domain-Agnostic     Tier 1: SC2 at Data Level     Tier 2: SC2 Types     Tier 3: SC2 API
─────────────────────────────    ──────────────────────────     ──────────────────     ──────────────
config, logger, web_socket       decision_engine                macro_manager          bot.py
api, console, batch_runner       claude_advisor                 micro                  connection.py
commands/{primitives,parser,     learning/trainer               scouting               observer.py
  interpreter,queue,recipes}     learning/rewards               commands/executor
learning/{features,neural_engine,                               build_orders
  rewards,database,checkpoints,
  hyperparams,imitation,trainer}
army_coherence, fortification
build_backlog, replay_parser
```

**Tier 0** — No SC2 imports, no SC2 concepts. Works with any domain today.
**Tier 1** — No SC2 imports, but operates on data shaped by SC2 (field names like
`army_supply`, reward rules that mention scouting). Swapping domains means changing
config/data, not code.
**Tier 2** — Imports `UnitTypeId`, `UpgradeId`, or `Point2` from sc2. Uses SC2 type
enums in lookup tables. Could be decoupled with a unit registry abstraction.
**Tier 3** — Imports `BotAI`, `run_game`, `Race`, `Difficulty`. Directly calls the
burnysc2 API. Would require a full rewrite or abstraction layer to swap.

### The SC2Env bridge (hybrid)

`learning/environment.py` deserves special mention. It's the **only module** that spans
tiers 0 and 3:

- **Public interface** (Tier 0): `gymnasium.Env` with `reset()`, `step()`, `close()`,
  `Box` observation space, `Discrete` action space. Fully domain-agnostic.
- **Private internals** (Tier 3): `_sync_game()` calls `sc2.main.run_game()`, creates
  `_FullTrainingBot(BotAI)`, uses `Race.Protoss` and `Difficulty` enums.

This is excellent design for domain abstraction — swap `_sync_game()` and
`_make_training_bot()` with equivalents for a new domain, and everything above
(trainer, SB3, checkpoints) works unchanged.

---

## Module-by-Module Map

> **At a glance:** Scan the "SC2?" column. Green means domain-agnostic. Red means
> tightly coupled. Yellow means data-level coupling only.

### Learning pipeline (all Tier 0)

| Module | SC2? | What makes it generic |
|--------|------|----------------------|
| `learning/trainer.py` | No | Manages RL loop via gymnasium.Env; never imports SC2 types |
| `learning/neural_engine.py` | No | Takes GameSnapshot, returns StrategicState via SB3 inference |
| `learning/rewards.py` | No | JSON-driven rule engine; operates on dict fields |
| `learning/features.py` | No | Pure numeric: GameSnapshot → normalized float vector |
| `learning/database.py` | No | SQLite: stores raw numpy arrays + metadata |
| `learning/checkpoints.py` | No | Save/load/prune SB3 models; manifest tracking |
| `learning/imitation.py` | No | Behavior cloning via PyTorch training loop |
| `learning/hyperparams.py` | No | Load PPO hyperparams from JSON |
| `learning/environment.py` | **Hybrid** | Public Env interface is generic; SC2 isolated in `_sync_game()` |

### Decision & strategy (Tier 0-1)

| Module | SC2? | Notes |
|--------|------|-------|
| `decision_engine.py` | No (Tier 1) | StrategicState enum is abstract (OPENING, EXPAND, ATTACK, etc.). Transitions use generic metrics (supply, army, time). Thresholds are tunable numbers, not SC2-specific logic. |
| `army_coherence.py` | No | Randomized parameter generation for army thresholds |
| `fortification.py` | No | Scaling formula: `enemy_advantage // divisor → defense_count` |
| `build_backlog.py` | No | Retry queue with expiry + affordability check |
| `build_orders.py` | No (Tier 1) | Generic `BuildOrder`/`BuildStep` dataclasses. Structure is domain-agnostic but content (unit names) is SC2-specific when instantiated |

### Command system (mostly Tier 0)

| Module | SC2? | Notes |
|--------|------|-------|
| `commands/primitives.py` | No | `CommandAction`, `CommandPrimitive`, `CommandMode` enums |
| `commands/parser.py` | No | Regex parser: "build stalker" → CommandPrimitive |
| `commands/interpreter.py` | No | Claude Haiku NLP: free text → command JSON. Prompt mentions SC2 but no code coupling |
| `commands/queue.py` | No | Priority queue with TTL eviction |
| `commands/recipes.py` | No (Tier 1) | TECH_RECIPES: generic structure, SC2-specific content |
| `commands/executor.py` | **Yes** (Tier 2) | `_UNIT_MAP`, `_STRUCTURE_MAP` hardcode `UnitTypeId`/`UpgradeId` |

### Infrastructure (all Tier 0)

| Module | SC2? | Notes |
|--------|------|-------|
| `api.py` | No | FastAPI REST + WebSocket; broadcasts dicts |
| `web_socket.py` | No | ConnectionManager; domain-agnostic |
| `logger.py` | No | Background thread JSONL writer |
| `config.py` | No | Settings from .env (SC2PATH is just a path string) |
| `console.py` | No | Format game state dict as one-line string |
| `batch_runner.py` | No | GameRecord aggregation |
| `replay_parser.py` | No | ReplayStats/TimelineEvent dataclasses (stub) |
| `claude_advisor.py` | No (Tier 1) | LLM client; prompt template mentions SC2 but no code dependency |
| `process_registry.py` | No | Process inventory + health tracking; no SC2 imports |

### SC2-coupled modules (Tier 2-3)

| Module | Tier | SC2 imports | Coupling detail |
|--------|------|-------------|-----------------|
| `bot.py` | 3 | `BotAI`, `UnitTypeId`, `Point2` | Subclasses BotAI; 300+ lines of Protoss build/micro logic. Cannot be decoupled — this IS the SC2 bot. |
| `connection.py` | 3 | `Race`, `Result`, `run_game`, `Bot`, `Computer` | `run_bot()` launches SC2 game via burnysc2 |
| `runner.py` | 3 (entry) | SC2 types at entry point | CLI dispatcher; delegates to generic training code |
| `observer.py` | 3 | `BotAI` (TYPE_CHECKING) | Accesses `bot.structures`, `bot.units`, `bot.state` directly |
| `macro_manager.py` | 2 | `UnitTypeId` | Production decisions keyed on Protoss unit IDs |
| `micro.py` | 2 | `UnitTypeId` | `TARGET_PRIORITY` dict: UnitTypeId → priority |
| `scouting.py` | 2 | `UnitTypeId` | `THREAT_WEIGHTS` dict: UnitTypeId → threat multiplier |
| `commands/executor.py` | 2 | `UnitTypeId`, `UpgradeId`, `Point2` | Maps command strings → SC2 type IDs |

---

## Key Interfaces for Domain Abstraction

> **At a glance:** Five interface boundaries matter. Swapping SC2 for another domain
> means: (1) new Env wrapper, (2) new feature spec, (3) new reward rules JSON,
> (4) new unit registry, (5) new observer. The learning pipeline stays untouched.

### 1. gymnasium.Env (environment.py)

The cleanest boundary. Trainer, SB3, and checkpoints interact only with:
- `reset() → (obs, info)`
- `step(action) → (obs, reward, done, truncated, info)`
- `observation_space: Box(0, 1, shape=(N,))`
- `action_space: Discrete(K)`

**To swap:** Implement a new `Env` subclass for the target domain. Everything above
(TrainingOrchestrator, PPO, curriculum, diagnostics) works unchanged.

### 2. Feature spec (features.py)

`FEATURE_DIM` and `_FEATURE_SPEC` define what the model sees. Currently 17 features,
some SC2-specific (gateway_count, robo_count, etc.).

**To swap:** Change `_FEATURE_SPEC` list and `FEATURE_DIM`. The encode/decode functions,
database schema (stores raw arrays by index), and PPO policy (just sees float vectors)
all adapt automatically.

**Caveat:** The database stores raw feature values by column name (`gateway_count`,
etc.). A domain swap would need a schema migration or a new DB file.

### 3. Reward rules (data/reward_rules.json)

The `RewardCalculator` is a generic rule engine. Rules reference field names from the
state dict.

**To swap:** Write a new `reward_rules.json` with conditions on the new domain's state
fields. The calculator code stays identical.

### 4. Unit registry (commands/executor.py, macro_manager.py, micro.py, scouting.py)

Four modules use hardcoded `UnitTypeId` maps. These are pure lookup tables.

**To swap:** Inject a unit registry at init time instead of importing SC2 enums.
Structure: `{string_name: UnitSpec(build_time, cost, prerequisites)}`. All four
modules become domain-agnostic with this one abstraction.

### 5. Observer (observer.py)

Direct `BotAI` API access: `bot.structures`, `bot.units`, `bot.minerals`, `bot.time`.

**To swap:** Define a `GameState` interface/protocol that the observer reads from.
The SC2 implementation wraps BotAI; another domain provides its own implementation.

---

## Abstraction Effort Estimate

| What to change | Files affected | Effort |
|----------------|---------------|--------|
| New gymnasium.Env wrapper | 1 (new file) | Medium — game-specific threading/async |
| New feature spec | 1 (features.py) | Low — change list + dim constant |
| New reward rules | 1 (data file) | Low — JSON only |
| Unit registry abstraction | 4 (executor, macro, micro, scouting) | Medium — extract maps to injected config |
| Observer abstraction | 2 (observer.py, bot.py) | Medium — define GameState protocol |
| New bot implementation | 1 (new file) | High — domain-specific game logic |
| Connection/runner updates | 2 | Low — swap game launch call |

**Learning pipeline changes needed: zero.** Trainer, neural engine, rewards engine,
database, checkpoints, imitation, hyperparams — all work as-is with any domain that
provides a gymnasium.Env.

### Key file locations

| File | Role in abstraction |
|------|-------------------|
| `src/alpha4gate/learning/environment.py` | The bridge — domain-specific internals, generic interface |
| `src/alpha4gate/learning/features.py` | Feature definition — change spec for new domain |
| `src/alpha4gate/learning/rewards.py` | Rule engine — domain-agnostic, swap rules JSON |
| `src/alpha4gate/observer.py` | State extraction — needs GameState protocol |
| `src/alpha4gate/commands/executor.py` | Unit mapping — needs registry injection |
| `src/alpha4gate/macro_manager.py` | Production — needs registry injection |
| `src/alpha4gate/micro.py` | Target priority — needs registry injection |
| `src/alpha4gate/scouting.py` | Threat assessment — needs registry injection |
| `data/reward_rules.json` | Domain-specific reward definitions |
| `data/hyperparams.json` | PPO config — may need tuning per domain |
