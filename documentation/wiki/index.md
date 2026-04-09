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

**Today:** The PLAY and TRAIN boxes work (manually triggered). EVALUATE is partial
(scripts, not continuous). PROMOTE and the loop itself don't exist yet. The dashboard
observes PLAY but not TRAIN or EVALUATE in depth.

**Goal:** All four stages run autonomously. The dashboard observes everything.

---

## Start Here

| Page | Best for |
|------|----------|
| [FAQ](faq.md) | First-time visitors — answers "what is this?", "what's happening?", "what's next?" |
| [Promotion History](promotions.md) | Model promotion log — when, why, and what changed |

---

## Wiki Pages

### Core Systems (autonomous loop)

| Page | Description | Exists Today? |
|------|-------------|---------------|
| [Evaluation Pipeline](evaluation-pipeline.md) | How the bot knows if it's improving — metrics, win rates, reward analysis | Partial |
| [Training Pipeline](training-pipeline.md) | PPO training, imitation learning, curriculum, checkpoints | Yes (manual) |
| [Monitoring & Observability](monitoring.md) | WebSocket streams, JSONL logs, what's persisted vs ephemeral | Partial |
| [Promotion History](promotions.md) | Model promotion log — when checkpoints became "best" and why | Manual |
| [Autonomous Loop](autonomous-loop.md) | Scheduler, triggers, model promotion — the "always up" infrastructure | Not yet |

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
