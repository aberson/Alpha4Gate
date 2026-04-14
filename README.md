# Alpha4Gate

A StarCraft II Protoss bot combining rule-based decision-making, a PPO neural policy network, and Claude AI as strategic advisor. Uses a layered architecture (strategy, tactics, coherence, micro) with three command execution modes (AI-Assisted, Human Only, Hybrid). Includes a React dashboard for live game visualization, training metrics, build order editing, and strategic command input.

## Vision

- **AI-vs-AI competition** — build a bot that wins consistently at increasing difficulty levels and eventually competes against other SC2 bots
- **Transparent model introspection** — live dashboard showing strategic state, decision reasoning, neural policy outputs, and Claude advisor suggestions
- **Evaluation metrics** — structured reward shaping, win-rate tracking, training diagnostics, and cross-game statistics
- **Autonomous self-improvement** — train-play-evaluate loop that runs 24/7, getting stronger with each cycle

**Advised improvement run 4 complete (2026-04-13)** — Second `--self-improve-code` run at difficulty 3: 3 iterations, 2 code improvements landed, 1 reverted as inert. Anti-float expansion override (`macro_manager.py`) allows expansion during DEFEND when workers saturated and ≥1500m banked, breaking the "die with a bank" death spiral. Warp-in forward pylon selection (`bot.py`) iterates pylons furthest-to-nearest from start so Stalkers stop getting trapped behind main-base buildings. Win rate 80% → 100% at difficulty 3. 829 Python tests, 0 type errors, 0 lint violations.

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
| Testing (Python) | pytest | 829 unit tests, SC2 integration markers |
| Testing (Frontend) | vitest + jsdom + @testing-library/react | 126 component / hook / lib tests |
| Linting | ruff + mypy | Strict type checking, consistent style |

## Dashboard tabs

| Tab | Purpose |
|---|---|
| Live | Real-time game state stream (WebSocket) |
| Stats | Cross-game win rates and aggregate stats from training.db |
| Decisions | Live decision log with rule firings and Claude advice |
| Training | Model comparison + improvement timeline + checkpoint list + reward rule editor |
| Loop | Daemon state, trigger evaluation, full daemon control panel |
| Advisor | Live advised-run status, loop controls, strategic hints, reward injection |
| Improvements | Recent promotions/rollbacks + per-rule reward trend chart |
| Processes | Live system process monitor, port status, state files, backend restart |
| Alerts | Severity-filtered alert list with ack/dismiss + unread badge in nav |

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
# One-shot: start backend + frontend together (Git Bash)
bash scripts/start-dev.sh

# Or in two terminals:
# Terminal 1: backend
uv run python -m alpha4gate.runner --serve

# Terminal 2: frontend dev server
cd frontend && npm run dev
# Opens http://localhost:3000 proxying to :8765
```

### Running without Claude

The Claude advisor is optional. Pass `--no-claude` (or simply don't install the `claude` CLI) and the bot runs entirely on its rule-based strategy + optional PPO policy. Skip step 2's Claude-auth note during setup.

What still works without Claude:

| Feature | Notes |
|---|---|
| Rule-based play vs SC2 AI | `uv run python -m alpha4gate.runner --no-claude --difficulty 3` |
| Batch runs + stats aggregation | `--batch 10 --no-claude` |
| Neural (PPO) play and training | Rule-based or hybrid decision mode; training loop is Claude-free |
| Dashboard — Live, Stats, Decisions, Training, Loop, Improvements, Processes, Alerts | All tabs render and update as normal |
| Build order editor + reward rule editor | Pure local config |
| Command panel — Human Only / Hybrid modes | Natural-language command parser runs locally |
| Daemon loop (auto-training, promotion, rollback) | Does not call Claude |

What needs Claude:

- **Advisor tab** and the `/improve-bot-advised` autonomous improvement loop
- **Live strategic advice** shown on the Live tab mid-game
- **AI-Assisted command mode** (falls back gracefully if unavailable)

Quick start without Claude:

```bash
uv sync
cd frontend && npm install && cd ..
uv run python -m alpha4gate.runner --map Simple64 --difficulty 3 --no-claude --realtime
# In another terminal, watch the dashboard:
bash scripts/start-dev.sh
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
uv run pytest              # 829 unit tests (no SC2 needed)
uv run pytest -m sc2       # SC2 integration tests (SC2 must be running)
uv run ruff check .        # Lint
uv run mypy src            # Type check
cd frontend && npx tsc --noEmit  # TypeScript check
```

## Project Structure

```
Alpha4Gate/
├── src/alpha4gate/          # 47 Python modules
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
├── tests/                   # 829 unit tests (+ SC2 integration markers)
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
