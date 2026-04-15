# Alpha4Gate — Master Upgrade Plan (AlphaStar + Versioning)

## Source

This plan merges two predecessors, both archived under `documentation/archived/`:

- `alphastar-upgrade-plan.md` — AlphaStar-inspired PPO upgrades (LSTM, obs
  expansion, self-play, z-statistic, autoregressive actions, transformer).
- `bot-versioning-selfplay-plan.md` — full-stack bot versioning with subprocess
  self-play and Elo ladder (the "big-box" approach).

The plans had one major overlap: **self-play**. The AlphaStar plan's Phase C
(checkpoint-only opponent pool) is strictly weaker than the versioning plan's
full-stack approach, and it self-destructs as soon as later AlphaStar phases
change the feature spec, action space, or bot code. Phase C is deleted here;
its goal is delivered by the versioning infrastructure.

## Vision

Break the "stuck at difficulty 4–5" ceiling by:

1. Validating the pending `feat/lstm-kl-imitation` PR on the current stack.
2. Versioning the entire bot stack — every improvement snapshot is a
   self-contained `bots/vN/` directory that can be rehydrated and played
   against any other version via subprocess self-play.
3. Layering AlphaStar-inspired PPO upgrades (obs expansion, build-order z,
   autoregressive actions, transformer) as per-version improvements inside
   the sandbox, promoted by Elo gain vs prior versions.

The versioning substrate is the primary investment; AlphaStar capabilities
ride on top of it.

## Principles

- **Validate before compounding.** Every phase has a go/no-go gate. No
  architecture is stacked on top of unvalidated changes.
- **Cheapest lever first.** Obs signal and training regime changes beat
  architectural rewrites in expected value at our scale.
- **Kill criteria are sacred.** If expected benefit fails to materialize,
  stop — don't double down by adding more complexity from the same family.
- **Versions are full stacks.** Each `bots/vN/` is a self-contained Python
  package (bot code, learning pipeline, API, reward engine, checkpoint,
  per-version data dir). A version is deleted by `rm -rf bots/vN/`.
- **Orchestrator is the only frozen substrate.** Subprocess manager that
  defines bot-spawn + result-reporting contracts. Changes require human PR.
- **Subprocess-per-version is the only self-play mode.** No in-process
  fallback. Clean isolation beats the small performance cost.
- **Self-play Elo is the truth signal.** SC2-AI win rate is secondary.
- **Imitation is the backbone.** `v0_pretrain` is the cold-start for every
  RL run inside a given version. Phases that break this contract need extra
  justification.
- **Versions are independent stacks.** No cross-version feature-spec or
  action-space invariants. Each `bots/vN/` can have its own obs width,
  policy class, reward rules. Append-only within a version is still good
  hygiene (supports the padding trick for imitation DB reuse) but is no
  longer a cross-version rule.

## Glossary

| Term | Definition |
|------|-----------|
| **`v0_pretrain`** | Imitation-pretrained PPO checkpoint at `<version>/data/checkpoints/v0_pretrain.zip`. Behavior-cloned from rule-based decisions in `<version>/data/training.db`. Per-version, not shared. |
| **KL-to-rules** | Auxiliary loss term from the `feat/lstm-kl-imitation` PR. After each PPO gradient step, an extra pass over the rollout buffer applies `kl_rules_coef * CE(policy_logits, rule_engine_action)`. Disabled at `kl_rules_coef=0.0`. |
| **Padding trick** | When a version's obs width grows, DB rows stored at the old width are zero-padded to match. Within-version only; not a cross-version contract. |
| **`_FEATURE_SPEC`** | Tuple list in `<version>/learning/features.py` defining obs slots. Single source of truth within a version. |
| **`_compute_next_state`** | Rule-engine method in `<version>/decision_engine.py` mapping `GameSnapshot` → `StrategicState`. KL-to-rules teacher. |
| **`ACTION_TO_STATE`** | Canonical list in `decision_engine.py` mapping PPO action indices to `StrategicState`. |
| **`bots/current/`** | Working copy the skill and dev tooling read from. Either a fork of `bots/vN/` or a fresh-cut working tree promoted to the next `vN+1` on snapshot. |
| **Orchestrator** | `src/orchestrator/` — subprocess manager, registry, snapshot, self-play, ladder. The only code the skill cannot touch. |
| **Bot-spawn contract** | Every `bots/vN/` must run via `python -m bots.vN --role {p1\|p2\|solo} --map ... --sc2-connect ... --result-out ... --seed ...`. |
| **Diagnostic states** | Fixed obs vectors at `<version>/data/diagnostic_states.json` logged each training cycle. Within-version regression canary. |
| **`improve-bot-advised`** | Autonomous improvement skill at `.claude/skills/improve-bot-advised/SKILL.md`. After this plan's Phase 5, it is sandboxed to `bots/current/**`. |
| **PFSP-lite** | Prioritised-fictitious-self-play sampling. `w_i ∝ (1 - win_rate_vs_opponent_i)²`, cold-start uniform. Lives inside `src/orchestrator/ladder.py` or `selfplay.py`. |

## How to read this plan

Each phase has:

- **Goal** — what question the phase answers
- **Track** — Validation / Versioning / Capability / Operational
- **Prerequisites** — earlier phases that must be green first
- **Scope** — code changes as a step table (runnable via `/build-step`)
- **Tests** — unit test files to create/update
- **Effort** — rough wall-clock for one developer
- **Validation** — experimental protocol to call it "done"
- **Gate** — pass condition to proceed
- **Kill criterion** — fail-and-stop condition
- **Rollback** — how to undo the phase if retroactively regretted

## Execution mode

Human-led with per-phase automation. Not a single `/build-phase` autonomous
run — individual phases take days to weeks, span multiple SC2 training runs,
and each gate requires human judgment on noisy win-rate / Elo signals.

Per phase:

1. Cut branch `master-plan/<letter>/<name>` from master.
2. Tag `master-plan/<letter>/baseline` before first commit.
3. Execute steps via `/build-step` where possible, manual otherwise.
4. Run validation protocol.
5. On gate pass: merge to master, tag `master-plan/<letter>/final`.
6. On kill: abandon branch, log outcome in the phase's kill-criterion section.

## Track structure

```
Track 1 — Validation   [Phase A]   on current src/alpha4gate/
Track 2 — Versioning   [0–5]       subprocess spike → move → registry → self-play → ladder → sandbox
Track 3 — Capability   [B, D, E]   per-version improvements inside bots/current/**
Track 4 — Capability-F [F]         deferred; only if B/D/E insufficient
Track 5 — Operational  [6]         autonomous self-improvement loop (needs 5 + at least one capability phase)
```

## Decision graph

```
Phase A (validate PR) ── gate: ≥ baseline WR → merge
    │
    └─ Phase 0 (subprocess spike) ── BLOCKING
            │
            ├─ pass ──→ Phase 1 (bots/v0/) ──→ Phase 2 (registry) ──→ Phase 3 (self-play) ──→ Phase 4 (ladder) ──→ Phase 5 (sandbox)
            │                                                                                                                │
            │                                                                                   ┌────────────┬────────────┬──┘
            │                                                                                   ▼            ▼            ▼
            │                                                                              Phase B       Phase D      Phase E
            │                                                                              (obs)         (z-stat)     (autoreg)
            │                                                                                   │            │            │
            │                                                                                   └────────────┴─ Phase 6 (loop) ── hungry? ──→ Phase F (transformer)
            │
            └─ fail ──→ escalate. Options: shared-process self-play (shrinks plan), or drop self-play entirely and keep versioning for manual curation.
```

## Baseline (as of 2026-04-15)

- **Policy:** SB3 `MlpPolicy`, 2×128 MLP, pure on-policy PPO
- **Observation:** 24-dim scalar (17 game + 7 advisor)
- **Action:** `Discrete(6)` strategic states
- **Training:** vs built-in AI only, no self-play
- **Win rate:** 75%+ at difficulty 3, struggles at 4–5
- **Pending PR:** `feat/lstm-kl-imitation` branch (commit 498f405) adds
  LSTM (`MlpLstmPolicy`), KL-to-rules auxiliary loss, imitation-init. **Not
  yet validated.** See Phase A.

---

## Phase A — Validate the pending PR

**Track:** Validation. **Goal:** Prove LSTM + KL-to-rules + imitation-init
does not regress baseline before anything else is layered on.

**Location:** runs on current `src/alpha4gate/` (pre-versioning).

**Prerequisites:** none.

### Scope

| Step | Description |
|------|-------------|
| A.0 | Checkout `feat/lstm-kl-imitation`; confirm 834 unit tests pass |
| A.1 | **No-op regression** — all flags at safe defaults. Confirms patch is a true no-op when off |
| A.2 | **Imitation init alone** — `use_imitation_init: true`, `--ensure-pretrain`. Verify `v0_pretrain.zip` created + loaded |
| A.3 | **KL-to-rules alone** — `kl_rules_coef: 0.1`, keep `MlpPolicy`. Verify no NaN, extra-pass overhead bounded |
| A.4 | **LSTM alone** — `policy_type: MlpLstmPolicy`. Watch `net_arch` dict-shape error |
| A.5 | **All three together** |
| A.6 | **Validation soak** — 20 games at difficulty 3, `--decision-mode hybrid`, compare vs 75% baseline |

### Testing procedure

See `documentation/archived/alphastar-upgrade-plan.md` Phase A section for
the full PowerShell command set (step-by-step with pass/fail criteria).
Those commands are unchanged; they run against current `src/alpha4gate/`.

### Known-failure diagnostic table

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `lstm_states` / `RNNStates` error at model build | `net_arch: [128,128]` flat, LSTM wants dict | Change to `{"pi":[128],"vf":[128]}` |
| `cross_entropy` NaN during KL pass | coef too high or obs decode broken | Drop to 0.05; probe `rule_actions_for_batch` |
| Imitation agreement ≈ 0.17 (1/6) | DB transitions empty or mono-action | `uv run python -c "from alpha4gate.learning.database import TrainingDB; print(TrainingDB('data/training.db').get_transition_count())"` |
| Trainer loads wrong class on resume | Pre-existing checkpoint from different `policy_type` | Clear `data/checkpoints/` or start fresh dir |

### Tests

Existing: `tests/test_rules_policy.py`, `tests/test_ppo_kl.py`,
`tests/test_imitation_init.py`, `tests/test_imitation.py`.
No new tests required — runtime validation phase.

### Effort

~1 day (mostly SC2 wall-clock).

### Validation

At least one of A.2–A.5 produces win-rate ≥ baseline over 20 validation
games at difficulty 3 (A.6).

### Gate

`(combo_passed & no_crashes & tests_green) → merge branch to master`.

### Kill criterion

All four configurations regress >10% win rate. Investigate root cause
before proceeding (see archived alphastar plan's Phase A for candidate
hypotheses).

### Rollback

Branch unmerged until gate passes. Post-merge regret: `git revert` the merge.

---

## Phase 0 — Subprocess self-play orchestration spike (BLOCKING)

**Track:** Versioning. **Goal:** Confirm two Python subprocesses can each
host a Bot and play each other headlessly on Simple64, coordinated by an
external orchestrator.

**Prerequisites:** Phase A merged (keeps current stack as the spike target).

### Scope

1. `scripts/spike_subprocess_selfplay.py` — launches two subprocesses from
   two minimal bot stubs, brokers SC2 connection, collects results.
2. Verify: both sides produce actions, game completes with a winner, no
   hangs, results file well-formed.
3. Investigate burnysc2's ladder / AI-arena protocol — confirm which
   mechanism lets two separate processes play. Document the approach in
   `documentation/wiki/subprocess-selfplay.md`.

### Effort

2 hours.

### Gate

All three spike deliverables pass within a single 30-min session.

### Kill criterion

Subprocess self-play requires unreasonable plumbing (custom SC2 replay
server, paid AI-Arena infra, etc.). Options:

- (a) Drop self-play entirely; keep versioning for manual curation (Elo
  replaced by SC2-AI difficulty ladder). Phases 3, 4, 6 shrink drastically.
- (b) Accept shared-process self-play with architecture constraints
  (revisit Phase C from the archived AlphaStar plan). This reintroduces
  the checkpoint-compatibility problem discussed in the merge rationale.

### Rollback

Spike script is throwaway; no state to undo.

---

## Phase 1 — Move full stack to `bots/v0/`; scaffold orchestrator

**Track:** Versioning. **Goal:** `src/alpha4gate/` becomes `bots/v0/`.
Orchestrator scaffolded. Everything works exactly as before, just loaded
via the registry.

**Prerequisites:** Phase 0 pass.

### Scope

1. Scaffold `src/orchestrator/` with `registry.py`, `contracts.py`,
   `snapshot.py`, `selfplay.py`, `ladder.py` stubs.
2. Move `src/alpha4gate/` → `bots/v0/` wholesale. Update imports
   (`from alpha4gate.bot import Bot` → `from bots.v0.bot import Bot`).
3. Entry-point wrapper: `python -m bots.v0` implements the bot-spawn
   contract.
4. Write `bots/v0/VERSION`, `bots/v0/manifest.json`.
5. Symlink or copy `bots/v0/` → `bots/current/` so dashboard, daemon,
   existing scripts keep working.
6. Update `pyproject.toml` entry points to include `bots/` and
   `orchestrator/`.
7. Update `runner.py` invocations in scripts to go through the
   orchestrator for spawning.
8. Per-version `data/` dir: `bots/v0/data/` holds training.db,
   checkpoints, reward_rules.json, hyperparams.json. Shared `data/`
   holds only cross-version state (ladder, selfplay results).

### Contracts (frozen under `src/orchestrator/contracts.py`)

**Bot-spawn:**

```
python -m bots.vN --role {p1|p2|solo} \
                  --map Simple64 \
                  --sc2-connect <protocol-arg> \
                  --result-out bots/vN/data/selfplay/<match-id>.json \
                  --seed <int>
```

**Result-reporting JSON:**

```json
{
  "version": "vN",
  "match_id": "...",
  "outcome": "win|loss|draw|crash",
  "duration_s": 612,
  "error": null
}
```

Breaking either contract requires a human PR.

### Risks

- **Import graph churn.** ~46 modules to rename. Largest mechanical task
  in the plan. 6–10h of careful refactor.
- **Daemon/API pathing.** Anything hardcoding `data/` or `src/alpha4gate/`
  must resolve relative to `bots/current/`.
- **Test imports.** `tests/` imports from `alpha4gate.*` — update to
  `bots.current.*` or a test-time alias.

### Tests

Full existing suite runs green against the moved tree. No new tests.

### Effort

8–12 h.

### Gate

- `uv run pytest` green (all 829 unit tests).
- Full SC2 game runs via `python -m bots.current`.
- Dashboard connects to `bots/current/` API and renders normally.

### Kill criterion

Import graph rewrite reveals hidden tight coupling that cannot be broken
without touching the orchestrator contract. Extend Phase 1 by one week
before escalating; if still blocked, redesign contracts before proceeding.

### Rollback

`git revert` the move commit. Entire suite lives on master untouched until
this phase is merged.

---

## Phase 2 — Registry + full-stack snapshot tool

**Track:** Versioning. **Prerequisites:** Phase 1.

### Scope

1. `src/orchestrator/registry.py`:
   - `list_versions() -> list[str]`
   - `get_version_dir(v) -> Path`
   - `get_manifest(v) -> Manifest`
2. `src/orchestrator/snapshot.py` + `scripts/snapshot_bot.py` CLI:
   - Copies `bots/current/` → `bots/vN/` (full tree, including
     `data/checkpoints/best.zip`).
   - Writes `VERSION`, `manifest.json` with parent, git SHA, timestamp,
     Elo snapshot, feature-dim and action-space fingerprints.
3. Registry CLI: `python -m orchestrator.registry list`,
   `python -m orchestrator.registry show vN`.
4. `bots/current/` semantics: working copy, either a fork of some
   `bots/vN/` or the pre-snapshot working tree. Snapshot promotes current
   to the next `vN+1` and re-forks current off of it.

### Tests

- `tests/test_registry.py` — list/get/manifest round-trip.
- `tests/test_snapshot.py` — snapshot produces self-contained tree,
  manifest fingerprints match source.

### Effort

3–4 h.

### Gate

- `scripts/snapshot_bot.py --name v1` produces a self-contained dir.
- `python -m bots.v1` boots and plays a game.
- `bots/v0/` and `bots/v1/` can both run, independently.

### Kill criterion

Snapshot tool cannot preserve all runtime state (e.g., checkpoint formats
break across Python restarts, or per-version data dirs leak). Fix with
explicit serialization discipline before proceeding.

### Rollback

`git revert`; orphan `bots/vN/` dirs are safely deletable.

---

## Phase 3 — Subprocess self-play runner

**Track:** Versioning. **Prerequisites:** Phase 0 + Phase 2.

Absorbs AlphaStar Phase C's opponent-pool concept: PFSP-lite weights live
inside `selfplay.py` as a sampler option; the opponent list comes from the
registry rather than a standalone pool.

### Scope

1. `src/orchestrator/selfplay.py` + `scripts/selfplay.py`:
   `--p1 v3 --p2 v5 --games 20 --map Simple64`.
2. Two subprocesses per game, each implementing the bot-spawn contract.
   Orchestrator coordinates SC2 handshake (exact mechanism from Phase 0).
3. Per-game result → `data/selfplay_results.jsonl` (shared JSONL, not
   per-version; one line per match). Readable via `pd.read_json(..., lines=True)`.
4. Alternating P1/P2 seats across the batch to control for side bias.
5. Crash handling: subprocess timeout → draw or excluded, logged. No
   orphan SC2 processes.
6. Sampling: plain `--p1 X --p2 Y` for head-to-head; optional
   `--sample pfsp --pool v0,v1,v2,v3 --games 40` uses PFSP-lite weights
   against a trainee.

### Tests

- `tests/test_selfplay.py` — 20-game batch completes, seats alternate,
  crash of one side doesn't leak SC2, results line-valid JSONL.
- `tests/test_pfsp_sampling.py` — PFSP-lite weights normalize, win-rate-0
  opponent gets zero weight, cold-start uniform.

### Effort

4–6 h.

### Gate

- 20-game self-play batch completes without hangs.
- Results well-formed and seat-alternated.
- One side crashes → other cleaned up; no orphan SC2.

### Kill criterion

Cannot reliably clean up subprocesses on crash (SC2 processes leak). If
hygiene fails, self-play is not production-ready — escalate to manual
curation per Phase 0's kill options.

### Rollback

`git revert`; shared `data/selfplay_results.jsonl` is append-only and can
be truncated or deleted.

---

## Phase 4 — Elo ladder

**Track:** Versioning. **Prerequisites:** Phase 3.

### Scope

1. `data/bot_ladder.json`: `{version: {elo, games_played, last_updated}}`.
2. `src/orchestrator/ladder.py` + `scripts/ladder.py`:
   - `update` — round-robin between top-N + current.
   - `show` — standings.
   - `compare vA vB --games 20`.
3. Standard Elo, K=32, new versions start at parent's Elo.
4. Dashboard tab: ladder + head-to-head grid. Frontend reads
   `data/bot_ladder.json` (shared data, schema-stable).
5. Analytics: any downstream queries use `pd.read_json('data/selfplay_results.jsonl', lines=True)`.

### Tests

- `tests/test_ladder.py` — Elo math correct, known-scenario reproducible,
  promotion threshold configurable.

### Effort

3–4 h.

### Gate

- Ladder updates reproducibly on a known scenario.
- Dashboard tab renders.
- Ladder JSON schema documented in `src/orchestrator/contracts.py`.

### Kill criterion

Elo signal is too noisy at 20-game batches to distinguish adjacent
versions. Mitigation: increase batch size to 40; if still noisy, promotion
gate shifts to win-rate-vs-SC2-AI as primary, Elo as secondary.

### Rollback

`git revert`; `data/bot_ladder.json` is regenerable from the matches JSONL.

---

## Phase 5 — Sandbox enforcement + skill integration

**Track:** Versioning. **Prerequisites:** Phases 2, 3, 4.

### Scope

1. **Sandbox hook** (`scripts/check_sandbox.py`, wired pre-commit):
   Commits tagged `[advised-auto]` may touch:
   - `bots/current/**` — freely.
   - Nothing else. Not `src/orchestrator/`, `pyproject.toml`, `tests/`,
     `frontend/`, `scripts/`.
   Hard fail if violated.
2. **`/improve-bot-advised` updates:**
   - Only edits `bots/current/**`.
   - Proposing orchestrator/dep changes → opens PR with label
     `advised-proposed-substrate`; no auto-merge.
   - After a passing iteration: snapshot to `vN+1`, commit tagged
     `[advised-auto]`, push.
3. **Validation rewire:** new `current` plays prior best via
   `selfplay.py`. Promotion on Elo gain ≥ threshold (default +10 Elo
   over 20 games, configurable in hyperparams).
4. **Run-start banner:** skill prints at session start:
   > I can edit: bots/current/**.
   > I cannot edit: src/orchestrator/, pyproject.toml, tests/, frontend/, scripts/.

### Tests

- `tests/test_sandbox_hook.py` — hook blocks forbidden paths, allows
  `bots/current/**`, detects missing `[advised-auto]` tag, passes
  human-PR commits through untouched.

### Effort

3–5 h.

### Gate

- Skill editing `pyproject.toml` → hook blocks.
- Skill editing `bots/current/learning/trainer.py` → allowed; snapshot
  happens; Elo validates.

### Kill criterion

Sandbox hook cannot be reliably enforced on Windows pre-commit (line
endings, path-separator bugs). Fallback: server-side hook on the remote
(less immediate but unbypassable).

### Rollback

`git revert`; hook disables by removing its entry from `.pre-commit-config.yaml`.

---

## Phase B — Unit-type histogram observation expansion

**Track:** Capability. **Prerequisites:** Phase 5 (so it runs inside the
sandbox as a skill-driven improvement, or as a human PR on
`bots/current/**`).

**Goal:** Answer "is observation signal the binding constraint on win rate?"

### Scope

| Step | Description |
|------|-------------|
| B.1 | Append ~15 own-army unit-type slots to `bots/current/learning/features.py` `_FEATURE_SPEC`: Zealot, Stalker, Sentry, Immortal, Colossus, Archon, HighTemplar, DarkTemplar, Phoenix, VoidRay, Carrier, Tempest, Disruptor, WarpPrism, Observer. Normalization: 40 worker, 20 core army. |
| B.2 | Append ~8 enemy-unit-seen slots driven by scouting memory. |
| B.3 | Bump `FEATURE_DIM`, add `FEATURE_DIM_V2` marker. Verify padding path in `imitation.py` handles the new width within this version. |
| B.4 | Add diagnostic-state entries covering typical mid-game compositions. |
| B.5 | Train 2 cycles from `v0_pretrain`, compare to Phase A end-state. |
| B.6 | Snapshot to `v1` on promotion. |

### Tests

- `tests/test_features_v2.py` — new slots produce expected values for
  synthetic snapshots; old DB rows round-trip via padding.
- `tests/test_imitation.py` — padding test covers 17 → V2 width.

### Effort

~1 day + cycle wall-clock.

### Validation

Win rate at difficulty 3 equal-or-better across 20 games AND Elo vs prior
`v0` ≥ +10 over 20 self-play games.

### Gate

Both win-rate hold AND Elo gain. Either failure → kill.

### Kill criterion

No improvement after 3 cycles — observation signal is not the bottleneck.
Skip to Phase D or E.

### Rollback

Delete `bots/v1/` if it existed; `current` re-forks from `v0`.

---

## Phase D — Build-order z-statistic (reward refactor)

**Track:** Capability. **Prerequisites:** Phase 5. B and D are orthogonal.

**Goal:** Collapse implicit build-order reward rules into an explicit `z`
target vector with edit-distance pseudo-reward.

### Scope

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

### Tests

- `tests/test_build_order_reward.py` — edit distance correct, reward
  monotonic in progress, empty target list handled.
- `tests/test_reward_migration.py` — pre/post migration reward totals on
  known game logs agree within 5%.

### Effort

~3 days (audit is most of it).

### Validation

Early-game (first 5 min) reward std-dev drops ≥30% AND win-rate holds at
difficulty 3 AND Elo gain ≥ +10 over 20 games.

### Gate

All three.

### Kill criterion

Reward variance does not drop — old rules already captured it. Keep the
migration as cleanup; skip `z`-as-obs wiring; do not snapshot.

### Rollback

Backup `reward_rules.pre-phase-d.json`; restore on kill. Delete the
promoted `vN` if already snapshotted.

---

## Phase E — Autoregressive action head

**Track:** Capability. **Prerequisites:** Phase 5. Ideally B done (more
interesting ATTACK decisions to differentiate).

**Goal:** Unlock tactical variety by making ATTACK / EXPAND conditional on
target choice rather than flat `Discrete(6)`.

### Scope

| Step | Description |
|------|-------------|
| E.1 | Structured action space: `(strategic_state, target)` — ATTACK → {main, natural, third}, EXPAND → {own_natural, own_third, own_fourth}, others unchanged. Effective size = 12. |
| E.2 | Custom `ActorCriticPolicy` subclass: two sequential softmax heads, `p(strategic)` then `p(target \| strategic)`. |
| E.3 | `bots/current/learning/database.py` — add `action_space_version INT`; `1` legacy, `2` Phase E. |
| E.4 | `_compute_next_state` also picks a target. Cascade into `rules_policy.py` KL teacher. |
| E.5 | Migration: 6-way DB rows infer target from game log when possible, else `*_main`. |
| E.6 | Snapshot to `vN+1` on promotion. |

### Impact matrix (within `bots/current/**`)

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

### Tests

- `tests/test_autoreg_policy.py` — two-head forward pass, target head
  gated by strategic head, gradients flow to both.
- `tests/test_action_migration.py` — 6-way DB migrates cleanly; old
  checkpoints rejected explicitly.

### Effort

~1 week.

### Validation

Replay inspection shows meaningful target differentiation (not always
`enemy_main`) AND win rate holds AND Elo gain ≥ +10 over 20 games.

### Gate

All three.

### Kill criterion

Target head collapses to single mode. May indicate insufficient signal —
Phase F might be needed first. Mark phase "deferred pending F".

### Rollback

Delete promoted `vN`; prior versions unaffected by design.

---

## Phase 6 — Self-play-driven improvement loop

**Track:** Operational. **Prerequisites:** Phase 5 + at least one of
{B, D, E} promoted (so there's a non-trivial starting point).

### Scope

1. `/improve-bot-advised --self-improve-code --opponent v5` — curriculum
   opponent selection (advance when +N Elo cleared).
2. Stretch: pool sampling from top-K versions for mixed-style validation
   (AlphaStar-lite league at single-box scale) using PFSP-lite sampler.
3. Operational mode — this is how B / D / E / F are driven autonomously
   once shipped. Not a one-shot phase; an ongoing regime.

### Tests

N/A (operational phase, not infrastructure).

### Effort

2 h to wire the skill flags; open-ended soak thereafter.

### Gate (first-cycle demonstration)

- Multi-hour run produces `vN → vN+1 → vN+2` with monotonically rising Elo.
- At least one version beats SC2 AI at a higher difficulty than the
  Phase A baseline `v0`.

### Kill criterion

Ladder exhibits cycling pathology (rock-paper-scissors, AlphaStar
Figure 3D) with no upward trend across 3 snapshots. Re-audit reward
rules and curriculum opponent selection before iterating further.

### Rollback

Operational phase — rollback is "stop running the skill." No code to revert.

---

## Phase F — Entity transformer encoder

**Track:** Capability-F. **Status:** Deferred. Only enter if B–E have all
landed and the bot is clearly bottlenecked by loss of per-unit information.

**Prerequisites:** Phases A, B, E merged and promoted. D preferred.

### Scope

| Step | Description |
|------|-------------|
| F.1 | Custom `BaseFeaturesExtractor` taking variable-length unit list with pad/mask. |
| F.2 | Transformer: 2 layers, 4 heads, 64-dim embedding, 128-dim FFN. ~100k params. `torch.nn.TransformerEncoder`, no new dep. |
| F.3 | Per-unit features: unit_type (embedding), health_pct, shield_pct, is_own, is_flying, is_cloaked, position_relative_to_main. |
| F.4 | Integrate via feature concat: transformer output + existing scalar features → MLP trunk. |
| F.5 | Train from scratch in a new `bots/vN+1/` — cannot use `vN`'s `v0_pretrain`. Build `v0_pretrain_transformer` via fresh imitation run. |
| F.6 | A/B against the best B/E version. |

### Tests

- `tests/test_entity_transformer.py` — variable unit counts, pad/mask
  correctness, gradient flow.
- `tests/test_imitation_transformer.py` — fresh pretrain builds + loads.

### Effort

~2 weeks.

### Validation

Beat best B/E version by ≥ 5% win rate over 20 games at difficulty 3 AND
show lower loss variance AND Elo gain ≥ +20 over 20 self-play games.

### Gate

All three.

### Kill criterion

Training diverges (NaN, policy collapse) OR no win-rate improvement after
the full 2-week build. Scalar histogram was sufficient — delete the
transformer version; keep A–E promoted versions.

### Rollback

Versioning makes this trivial: `rm -rf bots/vN+1/` where N+1 was the
transformer version. Prior stacks untouched.

---

## Compute target

Single Windows 11 box, CPU-only PyTorch (no CUDA). Phase F adds load —
if CPU training exceeds 2× baseline cycle wall-clock, that's F's kill
signal. GPU support explicitly out of scope.

## Time budget

| Phase | Optimistic | Realistic | Pessimistic |
|-------|-----------|-----------|-------------|
| A | 0.5 d | 1 d | 2 d (all configs regress) |
| 0 | 1 h | 2 h | 0.5 d (API investigation) |
| 1 | 6 h | 8–12 h | 2 d (hidden coupling) |
| 2 | 2 h | 3–4 h | 6 h (serialization edge cases) |
| 3 | 3 h | 4–6 h | 1 d (crash hygiene) |
| 4 | 2 h | 3–4 h | 6 h (Elo noise tuning) |
| 5 | 2 h | 3–5 h | 1 d (Windows hook issues) |
| B | 1 d | 1–2 d | 3 d (DB migration edges) |
| D | 2 d | 3 d | 1 w (rule audit tangled) |
| E | 1 w | 1 w | 2 w (SB3 override painful) |
| 6 | 2 h code | open-ended soak | ongoing |
| F | 1.5 w | 2 w | 3 w (training destabilizes) |
| **Sub-total (A–E + 0–5 + 6 wire-up)** | **~3 w** | **~5–6 w** | **~9–10 w** |
| **+ 20% integration buffer** | +0.6 w | +1.2 w | +2 w |
| **Total (excl. F)** | **~3.5 w** | **~6–7 w** | **~11–12 w** |
| **+ F if chased** | +1.5 w | +2 w | +3 w |

## What's NOT in this plan (deliberately)

- **Checkpoint-only opponent pools** (old AlphaStar Phase C). Subsumed
  by full-stack versioning; would regress as soon as Phase B ships.
- **League training** (AlphaStar's main-exploiter / league-exploiter
  split). PFSP-lite sampling over the registry captures ~30% at ~5% cost.
- **V-trace / UPGO** off-policy corrections. PPO is fine at single-box scale.
- **256×256 spatial map inputs.** Strategic action space doesn't need pixels.
- **Docker / containerized training.** Bare Windows per root `CLAUDE.md`.
- **Distributed actors.** Single Python process, single SC2, single game.
- **Per-version venvs.** Shared repo deps; deferred decision. If a
  version genuinely needs different deps, that's an
  `advised-proposed-substrate` PR.
- **Multi-map ladders.** Scoped to Simple64 for now.
- **Race-specific bot versioning.** Protoss-only.
- **Human-vs-bot interactive play.**
- **Cross-version feature-spec or action-space invariants.** Each
  `bots/vN/` is an independent stack; no global append-only rule.

## Tracking

Once Phase A merges, convert each subsequent phase into a GitHub issue
via `/repo-sync`. Milestone: `alpha4gate-master-plan`. Use issue threads
for interrupt-resume context.

Umbrella issue #105 (versioning big-box) and subprocess-spike issue #106
already exist; re-scope them to this merged plan or close and re-cut.

## Plan history

Append-only — do not edit prior entries.

- *2026-04-15* — merged from `alphastar-upgrade-plan.md` and
  `bot-versioning-selfplay-plan.md` (both archived). AlphaStar Phase C
  deleted; its goal subsumed by versioning Phases 0, 3, 4, 6. Cross-version
  obs-dim invariant dropped. JSONL-for-matches + JSON-for-ladder chosen
  over SQLite `opponent_matches` table.
- *2026-04-13* — (from alphastar plan) PR `feat/lstm-kl-imitation`
  (498f405) awaiting Phase A validation.
