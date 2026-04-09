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

- **Status:** IN PROGRESS

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

### Step 1: Training cycle status
- **Problem:** Show running/idle, current cycle, games completed, ETA in the dashboard.

### Step 2: Recent improvements view
- **Problem:** Show what changed in the last N promotions, how it played out (enhanced version of Phase 2 timeline).

### Step 3: Per-rule reward trends
- **Problem:** Show which reward rules fire most often and how their contribution changes over time.

### Step 4: Alerting
- **Problem:** Notify if win rate drops, training fails, disk fills up, or model regresses.

### Step 5: Training trigger UI
- **Problem:** Start/stop/configure training from the dashboard (the POST endpoint is currently a placeholder).

---

## Phase 5: Domain Abstraction

**Goal:** Clean separation so the training/eval/monitoring loop works with any domain.

### Step 1: Domain interface
- **Problem:** Define abstract interfaces (Environment, FeatureSpec, RewardSpec) that the training loop depends on.

### Step 2: Extract SC2-specific code
- **Problem:** Move SC2-specific implementations behind the domain interface.

### Step 3: Validate with toy domain
- **Problem:** Prove generality by running the full loop with CartPole or similar simple environment.

---

## Current State (2026-04-10)

**What exists:**
- TrainingOrchestrator — full RL loop, but CLI-only, manual trigger
- SQLite DB — games + transitions + action probabilities, win rate queries, per-model stats
- WebSocket broadcasting — live game state, decisions, commands (ephemeral)
- JSONL logging — per-game files in `data/reward_logs/` (always-on, opt-out via `--no-reward-log`)
- React dashboard — LiveView, TrainingDashboard, ModelComparison, ImprovementTimeline, CheckpointList, RewardRuleEditor
- Evaluation scripts — evaluate_model.py, analyze_rewards.py
- Curriculum system — auto-increases difficulty when win_rate >= 0.8
- Wiki — 15 pages documenting all systems (documentation/wiki/)
- Per-checkpoint win rate tracking via `GET /api/training/models`
- Persistent decision logs with action probability distributions

**What's missing:**
- No daemon/scheduler (everything is CLI-triggered)
- No model promotion/rollback
- No training trigger from dashboard (endpoint is a placeholder)
- No alerting

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
