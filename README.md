# Alpha4Gate

A StarCraft II Protoss bot combining rule-based decision-making, a PPO neural policy network, and Claude AI as strategic advisor. Uses a layered architecture (strategy, tactics, coherence, micro) with three command execution modes (AI-Assisted, Human Only, Hybrid). Includes a React dashboard for live game visualization, training metrics, build order editing, and strategic command input.

## Vision

- **AI-vs-AI competition** — build a bot that wins consistently at increasing difficulty levels and eventually competes against other SC2 bots
- **Transparent model introspection** — live dashboard showing strategic state, decision reasoning, neural policy outputs, and Claude advisor suggestions
- **Evaluation metrics** — structured reward shaping, win-rate tracking, training diagnostics, and cross-game statistics
- **Autonomous self-improvement** — train-play-evaluate loop that runs 24/7, getting stronger with each cycle

**Phase 4 complete** — issues #50–#59 closed. Transparency dashboard shipped: 9-tab SPA with Loop, Improvements, and Alerts tabs; live daemon control + manual evaluate/promote/rollback; per-rule reward trend visualization (Recharts); client-side alert engine with 6 rules and localStorage-backed ack/dismiss. New backend module `learning/reward_aggregator.py`, new endpoint `GET /api/training/reward-trends`, new field `reward_logs_size_bytes` on `/api/training/status`. Frontend test infrastructure (vitest + jsdom + @testing-library/react) added. 682 Python tests + 105 frontend tests passing, 0 type errors, 0 lint violations. (Phase 3 — Autonomous Training Loop, issues #43–#49 — closed previously.)

**Current capability:** Wins reliably at difficulty 1-3 (Easy through Medium AI). Struggles at 4-5 (Hard).

## Stack

| Layer | Tool | Why |
|---|---|---|
| Language | Python 3.12 | SC2 libraries are Python-native |
| SC2 interface | burnysc2 v7.1.3 | Async BotAI, actively maintained |
| AI advisor | Claude CLI (OAuth or API key) | Strategic advice mid-game, async subprocess |
| Build orders | Spawning Tool API | Community build order database |
| Backend | FastAPI | WebSocket + REST for dashboard |
| Frontend | React + TypeScript + Vite | Live dashboard with game state streaming |
| Deep learning | PyTorch + Stable Baselines 3 | PPO policy network for strategic decisions |
| Training data | SQLite | Structured (s,a,r,s') transition storage |
| Charts | Recharts 3.8 | Per-rule reward trend visualization |
| Testing (Python) | pytest | 682 unit tests, SC2 integration markers |
| Testing (Frontend) | vitest + jsdom + @testing-library/react | 105 component / hook / lib tests |
| Linting | ruff + mypy | Strict type checking, consistent style |

## Dashboard tabs

| Tab | Purpose |
|---|---|
| Live | Real-time game state stream (WebSocket) |
| Stats | Cross-game win rates and aggregate stats |
| Build Orders | Spawning Tool build order browser + editor |
| Replays | Match replay browser and analysis |
| Decisions | Live decision log with rule firings and Claude advice |
| Training | Model comparison + improvement timeline (Phase 2) |
| Loop | Daemon state, trigger evaluation, full daemon control panel (Phase 4) |
| Improvements | Recent promotions/rollbacks + per-rule reward trend chart (Phase 4) |
| Alerts | Severity-filtered alert list with ack/dismiss + unread badge in nav (Phase 4) |

In-app `AlertToast` lives at the App root and shows new alerts as they fire, regardless of which tab is active.

## Prerequisites

- Windows 11
- StarCraft II installed at `C:\Program Files (x86)\StarCraft II\`
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Node.js 18+ (for React frontend)
- SC2 maps from [Blizzard CDN](https://blzdistsc2-a.akamaihd.net/MapPacks/Melee.zip) (password: `iagreetotheeula`) — do NOT use GitHub map files (Git LFS pointers)

## Setup

1. Install Python dependencies:
   ```bash
   cd Alpha4Gate
   uv sync
   ```

2. Create `.env` from template and fill in your keys:
   ```bash
   cp .env.example .env
   # Edit .env: set SC2PATH, SPAWNING_TOOL_API_KEY
   # Claude advisor auth: claude CLI must be on PATH with OAuth token or API key
   ```

3. Install frontend dependencies:
   ```bash
   cd frontend && npm install && cd ..
   ```

## Usage

```bash
# Run a single game vs Easy AI
uv run python -m alpha4gate.runner --map Simple64

# Options
--difficulty 3     # AI difficulty 1-10 (default: Easy)
--realtime         # Watch in realtime
--batch 5          # Run 5 games, aggregate stats
--build-order 4gate  # Select build order (default: 4gate)
--serve            # Start dashboard API server only
--no-claude        # Disable Claude advisor
```

### Dashboard

```bash
# Terminal 1: backend
uv run python -m alpha4gate.runner --serve

# Terminal 2: frontend dev server
cd frontend && npm start
# Opens http://localhost:3000 proxying to :8765
```

## Architecture

```
Claude Advisor (async, non-blocking subprocess)
        |
Neural Engine — PPO policy network (optional, hybrid or pure RL mode)
        |
Strategy Layer — state machine: opening -> expand -> attack -> defend -> fortify -> late_game
        |
Command System — parser -> interpreter -> executor (AI-Assisted / Human Only / Hybrid)
        |
Tactics Layer — macro manager, scouting, production balance
        |
Coherence — army staging, grouping, engagement/retreat decisions
        |
Micro Layer — army movement, kiting, focus fire, ability usage
```

The bot follows a build order during the opening, then transitions to dynamic decision-making. Claude provides optional strategic advice via async subprocess calls (rate-limited to 1 per 30 game-seconds). The neural engine can override or supplement rule-based strategy decisions using a PPO-trained policy.

## Testing

```bash
uv run pytest              # 535 unit tests (no SC2 needed)
uv run pytest -m sc2       # SC2 integration tests (SC2 must be running)
uv run ruff check .        # Lint
uv run mypy src            # Type check
cd frontend && npx tsc --noEmit  # TypeScript check
```

## Project Structure

```
Alpha4Gate/
├── src/alpha4gate/          # 38 Python modules
│   ├── commands/            # Strategic command system (parser, interpreter, executor, queue)
│   ├── bot.py               # Main BotAI subclass, game loop orchestration
│   ├── decision_engine.py   # Strategic state machine (6 states)
│   ├── neural_engine.py     # PPO policy integration with SB3
│   ├── army_coherence.py    # Staging, grouping, engagement/retreat
│   ├── fortification.py     # Cannons + batteries + FORTIFY state
│   ├── macro_manager.py     # Economy, production, expansion
│   ├── micro.py             # Kiting, focus fire, abilities
│   ├── claude_advisor.py    # Async Claude CLI subprocess
│   ├── trainer.py           # PPO training orchestrator
│   ├── features.py          # Game state -> tensor encoding
│   ├── rewards.py           # Configurable reward shaping
│   ├── imitation.py         # Imitation pre-training from replays
│   ├── api.py               # FastAPI server (REST + WebSocket)
│   └── ...                  # scouting, config, logger, runner, etc.
├── tests/                   # 35 test files, 535 tests
├── frontend/                # React + TypeScript dashboard (Vite)
├── scripts/                 # Live test, training analysis, model evaluation
├── documentation/wiki/      # Project wiki (start with index.md)
├── documentation/plans/     # Active plans (always-up-plan.md)
├── documentation/archived/  # Completed plans (Phase 1, Phase 2, improvement cycles)
├── data/                    # Cross-game stats, training DB, checkpoints (gitignored)
├── logs/                    # JSONL game logs (gitignored)
└── replays/                 # SC2 replays (gitignored)
```

## Key Design Decisions

- **Layered separation**: Strategy, tactics, coherence, and micro are independent modules with clear interfaces, testable in isolation
- **Async Claude advisor**: Fire-and-forget subprocess call, bot never blocks waiting for AI advice
- **Deep learning pipeline**: PPO policy via Stable Baselines 3, SQLite transition storage, imitation learning bootstrap, gymnasium environment wrapping SC2 state. Hybrid mode combines neural + rule-based decisions
- **Strategic command system**: Three execution modes (AI-Assisted, Human Only, Hybrid), natural language parser with recipe library, command queue with priority and TTL
- **Defensive fortification**: FORTIFY strategic state triggered by threat assessment, BuildBacklog for deferred production retry, FortificationManager places cannons and shield batteries at expansion choke points
- **Army coherence**: Staging areas outside enemy range, group-based engagement with critical mass gate, retreat decisions based on relative army strength
- **JSONL logging**: Extended schema with `strategic_state`, `decision_queue`, and `claude_advice` for decision debugging
- **Cross-game persistence**: JSON files in `data/` and SQLite for training transitions
- **Build order system**: Named build orders importable from Spawning Tool API, sequenced by supply thresholds
