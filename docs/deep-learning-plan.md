# Alpha4Gate — Deep Learning Plan

## What This Is

A self-learning extension to the Alpha4Gate SC2 bot. A neural network replaces the rule-based
`DecisionEngine` (strategic state machine) with a PPO-trained policy that improves through
experience. The bot plays batches of games against the built-in AI, stores game state transitions
in SQLite, trains the policy network on collected experience, and repeats — getting stronger with
each training cycle. A reward shaping system lets the user inject domain knowledge (e.g., "defend
against cannon rushes") as configurable reward rules without changing code. Training metrics stream
to the existing React dashboard for monitoring overnight runs.

This is Phase 2 of Alpha4Gate. Phase 1 (the rule-based bot, steps 1–10) is complete and
functional — the bot wins consistently against Easy AI.

---

## Stack

| Layer              | Tool / Library          | Why                                                        |
| ------------------ | ----------------------- | ---------------------------------------------------------- |
| Language           | Python 3.12             | Matches existing project                                   |
| Package manager    | uv                      | Consistent with existing workflow                          |
| SC2 interface      | burnysc2 (v7.1.3)       | Already in use, async BotAI base class                     |
| Deep learning      | PyTorch (>=2.0)         | Industry standard for RL, user familiarity                 |
| RL algorithm       | stable-baselines3 (SB3) | Battle-tested PPO (Proximal Policy Optimization) implementation, built on PyTorch. PPO is a policy gradient RL algorithm that updates the neural network's action probabilities using clipped surrogate objectives — it learns which actions lead to higher rewards while staying stable (no catastrophic policy changes between updates). SB3 is a Python library that provides ready-to-use implementations of standard RL algorithms. |
| Training data      | SQLite                  | Structured storage, fast random access, no server needed   |
| Feature encoding   | NumPy                   | Game state → fixed-size tensor conversion                  |
| Dashboard          | React (existing)        | Training metrics via existing WebSocket infrastructure     |
| Testing            | pytest                  | Consistent with existing test suite                        |
| Linting            | ruff                    | E,F,I,UP,B rules, line-length=100                          |
| Type checking      | mypy                    | Strict mode, disallow_untyped_defs                         |

### New dependencies

```
torch>=2.0
stable-baselines3>=2.0
numpy>=1.24
gymnasium>=0.29
```

Added to `[project.dependencies]` in `pyproject.toml`. No CUDA required — CPU training is
sufficient for the action space size (5 strategic states). GPU optional for faster convergence
on larger batches.

---

## Data Store

### SQLite training database

**File**: `data/training.db`

The database stores every game state transition observed during training games. Each row is one
`on_step()` observation — the state before the decision, the action taken, the reward received,
and the next state. This is the standard (s, a, r, s') tuple used in RL.

**Schema**:

```sql
CREATE TABLE IF NOT EXISTS games (
    game_id       TEXT PRIMARY KEY,   -- ISO timestamp, e.g. "2026-03-29T14:30:00"
    map_name      TEXT NOT NULL,
    difficulty     INTEGER NOT NULL,   -- 1-10
    result         TEXT NOT NULL,       -- "win" or "loss"
    duration_secs  REAL NOT NULL,
    total_reward   REAL NOT NULL,       -- sum of shaped rewards for the game
    model_version  TEXT NOT NULL,       -- checkpoint name, e.g. "v0_pretrain" or "v12"
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transitions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id       TEXT NOT NULL REFERENCES games(game_id),
    step_index    INTEGER NOT NULL,    -- position within the game (0, 1, 2, ...)
    game_time     REAL NOT NULL,       -- game seconds elapsed
    -- State features (the GameSnapshot, encoded as individual columns for queryability)
    supply_used   INTEGER NOT NULL,
    supply_cap    INTEGER NOT NULL,
    minerals      INTEGER NOT NULL,
    vespene       INTEGER NOT NULL,
    army_supply   INTEGER NOT NULL,
    worker_count  INTEGER NOT NULL,
    base_count    INTEGER NOT NULL,
    enemy_near    INTEGER NOT NULL,    -- 0 or 1
    enemy_supply  INTEGER NOT NULL,
    -- Extended features (beyond current GameSnapshot)
    gateway_count     INTEGER NOT NULL DEFAULT 0,
    robo_count        INTEGER NOT NULL DEFAULT 0,
    forge_count       INTEGER NOT NULL DEFAULT 0,
    upgrade_count     INTEGER NOT NULL DEFAULT 0,
    enemy_structure_count INTEGER NOT NULL DEFAULT 0,
    -- Action and reward
    action        INTEGER NOT NULL,    -- index into StrategicState enum (0-4)
    reward        REAL NOT NULL,       -- shaped reward for this step
    -- Next state (for temporal-difference learning — computing how much better/worse the next state is)
    next_supply_used   INTEGER,
    next_supply_cap    INTEGER,
    next_minerals      INTEGER,
    next_vespene       INTEGER,
    next_army_supply   INTEGER,
    next_worker_count  INTEGER,
    next_base_count    INTEGER,
    next_enemy_near    INTEGER,
    next_enemy_supply  INTEGER,
    next_gateway_count     INTEGER DEFAULT 0,
    next_robo_count        INTEGER DEFAULT 0,
    next_forge_count       INTEGER DEFAULT 0,
    next_upgrade_count     INTEGER DEFAULT 0,
    next_enemy_structure_count INTEGER DEFAULT 0,
    done          INTEGER NOT NULL DEFAULT 0  -- 1 if game ended this step
);

CREATE INDEX IF NOT EXISTS idx_transitions_game ON transitions(game_id);
CREATE INDEX IF NOT EXISTS idx_transitions_action ON transitions(action);
CREATE INDEX IF NOT EXISTS idx_games_result ON games(result);
CREATE INDEX IF NOT EXISTS idx_games_model ON games(model_version);
```

### Feature vector

The state observation is a fixed-size float vector extracted from `GameSnapshot` plus extended
features. Total: **14 features**.

| Index | Feature                    | Normalization         | Source                        |
| ----- | -------------------------- | --------------------- | ----------------------------- |
| 0     | supply_used                | / 200                 | `GameSnapshot.supply_used`    |
| 1     | supply_cap                 | / 200                 | `GameSnapshot.supply_cap`     |
| 2     | minerals                   | / 2000                | `GameSnapshot.minerals`       |
| 3     | vespene                    | / 2000                | `GameSnapshot.vespene`        |
| 4     | army_supply                | / 200                 | `GameSnapshot.army_supply`    |
| 5     | worker_count               | / 80                  | `GameSnapshot.worker_count`   |
| 6     | base_count                 | / 5                   | `GameSnapshot.base_count`     |
| 7     | enemy_army_near_base       | 0 or 1                | `GameSnapshot.enemy_army_near_base` |
| 8     | enemy_army_supply_visible  | / 200                 | `GameSnapshot.enemy_army_supply_visible` |
| 9     | game_time_seconds          | / 1200 (20 min cap)   | `GameSnapshot.game_time_seconds` |
| 10    | gateway_count              | / 10                  | count of own Gateways         |
| 11    | robo_count                 | / 4                   | count of own Robo Facilities  |
| 12    | forge_count                | / 2                   | count of own Forges           |
| 13    | upgrade_count              | / 10                  | count of completed upgrades   |

All values are clipped to [0, 1] after normalization. This keeps the network input stable
regardless of game length or economy size.

### Action space

Discrete, 5 actions mapping to `StrategicState`:

| Index | Action      | Meaning                                |
| ----- | ----------- | -------------------------------------- |
| 0     | OPENING     | Follow build order strictly            |
| 1     | EXPAND      | Prioritize economy and tech            |
| 2     | ATTACK      | Move army out, engage enemy            |
| 3     | DEFEND      | Pull back, defend bases                |
| 4     | LATE_GAME   | Multi-prong, tech switches             |

The action is chosen every 22 game steps (~1 real second at normal speed), matching the existing
observation frequency in `bot.py`.

---

## Reward System

### Base reward

| Event       | Reward |
| ----------- | ------ |
| Win         | +10.0  |
| Loss        | -10.0  |
| Per step    | +0.001 (survival bonus, encourages longer games over instant losses) |

The win/loss reward is applied to the final transition of each game.

### Reward shaping engine

Configurable reward rules loaded from `data/reward_rules.json`. Each rule defines a condition
and a bonus/penalty applied per step when the condition is true.

**Schema** (`data/reward_rules.json`):

```json
{
  "rules": [
    {
      "id": "scout-early",
      "description": "Reward scouting before 3:00",
      "condition": {
        "field": "game_time_seconds",
        "op": "<",
        "value": 180
      },
      "requires": {
        "field": "has_scouted",
        "op": "==",
        "value": true
      },
      "reward": 0.1,
      "active": true
    },
    {
      "id": "defend-rush",
      "description": "Reward having army near base when enemy structures detected early",
      "condition": {
        "field": "enemy_structure_near_base_early",
        "op": "==",
        "value": true
      },
      "requires": {
        "field": "army_supply",
        "op": ">=",
        "value": 6
      },
      "reward": 0.3,
      "active": true
    },
    {
      "id": "no-supply-block",
      "description": "Penalize being supply blocked",
      "condition": {
        "field": "supply_used",
        "op": "==",
        "value_field": "supply_cap"
      },
      "requires": null,
      "reward": -0.05,
      "active": true
    }
  ]
}
```

**Rule evaluation**: Each rule has a `condition` (when to check) and an optional `requires`
(what must be true for the reward). If `condition` matches and `requires` is satisfied, the
`reward` value is added to that step's total reward. Rules with `"active": false` are skipped.

**Supported operators**: `<`, `>`, `<=`, `>=`, `==`, `!=`. The `value_field` variant compares
two fields from the same game state instead of a field vs. a constant.

**Custom fields**: Some conditions reference derived fields not directly in GameSnapshot:
- `has_scouted`: true if ScoutManager has assigned a scout this game
- `enemy_structure_near_base_early`: true if enemy structures within 40 units of start location
  and game_time < 300s

These derived fields are computed in the reward calculator and added to the state dict before
rule evaluation.

---

## Neural Network Architecture

### Policy network

A simple MLP (multi-layer perceptron) — appropriate for the small observation space (14 floats)
and action space (5 discrete). No need for CNNs (no spatial input) or RNNs (state is fully
observable from the snapshot).

```
Input (14) → Linear(14, 128) → ReLU → Linear(128, 128) → ReLU → Linear(128, 5) → Softmax
                                                                          ↓
                                                              action probabilities
```

SB3's `MlpPolicy` handles this automatically. Custom network size is configured via
`policy_kwargs={"net_arch": [128, 128]}`.

### Value network

Shares the same architecture trunk but outputs a single scalar (state value estimate).
SB3 handles this with shared feature extractor + separate heads for policy and value.

```
Input (14) → [shared layers] → Linear → 1 (state value)
```

### Hyperparameters (starting point)

| Parameter          | Value    | Rationale                                          |
| ------------------ | -------- | -------------------------------------------------- |
| learning_rate      | 3e-4     | SB3 default, works well for small networks         |
| n_steps            | 2048     | Steps per rollout buffer (~3-4 games worth)        |
| batch_size         | 64       | Small enough for CPU training                      |
| n_epochs           | 10       | PPO update epochs per rollout                      |
| gamma              | 0.99     | Standard discount factor                           |
| gae_lambda         | 0.95     | GAE (Generalized Advantage Estimation) — smooths reward-to-go estimates to reduce variance during training |
| clip_range         | 0.2      | PPO clipping (standard)                            |
| ent_coef           | 0.01     | Entropy bonus for exploration                      |
| vf_coef            | 0.5      | Value function loss weight                         |
| max_grad_norm      | 0.5      | Gradient clipping                                  |

These are stored in `data/hyperparams.json` and loaded at training start. Editable without
code changes.

**`data/hyperparams.json` schema**:

```json
{
  "learning_rate": 3e-4,
  "n_steps": 2048,
  "batch_size": 64,
  "n_epochs": 10,
  "gamma": 0.99,
  "gae_lambda": 0.95,
  "clip_range": 0.2,
  "ent_coef": 0.01,
  "vf_coef": 0.5,
  "max_grad_norm": 0.5,
  "net_arch": [128, 128]
}
```

All keys map directly to SB3 `PPO()` constructor kwargs. The `net_arch` key is passed
inside `policy_kwargs={"net_arch": value}`. Unknown keys are ignored with a warning.

---

## Training Pipeline

### Phase 1: Imitation pre-training

Train the policy network to mimic the rule-based `DecisionEngine` using supervised learning.

1. Run 100+ games with the rule-based bot at varying difficulties (1, 3, 5)
2. Store all transitions in SQLite with the rule-based engine's chosen action as the label
3. Train the network using cross-entropy loss (action prediction) + MSE loss (value prediction)
4. Continue until the network achieves >95% action agreement with the rule-based engine

This gives the neural network a "warm start" — it begins at the current bot's skill level
rather than random.

**Imitation training mechanism**: SB3's PPO does not natively support behavior cloning.
The imitation phase uses a **custom training loop** that accesses the SB3 model's underlying
PyTorch policy network directly:

1. Create an SB3 `PPO` instance with the target architecture (MlpPolicy, net_arch=[128,128])
2. Access the PyTorch network via `model.policy` (an `ActorCriticPolicy` with `.action_net`
   and `.value_net` attributes)
3. Run a standard PyTorch training loop using `torch.optim.Adam` on `model.policy.parameters()`
4. Loss = `cross_entropy(policy_logits, rule_action_label)` + `0.5 * mse(value_pred, discounted_return)`
5. After training, save via `model.save()` — the checkpoint is a standard SB3 zip that PPO
   can load directly for the RL phase

This approach reuses the exact same network architecture for both phases. The RL phase loads
the imitation checkpoint with `PPO.load()` and continues training with PPO's own update rule.

**Imitation training loop**:
```
model = PPO("MlpPolicy", env, policy_kwargs={"net_arch": [128, 128]})
optimizer = torch.optim.Adam(model.policy.parameters(), lr=3e-4)

for epoch in range(max_epochs):
    states, actions, returns = db.sample_batch(batch_size=256)
    logits = model.policy.action_net(model.policy.extract_features(states))
    values = model.policy.value_net(model.policy.extract_features(states))
    loss = cross_entropy(logits, actions) + 0.5 * mse(values, returns)
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()
    if action_agreement > 0.95:
        model.save("data/checkpoints/model_v0_pretrain.zip")
        break
```

### Phase 2: RL self-improvement (PPO)

Switch from supervised learning to PPO. The bot now plays games using its own policy, collects
experience, and updates the network to maximize cumulative reward.

**Training cycle**:
```
loop:
    1. Load latest model checkpoint
    2. Run N games (configurable, default 20) with neural net decision engine
    3. Store transitions in SQLite with shaped rewards
    4. Feed transitions to PPO for policy update
    5. Save new checkpoint
    6. Log metrics (win rate, avg reward, loss curves) to dashboard
    7. If win rate > threshold, increase difficulty
    8. Repeat
```

**Difficulty curriculum**: Start at difficulty 1. When win rate over last 50 games exceeds 80%,
bump difficulty by 1. This prevents the model from being stuck on easy opponents or overwhelmed
by hard ones.

### Checkpoint management

- Checkpoints saved to `data/checkpoints/` as `model_v{N}.zip` (SB3 format)
- Keep last 20 checkpoints, delete older ones (configurable)
- A `data/checkpoints/best.zip` symlink always points to the highest win-rate checkpoint
- Checkpoint metadata in `data/checkpoints/manifest.json`:

```json
{
  "checkpoints": [
    {
      "name": "model_v12",
      "path": "data/checkpoints/model_v12.zip",
      "created_at": "2026-03-30T02:15:00Z",
      "games_played": 240,
      "win_rate": 0.85,
      "difficulty": 3,
      "avg_reward": 7.2,
      "is_best": true
    }
  ]
}
```

---

## Bot Integration

### Decision mode selection

`bot.py` supports three decision modes, selected at launch via CLI flag `--decision-mode`:

| Mode          | Flag value    | Engine                         | Use case                  |
| ------------- | ------------- | ------------------------------ | ------------------------- |
| Rule-based    | `rules`       | `DecisionEngine` (existing)    | Baseline, fallback        |
| Neural net    | `neural`      | `NeuralDecisionEngine`         | Trained model inference   |
| Hybrid        | `hybrid`      | Neural with rule-based fallback| Early training, safety    |

**Hybrid mode**: The neural network proposes an action. If the rule-based engine would choose
`DEFEND` (enemy near base) but the neural net doesn't, the rule-based override wins. This
prevents the model from ignoring existential threats during early training when it hasn't
learned defense yet.

### NeuralDecisionEngine

Drop-in replacement for `DecisionEngine`. Same interface: `evaluate(snapshot, game_step)` →
`StrategicState`. Internally:

1. Convert `GameSnapshot` + extended features → normalized float vector (14 features)
2. Feed through policy network → action probabilities
3. Sample action (training) or take argmax (inference)
4. Return corresponding `StrategicState`
5. Log decision with probabilities for audit trail

```python
class NeuralDecisionEngine:
    def __init__(self, model_path: Path, deterministic: bool = False) -> None: ...
    def evaluate(self, snapshot: GameSnapshot, game_step: int = 0) -> StrategicState: ...
    def get_action_probs(self) -> dict[str, float]: ...  # for dashboard display
```

### Extended GameSnapshot

The existing `GameSnapshot` dataclass is extended with fields needed by the feature encoder:

```python
@dataclass
class GameSnapshot:
    # ... existing fields ...
    gateway_count: int = 0
    robo_count: int = 0
    forge_count: int = 0
    upgrade_count: int = 0
    enemy_structure_count: int = 0
```

These are populated in `bot.py._build_snapshot()` from `self.structures` queries.

---

## Training Orchestrator

### CLI interface

New subcommand added to `runner.py`:

```bash
# Run imitation pre-training (Phase 1)
uv run python -m alpha4gate.runner --train imitation --games 100 --difficulty 1,3,5

# Run RL training loop (Phase 2)
uv run python -m alpha4gate.runner --train rl --cycles 50 --games-per-cycle 20

# Resume RL training from a checkpoint
uv run python -m alpha4gate.runner --train rl --resume data/checkpoints/model_v12.zip

# Play a game with the trained model
uv run python -m alpha4gate.runner --decision-mode neural --model data/checkpoints/best.zip

# Run with hybrid mode (neural + rule-based safety)
uv run python -m alpha4gate.runner --decision-mode hybrid --model data/checkpoints/best.zip
```

### Autonomous training loop

The RL training loop is designed to run for hours unattended:

1. **Auto-save**: Checkpoint after every training cycle (20 games)
2. **Auto-difficulty**: Increase difficulty when win rate > 80% over last 50 games
3. **Crash recovery**: On startup, check for incomplete games in SQLite and resume from
   last completed cycle
4. **Stale SC2 cleanup**: Kill lingering `SC2_x64.exe` processes between games
5. **Progress logging**: Win rate, reward curves, and difficulty level pushed to dashboard
   WebSocket every cycle
6. **Disk guard**: Stop training if `data/training.db` exceeds 200 GB

### Dashboard integration

New training metrics streamed via the existing `/ws/game` WebSocket:

```json
{
  "event": "training_update",
  "cycle": 12,
  "games_played": 240,
  "win_rate_50": 0.82,
  "avg_reward": 7.1,
  "current_difficulty": 3,
  "model_version": "model_v12",
  "loss": {"policy": 0.42, "value": 0.18, "entropy": 0.31}
}
```

New REST endpoints:

| Method | Path                        | Request body                              | Response body                              |
| ------ | --------------------------- | ----------------------------------------- | ------------------------------------------ |
| GET    | `/api/training/status`      | —                                         | `{"state": "running"\|"idle", "cycle": int, "games_played": int, "win_rate_50": float, "current_difficulty": int, "model_version": str, "started_at": str\|null}` |
| GET    | `/api/training/history`     | —                                         | `{"entries": [{"cycle": int, "win_rate_50": float, "avg_reward": float, "difficulty": int, "games_played": int, "timestamp": str}]}` |
| GET    | `/api/training/checkpoints` | —                                         | `{"checkpoints": [CheckpointManifestEntry]}` (same shape as `manifest.json` entries) |
| POST   | `/api/training/start`       | `{"mode": "imitation"\|"rl", "games": int, "games_per_cycle": int, "cycles": int, "difficulty": str, "resume_from": str\|null}` | `{"status": "started", "mode": str}` |
| POST   | `/api/training/stop`        | —                                         | `{"status": "stopping", "will_finish_game": true}` |
| GET    | `/api/reward-rules`         | —                                         | Full `reward_rules.json` content           |
| PUT    | `/api/reward-rules`         | Full `reward_rules.json` content (replaces all rules) | `{"updated": true, "rule_count": int}` |

New React components:

| Component            | Purpose                                              |
| -------------------- | ---------------------------------------------------- |
| `TrainingDashboard`  | Win rate chart, reward curve, difficulty progression  |
| `CheckpointList`     | List checkpoints, load/compare models                |
| `RewardRuleEditor`   | Edit reward_rules.json from the dashboard            |

---

## Modules

### `src/alpha4gate/learning/`

New sub-package containing all deep learning code. Isolated from game code — the only
interface point is `NeuralDecisionEngine` which is imported by `bot.py`.

| File                    | Purpose                                                    |
| ----------------------- | ---------------------------------------------------------- |
| `__init__.py`           | Package init, re-exports key classes                       |
| `features.py`           | `GameSnapshot` → normalized float vector (14 features). `encode(snapshot) -> np.ndarray`, `FEATURE_DIM = 14`. Single source of truth for feature order and normalization constants. |
| `database.py`           | SQLite schema creation, insert/query transitions and games. `TrainingDB` class with `store_transition()`, `store_game()`, `sample_batch()`, `get_recent_win_rate()`. Connection pooling for concurrent reads during training. |
| `rewards.py`            | Reward calculator. Loads `reward_rules.json`, evaluates rules against game state, computes shaped reward per step. `RewardCalculator` class with `compute(state, action, done, won) -> float`. |
| `neural_engine.py`      | `NeuralDecisionEngine` — drop-in replacement for `DecisionEngine`. Loads SB3 model, runs inference, returns `StrategicState`. Supports deterministic (argmax) and stochastic (sample) modes. |
| `imitation.py`          | Imitation pre-training pipeline. Runs rule-based games, collects transitions, trains policy via supervised cross-entropy loss. Produces initial checkpoint. |
| `trainer.py`            | RL training loop. Manages the collect → train → checkpoint cycle. Handles difficulty curriculum, crash recovery, disk guards. The main entry point for `--train rl`. |
| `environment.py`        | Gymnasium-compatible environment wrapper around the SC2 game. `SC2Env(gym.Env)` with `reset()`, `step()`, `observation_space`, `action_space`. This is the bridge between SB3's PPO and burnysc2. |
| `checkpoints.py`        | Checkpoint save/load/prune. Manages `manifest.json`, keeps last N checkpoints, tracks best model by win rate. |
| `hyperparams.py`        | Load/save `hyperparams.json`. Provides PPO kwargs dict for SB3. |

### Changes to existing modules

| File                   | Change                                                    |
| ---------------------- | --------------------------------------------------------- |
| `bot.py`               | Accept `decision_mode` parameter. Use `NeuralDecisionEngine` when mode is `neural` or `hybrid`. Extend `_build_snapshot()` with gateway/robo/forge/upgrade counts. |
| `decision_engine.py`   | Add `GameSnapshot` extended fields (gateway_count, etc.). No logic changes — rule-based engine stays as-is. |
| `runner.py`            | Add `--train`, `--decision-mode`, `--model`, `--resume`, `--cycles`, `--games-per-cycle` CLI args. Route to imitation/RL training or neural game mode. |
| `config.py`            | Add `training_db_path`, `checkpoint_dir`, `reward_rules_path` to `Settings`. |
| `api.py`               | Add `/api/training/*` and `/api/reward-rules` endpoints.  |
| `batch_runner.py`      | Adapt to store results in SQLite as well as stats.json.   |
| `pyproject.toml`       | Add torch, stable-baselines3, numpy, gymnasium to deps.   |

---

## Project Structure (additions)

```
Alpha4Gate/
├── src/
│   └── alpha4gate/
│       ├── learning/                  # NEW — all deep learning code
│       │   ├── __init__.py
│       │   ├── features.py            # State → feature vector encoding
│       │   ├── database.py            # SQLite training data store
│       │   ├── rewards.py             # Reward shaping engine
│       │   ├── neural_engine.py       # NeuralDecisionEngine (inference)
│       │   ├── imitation.py           # Supervised pre-training pipeline
│       │   ├── trainer.py             # RL training loop orchestrator
│       │   ├── environment.py         # Gymnasium env wrapper for SC2
│       │   ├── checkpoints.py         # Checkpoint management
│       │   └── hyperparams.py         # PPO hyperparameter loading
│       ├── bot.py                     # MODIFIED — decision mode support
│       ├── decision_engine.py         # MODIFIED — extended GameSnapshot
│       ├── runner.py                  # MODIFIED — training CLI
│       ├── config.py                  # MODIFIED — training settings
│       ├── api.py                     # MODIFIED — training endpoints
│       └── ...                        # existing files unchanged
├── frontend/
│   └── src/
│       └── components/
│           ├── TrainingDashboard.tsx   # NEW — training metrics charts
│           ├── CheckpointList.tsx      # NEW — model checkpoint browser
│           └── RewardRuleEditor.tsx    # NEW — reward rule editor
├── tests/
│   ├── test_features.py               # NEW — feature encoding
│   ├── test_database.py               # NEW — SQLite operations
│   ├── test_rewards.py                # NEW — reward calculation
│   ├── test_neural_engine.py          # NEW — inference, mode selection
│   ├── test_imitation.py              # NEW — pre-training pipeline
│   ├── test_trainer.py                # NEW — training loop logic
│   ├── test_environment.py            # NEW — gym env wrapper
│   ├── test_checkpoints.py            # NEW — checkpoint management
│   └── ...                            # existing tests unchanged
├── data/
│   ├── training.db                    # SQLite training database — gitignored
│   ├── reward_rules.json              # Reward shaping rules — tracked in git
│   ├── hyperparams.json               # PPO hyperparameters — tracked in git
│   └── checkpoints/                   # Model checkpoints — gitignored
│       ├── manifest.json
│       ├── model_v0_pretrain.zip
│       ├── model_v1.zip
│       └── best.zip → model_v12.zip
└── ...
```

---

## Key Design Decisions

**PPO throughout (no DQN hybrid)** — PPO handles both the imitation pre-training phase
(via behavior cloning loss) and the RL phase. Using a single algorithm avoids maintaining
two model architectures and simplifies the codebase. PPO is stable, well-understood, and
works well for discrete action spaces.

**Structured features, not raw pixels** — The 14-feature vector captures the strategically
relevant game state (resources, army, economy, enemy presence) without the computational
cost of processing minimap images. This makes training tractable on CPU and keeps the model
small enough to run inference every game step without lag. Spatial features (minimap, unit
positions) are explicitly out of scope for v1.

**Gymnasium environment wrapper** — SB3 expects a standard `gym.Env` interface. Wrapping the
SC2 game in a Gymnasium environment lets us use SB3's PPO implementation directly without
custom training code. The wrapper handles game launch/teardown, observation encoding, action
decoding, and reward computation.

**Reward shaping via JSON config** — Domain knowledge injection (e.g., "defend against cannon
rushes") is expressed as declarative rules in `reward_rules.json` rather than hard-coded in
Python. This lets the user add, modify, or disable reward signals between training runs without
touching code. The reward calculator evaluates all active rules each step and sums the bonuses.

**Imitation pre-training before RL** — Pure RL from scratch would spend hundreds of games
learning behaviors the rule-based engine already knows (build probes, expand, attack when
strong). Pre-training on the rule-based bot's successful games gives the neural network a
warm start at the current bot's skill level. RL then improves beyond that baseline.

**Hybrid decision mode** — During early RL training, the model may make catastrophic decisions
(ignoring an attack on its base). Hybrid mode lets the rule-based engine override the neural
net for existential threats (DEFEND when enemy is near base) while the model learns everything
else. This safety net is gradually unnecessary as the model improves.

**SQLite over JSONL for training data** — The existing JSONL game logs work for human-readable
debugging but are too slow for random-access batch sampling during training. SQLite provides
indexed queries, atomic writes, and efficient random sampling via `ORDER BY RANDOM() LIMIT N`.
The JSONL logs continue to work for their original purpose (dashboard, replay analysis).

**Decision frequency: every 22 game steps** — Matches the existing observation frequency in
`bot.py`. The strategic state doesn't need to change faster than once per second. This keeps
the transition count manageable (~600 per 10-minute game) and avoids flooding the network
with redundant observations.

---

## Out of Scope (v1 — Deep Learning)

| Item                              | Why deferred                                      |
| --------------------------------- | ------------------------------------------------- |
| MicroController neural net        | Much larger action space (per-unit commands), needs spatial features, significantly more complex — planned for v2 |
| Raw spatial / minimap features    | Requires CNN, much larger network, GPU needed      |
| Multi-agent self-play             | Single bot vs built-in AI is sufficient for v1     |
| Distributed / multi-GPU training  | Single machine is tractable for this action space  |
| Non-Protoss races                 | Out of scope for entire project                    |
| Online learning during live games | Train offline in batches, deploy frozen checkpoints |

---

## Open Questions / Risks

| Item                           | Risk                                              | Mitigation                                                |
| ------------------------------ | ------------------------------------------------- | --------------------------------------------------------- |
| SC2 game launch reliability    | SC2 client can hang or crash during long batch runs | Kill stale SC2_x64.exe between games; timeout per game (15 min max); log and skip failed games |
| Reward shaping tuning          | Poorly tuned rewards can cause degenerate behavior (e.g., model learns to avoid combat entirely to avoid losing units) | Start with minimal shaping (win/loss + survival only), add rules incrementally, monitor via dashboard |
| Imitation ceiling              | Pre-trained model may be hard to improve beyond rule-based level if RL signal is weak | Track divergence from rule-based policy — if model converges back to imitation, increase entropy bonus or adjust rewards |
| Training instability           | PPO can be sensitive to hyperparameters with small batches | Use SB3 defaults (well-tuned), save checkpoints frequently, roll back to last good checkpoint on win rate collapse |
| Gymnasium wrapper complexity   | Wrapping an async burnysc2 game in a synchronous gym.Env interface requires careful event loop management | Use `asyncio.run()` inside `step()` and `reset()`, one game per episode, fresh SC2 client per reset |
| Disk usage over long runs      | SQLite DB could grow large over thousands of games | 200 GB cap enforced by trainer; prune old transitions after model has trained on them; VACUUM periodically |
| Feature sufficiency             | 14 features may not capture enough game state for good decisions at higher difficulties | Monitor where the model makes bad decisions; add features incrementally (e.g., tech tree progress, map control) |
| CPU training speed             | PPO on CPU may be slow for large batches           | 14-feature MLP is tiny — CPU training is fast. Only a concern if we add spatial features (v2) |

---

## How to Run

### Prerequisites (in addition to Phase 1 prerequisites)

- PyTorch (CPU version is fine): `pip install torch --index-url https://download.pytorch.org/whl/cpu`
- Or install everything via uv: `uv sync` (after deps are added to pyproject.toml)

### Imitation pre-training

```bash
cd .

# Step 1: Collect training data from rule-based bot (100 games across difficulties)
uv run python -m alpha4gate.runner --train imitation --games 100 --difficulty 1,3,5

# This will:
# - Run 100 games with rule-based engine
# - Store all transitions in data/training.db
# - Train neural net to mimic rule-based decisions
# - Save checkpoint to data/checkpoints/model_v0_pretrain.zip
# - Report final action agreement percentage
```

### RL training (runs for hours)

```bash
# Start RL training from pre-trained checkpoint
uv run python -m alpha4gate.runner --train rl --cycles 100 --games-per-cycle 20

# Monitor in dashboard (separate terminal):
uv run python -m alpha4gate.runner --serve
cd frontend && npm start
# Open http://localhost:3000 → Training Dashboard tab
```

### Play with trained model

```bash
# Single game with best model
uv run python -m alpha4gate.runner --decision-mode neural --model data/checkpoints/best.zip

# Hybrid mode (neural + rule-based safety)
uv run python -m alpha4gate.runner --decision-mode hybrid --model data/checkpoints/best.zip

# Batch evaluation of trained model
uv run python -m alpha4gate.runner --batch 20 --decision-mode neural --model data/checkpoints/best.zip --difficulty 5
```

### Tests

```bash
# All unit tests (no SC2 needed)
uv run pytest

# Just deep learning tests
uv run pytest tests/test_features.py tests/test_database.py tests/test_rewards.py tests/test_neural_engine.py tests/test_environment.py tests/test_trainer.py tests/test_checkpoints.py

# Lint and typecheck
uv run ruff check .
uv run mypy src
```

---

## Development Process

All steps use full RWL (Reviewer-Writer Loop) with `/rwl-direct`. Each step is a separate
GitHub issue. The reviewer gates on `pytest`/`ruff`/`mypy` before approving.

Steps are designed to run autonomously for long stretches. Each step produces a testable,
passing result before the next begins.

### Step 1 — Feature encoding and SQLite database

- Create `src/alpha4gate/learning/__init__.py`
- Implement `features.py`: `encode(snapshot) -> np.ndarray`, `FEATURE_DIM`, normalization
  constants, `decode()` for debugging
- Implement `database.py`: `TrainingDB` class — create schema, `store_transition()`,
  `store_game()`, `sample_batch(n) -> tuple[np.ndarray, np.ndarray, np.ndarray]`,
  `get_recent_win_rate(n_games)`, `get_game_count()`, `get_db_size_bytes()`
- Extend `GameSnapshot` in `decision_engine.py` with `gateway_count`, `robo_count`,
  `forge_count`, `upgrade_count`, `enemy_structure_count`
- Update `bot.py._build_snapshot()` to populate extended fields
- Write `tests/test_features.py`: encode/decode round-trip, normalization bounds, edge cases
- Write `tests/test_database.py`: store/retrieve transitions, batch sampling, win rate query
- Add `torch`, `numpy`, `stable-baselines3`, `gymnasium` to `pyproject.toml` dependencies
- **Done when**: `uv run pytest -k "features or database"` passes, existing 205 tests still pass

### Step 2 — Reward shaping engine

- Implement `rewards.py`: `RewardCalculator` class — load `reward_rules.json`, evaluate rules
  against game state dict, compute total reward per step, support all operators
- Create default `data/reward_rules.json` with 3 starter rules (scout-early, defend-rush,
  no-supply-block)
- Implement derived field computation (has_scouted, enemy_structure_near_base_early)
- Write `tests/test_rewards.py`: rule evaluation, operator testing, active/inactive toggle,
  edge cases (empty rules, missing fields), base reward (win/loss/survival)
- **Done when**: `uv run pytest -k rewards` passes

### Step 3 — Neural decision engine

- Implement `neural_engine.py`: `NeuralDecisionEngine` class — load SB3 model, encode
  snapshot, run inference, return `StrategicState`, log action probabilities
- Implement `hyperparams.py`: load/save `hyperparams.json`, provide PPO kwargs
- Create default `data/hyperparams.json` with starting values
- Modify `bot.py` to accept `decision_mode` parameter and use `NeuralDecisionEngine` when
  mode is `neural` or `hybrid`
- Implement hybrid override logic (rule-based DEFEND overrides neural net)
- Write `tests/test_neural_engine.py`: inference with mock model, deterministic vs stochastic,
  hybrid override, action probability logging
- **Done when**: `uv run pytest -k neural_engine` passes, bot runs with `--decision-mode rules`
  (existing behavior unchanged)

### Step 4 — Gymnasium environment wrapper

- Implement `environment.py`: `SC2Env(gymnasium.Env)` — `reset()` launches a new SC2 game,
  `step(action)` runs 22 game steps and returns (obs, reward, done, truncated, info),
  `close()` kills SC2 client
- Handle async-to-sync bridge (burnysc2 is async, gym.Env is sync)
- Integrate `RewardCalculator` for reward computation
- Integrate `TrainingDB` for transition storage
- Write `tests/test_environment.py`: mock SC2 game, verify obs shape, action application,
  reward computation, episode termination
- **Done when**: `uv run pytest -k environment` passes

### Step 5 — Imitation pre-training pipeline

- Implement `imitation.py`: run N games with rule-based engine collecting transitions,
  train SB3 PPO model using behavior cloning (cross-entropy on action labels),
  save checkpoint when action agreement > 95%
- Implement `checkpoints.py`: save/load/prune checkpoints, maintain `manifest.json`,
  track best model
- Add `--train imitation` CLI path in `runner.py`
- Write `tests/test_imitation.py`: mock game data, verify training reduces loss, checkpoint
  saved correctly
- Write `tests/test_checkpoints.py`: save/load/prune logic, manifest updates, best tracking
- **Done when**: `uv run pytest -k "imitation or checkpoints"` passes, `--train imitation`
  runs end-to-end with a real SC2 game (manual verification)

### Step 6 — RL training loop

- Implement `trainer.py`: `TrainingOrchestrator` class — manages collect → train → checkpoint
  cycle, difficulty curriculum (auto-increase at 80% win rate), crash recovery (resume from
  last complete cycle), disk guard (stop at 200 GB), SC2 process cleanup between games
- Add `--train rl`, `--cycles`, `--games-per-cycle`, `--resume` CLI paths in `runner.py`
- Write `tests/test_trainer.py`: curriculum logic, crash recovery, disk guard, cycle counting
- **Done when**: `uv run pytest -k trainer` passes, `--train rl --cycles 2 --games-per-cycle 2`
  runs end-to-end (manual verification with SC2)

### Step 7 — Dashboard integration

- Add `/api/training/status`, `/api/training/history`, `/api/training/checkpoints`,
  `/api/training/start`, `/api/training/stop` endpoints to `api.py`
- Add `/api/reward-rules` GET/PUT endpoints to `api.py`
- Stream `training_update` events via existing `/ws/game` WebSocket
- Implement `TrainingDashboard.tsx`: win rate chart (line), reward curve (line), difficulty
  progression (step), current status
- Implement `CheckpointList.tsx`: list checkpoints, show metadata, highlight best
- Implement `RewardRuleEditor.tsx`: list rules, toggle active/inactive, edit rewards,
  add/remove rules
- Update `App.tsx` routing to include Training tab
- Write/update `tests/test_api.py` for new endpoints
- **Done when**: Dashboard shows training metrics, reward rules are editable from UI,
  all tests pass

### Step 8 — End-to-end validation and tuning

- Run full pipeline: imitation pre-training (50 games) → RL training (10 cycles × 10 games)
- Verify trained model beats Easy AI with >90% win rate
- Verify model outperforms or matches rule-based engine at difficulty 3
- Tune reward rules based on observed weaknesses
- Document any hyperparameter changes in `hyperparams.json`
- Update `docs/deep-learning-plan.md` with results and lessons learned
- **Done when**: Trained model wins >90% vs Easy AI, dashboard shows training progression,
  all 205+ tests pass, ruff clean, mypy clean

---

## Appendix

### Gymnasium environment interface

The `SC2Env` wrapper translates between SB3's synchronous gym interface and burnysc2's
async game loop:

```python
class SC2Env(gymnasium.Env):
    observation_space = gymnasium.spaces.Box(low=0.0, high=1.0, shape=(14,), dtype=np.float32)
    action_space = gymnasium.spaces.Discrete(5)

    def reset(self, seed=None, options=None):
        # Kill any stale SC2 process
        # Launch new SC2 game with burnysc2
        # Run until first observation
        # Return (obs, info)

    def step(self, action):
        # Set bot's strategic state to action
        # Run 22 game steps
        # Compute reward from RewardCalculator
        # Encode new state as obs
        # Check if game ended (done)
        # Store transition in TrainingDB
        # Return (obs, reward, terminated, truncated, info)

    def close(self):
        # Kill SC2 client
```

The async-to-sync bridge uses `asyncio.run()` for each `reset()` and manages the game loop
via a custom callback that yields control back to `step()` every 22 game steps. This is the
trickiest implementation detail — see `environment.py` for the full approach.

### PPO training with SB3

```python
from stable_baselines3 import PPO

env = SC2Env(map_name="Simple64", difficulty=1, reward_calculator=rc, db=db)
model = PPO("MlpPolicy", env, **hyperparams)

# Imitation pre-training (behavior cloning phase)
model.learn(total_timesteps=50000)  # supervised on rule-based data

# RL training
model.learn(total_timesteps=200000)  # self-improvement via PPO
model.save("data/checkpoints/model_v1.zip")
```

### Reward shaping example: defending cannon rushes

If the bot loses to cannon rushes and doesn't learn the defense on its own, add this rule
to `reward_rules.json`:

```json
{
  "id": "cannon-rush-defense",
  "description": "Large reward for having army units near base when enemy buildings appear early",
  "condition": {
    "field": "enemy_structure_near_base_early",
    "op": "==",
    "value": true
  },
  "requires": {
    "field": "army_supply",
    "op": ">=",
    "value": 4
  },
  "reward": 0.5,
  "active": true
}
```

The model will learn that having army near base when enemy structures appear early is
heavily rewarded, and will start keeping units at home in those situations — without
being told *how* to defend (which units, where to position).
