# UI Live Test Plan

## Goal
Verify end-to-end that the frontend UI works during live SC2 games:
commands from the browser reach the bot and execute in-game, and live game
state streams back to the frontend via WebSocket.

## Prerequisites
- SC2 client installed and available (Phase 3 only)
- Node.js / npm for frontend dev server (Phase 3 only)
- All current uncommitted fixes committed (runner.py in-process server,
  API TTL fix, game-state broadcast)

## Architecture under test

```
Browser (localhost:5173)
  ├── POST /api/commands  → API → CommandQueue → bot.on_step drains → executor
  └── WS /ws/game         ← API ← drain_broadcast_queue ← bot.on_step → queue_broadcast
                           [game thread]                    [API daemon thread]
```

The bot's game loop runs in the main thread. The API runs in a daemon thread
started by `_start_server_background()`. The `_broadcast_queue` (threading.Queue)
bridges the two threads.

---

## Phase 1 — Commit fixes & write automated integration tests

These steps run via `/phase-runner` (no SC2 needed). They use FastAPI TestClient
and mock only the SC2 BotAI, not internal components.

### Step 1: Commit current fixes + migrate deprecated startup event
- **Problem:** Three uncommitted changes (runner.py, api.py, bot.py) and one
  untracked file (scripts/live-test.sh) need to be committed. Before committing,
  migrate `@app.on_event("startup")` in api.py to a FastAPI `lifespan` context
  manager — `on_event` is deprecated in FastAPI 0.135.2 and will break in a
  future release. The lifespan should start the `_game_state_broadcast_loop`
  task and yield.
- **Issue:** —
- **Acceptance:** `git status` clean, all 505 tests still pass,
  no `on_event` usage in api.py.

### Step 2: Test API command TTL (commands survive drain)
- **Problem:** Write a test in `tests/test_command_integration.py` that verifies
  commands submitted via the API (with `ttl=float("inf")`) are never expired by
  `drain()`, even at high game times (e.g., game_time=600). Contrast with a
  game-time-stamped command that does expire. Also test the drain-to-execute
  path: push a command via `get_command_queue().push()`, call a mocked bot's
  command drain logic, and assert the `CommandExecutor` receives the command.
  Mock only `BotAI`, not internal command components.
- **Issue:** —
- **Files:** `tests/test_command_integration.py`
- **Acceptance:** New tests pass. Existing tests still pass.

### Step 3: Test game-state broadcast pipeline
- **Problem:** Write tests in `tests/test_web_socket.py` that verify the full
  pipeline: `queue_broadcast(entry)` → `drain_broadcast_queue()` → entries
  contain the expected game state fields (`game_time_seconds`, `minerals`,
  `vespene`, `supply_used`, `supply_cap`, `units`, `strategic_state`).
  Also verify the lifespan handler is registered on `app` (check that
  `app.router.lifespan_context` is set).
- **Issue:** —
- **Files:** `tests/test_web_socket.py`
- **Acceptance:** New tests pass. Full suite still green.

### Step 4: Test WebSocket game endpoint receives broadcasts
- **Problem:** Write an async integration test that:
  1. Connects a TestClient WebSocket to `/ws/game`
  2. Calls `queue_broadcast()` with a sample game state entry
  3. Calls `_drain_and_broadcast_once()` directly (extracted from the loop)
     to avoid the 500ms sleep race condition
  4. Asserts the WebSocket client receives the JSON message
  To enable this, extract the drain-and-broadcast logic from
  `_game_state_broadcast_loop` into a standalone coroutine
  `_drain_and_broadcast_once()` that both the loop and tests can call.
  Use FastAPI TestClient WebSocket support.
- **Issue:** —
- **Files:** `src/alpha4gate/api.py`, `tests/test_web_socket.py`
- **Acceptance:** New test passes. Verifies the data path from bot thread to
  WebSocket client without needing SC2.

### Step 5: Test in-process server starts correctly
- **Problem:** Write a test in `tests/test_runner.py` that verifies
  `_start_server_background()` starts uvicorn in a daemon thread and the API
  becomes reachable (GET `/api/commands/mode` returns 200).
  `load_settings()` requires `SC2PATH` to exist — mock it or set the env var
  to a temp directory. Use a `Settings` object with a random free port
  (bind-then-close trick: `s = socket(); s.bind(("", 0)); port = s.getsockname()[1]; s.close()`).
  After the test, there is no clean way to stop the daemon thread — document
  this limitation and mark the test as needing process isolation if it causes
  flakiness (e.g., `@pytest.mark.forked` or skip in CI).
- **Issue:** —
- **Files:** `tests/test_runner.py`
- **Acceptance:** New test passes. Server thread is a daemon (won't block exit).

---

## Phase 2 — Live smoke test script (automated by phase-runner, no SC2 needed to write)

Phase-runner writes the script; the user executes it during a live game in Phase 3.

### Step 6: Write automated live-smoke-test script
- **Problem:** Create `scripts/live-smoke.py` — a Python script that:
  1. Waits for the API to be reachable (polls `GET /api/commands/mode`,
     max 30s timeout, 1s polling interval)
  2. Connects to `ws://localhost:8765/ws/game` using the `websockets` library
     with a 5s read timeout and waits for the first message (this signals the
     game loop is running — do not send commands before this)
  3. Sends `POST /api/commands` with `{"text": "build stalkers"}` and asserts
     status is `"queued"` or `"parsing"`
  4. Sends `POST /api/commands` with `{"text": "attack natural"}` and asserts
     status is `"queued"` or `"parsing"`
  5. Switches mode via `PUT /api/commands/mode` to each of the 3 modes and
     back to `ai_assisted`
  6. Collects 5 WebSocket game-state messages and prints a summary
     (game time, minerals, supply for each)
  7. Checks `GET /api/commands/history` has entries
  8. Prints PASS/FAIL for each check with a final summary line
  The WS client must use a read timeout (5s) since the server's `/ws/game`
  handler blocks on `receive_text()` for disconnect detection.
  The script should be runnable standalone (`uv run python scripts/live-smoke.py`)
  while a game is in progress via `live-test.sh`.
  Use only stdlib + `httpx` + `websockets` (both already in pyproject.toml).
- **Issue:** —
- **Files:** `scripts/live-smoke.py`
- **Acceptance:** Script runs without import errors. All checks produce clear
  PASS/FAIL output. (Actual PASS requires a running game — verified in Phase 3.)

---

## Phase 3 — Manual live verification (human-run, not phase-runner)

These steps require the SC2 client, a browser, and a human at the keyboard.
Phase-runner does NOT run this phase.

### Step 7: Live game + smoke script
- **Problem:** Run a live game and execute the smoke script against it:
  ```bash
  # Terminal 1: start game + frontend + API
  bash scripts/live-test.sh

  # Terminal 2: run smoke tests once game is loaded
  uv run python scripts/live-smoke.py
  ```
- **Acceptance:** Smoke script reports all PASS.

### Step 8: Browser visual verification
- **Problem:** While a game is running (via `scripts/live-test.sh`), verify
  in the browser at http://localhost:5173:
  - [ ] LiveView shows game stats (minerals, vespene, supply) — not "Waiting for game data..."
  - [ ] CommandPanel accepts text input and shows commands in history
  - [ ] "build stalkers" command causes gateway production in-game
  - [ ] "attack natural" command sends army toward enemy natural
  - [ ] Mode switch to `human_only` stops AI commands
  - [ ] Mode switch back to `ai_assisted` resumes AI commands
  - [ ] Mute toggle works
- **Acceptance:** All checklist items verified visually.

---

## Build order table

| Phase | Step | Name                              | Method | Issue |
|-------|------|-----------------------------------|--------|-------|
| 1     | 1    | Commit fixes + migrate lifespan   | —      | —     |
| 1     | 2    | Test API command TTL + drain path | DIRECT | —     |
| 1     | 3    | Test broadcast pipeline           | DIRECT | —     |
| 1     | 4    | Test WS game endpoint             | DIRECT | —     |
| 1     | 5    | Test in-process server            | DIRECT | —     |
| 2     | 6    | Write live-smoke script           | DIRECT | —     |
| 3     | 7    | Live game + smoke script          | MANUAL | —     |
| 3     | 8    | Browser visual verification       | MANUAL | —     |

## Bug fix: HUMAN_ONLY mode blocks all commands (found during Phase 3)

**Root cause:** Commit 500d168 fixed `or` → `and` in the drain gate (`bot.py:204`),
which correctly stopped the previous bug (wrong operator) but went too far — it
blocked ALL commands in HUMAN_ONLY mode, including human-sourced ones.

**Fix:** Extracted `filter_executable()` into `commands/primitives.py`. The drain gate
now only checks `muted`; mode-based filtering happens per-command via `filter_executable()`.
In HUMAN_ONLY mode, AI commands are dropped while human commands pass through.

**Files changed:**

| File | Change |
|---|---|
| `src/alpha4gate/commands/primitives.py` | Added `filter_executable()` function |
| `src/alpha4gate/commands/__init__.py` | Export `filter_executable` |
| `src/alpha4gate/bot.py` | Simplified drain gate, calls `filter_executable()` |
| `tests/test_command_integration.py` | 3 new tests for `filter_executable()` |

**Also:** Observation interval changed from 22 to 11 steps (~0.5s instead of ~1s).

**Validation:** rwl-full (Reviewer-Writer Loop with 7 parallel review agents) PASS
(4 code reviewers + 3 runtime reviewers).

---

## Bug fixes found during Phase 3 manual testing

### Fix 1: Command execution feedback not visible

Commands appeared in history as "queued" but never showed execution results.

**Root cause:** `bot.on_step()` executed commands but never broadcast results back to
the frontend. The executor returned `success=False` with generic messages.

**Fix:**
- Added `queue_command_event()` in `web_socket.py` — thread-safe queue for bot→API
- `bot.py` now broadcasts `executed`/`failed` events after each command
- `api.py` drains command events in the broadcast loop, updates history status
- `executor.py` failure messages now include specific details (e.g., "No Gateway built"
  vs "No idle GATEWAY available")
- `CommandPanel.tsx` displays execution status and failure reasons
- `game.ts` added `"failed"` event type and `message` field

### Fix 2: Claude advisor never instantiated

AI-Assisted mode never issued commands because `ClaudeAdvisor` was never created.

**Root cause:** `runner.py` defined `--no-claude` flag but never used it. Bot was
created without passing a `claude_advisor` argument.

**Fix:** `runner.py` now creates `ClaudeAdvisor(api_key=settings.anthropic_api_key)`
in `_run_single_game()` and `_run_batch()` when API key is available and `--no-claude`
is not set.

### Fix 3: Dashboard missing structure/building counts

The dashboard showed unit counts but not buildings.

**Root cause:** `observer.py` extracted unit counts but never structures. The frontend
`GameState.structures` field existed but was never populated.

**Fix:**
- `observer.py` now counts structures using the same `Counter` pattern as units
- `LiveView.tsx` renders a "Structures" section when data is present
- `test_observer.py` has 2 new tests for structure counting

### Fix 4: Vite WebSocket proxy target protocol

**Root cause:** `vite.config.ts` used `ws://` as the proxy target for `/ws`. Vite's
`http-proxy` handles WS upgrade internally and expects `http://`.

**Fix:** Changed proxy target from `ws://localhost:8765` to `http://localhost:8765`.

**All fixes validated:** 521/521 tests passing. Zero lint violations. Manual test with
SC2 confirmed commands execute in-game and dashboard shows live data.

### Fix 5: Duplicate command history entries and wrong sort order

Command history showed each command multiple times and did not display most recent
commands first.

**Root causes (3):**
1. **POST + WebSocket race:** `submit_command` in `api.py` awaits the WS broadcast
   *before* returning the HTTP response, so the frontend WebSocket handler adds the
   entry first, then `submitCommand` adds a duplicate (no dedup check).
2. **WebSocket reconnect leak:** `useWebSocket.ts` effect cleanup calls `ws.close()`,
   triggering `onclose`, which schedules a reconnect timer. React StrictMode's
   double mount/cleanup cycle spawns extra WS connections, multiplying every event.
3. **Sort order:** `history.slice(-20).reverse()` relied on insertion order, which is
   non-deterministic when WS and POST responses race.

**Fix:**
- `CommandPanel.tsx` `submitCommand`: added `prev.some(e => e.id === data.id)` dedup
  check before adding entries from POST responses
- `useWebSocket.ts`: added `closingRef` flag to suppress reconnect during intentional
  cleanup close
- `CommandPanel.tsx` render: replaced `slice(-20).reverse()` with explicit sort by
  `timestamp_utc` descending, then `slice(0, 20)`

---

## Quality gates
- `uv run pytest --tb=no -q` — all tests pass (run after Phase 1 Step 5 and Phase 2 Step 6)
- `uv run ruff check .` — clean
- `uv run mypy src` — clean (existing baseline)
