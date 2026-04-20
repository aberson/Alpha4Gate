# improve-bot-evolve — Evolutionary Self-Play Plan

## 1. What This Feature Does

`improve-bot-evolve` is an autonomous overnight skill that improves the Alpha4Gate bot
through **pure bot-vs-bot evolution**, with zero reliance on the SC2 built-in AI as a
fitness signal. At each round, the current best bot is snapshotted twice; two different
improvements from a Claude-generated pool are applied (one per snapshot); the two
candidates play 10 head-to-head games via the existing subprocess self-play runner; the
majority winner faces the parent in a 5-game safety gate; if the winner also beats the
parent, it is promoted to `bots/current/` and its improvement is consumed from the pool.
Rounds repeat until the 10-item pool is exhausted or the wall-clock budget (default
4 hours) expires.

Why this is being built: `/improve-bot-advised` uses WR-vs-built-in-AI as its signal,
which (per the user's accumulated memory) rewards overfitting to the scripted AI and
leaves the bot fragile at higher difficulties. Sibling-vs-sibling selection forces
improvements to be decisive against a bot with identical information, which is a
stronger fitness signal than beating a scripted opponent.

## 2. Existing Context

A fresh reader needs the following landmarks to orient:

- **Subprocess self-play runner** — [`src/orchestrator/selfplay.py`](src/orchestrator/selfplay.py)
  exposes `run_batch(p1, p2, games, map_name, ...)`. Boots two `python -m bots.vN`
  subprocesses per game via burnysc2's `BotProcess`. Handles seat alternation,
  port-collision workaround, PID discovery, crash/timeout recording. Each game writes
  a `SelfPlayRecord` to `data/selfplay_results.jsonl`.
- **Snapshot tool** — [`src/orchestrator/snapshot.py`](src/orchestrator/snapshot.py)
  `snapshot_current(name=None) -> Path` copies the version currently pointed to by
  `bots/current/current.txt` to `bots/vN+1/` (full tree: checkpoints, training.db,
  reward_rules.json, hyperparams.json, all code) and updates `current.txt` to the
  new name. This is the primitive for "save the winner as a new version".
- **Ladder + promotion** — [`src/orchestrator/ladder.py`](src/orchestrator/ladder.py)
  has `check_promotion(candidate, parent, games, elo_threshold)` — runs the H2H and
  calls `snapshot_current()` on win. This is the closest existing primitive to what
  `run_round()` needs; `run_round` adds the sibling-vs-sibling primary gate on top.
- **Improvement schema (reused)** — `/improve-bot-advised` Phase 2.2 defines the JSON
  shape Claude returns (`improvements[]` with `rank`, `title`, `type`, `description`,
  `principle_ids`, `expected_impact`, `concrete_change`). Evolve uses the identical
  schema for pool items. Spec at
  [`.claude/skills/improve-bot-advised/SKILL.md`](.claude/skills/improve-bot-advised/SKILL.md).
- **Sandbox hook** — [`scripts/check_sandbox.py`](scripts/check_sandbox.py) + the
  pre-commit hook in `.pre-commit-config.yaml` currently restrict autonomous commits
  (`ADVISED_AUTO=1`, `[advised-auto]` message marker) to `bots/current/**`. This plan
  extends the hook to accept `EVO_AUTO=1` + `[evo-auto]` marker and allow writes
  under `bots/<next_versions>/**` during a round.
- **Sibling skill** — `/improve-bot-advised` runs a single bot in an iterative
  train-validate-commit loop. `improve-bot-evolve` runs two bots in a tournament loop.
  They share the Claude improvement schema, sandbox philosophy, dashboard-control-file
  pattern, and morning-report structure — but they are genuinely parallel skills, not
  successors.
- **Self-play viewer** — [`src/selfplay_viewer/`](src/selfplay_viewer/) provides a
  pygame container that reparents the two SC2 windows into one operator view. Already
  wired into `scripts/selfplay.py`. Evolve defaults viewer OFF (overnight batch) with
  a `--viewer` flag to opt in.
- **Master plan context** — this feature sits **alongside** (not inside) master-plan
  Phase 6 (cross-version self-play loop). Phase 6's loop is PPO-training-driven (use
  self-play results as the RL signal for the trainee). This feature is
  improvement-pool-driven (discrete A/B selection). Both can coexist; they consume the
  same `run_batch` primitive.

## 3. Scope

### In scope

- New skill `.claude/skills/improve-bot-evolve/SKILL.md`.
- New orchestrator module `src/orchestrator/evolve.py` with `run_round()` and
  `generate_pool()`.
- New CLI `scripts/evolve.py`.
- New dashboard tab "Evolution" reading `data/evolve_run_state.json`.
- Sandbox hook extension for `EVO_AUTO=1`.
- Both training-type (reward_rules/hyperparams JSON edits) and dev-type (source code
  edits) improvements supported from v1. Dev-type delegates to a sub-agent with a
  target-dir override so the edit lands in the correct `bots/vN/`.
- 10-item pool, uniform-random sampling without replacement, both improvements
  consumed per round regardless of outcome.
- Parent safety gate: round winner must beat the parent ≥ 3/5 to promote; otherwise
  both improvements are discarded and parent remains base.

### Out of scope (explicitly deferred)

- `--return-loser` flag (reserved, stubbed off): returning the losing improvement to
  the pool for re-sampling. Simpler consume-both bookkeeping for v1.
- Mid-run pool regeneration: 10-item pool is frozen at run start.
- Multi-bot tournaments (>2 per round).
- Cross-race evolution (see §8 Open Questions — multi-race).
- Integration with the `TrainingDaemon` PPO loop. Evolve does not train PPO during
  rounds; it works on whatever checkpoints the current bot ships with.
- Replacing `/improve-bot-advised`. Both skills remain.

## 4. Impact Analysis

| File / Module | Nature | Notes |
|---|---|---|
| [`scripts/check_sandbox.py`](scripts/check_sandbox.py) | **Modify** | Recognise `EVO_AUTO=1` env + `[evo-auto]` message marker. When set, allow writes under `bots/<any>/**` (since evolve round creates `vN+1`, `vN+2` and edits each). |
| `.pre-commit-config.yaml` | **Modify** | Ensure the hook picks up the new env var. |
| `src/orchestrator/__init__.py` | **Modify** | Export `evolve` submodule. |
| `src/orchestrator/__main__.py` | **Modify** | Optionally add `evolve` subcommand wrapping `scripts/evolve.py`. |
| [`documentation/plans/alpha4gate-master-plan.md`](documentation/plans/alpha4gate-master-plan.md) | **Modify** | Add a line under Phase 6 noting this feature as a parallel track. |
| `frontend/src/App.tsx` (or routing) | **Modify** | Register the new "Evolution" tab. |
| `frontend/src/api/useApi.ts` (or equivalent) | **Modify** | Add `/api/evolve/state` fetch + cache key. |
| `bots/v0/runner.py` (or API server) | **Modify** | Add `/api/evolve/state` + `/api/evolve/control` read/write endpoints, modelled on `/api/advised/*`. |

| File / Module | Nature | Notes |
|---|---|---|
| `src/orchestrator/evolve.py` | **Create** | `run_round()`, `apply_improvement()`, `generate_pool()`, `RoundResult` dataclass. |
| `scripts/evolve.py` | **Create** | CLI wrapping the loop. |
| `.claude/skills/improve-bot-evolve/SKILL.md` | **Create** | Skill definition. |
| `frontend/src/components/EvolutionTab.tsx` | **Create** | Dashboard tab. |
| `tests/test_evolve.py` | **Create** | Unit tests for `run_round` / `apply_improvement` / `generate_pool` with mocked `run_batch` + mocked Claude responses. |
| `tests/test_check_sandbox.py` | **Extend** | Cases for `EVO_AUTO=1`. |
| `frontend/src/components/__tests__/EvolutionTab.test.tsx` | **Create** | Vitest component tests. |

## 5. New Components

### `src/orchestrator/evolve.py`

Core orchestrator module. Public surface:

- `@dataclass Improvement` — mirrors the JSON schema: `rank`, `title`, `type`
  (`"training" | "dev"`), `description`, `principle_ids`, `expected_impact`,
  `concrete_change`.
- `@dataclass RoundResult` — `parent`, `candidate_a`, `candidate_b`, `imp_a`,
  `imp_b`, `ab_record` (list of 10 SelfPlayRecord), `gate_record` (list of 5 or
  None if tie), `winner` (str | None), `promoted` (bool), `reason` (str).
- `apply_improvement(version_dir: Path, imp: Improvement) -> None` — for `training`
  type: edit `version_dir / "data" / "reward_rules.json"` or `hyperparams.json` per
  `concrete_change`. For `dev` type: spawn a sub-agent with the concrete change +
  target dir; sub-agent edits source files under `version_dir/`. Quality gates run
  after dev edits.
- `run_round(parent: str, imp_a: Improvement, imp_b: Improvement, *, ab_games: int = 10, gate_games: int = 5, map_name: str = "Simple64") -> RoundResult` —
  the full round primitive. Creates two snapshots, applies improvements, runs A vs B,
  runs gate if decisive, promotes or discards.
- `generate_pool(parent: str, *, mirror_games: int = 3, pool_size: int = 10) -> list[Improvement]` —
  runs `run_batch(parent, parent, mirror_games)`, reads the logs, constructs a Claude
  prompt with game data + bot source tree listing + guiding principles, parses the
  returned JSON into `Improvement` objects.

### `scripts/evolve.py`

CLI wrapping the skill's mechanics for direct invocation and for the skill to shell
out to. Flags:

- `--pool-size` (default 10)
- `--ab-games` (default 10)
- `--gate-games` (default 5)
- `--hours` (default 4)
- `--map` (default `Simple64`)
- `--viewer` (default off; when set, shells through `selfplay_viewer` per round)
- `--return-loser` (reserved; raises `NotImplementedError` if set in v1)
- `--results-path` (default `data/evolve_results.jsonl`)
- `--run-log` (default `documentation/soak-test-runs/evolve-<date>.md`)

### `.claude/skills/improve-bot-evolve/SKILL.md`

Phase-structured skill doc following the improve-bot-advised template:

- **Phase 0 — Bootstrap:** sandbox banner (EVO_AUTO scope), pre-flight (git state,
  SC2 installed, quality gates), seed pool generation, baseline tag
  `evolve/run/<ts>/baseline`, start API-only backend.
- **Phase 1 — Seed + Pool:** run 3 mirror games (`run_batch(current, current, 3)`);
  call `generate_pool()`; write `data/evolve_pool.json`.
- **Phase 2 — Evolution loop (one iteration = one round):**
  - Sample 2 improvements uniform-random from remaining pool.
  - Call `run_round(...)`; capture `RoundResult`.
  - Update `data/evolve_run_state.json`.
  - On promote: commit with `EVO_AUTO=1 [evo-auto] evolve: round N promoted <imp>`.
  - On discard: log reason, no commit.
  - Mark both improvements consumed in `evolve_pool.json`.
- **Phase 3 — Loop decision:** wall-clock check; pool-exhaustion check; consecutive
  no-progress check (3 discards in a row → stop).
- **Phase 4 — Morning Report:** `evolve/run/<ts>/final` tag, GitHub issue, report
  with promotion chain + consumed improvements.

### Dashboard "Evolution" tab

Reads `data/evolve_run_state.json`. Shows:

- Parent version name + git SHA + cumulative rounds promoted.
- Pool: 10 items with status (`active`, `consumed-won`, `consumed-lost`, `consumed-tie`).
- Round table: round #, imp A, imp B, A-vs-B score, winner, gate score, result
  (promoted | discarded-tie | discarded-gate | discarded-crash).
- Current round progress (if mid-round): which game is running, A or B label, etc.
- Control file write: `data/evolve_run_control.json` with `stop_run`, `pause_after_round`.

## 6. Design Decisions

**D1. (1+λ)-ES with parent safety gate.** Each round plays A vs B (10 games) and if
decisive, the winner must also beat the parent (≥3/5) before being promoted. Consumes
both improvements either way. *Alternative considered:* pure A-vs-B with no parent
gate (more evolutionary-pure). *Rejected because:* the pool is Claude-generated and
some improvements will be actively harmful; without a parent gate a single pair of
bad improvements can promote a regression and taint all future rounds. *Alternative
considered:* return-loser-to-pool. *Deferred because:* bookkeeping complexity and
uncertain benefit at 10-item scale; `--return-loser` flag reserved.

**D2. Pool generation via mirror-seed + code reading.** 3 parent-vs-parent mirror
games produce the game data (in-protocol "stalemates where can't my bot finish the
other") alongside the bot's source tree and `sc2/protoss/guiding-principles.md`,
all sent to Claude as one prompt returning 10 ranked improvements. *Alternative
considered:* built-in-AI observation games for seed data. *Rejected by user* — the
whole point is to escape SC2-AI-dependence. *Alternative considered:* pure code
reading (no games). *Rejected because:* mirror games reveal dynamic weaknesses
(stalemate patterns, timer losses) that static code reading misses.

**D3. Sandbox extension via `EVO_AUTO=1`.** Evolve writes to two sibling `bots/vN/`
dirs per round, not just `bots/current/`. The pre-commit hook is extended to recognise
`EVO_AUTO=1` env + `[evo-auto]` marker and allow writes across `bots/**` during an
evolve run. *Alternative considered:* temporarily flipping `current.txt` to point at
each candidate in turn. *Rejected because:* fragile and racy; explicit env var is
clearer and auditable.

**D4. Snapshot approach (not worktrees).** Each round creates two new `bots/vN/` via
`snapshot_current()` twice. *Alternative considered:* git worktrees so losing rounds
leave no trace. *Rejected because:* the subprocess self-play runner requires the bot
to be importable as `bots.vN` from the orchestrator's repo root; worktrees don't
give that without invasive rewiring. Post-run cleanup (`rm -rf` of non-winner
versions) can be added later if `vN` accumulation is a problem.

**D5. Both training and dev improvements from v1.** Training edits are fast and
safe (reward_rules.json / hyperparams.json). Dev edits delegate to a sub-agent with
an explicit target directory, running pytest/mypy/ruff after each edit. *Alternative
considered:* training-only v1. *Rejected by user* — wanted dev upfront to stress-test
the apply path early.

**D6. Parallel to master-plan Phase 6, not subsuming it.** Phase 6 is PPO-training-
driven self-play (use H2H results as the RL signal); evolve is improvement-pool-driven
A/B selection. They share `run_batch` but are orthogonal mechanisms.

**D7. Consume-both bookkeeping.** After each round, both sampled improvements are
removed from the pool regardless of outcome. Keeps "how many rounds left" predictable
(≈ `pool_size / 2`) and sidesteps questions like "does a discarded improvement get to
try again against a different sibling". Revisit if early runs show high false-discard
rates.

**D8. Viewer off by default.** Overnight batch runs are the target use case;
operators can opt in with `--viewer` for debugging.

## 7. Build Steps

### Step 1: Sandbox hook recognises `EVO_AUTO=1`

- **Issue:** #154
- **Problem:** Extend `scripts/check_sandbox.py` to accept `EVO_AUTO=1` as a second
  autonomous-commit mode. When set, allow writes under `bots/**` (any version dir),
  not just `bots/current/**`. Recognise `[evo-auto]` as the equivalent of
  `[advised-auto]` in commit messages. Add test cases for both the allow and block
  paths to `tests/test_check_sandbox.py`.
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** Updated `scripts/check_sandbox.py`, updated
  `.pre-commit-config.yaml` if needed, extended `tests/test_check_sandbox.py`.
- **Done when:** Tests pass for `EVO_AUTO=1` + `bots/v99/foo.py` edit (allowed) and
  `EVO_AUTO=1` + `src/orchestrator/foo.py` edit (blocked).
- **Depends on:** none.
- **Status:** DONE (2026-04-20)

### Step 2: `src/orchestrator/evolve.py` — round primitive

- **Issue:** #155
- **Problem:** Create `src/orchestrator/evolve.py` with `Improvement` dataclass,
  `RoundResult` dataclass, `apply_improvement(version_dir, imp)`, and
  `run_round(parent, imp_a, imp_b, *, ab_games=10, gate_games=5, map_name='Simple64')`.
  `apply_improvement` handles both `training` (JSON edit of `reward_rules.json` or
  `hyperparams.json` inside the version dir) and `dev` (spawn a sub-agent with the
  target `version_dir` and `concrete_change`; sub-agent runs pytest/mypy/ruff and
  reverts on failure). `run_round` calls `snapshot_current()` twice, applies
  improvements, calls `run_batch(A, B, ab_games)`, determines A/B winner by majority
  (tie → no promotion), on decisive winner calls `run_batch(winner, parent, gate_games)`,
  promotes to `bots/current/` only if winner ≥ majority, discards losing + unpromoted
  version dirs (`rm -rf bots/<loser>/`), returns `RoundResult`. Unit tests mock
  `run_batch` + Claude so no SC2 is required.
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** `src/orchestrator/evolve.py`, `tests/test_evolve.py` with ≥10 cases
  covering: training-imp apply, dev-imp apply, A-beats-B-beats-parent promote path,
  A-beats-B-loses-to-parent discard path, tie-in-AB discard path, crash-during-AB
  handling, concurrent snapshot name collision, version-dir cleanup on discard.
- **Done when:** Tests pass; `evolve` exports surface through `src/orchestrator/__init__.py`.
- **Depends on:** Step 1.
- **Status:** DONE (2026-04-20)

### Step 3: `generate_pool()` — mirror-seed + Claude pool generation

- **Issue:** #156
- **Problem:** Add `generate_pool(parent, *, mirror_games=3, pool_size=10) -> list[Improvement]`
  to `src/orchestrator/evolve.py`. Run `run_batch(parent, parent, mirror_games)`,
  collect resulting `SelfPlayRecord`s and matching log files from `logs/`, list the
  bot source tree under `bots/<parent>/`, read
  `documentation/sc2/protoss/guiding-principles.md`. Construct a Claude prompt
  including all three; parse the JSON response into `Improvement` objects; validate
  each against the dataclass schema; raise a clear error on malformed output. Unit
  tests use a fixture Claude response and a mocked `run_batch`.
- **Flags:** `--reviewers code`
- **Produces:** `generate_pool()` + prompt-template constant in `evolve.py`;
  additional tests in `tests/test_evolve.py` for malformed-JSON handling,
  wrong-schema items, zero-item response fallback.
- **Done when:** With fixture Claude response, returns exactly 10 well-typed
  `Improvement` objects; malformed responses raise a clear `ValueError` with the
  offending snippet in the message.
- **Depends on:** Step 2.
- **Status:** DONE (2026-04-20)

### Step 4: `scripts/evolve.py` CLI + orchestration loop

- **Issue:** #157
- **Problem:** Create `scripts/evolve.py` wrapping the round loop: pre-flight
  (SC2 + git + quality gates), call `generate_pool()`, loop sampling 2 items uniform
  random, call `run_round()`, update `data/evolve_pool.json` + `data/evolve_run_state.json`
  after each round, commit on promote (`EVO_AUTO=1 [evo-auto]` message), respect
  wall-clock budget, stop on pool-exhaustion or 3 consecutive no-progress rounds.
  `--return-loser` flag defined but raises `NotImplementedError` if set.
- **Flags:** `--reviewers code`
- **Produces:** `scripts/evolve.py`, JSONL log writer helpers, tests in
  `tests/test_evolve_cli.py` covering argument parsing, wall-clock early-stop,
  pool-exhaustion stop, state-file writes.
- **Done when:** `python scripts/evolve.py --hours 0 --pool-size 2` runs end-to-end
  with mocked inner calls and writes the expected state files.
- **Depends on:** Step 3.
- **Status:** DONE (2026-04-20)

### Step 5: Skill definition

- **Issue:** #158
- **Problem:** Create `.claude/skills/improve-bot-evolve/SKILL.md` mirroring the
  phase structure of `/improve-bot-advised` but adapted for the evolutionary loop.
  Include: sandbox banner (EVO_AUTO scope), zero-input contract (flags only, no
  interactive questions), phase 0 pre-flight with automated resolution of common
  dirty-git states, phase 1 seed + pool, phase 2 round loop, phase 3 loop decision,
  phase 4 morning report with promotion chain. Document every `data/evolve_*.json`
  file written and when it's written. Document the dashboard control-file protocol
  (`stop_run`, `pause_after_round`).
- **Flags:** (defaults)
- **Produces:** `.claude/skills/improve-bot-evolve/SKILL.md`.
- **Done when:** Skill registers in the user-invocable skills list; `/improve-bot-evolve`
  with no flags enters phase 0 without asking any questions; skill references pass
  `/skill-xref`.
- **Depends on:** Step 4.
- **Status:** DONE (2026-04-20)

### Step 6: Dashboard Evolution tab + API endpoints

- **Issue:** #159
- **Problem:** Add backend endpoints `GET /api/evolve/state` (reads
  `data/evolve_run_state.json`) and `POST /api/evolve/control` (writes
  `data/evolve_run_control.json`). Add React tab "Evolution" showing: parent version
  + cumulative promotions, 10-item pool with consumed/active markers, round table
  (round #, imp A, imp B, A-vs-B score, gate score, outcome), current-round
  progress indicator, stop-run button. Vitest component tests for all three views
  (idle, mid-round, completed-run).
- **Flags:** `--reviewers full --ui`
- **Produces:** `frontend/src/components/EvolutionTab.tsx`, backend endpoint
  additions, new vitest tests, Playwright screenshot evidence for idle and
  mid-round states.
- **Done when:** Tab renders correctly for all three state shapes; stop-run button
  writes the control file; no regressions on existing dashboard tabs per
  `/a4g-dashboard-check`.
- **Depends on:** Step 5.
- **Status:** DONE (2026-04-20) — code shipped with `--reviewers code`; Playwright/runtime UI evidence deferred to `/a4g-dashboard-check` before Step 7 smoke gate.

### Step 7: Smoke gate — one-round end-to-end

- **Issue:** #160
- **Problem:** Operator runs `python scripts/evolve.py --pool-size 2 --ab-games 3 --gate-games 3 --hours 1`
  with real SC2 running. Goal: exactly one round completes from seed-mirror →
  Claude pool → snapshot × 2 → apply training improvement × 2 → `run_batch(A,B,3)` →
  either promote with commit or discard. The commit (if any) passes the extended
  sandbox hook. State file is correctly updated throughout. Training-only
  improvements only (manually filter pool to `type: training` for this gate to
  keep the observation window short).
- **Type:** operator
- **Done when:** One round completes without crash; `data/evolve_run_state.json`
  reflects the expected shape at each phase; if the round promoted, the commit lands
  with `[evo-auto]` marker and passes pre-commit; if it discarded, no commit lands
  and the `no_progress_streak` counter increments by 1.
- **Depends on:** Step 6.
- **Status:** DONE (2026-04-20) — operator PASS; Evolution dashboard tab verified populating live.

### Step 8: Extended soak — multi-round overnight observation

- **Issue:** #161
- **Problem:** Operator runs a full `python scripts/evolve.py --hours 4` overnight
  with default pool size 10, dev + training improvements both allowed. Observe for:
  (a) eval-queue deadlock or backend port conflicts between rounds,
  (b) orphaned SC2/Python processes after a crashed round,
  (c) `bots/vN/` disk growth across many rounds (checkpoints are MB each),
  (d) mid-round crash recovery — if a game times out, does the round still return
  a valid `RoundResult`?,
  (e) dashboard tab staying live throughout,
  (f) the pre-commit hook correctly allowing per-round promotes while blocking any
  out-of-sandbox edits.
- **Type:** wait
- **Done when:** Run completes (or stops cleanly on pool exhaustion / consecutive
  failures / wall-clock). Morning report contains ≥3 completed rounds with at least
  one of each outcome (promote, tie-discard, gate-discard) across the run history.
  No orphaned processes on port 8765 after the run ends. No unintended files
  committed. Memory + CLAUDE.md updated with any operational lessons learned.
- **Depends on:** Step 7.

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| Mirror-match stalemates | Parent-vs-parent often ends via burnysc2 tie-breakers (army value, HP total); `winner=None` for most seed games. Claude's prompt must handle "no decisive winner" as the common case, not the edge case. | Prompt template explicitly reframes: "analyse why neither side could close out"; seed mirror game count is 3 (not 1) so at least some asymmetric outcomes surface. |
| `vN` directory accumulation | 10-round run with dev-type improvements can create 20+ `bots/vN/` dirs at multiple MB each. Disk footprint grows linearly. | `run_round()` deletes the losing and non-promoted directories at end of round. `--keep-losers` debug flag (not in v1) could preserve them for inspection. |
| Dev-apply sub-agent edits drifting | The dev-type `apply_improvement` path spawns a sub-agent to edit files in a specific `bots/vN/`; the sub-agent might edit files outside the target dir or fail to run quality gates. | `apply_improvement` enforces `EVO_AUTO=1` + target-dir path check; sub-agent is prompted with the exact target dir and the skill runs pytest/mypy/ruff after the sub-agent returns, reverting on failure. |
| Claude returns < 10 improvements | Pool generation might occasionally return 5–9 items on a short prompt. | `generate_pool` retries once with explicit "return exactly 10" instruction; if still short, pads with a repeated "null" improvement (no-op); if empty, aborts with clear error. |
| Sandbox hook edge cases | `EVO_AUTO=1` allows writes under `bots/**` — if a dev-type improvement accidentally touches `src/orchestrator/` or `tests/`, the hook still needs to block. | Hook logic: `EVO_AUTO=1` broadens the `bots/` allow-list but keeps `src/orchestrator/`, `tests/`, `scripts/`, `pyproject.toml` on the deny-list. Covered by Step 1 tests. |
| False discards (both improvements actually good but lost on noise) | 10 games is high-variance; a 6-4 loss can be coin-flip. A good improvement might get consumed-lost. | Accepted as noise tax; mitigation is the larger pool context (many rounds) + the `--return-loser` option reserved for v2. |
| Multi-race (OPEN QUESTION) | Currently the bot is Protoss-only. When Terran/Zerg bots land, the mirror-seed assumption ("parent vs same-race parent") still works, but improvements selected under Protoss-vs-Protoss evolution may not generalise to cross-race matchups. Experiment design for "evolve vs race X while validating vs Y" is unresolved. | **Flagged for future work.** Not in scope for v1. When second race lands, the skill may need a `--race` flag + separate parent chains per race, or a cross-race gate that runs a second safety check against a different-race parent. |
| Pool exhaustion before any promote | If every round discards (bad pool + strict parent gate), 5 rounds consume the pool with no progress. | Consecutive-no-progress stop after 3 discards in a row — fail fast, let operator regenerate pool or adjust gate threshold. Report makes this outcome visible. |
| Overlap with `/improve-bot-advised` | Operators may conflate the two skills; running both concurrently on the same repo could stomp `bots/current/`. | Pre-flight check: refuse to start if `data/advised_run_state.json` exists with `status: running`; and vice-versa in `/improve-bot-advised`. |

## 9. Testing Strategy

### Unit tests (no SC2 required)

- `tests/test_evolve.py`: `run_round` with mocked `run_batch` + mocked
  `apply_improvement` covering promote / gate-fail / AB-tie / crash paths; `generate_pool`
  with fixture Claude response covering well-formed / malformed / short-pool cases;
  `apply_improvement` for both training (JSON edit verifiable by reading the file)
  and dev (mocked sub-agent, quality-gate pass/fail).
- `tests/test_evolve_cli.py`: CLI argparse, wall-clock early-stop, pool-exhaustion,
  state-file writes, `--return-loser` raises `NotImplementedError`.
- `tests/test_check_sandbox.py` (extended): `EVO_AUTO=1` allows `bots/v99/foo.py`,
  blocks `src/orchestrator/foo.py`, blocks `tests/foo.py`.

### Vitest component tests

- `EvolutionTab.test.tsx`: renders correctly for `status: idle`, `status: running`
  with mid-round state, `status: completed` with full round history; stop-run button
  POSTs to `/api/evolve/control`.

### SC2 integration tests (@pytest.mark.sc2)

- One new test `tests/test_evolve_sc2.py::test_single_round_smoke` that runs
  `run_round(parent='v0', imp_a=<noop>, imp_b=<noop>, ab_games=2, gate_games=2)`
  end-to-end with real SC2. Marked skip-if-no-sc2. Used by Step 7 as the smoke gate.

### Regression guards

- Running evolve should not break `/improve-bot-advised` — verified by the
  pre-flight mutual-exclusion check and by the existing advised test suite
  continuing to pass.
- The extended sandbox hook must still enforce the `ADVISED_AUTO=1` rules
  unchanged. Covered by the existing check_sandbox tests continuing to pass
  alongside the new `EVO_AUTO` tests.

### End-to-end verification

- Step 7 (smoke gate) and Step 8 (extended soak) are the operator-run end-to-end
  verifications. Both produce artefacts (state files, commits, tags, morning
  report) that are inspected manually before declaring the feature complete.

## Related documents

- Sibling skill: [`.claude/skills/improve-bot-advised/SKILL.md`](.claude/skills/improve-bot-advised/SKILL.md)
- Self-play infrastructure: [`src/orchestrator/selfplay.py`](src/orchestrator/selfplay.py),
  [`src/orchestrator/snapshot.py`](src/orchestrator/snapshot.py),
  [`src/orchestrator/ladder.py`](src/orchestrator/ladder.py)
- Master plan context: [`documentation/plans/alpha4gate-master-plan.md`](documentation/plans/alpha4gate-master-plan.md)
  (this feature sits alongside master-plan Phase 6)
- Sandbox spec: [`scripts/check_sandbox.py`](scripts/check_sandbox.py) +
  `.pre-commit-config.yaml`
- Principles reference (consumed by `generate_pool`):
  `documentation/sc2/protoss/guiding-principles.md`
