# Alpha4Gate Wiki

## System Diagram — The Autonomous Improvement Loop

```
                         ┌─────────────────────────────────────────────┐
                         │           TRANSPARENCY DASHBOARD            │
                         │  Live game state | Training metrics | Logs  │
                         │  Improvement timeline | Model comparison    │
                         └──────────┬──────────────────┬───────────────┘
                                    │ observes          │ observes
           ┌────────────────────────┼──────────────────────────────────────────┐
           │                        │                   │                      │
           ▼                        ▼                   ▼                      ▼
   ┌──────────────┐       ┌──────────────┐     ┌──────────────┐      ┌──────────────┐
   │     PLAY     │──────>│   EVALUATE   │────>│    TRAIN     │─────>│   PROMOTE    │
   │              │       │              │     │              │      │              │
   │ Run games    │       │ Win rates    │     │ PPO cycles   │      │ If better:   │
   │ Log state    │       │ Reward rules │     │ Curriculum   │      │  new "best"  │
   │ Stream WS    │       │ Stats        │     │ Checkpoints  │      │ If worse:    │
   │              │       │              │     │              │      │  rollback    │
   └──────────────┘       └──────────────┘     └──────────────┘      └──────┬───────┘
           ▲                                                                │
           │                                                                │
           └────────────────────────────────────────────────────────────────┘
                                    loops continuously

   ┌──────────────────────────────────────────────────────────────────────────┐
   │                        DOMAIN LAYER (pluggable)                         │
   │                                                                         │
   │  SC2 Bot: Strategy → Commands → Tactics → Coherence → Micro            │
   │  (Could be any agent: game bot, trading bot, robotics controller...)    │
   └──────────────────────────────────────────────────────────────────────────┘
```

**Today:** All four stages exist and run autonomously. PLAY runs games and streams state,
EVALUATE compares checkpoints via `ModelEvaluator`, TRAIN runs PPO cycles via the
`TrainingDaemon` background thread, PROMOTE/ROLLBACK gate model changes via
`PromotionManager` + `RollbackMonitor`. The dashboard observes all four through the
Live, Stats, Decisions, Training, Loop, Improvements, and Alerts tabs (Phases 3 + 4).

**Validation gap:** The autonomous loop has been built and unit-tested but never observed
running unattended for hours against a real SC2 client. Phase 4.5 (the "First Real Soak
Test") closes that gap before Phase 5 (domain abstraction) begins.

---

## Start Here

| Page | Best for |
|------|----------|
| [FAQ](faq.md) | First-time visitors — answers "what is this?", "what's happening?", "what's next?" |
| [Promotion History](promotions.md) | Model promotion log — when, why, and what changed |

---

## Wiki Pages

### Core Systems (autonomous loop)

| Page | Description | Status |
|------|-------------|--------|
| [Evaluation Pipeline](evaluation-pipeline.md) | How the bot knows if it's improving — metrics, win rates, reward analysis, `ModelEvaluator` | Yes |
| [Training Pipeline](training-pipeline.md) | PPO training, imitation learning, curriculum auto-advancement, checkpoints, `TrainingDaemon` background thread | Yes (autonomous) |
| [Monitoring & Observability](monitoring.md) | WebSocket streams, per-game JSONL reward logs, what's persisted vs ephemeral, `reward_aggregator` | Yes |
| [Promotion History](promotions.md) | Model promotion log — `PromotionManager` + `RollbackMonitor` decisions, auto-updated table | Yes |

### Domain Layer (SC2 bot)

| Page | Description |
|------|-------------|
| [Architecture Overview](architecture.md) | Six-layer architecture, module map, how layers interact |
| [Decision Engine](decision-engine.md) | Strategic state machine — states, transitions, triggers |
| [Command System](command-system.md) | Three command modes, parser, interpreter, executor, queue |
| [Army & Combat](army-combat.md) | Coherence, staging, engagement, retreat, micro |
| [Economy & Production](economy.md) | Macro manager, build orders, expansion, production |
| [Claude Advisor](claude-advisor.md) | Async subprocess integration, rate limiting, prompt design |

### Infrastructure

| Page | Description |
|------|-------------|
| [Frontend Dashboard](frontend.md) | React components, WebSocket protocol, API endpoints |
| [Domain Coupling](domain-coupling.md) | What's SC2-specific vs domain-agnostic, abstraction boundaries |
| [Testing](testing.md) | Test structure, SC2 integration tests, coverage |

---

## How to Use This Wiki

**For Claude sessions:** Start with this index. Read the system diagram to understand the
overall architecture. Then read the specific page(s) relevant to your task. Each page
follows this structure:

1. **Purpose & Design** — stable overview, changes rarely
2. **Key Interfaces** — public API, data flow, entry points
3. **Implementation Notes** — internal details, key functions, data structures
   (marked "verify against code" since these can drift)

**For the plan:** See [always-up-plan.md](../plans/always-up-plan.md) for the roadmap
toward full autonomous operation.
