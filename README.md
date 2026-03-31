# Alpha4Gate

A StarCraft II Protoss bot with rule-based decision-making and Claude AI as strategic advisor. Uses a three-layer architecture (strategy, tactics, micro) to play against the built-in AI and human opponents. Includes a React dashboard for live game visualization, build order editing, replay browsing, and strategic reasoning display.

**Strategic Command System complete** — three-mode command system (AI-Assisted, Human-only, Hybrid) with unified command primitives, structured parser + Claude Haiku NLP fallback, real-time WebSocket events, and React CommandPanel UI. Issues #24–#29 closed. 505 tests passing, 0 type errors, 0 lint violations.

## Stack

| Layer | Tool | Why |
|---|---|---|
| Language | Python 3.12 | SC2 libraries are Python-native |
| SC2 interface | burnysc2 v7.1.3 | Async BotAI, actively maintained |
| AI advisor | Claude API (Anthropic SDK) | Strategic advice mid-game, async and non-blocking |
| Build orders | Spawning Tool API | Community build order database |
| Backend | FastAPI | WebSocket + REST for dashboard |
| Frontend | React + TypeScript + Vite | Live dashboard with game state streaming |
| Deep learning | PyTorch + SB3 | PPO policy network for strategic decisions |
| Training data | SQLite | Structured (s,a,r,s') transition storage |
| Testing | pytest | 505 unit tests, SC2 integration markers |
| Linting | ruff + mypy | Strict type checking, consistent style |

## Prerequisites

- Windows 11
- StarCraft II installed at `C:\Program Files (x86)\StarCraft II\`
- Python 3.14+
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
   # Edit .env: set SC2PATH, ANTHROPIC_API_KEY, SPAWNING_TOOL_API_KEY
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
Claude Advisor (async, non-blocking)
        |
Strategy Layer — state machine: opening → expand → attack → defend → late_game
        |
Tactics Layer — macro manager, scouting, production balance
        |
Coherence — army staging, grouping, engagement/retreat decisions
        |
Micro Layer — army movement, kiting, focus fire, ability usage
```

The bot follows a build order during the opening, then transitions to dynamic decision-making. Claude provides optional strategic advice via async API calls (rate-limited to 1 per 30 game-seconds).

## Testing

```bash
uv run pytest              # 505 unit tests (no SC2 needed)
uv run pytest -m sc2       # SC2 integration tests (SC2 must be running)
uv run ruff check .        # Lint
uv run mypy src            # Type check
cd frontend && npx tsc --noEmit  # TypeScript check
```

## Project Structure

```
Alpha4Gate/
├── src/alpha4gate/     # 26 Python source modules (incl. commands/ package)
├── tests/              # 21 test files, 505 tests
├── frontend/           # React + TypeScript dashboard
├── docs/plan.md        # Phase 1 project plan
├── docs/deep-learning-plan.md  # Phase 2 deep learning plan
├── documentation/improvements/  # Improvement plan docs (command system, army coherence, etc.)
├── data/               # Cross-game stats (gitignored)
├── logs/               # JSONL game logs (gitignored)
└── replays/            # SC2 replays (gitignored)
```

## Key Design Decisions

- **Three-layer separation**: Strategy, tactics, and micro are independent modules with clear interfaces, testable in isolation
- **Async Claude advisor**: Fire-and-forget `asyncio.create_task()`, bot never blocks waiting for AI advice
- **JSONL logging**: Extended schema with `strategic_state`, `decision_queue`, and `claude_advice` for decision debugging
- **Cross-game persistence**: Simple JSON files in `data/` — no database needed for single-user local app
- **Build order system**: Named build orders importable from Spawning Tool API, sequenced by supply thresholds
