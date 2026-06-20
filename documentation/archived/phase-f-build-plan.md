# Phase F Build Plan — Entity transformer encoder

**Parent plan:** [alpha4gate-master-plan.md](alpha4gate-master-plan.md) — Phase F
**Track:** Capability-F (deferred)
**Prerequisites:** Phases A, B, E merged and promoted. D preferred.
**Status:** Deferred. Only enter if B–E have all landed and the bot is clearly
bottlenecked by loss of per-unit information. Detail extracted from the
master plan on 2026-04-19 as part of the plan/build-doc cleanup.
**Effort estimate:** ~2 weeks.

## 1. What this feature does

Replaces the current scalar feature trunk with a small transformer
encoder that takes a **variable-length list of units** (own + visible
enemy) and embeds each unit's per-unit features. The transformer output
is concatenated with the existing scalar features and fed to the MLP
trunk.

Today's observation collapses each unit class to a count
(post-Phase-B histogram). That throws away per-unit information:
health, shields, position, individual unit type, cloak state. A
transformer over per-unit features preserves that information so the
policy can attend to the right unit at the right moment.

## 2. Existing context

- **Phase B** (post-promotion) has expanded the observation to ~47
  scalar slots including unit-type histograms.
- **Phase E** (post-promotion) has restructured the action space to
  `(strategic_state, target)` — important for "attend to which unit"
  decisions.
- **`bots/current/learning/features.py`** — current
  `BaseFeaturesExtractor` consumes a fixed-shape vector. A transformer
  needs a different extractor that takes a variable-length list with
  pad/mask.
- **`v0_pretrain.zip`** — imitation pretrain checkpoint cannot be
  reused because the input shape is different. Phase F needs a new
  `v0_pretrain_transformer.zip` from a fresh imitation run.

## 3. Scope (build steps)

| Step | Description |
|------|-------------|
| F.1 | Custom `BaseFeaturesExtractor` taking variable-length unit list with pad/mask. |
| F.2 | Transformer: 2 layers, 4 heads, 64-dim embedding, 128-dim FFN. ~100k params. `torch.nn.TransformerEncoder`, no new dep. |
| F.3 | Per-unit features: unit_type (embedding), health_pct, shield_pct, is_own, is_flying, is_cloaked, position_relative_to_main. |
| F.4 | Integrate via feature concat: transformer output + existing scalar features → MLP trunk. |
| F.5 | Train from scratch in a new `bots/vN+1/` — cannot use `vN`'s `v0_pretrain`. Build `v0_pretrain_transformer` via fresh imitation run. |
| F.6 | A/B against the best B/E version. |

## 4. Tests

- `tests/test_entity_transformer.py` — variable unit counts, pad/mask
  correctness, gradient flow.
- `tests/test_imitation_transformer.py` — fresh pretrain builds + loads.

## 5. Validation

Beat best B/E version by ≥ 5% win rate over 20 games at difficulty 3
AND show lower loss variance AND Elo gain ≥ +20 over 20 self-play
games.

## 6. Gate

All three validation criteria.

## 7. Kill criterion

Training diverges (NaN, policy collapse) OR no win-rate improvement
after the full 2-week build. Scalar histogram was sufficient — delete
the transformer version; keep A–E promoted versions.

## 8. Rollback

Versioning makes this trivial: `rm -rf bots/vN+1/` where N+1 was the
transformer version. Prior stacks untouched.

## 9. Compute concern

Single Windows 11 box, CPU-only PyTorch. The 2-layer 100k-param
transformer adds load but should not exceed 2× baseline cycle
wall-clock. If it does, that's F's kill signal — see master plan
"Compute target".
