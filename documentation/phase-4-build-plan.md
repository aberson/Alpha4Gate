# Phase 4 Build Plan — Elo ladder + cross-version promotion

**Parent plan:** [alpha4gate-master-plan.md](plans/alpha4gate-master-plan.md) Phase 4 (lines 589–648)
**Parent issue:** #105 (umbrella)
**Branch:** `master-plan/4/elo-ladder` (cut from master at `39b11e9`)
**Baseline tag:** `master-plan/4/baseline`
**Target tag on gate pass:** `master-plan/4/final`
**Effort estimate:** 3–4 h.

## 1. What this feature does

Adds a cross-version Elo ladder that ranks every `bots/vN/` snapshot by
strength, computed from self-play results produced by Phase 3's
`run_batch()`. A promotion gate decides when `bots/current/` is strong
enough to be snapshotted as `bots/vN+1/` — it requires both an Elo gain
(≥ +10 vs parent over ≥ 20 games) and WR non-regression vs SC2 AI.

After this phase, a developer can run:

    python scripts/ladder.py update --games 20 --map Simple64
    python scripts/ladder.py show
    python scripts/ladder.py show --json
    python scripts/ladder.py compare v0 v1 --games 20
    python scripts/ladder.py compare v0 v1 --dry-run
    python scripts/ladder.py replay

and see Elo standings on the 10th dashboard tab.

## 2. Existing context (for fresh-context models)

- **`src/orchestrator/selfplay.py`** — `run_batch(p1, p2, games, map_name)`
  runs N games between two versioned bots, returns `list[SelfPlayRecord]`,
  appends to `data/selfplay_results.jsonl`. `pfsp_sample()` does opponent
  weighting. Port-collision patch, crash handling, seat alternation all
  built in Phase 3.
- **`src/orchestrator/contracts.py`** — frozen dataclasses:
  `BotSpawnArgs`, `MatchResult`, `SelfPlayRecord`, `VersionFingerprint`,
  `Manifest`. `Manifest` already has an `elo: float` field. `SelfPlayRecord`
  has `p1_version`, `p2_version`, `winner`, `map_name`, `duration_s`,
  `seat_swap`, `timestamp`, `error`.
- **`src/orchestrator/registry.py`** — `list_versions()`,
  `current_version()`, `get_version_dir()`, `get_manifest()`,
  `resolve_data_path()`. All via pathlib (no imports of `bots.*`).
- **`src/orchestrator/snapshot.py`** — `snapshot_current(name?)` copies
  `bots/current/` → `bots/<name>/`, writes a fresh manifest inheriting
  parent Elo, and **updates `bots/current/current.txt`** to point at the
  new version. Phase 4's promotion gate calls this on success.
- **`scripts/selfplay.py`** — CLI for head-to-head and PFSP self-play.
- **`data/selfplay_results.jsonl`** — append-only match log from
  `run_batch()`. Each line is a `SelfPlayRecord` JSON object.
- **`bots/v0/api.py`** — FastAPI backend. Endpoints follow
  `@app.get("/api/<resource>")` pattern, read from `_data_dir / <file>`.
  **Note:** `_data_dir` points to the per-version data dir (e.g.
  `bots/v0/data/`), NOT the shared repo-root `data/`. Ladder and
  self-play files live in shared `data/` — the endpoint must resolve
  paths via `orchestrator.registry.resolve_data_path()` or hardcode
  `Path(<repo_root>) / "data"` (see design 5.2).
- **`frontend/src/App.tsx`** — 9-tab dashboard. `Tab` union type at
  line 24–33. Each tab is a `<button>` in `<nav>` + conditional render
  in `<main>`. `useApi<T>(endpoint, { pollMs })` hook for data fetching.
- **`frontend/src/components/TrainingDashboard.tsx`** — canonical example
  of a tab that reads JSON via `useApi` with `pollMs: 5000` + stale banner.
- **967 unit tests passing**, mypy strict (62 files), ruff clean on master.

**Critical constraints:**
- Do NOT import `bots.current` or `bots.<version>` from
  `src/orchestrator/` — triggers MetaPathFinder loop.
- Backend must be started from repo root CWD or data reads silently empty.
- `Manifest.elo` already exists — ladder updates should write back to it
  when updating standings, so snapshots carry their last known Elo.
- The self-play CLI is `python scripts/selfplay.py` (NOT
  `python -m orchestrator.selfplay`).

## 3. Scope

**In scope:**

- `src/orchestrator/ladder.py` — Elo math, ladder state, round-robin
  update, cross-version promotion gate.
- `scripts/ladder.py` — CLI wrapper (update, show, compare subcommands).
- Extend `src/orchestrator/contracts.py` with `LadderEntry` and
  `PromotionResult` frozen dataclasses.
- `data/bot_ladder.json` — `{version: {elo, games_played, last_updated}}`
  standings file.
- `bots/v0/api.py` — add `/api/ladder` GET endpoint.
- `frontend/src/components/LadderTab.tsx` — 10th dashboard tab showing
  standings + head-to-head grid.
- `frontend/src/App.tsx` — wire Ladder tab into navigation.
- Tests: `tests/test_ladder.py`, `tests/test_cross_version_gate.py`.

**Out of scope:**

- Sandbox enforcement (Phase 5).
- Daemon integration (wiring ladder updates into the daemon idle loop).
- PFSP win-rate source rewire (Phase 4 computes Elo from existing JSONL;
  PFSP still uses its own `--win-rates` input).
- Transition recording into per-version `training.db` during ladder games.

## 4. Impact analysis

| Area | Files | Nature of change |
|------|-------|------------------|
| `src/orchestrator/ladder.py` | 1 file | **Create** Elo ladder + promotion gate |
| `src/orchestrator/contracts.py` | 1 file | **Extend** with `LadderEntry`, `PromotionResult` |
| `scripts/ladder.py` | 1 file | **Create** CLI wrapper |
| `bots/v0/api.py` | 1 file | **Extend** with `/api/ladder` endpoint |
| `frontend/src/App.tsx` | 1 file | **Extend** Tab union + nav + render |
| `frontend/src/components/LadderTab.tsx` | 1 file | **Create** Ladder tab component |
| `tests/test_ladder.py` | 1 file | **Create** Elo math + ladder tests |
| `tests/test_cross_version_gate.py` | 1 file | **Create** promotion gate tests |

## 5. Design decisions

### 5.1 Standard Elo with K=32

Standard Elo formula: `E_A = 1 / (1 + 10^((R_B - R_A) / 400))`,
`R'_A = R_A + K * (S_A - E_A)` where `S_A ∈ {1.0, 0.5, 0.0}` for
win/draw/loss. K=32 per the master plan.

**Seeding hierarchy** for a version appearing in the ladder for the first
time (handled by `seed_version(version, standings)`):
1. If the version has a manifest (`get_manifest(version)`), use
   `Manifest.elo`.
2. Else if the manifest names a `parent` and the parent is in standings,
   inherit the parent's current Elo.
3. Else fallback to 1000.0.

`v0` (no parent) seeds at 1000.0 via rule 3. A newly snapshotted
`v1` (parent=v0) seeds at v0's manifest Elo via rule 1 (since
`snapshot_current` writes the parent Elo into the new manifest).

### 5.2 Ladder state file: `data/bot_ladder.json`

Shared data directory (not per-version) because the ladder is a
cross-version view. Located at `<repo_root>/data/bot_ladder.json`.
**Not** under `bots/v0/data/` — the API endpoint must NOT use `_data_dir`
to locate this file (see §2 note on `api.py`). Schema:

```json
{
  "standings": {
    "v0": {"elo": 1023.4, "games_played": 40, "last_updated": "2026-04-17T12:00:00+00:00"},
    "v1": {"elo": 976.6, "games_played": 40, "last_updated": "2026-04-17T12:00:00+00:00"}
  },
  "head_to_head": {
    "v0": {"v1": {"wins": 12, "losses": 8, "draws": 0}},
    "v1": {"v0": {"wins": 8, "losses": 12, "draws": 0}}
  }
}
```

Read/write is atomic: read → update in memory → write entire file. No
concurrent writers expected (CLI or daemon, never both). Head-to-head
is updated incrementally by `ladder_update` / `ladder_replay` alongside
standings — never recomputed from JSONL on read.

### 5.3 Contracts: `LadderEntry` and `PromotionResult`

```python
@dataclass(frozen=True)
class LadderEntry:
    version: str
    elo: float
    games_played: int
    last_updated: str  # ISO 8601 UTC

@dataclass(frozen=True)
class PromotionResult:
    candidate: str        # version being evaluated (usually "current")
    parent: str           # version it's compared against
    elo_delta: float      # candidate Elo - parent Elo
    games_played: int     # games in the evaluation
    wr_vs_sc2: float | None  # win rate vs SC2 AI (None if not tested)
    promoted: bool        # True if all gates passed
    reason: str           # human-readable explanation
```

### 5.4 Round-robin update

`ladder_update(versions, games_per_pair, map_name)`:
1. For each pair `(vA, vB)` in `versions`, run `selfplay.run_batch(vA, vB, games_per_pair)`.
2. For each `SelfPlayRecord`, compute Elo update for both sides.
3. Write updated `data/bot_ladder.json`.

"Top-N + current" means: take the top N versions by Elo from the ladder
file, plus `current_version()`, deduplicate. Default N=3 (or all if
fewer than 3 exist).

### 5.5 Cross-version promotion gate

`check_promotion(candidate, parent, games, map_name) -> PromotionResult`:
1. Run `selfplay.run_batch(candidate, parent, games)` (default 20).
2. Compute Elo from the batch. If Elo delta < +10, reject.
3. (Optional) Run candidate vs SC2 AI at current difficulty to check WR
   non-regression. This is a sanity check — a 100-Elo gain that drops
   WR 30% is suspect. For Phase 4, WR check is informational (logged in
   `PromotionResult.wr_vs_sc2`) but not blocking — blocking WR requires
   the daemon's SC2 AI runner, which is out of scope.
4. If Elo gate passes, call `snapshot.snapshot_current()` to create
   `bots/vN+1/` and return `PromotionResult(promoted=True)`.
   **Side effect:** `snapshot_current()` updates `bots/current/current.txt`
   to point at the new version. This is intentional — after promotion, the
   "current" bot IS the newly promoted version. Callers (e.g.
   `ladder_update`) that resolve "current" should do so BEFORE calling
   `check_promotion`, not after.

### 5.6 Dashboard Ladder tab

The 10th tab. Shows:
- Standings table sorted by Elo (rank, version, Elo, games played, last
  updated).
- Head-to-head grid (win counts between each pair).

Head-to-head is computed at `ladder_update` / `ladder_replay` time and
stored in `data/bot_ladder.json` alongside standings (not recomputed on
every API call). Schema extends to:

```json
{
  "standings": {"v0": {...}, "v1": {...}},
  "head_to_head": {"v0": {"v1": {"wins": 12, "losses": 8, "draws": 0}}, ...}
}
```

Backend: `GET /api/ladder` reads `data/bot_ladder.json` via
`_repo_root() / "data" / "bot_ladder.json"` (shared data, NOT
`_data_dir` which is per-version). Returns the file contents directly.

Frontend: `LadderTab.tsx` uses `useApi<LadderData>("/api/ladder", { pollMs: 10000 })`.
Follows the `TrainingDashboard.tsx` pattern with `StaleDataBanner`.

TypeScript response type:

```typescript
interface LadderEntry {
  version: string;
  elo: number;
  games_played: number;
  last_updated: string;
}
interface HeadToHeadRecord {
  wins: number;
  losses: number;
  draws: number;
}
interface LadderData {
  standings: LadderEntry[];
  head_to_head: Record<string, Record<string, HeadToHeadRecord>>;
}
```

### 5.7 Bootstrapping from existing JSONL (`ladder_replay`)

`ladder_update` always runs **fresh** games via `selfplay.run_batch()` —
it is not idempotent (running it twice doubles the game count).

A separate `ladder_replay(jsonl_path, ladder_path)` function replays
historical `SelfPlayRecord` lines from `selfplay_results.jsonl`
chronologically to rebuild standings + head-to-head. Use cases:
- Fresh ladder start: delete `data/bot_ladder.json`, run `ladder_replay`.
- Adding a new version: replay includes any past games involving it.
- Recovering from corruption.

The CLI exposes this as `python scripts/ladder.py replay [--jsonl <path>]`.
`replay` and `update` are distinct subcommands — never confused.

## 6. Build steps

### Step 4.1: Elo math + contracts + ladder state
- **Status:** DONE (2026-04-17)
- **Type:** code
- **Problem:** Create `src/orchestrator/ladder.py` with:
  (a) `elo_expected(rating_a, rating_b) -> float` — standard expected
  score formula.
  (b) `elo_update(rating, expected, actual, k=32) -> float` — new rating.
  (c) `LadderEntry` and `PromotionResult` dataclasses in
  `src/orchestrator/contracts.py` per design 5.3.
  (d) `load_ladder(path) -> dict[str, LadderEntry]` — read
  `data/bot_ladder.json`, return empty dict if missing.
  (e) `save_ladder(standings, path)` — write `data/bot_ladder.json`
  atomically.
  (f) `seed_version(version, standings) -> LadderEntry` — seed a new
  version per the hierarchy in design 5.1 (manifest Elo → parent Elo →
  1000.0). Called by `update_elo` when a version is not yet in standings.
  (g) `update_elo(standings, record: SelfPlayRecord, k=32) ->
  dict[str, LadderEntry]` — apply one game result to standings.
  Calls `seed_version` for any version not yet in standings.
  Also updates the `head_to_head` section of the ladder state.
  (h) Update `src/orchestrator/__init__.py` docstring if needed (it
  already mentions "ladder — Phase 4"; verify after creating the module).
  Create `tests/test_ladder.py` with cases: expected score is 0.5 for
  equal ratings, K=32 produces correct delta for known scenario,
  seed_version uses manifest Elo when available, seed_version falls
  back to 1000.0, load/save round-trip, draw splits Elo evenly,
  head-to-head increments correctly.
- **Issue:** #105
- **Flags:** `--reviewers auto`
- **Produces:** `src/orchestrator/ladder.py` (Elo math + state),
  updated `src/orchestrator/contracts.py`, new `tests/test_ladder.py`.
- **Done when:** new tests pass, existing 967 tests still pass, mypy
  strict green, ruff green.

### Step 4.2: Round-robin ladder update
- **Status:** DONE (2026-04-17)
- **Type:** code
- **Problem:** Add to `src/orchestrator/ladder.py`:
  (a) `get_top_n(standings, n=3) -> list[str]` — return top N versions
  by Elo.
  (b) `ladder_update(versions: list[str] | None, games_per_pair: int,
  map_name: str, ladder_path: Path | None) -> dict[str, LadderEntry]`
  — if `versions` is None, use top-N + current. For each pair, call
  `selfplay.run_batch()`, apply Elo updates per game (including
  head-to-head increments), save ladder. Return final standings.
  (c) `ladder_replay(jsonl_path: Path, ladder_path: Path | None) ->
  dict[str, LadderEntry]` — replay all `SelfPlayRecord` lines from
  JSONL chronologically to rebuild standings + head-to-head from
  scratch. Clears existing ladder state before replaying.
  Add tests: mock `selfplay.run_batch` to return deterministic
  `SelfPlayRecord` lists. Verify that after a round-robin of 3 versions
  × 4 games each pair, standings reflect correct Elo movement. Verify
  `current_version()` is included automatically. Verify `ladder_replay`
  produces identical standings to `ladder_update` given the same records.
- **Issue:** #105
- **Flags:** `--reviewers auto`
- **Produces:** updated `src/orchestrator/ladder.py`,
  updated `tests/test_ladder.py`.
- **Done when:** new tests pass, all tests green, mypy/ruff clean.
- **Depends on:** 4.1.

### Step 4.3: Cross-version promotion gate
- **Status:** DONE (2026-04-17)
- **Type:** code
- **Problem:** Add to `src/orchestrator/ladder.py`:
  (a) `check_promotion(candidate: str, parent: str, games: int,
  map_name: str, elo_threshold: float = 10.0) -> PromotionResult` —
  run `selfplay.run_batch(candidate, parent, games)`, compute Elo
  delta from the batch, return `PromotionResult`. If delta ≥ threshold,
  `promoted=True`. `wr_vs_sc2` is None for now (Phase 4 scope — SC2 AI
  WR check is informational only, requires daemon runner).
  (b) If promoted, call `snapshot.snapshot_current()` (from
  `orchestrator.snapshot`, NOT `registry`) and log the promotion.
  Note: `snapshot_current()` updates `current.txt` to point at the
  new version — this is intentional (see design 5.5).
  Create `tests/test_cross_version_gate.py` with cases: Elo gain above
  threshold → promoted, below threshold → rejected, exactly at
  threshold → promoted (boundary), all-draws → rejected (Elo delta 0),
  configurable threshold.
- **Issue:** #105
- **Flags:** `--reviewers auto`
- **Produces:** updated `src/orchestrator/ladder.py`,
  new `tests/test_cross_version_gate.py`.
- **Done when:** new tests pass, all tests green, mypy/ruff clean.
- **Depends on:** 4.1.

### Step 4.4: CLI wrapper `scripts/ladder.py`
- **Status:** DONE (2026-04-17)
- **Type:** code
- **Problem:** Create `scripts/ladder.py` as a thin CLI wrapper with
  three subcommands:
  ```
  python scripts/ladder.py update [--games 20] [--map Simple64] [--versions v0,v1]
  python scripts/ladder.py show [--json]
  python scripts/ladder.py compare v0 v1 [--games 20] [--map Simple64] [--dry-run]
  python scripts/ladder.py replay [--jsonl data/selfplay_results.jsonl]
  ```
  `update` — calls `ladder_update()`, prints updated standings.
  `show` — calls `load_ladder()`, prints standings table sorted by Elo.
  `--json` outputs raw JSON for scripting/piping.
  `compare` — calls `check_promotion(vA, vB, games)`, prints result
  including Elo delta and promotion decision. `--dry-run` computes
  what would happen from existing JSONL data without running new games
  (useful for debugging — replays only records matching the two versions).
  `replay` — calls `ladder_replay()` to rebuild standings from JSONL.
  Prints formatted table to stdout. Exit 0 on success, exit 1 on error.
  Add argparse test in `tests/test_ladder.py`.
- **Issue:** #105
- **Flags:** `--reviewers auto`
- **Produces:** new `scripts/ladder.py`, updated tests.
- **Done when:** `--help` exits 0, argparse tests pass, all green.
- **Depends on:** 4.2, 4.3.

### Step 4.5: Backend `/api/ladder` endpoint
- **Status:** pending
- **Type:** code
- **Problem:** Add to `bots/v0/api.py`:
  (a) `GET /api/ladder` — reads shared `data/bot_ladder.json` via
  `_repo_root / "data" / "bot_ladder.json"` (NOT `_data_dir`, which
  points to per-version `bots/v0/data/`). Resolve `_repo_root` once
  at configure-time from `Path(__file__).resolve().parent.parent.parent`
  or accept it as a configure() parameter.
  Returns the file contents directly — standings sorted by Elo
  descending plus the pre-computed head-to-head grid (both written by
  `ladder_update` / `ladder_replay`, not computed per-request).
  If the file doesn't exist, return `{"standings": [], "head_to_head": {}}`.
  (b) Similarly, any future endpoint reading `selfplay_results.jsonl`
  must use the shared `data/` path, not `_data_dir`.
  Add test in `tests/test_ladder.py` (or existing API test file) that
  verifies the endpoint returns correct shape with mock data.
- **Issue:** #105
- **Flags:** `--reviewers auto`
- **Produces:** updated `bots/v0/api.py`, updated tests.
- **Done when:** endpoint returns valid JSON, tests pass, mypy/ruff clean.
- **Depends on:** 4.1.

### Step 4.6: Dashboard Ladder tab (frontend)
- **Status:** pending
- **Type:** code
- **Problem:** Create the 10th dashboard tab:
  (a) Create `frontend/src/components/LadderTab.tsx` — call
  `useApi<LadderData>("/api/ladder", { pollMs: 10000 })`.
  Show standings as a table (rank, version, Elo, games, last updated).
  Show head-to-head grid if data exists. Include `StaleDataBanner`.
  Follow `TrainingDashboard.tsx` structure.
  (b) Update `frontend/src/App.tsx`:
  - Add `"ladder"` to the `Tab` union type (line ~33).
  - Add a `<button>` in `<nav>` for the Ladder tab.
  - Add `{tab === "ladder" && <LadderTab />}` in `<main>`.
  - Import `LadderTab`.
  (c) Define the `LadderData`, `LadderEntry`, and `HeadToHeadRecord`
  TypeScript interfaces per design 5.6 (inline in `LadderTab.tsx` or
  a shared types file).
  (d) Add vitest test at `frontend/src/components/LadderTab.test.tsx`
  (sibling pattern, matching existing tests like `AlertsPanel.test.tsx`).
- **Issue:** #105
- **Flags:** `--reviewers auto --ui --start-cmd "bash scripts/start-dev.sh" --url http://localhost:3000 --ready-url http://localhost:8765/api/status`
- **Produces:** new `frontend/src/components/LadderTab.tsx`,
  new `frontend/src/components/LadderTab.test.tsx`,
  updated `frontend/src/App.tsx`.
- **Done when:** tab renders with mock data, vitest passes,
  `npm run build` succeeds, no type errors.
- **Depends on:** 4.5.

### Step 4.7: Gate verification
- **Status:** pending
- **Type:** operator
- **Problem:** Human-run end-to-end verification. Checklist:
  (a) `uv run pytest --tb=short -q` — all tests pass (target: ~990+).
  (b) `uv run mypy src bots --strict` — clean.
  (c) `uv run ruff check .` — clean.
  (d) `cd frontend && npm run build` — clean.
  (e) `python scripts/ladder.py show` — prints empty or seeded standings.
  (f) Ladder updates reproducibly on a known scenario: seed two versions
  with known Elo, run a batch with mocked results, verify Elo changes
  match hand-computed values.
  (g) Ladder tab renders alongside the existing 9 tabs in the dashboard.
  (h) Ladder JSON schema documented in `src/orchestrator/contracts.py`
  (`LadderEntry` + `PromotionResult`).
  (i) Cross-version promotion can be triggered end-to-end via
  `python scripts/ladder.py compare v0 v0 --games 4` and respects
  the Elo gate (v0 vs itself should produce ~0 Elo delta → rejected).
- **Issue:** #105
- **Produces:** comment on #105 with checklist results.
- **Done when:** all checklist items pass, user signs off.
- **Depends on:** 4.1, 4.2, 4.3, 4.4, 4.5, 4.6.

## 7. Risks

| Risk | Mitigation |
|------|------------|
| Elo noise at 20-game batches | Master plan kill criterion: increase to 40 games; if still noisy, shift to WR-vs-SC2-AI as primary signal. |
| Only one version (`v0`) exists — no cross-version games possible | `compare v0 v0` is valid (self-play, Elo stays ~flat). Real cross-version testing requires Phase 5 or manual `snapshot_current`. |
| `snapshot_current()` not yet tested in promotion path | Phase 2 built and gate-verified `snapshot_current` in `snapshot.py`. Step 4.3 calls it; if it breaks, the error is clear. Note: it also updates `current.txt` (see design 5.5). |
| Head-to-head data stale after manual JSONL edits | Head-to-head is computed at update/replay time, not per-request. If someone manually edits the JSONL, run `ladder replay` to rebuild. |
| WR non-regression check requires SC2 AI runner (daemon) | Phase 4 makes WR informational only (`wr_vs_sc2: None`). Phase 5 wires the daemon to run sanity-check games. |
| `current.txt` is a single-lineage pointer | Sufficient for Phase 4 (one active version). When parallel evolutionary lines land (sky-toss, ground-toss, etc.), upgrade to `current.json` with `{"default": "v3", "lineages": {"sky-toss": "v5", ...}}` and extend `current_version(lineage=...)`. ~20 lines across `registry.py`, `snapshot.py`, `bots/current/__init__.py`. |
