# Phase 3 Build Plan — Subprocess self-play runner

**Parent plan:** [alpha4gate-master-plan.md](alpha4gate-master-plan.md) — Phase 3 (line numbers stale after 2026-04-19 refactor; search "Phase 3 — Subprocess self-play")
**Parent issue:** #109
**Branch:** `master-plan/3/selfplay-runner` (cut from master at `f26bb55`)
**Baseline tag:** `master-plan/3/baseline`
**Target tag on gate pass:** `master-plan/3/final`
**Effort estimate:** 4–6 h.

## 1. What this feature does

Fills `src/orchestrator/selfplay.py` with a batch self-play runner that
spawns two bot subprocesses per game, alternates P1/P2 seats across the
batch, records per-game results to `data/selfplay_results.jsonl`, and
optionally samples opponents via PFSP-lite weighting. Also adds crash
handling (subprocess timeout → draw, no orphan SC2 processes) and a CLI
wrapper at `scripts/selfplay.py`.

After this phase, a developer can run:

    python scripts/selfplay.py --p1 v0 --p2 v0 --games 20 --map Simple64

and get a well-formed JSONL results file with alternating seats and
automatic crash cleanup.

## 2. Existing context (for fresh-context models)

- **`src/orchestrator/selfplay.py`** — stub (docstring only, no code).
  This is what we fill.
- **`scripts/spike_subprocess_selfplay.py`** — Phase 0 spike that proved
  subprocess self-play works. Contains the `Portconfig.contiguous_ports`
  monkey-patch for port-collision, `BotProcess` construction, and
  `a_run_multiple_games` call. This is the source of truth for the
  orchestration pattern.
- **`scripts/spike_bot_stub.py`** — minimal bot stub used by the spike.
  No longer needed for Phase 3; the real bot entry point is
  `python -m bots.v0`.
- **`bots/v0/__main__.py`** — full ladder entry point. Already accepts
  `--role {p1|p2|solo}`, `--GamePort`, `--LadderServer`, `--StartPort`,
  `--result-out`, `--map`. The `_run_ladder()` path creates an
  `Alpha4GateBot`, reconstructs the shared `Portconfig`, and joins via
  `play_from_websocket`. This is the bot-side contract Phase 3 invokes.
- **`src/orchestrator/contracts.py`** — frozen dataclasses:
  `BotSpawnArgs`, `MatchResult` (with `to_json()`/`from_json()`),
  `Role`, `Outcome`. `MatchResult` has fields: `version`, `match_id`,
  `outcome`, `duration_s`, `error`.
- **`src/orchestrator/registry.py`** — `list_versions()`,
  `current_version()`, `get_version_dir()`, `get_data_dir()`. All via
  pathlib (no imports of `bots.*`).
- **`documentation/wiki/subprocess-selfplay.md`** — documents the
  burnysc2 ladder protocol, port-collision workaround, bot CLI contract,
  and known issues (on_end not always firing, Python 3.14 asyncio).
- **943 unit tests passing**, mypy strict (62 files), ruff clean on master.

**Critical constraints:**
- Do NOT import `bots.current` or `bots.<version>` from
  `src/orchestrator/` — triggers MetaPathFinder loop.
- The port-collision monkey-patch MUST be applied before any
  `Portconfig.contiguous_ports` call. It's safe to apply multiple times
  (idempotent blocklist).
- `a_run_multiple_games` is async; use `asyncio.run()` from the sync
  entry point (not the deprecated `get_event_loop()`).
- SC2 must be installed for integration tests. Unit tests mock the SC2
  layer.

## 3. Scope

**In scope:**

- Fill `src/orchestrator/selfplay.py` with batch runner, port-collision
  patch, seat alternation, crash handling, PFSP-lite sampler.
- Create `scripts/selfplay.py` CLI wrapper.
- Extend `src/orchestrator/contracts.py` with `SelfPlayResult` if the
  existing `MatchResult` doesn't cover the per-game JSONL schema (it may
  need `p1_version`, `p2_version`, `winner`, `map`, `timestamp`, `seat`
  fields beyond what `MatchResult` has).
- Tests: `tests/test_selfplay.py`, `tests/test_pfsp_sampling.py`.
- Update `src/orchestrator/__main__.py` to add a `selfplay` subcommand
  (optional — the `scripts/selfplay.py` CLI may suffice).

**Out of scope:**

- Transition hand-off to per-version `training.db` (master plan item 7).
  This requires changes to `bots/v0/__main__.py`'s ladder path to pass
  `--training-db` and record transitions during ladder games. It's a
  follow-on task after the core runner works.
- Elo ladder / promotion gate (Phase 4).
- Sandbox enforcement (Phase 5).
- Daemon integration (wiring self-play into the daemon's idle loop).

## 4. Impact analysis

| Area | Files | Nature of change |
|------|-------|------------------|
| `src/orchestrator/selfplay.py` | 1 file | **Fill** stub with batch runner |
| `src/orchestrator/contracts.py` | 1 file | **Extend** with `SelfPlayRecord` |
| `scripts/selfplay.py` | 1 file | **Create** CLI wrapper |
| `tests/test_selfplay.py` | 1 file | **Create** batch runner tests |
| `tests/test_pfsp_sampling.py` | 1 file | **Create** PFSP-lite sampler tests |

## 5. Design decisions

### 5.1 Port-collision patch moves to selfplay.py

The monkey-patch from `scripts/spike_subprocess_selfplay.py` (lines 51–74)
moves into `selfplay.py` as `_install_port_collision_patch()`. It's
idempotent (re-applying is safe) and must run before any
`Portconfig.contiguous_ports` call. The spike script is left as-is for
historical reference.

### 5.2 Games run one-at-a-time within the batch

Each game in the `--games N` batch uses two parallel subprocesses (two SC2
instances, two bot processes playing against each other simultaneously —
the Phase 0 architecture). However, the *batch* is serialized: game 1
finishes before game 2 starts. Running multiple games concurrently (e.g.
4 games = 8 SC2 instances + 8 bot subprocesses) would drastically increase
port-collision risk and memory pressure. At ~20–30s per game (Phase 0
measured 22.4s), a 20-game batch takes ~8–10 minutes — acceptable for the
current scale.

### 5.3 Seat alternation is index-based

Even-indexed games (0, 2, 4, …): `--p1` plays as player 1, `--p2` as
player 2. Odd-indexed games: seats swap. This neutralizes spawn-side bias
without randomization (deterministic for reproducibility).

### 5.4 PFSP-lite sampling is a pure function

`pfsp_sample(pool: list[str], win_rates: dict[str, float]) -> str` returns
a version string. Weight = `(1 - win_rate)^p` where `p` is a temperature
parameter (default 1.0). Win-rate-0 opponents get zero weight. Cold-start
(no win-rate data) → uniform sampling. The sampler is stateless and tested
independently of SC2.

### 5.5 Crash handling via subprocess timeout

Each `a_run_multiple_games` call has a `game_time_limit` (from the master
plan: same as the spike's 300s). Additionally, wrap the async call with
`asyncio.wait_for(timeout=...)` for a hard wall-clock limit (e.g. 600s)
that catches hangs beyond game-time. On timeout, the game is recorded as
a draw with `outcome="crash"` and an error message. burnysc2's
`KillSwitch.kill_all()` handles SC2 process cleanup on normal exit; for
hard timeouts, we additionally call it explicitly.

### 5.6 Result schema extends MatchResult

The per-game JSONL line needs both players' versions and the seat
assignment, which `MatchResult` (version, match_id, outcome, duration_s,
error) doesn't capture. Add a `SelfPlayRecord` dataclass:

```python
@dataclass(frozen=True)
class SelfPlayRecord:
    match_id: str          # UUID
    p1_version: str        # e.g. "v0"
    p2_version: str        # e.g. "v1"
    winner: str | None     # version string or None for draw/crash
    map_name: str
    duration_s: float
    seat_swap: bool        # True if p1/p2 were swapped from CLI order
    timestamp: str         # ISO 8601 UTC
    error: str | None      # non-null only on crash
```

### 5.7 BotProcess construction mirrors the spike

Each player is a `BotProcess` with `launch_list` pointing at
`sys.executable` + `"-m"` + `"bots.<version>"` + ladder flags. The
`path` arg is the repo root. The `--result-out` flag points to a
temp file so per-bot results can be collected after the game.

### 5.8 Version existence validated before batch starts

Before launching any games, verify both `--p1` and `--p2` versions exist
in the registry (`list_versions()`). Fail fast with a clear error rather
than discovering a missing version mid-batch.

## 6. Build steps

### Step 3.1: PFSP-lite sampler + tests
- **Status:** pending
- **Problem:** Create `pfsp_sample(pool, win_rates, temperature)` as a
  pure function in `src/orchestrator/selfplay.py`. Weight formula:
  `w_i = (1 - wr_i)^temperature`. Zero-WR opponents get zero weight.
  Empty pool raises `ValueError`. Cold-start (missing WR data) → uniform.
  Create `tests/test_pfsp_sampling.py` with cases: normal weighting,
  zero-WR exclusion, cold-start uniform, single-opponent pool, empty pool
  error, temperature=0 (uniform).
- **Issue:** #109
- **Flags:** `--reviewers auto`
- **Produces:** partial `src/orchestrator/selfplay.py` (sampler only),
  new `tests/test_pfsp_sampling.py`.
- **Done when:** new tests pass, 943 existing tests still pass, mypy
  strict green, ruff green.

### Step 3.2: Core batch runner + seat alternation + result recording
- **Status:** pending
- **Problem:** Fill `src/orchestrator/selfplay.py` with:
  (a) `_install_port_collision_patch()` — moved from spike script,
  idempotent.
  (b) `SelfPlayRecord` dataclass in `src/orchestrator/contracts.py` per
  design 5.6.
  (c) `run_batch(p1: str, p2: str, games: int, map_name: str, ...) ->
  list[SelfPlayRecord]` — the core batch runner. For each game:
  validate versions exist, construct two `BotProcess` entries with
  seat alternation (design 5.3), run via `a_run_multiple_games`,
  parse results, append to `data/selfplay_results.jsonl`, return
  the full list.
  (d) Result parsing: map burnysc2's `Result.Victory`/`Result.Defeat`
  to winner version string. `Result.Tie` or unexpected → draw.
  Create `tests/test_selfplay.py` with cases: batch of 4 games
  produces 4 records, seats alternate correctly, results are valid
  `SelfPlayRecord` JSON lines, version validation rejects unknown
  versions. Use mocking to avoid SC2 dependency in unit tests (mock
  `a_run_multiple_games` to return deterministic results).
- **Issue:** #109
- **Flags:** `--reviewers auto`
- **Produces:** updated `src/orchestrator/selfplay.py`,
  updated `src/orchestrator/contracts.py`, new `tests/test_selfplay.py`.
- **Done when:** new tests pass, all existing tests still pass, mypy
  strict green, ruff green.
- **Depends on:** 3.1.

### Step 3.3: Crash handling + subprocess timeout
- **Status:** pending
- **Problem:** Add crash/timeout handling to `run_batch()`:
  (a) Wrap each game's `a_run_multiple_games` call with
  `asyncio.wait_for(timeout=hard_timeout)` (default 600s).
  (b) On `asyncio.TimeoutError`, record as `SelfPlayRecord` with
  `winner=None`, `error="timeout after {N}s"`.
  (c) On any other exception from the SC2 layer, record with
  `error=str(exc)`.
  (d) After timeout/crash, call `KillSwitch.kill_all()` to clean up
  orphaned SC2 processes (import from `sc2.main`).
  (e) The batch continues to the next game after a crash — one
  failed game doesn't abort the whole batch.
  Add tests: mock `a_run_multiple_games` to raise `TimeoutError` or
  `RuntimeError`, verify the record has `error` set and the batch
  continues past it.
- **Issue:** #109
- **Flags:** `--reviewers auto`
- **Produces:** updated `src/orchestrator/selfplay.py`,
  updated `tests/test_selfplay.py`.
- **Done when:** crash-handling tests pass, all tests green, mypy/ruff.
- **Depends on:** 3.2.

### Step 3.4: CLI wrapper `scripts/selfplay.py`
- **Status:** pending
- **Problem:** Create `scripts/selfplay.py` as a thin CLI wrapper:
  ```
  python scripts/selfplay.py --p1 v0 --p2 v0 --games 20 --map Simple64
  python scripts/selfplay.py --sample pfsp --pool v0,v1,v2 --games 40 --map Simple64
  ```
  Head-to-head mode: `--p1` + `--p2` + `--games` + `--map`.
  PFSP mode: `--sample pfsp` + `--pool v0,v1,...` + `--games` + `--map`
  (trainee is always current version from `current.txt`; opponents
  sampled from pool via `pfsp_sample`).
  Prints progress (game N/total, winner) to stdout. Prints summary
  (wins for each side, draws) at end. Exit 0 on success, exit 1 if
  all games crashed.
  Add argparse test in `tests/test_selfplay.py`.
- **Issue:** #109
- **Flags:** `--reviewers auto`
- **Produces:** new `scripts/selfplay.py`, updated tests.
- **Done when:** `--help` exits 0, argparse tests pass, all green.
- **Depends on:** 3.3.

### Step 3.5: Gate verification — 20-game self-play batch
- **Status:** pending
- **Type:** operator
- **Problem:** Human-run end-to-end verification. Requires SC2 running.
  Checklist:
  (a) `uv run python scripts/selfplay.py --p1 v0 --p2 v0 --games 4
  --map Simple64` completes without hangs.
  (b) `data/selfplay_results.jsonl` contains 4 well-formed JSON lines.
  (c) Seats alternate: games 0,2 have `seat_swap: false`, games 1,3
  have `seat_swap: true`.
  (d) No orphaned SC2 processes after batch completes (check via
  `tasklist | grep SC2` — only the user's main-menu SC2 should
  remain if it was open).
  (e) Force-kill a bot mid-game (manually, or with a very short
  `game_time_limit`) and verify the crash is recorded and the
  batch continues.
  (f) `uv run pytest --tb=short -q` — all tests pass.
  (g) `uv run mypy src bots --strict` — clean.
  (h) `uv run ruff check .` — clean.
- **Issue:** #109
- **Produces:** comment on #109 with checklist results.
- **Done when:** all checklist items pass, user signs off.
- **Depends on:** 3.1, 3.2, 3.3, 3.4.

## 7. Risks

| Risk | Mitigation |
|------|------------|
| SC2 hangs on port collision despite patch | The spike proved the patch works; selfplay.py reuses the exact same code. If it regresses, the kill criterion applies. |
| `a_run_multiple_games` doesn't propagate subprocess crashes cleanly | The `asyncio.wait_for` hard timeout is the backstop. `KillSwitch.kill_all()` cleans up SC2 processes. |
| `bots/v0/__main__.py` ladder path missing `--result-out` handling | It already accepts `--result-out` in argparse but doesn't write it in `_run_ladder`. Step 3.2 may need a small fix there if per-bot result files are needed beyond the burnysc2 match result. |
| PFSP win-rate data source undefined | For Phase 3, PFSP uses a `--win-rates` JSON file or inline dict. Phase 4's Elo ladder will be the real source. |
