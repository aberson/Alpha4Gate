# Phase N — Win-probability heuristic + give-up logic (build plan)

## 1. What this phase ships

Two paired capabilities riding on a single signal:

1. **Win-probability heuristic** — a per-step `P(win)` scalar computed
   from the existing 24-feature observation, written to a new
   `transitions.win_prob` column, logged every 10 decision steps.
   Implements **Option (c)** from
   [`win-probability-forecast-investigation.md`](../investigations/win-probability-forecast-investigation.md).
   Pure function, no model checkpoint, no training.
2. **Give-up trigger** — when `win_prob < 0.05` for ≥ 30 consecutive
   decision steps AND `game_time > 8 min`, issue `RequestLeaveGame`
   ("know when to fold-em"). Saves wall-clock on lost games and
   produces clean defeat records for downstream stats.

Phase N is **unblocked** because the win-probability investigation
already concluded — Option (c) is the recommended starting point and
the heuristic formula is published in §5 of that investigation.

## 2. Existing context

**Investigation finding to use as-is:**

> Heuristic formula:
>     0.25 * army_vs_enemy_ratio +
>     0.25 * (0.5*workers/50 + 0.5*bases/3) +
>     0.15 * supply_used/200 +
>     0.15 * (gateways + 2*robo)/6 +
>     0.10 * upgrades/4 +
>     0.10 * (cannons + batteries)/4 -
>     0.30 * enemy_army_near_base

> Mean P(win) on WIN  transitions: 0.342
> Mean P(win) on LOSS transitions: 0.197
> Separation:                     0.145 absolute

**Code refs.**

- `bots/v0/decision_engine.py::GameSnapshot` — input to the
  heuristic. All required fields exist today.
- `bots/v0/learning/environment.py::SC2Env.step` — write-site for
  `transitions.win_prob`. Existing pattern: env populates
  `info["..."]` and `database.py` consumes.
- `bots/v0/learning/database.py::store_transition` — DB write.
  Migration via the existing `_LATER_ADDED_COLS` pattern.
- `bots/v0/bot.py` (or equivalent end-of-step hook) — give-up
  trigger lives here. Check `win_prob` history each step; on
  threshold, call `await self.client.leave()`.

**Memory refs.**

- `feedback_check_logs_during_debug.md` — the new INFO log line
  (`winprob=0.42 state=attack`) is a debug surface. Make sure it
  shows in `backend.log` even when running in batch mode.
- `feedback_per_version_vs_cross_version_data_dir.md` —
  `winprob_heuristic.py` lives at `bots/v0/learning/`. Per-version
  state. Phase N ships ON `bots/current/` (which is `bots/v0/` today).
- `feedback_single_game_db_recording.md` — verify the give-up
  trigger doesn't cause `_run_single_game` to skip
  `database.store_game()`. Defeat path must complete normally.

## 3. Scope

**In scope.**

- `bots/v0/learning/winprob_heuristic.py` with single pure function:
  `score(snapshot: GameSnapshot) -> float`.
- `transitions.win_prob REAL` column via `_LATER_ADDED_COLS` migration.
- Wire in `SC2Env.step` so every transition row writes the heuristic
  score next to the state.
- Logger line every 10 decision steps:
  `winprob=%.2f state=%s` at INFO level.
- Give-up trigger module: `bots/v0/give_up.py` —
  `should_give_up(history: deque[float], game_time: float) -> bool`.
- Bot integration: end-of-step hook calls `should_give_up`; on True,
  `await self.client.leave()`.

**Out of scope.**

- Option (b) classifier (deferred per investigation §4).
- Option (a) value head on PPO (deferred — out of investigation
  scope).
- WebSocket live curve on Live tab (deferred per investigation §6;
  Phase N ships log + DB column only).
- Adjusting reward function based on win-prob (explicitly
  deferred per investigation §8.5).

## 4. Build steps

### Step N.1: Heuristic module + unit tests

- **Issue:** #228
- **Problem:** Implement `score(snapshot)` per investigation §5
  formula. All inputs come from `GameSnapshot` fields that exist
  today; no DB reads inside the function.
- **Type:** code
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** `bots/v0/learning/winprob_heuristic.py`,
  `tests/test_winprob_heuristic.py`.
- **Done when:** Unit tests pass: (a) formula matches investigation
  §5 numerically on a hand-crafted snapshot, (b) clamp to [0, 1]
  works on extreme inputs, (c) `enemy_army_near_base=True` reduces
  score by exactly 0.30 vs `False`. Mypy strict + ruff clean.
- **Status:** DONE (2026-04-27)

### Step N.2: DB migration + write-path

- **Issue:** #229
- **Problem:** Add `win_prob REAL` to `transitions` via
  `_LATER_ADDED_COLS`. In `SC2Env.step`, compute
  `winprob_heuristic.score(snapshot)` once per decision step;
  thread through `info["win_prob"]`; `store_transition` writes the
  value.
- **Type:** code
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** Updates to `bots/v0/learning/database.py`,
  `bots/v0/learning/environment.py`, `tests/test_database_migration.py`,
  `tests/test_environment_winprob.py`.
- **Done when:** Migration test passes (old DB → new DB →
  win_prob column populated on next write); 1-game live SC2 run
  produces non-NULL win_prob values across all transitions.
- **Depends on:** N.1.

### Step N.3: Logger line

- **Issue:** #230
- **Problem:** In the bot's main step loop, log
  `winprob=%.2f state=%s` at INFO every 10 decision steps. Use the
  existing `bots.v0.logging` logger (not `print`).
- **Type:** code
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** Updates to `bots/v0/bot.py` (or step-loop module),
  `tests/test_winprob_logging.py` (caplog fixture).
- **Done when:** Test asserts log line appears every 10 steps with
  matching format; manual smoke run shows lines in `backend.log`.
- **Depends on:** N.2.

### Step N.4: Give-up module + unit tests

- **Issue:** #231
- **Problem:** `should_give_up(history: deque[float], game_time:
  float) -> bool`. Returns True iff `len(history) >= 30` AND `all(p <
  0.05 for p in history[-30:])` AND `game_time > 480` (seconds).
- **Type:** code
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** `bots/v0/give_up.py`,
  `tests/test_give_up_trigger.py`.
- **Done when:** Unit tests cover: (a) trigger fires with 30 zeros
  and game_time=500, (b) does NOT fire with 30 zeros and
  game_time=400 (under 8 min), (c) does NOT fire with 29 zeros at
  game_time=500, (d) deque maintenance trims oldest when length
  exceeds 30. Mypy + ruff clean.
- **Depends on:** none (pure function with no game integration yet).

### Step N.5: Give-up bot integration

- **Issue:** #232
- **Problem:** In bot's end-of-step hook, append current `win_prob`
  to a 30-deep deque; call `should_give_up`; on True, `await
  self.client.leave()`. Verify the leave path still triggers
  `database.store_game()` (loss recorded normally).
- **Type:** code
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** Updates to `bots/v0/bot.py`,
  `tests/test_give_up_integration.py` (mocked `Client`).
- **Done when:** Mocked-client test verifies (a) `leave()` is called
  exactly once when threshold met, (b) `store_game()` runs
  afterward with `result=Defeat`, (c) no `leave()` call when
  threshold not met.
- **Depends on:** N.2 + N.4.

### Step N.6: Operator smoke gate

- **Issue:** #233
- **Problem:** Run a real-SC2 1-game session against difficulty 5
  (a likely losing matchup): `python -m bots.v0 --role solo
  --map Simple64 --difficulty 5`. Verify (a) `win_prob` written
  to `transitions` rows, (b) log lines appear every 10 steps,
  (c) if game goes badly, give-up triggers and game ends in
  recorded loss. If game goes well, manually contrive a loss
  scenario for trigger verification.
- **Type:** operator
- **Produces:** Manual verification + 1 row in
  `bots/v0/data/training.db::games` and ≥30 in `transitions`
  with non-NULL `win_prob`.
- **Done when:** All smoke checks green.
- **Depends on:** N.5.

## 5. Tests

- `tests/test_winprob_heuristic.py` — formula correctness, clamping,
  enemy-near-base impact.
- `tests/test_database_migration.py` (extended) — new column.
- `tests/test_environment_winprob.py` — env writes win_prob in
  info dict.
- `tests/test_winprob_logging.py` — logger line cadence and format.
- `tests/test_give_up_trigger.py` — pure-function trigger logic.
- `tests/test_give_up_integration.py` — bot hook + leave + store_game.

## 6. Effort

~1 day code, including the 1-game smoke gate.

## 7. Validation

- Heuristic separates win-game and loss-game mean scores by ≥ 0.10
  absolute (investigation §5.1 measured 0.145 on the 8-game soak;
  validate on a fresh 20-game soak).
- Give-up trigger fires < 5% of games in winning soaks (against
  difficulty 1-2 on `bots/v0`).
- Give-up trigger fires ≥ 30% of games in losing soaks (against
  difficulty 5 on `bots/v0` — known-bad matchup).

## 8. Gate

All three validation criteria.

## 9. Kill criterion

Heuristic separation collapses on more recent versions (< 0.05).
Pivot to Option (b) classifier per investigation §4 — meaning Phase
N ships only the give-up trigger (deferring it as the only
consumer) OR pivots to a small-MLP classifier with the same
write-path.

## 10. Rollback

Drop `winprob_heuristic.py`, give-up module; remove the column via
`ALTER TABLE` migration (or leave as no-op). Bot integration removed
in one Edit.

## 11. Cross-references

- Master plan Phase N pointer: `documentation/plans/alpha4gate-master-plan.md`
- Investigation: `documentation/investigations/win-probability-forecast-investigation.md`
- Phase O (Hydra) — uses win-prob as candidate switching signal
- Phase P (distillation) — separate; doesn't depend on N
