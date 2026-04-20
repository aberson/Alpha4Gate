# Phase 7 Build Plan — Advised loop stale-policy detection

**Parent plan:** [alpha4gate-master-plan.md](alpha4gate-master-plan.md) — Phase 7
**Track:** Operational
**Prerequisites:** Phase 5 (sandbox + skill integration). Independent of B/D/E/6 — ships standalone.
**Effort estimate:** ~1 day build + one overnight validation run.
**Status:** Drafted, not yet started. Detail extracted from the master plan
on 2026-04-19 as part of the plan/build-doc cleanup.

## 1. What this feature does

Teaches `/improve-bot-advised` to recognize when the PPO policy is
stale relative to the current reward / hyperparam config, and to
schedule an extended training soak as a first-class improvement type —
rather than relying on the user to manually switch to
`/improve-bot --mode training`.

Today the advised loop's `training` path only does a 2-game sync soak
(Phase 6.3 of the existing skill) — enough to create a checkpoint, not
enough to actually train PPO against new rewards. The loop can iterate
on reward rules forever without the policy ever catching up. This
phase closes that gap.

## 2. Existing context

- **`/improve-bot-advised`** (`.claude/skills/improve-bot-advised/SKILL.md`)
  — autonomous loop with Phase 0 bootstrap, Phase 2 Claude analysis
  (returns improvements list), Phase 4 dispatch by improvement type.
- **Phase 4 Elo ladder** (`src/orchestrator/ladder.py`,
  `data/bot_ladder.json`) — provides the deterministic eval history
  needed to compute WR trends.
- **`bots/current/data/checkpoints/*.zip`** — PPO checkpoints; mtime
  reveals when training last ran.
- **`bots/current/data/reward_rules.json`** — reward config; mtime
  reveals when rules were last edited.

## 3. Scope (build steps)

| Step | Issue | Description |
|------|-------|-------------|
| 7.1  | #180  | `src/orchestrator/staleness.py` — `StalenessReport` dataclass |
| 7.2  | #181  | Extend Claude analysis prompt with `type: "soak"` improvement |
| 7.3  | #182  | Phase 4 routing for `type: "soak"` (daemon lifecycle) |
| 7.4  | #183  | Budget guard for soak hours |
| 7.5  | #184  | Tests: staleness_signal + advised_soak_routing |

### Step 7.1: `staleness.py` — StalenessReport dataclass

**What to build.** Create `src/orchestrator/staleness.py` exporting a
`StalenessReport` dataclass and a `compute_staleness(version) ->
StalenessReport` function. The report combines two signals:

- `eval_wr_trend`: slope of WR across the last 3 deterministic evals
  for the version's current best checkpoint.
- `checkpoint_age_since_last_reward_edit`: time delta between the
  newest `bots/<version>/data/checkpoints/*.zip` mtime and the
  `bots/<version>/data/reward_rules.json` mtime.

`is_stale = (trend_flat OR trend_falling) AND
(checkpoint_age_since_last_reward_edit > 0)` — i.e., reward rules were
edited after the policy last trained, AND the eval trend is not improving.

**Existing context.**
- Phase 4's ladder log: `data/bot_ladder.json` (or registry) holds the
  deterministic-eval history per version. `ladder.py::history(version,
  n=3)` is the read API.
- Per-version data dir: `bots/<version>/data/checkpoints/` and
  `reward_rules.json`. Use `pathlib.Path.stat().st_mtime`.
- Eval trend: simple linear regression on (eval_index, win_rate);
  classify as flat / rising / falling using a configurable slope
  threshold (default 0.01 WR per eval).

**Files to modify/create.**
- `src/orchestrator/staleness.py` (NEW)
- `tests/test_staleness_signal.py` (NEW; covered in Step 7.5)

**Done when.**
- `StalenessReport` is a frozen dataclass with fields:
  `is_stale: bool`, `eval_wr_trend: float`, `checkpoint_age_seconds: int`,
  `last_eval_wrs: tuple[float, ...]`, `reason: str`.
- `compute_staleness(version)` returns a populated report; raises
  `ValueError` if version has fewer than 3 evals.
- Public surface exported in `src/orchestrator/__init__.py`.

**Flags (recommended).** `--reviewers code --isolation worktree`

**Depends on.** none.

**Produces.** New module + dataclass; export hook.

### Step 7.2: Extend Claude analysis prompt with `type: "soak"`

**What to build.** `/improve-bot-advised` Phase 2 (Claude analysis)
currently accepts improvement types `training` and `dev`. Extend the
prompt to:
- Accept a `staleness_report` JSON block in the input (computed via
  Step 7.1 before the Claude call).
- Allow Claude to return a third type:
  `{"type": "soak", "soak_hours": N, "decision_mode": "hybrid",
  "rationale": "..."}`.
- Update response-schema example in SKILL.md to include the soak type
  with constraints (`soak_hours ∈ [1, 4]`).

**Existing context.**
- `.claude/skills/improve-bot-advised/SKILL.md` Phase 2 has a Claude
  prompt template with existing improvement types and a JSON response
  schema. Extending requires adding to the schema example AND updating
  the prompt's "consider these factors" preamble to include staleness.

**Files to modify/create.**
- `.claude/skills/improve-bot-advised/SKILL.md` — Phase 2 prompt
  section; response-schema example; constraint description.

**Done when.**
- SKILL.md Phase 2 documents the `staleness_report` input block.
- Schema example includes a `soak` improvement entry.
- A doc-fragment unit test (or manual review) confirms the prompt
  parses `staleness_report` correctly when included.

**Flags (recommended).** `--reviewers code` (no source code changed
beyond SKILL.md; no isolation needed).

**Depends on.** Step 7.1 (StalenessReport must exist for the prompt
input shape).

**Produces.** Updated SKILL.md only.

### Step 7.3: Phase 4 routing for `type: "soak"` (daemon lifecycle)

**What to build.** When Phase 2 Claude returns a `type: "soak"`
improvement, Phase 4 of the skill routes it through a different code
path than `training`/`dev`:

1. Save current API-only backend state.
2. Shut down API-only backend gracefully (preserve `data/`).
3. Spawn `python -m bots.v0.runner --serve --daemon
   --decision-mode hybrid` for `soak_hours` hours.
4. After soak completes (or wall-clock cap hits): graceful daemon
   shutdown, then restart API-only backend.
5. Continue Phase 4 dispatch.

**Existing context.**
- `.claude/skills/improve-bot-advised/SKILL.md` Phase 4 already has
  daemon-lifecycle handling for the `training` type (Phase 6.3 of
  the skill). Reuse that pattern verbatim — the only difference is
  duration (longer for `soak`) and `--decision-mode hybrid` flag.
- Phase 6.3 calls `daemon_start.sh` / `daemon_stop.sh` (or equivalent
  inline commands).

**Files to modify/create.**
- `.claude/skills/improve-bot-advised/SKILL.md` — Phase 4 dispatch
  section; add a `soak` case mirroring the `training` daemon pattern
  with longer duration and hybrid mode.

**Done when.**
- Mock test: feeding a `type: "soak"` improvement into Phase 4 invokes
  the daemon lifecycle with correct flags + duration.
- Real run: a 1h test soak runs to completion, daemon comes back
  cleanly, API-only restart succeeds, Phase 4 continues.

**Flags (recommended).** `--reviewers code`

**Depends on.** Step 7.2 (improvement type must exist before routing).

**Produces.** SKILL.md Phase 4 update.

### Step 7.4: Budget guard for soak hours

**What to build.** Soak hours debit the run's `--hours` wall-clock
budget. Cap a single soak at `min(remaining_budget / 2, 4h)` so the
loop can't consume itself with a single oversized soak. If Claude
requests more, emit a warning and clamp.

**Existing context.**
- `/improve-bot-advised` already tracks wall-clock via Phase 0
  bootstrap timestamp + `--hours` arg.
- `data/advised_run_state.json` has `started_at`, `hours_budget`,
  `hours_consumed` fields — extend if needed for soak debit.

**Files to modify/create.**
- `.claude/skills/improve-bot-advised/SKILL.md` Phase 4 — clamp soak
  duration to budget cap before invoking Step 7.3 path.
- Possibly small extension to `advised_run_state.json` schema
  (frontend cache key bumped).

**Done when.**
- A mock Claude response with `soak_hours: 8` and `--hours 6` budget
  remaining → soak gets clamped to 3h (half of remaining), warning
  logged.
- `advised_run_state.json` shows `hours_consumed` updated after soak.
- A unit test simulates the budget math.

**Flags (recommended).** `--reviewers code`

**Depends on.** Step 7.3 (routing must exist for budget guard to wrap).

**Produces.** SKILL.md Phase 4 update; possibly state-file schema
extension.

### Step 7.5: Tests — staleness_signal + advised_soak_routing

**What to build.** Two test files covering the new code paths.

`tests/test_staleness_signal.py`:
- Fixture: synthetic ladder history with flat / rising / falling WR
  trends.
- Fixture: temp dir mimicking `bots/<v>/data/checkpoints/*.zip` and
  `reward_rules.json` with controlled mtimes.
- Cases: `is_stale` only fires when trend flat AND checkpoint older
  than reward edit; raises on <3 evals; thresholds configurable.

`tests/test_advised_soak_routing.py`:
- Mock Claude response returning `type: "soak"`.
- Assert Phase 4 invokes daemon-lifecycle helpers with
  `--decision-mode hybrid` and the requested duration (clamped if
  needed per Step 7.4).
- Assert no actual daemon spawn (mocked subprocess).

**Existing context.**
- `tests/conftest.py` has fixtures for temp data dirs.
- Existing `tests/test_advised_*.py` files for SKILL-related testing
  patterns.

**Files to modify/create.**
- `tests/test_staleness_signal.py` (NEW)
- `tests/test_advised_soak_routing.py` (NEW)

**Done when.**
- Both files green on `uv run pytest tests/test_staleness_signal.py
  tests/test_advised_soak_routing.py`.
- Mypy strict + ruff clean.
- Coverage of all branches in `staleness.py::compute_staleness`.

**Flags (recommended).** `--reviewers code --isolation worktree`

**Depends on.** Steps 7.1, 7.3, 7.4.

**Produces.** Two new test files.

## 4. Tests

- `tests/test_staleness_signal.py` — fixtures for flat/rising/falling
  WR trends and varying checkpoint ages; confirm `is_stale` fires only
  when trend flat AND checkpoint older than last reward edit.
- `tests/test_advised_soak_routing.py` — mock Claude response with
  `type: "soak"`; confirm Phase 4 invokes daemon with
  `--decision-mode hybrid` for requested hours and respects budget cap.

## 5. Validation

Run `/improve-bot-advised --self-improve-code --hours 8` with reward
rules recently edited. Loop must:

1. Detect staleness on first iteration (reward edit newer than checkpoint).
2. Emit a `soak` improvement.
3. Run the soak to completion (2–4h).
4. Post-soak deterministic eval shows measurable Elo delta (sign doesn't
   matter for the gate — just proves the path works end-to-end).
5. Loop resumes and completes remaining budget without starving other
   iterations.

## 6. Gate

All 5 validation steps pass in a single run. Soak-type improvements
must not exceed 50% of the total `--hours` budget across the run.

## 7. Kill criterion

Staleness signal fires on every iteration (false-positive storm) OR
never fires across 3 runs with obviously stale policies
(false-negative). Either means the heuristic is wrong — revisit step
7.1 before shipping.

## 8. Rollback

Revert the phase's commits on `bots/current/` and
`src/orchestrator/staleness.py`. SKILL.md change is self-contained in
`.claude/skills/improve-bot-advised/SKILL.md`. No data migrations.
