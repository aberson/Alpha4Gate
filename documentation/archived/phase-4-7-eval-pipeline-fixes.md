# Phase 4.7 — Eval pipeline fixes (soak-2026-04-11b aftermath)

**Status:** DONE (2026-04-11) — all 5 steps landed, all 5 issues closed. 808 passed + 1 deselected (baseline 799 + 1; net +9 tests). mypy 45/45 strict-clean, ruff check clean.

**Commits (master):** `c37492d` (Step 1 / #82), `937da81` (Step 2 / #83), `ca4e73a` (Step 3 / #84), `8f8f9b4` (Step 4 / #85), `e43a5ab` (Step 5 / #86). Plus `e6dc0d0` docs(phase4.7) — plan + soak-4 run log + 5 GitHub issue references (the phase-start commit).

**GitHub issues:** #82 (Step 1, BLOCKER) — CLOSED. #83 (Step 2) — CLOSED. #84 (Step 3, sibling of #70) — CLOSED. #85 (Step 4, docs) — CLOSED. #86 (Step 5, docs) — CLOSED.

**Source soak run:** [soak-2026-04-11b.md](../soak-test-runs/soak-2026-04-11b.md)

**Master at plan draft:** `d65b14f` (all Phase 4.6 commits landed, pushed)

**Review history:** drafted 2026-04-11 post soak-4, reviewed 2026-04-11 via
`/plan-review` (16 items addressed — option picks locked, acceptance criteria
tightened, test mechanism specified, scope confirmed tight).

**Commit message convention:** `fix(phase4.7-stepN): <summary>` for code fixes,
`docs(phase4.7-stepN): <summary>` for docs-only steps — matching Phase 4.6 log
pattern (`git log --oneline master` shows `b27c6cc fix(phase4.6-step1): ...`
etc.).

## Schema reference (inline to keep the plan clean-context-readable)

**`data/training.db` — `games` table (columns verified against soak-4 DB)**

| Column | Type | Notes |
|---|---|---|
| `game_id` | TEXT PK | Currently suffixed `{base}_{uuid4.hex[:12]}` per Phase 4.6 Step 1 (`b27c6cc`). Primary key. |
| `map_name` | TEXT NOT NULL | e.g. `Simple64` |
| `difficulty` | INTEGER NOT NULL | 1-10, matches `DaemonConfig.current_difficulty` |
| `result` | TEXT NOT NULL | `win` / `loss` |
| `duration_secs` | REAL NOT NULL | Game duration in seconds |
| `total_reward` | REAL NOT NULL | Sum from `RewardCalculator` |
| `model_version` | TEXT NOT NULL | Checkpoint label, e.g. `v5` |
| `created_at` | TEXT NOT NULL | `datetime('now')` default |

**`data/training.db` — `transitions` table (relevant columns only)**

- `game_id` FK to `games.game_id`
- `state` — 17-dim observation vector (per `learning/features.py::FEATURE_DIM`)
- `action` — rule-engine action id
- `reward` — per-step reward
- `next_state` — 17-dim next observation
- `action_probs` — **currently 100% NULL** (Phase 4.6 Step 7 bonus finding,
  deferred to Phase 5 per investigation report)
- `game_time` / `game_time_secs` — cosmetic redundancy (both populated)

**`data/promotion_history.json` — history list schema (per Phase 4.6 Step 4 `dba72d7`)**

```json
{
  "history": [
    {
      "reason_code": "first_baseline | win_rate_gate | max_crashed | rollback | ...",
      "checkpoint": "v1",
      "promoted": true,
      "reason": "human-readable detail",
      "ts": "2026-04-11T14:00:00+00:00",
      ...
    }
  ]
}
```

Dashboard polls `GET /api/training/promotions/history` → returns `{history: [...]}`.

## Summary

Soak-4 (run id `2026-04-11b`) ran for ~70 minutes on master after Phase 4.6 and
surfaced a Phase 4.6 Step 1 regression that silently breaks the entire eval path.
Every eval game is flagged as "crashed" because the evaluator and the env
disagree on the game's `game_id`: the env appends a 12-char uuid suffix on every
`reset()` (Step 1 fix `b27c6cc`), but the evaluator's `_get_game_result` lookup
uses the pre-fix base id. All 12 eval games ran; all 12 rows landed in the
`games` table correctly (including **one actual win**, `eval_v5_3c9bd047_d7b2ef1dde4e`
with `total_reward=0.157, duration=619.8s`); none of them were visible to the
promotion gate. This is a hard blocker for the autonomous improvement loop and
the primary scope of Phase 4.7.

Soak-4 also reproduced two other issues that the Step 1 regression had been
masking: a `environment.py` env-thread-hang after sc2 `Status.ended` (5-minute
observation timeouts — 6 of 12 eval games), and a gap in the #73 watchdog that
doesn't poll during eval phase so `daemon.last_error` stayed `null` across 18
backend errors. Both are Phase 4.7 scope.

## Steps

### Step 1 — Fix evaluator / env `game_id` disagreement (BLOCKER)

**Issue:** #82

**Commit message:** `fix(phase4.7-step1): expose env.game_id for evaluator lookup`

**Status:** DONE (2026-04-11)

**Problem.**
`environment.py:172-173` regenerates `self._game_id` on every `reset()` by
appending a 12-char uuid hex suffix to the id supplied at construction time.
This was Phase 4.6 Step 1's collision fix for the trainer path (commit
`b27c6cc`). The evaluator, however, creates its own unique base id per game
(`eval_{checkpoint}_{uuid.hex[:8]}`) and immediately calls
`self._get_game_result(game_id)` with that pre-suffix id after the game
returns. The lookup exact-matches the primary key and returns `None` because
the row in the `games` table carries the suffixed id. Evaluator then flags
every game as `crashed`.

**Fix (locked during plan review — Option (a)).**

Add a read-only `@property` `SC2Env.game_id` returning `self._game_id`
(the post-`reset()` actual id). `evaluator._run_single_game` captures
`actual_id = env.game_id` after calling the first step/reset and uses
`actual_id` for the later `_get_game_result` call AND for all log
messages. The pre-reset base id that the evaluator constructs (`eval_{cp}_{uuid[:8]}`)
becomes a construction-time label only; it never escapes the first
`env.reset()` boundary.

Rejected options (for history):
- **(b)** `regenerate_game_id: bool = True` kwarg — adds a two-branch mode
  flag that both branches need tests; rejected for the complexity cost.
- **(c)** remove regeneration, callers supply unique ids — moves the
  collision logic up a layer and the trainer's per-cycle env reuse would
  need to regenerate anyway; rejected.

**Acceptance criteria.**
- `SC2Env.game_id` exists as a read-only `@property` that returns
  `self._game_id`. Docstring says: "The current post-reset game id this
  env will write to `games.game_id`. Callers that constructed the env
  with a base id MUST read this property after `reset()` before querying
  `TrainingDB.get_game_result`, because the env appends a per-reset uuid
  suffix."
- `evaluator._run_single_game` captures `actual_id = env.game_id` after
  `env.reset()` and uses `actual_id` for `self._get_game_result(actual_id)`.
- **Evaluator log messages use `actual_id`, not the pre-reset base id.**
  Lines to update: `_log.info("Eval game %d/%d ...")`, `_log.exception("Eval game %s crashed", ...)`,
  `_log.error("Eval game %s completed but no result row was recorded; treating as crashed", ...)`.
  Rationale: every ERROR/INFO log line must be `grep`-matchable against the
  `games.game_id` column of `training.db` so post-hoc debugging works.
- Nice-to-have cleanup (include if diff stays small): rename
  `_base_game_id` → `_construction_game_id` or drop it if nothing else
  reads it. Confirm via grep before dropping.
- After the fix, eval games' `games.game_id` column equals the id the
  evaluator uses to query for the result row.
- All 20 eval games in one eval run land as real `win`/`loss` outcomes,
  not `crashed`, unless they actually crashed.
- No regressions in the trainer path: collision protection from Step 1
  must still hold for multi-game env reuse (trainer's `_make_env` reuses
  one env across multiple `reset()` calls — each reset must produce a
  unique `game_id`).
- New integration test at `tests/test_evaluator_db_roundtrip.py` exercises
  the full round trip: `evaluator._run_single_game` with a real `SC2Env`
  (use a `FakeSC2Client` / mocked burnysc2 `run_game` that drives the env
  thread end-to-end) that writes through the real `TrainingDB`, and
  asserts `db.get_game_result(env.game_id)` returns non-None after the
  game returns.
- Regression unit test at `tests/test_environment.py` asserts
  `env._game_id` at DB write time equals `env.game_id` (property read)
  equals the id the evaluator would look up — exact equality, not
  `startswith`. Also asserts two consecutive `reset()` calls on the same
  env produce two different `game_id` values (trainer collision protection).

**Audit — deliverable required before PR opens.**
Before writing the fix, grep all callers of `TrainingDB.get_game_result`
and every site that passes a `game_id` to `SC2Env.__init__`. Attach the
audit result as a comment on the Step 1 GitHub issue with one row per
caller:

```
<file>:<line>  <caller>  <verdict: OK | needs fix | already handled>
```

Verdicts:
- **OK**: caller already reads `env.game_id` post-reset or never queries
  the DB by the construction-time id.
- **needs fix**: same bug class as evaluator — fix in this PR.
- **already handled**: caller uses a different code path that bypasses
  the regeneration (e.g. direct DB write, not through `SC2Env`).

Goal: no "needs fix" rows missed. This is the one-grep audit that Phase 4.6
Step 1's gauntlet failed to do.

---

### Step 2 — Extend #73 watchdog to cover eval phase

**Issue:** #83

**Status:** DONE (2026-04-11)

**Problem — confirmed by source read of `daemon.py::_run_training`:**

```
line 543:  self._start_watchdog()                                # watchdog starts
line 561:  result = orchestrator.run(n_cycles=..., ...)          # TRAINING CYCLES ONLY
line 575:  self._stop_watchdog()                                  # watchdog STOPS here
...
line 620:  decision = pm.evaluate_and_promote(latest_cp, diff)   # EVAL runs HERE
...
line 770:  self._stop_watchdog()  # in finally: belt-and-braces (idempotent)
```

The eval phase is not inside `orchestrator.run()`. It's driven from
`PromotionManager.evaluate_and_promote()` which the daemon calls AFTER
`orchestrator.run()` returns. Line 575's explicit `_stop_watchdog()` was
placed there in the #73 design (iter 2) to "eliminate the race between the
two writers of `_last_error`" before the post-cycle bookkeeping block. That
choice was fine when eval was out of the #73 scope; soak-4 proved it is not
fine now. `daemon.last_error` stayed `null` for the entire eval crash storm
(18 accumulated backend errors, >5 above the watchdog threshold).

**Fix sketch (minimal diff).**
Move the line-575 `_stop_watchdog()` call down to after the promotion gate
block (after line ~672) and the rollback check block (lines 674+). The
`finally`-block `_stop_watchdog()` at line 770 stays — it's documented
idempotent and covers the exception path.

The watchdog's `_last_error` writer already takes `self._lock`, so the
race concern the iter-2 comment mentions is already handled by the lock;
the explicit post-`orchestrator.run()` stop was a belt-and-braces
defense that we can relax now that we want the watchdog's scope
broadened.

**Pre-implementation audit — happy-path ERROR log scan (mandatory).**

Before moving the `_stop_watchdog()` call, grep `src/alpha4gate/learning/`
for any `_log.error` / `logging.error` / `_log.exception` calls that fire
on the NORMAL (non-crash) path — i.e. legitimate errors that don't
represent a real failure. The current evaluator + environment + promotion
code DOES emit `_log.error` lines in known crash paths (#67 `no result
row was recorded`, env `Timeout waiting for observation`, etc.), and
those are FINE — they represent genuine errors. The question is: does
anything log at ERROR level when a game or eval completes successfully?

Deliverable: attach grep result as a comment on the Step 2 issue listing
every `_log.error` call site in `evaluator.py` / `environment.py` /
`promotion.py` / `daemon.py`, with one of:
- **crash path** (OK — watchdog SHOULD count this)
- **happy path** (needs downgrade to WARNING before Step 2's watchdog
  extension lands, otherwise false positives)
- **removed/unreachable** (ignore)

If any "happy path" ERRORs exist, **downgrade them to WARNING** in this
step, OR bump `watchdog_error_threshold` for eval. Prefer the downgrade:
WARNING + structured log level is the cleaner signal.

**Acceptance criteria.**
- Watchdog polls for the entire duration of a daemon-triggered training
  run, including `evaluate_and_promote` and the rollback check. Verify by
  reading `daemon.py::_run_training` post-fix and confirming
  `_stop_watchdog()` is called only AFTER the promotion gate + rollback
  blocks complete (the `finally` block's idempotent call remains).
- New test extends `tests/test_daemon.py::TestWatchdogPerCycleCrashVisibility`
  (don't create a new test file — extend the existing class per Phase 4.5
  #73's structure). Test name: `test_watchdog_fires_on_eval_phase_errors`.
- **Test mechanism** (specified to avoid the Phase 4.5 #68 iter-1 mistake):
  use the REAL Python logging API, not `get_error_log_buffer().emit(...)`.
  Pattern:

  ```python
  test_logger = logging.getLogger("alpha4gate.learning.evaluator")
  # ... patch pm.evaluate_and_promote to call test_logger.error(...) five times ...
  daemon._run_training()
  assert daemon.last_error is not None
  assert daemon.last_error.startswith("Watchdog:")
  ```

  The iter-2 lesson from #68 (per `memory/alpha4gate.md`): "rewrote the
  end-to-end test to use the real logging API instead of bypassing to
  `buffer.emit()` directly." Don't repeat that mistake.
- Existing #73 tests still pass unchanged: one-shot exit, stop-event exit,
  baseline reset on retry, `_start_watchdog` inside try-block, join-timeout
  zombie handling (all per `memory/alpha4gate.md` Phase 4.5 #73 commit
  `ea04caa`/`7431fc8`).
- No false positives during normal eval: add a second test
  `test_watchdog_silent_on_happy_path_eval` that patches
  `pm.evaluate_and_promote` to return a success `PromotionDecision` without
  logging any errors, runs `_run_training`, and asserts `daemon.last_error`
  is `None`.

**Audit.** Soak-4 is the authoritative evidence: 18 errors during eval
with `last_error=null`. The new test must reproduce that condition and
prove the post-fix watchdog catches it.

---

### Step 3 — Fix env teardown hang in eval path (flavor A crashes)

**Issue:** #84 (sibling of #70 — filed as new issue per operator preference, not a reuse)

**Commit message:** `fix(phase4.7-step3): push terminal sentinel on _obs_queue at game-end`

**Status:** DONE (2026-04-11)

**Problem.**
Six of twelve eval games in soak-4 hit the pattern:

```
sc2.main Status.ended + Result for player 1: Defeat
<5 minutes elapse>
environment.py ERROR Timeout waiting for observation from game thread
evaluator ERROR Eval game X completed but no result row was recorded
```

The 5-minute wait is the `_obs_queue` consumer's observation timeout. It fires
because the game thread, which puts observations on `_obs_queue`, never sends
a terminal sentinel after sc2 reports the game has ended. After Step 1 fixes
the evaluator lookup, these stalls still waste 5 minutes per occurrence, so a
20-game eval would take at minimum `6 * 5 = 30` minutes of dead time (or up
to 50 min if the flavor-A rate extrapolates from 50% to 50% across all 20
games; see projection caveat below).

**Projection caveat.** Soak-4 observed 6 flavor-A crashes in 12 games (50%).
That rate is not guaranteed to hold for 20 games — it could be lower if the
bug is coupled to specific map/game-state conditions, or higher. Plan Step 3
around the worst case (10 stalls × 5 min = 50 min) when sizing the soak-5
window.

**Interaction with Phase 4.5 #72's `_resign_and_mark_done` helper.**
`memory/alpha4gate.md` documents commit `f954d4b`/`7369329` (#72), which added
`_FullTrainingBot._episode_done` + `_resign_and_mark_done()` to handle the
**early-termination** paths: timeout, shutdown-sentinel, `queue.Empty`. That
helper fires when the bot decides to leave the game voluntarily.

Step 3 addresses a DIFFERENT path: the **normal end-of-game** path where SC2
itself transitions to `Status.ended` (Victory or Defeat), which happens
outside the bot's control and doesn't go through `_resign_and_mark_done`.

**Scope decision (locked during plan review):** Step 3's fix lives in
`environment.py::_run_game_thread`'s `finally` block (or adjacent), pushing a
terminal sentinel onto `_obs_queue` once `run_game` returns. This is adjacent
to but separate from #72's helper — it covers the natural game-end case that
#72 was never designed for.

**Fix sketch.**
In `_run_game_thread`, after `run_game(...)` returns (whether by normal
game-end or exception), push a terminal marker onto `self._obs_queue`.
Terminal marker should be a distinguishable value the consumer loop can
recognise — either a module-level `_GAME_ENDED` sentinel singleton or
`None`-as-terminator if not already reserved. Consumer side: when it
pulls the sentinel off the queue, exit the observation wait loop
immediately (instead of waiting the 5-min `_obs_timeout`).

Important: the sentinel push must happen **unconditionally** in the
`finally` block, so it fires for both normal game-end AND exception
teardown. That means the consumer must handle the sentinel appearing
after a clean game-end OR after a crash with equal grace.

**Acceptance criteria.**
- `_run_game_thread` pushes a terminal sentinel onto `self._obs_queue`
  in its `finally` block (unconditionally) after `run_game` returns.
- `_obs_queue` consumer recognises the sentinel and exits the
  observation wait loop in <1 second instead of waiting 5 minutes.
- Regression test at `tests/test_environment_teardown.py` — **unit-level,
  does NOT launch a real SC2 game**. Pattern:

  ```python
  def test_obs_queue_consumer_exits_on_sentinel() -> None:
      env = SC2Env(... minimal construction ...)
      # Drive _obs_queue manually:
      env._obs_queue.put(_GAME_ENDED_SENTINEL)
      # Call the consumer method (or env.step() if the consumer is inlined):
      result = env._wait_for_next_observation()  # or whatever the method is
      assert result is None  # or whatever "game ended" returns
      # Should complete in < 0.1s, not 300s
  ```

  This test is the "consumer loop in isolation against a manually-driven
  queue" approach — chosen over burnysc2 monkey-patching during plan
  review. **Does not couple to burnysc2 internals** → no fragility on
  upstream version bumps.
- Second regression test: `test_obs_queue_consumer_exits_on_sentinel_after_games`
  — put a few fake observations then the sentinel, assert consumer
  processes the real obs then exits on sentinel.
- Third test: integration-style smoke test at
  `tests/test_environment_teardown.py::test_run_game_thread_pushes_sentinel_on_finally`
  — mock `run_game` to return immediately, call `_run_game_thread`, assert
  `_obs_queue` contains the sentinel.
- No regressions in Phase 4.5 #72's early-termination path. Existing
  `_resign_and_mark_done` and `_episode_done` tests in the suite still
  pass unchanged.
- Eval-game wall-clock time drops to roughly the actual game duration + a
  handful of seconds of teardown — verify against soak-5.

**Nice-to-have defensive observability (include if diff stays small):**
add a WARNING log at the 30-second mark of waiting for an observation
(currently the `_obs_timeout` is the only signal, at 300s). Something
like: `_log.warning("Observation queue idle 30s; game may be ending.")`
Turns silent stalls into a visible pattern if the sentinel push ever
regresses.

**Blocked on Step 1** because without Step 1 the flavor-A crashes look
identical to the flavor-B "no result row" bug — you cannot validate
Step 3's fix while every game is also misclassified as crashed.

---

### Step 4 (docs) — Fix soak-test.md §3.2 line-count gate

**Issue:** #85

**Commit message:** `docs(phase4.7-step4): replace soak-test.md §3.2 stale line-count gate`

**Status:** DONE (2026-04-11)

**Problem.**
soak-test.md §3.2 says: "The line count must be greater than 50 within 30
seconds of launch." Actual current uvicorn + daemon startup emits **7
substantive lines** (1 VIRTUAL_ENV warning + 2 daemon lines + 4 uvicorn banner
lines). The gate is stale and would force an abort on every current soak.
soak-4 had to be hand-waived past this gate (noted in the run log as a
finding candidate at 07:06:40).

**Acceptance criteria.**
- §3.2 pre-flight gate replaced with a substring grep for `"Uvicorn running on"`
  and `"Training daemon started"` within 30 seconds of launch. Both must
  match for the pre-flight to pass.
- Also grep for `"Traceback"` / `"ERROR"` in the same window → abort if present.
- Document the current baseline (~7 lines) as reference, so future operators
  know what "healthy" startup looks like.
- soak-test.md §3.2 also mentions `wc -l logs/...` as part of the gate;
  remove that instruction (replaced by the substring grep).
- Reviewer should also `grep -n "line count" documentation/` for any other
  stale references that need updating.

---

### Step 5 (docs) — Rename misleading trainer "10 games" log line

**Issue:** #86

**Commit message:** `docs(phase4.7-step5): rename trainer "10 games" log to timestep budget`

**Status:** DONE (2026-04-11)

**Problem.**
`trainer.py` emits `Training: 10 games, ~150 timesteps` at cycle start. Under
`realtime=False`, one game consumes the full 150-timestep PPO budget, so the
"10 games" label is aspirational. An operator watching logs for 10 sc2 game
completions per cycle will be confused — soak-4 operator was, and the finding
went into the run log as a doc/log cleanup item.

**Acceptance criteria.**
- Log line renames from "N games" to the actual PPO learn-budget description:
  e.g. `Training cycle K: PPO.learn(total_timesteps=150)`.
- Documented somewhere in `documentation/wiki/training-pipeline.md` (the
  existing wiki page per `memory/alpha4gate.md`) that under `realtime=False`,
  "cycle" and "game" are decoupled and cycle duration is dominated by the
  PPO timestep budget, not by game count.
- Grep `src/alpha4gate/learning/` for other "N games" / "games_per_cycle"
  log strings that suffer the same misleading wording — update any siblings
  found.

---

## Investigation outcomes from Phase 4.6 — disposition

Phase 4.6 Steps 6 and 7 shipped as investigation-only reports. Both are
read here and dispositioned for Phase 4.7:

### Phase 4.6 Step 6 — headless SC2 investigation

**Report:** [documentation/headless-sc2-investigation.md](../headless-sc2-investigation.md)

**Recommendation in report:** _Not feasible / not worth it within current
dependency surface — defer._

**Disposition for Phase 4.7: OUT OF SCOPE, no step, no follow-up issue.**

Key facts from the report:
- burnysc2 v7.1.3 does not expose a launch-arg pass-through in `run_game`,
  `run_multiple_games`, or `SC2Process.__init__`. The only way to inject
  `-HeadlessNoRender` is a monkey-patch of `SC2Process._launch` or a
  library fork. Both fail the "clean wiring, < 20 lines, feature-flagged,
  mockable test" bar.
- `-HeadlessNoRender` is a pysc2 / Linux-package flag; there is no public
  evidence that the Windows retail `SC2_x64.exe` binary honours it. A
  speculative patch may buy nothing.
- The original motivation (speeding up eval games) was **misdiagnosed**.
  Phase 4.6 Step 1 partially and Phase 4.7 Step 3 now conclusively trace
  slow eval games to **env-teardown hangs**, not rendering cost. Fixing
  those closes the eval speedup need on its own.

The screen-off / focus-contention benefits the operator cared about are
available today with OS-level tooling (minimize window, Windows focus
assist, separate user session) — no code change required. If a future
engineer wants to revisit, the report's Section 6 specifies a 30-minute
`scripts/try_headless_monkey_patch.py` empirical check as the cheapest
next step; that would live outside `src/` and outside CI.

### Phase 4.6 Step 7 — win-probability forecast investigation

**Report:** [documentation/win-probability-forecast-investigation.md](../win-probability-forecast-investigation.md)

**Recommendation in report:** _Option (c) heuristic first, then Option (b)
learned classifier once labeled-game pool reaches ≥50. Option (a) value
head on PPO deferred indefinitely._

**Disposition for Phase 4.7: OUT OF SCOPE for the body of the plan. Three
follow-up issues to file AFTER 4.7 ships — NOT in 4.7's issue list.**

Rationale: Phase 4.7's purpose is to unblock the autonomous improvement
loop (which the eval-pipeline blocker has crippled). Win-probability is a
debug-surface / model-quality enhancement that is only meaningful once
the loop is producing real labeled games at volume — which is blocked on
Step 1. So Phase 4.7 has to land first. Also, the report's Section 5
prototype already proved that **the learned model (Option b) overfits on
the 8 labeled games available** at time of investigation; more labels
are needed before (b) can be validated honestly.

Follow-up issues to open **after Phase 4.7 lands, scoped to Phase 5**:

1. **"Win-probability heuristic (Option c) + `transitions.win_prob`
   column"** — implements the Section 5 heuristic formula as
   `winprob_heuristic.py`, adds the column via
   `database._LATER_ADDED_COLS`, writes per-transition from
   `SC2Env.step`, and logs `winprob=%.2f` every 10 decision steps. Do NOT
   wire into the reward function; do NOT add a frontend curve. Unit test
   the heuristic on synthetic snapshots.
2. **"Capture `action_probs` in training.db (currently 100% NULL)"** —
   a Phase 4.6 Step 7 bonus finding. `_GymStateProxy.last_probabilities`
   is never populated during eval or training, so `transitions.action_probs`
   is unusable. This is an independent bug from the eval blocker but
   lives in the same learning/ area; fixing it is a prerequisite for any
   future Option (a) value-head work. **Could be pulled into Phase 4.7
   as an optional Step 6 if the operator wants** — see "Optional Phase 4.7
   Step 6" below.
3. **"Grow labeled-game pool to ≥50 before win-probability Option (b)
   training"** — blocking gate for learned-classifier work. Not code,
   just a tracking issue that pins the rule of thumb (10× feature count
   after game-level aggregation).

### Phase 4.7 Step 6 — DEFERRED to Phase 5

Locked during plan review: **not in Phase 4.7.** Filed as Phase 5 issue #2
in the win-probability follow-up list above (`Capture action_probs in
training.db (currently 100% NULL)`). Rationale: keep Phase 4.7 tight to the
eval unblock + adjacent fixes. Pulling in an independent data-capture bug
bloats the phase with an unrelated concern.

Operator noted: "it would be cool to see" — which is a real signal that
this should land soon after Phase 4.7, not buried indefinitely. Phase 5
issue #2 should be prioritised accordingly.

## Other items out of scope for Phase 4.7

- **`append_stats_game` O(N²) per game** (Phase 4.6 Step 2 LOW finding).
  Optimisation, not a correctness bug.
- **`PromotionLogger` concurrent-writer file lock** (Phase 4.6 Step 4 LOW).
  Pre-existing, three writers without lock — add if Phase 4.7 lands before
  multi-writer exposure.
- **Frontend `PromotionHistoryEntry` TypeScript type missing `reason_code?`**.
  Frontend polish, defer until a UI-touching phase.
- **#74 stop-path cleanup**. soak-4 hit the `POST /api/training/stop` path
  mid-eval and it returned cleanly; the #74 nice-to-have may be less urgent
  than previously thought. Re-evaluate after Step 1.
- **Seed log double "Saved replay to..." lines** — cosmetic finding from soak-4,
  seed game logs the replay-save line twice with identical path. Investigate
  only if it turns out to be a real double-write rather than double-log.

## Dependencies & sequencing

- **Steps 1 and 2 are independent** and can run in parallel worktrees.
- **Step 3 is blocked on Step 1** because without Step 1 the symptoms
  overlap and validation is impossible.
- **Steps 4 and 5 are docs-only** and can land anytime.

## Quality gate between every step (per `AGENTS.md`)

Every step must pass all three gates before the PR opens. Run in order:

```bash
uv run mypy src                      # 45/45 strict-clean expected
uv run ruff check .                  # clean expected
uv run pytest -q                     # full suite, no failures
```

After the PR lands on master, the next step's worktree rebases on the new
master before starting. Steps running in parallel (1 + 2) must converge on
master with a clean merge — watch for `daemon.py` edits in Step 2 potentially
conflicting with any `environment.py` edits in Step 1 that touched nearby
module boundaries (unlikely but possible).

## Reviewer preset per step (per `feedback_plan_reviewer_flags.md`)

| Step | Reviewers | Justification |
|---|---|---|
| 1 | `code` | Backend-only fix + new integration test; no runtime/UI to validate via `full`. |
| 2 | `code` | Backend-only daemon change + regression test. |
| 3 | `code` | Backend-only env teardown fix + regression test. |
| 4 | `code` | Docs-only, but the reviewer should grep for other stale line-count references. |
| 5 | `code` | Docs + one log-string change. |

No step requires `--reviewers full` or a `--start-cmd` / `--url` pairing.

## Soak-5 launch criteria

After Phase 4.7 lands:
- Fresh `data/` (Option B), seed via `--batch 1 --difficulty 1`, carry prior
  tuning files if desired.
- First eval run should produce **non-zero real win/loss counts** in the
  `games` table AND in the evaluator's return value. This is the Step 1
  validation — every eval game that actually completed on the sc2 side
  should land as `win` or `loss`, never `crashed`.
- `/api/training/promotions/history` should have **exactly one entry**
  with `reason_code` set. **Expected value: `win_rate_gate` refusal**
  (because v1's win rate after 5 cycles of fresh PPO training will almost
  certainly be << 80% — soak-4 saw 8.3%). A `first_baseline` auto-promote
  is ALSO a valid outcome, but the realistic soak-5 expectation is a
  principled refusal with the correct reason_code — that validates Phase
  4.6 Step 4's promotion logger end-to-end, which soak-4 could not reach.
- `daemon.last_error` should stay `null` throughout a clean run. Any
  non-null value is either a real failure OR Step 2's watchdog firing on
  an unexpected ERROR-level log — both are findings worth capturing.
- Eval wall-clock per game should drop from 3-9 min (soak-4) to ~2-4 min
  (Step 3 teardown fix working).
- Target duration: 4 hours, `N promotions = 1` as alternate stop — where
  "promotions" counts both promote and refuse decisions on the
  `promotions/history` endpoint. Accept either a `first_baseline` or a
  `win_rate_gate` entry as success.

## Test expectations

After Phase 4.7 lands, pytest count should grow by ~11-16 tests:
- Step 1: ~5 tests (integration round-trip + regression + two-reset
  collision test + log-line audit spot-check + any "needs fix" callers
  found by the audit).
- Step 2: ~3 tests (eval-phase watchdog fires; happy-path watchdog silent;
  plus whichever of the iter-1 #73 tests need to extend to cover the
  widened watchdog lifetime).
- Step 3: ~3 tests (consumer exits on sentinel from empty queue; consumer
  exits on sentinel after real obs; `_run_game_thread` pushes sentinel in
  `finally`).
- Steps 4, 5: 0 tests (docs/strings only).

Pre-Phase 4.7 baseline: **799 passed + 1 deselected**. Expected post-4.7:
**~810-820 passed + 1 deselected**. mypy should stay at 45/45 strict-clean
(no new source files introduced — Step 1's integration test adds to
`tests/`, not `src/`).
