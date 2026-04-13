# Architecture Overview

How the layers fit together.

> **At a glance:** Six layers (Claude Advisor → Neural Engine → Strategy → Commands →
> Tactics → Coherence → Micro) orchestrated by `bot.py.on_step()` in a 14-step pipeline.
> Three command modes. Neural model can override rule-based strategy. Game state flows
> up (observer → logger → WebSocket → dashboard) while decisions flow down (strategy →
> commands → macro → micro → SC2 API). See [domain-coupling.md](domain-coupling.md) for
> which layers are SC2-specific vs generic.

## Purpose & Design

Alpha4Gate is a layered SC2 Protoss bot where each layer has a clear responsibility and
interface. Layers communicate through data structures (GameSnapshot, StrategicState,
MacroDecision, MicroCommand) rather than direct method calls between distant modules.

### Layer diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     CLAUDE ADVISOR (async)                       │
│  Async subprocess, rate-limited (1 per 30 game-seconds)         │
│  Fires advice + optional commands into the command queue         │
└────────────────────────────┬────────────────────────────────────┘
                             │ optional override
┌────────────────────────────▼────────────────────────────────────┐
│                     NEURAL ENGINE (optional)                     │
│  PPO policy network, SB3 inference, deterministic prediction     │
│  Modes: RULES (skip), NEURAL (replace), HYBRID (override DEFEND)│
└────────────────────────────┬────────────────────────────────────┘
                             │ StrategicState
┌────────────────────────────▼────────────────────────────────────┐
│                     DECISION ENGINE (strategy)                   │
│  State machine: OPENING → EXPAND → ATTACK → DEFEND → FORTIFY    │
│  → LATE_GAME. Priority: command override > defend > fortify >    │
│  build order > late game > attack > expand                       │
└────────────────────────────┬────────────────────────────────────┘
                             │ StrategicState + commands
┌────────────────────────────▼────────────────────────────────────┐
│                     COMMAND SYSTEM                                │
│  Text → parse → queue → execute. Three modes: AI-Assisted,       │
│  Human Only, Hybrid (with AI lockout). Tech recipes expand       │
│  high-level commands into prerequisite build chains.              │
└────────────────────────────┬────────────────────────────────────┘
                             │ MacroDecision list
┌────────────────────────────▼────────────────────────────────────┐
│                     TACTICS (macro)                               │
│  MacroManager: workers, supply, production, gas, expansion.      │
│  FortificationManager: cannons + shield batteries when FORTIFY.  │
│  BuildBacklog: retry failed builds with TTL expiry.              │
└────────────────────────────┬────────────────────────────────────┘
                             │ unit groups
┌────────────────────────────▼────────────────────────────────────┐
│                     COHERENCE                                    │
│  ArmyCoherenceManager: staging point, critical mass gate,        │
│  attack/retreat thresholds, hysteresis. Randomized parameters    │
│  per game. STAGING_TIMEOUT forces attack after 60s wait.         │
└────────────────────────────┬────────────────────────────────────┘
                             │ MicroCommand list
┌────────────────────────────▼────────────────────────────────────┐
│                     MICRO                                        │
│  MicroController: target priority, kiting, focus fire.           │
│  Per-unit commands: attack, move, ability.                        │
└─────────────────────────────────────────────────────────────────┘
```

### The on_step() pipeline

`bot.py.on_step()` is called every game loop iteration by burnysc2. It runs a 14-step
pipeline:

| # | Step | Layer | Frequency |
|---|------|-------|-----------|
| 1 | Build GameSnapshot | — | Every step |
| 2 | DecisionEngine.evaluate() | Strategy | Every step |
| 3 | NeuralDecisionEngine.predict() (optional override) | Neural | Every step |
| 4 | Drain command queue + execute | Commands | Every step |
| 5 | Collect Claude async response | Advisor | Every step (usually no-op) |
| 6 | Fire new Claude request (if rate limit allows) | Advisor | ~1 per 30 game-seconds |
| 7 | Record training transition | Learning | Every 22 steps |
| 8 | Execute build order (OPENING only) | Tactics | During OPENING |
| 9 | MacroManager.evaluate() (post-OPENING) | Tactics | After OPENING |
| 10 | FortificationManager.evaluate() (FORTIFY only) | Tactics | During FORTIFY |
| 11 | BuildBacklog.tick() — retry failed builds | Tactics | Every step |
| 12 | Train army from idle production | Tactics | Every step |
| 13 | Scouting probe routine | Tactics | When should_scout() |
| 14 | MicroController / rally idle army | Micro/Coherence | Every step |

After step 14, the observer captures a snapshot every 11 steps and feeds it to the
logger → WebSocket → dashboard pipeline (see [monitoring.md](monitoring.md)).

### Data structures flowing between layers

| Structure | From | To | Purpose |
|-----------|------|----|---------|
| `GameSnapshot` | bot.py | DecisionEngine, NeuralEngine, Features | Current game state (17 fields) |
| `StrategicState` | DecisionEngine | bot.py, MacroManager, MicroController | What the bot should be doing |
| `CommandPrimitive` | Parser/Interpreter | Queue → Executor | A single actionable command |
| `MacroDecision` | MacroManager, FortificationManager | bot.py | A build/train/expand decision |
| `MicroCommand` | MicroController | bot.py | Per-unit combat command |
| `AdvisorResponse` | ClaudeAdvisor | bot.py → command queue | Claude's strategic suggestion |
| `DecisionEntry` | DecisionEngine | decision_audit.json, /ws/decisions | Logged state transition |

---

## Key Interfaces

> **At a glance:** Each layer exposes one or two key methods. Strategy returns a state
> enum. Macro/fortification return decision lists. Micro returns command lists. Commands
> flow through a parse → queue → execute pipeline.

### Strategy: DecisionEngine.evaluate(snapshot, game_step) → StrategicState

Priority order (highest wins):
1. **Command override** — human/AI forced state (expires after duration)
2. **DEFEND** — enemy army near base
3. **FORTIFY** — recently retreated AND enemy supply > own * fortify_trigger_ratio
4. **OPENING** — build order not yet complete
5. **LATE_GAME** — 3+ bases AND 480+ seconds
6. **ATTACK** — army_supply >= 20
7. **EXPAND** — default

### Coherence: ArmyCoherenceManager

All parameters randomized per game within ranges:

| Parameter | Range | Purpose |
|-----------|-------|---------|
| attack_supply_ratio | 1.0 - 1.5 | Army ratio needed to attack |
| attack_supply_floor | 15 - 25 | Minimum supply before any attack |
| retreat_supply_ratio | 0.4 - 0.7 | Ratio below which retreat triggers |
| coherence_pct | 0.60 - 0.80 | Fraction of army that must be grouped |
| coherence_distance | 6 - 10 | Max distance from centroid to count as grouped |
| staging_distance | 12 - 20 | Distance from enemy to stage army |
| fortify_trigger_ratio | 1.3 - 2.0 | Enemy advantage ratio to trigger FORTIFY |
| max_defenses | 3 - 5 | Max cannons + batteries |

Hysteresis: after retreat, attack requires ratio * 1.2 (prevents oscillation).
Staging timeout: 60 seconds max wait before forcing attack.

### Commands: parse → queue → execute

```
"build stalkers" ──> StructuredParser.parse()
                         │
              ┌──────────┴──────────┐
              │ success              │ failure
              ▼                      ▼
     CommandPrimitive      CommandInterpreter.interpret()
              │             (Claude Haiku, 5s timeout)
              │                      │
              └──────────┬───────────┘
                         ▼
                  CommandQueue.push()    (max 10, priority eviction)
                         │
                  CommandQueue.drain()   (filter expired TTL)
                         │
                  CommandExecutor.execute()
                    ├─ BUILD → structure or train unit
                    ├─ TECH → expand_tech() → recursive prerequisites
                    ├─ EXPAND → build nexus
                    ├─ ATTACK/DEFEND → set decision override
                    ├─ SCOUT → force scout target
                    ├─ UPGRADE → research at forge/cybernetics
                    └─ RALLY → update staging point
```

---

## Implementation Notes

> Verify against code before relying on exact details.

### Key constants in bot.py

| Constant | Value | Purpose |
|----------|-------|---------|
| `PUSH_MAIN_SUPPLY` | 160 | Attack enemy main above this supply |
| `_STAGING_RECALC_SECONDS` | 30.0 | Recalculate staging point interval |
| `_GATEWAY_ARMY` | [STALKER, ZEALOT] | Priority order for gateway production |
| `_ROBO_ARMY` | [IMMORTAL, OBSERVER] | Priority order for robo production |

### Module map

```
src/alpha4gate/
├── bot.py                 # Orchestrator — on_step() pipeline (869 lines)
├── decision_engine.py     # StrategicState state machine + GameSnapshot
├── macro_manager.py       # Economy: workers, supply, production, expansion
├── micro.py               # Combat: target priority, kiting, focus fire
├── army_coherence.py      # Grouping: staging, critical mass, retreat
├── fortification.py       # Static defense: cannons, batteries, scaling
├── scouting.py            # Threat assessment + scout probe management
├── claude_advisor.py      # Async Claude subprocess + rate limiting
├── observer.py            # Extract game state dict for logging/WS
├── logger.py              # Background JSONL writer thread
├── web_socket.py          # WebSocket connection manager + queues
├── api.py                 # FastAPI REST + WS endpoints
├── config.py              # .env settings loader
├── console.py             # One-line status formatter
├── connection.py          # SC2 game launcher (run_game)
├── runner.py              # CLI entry point
├── batch_runner.py        # Multi-game stats aggregation
├── build_orders.py        # BuildOrder/BuildStep/BuildSequencer
├── build_backlog.py       # Retry queue for failed builds
├── replay_parser.py       # Replay parsing (stub)
├── process_registry.py    # Process inventory + health tracking
├── commands/
│   ├── primitives.py      # CommandPrimitive, CommandAction, CommandMode
│   ├── parser.py          # Regex text parser
│   ├── interpreter.py     # Claude Haiku NLP fallback
│   ├── executor.py        # Execute commands via SC2 API
│   ├── queue.py           # Priority queue with TTL
│   └── recipes.py         # Tech prerequisite chains
└── learning/
    ├── trainer.py          # TrainingOrchestrator — RL loop
    ├── environment.py      # SC2Env gymnasium wrapper
    ├── neural_engine.py    # PPO inference for gameplay
    ├── features.py         # GameSnapshot → float vector
    ├── rewards.py          # JSON-driven reward shaping
    ├── database.py         # SQLite transitions + games
    ├── checkpoints.py      # Model save/load/prune
    ├── imitation.py        # Behavior cloning
    └── hyperparams.py      # PPO config loader
```

### Key file locations

| File | Purpose |
|------|---------|
| `src/alpha4gate/bot.py` | Main orchestrator — start here to understand game flow |
| `src/alpha4gate/decision_engine.py` | Strategic state machine — state transitions |
| `src/alpha4gate/commands/executor.py` | Where commands become SC2 API calls |
| `src/alpha4gate/army_coherence.py` | Attack/retreat decisions with randomized params |
| `src/alpha4gate/macro_manager.py` | Economy automation |
| `src/alpha4gate/micro.py` | Per-unit combat commands |
