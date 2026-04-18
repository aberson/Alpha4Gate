# Alpha4Gate — Master Plan (Platform + AlphaStar + Versioning)

## Source

This plan is the third-generation merge. It supersedes three predecessors,
all archived under `documentation/archived/`:

- `alphastar-upgrade-plan.md` — AlphaStar-inspired PPO upgrades (LSTM, obs
  expansion, self-play, z-statistic, autoregressive actions, transformer).
- `bot-versioning-selfplay-plan.md` — full-stack bot versioning with
  subprocess self-play and Elo ladder (the "big-box" approach).
- `always-up-plan.md` — autonomous improvement platform (daemon,
  evaluator, promotion gate, rollback, curriculum, transparency
  dashboard). Phases 1–4.5 of that plan shipped and are this plan's
  Baseline; its Phase 5 (Domain Abstraction) is dropped as subsumed by
  full-stack versioning.

Two structural decisions made during the merges:

1. **AlphaStar Phase C (checkpoint-only opponent pool) was deleted** in
   the prior merge — strictly weaker than full-stack self-play and
   self-destructs as soon as later phases change the feature spec.
2. **Always-up Phase 5 (Domain interface / CartPole validation) is
   dropped** in this merge — `bots/vN/` already provides the domain
   boundary via physical isolation. A typed Environment / FeatureSpec /
   RewardSpec abstraction is redundant with per-version stacks and
   would be built long before anyone would ever run the loop on a
   non-SC2 domain. Finding #11 (promotion-gate bootstrap) becomes a
   Phase 1 cleanup line item; Finding #12 (daemon idle deadlock) is
   structurally resolved by Phase 3 (subprocess self-play produces
   transitions while the daemon is otherwise idle).

## Vision

Alpha4Gate is an **autonomous improvement platform** whose first domain
is SC2 Protoss. The platform already plays, evaluates, trains, promotes,
and rolls back models unattended, with full dashboard transparency
(phases 1–4.5 shipped). The next era breaks the "stuck at difficulty 4–5"
ceiling by:

1. Validating the pending `feat/lstm-kl-imitation` PR on the current stack.
2. Versioning the entire bot stack — every improvement snapshot is a
   self-contained `bots/vN/` directory that can be rehydrated and played
   against any other version via subprocess self-play.
3. Layering AlphaStar-inspired PPO upgrades (obs expansion, build-order z,
   autoregressive actions, transformer) as per-version improvements
   inside the sandbox, promoted by Elo gain vs prior versions.
4. Keeping the existing daemon/evaluator/promotion/rollback loop running
   as the intra-version improvement engine. Elo vs prior `bots/vN/`
   becomes the cross-version promotion signal layered on top.

The versioning substrate is the primary new investment; AlphaStar
capabilities ride on top of it. The platform infrastructure from the
always-up plan stays in place underneath.

## Principles

- **Transparency first.** Every decision, training cycle, promotion, and
  rollback is observable via the dashboard. A feature that isn't visible
  to the operator is a feature that isn't finished.
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
- **Two-tier promotion.** Intra-version promotion uses WR delta on the
  existing daemon loop; cross-version promotion (`vN` → `vN+1`) uses
  Elo gain via subprocess self-play. SC2-AI WR is the daily signal; Elo
  is the cross-version truth signal.
- **Imitation is the backbone.** `v0_pretrain` is the cold-start for every
  RL run inside a given version. Phases that break this contract need extra
  justification.
- **Versions are independent stacks.** No cross-version feature-spec or
  action-space invariants. Each `bots/vN/` can have its own obs width,
  policy class, reward rules. Append-only within a version is still good
  hygiene (supports the padding trick for imitation DB reuse) but is no
  longer a cross-version rule.
- **Domain abstraction via isolation, not interfaces.** Generality is
  achieved by running different stacks as independent subprocesses, not
  by defining a typed `Environment` / `FeatureSpec` / `RewardSpec` layer.

## Glossary

| Term | Definition |
|------|-----------|
| **`v0_pretrain`** | Imitation-pretrained PPO checkpoint at `<version>/data/checkpoints/v0_pretrain.zip`. Behavior-cloned from rule-based decisions in `<version>/data/training.db`. Per-version, not shared. |
| **KL-to-rules** | Auxiliary loss from the `feat/lstm-kl-imitation` PR. After each PPO gradient step, `kl_rules_coef * CE(policy_logits, rule_engine_action)` is applied. Disabled at `kl_rules_coef=0.0`. |
| **Padding trick** | When a version's obs width grows, DB rows stored at the old width are zero-padded to match. Within-version only. |
| **`_FEATURE_SPEC`** | Tuple list in `<version>/learning/features.py` defining obs slots. Single source of truth within a version. |
| **`_compute_next_state`** | Rule-engine method in `<version>/decision_engine.py` mapping `GameSnapshot` → `StrategicState`. KL-to-rules teacher. |
| **`ACTION_TO_STATE`** | Canonical list in `decision_engine.py` mapping PPO action indices to `StrategicState`. |
| **`bots/current/`** | Working copy the skill and dev tooling read from. Either a fork of `bots/vN/` or a fresh-cut working tree promoted to the next `vN+1` on snapshot. |
| **Orchestrator** | `src/orchestrator/` — subprocess manager, registry, snapshot, self-play, ladder. The only code the skill cannot touch. |
| **Bot-spawn contract** | Every `bots/vN/` must run via `python -m bots.vN --role {p1\|p2\|solo} --map ... --sc2-connect ... --result-out ... --seed ...`. |
| **Diagnostic states** | Fixed obs vectors at `<version>/data/diagnostic_states.json` logged each training cycle. Within-version regression canary. |
| **`improve-bot-advised`** | Autonomous improvement skill at `.claude/skills/improve-bot-advised/SKILL.md`. After Phase 5 it is sandboxed to `bots/current/**`. |
| **PFSP-lite** | Prioritised-fictitious-self-play sampling. `w_i ∝ (1 - win_rate_vs_opponent_i)²`, cold-start uniform. Lives in `src/orchestrator/ladder.py` or `selfplay.py`. |
| **`TrainingDaemon`** | Background thread in the API server that runs the intra-version RL loop. Triggers on transition count or time. Built in always-up Phase 3; runs today. |
| **`PromotionManager`** | Intra-version promoter: compares new checkpoint WR against current best in `training.db`, promotes on ≥ 5% delta. Built in always-up Phase 3. |
| **`RollbackMonitor`** | Intra-version regression detector: reverts `manifest.best` to `previous_best` on ≥ 15% WR drop over 30 games. Built in always-up Phase 3. |
| **Intra-version promotion** | WR-based promotion *within* a single `bots/vN/` across training cycles. Uses `PromotionManager`. |
| **Cross-version promotion** | Elo-based promotion `bots/vN/` → `bots/vN+1/` via subprocess self-play. Built in Phase 4. |

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
Track 2 — Versioning   [0–5]       subprocess spike → move+data migration → registry → self-play → ladder → sandbox
Track 3 — Capability   [B, D, E]   per-version improvements inside bots/current/**
Track 4 — Capability-F [F]         deferred; only if B/D/E insufficient
Track 5 — Operational  [6]         cross-version self-play loop on top of existing daemon (needs 5 + at least one capability phase)
```

## Decision graph

```
Phase A (validate PR) ── gate: ≥ baseline WR → merge
    │
    └─ Phase 0 (subprocess spike) ── BLOCKING
            │
            ├─ pass ──→ Phase 1 (bots/v0/ + data migration) ──→ Phase 2 (registry) ──→ Phase 3 (self-play) ──→ Phase 4 (ladder) ──→ Phase 5 (sandbox)
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

## Baseline (as of 2026-04-17)

**Model / training stack:**

- **Policy:** SB3 `MlpPolicy`, 2×128 MLP, pure on-policy PPO
- **Observation:** 24-dim scalar (17 game + 7 advisor)
- **Action:** `Discrete(6)` strategic states
- **Training:** vs built-in AI only, no self-play
- **Win rate:** 75%+ at difficulty 3, struggles at 4–5
- **Phase A MERGED 2026-04-15** (tag `alphastar/A/final` on `cfeeb99`):
  `feat/lstm-kl-imitation` validated and fast-forwarded to master. Full
  stack (LSTM + KL-to-rules + imitation-init) scored **19/20 = 95% WR**
  at difficulty 3 hybrid in the A.6 soak. Shipped defaults on master are
  still MlpPolicy/kl=0/imitation=false; the winning full-stack
  hyperparams are in `git stash` awaiting Phase 1 reactivation.
- **Phase 1 COMPLETE 2026-04-16** (tag `master-plan/1/final`):
  Full bot stack moved from `src/alpha4gate/` to `bots/v0/`. Per-version
  data dir at `bots/v0/data/`. `bots/current/` MetaPathFinder alias.
  `src/orchestrator/` scaffolded with registry, contracts, stubs. 916
  tests import from `bots.v0.*`. `src/alpha4gate/` deleted.
- **Phase 2 COMPLETE 2026-04-16** (tag `master-plan/2/final`):
  `list_versions()` added to registry. Full-stack snapshot tool
  (`snapshot_current()`) copies `bots/vN/` → `bots/vN+1/` with manifest
  lineage, git SHA, fingerprints. CLI: `python -m orchestrator list/show`,
  `scripts/snapshot_bot.py`. Gate verified: snapshot → boot → SC2 game.
  943 tests, mypy strict (62 files).
- **Phase 3 COMPLETE 2026-04-17** (tag `master-plan/3/final`):
  Subprocess self-play batch runner in `src/orchestrator/selfplay.py`.
  Port-collision patch absorbed from Phase 0 spike. Seat alternation,
  crash/timeout handling, PFSP-lite opponent sampling. Results to
  `data/selfplay_results.jsonl`. CLI: `scripts/selfplay.py` (head-to-head
  + PFSP modes). Gate: 4-game live batch PASS. 967 tests.
- **Phase 4 COMPLETE 2026-04-17** (tag `master-plan/4/final`):
  Elo ladder in `src/orchestrator/ladder.py`. Standard Elo K=32, seeding
  from manifest/parent/1000. Round-robin `ladder_update()`, JSONL replay,
  cross-version promotion gate (`check_promotion()` → `snapshot_current()`).
  CLI: `scripts/ladder.py` (update/show/compare/replay). `GET /api/ladder`
  endpoint. Ladder tab (10th dashboard tab) with standings + head-to-head
  grid. `LadderEntry` + `PromotionResult` contracts. Gate: v0-vs-v0
  produces Elo delta 0 → correctly rejected. 1011 tests, 129 vitest.

**Autonomous platform (from completed always-up Phases 1–4.5):**

- `TrainingDaemon` — threaded daemon in API server, transition-count +
  time triggers, curriculum auto-advance, persistent config in
  `data/daemon_config.json`.
- `ModelEvaluator` + `PromotionManager` + `PromotionLogger` —
  inference-only eval, WR-delta promotion gate, JSON + wiki logging.
- `RollbackMonitor` — regression detection + auto-revert with
  difficulty floor.
- `training.db` (SQLite) — games + transitions + action probabilities,
  per-model WR queries, source of truth for intra-version promotion.
- `data/reward_logs/` — always-on per-game JSONL; aggregated via
  `learning/reward_aggregator.py` for the dashboard Reward Trends card.
- Dashboard (9 tabs): Live, Stats, Decisions, Training, Loop, Advisor,
  Improvements, Processes, Alerts. Client-side alert engine with a
  backend ERROR ring buffer.
- 46 API endpoints for daemon/trigger/evaluate/promote/rollback/
  curriculum/advised-run control.
- 48 reward rules in `data/reward_rules.json` (only affect PPO under
  `--decision-mode hybrid`, not rule-based default play).
- **967 Python unit tests + 126 frontend vitest tests passing.**
- Four `/improve-bot-advised` runs completed; run-4 code improvements
  landed (anti-float expansion override, warp-in forward pylon
  selection, bot wins 100% at difficulty 3).
- **Outstanding findings.** `phase-4-5-backlog.md` holds the
  dashboard-polish / daemon-tuning / docs-gaps carryover. Finding #11
  (unconditional bootstrap promotion) is picked up as a Phase 1 cleanup
  line item. Finding #12 (daemon idle deadlock) is resolved by Phase 3.

See `documentation/archived/always-up-plan.md` for the full
phase-by-phase history of how this baseline was built.

---

## Phase A — Validate the pending PR

**Track:** Validation. **Goal:** Prove LSTM + KL-to-rules + imitation-init
does not regress baseline before anything else is layered on.

**Location:** runs on current `src/alpha4gate/` (pre-versioning).

**Prerequisites:** none.

### Scope

| Step | Description |
|------|-------------|
| A.0 | Checkout `feat/lstm-kl-imitation`; confirm 829 unit tests pass |
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

## Phase 1 — Move full stack + data to `bots/v0/`; scaffold orchestrator ✅

**Status: COMPLETE** (tag `master-plan/1/final`, 2026-04-16). See `documentation/phase-1-build-plan.md` for step-by-step details.

**Track:** Versioning. **Goal:** `src/alpha4gate/` becomes `bots/v0/`.
`data/` splits: per-version state moves into `bots/v0/data/`, shared
cross-version state stays at the repo root. Orchestrator scaffolded.
Everything — including the daemon, dashboard, and every existing script —
works exactly as before, just loaded via the registry.

**Prerequisites:** Phase 0 pass.

**Effort estimate is intentionally larger than the prior draft.** This
phase does the code move AND the data migration AND fixes Finding #11 in
one shot; interim broken state on a move of this size is worse than a
bigger single commit.

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
8. **Data migration (per-version vs shared split):**
   - **Move to `bots/v0/data/`:** `training.db`, `checkpoints/`,
     `reward_rules.json`, `hyperparams.json`, `reward_logs/`,
     `daemon_config.json`, `diagnostic_states.json`,
     `promotion_history.json`, `advised_run_state.json`,
     `advised_run_control.json`.
   - **Keep at repo-root `data/`:** future `selfplay_results.jsonl`
     (Phase 3), `bot_ladder.json` (Phase 4). Nothing else.
   - Update every API endpoint, every script, every frontend fetch to
     resolve paths through the registry rather than hardcoding
     `data/…`. The dashboard's `ProcessMonitor` state-file readers and
     the advised-run bridge are the highest-churn touch points.
9. **Finding #11 cleanup (promotion-gate bootstrap).** The current
   `PromotionManager` short-circuits to "promote" when `manifest.best`
   is null, which means the comparison code path was never exercised
   in the soak runs. Pre-seed `bots/v0/manifest.json` with `best` and
   `previous_best` set to the current best-known checkpoint during the
   migration so that the first post-merge promotion goes through the
   real comparison path. Add a `bootstrap_promotion` test that fails
   if an unseeded manifest is passed to the gate.

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

- **Import graph churn.** ~47 modules to rename. Largest mechanical task
  in the plan.
- **Daemon/API pathing.** The daemon, API endpoints, and frontend all
  hardcode `data/…`. Each needs to resolve through the registry.
- **Test imports.** `tests/` imports from `alpha4gate.*` — update to
  `bots.current.*` or a test-time alias.
- **Frontend path churn.** File-based bridges (`advised_run_state.json`,
  `advised_run_control.json`) move from `data/` to `bots/current/data/`.

### Tests

Full existing suite runs green against the moved tree. Add
`tests/test_bootstrap_promotion.py` for Finding #11.

### Effort

12–16 h (was 8–12 h; data migration + Finding #11 folded in).

### Gate

- `uv run pytest` green (all 829 + new bootstrap test).
- Full SC2 game runs via `python -m bots.current`.
- Dashboard connects to `bots/current/` API and renders normally across
  all 9 tabs.
- Daemon starts, triggers, completes a training cycle, promotes via the
  non-bootstrap code path.

### Kill criterion

Import graph rewrite reveals hidden tight coupling that cannot be broken
without touching the orchestrator contract. Extend Phase 1 by one week
before escalating; if still blocked, redesign contracts before proceeding.

### Rollback

`git revert` the move commit. Entire suite lives on master untouched until
this phase is merged.

---

## Phase 2 — Registry + full-stack snapshot tool ✅

**Status: COMPLETE** (tag `master-plan/2/final`, 2026-04-16). See `documentation/phase-2-build-plan.md` for step-by-step details.

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

**Structural note — resolves always-up Finding #12.** When the daemon is
"idle" vs SC2 AI (no games in flight, `transitions_since_last=0`), the
self-play runner can produce transitions against prior `bots/vN/`
opponents. This removes the idle-deadlock hard cap at ~1 cycle/hour that
soak run #1 hit. The daemon's trigger logic stays unchanged; the new
transition supply comes from self-play subprocess runs that don't
require the daemon to be "active."

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
7. Transition hand-off: self-play games write their transitions into the
   `p1` version's `bots/vN/data/training.db` (or both versions' DBs if
   configured). This is what feeds the daemon during otherwise-idle
   windows.

### Tests

- `tests/test_selfplay.py` — 20-game batch completes, seats alternate,
  crash of one side doesn't leak SC2, results line-valid JSONL.
- `tests/test_pfsp_sampling.py` — PFSP-lite weights normalize, win-rate-0
  opponent gets zero weight, cold-start uniform.
- `tests/test_selfplay_transition_hand_off.py` — self-play games produce
  rows in the per-version training.db, resolving Finding #12.

### Effort

4–6 h.

### Gate

- 20-game self-play batch completes without hangs.
- Results well-formed and seat-alternated.
- One side crashes → other cleaned up; no orphan SC2.
- Daemon "idle" state no longer blocks transition production when
  self-play is running.

### Kill criterion

Cannot reliably clean up subprocesses on crash (SC2 processes leak). If
hygiene fails, self-play is not production-ready — escalate to manual
curation per Phase 0's kill options.

### Rollback

`git revert`; shared `data/selfplay_results.jsonl` is append-only and can
be truncated or deleted.

---

## Phase 4 — Elo ladder + cross-version promotion

**Track:** Versioning. **Prerequisites:** Phase 3.

Introduces the **cross-version promotion signal**. The intra-version
`PromotionManager` from always-up Phase 3 keeps running on WR delta
inside `bots/current/`; this phase adds the Elo gate for promoting
`bots/current/` → `bots/vN+1/`.

### Scope

1. `data/bot_ladder.json`: `{version: {elo, games_played, last_updated}}`.
2. `src/orchestrator/ladder.py` + `scripts/ladder.py`:
   - `update` — round-robin between top-N + current.
   - `show` — standings.
   - `compare vA vB --games 20`.
3. Standard Elo, K=32, new versions start at parent's Elo.
4. Dashboard: **Ladder becomes the 10th tab** (sibling to Training,
   Improvements, etc.). Shows standings + head-to-head grid. Frontend
   reads `data/bot_ladder.json` (shared data, schema-stable).
5. Analytics: any downstream queries use
   `pd.read_json('data/selfplay_results.jsonl', lines=True)`.
6. **Cross-version promotion gate.** Snapshot `bots/current/` → `bots/vN+1/`
   requires:
   - Elo gain ≥ +10 vs parent `bots/vN/` over ≥ 20 self-play games, AND
   - WR non-regression vs SC2 AI at the current curriculum difficulty
     (sanity check — a 100-Elo gain that also drops WR 30% is suspect).
   The intra-version `PromotionManager` continues to run independently
   on WR delta inside `bots/current/`; cross-version is a second level.

### Tests

- `tests/test_ladder.py` — Elo math correct, known-scenario reproducible,
  promotion threshold configurable.
- `tests/test_cross_version_gate.py` — WR sanity check rejects
  suspicious Elo gains.

### Effort

3–4 h (was 3–4 h; 10th-tab scope is small — reuses existing dashboard
patterns).

### Gate

- Ladder updates reproducibly on a known scenario.
- Ladder tab renders alongside the existing 9.
- Ladder JSON schema documented in `src/orchestrator/contracts.py`.
- Cross-version promotion can be triggered end-to-end and respects both
  Elo and WR gates.

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
   `selfplay.py`. Cross-version promotion on Elo gain ≥ threshold
   (default +10 Elo over 20 games, configurable in hyperparams) AND
   intra-version WR non-regression.
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

### Result — COMPLETE (2026-04-17)

**All 5 issues closed (#118–#122). 1020/1020 tests passing. Zero type errors. Zero lint violations.**

What was built:
- `scripts/check_sandbox.py` — pre-commit hook enforcing `bots/current/`-only sandbox for `ADVISED_AUTO=1` commits
- `tests/test_sandbox_hook.py` — 9 test cases (passthrough, allowed/forbidden paths, path traversal, mixed, edge cases)
- `.pre-commit-config.yaml` — `repo: local` + `language: system` wiring
- `pre-commit` added to dev dependencies
- `/improve-bot-advised` SKILL.md updated: run-start banner, `[advised-auto]` commit tag, `ADVISED_AUTO=1` env var, `check_promotion()` Elo gate, `--self-improve-code` path restriction

Gate verified: forbidden file blocked, allowed file passed, banner matches sandbox scope.

Build plan: `documentation/phase-5-build-plan.md`

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

## Tactical refinements backlog (surfaced during Phase B eval)

Bugs observed during Phase B Step 5 eval that are tactical, not observational.
These block Phase B Step 6 (v1 snapshot) because win-rate numbers are inflated
(games take 40+ min due to passivity). Not formal phases — handle via
`/improve-bot --self-improve-code` or standalone `/build-step` fixes.

### T.1 — Soften max-supply ATTACK override

**Status:** Hard override shipped as commit `0bc2f90` (#134). `MAX_SUPPLY_ATTACK_THRESHOLD=180`
forces ATTACK regardless of DEFEND/FORTIFY/EXPAND/OPENING state. **Deliberately
heavy-handed for now — get it working, tune later.**

**Problem with the current fix:** At higher difficulties (4-5), a legitimate
defensive stand (e.g., enemy doom-drop into main while own army is across the
map at 180+ supply) would be incorrectly preempted into ATTACK, losing the
defender's advantage. The 180-supply check fires even when defending is the
correct play.

**Softer solution candidates (pick one when tuning):**
- Require `army_supply >= enemy_army_supply_visible * k` before ATTACK fires,
  so max-supply doesn't override when outgunned in the engagement area.
- Add a cooldown: override fires only if `supply_used >= 180` has held for
  N seconds, preventing flip-flop with a visible enemy raid.
- Scale the threshold by difficulty (180 at diff 3, 190 at diff 4, 195 at diff 5).
- Replace with a "production saturation" signal: override when no new units
  can be produced (all warp-gates on cooldown + all production full) AND
  supply at cap — captures the actual "waste" condition without false-firing.

**When to tune:** After re-eval at diff 3 confirms win-rate hold, before pushing
to diff 4-5. The hard fix is safe for diff 1-3 where opponents rarely doom-drop.

### T.2 — Low-ground bleeding (rally below ramps)

See `memory/feedback_lowground_bleeding.md`. Army rallies below enemy ramps and
takes free ranged fire instead of committing up or retreating. Rally-point
selection needs elevation awareness.

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

This phase does not rebuild the intra-version loop — the existing
`TrainingDaemon` / `PromotionManager` / `RollbackMonitor` (from always-up
Phase 3) continues to run inside `bots/current/`. Phase 6 adds the
**cross-version** self-play layer on top.

### Scope

1. `/improve-bot-advised --self-improve-code --opponent v5` — curriculum
   opponent selection (advance when +N Elo cleared).
2. Stretch: pool sampling from top-K versions for mixed-style validation
   (AlphaStar-lite league at single-box scale) using PFSP-lite sampler.
3. Operational mode — this is how B / D / E / F are driven autonomously
   once shipped. Not a one-shot phase; an ongoing regime.
4. Dashboard surfacing: Ladder tab (from Phase 4) shows cross-version
   progress; Improvements tab continues to show intra-version
   promotions/rollbacks from the existing daemon.

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

## Phase G — Multi-race support (Zerg, then Terran)

**Track:** Capability. **Status:** Future. **Prerequisites:** Phase 6
operational (the autonomous loop works end-to-end for Protoss first).

**Goal:** Extend the bot from Protoss-only to all three SC2 races. Each
race is a separate `bots/` version line sharing infrastructure but with
its own gameplay code.

This is a large effort — the gameplay layer (macro, micro, production,
abilities, build orders, reward rules, feature encoding) is deeply
Protoss-specific today. The architecture (decision engine, command system,
PPO pipeline, dashboard, ladder, sandbox) is already race-agnostic.

### Phased approach

**Phase G.1 — Race interface extraction.** Before adding new races,
extract a race-agnostic interface from the Protoss code:
- `RaceConfig`: unit roster, production tree, ability set, macro mechanic
  (Chronoboost vs Inject vs MULE), worker type, supply structure
- `ProductionAdapter`: abstract over Warp Gate vs Larva vs Add-On
- `MicroAdapter`: abstract over race-specific abilities
- `FeatureSpec`: race-parameterized unit-type slots
- `RewardTemplate`: race-parameterized reward rules
- Refactor `bots/v0/` (Protoss) to use these interfaces — no behavior
  change, just structural extraction. All 1020+ tests must still pass.

**Phase G.2 — Zerg.** First new race (most different from Protoss —
validates the interface deeply):
- `bots/zerg_v0/`: Larva/Inject economy, Creep spread, Overlord supply,
  morph-based production (Roach, Hydra, Lurker, Mutalisk, Corruptor,
  Brood Lord, Viper, Infestor, Ultralisk, Baneling, Zergling)
- Zerg-specific micro: Bile, Fungal, Burrow, Abduct
- Zerg build orders (hatch-first, pool-first, 12-pool)
- Zerg reward rules (~40 rules, adapted from Protoss patterns)
- Zerg feature encoding (unit-type slots, larva count, inject timers)
- Train from scratch, promote via Elo ladder (vs SC2 AI, not vs Protoss)

**Phase G.3 — Terran.** Second new race:
- `bots/terran_v0/`: SCV economy, MULEs, Supply Depot walls, Add-On
  production (Reactor/Tech Lab), Siege mode, Stim, Medivac healing
- Terran-specific micro: Siege/Unsiege, Stim, Snipe, EMP, Nuke
- Terran build orders (1-1-1, 2-1-1, mech, bio)
- Terran reward rules and feature encoding
- Train from scratch, promote via Elo ladder

**Phase G.4 — Cross-race ladder.** Once all three races have promoted
versions, enable cross-race self-play in the ladder. Each race's version
line competes within-race for promotion; cross-race matches are
informational (Elo tracked separately).

### Effort estimate

| Sub-phase | Estimate |
|-----------|----------|
| G.1 (interface extraction) | 1–2 weeks |
| G.2 (Zerg) | 3–4 weeks |
| G.3 (Terran) | 2–3 weeks (interface proven) |
| G.4 (cross-race ladder) | 2–3 days |
| **Total** | **~7–10 weeks** |

### Gate

Each sub-phase gates independently:
- G.1: All existing Protoss tests pass, interface coverage for 3 races
- G.2: Zerg wins ≥50% at difficulty 3 over 20 games
- G.3: Terran wins ≥50% at difficulty 3 over 20 games
- G.4: Cross-race Elo ladder produces stable rankings

### Kill criterion

G.1 interface extraction proves too invasive (breaks >5% of tests or
requires >500 lines of adapter code). Indicates the gameplay layer is
more tightly coupled than expected — defer and revisit after more
capability phases mature the Protoss codebase.

### Rollback

Each race is its own `bots/` directory — delete and ladder is unaffected.

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
| 1 | 10 h | 12–16 h | 3 d (hidden coupling + data migration edge cases) |
| 2 | 2 h | 3–4 h | 6 h (serialization edge cases) |
| 3 | 3 h | 4–6 h | 1 d (crash hygiene) |
| 4 | 2 h | 3–4 h | 6 h (Elo noise tuning, 10th-tab integration) |
| 5 | 2 h | 3–5 h | 1 d (Windows hook issues) |
| B | 1 d | 1–2 d | 3 d (DB migration edges) |
| D | 2 d | 3 d | 1 w (rule audit tangled) |
| E | 1 w | 1 w | 2 w (SB3 override painful) |
| 6 | 2 h code | open-ended soak | ongoing |
| F | 1.5 w | 2 w | 3 w (training destabilizes) |
| **Sub-total (A–E + 0–5 + 6 wire-up)** | **~3.5 w** | **~5.5–6.5 w** | **~10–11 w** |
| **+ 20% integration buffer** | +0.7 w | +1.3 w | +2.2 w |
| **Total (excl. F)** | **~4 w** | **~7–8 w** | **~12–13 w** |
| **+ F if chased** | +1.5 w | +2 w | +3 w |
| **+ G (multi-race)** | +6 w | +8–10 w | +14 w |

## What's NOT in this plan (deliberately)

- **Checkpoint-only opponent pools** (old AlphaStar Phase C). Subsumed
  by full-stack versioning; would regress as soon as Phase B ships.
- **Domain interface abstraction / CartPole validation** (old always-up
  Phase 5, issues #101–#103). Subsumed by `bots/vN/` physical
  isolation. Each version is already its own stack with its own
  observation / action / reward spec; a typed
  `Environment` / `FeatureSpec` / `RewardSpec` layer would be
  redundant. If a non-SC2 domain ever matters, a `bots/cartpole_v0/`
  stack is the expression of it — no interface hoisting needed.
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
- **~~Race-specific bot versioning.~~** Moved to Phase G (Zerg first, then Terran).
- **Human-vs-bot interactive play.**
- **Cross-version feature-spec or action-space invariants.** Each
  `bots/vN/` is an independent stack; no global append-only rule.
- **WebSocket upgrade for training/loop/alerts.** Deferred from
  always-up Phase 4; reconsider after Phase 6 operational soak.
- **Disk rotation / compression for `reward_logs/`.** Deferred from
  always-up Phase 2; still monitor manually.

## Historical phases (from the archived always-up plan)

One-line summary of work that shipped and became this plan's Baseline.
Full detail in `documentation/archived/always-up-plan.md`.

| Phase | Status | Deliverables |
|-------|--------|--------------|
| 1. Wiki & Documentation | DONE 2026-04-09 | 15 wiki pages covering system, evaluation, training, monitoring, domain coupling, frontend, testing, promotions, FAQ |
| 2. Monitoring & Observability | DONE 2026-04-09 | Action-probability persistence, always-on reward JSONL, per-checkpoint WR, model-comparison + improvement-timeline dashboard cards |
| 3. Autonomous Training Loop | DONE 2026-04-10 | `TrainingDaemon`, trigger logic, `ModelEvaluator`, `PromotionManager`, `RollbackMonitor`, curriculum auto-advance |
| 4. Transparency Dashboard | DONE 2026-04-09 | 9-tab dashboard, Loop / Improvements / Alerts tabs, `RewardTrends`, client-side alert engine, trigger controls |
| 4.5. First Real Soak Test | DONE 2026-04-11 | `soak-test.md` procedure, 4-hour soak run, 17 findings triaged, 5 blockers fixed (#66–#74), #11/#12 handed to this plan |

## Tracking

Once Phase A merges, convert each subsequent phase into a GitHub issue
via `/repo-sync`. Milestone: `alpha4gate-master-plan`. Use issue threads
for interrupt-resume context.

Umbrella issue #105 (versioning big-box) and subprocess-spike issue #106
already exist; re-scope them to this merged plan or close and re-cut.

Issues **#101, #102, #103** (old always-up Phase 5 Domain Abstraction
steps) are closed as subsumed by full-stack versioning; see this plan's
"What's NOT in this plan" entry and plan history for rationale.

## Auxiliary features

Features outside the versioning spine. Each has its own plan doc; they
can be built in any order and don't block numbered phases.

### Self-play viewer — windowed container

Embed the two SC2 windows spawned during self-play into a single themed
container window (side-by-side, 1024×768 each, 200px buffer). Uses Win32
`SetParent` reparenting after `a_run_multiple_games` spawns the
processes. Background image from `img_backgrounds/`. Local developer
experience only (no dashboard integration). Plan:
`documentation/plans/selfplay-viewer-plan.md`.

---

## Plan history

Append-only — do not edit prior entries.

- *2026-04-17* — Phase 4 (Elo ladder + cross-version promotion) complete.
  All 7 build steps shipped. `src/orchestrator/ladder.py`, `scripts/ladder.py`,
  `GET /api/ladder`, Ladder tab (10th). 1011 tests + 129 vitest.
- *2026-04-15* — merged `always-up-plan.md` into this plan. Always-up
  Phases 1–4.5 collapsed into the Baseline + Historical phases table;
  always-up Phase 5 (Domain interface / CartPole) dropped as subsumed
  by `bots/vN/` physical isolation. Finding #11 (unconditional
  bootstrap promotion) folded into Phase 1 as a cleanup line item;
  Finding #12 (daemon idle deadlock) noted as structurally resolved by
  Phase 3's subprocess self-play transition supply. Intra-version WR
  promotion (existing daemon) and cross-version Elo promotion (new
  Phase 4 gate) named as the two-tier promotion model. Phase 1 effort
  raised 8–12 h → 12–16 h to cover the one-shot code + data migration.
  Ladder named as the dashboard's 10th tab. Issues #101/#102/#103
  closed as subsumed. `always-up-plan.md` archived under
  `documentation/archived/`.
- *2026-04-15* — merged from `alphastar-upgrade-plan.md` and
  `bot-versioning-selfplay-plan.md` (both archived). AlphaStar Phase C
  deleted; its goal subsumed by versioning Phases 0, 3, 4, 6. Cross-version
  obs-dim invariant dropped. JSONL-for-matches + JSON-for-ladder chosen
  over SQLite `opponent_matches` table.
- *2026-04-13* — (from alphastar plan) PR `feat/lstm-kl-imitation`
  (498f405) awaiting Phase A validation.
