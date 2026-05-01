# Promotions & Rollback

How the inner loop decides whether a new neural checkpoint is actually better, and what happens when it isn't.

> **At a glance:** After every training cycle, `PromotionManager.evaluate_and_promote()` runs a deterministic inference-only eval on both the new checkpoint and the current best, compares win rates with a 5% threshold, and refuses to promote on any crashed eval game. `RollbackMonitor` watches the promoted checkpoint and reverts to the previous best if win rate drops 15% below its promotion-time rate. All decisions append to `bots/<active>/data/promotion_history.json` (per-version state under `bots/current/`, currently `bots/v10/`) with a stable `reason_code`, surfaced in the Improvements tab.

See [training-pipeline.md](training-pipeline.md) for where this sits in the inner loop; [improve-bot-advised-architecture.md](improve-bot-advised-architecture.md) for how the outer loop's TRAIN phase invokes it.

---

## The Gate (one diagram)

```
 new checkpoint saved by TrainingOrchestrator
            │
            ▼
 ┌─────────────────────────────────────────────────┐
 │  PromotionManager.evaluate_and_promote()        │
 │                                                 │
 │   1. ModelEvaluator.evaluate(new)   → EvalResult│
 │   2. ModelEvaluator.evaluate(best)  → EvalResult│
 │   3. Check max_crashed (default 0):             │
 │         ANY crashed game on either side → REJECT│
 │   4. Check min_eval_games (default 10):         │
 │         fewer valid games → REJECT              │
 │   5. Compare win rates:                         │
 │         new_wr >= best_wr + threshold (5%) ?    │
 │         YES → PROMOTE                           │
 │         NO  → REJECT                            │
 └─────────────────────────────────────────────────┘
            │
            ├── PROMOTE ──> promote_checkpoint() updates manifest.json
            │               └──> (may trigger curriculum difficulty++)
            │
            └── REJECT  ──> new checkpoint stays on disk but isn't "best"
                            (pruned eventually by checkpoint pruner)

 Every decision appends one entry to bots/<active>/data/promotion_history.json
 (resolved via bots/current/current.txt; bots/v10/ today)
```

And separately, on every daemon cycle:

```
 RollbackMonitor.check_for_regression(current_best)
            │
            ▼
 Compare current win rate vs. win rate at promotion time
     (needs min_games_before_check, default 10 games since promotion)
            │
            ▼
 drop > regression_threshold (default 15%) ?
     YES → execute_rollback(): manifest best ← previous_best
            Append entry with reason_code="rollback"
     NO  → no-op
```

---

## Decision Outcomes

Every evaluation appends one `PromotionDecision` to `bots/v0/data/promotion_history.json`. The `reason_code` field is the stable classifier:

| `reason_code` | Meaning | Promoted? |
|---|---|---|
| `first_baseline` | No prior best existed — promoted unconditionally (unless eval crashed) | Yes |
| `win_rate_gate` | New WR exceeded old by ≥5% over ≥10 valid games | Yes |
| `rejected_not_better` | New WR did not exceed old by the threshold | No |
| `rejected_insufficient_games` | Eval produced fewer than `min_eval_games` valid games | No |
| `rejected_crashed` | Either eval had ≥1 crashed game (`max_crashed=0`) | No |
| `manual` | Operator promoted via `POST /api/training/promote` | Yes |
| `rollback` | `RollbackMonitor` reverted to `previous_best` after regression | No (rollback) |

**Why `max_crashed=0`?** Phase 4.5 blocker #67 — crashed eval games used to be silently counted as losses, which made a broken run look like a regression. The gate now refuses to decide if any game crashed, forcing a re-eval.

---

## Configuration

### Promotion gate — `PromotionConfig` (`learning/promotion.py:19`)

| Field | Default | What it gates |
|---|---|---|
| `eval_games` | 20 | Games played per checkpoint per evaluation |
| `win_rate_threshold` | 0.05 | Minimum WR delta (new − old) required to promote |
| `min_eval_games` | 10 | Minimum non-crashed games to trust the WR |
| `max_crashed` | 0 | Refuse if more than this many eval games crashed |

### Rollback monitor — `RollbackConfig` (`learning/rollback.py:19`)

| Field | Default | What it gates |
|---|---|---|
| `regression_threshold` | 0.15 | WR must drop this far below promotion-time WR to revert |
| `min_games_before_check` | 10 | Games since promotion before rollback can fire |

---

## The Log — `bots/v0/data/promotion_history.json`

Append-only JSON array. Each entry:

```json
{
  "timestamp": "2026-04-14T08:32:11+00:00",
  "new_checkpoint": "v37",
  "old_best": "v35",
  "new_win_rate": 0.72,
  "old_win_rate": 0.65,
  "delta": 0.07,
  "eval_games_played": 20,
  "promoted": true,
  "reason": "win rate delta 0.07 >= 0.05 threshold",
  "reason_code": "win_rate_gate",
  "difficulty": 3,
  "action_distribution_shift": 0.14
}
```

Rollback entries use the same schema with `promoted=false` and `reason` prefixed `"rollback:"`.

Self-healing: `PromotionLogger` tolerates a corrupt history file (renames it to `.corrupt` and starts fresh) so a malformed entry can't lock the gate out.

---

## API Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/training/promotions` | GET | Summary: total promotions, rollbacks, latest entry |
| `/api/training/promotions/history` | GET | Full history (consumed by Improvements → Recent Improvements) |
| `/api/training/promotions/latest` | GET | Most recent entry only |
| `/api/training/promote` | POST | Manual promotion (uses `reason_code="manual"`) |
| `/api/training/rollback` | POST | Manual rollback |

---

## Dashboard Surfaces

| Component | File | What it shows |
|---|---|---|
| Recent Improvements | `frontend/src/components/RecentImprovements.tsx` | Classifies each history entry as `promotion \| rollback \| rejected` via `classifyEntry()`; timestamp, delta, reason_code, difficulty |
| Reward Trends | `frontend/src/components/RewardTrends.tsx` | Per-rule reward contribution over last N games |
| Checkpoint List | `frontend/src/components/CheckpointList.tsx` | All checkpoints with `best` indicator from `manifest.json` |
| Loop tab | `frontend/src/components/LoopStatus.tsx` | Current daemon cycle; `last_result` contains `win_rate` and `final_difficulty` post-promotion |

Alert rule `ruleRollbackFired` (warning severity) fires when the latest history entry has `promoted=false` and `reason` starts with `"rollback:"`. See [monitoring.md](monitoring.md) for the full alert table.

---

## Relationship to the Outer Loop

The outer loop (`/improve-bot-advised`) never directly promotes. Its TRAIN phase kicks off a short daemon soak, and the inner loop's promotion gate decides what counts as "better." This means:

- An outer-loop iteration that changes reward rules or code can indirectly affect the next promotion by changing what the PPO policy learns.
- An outer-loop iteration is considered successful (PASS in TEST phase) based on validation win rate vs. baseline — independently of whether the inner loop promoted anything.
- Rollbacks can happen without the outer loop's involvement (daemon detects regression between advised-run iterations).

See [improve-bot-advised-architecture.md](improve-bot-advised-architecture.md) for how TRAIN slots into the outer loop.

---

## Key File Locations

| File | Purpose |
|---|---|
| `bots/v0/learning/promotion.py` | `PromotionManager`, `PromotionConfig`, `PromotionDecision`, `PromotionLogger` |
| `bots/v0/learning/rollback.py` | `RollbackMonitor`, `RollbackConfig`, `RollbackDecision` |
| `bots/v0/learning/evaluator.py` | `ModelEvaluator` — inference-only eval used by both |
| `bots/v0/learning/checkpoints.py` | `get_best_name`, `promote_checkpoint` — manifest manipulation |
| `bots/v0/data/promotion_history.json` | Append-only decision log |
| `bots/v0/data/checkpoints/manifest.json` | Source of truth for current best + `previous_best` |
| `tests/test_promotion.py` | Gate logic tests |
| `tests/test_rollback.py` | Rollback monitor tests |
| `tests/test_evaluator.py` | Evaluator tests including crash-handling |
