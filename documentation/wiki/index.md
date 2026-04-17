# Alpha4Gate Wiki

An autonomous AI agent that teaches itself to get better at a task. The task happens to be StarCraft II, but the loop is general.

## Two Loops, Nested

Alpha4Gate runs two self-improvement loops at different timescales. They're both
here because they operate on different things.

```
┌───────────────────────────────────────────────────────────────────────────────┐
│  OUTER LOOP — /improve-bot-advised (hours)                                    │
│  Improves the bot's CODE and CONFIG                                            │
│                                                                                │
│    PLAY ──> THINK ──> FIX ──> TEST ──> COMMIT ──> TRAIN ──> loop              │
│     ▲                 (edit config                  │                          │
│     │                  or write code)               │                          │
│     │                                               ▼                          │
│     │     ┌──────────────────────────────────────────────────┐                │
│     │     │  INNER LOOP — TrainingDaemon (minutes)           │                │
│     │     │  Improves the NEURAL POLICY                      │                │
│     │     │                                                  │                │
│     │     │  PLAY ──> EVALUATE ──> TRAIN ──> PROMOTE ──> loop│                │
│     │     │                                    │             │                │
│     │     │  (rollback if worse ◄──────────────┘)           │                │
│     │     └──────────────────────────────────────────────────┘                │
│     │                                                                          │
│     └──────────────── run new games to see if the fix held ─────────────────  │
│                                                                                │
└───────────────────────────────────────────────────────────────────────────────┘

                              observed by
                                   │
                                   ▼
                   ┌───────────────────────────────┐
                   │   Transparency Dashboard      │
                   │                               │
                   │  10 tabs: Live, Stats,        │
                   │  Decisions, Training, Loop,   │
                   │  Advisor, Improvements,       │
                   │  Processes, Alerts, Ladder    │
                   └───────────────────────────────┘
```

- **Outer loop** ([improve-bot-advised-architecture.md](improve-bot-advised-architecture.md)) — Claude observes games, diagnoses weaknesses, writes code or config changes, validates them, commits. Hours-long autonomous sessions.
- **Inner loop** ([training-pipeline.md](training-pipeline.md), [promotions.md](promotions.md)) — `TrainingDaemon` background thread runs PPO cycles, `PromotionManager` gates new checkpoints, `RollbackMonitor` reverts regressions.
- **THE TASK** — an SC2 1v1 game. The loops treat it as a black box: code + config in, win/loss + stats out. See [domain-coupling.md](domain-coupling.md) for the abstraction boundary.

---

## Start Here

| Page | Best for |
|------|----------|
| [FAQ](faq.md) | First-time visitors — "what is this?", "what's happening?", "what's next?" |
| [improve-bot-advised-architecture](improve-bot-advised-architecture.md) | How the outer loop teaches itself |
| [Monitoring & Observability](monitoring.md) | How to watch the loop run |

---

## Wiki Pages

### The Autonomous Loop

| Page | Description |
|------|-------------|
| [improve-bot-advised architecture](improve-bot-advised-architecture.md) | Outer loop — PLAY / THINK / FIX / TEST / COMMIT / TRAIN, with SC2 as an opaque task |
| [Monitoring & Observability](monitoring.md) | What's visible at each phase, the state-file single source of truth, 7 alert rules |
| [Training Pipeline](training-pipeline.md) | Inner loop — PPO, imitation pre-training, LSTM, KL-to-rules, curriculum, `TrainingDaemon` |
| [Evaluation Pipeline](evaluation-pipeline.md) | Win rates, reward rules (63 active), `ModelEvaluator` (deterministic inference-only eval) |
| [Promotion History](promotions.md) | `PromotionManager` + `RollbackMonitor` — live gate, auto-updated log |

### The Task (SC2 bot internals)

| Page | Description |
|------|-------------|
| [Architecture Overview](architecture.md) | Six-layer bot architecture, on_step() pipeline, inter-layer data flow |
| [Decision Engine](decision-engine.md) | Strategic state machine — states, transitions, triggers |
| [Command System](command-system.md) | Three command modes, parser, interpreter, executor, queue |
| [Army & Combat](army-combat.md) | Coherence, staging, engagement, retreat, micro |
| [Economy & Production](economy.md) | Macro manager, build orders, expansion, production |
| [Claude Advisor](claude-advisor.md) | Live in-game advisor — async subprocess, rate limiting |

### Infrastructure

| Page | Description |
|------|-------------|
| [Frontend Dashboard](frontend.md) | 10 tabs, React components, WebSocket protocol, poll cadences |
| [Domain Coupling](domain-coupling.md) | What's SC2-specific vs domain-agnostic |
| [Testing](testing.md) | 1020 unit tests, SC2 integration tests, coverage map |

---

## How to Use This Wiki

**For Claude sessions:** Start with this index, then read the page for the system you're touching. Each page follows the structure:

1. **Purpose & Design** — stable overview
2. **Key Interfaces** — public API, data flow
3. **Implementation Notes** — internal details (marked "verify against code" — these can drift)

**For the active plan:** See [alpha4gate-master-plan.md](../plans/alpha4gate-master-plan.md) for the current roadmap. The always-up loop (Phases 1–4.5) is now the baseline.
