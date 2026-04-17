# Evaluation Pipeline

How the bot knows if it's getting better.

> **At a glance:** Four evaluation layers at four timescales — per-step shaped rewards (63 JSON rules + base rewards), per-game win/loss (SQLite), cross-game rolling win rates, and per-checkpoint deterministic eval via `ModelEvaluator`. The `PromotionManager` uses the per-checkpoint eval as its promotion gate (see [promotions.md](promotions.md)). Reward logging is always-on per-game in `bots/v0/data/reward_logs/`.

This doc covers "how do we measure improvement." See [training-pipeline.md](training-pipeline.md) for what drives the improvement, and [improve-bot-advised-architecture.md](improve-bot-advised-architecture.md) for how the outer loop's TEST phase uses these measurements to validate a fix.

---

## Four Timescales

| Question | Timescale | Mechanism | Used by |
|---|---|---|---|
| Did this step help? | Per-step (every 22 game ticks) | `RewardCalculator` — 63 shaped rules + base rewards | PPO training (inner-loop TRAIN) |
| Did we win? | Per-game | Win/loss row in `bots/v0/data/training.db` | All higher-timescale signals |
| Is the model's WR trending? | Cross-game | `get_recent_win_rate(N)` over last N games | Curriculum auto-advancement; TrainingDashboard rolling windows |
| Is this checkpoint better than the last? | Per-checkpoint | `ModelEvaluator` — deterministic inference-only eval over 20 games | `PromotionManager`, `RollbackMonitor`, TEST-phase validation |

All four write to the same `bots/v0/data/training.db`. `ModelEvaluator` uses a distinct `eval_*` game-id prefix so its games don't pollute training-WR windows.

---

## Reward Shaping (per-step)

### The calculator

Entry point: `RewardCalculator.compute_step_reward(state, is_terminal, result)` → `float`

```
state dict ──> _add_derived_fields() ──> evaluate each active rule
                                              │
                                              ├─ condition clause (field op value)
                                              ├─ requires clause (optional gate)
                                              └─ if both pass: add rule.reward
                                         + base step reward
                                         + terminal bonus/penalty (if is_terminal)
                                         + JSONL logging (per-game)
```

### Rule schema (`bots/v0/data/reward_rules.json`)

```json
{
  "rules": [
    {
      "id": "scout-early",
      "description": "Reward scouting before 3:00",
      "condition": {"field": "game_time_seconds", "op": "<", "value": 180},
      "requires":  {"field": "has_scouted", "op": "==", "value": true},
      "reward": 0.01,
      "active": true
    }
  ]
}
```

63 rules currently active. Operators: `<`, `>`, `<=`, `>=`, `==`, `!=`. Outer-loop FIX-phase training-type iterations append/edit rules directly; config backups live at `data/reward_rules.pre-advised-<RUN_TS>.json` per run.

### Derived fields (computed before rule evaluation)

| Field | Condition |
|---|---|
| `has_scouted` | Set by caller from `ScoutManager` state (default `False`) |
| `enemy_structure_near_base_early` | `enemy_structure_count > 0 and game_time_seconds < 300 and enemy_army_near_base` |
| `is_mineral_floating` | `minerals > 1000` |
| `army_stronger_than_enemy` | `army_supply > enemy_army_supply_visible` |
| `is_defending_rush` | `enemy_army_near_base and current_state == "defend"` |

### Base rewards (always applied)

| Step | Value |
|---|---|
| Per-step survival | +0.001 |
| Terminal: win | +100.0 |
| Terminal: loss | -100.0 |
| Terminal: timeout | -50.0 ± 30.0 (gradient by army/enemy-army ratio — big idle army is punished harder) |

### Per-game reward logging (always on)

`RewardCalculator` is constructed with `log_dir=bots/v0/data/reward_logs/`. Before each game, `open_game_log(game_id)` opens `bots/v0/data/reward_logs/game_<id>.jsonl` and writes one line per step:

```json
{"game_time": 150.0, "total_reward": 0.581, "fired_rules": [{"id": "mineral-floating", "reward": -0.02}], "is_terminal": false, "result": null}
```

The Improvements → Reward Trends tab aggregates these via `reward_aggregator.aggregate_reward_trends()` exposed at `/api/training/reward-trends`.

---

## Win-Rate Tracking (per-game & cross-game)

`TrainingDB.get_recent_win_rate(n_games)` — queries the last N games from the `games` table, counts wins, returns proportion.

Two downstream consumers:

- **Curriculum auto-advance** (`TrainingOrchestrator`) — after each cycle, if `win_rate >= 0.8` and difficulty < max, difficulty++.
- **TrainingDashboard** — rolling windows last-10/50/100/overall, 5s poll.

Crashed games are tracked separately; `compute_adjusted_win_rate()` shrinks the query window to exclude them from the denominator (Phase 4.5 blocker #67).

> **Training WR is noisy.** PPO exploration means training-mode WR is not a reliable learning signal. For promotion decisions and "did this fix work" questions, use deterministic eval (below) instead.

---

## Per-Checkpoint Evaluation (`ModelEvaluator`)

`ModelEvaluator.evaluate(checkpoint_name, n_games, difficulty, cancel_check=None)` → `EvalResult`

Runs N inference-only games (no gradient updates, deterministic policy) with the specified checkpoint loaded. Returns:

```python
EvalResult(
    games_played: int,    # Valid (non-crashed) games
    wins: int,
    losses: int,
    crashed: int,         # Excluded from games_played
    win_rate: float,      # wins / games_played, NOT wins / (played + crashed)
    avg_reward: float,
    avg_duration: float,
    difficulty: int,
    action_distribution: dict[int, float],
)
```

`ComparisonResult` — thin significance check: >5% WR delta with ≥10 total games.

**Job system** — evaluator runs can be triggered via API (`POST /api/training/evaluate`), polled (`GET /api/training/evaluate/{job_id}`), and cancelled (`POST /api/training/evaluate/{job_id}/stop`). This is what the Training tab uses for manual checkpoint comparison.

`ModelEvaluator` is the engine behind the promotion gate — see [promotions.md](promotions.md).

---

## Game-ID Conventions (important for debugging)

Every game gets a unique ID of the shape `{base}_{uuid4.hex[:12]}`:

| Base prefix | Source |
|---|---|
| `rl_{uuid[:8]}` | Trainer games (PPO gradient updates) |
| `eval_{checkpoint}_{uuid[:8]}` | Evaluator games (inference only) |
| `game_{uuid[:8]}` | `--batch` runs (outside training) |

`SC2Env.game_id` is set after `reset()`. Callers querying `TrainingDB.get_game_result` MUST read the property after reset — reading the construction-time base ID will miss every row (Phase 4.7 Step 1 regression guard in `tests/test_evaluator_db_roundtrip.py`).

---

## Database Schema

SQLite at `bots/v0/data/training.db`. Two tables:

### `games` — one row per completed game

| Column | Type | Notes |
|---|---|---|
| `game_id` | TEXT PRIMARY KEY | `{base}_{uuid}` format |
| `map_name` | TEXT | |
| `difficulty` | INTEGER | SC2 AI difficulty 1–10 |
| `result` | TEXT | `win` / `loss` / `timeout` |
| `duration_secs` | REAL | |
| `total_reward` | REAL | Sum of all step rewards |
| `model_version` | TEXT | Checkpoint name |
| `created_at` | TEXT | ISO datetime |

### `transitions` — one row per environment step (every 22 game ticks)

- 17 base game-state features (see `BASE_GAME_FEATURE_DIM` in `features.py`)
- `action INTEGER` — one of 6 strategic states (0=OPENING through 5=FORTIFY)
- `reward REAL` — shaped reward for this step
- 17 next_state features (nullable for terminal steps)
- `done INTEGER` — 1 if terminal

Indexes on `game_id`, `action`, `result`, `model_version`.

Note: the PPO feature vector is actually **24 dimensions** (`FEATURE_DIM = 24`) — 17 base + 7 advisor features (6 binary advisor-command-action indicators + 1 advisor urgency). Only the 17 base features are stored in the DB; advisor features are ephemeral context at inference time.

---

## Feature Vector (24 dimensions)

Normalized to [0, 1] by dividing by per-feature max values.

| # | Feature | Max | Notes |
|---|---|---|---|
| 0 | supply_used | 200 | |
| 1 | supply_cap | 200 | |
| 2 | minerals | 2000 | |
| 3 | vespene | 2000 | |
| 4 | army_supply | 200 | |
| 5 | worker_count | 80 | |
| 6 | base_count | 5 | |
| 7 | enemy_army_near_base | 1 | bool |
| 8 | enemy_army_supply_visible | 200 | |
| 9 | game_time_seconds | 1200 | |
| 10 | gateway_count | 10 | Protoss-specific |
| 11 | robo_count | 4 | Protoss-specific |
| 12 | forge_count | 2 | Protoss-specific |
| 13 | upgrade_count | 10 | Protoss-specific |
| 14 | enemy_structure_count | 50 | |
| 15 | cannon_count | 10 | Protoss-specific |
| 16 | battery_count | 10 | Protoss-specific |
| 17 | advisor: scout | 1 | bool, advisor command recommended |
| 18 | advisor: build | 1 | |
| 19 | advisor: expand | 1 | |
| 20 | advisor: attack | 1 | |
| 21 | advisor: defend | 1 | |
| 22 | advisor: upgrade | 1 | |
| 23 | advisor: urgency | 1 | low=0.25, medium=0.5, high=0.75, critical=1.0 |

Features 10–16 are SC2/Protoss-specific (domain coupling). Features 17–23 let PPO learn to follow the advisor when that correlates with winning (Phase 4.8, #89 approach B).

---

## Cross-Game Statistics

`batch_runner.compute_aggregates(games)` — aggregates `GameRecord` list into `StatsAggregates`:

- Total wins/losses
- Breakdown by map, opponent, build order
- Persisted in `data/stats.json`, served by `/api/stats`

### Two data stores (not one)

| Store | Written by | Used for |
|---|---|---|
| `bots/v0/data/training.db` | `TrainingDB.store_game()` during training + eval | Win-rate queries, curriculum, PPO batches, ModelEvaluator |
| `data/stats.json` | `batch_runner.save_stats()` during `--batch` runs | Cross-game aggregates, Stats tab |

These are **not synced**. A game played during RL training/eval goes into SQLite only. A game played via `--batch` goes into stats.json only. The Stats tab reads both: per-difficulty aggregates from `stats.json`, per-game browsable history from `training.db`.

---

## Post-Hoc Analysis Scripts

| Script | Input | Output |
|---|---|---|
| `scripts/evaluate_model.py` | `--mode {rules,neural,hybrid} --games N --difficulty D` | Stdout: win/loss count, avg duration |
| `scripts/analyze_rewards.py` | `bots/v0/data/reward_logs/game_*.jsonl` | Stdout: per-rule fire rate, total contribution, dead/noisy rule warnings |

Since reward logging is now always-on, `analyze_rewards.py` always has data to read.

---

## Key File Locations

| File | Purpose |
|---|---|
| `bots/v0/learning/rewards.py` | `RewardCalculator`, `RewardRule`, derived fields |
| `bots/v0/learning/database.py` | `TrainingDB`, schema, queries |
| `bots/v0/learning/features.py` | `encode` / `decode`, `FEATURE_DIM=24`, `BASE_GAME_FEATURE_DIM=17` |
| `bots/v0/learning/evaluator.py` | `ModelEvaluator`, `EvalResult`, `ComparisonResult`, job system |
| `bots/v0/learning/reward_aggregator.py` | Per-rule trend aggregation for Reward Trends tab |
| `bots/v0/batch_runner.py` | `GameRecord`, `StatsAggregates`, `compute_aggregates` |
| `bots/v0/data/reward_rules.json` | 63 active reward rule definitions |
| `bots/v0/data/reward_logs/` | Per-game reward JSONL files |
| `bots/v0/data/training.db` | SQLite database |
| `data/stats.json` | Cross-game aggregates |
| `scripts/evaluate_model.py` | Manual model evaluation |
| `scripts/analyze_rewards.py` | Post-hoc reward analysis |
