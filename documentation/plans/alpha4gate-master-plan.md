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
Track 5 — Operational  [6, 7, 9]   cross-version PPO loop (6) + advised stale-policy detection (7) + improve-bot-evolve sibling-tournament loop (9; numbered 9 not 8 to match issue titles); orthogonal mechanisms that all share run_batch
Track 6 — Multi-race   [G]         post-Phase-6; Zerg then Terran via per-race bots/<race>_v0/ stacks
```

## Decision graph

```
Phase A ✅ ──→ Phase 0 ✅ ──→ Phase 1 ✅ ──→ Phase 2 ✅ ──→ Phase 3 ✅ ──→ Phase 4 ✅ ──→ Phase 5 ✅
                                                                                                  │
              ┌───────────────────┬─────────────────┬─────────────────┬───────────────┬───────────┤
              ▼                   ▼                 ▼                 ▼               ▼           ▼
         Phase B             Phase D           Phase E           Phase 7         Phase 9     Phase G
         (obs)               (z-stat)          (autoreg)         (advised        (evolve     (multi-race;
                                                                  staleness;     sibling     after Phase 6)
                                                                  standalone)    tournament;
                                                                                 standalone)
              │                   │                 │
              └───────────────────┴─────────────────┴─→ Phase 6 (PPO-driven loop) ── hungry? ──→ Phase F (transformer)
```

Operational tracks (6, 7, 8) are independent of each other and of the
capability tracks (B/D/E/F); each can ship without the others. Phase G is
gated on Phase 6 operational maturity.

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
- **Phase 4 COMPLETE 2026-04-17** (tag `master-plan/4/final` on `dba2d40`):
  Elo ladder in `src/orchestrator/ladder.py`. Standard Elo K=32, seeding
  from manifest/parent/1000. Round-robin `ladder_update()`, JSONL replay,
  cross-version promotion gate (`check_promotion()` → `snapshot_current()`).
  CLI: `scripts/ladder.py` (update/show/compare/replay). `GET /api/ladder`
  endpoint. Ladder tab (10th dashboard tab) with standings + head-to-head
  grid. `LadderEntry` + `PromotionResult` contracts. Gate: v0-vs-v0
  produces Elo delta 0 → correctly rejected. 1011 tests, 129 vitest.
- **Phase 5 COMPLETE 2026-04-17** (no tag cut; latest sandbox docs
  commit `0c9e273`): `scripts/check_sandbox.py` pre-commit hook +
  `tests/test_sandbox_hook.py` (9 cases) wired via
  `.pre-commit-config.yaml`. `/improve-bot-advised` SKILL.md updated with
  run-start banner, `[advised-auto]` commit tag, `ADVISED_AUTO=1` env,
  `check_promotion()` Elo gate, `--self-improve-code` path restriction.
  1020 tests, zero mypy/ruff. Sandbox-modes table lives in the Phase 5
  section; Phase 9 will extend it with `EVO_AUTO=1`.

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
- Dashboard (10 tabs today; will be 11 when Phase 9 ships its Evolution
  tab): Live, Stats, Decisions, Training, Loop, Advisor, Improvements,
  Processes, Alerts, Ladder. Client-side alert engine with a backend
  ERROR ring buffer.
- 46 API endpoints for daemon/trigger/evaluate/promote/rollback/
  curriculum/advised-run control.
- 48 reward rules in `data/reward_rules.json` (only affect PPO under
  `--decision-mode hybrid`, not rule-based default play).
- **967 Python unit tests + 126 frontend vitest tests passing.**
- Four `/improve-bot-advised` runs completed; run-4 code improvements
  landed (anti-float expansion override, warp-in forward pylon
  selection, bot wins 100% at difficulty 3).
- **Outstanding findings.** `documentation/archived/phase-4-5-backlog.md`
  holds the dashboard-polish / daemon-tuning / docs-gaps carryover.
  Finding #11 (unconditional bootstrap promotion) is picked up as a
  Phase 1 cleanup line item. Finding #12 (daemon idle deadlock) is
  resolved by Phase 3.

See `documentation/archived/always-up-plan.md` for the full
phase-by-phase history of how this baseline was built.

---

## Phase A — Validate the pending PR ✅

**Status: COMPLETE** (tag `alphastar/A/final` on `cfeeb99`, 2026-04-15).
Full-stack LSTM + KL-to-rules + imitation-init validated at **19/20 = 95% WR**
at difficulty 3 hybrid in the A.6 soak. Validation procedure (six-step
PowerShell harness, known-failure diagnostic table) preserved in
`documentation/archived/alphastar-upgrade-plan.md` — read it before reopening
this phase.

Shipped defaults on master are still MlpPolicy / kl=0 / imitation=false; the
winning full-stack hyperparams are in `git stash` awaiting Phase 1
reactivation.

---

## Phase 0 — Subprocess self-play orchestration spike ✅

**Status: COMPLETE** (tag `master-plan/0/final` on `0b67b30`, 2026-04-16).
Two `python -m bots.vN` subprocesses played a 1v1 on Simple64 in 22.4s
end-to-end. burnysc2 7.1.3 port-collision and Py3.14 asyncio patches were
captured in `documentation/wiki/subprocess-selfplay.md` and absorbed into
Phase 3's `selfplay.py`. Spike script was throwaway; no rollback artifact.

---

## Phase 1 — Move full stack + data to `bots/v0/`; scaffold orchestrator ✅

**Status: COMPLETE** (tag `master-plan/1/final` on `e477235`, 2026-04-16).
Build detail in `documentation/plans/phase-1-build-plan.md` — read it before
reopening this phase.

`src/alpha4gate/` deleted, full bot stack at `bots/v0/`. Per-version data
dir at `bots/v0/data/` (training.db, checkpoints, reward_rules.json,
hyperparams.json, reward_logs/, etc.). `bots/current/` MetaPathFinder
alias. `src/orchestrator/` scaffolded with `registry.py`, `contracts.py`
(frozen bot-spawn + result-reporting JSON contracts), `snapshot.py`,
`selfplay.py`, `ladder.py` stubs. Finding #11 (promotion-gate bootstrap)
closed via pre-seeded manifest + `tests/test_bootstrap_promotion.py`. 916
tests green.

---

## Phase 2 — Registry + full-stack snapshot tool ✅

**Status: COMPLETE** (tag `master-plan/2/final` on `f26bb55`, 2026-04-16).
Build detail in `documentation/plans/phase-2-build-plan.md` — read it before
reopening this phase.

`registry.list_versions()`, `registry.get_version_dir()`,
`registry.get_manifest()`. Full-stack `snapshot_current()` copies
`bots/current/` → `bots/vN+1/` with manifest lineage, git SHA,
feature-dim + action-space fingerprints. CLIs: `python -m orchestrator
list/show`, `scripts/snapshot_bot.py`. Gate verified: snapshot → boot →
SC2 game on the snapshotted version. 943 tests, mypy strict (62 files).

---

## Phase 3 — Subprocess self-play runner ✅

**Status: COMPLETE** (tag `master-plan/3/final` on `85d5fb2`, 2026-04-17).
Build detail in `documentation/plans/phase-3-build-plan.md` — read it before
reopening this phase.

`src/orchestrator/selfplay.py` + `scripts/selfplay.py`: subprocess batch
runner via burnysc2 `BotProcess`, port-collision patch absorbed from
Phase 0, seat alternation, crash/timeout handling, PFSP-lite opponent
sampling. Results → `data/selfplay_results.jsonl` (one line per match,
`pd.read_json(..., lines=True)`-compatible). Self-play games can write
transitions back into the per-version `training.db`, structurally
resolving always-up Finding #12 (daemon idle deadlock — self-play
produces transitions during otherwise-idle daemon windows). Gate: 4-game
live batch PASS, 20-game soak clean. 967 tests.

---

## Phase 4 — Elo ladder + cross-version promotion ✅

**Status: COMPLETE** (tag `master-plan/4/final` on `dba2d40`, 2026-04-17).
Build detail in `documentation/plans/phase-4-build-plan.md` — read it before
reopening this phase.

`src/orchestrator/ladder.py`: standard Elo K=32, seeding from
manifest/parent/1000, round-robin `ladder_update()`, JSONL replay.
**Cross-version promotion gate** (`check_promotion()` → `snapshot_current()`):
requires Elo gain ≥ +10 vs parent over ≥ 20 self-play games AND WR
non-regression vs SC2 AI. Intra-version `PromotionManager` (always-up
Phase 3) keeps running independently on WR delta inside `bots/current/`;
cross-version Elo is the second tier. CLI: `scripts/ladder.py`
(update/show/compare/replay). `GET /api/ladder` endpoint. Ladder tab
(10th dashboard tab) with standings + head-to-head grid. `LadderEntry` +
`PromotionResult` contracts in `contracts.py`. Gate: v0-vs-v0 produces
Elo delta 0 → correctly rejected. 1011 tests, 129 vitest.

---

## Phase 5 — Sandbox enforcement + skill integration ✅

**Status: COMPLETE** (commits land via #118–#122 closures; no
`master-plan/5/final` tag was cut; latest sandbox docs commit `0c9e273`,
2026-04-17). 1020 tests, zero mypy/ruff. Build detail in
`documentation/plans/phase-5-build-plan.md` — read it before reopening this
phase.

`scripts/check_sandbox.py` pre-commit hook + `tests/test_sandbox_hook.py`
(9 cases) wired via `.pre-commit-config.yaml` (`repo: local` + `language:
system`). `pre-commit` added to dev deps. `/improve-bot-advised` SKILL.md
updated: run-start banner, `[advised-auto]` commit tag, `ADVISED_AUTO=1`
env var, `check_promotion()` Elo gate wired in, `--self-improve-code`
path restriction. Gate verified: forbidden file blocked, allowed file
passed, banner matches sandbox scope.

### Sandbox modes (current and planned)

The pre-commit hook recognises two autonomous-commit modes plus the
default human-PR mode. Both autonomous modes share the same hard-deny set;
only the allowed-write path widens for evolve, since a round produces two
sibling `bots/vN/` snapshots in one round.

| Env var | Commit marker | Allowed write paths | Hard deny (always) | Status |
|---------|---------------|--------------------|--------------------|--------|
| (none) | (any) | unrestricted (human PR) | (none) | Default |
| `ADVISED_AUTO=1` | `[advised-auto]` | `bots/current/**` | `src/orchestrator/**`, `tests/**`, `scripts/**`, `pyproject.toml`, `frontend/**` | SHIPPED Phase 5 |
| `EVO_AUTO=1` | `[evo-auto]` | `bots/**` (any version dir) | (same hard-deny set as above) | PENDING Phase 9 |

If a future autonomous mode is added, extend this table — do not split
the hook spec across multiple plan sections.

---

## Phase B — Unit-type histogram observation expansion

**Track:** Capability. **Prerequisites:** Phase 5 (so it runs inside the
sandbox as a skill-driven improvement, or as a human PR on
`bots/current/**`).

> **Build detail lives in
> `documentation/plans/phase-b-build-plan.md`. Read it before starting
> work on this phase.**

**Goal:** Answer "is observation signal the binding constraint on win
rate?" by appending ~23 unit-type histogram slots (15 own-army by type +
~8 enemy-seen) to the PPO observation vector and re-training from
`v0_pretrain`.

### Scope summary

Six build steps (B.1–B.6) covering: own-army histogram → enemy-seen slots
→ `FEATURE_DIM_V2` bump + within-version padding → diagnostic-state
entries → 2-cycle re-train from pretrain → snapshot to `v1` on promotion.

### Tests

`tests/test_features_v2.py`, `tests/test_imitation.py` (padding for
17 → V2 width).

### Effort

~1 day code + cycle wall-clock.

### Validation

Win rate at difficulty 3 equal-or-better across 20 games AND Elo vs prior
`v0` ≥ +10 over 20 self-play games.

### Gate

Both WR-hold AND Elo gain. Either failure → kill.

### Kill criterion

No improvement after 3 cycles — observation signal is not the bottleneck.
Skip to Phase D or E.

### Rollback

Delete `bots/v1/` if it existed; `current` re-forks from `v0`.

### Related: tactical-bugs backlog

The T.1–T.12 tactical bugs surfaced during Phase B Step 5 eval (placement,
target priority, idle armies, attack-walking) are NOT observation work
and live in their own tracker:
**`documentation/sc2/protoss/tactical-bugs.md`**. Several block Phase B Step 6 (v1
snapshot) because games drag out and inflate WR numbers; address those
via `/improve-bot --self-improve-code` or standalone `/build-step` fixes
before the Phase B Validation gate.

---

## Phase D — Build-order z-statistic (reward refactor)

**Track:** Capability. **Prerequisites:** Phase 5. B and D are orthogonal.

> **Build detail lives in
> `documentation/plans/phase-d-build-plan.md`. Read it before starting
> work on this phase.**

**Goal:** Collapse implicit build-order reward rules into an explicit
`z` target vector with edit-distance pseudo-reward, dropping early-game
reward variance.

### Scope summary

8 steps (D.1–D.8): audit reward_rules.json → define `z` schema → write
edit-distance reward module → migrate build-order rules → optional
`z`-as-obs slot → backwards-compat default-off → 3 train cycles →
snapshot on promotion.

### Tests

`tests/test_build_order_reward.py`, `tests/test_reward_migration.py`.

### Effort

~3 days (audit is most of it).

### Validation

Early-game (first 5 min) reward std-dev drops ≥30% AND win-rate holds at
difficulty 3 AND Elo gain ≥ +10 over 20 games.

### Gate

All three validation criteria.

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

> **Build detail lives in
> `documentation/plans/phase-e-build-plan.md`. Read it before starting
> work on this phase.**

**Goal:** Unlock tactical variety by making ATTACK / EXPAND conditional
on target choice rather than flat `Discrete(6)`. New action space size 12.

### Scope summary

6 steps (E.1–E.6): structured `(strategic_state, target)` action space →
custom `ActorCriticPolicy` with two sequential softmax heads → DB
`action_space_version` column → cascade target picking into rules KL
teacher → 6-way → 12-way migration with target inference → snapshot on
promotion. Cross-version concern is none: `v0`/`v1` keep 6-way; the new
version uses 12-way; self-play works because each subprocess loads its
own stack.

### Tests

`tests/test_autoreg_policy.py`, `tests/test_action_migration.py`.

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

> **Build detail lives in
> `documentation/plans/phase-6-build-plan.md`. Read it before starting
> work on this phase.**

**Goal:** Add a cross-version self-play layer on top of the existing
intra-version `TrainingDaemon` loop. PPO-training-driven cross-version
improvement: use H2H self-play results as the RL signal for the
trainee. This is the operational regime that drives B/D/E/F autonomously
once they ship; it's an ongoing mode, not a one-shot phase.

### Scope summary

Wire `/improve-bot-advised --self-improve-code --opponent vN` for
curriculum opponent selection; optional PFSP-lite pool sampling stretch;
dashboard surfacing via existing Ladder + Improvements tabs.

### Tests

N/A — operational phase. Primitives tested in their own phases.

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

### Relationship to Phase 9 (improve-bot-evolve)

Both consume `run_batch`; orthogonal mechanisms. Phase 6 = PPO-training
signal extension; Phase 9 = discrete A/B improvement-pool selection.
Mutually exclusive on the same `bots/current/` working tree (pre-flight
check).

---

## Phase 7 — Advised loop stale-policy detection

**Track:** Operational. **Prerequisites:** Phase 5 (sandbox + skill integration).
Independent of B/D/E/6 — ships standalone.

> **Build detail lives in
> `documentation/plans/phase-7-build-plan.md`. Read it before starting
> work on this phase.**

**Goal:** Teach `/improve-bot-advised` to recognize when the PPO policy
is stale relative to the current reward/hyperparam config and to
schedule an extended training soak as a first-class improvement type
(rather than relying on the user to switch to `/improve-bot --mode
training` manually).

### Scope summary

5 steps (7.1–7.5): `staleness.py` module → extend Claude analysis
prompt with `type: "soak"` improvement → Phase 4 daemon-lifecycle
routing → wall-clock budget guard → tests.

### Tests

`tests/test_staleness_signal.py`, `tests/test_advised_soak_routing.py`.

### Effort

~1 day build + one overnight validation run.

### Validation

8h advised run with recently-edited reward rules: detect staleness →
emit `soak` improvement → run 2–4h soak → post-soak eval shows
measurable Elo delta → loop resumes within budget.

### Gate

All 5 validation steps pass in a single run. Soak-type improvements
must not exceed 50% of the total `--hours` budget across the run.

### Kill criterion

Staleness signal fires on every iteration (false-positive storm) OR
never fires across 3 runs with obviously stale policies. Heuristic is
wrong — revisit step 7.1 before shipping.

### Rollback

Revert the phase's commits on `bots/current/` and
`src/orchestrator/staleness.py`. SKILL.md change is self-contained.
No data migrations.

---

## Phase 9 — improve-bot-evolve (sibling-tournament evolutionary loop)

**Track:** Operational. **Prerequisites:** Phase 5 (sandbox + skill
integration). Independent of B/D/E/6/7 — ships standalone.

> **Build detail and step issues (#154–#161) live in
> `documentation/plans/phase-9-build-plan.md`. Read that document
> before starting work on this phase — the per-step problem statements,
> impact tables, design decisions, risks/open questions, and testing
> strategy are canonical there, not summarised here.**

**Note on numbering:** This phase is **Phase 9** (not Phase 8) to match
the existing `#154–#161` issue titles ("Phase 9 Step N: ...") cut
before the plan-merge. There is no Phase 8 in the master plan — that
slot is intentionally skipped, similar to how Phase C was dropped in an
earlier merge. See plan history (2026-04-19 renumber entry) for context.

**Goal:** Drive cross-version improvement via discrete A/B selection
between two sibling snapshots of the parent, with Claude-generated
improvement-pool items applied one per snapshot, and a parent safety gate
before promotion. Removes the SC2-built-in-AI fitness signal that
`/improve-bot-advised` relies on.

The loop: snapshot parent twice → apply improvement A to one, B to the
other → 10 head-to-head games → if decisive, winner plays parent for 5
games → promote winner if it beats parent ≥ 3/5; otherwise discard both
improvements. Repeat until 10-item pool exhausted, wall-clock budget
expires (default 4h), or 3 consecutive no-progress rounds.

This phase sits **alongside** Phase 6, not inside it. Phase 6 is
PPO-training-driven self-play (use H2H results as the RL signal). Phase 9
is improvement-pool-driven A/B selection (discrete edits, no PPO update
per round). Both consume `run_batch` from `src/orchestrator/selfplay.py`.

### Scope summary

Eight build steps, mapped to issues #154–#161:

| # | Issue | Deliverable |
|---|-------|-------------|
| 1 | #154 | Sandbox hook recognises `EVO_AUTO=1` + `[evo-auto]` (see Phase 5 sandbox-modes table) |
| 2 | #155 | `src/orchestrator/evolve.py`: `Improvement` + `RoundResult` dataclasses, `apply_improvement()` (training + dev paths), `run_round()` |
| 3 | #156 | `generate_pool()`: mirror-seed games + Claude prompt → 10 typed `Improvement` objects |
| 4 | #157 | `scripts/evolve.py` CLI: pool-sample loop, wall-clock guard, state-file writes, commit on promote |
| 5 | #158 | `.claude/skills/improve-bot-evolve/SKILL.md` (sibling skill to `/improve-bot-advised`) |
| 6 | #159 | Backend `/api/evolve/state` + `/api/evolve/control` endpoints + `EvolutionTab.tsx` (the **11th dashboard tab**) |
| 7 | #160 | Operator smoke gate: one round end-to-end with real SC2, `--pool-size 2 --ab-games 3 --gate-games 3` |
| 8 | #161 | Operator extended soak: full `--hours 4` overnight, dev + training improvements both allowed |

### Tests

Per the build doc — covered exhaustively there. Headline:

- `tests/test_evolve.py`: ≥10 cases for `run_round` / `apply_improvement` /
  `generate_pool` with mocked `run_batch` + Claude responses.
- `tests/test_evolve_cli.py`: argparse, wall-clock early-stop,
  pool-exhaustion, state-file writes.
- `tests/test_check_sandbox.py` extended: `EVO_AUTO=1` allow/deny cases.
- `EvolutionTab.test.tsx` (vitest): idle / mid-round / completed views.
- `tests/test_evolve_sc2.py::test_single_round_smoke` (`@pytest.mark.sc2`):
  end-to-end real-SC2 round.

### Effort

Steps 1–6: ~3–5 days code. Steps 7–8: operator wall-clock (1h smoke +
overnight 4h soak).

### Validation

Step 8 morning report contains ≥3 completed rounds with at least one of
each outcome (promote, tie-discard, gate-discard); no orphaned processes
on port 8765 after the run; the pre-commit hook correctly allowed
per-round promotes while blocking any out-of-sandbox edits.

### Gate

- All 8 build steps green per their per-step "Done when" criteria.
- No regression on `/improve-bot-advised` (mutual-exclusion pre-flight
  prevents concurrent runs of the two skills).
- Sandbox hook tests pass for both `ADVISED_AUTO` and `EVO_AUTO` modes.

### Kill criterion

Step 8 soak shows pool exhaustion before any promote across two attempts
with regenerated pools — indicates Claude's pool generation is not
producing sibling-decisive improvements at this codebase's maturity. Pause
Phase 9, return to `/improve-bot-advised` for capability gains, revisit
once Phase B/D/E land more measurable headroom.

### Rollback

Revert the orchestrator + scripts + skill commits. Sandbox hook stays
extended (the `EVO_AUTO` path is no-op without the skill driving it; cost
of leaving is one extra deny-list code path to maintain). Dashboard tab
disable: remove the `EvolutionTab.tsx` route registration. `bots/vN/`
directories accumulated during runs are safe to `rm -rf`.

---

## Phase F — Entity transformer encoder

**Track:** Capability-F. **Status:** Deferred. Only enter if B–E have all
landed and the bot is clearly bottlenecked by loss of per-unit information.

**Prerequisites:** Phases A, B, E merged and promoted. D preferred.

> **Build detail lives in
> `documentation/plans/phase-f-build-plan.md`. Read it before starting
> work on this phase.**

**Goal:** Replace scalar feature trunk with a transformer over a
variable-length unit list to preserve per-unit info (health, shields,
position, type) that the histogram throws away.

### Scope summary

6 steps (F.1–F.6): variable-length pad/mask `BaseFeaturesExtractor` →
2-layer 4-head 64-dim transformer (~100k params, no new dep) →
per-unit feature spec → concat with scalar trunk → fresh
`v0_pretrain_transformer` (cannot reuse prior pretrain — input shape
differs) → A/B vs best B/E version.

### Tests

`tests/test_entity_transformer.py`, `tests/test_imitation_transformer.py`.

### Effort

~2 weeks.

### Validation

Beat best B/E version by ≥ 5% WR over 20 games at difficulty 3 AND
lower loss variance AND Elo gain ≥ +20 over 20 self-play games.

### Gate

All three.

### Kill criterion

Training diverges (NaN, policy collapse) OR no WR improvement after the
full 2-week build. Scalar histogram was sufficient — delete the
transformer version; keep A–E promoted versions.

### Rollback

Trivial: `rm -rf bots/vN+1/`. Prior stacks untouched.

---

## Phase G — Multi-race support (Zerg, then Terran)

**Track:** Multi-race. **Status:** Future. **Prerequisites:** Phase 6
operational (the autonomous loop works end-to-end for Protoss first).

> **Build detail (G.1–G.4 sub-phases, per-race scope, evolve interaction)
> lives in `documentation/plans/phase-g-build-plan.md`. Read it before
> starting work on this phase.**

**Goal:** Extend the bot from Protoss-only to all three SC2 races. Each
race is a separate `bots/<race>_vN/` version line sharing infrastructure
but with its own gameplay code.

### Scope summary (4 sub-phases)

- **G.1** Race interface extraction (RaceConfig / ProductionAdapter /
  MicroAdapter / FeatureSpec / RewardTemplate). Refactor `bots/v0/` to
  use the interfaces with zero behavior change.
- **G.2** Zerg (`bots/zerg_v0/`) — first new race; validates the
  interface deeply.
- **G.3** Terran (`bots/terran_v0/`) — second new race; benefits from
  proven interface.
- **G.4** Cross-race ladder — within-race promotion, cross-race
  informational.

Phase 9 (improve-bot-evolve) gains a `--race` flag and per-race parent
chains when G.2 ships — see Phase G build doc for details.

### Effort

~7–10 weeks total: G.1 = 1–2 w, G.2 = 3–4 w, G.3 = 2–3 w, G.4 = 2–3 d.

### Gates (per sub-phase)

- G.1: All existing Protoss tests pass, interface covers 3 races.
- G.2: Zerg wins ≥50% at difficulty 3 over 20 games.
- G.3: Terran wins ≥50% at difficulty 3 over 20 games.
- G.4: Cross-race Elo ladder produces stable rankings.

### Kill criterion

G.1 interface extraction proves too invasive (breaks >5% of tests or
requires >500 lines of adapter code). Defer and revisit after more
capability phases mature the Protoss codebase.

### Rollback

Each race is its own `bots/` directory — delete and ladder is
unaffected.

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
| 7 | 1 d build | 1 d build + 1 overnight validation | 3 d (heuristic tuning) |
| 9 | 3 d code (steps 1–6) | 3–5 d code + 1 h smoke + overnight soak | 1 w (dev-apply sub-agent edge cases) |
| F | 1.5 w | 2 w | 3 w (training destabilizes) |
| **Sub-total (A–E + 0–5 + 6 wire-up + 7 + 9)** | **~5 w** | **~7–8 w** | **~12–13 w** |
| **+ 20% integration buffer** | +1.0 w | +1.6 w | +2.6 w |
| **Total (excl. F)** | **~6 w** | **~9–10 w** | **~15 w** |
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

- *2026-04-19* — **renumbered evolve from Phase 8 → Phase 9** to align
  with pre-existing GitHub issues #154-#161 (titled "Phase 9 Step N").
  The plan-merge earlier in the session had assigned Phase 8 (next slot
  after Phase 7) without checking the issue-titling convention. Issues
  #154-#161 were cut against an older plan version that numbered evolve
  as Phase 9; renumbering the master plan was cheaper than renaming 8
  issue titles. Master plan now has no Phase 8 (intentional gap, similar
  to how Phase C was dropped in an earlier merge). All body-section
  references updated; plan-history entries left intact (append-only).
  Build doc renamed `improve-bot-evolve-plan.md` →
  `phase-9-build-plan.md` for filename symmetry with other phase build
  docs. 3 inbound references updated (master plan pointer, Phase G
  build doc cross-ref, Phase 6 build doc Relationship section). Memory
  entry `project_evolve_plan.md` updated to reflect Phase 9.

- *2026-04-19* — **data-snapshots consolidation + improvements/ cleanup.**
  Project root had 13 root-level `data-*` and `data.bak-*` snapshot dirs
  (untracked but not gitignored — defensive gap). Consolidated all
  current snapshots into a new `data-snapshots/` parent dir, added
  `/data-snapshots/` to `.gitignore`, updated the
  `.claude/skills/improve-bot/SKILL.md` convention so future
  `data.bak-$TS` and `data.demo-snapshot-$TS` writes land under
  `data-snapshots/` instead of the project root. Tossed 7 pre-2026-04-14
  snapshots (corresponding runs closed). Kept 5 snapshots from
  2026-04-14 onward. Added `data-snapshots/README.md` documenting the
  convention. Updated `documentation/soak-test-runs/README.md` (the
  procedure doc) to reference new snapshot paths. Also moved the orphan
  `documentation/improvements/self-improve/20260414-1127-retry-storm-dedup.md`
  → `documentation/soak-test-runs/improve-2026-04-14-retry-storm-dedup.md`
  (pairs with originating run record) and removed the now-empty
  `documentation/improvements/` subtree.

- *2026-04-19* — **doc-tree platform/domain split.** Established the
  `documentation/sc2/<race>/` convention so platform docs (master plan,
  build plans, wiki, soak-test) stay separable from game-specific docs.
  Moved `protoss_sc2_guiding_principles.md` → `sc2/protoss/guiding-principles.md`,
  `protoss_tech_tree.md` → `sc2/protoss/tech-tree.md`,
  `tactical-bugs.md` → `sc2/protoss/tactical-bugs.md` (filenames
  normalised to kebab-case; redundant `protoss_` prefix dropped since
  the directory carries the race). Phase G's race-specific docs land
  under sibling `sc2/zerg/` and `sc2/terran/`. Co-located the soak-test
  procedure with its run records: `documentation/soak-test.md` →
  `documentation/soak-test-runs/README.md`. Moved the narrative
  `project-history.md` → `wiki/project-history.md` and registered it in
  the Start Here table. Updated 8 live inbound references (advisor
  bridge, advisor test, two SKILL.md files, wiki architecture page,
  master plan, evolve plan, api docstring).

- *2026-04-19* — **plan/build-doc cleanup pass.** Applied the
  pointer-extraction pattern uniformly across every phase:

  - **Tier 1 file moves.** All `phase-N-build-plan.md` (1–5) moved from
    `documentation/` → `documentation/plans/` for path consistency with
    the master plan and `improve-bot-evolve-plan.md`. Renamed
    `documentation/unit-type-histogram-plan.md` →
    `documentation/plans/phase-b-build-plan.md` (this was the Phase B
    build doc all along, just under a non-conforming name). Archived
    completed always-up-era plans (`phase-4-5-backlog.md`,
    `phase-4-7-eval-pipeline-fixes.md`,
    `phase-4-transparency-dashboard-plan.md`,
    `phase-a-buildphase-runbook.md`, `soak-2026-04-11-fixes.md`) →
    `documentation/archived/`. Moved `soak-2026-04-18-selfplay-viewer.md`
    → `documentation/soak-test-runs/`.

  - **Tier 2 pointer extraction.** Created build docs for every
    previously-inline phase: `phase-d-build-plan.md`, `phase-e-build-plan.md`,
    `phase-6-build-plan.md`, `phase-7-build-plan.md`,
    `phase-f-build-plan.md`, `phase-g-build-plan.md`. Each phase in the
    master plan is now a pointer-style summary (Goal, Track, Prereqs,
    Scope summary, Tests, Effort, Validation, Gate, Kill criterion,
    Rollback) with a "read the build doc first" instruction. Phase B
    pointer-ized to reference the existing `phase-b-build-plan.md`.

  - **Tactical bugs extracted.** The T.1–T.12 backlog wedged inside
    Phase B was extracted to `documentation/sc2/protoss/tactical-bugs.md`
    (preserves item numbering and content verbatim, organised into
    Resolved / Open). T.1–T.12 were not master-plan phases and don't
    belong in the strategic plan.

  - **Effect.** Master plan went 1373 → ~1010 lines. Every phase now
    has a single canonical build-doc location. Every parent-plan back
    reference in the moved phase-1..5 build docs was updated.

- *2026-04-19* — merged `documentation/improve-bot-evolve-plan.md` (was a
  freestanding feature plan) into this master plan as **Phase 8** in the
  Operational track. Pointer-style merge: Phase 8 carries the canonical
  fields (Goal, Track, Prereqs, Scope summary, Tests, Effort, Validation,
  Gate, Kill criterion, Rollback) plus a strong pointer back to the
  build doc, which moved to `documentation/plans/improve-bot-evolve-plan.md`
  and stays live as the build-detail source of truth (not archived).

  In the same pass, completed phases A, 0, 1, 2, 3, 4, 5 were compressed
  to one-paragraph summary + pointer to their respective build docs
  (`documentation/phase-N-build-plan.md`, `documentation/wiki/subprocess-selfplay.md`,
  or `documentation/archived/alphastar-upgrade-plan.md`). Master plan
  shrunk from 1574 → ~1340 lines despite adding Phase 8.

  Sandbox spec moved to a single mode-table in the Phase 5 section
  covering both `ADVISED_AUTO=1` (shipped, `bots/current/**`) and
  `EVO_AUTO=1` (Phase 8, `bots/**`) — replacing the prior single-mode
  spec. Phase G picked up an addendum about per-race parent chains for
  evolve once a second race ships. Track structure gained a Track 6
  (Multi-race) and Track 5 grew to include Phase 8. Decision graph
  redrawn to show all completed phases consolidated and operational
  phases (6, 7, 8) as parallel branches off the Phase 5 gate. Time
  budget table gained Phase 7 and Phase 8 rows with corresponding total
  adjustments.

  Recurring rule extracted to memory: any plan past ~2000 lines / 40K
  tokens should refactor completed phases and fully-scoped sub-features
  to summary+pointer, since the Read tool refuses single reads at 25K
  tokens.

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
