# Promotion History

A record of every model promotion — when a new checkpoint became "best" and why.

> **At a glance:** This page tracks model promotions: the moment a trained checkpoint is
> evaluated, deemed better than the previous best, and promoted. Today promotions are
> manual (human picks "best" during training). The always-up plan targets automated
> promotion with rollback. This page will grow into a living log as the autonomous
> loop matures.

## What is a promotion?

A promotion happens when a new model checkpoint replaces the current "best" model. The
promoted model is what the bot uses during gameplay (when `--decision-mode neural` is
active) and what the next training cycle resumes from.

### Current promotion flow (manual)

```
1. TrainingOrchestrator runs N cycles
2. Each cycle saves a checkpoint with metadata {cycle, difficulty, win_rate}
3. Orchestrator marks the last cycle as "best" in manifest.json
4. Human reviews training_diagnostics.json and win rates
5. Human may override by editing manifest.json or re-running with different params
```

**Problem:** There's no evaluation gate. The last checkpoint becomes "best" even if it
performs worse than the previous one. There's no comparison, no rollback, no record of
*why* a promotion happened.

### Target promotion flow (autonomous)

```
1. Training cycle completes → new checkpoint saved
2. Evaluation: run N games with new checkpoint vs. current best
3. Compare: win rate, reward trends, action distribution
4. If better by threshold → PROMOTE (update manifest, log reason)
5. If worse → REJECT (keep current best, log reason)
6. Either way → record in promotion history
```

---

## Promotion Log

> This table will be populated as promotions happen. Each entry records what changed,
> the evidence, and the outcome.

| Date | From | To | Win Rate Change | Difficulty | Reason | Outcome |
|------|------|----|----------------|------------|--------|---------|
| *2026-04-xx* | *v0_pretrain* | *v1* | *— → 65%* | *1* | *First RL cycle, baseline* | *promoted* |

*(Entries will be added as the autonomous loop runs promotions.)*

---

## What to record per promotion

Each promotion entry should capture:

1. **Timestamp** — when the promotion decision was made
2. **Previous best** — checkpoint name and its known win rate
3. **New candidate** — checkpoint name, training cycle, difficulty level
4. **Evidence** — win rate comparison, number of eval games, confidence
5. **Action distribution shift** — did the model's behavior change meaningfully?
   (from training_diagnostics.json)
6. **Decision** — promoted or rejected
7. **Reason** — human-readable summary of why

### Gaps (feeds into [Phase 3](../plans/always-up-plan.md))

- **No automated evaluation gate** — last checkpoint auto-becomes best
- **No comparison infrastructure** — can't run "new vs old" benchmark
- **No promotion logging** — manifest.json tracks "best" but not history
- **No rollback** — if promoted model is worse, manual intervention needed
- **training_diagnostics.json exists** but isn't used for promotion decisions

### Key file locations

| File | Role |
|------|------|
| `data/checkpoints/manifest.json` | Current "best" model and checkpoint list |
| `data/training_diagnostics.json` | Per-cycle action distributions (could inform promotions) |
| `src/alpha4gate/learning/checkpoints.py` | save/load/prune/get_best_name |
| `src/alpha4gate/learning/trainer.py` | Where promotions currently happen (implicit) |
