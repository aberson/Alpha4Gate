# Phase E Build Plan — Autoregressive action head

**Parent plan:** [alpha4gate-master-plan.md](alpha4gate-master-plan.md) — Phase E
**Track:** Capability
**Prerequisites:** Phase 5. Ideally Phase B done first (more interesting ATTACK decisions to differentiate).
**Effort estimate:** ~1 week.
**Status:** Drafted, not yet started. Detail extracted from the master plan
on 2026-04-19 as part of the plan/build-doc cleanup.

## 1. What this feature does

Unlocks tactical variety by making ATTACK / EXPAND conditional on
**target choice** rather than a flat `Discrete(6)` action space. The
current policy can only output one of six strategic states; it cannot
distinguish "attack the enemy main" from "attack the enemy natural" or
"expand to my third base" from "expand to my fourth." This phase
restructures the action space into `(strategic_state, target)` pairs
and gives the policy two sequential softmax heads so it can learn
target-conditional behavior.

## 2. Existing context

- **Action space today:** `Discrete(6)` — strategic states only.
- **`ACTION_TO_STATE`** in `bots/current/decision_engine.py` — canonical
  mapping from action index to `StrategicState`.
- **`SC2Env.action_space`** in `bots/current/learning/environment.py`
  — currently `Discrete(6)`.
- **`rules_policy.py`** — KL teacher for `--decision-mode hybrid`. Must
  be updated to emit 12-way targets.
- **Diagnostic states** at `bots/current/data/diagnostic_states.json`
  hold expected-action per fixed snapshot for canary regression.

## 3. Scope (build steps)

| Step | Description |
|------|-------------|
| E.1 | Structured action space: `(strategic_state, target)` — ATTACK → {main, natural, third}, EXPAND → {own_natural, own_third, own_fourth}, others unchanged. Effective size = 12. |
| E.2 | Custom `ActorCriticPolicy` subclass: two sequential softmax heads, `p(strategic)` then `p(target \| strategic)`. |
| E.3 | `bots/current/learning/database.py` — add `action_space_version INT`; `1` legacy, `2` Phase E. |
| E.4 | `_compute_next_state` also picks a target. Cascade into `rules_policy.py` KL teacher. |
| E.5 | Migration: 6-way DB rows infer target from game log when possible, else `*_main`. |
| E.6 | Snapshot to `vN+1` on promotion. |

## 4. Impact matrix (within `bots/current/**`)

| Module | Change |
|--------|--------|
| `decision_engine.py` | `ACTION_TO_STATE` → new 12-way `ActionTarget` |
| `learning/environment.py` | `SC2Env.action_space` = `Discrete(12)` |
| `learning/rules_policy.py` | `rule_actions_for_batch` returns 12-way |
| `learning/features.py` | No change |
| `learning/imitation.py` | DB re-labeling migration |
| `learning/database.py` | `action_space_version` column |
| `learning/checkpoints.py` | Reject mismatched-action-space loads unless `--force` |
| `data/diagnostic_states.json` | Expected-action expands to 12-way |

Cross-version concern is gone: `v0` / `v1` keep their 6-way space
forever; the new `vN+1` uses 12-way. Self-play just works because each
subprocess loads its own stack.

## 5. Tests

- `tests/test_autoreg_policy.py` — two-head forward pass, target head
  gated by strategic head, gradients flow to both.
- `tests/test_action_migration.py` — 6-way DB migrates cleanly; old
  checkpoints rejected explicitly.

## 6. Validation

Replay inspection shows meaningful target differentiation (not always
`enemy_main`) AND win rate holds AND Elo gain ≥ +10 over 20 games.

## 7. Gate

All three validation criteria.

## 8. Kill criterion

Target head collapses to single mode. May indicate insufficient signal —
Phase F might be needed first. Mark phase "deferred pending F".

## 9. Rollback

Delete promoted `vN`; prior versions unaffected by design.
