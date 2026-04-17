# Monitoring & Observability

How to watch the autonomous learning loop from the outside.

> **At a glance:** This system has two layers — an **autonomous learning loop**
> (PLAY → THINK → FIX → TEST → COMMIT → TRAIN) and the **task** it's learning
> (an SC2 game). Monitoring answers two questions: *"Is the loop making
> progress?"* and *"Is the task actually running?"* The dashboard has a tab
> tuned to each phase of the loop, plus the single source of truth —
> `data/advised_run_state.json` — that tells you where the loop is right now.

See [improve-bot-advised-architecture.md](improve-bot-advised-architecture.md) for the loop itself. This doc explains how to watch it run.

---

## The Framework

```
          ┌──────────────────────────────────────────────────┐
          │          autonomous learning loop                │
          │                                                  │
  ┌─────┐ │  ┌──────┐   ┌───────┐   ┌──────┐                │
  │     │◄───┤ PLAY │──►│ THINK │──►│ FIX  │                │
  │ THE │ │  └──────┘   └───────┘   └──┬───┘                │
  │TASK │ │                            │                    │
  │     │◄───┬───────┬───◄───┬──◄──────┘                    │
  │(SC2)│ │  │ TRAIN │◄──┤COMMIT│◄───┤TEST │                │
  │     │ │  └───────┘   └──────┘    └─────┘                │
  └─────┘ └──────────────────────────────────────────────────┘

         ▲                                   ▲
         │ observe the game                  │ observe the loop
         │                                   │
   ┌─────┴─────┐                       ┌─────┴─────┐
   │ Live tab  │                       │ Advisor   │
   │ WebSocket │                       │ tab +     │
   │ JSONL     │                       │ run log   │
   └───────────┘                       └───────────┘
```

Two vantage points:
- **Task-level** — what's happening inside the current SC2 game (Live tab, WebSocket `/ws/game`).
- **Loop-level** — which phase the loop is in, whether it's making progress (Advisor tab, run log, state file).

---

## The Single Source of Truth

`data/advised_run_state.json` is rewritten by `/improve-bot-advised` after every phase boundary. Everything else in this doc is a view onto parts of it, or evidence that one of its claims is still true.

| Field | What it tells you |
|---|---|
| `status` | `idle \| running \| paused \| stopped \| completed \| resetting` |
| `phase` (0–8) + `phase_name` | Which loop step is currently executing |
| `iteration` / `max_iterations` / `fail_streak` | Loop progress + consecutive-failure counter |
| `mode` / `games_per_cycle` / `difficulty` | Active run parameters |
| `baseline_win_rate` → `current_win_rate` | Headline metric |
| `hours_budget` / `elapsed_seconds` | Wall-clock budget remaining |
| `current_improvement` | What's being tried this iteration (set in FIX, cleared after COMMIT) |
| `iterations[]` | History: `{num, title, result (pass/marginal/fail), delta}` |
| `updated_at` | **If this is older than ~2 min during a "running" status, the loop is stuck.** |

**Control counterpart:** `data/advised_run_control.json` is the write-to-the-loop file. The dashboard (`PUT /api/advised/control`) sets fields here; the skill reads them at the start of the next PLAY, THINK, and loop-decision phase. Fields: `games_per_cycle`, `difficulty`, `fail_threshold`, `user_hint`, `stop_run`, `reset_loop`, `reward_rule_add`.

---

## Monitoring by Phase

Each loop phase has a primary dashboard tab, a persistent evidence file, and characteristic signatures in the run log.

### Phase overview

| Phase | Primary tab | Persistent evidence | Run-log marker |
|---|---|---|---|
| **THE TASK** (SC2) | Live | `logs/game_*.jsonl`, `replays/*.SC2Replay` | `Game X: Result.Victory (Xs)` |
| **PLAY** | Stats, Decisions | `bots/v0/data/training.db`, `data/stats.json`, `data/decision_audit.json` | `=== ITERATION N BATCH START/COMPLETE ===` |
| **THINK** | Advisor (`current_improvement`) | `data/decision_audit.json`, skill-scoped prompt in log | "## Iteration N Summary — selected improvement" |
| **FIX** | Advisor | `data/reward_rules.pre-advised-<RUN_TS>.json` backup, feature branch | "applying change: <title>" |
| **TEST** | Stats (validation rows) | validation games in `training.db` | `=== ITER N VALIDATION START/COMPLETE ===` |
| **COMMIT** | Improvements | `data/improvement_log.json`, git master, GitHub issue | `improve-bot-advised: <title> (iteration N)` commit |
| **TRAIN** | Training, Loop, Improvements | `bots/v0/data/promotion_history.json`, `bots/v0/data/checkpoints/manifest.json` | "daemon cycle N complete" |

### THE TASK — "is the game actually running?"

```
SC2 engine
  ├──> on_step() every 11 game steps
  │      └──> observer.observe() ──> GameLogger thread
  │                                    ├──> logs/game_<TS>.jsonl   (permanent)
  │                                    └──> /ws/game broadcast     (ephemeral)
  └──> game end ──> training.db row + replays/<ID>.SC2Replay
```

- **Live tab** (`LiveView.tsx`) — minerals, supply, army composition, strategic state, Claude advice. Driven by `/ws/game` over the broadcast loop (500ms drain cadence).
- **Processes tab** — confirms `SC2_x64` is alive. If it's not and the loop says `status=running`, something has crashed.
- **Per-game files** — one JSONL per game (`logs/game_<timestamp>.jsonl`), one SC2 replay (`replays/<ID>.SC2Replay`), one DB row.

### PLAY — "what happened in the batch?"

```
N games ──> training.db (games + transitions)
         ├─> stats.json (per-difficulty aggregates)
         ├─> reward_logs/game_*.jsonl (per-step reward firing)
         └─> decision_audit.json (live mode only)
```

- **Stats tab** (`Stats.tsx`) — per-difficulty W/L, recent games table, click a row for the per-game reward timeline (`/api/games/{id}`).
- **Decisions tab** (`DecisionQueue.tsx`) — state transitions and advisor suggestions captured during live-mode PLAY.
- **Run log** — every batch writes `=== ITERATION N BATCH START/COMPLETE ===` bookends with per-game results between them.

### THINK — "what did Claude conclude?"

- **Advisor tab** — `current_improvement` field in the state JSON reflects the output of THINK.
- **Run log** — the "## Iteration N Summary" block names the selected improvement, its rank among candidates, type (training/dev), and principles addressed.
- **User steering** — set `user_hint` via the Advisor tab's text input; the skill injects it into the next THINK prompt.

### FIX — "what's being changed?"

- **Advisor tab** — `current_improvement` set, `phase=4`.
- **Config changes** (training mode) — before editing, the skill writes backups to `data/reward_rules.pre-advised-<RUN_TS>.json` and `data/hyperparams.pre-advised-<RUN_TS>.json`. Diff these against the current file to see what FIX changed.
- **Code changes** (dev mode, `--self-improve-code`) — `/improve-bot` creates a feature branch and GitHub issue. `gh issue list --label self-improve-run` shows the active branch.

### TEST — "did the fix hold up?"

- **Run log** — `=== ITER N VALIDATION START/COMPLETE: W / L (N timeouts) ===`.
- **Advisor tab** — iteration appears in the history table with `result: pass | marginal | fail` and win-rate `delta`.
- **fail_streak counter** — visible in the Advisor tab. At 3 consecutive fails the loop exits to Phase 8.
- **Threshold** — `fail_threshold` (default 30%) is adjustable via the control file during the run.

### COMMIT — "what got locked in?"

```
PASS ──> git commit to master
      ├─> data/improvement_log.json append
      ├─> per-iteration GitHub issue updated
      └─> Improvements tab refreshes
```

- **Improvements tab** (`AdvisedImprovements.tsx`) — persistent per-iteration log. Fields: `id, run_id, iteration, title, type, description, principles[], result, metrics {observation_wins, validation_wins, duration_delta_pct}, files_changed[]`. Endpoint: `/api/improvements`.
- **Git history** — `git log --grep="improve-bot-advised"` on master.
- **Run tags** — `advised/run/<RUN_TS>/baseline` and `advised/run/<RUN_TS>/final` bracket the session. Diff with `git log <baseline>..<final> --oneline`.
- **GitHub umbrella issue** — label `advised-improvement-run`, titled with `$RUN_TS`, created in Phase 8.

### TRAIN — "is the neural net getting better?"

```
new games ──> PPO gradient update ──> new checkpoint
                                         │
                                         ▼
                              PromotionManager.evaluate
                              ├─> better? promote + maybe difficulty++
                              └─> worse? rollback to previous best
                                         │
                                         ▼
                              promotion_history.json append
```

- **Training tab** (`TrainingDashboard.tsx`, 5s poll) — current checkpoint, total games, total transitions, DB size, rolling win rates (last-10/50/100/overall).
- **Loop tab** (`LoopStatus.tsx`) — daemon state (`idle | checking | training | evaluating`), `runs_completed`, `last_run`, `next_check`, `last_error`, `last_result`.
- **Improvements → Recent Improvements** (`RecentImprovements.tsx`) — reads `bots/v0/data/promotion_history.json` and classifies each entry as `promotion | rollback | rejected`. Fields per entry: `new_checkpoint, old_best, new_win_rate, old_win_rate, delta, reason, reason_code, difficulty`.
- **Improvements → Reward Trends** (`RewardTrends.tsx`) — per-rule contribution over the last N games, sourced from `bots/v0/data/reward_logs/game_*.jsonl` via `/api/training/reward-trends`.
- **Checkpoint list** (`CheckpointList.tsx`) — table of all saved checkpoints from `bots/v0/data/checkpoints/manifest.json`, with `best` indicator.

---

## Is Anything Happening? (stuck-loop detection)

The loop is a long-running autonomous process. Four signatures say "something is wrong."

### 1. State file is stale

`data/advised_run_state.json` has `status="running"` but `updated_at` is older than ~2 minutes.

- Check **Processes tab**: is `SC2_x64` running? is the backend (port 8765) still bound? is the Python daemon alive?
- The `StaleDataBanner` component renders on every tab when the poll fails, so dashboard staleness is loud.

### 2. Alert rule fires

Client-side rule engine in `frontend/src/lib/alertRules.ts`, polled by `useAlerts()`. All alerts are dashboard-only — no email/push.

| Rule | Severity | Fires when | Maps to |
|---|---|---|---|
| `ruleWinRateDropped` | warning | last-10 WR is 15%+ below last-50 (suppressed during advised runs, since WR swings are expected) | TRAIN |
| `ruleTrainingFailed` | error | daemon `last_error != null` | TRAIN |
| `ruleDaemonStoppedUnexpectedly` | error | daemon was `training/checking/evaluating`, now `idle` with error | TRAIN |
| `ruleRollbackFired` | warning | latest promotion entry is a rollback | TRAIN |
| `ruleNoTrainingInHours` | info | daemon running but `hours_since_last > 24` | TRAIN liveness |
| `ruleDiskUsageHigh` | warning | DB + reward logs > 50 GB | storage |
| `ruleBackendErrors` | error | `error_count_since_start > 0`; hashed on latest ts so new errors re-fire | cross-cutting |

Backed by `ErrorLogBuffer` (50-entry ring in `bots/v0/error_log.py`), surfaced via `/api/training/status → recent_errors[]`. Synthetic test: `POST /api/debug/raise_error` (gated by `DEBUG_ENDPOINTS=1`) — use before a multi-hour run to confirm the alerts pipe works end-to-end.

### 3. Fail streak

`fail_streak >= 3` in the state file → the loop exits Phase 7 straight to Phase 8. Check the run log for the last 3 iterations to see why the fixes kept failing.

### 4. Wall-clock budget exhausted

`elapsed_seconds >= hours_budget × 3600` → no new iteration starts. Progress bar in the Advisor tab turns red above 90%.

---

## Operator Controls (writing back to the loop)

| Action | How | Effect |
|---|---|---|
| Stop the run | Advisor tab "Stop Run" button → `POST /api/cleanup/stop-run` | Sets `stop_run=true`; next phase boundary exits to Phase 8 + morning report |
| Reset to baseline | Advisor tab "Reset Loop" → `POST /api/cleanup/reset-loop` | Reverts to `advised/run/<RUN_TS>/baseline` tag, clears iteration history |
| Change games/cycle | Advisor tab numeric input → `PUT /api/advised/control` | Applied at next PLAY |
| Change difficulty | Advisor tab → control file | Applied at next PLAY |
| Inject a hint | Advisor tab text input → control file `user_hint` | Appended to the next THINK prompt |
| Add a reward rule | Advisor tab → control file `reward_rule_add` | Appended to `bots/v0/data/reward_rules.json` before next PLAY |
| Force-kill daemon | Processes tab → `POST /api/kill-daemons` | Targeted daemon shutdown |
| Restart backend | Processes tab → `POST /api/restart` | Backend lifecycle bounce |

---

## Persistence Map — what's saved vs ephemeral

| Data | Storage | Lifetime | Phase |
|------|---------|----------|-------|
| Loop state | `data/advised_run_state.json` | Current run (overwritten each phase boundary) | All |
| Loop control | `data/advised_run_control.json` | Until consumed by the skill | Operator → loop |
| Run narrative | `documentation/soak-test-runs/advised-YYYY-MM-DD.md` | Permanent | All |
| Per-iteration record | `data/improvement_log.json` | Permanent | COMMIT |
| Per-game snapshots | `logs/game_*.jsonl` | Permanent (one file per game) | THE TASK |
| Per-game rewards | `bots/v0/data/reward_logs/game_*.jsonl` | Permanent | PLAY, TEST, TRAIN |
| Game results + transitions | `bots/v0/data/training.db` | Permanent | PLAY, TEST, TRAIN |
| Batch aggregates | `data/stats.json` | Permanent | PLAY, TEST |
| Decision log | `data/decision_audit.json` | Current session; cleared between iterations | THINK |
| Promotion/rollback events | `bots/v0/data/promotion_history.json` | Permanent | TRAIN |
| Checkpoint manifest | `bots/v0/data/checkpoints/manifest.json` | Permanent | TRAIN |
| Config backups | `data/{reward_rules,hyperparams,daemon_config}.pre-advised-<RUN_TS>.json` | Per run (restore points) | FIX |
| Git tags | `advised/run/<RUN_TS>/{baseline,final}` | Permanent | Bootstrap + Phase 8 |
| GitHub issues | Umbrella + per-iteration, labeled | Permanent | COMMIT, Phase 8 |
| Live game state | WebSocket `/ws/game` | Ephemeral (lost on disconnect) | THE TASK |
| Command events | WebSocket `/ws/commands` | Ephemeral | THE TASK |
| Action probabilities | `NeuralDecisionEngine._last_probabilities` | Ephemeral (memory) | THE TASK |

---

## Dashboard Tab Reference

Tabs defined in `frontend/src/App.tsx`. Each tab consumes the endpoints or WebSockets listed, polls at the stated cadence, and maps to one or more loop phases.

| Tab | Component | Feeds | Cadence | Phase |
|---|---|---|---|---|
| Live | `LiveView.tsx` | `/ws/game` | Real-time | THE TASK |
| Stats | `Stats.tsx` | `/api/stats`, `/api/games`, `/api/games/{id}` | 10s | PLAY, TEST |
| Decisions | `DecisionQueue.tsx` | `/api/decision-log`, `/ws/decisions` | Initial + live | THINK, THE TASK |
| Training | `TrainingDashboard.tsx` + `ModelComparison` + `CheckpointList` + `RewardRuleEditor` | `/api/training/{status,history,models,checkpoints}`, `/api/reward-rules` | 5s | TRAIN |
| Loop | `LoopStatus.tsx` + `TriggerControls.tsx` | `/api/training/daemon`, `/api/training/triggers` | 5s | TRAIN |
| Advisor | `AdvisedControlPanel.tsx` | `/api/advised/state`, `/api/advised/control` | 3s state / 10s control | All 6 loop phases |
| Improvements | `RecentImprovements.tsx` + `RewardTrends.tsx` + `AdvisedImprovements.tsx` | `/api/training/promotions/history`, `/api/training/reward-trends`, `/api/improvements` | 10s | COMMIT, TRAIN |
| Processes | `ProcessMonitor.tsx` | `/api/processes`, cleanup endpoints | 5s | Cross-cutting (liveness) |
| Alerts | `AlertsPanel.tsx` + `AlertToast.tsx` | client rules over poll data | — | Cross-cutting |
| Ladder | `LadderTab.tsx` | `/api/ladder` (`data/bot_ladder.json`) | 10s | Cross-cutting (Elo) |

A green dot appears in the nav bar when `advised_run_state.status ∈ {running, paused}` (`App.tsx:86–99`).

---

## Technical Reference — how the data flows

> Verify against code before relying on exact signatures.

### Observer → Logger → WebSocket pipeline (for THE TASK)

```
Game Thread (bot.py)          Logger Thread (logger.py)     Async Loop (api.py)
  │                                │                             │
  ├─ observer.observe()             │                             │
  │   └─ logger.put(entry) ─────> queue.get()                    │
  │                                 ├─ write JSONL                │
  │                                 └─ queue_broadcast() ─────> drain_broadcast_queue()
  │                                                               ├─ ws_manager.broadcast()
  │                                                               │
  ├─ queue_command_event() ─────────────────────────────────> drain_command_event_queue()
  │                                                               └─ broadcast command event
```

Three threads, two queues. All cross-thread communication uses `queue.Queue` (thread-safe). The async broadcast loop uses `get_nowait()` to avoid blocking the event loop and drains at 500ms.

### Timing constants

| What | Interval | Where |
|------|----------|-------|
| Observer snapshot | Every 11 game steps (~0.5s at 1x) | `bot.py` |
| Broadcast loop drain | 500ms | `api.py` |
| Training dashboard poll | 5000ms | `TrainingDashboard.tsx` |
| Advisor tab poll | 3000ms state, 10000ms control | `useAdvisedRun.ts` |
| Processes tab poll | 5000ms | `ProcessMonitor.tsx` |
| Alerts recheck | 5000ms | `useAlerts.ts` |
| WebSocket reconnect | 3000ms | `useWebSocket.ts` |

### Key file locations

| File | Purpose |
|------|---------|
| `bots/v0/observer.py` | Extract game state dict from BotAI |
| `bots/v0/logger.py` | GameLogger — background thread JSONL writer |
| `bots/v0/web_socket.py` | ConnectionManager + broadcast/command queues |
| `bots/v0/api.py` | FastAPI app — REST endpoints, WS handlers, broadcast loop |
| `bots/v0/process_registry.py` | Process/port/state-file inventory for Processes tab |
| `bots/v0/error_log.py` | 50-entry error ring buffer surfaced via `/api/training/status` |
| `bots/v0/learning/promotion.py` | PromotionLogger writes `promotion_history.json` |
| `bots/v0/learning/rollback.py` | RollbackMonitor appends to same log |
| `.claude/skills/improve-bot-advised/SKILL.md` | Writes state file at each phase boundary |
| `frontend/src/lib/alertRules.ts` | Alert rule definitions |
| `frontend/src/hooks/useAdvisedRun.ts` | Advisor tab state + control hook |

### Known gaps

- **Decision data is ephemeral across sessions.** Action probabilities from the neural engine exist only in memory (`_last_probabilities`); no persistence.
- **No email/push alerting.** All alerts are dashboard-only; a run failing overnight is visible on the GitHub umbrella issue only after Phase 8 completes.
- **`/ws/decisions` is unused.** The endpoint accepts connections but no code broadcasts to it; decisions are served via REST instead.
- **`reward-log` flag is batch-only.** `--train rl` creates its own RewardCalculator without a log path — silently ignored. `bots/v0/data/reward_logs/` is populated by the advised loop directly, not the flag.
