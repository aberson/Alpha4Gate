# Alpha4Gate — Always-Up Autonomous Improvement Plan

## Vision

Transform Alpha4Gate from a manually-operated SC2 bot into an **always-up autonomous
improvement platform** — a system that plays, evaluates, trains, and improves
continuously with full transparency into every stage. The SC2 bot is one domain
implementation; the evaluation/training/monitoring loop is the real product.

## Principles

- **Transparency first** — every decision, training cycle, and improvement should be
  observable and explainable through the dashboard
- **Domain-agnostic core** — training, evaluation, and monitoring should not assume SC2;
  the bot is a pluggable component
- **Progressive automation** — each phase reduces manual steps until the system runs
  unattended

## How to read this plan

Each step has a `**Flags:**` line like `--isolation worktree --reviewers code`. Those
are arguments to the `/build-step` skill (defined in `dev/.claude/skills/build-step/SKILL.md`),
which is the standard way of executing one build step end-to-end. Phases are run
via `/build-phase --plan documentation/plans/always-up-plan.md`, which iterates
build-step over each step in order. A fresh model running this plan should treat
those flags as runtime knobs for build-step, not as anything that needs to be
re-derived.

Phase 4.5 issues are #61–#65 (created via `/repo-sync` on 2026-04-10). Use
`/repo-sync` again if Phase 5 step bodies get fleshed out and need their own
issue numbers.

---

## Phase 1: Wiki & Documentation

**Goal:** Document what exists today so future sessions have full context.

- **Status:** DONE (2026-04-09)

| Step | Description | Status |
|------|-------------|--------|
| 1.1 | Create wiki index with system diagram | done |
| 1.2 | Write evaluation-pipeline wiki page | done |
| 1.3 | Write training-pipeline wiki page | done |
| 1.4 | Write monitoring wiki page | done |
| 1.5 | Write domain-coupling wiki page (SC2-specific vs agnostic) | done |
| 1.6 | Write architecture overview wiki page | done |
| 1.7 | Write remaining system pages (decision engine, commands, army, economy, claude advisor) | done |
| 1.8 | Write frontend and testing wiki pages | done |
| 1.9 | Write promotions wiki page (model promotion history + log) | done |
| 1.10 | Write FAQ wiki page (first-time visitor guide) | done |

---

## Phase 2: Monitoring & Observability Gaps

**Goal:** Identify and fill gaps so we can see everything the system does.

- **Status:** DONE (2026-04-09) — 6/6 steps, issues #36–#41 closed. 535 tests passing.

### Step 1: Audit current logging

- **Status:** DONE (2026-04-09)
- **Issue:** #36
- **Problem:** Verify what data is persisted vs ephemeral across the system. Confirm the
  gaps documented in `documentation/wiki/monitoring.md` are accurate against current code.
  Specifically verify: (1) action probabilities from NeuralDecisionEngine._last_probabilities
  are memory-only and never written to DB or file, (2) reward JSONL logging only works in
  --batch mode, not --train rl mode, (3) the transitions table has no column for action
  probabilities or reward breakdown. Update the monitoring wiki page if findings differ.
- **Flags:** --isolation worktree --reviewers code

### Step 2: Persist decision logs with action probabilities

- **Status:** DONE (2026-04-09)
- **Issue:** #37
- **Problem:** Add action probability persistence so we can track how the model's decision
  distribution changes over time. Changes needed:
  (1) Add `action_probs TEXT DEFAULT NULL` column to transitions table in database.py
  (use ALTER TABLE migration in __init__ for existing DBs).
  (2) Update `store_transition()` to accept optional `action_probs: list[float] | None`
  parameter, JSON-encode it for storage.
  (3) Update `bot.py._record_transition()` to read `self._neural_engine.last_probabilities`
  (when neural engine exists) and pass it to store_transition().
  (4) Update `SC2Env` in environment.py similarly — the _FullTrainingBot should capture
  probabilities from the gym state proxy or neural engine and pass them through the
  observation queue so SC2Env.step() can include them in store_transition().
  (5) Add a query method `get_action_distribution(model_version, n_games)` that returns
  average action probabilities for a given model version.
  (6) Add/update tests for all changes. Existing 500 tests must still pass.
- **Flags:** --isolation worktree --reviewers code

### Step 3: Make reward logging default

- **Status:** DONE (2026-04-09)
- **Issue:** #38
- **Problem:** Reward JSONL logging is currently opt-in via `--reward-log` flag and only
  works in --batch mode, not --train rl mode. Make it always-on so reward analysis is
  always available. Changes needed:
  (1) In trainer.py `_make_env()`, pass `log_path=self._data_dir / "reward_log.jsonl"`
  to RewardCalculator (currently hardcoded to None).
  (2) In runner.py `_run_batch()` and `_run_single_game()`, always pass log_path to
  RewardCalculator regardless of --reward-log flag. Keep the flag but change its meaning
  to --no-reward-log (opt-out instead of opt-in), or remove it entirely.
  (3) Add a proper `close()` method to RewardCalculator that flushes and closes the log
  file handle (currently never closed — data loss risk on crash). Make RewardCalculator
  a context manager (`__enter__`/`__exit__`).
  (4) Use per-game log files (`data/reward_logs/game_<ID>.jsonl`) instead of a single
  growing file to prevent unbounded growth.
  (5) Add/update tests. Existing tests must still pass.
  NOTE: Disk usage will increase since all games now write JSONL. This is acceptable for
  now. Disk usage management (rotation, compression, cleanup) is deferred to a later phase.
- **Flags:** --isolation worktree --reviewers code

### Step 4: Per-checkpoint win rate tracking

- **Status:** DONE (2026-04-09)
- **Issue:** #39
- **Problem:** Win rates are currently queried as a sliding window over recent games
  regardless of which model played them. Add per-checkpoint tracking so we can compare
  model versions. Changes needed:
  (1) The games table already has `model_version` — add a new query method
  `get_win_rate_by_model(model_version: str) -> dict` that returns
  {wins, losses, total, win_rate} for a specific model version.
  (2) Add `get_all_model_stats() -> list[dict]` that returns per-model stats ordered by
  first game timestamp (chronological — shows improvement over time).
  (3) Add API endpoint `GET /api/training/models` returning the per-model stats list.
  (4) Ensure model_version is set correctly everywhere: in trainer.py (checkpoint name),
  in runner.py (decision mode name or checkpoint name), in bot.py.
  (5) Add/update tests. Existing tests must still pass.
- **Flags:** --isolation worktree --reviewers code

### Step 5: Dashboard — model comparison view

- **Status:** DONE (2026-04-09)
- **Issue:** #40
- **Problem:** Add a minimal model comparison table to the Training tab so users can see
  per-checkpoint performance at a glance. This is a minimal version — enhanced charts
  and visualizations are deferred to Phase 4. Changes needed:
  (1) Add a new React component `ModelComparison.tsx` that fetches
  `GET /api/training/models` and renders a table: Model Version | Games | Wins | Losses |
  Win Rate | Difficulty | First Game | Last Game.
  (2) Highlight the current "best" model (from /api/training/checkpoints).
  (3) Add the component to the Training tab (below existing TrainingDashboard).
  (4) Poll on the same 5s interval as TrainingDashboard.
  The frontend dev server runs at localhost:3000 proxying to backend :8765.
  Start the backend with: `uv run python -m alpha4gate.runner --serve`
  Start the frontend with: `cd frontend && npm start`
- **Flags:** --isolation worktree --reviewers auto --ui --start-cmd "cd frontend && npm start" --url http://localhost:3000

### Step 6: Dashboard — improvement timeline

- **Status:** DONE (2026-04-09)
- **Issue:** #41
- **Problem:** Add a minimal improvement timeline to the Training tab showing how win
  rates have changed across model versions. This is a minimal version — charts deferred
  to Phase 4. Changes needed:
  (1) Add a new React component `ImprovementTimeline.tsx` that fetches
  `GET /api/training/models` and renders a table showing model versions in chronological
  order with: Model | Win Rate | Change (delta from previous) | Difficulty | Games Played.
  (2) Use simple visual indicators for the Change column: green up arrow for improvement,
  red down arrow for regression, dash for no change. Plain text/unicode is fine — no
  chart library needed.
  (3) Add the component to the Training tab (below ModelComparison).
  (4) Poll on same 5s interval.
  The frontend dev server runs at localhost:3000 proxying to backend :8765.
  Start the backend with: `uv run python -m alpha4gate.runner --serve`
  Start the frontend with: `cd frontend && npm start`
- **Flags:** --isolation worktree --reviewers auto --ui --start-cmd "cd frontend && npm start" --url http://localhost:3000

---

## Phase 3: Autonomous Training Loop

**Goal:** System plays, trains, and evaluates without human intervention.

- **Status:** DONE (2026-04-10) — 7/7 steps, issues #43–#49. 661 tests passing.

### Step 1: Training loop daemon

- **Status:** DONE (2026-04-10)
- **Issue:** #43
- **Problem:** Add a `TrainingDaemon` class in `src/alpha4gate/learning/daemon.py` that
  runs as a background thread inside the API server process. No new dependencies — use
  `threading.Thread` + `threading.Event` for start/stop control.
  Changes needed:
  (1) Create `daemon.py` with `TrainingDaemon` class. Constructor takes `settings` (paths),
  `config: DaemonConfig` (dataclass with `check_interval_seconds: int = 60`,
  `min_transitions: int = 500`, `min_hours_since_last: float = 1.0`,
  `cycles_per_run: int = 5`, `games_per_cycle: int = 10`). Methods: `start()`, `stop()`,
  `is_running() -> bool`, `get_status() -> dict` (returns current state: idle/checking/training,
  last_run timestamp, next_check timestamp, runs_completed count).
  (2) The daemon loop: sleep for `check_interval_seconds`, then call `_should_train()` to
  evaluate trigger conditions (see Step 2). If triggered, create a `TrainingOrchestrator`
  and call `run(n_cycles, games_per_cycle, resume=True)`. Store the result dict. Catch
  exceptions so the daemon never crashes — log errors and continue to next check.
  (3) Wire into `api.py`: implement `POST /api/training/start` to start the daemon (replace
  the current placeholder), `POST /api/training/stop` to stop it, `GET /api/training/daemon`
  to return daemon status. The daemon is created in `api.configure()` but only starts when
  the endpoint is called.
  (4) Wire into `runner.py`: add `--daemon` flag to `--serve` mode that auto-starts the
  daemon when the API server launches.
  (5) Add `DaemonConfig` to `data/daemon_config.json` with sensible defaults. Load it in
  runner.py alongside other settings.
  (6) Add/update tests for the daemon (mock TrainingOrchestrator to avoid needing SC2).
  Existing 535 tests must still pass.
- **Flags:** --isolation worktree --reviewers code

### Step 2: Training trigger logic

- **Status:** DONE (2026-04-10)
- **Issue:** #44
- **Problem:** Implement the `_should_train()` method in `TrainingDaemon` that decides
  whether to start a training run. Two trigger conditions (OR logic — either triggers
  training):
  (1) **Transition count trigger:** Query `db.get_transition_count()` and compare against
  `self._last_transition_count + config.min_transitions`. If enough new transitions have
  accumulated since the last training run, trigger. Store `_last_transition_count` after
  each run.
  (2) **Time trigger:** Compare `datetime.now() - self._last_run_time` against
  `config.min_hours_since_last`. If enough time has passed, trigger. Use
  `datetime.min` as initial value so first check always triggers if transitions exist.
  (3) **Safety gate:** Never trigger if `db.get_transition_count() == 0` (no data to
  train on). Never trigger if a training run is already in progress (`_training_active`
  flag).
  (4) Add a `GET /api/training/triggers` endpoint that returns the current trigger state:
  `{transitions_since_last: int, hours_since_last: float, would_trigger: bool,
  reason: str}`. This helps debugging without waiting for the check interval.
  (5) Add `PUT /api/training/daemon/config` endpoint to update daemon config at runtime
  (check_interval, min_transitions, min_hours, cycles_per_run, games_per_cycle).
  (6) Add/update tests. Mock the database to test trigger logic in isolation.
  Existing tests must still pass.
- **Flags:** --isolation worktree --reviewers code

### Step 3: Model evaluator

- **Status:** DONE (2026-04-10)
- **Issue:** #45
- **Problem:** Create a `ModelEvaluator` class in `src/alpha4gate/learning/evaluator.py`
  that runs N evaluation games with a specific checkpoint and returns win rate + stats.
  This is the building block for promotion decisions (Step 4). Evaluation runs
  sequentially on the same machine — true parallelism deferred to Phase 5.
  Changes needed:
  (1) Create `evaluator.py` with `ModelEvaluator` class. Constructor takes `settings`
  (paths), `db: TrainingDB`. Main method: `evaluate(checkpoint_name: str, n_games: int,
  difficulty: int) -> EvalResult` where `EvalResult` is a dataclass with
  `{checkpoint: str, games_played: int, wins: int, losses: int, win_rate: float,
  avg_reward: float, avg_duration: float, difficulty: int, action_distribution: list[float] | None}`.
  (2) Implementation: for each game, create an `SC2Env` with the specified checkpoint
  loaded via `NeuralDecisionEngine`, run the game via `env.reset()` + `env.step()` loop
  (not `model.learn()` — we want inference only, no gradient updates). Record results
  to `training.db` with `model_version=checkpoint_name`. Collect action probabilities
  across games for distribution stats.
  IMPORTANT: Evaluation games should NOT use `model.learn()`. Use a simple inference loop:
  `obs = env.reset(); while not done: action, _ = model.predict(obs); obs, reward, done, _, info = env.step(action)`.
  (3) Add `GET /api/training/evaluate` endpoint that accepts `?checkpoint=NAME&games=N&difficulty=D`
  and runs evaluation (returns result). This is a long-running operation — return 202
  Accepted with a job ID, then poll via `GET /api/training/evaluate/{job_id}`.
  (4) Add a `compare(checkpoint_a: str, checkpoint_b: str, n_games: int, difficulty: int)
  -> ComparisonResult` method that evaluates both and returns which is better with
  confidence. `ComparisonResult`: `{a: EvalResult, b: EvalResult, winner: str,
  win_rate_delta: float, significant: bool}`. Use a simple threshold (>5% better with
  >=10 games) rather than statistical tests.
  (5) Add/update tests. Mock SC2Env to test evaluation logic without SC2. Test the
  compare method with predetermined results.
  Existing tests must still pass.
  NOTE: SC2 must be installed for actual evaluation. Unit tests should mock the env.
- **Flags:** --isolation worktree --reviewers code

### Step 4: Model promotion gate

- **Status:** DONE (2026-04-10)
- **Issue:** #46
- **Problem:** After each training run, automatically evaluate the new checkpoint against
  the current best. Promote if the new model is better. This replaces the current implicit
  "last checkpoint = best" behavior in `trainer.py`.
  Changes needed:
  (1) Add a `PromotionManager` class in `src/alpha4gate/learning/promotion.py`. Constructor
  takes `evaluator: ModelEvaluator`, `config: PromotionConfig` (dataclass with
  `eval_games: int = 20`, `win_rate_threshold: float = 0.05`,
  `min_eval_games: int = 10`).
  (2) Main method: `evaluate_and_promote(new_checkpoint: str, difficulty: int)
  -> PromotionDecision` where `PromotionDecision` is a dataclass with
  `{new_checkpoint: str, old_best: str, new_eval: EvalResult, old_eval: EvalResult,
  promoted: bool, reason: str, timestamp: str}`.
  (3) Integration with daemon: after `TrainingOrchestrator.run()` completes in the daemon
  loop, call `promotion_manager.evaluate_and_promote(latest_checkpoint, current_difficulty)`.
  If promoted, update `manifest.json` best via `save_checkpoint(..., is_best=True)` or
  a new `promote_checkpoint(name)` function in `checkpoints.py`.
  (4) Modify `trainer.py` `run()` method: stop auto-marking the last checkpoint as best
  (remove the `is_best=True` logic that fires when `win_rate > self._best_win_rate`).
  Instead, just save with `is_best=False` and let the promotion gate decide.
  (5) **Two data store decision:** `training.db` is the source of truth for win rates used
  in promotion decisions. `stats.json` is NOT consulted. Document this in the wiki.
  (6) Add `GET /api/training/promotions` endpoint returning promotion history (list of
  PromotionDecision dicts). Add `POST /api/training/promote` for manual promotion
  override (accepts checkpoint name, skips evaluation).
  (7) Add/update tests. Mock evaluator to test promotion logic in isolation.
  Existing tests must still pass.
- **Flags:** --isolation worktree --reviewers code

### Step 5: Promotion logging

- **Status:** DONE (2026-04-10)
- **Issue:** #47
- **Problem:** Record every promotion/rejection decision with full evidence so the history
  is auditable. Two outputs: a structured JSON log and the wiki page.
  Changes needed:
  (1) Create `data/promotion_history.json` as the structured log. Each entry is a
  `PromotionDecision` dict (from Step 4) serialized with: timestamp, new_checkpoint,
  old_best, new_win_rate, old_win_rate, delta, eval_games_played, promoted (bool),
  reason, difficulty, action_distribution_shift (optional).
  (2) Add `PromotionLogger` class in `promotion.py` (or extend `PromotionManager`) with
  `log_decision(decision: PromotionDecision)` that appends to the JSON file and
  auto-updates `documentation/wiki/promotions.md` with a new table row.
  (3) The promotions.md table format: `| Date | From | To | Win Rate (Old→New) |
  Games | Difficulty | Reason | Outcome |`. Append-only — never edit existing rows.
  (4) Add action distribution shift tracking: compare
  `db.get_action_distribution(old_model, N)` vs `db.get_action_distribution(new_model, N)`.
  Include the L1 distance (sum of absolute differences) as a scalar metric in the log.
  This shows how much the model's behavior changed.
  (5) Add `GET /api/training/promotions/history` endpoint returning the full promotion
  history JSON. Add `GET /api/training/promotions/latest` for just the most recent decision.
  (6) Add/update tests. Test JSON serialization, wiki append logic, action distribution
  comparison. Existing tests must still pass.
- **Flags:** --isolation worktree --reviewers code

### Step 6: Rollback mechanism

- **Status:** DONE (2026-04-10)
- **Issue:** #48
- **Problem:** If a promoted model performs worse than expected over subsequent games,
  automatically revert to the previous best checkpoint.
  Changes needed:
  (1) Add a `RollbackMonitor` class in `src/alpha4gate/learning/rollback.py`. Constructor
  takes `db: TrainingDB`, `config: RollbackConfig` (dataclass with
  `monitoring_window: int = 30`, `regression_threshold: float = 0.15`,
  `min_games_before_check: int = 10`).
  (2) Main method: `check_for_regression(current_best: str) -> RollbackDecision | None`.
  Query `db.get_win_rate_by_model(current_best)` — if it has played
  `>= min_games_before_check` games and win rate is more than `regression_threshold`
  below the win rate recorded at promotion time, return a `RollbackDecision` with
  `{current_model, revert_to, current_win_rate, promotion_win_rate, games_played, reason}`.
  (3) Add `execute_rollback(decision: RollbackDecision)` that updates `manifest.json`
  best back to the previous checkpoint. Log the rollback to `promotion_history.json`
  as a special entry with `promoted=False, reason="rollback: ..."`.
  (4) Integrate with daemon: after each training run (and its promotion decision), also
  run `rollback_monitor.check_for_regression()` on the current best. If regression
  detected, rollback before the next training cycle.
  (5) Track the "previous best" checkpoint name in `manifest.json` — add a
  `previous_best` field alongside `best`. Updated by promotion logic in Step 4.
  (6) Add `POST /api/training/rollback` endpoint for manual rollback (accepts checkpoint
  name to revert to). Add rollback status to `GET /api/training/daemon` response.
  (7) Add/update tests. Test regression detection with mock DB data. Test rollback
  updates manifest correctly. Existing tests must still pass.
- **Flags:** --isolation worktree --reviewers code

### Step 7: Curriculum auto-advancement

- **Status:** DONE (2026-04-10)
- **Issue:** #49
- **Problem:** Wire the existing curriculum logic (`should_increase_difficulty()` /
  `increase_difficulty()` in `trainer.py`) into the autonomous daemon loop so difficulty
  advances automatically across training runs, not just within a single CLI invocation.
  Changes needed:
  (1) Move curriculum state out of `TrainingOrchestrator` instance into persistent storage.
  Add `current_difficulty` and `max_difficulty` fields to `data/daemon_config.json`.
  The daemon reads these on startup and passes them to each `TrainingOrchestrator.run()`
  call.
  (2) After each training run, update `daemon_config.json` with the final difficulty from
  the orchestrator result (`result["final_difficulty"]`). This persists difficulty across
  daemon restarts.
  (3) Add curriculum-aware promotion: when a model is promoted at difficulty N, if its
  win rate exceeds the threshold (0.8), auto-advance difficulty to N+1 for the next
  training run. Log the advancement in `promotion_history.json`.
  (4) Add `GET /api/training/curriculum` endpoint returning `{current_difficulty: int,
  max_difficulty: int, win_rate_threshold: float, last_advancement: str | null}`.
  Add `PUT /api/training/curriculum` to manually set difficulty.
  (5) Add a difficulty floor: if rollback happens (Step 6), also revert difficulty to the
  level at which the previous best was trained. Prevent difficulty from racing ahead of
  model capability.
  (6) Add/update tests. Test curriculum persistence across mock daemon restarts. Test
  difficulty revert on rollback. Existing tests must still pass.

---

## Phase 4: Transparency Dashboard

**Goal:** Full visibility into the autonomous loop from the browser.

- **Status:** DONE (2026-04-09) — 10/10 steps, issues #50–#59. 682 Python tests + 105
  frontend vitest tests passing. Detail plan:
  `documentation/plans/phase-4-transparency-dashboard-plan.md`.

The legacy 5-step outline below maps to the 10 detailed steps in the transparency
dashboard plan:

| Legacy step | Description | Detail-plan step(s) | Deliverable(s) |
|-------------|-------------|---------------------|----------------|
| 1 | Training cycle status | Step 5 | `useDaemonStatus.ts`, `LoopStatus.tsx` |
| 2 | Recent improvements view | Step 7 | `RecentImprovements.tsx` (splits promotions vs rollbacks) |
| 3 | Per-rule reward trends | Steps 1, 2, 8 | `learning/reward_aggregator.py`, `GET /api/training/reward-trends`, `RewardTrends.tsx` |
| 4 | Alerting | Step 9 | `alertRules.ts`, `alertStorage.ts`, `useAlerts.ts`, `AlertToast.tsx`, `AlertsPanel.tsx` |
| 5 | Training trigger UI | Step 6 | `TriggerControls.tsx` (wired to existing start/stop/config endpoints) |

Detail-plan Steps 3 (frontend vitest infra), 4 (`ConfirmDialog` reusable modal), and
10 (this docs pass) are infrastructure / docs that the legacy outline did not
enumerate.

### Step 1: Training cycle status — DONE
- Legacy item for "show running/idle, current cycle, games completed, ETA in the
  dashboard." Delivered by the Loop tab (`LoopStatus` + `useDaemonStatus`), which
  polls `/api/training/daemon` + `/api/training/status` every 5s and surfaces daemon
  state, last/next run timestamps, `runs_completed`, trigger preview, and reward-log
  disk usage.

### Step 2: Recent improvements view — DONE
- Delivered by `RecentImprovements` on the Improvements tab, which consumes
  `/api/training/promotions/history` and splits promotions from rollbacks
  (`promoted: false` + `reason` starting with `rollback:`).

### Step 3: Per-rule reward trends — DONE
- Backend: `src/alpha4gate/learning/reward_aggregator.py` walks per-game JSONL files
  under `data/reward_logs/`, unpacks the `fired_rules` array per line, and aggregates
  totals + fire counts per rule.
- API: `GET /api/training/reward-trends?games=N` (default 100, min 1, max 1000).
- Frontend: `RewardTrends.tsx` on the Improvements tab.

### Step 4: Alerting — DONE
- Client-side only — no alert backend. `useAlerts` polls training endpoints,
  `alertRules.ts` evaluates rules against the snapshot, `alertStorage.ts` persists
  history + acks to `localStorage`. Surfaces: `AlertToast` overlay for new alerts,
  `AlertsPanel` for full history on the Alerts tab, unread count badge on the tab.

### Step 5: Training trigger UI — DONE
- `TriggerControls.tsx` on the Loop tab. Start/stop buttons gated by a
  `ConfirmDialog`, plus editable daemon config (check interval, min transitions,
  min hours, cycles per run, games per cycle) persisted via
  `PUT /api/training/daemon/config`. Wired to the existing `POST /api/training/start`
  and `/stop` endpoints (previously placeholder-only from the frontend's perspective).

### Deferred (Phase 4 decision reaffirmed)
- **WebSocket upgrade for training data.** Phase 4 stayed on 5s REST polling for the
  Loop, Improvements, and Alerts tabs. A dedicated `/ws/training` channel that
  pushes daemon state transitions, new promotions, and new alerts as they happen
  remains deferred — reconsider after Phase 4.5 soak-test findings. Live game data
  on the Live and Decisions tabs continues to use WebSockets.

---

## Phase 4.5: First Real Soak Test

**Goal:** Prove the autonomous improvement loop actually works end-to-end with real
SC2 game data, not just unit-tested components. This is the first time the system runs
unattended for hours and we observe the loop instead of inferring it from passing tests.

**Why this phase exists:** Phases 2-4 ship a daemon, evaluator, promotion gate, rollback
monitor, and a dashboard to observe all of them. Every component has unit tests with
mocked dependencies. **Nothing has actually validated that the daemon, when left
running for hours against a real SC2 client, will autonomously trigger training, evaluate
checkpoints, promote a better model, and recover from a regression.** The 661 unit tests
prove the components work in isolation. They do not prove the components work *together*
under real game variance, real PPO failure modes, real disk growth, and real timing.

This is the elephant-in-the-room validation gap. Phase 4.5 closes it before Phase 5
(domain abstraction) starts pulling things apart for refactoring, because once you start
refactoring you want a known-good baseline to compare against.

**What this phase is NOT:**
- Not a code-writing phase. The output is a soak-test procedure, observed evidence,
  and a backlog of issues found.
- Not a "fix everything you find" phase. Bugs found go into a Phase 4.5 followup
  backlog and are triaged, not auto-fixed.
- Not a benchmark of the bot's strategic skill. The win rate against built-in AI is
  not the metric being measured here — the metric is "did the autonomous loop run
  unattended without breaking."

### Step 1: Document the soak test procedure

- **Problem:** Write `documentation/soak-test.md` describing the full procedure to run
  Alpha4Gate in autonomous mode for several hours and observe the loop. The procedure
  must be precise enough that someone (or a future model) can run it without prior
  context. Include:
  (1) **Prerequisites:** SC2 client installed at `C:\Program Files (x86)\StarCraft II\`,
  maps downloaded from Blizzard CDN (NOT the Git LFS pointers in the repo), Python
  environment installed via `uv sync`, frontend deps installed via
  `cd frontend && npm install`.
  (2) **Pre-soak checklist:** clean `data/` directory or known-state snapshot, daemon
  config values to use (`min_transitions`, `min_hours_since_last`, `cycles_per_run`,
  `games_per_cycle`, `current_difficulty`), expected disk usage budget, expected
  duration window (start at 4 hours; the procedure should also accept "until N
  promotions occur" as a stop condition).
  (3) **Startup sequence:** exact commands to start SC2 client, start the backend with
  daemon enabled (`uv run python -m alpha4gate.runner --serve --daemon`),
  start the frontend dev server, open the dashboard. Document any required SC2 client
  state (in main menu, not in a game, etc.).
  (4) **Observation protocol:** what to watch on the dashboard, what counts as a
  successful cycle, what counts as a failure mode, when to intervene vs let it run.
  Include a screenshot checklist for each dashboard tab showing the kind of state
  evidence you want captured.
  (5) **Stop and post-soak protocol:** how to stop cleanly (daemon stop button vs
  `--no-daemon` shutdown), what to capture (training.db copy, full data/ snapshot,
  daemon log, frontend console log, screenshots of all dashboard tabs in their final
  state), how to file findings.
- **Issue:** #61
- **Flags:** --reviewers code
- **Status:** DONE (2026-04-10)
- **Produces:** `documentation/soak-test.md`
- **Done when:** doc reviewed for self-containment via `/session-check`. A fresh
  context model could run the procedure without asking questions.
- **Depends on:** Phase 4 complete (dashboard exists to observe)

### Step 2: Verify daemon mode is actually wired

- **Problem:** Phase 3 added a `--daemon` flag to `runner.py` but the autonomous loop
  has never been observed running in this mode end-to-end. Before the soak test, do a
  short smoke test to verify the daemon mode actually does what's expected:
  (1) Start `uv run python -m alpha4gate.runner --serve --daemon` (or whatever the
  exact CLI is — verify against the current `runner.py`).
  (2) Confirm via `GET /api/training/daemon` that `running: true` and `state` cycles
  through values, not stuck.
  (3) Manually populate the training database with enough transitions to cross
  `min_transitions` (or temporarily lower the threshold) and confirm the daemon
  *attempts* to fire a training run within `check_interval_seconds`.
  (4) Stop daemon, verify clean shutdown and no leaked processes.
  (5) Record exact CLI flags, observed behavior, and any required environment setup
  in `documentation/soak-test.md` from Step 1.
- **Issue:** #62
- **Flags:** --reviewers runtime
- **Status:** DONE (2026-04-10) — wiring verified end-to-end. Smoke tests surfaced 9 findings (F1-F9) ALL fixed across commits 6d0b4c9 (F2 fallthrough), afaeda5 (F1+F6 spaces, F5 docs), c120e7b (F7+F8+F9 single-source-of-truth audit). Smoke 4 ran a full training cycle in 61s and started a real promotion eval. 695 pytest passing. Pre-Step-3 cleanup: `rm data/daemon_config.json` to restore 5×10 defaults. See `documentation/soak-test-runs/smoke-2026-04-10d.md` for the final run log + closing summary.
- **Produces:** observation notes in `documentation/soak-test.md`. No code changes
  expected — but if the CLI is missing or broken, this step pivots to fixing it
  (and that fix becomes its own commit).
- **Done when:** daemon can be started, observed, and stopped cleanly via the CLI
  documented in the soak test procedure.
- **Depends on:** Step 1

### Step 3: Run the soak test (4-hour run)

- **Problem:** Execute the procedure from Step 1 for at least 4 hours of unattended
  runtime, or until N promotions have occurred (whichever comes first). The goal is
  evidence collection, not bug-fixing. While the loop runs:
  (1) Capture screenshots of every dashboard tab at start, midpoint, and end. The
  Loop tab should show daemon state transitions; the Improvements tab should show
  any promotions or rollbacks; the Alerts tab should show any alerts that fired.
  (2) Note every alert that fires, with timestamp and severity. Note false positives
  (alerts that fired but shouldn't have).
  (3) Note every daemon state transition manually observed. Compare against
  `runs_completed` counter and `last_run` timestamp at the end.
  (4) Watch for: daemon getting stuck in a state, training runs that never complete,
  promotion logic firing incorrectly, dashboard showing stale data, frontend losing
  connection, browser console errors, backend log errors, disk usage growing
  unexpectedly fast, SC2 client crashes, the bot itself crashing mid-game.
  (5) Save the final `data/` directory as `data-soak-test-<date>.zip` for postmortem.
- **Issue:** #63
- **Flags:** --reviewers runtime
- **Status:** DONE (2026-04-10) — soak run #1 completed. Section 4.2 six-step success criterion met at 14:32:57 with v5 promoted to best after 5 training cycles + 20-game eval. Run stopped at ~14:44 on the early-stop-on-N=1-promotions condition (Section 2.4). **17 findings catalogued: 3 BLOCKERS (B6 SQLite thread bug at `database.py:127`, B7 silent eval-game crash recovery same as F2 antipattern, B10 30% data-loss rate is guaranteed not occasional), 7 majors (M14 alerts pipeline never fired despite 7 backend exceptions, M11 first-cycle promotion is unconditional so gating logic untested, others), 5 minors, 2 resolved during analysis.** Step 5 will need to pre-seed `manifest.best` so the gate's comparison logic actually executes. Deliverables: `documentation/soak-test-runs/soak-2026-04-10.md` (run log), `~/soak-artifacts/2026-04-10/backend.log` (1.2 MB backend log evidence with all 7 game-thread tracebacks + the promotion line), `scripts/soak_poll.py` (new 60s state poller, surfaced Findings #1/#4/#12 in real time). Commit `21d774c`.
- **Produces:** `documentation/soak-test-<date>-results.md` with observations,
  screenshot inventory, alert log, and a raw findings list (each finding gets a
  one-line description, severity, and suggested category: dashboard / daemon /
  training / alert tuning / docs / unknown).
- **Done when:** soak test ran for the target duration, all observations captured,
  results doc written. Pass/fail of the soak test itself is irrelevant — the doc IS
  the deliverable. Even if the loop completely failed in the first 5 minutes, the
  finding "loop dies after 5 minutes for reason X" is the most valuable possible
  output.
- **Depends on:** Step 2

### Step 4: Triage findings into a Phase 4.5 backlog

- **Problem:** Process the raw findings from Step 3 into actionable categories:
  (1) **Blockers:** the autonomous loop cannot run unattended for the target duration
  without manual intervention. These must be fixed before Phase 5 starts.
  (2) **Alert tuning:** alert thresholds that were too tight or too loose under real
  variance. Adjust constants in `frontend/src/lib/alertRules.ts` and re-soak.
  (3) **Dashboard polish:** UX issues found while watching the dashboard for hours
  (e.g., timestamps not updating, polling stale, tab order awkward, missing context
  on a card). File as a "Phase 4 polish" mini-backlog.
  (4) **Daemon tuning:** values like `min_transitions`, `cycles_per_run`,
  `check_interval_seconds` that are wrong for actual usage. Update the
  `data/daemon_config.json` defaults in code.
  (5) **Documentation gaps:** wiki pages or plan sections that don't match reality
  after seeing the loop run.
  (6) **Phase 5 inputs:** anything that suggests the domain abstraction in Phase 5
  needs to account for something not yet considered.
- **Issue:** #64
- **Flags:** --reviewers code
- **Status:** DONE (2026-04-10) — 17 findings triaged into 6 buckets. 4 blockers consolidated into 2 issues by root cause: [#66](https://github.com/aberson/Alpha4Gate/issues/66) (SQLite thread-safety + 30% loss + cycle-uniformity, covering findings #6/#10/#17) and [#67](https://github.com/aberson/Alpha4Gate/issues/67) (silent eval-game crash recovery, finding #7). Alert tuning: [#68](https://github.com/aberson/Alpha4Gate/issues/68) (alerts pipeline fired zero alerts, finding #14). Dashboard polish, daemon tuning, and docs gaps documented in [phase-4-5-backlog.md](phase-4-5-backlog.md). Phase 5 inputs (#11, #12) recorded in the Phase 5 section below. Issue [#65](https://github.com/aberson/Alpha4Gate/issues/65) (Step 5) updated with the full Step 5 pre-flight checklist (manifest pre-seed, tee-from-start, synthetic alert verification).
- **Produces:** GitHub issues created for each Blocker and Alert Tuning finding.
  A `documentation/plans/phase-4-5-backlog.md` file documenting the Dashboard Polish,
  Daemon Tuning, and Documentation Gaps lists. Phase 5 inputs noted in
  `always-up-plan.md` Phase 5 section.
- **Done when:** every finding from Step 3 is in one of the six buckets, blockers
  have GitHub issues, the backlog file exists.
- **Depends on:** Step 3

### Step 5: Fix blockers and re-soak (conditional)

- **Problem:** If Step 4 identified any Blockers (autonomous loop cannot run
  unattended), fix them and run a second soak test. Repeat until the loop can run
  for the target duration without manual intervention. If Step 4 identified zero
  Blockers, **skip this step entirely** and mark Phase 4.5 as complete.
- **Issue:** #65
- **Flags:** --reviewers code (for fixes), --reviewers runtime (for re-soak)
- **Status:** All blocker fix code work DONE. Soak-3 ready to launch (2026-04-11 after #72/#73).
  - [#66](https://github.com/aberson/Alpha4Gate/issues/66) SQLite thread-safety + write serialization — fixed in `49c4a97` (2 iterations; iter 2 added read-method locking).
  - [#67](https://github.com/aberson/Alpha4Gate/issues/67) Silent eval-game crash recovery + promotion gate refusal + action_probs pollution fix — fixed in `d2170e3` (2 iterations).
  - [#68](https://github.com/aberson/Alpha4Gate/issues/68) Backend ERROR log ring buffer + alerts pipeline + synthetic pre-flight — fixed in `98e822f` (2 iterations).
  - [#71](https://github.com/aberson/Alpha4Gate/issues/71) Daemon reports all-crashed training run as failure (discovered live during soak-2 attempt 1) — fixed in `3a7ce10` (cleanest iter-1 of the session, no iter-2).
  - [#72](https://github.com/aberson/Alpha4Gate/issues/72) RL trainer `WSMessageTypeError` cascade (soak-2 attempt 2 blocker) — fixed in `f954d4b` → `7369329` (merge). Root cause was a **four-layer episode-teardown bug** in `SC2Env` / `_FullTrainingBot`, NOT the "concurrent external SC2 / parallel VecEnv" hypothesis in the issue body — the trainer uses a single `SC2Env` per cycle wrapped in `DummyVecEnv`. Fix adds `_episode_done` flag + `_resign_and_mark_done()` helper (awaits `client.leave()`), fresh queues allocated per `reset()`, and drains burnysc2's `KillSwitch._to_kill` class-level list in `_run_game_thread.finally`. 7 new unit tests + 1 `@pytest.mark.sc2`-gated. 2 iterations; wiki gains "Episode teardown contract", "Queue isolation on reset", and "KillSwitch hygiene" sections.
  - [#73](https://github.com/aberson/Alpha4Gate/issues/73) Daemon `_last_error` not surfaced while orchestrator is still iterating (#71 cycle-granularity follow-up) — fixed in `ea04caa` → `7431fc8` (merge). Approach A watchdog thread polls `ErrorLogBuffer._count_since_start` during `_run_training`, writes interim `_last_error` within ~25s of the 5th per-cycle ERROR. Joined BEFORE the #71 post-bookkeeping to eliminate the race. 7 new tests in `TestWatchdogPerCycleCrashVisibility`. 2 iterations; wiki gains "Daemon state vs. training progress (#71 / #73)" section.
  - `soak-test.md` updated with Option B bootstrap contract (§2.1 `--batch` vs `--map` asymmetry), #73 watchdog failure-mode row (§4.3/4.4), and #74 mid-training stop caveat (§5.1) — commit `0ba3069`.
  - Follow-ups (not blocking soak-3): [#69](https://github.com/aberson/Alpha4Gate/issues/69) `run_job` all-crashed status, [#70](https://github.com/aberson/Alpha4Gate/issues/70) `environment.py:188` training-path cousin of #67, [#74](https://github.com/aberson/Alpha4Gate/issues/74) `POST /api/training/stop` doesn't kill in-flight trainer subprocess (workaround documented in `soak-test.md §5.1`).
  - Test counts: **742 Python** (was 695 at Step 4 close, +47) and 115 frontend, mypy strict clean, ruff clean.
  - Operator action remaining: launch soak-3. Fresh empty `data/` (pre-F1 snapshot at `data-pre-soak-2026-04-10/` is permanently incompatible — 15-dim obs space vs current 17-dim). Seed via `uv run python -m alpha4gate.runner --batch 1 --difficulty 1` (see `soak-test.md §2.1` for the Option B bootstrap contract — `--map Simple64` is a silent no-op). `tee` from start, §3.5 pre-flight, commit to 4h window. Trust `daemon.last_error` for mid-training failure visibility via the #73 watchdog.
- **Produces:** fixes for each blocker, a second soak-test results doc.
- **Done when:** the autonomous loop has run for the target duration without manual
  intervention OR the user has decided the remaining issues are acceptable to defer
  into Phase 5.
- **Depends on:** Step 4 found at least one Blocker

### Notes on Phase 4.5

- **Why a separate phase, not part of Phase 4:** Phase 4 is about *building* the
  observation infrastructure. Phase 4.5 is about *using* that infrastructure to
  observe something real for the first time. They have different definitions of
  "done" — Phase 4 done = code shipped + tests pass, Phase 4.5 done = the system
  has been observed running unattended.
- **Time budget:** Steps 1, 2, 4, 5 are short (hours each). Step 3 is the long pole
  (4+ hours of wall-clock runtime, mostly waiting). Total elapsed time is likely
  1-2 days depending on findings.
- **Hardware:** This is the most resource-intensive phase. SC2 client + bot +
  backend + frontend + Claude advisor (if enabled) = significant CPU and possibly
  GPU usage. Plan accordingly.
- **What success looks like:** Not "the bot wins more games." Not "the model gets
  better." Success is **the dashboard accurately reflects what the loop did, the
  loop did not crash, and any problems found are documented as actionable items.**
  The bot's actual training improvements are out of scope — that's a separate
  ongoing concern that lives in the daily-use loop after Phase 4.5 ships.
- **Relationship to Phase 5:** Phase 4.5 findings about coupling between SC2 and
  the loop infrastructure feed directly into Phase 5 design. A finding like
  "the daemon assumes a single SC2 client process" is a Phase 5 input even if it's
  not blocking the soak test itself.

---

## Phase 5: Domain Abstraction

**Goal:** Clean separation so the training/eval/monitoring loop works with any domain.

### Step 1: Domain interface
- **Problem:** Define abstract interfaces (Environment, FeatureSpec, RewardSpec) that the training loop depends on.

### Step 2: Extract SC2-specific code
- **Problem:** Move SC2-specific implementations behind the domain interface.

### Step 3: Validate with toy domain
- **Problem:** Prove generality by running the full loop with CartPole or similar simple environment.

### Inputs from Phase 4.5 soak run #1 (2026-04-10)

The first end-to-end soak run surfaced two findings whose resolution should shape Phase 5 design decisions rather than be patched in place. Triaged in [phase-4-5-backlog.md](phase-4-5-backlog.md) and recorded here so they are not lost between phases.

- **Promotion-gate bootstrap is unconditional (Finding #11).** The 14:32:57 promotion in soak run #1 fired with `reason=no previous best checkpoint`, short-circuiting the win-rate comparison entirely. Any checkpoint passed to the gate when `manifest.best == null` will promote — the comparison code path was never executed even after a full 5-cycle + 20-game-eval run. Phase 5's domain interface should make the gate's bootstrap policy explicit (e.g., a `PromotionPolicy` with separate `bootstrap()` and `compare(new, best)` methods) so that the toy-domain validation in Step 3 can exercise the comparison path on cycle 1 rather than only on cycle 2+. Soak Step 5 (#65) handles the immediate workaround by pre-seeding `manifest.best` before launch; Phase 5 should make the workaround unnecessary.
- **Daemon idle deadlock (Finding #12).** After run #1 completed, the daemon went `state=idle, transitions_since_last=0` and could not produce more transitions because no games run while idle. The transition trigger (`min_transitions=500`) is unreachable in this state; the only escape is the time trigger (`min_hours_since_last=1.0`). For an unattended run this hard-caps cycle rate at ~1/hour. The Phase 5 domain abstraction should treat "transition supply" as a property of the environment, not the daemon — domains where idle generates no data (SC2, anything that needs an external client) need an explicit "background self-play" or "warm pool" affordance that the daemon can rely on, separate from the trigger logic. The Daemon tuning bucket in `phase-4-5-backlog.md` covers the immediate fix; Phase 5 should make the abstraction load-bearing.

---

## Current State (2026-04-09)

**What exists:**
- TrainingOrchestrator — full RL loop, CLI + daemon-triggered
- TrainingDaemon — background thread with trigger logic, auto-training, curriculum persistence
- ModelEvaluator — inference-only checkpoint evaluation with async job management
- PromotionManager + PromotionLogger — automated promote gate with JSON + wiki logging
- RollbackMonitor — regression detection, automatic revert, difficulty floor
- Curriculum auto-advancement — persistent difficulty across daemon restarts, auto-advance on promotion
- `learning/reward_aggregator.py` — per-rule reward contribution aggregator over
  `data/reward_logs/` JSONL files (Phase 4 Step 1)
- SQLite DB — games + transitions + action probabilities, win rate queries, per-model stats
- WebSocket broadcasting — live game state, decisions, commands (ephemeral)
- JSONL logging — per-game files in `data/reward_logs/` (always-on, opt-out via `--no-reward-log`)
- React dashboard (9 tabs) — Live, Stats, Builds, Replays, Decisions, Training, Loop,
  Improvements, Alerts. Training tab: TrainingDashboard + ModelComparison +
  ImprovementTimeline + CheckpointList + RewardRuleEditor. Loop tab: LoopStatus +
  TriggerControls (with ConfirmDialog). Improvements tab: RecentImprovements +
  RewardTrends. Alerts tab: AlertsPanel + global AlertToast overlay.
- Frontend test infrastructure — vitest + jsdom, `frontend/vitest.config.ts`, shared
  setup in `frontend/src/test/setup.ts`, 105 tests currently passing.
- Client-side alert engine — `alertRules.ts`, `alertStorage.ts`, `useAlerts` hook.
  Rules evaluated on each 5s poll, persisted to `localStorage`. **Phase 4.5 #68:**
  backend now exposes a bounded ring buffer of ERROR-level log records via
  `error_count_since_start` and `recent_errors[]` on `GET /api/training/status`,
  and the new `ruleBackendErrors` rule (severity `error`, `persistent: true` — no
  auto-dismiss) fires on any non-zero count. `POST /api/debug/raise_error` behind
  `DEBUG_ENDPOINTS=1` provides the synthetic pre-flight trigger.
- Evaluation scripts — evaluate_model.py, analyze_rewards.py
- Wiki — 15 pages documenting all systems (documentation/wiki/)
- Per-checkpoint win rate tracking via `GET /api/training/models`
- Persistent decision logs with action probability distributions
- 15+ API endpoints for daemon control, triggers, evaluation, promotions, rollback,
  curriculum. **New in Phase 4:** `GET /api/training/reward-trends?games=N` (Step 2)
  and `reward_logs_size_bytes` field added to `GET /api/training/status` (Step 1).
- 724 Python unit tests + 115 frontend vitest tests passing (as of Phase 4.5 Step 5 blocker fixes, 2026-04-11).

**What's missing:**
- No real end-to-end soak test yet — the autonomous loop has never been observed
  running unattended against SC2 for hours (addressed by Phase 4.5).
- No WebSocket channel for training/loop events — Loop, Improvements, and Alerts
  tabs still poll every 5s. Decision reaffirmed as deferred in Phase 4; revisit
  after soak-test findings.
- No domain abstraction (SC2 code still interleaved with training loop — Phase 5).

## Decisions

- [x] Scheduler technology: Custom daemon thread (`threading.Thread` + `threading.Event`)
  inside the API server process. No new dependencies. Simple, testable, restartable.
- [x] Should eval games run on same machine as training or separate? Same machine,
  sequential. True parallelism deferred to Phase 5 (domain abstraction).
- [x] Dashboard tech: Keep React 5s polling for Phase 3. WebSocket upgrade deferred to
  Phase 4 (transparency dashboard).
- [x] Model storage: Local checkpoints in `data/checkpoints/`. Artifact registry deferred.
- [x] Data store authority: `training.db` (SQLite) is the source of truth for win rates
  used in promotion decisions. `stats.json` is supplementary and not consulted.

## Notes

- **Disk usage:** Phase 2 step 3 makes reward JSONL logging always-on. This increases
  disk usage proportionally to games played. Disk management (log rotation, compression,
  max file age, cleanup scripts) is deferred to a later phase. For now, monitor
  `data/reward_logs/` size manually. The training DB already has a 200 GB disk guard.
