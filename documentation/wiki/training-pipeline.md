# Training Pipeline

How the bot learns from experience.

> **At a glance:** Two training modes — imitation pre-training (behavior cloning from
> rule-based play) and RL training (PPO via Stable Baselines 3). Both are CLI-triggered,
> synchronous, and single-machine. Curriculum auto-advances difficulty when win rate
> hits 80%. Checkpoints managed via manifest with best-tracking and pruning. The SC2 game
> runs in a background thread bridged to a gymnasium.Env interface via queues.

## Purpose & Design

The training pipeline turns game experience into a better policy. It has two stages:

| Stage | What it does | When to use |
|-------|-------------|-------------|
| **Imitation pre-training** | Clone the rule-based bot's decisions via supervised learning | Once, before RL, to give PPO a sensible starting policy |
| **RL training** | PPO gradient updates on actual game outcomes | Repeatedly, to improve beyond rule-based play |

Both stages produce SB3 checkpoint files (`.zip`) stored in `data/checkpoints/` with
a `manifest.json` tracking versions and the current "best" model.

### Training flow overview

```
                    ┌──────────────────────────────────┐
                    │         CLI Entry Point           │
                    │  runner.py --train {imitation|rl} │
                    └──────────────┬───────────────────┘
                                   │
              ┌────────────────────┴────────────────────┐
              ▼                                         ▼
   ┌─────────────────────┐               ┌──────────────────────────┐
   │  Imitation Training  │               │    RL Training Loop       │
   │                      │               │                          │
   │  DB transitions      │               │  For each cycle:         │
   │  → PyTorch CE loss   │               │    1. Create SC2Env      │
   │  → v0_pretrain.zip   │               │    2. model.learn()      │
   │                      │               │    3. Check win rate     │
   │  Stop: agreement     │               │    4. Curriculum advance │
   │  >= 95% or 100 epochs│               │    5. Save checkpoint    │
   └─────────────────────┘               │    6. Prune old CPs      │
                                          └──────────────────────────┘
```

### Gaps

> These feed directly into [Phase 3 of the always-up plan](../plans/always-up-plan.md).

- **No daemon or scheduler.** Training only runs when a human types `--train rl`.
  There's no background process, no cron, no event-triggered training.
- **No model promotion.** Checkpoints are saved with a "best" flag, but there's no
  automated "evaluate new checkpoint against old, promote if better" flow.
- **No parallel evaluation.** Training and evaluation share the same process. You can't
  benchmark a checkpoint while another training cycle runs.
- **No distributed training.** Single machine, single GPU, serial cycles.
- **Single environment per cycle.** SB3's `model.learn()` runs one SC2 game at a time
  (no vectorized environments).

---

## Key Interfaces

> **At a glance:** `TrainingOrchestrator.run()` is the main entry point for RL.
> `run_imitation_training()` is a standalone function. Both write checkpoints.
> `SC2Env` bridges gymnasium and burnysc2 via thread queues.
> `NeuralDecisionEngine` loads checkpoints for inference during gameplay.

### RL training entry point

`TrainingOrchestrator.run(n_cycles, games_per_cycle, resume)` → `dict`

```
For each cycle (1 to n_cycles):
  1. SC2Env created with current difficulty
  2. model.learn(total_timesteps = games_per_cycle * 15)
     └─ SB3 internally calls env.reset() and env.step() repeatedly
     └─ Each step: 22 game ticks → snapshot → encode → reward → transition stored
  3. Win rate queried: db.get_recent_win_rate(games_per_cycle * 2)
  4. If win_rate >= 0.8 and difficulty < max: difficulty += 1
  5. Diagnostics logged (action probabilities on test states)
  6. Checkpoint saved with metadata {cycle, difficulty, win_rate}
  7. Old checkpoints pruned (keep 5 + best)
```

**Return value:**
```python
{
    "cycles_completed": int,
    "total_games": int,
    "final_difficulty": int,
    "stopped": bool,           # True if disk guard triggered
    "stop_reason": str,
    "cycle_results": [
        {"cycle": 1, "difficulty": 1, "win_rate": 0.65, "checkpoint": "v1"},
        ...
    ]
}
```

**Timestep estimation:** `games_per_cycle * 15` — approximately 15 decisions per game
(900s max game time / 22 steps per action ~= 13.6, rounded up). This is passed to
`model.learn(total_timesteps=...)`.

### Imitation pre-training

`run_imitation_training(db, checkpoint_dir, ...)` → `dict`

```
1. Load all transitions from DB: db.sample_batch(total_count)
2. Normalize state vectors to [0, 1] using feature spec divisors
3. Create dummy gymnasium env + fresh PPO model (MlpPolicy, [128, 128])
4. Custom PyTorch loop (NOT SB3's learn()):
   For epoch in range(max_epochs):
     For batch in minibatches:
       logits = policy(states)
       loss = cross_entropy(logits, actions)
       optimizer.step()
     agreement = (argmax(logits) == actions).mean()
     If agreement >= 0.95: break
5. Save as "v0_pretrain" checkpoint (marked as best)
```

**Return value:**
```python
{
    "epochs": int,
    "final_loss": float,
    "agreement": float,      # e.g. 0.9523
    "transitions": int,
    "saved_path": str
}
```

**Integration with RL:** When `TrainingOrchestrator.run(resume=True)` is called, it loads
the best checkpoint (which is `v0_pretrain` after imitation). PPO fine-tunes from those
pre-trained weights with `reset_num_timesteps=False` to keep the cumulative step counter.

### SC2Env: the gymnasium bridge

`SC2Env` wraps a full SC2 game as a `gymnasium.Env[NDArray[float32], int]`.

**Spaces:**
- Observation: `Box(0.0, 1.0, shape=(17,), dtype=float32)`
- Action: `Discrete(6)` — maps to `[OPENING, EXPAND, ATTACK, DEFEND, LATE_GAME, FORTIFY]`

**Threading model:**

```
Main Thread (SB3/Trainer)              Background Thread (SC2 Game)
  │                                       │
  ├─ env.reset()                          │
  │   └─ Thread.start() ──────────────>   ├─ _run_game_thread()
  │                                       │   └─ _sync_game()
  │                                       │       └─ sc2.main.run_game(...)
  │   obs ◄── _obs_queue ◄───────────    │       └─ _FullTrainingBot.on_step()
  │                                       │            every 22 game steps:
  ├─ env.step(action)                     │            put obs → _obs_queue
  │   └─ _action_queue ──────────────>    │            get action ◄─ _action_queue
  │   obs ◄── _obs_queue ◄───────────    │            inject state into bot
  │                                       │            call super().on_step()
  ├─ ... repeat ...                       │
  │                                       │
  └─ env.close()                          │
      └─ _action_queue.put(None) ─────>   └─ shutdown
         Thread.join(timeout=30)
```

**Queue timeouts:**
| Operation | Timeout | On timeout |
|-----------|---------|------------|
| `reset()` waiting for first obs | 300s | Propagates `queue.Empty` |
| `step()` waiting for next obs | 300s | Returns obs=zeros, reward=-10.0, done=True |
| `on_step()` waiting for action | 120s | Returns (triggers shutdown) |
| `close()` joining thread | 30s | Continues anyway, sets thread=None |

**Signal handling workaround:** burnysc2 registers signal handlers that only work in the
main thread. Since the game runs in a background thread, `_run_game_thread()` monkey-patches
`signal.signal` to a no-op before launching, then restores it afterward.

### Inference path (using the trained model)

During actual gameplay (not training), the model is loaded via `NeuralDecisionEngine`:

```
CLI: --decision-mode neural --model-path checkpoints/v5.zip
  │
  └─ Alpha4GateBot.__init__()
       └─ NeuralDecisionEngine(model_path, mode=NEURAL)
            └─ PPO.load(path)  # SB3 load
  
Each game step:
  snapshot = bot._build_snapshot()      # GameSnapshot dataclass
  state = neural_engine.predict(snapshot)
    1. encode(snapshot) → 17-dim float32 vector
    2. model.predict(obs, deterministic=True) → action index
    3. Extract probability distribution → self._last_probabilities
    4. _ACTION_TO_STATE[action] → StrategicState
  bot uses state for all macro/micro/command decisions
```

**Decision modes:**
| Mode | Behavior |
|------|----------|
| `RULES` | Rule-based `DecisionEngine` only; no model loaded |
| `NEURAL` | `NeuralDecisionEngine` exclusively; model required |
| `HYBRID` | `NeuralDecisionEngine` with one override: if `enemy_army_near_base`, force `DEFEND` |

### Curriculum system

Automatic difficulty scaling within a training run:

```
Initial difficulty: 1 (configurable)
Max difficulty: 10
Threshold: win_rate >= 0.8
Window: last (games_per_cycle * 2) games

After each cycle:
  win_rate = db.get_recent_win_rate(games_per_cycle * 2)
  if should_increase_difficulty(win_rate):
      difficulty += 1
```

Difficulty maps to SC2's built-in AI levels (1=Easy through 10=CheatInsane).

### Disk guard

Training stops if `data/training.db` exceeds 200 GB:

```python
def check_disk_guard(self) -> bool:
    size = self._db.get_db_size_bytes()
    if size > self._disk_limit_gb * 1e9:
        self._stopped = True
        self._stop_reason = f"DB size {size} exceeds limit"
        return False
    return True
```

---

## Implementation Notes

> **At a glance:** Checkpoints are SB3 `.zip` files tracked by `manifest.json`. PPO uses
> MlpPolicy with `[128, 128]` hidden layers. 6 actions map to strategic states. Training
> errors are caught per-cycle (log and continue). Imitation uses raw PyTorch, not SB3's
> learn().

> Verify against code before relying on exact signatures — implementations change with
> refactors.

### Checkpoint system

**Directory:** `data/checkpoints/`

**Files:**
```
data/checkpoints/
├── manifest.json       # Index: list of checkpoints + best name
├── v0_pretrain.zip     # Imitation pre-training
├── v1.zip              # RL cycle 1
├── v2.zip              # RL cycle 2
└── ...                 # Keep 5 most recent + best
```

**manifest.json structure:**
```json
{
  "checkpoints": [
    {
      "name": "v0_pretrain",
      "file": "v0_pretrain.zip",
      "metadata": {
        "type": "imitation",
        "epochs": 42,
        "final_loss": 0.0234,
        "agreement": 0.9523,
        "transitions": 10234
      }
    },
    {
      "name": "v1",
      "file": "v1.zip",
      "metadata": {
        "cycle": 1,
        "difficulty": 1,
        "total_games": 10,
        "win_rate": 0.65
      }
    }
  ],
  "best": "v2"
}
```

**Checkpoint functions** (`learning/checkpoints.py`):
```python
save_checkpoint(model, checkpoint_dir, name, metadata=None, is_best=False) -> Path
load_checkpoint(checkpoint_dir, name=None) -> PPO  # name=None loads best
get_best_name(checkpoint_dir) -> str | None
list_checkpoints(checkpoint_dir) -> list[dict]
prune_checkpoints(checkpoint_dir, keep=5) -> list[str]  # returns removed names
```

Pruning keeps the last `keep` entries plus the best checkpoint. Removes `.zip` files
from disk and updates the manifest.

### PPO hyperparameters

**Default values** (`data/hyperparams.json`):
```json
{
  "learning_rate": 3e-4,
  "n_steps": 64,
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

`to_ppo_kwargs()` converts this to SB3-compatible kwargs, extracting `net_arch` into
`policy_kwargs={"net_arch": [128, 128]}` and passing through all other known PPO fields.
Unknown keys are logged as warnings.

### Action space

6 strategic states, mapped by index:

| Index | StrategicState | Description |
|-------|---------------|-------------|
| 0 | OPENING | Early build, scouts |
| 1 | EXPAND | Take expansion, build production |
| 2 | ATTACK | Push with army |
| 3 | DEFEND | Defend main base |
| 4 | LATE_GAME | Macro for late game |
| 5 | FORTIFY | Build static defense |

**Note:** The gymnasium env declares `Discrete(6)` but imitation training creates
`Discrete(5)` (excluding FORTIFY). This mismatch exists in the codebase — FORTIFY was
added after imitation training was implemented.

### Diagnostics

After each RL cycle, `_log_diagnostics()` evaluates the model on predefined test states:

**Input:** `data/diagnostic_states.json` — hand-crafted scenarios:
```json
[
  {"name": "Early game minerals low", "features": [30, 50, 100, 0, 10, 12, 1, 0, 0, 30, 1, 0, 0, 0, 5, 0, 0]},
  ...
]
```

**Output:** `data/training_diagnostics.json` — per-cycle action distributions:
```json
[
  {
    "cycle": 1,
    "win_rate": 0.65,
    "states": [
      {"name": "Early game minerals low", "action": 2, "probs": [0.15, 0.23, 0.42, 0.12, 0.07]}
    ]
  }
]
```

This is the only way to see how the model's behavior changes across cycles. It's
written to disk (persistent) but not surfaced in the dashboard.

### Error handling

| Error | Where | Recovery |
|-------|-------|----------|
| Exception in `model.learn()` | trainer.py | Log, increment game count by 1, continue to next cycle |
| Disk limit exceeded | trainer.py | Set `stopped=True`, break loop, return summary |
| No checkpoint to resume | trainer.py | `get_best_name()` returns None → create fresh model |
| Diagnostic states file missing | trainer.py | Check `.exists()`, skip diagnostics |
| Game thread crash | environment.py | Catch exception, put terminal obs (loss), return |
| Queue timeout (obs) | environment.py | Return obs=zeros, reward=-10.0, done=True |
| Queue timeout (action) | environment.py | Return from on_step (triggers shutdown) |
| No transitions for imitation | imitation.py | Raise `ValueError` |

### Key constants

| Constant | Value | Location |
|----------|-------|----------|
| `STEPS_PER_ACTION` | 22 game ticks | environment.py |
| `MAX_GAME_TIME_SECONDS` | 900.0 (15 min) | environment.py |
| `DEFAULT_DISK_LIMIT_GB` | 200.0 | trainer.py |
| `FEATURE_DIM` | 17 | features.py |
| Default `win_rate_threshold` | 0.8 | trainer.py |
| Default `max_epochs` (imitation) | 100 | imitation.py |
| Default `agreement_threshold` | 0.95 | imitation.py |

### Key file locations

| File | Purpose |
|------|---------|
| `src/alpha4gate/learning/trainer.py` | TrainingOrchestrator — cycle loop, curriculum, diagnostics |
| `src/alpha4gate/learning/environment.py` | SC2Env — gymnasium wrapper, threading, queue bridge |
| `src/alpha4gate/learning/imitation.py` | run_imitation_training — behavior cloning |
| `src/alpha4gate/learning/neural_engine.py` | NeuralDecisionEngine — inference, hybrid override |
| `src/alpha4gate/learning/checkpoints.py` | save/load/prune/list checkpoints, manifest management |
| `src/alpha4gate/learning/hyperparams.py` | Load/convert PPO hyperparameters |
| `src/alpha4gate/runner.py` | CLI entry point — `--train`, `--decision-mode`, `--model-path` |
| `data/checkpoints/` | Checkpoint files + manifest.json |
| `data/hyperparams.json` | PPO hyperparameter defaults |
| `data/diagnostic_states.json` | Hand-crafted test states for diagnostics |
| `data/training_diagnostics.json` | Per-cycle action distributions on test states |
