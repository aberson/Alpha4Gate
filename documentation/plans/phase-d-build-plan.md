# Phase D Build Plan — Build-order z-statistic (reward refactor)

**Parent plan:** [alpha4gate-master-plan.md](alpha4gate-master-plan.md) — Phase D
**Track:** Capability
**Prerequisites:** Phase 5. B and D are orthogonal — D can run before, after, or in parallel with B.
**Effort estimate:** ~3 days (audit is most of it).
**Status:** Drafted, not yet started. Detail extracted from the master plan
on 2026-04-19 as part of the plan/build-doc cleanup.

## 1. What this feature does

Collapses implicit build-order reward rules into an explicit `z` target
vector with an edit-distance pseudo-reward. Today's
`bots/current/data/reward_rules.json` mixes build-order rules
(timed structure/tech goals) with tactical, economic, and other rules
in one flat list. That conflation makes the early-game reward signal
noisy because every rule fires regardless of which build the policy is
trying to follow.

By extracting build-order rules into named target sequences and
rewarding the policy on edit-distance to the chosen sequence, the
early-game reward variance should drop substantially while still letting
tactical/economic rules shape mid- and late-game.

## 2. Existing context

- **`bots/current/data/reward_rules.json`** — 48 active reward rules
  (per master plan baseline). Some are build-order timing rules
  (e.g. "Gateway by 1:30"), others are tactical (e.g. "engage when
  army_supply > enemy * 1.2") or economic (e.g. "saturate gas").
- **`bots/current/learning/`** — PPO training pipeline. Reward signal
  is computed per step, summed across an episode.
- **Curriculum** — current curriculum already has a notion of difficulty
  but no notion of "which build are we trying."

## 3. Scope (build steps)

| Step | Description |
|------|-------------|
| D.1 | Audit `bots/current/data/reward_rules.json`: tag each rule as (a) build-order, (b) tactical, (c) economy, (d) other. Edge cases tagged by primary purpose. |
| D.2 | `z` schema: `bots/current/data/build_orders/<label>.json` = `{"name": str, "targets": [{"action": str, "time_seconds": int, "weight": float}], "tolerance_seconds": int}`. |
| D.3 | `bots/current/learning/build_order_reward.py` — edit-distance between executed and target; per-step reward = `-α * edit_distance_delta`. |
| D.4 | Migrate (a) rules into build-order files; keep (b)(c)(d) as shaped rewards. |
| D.5 | Append `z` identifier as optional obs slot so policy can condition on chosen build. |
| D.6 | Backwards-compat: existing rules keep working; `use_build_order_reward: false` default. |
| D.7 | Train 3 cycles, measure early-game reward variance + win rate. |
| D.8 | Snapshot to `vN+1` on promotion. |

## 4. Tests

- `tests/test_build_order_reward.py` — edit distance correct, reward
  monotonic in progress, empty target list handled.
- `tests/test_reward_migration.py` — pre/post migration reward totals on
  known game logs agree within 5%.

## 5. Validation

Early-game (first 5 min) reward std-dev drops ≥30% AND win-rate holds at
difficulty 3 AND Elo gain ≥ +10 over 20 games.

## 6. Gate

All three validation criteria simultaneously.

## 7. Kill criterion

Reward variance does not drop — old rules already captured it. Keep the
migration as cleanup; skip `z`-as-obs wiring; do not snapshot.

## 8. Rollback

Backup `reward_rules.pre-phase-d.json`; restore on kill. Delete the
promoted `vN` if already snapshotted.
