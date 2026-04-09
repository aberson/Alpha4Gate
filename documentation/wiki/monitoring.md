# Monitoring & Observability

What the system makes visible, and how.

> **At a glance:** Three WebSocket channels stream live game state, decisions, and
> command events. JSONL files persist per-game logs. A React dashboard shows live view,
> training metrics (5s poll), stats, checkpoints, and command history. Decision logs and
> action probabilities are **ephemeral** — not persisted to disk. Reward logging is
> opt-in. The dashboard observes gameplay well but has almost no visibility into training
> or improvement over time.

## Purpose & Design

Monitoring answers: "What is the system doing right now, and how has it been doing?"

Three data paths exist today:

```
Game Loop (bot.py on_step)
  │
  ├──[every 11 steps]──> observer.py ──> GameLogger thread
  │                                          │
  │                                   ┌──────┴──────┐
  │                                   ▼              ▼
  │                            JSONL file       WebSocket broadcast
  │                          (persistent)         (ephemeral)
  │                         logs/game_*.jsonl       │
  │                                          ┌──────┴──────┐
  │                                          ▼              ▼
  │                                     /ws/game      /ws/commands
  │                                          │              │
  │                                          ▼              ▼
  │                                     LiveView     CommandPanel
  │
  ├──[per game end]──> TrainingDB (SQLite) ──> /api/training/* ──> TrainingDashboard
  │
  └──[per batch run]──> stats.json ──> /api/stats ──> Stats view
```

### What's persistent vs ephemeral

| Data | Storage | Lifetime | Gap |
|------|---------|----------|-----|
| Game state snapshots | `logs/game_*.jsonl` | Permanent (one file per game) | No reward data, no action probabilities |
| Game results | `data/training.db` games table | Permanent | No per-step detail |
| Transitions (s,a,r,s') | `data/training.db` transitions table | Permanent | Only during training, not regular play |
| Batch stats | `data/stats.json` | Permanent | Separate from training DB (not synced) |
| Decision log | In-memory + `data/decision_audit.json` | Session / file | Only state transitions, not every step |
| Live game state | WebSocket `/ws/game` | Ephemeral (lost on disconnect) | Not archived |
| Command events | WebSocket `/ws/commands` | Ephemeral | In-memory history only |
| Action probabilities | `NeuralDecisionEngine._last_probabilities` | Ephemeral (memory) | Never persisted anywhere |
| Reward breakdown | `data/reward_log.jsonl` | Permanent **if** `--reward-log` flag used | Off by default |
| Training diagnostics | `data/training_diagnostics.json` | Permanent | Not surfaced in dashboard |

### Gaps

> These feed directly into [Phase 2 of the always-up plan](../plans/always-up-plan.md).

- **Decision data is ephemeral.** Action probabilities from the neural engine exist only
  in memory (`_last_probabilities`). WebSocket consumers see them briefly; nothing
  persists them. A future session can't analyze how the model's decisions evolved.
- **Reward logging is opt-in.** Must pass `--reward-log` flag. Without it,
  `analyze_rewards.py` has nothing to read. Should be default.
- **Training diagnostics aren't in the dashboard.** `training_diagnostics.json` has
  per-cycle action distributions on test states — valuable for tracking improvement —
  but no frontend component renders it.
- **No improvement timeline.** The dashboard shows current win rates but not how they
  changed over time. No charts, no trend lines, no "model v3 vs v4" comparison.
- **No alerting.** Nothing detects performance regression, training failure, or disk
  pressure. A human has to check manually.

### Disk usage note

When reward JSONL logging becomes always-on (Phase 2, step 3), disk usage increases
proportionally to games played. Each game generates a per-game JSONL file in
`data/reward_logs/`. The training DB already has a 200 GB disk guard, but reward logs
do not. Disk management (log rotation, compression, max file age, cleanup scripts) is
deferred to a later phase. Monitor `data/reward_logs/` size manually for now.

---

## Key Interfaces

> **At a glance:** Observer extracts game state every 11 steps. GameLogger writes JSONL
> in a background thread and feeds the WebSocket broadcast queue. FastAPI drains both
> queues every 500ms. Three WS endpoints, ~15 REST endpoints. Frontend has 9 components.

### Data collection: Observer

Entry point: `observe(bot: BotAI, actions_taken: list[dict])` → `dict`

Called every 11 game steps (~0.5 real seconds at 1x speed) from `bot.py.on_step()`.
Extracts a snapshot dict:

```python
{
    "timestamp": "2026-04-09T10:30:45.123456+00:00",  # UTC ISO 8601
    "game_step": 1250,
    "game_time_seconds": 87.5,
    "minerals": 450,
    "vespene": 120,
    "supply_used": 45,
    "supply_cap": 60,
    "units": [{"type": "Stalker", "count": 8}, ...],       # descending by count
    "structures": [{"type": "Gateway", "count": 2}, ...],   # descending by count
    "actions_taken": [{"action": "Build", "target": "Stalker"}],
    "score": 2840,
    "strategic_state": "attack",
    "coherence_params": {...},       # first snapshot only
    "claude_advice": "Build 2 more gateways"  # if advice pending, else null
}
```

### Logging: GameLogger

Background thread that drains a queue and writes JSONL.

**Lifecycle:**
1. `start()` — creates thread, opens `logs/game_<TIMESTAMP>.jsonl`
2. `put(entry)` — thread-safe enqueue (called from observer)
3. Thread loop: `queue.get(timeout=0.1)` → deduplicate by `game_step` → write JSON line → flush → broadcast to WebSocket queue
4. `stop()` — sends sentinel, joins thread (5s timeout)

**Deduplication:** Tracks `_last_step`. If incoming `game_step <= _last_step`, the entry
is silently dropped. Prevents duplicate writes if the game loop re-enters.

**Two output paths from the same entry:**
1. File write → `logs/game_<TIMESTAMP>.jsonl` (permanent)
2. Callback → `queue_broadcast(entry)` → WebSocket broadcast queue (ephemeral)

### WebSocket: broadcast loop

`_game_state_broadcast_loop()` in `api.py` — runs as a FastAPI background task:

```python
while True:
    # Drain game state entries
    entries = drain_broadcast_queue()       # non-blocking, returns list
    for entry in entries:
        await ws_manager.broadcast_game_state(entry)
    
    # Drain command events
    cmd_events = drain_command_event_queue()
    for event in cmd_events:
        await ws_manager.broadcast_command_event(event)
        # Update in-memory _command_history by matching event ID
    
    await asyncio.sleep(0.5)  # 500ms polling interval
```

**Queue architecture:** Two `queue.Queue` instances in `web_socket.py`:
- `_broadcast_queue` — game state entries from logger thread
- `_command_event_queue` — command execution results from bot.on_step()

Both are drained via `get_nowait()` in a loop (non-blocking). This bridges the
synchronous game thread to the async FastAPI event loop.

### WebSocket endpoints

**`/ws/game`** — Live game state
- Broadcasts observer snapshots every ~500ms
- Message format: the full observer dict (see above)
- Frontend: `useGameState()` hook → `LiveView` component

**`/ws/commands`** — Command execution events
- Event types: `queued`, `executed`, `failed`, `rejected`, `cleared`
- Message format varies by type:
  ```json
  {"type": "queued",   "id": "uuid", "parsed": [...], "source": "human"}
  {"type": "executed", "id": "uuid", "reason": "built 1 Stalker"}
  {"type": "failed",   "id": "uuid", "reason": "not enough minerals"}
  {"type": "rejected", "id": "uuid", "reason": "could not parse input"}
  ```
- Frontend: `CommandPanel` updates history entries by matching `id`

**`/ws/decisions`** — Decision log events
- **Currently unused.** The endpoint exists and accepts connections, but no code
  actively broadcasts to it. Decisions are served via REST (`/api/decision-log`)
  instead. This is a gap — live decision streaming is wired up but not connected.

### ConnectionManager

Three separate connection lists (`web_socket.py`):
```python
_game_connections: list[WebSocket]
_decision_connections: list[WebSocket]
_command_connections: list[WebSocket]
```

Each has `connect_*()`, `disconnect_*()`, and `broadcast_*()` methods. Broadcasts
serialize to compact JSON and silently remove disconnected clients.

### REST endpoints

**Game:**
| Endpoint | Method | Returns |
|----------|--------|---------|
| `/api/status` | GET | Current game state (placeholder — returns nulls) |
| `/api/stats` | GET | `data/stats.json` — game records + aggregates |
| `/api/decision-log` | GET | Decision state transitions from `data/decision_audit.json` |

**Training:**
| Endpoint | Method | Returns |
|----------|--------|---------|
| `/api/training/status` | GET | Checkpoint count, game/transition count, DB size |
| `/api/training/history` | GET | Win rates: last 10/50/100/overall |
| `/api/training/checkpoints` | GET | Checkpoint list from manifest.json |
| `/api/training/start` | POST | **Not implemented** (placeholder) |

**Commands:**
| Endpoint | Method | Returns |
|----------|--------|---------|
| `/api/commands` | POST | Submit command text → parse → queue |
| `/api/commands/history` | GET | In-memory command history |
| `/api/commands/mode` | GET/PUT | AI-assisted / human-only / hybrid |
| `/api/commands/settings` | GET/PUT | Claude interval, lockout duration, muted |
| `/api/commands/primitives` | GET | Available actions, targets, locations (vocabulary) |

**Other:**
| Endpoint | Method | Returns |
|----------|--------|---------|
| `/api/build-orders` | GET/POST | Build order CRUD |
| `/api/replays` | GET | Replay file listing |
| `/api/reward-rules` | GET/PUT | Reward rules from `data/reward_rules.json` |

### Command flow (end to end)

```
User types "build stalkers" in CommandPanel
  │
  ├─ POST /api/commands {"text": "build stalkers"}
  │   ├─ Fast path: regex parser succeeds
  │   │   └─ Broadcast "queued" event + add to _command_history
  │   └─ Slow path: regex fails → background task → Claude Haiku interpreter
  │
  ├─ Command pushed to global command queue
  │
  ├─ bot.on_step() drains queue, executes command
  │   └─ Result queued to _command_event_queue
  │
  ├─ Broadcast loop drains _command_event_queue
  │   └─ Broadcasts "executed" or "failed" event to /ws/commands
  │
  └─ Frontend CommandPanel updates history entry status
```

---

## Implementation Notes

> **At a glance:** 9 React components, 3 WebSocket hooks, 5s training poll, 3s WS
> reconnect. Observer runs in game thread, logger in its own thread, broadcast loop
> in async FastAPI. Three separate threads touching two queues.

> Verify against code before relying on exact signatures.

### Frontend components

| Component | Data Source | Refresh | What it shows |
|-----------|-----------|---------|---------------|
| **LiveView** | `/ws/game` | Real-time | Game time, minerals, gas, supply, score, strategic state, units, structures, Claude advice |
| **CommandPanel** | `/ws/commands` + REST | Real-time + initial fetch | Text input with autocomplete, command history (last 20), mode selector, mute toggle, settings |
| **TrainingDashboard** | `/api/training/*` | 5s poll | Current checkpoint, total games, transitions, DB size, win rates (10/50/100/overall) |
| **CheckpointList** | `/api/training/checkpoints` | One-time fetch | Table: name, type, metadata details, best indicator |
| **DecisionQueue** | `/api/decision-log` + `/ws/decisions` | Initial fetch + live | Last 20 state transitions: step, from, to, reason, Claude advice |
| **Stats** | `/api/stats` | One-time fetch | Total wins/losses, by-map breakdown, last 10 games table |
| **BuildOrderEditor** | `/api/build-orders` | One-time fetch | Build order list with steps, create/delete |
| **ReplayBrowser** | `/api/replays` | One-time fetch | Replay file list, stats on click (placeholder) |
| **RewardRuleEditor** | `/api/reward-rules` | One-time fetch + manual save | Table: rule ID, description, reward value (editable), active toggle |

### Timing constants

| What | Interval | Where |
|------|----------|-------|
| Observer snapshot | Every 11 game steps (~0.5s at 1x) | bot.py |
| Logger write | As fast as queue drains | logger.py thread |
| Broadcast loop drain | 500ms | api.py |
| Training dashboard poll | 5000ms | TrainingDashboard.tsx |
| WebSocket reconnect | 3000ms | useWebSocket.ts |
| Command error toast | 5000ms display | CommandPanel.tsx |

### Thread safety model

Three threads interact via two queues:

```
Game Thread (bot.py)          Logger Thread (logger.py)        Async Loop (api.py)
  │                                │                               │
  ├─ observer.observe()            │                               │
  │   └─ logger.put(entry) ──>  queue.get()                       │
  │                                ├─ write JSONL                  │
  │                                └─ queue_broadcast() ──>  drain_broadcast_queue()
  │                                                                ├─ ws_manager.broadcast()
  │                                                                │
  ├─ queue_command_event() ─────────────────────────────>  drain_command_event_queue()
  │                                                                └─ broadcast command event
```

All cross-thread communication uses `queue.Queue` (thread-safe). The async broadcast
loop uses `get_nowait()` to avoid blocking the event loop.

### JSONL format

One line per snapshot in `logs/game_<TIMESTAMP>.jsonl`:
```json
{"timestamp":"2026-04-09T10:30:45.123Z","game_step":1250,"game_time_seconds":87.5,"minerals":450,"vespene":120,"supply_used":45,"supply_cap":60,"units":[{"type":"Stalker","count":8}],"structures":[{"type":"Gateway","count":2}],"actions_taken":[],"score":2840,"strategic_state":"attack"}
```

Compact JSON (no spaces). One file per game session. Deduplicated by `game_step`.

### Key file locations

| File | Purpose |
|------|---------|
| `src/alpha4gate/observer.py` | Extract game state dict from BotAI |
| `src/alpha4gate/logger.py` | GameLogger — background thread JSONL writer |
| `src/alpha4gate/web_socket.py` | ConnectionManager + broadcast/command queues |
| `src/alpha4gate/api.py` | FastAPI app — REST endpoints, WS handlers, broadcast loop |
| `frontend/src/components/LiveView.tsx` | Real-time game state display |
| `frontend/src/components/CommandPanel.tsx` | Command input, history, mode/settings |
| `frontend/src/components/TrainingDashboard.tsx` | Training metrics (5s poll) |
| `frontend/src/components/CheckpointList.tsx` | Checkpoint table |
| `frontend/src/components/DecisionQueue.tsx` | Decision state transition log |
| `frontend/src/components/Stats.tsx` | Game statistics |
| `frontend/src/components/BuildOrderEditor.tsx` | Build order CRUD |
| `frontend/src/components/ReplayBrowser.tsx` | Replay listing (stub) |
| `frontend/src/components/RewardRuleEditor.tsx` | Reward rule editing |
| `logs/` | JSONL game logs (gitignored) |
| `data/decision_audit.json` | Persisted decision log |
| `data/stats.json` | Cross-game aggregates |
