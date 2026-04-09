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

### Step 1: Scheduler daemon
- **Problem:** Add a background scheduler that can trigger training cycles on a cron schedule or event-based trigger.

### Step 2: Training trigger logic
- **Problem:** Implement "train if transitions > N" or "train if time since last cycle > T" automation.

### Step 3: Continuous evaluation
- **Problem:** Run evaluation games in parallel with training to benchmark the current model continuously.

### Step 4: Model promotion
- **Problem:** After training, evaluate new checkpoint against current best. If better by threshold, promote to "best".

### Step 5: Promotion logging
- **Problem:** Record every promotion/rejection with evidence (win rate comparison, eval game count, action distribution shift) to documentation/wiki/promotions.md.

### Step 6: Rollback mechanism
- **Problem:** If promoted model performs worse over N subsequent games, auto-revert to previous best.

### Step 7: Curriculum auto-advancement
- **Problem:** Difficulty auto-management based on real win rates (already partially exists in trainer.py, wire it into the autonomous loop).

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

## Current State (2026-04-09)

**What exists:**
- TrainingOrchestrator — full RL loop, but CLI-only, manual trigger
- SQLite DB — games + transitions, win rate queries
- WebSocket broadcasting — live game state, decisions, commands (ephemeral)
- JSONL logging — per-game logs (no reward data by default)
- React dashboard — LiveView, TrainingDashboard, CheckpointList, RewardRuleEditor
- Evaluation scripts — evaluate_model.py, analyze_rewards.py (manual, post-hoc)
- Curriculum system — auto-increases difficulty when win_rate >= 0.8
- Wiki — 15 pages documenting all systems (documentation/wiki/)

**What's missing:**
- No daemon/scheduler (everything is CLI-triggered)
- No persistent decision logs (WebSocket data lost on disconnect)
- Reward logging is opt-in (`--reward-log` flag)
- No model promotion/rollback
- No training trigger from dashboard (endpoint is a placeholder)
- No cross-checkpoint comparison
- No alerting

## Decisions

- [ ] Scheduler technology: APScheduler vs cron vs custom daemon?
- [ ] Should eval games run on same machine as training or separate?
- [ ] Dashboard tech: keep React polling or switch to full WebSocket for training status?
- [ ] Model storage: local checkpoints vs artifact registry?

## Notes

- **Disk usage:** Phase 2 step 3 makes reward JSONL logging always-on. This increases
  disk usage proportionally to games played. Disk management (log rotation, compression,
  max file age, cleanup scripts) is deferred to a later phase. For now, monitor
  `data/reward_logs/` size manually. The training DB already has a 200 GB disk guard.
