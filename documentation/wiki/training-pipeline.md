# Training Pipeline

How the bot learns from experience.

> **At a glance:** The inner self-improvement loop. `TrainingDaemon` triggers RL cycles in the background (no human input). Each cycle: `TrainingOrchestrator` runs PPO via Stable Baselines 3, then `PromotionManager` gates the new checkpoint and `RollbackMonitor` watches for regressions. Four PPO variants are dispatched by hyperparams: `PPO`, `PPOWithKL` (KL regularized to the rule-based policy), `RecurrentPPO` (LSTM memory), and `RecurrentPPOWithKL`. Imitation pre-training provides a warm-start via the `use_imitation_init` hyperparam. Curriculum auto-advances difficulty when win rate hits 80%. Checkpoints managed via manifest with best-tracking and pruning. The SC2 game runs in a background thread bridged to a gymnasium.Env interface via queues.

This doc covers the **inner loop** of the autonomous system. For how it plugs into `/improve-bot-advised`'s outer loop (the TRAIN phase), see [improve-bot-advised-architecture.md](improve-bot-advised-architecture.md). For the promotion/rollback gate specifically, see [promotions.md](promotions.md).

## Purpose & Design

The training pipeline turns game experience into a better policy. Two stages, plus
the autonomous daemon that drives them:

| Stage | What it does | When to use |
|-------|-------------|-------------|
| **Imitation pre-training** | Clone the rule-based bot's decisions via supervised learning | Once, before RL, to give PPO a sensible starting policy |
| **RL training** | PPO gradient updates on actual game outcomes | Repeatedly, to improve beyond rule-based play |
| **Autonomous daemon** | Trigger RL cycles in the background based on transitions + time | Always-on; enables the outer loop's TRAIN phase |

All stages produce SB3 checkpoint files (`.zip`) stored in `data/checkpoints/` with
a `manifest.json` tracking versions and the current "best" model.

### Training flow overview

```
              ┌────────────────────────────────────────────────────┐
              │  TrainingDaemon (background thread)                │
              │                                                    │
              │   Every check_interval (default 60s):              │
              │     transitions-since-last >= min_transitions ?    │
              │     OR hours-since-last >= min_hours_since_last ?  │
              │     → fire training run                            │
              └─────────────────────┬──────────────────────────────┘
                                    │
                                    ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │  TrainingOrchestrator.run(cycles_per_run, games_per_cycle)       │
 │                                                                  │
 │   For each cycle (default 5 per run):                            │
 │     1. Create SC2Env with current difficulty                     │
 │     2. _init_or_resume_model() — dispatch PPO variant            │
 │        - PPO / PPOWithKL / RecurrentPPO / RecurrentPPOWithKL     │
 │        - Load v0_pretrain if use_imitation_init=true             │
 │     3. model.learn(total_timesteps = games_per_cycle * 15)       │
 │     4. Check win rate; curriculum-advance if >= 0.8              │
 │     5. Save checkpoint; prune old ones                           │
 │     6. Log diagnostics (action distribution on test states)      │
 └─────────────────────┬────────────────────────────────────────────┘
                       │
                       ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │  PromotionManager.evaluate_and_promote()                         │
 │   Eval new checkpoint + current best, compare, promote or reject │
 │   (see promotions.md for the full gate)                          │
 └─────────────────────┬────────────────────────────────────────────┘
                       │
                       ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │  RollbackMonitor.check_for_regression()                          │
 │   If current WR < promotion WR by regression_threshold → revert  │
 └──────────────────────────────────────────────────────────────────┘
```

### What's changed since this doc was originally written

- **The daemon exists.** `TrainingDaemon` triggers RL cycles autonomously; no human `--train rl` needed.
- **Promotion + rollback exist.** `PromotionManager` and `RollbackMonitor` gate every new checkpoint.
- **Four PPO variants** are dispatched by hyperparams: `PPO`, `PPOWithKL`, `RecurrentPPO`, `RecurrentPPOWithKL`.
- **Imitation-init path**: setting `use_imitation_init=true` in hyperparams warm-starts from `v0_pretrain.zip` (AlphaStar-style supervised init).
- **Still single-machine, single-GPU, serial cycles.** No vectorized envs yet.

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

**Cycle ≠ game count (Phase 4.7 Step 5, #86):** the `games_per_cycle * 15`
calculation above is an **upper bound**, not a target. Under `realtime=False`
(the default for training), a single SC2 game typically consumes the full PPO
timestep budget before the game actually finishes, so one cycle runs closer to
"1 game" than "`games_per_cycle` games". The trainer per-cycle log line is
framed as `Training cycle K: PPO.learn(total_timesteps=N)` — NOT
`Training: N games` — so operators watching logs do not sit waiting for
`games_per_cycle` sc2 game completions that will not arrive. The
`games_per_cycle` configuration field is retained because the win-rate window
downstream (`db.get_recent_win_rate(games_per_cycle * 2)`) still uses it as its
window size; it just does not correspond to a per-cycle game count under
`realtime=False`.

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
- Observation: `Box(0.0, 1.0, shape=(24,), dtype=float32)` — 17 base game features + 7 advisor features
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

**Normal-end path is fast (Phase 4.7 Step 3 / #84).** The 300s `step()`
timeout above is a fallback for genuine hangs. On the normal
end-of-game path — where sc2 transitions to `Status.ended` on its own
and `_sync_game` returns cleanly — `_run_game_thread`'s `finally`
block pushes an unconditional terminal sentinel
`(zeros, {}, True, None)` onto `_obs_queue`, so the next `step()` call
returns with `done=True` within milliseconds of game-end instead of
stalling the full 300s. The sentinel uses `result=None` (not
`"loss"`) so `RewardCalculator.compute_step_reward(is_terminal=True,
result=None)` cleanly skips the terminal-bonus branch and does not
poison the total reward. This path is DISTINCT from the
`_FullTrainingBot._resign_and_mark_done` helper (Phase 4.5 #72) which
covers early termination (bot voluntarily leaves the game); Step 3
covers the normal case where sc2 ends the game on its own and the
bot's `on_end` hook does not push a terminal tuple.

**Signal handling workaround:** burnysc2 registers signal handlers that only work in the
main thread. Since the game runs in a background thread, `_run_game_thread()` monkey-patches
`signal.signal` to a no-op before launching, then restores it afterward.

**Episode teardown contract (#72 blocker):**
Every exit from an episode — whether the bot times out (`game_time_seconds >=
MAX_GAME_TIME_SECONDS`), gym signals shutdown (`action=None` from
`SC2Env.close()`), or the parent loop decides to reset — MUST call
`await self.client.leave()` and flip `_FullTrainingBot._episode_done = True`
before returning from `on_step`. This matters because:

1. `sc2.main._play_game_ai` drives the bot loop synchronously; the only way
   for the game to end from inside `on_step` is for `client.in_game` to
   become `False`, which `client.leave()` achieves. Just `return`-ing leaves
   SC2 running and — because the trainer runs `realtime=False` — game-time
   ticks forward as fast as the CPU allows, so `on_step` is immediately
   re-entered on the next tick.
2. If the bot re-enters the timeout branch on every subsequent tick it
   floods `_obs_queue` with phantom terminal observations and the thread
   orphans (because it never reads the `None` shutdown signal from
   `_action_queue`).
3. Orphaned threads compound: on the next `reset()`, `close()` joins with a
   30 s timeout, the join times out, `reset()` spawns a *new* game thread
   alongside the zombie, and burnysc2's `KillSwitch._to_kill` starts
   accumulating references. When one of the sibling threads eventually
   exits normally, its `SC2Process.__aexit__` calls `KillSwitch.kill_all()`
   which iterates the class-level list and `_clean()`s **every** registered
   process — including the other sibling's live `SC2Process`. The live
   sibling's next `receive_bytes` call then returns `WSMessageTypeError(257,
   None)` (type 257 = `WSMsgType.CLOSED`), which burnysc2 wraps as
   `ConnectionAlreadyClosedError` and the training cycle crashes.

**Queue isolation on reset:** `SC2Env.reset()` allocates *fresh*
`queue.Queue` instances and passes them into `_run_game_thread` as
arguments. The bot's closure captures those specific queue references at
construction time, so a hypothetical zombie thread from a previous episode
can only push into its own dead queue — it cannot contaminate the new
game's observation stream.

**`KillSwitch._to_kill` hygiene:** `_run_game_thread` clears
`sc2.sc2process.KillSwitch._to_kill` in its `finally` block. burnysc2 never
prunes that class-level list on its own, so across many sequential games
the dead references accumulate and a later `kill_all()` would cross-kill
unrelated processes. Draining it per-thread is the only safe defensive
measure short of patching burnysc2.

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

### Daemon state vs. training progress (#71 / #73)

The `TrainingDaemon` exposes `state: idle | checking | training` via
`/api/training/daemon`. Historically, `state: training` meant only "the daemon
entered `_run_training` and has not yet returned" — it said nothing about
whether `TrainingOrchestrator.run(...)` was actually making progress or was
stuck inside SB3's `.learn()` retry loop while every per-environment cycle
crashed in the background.

Two observability layers now disambiguate the two meanings:

1. **Post-orchestrator bookkeeping (#71).** Once `orchestrator.run()` returns,
   the daemon inspects `cycle_results` and, if every entry has
   `status: "crashed"` (or the orchestrator returned `cycles_completed == 0`
   with empty `cycle_results`), sets `_last_error` to a descriptive string,
   logs at ERROR level so the entry reaches `ErrorLogBuffer`, and does NOT
   increment `runs_completed`. This is the authoritative final state for
   the run.

2. **Per-cycle watchdog (#73, scope widened by Phase 4.7 Step 2 / #83).**
   Because #1 only runs after the training + post-training path has
   completed — and that path can block for an arbitrarily long time
   inside SB3's `.learn()` loop during training OR inside the
   promotion/rollback blocks afterward — the daemon spawns a
   short-lived watchdog thread inside `_run_training` covering the
   **full window** from `orchestrator.run(...)` through the
   promotion-gate (`evaluate_and_promote`) and rollback-check
   (`check_for_regression` / `execute_rollback`) blocks, stopping
   just before the `_last_result` / `_last_error` bookkeeping block.
   The watchdog captures a baseline of
   `ErrorLogBuffer._count_since_start` before the orchestrator starts
   and polls every `DaemonConfig.watchdog_poll_seconds` (default 5s).
   If the delta exceeds `DaemonConfig.watchdog_error_threshold`
   (default 5) the watchdog sets an interim `_last_error` through the
   daemon's lock and logs at ERROR level. The watchdog is joined
   before the bookkeeping block so there is no race with the #71
   bookkeeping; the bookkeeping is always the final writer of
   `_last_error` for the run. The original #73 scope was narrower
   ("exactly the duration of the orchestrator call"); soak-2026-04-11b
   caught 18 backend errors accumulating silently during the eval
   phase with `daemon.last_error` staying `null`, which is exactly
   what Phase 4.7 Step 2 widened the window to catch.

**Reading the signals:**

| `state` | `last_error` | `runs_completed` | Meaning |
|---------|--------------|------------------|---------|
| `training` | `null` | unchanged | Training in progress, no anomalies surfaced yet |
| `training` | `"Watchdog: N ERROR-level log records..."` | unchanged | Per-cycle errors piling up mid-run; operator should investigate |
| `idle` | `"All N training cycles crashed; first error: ..."` | unchanged | #71 bookkeeping: orchestrator returned with every cycle crashed |
| `idle` | `null` | incremented | Clean run |

**Scope of the watchdog.** It is purely observability. It does NOT transition
state, does NOT kill the trainer subprocess (#74 owns termination semantics),
and does NOT mutate any field other than `_last_error` and its own
`_watchdog_thread` handle. A single watchdog fires at most one ERROR log
record per training run (one-shot) so a broken cycle cannot feed its own
watchdog.

### PPO variant dispatch

`_init_or_resume_model()` picks one of four model classes based on hyperparams:

| `policy_type` | `kl_rules_coef` | Class | Notes |
|---|---|---|---|
| `MlpPolicy` (default) | 0.0 | `PPO` | Stock SB3 PPO |
| `MlpPolicy` | > 0.0 | `PPOWithKL` | Adds a KL penalty term to keep policy close to the rule-based reference |
| `MlpLstmPolicy` | 0.0 | `RecurrentPPO` | LSTM memory via `sb3_contrib` |
| `MlpLstmPolicy` | > 0.0 | `RecurrentPPOWithKL` | LSTM + KL-to-rules (AlphaStar-style) |

The KL-to-rules penalty pulls the policy toward a frozen rule-based reference (`learning/rules_policy.py`) — prevents catastrophic forgetting of the bootstrapped behavior during RL.

**Imitation-init path** (`use_imitation_init: true` in hyperparams):
- If `data/checkpoints/v0_pretrain.zip` exists, load it as starting weights.
- If missing and `--ensure-pretrain` not passed, log a warning and fall through to a fresh model.
- `resume` takes priority: if a `best` checkpoint exists, resume wins over imitation.

### TrainingDaemon

Background thread that triggers training runs autonomously (`learning/daemon.py`).

**Trigger logic** (OR):
- `transitions-since-last-run >= min_transitions` (default 500)
- `hours-since-last-run >= min_hours_since_last` (default 1.0)

**Default config** (`DaemonConfig`):
| Field | Default |
|---|---|
| `check_interval_seconds` | 60 |
| `min_transitions` | 500 |
| `min_hours_since_last` | 1.0 |
| `cycles_per_run` | 5 |
| `games_per_cycle` | 10 |
| `watchdog_poll_seconds` | 5 |
| `watchdog_error_threshold` | 5 |

**API surface** — `/api/training/daemon` (status), `/api/training/triggers` (trigger evaluation). See [monitoring.md](monitoring.md) for the Loop tab and LoopStatus component.

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
| `FEATURE_DIM` | 24 (17 base + 7 advisor) | features.py |
| `BASE_GAME_FEATURE_DIM` | 17 | features.py |
| Default `win_rate_threshold` | 0.8 | trainer.py |
| Default `max_epochs` (imitation) | 100 | imitation.py |
| Default `agreement_threshold` | 0.95 | imitation.py |

### Key file locations

| File | Purpose |
|------|---------|
| `src/alpha4gate/learning/trainer.py` | TrainingOrchestrator — cycle loop, curriculum, diagnostics, variant dispatch |
| `src/alpha4gate/learning/daemon.py` | TrainingDaemon — background triggers, watchdog |
| `src/alpha4gate/learning/environment.py` | SC2Env — gymnasium wrapper, threading, queue bridge |
| `src/alpha4gate/learning/imitation.py` | run_imitation_training — behavior cloning |
| `src/alpha4gate/learning/neural_engine.py` | NeuralDecisionEngine — inference, hybrid override |
| `src/alpha4gate/learning/checkpoints.py` | save/load/prune/list/promote checkpoints, manifest management |
| `src/alpha4gate/learning/hyperparams.py` | Load/convert PPO hyperparameters |
| `src/alpha4gate/learning/rules_policy.py` | Rule-based policy reference for KL-to-rules target |
| `src/alpha4gate/learning/ppo_kl.py` | PPOWithKL + RecurrentPPOWithKL variants |
| `src/alpha4gate/learning/promotion.py` | PromotionManager — see [promotions.md](promotions.md) |
| `src/alpha4gate/learning/rollback.py` | RollbackMonitor — see [promotions.md](promotions.md) |
| `src/alpha4gate/learning/evaluator.py` | ModelEvaluator — deterministic inference eval |
| `src/alpha4gate/learning/advisor_bridge.py` | Thread-safe Claude advisor queue for training |
| `src/alpha4gate/runner.py` | CLI entry point — `--train`, `--decision-mode`, `--serve`, `--daemon` |
| `data/checkpoints/` | Checkpoint files + manifest.json |
| `data/hyperparams.json` | PPO hyperparameter defaults + `policy_type`, `kl_rules_coef`, `use_imitation_init` |
| `data/diagnostic_states.json` | Hand-crafted test states for diagnostics |
| `data/training_diagnostics.json` | Per-cycle action distributions on test states |
