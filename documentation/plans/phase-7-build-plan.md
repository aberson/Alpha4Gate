# Phase 7 Build Plan — Advised loop stale-policy detection

**Parent plan:** [master_plan.md](../master_plan.md) — Phase 7
**Track:** Operational
**Prerequisites:** Phase 5 (sandbox + skill integration). Independent of B/D/E/6 — ships standalone.
**Effort estimate:** ~1 day build + one overnight validation run.
**Status:** Reshaped + repaired 2026-06-20 (build-phase field format; Step 1 data source corrected to `TrainingDB`; budget guard corrected to wall-clock; live soak split into an operator step). Original draft 2026-04-19.

## 1. What this feature does

Teaches `/improve-bot-advised` to recognize when the PPO policy is stale relative to the
current reward / hyperparam config, and to schedule an extended training soak as a
first-class improvement type — rather than relying on the user to manually switch to
`/improve-bot --mode training`.

Today the advised loop's `training` path only does a short sync soak — enough to create a
checkpoint, not enough to actually train PPO against new rewards. The loop can iterate on
reward rules forever without the policy ever catching up. This phase closes that gap.

## 2. Existing context (verified 2026-06-20)

- **`/improve-bot-advised`** (`.claude/skills/improve-bot-advised/SKILL.md`) — autonomous loop;
  Phase 2 Claude analysis returns an improvements list; Phase 4 dispatches by improvement
  type (`training`, `dev`). Wall-clock budget is tracked **in-shell**: the `--hours` arg
  (default 4) plus the `SOAK_START` / `SOAK_ELAPSED=$(( $(date +%s) - SOAK_START ))` pattern
  (SKILL.md §4.2 "Execution budget" + §7.3 "Wall-clock check"). **There is no
  `advised_run_state.json`** — the original draft's claim was wrong; budget state is the shell
  timer, not a JSON file.
- **Per-version training store** — `bots/<version>/data/training.db`, owned by
  `bots/v0/learning/database.py::TrainingDB`. The `games` table holds one row per game
  (`game_id, map_name, difficulty, result, duration_secs, total_reward, model_version,
  created_at`). Read APIs: `get_recent_win_rate(n_games)`, `get_win_rate_by_model(model_version)`
  → `{wins, losses, total, win_rate}`, and `get_all_model_stats()` → per-version rows ordered
  chronologically by first game. **This is the real eval-WR source** — not `ladder.py::history()`
  (no such function) and not `data/bot_ladder.json` (does not exist on disk).
- **Deterministic evaluator** — `bots/v0/learning/evaluator.py::Evaluator.evaluate(checkpoint_name,
  n_games, difficulty)` → `EvalResult` (win rate + stats). Eval games and training games are
  **commingled** in the `games` table (no `is_eval` flag); see Step 1's trend definition for how
  this is handled.
- **Per-version checkpoints / rewards** — `bots/<version>/data/checkpoints/*.zip` and
  `bots/<version>/data/reward_rules.json`; mtimes reveal when training last ran vs when rules
  were last edited (`pathlib.Path.stat().st_mtime`).
- **Cross-import constraint (load-bearing)** — per `Alpha4Gate/CLAUDE.md`, code under
  `src/orchestrator/` must **NOT** `import bots.current` / `bots.<version>` (triggers the
  MetaPathFinder loop). `src/orchestrator/registry.py` resolves version → path via `pathlib`.
  Therefore `staleness.py` (Step 1) reads `training.db` with **direct `sqlite3` + `pathlib`**,
  mirroring `TrainingDB`'s SQL, rather than importing `TrainingDB`.

## 3. Scope (build steps)

| Step | Issue | Type | Description |
|------|-------|------|-------------|
| 1 | #180 | code | `src/orchestrator/staleness.py` — `StalenessReport` + `compute_staleness` (sqlite-direct) |
| 2 | #181 | code | Extend Claude analysis prompt with `type: "soak"` improvement |
| 3 | #182 | code | Phase 4 routing for `type: "soak"` (daemon lifecycle; code + mock test only) |
| 4 | #183 | code | Budget guard — clamp soak hours against the shell wall-clock budget |
| 5 | #184 | code | Tests + smoke: `staleness_signal` + `advised_soak_routing` + real-DB smoke |
| 6 | #280 | operator | End-to-end validation soak (live `/improve-bot-advised` run) |

---

### Step 1: staleness.py — StalenessReport + compute_staleness

- **Problem:** Create `src/orchestrator/staleness.py` exporting a frozen `StalenessReport`
  dataclass and `compute_staleness(version) -> StalenessReport`, reading the per-version
  `training.db` directly via `sqlite3` (NOT by importing `TrainingDB` — cross-import rule) plus
  `pathlib` mtimes, and expose a `python -m orchestrator.staleness <version>` CLI that prints the
  report as JSON for the skill to consume.
- **Type:** code
- **Issue:** #180
- **Flags:** --reviewers code --isolation worktree
- **Status:** DONE (2026-06-20)
- **Files:** `src/orchestrator/staleness.py` (NEW), `src/orchestrator/__init__.py` (export)
- **Depends on:** none
- **Done when:**
  - `StalenessReport` is a frozen dataclass: `is_stale: bool`, `eval_wr_trend: float`,
    `checkpoint_age_seconds: int`, `recent_win_rates: tuple[float, ...]`, `reason: str`.
  - `compute_staleness(version)` resolves the version's data dir via `registry` path resolution
    (or `pathlib`), opens `bots/<version>/data/training.db` read-only with `sqlite3`, and returns
    a populated report; raises `ValueError` if the version has fewer than `min_games` (default 10)
    games recorded.
  - `python -m orchestrator.staleness <version>` prints the report as a JSON object on stdout,
    exit 0; unknown/empty version exits non-zero with a stderr message.
  - `staleness` (the function + dataclass) is exported from `src/orchestrator/__init__.py`.
  - `uv run mypy src --strict` and `uv run ruff check .` clean.

**What to build.**

`eval_wr_trend` — **re-grounded to real data.** There is no stored "deterministic eval batch"
series in this codebase, and eval/training games are commingled in the `games` table. Compute the
trend as the linear-regression slope of the per-game win(1)/loss(0) series over the current
version's most recent `N` games (default `N=30`, configurable), read with SQL
`SELECT result FROM games ORDER BY rowid DESC LIMIT ?` (**no `model_version` filter** — the
per-version `training.db` is already version-scoped, and production writes decision-mode /
checkpoint / training-cycle labels into `model_version`, never the package version, so a filter
would match zero rows; this matches `TrainingDB.get_recent_win_rate`, which also has no filter).
Classify slope as flat / rising / falling against a
configurable threshold (default 0.01 WR per game). `recent_win_rates` holds the windowed WRs used
for the regression (e.g. WR per 10-game bucket) so the report is inspectable.

> **Eval-vs-training commingling (explicit decision):** the default uses *all* recent games for
> the version. If eval-only isolation is later required, add an `is_eval`/`mode` column to the
> `games` schema via the `_LATER_ADDED_COLS` migration pattern already in `database.py` and filter
> on it — tracked as a follow-up, NOT in this step's scope.

`checkpoint_age_since_last_reward_edit` — newest `bots/<version>/data/checkpoints/*.zip` mtime
minus `reward_rules.json` mtime (`pathlib.Path.stat().st_mtime`); store as
`checkpoint_age_seconds` (int; negative ⇒ checkpoint newer than reward edit).

`is_stale = (trend flat OR falling) AND (reward_rules edited after the newest checkpoint, i.e.
checkpoint_age_seconds < 0)`. `reason` is a human-readable explanation of which condition(s) fired.

**Existing context.** Real read patterns to mirror: `bots/v0/learning/database.py`
`get_recent_win_rate` (l.415), `get_win_rate_by_model` (l.485), `get_all_model_stats` (l.506).
Version→path resolution: `src/orchestrator/registry.py`. Do NOT import from `bots.*` here.

**Produces.** New module + dataclass + CLI; export hook.

### Step 2: Extend Claude analysis prompt with `type: "soak"`

- **Problem:** Extend `/improve-bot-advised` Phase 2 to accept a `staleness_report` JSON block
  (from Step 1's CLI) in the analysis input and allow Claude to return a third improvement type
  `{"type": "soak", "soak_hours": N, "decision_mode": "hybrid", "rationale": "..."}` with
  `soak_hours ∈ [1, 4]`.
- **Type:** code
- **Issue:** #181
- **Status:** DONE (2026-06-20)
- **Flags:** --reviewers code
- **Files:** `.claude/skills/improve-bot-advised/SKILL.md` (Phase 2 prompt + response-schema example + constraints)
- **Depends on:** Step 1 (the `staleness_report` JSON shape comes from `StalenessReport`).
- **Done when:**
  - SKILL.md Phase 2 documents the `staleness_report` input block and how it is produced
    (`python -m orchestrator.staleness <version>`).
  - The response-schema example includes a `soak` entry with the `soak_hours ∈ [1,4]` constraint.
  - SKILL.md's Phase 2 "consider these factors" preamble references staleness.

**What to build.** Doc-only edit to the skill. Add the staleness input + soak output to the
existing JSON response schema; keep the existing `training`/`dev` types intact.

**Produces.** Updated SKILL.md only.

### Step 3: Phase 4 routing for `type: "soak"` (daemon lifecycle)

- **Problem:** When Phase 2 returns `type: "soak"`, Phase 4 routes it through a daemon-lifecycle
  path (save API-only backend state → graceful shutdown preserving `data/` → spawn
  `python -m bots.current.runner --serve --daemon --decision-mode hybrid` for the (clamped) duration →
  graceful daemon shutdown → restart API-only backend → continue dispatch), mirroring the existing
  `training` daemon pattern but with longer duration + hybrid mode.
- **Type:** code
- **Issue:** #182
- **Status:** DONE (2026-06-20)
- **Flags:** --reviewers code
- **Files:** `.claude/skills/improve-bot-advised/SKILL.md` (Phase 4 dispatch — add a `soak` case)
- **Depends on:** Step 2 (the improvement type must exist before routing).
- **Done when:**
  - SKILL.md Phase 4 has a `soak` case mirroring the `training` daemon lifecycle with
    `--decision-mode hybrid` and the requested (clamped) duration.
  - Mock test (in Step 5) feeding a `type: "soak"` improvement asserts the daemon lifecycle is
    invoked with the correct flags + duration. **No live soak in this step** — the real
    end-to-end soak is Step 6 (operator).
  - Runner flags verified to exist: `--serve` (runner.py:21), `--daemon` (255), `--decision-mode` (98).

**What to build.** SKILL.md Phase 4 edit only. Reuse the documented `training` daemon
start/stop sequence verbatim; the only deltas are duration and the `--decision-mode hybrid` flag.

**Produces.** SKILL.md Phase 4 update.

### Step 4: Budget guard for soak hours (wall-clock)

- **Problem:** Clamp a single soak to `min(remaining_wall_clock / 2, 4h)` so the loop can't
  consume itself with one oversized soak. "Remaining" is computed from the loop's existing shell
  budget — `--hours` minus elapsed (`$(date +%s) - <run start>`), the §4.2/§7.3 pattern — **not**
  a JSON state file (none exists). If Claude requests more, log a warning and clamp.
- **Type:** code
- **Issue:** #183
- **Status:** DONE (2026-06-20)
- **Flags:** --reviewers code
- **Files:** `.claude/skills/improve-bot-advised/SKILL.md` (Phase 4 — clamp before the Step 3 soak path)
- **Depends on:** Step 3 (routing must exist for the guard to wrap).
- **Done when:**
  - SKILL.md Phase 4 clamps `soak_hours` to `min(remaining_hours/2, 4)` using the shell wall-clock
    budget, logs a warning when clamping, and passes the clamped value to the Step 3 soak path.
  - A unit test (Step 5) simulates the budget math: `soak_hours=8`, `remaining=6h` → clamped `3h`.
  - The §6 gate ("soak ≤ 50% of total `--hours`") is enforced by this clamp.

**What to build.** SKILL.md Phase 4 edit: a clamp expression against the shell budget. No
state-file schema change (the original draft's `advised_run_state.json` extension was based on a
file that does not exist).

**Produces.** SKILL.md Phase 4 update.

### Step 5: Tests + smoke — staleness_signal + advised_soak_routing

- **Problem:** Cover the new code paths with unit tests, plus a §15.5 smoke that wires
  `compute_staleness` to a real per-version `training.db`.
- **Type:** code
- **Issue:** #184
- **Flags:** --reviewers code --isolation worktree
- **Files:** `tests/test_staleness_signal.py` (NEW), `tests/test_advised_soak_routing.py` (NEW)
- **Depends on:** Steps 1, 3, 4.
- **Done when:**
  - `tests/test_staleness_signal.py`: synthetic `training.db` (via `sqlite3`) with flat/rising/
    falling per-game result series + a temp dir mimicking `checkpoints/*.zip` and
    `reward_rules.json` with controlled mtimes; asserts `is_stale` fires only when trend
    flat/falling AND `checkpoint_age_seconds < 0`; `ValueError` on `< min_games`; threshold +
    window size configurable.
  - **Smoke (§15.5):** one test opens a real existing `training.db` (e.g. `bots/current/data/
    training.db`, skip-if-absent) and asserts `compute_staleness` returns a `StalenessReport`
    without exception — catches sqlite/path/schema drift the synthetic test can't.
  - `tests/test_advised_soak_routing.py`: mock Claude response `type: "soak"`; assert Phase 4
    invokes the daemon lifecycle with `--decision-mode hybrid` + clamped duration; assert no real
    daemon spawn (mocked subprocess).
  - `uv run pytest tests/test_staleness_signal.py tests/test_advised_soak_routing.py` green;
    mypy strict + ruff clean; all branches of `compute_staleness` covered.

**What to build.** Two test files per above. Reuse `tests/conftest.py` temp-dir fixtures and the
existing `tests/test_advised_*.py` patterns.

**Produces.** Two new test files (incl. the real-DB smoke).

### Step 6: End-to-end validation soak (operator)

- **Problem:** Run the full loop unattended to confirm staleness detection → soak scheduling →
  recovery works end-to-end against the live system. This is operator/wall-clock work (a multi-hour
  soak), not a `/build-step` code change — hence a separate `Type: operator` step (per §22; the
  original draft buried a 1h live soak inside a code step's Done-when).
- **Type:** operator
- **Issue:** #280
- **Depends on:** Steps 1–5 (all code shipped + green).
- **Done when (operator runs + reports):**
  1. With reward rules freshly edited, run `/improve-bot-advised --self-improve-code --hours 8`.
  2. Staleness detected on iteration 1 (reward edit newer than newest checkpoint).
  3. A `soak` improvement is emitted and runs to completion (2–4h, clamped ≤ 50% of budget).
  4. Post-soak deterministic eval shows a measurable Elo/WR delta (sign doesn't matter — proves
     the path works end-to-end).
  5. Loop resumes and finishes the remaining budget without starving other iterations.

**Operator commands.**
```powershell
# (edit a reward rule first so the policy is provably stale, then:)
uv run python -m orchestrator.staleness <current-version>   # sanity: is_stale should be true
# then the full run:
uv run improve-bot-advised --self-improve-code --hours 8
```

**What to look for.**

| Check | Expected |
|---|---|
| Iteration 1 staleness | `is_stale: true`, reason cites reward-edit-after-checkpoint |
| Soak emitted + clamped | One `type: soak`, duration ≤ min(remaining/2, 4h) |
| Daemon recovery | API-only backend restarts cleanly after the soak; `data/` intact |
| Budget | Soak-type time ≤ 50% of total `--hours` |
| End-to-end | Post-soak eval delta is non-zero; loop completes remaining budget |

## 4. Tests (full list)

- `tests/test_staleness_signal.py` — synthetic sqlite `games` series + controlled mtimes; the
  `is_stale` truth table; `ValueError` on `< min_games`; configurable threshold/window; plus the
  real-DB smoke.
- `tests/test_advised_soak_routing.py` — mocked Claude `type: soak`; Phase 4 invokes daemon with
  `--decision-mode hybrid` + clamped duration; budget-clamp math.

## 5. Validation

Step 6 (operator) is the end-to-end validation. Its 5 done-when checks are the gate evidence.

## 6. Gate

Steps 1–5 land green (tests + mypy strict + ruff). Step 6's 5 checks pass in a single run.
Soak-type improvements must not exceed 50% of total `--hours` budget across the run.

## 7. Kill criterion

Staleness fires on every iteration (false-positive storm) OR never fires across 3 runs with
obviously stale policies (false-negative). Either means the heuristic is wrong — revisit Step 1
(trend window/threshold, or the eval-vs-training commingling decision) before shipping.

## 8. Rollback

Revert the phase commits. `staleness.py` is a new, isolated module (no callers until the SKILL.md
edits land). SKILL.md changes are self-contained in `.claude/skills/improve-bot-advised/SKILL.md`.
No data migrations (the eval-isolation column is explicitly out of scope).
