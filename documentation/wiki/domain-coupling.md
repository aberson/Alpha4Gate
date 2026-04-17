# Domain Coupling

What's SC2-specific vs what could work with any domain.

> **At a glance:** 51 Python modules in `bots/v0/`. The entire `learning/` pipeline (17 modules — trainer, daemon, evaluator, promotion, rollback, rewards, features, checkpoints, imitation, advisor_bridge, etc.) has **zero SC2 imports** and is domain-agnostic. SC2 coupling concentrates in 8 modules: `bot`, `connection`, `observer`, `macro_manager`, `micro`, `scouting`, `commands/executor`, plus the hybrid `learning/environment` bridge. The `/improve-bot-advised` loop reinforces this: it treats SC2 as an opaque task — code + config go in, win/loss + stats come out. See [improve-bot-advised-architecture.md](improve-bot-advised-architecture.md).

## Purpose & Design

This page maps the boundary between SC2-specific code and domain-agnostic infrastructure. The payoff: when modifying the learning pipeline, know that you're in domain-agnostic territory and shouldn't introduce SC2 imports; when swapping domains, know exactly what to change.

### The coupling spectrum

```
Tier 0: Pure Domain-Agnostic     Tier 1: SC2 at Data Level     Tier 2: SC2 Types     Tier 3: SC2 API
─────────────────────────────    ──────────────────────────     ──────────────────     ──────────────
learning/*  (17 modules)         decision_engine                macro_manager          bot
api, web_socket, logger          claude_advisor                 micro                  connection
config, console, batch_runner    build_orders                   scouting               observer
commands/{primitives,parser,     build_backlog                  commands/executor      runner (entry)
  interpreter,queue,recipes,                                                            learning/environment*
  dispatch_guard}                                                                       (*hybrid — public
army_coherence, fortification                                                            Env is Tier 0)
replay_parser, audit_log
error_log, process_registry
```

**Tier 0** — No SC2 imports, no SC2 concepts. Works with any domain today.
**Tier 1** — No SC2 imports, but operates on data shaped by SC2 (field names like `army_supply`, reward rules that mention scouting). Swapping domains means changing config/data, not code.
**Tier 2** — Imports `UnitTypeId`, `UpgradeId`, or `Point2` from sc2. Uses SC2 type enums in lookup tables. Could be decoupled with a unit registry abstraction.
**Tier 3** — Imports `BotAI`, `run_game`, `Race`, `Difficulty`. Directly calls the burnysc2 API. Would require a full rewrite or abstraction layer to swap.

### The SC2Env bridge (hybrid)

`learning/environment.py` is the **only learning module** that spans tiers 0 and 3:

- **Public interface** (Tier 0): `gymnasium.Env` with `reset()`, `step()`, `close()`, `Box` observation space, `Discrete` action space. Fully domain-agnostic.
- **Private internals** (Tier 3): `_sync_game()` calls `sc2.main.run_game()`, creates `_FullTrainingBot(BotAI)`, uses `Race.Protoss` and `Difficulty` enums.

Everything above this bridge — trainer, daemon, evaluator, promotion, rollback, SB3, checkpoints — works unchanged with any domain that provides a gymnasium.Env.

### Why this matters for `/improve-bot-advised`

The outer autonomous loop ([improve-bot-advised-architecture.md](improve-bot-advised-architecture.md)) treats SC2 as an opaque task:

```
 code + config  ──>  THE TASK (SC2 game)  ──>  win/loss + stats
                           ▲
                           │  The learning loop never touches SC2 internals.
                           │  It only reads outputs and modifies inputs.
```

This is the same abstraction the domain-coupling map reinforces. The loop's PLAY, THINK, FIX, TEST, COMMIT, TRAIN phases are all domain-agnostic — they just happen to operate on SC2 today.

---

## Module-by-Module Map

### Learning pipeline — all Tier 0 except the hybrid Env bridge

| Module | Tier | What it does |
|---|---|---|
| `learning/trainer.py` | 0 | TrainingOrchestrator — RL cycle loop |
| `learning/daemon.py` | 0 | TrainingDaemon — background triggers, watchdog |
| `learning/evaluator.py` | 0 | ModelEvaluator — deterministic inference-only eval + job system |
| `learning/promotion.py` | 0 | PromotionManager — checkpoint gate |
| `learning/rollback.py` | 0 | RollbackMonitor — regression detection |
| `learning/neural_engine.py` | 0 | NeuralDecisionEngine — PPO inference for gameplay |
| `learning/rewards.py` | 0 | RewardCalculator — JSON rule engine |
| `learning/reward_aggregator.py` | 0 | Per-rule trend aggregation |
| `learning/features.py` | 0 | encode/decode — normalized float vector |
| `learning/database.py` | 0 | TrainingDB — SQLite games + transitions |
| `learning/checkpoints.py` | 0 | Save/load/prune/promote manifest |
| `learning/imitation.py` | 0 | Behavior cloning via PyTorch |
| `learning/rules_policy.py` | 0 | Rule-based policy reference for KL targets |
| `learning/ppo_kl.py` | 0 | PPO-with-KL-to-rules variant |
| `learning/policy_probe.py` | 0 | Policy introspection utilities |
| `learning/advisor_bridge.py` | 0 | Thread-safe advisor queue for training |
| `learning/hyperparams.py` | 0 | Load PPO hyperparams from JSON |
| `learning/environment.py` | **Hybrid** | Gymnasium bridge — Tier 0 public / Tier 3 private |

### Decision & strategy

| Module | Tier | Notes |
|---|---|---|
| `decision_engine.py` | 1 | `StrategicState` enum is abstract (OPENING, EXPAND, ATTACK, etc.). Transitions use generic metrics. |
| `army_coherence.py` | 0 | Randomized parameter generation for army thresholds |
| `fortification.py` | 0 | Scaling formula: `enemy_advantage // divisor → defense_count` |
| `build_backlog.py` | 0 | Retry queue with expiry + affordability check |
| `build_orders.py` | 1 | Generic dataclasses; content (unit names) is SC2-specific when instantiated |

### Command system

| Module | Tier | Notes |
|---|---|---|
| `commands/primitives.py` | 0 | `CommandAction`, `CommandPrimitive`, `CommandMode` enums |
| `commands/parser.py` | 0 | Regex parser |
| `commands/interpreter.py` | 0 | Claude Haiku NLP — prompt mentions SC2, no code coupling |
| `commands/queue.py` | 0 | Priority queue with TTL eviction |
| `commands/recipes.py` | 1 | TECH_RECIPES structure is generic, content is SC2-specific |
| `commands/dispatch_guard.py` | 0 | Retry-storm guard (#retry-storm fix 2026-04-14) |
| `commands/executor.py` | 2 | `_UNIT_MAP`, `_STRUCTURE_MAP` hardcode `UnitTypeId`/`UpgradeId` |

### Infrastructure (all Tier 0)

| Module | Notes |
|---|---|
| `api.py` | FastAPI REST + WebSocket; broadcasts dicts |
| `web_socket.py` | ConnectionManager |
| `logger.py` | Background thread JSONL writer |
| `config.py` | Settings from .env |
| `console.py` | Format game state dict as one-line string |
| `batch_runner.py` | GameRecord aggregation |
| `replay_parser.py` | ReplayStats/TimelineEvent dataclasses |
| `claude_advisor.py` | LLM client; prompt template mentions SC2, no code dependency |
| `process_registry.py` | Process inventory + state-file introspection |
| `error_log.py` | 50-entry error ring buffer |
| `audit_log.py` | Decision audit log persistence |

### SC2-coupled modules (Tier 2–3)

| Module | Tier | SC2 imports | Coupling detail |
|---|---|---|---|
| `bot.py` | 3 | `BotAI`, `UnitTypeId`, `Point2` | Subclasses BotAI. Cannot be decoupled — this IS the SC2 bot. |
| `connection.py` | 3 | `Race`, `Result`, `run_game`, `Bot`, `Computer` | Launches SC2 game via burnysc2 |
| `runner.py` | 3 (entry) | SC2 types at entry point | CLI dispatcher; delegates to generic training code |
| `observer.py` | 3 | `BotAI` (TYPE_CHECKING) | Accesses `bot.structures`, `bot.units`, `bot.state` directly |
| `macro_manager.py` | 2 | `UnitTypeId` | Production decisions keyed on Protoss unit IDs |
| `micro.py` | 2 | `UnitTypeId` | `TARGET_PRIORITY` dict |
| `scouting.py` | 2 | `UnitTypeId` | `THREAT_WEIGHTS` dict |
| `commands/executor.py` | 2 | `UnitTypeId`, `UpgradeId`, `Point2` | Maps command strings → SC2 type IDs |

---

## Interfaces for Domain Abstraction

Five boundaries matter. Swapping SC2 for another domain means: (1) new Env wrapper, (2) new feature spec, (3) new reward rules JSON, (4) new unit registry, (5) new observer. The learning pipeline stays untouched.

### 1. gymnasium.Env (environment.py)

The cleanest boundary. Trainer, SB3, evaluator, promotion gate, rollback monitor all interact only with:
- `reset() → (obs, info)`
- `step(action) → (obs, reward, done, truncated, info)`
- `observation_space: Box(0, 1, shape=(N,))`
- `action_space: Discrete(K)`

### 2. Feature spec (features.py)

`FEATURE_DIM=24` (= 17 base + 7 advisor) and `_FEATURE_SPEC` define what the model sees. Features 10–16 are SC2/Protoss-specific.

**To swap:** Change `_FEATURE_SPEC` list and the two dim constants. The encode/decode functions and PPO policy adapt automatically; the database would need a schema migration.

### 3. Reward rules (bots/v0/data/reward_rules.json)

The `RewardCalculator` is a generic rule engine. 63 rules currently active. Rules reference field names from the state dict.

**To swap:** Write a new `reward_rules.json` with conditions on the new domain's state fields. Calculator code stays identical.

### 4. Unit registry (4 modules)

`commands/executor.py`, `macro_manager.py`, `micro.py`, `scouting.py` use hardcoded `UnitTypeId` maps.

**To swap:** Inject a unit registry at init time. All four modules become domain-agnostic with this one abstraction.

### 5. Observer (observer.py)

Direct `BotAI` API access.

**To swap:** Define a `GameState` interface that the observer reads from.

---

## Abstraction Effort Estimate

| What to change | Files affected | Effort |
|---|---|---|
| New gymnasium.Env wrapper | 1 (new file) | Medium — game-specific threading/async |
| New feature spec | 1 (features.py) | Low |
| New reward rules | 1 (data file) | Low |
| Unit registry abstraction | 4 (executor, macro, micro, scouting) | Medium |
| Observer abstraction | 2 (observer.py, bot.py) | Medium |
| New bot implementation | 1 (new file) | High — domain-specific game logic |
| Connection/runner updates | 2 | Low |

**Learning pipeline changes needed: zero.** Trainer, daemon, evaluator, promotion, rollback, neural engine, rewards, database, checkpoints, imitation, hyperparams, advisor_bridge — all 17 learning modules work as-is with any domain providing a gymnasium.Env.

### Key file locations

| File | Role in abstraction |
|---|---|
| `bots/v0/learning/environment.py` | The bridge — domain-specific internals, generic interface |
| `bots/v0/learning/features.py` | Feature definition — change spec for new domain |
| `bots/v0/learning/rewards.py` | Rule engine — domain-agnostic, swap rules JSON |
| `bots/v0/observer.py` | State extraction — needs GameState protocol |
| `bots/v0/commands/executor.py` | Unit mapping — needs registry injection |
| `bots/v0/macro_manager.py` | Production — needs registry injection |
| `bots/v0/micro.py` | Target priority — needs registry injection |
| `bots/v0/scouting.py` | Threat assessment — needs registry injection |
| `bots/v0/data/reward_rules.json` | Domain-specific reward definitions |
| `bots/v0/data/hyperparams.json` | PPO config — may need tuning per domain |
