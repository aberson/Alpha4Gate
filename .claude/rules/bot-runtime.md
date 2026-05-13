---
description: Backend / daemon / SC2 client lifecycle rules. Mis-launching any of these silently produces empty-data symptoms.
paths:
  - "bots/**/runner.py"
  - "bots/**/learning/daemon.py"
  - "bots/current/**"
  - "scripts/start-dev.sh"
  - "scripts/live-test.sh"
  - "src/orchestrator/**"
---

# Bot runtime

## SC2 process lifecycle

**Never kill `SC2_x64.exe`** — only kill the Python/uvicorn daemon process. SC2 must stay running for burnysc2 to connect on the next invocation. Use `Stop-Process -Id <python-pid> -Force` against the Python PID; never `taskkill /F /IM SC2_x64.exe`.

## Backend `--serve` lifecycle

Start exactly ONE `--serve` process. The runner's `_start_server_background()` skips if port 8765 is already bound. Never start the backend via `run_in_background` from a Claude Code session — background tasks get killed on cleanup and you'll lose the dashboard mid-task. Launch from the operator's own PowerShell terminal.

**Always launch from the project root** (`cd Alpha4Gate`). `DATA_DIR=data` is relative; a wrong CWD silently routes all reads/writes to `frontend/data/`. Symptoms: `/api/reward-rules` returns `[]`, daemon refuses to spawn games, all dashboard tabs go empty — no errors logged anywhere. Fast confirmation: check whether `Alpha4Gate/frontend/data/training.db` exists; if it does, the backend was launched with wrong CWD at some point.

Before declaring the backend down on Windows, verify with `(Get-NetTCPConnection -LocalPort 8765 -State Listen).OwningProcess`. Closing the terminal does not reliably propagate Ctrl+C to uvicorn — confirm the port is free before launching a replacement.

## Daemon

`--daemon` spawns SC2 games every 60s. Only the improvement-loop skill should start the daemon. `--serve` alone is fine for monitoring; `/api/restart` must drop the `--daemon` flag before restarting, or every restart inherits the loop.

## Per-version vs cross-version state

- **Per-version state** (`training.db`, `checkpoints/`, `reward_rules.json`, `hyperparams.json`) lives at `bots/<current>/data/` via `orchestrator.registry.get_data_dir()`.
- **Cross-version orchestrator state** (evolve/, ladder/, promotion chain) lives at repo-root `data/`.

When adding a new backend endpoint that touches cross-version state, use a dedicated `_<feature>_dir` module global, not the shared `_data_dir`. Sharing one resolver silently breaks either category — the endpoint reads the default empty skeleton even though the file exists at a different absolute path.

## Decision modes and PPO

Default `--decision-mode rules` runs the rule-based state machine; the PPO model is **never consulted**. Reward-rule and hyperparam changes only affect PPO training, not regular games.

Validate training-mode iterations with `--decision-mode hybrid --model-path <ckpt>`, or use `--self-improve-code` to change the rule-based engine directly. Don't conclude "the new reward rule didn't help" from a `--decision-mode rules` run — the run never consulted the trained policy.

## SC2 API invariants

- **The SC2 server caps API clients at 2.** Any architecture with 3+ live API clients is dead (`Only 1v1 is supported when using multiple agents` from `_setup_host_game`). Late observer joins also hang and corrupt the live game. Workarounds: replay-stream-as-live (3rd process tails the .SC2Replay file) or custom renderer reading `RequestObservation`. Phase L v1 chose replay-stream.
- **`debug_show_map()` is perception-affecting**, not rendering-only. Toggling it on a training bot changes `self.enemy_units` (0 → 13.46 in spike data) — the PPO policy will overfit to cheating vision. Same for `disable_fog=True` at create_game. Both are banned for any bot whose actions feed PPO training; both are banned for exhibition viewers (bots play differently than rated). Safe only for dev-only debug runs and isolated bot evaluation that doesn't feed training.

## burnysc2 combineable abilities

Abilities in `sc2.constants.COMBINEABLE_ABILITIES` (MORPH_ARCHON, STOP, MOVE, ATTACK, EFFECT_BLINK, BURROWDOWN, etc.) take **no target argument** when issued to multiple units — burnysc2 merges per-unit calls at the protocol layer.

```python
# Correct
for unit in selected:
    unit(AbilityId.MORPH_ARCHON)
# WRONG — burnysc2 emits RuntimeWarning and silently drops the command
unit_a(AbilityId.MORPH_ARCHON, unit_b)
```

Check stderr for `RuntimeWarning` on signature mismatch before tuning filters.

## Source memories

`feedback_sc2_process_management`, `feedback_backend_lifecycle`, `feedback_daemon_no_autostart`, `feedback_backend_wrong_cwd_silent`, `feedback_dev_serve_windows_close`, `feedback_per_version_vs_cross_version_data_dir`, `feedback_training_mode_requires_hybrid`, `feedback_sc2_server_multi_agent_cap`, `feedback_show_map_is_perception_affecting`, `feedback_disable_fog_rejected_for_observable`, `feedback_burnysc2_combineable_abilities`.
