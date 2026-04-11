# Evaluation Pipeline

How the bot knows if it's getting better.

> **At a glance:** Three evaluation layers — per-step shaped rewards (JSON rule engine),
> per-game win/loss (SQLite), and cross-game trends (sliding window + stats.json).
> Training and post-hoc analysis are manual. No continuous benchmarking, no per-checkpoint
> comparison, no regression detection.

## Purpose & Design

Evaluation answers three questions at different timescales:

| Question | Timescale | Mechanism Today |
|----------|-----------|-----------------|
| Did this step help? | Per-step (every 22 game ticks) | Shaped reward via `RewardCalculator` |
| Did we win? | Per-game | Win/loss stored in `games` table |
| Is the model improving? | Cross-game | `get_recent_win_rate()` over sliding window |

The evaluation system has two layers:

1. **Reward shaping** — a JSON-driven rule engine that assigns dense per-step rewards,
   guiding PPO toward good play even before terminal outcomes are known.
2. **Win-rate tracking** — SQLite-backed game results that drive curriculum decisions
   (increase difficulty when win_rate >= 0.8).

There are also two post-hoc analysis scripts (`evaluate_model.py`, `analyze_rewards.py`)
that run manually after training.

### Gaps

> These feed directly into [Phase 2 of the always-up plan](../plans/always-up-plan.md).

- **No continuous evaluation.** Evaluation only happens inside training cycles or via
  manual script runs. There's no background process that periodically benchmarks the
  current model.
- **No per-checkpoint comparison.** Win rates are queried over recent games regardless
  of which model version played them. There's no "model v3 had 70% win rate, v4 has 85%"
  view.
- **No statistical significance.** Win rates are raw proportions with no confidence
  intervals or hypothesis testing.
- **No action distribution tracking.** We don't track how the model's action preferences
  change over time (e.g., "v4 attacks more aggressively than v3").
- **No regression detection.** Nothing alerts when a new model performs worse than the
  previous one.

### Two data stores (not one)

There are two independent data paths for game results — a common source of confusion:

| Store | Written by | Contains | Used for |
|-------|-----------|----------|----------|
| `data/training.db` (SQLite) | `TrainingDB.store_game()` during RL training | Per-game results + per-step transitions | Win-rate queries, curriculum decisions, PPO training batches |
| `data/stats.json` (JSON) | `batch_runner.save_stats()` during `--batch` runs | Per-game records with map/opponent/build-order breakdown | Cross-game aggregates, dashboard Stats view |

These are **not synced**. A game played during RL training goes into SQLite but not
stats.json. A game played via `--batch` goes into stats.json but not SQLite (unless
training mode is active). A future "always up" system would need to unify these or pick
one as the source of truth.

---

## Key Interfaces

> **At a glance:** `RewardCalculator` is the hot path — called every 22 game ticks.
> `TrainingDB` stores results. `batch_runner` aggregates stats separately.
> Two scripts exist for manual post-hoc analysis.

### Reward calculation

Entry point: `RewardCalculator.compute_step_reward(state, is_terminal, result)`

```
state dict ──> _add_derived_fields() ──> check each RewardRule ──> sum rewards
                                              │
                                              ├─ condition clause (field op value)
                                              ├─ requires clause (optional gate)
                                              └─ if both pass: add rule.reward
                                         + base rewards (win/loss/step/timeout)
                                         + optional JSONL logging
```

**Input:** A `state` dict containing GameSnapshot fields (minerals, supply, army_supply,
game_time, etc.). The calculator adds derived fields before evaluating rules:

| Derived field | Logic |
|---------------|-------|
| `has_scouted` | `enemy_structure_count > 0` |
| `enemy_structure_near_base_early` | `enemy_near == 1 and game_time < 300` |
| `is_mineral_floating` | `minerals > 800` |
| `army_stronger_than_enemy` | `army_supply > enemy_supply` |
| `is_defending_rush` | `enemy_near == 1 and game_time < 180 and army_supply >= 4` |

**Output:** A single float — the total shaped reward for this step.

**Base rewards** (always applied on terminal steps):

| Outcome | Reward |
|---------|--------|
| Win | +10.0 |
| Loss | -10.0 |
| Timeout | -3.0 |
| Per-step survival | +0.001 |

### Reward rules

Rules live in `data/reward_rules.json`. Each rule has:

```json
{
  "id": "scout-early",
  "description": "Reward scouting before 3 minutes",
  "condition": {"game_time": {"<": 180}, "has_scouted": {"==": true}},
  "requires": null,
  "reward": 0.1,
  "active": true
}
```

14 rules currently defined, grouped by concern:

| Category | Rules | Total reward range |
|----------|-------|-------------------|
| Scouting & info | scout-early, early-scout-tight, map-awareness | +0.1 to +0.15 per step |
| Economy | worker-saturation, worker-production, expand-on-time, mineral-floating, no-supply-block | -0.05 to +0.2 |
| Military | army-buildup, army-ratio, gateway-efficiency, tech-progress | +0.03 to +0.1 |
| Defense | defend-rush, react-to-rush | +0.3 each |

Supported operators: `<`, `>`, `<=`, `>=`, `==`, `!=`.

#### Worked example: how a step gets scored

Given this game state at game_time=150, minerals=900, army_supply=8, enemy_near=1:

1. `_add_derived_fields` computes: `has_scouted=False`, `is_mineral_floating=True`,
   `enemy_structure_near_base_early=True`, `is_defending_rush=True`
2. Rules evaluated (only active, matching rules shown):
   - `mineral-floating`: condition `minerals > 800` passes → **-0.02**
   - `react-to-rush`: condition `is_defending_rush == true` passes → **+0.3**
   - `defend-rush`: condition `enemy_structure_near_base_early == true`,
     requires `army_supply >= 6` passes → **+0.3**
3. Base step reward: **+0.001**
4. **Total: +0.581**

If the game ends this step as a win, add **+10.0** → total **10.581**.

#### Reward logging (opt-in)

Reward-level JSONL logging is **off by default**. To enable it, pass `--reward-log` when
running games. This writes `data/reward_log.jsonl` with one line per step:

```json
{"game_time": 150.0, "total_reward": 0.581, "fired_rules": [{"id": "mineral-floating", "reward": -0.02}, ...], "is_terminal": false, "result": null}
```

Without this flag, `scripts/analyze_rewards.py` has nothing to read. This is a gap —
the always-up plan (Phase 2, step 2.3) targets making reward logging default.

### Win-rate tracking

Entry point: `TrainingDB.get_recent_win_rate(n_games)`

Queries the last N games from the `games` table, counts wins, returns proportion.
Used by `TrainingOrchestrator` to decide curriculum advancement:

```
win_rate = db.get_recent_win_rate(2 * games_per_cycle)
if win_rate >= 0.8 and difficulty < max_difficulty:
    difficulty += 1
```

### Cross-game statistics

Entry point: `batch_runner.compute_aggregates(games)`

Aggregates `GameRecord` list into `StatsAggregates`:
- Total wins/losses
- Breakdown by map, opponent, build order
- Stored in `data/stats.json`

### Post-hoc scripts

| Script | Input | Output |
|--------|-------|--------|
| `scripts/evaluate_model.py` | `--mode {rules,neural,hybrid} --games N --difficulty D` | Stdout: win/loss count, avg duration |
| `scripts/analyze_rewards.py` | `data/reward_log.jsonl` (requires `--reward-log` flag during games) | Stdout: per-rule fire rate, total contribution, dead/noisy rule warnings |

---

## Implementation Notes

> **At a glance:** SQLite with 2 tables (games + transitions), 17-dim feature vector
> normalized to [0,1], RewardCalculator with operator-based clause matching.
> Features 10-16 are SC2/Protoss-specific.

> These reference specific functions and data structures. Verify against code before
> relying on exact signatures — implementations change with refactors.

### Database schema

SQLite at `data/training.db`. Two tables:

**`games`** — one row per completed game:
- `game_id TEXT PRIMARY KEY` — shape `{base}_{uuid4.hex[:12]}`, where `{base}`
  is the construction-time label supplied by the caller
  (`rl_{uuid[:8]}` for trainer games, `eval_{checkpoint}_{uuid[:8]}` for
  evaluator games). `SC2Env.reset()` appends the 12-char uuid suffix on every
  reset as collision protection (Phase 4.6 Step 1 / `b27c6cc`). Callers that
  need to query `TrainingDB.get_game_result` by this primary key MUST read
  `SC2Env.game_id` as a read-only `@property` AFTER `reset()` returns —
  reading the construction-time base id will miss every row (Phase 4.7 Step 1
  / `c37492d`; regression guard in `tests/test_evaluator_db_roundtrip.py`).
- `map_name`, `difficulty`, `result` (win/loss/timeout), `duration_secs`
- `total_reward` — sum of all step rewards for the game
- `model_version` — checkpoint name that played this game
- `created_at` — ISO datetime

**`transitions`** — one row per environment step (every 22 game ticks):
- 17 state features (supply, minerals, army, structures, etc.)
- `action INTEGER` — one of 6 strategic states (0=OPENING through 5=FORTIFY)
- `reward REAL` — shaped reward for this step
- 17 next_state features (nullable for terminal steps)
- `done INTEGER` — 1 if terminal

Indexes on: `game_id`, `action`, `result`, `model_version`.

### Feature vector

17 dimensions, each normalized to [0, 1] by dividing by a max value:

| Index | Feature | Max |
|-------|---------|-----|
| 0 | supply_used | 200 |
| 1 | supply_cap | 200 |
| 2 | minerals | 2000 |
| 3 | vespene | 2000 |
| 4 | army_supply | 200 |
| 5 | worker_count | 80 |
| 6 | base_count | 5 |
| 7 | enemy_army_near_base | 1 (bool) |
| 8 | enemy_army_supply_visible | 200 |
| 9 | game_time_seconds | 1200 |
| 10 | gateway_count | 10 |
| 11 | robo_count | 4 |
| 12 | forge_count | 2 |
| 13 | upgrade_count | 10 |
| 14 | enemy_structure_count | 50 |
| 15 | cannon_count | 10 |
| 16 | battery_count | 10 |

Features 10-16 are Protoss-specific (SC2 domain coupling).

### RewardCalculator internals

- `__init__` optionally loads rules from JSON and opens a JSONL log file
- `compute_step_reward` iterates all active rules, checks `condition` and `requires`
  clauses against the state dict (with derived fields), sums matching rule rewards
  plus base rewards
- `_check_clause` evaluates nested `{field: {op: value}}` dicts using Python's
  `operator` module
- `_add_derived_fields` computes 5 derived booleans from raw state
- JSONL logging (if `log_path` set) writes one line per step:
  `{game_time, total_reward, fired_rules: [{id, reward}], is_terminal, result}`

### Key file locations

| File | Purpose |
|------|---------|
| `src/alpha4gate/learning/rewards.py` | RewardCalculator, RewardRule |
| `src/alpha4gate/learning/database.py` | TrainingDB, schema, queries |
| `src/alpha4gate/learning/features.py` | encode/decode, FEATURE_DIM, feature spec |
| `src/alpha4gate/batch_runner.py` | GameRecord, StatsAggregates, compute_aggregates |
| `data/reward_rules.json` | 14 reward rule definitions |
| `data/training.db` | SQLite database |
| `data/stats.json` | Cross-game aggregates |
| `scripts/evaluate_model.py` | Manual model evaluation |
| `scripts/analyze_rewards.py` | Post-hoc reward analysis |
