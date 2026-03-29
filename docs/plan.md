# Alpha4Gate — Project Plan

## What This Is

A StarCraft II Protoss bot that plays against the built-in AI and human opponents, using
rule-based decision-making with a layered architecture (strategy → tactics → micro). A Claude
API integration provides asynchronous strategic advice mid-game without blocking the game loop.
A React dashboard provides live game visualization, stats, build order editing, replay
browsing, and display of the bot's strategic reasoning. The ultimate goal is to beat a specific
human opponent (William Gathright) in multiplayer.

---

## Stack

| Layer              | Tool / Library       | Why                                                        |
| ------------------ | -------------------- | ---------------------------------------------------------- |
| Language           | Python 3.14          | Matches sc2ai_explore; SC2 libs are Python-native          |
| Package manager    | uv                   | Consistent with existing workflow                          |
| SC2 interface      | burnysc2 (v7.1.3)    | Async BotAI base class, actively maintained (replaced PySC2) |
| AI advisor         | Claude API (Anthropic SDK) | Strategic advice mid-game via async calls             |
| Build orders       | Spawning Tool API    | Community SC2 build order database (spawningtool.com); JSON API returns build order step sequences. Auth: API key in `SPAWNING_TOOL_API_KEY` env var. |
| Backend            | FastAPI              | WebSocket + REST, async-native, lightweight                |
| Frontend           | React                | Live dashboard, stats, build order editor, replay browser  |
| Testing            | pytest               | Markers separate unit from SC2 integration tests           |
| Linting            | ruff                 | E,F,I,UP,B rules, line-length=100                          |
| Type checking      | mypy                 | Strict mode, disallow_untyped_defs                         |
| Config             | python-dotenv        | Load SC2PATH, API keys from `.env`                         |

### Library choice: burnysc2 (not PySC2)

PySC2 (DeepMind) is unmaintained and its `dm-tree` dependency requires CMake to build C
extensions. `burnysc2` (v7.1.3) installs cleanly on Python 3.14, is actively maintained, and
provides a higher-level async API over the same `s2clientprotocol` protobuf layer. Key
difference: burnysc2 uses `async/await` with `BotAI` base class instead of PySC2's synchronous
`BaseAgent.step(obs)` pattern. The decision engine, Claude advisor, and all game-loop code must
be async-compatible.

---

## Data Store

### JSONL game logs

- **One file per game session**, named `logs/game_<ISO-timestamp>.jsonl`
- **Log entry schema** (one JSON object per line):

```json
{
  "timestamp": "2026-03-29T14:30:05.123Z",
  "game_step": 1024,
  "game_time_seconds": 64.0,
  "minerals": 350,
  "vespene": 125,
  "supply_used": 23,
  "supply_cap": 31,
  "units": [
    {"type": "Probe", "count": 16},
    {"type": "Stalker", "count": 4}
  ],
  "structures": [
    {"type": "Nexus", "count": 2},
    {"type": "Gateway", "count": 3}
  ],
  "actions_taken": [
    {"action": "Build", "target": "Pylon", "location": [32, 48]}
  ],
  "strategic_state": "expand",
  "decision_queue": ["expand_natural", "build_gateway_x2"],
  "claude_advice": "Consider double forge upgrades before third base",
  "score": 1250
}
```

### Cross-game persistence

Stored as JSON files in `data/`:

| File                        | Contents                                              |
| --------------------------- | ----------------------------------------------------- |
| `data/stats.json`           | Win/loss record by opponent, map, difficulty           |
| `data/build_orders.json`    | Named build orders with step sequences                 |
| `data/opponent_profiles.json` | Observed opponent tendencies (human opponents)       |
| `data/decision_audit.json`  | Decision engine choices with outcomes for review       |
| `data/milestones.json`      | Timestamps for first win, first multiplayer game, etc. |

#### `stats.json` schema

```json
{
  "games": [
    {
      "timestamp": "2026-03-29T14:30:00Z",
      "map": "Simple64",
      "opponent": "built-in-easy",
      "result": "win",
      "duration_seconds": 420,
      "build_order_used": "4gate",
      "score": 3200
    }
  ],
  "aggregates": {
    "total_wins": 5,
    "total_losses": 2,
    "by_map": {"Simple64": {"wins": 3, "losses": 1}},
    "by_opponent": {"built-in-easy": {"wins": 5, "losses": 0}},
    "by_build_order": {"4gate": {"wins": 4, "losses": 1}}
  }
}
```

#### `build_orders.json` schema

```json
{
  "orders": [
    {
      "id": "4gate",
      "name": "4-Gate Timing Push",
      "source": "manual",
      "steps": [
        {"supply": 14, "action": "build", "target": "Pylon"},
        {"supply": 16, "action": "build", "target": "Gateway"},
        {"supply": 16, "action": "build", "target": "Assimilator"},
        {"supply": 19, "action": "build", "target": "Nexus"},
        {"supply": 20, "action": "build", "target": "CyberneticsCore"},
        {"supply": 21, "action": "build", "target": "Pylon"},
        {"supply": 23, "action": "build", "target": "Gateway"},
        {"supply": 25, "action": "build", "target": "Gateway"},
        {"supply": 27, "action": "build", "target": "Gateway"}
      ]
    }
  ]
}
```

Build order step fields: `supply` is the trigger (begin step when current supply >= value),
`action` is `"build"` (structure), `"train"` (unit), or `"research"` (upgrade), and `target`
is the `UnitTypeId` or `UpgradeId` name.

### Replays

- Saved to `replays/game_<ISO-timestamp>.SC2Replay`
- Parsed on-demand from the dashboard for post-game analysis

### Directory auto-creation

`logs/`, `replays/`, and `data/` are created automatically on startup if they don't exist.

---

## Decision Architecture

The bot uses a three-layer decision architecture:

```
┌─────────────────────────────────────────────┐
│              Claude Advisor                  │
│  (async, non-blocking strategic advice)     │
└──────────────┬──────────────────────────────┘
               │ suggestions (async)
               ▼
┌─────────────────────────────────────────────┐
│          Strategy Layer                      │
│  State machine: opening → expand → attack   │
│  → defend → late_game                       │
│  Owns: build order queue, strategic state   │
└──────────────┬──────────────────────────────┘
               │ orders
               ▼
┌─────────────────────────────────────────────┐
│          Tactics Layer                       │
│  Macro manager: workers, expansions, supply │
│  Scouting: probe scout, threat assessment   │
│  Production: gateway/robo/stargate balance  │
└──────────────┬──────────────────────────────┘
               │ commands
               ▼
┌─────────────────────────────────────────────┐
│          Micro Layer                         │
│  Army movement, kiting, focus fire          │
│  Ability usage (blink, force field, storm)  │
└─────────────────────────────────────────────┘
```

### Strategic states

| State       | Entry condition                     | Behavior                              |
| ----------- | ----------------------------------- | ------------------------------------- |
| `opening`   | Game start                          | Follow selected build order strictly  |
| `expand`    | Build order complete, safe          | Take natural/third, saturate workers  |
| `attack`    | Army threshold met or timing window | Move out, engage enemy                |
| `defend`    | Enemy army detected near base       | Pull back, defend with structures     |
| `late_game` | 3+ bases, high tech                 | Tech switches, multi-prong attacks    |

### Claude advisor integration

- Claude receives: current game state snapshot, strategic state, recent decisions, enemy composition
- Claude returns: strategic suggestions (text), rated urgency
- Calls are `asyncio.create_task()` — fire and forget, results processed on next `on_step()`
- Rate limited: max 1 call every 30 game-seconds to avoid API cost explosion
- Fallback: if Claude is unavailable, bot continues with rule-based decisions only

---

## Modules

### `src/alpha4gate/`

| File                  | Purpose                                                      |
| --------------------- | ------------------------------------------------------------ |
| `__init__.py`         | Package init                                                 |
| `config.py`           | Load `.env`, validate SC2PATH, expose settings dataclass (ported from sc2ai_explore) |
| `connection.py`       | Launch SC2 via burnysc2, verify connection (ported)          |
| `observer.py`         | Extract game state from bot state into typed dicts (ported, extended) |
| `logger.py`           | Background thread: drain queue, write JSONL, push WebSocket (ported) |
| `console.py`          | Real-time one-line console status output (ported)            |
| `decision_engine.py`  | Strategy state machine, build order queue, state transitions |
| `macro_manager.py`    | Economy: worker production, expansions, supply, production buildings |
| `scouting.py`         | Probe scouting, enemy tracking, threat assessment            |
| `micro.py`            | Army movement, kiting, focus fire, ability usage             |
| `claude_advisor.py`   | Async Claude API calls, prompt construction, response parsing |
| `build_orders.py`     | Build order definitions, sequencer, Spawning Tool integration |
| `replay_parser.py`    | Parse SC2Replay files, extract stats and timelines           |
| `batch_runner.py`     | Run N games in sequence, aggregate statistics                |
| `api.py`              | FastAPI REST endpoints for dashboard data                    |
| `web_socket.py`       | WebSocket endpoint for live game state streaming             |
| `bot.py`              | Main BotAI subclass: `on_step()` orchestrates all layers     |
| `runner.py`           | CLI entry point: game launch, mode selection, web server     |

### `frontend/`

| Path                  | Purpose                                                      |
| --------------------- | ------------------------------------------------------------ |
| `package.json`        | React dependencies, build scripts                            |
| `src/App.tsx`         | Main app layout with routing                                 |
| `src/components/`     | LiveView, Stats, BuildOrderEditor, ReplayBrowser, DecisionQueue |
| `src/hooks/`          | useWebSocket, useGameState, useBuildOrders                   |
| `src/types/`          | TypeScript interfaces matching Python data models            |

### `tests/`

| File                        | Purpose                                          |
| --------------------------- | ------------------------------------------------ |
| `test_config.py`            | Config loading and validation (ported)           |
| `test_observer.py`          | Observation parsing (ported, extended)            |
| `test_logger.py`            | JSONL serialization and queue drain (ported)      |
| `test_console.py`           | Console output formatting (ported)               |
| `test_decision_engine.py`   | State machine transitions, build order queue     |
| `test_macro_manager.py`     | Worker/expansion/supply logic                    |
| `test_scouting.py`          | Threat assessment scoring                        |
| `test_micro.py`             | Kiting, focus fire target selection              |
| `test_claude_advisor.py`    | Prompt construction, response parsing, rate limit |
| `test_build_orders.py`      | Build order sequencing, Spawning Tool parsing    |
| `test_replay_parser.py`     | Replay parsing and stat extraction               |
| `test_batch_runner.py`      | Multi-game aggregation                           |
| `test_api.py`               | REST endpoint responses                          |
| `test_web_socket.py`        | WebSocket streaming                              |
| `test_bot.py`               | Integration: bot plays N steps (marked `sc2`)    |
| `test_full_game.py`         | Full game vs AI (marked `sc2`)                   |
| `test_multiplayer.py`       | Multiplayer connection (marked `sc2`, `mp`)      |

---

## API Route Contract

### Identifier formats

- **Build order ID**: slug derived from the name (lowercase, hyphens for spaces, e.g. `"4gate"`,
  `"stalker-expand"`). Used in URL paths and `build_orders.json`.
- **Replay ID**: ISO timestamp from the filename (e.g. `"2026-03-29T14-30-00"`). Matches
  the log filename stem. Used in URL paths.
- **Game session ID**: same ISO timestamp as replay/log ID. Not a separate value.

### REST endpoints (FastAPI, served at `http://localhost:8765`)

| Method | Path                     | Request body                              | Response body                              |
| ------ | ------------------------ | ----------------------------------------- | ------------------------------------------ |
| GET    | `/api/status`            | —                                         | `{"state": "playing"\|"idle", "game_step": int, "game_time_seconds": float, "minerals": int, "vespene": int, "supply_used": int, "supply_cap": int, "strategic_state": str}` (matches JSONL log entry subset; all fields null when idle except `state`) |
| GET    | `/api/stats`             | —                                         | Full `stats.json` content (see schema above) |
| GET    | `/api/build-orders`      | —                                         | `{"orders": BuildOrder[]}` (see `build_orders.json` schema) |
| POST   | `/api/build-orders`      | `BuildOrder` object (same shape as in schema; `id` auto-generated from `name` if omitted) | `{"id": str, "created": bool}` |
| DELETE | `/api/build-orders/:id`  | — (`:id` is build order slug)             | `{"deleted": bool}` |
| GET    | `/api/replays`           | —                                         | `{"replays": [{"id": str, "timestamp": str, "map": str, "result": str, "duration_seconds": int}]}` |
| GET    | `/api/replays/:id`       | — (`:id` is ISO timestamp)                | `{"id": str, "timeline": [{"game_time_seconds": float, "event": str, "detail": str}], "stats": {"minerals_collected": int, "gas_collected": int, "units_produced": int, "units_lost": int, "structures_built": int}}` |
| GET    | `/api/decision-log`      | —                                         | `{"entries": [{"timestamp": str, "game_step": int, "from_state": str, "to_state": str, "reason": str, "claude_advice": str\|null}]}` |
| POST   | `/api/game/start`        | `{"map": str, "difficulty": str}`         | `{"game_id": str, "status": "starting"}` |
| POST   | `/api/game/batch`        | `{"count": int, "map": str, "difficulty": str}` | `{"batch_id": str, "count": int, "status": "running"}` |

### WebSocket endpoints

| Path           | Direction       | Message format                              | Frequency                      |
| -------------- | --------------- | ------------------------------------------- | ------------------------------ |
| `/ws/game`     | server → client | One JSON message per tick, matching JSONL log entry schema | Every 22 game steps (~1 real second at Normal speed) |
| `/ws/decisions`| server → client | `{"event": "state_change"\|"claude_advice", "timestamp": str, "detail": object}` | On every state transition or Claude response |

### Frontend proxy

In development, React dev server proxies `/api/*` and `/ws/*` to FastAPI on port 8765.
In production, FastAPI serves the built React static files from `frontend/build/`.

---

## Project Structure

```
Alpha4Gate/
├── .env                        # Local config — gitignored
├── .env.example                # Template with keys and defaults
├── .gitignore
├── pyproject.toml              # Python deps, ruff/mypy/pytest config
├── docs/
│   └── plan.md                 # This file
├── src/
│   └── alpha4gate/
│       ├── __init__.py
│       ├── config.py
│       ├── connection.py
│       ├── observer.py
│       ├── logger.py
│       ├── console.py
│       ├── decision_engine.py
│       ├── macro_manager.py
│       ├── scouting.py
│       ├── micro.py
│       ├── claude_advisor.py
│       ├── build_orders.py
│       ├── replay_parser.py
│       ├── batch_runner.py
│       ├── api.py
│       ├── web_socket.py
│       ├── bot.py
│       └── runner.py
├── frontend/
│   ├── package.json
│   ├── tsconfig.json
│   ├── public/
│   │   └── index.html
│   └── src/
│       ├── App.tsx
│       ├── index.tsx
│       ├── components/
│       │   ├── LiveView.tsx
│       │   ├── Stats.tsx
│       │   ├── BuildOrderEditor.tsx
│       │   ├── ReplayBrowser.tsx
│       │   └── DecisionQueue.tsx
│       ├── hooks/
│       │   ├── useWebSocket.ts
│       │   ├── useGameState.ts
│       │   └── useBuildOrders.ts
│       └── types/
│           └── game.ts
├── tests/
│   ├── test_config.py
│   ├── test_observer.py
│   ├── test_logger.py
│   ├── test_console.py
│   ├── test_decision_engine.py
│   ├── test_macro_manager.py
│   ├── test_scouting.py
│   ├── test_micro.py
│   ├── test_claude_advisor.py
│   ├── test_build_orders.py
│   ├── test_replay_parser.py
│   ├── test_batch_runner.py
│   ├── test_api.py
│   ├── test_web_socket.py
│   ├── test_bot.py
│   ├── test_full_game.py
│   └── test_multiplayer.py
├── data/                       # Cross-game persistence — gitignored
├── logs/                       # Game logs — gitignored
└── replays/                    # SC2 replays — gitignored
```

---

## Key Design Decisions

**Three-layer decision architecture** — Strategy, tactics, and micro are separate modules with
clear interfaces. Strategy decides *what* to do (expand, attack, defend), tactics decides *how*
(which buildings, how many units), micro decides *where* (unit positioning, targeting). This
separation allows testing each layer independently and swapping implementations (e.g., replacing
rule-based micro with ML micro later).

**Async Claude advisor** — Claude API calls are fired as `asyncio.create_task()` and results are
consumed on the next `on_step()` iteration. The bot never blocks waiting for Claude. If the API
is slow or down, the bot continues with pure rule-based decisions. Rate limited to 1 call per
30 game-seconds to control costs.

**React frontend separate from Python backend** — The dashboard has enough complexity (live
updates, build order editing, replay browsing) to justify a proper React app over the vanilla
HTML approach used in sc2ai_explore. FastAPI serves the API and WebSocket; React handles all UI.

**JSONL logs with extended schema** — Same JSONL format as sc2ai_explore but extended with
`strategic_state`, `decision_queue`, and `claude_advice` fields. This makes decision debugging
possible by reading raw log files.

**Cross-game persistence via JSON files** — Simple JSON files in `data/` for stats, build
orders, opponent profiles, and decision audit trails. No database needed for a single-user
local application. Files are loaded into memory on startup and flushed after each game.

**Build order system** — Named build orders with step sequences, importable from Spawning Tool
API. The decision engine follows the active build order during the `opening` state, then
transitions to dynamic decision-making.

**Ported infrastructure** — `config.py`, `connection.py`, `logger.py`, `console.py`, and
`observer.py` are ported directly from sc2ai_explore with namespace changes. This avoids
re-implementing tested code. Source files live at
`~/dev\sc2ai_explore\src\sc2explore\`; tests at
`~/dev\sc2ai_explore\tests\`.

---

## Out of Scope (v1)

- ML/RL training (planned for future versions, not this build)
- Non-Protoss races (Zerg, Terran)
- Automated ladder play or tournament participation
- Multi-bot management (running multiple bot instances)
- Voice or chat integration during games
- Mobile or remote dashboard access (local only)

---

## Open Questions / Risks

| Item                             | Risk                                              | Mitigation                                                |
| -------------------------------- | ------------------------------------------------- | --------------------------------------------------------- |
| Claude API latency               | Responses may take 2-5s; stale advice if game moves fast | Fire-and-forget async; rate limit; bot doesn't depend on advice |
| Claude API cost                  | Frequent calls during batch runs could be expensive | Rate limit (1/30s game-time), disable in batch mode, track costs |
| Spawning Tool API reliability    | Third-party API may be slow or down               | Cache build orders locally; fallback to bundled defaults   |
| burnysc2 multiplayer support     | Multiplayer lobby joining may have quirks          | Step 10 is last; research burnysc2 multiplayer docs early  |
| React build pipeline complexity  | Adding Node.js/npm alongside Python/uv            | Keep frontend simple; Vite for zero-config setup           |
| SC2 patch breaking burnysc2      | Blizzard patch could break protobuf compatibility  | burnysc2 is actively maintained; pin version, watch releases |
| Replay parser complexity         | SC2Replay format is binary protobuf, complex       | Use burnysc2's built-in replay support first; fall back to `sc2reader` (pip package) if burnysc2 lacks needed detail |
| Decision engine tuning           | Rule-based decisions may be too rigid              | Audit trail + replay analysis to identify failure patterns |

---

## How to Run

### Prerequisites

- Windows 11
- StarCraft II installed at `C:\Program Files (x86)\StarCraft II\`
- Python 3.14+
- uv package manager
- Node.js 18+ (for React frontend)

### Map setup

Maps must be downloaded from Blizzard's CDN (password: `iagreetotheeula`):

```bash
curl -o /tmp/Melee.zip https://blzdistsc2-a.akamaihd.net/MapPacks/Melee.zip
cd "C:\Program Files (x86)\StarCraft II\Maps"
mkdir -p Melee
unzip -P iagreetotheeula /tmp/Melee.zip -d Melee/
```

**Important**: Map files on GitHub (s2client-proto) are Git LFS pointers, not real binary files.
Always use the Blizzard CDN zip packages.

### Setup

```bash
cd .

# Install Python dependencies
uv sync

# Create .env from template
cp .env.example .env
# Edit .env: set SC2PATH, ANTHROPIC_API_KEY, SPAWNING_TOOL_API_KEY

# Install frontend dependencies
cd frontend && npm install && cd ..
```

### Run a game vs AI

```bash
uv run python -m alpha4gate.runner
```

### Start the dashboard

```bash
# Terminal 1: backend
uv run python -m alpha4gate.runner --serve

# Terminal 2: frontend (dev mode)
cd frontend && npm start
# Opens http://localhost:3000 proxying to :8765
```

### Run tests

```bash
# Unit tests only (no SC2 needed)
uv run pytest

# SC2 integration tests
uv run pytest -m sc2

# Frontend tests
cd frontend && npm test

# Lint and typecheck
uv run ruff check .
uv run mypy src
```

---

## Development Process

All steps use full RWL (Reviewer-Writer Loop). RWL is a two-agent build pattern: a developer
agent writes code in an isolated git worktree while a reviewer agent gates it with
`pytest`/`ruff`/`mypy` before approving the merge back to the main branch. Each step is built
as a separate RWL run. The developer receives the step description as its problem statement; the
reviewer rejects until all tests pass and lint/typecheck are clean.

Each step builds on the previous one and produces a testable result with passing tests before
moving to the next.

### Step 1 — Project scaffolding

- Create directory structure, `pyproject.toml`, `.env.example`, `.gitignore`
- Configure ruff, mypy, pytest in `pyproject.toml`
- Port from sc2ai_explore: `config.py`, `connection.py`, `logger.py`, `console.py`, `observer.py`
- Rename package from `sc2explore` to `alpha4gate` in all imports
- Port and adapt unit tests: `test_config.py`, `test_observer.py`, `test_logger.py`, `test_console.py`
- Initialize React app skeleton in `frontend/` using Vite with React + TypeScript template
- **Done when**: `uv run pytest` passes ported tests, `uv run ruff check .` clean, `cd frontend && npm start` serves blank page

### Step 2 — Decision engine core

- Implement `decision_engine.py`: strategic state machine (opening → expand → attack → defend → late_game)
- Implement `build_orders.py`: build order data structure, sequencer, hardcoded 4-gate opener
- Wire decision engine into `bot.py` (BotAI subclass with `on_step()`)
- State transitions based on game state (supply, army size, enemy presence)
- Write `test_decision_engine.py`: state transitions with mock game states
- Write `test_build_orders.py`: sequencer step-through, completion detection
- **Done when**: `uv run pytest -k "decision or build_order"` passes, state machine transitions are logged

### Step 3 — Macro manager

- Implement `macro_manager.py`: worker production (saturate minerals/gas), expansion timing, supply management (pre-build pylons), production building placement
- Integrate with decision engine: macro manager reads strategic state to adjust priorities
- Write `test_macro_manager.py`: worker count logic, expansion triggers, supply math
- **Done when**: Bot plays vs Easy AI and maintains economy (workers, expansions, no supply block)

### Step 4 — Scouting & threat assessment

- Implement `scouting.py`: probe scout on timer, enemy base location tracking, enemy unit/structure tracking, threat level scoring
- Threat assessment feeds into decision engine (trigger `defend` state when threat detected)
- Write `test_scouting.py`: threat scoring with mock enemy compositions
- **Done when**: Bot scouts enemy base, logs enemy composition, transitions to defend when threatened

### Step 5 — Micro controller

- Implement `micro.py`: army rally points, attack-move, kiting (stalkers), focus fire (priority targeting), ability usage stubs (blink, force field)
- Integrate with decision engine: micro layer executes when state is `attack` or `defend`
- Write `test_micro.py`: target priority selection, kiting distance math
- **Done when**: Bot wins vs Easy AI using produced army with basic micro

### Step 6 — Claude advisor integration

- Implement `claude_advisor.py`: prompt construction from game state, async API call via `asyncio.create_task()`, response parsing, rate limiting (1 call / 30 game-seconds)
- Feed Claude suggestions into decision engine as advisory input (not mandatory)
- Mock Claude in tests (no real API calls in CI)
- Write `test_claude_advisor.py`: prompt format, rate limiter, response parsing, graceful failure
- **Done when**: Bot queries Claude during games, logs advice, continues if Claude is unavailable

### Step 7 — React dashboard

- Build React components: LiveView (WebSocket game state), Stats (win/loss charts), BuildOrderEditor (CRUD), ReplayBrowser (list + details), DecisionQueue (live strategic state + Claude advice)
- Implement `api.py`: REST endpoints for stats, build orders, replays, game control
- Implement `web_socket.py`: live game state and decision streaming
- Frontend proxy config for development
- Write `test_api.py`, `test_web_socket.py`: endpoint responses, WebSocket streaming
- **Done when**: Dashboard shows live game data, build orders are editable, replays are browsable

### Step 8 — Replay analysis engine

- Implement `replay_parser.py`: parse SC2Replay files, extract resource timelines, army composition over time, key events (expansions, attacks, losses)
- Expose parsed data via `/api/replays/:id` endpoint
- Write `test_replay_parser.py`: parse a saved replay, verify extracted stats
- **Done when**: Dashboard replay browser shows parsed game timeline and stats

### Step 9 — Batch runner & statistics

- Implement `batch_runner.py`: run N games sequentially, aggregate win rates by map/difficulty, track build order performance, save results to `data/stats.json`
- Disable Claude advisor in batch mode (cost control)
- Expose via `/api/game/batch` endpoint
- Write `test_batch_runner.py`: multi-game stat aggregation
- **Done when**: `uv run python -m alpha4gate.runner --batch 10` plays 10 games and produces stats

### Step 10 — Multiplayer preparation

- Research burnysc2 multiplayer lobby API
- Implement multiplayer game joining in `connection.py`
- Implement opponent profiling: track William's tendencies across games in `data/opponent_profiles.json`
- Adapt decision engine: load opponent profile to adjust strategy
- Write `test_multiplayer.py` (marked `sc2`, `mp`): connect to multiplayer lobby
- **Done when**: Bot can join a multiplayer game and play against a human opponent

---

## Appendix

### burnysc2 key concepts

- **BotAI**: Base class for bots. Subclass and override `async on_step(iteration)` for game
  logic. Provides `self.minerals`, `self.vespene`, `self.units`, `self.structures`,
  `self.enemy_units`, etc.
- **run_game()**: Entry point. `sc2.main.run_game(map, [bot, opponent], realtime=False)` starts
  a game, handles SC2 client launch and teardown.
- **Units**: `self.units` / `self.structures` return `Units` collections with filtering
  (`self.units(UnitTypeId.PROBE)`) and spatial queries (`.closest_to()`, `.ready`).
- **Actions**: `unit.train(UnitTypeId.PROBE)`, `unit.build(UnitTypeId.PYLON, position)`,
  `unit.attack(target)`.
- **UnitTypeId / AbilityId**: Enums from `sc2.ids.unit_typeid` and `sc2.ids.ability_id`.
- **Async model**: Game loop calls `on_step()` as async. Other hooks: `on_start()`,
  `on_end()`, `on_unit_created()`, `on_unit_destroyed()`.

### SC2 environment setup (Windows)

- SC2 path: `C:\Program Files (x86)\StarCraft II\`
- Build 95841 works with burnysc2 v7.1.3 despite not being in burnysc2's versions list
- Maps go in `<SC2PATH>\Maps\Melee\` (downloaded from Blizzard CDN, NOT GitHub LFS)
- Stale `SC2_x64.exe` processes can linger after failed tests — kill manually if ports conflict

### Protoss unit reference (early-to-mid game)

| Unit / Building    | Mineral | Gas | Supply | Built from       | Notes              |
| ------------------ | ------- | --- | ------ | ---------------- | ------------------ |
| Probe              | 50      | 0   | 1      | Nexus            | Worker             |
| Pylon              | 100     | 0   | 0      | Probe            | Supply + power     |
| Gateway            | 150     | 0   | 0      | Probe            | Core military      |
| Zealot             | 100     | 0   | 2      | Gateway          | Melee              |
| Cybernetics Core   | 150     | 0   | 0      | Probe (req Gate) | Unlocks Stalker    |
| Stalker            | 125     | 50  | 2      | Gateway (req Cy) | Ranged, blink      |
| Sentry             | 50      | 100 | 2      | Gateway (req Cy) | Force field, guardian shield |
| Assimilator        | 75      | 0   | 0      | Probe            | Gas extraction     |
| Robotics Facility  | 200     | 100 | 0      | Probe (req Cy)   | Unlocks Immortal   |
| Immortal           | 275     | 100 | 4      | Robo Facility    | Anti-armor         |
| Twilight Council   | 150     | 100 | 0      | Probe (req Cy)   | Unlocks blink/charge |
| Forge              | 150     | 0   | 0      | Probe            | Ground upgrades    |

### Claude advisor prompt template (draft)

```
You are a StarCraft II Protoss strategic advisor. Analyze the current game state and provide
one actionable suggestion.

Game time: {game_time}
Strategic state: {strategic_state}
Resources: {minerals} minerals, {vespene} gas, {supply_used}/{supply_cap} supply
Army: {army_composition}
Enemy (known): {enemy_composition}
Recent decisions: {recent_decisions}
Current build order: {build_order_name} (step {build_step}/{total_steps})

Respond with:
- suggestion: one sentence, actionable
- urgency: low / medium / high
- reasoning: one sentence
```
