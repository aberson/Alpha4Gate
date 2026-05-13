---
description: Evolve mechanics — reading run state, pre-launch hygiene, snapshot import isolation, dev-apply sanitization, fitness noise floor, training-imp pool restriction.
paths:
  - "scripts/evolve*.py"
  - "src/orchestrator/evolve.py"
  - "src/orchestrator/snapshot.py"
  - "bots/v*/learning/post_promotion_hooks.py"
---

# Evolve

## Reading evolve state

Before assuming evolve promoted anything (or committing a `bots/vN/`):

```powershell
Get-Content data/evolve_run_state.json | ConvertFrom-Json | Select generations_promoted, parent_current
Get-Content data/evolve_results.jsonl -Tail 1 | ConvertFrom-Json | Select phase, outcome
```

Only commit a `bots/vN/` directory if `generations_promoted` increased **AND** the last result row shows a terminal-success outcome. Untracked `bots/vN/` in `git status` is just as likely to be transient scratch from a rolled-back promotion attempt — committing it freezes a failure into history.

## Pre-launch hygiene

`EVO_AUTO=1` commits sweep the **entire git index**, not just `bots/<v>/`. Before launching `scripts/evolve.py` or `scripts/evolve_inject_one.py`:

```bash
git diff --staged --stat
```

Anything staged rides along into the next `[evo-auto]` promote commit. The sandbox hook (`scripts/check_sandbox.py`) blocks invalid paths but doesn't enforce "ONLY these paths."

## Where features must land

Evolve always snapshots from `bots/current` (resolved via `bots/current/current.txt`). Feature work in any other `bots/vN/` is **dormant** — invisible to evolve and to the runtime.

To fold dormant work in, use:
```bash
uv run python scripts/snapshot_bot.py --from <source> --name <new>
```
Then commit `bots/<new>/` and the pointer flip together. WR-changing features land in `bots/current/` directly and earn promotion via evolve's 2-gate pipeline.

## Snapshot import isolation

`bots/v0/*.py` uses absolute imports (`from bots.v0.X import Y`). `snapshot_current` rewrites these to `bots.<new>.*` at column 0 — a regex pass in `src/orchestrator/snapshot.py::_rewrite_imports`. Removing or weakening that rewrite silently breaks isolation: the new candidate's runtime call graph flows through `bots.v0.*` and all edits to the candidate are ignored.

If converting v0 to relative imports, audit and remove the rewrite **in the same change** — otherwise the rewrite mangles already-relative paths.

## Dev-apply sub-agent sanitization

`_invoke_subagent` must keep BOTH fixes from commit `e7fb758`:

1. **Strip `bots/v\d+/` prefixes** from `imp.description` / `imp.concrete_change` before the prompt — otherwise the sub-agent edits the parent version, not the candidate, and stack-apply fails with `DevApplyOutOfScopeError`.
2. **Force `encoding="utf-8", errors="replace"`** on the `subprocess.run` call — Windows default cp1252 can't encode `→`, em-dashes, smart quotes routinely emitted by the advisor.

## Fitness noise floor

`pass_threshold = games // 2 + 1` is strict majority — always **50% null-hit, regardless of `n`**. Raising `--games-per-eval` alone does not tighten the gate.

- Prefer `n=9` over `n=5` for the **smaller standard error**, not for a tighter null.
- A pass that doesn't reproduce in next-gen retry is a noise sample, not a regression.

## Training imps are noise (pool is dev-only)

Reward-rule / hyperparam patches don't affect in-game behavior — `compute_step_reward` has no runtime consumer in `--decision-mode rules` (the default). The evolve pool filters to `type=dev` via `_filter_dev_only()`. Don't re-enable training imps in the pool without adding a PPO retrain step inside the round. Training imps are handled separately by the post-promotion daemon hook (`--post-training-cycles N`).

## Source memories

`feedback_dont_commit_evolve_scratch`, `feedback_evo_auto_commits_sweep_staged`, `feedback_phase_n_dormant_in_current`, `feedback_snapshot_import_isolation`, `feedback_dev_apply_imp_text_sanitization`, `feedback_evolve_fitness_5game_noise_floor`, `feedback_evolve_training_imps_variance_only`, `feedback_evolve_powershell_launch_canonical`.
