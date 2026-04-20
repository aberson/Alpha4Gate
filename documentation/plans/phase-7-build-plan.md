# Phase 7 Build Plan — Advised loop stale-policy detection

**Parent plan:** [alpha4gate-master-plan.md](alpha4gate-master-plan.md) — Phase 7
**Track:** Operational
**Prerequisites:** Phase 5 (sandbox + skill integration). Independent of B/D/E/6 — ships standalone.
**Effort estimate:** ~1 day build + one overnight validation run.
**Status:** Drafted, not yet started. Detail extracted from the master plan
on 2026-04-19 as part of the plan/build-doc cleanup.

## 1. What this feature does

Teaches `/improve-bot-advised` to recognize when the PPO policy is
stale relative to the current reward / hyperparam config, and to
schedule an extended training soak as a first-class improvement type —
rather than relying on the user to manually switch to
`/improve-bot --mode training`.

Today the advised loop's `training` path only does a 2-game sync soak
(Phase 6.3 of the existing skill) — enough to create a checkpoint, not
enough to actually train PPO against new rewards. The loop can iterate
on reward rules forever without the policy ever catching up. This
phase closes that gap.

## 2. Existing context

- **`/improve-bot-advised`** (`.claude/skills/improve-bot-advised/SKILL.md`)
  — autonomous loop with Phase 0 bootstrap, Phase 2 Claude analysis
  (returns improvements list), Phase 4 dispatch by improvement type.
- **Phase 4 Elo ladder** (`src/orchestrator/ladder.py`,
  `data/bot_ladder.json`) — provides the deterministic eval history
  needed to compute WR trends.
- **`bots/current/data/checkpoints/*.zip`** — PPO checkpoints; mtime
  reveals when training last ran.
- **`bots/current/data/reward_rules.json`** — reward config; mtime
  reveals when rules were last edited.

## 3. Scope (build steps)

| Step | Description |
|------|-------------|
| 7.1 | `src/orchestrator/staleness.py` — compute `eval_wr_trend` over last 3 deterministic evals (from Phase 4 Elo ladder) + `checkpoint_age_since_last_reward_edit` (mtime of `bots/current/data/checkpoints/*.zip` vs. `reward_rules.json`). Return `StalenessReport` dataclass. |
| 7.2 | Extend Phase 2 Claude analysis prompt in `/improve-bot-advised` to accept a `staleness_report` block and allow a third improvement type: `{type: "soak", soak_hours: N, decision_mode: "hybrid", rationale: "..."}`. Update response-format example in SKILL.md. |
| 7.3 | Phase 4 routing for `type: "soak"`: shut down API-only backend, spawn daemon with `--decision-mode hybrid` for N hours, graceful shutdown, API-only restart. Reuse the existing Phase 6.3 daemon-lifecycle pattern verbatim. |
| 7.4 | Budget guard: soak hours debit the `--hours` wall-clock budget; cap single soak at `min(remaining_budget / 2, 4h)` so the loop can't consume itself. Emit warning if analyzer requests more. |
| 7.5 | Tests: `tests/test_staleness_signal.py` (staleness heuristic with synthetic eval history), `tests/test_advised_soak_routing.py` (Phase 4 dispatch of soak-type improvements, mocked daemon). |

## 4. Tests

- `tests/test_staleness_signal.py` — fixtures for flat/rising/falling
  WR trends and varying checkpoint ages; confirm `is_stale` fires only
  when trend flat AND checkpoint older than last reward edit.
- `tests/test_advised_soak_routing.py` — mock Claude response with
  `type: "soak"`; confirm Phase 4 invokes daemon with
  `--decision-mode hybrid` for requested hours and respects budget cap.

## 5. Validation

Run `/improve-bot-advised --self-improve-code --hours 8` with reward
rules recently edited. Loop must:

1. Detect staleness on first iteration (reward edit newer than checkpoint).
2. Emit a `soak` improvement.
3. Run the soak to completion (2–4h).
4. Post-soak deterministic eval shows measurable Elo delta (sign doesn't
   matter for the gate — just proves the path works end-to-end).
5. Loop resumes and completes remaining budget without starving other
   iterations.

## 6. Gate

All 5 validation steps pass in a single run. Soak-type improvements
must not exceed 50% of the total `--hours` budget across the run.

## 7. Kill criterion

Staleness signal fires on every iteration (false-positive storm) OR
never fires across 3 runs with obviously stale policies
(false-negative). Either means the heuristic is wrong — revisit step
7.1 before shipping.

## 8. Rollback

Revert the phase's commits on `bots/current/` and
`src/orchestrator/staleness.py`. SKILL.md change is self-contained in
`.claude/skills/improve-bot-advised/SKILL.md`. No data migrations.
