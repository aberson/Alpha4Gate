# Improvement: Strategic Command System

## Summary
Add a three-mode strategic command system that allows Claude AI, a human player,
or both to issue real-time strategic commands to the bot during SC2 games. Commands
flow through a unified primitive library so all input — whether AI-generated or
human-typed — resolves to the same bot actions.

## Existing codebase context

Alpha4Gate is a StarCraft II Protoss bot at `./`.
It uses burnysc2 (a Python SC2 API library) with a three-layer architecture.
Key modules a builder needs to understand:

| Module | Path | What it does |
|--------|------|-------------|
| `Alpha4GateBot` | `src/alpha4gate/bot.py` | Main bot class, subclasses burnysc2's `BotAI`. The `async on_step(iteration)` method runs every game frame and orchestrates all decision layers. |
| `DecisionEngine` | `src/alpha4gate/decision_engine.py` | Rule-based state machine with 5 states: `StrategicState(StrEnum)` = OPENING, EXPAND, ATTACK, DEFEND, LATE_GAME. `evaluate(snapshot, game_step)` returns the current state. Has `set_claude_advice(advice)` (exists but never called). `DecisionEntry` dataclass logs transitions with a `claude_advice` field. |
| `GameSnapshot` | `src/alpha4gate/decision_engine.py` | Dataclass holding minimal game state for decisions: supply, minerals, vespene, army_supply, worker_count, base_count, enemy_army_near_base, game_time_seconds, structure counts. |
| `MacroManager` | `src/alpha4gate/macro_manager.py` | Economy and production decisions (worker saturation, supply, expansions, production building scaling). Called from `on_step()` for non-OPENING states. |
| `MicroController` | `src/alpha4gate/micro.py` | Army combat logic (target priority, kiting, focus fire). Called from `on_step()` during ATTACK/DEFEND states. |
| `NeuralDecisionEngine` | `src/alpha4gate/learning/neural_engine.py` | Optional PPO model that can replace rule-based decisions. `DecisionMode(StrEnum)` = RULES, NEURAL, HYBRID. In HYBRID mode, neural picks action but DEFEND is forced if enemy near base. |
| `ClaudeAdvisor` | `src/alpha4gate/claude_advisor.py` | Async Claude API advisor. Fire-and-forget `asyncio.Task`. `RateLimiter` gates calls (default 30 game-seconds). Returns `AdvisorResponse(suggestion, urgency, reasoning)`. **Built but NOT wired into game loop.** |
| `ScoutManager` | `src/alpha4gate/scouting.py` | Sends probe/observer to scout enemy base. |
| `ArmyCoherenceManager` | `src/alpha4gate/army_coherence.py` | Army grouping params: staging timeout, gather radius. |
| `ConnectionManager` | `src/alpha4gate/web_socket.py` | WebSocket broadcast manager. `_broadcast_queue` (thread-safe `queue.Queue`) bridges game thread → FastAPI async. |
| `api.py` | `src/alpha4gate/api.py` | FastAPI app with REST + WebSocket endpoints. Module-level `ws_manager = ConnectionManager()` singleton. |
| `runner.py` | `src/alpha4gate/runner.py` | CLI entry point. Modes: `--serve`, `--batch N`, `--train`, `--decision-mode`, `--no-claude`. |

Key bot methods used by the command executor:
- `self.train(unit_type)` — train a unit from a production structure
- `self.build(structure_type, near=position)` — place a structure
- `self.research(upgrade_type)` — start research at an appropriate structure
- `self.start_location` — `Point2` of own start position
- `self.enemy_start_locations[0]` — `Point2` of enemy start
- `self.expansion_locations_list` — list of all expansion `Point2` positions
- `self._enemy_natural()` — cached `Point2` of enemy natural expansion (added in natural-denial improvement)
- `self._cached_staging_point` — army rally `Point2`, updated by coherence manager

Frontend: React + TypeScript SPA in `frontend/`, built with Vite.
Existing components in `frontend/src/components/`: LiveView, DecisionQueue,
BuildOrderEditor, Stats, TrainingDashboard, CheckpointList, ReplayBrowser,
RewardRuleEditor.

## Development

```bash
# Install Python dependencies
cd .
uv sync

# Run tests (378 unit tests, no SC2 needed)
uv run pytest

# Lint and typecheck
uv run ruff check .
uv run mypy src

# Frontend dev server
cd frontend && npm install && npm start
# Opens http://localhost:3000 proxying to :8765

# Start backend API server
uv run python -m alpha4gate.runner --serve
```

## Out of scope for v1

- Voice input (speech-to-text for commands)
- Replay command playback (re-executing commands from a saved game)
- Multi-human support (only one human commander at a time)
- Command scripting / macro recording
- Persistent command presets across games

## Three Modes

| Mode | Enum value | Description | Default for |
|------|-----------|-------------|-------------|
| AI-Assisted | `AI_ASSISTED` | Claude observes game state every N seconds and issues commands | Batch testing |
| Human-only | `HUMAN_ONLY` | Human types commands in web UI | — |
| Hybrid | `HYBRID_CMD` | Both Claude and human can issue commands; human always overrides | vs William |

- Mode is switchable mid-game via the web UI.
- In hybrid mode, a human command locks out Claude for a configurable duration
  (default 5 seconds game-time). Lockout duration is adjustable in the UI.

### Mode vs Mute semantics

**Mode** controls conflict resolution rules and whether Claude is active:
- `AI_ASSISTED`: Claude issues commands, no human input expected.
- `HUMAN_ONLY`: Claude advisor disabled (no API calls), human only.
- `HYBRID_CMD`: Both active, human overrides with lockout.

**Mute Claude** is a toggle independent of mode. It suppresses Claude API calls
without changing the mode. Effects by mode:
- `AI_ASSISTED` + muted: No commands issued at all (bot runs on decision engine only).
- `HYBRID_CMD` + muted: Functionally `HUMAN_ONLY` behavior but mode stays `HYBRID_CMD`.
- `HUMAN_ONLY` + muted: No effect (Claude already disabled).

Unmuting resumes Claude calls in whatever mode is active.

## Command Primitive Library

All strategic input — whether from Claude NLP, human free text, or structured
shortcuts — resolves to one or more `CommandPrimitive` instances.

### Primitive schema

```python
class CommandAction(StrEnum):
    BUILD = "build"
    EXPAND = "expand"
    DEFEND = "defend"
    ATTACK = "attack"
    SCOUT = "scout"
    TECH = "tech"
    UPGRADE = "upgrade"
    RALLY = "rally"


class CommandSource(StrEnum):
    AI = "ai"
    HUMAN = "human"


class CommandMode(StrEnum):
    AI_ASSISTED = "ai_assisted"
    HUMAN_ONLY = "human_only"
    HYBRID_CMD = "hybrid_cmd"  # named HYBRID_CMD to avoid collision with DecisionMode.HYBRID


@dataclass
class CommandPrimitive:
    action: CommandAction          # what to do
    target: str                    # unit type, structure, upgrade, or keyword
    location: str | None = None   # "natural", "third", "main", "enemy_natural", etc.
    priority: int = 5             # 1-10, higher = more urgent
    source: CommandSource = CommandSource.HUMAN
    id: str = ""                  # UUID, assigned on creation
    timestamp: float = 0.0        # game_time_seconds when command was created
    ttl: float = 60.0             # expires after this many game-seconds
```

- `id`: UUID4 string, assigned when the primitive is created. Used by the UI to
  track the full lifecycle (parsed → queued → executed/expired/rejected).
- `timestamp`: Game time when the command entered the queue.
- `ttl`: Time-to-live in game-seconds. Default 60s. Commands older than
  `timestamp + ttl` are dropped during queue drain. Expired commands are logged
  with status `"expired"` and broadcast via WebSocket.

### Queue constraints

- **Max queue depth**: 10 commands. If full, lowest-priority command is evicted
  (AI commands evicted before human commands at equal priority).
- **TTL**: Default 60 game-seconds per command. Expired commands are removed
  during each `on_step()` drain cycle and broadcast as `"expired"` events.
- **Eviction logging**: All evictions (overflow, expiry, conflict-clear) are
  logged to command history and broadcast via `/ws/commands`.

### Initial action vocabulary

| Action | Target examples | Location examples | Bot mechanism |
|--------|----------------|-------------------|---------------|
| `BUILD` | stalkers, zealots, immortals, pylon, gateway | main, natural, third | Queue production / place building |
| `EXPAND` | — | third, fourth | Take next expansion |
| `DEFEND` | — | natural, main, third | Force DEFEND state + rally army to location |
| `ATTACK` | — | enemy_natural, enemy_main, enemy_third | Force ATTACK state + set attack target |
| `SCOUT` | — | enemy_base, map | Send probe/observer |
| `TECH` | voidrays, colossi, high_templar, blink | — | Build prerequisite structures + queue units |
| `UPGRADE` | weapons, armor, shields, blink, charge | — | Queue research at appropriate structure |
| `RALLY` | army | natural, third, enemy_natural | Change army gather point |

### Composable commands

Some commands imply a chain of primitives (e.g. "tech to voidrays" = build stargate,
then train voidrays). Two execution strategies:

1. **Bot-level chains**: `CommandExecutor` has built-in recipes that expand a single
   primitive into a prerequisite chain (e.g. TECH voidrays → check/build stargate →
   queue voidray production).
2. **Claude-decomposed**: Claude breaks free text into multiple sequential primitives
   and sends them as an ordered list.

Both paths exist. Testing will determine which works better for each scenario.

### Location resolution

The executor resolves location strings to `Point2` coordinates via a
`resolve_location()` method on the bot:

| Location string | Resolution |
|----------------|------------|
| `"main"` | `self.start_location` |
| `"natural"` | Closest expansion to start location (cached) |
| `"third"` | Second-closest expansion to start location |
| `"fourth"` | Third-closest expansion |
| `"enemy_main"` | `self.enemy_start_locations[0]` |
| `"enemy_natural"` | `self._enemy_natural()` (already implemented) |
| `"enemy_third"` | Second-closest expansion to enemy start |
| `None` | Action-dependent default (e.g., BUILD defaults to main) |

This reuses the existing `self.expansion_locations_list` from BotAI and the
`_enemy_natural()` cache from the natural-denial improvement.

## Architecture

### New modules

```
src/alpha4gate/
    commands/
        __init__.py          # exports: CommandPrimitive, CommandAction, CommandSource,
                             #          CommandQueue, CommandExecutor, StructuredParser
        primitives.py        # CommandPrimitive, CommandAction, CommandSource, CommandMode
        parser.py            # StructuredParser — parse structured shortcuts
        executor.py          # CommandExecutor — translates primitives into bot actions
        queue.py             # CommandQueue — thread-safe priority queue
        interpreter.py       # Claude NLP interpreter — free text → primitives
        recipes.py           # TECH prerequisite chains (stargate→voidray, etc.)
```

### Shared state bridge

The `CommandQueue` is a **module-level singleton** (same pattern as `ws_manager`
in `api.py`). Both the FastAPI request handlers and the bot's `on_step()` access
the same instance:

```python
# commands/queue.py
_command_queue: CommandQueue | None = None

def get_command_queue() -> CommandQueue:
    """Return the global command queue singleton."""
    global _command_queue
    if _command_queue is None:
        _command_queue = CommandQueue(max_depth=10)
    return _command_queue
```

- `api.py` imports `get_command_queue()` and pushes commands in POST handlers.
- `bot.py` imports `get_command_queue()` and drains commands in `on_step()`.
- Thread-safe: `CommandQueue` uses `threading.Lock` internally (same pattern as
  `_broadcast_queue` in `web_socket.py`).

Similarly, `CommandMode` state and runtime settings (claude interval, lockout
duration, mute toggle) live in a `CommandSettings` singleton:

```python
# commands/primitives.py
@dataclass
class CommandSettings:
    mode: CommandMode = CommandMode.AI_ASSISTED
    claude_interval: float = 30.0
    lockout_duration: float = 5.0
    muted: bool = False

_settings: CommandSettings | None = None

def get_command_settings() -> CommandSettings:
    """Return the global command settings singleton."""
    global _settings
    if _settings is None:
        _settings = CommandSettings()
    return _settings
```

API endpoints read/write settings; bot reads them in `on_step()`.

### Data flow

```
Human (web UI)  ──→  POST /api/commands  ──→  StructuredParser.parse(text)
                                                  │
                                                  ├─ matched? → CommandPrimitive(s) → queue
                                                  │
                                                  └─ no match → return {status: "parsing"}
                                                                  │
                                                                  └─ background task:
                                                                       interpreter.py (Haiku)
                                                                       → CommandPrimitive(s) → queue
                                                                       → broadcast result via /ws/commands
                                                                    OR
                                                                       → broadcast {status: "rejected",
                                                                          reason: "could not parse"}

Claude Advisor  ──→  structured JSON response  ──→  CommandPrimitive(s) → queue

CommandQueue (in on_step)  ──→  drain + expire stale  ──→  CommandExecutor
                                                               │
                                                               ▼
                                                         Bot actions
```

### Human free-text parsing: async with fast feedback

When a human types free text that doesn't match the structured parser:

1. `POST /api/commands` returns **immediately** with
   `{ id, status: "parsing", text: "..." }`.
2. A background `asyncio.Task` calls the interpreter (Claude Haiku, max_tokens=128,
   timeout 5 seconds).
3. On success: primitives are queued and a `{ id, status: "queued", parsed: [...] }`
   event is broadcast via `/ws/commands`.
4. On failure (timeout, unparseable): a `{ id, status: "rejected", reason: "..." }`
   event is broadcast. The UI shows "I don't understand that command" with the
   original text.

This avoids blocking the HTTP response on Claude API latency.

### Integration with existing code

1. **`bot.py` on_step()`** — After `DecisionEngine.evaluate()`, drain `CommandQueue`
   and pass each primitive to `CommandExecutor`. State-changing commands (ATTACK,
   DEFEND) override the decision engine state. Production commands (BUILD, TECH,
   UPGRADE) queue alongside normal macro.

2. **`claude_advisor.py`** — Modify response format. Instead of free-text suggestion,
   Claude returns structured JSON with a list of primitives. The advisor validates
   its own output against the primitive schema before queuing.

3. **`decision_engine.py`** — Add `set_command_override(state, source, duration)`
   method that forces a state for N seconds, with source tracking for audit log.

4. **`api.py`** — New endpoints (schemas below).

5. **`web_socket.py`** — New channel `/ws/commands` broadcasting command events
   (received, parsed, executed, expired, rejected) for UI feedback.

### Decision precedence

When commands interact with the existing decision engine and neural engine:

```
Command override (ATTACK/DEFEND from CommandExecutor)
    ↓ (highest priority — active for override duration)
Neural/Hybrid decision (NeuralDecisionEngine.predict())
    ↓
Rules decision (DecisionEngine.evaluate())
    ↓ (lowest priority)
```

There are two separate timers — do not confuse them:

- **State override duration** (120 game-seconds default): A command like "attack
  enemy natural" forces the `StrategicState` to ATTACK for this duration. After it
  expires (or the next command arrives), the decision engine / neural engine resumes
  control. This applies to ATTACK, DEFEND, and RALLY commands.
- **AI lockout duration** (5 game-seconds default, configurable in UI): In hybrid
  mode, a human command silences Claude for this duration. AI commands are dropped
  during lockout. This is separate from the state override — the state may still be
  overridden while lockout has expired.

While a state override is active, the neural engine still runs but its output is
ignored. When the override expires, the neural engine resumes control (if in NEURAL
or HYBRID decision mode).

The `DecisionEntry` audit log records command overrides with
`reason: "command_override"` and the source (AI/HUMAN).

### Executor contract with bot internals

`CommandExecutor` receives a reference to the `Alpha4GateBot` instance (the `BotAI`
subclass) at construction. It calls bot methods directly:

```python
class CommandExecutor:
    def __init__(self, bot: Alpha4GateBot) -> None:
        self._bot = bot

    async def execute(self, cmd: CommandPrimitive) -> ExecutionResult:
        """Translate a primitive into bot actions. Returns success/failure."""
        ...


@dataclass
class ExecutionResult:
    success: bool               # whether the command was executed
    message: str                # human-readable status ("built stalker", "no idle gateway")
    primitives_executed: int    # how many sub-primitives ran (for chain commands)
```

Execution by action type:

| Action | Bot method(s) called |
|--------|---------------------|
| `BUILD` (unit) | `self._bot.train(unit_type, near=structure)` via idle production |
| `BUILD` (structure) | `self._bot.build(structure_type, near=position)` |
| `EXPAND` | `self._bot.build(UnitTypeId.NEXUS, near=resolved_location)` |
| `DEFEND` | `decision_engine.set_command_override(DEFEND, ...)` + rally army |
| `ATTACK` | `decision_engine.set_command_override(ATTACK, ...)` + set target |
| `SCOUT` | `scout_manager.force_scout(target_position)` |
| `TECH` | Look up recipe in `recipes.py`, execute prerequisite chain |
| `UPGRADE` | `self._bot.research(upgrade_type)` at appropriate structure |
| `RALLY` | Update `_cached_staging_point` to resolved location |

The executor does **not** go through `MacroManager` — it issues direct `BotAI`
commands. This means commanded actions can conflict with macro decisions.
Acceptable for v1: commands are intentional overrides. If testing reveals
problems, we add a "commanded production" queue that `MacroManager` checks
before making its own decisions.

### API endpoint schemas

#### `POST /api/commands`
Submit a text command.

Request:
```json
{ "text": "build stalkers at natural" }
```

Response (structured match):
```json
{
  "id": "a1b2c3d4-...",
  "status": "queued",
  "text": "build stalkers at natural",
  "parsed": [
    {
      "action": "build",
      "target": "stalkers",
      "location": "natural",
      "priority": 5,
      "source": "human"
    }
  ]
}
```

Response (needs Claude parsing):
```json
{
  "id": "a1b2c3d4-...",
  "status": "parsing",
  "text": "maybe we should get some air units going"
}
```

#### `GET /api/commands/history`
Recent command log. In-memory, cleared on game restart.

Response:
```json
{
  "commands": [
    {
      "id": "a1b2c3d4-...",
      "text": "build stalkers at natural",
      "parsed": [{ "action": "build", "target": "stalkers", "location": "natural" }],
      "source": "human",
      "status": "executed",
      "game_time": 245.0,
      "timestamp_utc": "2026-03-30T14:22:01Z"
    }
  ]
}
```

#### `GET /api/commands/mode`
Response:
```json
{ "mode": "hybrid_cmd", "muted": false }
```

#### `PUT /api/commands/mode`
Request:
```json
{ "mode": "ai_assisted" }
```

Response:
```json
{ "mode": "ai_assisted", "queue_cleared": true }
```

Queue is cleared on mode switch.

#### `PUT /api/commands/settings`
Request (all fields optional):
```json
{
  "claude_interval": 45,
  "lockout_duration": 10,
  "muted": true
}
```

Response:
```json
{
  "claude_interval": 45.0,
  "lockout_duration": 10.0,
  "muted": true
}
```

#### `GET /api/commands/primitives`
Lists available actions, targets, and locations for UI autocomplete.

Response:
```json
{
  "actions": ["build", "expand", "defend", "attack", "scout", "tech", "upgrade", "rally"],
  "targets": {
    "build": ["stalkers", "zealots", "immortals", "sentries", "pylon", "gateway", "forge"],
    "tech": ["voidrays", "colossi", "high_templar", "dark_templar", "blink", "charge"],
    "upgrade": ["weapons", "armor", "shields", "blink", "charge"]
  },
  "locations": ["main", "natural", "third", "fourth", "enemy_main", "enemy_natural", "enemy_third"]
}
```

### `/ws/commands` event format

All events include the command `id` for UI correlation:

```json
{ "type": "queued",   "id": "...", "parsed": [...], "source": "human" }
{ "type": "executed", "id": "...", "action": "build", "result": "ok" }
{ "type": "expired",  "id": "...", "reason": "ttl exceeded (60s)" }
{ "type": "rejected", "id": "...", "reason": "could not parse input" }
{ "type": "evicted",  "id": "...", "reason": "queue full, lower priority" }
{ "type": "cleared",  "id": "...", "reason": "human override conflict" }
```

### Web UI additions

New component: `CommandPanel.tsx`
- Text input field for typing commands
- Mode selector dropdown (AI-Assisted / Human / Hybrid)
- Settings panel (Claude interval slider, lockout duration slider)
- "Mute Claude" toggle button (instant on/off for AI commands)
- Command history feed showing:
  - What was typed/suggested
  - How it was parsed (which primitives)
  - Execution status (queued → executed/expired/rejected)
  - Source badge (AI / Human)
- Autocomplete dropdown powered by `GET /api/commands/primitives`
- Error display: "I don't understand that command" for rejected free text
- Integrate into existing `LiveView.tsx` layout

### Claude advisor changes

- Default call interval: 30 game-seconds (configurable via `--claude-interval` CLI
  flag and adjustable in UI at runtime)
- Prompt updated to return structured primitive list instead of free-text advice
- In AI-Assisted mode: commands auto-queue
- In Hybrid mode: commands auto-queue unless human lockout is active
- In Human-only mode: advisor is disabled (no API calls made)
- "Mute Claude" toggle disables advisor without changing mode
- Interpreter uses Claude Haiku (`claude-haiku-4-5-20251001`, fastest/cheapest Claude tier)
  for human free-text parsing
- Advisor uses Claude Sonnet (`claude-sonnet-4-20250514`, mid-tier, better reasoning)
  for strategic advice

### Conflict resolution (hybrid mode)

1. Human command always takes priority over pending/queued AI commands.
2. When a human command is received:
   - Clear any queued AI commands that conflict (same action category).
     Cleared commands are broadcast as `"cleared"` events via `/ws/commands`.
   - Start lockout timer (default 5 game-seconds, configurable in UI).
   - During lockout, AI commands are silently dropped.
3. Non-conflicting AI commands still execute (e.g., human says "defend natural",
   Claude's "upgrade weapons" still goes through).

## Implementation Steps

### Step 1: Command primitives, parser, and queue
- [ ] Create `commands/primitives.py` with `CommandAction`, `CommandSource`,
  `CommandMode` enums, `CommandPrimitive` dataclass (with `id`, `timestamp`,
  `ttl` fields), and `CommandSettings` singleton.
- [ ] Create `commands/parser.py` with `StructuredParser`:
  `parse(text: str, source: CommandSource) -> list[CommandPrimitive] | None`.
  Returns None if no structured match found.
- [ ] Create `commands/queue.py` with thread-safe `CommandQueue`:
  priority ordering, max depth 10, source filtering, conflict clearing,
  TTL expiry on drain. Module-level `get_command_queue()` singleton.
- [ ] Create `commands/recipes.py` with TECH prerequisite chains
  (voidray → stargate, colossus → robo + robo bay, etc.).
- [ ] Create `commands/__init__.py` with public exports.
- [ ] Unit tests for parser (structured commands), queue behavior (overflow,
  expiry, conflict clearing), and recipe expansion.

### Step 2: Command executor and bot integration
- [ ] Create `commands/executor.py` with `CommandExecutor` class. Constructor
  takes `Alpha4GateBot` reference. `execute(cmd) -> ExecutionResult` calls
  bot methods directly (`train`, `build`, `research`, etc.).
- [ ] Implement `resolve_location(location_str) -> Point2` using bot's
  expansion list, start location, and `_enemy_natural()` cache.
- [ ] Implement prerequisite chain execution for TECH commands via `recipes.py`.
- [ ] Add `set_command_override(state, source, duration)` to `DecisionEngine`.
  Override expires after duration or next command. Logged in `DecisionEntry`.
- [ ] Integrate into `bot.py on_step()`: after `evaluate()`, drain queue,
  execute each primitive. Respect `CommandMode` and mute state from
  `CommandSettings`.
- [ ] Define decision precedence: command override > neural/hybrid > rules.
  While override is active, neural engine output is ignored.
- [ ] Unit tests for executor logic, location resolution, chain expansion,
  and override expiry.

### Step 3: Claude interpreter and advisor wiring
- [ ] Create `commands/interpreter.py` that calls Claude Haiku (max_tokens=128,
  5s timeout) to parse free text into `CommandPrimitive` list. Returns None
  on failure.
- [ ] Update `claude_advisor.py` prompt to return structured primitive JSON
  instead of free-text suggestion. Advisor uses Sonnet for strategic reasoning.
- [ ] Wire advisor into command queue: advisor output → validate against
  primitive schema → queue (if mode allows and not muted/locked out).
- [ ] Implement lockout logic: human command sets lockout timestamp, AI commands
  dropped if `game_time < lockout_timestamp + lockout_duration`.
- [ ] Update `RateLimiter` to accept runtime interval changes from
  `CommandSettings.claude_interval`.
- [ ] Unit tests for interpreter parsing, lockout behavior, and mute toggle.

### Step 4: API endpoints and WebSocket
- [ ] Add command REST endpoints to `api.py` per schemas above:
  POST/GET commands, GET/PUT mode, PUT settings, GET primitives.
- [ ] `POST /api/commands`: try structured parse → if match, queue + return
  `"queued"`; if no match, fire background interpreter task + return
  `"parsing"`. On interpreter success/failure, broadcast via WebSocket.
- [ ] Add `/ws/commands` WebSocket channel with event format defined above.
- [ ] Mode switching clears queue and broadcasts `"cleared"` events.
- [ ] Command history: in-memory list, cleared on game restart.
  Sufficient for v1 — no persistence needed.
- [ ] Integration tests for API endpoints and WebSocket events.

### Step 5: Web UI — CommandPanel
- [ ] Create `CommandPanel.tsx` component with text input, mode selector,
  settings sliders, mute toggle.
- [ ] Add command history feed with source badges (AI/Human), status tracking
  (queued → executed/expired/rejected) using command `id` correlation.
- [ ] Add autocomplete dropdown powered by `GET /api/commands/primitives`.
- [ ] Add error display for rejected commands ("I don't understand that command").
- [ ] Integrate CommandPanel into `LiveView.tsx` layout.
- [ ] Connect to `/ws/commands` for real-time status updates.
- [ ] Connect mode/settings/mute controls to REST endpoints.

### Step 6: End-to-end integration and testing
- [ ] Run a game in AI-Assisted mode — verify Claude issues commands and bot executes.
- [ ] Run a game in Human-only mode — verify text commands work, Claude is silent.
- [ ] Run a game in Hybrid mode — verify human override, lockout, and conflict clearing.
- [ ] Test mode switching mid-game (queue cleared, new mode active).
- [ ] Test mute toggle in each mode.
- [ ] Test composable commands (both bot-chain and Claude-decomposed paths).
- [ ] Test free-text fallback (structured parser miss → Haiku interpreter → queue).
- [ ] Test rejection flow (unparseable free text → "rejected" event → UI error).
- [ ] Test command expiry (queue a command, wait > TTL, verify expiry event).
- [ ] Verify batch mode works with AI-Assisted as default.
- [ ] Verify command log appears in game result data for replay analysis.

## Configuration

### CLI flags (runner.py)
- `--command-mode [ai_assisted|human_only|hybrid_cmd]` (default: ai_assisted)
- `--claude-interval SECONDS` (default: 30)
- `--lockout-duration SECONDS` (default: 5)

### Runtime settings (adjustable via UI)
- Command mode
- Claude call interval
- Human lockout duration
- Mute Claude toggle

## Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| Claude API latency blocks game loop | Advisor is async fire-and-forget; interpreter runs as background task, HTTP returns immediately |
| Human free-text parsing latency | Structured parser is first pass (instant); Haiku fallback is async with 5s timeout; UI shows "parsing..." status |
| Free-text parsing fails | Rejected commands broadcast via WebSocket; UI shows error with original text |
| Too many commands overwhelm bot | Queue max depth 10; TTL 60s; low-priority evicted first; all evictions logged |
| Composable chains conflict with macro manager | Executor issues direct BotAI calls, bypassing macro. Acceptable for v1 — commands are intentional overrides |
| Mode switch mid-game loses queued commands | Queue is cleared on mode switch; broadcast "cleared" events; fresh start in new mode |
| Command override conflicts with neural engine | Defined precedence: command > neural > rules. Override has expiry (120s default). Neural resumes after |
| Executor bypasses macro manager | Direct BotAI calls for v1. If conflicts arise in testing, add commanded-production queue that macro checks first |
