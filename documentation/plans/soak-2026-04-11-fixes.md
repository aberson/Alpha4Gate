# Soak 2026-04-11 — Fix Plan

## Why this exists

The soak run on 2026-04-11 (`documentation/soak-test-runs/soak-2026-04-11.md`) ran for ~35 minutes from T0=22:56 local before being stopped early (one error away from the #73 watchdog firing). It surfaced **6 concrete findings**, several with stack-trace-level diagnoses. This plan turns the codable subset into a sequence of build-step / build-step-tdd tasks suitable for autonomous execution via `/build-phase`.

**Scope:** the 6 steps below address the 4 most impactful findings (5 / 2 / 4 / 6) plus a related replay-filename fix and a low-priority headless SC2 investigation. **Out of scope:** Finding 1 (PPO win-rate quality is research, not a code fix), the §3.2 stale-gate doc tweak, and the cosmetic dashboard items — those are tracked in the soak run log and can be addressed in follow-ups.

**Validation strategy:** every step is unit-test-validated. SC2 client does NOT need to be running for any step in this plan. The fresh soak that validates the cluster end-to-end is a separate post-merge run, not part of build-phase.

## Reading order for a fresh-context model

1. This file
2. `documentation/soak-test-runs/soak-2026-04-11.md` — full Findings table with evidence rows for every step here
3. `CLAUDE.md` and `AGENTS.md` for project conventions (Python 3.12, uv, ruff, mypy strict, 742 baseline tests)
4. The specific source files referenced in each step's "Files" section

---

## Steps

### Phase 4.6 Step 1: Fix `game_id` collision + stop silent exception fallthrough

- **Status:** DONE (2026-04-11, iter 2/3, reviewers=code)
- **Issue:** #75
- **Problem:** During soak-2026-04-11 cycle 5, the trainer hit `sqlite3.IntegrityError: UNIQUE constraint failed: games.game_id` in `database.py:194` `store_game()` called from `environment.py:282` `_sync_game()`. Full stack trace in `documentation/soak-test-runs/soak-2026-04-11.md` Finding 5 evidence row. The exception was caught and swallowed somewhere up the call stack — the cycle continued, reported `win_rate=0.29`, and saved checkpoint v5 as if nothing happened. This is the silent-exception-fallthrough pattern from `feedback_silent_exception_fallthrough.md`.

  **Two changes required:**
  1. Make `_game_id` allocation collision-free under timeout-induced retry. The current allocation appears to recycle / reuse IDs after a `Timeout waiting for observation from game thread` ERROR. Audit how `_game_id` is generated in `learning/environment.py` and switch to a strictly monotonic / UUID-based scheme that cannot collide even when a previous game's row is still in the WAL.
  2. Stop swallowing the exception. When `store_game()` raises, the cycle's win_rate computation must mark the failed game as "errored / excluded" rather than silently treating it as a played game. Either propagate the exception up or record the failure explicitly and exclude from win_rate.

  **Tests required:**
  - Repro test that simulates two `store_game` calls with the same `game_id` (with the second triggered after a `Timeout waiting for observation`-style abandonment) and asserts the second uses a fresh ID and does not raise.
  - Test that injects an `IntegrityError` into `store_game` and asserts the cycle's `win_rate` calculation excludes the failed game (or that the exception propagates, depending on the chosen semantics — pick one and document).
  - Existing tests still pass (`uv run pytest`).

- **Files (initial guess; expand as needed):**
  - `src/alpha4gate/learning/environment.py` (`_FullTrainingBot._game_id` allocation, `_sync_game`, `_run_game_thread`)
  - `src/alpha4gate/learning/database.py` (only if you need to inspect the schema; the bug is upstream)
  - `tests/test_learning_environment.py` (or wherever the trainer's game-recording tests live)

- **Out of scope:**
  - Fixing the underlying eval-game-timeout pattern (separate root cause, possibly related but tracked separately).
  - Win-rate quality improvements (Finding 1).

- **Flags:** `--isolation worktree --reviewers full` (this is the candidate-blocker bug; full reviewer gauntlet is appropriate)

### Phase 4.6 Step 2: Wire trainer environment to legacy per-game producers (Stats, Replays, Model Comparison, Reward Trends)

- **Status:** DONE (2026-04-11, iter 1/3, reviewers=code)
- **Issue:** #76
- **Architectural revelation:** Model Comparison was NOT broken — it's a per-cycle rollup (the "4 rows" operator saw = 4 cycles, not 4 games, grouping ~50 games into per-cycle summaries via `SELECT ... GROUP BY model_version`). Reward Trends "Scanned 8 games" had a one-line root cause: `reward_calc.open_game_log()` was called once per cycle in `trainer._make_env` instead of per-game in `SC2Env.reset()`, so all games in one cycle appended to the same `.jsonl` file. Moved the call into `reset()`; fix is surgical.
- **Problem:** The trainer's `learning/environment.py` runs SC2 through its own connection path and bypasses `connection.run_bot`. As a result, every per-game producer that lives in `connection.py` / `runner.py` / `batch_runner.py` is never called for trainer games. After ~50 trainer games during soak-2026-04-11, the dashboard surfaces showed: Stats=1 game, Model Comparison=4, Reward Trends=8, Replays=1 (mtime = seed game time, not overwritten — simply not written). The Live tab and Loop tab work fine because they read from a bot-layer broadcast queue that the trainer participates in.

  **Architectural decision (locked in by this plan):** call the legacy producers from `learning/environment.py` after each game completes. Do NOT hoist the producers up to the bot layer — that's a bigger refactor and the additive call is much smaller.

  **Specifically, after each trainer game completes and `_sync_game` finishes, call:**
  1. `save_stats()` (or its single-game equivalent — extract one if needed) so `data/stats.json` is updated with the game record (map, opponent, result, duration, build_order_used).
  2. `save_replay()` from `connection.py` (or its equivalent) so the replay file is written. **NOTE:** this also requires Step 5 (replay filename uniqueness) to land first or simultaneously, otherwise every game overwrites the same file.
  3. The Model Comparison aggregator update — find where rule-based / batch games update Model Comparison and call the same hook.
  4. The Reward Trends aggregator update — same pattern.

  **Tests required:**
  - Unit test with mocked filesystem / DB that runs a fake trainer game through the new path and asserts each producer was called exactly once with the expected arguments.
  - Unit test that asserts the producers are NOT called twice when the game errors mid-cycle (defensive — should not double-record).
  - Existing tests still pass.

- **Files (initial guess):**
  - `src/alpha4gate/learning/environment.py`
  - `src/alpha4gate/connection.py` (extract `save_replay` so it's callable from outside the run_bot flow)
  - `src/alpha4gate/batch_runner.py` (extract the per-game record helper from `save_stats` so it can be called incrementally)
  - `tests/test_learning_environment.py`

- **Out of scope:**
  - Decision Log producer (Step 3 below).
  - Replay filename uniqueness (Step 5 below — but coordinate with this step since they share files).
  - Hoisting any producers to the bot layer.

- **Depends on:** none (independent of Step 1)
- **Coordinates with:** Step 5 (replay filename) — these touch overlapping files, and Step 5 must land at least the filename change before Step 2's `save_replay` call is useful.

- **Flags:** `--isolation worktree --reviewers full`

### Phase 4.6 Step 3: Add Decision Log producer

- **Status:** DONE (2026-04-11, iter 2/3, reviewers=code)
- **Issue:** #77
- **Scope locked at Claude advisor only.** Rule-based decision engine and PPO action audit are follow-ups. New `audit_log.py` module with canonical GC-resistant asyncio pattern (pending-broadcasts set + done_callback). ClaudeAdvisor threads data_dir/ws_manager through as optional kwargs for backward compat.
- **Problem:** The Decision Log feature is dead on arrival across all game modes. `GET /api/decision-log` reads `data/decision_audit.json` ([api.py:319-326](../../src/alpha4gate/api.py#L319-L326)) but no code in `src/` writes that file. `/ws/decisions` broadcasts via `broadcast_decision()` ([web_socket.py:52](../../src/alpha4gate/web_socket.py#L52)) but no code calls it. Even rule-based games with the Claude advisor (which DOES log decisions to stdout via the Python logger) don't reach the audit pipeline.

  **Scope decision (locked in by this plan):** start narrow. Wire the **Claude advisor's responses** into the audit log only — that's the most concrete decision producer in the codebase. Defer rule-based decision-engine audit and PPO action audit to follow-up issues; trying to do all three in one step risks a vague scope.

  **Specifically:**
  1. Add a small `audit_log.py` helper (or extend an existing module) that exposes `record_decision(decision: dict)` which both appends to `data/decision_audit.json` AND calls `ws_manager.broadcast_decision()`. Use the same path resolution as `api.py:322` so the API and producer agree on the location.
  2. Call `record_decision(...)` from `claude_advisor.py` after each successful advisor response, with fields: timestamp, game_time, request_summary, response_commands, model used.
  3. The JSON file format must match what `api.py:322-326` reads: `{"entries": [...]}` — verify before writing.

  **Tests required:**
  - Unit test that records a decision via the helper and asserts the JSON file is updated and `broadcast_decision` is called with the correct payload.
  - Unit test that simulates a Claude advisor response and asserts `record_decision` is called.
  - Existing tests still pass.

- **Files (initial guess):**
  - New: `src/alpha4gate/audit_log.py` (or add helper to an existing utility module)
  - `src/alpha4gate/claude_advisor.py`
  - `tests/test_audit_log.py` (new)
  - `tests/test_claude_advisor.py` (extend if exists)

- **Out of scope:**
  - Rule-based decision-engine audit. Track as follow-up.
  - PPO action audit. Track as follow-up.
  - Frontend changes — the existing `DecisionQueue.tsx` already fetches `/api/decision-log`, so once the producer writes the file, the dashboard should populate automatically.

- **Depends on:** none
- **Flags:** `--isolation worktree --reviewers code`

### Phase 4.6 Step 4: Persist promotion records when first-checkpoint auto-promote fires

- **Status:** DONE (2026-04-11, iter 2/3, reviewers=code)
- **Issue:** #78
- **Architectural revelation:** The gap was UNIVERSAL, not first-baseline-specific. Every promotion decision (first-baseline, win-rate-gate, rejected) was invisible to the dashboard because `TrainingDaemon` never called `PromotionLogger.log_decision()` after `pm.evaluate_and_promote()`. Also, the dashboard polls `/api/training/promotions/history` (not `/promotions` as the plan assumed). Fix: added `reason_code` field with 6 stable codes, wired the logger into the daemon, added corrupt-JSON self-heal, aligned rollback schema. Iter 2 addressed test quality's silent-exception-fallthrough + concurrent-writers gap.
- **Problem:** When the promotion gate auto-promotes the first checkpoint as a baseline (`No current best checkpoint -- promoting v5` log line at 23:13:40 in soak-2026-04-11), the log line fires but `/api/training/promotions` continues to return `[]`. The Improvements tab cannot show the first-ever promotion. Either the persistence call is missing, or the API reads from a different source than where the auto-promote logs.

  **Investigation phase first:** read `src/alpha4gate/learning/promotion.py` to find where the `No current best -- promoting` log line lives, trace what (if anything) it persists to, and compare against what `/api/training/promotions` reads from. Document the gap in the issue body before writing code.

  **Then fix:** ensure the auto-promote path persists to the same source the API reads from (`data/promotion_history.json` is the most likely target — confirm). Add a `reason: "first_baseline"` (or similar) field to distinguish auto-promotes from win-rate-gate promotes, so the dashboard can label them differently in a follow-up.

  **Tests required:**
  - Unit test that triggers the auto-promote code path with no prior best, then asserts `/api/training/promotions` returns the new entry with the `first_baseline` reason.
  - Unit test that triggers the win-rate-gate path and asserts the entry has a different reason.
  - Existing promotion tests still pass.

- **Files (initial guess):**
  - `src/alpha4gate/learning/promotion.py`
  - `src/alpha4gate/api.py` (only if the API reader needs to learn about the new reason field)
  - `tests/test_promotion.py`

- **Out of scope:**
  - Frontend label change (track as follow-up). This step only fixes the wiring.

- **Depends on:** none
- **Flags:** `--isolation worktree --reviewers code`

### Phase 4.6 Step 5: Replay filename uniqueness (no more overwrite collisions)

- **Status:** DONE (2026-04-11, iter 1/3, reviewers=code)
- **Issue:** #79
- **Problem:** `connection.py:59` builds the replay filename as `f"game_{map_name}.SC2Replay"` — a constant per-map filename that overwrites every prior game on the same map. (During soak-2026-04-11 the trainer didn't write replays at all because of Finding 2's bypass, so the overwrite hadn't manifested yet — but it WILL once Step 2 lands and the trainer starts writing replays.) This step makes the filename unique BEFORE Step 2 hooks the trainer into save_replay, so Step 2 doesn't immediately destroy 49 of every 50 replays.

  **Specifically:**
  1. Change `connection.py:59` (and any sibling sites) to `f"game_{map_name}_{game_id}.SC2Replay"` — incorporate the same `game_id` that uniquely identifies the game in `training.db`. If the seed game / batch path doesn't have a `game_id`, use a timestamp fallback like `f"game_{map_name}_{ts}.SC2Replay"` where `ts = datetime.now().strftime('%Y%m%dT%H%M%S')`.
  2. Verify no other code reads replays by the constant filename.

  **Tests required:**
  - Unit test that calls `save_replay` (or whatever API takes the filename) with two distinct game_ids on the same map and asserts two distinct files would be written (mock filesystem is fine).
  - Existing tests still pass.

- **Files:**
  - `src/alpha4gate/connection.py`
  - `tests/test_connection.py` (or wherever)

- **Out of scope:**
  - The two `replays/` directory issue (canonical vs `src/replays/`). That's a separate cwd-relative-path bug, tracked as the cosmetic Finding in the run log.

- **Depends on:** none
- **Coordinates with:** Step 2 — Step 5 should land first or in the same merge as Step 2.
- **Flags:** `--isolation worktree --reviewers code`

### Phase 4.6 Step 7: Win-probability forecast investigation (low priority, investigate-only)

- **Status:** DONE — REPORT ONLY (2026-04-11, investigate-only)
- **Issue:** #81
- **Output:** `documentation/win-probability-forecast-investigation.md` (318 lines)
- **Recommendation:** Option (c) weighted-feature heuristic deferred to Phase 5. Option (b) learned classifier is a **data-size blocker** — only 8 labeled games available, LogReg overfits to majority class. Option (a) PPO value head explicitly out of scope.
- **Bonus bugs found (file as follow-ups):** (1) 262 orphaned transitions in soak artifact (Phase 4.6 Step 1 `b27c6cc` fixes this going forward); (2) `transitions.action_probs` column is 100% NULL despite environment wiring — `_GymStateProxy.last_probabilities` was never populated during the soak run.
- **Problem:** Currently the only signal of "is the bot doing well" during a game is the post-game `Result.Victory` / `Result.Defeat`. There is no per-step or per-decision estimate of how likely the bot is to win at any given moment. Adding a **win-probability forecast** — a model that takes the current game state (plus any other useful features) and outputs `P(win)` — could be valuable in several ways:
  1. **As a debugging surface for operators.** A live "win probability" curve on the Live tab would let an operator see *when* the game is going wrong, not just *that* it went wrong. Did the bot lose at minute 2 or minute 10?
  2. **As a training signal candidate.** Currently the trainer uses a hand-crafted reward function (12 reward rules visible in the dashboard with hand-tuned weights). A learned win-probability could replace or augment that with a less-hand-crafted signal — every step's reward becomes the change in `P(win)`. This is a well-known approach (similar to how AlphaZero uses value heads).
  3. **As a model-quality proxy independent of win rate.** Two models with identical win rates can differ in calibration / confidence. A bot that "thought it was winning" right up to the moment it lost is a different failure mode than one that "knew it was losing" early. Track this as a soak finding for Finding 1 (PPO win-rate quality is research-scope, but this gives a richer per-game signal than a single binary outcome).

  **Per operator direction:** investigate and report. Only commit a code change if the wiring is clean and small (matching the Step 6 / headless-SC2 contract). Default: report-only.

  **Investigation steps:**
  1. **Inventory existing inputs.** Read `src/alpha4gate/learning/features.py`, `src/alpha4gate/learning/environment.py`, and the `transitions` table schema in `src/alpha4gate/learning/database.py` — what game-state features are already captured per step? Are they enough to train a forecast, or are key features (e.g. opponent army composition, scouting state, supply differential, mineral/gas income rate) missing? Note any gaps.
  2. **Check `transitions` table contents.** Quick query: how many transitions exist (the soak left ~1750), what fields they have, and whether the post-game `Result.Victory/Defeat` is propagated back to every transition in the same game (this is the supervised label). If not, document what'd be needed.
  3. **Architectural sketch.** Where would the forecast model live? Three options to evaluate:
     - **(a) New head on the existing PPO neural net.** Add a value head alongside the policy head; train it jointly. Cleanest but requires touching `src/alpha4gate/learning/neural_engine.py` and may interact with Finding 1.
     - **(b) Separate small classifier.** A standalone model (sklearn LogisticRegression or a tiny torch MLP) trained offline on the transitions table. Cleanest separation, easiest to validate, doesn't risk breaking the trainer.
     - **(c) Hand-rolled heuristic.** A simple weighted-feature score (supply ratio × army value × tech progress, etc.). No training, easy to ship, decent baseline. Useful as a sanity check even if (a) or (b) is the long-term answer.
     Pick a recommended approach in the report and explain why.
  4. **Outputs to surface.** Where would `P(win)` show up? Three candidate sinks:
     - Backend log line per N steps.
     - WebSocket event on `/ws/game` so the Live tab can render a live curve.
     - A new column in the `transitions` table for offline analysis.
     Pick which subset is in-scope for an initial implementation.
  5. **Prototype (optional, only if time and clean).** Train a tiny baseline forecaster (option (c) heuristic, or option (b) sklearn on the soak's existing 1750 transitions) and report initial calibration / accuracy numbers. Can use the artifact at `~/soak-artifacts/2026-04-11/training.db`.
  6. **Connection to Finding 1 (PPO win-rate degradation).** Briefly speculate whether a learned win-probability reward signal could plausibly fix the degradation pattern (0.333 → 0.250 → 0.200 → 0.167 → 0.286). Don't over-promise — this is a sketch, not a proof.

  **Output (always required):**
  - `documentation/win-probability-forecast-investigation.md` — short report (~1-2 pages) covering: existing feature inventory, transitions table state, architectural recommendation among (a)/(b)/(c) with justification, recommended output sinks, prototype results if any, and a clear "what to build next" recommendation. Should be readable by a fresh-context model that hasn't seen this soak.

  **Output (conditional, only commit code if clean):**
  - If a prototype is small and clean: a script under `scripts/` (e.g. `scripts/train_winprob_baseline.py`) that trains the baseline against `training.db` and prints calibration metrics. Tests asserting it runs end-to-end on a tiny fixture DB.

  **Tests required (only if code is committed):**
  - Unit test that the script runs against a small synthetic transitions table and produces output without crashing.
  - Existing tests still pass.

- **Files (only if code is committed):**
  - New: `documentation/win-probability-forecast-investigation.md` (always)
  - Possibly: `scripts/train_winprob_baseline.py`, fixture DB under `tests/fixtures/`
  - **Do NOT touch** `src/alpha4gate/learning/neural_engine.py` or `src/alpha4gate/learning/trainer.py` in this step — those changes belong in a follow-up Phase 5 issue if the report recommends going down that path.

- **Out of scope:**
  - Actually wiring the forecast into the trainer's reward function. That's a follow-up.
  - Frontend Live-tab changes to render the curve. That's a follow-up.
  - Improving Finding 1's PPO win-rate quality directly. Still research-scope.

- **Depends on:** none (independent of Steps 1-6)
- **Flags:** `--isolation worktree --reviewers code`

### Phase 4.6 Step 6: Headless SC2 investigation (low priority, investigate-only)

- **Status:** NOT STARTED
- **Issue:** #80
- **Problem:** During soak-2026-04-11 the trainer's training-cycle games ran at ~12-15 sec each (already at API speed via `realtime=False`), but the eval games were taking ~6 minutes each — and the SC2 client was rendering each game visibly. This step investigates whether burnysc2 / SC2 supports a fully headless mode that skips graphics rendering entirely, to (a) reduce GPU contention, (b) eliminate window-focus issues, (c) potentially squeeze additional speed out of trainer cycles, and (d) let soaks run with the screen off.

  **Per operator direction:** investigate and report. Only commit a code change if the wiring is clean and small. If it requires Windows-specific yak shaving or burnysc2 monkey-patches, write up the findings and stop — do not commit.

  **Investigation steps:**
  1. Check `burnysc2`'s `run_game` / `run_multiple_games` signature for headless / no-render args.
  2. Check whether SC2 supports `--HeadlessNoRender` or similar launch flags on Windows for the version we use (check `C:\Program Files (x86)\StarCraft II\Versions\Base*\` for what's available).
  3. Look at how `learning/environment.py` and `connection.py` launch SC2 — is there a config seam where launch args can be added?
  4. Prototype a single-game headless run (maybe a one-off script in `scripts/`) and time it against a non-headless run on the same map / opponent.

  **Output (always required):**
  - `documentation/headless-sc2-investigation.md` — short report (~1 page) covering: what burnysc2 supports, what SC2 supports on this Windows install, whether a clean wiring exists, measured speedup if a prototype was run, and recommendation (commit / defer / not feasible).

  **Output (conditional, only commit if clean):**
  - If wiring is clean and small: a feature-flagged code change that lets the trainer launch SC2 headless. Default OFF until validated in a soak. Tests asserting the flag is honored.

  **Tests required (only if code is committed):**
  - Unit test that the launch arg is forwarded when the flag is on.
  - Existing tests still pass.

- **Files (only if code is committed):**
  - New: `documentation/headless-sc2-investigation.md` (always)
  - Possibly: `src/alpha4gate/learning/environment.py`, `src/alpha4gate/connection.py`, `src/alpha4gate/config.py`

- **Depends on:** none
- **Flags:** `--isolation worktree --reviewers code`

---

## Build-phase execution order

`/build-phase --plan documentation/plans/soak-2026-04-11-fixes.md` should run the steps in this order:

1. **Step 1** (game_id + silent fallthrough) — most critical, smallest blast radius, fixes the actual crash from the soak.
2. **Step 5** (replay filename) — must land before Step 2 so Step 2 doesn't immediately destroy replays.
3. **Step 2** (trainer → legacy producers wiring) — biggest impact for future soak observability.
4. **Step 4** (promotion API persistence) — independent, small.
5. **Step 3** (Decision Log producer) — independent, narrow scope.
6. **Step 7** (win-probability forecast investigation) — investigate-only, may produce a report-only outcome.
7. **Step 6** (headless SC2 investigation) — last because it's the other step that may produce a "no commit" outcome.

The two investigation steps (6 and 7) run last because they're the most likely to produce report-only outcomes; if the rest of the build-phase runs over budget, we get the high-value code fixes done first and the investigations are a bonus.

If any step fails its reviewer gauntlet and can't be fixed in 2-3 iterations, build-phase should leave the worktree intact, post a comment to the GitHub issue, skip the step, and continue. We can pick up failed steps manually in the morning.

## Out of scope (tracked separately, follow-up)

- **Finding 1 (PPO win-rate quality):** research-scope. Needs experiments around reward shaping, PPO hyperparameters, per-cycle experience buffer size. Not a one-shot code fix.
- **Eval-game timeout pattern:** every eval game during the soak hung for ~6 minutes until the timeout fired. Possibly related to Finding 5 (same teardown path) — Step 1 may also fix this as a side effect, but if not, file as a follow-up.
- **§3.2 stale 50-line gate in soak-test.md:** docs-only change, not worth a build-step. Can be a small commit any time.
- **Loop tab "Next Check" doesn't refresh during training:** cosmetic dashboard fix.
- **Two `replays/` directories from cwd-relative path:** cosmetic. Pin `_replay_dir` to absolute or refuse to start from non-root cwd.
- **Decision Log: rule-based decision-engine audit and PPO action audit:** Step 3 only handles Claude advisor decisions. The other producers are follow-up.
- **Promotion dashboard label for first-baseline auto-promotes:** Step 4 only fixes the wiring. UI label is a follow-up.

## Validation gate (post-build-phase, before next soak)

After build-phase completes, before launching a fresh soak run #4:

1. `uv run pytest` — full test suite must pass (baseline 742 + new tests).
2. `uv run mypy src` — must be clean (44 source files).
3. `uv run ruff check .` — must be clean.
4. Spot-check `git log --oneline` to confirm only the expected step merges landed.
5. Fresh empty `data/` per Option B, seed via `--batch 1 --difficulty 1`, launch backend + frontend + poller per soak-test.md §3, run for at least one full orchestrator cycle, verify (a) no `IntegrityError` in backend log, (b) Stats / Replays / Model Comparison / Reward Trends all show trainer games, (c) Decision Log shows Claude advisor entries, (d) `/api/training/promotions` shows the auto-promote when v1 is first promoted.
