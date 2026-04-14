# Alpha4Gate — AlphaStar-Inspired Upgrade Plan

## Source

Derived from analysis of the AlphaStar paper (Vinyals et al. 2019, *Grandmaster
level in StarCraft II using multi-agent reinforcement learning*) against the
Alpha4Gate baseline (SB3 PPO, 2×128 MLP, `Discrete(6)` strategic actions, 24-dim
scalar obs, built-in-AI opponent only).

AlphaStar's 32-TPU × 44-day × 12-agent league setup is not portable; the phases
below are the subset whose ideas transfer to single-box training.

## Vision

Close the gap between a Markovian strategic-action PPO baseline and an
AlphaStar-shaped architecture — **where that gap can be closed cheaply**. The
goal is not to reproduce AlphaStar; it is to break through the "stuck at
difficulty 4–5" ceiling by adopting the paper's ideas that have the best
effort-to-impact ratio at single-box scale.

## Principles

- **Validate before compounding.** Every phase has a go/no-go gate. No
  architecture is stacked on top of unvalidated changes.
- **Cheapest lever first.** Obs signal and training regime changes beat
  architectural rewrites in expected value at our scale.
- **Kill criteria are sacred.** If a phase's expected benefit fails to
  materialize, stop — don't double down by adding more complexity from the
  same family.
- **Imitation is the backbone.** `v0_pretrain` (see Glossary) is the cold-start
  for every RL run. Phases that break this contract need extra justification.
- **Append-only observations.** Feature slots are stable once assigned. New
  features are appended; old rows upgrade via zero-padding. See "Obs-dim
  invariant" below.

## Glossary

Terms used throughout this plan, defined once. A fresh model reading this doc
should not need prior session context.

| Term | Definition |
|------|-----------|
| **`v0_pretrain`** | Imitation-pretrained PPO checkpoint stored at `data/checkpoints/v0_pretrain.zip`. Behavior-cloned from rule-based decisions in `training.db`. Default starting point for RL runs when `use_imitation_init=true` in `hyperparams.json`. |
| **KL-to-rules** | Auxiliary loss term introduced in the `feat/lstm-kl-imitation` PR. After each PPO gradient step, one extra pass over the rollout buffer applies `kl_rules_coef * CE(policy_logits, rule_engine_action)` to keep the learned policy close to the rule baseline. Disabled at `kl_rules_coef=0.0`. |
| **Padding trick** | When observation width grows (e.g., adding new feature slots), DB rows stored at the old width are extended with zeros to match the new width. Introduced in the imitation PR so old 17-dim transitions pad to 24-dim. Only works for **appends**; reordering or replacing slots breaks it. |
| **PFSP** | Prioritised Fictitious Self-Play (AlphaStar Methods §PFSP). Sample training opponents from a pool with weight proportional to difficulty, concentrating learning signal on harder opponents. Phase C implements a simplified "PFSP-lite" variant. |
| **`_FEATURE_SPEC`** | The tuple list in `src/alpha4gate/learning/features.py` defining name and normalization divisor for each observation slot. Single source of truth for obs width. |
| **`_compute_next_state`** | Rule-engine method in `src/alpha4gate/decision_engine.py` that maps a `GameSnapshot` to a `StrategicState`. Used by the KL-to-rules teacher. |
| **`ACTION_TO_STATE`** | The canonical list in `decision_engine.py` mapping PPO action indices to `StrategicState` values. Single source of truth for action space; Phase 4.5 findings F6/F9 flagged drift here as a recurring bug. |
| **`TrainingOrchestrator`** | Top-level training driver in `src/alpha4gate/learning/trainer.py`. Manages cycles, checkpoints, curriculum. |
| **`improve-bot-advised`** | Long-running autonomous improvement skill at `.claude/skills/improve-bot-advised/SKILL.md`. Observes games, analyzes, picks one improvement, validates. This plan replaces ad-hoc skill runs with phased architecture work. |
| **Diagnostic states** | Fixed observation vectors at `data/diagnostic_states.json` used to log action probabilities each training cycle to `data/training_diagnostics.json`. Regression canary for policy changes. |

## How to read this plan

Each phase has:

- **Goal** — what question the phase answers
- **Prerequisites** — earlier phases that must be green first
- **Scope** — code changes as a step table (runnable via `/build-step`)
- **Tests** — unit test files to create/update
- **Effort** — rough wall-clock for one developer
- **Validation** — experimental protocol to call it "done"
- **Gate** — pass condition to proceed
- **Kill criterion** — fail-and-stop condition
- **Rollback** — how to undo the phase if it's retroactively regretted

## Execution mode

This plan is **human-led with per-phase automation**. It is **not** designed
for a single `/build-phase` autonomous run because individual phases take days
to weeks, span multiple real-time SC2 training runs, and each gate requires
human judgment on noisy win-rate signals.

Expected per-phase execution:

1. Cut branch `alphastar/<letter>/<name>` from master
2. Tag `alphastar/<letter>/baseline` before first commit
3. Execute steps via `/build-step` where possible, manual edits otherwise
4. Run validation protocol (usually an `improve-bot-advised` soak)
5. On gate pass: merge to master, tag `alphastar/<letter>/final`
6. On kill: abandon branch, log outcome in the phase's "Kill criterion" section
   of this plan, move to next phase

## Obs-dim invariant

**Rule:** feature slots are **append-only** once assigned. Never reorder, never
replace, never remove.

This is the load-bearing assumption behind the padding trick. Any phase that
wants to change existing slot semantics must instead:

1. Keep the old slot (write zero, treat as deprecated)
2. Add the new semantics as an appended slot
3. Bump a commented `FEATURE_DIM_V<N>` marker in `features.py`
4. Document the retired slot in this plan's history section

Phase F (transformer encoder) is the sole allowed exception — it replaces the
scalar-concatenation path wholesale, which is why it also requires building a
fresh `v0_pretrain_transformer` from scratch.

## Branch and rollback strategy

| Event | Action |
|-------|--------|
| Start of Phase X | `git checkout -b alphastar/X/<name>` off master; `git tag alphastar/X/baseline` |
| Phase passes gate | `git checkout master && git merge alphastar/X/<name>`; `git tag alphastar/X/final`; push tags |
| Phase fails / killed | `git checkout master`; leave branch unmerged; log outcome in this plan |
| Post-merge regret | `git revert $(git merge-base alphastar/X/final master)..alphastar/X/final` — or reset to previous phase's final tag and cherry-pick subsequent merges |

Every phase must leave master shippable. Phase baselines are the restore points.

## Resume after interruption

If a phase is interrupted mid-execution (context exhaustion, system crash,
whatever):

1. `git status` on the phase branch — confirm working tree state
2. Read the phase section below and identify the last completed step
3. Check `data/checkpoints/manifest.json` for the last saved checkpoint
4. Look for an open issue on the milestone (if `/repo-sync` has run) for the
   phase — comment thread often captures interrupt context
5. Resume from the next step; do not rewind unless tests regressed

## Compute target

Single Windows 11 box, CPU-only PyTorch training (no CUDA currently wired).
Phase F's transformer adds load — if CPU training exceeds 2x baseline cycle
wall-clock, that's the kill signal for F. GPU support is explicitly out of
scope for this plan.

## Baseline (as of 2026-04-13)

- **Policy:** SB3 `MlpPolicy`, 2×128 MLP, pure on-policy PPO
- **Observation:** 24-dim scalar (17 game + 7 advisor)
- **Action:** `Discrete(6)` strategic states
- **Training:** vs built-in AI only, no self-play
- **Win rate:** 75%+ at difficulty 3 (from improve-bot-advised run 6),
  struggles at difficulty 4–5
- **Pending PR:** `feat/lstm-kl-imitation` branch (commit 498f405) adds
  LSTM (`MlpLstmPolicy`), KL-to-rules auxiliary loss, and imitation-init.
  **Not yet validated.** See Phase A.

---

## Phase A: Validate the pending PR

**Goal:** Prove the LSTM + KL-to-rules + imitation-init patch does not regress
(and ideally improves) baseline performance before anything else is layered on.

**Prerequisites:** none (first phase).

**Status:** Blocks all subsequent phases.

### Scope

| Step | Description |
|------|-------------|
| A.0 | Checkout `feat/lstm-kl-imitation` branch; confirm 834 unit tests pass |
| A.1 | **No-op regression** — all new flags at safe defaults (as shipped in the PR's `data/hyperparams.json`). Command below. Confirms patch is a true no-op when off |
| A.2 | **Imitation init alone** — flip `use_imitation_init: true`, run with `--ensure-pretrain`. Verify `v0_pretrain.zip` is created and loaded on next cycle |
| A.3 | **KL-to-rules alone** — flip `kl_rules_coef: 0.1`, keep `MlpPolicy`. Verify no NaN / crash and extra-pass overhead is bounded |
| A.4 | **LSTM alone** — flip `policy_type: MlpLstmPolicy`, move existing checkpoints aside (architecture-incompatible). Watch for `net_arch` dict-shape error flagged in PR notes |
| A.5 | **All three together** — full stack on |
| A.6 | **Validation soak** — 20 games at difficulty 3 with `--decision-mode hybrid`, compare win rate to 75% baseline |

### Testing procedure (PowerShell, inline)

Each step assumes the prior has passed. Do not batch — the purpose is to
isolate regressions.

**A.1 — no-op regression**

```powershell
uv run python -m alpha4gate.runner --train rl --cycles 1 --games-per-cycle 3 --difficulty 3
```

Pass: cycle completes, checkpoint saves, win-rate logged. Fail: any import /
shape / class-mismatch error from the dispatch logic in `_init_or_resume_model`.

**A.2 — imitation init alone**

```powershell
(Get-Content data/hyperparams.json) -replace '"use_imitation_init": false', '"use_imitation_init": true' | Set-Content data/hyperparams.json
uv run python -m alpha4gate.runner --train rl --cycles 1 --games-per-cycle 3 --difficulty 3 --ensure-pretrain
```

Pass: log shows `--ensure-pretrain: running imitation training` → `agreement=X.XXX`
→ `Loading imitation-pretrained checkpoint v0_pretrain` before cycle 1.
`Test-Path data/checkpoints/v0_pretrain.zip` returns True.

Re-run without `--ensure-pretrain` to confirm idempotence:

```powershell
uv run python -m alpha4gate.runner --train rl --cycles 1 --games-per-cycle 3 --difficulty 3
```

Pass: log shows `Loading imitation-pretrained checkpoint v0_pretrain` without re-running imitation.

**A.3 — KL-to-rules alone**

```powershell
(Get-Content data/hyperparams.json) `
  -replace '"use_imitation_init": true', '"use_imitation_init": false' `
  -replace '"kl_rules_coef": 0.0', '"kl_rules_coef": 0.1' `
  | Set-Content data/hyperparams.json

uv run python -m alpha4gate.runner --train rl --cycles 2 --games-per-cycle 3 --difficulty 3
```

Pass: no crash, cycle wall-clock ≤ 1.5× the Step A.1 wall-clock (extra pass
overhead is bounded). Optional check: `data/training_diagnostics.json`
probabilities on diagnostic states should drift toward the rule-engine's
choice across cycles.

**A.4 — LSTM alone**

```powershell
(Get-Content data/hyperparams.json) `
  -replace '"kl_rules_coef": 0.1', '"kl_rules_coef": 0.0' `
  -replace '"policy_type": "MlpPolicy"', '"policy_type": "MlpLstmPolicy"' `
  | Set-Content data/hyperparams.json

# LSTM checkpoint is incompatible with prior MlpPolicy checkpoints
Move-Item data/checkpoints data/checkpoints.bak-pre-lstm

uv run python -m alpha4gate.runner --train rl --cycles 2 --games-per-cycle 3 --difficulty 3
```

Pass: env loop runs, hidden state threads through, cycles complete. Known
failure mode: if `net_arch: [128, 128]` flat-list isn't valid for
`MlpLstmPolicy`, model construction crashes. Fix is to change `net_arch` in
hyperparams to `{"pi": [128], "vf": [128]}` — update the plan history below if
hit.

**A.5 — all three together**

```powershell
(Get-Content data/hyperparams.json) `
  -replace '"use_imitation_init": false', '"use_imitation_init": true' `
  -replace '"kl_rules_coef": 0.0', '"kl_rules_coef": 0.1' `
  | Set-Content data/hyperparams.json

uv run python -m alpha4gate.runner --train rl --cycles 3 --games-per-cycle 3 --difficulty 3 --ensure-pretrain
```

**A.6 — validation soak**

```powershell
uv run python -m alpha4gate.runner --batch 20 --difficulty 3 --decision-mode hybrid `
  --model-path data/checkpoints/best.zip
```

### Known-failure diagnostic table

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `lstm_states` / `RNNStates` error at model build | `net_arch: [128,128]` flat, LSTM wants dict | Change to `{"pi":[128],"vf":[128]}` |
| `cross_entropy` NaN during KL pass | coef too high or obs decode broken | Drop to 0.05; probe `rule_actions_for_batch` output |
| Imitation agreement stuck ≈ 0.17 (1/6) | DB transitions empty or all one action | `uv run python -c "from alpha4gate.learning.database import TrainingDB; db=TrainingDB('data/training.db'); print(db.get_transition_count())"` |
| Trainer loads wrong class on resume | Pre-existing checkpoint from different `policy_type` | Clear `data/checkpoints/` or start fresh dir |

### Tests

Unit tests for the patch already exist:
- `tests/test_rules_policy.py`
- `tests/test_ppo_kl.py`
- `tests/test_imitation_init.py`
- `tests/test_imitation.py` (updated)

No new tests required for Phase A — this phase is runtime validation.

### Effort

~1 day (mostly SC2 wall-clock).

### Validation

At least one of the flag combos in A.2–A.5 produces win-rate ≥ baseline over
20 validation games at difficulty 3 (A.6).

### Gate

`(combo_passed & no_crashes & tests_green) → merge branch to master`.

### Kill criterion

All four configurations regress >10% win rate vs baseline. In that case,
investigate root cause before proceeding — likely candidates:

- Stateless rule-teacher is too lossy (see `rules_policy.py` "TRADE-OFF"
  block in the PR); upgrade to option (b) from the PR conversation (record
  `rule_action` per step in `SC2Env`)
- Imitation padding introduces distribution shift that overrides PPO learning
  signal; disable padding and use `BASE_GAME_FEATURE_DIM`-only obs for RL

### Rollback

Branch is unmerged until gate passes, so "rollback" is simply `git checkout master`.
Post-merge regret: `git revert` the merge commit.

---

## Phase B: Unit-type histogram observation expansion

**Goal:** Answer "is observation signal the binding constraint on win rate?"

**Prerequisites:** Phase A merged to master.

### Scope

| Step | Description |
|------|-------------|
| B.1 | Append ~15 own-army unit-type count slots to `_FEATURE_SPEC`: Zealot, Stalker, Sentry, Immortal, Colossus, Archon, HighTemplar, DarkTemplar, Phoenix, VoidRay, Carrier, Tempest, Disruptor, WarpPrism, Observer. Normalization divisors: 40 for worker-class, 20 for core army |
| B.2 | Append ~8 enemy-unit-seen count slots driven by scouting memory: enemy_zealot_seen, enemy_marine_seen, enemy_zergling_seen, enemy_roach_seen, enemy_air_seen, enemy_tech_structure_seen, etc. |
| B.3 | Bump `FEATURE_DIM` and add `FEATURE_DIM_V2` marker. Verify imitation padding path in `imitation.py` handles the new width |
| B.4 | Add diagnostic-state entries covering typical mid-game unit compositions to `data/diagnostic_states.json` |
| B.5 | Train 2 cycles from `v0_pretrain`, compare to Phase A end-state |

### Tests

- `tests/test_features_v2.py` — new: assert new slots produce expected values for synthetic snapshots; confirm old 17-dim DB rows still round-trip via padding
- `tests/test_imitation.py` — update: padding test should cover 17 → V2 width

### Effort

~1 day + cycle wall-clock.

### Validation

Win rate at difficulty 3 stays equal-or-better across 20 games. Bonus signal:
diagnostic probabilities respond meaningfully to army composition.

### Gate

Any measurable improvement vs Phase A end-state (noise threshold: ±2 wins per
20 games = ±10%).

### Kill criterion

No improvement after 3 cycles → observation signal is not the bottleneck.
Skip to Phase C.

### Rollback

`git revert` the merge commit. Feature slots stay deprecated-zero rather than
removed (append-only invariant).

---

## Phase C: Poor-man's self-play (opponent pool)

**Goal:** Break out of the "only beats built-in AI" ceiling by training
against frozen versions of the bot itself.

**Prerequisites:** Phase A merged to master. Phase B optional — Phase C can run
against either Phase A or Phase B as baseline.

### Scope

| Step | Description |
|------|-------------|
| C.0 | **Spike** — see pass criteria below |
| C.1 | `src/alpha4gate/learning/opponent_pool.py` — opponent registry + sampler |
| C.2 | Sampling weight: `w_i ∝ (1 - win_rate_vs_opponent_i)^2`; cold start is uniform |
| C.3 | Wire into `TrainingOrchestrator`: each game picks an opponent via the pool |
| C.4 | DB schema — see "DB schema" below |
| C.5 | Dashboard surface **(nice-to-have, defer unless pool is hard to debug otherwise)**: opponent-pool tab with win-rate matrix |
| C.6 | Train 5 cycles, evaluate |

### C.0 Spike pass criteria

**Pass iff all of the following within a single 30-minute session:**

1. Two PPO policies (loaded from different checkpoints) can be instantiated in
   the same Python process without state collision
2. An `SC2Env`-like harness runs one full SC2 match where both sides are
   bot-controlled, neither side is the built-in AI
3. The match reaches a terminal state (win / loss / draw) within 5 real-time
   minutes
4. Final reward is correctly attributed to the bot-under-training (not the
   frozen opponent)

**Fail** if any of the above doesn't happen within 30 minutes, OR if the SC2
API rejects two bot instances per match. On fail, defer Phase C, do Phase D
next, and revisit the spike once a specific workaround is identified.

### C.4 DB schema

New table in `training.db`:

```sql
CREATE TABLE opponent_matches (
    game_id TEXT NOT NULL,
    opponent_name TEXT NOT NULL,  -- 'v0_pretrain', 'checkpoint_N-5', 'built_in_diff_3', etc.
    outcome TEXT NOT NULL,         -- 'win' | 'loss' | 'draw'
    duration_secs REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);
CREATE INDEX idx_opponent_matches_opponent ON opponent_matches (opponent_name);
```

Sampling query (pseudocode):

```sql
SELECT opponent_name,
       1.0 - (SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) * 1.0 / COUNT(*)) AS inverse_win_rate
FROM opponent_matches
WHERE opponent_name = ?
GROUP BY opponent_name;
```

Weight is `inverse_win_rate^2`, normalized across the pool. Cold start (zero
rows for an opponent) returns 0.5.

### Tests

- `tests/test_opponent_pool.py` — sampling weights normalize, cold-start returns
  uniform, win-rate-0 opponent gets zero weight, win-rate-1 opponent gets max weight
- `tests/test_training_db_opponent.py` — new table migration is idempotent, FK integrity holds

### Effort

~1 week. C.0 spike determines feasibility.

### Validation

5-cycle training improves win rate at difficulty 4–5 on evaluation vs built-in
AI — the "break the ceiling" test.

### Gate

Difficulty-4 win rate ≥ 40% over 20 eval games.

### Kill criterion

C.0 spike fails → defer phase, proceed to D. Post-spike kills: 5-cycle training
shows cycling pathology (rock-paper-scissor, AlphaStar Figure 3D) — detected as
oscillating wins-vs-frozen-pool with no upward trend.

### Rollback

Branch is unmerged on kill; post-merge revert is clean because the feature is
gated by an off-by-default flag in hyperparams.

---

## Phase D: Build-order z-statistic (reward refactor)

**Goal:** Collapse implicit build-order reward rules into an explicit `z`
target vector with edit-distance pseudo-reward, matching AlphaStar's
supervised statistic approach.

**Prerequisites:** Phase A merged. Phases B and C may be done or not — D is
orthogonal.

### Scope

| Step | Description |
|------|-------------|
| D.1 | Audit `data/reward_rules.json`: tag each of the ~50 rules as (a) build-order timing, (b) tactical/combat, (c) economy, (d) other. Edge-case rules (both timing and tactical) tag with the **primary** purpose and keep in both systems until migration is validated |
| D.2 | Define `z` schema: `data/build_orders/<label>.json` = `{"name": str, "targets": [{"action": str, "time_seconds": int, "weight": float}], "tolerance_seconds": int}` |
| D.3 | `src/alpha4gate/learning/build_order_reward.py` — edit-distance between executed and target. Per-step reward = `-α * edit_distance_delta` |
| D.4 | Migrate category (a) rules from `reward_rules.json` into build-order files; keep (b)(c)(d) as shaped rewards |
| D.5 | Append `z` identifier as an optional observation slot (so policy can condition on the chosen build). Append-only, per obs-dim invariant |
| D.6 | Backwards-compat: existing `reward_rules.json` continues to work; `z` is additive. Flag `use_build_order_reward: false` default |
| D.7 | Train 3 cycles, measure early-game reward variance and win rate |

### Tests

- `tests/test_build_order_reward.py` — edit distance correctness, reward monotonic in progress, handles empty target list
- `tests/test_reward_migration.py` — pre/post migration reward totals on known game logs agree within 5%

### Effort

~3 days. Audit (D.1) is most of the work.

### Validation

Early-game (first 5 min) reward std-dev drops by ≥ 30% across games; win rate
at difficulty 3 holds or improves. Ablate by running with `z` disabled to
confirm the reward refactor itself didn't regress.

### Gate

Win rate steady + reward variance reduced.

### Kill criterion

Reward variance does not drop after migration — old rules were already
capturing build-order variance. Keep the migration (it's a cleanup win even
without the variance reduction) but skip the `z`-as-obs wiring.

### Rollback

`reward_rules.json` migration is file-backup-able (`reward_rules.pre-phase-d.json`).
Restore backup on kill.

---

## Phase E: Autoregressive action head

**Goal:** Unlock tactical variety by making ATTACK / EXPAND conditional on
target choice rather than flat `Discrete(6)`.

**Prerequisites:** Phase A merged. Ideally Phase C done first (self-play gives
more interesting ATTACK decisions to differentiate).

### Scope

| Step | Description |
|------|-------------|
| E.1 | Define structured action space: `(strategic_state, target)` — ATTACK → {main, natural, third}, EXPAND → {own_natural, own_third, own_fourth}, others unchanged. Effective size = 12 |
| E.2 | Custom `ActorCriticPolicy` subclass with two sequential softmax heads: `p(strategic)` then `p(target \| strategic)`. Target head conditioned on strategic embedding |
| E.3 | DB versioning — see "Impact matrix" below |
| E.4 | Rule engine update: `_compute_next_state` also picks a target when returning ATTACK/EXPAND. Cascades into `rules_policy.py` KL teacher |
| E.5 | Validation: replay inspection for target differentiation |

### Impact matrix

Phase E changes the action space. Every module coupled to action-space shape
must be updated in the same PR:

| Module | Change |
|--------|--------|
| `src/alpha4gate/decision_engine.py` | `ACTION_TO_STATE` → new 12-way enum `ActionTarget` combining state + target |
| `src/alpha4gate/learning/environment.py` | `SC2Env.action_space` = `Discrete(12)` |
| `src/alpha4gate/learning/rules_policy.py` | `rule_actions_for_batch` returns 12-way indices |
| `src/alpha4gate/learning/features.py` | No change — obs unchanged |
| `src/alpha4gate/learning/imitation.py` | DB 6-way rows need re-labeling; add migration that infers target from game log when possible, else defaults to `*_main` |
| `src/alpha4gate/learning/database.py` | Add `action_space_version INT` column to transitions table; `1` for legacy, `2` for Phase E |
| `src/alpha4gate/learning/checkpoints.py` | Load rejects checkpoints with mismatched action space unless explicitly requested |
| `data/diagnostic_states.json` | Expected-action field expands to 12-way |
| `tests/test_rules_policy.py` | Assertions for 12-way output |
| `tests/test_imitation.py` | Migration round-trip test |

### Tests

- `tests/test_autoreg_policy.py` — two-head forward pass, target head gated
  by strategic head, gradient flows correctly to both
- `tests/test_action_migration.py` — 6-way DB rows migrate cleanly to 12-way;
  old checkpoints load rejection is explicit

### Effort

~1 week. The custom policy subclass is the hardest part.

### Validation

Replay-level inspection shows meaningful target differentiation (not always
`enemy_main`). Win rate holds or improves.

### Gate

Qualitative replay inspection passes + quantitative win rate holds.

### Kill criterion

Target head collapses to a single mode (always picks `enemy_main` regardless
of game state). May indicate insufficient signal — Phase F might be needed
before E can work. Mark phase as "deferred pending F".

### Rollback

Checkpoint compatibility break means this phase cannot be cleanly reverted
once a V2 checkpoint is promoted to `best`. Mitigation: keep the last V1
checkpoint pinned as `last_v1_baseline.zip` on master during E's branch life.

---

## Phase F: Entity transformer encoder

**Goal:** Replace scalar unit histograms with a transformer over the unit list
(AlphaStar's observation architecture).

**Status:** Deferred. Only enter this phase if Phases B–E have all landed and
the bot is clearly bottlenecked by the loss of per-unit information.

**Prerequisites:** Phases A, B, C, E merged. Phase D preferred but not required.

### Scope

| Step | Description |
|------|-------------|
| F.1 | Custom `BaseFeaturesExtractor` taking variable-length unit list with pad/mask |
| F.2 | Transformer: 2 layers, 4 heads, 64-dim embedding, 128-dim FFN. ~100k params. Use `torch.nn.TransformerEncoder` (stdlib) — no new dependency |
| F.3 | Per-unit features: unit_type (embedding), health_pct, shield_pct, is_own, is_flying, is_cloaked, position_relative_to_main |
| F.4 | Integrate via feature concat: transformer output + existing scalar features → MLP trunk |
| F.5 | Train from scratch — cannot use `v0_pretrain` (arch incompatible). Build `v0_pretrain_transformer` via fresh imitation run |
| F.6 | A/B against Phase E end-state |

### Tests

- `tests/test_entity_transformer.py` — forward pass with variable unit counts,
  padding mask correctness, gradient flow through transformer
- `tests/test_imitation_transformer.py` — fresh `v0_pretrain_transformer` builds
  and loads

### Effort

~2 weeks.

### Validation

Must beat Phase E win rate by ≥ 5% over 20 A/B games at difficulty 3, AND show
lower loss variance during training. Otherwise roll back — the scalar
histogram was sufficient.

### Gate

The 5% improvement + variance reduction.

### Kill criterion

Training diverges (NaN loss, policy collapse) OR no win-rate improvement after
the full 2-week build. Scalar histogram was sufficient — delete Phase F, keep
A–E.

### Rollback

Phase F is a breaking arch change. Rollback is `git revert`; `v0_pretrain`
(the non-transformer version) must be preserved during Phase F's life as the
restore point.

---

## Decision graph

```
Phase A (validate PR) ─── GATE: at least one config ≥ baseline
    │
    └─ pass ──→ Phase B (unit histogram)
                    │
                    ├─ gain ─────────→ Phase C or D (operator picks)
                    └─ no gain ──────→ Phase C (training regime, not obs)
    
Phase C (self-play) ─or─ Phase D (z-statistic)
    │
    └─ both done ──→ Phase E (autoreg actions)
                         │
                         └─ done + still hungry ──→ Phase F (transformer)
```

## Time budget

| Phase | Optimistic | Realistic | Pessimistic |
|-------|-----------|-----------|-------------|
| A | 0.5 day | 1 day | 2 days (if all configs regress) |
| B | 1 day | 1–2 days | 3 days (if DB migration hits edge cases) |
| C | 4 days | 1 week | 2 weeks (if bot-vs-bot plumbing fails) |
| D | 2 days | 3 days | 1 week (if rule audit reveals tangled dependencies) |
| E | 1 week | 1 week | 2 weeks (if SB3 policy override is painful) |
| F | 1.5 weeks | 2 weeks | 3 weeks (if training destabilizes) |
| **Sub-total** | **~3 weeks** | **~5–6 weeks** | **~9–10 weeks** |
| **+ 20% integration buffer** | +0.6 week | +1.2 weeks | +2 weeks |
| **Total** | **~3.5 weeks** | **~6–7 weeks** | **~11–12 weeks** |

Integration buffer covers: validation soaks coming in noisier than expected,
cross-phase fix-ups surfaced during later phases, and `/improve-bot-advised`
runs to re-establish baselines between phases.

## What's NOT in this plan (deliberately)

- **League training** (AlphaStar's main-exploiter / league-exploiter split).
  Requires multi-agent training orchestration at scale. Phase C's opponent-pool
  approach captures ~30% of the benefit at ~5% of the cost.
- **V-trace / UPGO** off-policy corrections. SB3 PPO is fine at single-box
  scale. Would matter if we had distributed actors.
- **256×256 spatial map inputs.** Strategic action space doesn't need pixels —
  scalar + unit-list observations cover the signal.
- **44 days of compute.** Alpha4Gate runs in cycles, not continuously. The
  `improve-bot-advised` skill is our analogue to AlphaStar's automated training.
- **Docker / containerized training.** Bare Windows per project conventions
  (see root `CLAUDE.md`). GPU-on-Docker on Windows is a separate project.
- **Distributed actors.** Single Python process, single SC2 client, single
  game at a time. Parallelism is out of scope.

## Tracking

Once Phase A completes and the branch merges, convert each subsequent phase
into a GitHub issue via `/repo-sync`. Milestone: `alphastar-upgrade`. Use the
issue threads to capture interrupt-resume context (see "Resume after
interruption" above).

## Plan history

Space for recording phase outcomes, killed phases, and scope changes
discovered during execution. Append-only — do not edit prior entries.

- *2026-04-13* — plan drafted; PR `feat/lstm-kl-imitation` (498f405) awaiting
  Phase A validation
