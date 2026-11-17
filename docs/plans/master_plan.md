# Alpha4Gate master plan

Status: outstanding work consolidated 2026-10-14; direction reset 2026-11-11 (§16): measurable playing strength is the goal, Jev is closed as a completed proof of concept (§8), and PPO is under active exploration. This is the single project execution inventory. Completed code is not operator acceptance. Neither reconciliation ran gameplay, paid dispatch, issue closure or implementation.

## 1. Purpose and authority

Build a Protoss SC2 bot that measurably gets stronger, on an inspectable platform: frozen full-stack versions, an evolution loop judged by a fixed external scoreboard, and PPO under exploration. Jev, the separate graph player, is a completed proof of concept (§8). Preserve empirical promotion/acceptance gates, honest measurement and local command legality. Workspace Toolkit scheduling/status remains authoritative where mapped: [shared program](../../../plan.md). This project plan owns requirements, not a replacement shared schedule. Source statements and original issue identities remain auditable in [source map](../plan-reconciliation/migration.json).

Proposal: docs/plan-redline/alpha4gate-proposal.html

## 2. Current context

Read [CLAUDE.md](../../CLAUDE.md) and project rules before implementation. The 2026-10-14 migration baseline was c0836c3 on the de-facto branch master-plan/phase-ev, whose working tree carried extensive uncommitted work; most of it was committed on 2026-11-16, and landing the branch on `master` is still BL.1. Production `bots/current` points to v13; the last promotion was 2026-05-07. Since SC2 build 98310 (2026-10-14), v0–v12 crash about 45 game-seconds in; the fix (#364) landed on this branch on 2026-11-16 as commit 52f1afd: `src/orchestrator/sc2_compat.py` exists and v13 and Jev install it at import, while v0–v12 still need ES.14's launcher to install it (Phase ES, ES-A). The goal set on 2026-11-11 is playing strength measured on a fixed external scoreboard ([evolve-for-strength seed](../seeds/evolve-for-strength.md)), not self-improvement as a proof of concept. Jev is closed (§8); its frozen v1 snapshot v1-b53039a693cadf75 must still never be recaptured. Since 2026-11-11 (D16), evolution runs headless on WSL Linux SC2 4.10; since 2026-11-16 (T1) stage scoreboards and champion decisions run on the current Windows build, while evolution's inner loop and the promotion panel stay headless. Live Windows games are for spot checks and demos. Evolve live gates are independently open. Spending remains gated on durable budget, safety and real smoke.

## 3. Scope and exclusions

Only outstanding leaf work appears below. Shipped phases A/0–5/8/9/B/N, D code/counts, EL1–6, EJ1–6, EV1–3, EH1–2, JV201–206, JI209–211, J2 code212/224/214–221 are excluded from estimates. Jev's unfinished steps were removed on 2026-11-11 (§8, §16); their reusable ideas live in the [Jev harvest seed](../seeds/jev-harvest.md). Phase8 ceremonial validation was explicitly closed and cloud step12 cut; stale historical TODOs do not reopen it. PhaseF transformer, checkpoint-only opponent pool, domain abstraction and learned Hydra remain cut. PARKED steps (D8, D9, D12, D13 in §16) stay on file and are not dispatch-ready. No autonomous merges, recurring jobs or broader model gameplay authority is added here.

## 4. Architecture and dependencies

Python>=3.12, uv, burnysc2, FastAPI, React/TypeScript/Vite. RL versions are full frozen physical stacks; current is a pointer. src/orchestrator must not import bots.current/version modules. Jev owns its graph/runtime, no RL-stack dependency. Backend8765/frontend3000; verify actual health and IPv6 localhost behavior. Windows local CPU PPO; WSL Ubuntu-22.04 with SC2PATH and SC2_WSL_DETECT=0 for Linux work. Follow existing installed dependency locks, not historical test counts.

Phase ES begins with ES-A (SC2 compatibility), because every cross-version gate depends on older trees running. EH before EI shared-file edits; re-derive anchors after each landing. EH6 dependsEH8; do not enable long auto-committing launch while index scoping remains unfixed. Deep review remains required for trust/persistence boundaries; historical toolkit freeze has lifted, not a reason to downgrade. All Evolve/ES/PhaseD/Phase6/7 live gates serialize machine-wide around one pointer and SC2 resources.

## 5. Operational corrections and contracts

Archived plans preserve original bytes and may contain superseded instructions. These corrections govern this master. Register frozen baselines before gauntlet/floor gates; verify list output. --lineages N never creates N records: seed through scripts/lineage.py; EH2 persists only disk-loaded registries. Four seeded records/cap3 makes culling possible, not guaranteed: dominance plus fingerprint distance<0.15 still required. A single-lineage fallback cannot satisfy multi-lineage acceptance. Record not-triggered-by-design separately; >=2 generations per actual lineage and nontrivial diversity remain required. Remove seeded lineages after gates unless explicitly retained; baselines persist. EH has no extra six-hour acceptance step: EH10 is terminal by operator decision.

Use --generations0 for soaks; argparse default1 remains quick-test behavior. The dashboard Stop button is unwired and EH4/EH5 were dropped as duplicate functionality (D7, 2026-11-11): stop a run by closing the evolve console (or Ctrl+C on a headless run); viewer close only detaches. Never Ctrl+C the threaded evolve viewer or kill SC2 manually; use documented console closure/owned graceful mechanisms. No live tests during docs migration. EVO_AUTO currently sweeps staged work; never run it over this migration.

PhaseD M1: take baseline before flag, three PPO cycles, >=30% early reward standard-deviation reduction and nonregressing difficulty3 WR, then M2 >=+10 Elo/20 games. Bare snapshot_bot.py resolves current version; --from current and hardcoded pointer v14 are invalid. Use actual emitted versions. Restore reward backup and leave flag off on failure. Phase7 is a skill invocation /improve-bot-advised, not a uv console script. PhaseE10-versus12 actions is unresolved E0; no implicit choice (E0 now sits under PPO.0, D11).

EI log identity is match_id, not PFSP game_index; one producer/consumer helper, newest-N byte cap and retention at every generation including serial/viewer/resume, never the concurrency-only stale sweeper. Trace fields: match_id/version/role/terminal marker/n_steps(nullable)/duration_s(nullable)/status_chunk_count/parse_errors; mechanical evidence only. Six flags preserve defaults, fitness flags cross worker argv. SelfPlayRecord adds two optional fields to nine existing keys (eleven total, correcting source arithmetic); all readers tolerate unknown fields from this release onward, not genuine pre-EI checkouts. PerItemState serializers and load_pool_state pop tuple change together; score pairs remain pairs. EI8 preserves existing clean-sweep tests and adds near-miss characterization; its late contradictory sentence is superseded by the detailed Done-when. EI14 co-tenants EL7 with trace-capture/capture-patch and optional static observe only, not enforce/stack-size/rule-stop; it claims no throughput or EL outcome, zero added games.

## 6. Design decisions

Operator-picked historical approvals retain their scope: EH pause wiring cut and checkbox disabled, resume clears pending stop with INFO, EH7/EH8 retained, no EH unattended-observation obligation, EH9 depends only shippedEH1/EH2, EV sibling correction #302. JDI integration/experiment choices approved with flexible single$5 cap; no stage subcap or renewed approval within that cap. Agent-defaulted cleanup: one master, source-preserving archive, unchanged IDs except explicit aliases, independent gates, conservative unresolved holds and active-hour estimates. EI product defaults remain unratified (now only for the reduced set, D8); E0 sits under PPO.0 and branch landing remains a decision. J2 residual scope closed with Jev. The 2026-11-11 decision register (§16) supersedes this section where they conflict. Original append-only inventories are preserved in exact archives; this reconciliation appends to them rather than rewriting history.

## 7. Outstanding build and acceptance steps

Status PENDING can still have dependencies. HELD/PARKED/DEFERRED are not dispatch-ready. Historical source bodies below retain exact requirements; canonical corrections above take precedence. Completed dependencies are listed in the provenance report rather than reintroduced as forecast work.

Phase ES (ES.1–ES.14; design in §17), PPO.0 and VW.0 come first; they were added on 2026-11-11 (§16). VW.0 was planned on 2026-11-16 into VW.1–VW.4 (VW.3 PARKED).


### Step ES.1: Prove the headless WSL substrate still plays games

- **Status:** PENDING
- **Problem:** Evolve and the scoreboard move to headless Linux SC2 in WSL (D16), but that substrate has not run a game since 2026-04-30, and its venv and lockfile have drifted since.
- **Type:** operator
- **Issue:** #366
- **Files:** none authored; evidence in `docs/soak-test-runs/headless-substrate-check-<date>.md`
- **Produces:** a recorded check: Linux venv synced, one solo game and one two-game self-play completed headless, SC2 build reported, wall-clock per game measured.
- **Done when:** run from PowerShell, in order, observing each:
  ```powershell
  wsl -d Ubuntu-22.04 --cd "$env:USERPROFILE\dev\Alpha4Gate" -- bash -lc "printenv SC2PATH SC2_WSL_DETECT UV_PROJECT_ENVIRONMENT; uv sync --extra dev"
  ```
  ```powershell
  wsl -d Ubuntu-22.04 --cd "$env:USERPROFILE\dev\Alpha4Gate" -- bash -lc "DATA_DIR=/tmp/a4g-substrate uv run python -m bots.v13 --role solo --map Simple64 --difficulty 1"
  ```
  ```powershell
  wsl -d Ubuntu-22.04 --cd "$env:USERPROFILE\dev\Alpha4Gate" -- bash -lc "uv run python scripts/selfplay.py --p1 v0 --p2 v13 --games 2 --map Simple64 --results-path /tmp/a4g-substrate/selfplay.jsonl"
  ```

  | Check | What to look for |
  |---|---|
  | Environment | `SC2PATH` set, `SC2_WSL_DETECT=0`, venv under `~/venv-alpha4gate-linux`; `uv sync` exits 0 |
  | Solo game | Ends in Victory/Defeat with no `KeyError` or Windows `SC2_x64.exe` launch (WSL auto-detect off) |
  | Self-play | Two results appended to `/tmp/a4g-substrate/selfplay.jsonl`, seats swapped on game 2, no orphan `SC2_x64` processes afterwards (`pgrep -a SC2` empty) |
  | Timing | Wall-clock seconds per game recorded, for scoreboard budgeting |
  | Replay | One saved `.SC2Replay` copied to Windows; record whether the Windows client opens it (Linux build 75689 vs Windows 98310) |

  Any failure is recorded with its error and returned to a code fix before ES.4; the replay result decides ES.11's review path.
- **Depends on:** none


<!-- autofix-applied: 2026-11-11 -->


### Step ES.2: Play a version against the built-in AI on a sealed schedule

- **Status:** PENDING
- **Problem:** Nothing measures a bot version against a fixed external opponent: evolve plays only bot-vs-bot, and every runner forces a Random-race opponent and writes into the current tree's database.
- **Type:** code
- **Issue:** #367
- **Flags:** --reviewers deep --isolation worktree
- **Files:** new `src/orchestrator/scoreboard.py` (schedule, result rows, resume), new `scripts/scoreboard_worker.py` (one game per subprocess, launched by path, RD1), new `scripts/scoreboard.py` (`run` subcommand), `src/orchestrator/sc2_compat.py` + `tests/test_sc2_compat.py` (already in the tree since 52f1afd, #364; the worker imports it, no edit expected), new `tests/test_scoreboard.py`
- **Produces:** `uv run python scripts/scoreboard.py run --versions <list> --difficulty <1-10> --races zerg,terran,protoss --games-per-race N --maps Simple64,Simple96,Simple128 [--decision-mode rules|neural|hybrid] [--model-path P] [--concurrency K] [--out DIR] [--resume DIR] [--dry-run]`. It expands a sealed schedule (maps and races rotated in a fixed order) and writes one JSONL row per game: version, race, difficulty, map, schedule index, result (`win|loss|tie|crash`), in-game and wall seconds, replay path, decision mode, model path, error. Each game runs in its own subprocess that installs the SC2 compatibility shim, sets an isolated `DATA_DIR` under the run directory, and builds the bot exactly as `bots.<v>.runner._run_single_game` does (`bots/v13/runner.py:307`), adding only `opponent_race` to `bots.<v>.connection.run_bot` (`bots/v13/connection.py:46`). No frozen tree is edited. It runs on Windows (SC2 build 98310) for stage scoreboards and headless in WSL (SC2 4.10) for the promotion panel (T1); every row records the substrate and SC2 base build.
- **Done when:** (1) `--dry-run` prints the full schedule and plays nothing. (2) A test drives `scripts/scoreboard.py run` with only `run_bot` replaced at the `bots.<v>.connection` boundary and asserts: the row fields, a recorded replay path, that `bots/v13/data/training.db` is untouched, that `--resume` skips completed rows without duplicating any, and that a raising worker produces a `crash` row while the run continues. (3) An equivalence test asserts the worker passes the same bot-construction arguments as `_run_single_game` for v0 and v13, and honors `--decision-mode`/`--model-path` (the runner's `--batch` path ignores both, `bots/v13/runner.py:482-490`). (4) `git diff --stat -- bots/v0 bots/v1 bots/v2 bots/v3 bots/v4 bots/v5 bots/v6 bots/v7 bots/v8 bots/v9 bots/v10 bots/v11 bots/v12` is empty. (5) `uv run mypy scripts/scoreboard.py scripts/scoreboard_worker.py --strict` is clean. (6) A test asserts no module under `src/orchestrator/` imports `bots.*`, per the CLAUDE.md rule (RD1).
- **Depends on:** none


<!-- autofix-applied: 2026-11-11 -->


### Step ES.3: Report scoreboard results against pre-registered stage rules

- **Status:** PENDING
- **Problem:** Raw game rows do not say whether a version improved, which stage it passes, or whether an older version should challenge the champion.
- **Type:** code
- **Issue:** #368
- **Flags:** --reviewers deep --isolation worktree
- **Files:** `src/orchestrator/scoreboard.py` (`STAGES` constant and report), `scripts/scoreboard.py` (`report` subcommand), `tests/test_scoreboard.py`
- **Produces:** `uv run python scripts/scoreboard.py report --run DIR [--stage N]` writes `report.json` and `report.md`. They give win rates per version × difficulty × race and overall, with Wilson 95% intervals, in an intention-to-test view (ties, timeouts and crashes count as losses) and a completed-only view. They give a stage verdict (`PASS | TARGET-UNMET | INCOMPLETE`) per the `STAGES` table in the Phase ES design (§17) and a challenger list: anchors whose overall rate exceeds the current champion's (`bots/current/current.txt`).
- **Done when:** fixture rows produce exact expected rates and Wilson bounds; all-win and all-loss fixtures give 100% and 0% (calibration anchor); ties and crashes lower the intention-to-test rate but not the completed-only rate; each stage verdict is tested at its threshold boundaries, including a race below its floor; a missing scheduled game yields `INCOMPLETE`, never `PASS`.
- **Depends on:** ES.2


### Step ES.4: Run the Stage 0 baseline on Windows

- **Status:** PENDING
- **Problem:** Nobody knows whether thirteen promotions made the bot stronger, or which version is strongest against a fixed opponent.
- **Type:** wait
- **Issue:** #369
- **Files:** generated `data/scoreboard/<run-id>/` (rows, replays, `report.json`, `report.md`); summary in `docs/soak-test-runs/scoreboard-stage0-<date>.md`
- **Produces:** calibration results plus the Stage 0 report for v0, v3, v7, v11, v12 and v13: VeryHard (7), Zerg/Terran/Protoss, 10 games per race per version, maps rotated over Simple64/96/128 (180 games, about 8 hours at concurrency 1).
- **Done when:** calibration runs first and passes before the baseline starts: v13 at Easy (2) wins at least 8 of 9 (3 per race), and v13 at CheatInsane (10) wins at most 1 of 9. A failed calibration stops the step with the evidence recorded. The baseline runs on Windows, the build real matches are played on (T1), from PowerShell in the repo root: `uv run python scripts/scoreboard.py run --versions v0,v3,v7,v11,v12,v13 --difficulty 7 --races zerg,terran,protoss --games-per-race 10 --maps Simple64,Simple96,Simple128 --out data/scoreboard/stage0`. The report accounts for all 180 scheduled games or names each missing one; the summary records per-version rates with intervals, the challenger list and wall-clock cost. No result-driven reruns.
- **Depends on:** ES.3


### Step ES.14: Launch every version through the SC2 compatibility shim in self-play

- **Status:** DONE (2026-11-16)
- **Problem:** On Windows build 98310, self-play launches each version as `python -m bots.<v>` (`src/orchestrator/selfplay.py:277-293`), so v0–v12 crash about 45 game-seconds in; the Windows champion challenge (ES.5, T1) and the evolve-demo two-bot viewer both need older versions to play there.
- **Type:** code
- **Issue:** #370
- **Flags:** --reviewers deep --isolation worktree
- **Files:** new `scripts/compat_launch.py`, `src/orchestrator/selfplay.py` (launch list), `tests/test_selfplay.py`
- **Produces:** self-play launches every version as `[python, scripts/compat_launch.py, <version>, ...]`; the wrapper calls `orchestrator.sc2_compat.install()` and then runs that version's own `bots.<v>.__main__` entry with the original arguments. No frozen tree is edited, and the wrapper lives in `scripts/` because `src/orchestrator/` must not import `bots.*`.
- **Done when:** a test drives `selfplay._build_match` and asserts every launch list starts with the wrapper and the version; a subprocess test asserts the shim is installed before `bots.v0`'s entry runs; `git diff --stat` over `bots/v0`–`bots/v12` is empty; a test asserts `src/orchestrator/` still imports no `bots.*`.
- **Depends on:** none outstanding (`orchestrator.sc2_compat` landed in 52f1afd, #364)
- **Decision (2026-11-16, T1):** Added when the operator moved champion decisions to the Windows build. Owned by lane evolve (it owns `selfplay.py`), built ahead of its RD2 queue because ES.5 needs it.


### Step ES.5: Run the champion challenge if an older version leads

- **Status:** PENDING
- **Problem:** If an older version outscores v13 at Stage 0, evolution may be building on a weaker parent (operator rule, §16 answer 4).
- **Type:** operator
- **Issue:** #371
- **Files:** none authored; record in `docs/soak-test-runs/champion-challenge-<date>.md`
- **Produces:** either "no challenger" (copied from the Stage 0 report) or head-to-head results and a pointer decision.
- **Done when:** if the Stage 0 challenger list is empty, record that and finish. Otherwise, for the top challenger `vX`, run on Windows, the build real matches are played on (T1; seats alternate automatically):
  ```powershell
  uv run python scripts/selfplay.py --p1 vX --p2 v11 --games 10 --map Simple64
  ```
  Repeat with `--p2 v12` and `--p2 v13`. The challenger passes only with at least 6 of 10 wins against each of the three, and a Stage 0 rate no worse than v13's. On a pass, repoint and commit (evolve refuses an uncommitted pointer change, `scripts/evolve.py:770-784`):
  ```powershell
  uv run python -c "from orchestrator.evolve import _restore_pointer; _restore_pointer('vX')"
  ```
  ```powershell
  git add bots/current/current.txt; git commit -m "chore: repoint champion to vX after Phase ES challenge"
  ```
  Never use `scripts/ladder.py compare`: it snapshots a new version and moves the pointer (`src/orchestrator/ladder.py:439-442`).
- **Depends on:** ES.4, ES.14, BL.1


<!-- autofix-applied: 2026-11-11 -->


### Step ES.6: Launch evolve headless in WSL and deprecate live evolution

- **Status:** HELD — T2 (2026-11-16): PPO first after the scoreboard
- **Decision (2026-11-16):** Paused until PPO.0's build phase is planned and the operator re-decides the order. The step text stands as written.
- **Problem:** The production launcher and dashboard button run evolve on Windows with the live `--viewer` (`scripts/launch-evolve.ps1:72`), which the operator wants retired: evolution should run headless (D16).
- **Type:** code
- **Issue:** #372
- **Flags:** --reviewers deep --isolation worktree
- **Files:** `scripts/launch-evolve.ps1` (ASCII-only, no BOM; pinned by `tests/test_evolve_cli.py:5167-5172`), `scripts/evolve.py` (`--viewer` deprecation warning), `.claude/skills/improve-bot-evolve/SKILL.md`, `docs/wiki/operator-commands.md`, `tests/test_evolve_cli.py`
- **Produces:** the launcher opens one console window that runs `wsl -d Ubuntu-22.04 --cd <repo> -- bash -lc "uv run python scripts/evolve.py ..."` in the foreground (no `--viewer`), so closing that console remains the stop gesture. `-Hours` and `-Generations` (EH.6) are forwarded, and a new `-EvolveArgs "<flags>"` string is appended verbatim. The `--viewer` flag still works but logs a deprecation warning naming the live spot-check path (VW.0).
- **Done when:** the launcher spawn-contract test asserts the parsed spawn line invokes `wsl -d Ubuntu-22.04`, forwards `--hours`, `--generations` and the `-EvolveArgs` string, and contains no `--viewer`; a `caplog` test asserts `--viewer` emits the deprecation warning while the run otherwise behaves as before; the ASCII/no-BOM launcher test passes; the skill and wiki stop instructions match. Live behavior is observed in ES.12.
- **Depends on:** EH.9 (RD2 order, 2026-11-16)


### Step ES.7: Promote the exact code that was scored

- **Status:** HELD — T2 (2026-11-16): PPO first after the scoreboard
- **Decision (2026-11-16):** Paused until PPO.0's build phase is planned and the operator re-decides the order. The step text stands as written.
- **Problem:** Fitness scores a candidate tree that is deleted afterwards (`src/orchestrator/evolve.py:675-681`), and promotion re-authors each winner from its text (`scripts/evolve.py:2219-2221`), so what ships was never tested.
- **Type:** code
- **Issue:** #373
- **Flags:** --reviewers deep --isolation worktree
- **Files:** `src/orchestrator/evolve.py` (`run_fitness_eval` capture before cleanup), `scripts/evolve.py` (`_stack_apply_and_promote`, new `--promote-scored` flag, default off), `scripts/evolve_worker.py` (parallel path), new run-scoped store `data/evolve_scored/<run-id>/<imp-id>/`, `tests/test_evolve_cli.py`
- **Produces:** with `--promote-scored`, each scored candidate's changed files are saved, with version-name tokens normalized, before the scratch tree is deleted. Stack-apply writes those exact bytes into the new version in rank order instead of calling the dev sub-agent. A later winner whose files conflict with an earlier one is benched for the next generation, never re-authored. Without the flag, behavior is unchanged.
- **Done when:** a test drives the real `run_loop` with the game runner and dev-apply stubbed at their boundaries, and asserts: the promoted version's files equal parent plus the scored edits byte for byte (after name normalization); the dev sub-agent is not invoked at stack time; a conflicting second winner is benched; serial and `--concurrency 2` paths behave the same; the flag-off path still re-authors (pins the default). The pool-state JSON shape is unchanged (patches live only in the new store).
- **Depends on:** ES.6 (RD2 order, 2026-11-16)


<!-- autofix-applied: 2026-11-11 -->


### Step ES.8: Record a built-in-AI panel for each promoted version

- **Status:** HELD — T2 (2026-11-16): PPO first after the scoreboard
- **Decision (2026-11-16):** Paused until PPO.0's build phase is planned and the operator re-decides the order. The step text stands as written.
- **Problem:** Promotion is checked only against bot versions (`data/baselines.json` holds versions only, `src/orchestrator/baselines.py:105-125`), so a promotion that plays worse against real opponents still ships.
- **Type:** code
- **Issue:** #374
- **Flags:** --reviewers deep --isolation worktree
- **Files:** `scripts/evolve.py` (new `--ai-panel` flag next to the gauntlet at `:4769-4789`), `src/orchestrator/scoreboard.py` (panel schedule and cache), `tests/test_evolve_cli.py`, `tests/test_scoreboard.py`
- **Produces:** with `--ai-panel`, after each promotion commit the new version plays the current stage's panel: 12 games, 4 per race, at the stage difficulty, maps rotated, through the ES.2 worker. Results are persisted under `data/scoreboard/panel/<version>.jsonl` and summarized in the run log; the incumbent's panel result is reused rather than replayed. `Baseline` and `data/baselines.json` keep their shape (the lineage fingerprint reads them, `src/orchestrator/fingerprint.py:197`). Log-only in this step.
- **Done when:** a `run_loop` test with games stubbed at the worker boundary asserts panel rows for the promoted version, reuse of the incumbent's cached rows, run-log lines, and no change to fitness, regression or rollback outcomes; flag off plays no panel games.
- **Depends on:** ES.2, ES.7 (RD2 order, 2026-11-16)


### Step ES.9: Roll back a promotion that is probably worse on the panel

- **Status:** HELD — T2 (2026-11-16): PPO first after the scoreboard
- **Decision (2026-11-16):** Paused until PPO.0's build phase is planned and the operator re-decides the order. The step text stands as written.
- **Problem:** A promotion that beats its parent in self-play can still play worse against the built-in AI, and nothing rolls it back.
- **Type:** code
- **Issue:** #375
- **Flags:** --reviewers deep --isolation worktree
- **Files:** `src/orchestrator/gate_stats.py` (two-sample posterior), `scripts/evolve.py` (`--panel-gate posterior`, wired into `_apply_rollback` at `:4946-5002`), `tests/test_gate_stats.py`, `tests/test_evolve_cli.py`
- **Produces:** with `--panel-gate posterior` (requires `--ai-panel`), a promotion is rolled back when P(new version's panel win rate < incumbent's) ≥ 0.85 under independent Beta(1,1) priors (exact, stdlib only, matching the existing `ROLLBACK_THRESHOLD`, `gate_stats.py:53`). The verdict and probability are logged; a panel crash fails open, as the panel floor does today.
- **Done when:** exact-value tests for the new probability (symmetric cases give 0.5; a clear loss crosses 0.85); a `run_loop` test where the stubbed panel shows a clear drop triggers the real revert path and restores the incumbent pointer, an equal panel keeps the promotion, and a panel crash keeps it with a logged reason.
- **Depends on:** ES.8


<!-- autofix-applied: 2026-11-11 -->


### Step ES.10: Feed scoreboard losses and operator flags to the proposer

- **Status:** HELD — T2 (2026-11-16): PPO first after the scoreboard
- **Decision (2026-11-16):** Paused until PPO.0's build phase is planned and the operator re-decides the order. The step text stands as written.
- **Problem:** The improvement proposer never sees how the bot loses to real opponents or what the operator spotted while watching.
- **Type:** code
- **Issue:** #376
- **Flags:** --reviewers deep --isolation worktree
- **Files:** `scripts/scoreboard.py` (`flag` subcommand), `src/orchestrator/scoreboard.py` (findings store `data/findings.jsonl`), `src/orchestrator/evolve.py` (`generate_pool` prompt), `tests/test_scoreboard.py`, `tests/test_evolve_cli.py`
- **Produces:** `uv run python scripts/scoreboard.py flag --version vN --replay PATH --game-time MM:SS --class execution|information|composition|strategy|infrastructure [--tactical T.n] --note TEXT` appends one finding. Pool generation includes a bounded evidence block: the latest scoreboard summary (per-race rates at the current stage) and the most recent findings, each labeled with its source.
- **Done when:** a test calls `generate_pool` through its production caller with the model call stubbed, asserting the prompt contains the evidence block, its size stays within a fixed cap with many findings, and an absent scoreboard or findings file leaves today's prompt unchanged; `flag` rejects an unknown class.
- **Depends on:** ES.3, ES.9 (RD2 order, 2026-11-16)


### Step ES.11: Review Stage 0 losses and revalidate the tactical bugs

- **Status:** HELD — T2 (2026-11-16): PPO first after the scoreboard
- **Decision (2026-11-16):** Paused until PPO.0's build phase is planned and the operator re-decides the order. The step text stands as written.
- **Problem:** The T.* tactical bugs were observed in April on older versions; nobody knows which still happen on v13 (or the new champion).
- **Type:** operator
- **Issue:** #377
- **Files:** none authored; findings in `data/findings.jsonl`, summary in `docs/soak-test-runs/stage0-loss-review-<date>.md`
- **Produces:** at least 12 reviewed losses of the current champion (4 per race) with flagged findings, and a disposition for every T step (T.1, T.3, T.4, T.7–T.12, T.2.1): `reproduces` (with replay and game time), `not observed` or `unclear`.
- **Done when:** each reviewed loss has a `scripts/scoreboard.py flag` entry, and the summary table lists every T step's disposition. Replays are watched in the Windows client if ES.1 showed Linux replays open there; otherwise the same matchups are replayed live on Windows (`uv run python -m bots.current.runner --map Simple64 --difficulty 7`) and observed. Reproducing T steps stay PENDING for fixes; the others are retired in this plan with the evidence linked.
- **Depends on:** ES.4, ES.10


### Step ES.12: Smoke-test an armed headless evolve run

- **Status:** HELD — T2 (2026-11-16): PPO first after the scoreboard
- **Decision (2026-11-16):** Paused until PPO.0's build phase is planned and the operator re-decides the order. The step text stands as written.
- **Problem:** The new launcher, scored-code promotion, AI panel, panel gate and evidence block have only been tested with stubs.
- **Type:** operator
- **Issue:** #378
- **Files:** none authored; record in `docs/soak-test-runs/es-armed-smoke-<date>.md`
- **Produces:** a 45-minute armed run on the production launcher with its observations.
- **Done when:** Gate 0 is done (`uv run python scripts/baseline.py add v10 v10 --note "frozen anchor"`, the same for v13, then `list`), the staged index is empty (`git diff --staged --stat`), and this runs:
  ```powershell
  .\scripts\launch-evolve.ps1 -Hours 0.75 -Generations 0 -EvolveArgs "--promote-scored --ai-panel --panel-gate posterior --regression-rule one-sided --panel-floor --fitness-mode both --screen-null-diff --refresh-dedup --budget-fit"
  ```

  | Check | What to look for |
  |---|---|
  | Headless | Evolve runs inside WSL; no SC2 window opens on Windows |
  | Dashboard | Evolution tab updates from the WSL-written state files |
  | Promotion | Any promotion names its scored-file source and runs the 12-game panel; the gate verdict is logged |
  | Proposer | The pool prompt log shows the evidence block |
  | Stop | Closing the console stops the run; `pgrep -a SC2` in WSL is empty afterwards |
  | Lock | A second launch while running is refused (EH.7) |
  | Rollback | A known-weak candidate injected with `scripts/evolve_inject_one.py` under `--ai-panel --panel-gate posterior` is rolled back, with the panel verdict logged (RD3) |

  This absorbs the former EJ.7 flags-on smoke.
- **Depends on:** ES.6, ES.7, ES.9, ES.10, EH.3, EH.7, EH.8, BL.1


### Step ES.13: Run the first scoreboard-judged evolve soak and Stage 1

- **Status:** HELD — T2 (2026-11-16): PPO first after the scoreboard
- **Decision (2026-11-16):** Paused until PPO.0's build phase is planned and the operator re-decides the order. The step text stands as written.
- **Problem:** Only a multi-hour unattended run shows whether scoreboard-judged evolution produces a measurably stronger champion.
- **Type:** wait
- **Issue:** #379
- **Files:** generated run artifacts and `data/scoreboard/stage1/`; record in `docs/soak-test-runs/es-soak-<date>.md`
- **Produces:** a six-hour armed headless soak (`.\scripts\launch-evolve.ps1 -Hours 6 -Generations 0 -EvolveArgs "<the ES.12 flags>"`), then a Stage 1 scoreboard of the resulting champion on Windows (T1): VeryHard, 20 games per race, maps rotated.
- **Done when:** the soak completes or stops with a recorded reason. The record lists every promotion with its scored-code provenance, panel verdict and rollback outcome, plus the observed fitness pass rate and rollback rate (absorbing the former EJ.8 measurement). The Stage 1 report gives a verdict; `TARGET-UNMET` is a valid, recorded outcome and is not rerun on new seeds. The record states the next stage or the next gap.
- **Depends on:** ES.12, ES.4


### Step PPO.0: Explore PPO's role and choose a first control surface

- **Status:** PENDING
- **Type:** operator
- **Issue:** not yet assigned
- **Depends on:** ES.4 (the P2 design-space survey finished 2026-11-16: `docs/investigations/ppo-design-space-2026-11-16.md`)
- **Decision (2026-11-16):** T2: PPO comes first after the scoreboard, so PPO.0 now ends in a PPO build-phase plan, and ES.6–ES.13 wait for it. T4: PPO.0 must evaluate Jev's legal-moves graph (`docs/seeds/jev-harvest.md` H19) as one candidate control surface and record yes or no.
- **Source:** [ppo-direction.md](../seeds/ppo-direction.md); decision D11 (§16).

- **Problem:** PPO is expected to be the strongest path to playing strength. Default games never consult it, its control surface is a choice among six strategic states, and no checkpoint has had a deterministic evaluation against a fixed panel.
- **Produces:** the P1 measurement (v13 rules vs hybrid on the scoreboard), a design-space survey, and a recorded decision: control surface, reward, opponent curriculum and kill criterion. Also a disposition for each held PPO step (M1, M2, E.0–E.6, 6.1–6.3, 7.6, ER.8).
- **Done when:** the decision is recorded in this plan, including a yes or no on the legal-moves graph (T4); every held PPO step is kept, rewritten or retired; and a PPO build phase exists as steps in this plan, ready for plan-review (T2).


### Step VW.0: Plan the strength viewer from the seed

- **Status:** DONE 2026-11-16 — planned by lane evolve-demo in [viewer-vw-steps-2026-11-16.md](../investigations/viewer-vw-steps-2026-11-16.md); folded below as VW.1, VW.2, VW.3 (PARKED) and VW.4
- **Type:** operator
- **Issue:** not yet assigned
- **Depends on:** none outstanding
- **Decision (2026-11-16, T3):** Spot-check by re-playing: after a promotion the viewer offers to watch that candidate live at the same race and difficulty, and flags feed the next proposals. Owned by lane evolve-demo.
- **Decision (2026-11-16, operator):** Build VW.1 then VW.2. Park VW.3 (flag into ES.10) while ES.10 is HELD under T2. One new Observer Rush-style window serves both layouts; the old pygame container stays for legacy callers.
- **Disposition (2026-11-16):** EV.4/EV.5, SV.1–SV.6 and UI.7/UI.8 are retired or superseded as recorded in the VW plan's "Disposition of removed items". The unwired dashboard Stop Run button (D7) is a Direct task, not a step: replace it in `frontend/src/components/EvolutionTab.tsx` with "To stop an evolve run, close its console window." and update its vitest (`npm run test:run -- src/components/EvolutionTab` from `frontend/`); lane evolve-demo owns the file.
- **Decision (2026-11-11, D16):** Evolve runs headless and live evolution is deprecated. The viewer serves separate live spot-check and demo games on Windows (one bot vs the built-in AI, or two bots), which also needs the SC2 compatibility shim for older versions on build 98310.
- **Source:** [strength-viewer.md](../seeds/strength-viewer.md); decisions D7 and D10 (§16).

- **Problem:** The evolve viewer predates Jev's Observer Rush. A candidate cannot be spot-checked with its decisions visible, and an issue spotted while watching cannot be flagged into the improvement loop.
- **Produces:** two or three build steps (single layout, dual layout, flag-this-moment) via `/plan-feature`, plus a disposition for the removed EV/SV/UI items and the unwired dashboard Stop Run button.
- **Done when:** the steps exist with acceptance tied to spotting and flagging a real candidate behavior, within the seed's scope cap.


### Step VW.1: Watch one bot version against the built-in AI with its decisions

- **Status:** PENDING
- **Type:** code
- **Issue:** #381
- **Files:** new `src/selfplay_viewer/strength/{__init__,__main__,player,telemetry,host}.py`, new `scripts/launch-strength-viewer.ps1`, `frontend/src/main.tsx`, new `frontend/src/components/StrengthViewer.tsx` + `StrengthViewer.test.tsx`, new `tests/test_strength_viewer.py`
- **Depends on:** none outstanding (the SC2 compatibility shim is in the tree; ES.2 is not required)
- **Source:** [viewer-vw-steps-2026-11-16.md](../investigations/viewer-vw-steps-2026-11-16.md) §7 VW.1; design decisions there §6. Owned by lane evolve-demo.

- **Problem:** The operator cannot watch a chosen bot version play the built-in AI beside a live panel of its own recorded decisions.
- **Flags:** --reviewers code --isolation worktree --ui
- **Produces:** `powershell -File scripts/launch-strength-viewer.ps1 -Version v13 -Difficulty 3 -Race Terran -Map Simple64 [-Realtime] [-CheckOnly]` opens one 2160×1200 window: native SC2 plus a panel with version, game time, strategic-state timeline, army target and win probability, updating while the game runs. Stop leaves the match normally; the run folder `data/strength_viewer/runs/<run_id>/` holds the manifest, telemetry, replay and that seat's isolated data dir. Telemetry is passive: the player wrapper records `queue_broadcast`, `_resolve_attack_rally` and `winprob_heuristic.score` inputs and returns unchanged, with no frozen-tree edit. The native host is copied and trimmed from `scripts/probes/jev_battle_view.py` (owned by lane repair, not imported or changed).
- **Done when:** a test runs the production player entry (`python -m selfplay_viewer.strength.player ... --seat solo`) with only the SC2 boundary replaced and shows: the shim is installed before the tree import; the three wrapped seams return exactly the original values; `latest.json` gets strategic state, army target and win probability for v13 and `not recorded` win probability for v1; nothing is written under `bots/<vN>/data`; the loaded build order matches a normal launch. A test drives the host's bridge and ready barrier: the game child does not start until the page reports the exact run id, a wrong or stale run id is refused, Stop before ready starts no game. A vitest renders the page from a status fixture, shows `not recorded` fields and an update age, and hides Stop after the match ends. `-CheckOnly` exits 0 without starting SC2 and fails with a message when SC2 is already running. UI evidence: a Playwright screenshot of the Vite page `http://localhost:3000/?strength=1` with `window.strengthAction` stubbed (init script) to return the single-layout status fixture. Gates: `uv run pytest tests/test_strength_viewer.py tests/test_reparent_portable.py`, `uv run ruff check .`, `uv run mypy src bots --strict`, and from `frontend/` `npm run test:run -- src/components/StrengthViewer`, `npm run lint`, `npm run build`.


### Step VW.2: Watch both bots of one bot-vs-bot match side by side

- **Status:** PENDING
- **Type:** code
- **Issue:** #382
- **Files:** `src/selfplay_viewer/strength/{host,telemetry,__main__}.py`, new `src/selfplay_viewer/strength/dual.py`, `scripts/launch-strength-viewer.ps1`, `frontend/src/components/StrengthViewer.tsx` + test, `src/orchestrator/selfplay.py` (opt-in `launch_prefix`), `tests/test_strength_viewer.py`, `tests/test_selfplay.py`
- **Depends on:** VW.1; lane evolve lands or agrees to the `selfplay.py` `launch_prefix` change (lanes table: evolve owns `selfplay.py` and adds this keyword)
- **Source:** [viewer-vw-steps-2026-11-16.md](../investigations/viewer-vw-steps-2026-11-16.md) §7 VW.2. Owned by lane evolve-demo.

- **Problem:** The operator cannot watch two bot versions play each other with each side's decisions shown next to that side's own SC2 client.
- **Flags:** --reviewers code --isolation worktree --ui
- **Produces:** `powershell -File scripts/launch-strength-viewer.ps1 -P1 v13 -P2 v0 -Map Simple64` opens one 3440×1360 window: both SC2 clients side by side, each with its own decision panel beneath it, for one match run by `run_batch(games=1, launch_prefix=[...player wrapper...])` in a child process. Closing the window detaches both clients and lets the match finish; no further match starts. `run_batch`'s new keyword-only `launch_prefix: list[str] | None = None` is threaded through `_build_match` to `_build_bot_process`; existing callers (`scripts/selfplay.py`, `scripts/evolve.py`, `src/orchestrator/evolve.py`) omit it.
- **Done when:** a test calls `run_batch` with `launch_prefix` and asserts each `BotProcess.launch_list` starts with the prefix and still ends with the original bot arguments, and a test asserts `launch_prefix=None` leaves `launch_list` byte-identical to today. A host test feeds two fake seats whose telemetry interleaves and asserts each pane updates only from its own seat, with same-version opponents (v13 vs v13) kept apart by seat. Pane pairing comes from a recorded per-player SC2 identity, and a test fails if it falls back to PID order (if no mechanism is provable, panes are labelled "pairing unverified"). A missing or late SC2 window shows a placeholder without blocking the event pump. Closing the host restores both windows and the child exits after the match. UI evidence: a Playwright screenshot of `?strength=1` with `window.strengthAction` stubbed to the dual-layout status fixture, showing two panels. Gates: `uv run pytest tests/test_strength_viewer.py tests/test_selfplay.py tests/test_selfplay_callbacks.py tests/test_reparent_portable.py` (existing `tests/test_selfplay*.py` stay green), `uv run ruff check .`, `uv run mypy src bots --strict`, and the VW.1 frontend gates.


### Step VW.3: Flag a moment from the viewer into ES.10's findings

- **Status:** PARKED — operator 2026-11-16: needs ES.10, which is HELD under T2; revisit when ES.10 is released
- **Type:** code
- **Issue:** #383
- **Files:** `src/selfplay_viewer/strength/host.py`, new `src/selfplay_viewer/strength/flags.py`, `frontend/src/components/StrengthViewer.tsx` + test, `tests/test_strength_viewer.py`
- **Depends on:** VW.1, VW.2, ES.10
- **Source:** [viewer-vw-steps-2026-11-16.md](../investigations/viewer-vw-steps-2026-11-16.md) §7 VW.3. Owned by lane evolve-demo.

- **Problem:** A behavior the operator sees in the viewer cannot be recorded as a finding the proposer will read.
- **Flags:** --reviewers code --isolation worktree --ui
- **Produces:** a Flag button (and `F` key while the page has focus) on each panel that freezes that seat's version and game time at the press, then takes class and note. Flags wait in the run's `flags-pending.jsonl` until the replay is final, then each is sent once as `uv run python scripts/scoreboard.py flag --version vN --replay PATH --game-time MM:SS --class C --note TEXT`. The panel shows "saved, waiting for replay", then "sent" or the command's error.
- **Done when:** a test drives the host's flag handler end to end with a temporary run and replay into the real ES.10 `flag` command and then the real `generate_pool` evidence builder (model call stubbed), and finds exactly one finding with the pressed seat's version, game time and replay in the evidence block. A flag pressed mid-match is not sent before the replay exists; a resend after restart sends nothing new for an already-sent flag; a failed command keeps the flag pending with the error shown. No `training.db` or scoreboard result row is written.


### Step VW.4: Operator acceptance of the strength viewer (single and dual)

- **Status:** PENDING
- **Type:** operator
- **Issue:** #384
- **Depends on:** VW.1, VW.2
- **Source:** [viewer-vw-steps-2026-11-16.md](../investigations/viewer-vw-steps-2026-11-16.md) §7 "Operator acceptance"; the Flag row moves to VW.3's own acceptance when VW.3 is released.

- **Problem:** The native window, display fit, DPI and audio behavior are only observable on the real Windows display.
- **Produces:** an operator pass or a list of issues found while watching, recorded in this step.
- **Done when:** on Windows SC2 with no other SC2 client open (no paid calls), each check below passes.

```powershell
powershell -File scripts/launch-strength-viewer.ps1 -CheckOnly
```

```powershell
powershell -File scripts/launch-strength-viewer.ps1 -Version v13 -Difficulty 3 -Race Terran -Map Simple64
```

```powershell
powershell -File scripts/launch-strength-viewer.ps1 -P1 v13 -P2 v0 -Map Simple64
```

| Check | What to look for |
|---|---|
| CheckOnly | Lists version, shim, prerequisites; starts no SC2 |
| Single match | Panel's state, army target and win probability change with what you see; Stop leaves the game normally and nothing else closes |
| Dual match | Both clients readable on the 3440×1440 display; each panel follows its own side; v0 plays past ~45 s (the old 98310 crash point); closing the window restores both clients and leaves no orphan SC2 |


<!-- autofix-applied: 2026-11-11 -->


### Step EH.3: Fail loudly when the gauntlet is inert

- **Status:** DONE (2026-11-16)
- **Decision (2026-11-11):** Kept by D6 (2026-11-11): run safety before unattended Phase ES runs.
- **Type:** code
- **Issue:** #308
- **Files:** `scripts/evolve.py`, `scripts/baseline.py`, `tests/test_evolve_cli.py`
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EH.3.

- **Problem:** With `data/baselines.json` absent, `--fitness-mode baseline|both` degrades to `parent` (`scripts/evolve.py:3941-3947`) and `--panel-floor` is INERT (`:3955-3967`) — each with a startup WARNING but **no error and no failed run**, which `../operator-gate-runbook.md`:72-80 notes is "easy to miss in a long log". The operator-visible cost is a six-hour soak that produces a green-looking record for capabilities that never executed. Add `--require-baselines` (default OFF, preserving the byte-identical bare invocation) which converts the degradation into a **two-second non-zero exit** naming the missing registry and the exact `scripts/baseline.py add` command from `../operator-gate-runbook.md`:85-92. Keep the existing warn-and-proceed as the default so no queued gate command changes behaviour unless it opts in.
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/evolve.py`; extended `tests/test_evolve_cli.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7); a test asserts `--fitness-mode both --require-baselines` with an empty registry exits non-zero **before any game is dispatched** and the message names `scripts/baseline.py`; a paired negative test asserts the same invocation **without** the flag still warns and proceeds, pinning the default as byte-identical.



<!-- autofix-applied: 2026-11-11 -->


### Step EH.6: Launcher `--generations` passthrough

- **Status:** PENDING
- **Decision (2026-11-11):** Kept by D6 (2026-11-11): run safety before unattended Phase ES runs. ES.6 then moves the launcher's spawn into WSL (D16).
- **Type:** code
- **Issue:** #311
- **Files:** `scripts/evolve.py`, `scripts/launch-evolve.ps1`, `tests/test_evolve_cli.py`, `claude/skills/improve-bot-evolve/SKILL.md`
- **Depends on:** EH.8
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EH.6.

- **Problem:** `scripts/evolve.py` declares `--generations` with `default=1` (`:217`) and the loop breaks with `stop_reason = "generations-reached"` as soon as the cap is met (`:4290-4297`). `scripts/launch-evolve.ps1` has exactly one parameter, `[double]$Hours = 4` (`:45`), and its spawn line (`:72`) forwards only `--hours` and `--viewer` — so the observatory `run-evolution` button and every `.\scripts\launch-evolve.ps1 -Hours N` promise an N-hour soak and deliver **one generation** (~20 min), then exit cleanly with nothing anywhere saying so. `../operator-gate-runbook.md`:40-45 rates this **fatal to EV.5, EJ.8 and EL.7**. Add `[int]$Generations = 0` at `:45` and `--generations $Generations` to `:72`; correct the false header comment at `:27-29` (which claims the run "keeps going headless to its `--hours` budget"); add `--generations 0` plus a flag-table row to the **second independent caller**, `.claude/skills/improve-bot-evolve/SKILL.md:216-222` and `:34-53`; and add a non-fatal startup WARNING in `scripts/evolve.py` when `--hours > 0` and `--generations` was left at its default of 1, naming the contradiction. **Do not flip the argparse default** (§3 Explicitly out) and leave `tests/test_evolve_cli.py:457` `assert args.generations == 1` untouched — it is the tripwire that forces the docstring and wiki to move together if a future change ever does flip it. `scripts/launch-evolve.ps1` is pinned ASCII-only with no BOM by `tests/test_evolve_cli.py:5167-5172`. Sequenced after EH.8 so the unattended multi-hour run this step unblocks cannot sweep the operator's staged index into an `[evo-auto]` commit (operator decision P-4, § Decision Inventory).
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `scripts/launch-evolve.ps1`; modified `scripts/evolve.py`; modified `.claude/skills/improve-bot-evolve/SKILL.md`; extended `tests/test_evolve_cli.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7) and (1) `test_launch_evolve_ps1_pins_the_viewer_spawn_contract` (`:5160`) gains an assertion that `--generations` appears in the parsed `$evolveCmd` line, mirroring its existing `assert "--viewer" in spawn` at `:5192`, plus a separate assertion that the param block declares a default of 0; (2) a new `caplog` test asserts a WARNING naming both `--hours` and `--generations` fires for `hours=4.0, generations=1`; (3) a paired negative test with an explicitly-passed `--generations 1` and `hours=0.75` asserts **no** warning, so the guard cannot regress into noise; (4) the ASCII/no-BOM launcher test still passes.



<!-- autofix-applied: 2026-11-11 -->


### Step EH.7: Single-run lock + stale run-state reconciliation

- **Status:** PENDING
- **Decision (2026-11-11):** Kept by D6 (2026-11-11): run safety before unattended Phase ES runs. D16 moves evolve into WSL: liveness must be judged in the platform that wrote the record. Stamp the platform with the pid and handle a record from the other platform explicitly (tested both ways); a Windows `psutil` probe of a Linux pid is meaningless.
- **Type:** code
- **Issue:** #312
- **Files:** `bots/current/current.txt`, `scripts/evolve.py`, `data/evolve_run_state.json`, `pyproject.toml`, `tests/test_evolve_cli.py`
- **Depends on:** EH.6 (RD2 order, 2026-11-16)
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EH.7.

- **Problem:** *"One evolve run at a time, machine-wide"* is enforced **by convention, not code** (`../operator-gate-runbook.md`:174-183): "There is no lock file, no pid file." Two runs collide on `data/evolve_*.json`, both flip `bots/current/current.txt`, and — worst — a second run starting mid-soak calls `_clear_fresh_run_state` (`scripts/evolve.py:1888-1899`), which **truncates the first run's `evolve_results.jsonl`**, destroying the evidence of a soak in progress. The only guard is a Windows CIM probe in `launch-evolve.ps1:54-75` that is launcher-scoped, Windows-only, blind to a WSL run, and explicitly fails open — and **every gate command in the runbook is a direct `python scripts/evolve.py` that bypasses it entirely.** Stamp pid + start timestamp into the already-existing `data/evolve_run_state.json` at the `:4022` write and check it at startup. **Read the pre-existing file strictly before the `:4022` write** — that write is a whole-file `_atomic_write_json` (`:1472`) and would otherwise clobber the very record being checked; both precede `_clear_fresh_run_state` (`:4096`), which is what truncates `evolve_results.jsonl`. **Liveness is `psutil.pid_exists(pid)`, never `os.kill(pid, 0)`:** on Windows (the sole declared platform, `pyproject.toml:16`) `os.kill` resolves to `OpenProcess` + `TerminateProcess(handle, 0)`, so the POSIX idiom *kills* the soak this lock exists to protect — and would kill the pytest process in this step's own Done-when (1). `psutil` is guaranteed importable with no fallback branch: it is an **unconditional transitive dependency of the core `portpicker>=1.6`** (`pyproject.toml:34`; `uv.lock` portpicker block is `dependencies = [{ name = "psutil" }]`, distinct from the `viewer`-extra declaration at `uv.lock:171`), and `bots/v*/system_info.py:35` already imports it at module scope inside the current baseline — add no lazy import and no degrade-on-`ImportError` path, because a lock that fails open is not a lock. Guard pid reuse by comparing the stamped start timestamp against `psutil.Process(pid).create_time()`: a process younger than the stamp means the pid was recycled and the record is stale. Then: if the recorded pid is alive and the state is `running`, refuse to start with a non-zero exit naming the live pid; and — generalizing — refusal requires a **positively confirmed** live pid, so any `running` record from which liveness cannot be established (pid key absent, non-integer, or the probe itself raising — the pid-less shape is the ONLY shape on every pre-EH.7 machine, including the 17-key record on disk at HEAD) is treated as stale: reap the stale record and proceed, emitting an INFO line naming the reaped `run_id` and `started_at` so a mixed-version collision is diagnosable after the fact — which also fixes the write-only staleness defect (`:180`, `:190-193`), where the file has claimed `"status": "running"` since a 2026-09-14 launcher run (`started_at: 2026-09-14T04:19:28+00:00`, re-verified 2026-10-05) that never completed a generation, making the dashboard render a phantom soak indefinitely. Provide `--force` to override for the genuine recovery case.
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `scripts/evolve.py`; extended `tests/test_evolve_cli.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7) and (1) a test with a state file naming **this** process's own live pid asserts a second `run_loop` refuses with a non-zero exit and the message names the pid, and asserts `evolve_results.jsonl` is **byte-unchanged** — the truncation is the actual harm; (2) a test with a state file naming a dead pid asserts the run proceeds and the stale record is reaped; (3) `--force` overrides (1); (4) a fresh checkout with no state file is unaffected; (5) a test seeded with the **real pre-EH.7 record shape** — `status: "running"`, a past `started_at`, and NO `pid` key, i.e. exactly what `write_run_state` (`:1412`) produces today — asserts the run **proceeds** (rc 0, not a refusal) and that the emitted INFO line names the reaped `run_id` and `started_at`; a sibling case with `"pid": "not-an-int"` takes the same path; (6) a recycled-pid test where the state file names a live pid whose `create_time()` is newer than the stamped start timestamp asserts the run proceeds and reaps; (7) a source-level pin asserts the lock code contains no `os.kill`, mirroring the cross-file pin at `tests/test_evolve_cli.py:5142-5157`. Clause (1) additionally asserts the probing process is **still alive** after the refusal — the canary that catches a `TerminateProcess`-shaped probe.



<!-- autofix-applied: 2026-11-11 -->


### Step EH.8: Path-scope the `[evo-auto]` commit

- **Status:** DONE (2026-11-16)
- **Decision (2026-11-11):** Kept by D6 (2026-11-11): run safety before unattended Phase ES runs.
- **Type:** code
- **Issue:** #313
- **Files:** `scripts/evolve.py`, `bots/current/current.txt`, `scripts/evolve_inject_one.py`, `scripts/check_sandbox.py`, `tests/test_evolve_cli.py`
- **Depends on:** EH.3 (RD2 order, 2026-11-16)
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EH.8.

- **Problem:** `EVO_AUTO=1` commits **sweep the entire git index**, not just `bots/<v>/` (`../operator-gate-runbook.md`:208; `.claude/rules/evolve.md` § Pre-launch hygiene), so anything an operator happened to stage rides into the next `[evo-auto]` commit — on whatever branch is checked out, during an unattended six-hour soak. The only mitigation today is a pre-flight `git diff --staged --stat` the operator has to remember. **The `git add` is already path-scoped** — `scripts/evolve.py:1564` is `["git", "add", f"bots/{new_version}/", "bots/current/current.txt"]`, the only `git add` call site in the repo — so there is nothing to replace there, and it must be **kept**: a fresh `bots/<vN>/` is untracked, so the add is what makes those files committable at all. The sweep is the **bare commit** at `:1592`, `["git", "commit", "--no-verify", "-m", msg]`, which carries no pathspec and no `-a` and therefore commits everything already in the index — evolve.py's own docstrings say exactly this at `:1646` and `:1678-1681`. Fix **four** sites, not one: (a) make the promote commit partial — `git commit --no-verify --only -m msg -- bots/<new_version>/ bots/current/current.txt`; (b) immediately before it, read `git diff --cached --name-only` and log an **ERROR naming any staged path outside that set — log, never abort**, because a refusal produces no commit at all, fails this step's own Done-when (1), and would silently drop a promotion on every generation of an unattended soak; (c) `git_revert_evo_auto` carries the identical bare commit at `:1750` and is live via `scripts/evolve.py:3825` and `scripts/evolve_inject_one.py:287` — scope it too, but verify the shape in a scratch repo first, since `git revert --no-commit` leaves sequencer state and a partial commit there may be refused (fall back to `git revert --quit` after the scoped commit, or an isolated `GIT_INDEX_FILE`); (d) `_reset_staged_promote` (`:1657`) and `_reset_staged_revert` (`:1687`) both run `git reset HEAD -- .`, which unstages the operator's unrelated work on every commit-failure path — scope both resets to the same paths (the revert path derives its set from `git show --name-only --pretty= <promote_sha>`, since a pre-EH.8 promote commit may itself have swept foreign paths). Correct the stale rationale comment at `:1585-1586` in the same edit — `--no-verify` skips `scripts/check_sandbox.py` entirely, and per ``.claude/rules/evolve.md`:31 that hook "blocks invalid paths but doesn't enforce ONLY these paths" — but keep `--no-verify` itself; the WSL shebang rationale below it is real. Note the partial-commit gotcha: `git commit -- <paths>` commits the WORKING-TREE state of those paths, identical to the staged state here only because the `git add` immediately precedes it. This is the workspace's standing rule for git-touching automation (`dev/.claude/rules/working-directory.md`: "Prefer path-scoped `git add <paths>`").
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `scripts/evolve.py` (`git_commit_evo_auto`, `git_revert_evo_auto`, `_reset_staged_promote`, `_reset_staged_revert`); extended `tests/test_evolve_cli.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7) and a test that stages an unrelated file in a temp git repo, runs the promotion commit path, and asserts (1) the resulting commit contains **only** the promotion paths and (2) the unrelated staged file is still staged and uncommitted afterwards; (3) the same two assertions hold for `git_revert_evo_auto` — an unrelated staged file survives the rollback commit still staged; and (4) a forced commit failure leaves the unrelated file staged, proving the `_reset_staged_*` cleanups no longer run `git reset HEAD -- .`.

<!-- autofix-applied: 2026-09-05 -->


### Step EH.9: Producer→consumer smoke gate

- **Status:** PENDING
- **Decision (2026-11-11):** Kept by D6 (2026-11-11): run safety before unattended Phase ES runs.
- **Type:** code
- **Issue:** #314
- **Files:** `tests/test_evolve_cli.py`, `tests/test_api_evolve_lineages.py`, `tests/test_lineage_cli.py`, `scripts/lineage.py`, `tests/test_evolve_chain_smoke.py`
- **Depends on:** EH.7 (RD2 order, 2026-11-16)
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EH.9.

- **Problem:** Every existing lineage test either calls `write_lineages` directly as a fixture seed (`tests/test_evolve_cli.py:2156` and five siblings) or hand-writes `lineages.json` as JSON (`tests/test_api_evolve_lineages.py:84` and four siblings). **That is precisely why this defect survived a 2,026-test suite**: both endpoints are asserted, the relationship between them never is. **Premise narrowed 2026-10-05 — EH.1 and EH.2 each shipped part of what this step was written to add.** EH.1's `tests/test_lineage_cli.py:354` (`test_cli_engages_and_disengages_the_evolve_consumer`) already seeds via `scripts/lineage.py`'s `main()` at the default path and asserts the real `_load_lineage_registry_if_engaged` reads it back, and EH.2's `tests/test_evolve_cli.py:2668` (`test_lineage_write_back_persists_advanced_head_and_logs`) already re-reads through `load_lineages` and asserts `created_at`/`pool_path`/`parent_chain`/`status` survive for both records. **What remains genuinely unbuilt is the `run_loop` → disk → API half of the chain, and the UNSCHEDULED-third-record case** — scope this step to those two and do not re-add coverage that already exists. Add one chain smoke that wires the real components: seed by calling `scripts/lineage.py`'s `main()` **in-process** under the same `monkeypatch.setattr(lineages_mod, "_repo_root", lambda: tmp_path)` harness §4 already designates (`tests/test_evolve_cli.py:2133-2156`), additionally passing `--path tmp_path / "data" / "lineages.json"`; the seeded path and the path `run_loop` reads MUST be the same file, because `_load_lineage_registry_if_engaged` hardcodes `default_lineages_path()` (`:3736`) with no path parameter and an unmonkeypatched seed would read — or write — the **real** repo-root registry. Drive real `run_loop` with `--lineages 3` for two generations, assert `_load_lineage_registry_if_engaged` returned `len(registry) == 3` (unpacking the `(registry, from_disk)` tuple EH.2 introduces, and asserting `from_disk is True`) rather than the single implicit `main` from `:3759-3768`, then assert the on-disk file, re-read via `load_lineages`, still contains **all three** records, that the **two** lineages which took a round-robin turn have `head_version` differing from their seed, and that the **unscheduled** third record is present with its seed `head_version` and seed `created_at` intact — a generation is one lineage's turn (`:4310-4333`), so three lineages over two generations leaves one unscheduled by construction, and that survivor assertion is the only one in the phase that catches a persist hop which writes only the live `_lineage_heads` dict, then serve `GET /api/evolve/lineages` against that same directory and assert the response body carries a non-empty `lineages` array with the **advanced** `head_version`. **Point the API at that directory explicitly** — `configure(tmp_path/"data", tmp_path/"logs", tmp_path/"replays", evolve_dir=tmp_path/"data")` before driving it through `fastapi.testclient.TestClient(app)`, reusing the `evolve_dir`/`client` fixture pair at `tests/test_api_evolve_lineages.py:26-41`. Monkeypatching `lineages_mod._repo_root` repoints the **writer only**; the endpoint resolves its directory from the module-level `_evolve_dir` that `bots.v13.api.configure(...)` sets, so without this call the consumer half of the chain reads the real repo-root registry and the smoke gate silently proves nothing. No SC2 process and no real games — the fitness function is stubbed; everything else is production code on its real import path.
- **Flags:** --reviewers code --isolation worktree
- **Produces:** new chain-smoke test in `tests/test_evolve_cli.py` (or `tests/test_evolve_chain_smoke.py`); extended `tests/test_api_evolve_lineages.py`
- **Done when:** full suite green at ≥ the EH.1-recorded pytest baseline (§7); the smoke completes in under 60 seconds; it **fails** when run against the pre-EH.2 persist hop (verify by reverting the hop locally and observing red — record that red-first evidence in the step checkpoint); and it asserts the API response, not just the file.



<!-- autofix-applied: 2026-11-11 -->


### Step EH.10: Operator verification — Gate 0 plus one armed short run

- **Status:** PENDING
- **Decision (2026-11-11):** D7 (2026-11-11) dropped EH.4/EH.5 as duplicate stop functionality. End the armed run at its second generation boundary with `-Generations 2` (expect `Stop reason: generations-reached`) or by closing the evolve console; the Stop Run, pending-badge and `dashboard-stop` checks below are void. Run it after ES.6 so the launcher check covers the headless WSL path (D16); it may share a session with ES.12.
- **Type:** operator
- **Issue:** #315
- **Files:** `scripts/baseline.py`, `scripts/lineage.py`, `scripts/evolve.py`, `docs/soak-test-runs`
- **Depends on:** EH.3, EH.6, EH.7, EH.9, ES.6
- **Source:** [evolve-operational-hardening-plan.md](../archived-plans/evolve-operational-hardening-plan.md); original EH.10.

- **Problem:** Six of the nine preceding steps change behaviour that only a real run can demonstrate, and three of them (EH.4, EH.6, EH.7) are deployment-seam changes — a launcher script, a control file the dashboard writes, and a startup guard — which unit tests structurally cannot exercise against the real environment. Perform the runbook's **Gate 0** first: `uv run python scripts/baseline.py add v10 v10 --note "frozen anchor"`, again for `v13`, then `list` (`../operator-gate-runbook.md`:85-92, :253-256). Then seed a 2-lineage registry with the new `scripts/lineage.py`, start a short run through the **launcher** (the real production entry point for the observatory button), and confirm on live substrate: the run does not stop after one generation; the dashboard's Lineages panel shows non-empty data with advancing heads; pressing **Stop Run** ends the run at the next generation boundary and the badge reflects a pending state until it does; and a second `python scripts/evolve.py` launched mid-run is refused rather than truncating `evolve_results.jsonl`.

  **Pre-flight, run from the repo root:**

  ```powershell
  cd $env:USERPROFILE\dev\Alpha4Gate
  git branch --show-current          # expect master-plan/phase-ev
  git diff --staged --stat           # MUST be empty - EH.8 has landed by now (EH.10 -> EH.6 -> EH.8), so this is belt-and-braces
  uv run python scripts/baseline.py list
  uv run python scripts/lineage.py list
  uv run python scripts/lineage.py add main v13
  uv run python scripts/lineage.py add line-2 v10
  uv run python scripts/lineage.py list      # expect 2 active records
  .\scripts\launch-evolve.ps1 -Hours 1 -Generations 0
  ```

  Do **not** run the launcher bare — post-EH.6 its defaults are `-Hours 4 -Generations 0`, an unbounded
  four-hour soak, which is exactly the long slot this step declines to consume. `-Hours 1` is a ceiling,
  not a plan: check (3), pressing **Stop Run**, is the intended terminator, pressed once the second
  generation boundary has been observed. Budget ~45-60 min of machine time at ~20 min/generation,
  serialized machine-wide against every other pending soak. The existing pid-less
  `data/evolve_run_state.json` is **expected** to be reaped by the first launch and must NOT be
  hand-deleted beforehand — deleting it converts this step's most valuable real-world check into a re-run
  of EH.7's Done-when (4).

  **Post-flight teardown, same shell:**

  ```powershell
  uv run python scripts/lineage.py list      # take the seeded ids from here, do not assume names
  uv run python scripts/lineage.py remove main
  uv run python scripts/lineage.py remove line-2
  uv run python scripts/lineage.py list      # expect empty
  git log --oneline -20                      # capture every [evo-auto] commit this run produced
  ```
- **Produces:** a verification record under `docs/soak-test-runs/` with the pre-flight state, the observed outcome of each of the four checks, and the run log excerpt showing `Stop reason: dashboard-stop`
- **Done when:** all four checks are observed and recorded **from the live environment's own evidence** (dashboard screenshot, run log, `data/lineages.json` before/after) rather than inferred from tests; Gate 0's `baseline.py list` output is captured; **the seeded registry is torn down before the record is filed** — remove every lineage id the run's own `scripts/lineage.py list` reports (`main`/`line-2` are only the runbook's example at `../operator-gate-runbook.md`:148-151), then capture a post-flight `list` showing none; `remove` routes through `write_lineages`, so the file normally remains on disk as `{}`, which is the pass condition, not a failure. If the operator deliberately keeps the registry live to feed EL.7 (#279), the record must say so **in writing** — the default is teardown, because a surviving non-empty registry engages multi-lineage for every later bare invocation (`scripts/evolve.py:3756-3757`), including EJ.7 #288, EJ.8 #289, EV.5 #295, Phase 7 Step 6 #280 and EI.14. **Do not tear down `data/baselines.json`** — Gate 0's frozen anchors are meant to persist (§3) and EH.3 depends on them. Commit posture is also recorded: the launcher cannot pass `--no-commit`, so re-confirm `git diff --staged --stat` is empty and `git branch --show-current` is `master-plan/phase-ev` immediately before launching, then capture `git log --oneline` for every `[evo-auto]` commit the run produced — or record explicitly that none were — plus `git show --stat` for each, confirming only `bots/<vN>/` and `bots/current/current.txt` paths appear. **Claims survival and control-correctness only** — this is not a throughput or fitness verdict, and it deliberately does **not** consume a long soak slot. **No step in this phase owns the multi-hour tier** — operator decision **P-2 as overridden** cut the co-tenant `Type: wait` step the draft carried (§3), so EH.10 is the phase's terminal verification and there is nothing downstream to hand off to. EH.10 claims nothing about time-dependent behaviour; whatever EL.7 (#279) or EI.14 happens to show is opportunistic evidence this plan neither owes nor collects.



<!-- autofix-applied: 2026-11-11 -->


### Step EI.1: Per-game log identity + retention

- **Status:** PENDING
- **Decision (2026-11-11):** Kept by D8 (2026-11-11); feeds Phase ES proposal evidence (ES-H).
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Files:** `src/orchestrator/selfplay.py`, `src/orchestrator/evolve.py`, `scripts/evolve.py`
- **Depends on:** EI.0, EH.10, ES.10 (RD2 order, 2026-11-16)
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.1.

- **Problem:** `src/orchestrator/selfplay.py:292` writes `logs/selfplay_{version}_{role}.log`, which burnysc2 opens `"w+"`, so every game truncates the previous game's only trace. Four coupled changes. **(a) Filename.** Use the literal template `selfplay_{version}_{role}_{match_id}.log`, keying on the `match_id` already minted at `selfplay.py:684`. **Keep the `selfplay_{version}_{role}` prefix** — the only machine consumer globs on the parent version (`src/orchestrator/evolve.py:1381`), so a name that drops the version can never match and the function silently returns its fallback string straight into the proposer prompt. Do NOT key on `game_index`: it is always 0 in PFSP mode (D-1). Threading `match_id` to the producer adds one parameter to **each** of `_build_match` (`selfplay.py:296-303`) and `_build_bot_process` (`:260`) — neither has a test caller, so the signature change is contained. **(b) One source of truth.** Expose the shape as a single importable format helper (not a raw string) that BOTH producer and consumer import, replacing the inline f-string at `:292` and the two duplicate glob patterns at `evolve.py:1381` and `:1383`. **(c) Reader budget.** Re-anchor `_read_log_tails` and bound it to a newest-N set — its 4,000-byte cap is applied **per file** inside the loop, so per-game naming turns an ~8 KB prompt section into games × 4 KB and then generations × games × 4 KB (D-2). **(d) Retention.** Add a dedicated `_prune_selfplay_logs(logs_dir, keep_n)` called at the **generation boundary inside `run_loop`**. Do **NOT** put it in `_cleanup_stale_round_files`: that function's sole caller sits behind `if concurrency > 1` at `scripts/evolve.py:3873`, so retention placed there never runs at the default `--concurrency 1` — which is also mandatory for every `--viewer` run — and even at higher concurrency it runs once at dispatcher startup, not per generation. Retention must never unlink a file belonging to the live run, and must not delete a prior run's retained logs on `--resume`.
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `src/orchestrator/selfplay.py` (log-name helper; `_build_match` and `_build_bot_process` signatures), `src/orchestrator/evolve.py` (`_read_log_tails` re-anchor + newest-N bound), `scripts/evolve.py` (`_prune_selfplay_logs` + its generation-boundary call site); a new producer→consumer round-trip test
- **Done when:** full `uv run pytest` green at or above the 2,007/2,024 baseline, ruff clean, mypy clean in both scopes (packages, and the `scripts/` gate at or below its 3-error baseline), with new tests asserting (1) two games in one batch produce two distinct log files, neither truncating the other; (2) an **integration test through the production caller** per [`code-quality.md`](../../../.claude/rules/code-quality.md) — generate logs via the real producer, then read them back via the real `_read_log_tails`, asserting non-empty content for a known match, because a pattern-only unit test passes while production silently degrades to the empty-glob fallback string; (3) the shared name helper is asserted `is`-identical between producer and consumer, per `code-quality.md` § "One source of truth for data-shape constants"; (4) the newest-N bound holds the tails section under a fixed byte ceiling given 50 files; (5) **retention actually fires through `run_loop` at `--concurrency 1`** — a direct call to the retention helper does NOT satisfy this clause, because that is exactly the silent-wiring shape this step exists to avoid; (6) retention never unlinks a file whose mtime is inside the live run's window, and a `--resume` does not delete the prior run's retained logs. The two existing prompt assertions at `tests/test_evolve.py:1841` and `:1872` still pass unmodified.



<!-- autofix-applied: 2026-11-11 -->


### Step EI.2: `src/orchestrator/trace.py` — stdlib trace parser

- **Status:** PENDING
- **Decision (2026-11-11):** Kept by D8 (2026-11-11); feeds Phase ES proposal evidence (ES-H).
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Files:** `src/orchestrator/trace.py`, `tests/test_trace.py`
- **Depends on:** EI.0, EH.10, ES.10 (RD2 order, 2026-11-16)
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.2.

- **Problem:** Add a leaf module that parses one game's console log into a structured record and extracts **mechanical** features only (terminal-result marker present, step count, duration, status-chunk counts). No `bots.*` import, no `evolve.py` import, no play-quality judgement — round 2 measured that whole class at chance. Pure stdlib so it is unit-testable in isolation and cannot break a run by existing.
- **Flags:** --reviewers code --isolation worktree
- **Produces:** new `src/orchestrator/trace.py`; `tests/test_trace.py`
- **Done when:** full suite green, mypy clean (this module IS in mypy's scope), with tests covering a complete log, a truncated log, an empty file, and a log that predates the status-log line entirely — the last case matters because 36.7% of the existing candidate corpus has no status line at all, and the parser must report absence rather than inferring a value.



<!-- autofix-applied: 2026-11-11 -->


### Step EI.3: Trace capture at the two existing game-end seams

- **Status:** PENDING
- **Decision (2026-11-11):** Kept by D8 (2026-11-11); feeds Phase ES proposal evidence (ES-H).
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Files:** `src/orchestrator/evolve.py`, `scripts/evolve.py`, `scripts/evolve_worker.py`, `src/orchestrator/trace.py`
- **Depends on:** EI.1, EI.2
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.3.

- **Problem:** Wire `trace.py` into the fitness and regression game-end callbacks (`src/orchestrator/evolve.py:626` and `:827`), writing one parsed trace per game, fail-open so any parse error logs and continues rather than aborting a soak. **Capture is gated on a new `--trace-capture` flag, default OFF**, so the bare invocation stays byte-identical; thread it through the worker argv as well as the serial path, since the fitness callback runs inside the worker at `--concurrency > 1`. Traces are written to `logs/traces/` under a filename shape exported as a single importable constant from `trace.py`, mirroring EI.1's treatment of the log name, and pruned by the same `_prune_selfplay_logs`-style generation-boundary helper with its own `keep_n`. Sequenced after EI.1 so the per-worker splice cause is already removed; do NOT disable under concurrency, which would make the layer inert on the most recently recorded soak configuration.
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `src/orchestrator/evolve.py`, `scripts/evolve.py` (flag + generation-boundary prune), `scripts/evolve_worker.py` (argv), `src/orchestrator/trace.py` (output-name constant); capture tests
- **Done when:** full suite green; a test drives a fake batch through the **production** callback path and asserts a trace lands per game; a test asserts a raising parser does not propagate and does not prevent the caller's own callback from running; an assertion that with `--trace-capture` absent the callback chain is byte-identical; and an assertion that the flag reaches the worker argv.



<!-- autofix-applied: 2026-11-11 -->


### Step EI.4: Proposer evidence provenance

- **Status:** PENDING
- **Decision (2026-11-11):** Kept by D8 (2026-11-11); feeds Phase ES proposal evidence (ES-H).
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Files:** `data/selfplay_results.jsonl`, `src/orchestrator/evolve.py`
- **Depends on:** EI.1
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.4.

- **Problem:** The proposer is handed log tails with nothing saying which games they are. Label each tail with its file, seat, opponent and outcome. **Join on the `match_id` that EI.1 puts into the filename, against the persisted `data/selfplay_results.jsonl`** — NOT against the in-scope mirror `records` at `src/orchestrator/evolve.py:1823`, which cover mirror games only while most tails are fitness games and would therefore go unlabelled. Specify the degraded label for a tail whose `match_id` has no matching row (e.g. `(unjoined — no result row for this match)`) so a missing join fails honestly rather than silently. **Do not** add a "no mirror games this generation" line: shipped code already prints `Mirror games run (parent vs parent): 0` on refresh generations, so round 1's framing of this as a false label was wrong; the defect is missing provenance only. **This step depends on EI.1** — before per-game log identity exists there is exactly one truncated file per (version, seat) and nothing to join a tail to, so labelling it would stamp false provenance onto the most expensive call in the loop.
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `src/orchestrator/evolve.py` prompt assembly
- **Done when:** full suite green; a test asserts the assembled prompt names the seat and outcome for a known record; a test asserts the degraded label is emitted for an unjoinable tail rather than a wrong one; the two existing prompt assertions still pass. There are **no prompt goldens** in this repo (verified: zero test references to the prompt builder or log-tail reader), so this step has no golden-update cost.

<!-- autofix-applied: 2026-09-02 -->


### Step EI.5: `SelfPlayRecord` evaluation id + bidirectional compat

- **Status:** PARKED — D8 (2026-11-11): outside the reduced evidence layer; Phase ES planning (ES.0) may retire it
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Files:** `src/orchestrator/selfplay.py`, `src/orchestrator/evolve.py`, `src/orchestrator/ladder.py`, `scripts/ladder.py`, `scripts/evolve.py`, `src/orchestrator/contracts.py`, `selfplay.py`, `evolve.py`, `ladder.py`, `scripts/evolve_worker.py`, `tests/fixtures/selfplay_results_legacy.jsonl`, `tests/test_selfplay.py`, `tests/test_contracts.py`
- **Depends on:** EI.0, EH.10
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.5.

- **Problem:** Add `fitness_eval_id: str | None = None` and `eval_game_index: int | None = None` to `SelfPlayRecord` as **optional with defaults**, stamp them at the single production construction site (`src/orchestrator/selfplay.py:730`), thread the id through `run_batch` as a keyword-only argument and pass it at the **four** `run_batch_fn(` call sites in `src/orchestrator/evolve.py` — `:650` (fitness), `:858` (regression), `:1017` (baseline gauntlet), `:1819` (mirror). **`fitness_eval_id` is a `str(uuid.uuid4())` minted once per `run_batch` invocation by the CALLER**, mirroring how `match_id` is minted per game at `selfplay.py:684`; `eval_game_index` is the zero-based index of the game within that batch. Callers that do not supply an id leave both fields `None`, which is what every historical row already means. Consumers: the two replay loops (`src/orchestrator/ladder.py:359`, `scripts/ladder.py:142`), the parallel mirror rehydration in `scripts/evolve.py`, the worker result path, and any later analysis that needs to group a 5-game evaluation without timestamp clustering — which is the whole point of the field. and harden `from_json` to **filter unknown keys** rather than only supplying defaults (D-4). Apply the same tolerance at `src/orchestrator/evolve.py:205`, which bypasses `from_json` entirely (D-5), and in lockstep at both replay loops (`src/orchestrator/ladder.py:359` and `scripts/ladder.py:142`) and the parallel mirror rehydration.

  **`SelfPlayRecord` current shape** (`src/orchestrator/contracts.py:121-145`, verified 2026-09-02) — the nine keys every one of the 1,792 existing rows carries:

  | Field | Type | Note |
  |---|---|---|
  | `match_id` | `str` | `str(uuid.uuid4())`, minted per game at `selfplay.py:684`; the EI.1 filename key and the EI.4 join key |
  | `p1_version` | `str` | |
  | `p2_version` | `str` | |
  | `winner` | `str \| None` | version string of the winning side; `None` for draw/crash |
  | `map_name` | `str` | |
  | `duration_s` | `float` | |
  | `seat_swap` | `bool` | always `False` in PFSP mode (D-1 corollary) |
  | `timestamp` | `str` | ISO 8601 |
  | `error` | `str \| None = None` | the only field carrying a default today |

  Frozen dataclass. `to_json` is `dataclasses.asdict`; `from_json` is a strict `cls(**json.loads(data))` — which is what D-4 hardens. The two new fields are additive optional-with-default, making an eleven-key row (nine plus two).

- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `src/orchestrator/contracts.py`, `selfplay.py`, `evolve.py`, `ladder.py`; `scripts/ladder.py`, `scripts/evolve.py`, `scripts/evolve_worker.py`; a NEW tracked fixture `tests/fixtures/selfplay_results_legacy.jsonl` (~50 rows copied verbatim from the live file); extended `tests/test_selfplay.py`, new `SelfPlayRecord` coverage in `tests/test_contracts.py`
- **Done when:** full suite green; tests assert (1) a legacy nine-key line loads and yields defaults; (2) a dataclass constructed **without** the new fields accepts a wider eleven-key payload — this simulates an older build and is the honest form of the roll-back claim (see D-4); (3) a round trip that **sets the new field to a non-default value** — dataclass equality passes trivially when both sides hold equal defaults, so a default-valued round trip cannot detect the drift; (4) the **tracked fixture** replays without error through **both** replay loops. Do NOT assert against `data/selfplay_results.jsonl`: it is gitignored (`.gitignore:42`) and this step runs `--isolation worktree`, so the file does not exist there and the assertion would be silently skipped or fabricated. The full 1,792-row replay moves to EI.14's operator checklist, where the file exists. (5) the id is actually stamped by `run_batch` and groups a 5-game evaluation.

<!-- autofix-applied: 2026-09-02 -->


### Step EI.6: Persist `wins_parent` and games-played

- **Status:** PENDING
- **Decision (2026-11-11):** Kept by D8 (2026-11-11); feeds Phase ES proposal evidence (ES-H).
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Files:** `frontend/src/hooks/useEvolveRun.ts`, `scripts/evolve.py`, `frontend/src/components/EvolutionTab.test.tsx`
- **Depends on:** EI.0, EH.10, ES.10 (RD2 order, 2026-11-16)
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.6.

- **Problem:** `PerItemState` records `fitness_score = [wins_candidate, games]` and drops the parent's win count, and the denominator is the **declared** games rather than games played. Persist both **as NEW keys (`wins_parent`, `games_played`)** — do **not** change the arity of `fitness_score` or of the results-row `score`. Both are rendered as fixed-arity pairs by the dashboard (`frontend/src/hooks/useEvolveRun.ts:29` types `fitness_score` as `[number, number] | null`; `EvolutionTab.tsx:886` renders `{item.fitness_score[0]}/{item.fitness_score[1]}`), so widening either list in place silently mislabels wins and games in the Evolution tab — the wire-shape failure class in [`code-quality.md`](../../../.claude/rules/code-quality.md) § "Audit wire shape when storage representation changes". **All four copies of the pool-state field list must change in the same commit** — the dataclass, `to_json`, `from_json`, and the hardcoded pop-tuple in `load_pool_state` (`scripts/evolve.py:928-935`) — or every `--resume` of a run started after this change dies at startup with a single swallowed log line (D-6).

  **`PerItemState` current field list** (`scripts/evolve.py:827-838`, verified 2026-09-02) — the six keys that must stay in lockstep across all four copies: `status` (default `_ACTIVE`), `fitness_score` (`list[int] | None`, the fixed-arity `[wins_candidate, games]` pair), `retry_count` (`int`), `first_evaluated_against` (`str | None`), `last_evaluated_against` (`str | None`), `consecutive_null_diffs` (`int`, added by Phase EJ.2 — the in-repo precedent for an additive defaulted field here). The fourth copy is the hardcoded pop-tuple in `load_pool_state` (`scripts/evolve.py:928-935`), which pops exactly these six and splats whatever remains into the frozen `Improvement`.

- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `scripts/evolve.py`; pool-state round-trip tests; a frontend render assertion in `frontend/src/components/EvolutionTab.test.tsx`
- **Done when:** full suite green; a **full write-then-load round-trip test** (not isolated `to_json`/`from_json` unit tests, which cannot see this failure) proves a pool file written by the new build loads; a pool file written by the **old** build still loads; a fitness row carries both win counts plus games played alongside declared games; and `cd frontend && npm run test:run` green at or above 234 with an assertion that the Evolution tab's wins/games render is **unchanged** — the new values are additive keys, not a widened pair.



<!-- autofix-applied: 2026-11-11 -->


### Step EI.7: Patch capture, log-only

- **Status:** PARKED — D8 (2026-11-11): outside the reduced evidence layer; Phase ES planning (ES.0) may retire it
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Files:** `src/orchestrator/evolve_dev_apply.py`, `src/orchestrator/evolve.py`, `scripts/evolve.py`, `scripts/evolve_worker.py`
- **Depends on:** EI.0, EH.10
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.7.

- **Problem:** Capture the fitness-time patch and the stack-time patch as a **log-only artifact**, behind a new `--capture-patch` flag (default OFF). **The pre-edit content is NOT collected on the default path.** `_collect_candidate_py_content` is guarded at `src/orchestrator/evolve_dev_apply.py:323-325` on `screen_null_diff`, which defaults OFF — so today `content_before` is an empty dict and there is nothing to diff against. Widen that guard to `if screen_null_diff or capture_patch`, so patch capture does not silently depend on an unrelated Phase EJ flag being armed. Dev-apply runs **inside the worker**, so `--capture-patch` genuinely crosses the subprocess boundary and must be threaded through the worker argv, not only bound serially. Store the patch in an applicable form so deterministic replay becomes a follow-on rather than a rebuild. **This is not a decision input**: round 2 deleted round 1's pre-registered threshold rule, because both of its branches point the same way and a clean-apply check cannot separate "same idea, differently implemented" from "different idea". If the captured value crosses the worker boundary it must be added to **both** hand-written `FitnessResult` serializers (D-11).
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `src/orchestrator/evolve_dev_apply.py` (widened guard), `src/orchestrator/evolve.py`, `scripts/evolve.py` (`--capture-patch` argparse + serial binding), `scripts/evolve_worker.py` (argv parse + forward)
- **Done when:** full suite green; a test asserts a patch is captured **with `--capture-patch` ON and `--screen-null-diff` OFF** — the flag-off-sibling case is the one that matters, because a test that arms both would pass over a production no-op; a test asserts the capture is absent-not-crashing for a candidate that changed no files; a test asserts the default path is byte-identical with the flag absent; a test asserts the flag reaches the worker argv; and if the value is ferried, a round-trip test sets it to a non-default value and asserts it survives the worker result file.



<!-- autofix-applied: 2026-11-11 -->


### Step EI.8: `--fitness-stop rule|majority`

- **Status:** PARKED — D8 (2026-11-11): outside the reduced evidence layer; Phase ES planning (ES.0) may retire it
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Files:** `src/orchestrator/evolve.py`, `scripts/evolve.py`, `docs/archived-plans/evolve-viewer-plan.md`, `scripts/evolve_worker.py`, `claude/skills/improve-bot-evolve/SKILL.md`
- **Depends on:** EI.6
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.8.

- **Problem:** The gate-1 early stop is hardcoded to a strict majority and is **not** bucket-neutral (D-8). Add a `played` counter to the fitness callback — it does not exist, and the regression sibling's arithmetic cannot be ported without it (D-9) — then extract a pure `_fitness_stop_locked(...)` helper (the sibling of `_regression_stop_locked` at `src/orchestrator/evolve.py:689`) and add `--fitness-stop rule|majority` defaulting to `majority`, which must be byte-identical to today. Thread it through the **worker argv** as well as the serial path, or it is a silent no-op under concurrency. Correct the false in-source comment claiming the stop saves 2-3 games with no effect on bucket assignment, and the copies of that claim in the viewer wrapper docstring (`scripts/evolve.py:600`) and `docs/archived-plans/evolve-viewer-plan.md:249`.
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `src/orchestrator/evolve.py`, `scripts/evolve.py`, `scripts/evolve_worker.py`, `.claude/skills/improve-bot-evolve/SKILL.md`, `docs/archived-plans/evolve-viewer-plan.md`
- **Done when:** full suite green, which requires the three clauses below to be **mutually satisfiable** — they are only if the property is scoped as stated. (1) An exhaustive soundness property parametrized over **`--fitness-stop rule` ONLY** (not over `majority`), asserting the rule-derived stop never changes a bucket versus full play. Run it against the extracted `_fitness_stop_locked` **helper**, not against `run_fitness_eval` — the primitive does a real `snapshot_current()` tree copy per call, measured at ~34 ms against the regression twin's ~56 µs, so a literal mirror of that test's parametrization would add roughly 25 minutes to the gate that flips every later step DONE. Budget: the new property adds **under 30 seconds**. Pair it with a handful of end-to-end `run_fitness_eval` cases proving the helper is actually wired. (2) A separate **characterization** test pinning the majority stop's known non-neutrality by enumerating the five-game sequences where it collapses the near-miss bucket into fail — so the defect stays codified as a known deviation rather than being silently re-blessed or silently fixed. (3) A test asserting `--fitness-stop majority` is byte-identical to today, and one asserting the flag reaches the worker argv, which the existing `--regression-rule` test pair does **not** cover. **The two existing early-stop tests are clean sweeps and remain valid unmodified** — neither stops one win short, so neither codifies the collapse; this step ADDS a near-miss case they cannot reach. Any diff that modifies either existing test is suspect and must be justified, per [`code-quality.md`](../../../.claude/rules/code-quality.md) § "Audit wire shape".



<!-- autofix-applied: 2026-11-11 -->


### Step EI.9: `--stack-size K`

- **Status:** PARKED — D8 (2026-11-11): outside the reduced evidence layer; Phase ES planning (ES.0) may retire it
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Files:** `scripts/evolve.py`, `claude/skills/improve-bot-evolve/SKILL.md`
- **Depends on:** EI.6
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.9.

- **Problem:** Cap how many gate-1 winners are stacked into one promotion (`winner_idxs`, `scripts/evolve.py:4559-4562`), default 0 = unlimited = today. Three changes are mandatory together (D-7): exempt deferred winners from `_RETRY_CAP` so a winner that passed gate 1 is not evicted after two more evaluations; stop `promoted_imp_idxs = list(winner_idxs)` at `:4676` from stamping deferred winners as promoted, which would poison the promoted-titles exclusion and the rollback path; and keep deferred winners visible to `_count_active` so the pool does not top up past `--pool-size` every generation.
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/evolve.py`, `.claude/skills/improve-bot-evolve/SKILL.md`
- **Done when:** full suite green; tests assert (1) at `K=0` behaviour is byte-identical; (2) at `K=1` only the rank-1 winner is stacked and the rest are deferred, **not** evicted and **not** marked promoted; (3) a deferred winner survives more than `_RETRY_CAP` generations; (4) `_count_active` includes deferred winners so `delta` is unchanged; (5) an **integration test through `run_loop`** proving the cap reaches the real stack-apply call rather than being unit-tested in isolation.



<!-- autofix-applied: 2026-11-11 -->


### Step EI.10: `--stack-model MODEL`

- **Status:** PARKED — D8 (2026-11-11): outside the reduced evidence layer; Phase ES planning (ES.0) may retire it
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Files:** `scripts/evolve.py`, `scripts/evolve_inject_one.py`, `claude/skills/improve-bot-evolve/SKILL.md`
- **Depends on:** EI.0, EH.10
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.10.

- **Problem:** The operator's idea (i), literally. `spawn_dev_subagent` takes a `model` keyword that **no production caller sets**. Bind a `functools.partial` at the single `stack_apply_fn(` call site (`scripts/evolve.py:4609`), leaving the fitness binding at `:4434` untouched. State explicitly in the step whether `scripts/evolve_inject_one.py:211` — the second production caller, invisible to the obvious grep — is in or out of the flag's reach (D-10).
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/evolve.py`, `.claude/skills/improve-bot-evolve/SKILL.md`
- **Done when:** full suite green; an integration test through `run_loop` (template: the existing null-diff integration test) proves the escalated model reaches the **stack** dev-apply and **not** the fitness one; a test asserts the **fitness** dev-apply callable at `scripts/evolve.py:4434` stays the bare identity when the flag is absent, preserving the two existing byte-identical-default assertions. Note `scripts/evolve_worker.py` has **zero** stack involvement (`rg -n "stack"` returns nothing), so this step needs no worker edit and no worker-argv assertion.



<!-- autofix-applied: 2026-11-11 -->


### Step EI.11: `--stack-static-gate`

- **Status:** PARKED — D8 (2026-11-11): outside the reduced evidence layer; Phase ES planning (ES.0) may retire it
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Files:** `scripts/evolve.py`, `src/orchestrator/evolve_dev_apply.py`, `claude/skills/improve-bot-evolve/SKILL.md`, `docs/wiki/operator-commands.md`
- **Depends on:** EI.9
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.11.

- **Problem:** Per-improvement mypy validates changed files only, so cross-improvement signature drift is invisible. Add a whole-package `mypy --strict` on the new version **before** the `[evo-auto]` commit. Ship it with **two modes**: `--stack-static-gate=observe` records the verdict and the outcome token but never blocks a promotion or drops a rank, and `--stack-static-gate=enforce` reuses the existing cleanup path on failure (never the fragile revert path) with a drop-lowest-rank rule so a repeated static failure cannot livelock the retry ladder. Default is OFF. The observe mode exists because a blocking gate changes stack composition and can suppress a promotion — which suppresses the regression evaluation gated on `promoted_imp_idxs` at `scripts/evolve.py:4857`, removing both a promotion and `--games-per-eval` games from a generation. That is a real perturbation and must not be armed inside another phase's measurement (see EI.14). The gate takes an **injectable callable defaulted off**, mirroring the existing import-check seam, because an unconditional gate would fire in the debug tool and change the operator's ~10-minute iteration cycle (D-10). **The new outcome token literal is `stack-apply-static-fail`**, joining the ten existing members of the `PhaseOutcome` literal at `scripts/evolve.py:957-968` alongside its `stack-apply-import-fail` and `stack-apply-commit-fail` siblings. That exact string must appear identically in `scripts/evolve.py`, the frontend union, `SKILL.md` and the wiki token list, or a graded skill eval fails (D-12).
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/evolve.py`, `src/orchestrator/evolve_dev_apply.py`, `.claude/skills/improve-bot-evolve/SKILL.md`, `docs/wiki/operator-commands.md`
- **Done when:** full suite green; a primitive test mirrors the existing import-fail rollback test's assertion set for a static failure **in `enforce` mode**; a test asserts `observe` mode records a verdict and leaves `promoted_imp_idxs`, the commit and the regression phase untouched; a test asserts the gate does not run when the flag is off, so the four existing stack-apply primitive tests do not shell out to a real mypy against a synthetic tree; the outcome token is present in the skill flag table and the wiki token list, and the two live SKILL.md assertion tests still pass.



<!-- autofix-applied: 2026-11-11 -->


### Step EI.12: Frontend token repair + dashboard file counts

- **Status:** PARKED — D8 (2026-11-11): outside the reduced evidence layer; Phase ES planning (ES.0) may retire it
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Files:** `frontend/src/hooks/useEvolveRun.ts`, `bots/v13/process_registry.py`, `frontend/src/components/TimelineList.tsx`
- **Depends on:** EI.1, EI.3, EI.11
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.12.

- **Problem:** The `EvolveOutcome` union in `frontend/src/hooks/useEvolveRun.ts` is a hand-maintained duplicate of the Python outcome literal and is **already** missing `screen-null-diff` from Phase EJ.2 — the drift is invisible because the value is consumed as a widened type. Add `stack-apply-static-fail`, repair the existing drift, and add the token to `WARNING_OUTCOMES` in `TimelineList.tsx` beside its import-fail and commit-fail siblings. **Also extend `get_temp_file_counts()`** (`bots/v13/process_registry.py:268-285`): its loop is suffix-filtered to `.jsonl` and `.SC2Replay`, so the dashboard cannot even count the `.log` and trace files this phase creates. Add rows for `logs/*.log` and `logs/traces/`, so the operator can see growth on the Processes tab rather than discovering it on disk.
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `frontend/src/hooks/useEvolveRun.ts`, `frontend/src/components/TimelineList.tsx`, `bots/v13/process_registry.py`
- **Done when:** `cd frontend && npm run test:run` green at or above 234; a test asserts the union matches the Python `PhaseOutcome` literal member-for-member, so this drift cannot recur silently; `npm run build` clean; full `uv run pytest` green with a test asserting the new file-count rows appear in `get_temp_file_counts()`.



### Step EI.13: Producer→consumer smoke gate

- **Status:** PENDING
- **Decision (2026-11-11):** Kept by D8 (2026-11-11) as a smaller chain smoke: re-scope its dependencies to the kept steps EI.1–EI.4 and EI.6 at EI.0 (EI.5 and EI.7 are parked).
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.1, EI.2, EI.3, EI.4, EI.5, EI.6, EI.7
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.13.

- **Problem:** An end-to-end run of the evidence chain with real components, exercising producer→consumer drift that mock-bounded unit tests cannot see — each such test mocks the boundary it would have asserted on. **The ONLY substitution permitted is the `GameMatch` runner inside `_build_match`**, replaced by a fake that writes a real recorded console log to the production path via the production log-name helper. Everything downstream runs unmocked: the log write, the trace parse, `_read_log_tails`, prompt assembly with provenance, the pool-state write-then-load round trip, and both `SelfPlayRecord` replay loops. **No SC2 process launches and no games are played**, which is what keeps this step inside the phase's zero-added-games claim and runnable in a worktree. Do not reach for a real batch: the log file is written only by burnysc2's bot process, so "real batch" would mean a real StarCraft II game, which cannot run in the default suite (`addopts = "-m 'not sc2'"`), cannot finish in seconds, and would add games.
- **Flags:** --reviewers code --isolation worktree
- **Produces:** a chain-smoke test in the standard suite exercising the full evidence chain; recorded output
- **Done when:** the chain completes with no exception; the log files produced are distinct per game and carry the production name shape; the trace parser reads every one; the proposer evidence block is non-empty and carries provenance including the degraded label for an unjoinable tail; the pool file round-trips; and the **tracked fixture** `tests/fixtures/selfplay_results_legacy.jsonl` replays clean through both replay loops. Do NOT assert against `data/selfplay_results.jsonl` — it is gitignored and absent from a worktree. Run it **before** EI.14.



<!-- autofix-applied: 2026-11-11 -->


### Step EI.14: Shadow-half observation soak

- **Status:** PARKED — D8 (2026-11-11): outside the reduced evidence layer; Phase ES planning (ES.0) may retire it
- **Type:** wait
- **Issue:** not yet assigned; no issue created by cleanup
- **Files:** `docs/soak-test-runs`
- **Depends on:** EI.11, EI.12, EI.13, EL.7
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.14.

- **Problem:** The evolve loop is unattended, multi-hour, wall-clock-dependent behaviour, and this phase adds capture that runs inside it. Time-dependent failures — unbounded log growth, retention deleting a live file, prompt-budget creep across generations, a fail-open path that silently swallows every trace — are invisible to the EI.13 chain smoke.

  **Host: EL.7 (#279), the 6-hour multi-lineage soak.** Round 1's "ride the flag-OFF shadow half of a queued soak" is not runnable as written — none of EJ.7, EJ.8 or EL.7 has a multi-hour flag-off half. EJ.7's only flags-off leg is a 15-minute smoke, and EJ.8 and EL.7 are each a single flags-on run compared against *historical* controls. EL.7 is chosen because it is the longest queued gate and its recorded observations (generations per lineage, diversity matrix, extinction, orphan processes, commit hygiene) do not include any metric this phase's capture can move.

  **Arming set — log-only, and nothing else.** Arm exactly `--trace-capture`, `--capture-patch`, and the EI.1 log naming and retention, which are unconditional. Do **NOT** arm `--stack-static-gate` in `enforce`, `--stack-size`, or `--fitness-stop rule` on a host run: each changes stack composition or games per generation. `observe` mode of the static gate is permitted because it records without blocking. **EI claims nothing from EL.7's own result table**, and EL.7's record must note the co-tenancy.

  **The command.** EL.7's own invocation with this phase's log-only flags appended. Read the
  runbook's EL.7 section first — it carries two blocking preconditions (register frozen baselines,
  and decide the lineage-registry path, because `--lineages N` does not create N lineages):

  ```powershell
  cd $env:USERPROFILE\dev\Alpha4Gate
  git branch --show-current          # expect master-plan/phase-ev
  git diff --staged --stat           # MUST be empty - evo-auto commits sweep the whole index
  uv run python scripts/evolve.py --lineages 4 --population-cap 3 --hours 6 --generations 0 --fitness-mode both --trace-capture --capture-patch --stack-static-gate observe
  ```

  **Zero added SC2 games**, and that claim is now true rather than assumed: the armed set writes files and records rows, and touches neither gating nor composition. Serialize against the other pending soaks — one evolve run at a time, machine-wide. See [`../operator-gate-runbook.md`](../operator-gate-runbook.md), which also carries EL.7's own two blocking preconditions (the absent baselines registry, and the fact that `--lineages N` does not create N lineages).
- **Produces:** an observation report under `docs/soak-test-runs/`
- **Done when:** the soak completes and the report records: one trace file per game and worker, not one per version and seat; a bounded `logs/` directory after retention, with no file from the live run unlinked; the proposer evidence block naming each tail's game, seat, opponent and outcome for at least one refresh generation; every fitness row carrying a non-null parent win count and evaluation id; at least one whole-package type-check verdict recorded in **observe** mode (advisory, non-blocking — not a blocked promotion); the gate-1 accept rate reported with an exact binomial p-value against the 0.5 null; and the full `data/selfplay_results.jsonl` replaying clean through both replay loops in the main project tree, where the file exists. **Claims survival and instrumentation only** — no throughput verdict, which would need a paired control.

---


<!-- autofix-applied: 2026-11-11 -->


### Step EI.0: Approve evidence-layer defaults and synchronize issues

- **Status:** HELD — product decisions and issue synchronization pending
- **Decision (2026-11-11):** D8 (2026-11-11): approve only the reduced set EI.1–EI.4, EI.6 and a smaller EI.13; the other EI steps are parked.
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Done when:** <TBD — operator fills in>
- **Source:** [evolve-evidence-layer-plan.md](../archived-plans/evolve-evidence-layer-plan.md); original EI.0.

EI remains planned, not product-approved by this cleanup. Review/redline the existing choices and mint its fourteen missing issue IDs before implementation. The historical mesh freeze was lifted; it is not a present blocker. Do not downgrade EI.1/EI.5/EI.6 deep review. Resolve reach of stack-model into evolve_inject_one explicitly. This planning decision is independent of the truncated idea (ii).


### Step EL.7: Multi-hour observation soak

- **Status:** PARKED — D9 decided 2026-11-16 (T5): parked until Stage 1 passes
- **Type:** wait
- **Issue:** #279
- **Depends on:** EH.10
- **Source:** [evolution-lines-plan.md](../archived-plans/evolution-lines-plan.md); original EL.7.

- **Problem:** Run 2–3 lineages under a multi-hour budget
  (`--lineages 4 --population-cap 3 --hours 6 --generations 0 --fitness-mode both`, after seeding four actual registry records) and
  observe the autonomous loop: lineages diverge on fingerprint, ≥1
  extinction event fires (or is explicitly documented as not-triggered-by-design; multi-lineage scheduling must still be observed),
  baseline-fitness trends are sane, no orphan SC2 processes, EVO_AUTO commit
  hygiene holds. This is the autonomous-behavior observation step — the loop
  runs unattended over wall-clock time, so time-dependent failures
  (scheduler drift, registry write races, extinction thrash) are invisible
  to unit tests.
- **Produces:** soak record under
  `docs/soak-test-runs/evolution-lines-soak-<ts>.md`.
- **Done when:** ≥2 generations per lineage complete; diversity matrix shows
  non-trivial separation; extinction logic exercised at least once or
  documented as not-triggered-by-design; zero orphan processes; commit log
  shows clean `[evo-auto]` rows.



<!-- autofix-applied: 2026-11-11 -->


### Step M1: Train 3 cycles + measure early-game reward variance

- **Status:** HELD — D11 (2026-11-11): under PPO.0 exploration (not parked); PPO.0 keeps, rewrites or retires this step
- **Type:** operator
- **Issue:** #170
- **Depends on:** none outstanding; retain shipped prerequisites
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-d-build-plan.md](../archived-plans/phase-d-build-plan.md); original M1.

**Problem:** Train 3 PPO cycles with `use_build_order_reward: true` against the current curriculum, then aggregate early-game reward variance and difficulty-3 win rate to test the Phase D premise.

**Commands.**

```powershell
# Restart evolve daemon (paused during automated run, see preflight) — optional if M1 runs immediately:
# uv run python scripts/evolve.py --hours 0  # do NOT restart until after M1 finishes

# Train 3 cycles (foreground or `Start-Process` background):
uv run python -m bots.current.runner --serve --daemon --decision-mode hybrid
# ...wait for 3 cycles to complete (~2-4h depending on game length)...

# Early-game (first 5 min) reward std-dev — inline measurement, no script needed.
# Run BEFORE flipping the flag (baseline) and again AFTER 3 cycles with flag on:
uv run python -c @"
import json, statistics
from pathlib import Path
logs = Path('bots/v13/data/reward_logs').glob('game_*.jsonl')
rewards = []
for log in logs:
    for line in log.read_text().splitlines():
        if not line: continue
        rec = json.loads(line)
        if rec.get('game_time', 0) <= 300:
            rewards.append(rec['total_reward'])
print(f'n={len(rewards)} mean={statistics.mean(rewards):.4f} stdev={statistics.stdev(rewards):.4f}')
"@

# Deterministic eval (20 games at difficulty 3):
uv run python scripts/evaluate_model.py --difficulty 3 --games 20
```

**What to look for.**

| Metric | Pass | Fail |
|---|---|---|
| Early-game (first 5 min) reward std-dev | ≥ 30% drop vs pre-D.6 baseline | < 30% drop or no change |
| Difficulty-3 win rate (20 games) | ≥ baseline WR | regression |

If variance drops but WR regresses, tune `build_order_reward_alpha` down (try 0.5, then 0.25) and re-run before snapshotting. If variance does not drop after 3 cycles, kill per §7.

**Done when.** Results appended to `docs/soak-test-runs/phase-d-M1-<TS>.md` with both metrics and a snapshot/kill decision. Pre-D.6 baseline numbers cited explicitly (rerun `reward_aggregator` on a recent pre-D.6 log for the comparison).




<!-- autofix-applied: 2026-11-11 -->


### Step M2: Snapshot to `vN+1` on promotion

- **Status:** HELD — D11 (2026-11-11): under PPO.0 exploration (not parked); PPO.0 keeps, rewrites or retires this step
- **Type:** operator
- **Issue:** #171
- **Depends on:** M1
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-d-build-plan.md](../archived-plans/phase-d-build-plan.md); original M2.

**Problem:** If M1 gates passed, snapshot the new stack and run cross-version Elo to validate promotion.

**Commands.**

```powershell
# Snapshot from bots/current/ (= v13) into the next auto-numbered vN+1:
uv run python scripts/snapshot_bot.py
# Note the new version name printed by the script (likely "v14"). Use it below.

# Update bots/current/current.txt to point at the new version IF this is the new baseline.
# Skip this if Phase D is staying behind the flag and current should keep pointing at v13:
# Do not manually repoint current: bare snapshot resolves the actual version and performs its pointer update.

# Cross-version Elo via the compare subcommand (20 games new-vs-v13):
uv run python scripts/ladder.py compare v14 v13 --games 20
```

**What to look for.**

| Metric | Pass | Fail |
|---|---|---|
| Elo gain vs v13 | ≥ +10 over 20 self-play games | < +10 or regression |
| WR vs SC2 AI diff 3 (sanity) | hold vs v13 | regression |

**Done when.** `bots/v14/` exists with manifest lineage; `check_promotion()` passes ([src/orchestrator/ladder.py](../../src/orchestrator/ladder.py)); ladder table shows new entry.


---

Canonical correction from the operator runbook: use the actual emitted new version and previous version in the ladder command; v14/v13 are historical examples, never reserved identities. Never pass --from current (that is the four-file shim). Snapshot only after M1 passes; preserve source stack and reward backup.


<!-- autofix-applied: 2026-11-11 -->


### Step 6.1: `/improve-bot-advised --opponent vN` curriculum flag

- **Status:** HELD — D11 (2026-11-11): under PPO.0 exploration (not parked); PPO.0 keeps, rewrites or retires this step
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-6-build-plan.md](../archived-plans/phase-6-build-plan.md); original 6.1.

**What to build.** Today `/improve-bot-advised --self-improve-code`
validates trainee runs against SC2 built-in AI. Phase 6 swaps in a
prior `bots/vN/` opponent via subprocess self-play. Operator passes
`--opponent v5` (or any registered version). Curriculum logic: pin the
opponent until trainee clears +N Elo, then advance to next opponent in
the registry. If `--opponent` is not specified, fall back to current
SC2-AI behavior (no breaking change).

**Existing context.**
- `.claude/skills/improve-bot-advised/SKILL.md` — Phase 4 dispatch loop
  picks an improvement and runs validation games. The current
  validation invocation uses SC2-AI difficulty.
- `src/orchestrator/selfplay.py::run_batch(p1, p2, games, map_name, …)`
  — Phase 3 primitive that boots two `python -m bots.vN` subprocesses
  per game.
- `src/orchestrator/ladder.py::get_elo(version)` + `LadderEntry`
  contract from Phase 4 — read current Elo to drive curriculum
  advancement.
- `src/orchestrator/registry.py::list_versions()` — enumerate
  registered opponents to walk the curriculum.

**Files to modify/create.**
- `.claude/skills/improve-bot-advised/SKILL.md` — accept `--opponent vN`
  flag in Phase 0 bootstrap; thread through to Phase 4 validation; fall
  through to SC2-AI if absent.
- `src/orchestrator/curriculum.py` (NEW) —
  `pick_opponent(current_elo, opponents, advance_threshold) -> str`.
- `tests/test_curriculum_opponent.py` (NEW) — synthetic Elo trajectory
  walks 3 opponents.

**Done when.**
- `--opponent v5` flag accepted; SKILL.md documents the flag.
- Curriculum advances correctly: trainee at Elo X+10 vs pinned opponent
  → next opponent gets pinned automatically.
- Backwards-compat: omitting `--opponent` runs the existing SC2-AI eval
  unchanged.
- `tests/test_curriculum_opponent.py` covers: pinning, advancement on
  threshold cross, exhaustion of opponent list (graceful stop).
- Dashboard "Loop" tab shows current opponent (read from
  `bots/current/data/advised_run_state.json`).

**Flags (recommended for `/build-step`).** `--reviewers code --isolation worktree`


**Produces.** Updated SKILL.md; new `curriculum.py`; new tests; small
addition to Loop dashboard tab.


<!-- autofix-applied: 2026-11-11 -->


### Step 6.2: PFSP-lite pool sampling (stretch)

- **Status:** HELD — D11 (2026-11-11): under PPO.0 exploration (not parked); PPO.0 keeps, rewrites or retires this step
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** 6.1
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-6-build-plan.md](../archived-plans/phase-6-build-plan.md); original 6.2.

**What to build.** Generalize Step 6.1's "pin one opponent" to
"sample from top-K opponents using PFSP-lite weights." Trainee plays
mixed-style validation games against a pool instead of single
opponent. AlphaStar-lite league at single-box scale.

**Existing context.**
- Phase 3 already shipped the PFSP-lite sampler in
  `src/orchestrator/selfplay.py` (`--sample pfsp --pool v0,v1,v2,v3`).
- Step 6.1's `pick_opponent` returns a single string; Step 6.2
  generalizes to `pick_opponent_pool` returning a sampler.
- Promotion gate must track WR per opponent in pool, not aggregate
  (so a strong-vs-weak pool doesn't artificially inflate a single
  number).

**Files to modify/create.**
- `.claude/skills/improve-bot-advised/SKILL.md` — accept
  `--opponent-pool top-K` flag (mutually exclusive with `--opponent`).
- `src/orchestrator/curriculum.py` — add `pick_opponent_pool(top_k,
  ladder, sampler="pfsp")` returning a callable that draws an
  opponent per game.
- `tests/test_curriculum_opponent.py` — extend with pool-sampling
  cases.

**Done when.**
- `--opponent-pool top-3` flag accepted; sampler draws across top-3
  weighted by PFSP-lite (lower WR vs trainee → higher weight).
- Per-opponent WR tracked in `advised_run_state.json` (not just
  aggregate).
- Promotion gate fires only if trainee beats EACH opponent in the
  pool by margin (configurable; default ≥ +5 Elo per opponent).

**Flags (recommended).** `--reviewers code --isolation worktree`


**Produces.** SKILL.md update; `curriculum.py` extension; test
coverage; possibly minor tweak to Phase 4 promotion gate to handle
pool-mode trainees.


<!-- autofix-applied: 2026-11-11 -->


### Step 6.3: Demonstrate cross-version PPO improvement

- **Status:** HELD — D11 (2026-11-11): under PPO.0 exploration (not parked); PPO.0 keeps, rewrites or retires this step
- **Type:** wait
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** 6.1
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-6-build-plan.md](../archived-plans/phase-6-build-plan.md); original 6.3.

After 6.1 (and 6.2 only if chosen), run the multi-hour first cycle: vN → vN+1 → vN+2 with increasing Elo and at least one higher SC2-AI difficulty than v0. Stop/re-audit curriculum and reward if ladder cycles across three snapshots. This is PPO-driven and independent of discrete evolve; exclusive use of current working tree. Phase7 establishes there is no advised_run_state.json; re-anchor any future telemetry writer, do not pretend the older Phase6 path is implemented.


<!-- autofix-applied: 2026-11-11 -->


### Step 7.6: End-to-end advised staleness/soak validation

- **Status:** HELD — D11 (2026-11-11): under PPO.0 exploration (not parked); PPO.0 keeps, rewrites or retires this step
- **Type:** operator
- **Issue:** #280
- **Depends on:** none outstanding; retain shipped prerequisites
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-7-build-plan.md](../archived-plans/phase-7-build-plan.md); original Phase7 Step6.

- **Problem:** Run the full loop unattended to confirm staleness detection → soak scheduling →
  recovery works end-to-end against the live system. This is operator/wall-clock work (a multi-hour
  soak), not a `/build-step` code change — hence a separate `Type: operator` step (per §22; the
  original draft buried a 1h live soak inside a code step's Done-when).
- **Done when (operator runs + reports):**
  1. With reward rules freshly edited, run `/improve-bot-advised --self-improve-code --hours 8`.
  2. Staleness detected on iteration 1 (reward edit newer than newest checkpoint).
  3. A `soak` improvement is emitted and runs to completion (2–4h, clamped ≤ 50% of budget).
  4. Post-soak deterministic eval shows a measurable Elo/WR delta (sign doesn't matter — proves
     the path works end-to-end).
  5. Loop resumes and finishes the remaining budget without starving other iterations.

**Operator commands.**
```powershell
# (edit a reward rule first so the policy is provably stale, then:)
uv run python -m orchestrator.staleness <current-version>   # sanity: is_stale should be true
# then the full run:
/improve-bot-advised --self-improve-code --hours 8
```

**What to look for.**

| Check | Expected |
|---|---|
| Iteration 1 staleness | `is_stale: true`, reason cites reward-edit-after-checkpoint |
| Soak emitted + clamped | One `type: soak`, duration ≤ min(remaining/2, 4h) |
| Daemon recovery | API-only backend restarts cleanly after the soak; `data/` intact |
| Budget | Soak-type time ≤ 50% of total `--hours` |
| End-to-end | Post-soak eval delta is non-zero; loop completes remaining budget |


<!-- autofix-applied: 2026-11-11 -->


### Step E.0: Resolve action-space cardinality before implementation

- **Status:** HELD — D11 (2026-11-11): under PPO.0 exploration (not parked); PPO.0 keeps, rewrites or retires this step
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original E.0.

The source enumerates ATTACK main/natural/third (3), EXPAND own_natural/own_third/own_fourth (3), and DEFEND/FORTIFY/OPENING/LATE_GAME singleton states (4), while requiring Discrete(12), 12 ActionTarget entries and range 0..11. These total ten legal pairs. Decide ten legal actions versus twelve with explicitly defined extra/reserved slots and masking; then update migration/teacher/checkpoint contracts together. No additional actions are invented by reconciliation. Phase F is cut; target collapse means revisit signal or defer, not reactivate F.


<!-- autofix-applied: 2026-11-11 -->


### Step E.1: Structured `(strategic_state, target)` action space

- **Status:** HELD — D11 (2026-11-11): under PPO.0 exploration (not parked); PPO.0 keeps, rewrites or retires this step
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** E.0
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original E.1.

**What to build.** Replace flat `Discrete(6)` action space with
structured `(strategic_state, target)` pairs. Mapping:
- `ATTACK` → {main, natural, third}
- `EXPAND` → {own_natural, own_third, own_fourth}
- `DEFEND`, `FORTIFY`, `OPENING`, `LATE_GAME` → no target dim (use sentinel)

Effective space = 12 actions. Update `ACTION_TO_STATE` in
`decision_engine.py` to a new `ActionTarget` enum that captures both
dimensions, and update `SC2Env.action_space` to `Discrete(12)`.

**Existing context.**
- `bots/current/decision_engine.py` — `ACTION_TO_STATE: list[StrategicState]`
  is the canonical 6-way mapping. Used by tests, training, and the
  rules policy.
- `bots/current/learning/environment.py` — `SC2Env.action_space =
  Discrete(6)` today. Action index is fed through `ACTION_TO_STATE`.
- `bots/current/learning/rules_policy.py` — `rule_actions_for_batch`
  returns 6-way action indices for the KL teacher.
- `bots/current/data/diagnostic_states.json` — fixed snapshots with
  expected actions (currently 6-way ints).

**Files to modify/create.**
- `bots/current/decision_engine.py` — new `ActionTarget` enum, new
  `ACTION_TO_STATE` (12-way) and helper to decompose into
  `(strategic, target)`.
- `bots/current/learning/environment.py` — `Discrete(12)` action
  space.
- `bots/current/data/diagnostic_states.json` — expand expected actions
  to 12-way (or migrate per Step E.5).

**Done when.**
- `ActionTarget` enum has 12 entries with stable ordinal indices.
- `decision_engine.action_to_target(idx)` returns
  `(StrategicState, TargetEnum | None)`.
- `SC2Env.action_space.n == 12`.
- All existing tests still pass (action-space size assertions updated
  where needed).

**Flags (recommended).** `--reviewers code --isolation worktree`


**Produces.** Updated `decision_engine.py`, `environment.py`,
`diagnostic_states.json`.


<!-- autofix-applied: 2026-11-11 -->


### Step E.2: Custom `ActorCriticPolicy` with two sequential softmax heads

- **Status:** HELD — D11 (2026-11-11): under PPO.0 exploration (not parked); PPO.0 keeps, rewrites or retires this step
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** E.1
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original E.2.

**What to build.** Custom SB3 `ActorCriticPolicy` subclass with two
softmax heads:
- `p(strategic)`: 6-way distribution over strategic states.
- `p(target | strategic)`: conditional 3-way distribution over targets
  (gated by which strategic state was sampled).

Sample order: strategic head first, then target head conditioned on
the sampled strategic. Effective joint distribution covers all 12
actions but with explicit factorization (autoregressive).

**Existing context.**
- SB3 `ActorCriticPolicy` is the parent class; current bot uses
  `MlpPolicy` (default). Custom subclass goes in
  `bots/current/learning/autoreg_policy.py` (NEW).
- Phase A uses `MlpLstmPolicy` for some configs — autoreg subclass
  must work alongside or as alternative; configurable via
  `hyperparams.json::policy_type: "autoreg"`.

**Files to modify/create.**
- `bots/current/learning/autoreg_policy.py` (NEW) — custom policy
  subclass with two heads.
- `bots/current/learning/trainer.py` — pick `autoreg_policy` when
  `hyperparams.policy_type == "autoreg"`.
- `bots/current/data/hyperparams.json` — add example
  `policy_type: "autoreg"` (off by default).

**Done when.**
- `autoreg_policy.AutoregPolicy` instantiates without error.
- Forward pass returns valid joint distribution over 12 actions.
- Gradients flow to both heads (verified in unit test).
- Target head correctly gated by strategic head choice (target probs
  are zero for invalid combos).

**Flags (recommended).** `--reviewers code --isolation worktree`


**Produces.** New policy module; trainer hook; hyperparam example.


<!-- autofix-applied: 2026-11-11 -->


### Step E.3: DB column `action_space_version`

- **Status:** HELD — D11 (2026-11-11): under PPO.0 exploration (not parked); PPO.0 keeps, rewrites or retires this step
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** E.1
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original E.3.

**What to build.** Add `action_space_version INT` column to
`bots/current/data/training.db` schema (transitions table). Values:
`1` = legacy 6-way, `2` = Phase E 12-way. Migration script that flags
all existing rows as version 1.

**Existing context.**
- `bots/current/learning/database.py` — `TrainingDB` class, `_STATE_COLS`,
  `_LATER_ADDED_COLS` for schema migration. Phase B already
  established this pattern.

**Files to modify/create.**
- `bots/current/learning/database.py` — add column + migration; add
  helper `get_action_space_version(transition_id)`.
- `tests/test_database_action_space_version.py` (NEW) — verify
  migration adds default `1`, new inserts can use `2`.

**Done when.**
- Existing transitions table gains `action_space_version` defaulting
  to `1`.
- New inserts respect provided version.
- `tests/test_database_action_space_version.py` covers fresh DB,
  migrated DB, and version filtering on read.

**Flags (recommended).** `--reviewers code --isolation worktree`


**Produces.** Updated schema + migration; new test file.


<!-- autofix-applied: 2026-11-11 -->


### Step E.4: Cascade target picking into `_compute_next_state` + rules KL teacher

- **Status:** HELD — D11 (2026-11-11): under PPO.0 exploration (not parked); PPO.0 keeps, rewrites or retires this step
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** E.1, E.2
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original E.4.

**What to build.** Update the rule engine to pick BOTH a strategic
state AND a target (matching the new 12-way action space). Cascade
into the KL teacher so `rule_actions_for_batch` returns 12-way.

**Existing context.**
- `bots/current/decision_engine.py::_compute_next_state` — currently
  returns `StrategicState`. Phase E needs it to also return `(state,
  target)`.
- `bots/current/learning/rules_policy.py::rule_actions_for_batch` —
  consumes `_compute_next_state`'s output, returns action indices for
  the KL teacher loss.
- Target picking heuristics: ATTACK → enemy main if scouted, else
  natural; EXPAND → own_natural if not built, else own_third, etc.

**Files to modify/create.**
- `bots/current/decision_engine.py` — extend `_compute_next_state` to
  return `ActionTarget`; add target-picking heuristics.
- `bots/current/learning/rules_policy.py` — return 12-way action
  indices.
- `tests/test_decision_engine_targets.py` (NEW) — verify
  target-picking heuristics for each strategic state under various
  game snapshots.

**Done when.**
- `_compute_next_state(snap)` returns `ActionTarget` enum value.
- `rule_actions_for_batch(batch)` returns ints in `[0, 11]`.
- Target heuristics covered by tests (own_natural picked when
  available, else own_third, etc.).

**Flags (recommended).** `--reviewers code --isolation worktree`


**Produces.** Updated decision engine + rules policy; new test file.


<!-- autofix-applied: 2026-11-11 -->


### Step E.5: 6-way → 12-way DB migration with target inference

- **Status:** HELD — D11 (2026-11-11): under PPO.0 exploration (not parked); PPO.0 keeps, rewrites or retires this step
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** E.3, E.4
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original E.5.

**What to build.** Migrate existing 6-way DB rows to 12-way: for each
old transition, infer target from game log when possible, else
default to `*_main`. Reject loading old checkpoints that were trained
on 6-way unless `--force` is passed (mismatched action-space loads
would silently produce garbage).

**Existing context.**
- `bots/current/data/training.db` has thousands of legacy
  transitions. Game logs in `logs/` may have target info via the
  ATTACK target position; otherwise default.
- `bots/current/learning/checkpoints.py` loads SB3 checkpoints; needs
  action-space compatibility check.

**Files to modify/create.**
- `bots/current/learning/database.py` — `migrate_action_space(db,
  log_dir)` function.
- `bots/current/learning/checkpoints.py` — reject mismatched-action-space
  loads unless `--force`.
- `scripts/migrate_phase_e.py` (NEW) — operator-run script that
  invokes the migration.
- `tests/test_action_migration.py` (NEW) — synthetic 6-way DB
  migrates cleanly; old checkpoints rejected explicitly.

**Done when.**
- `scripts/migrate_phase_e.py --db bots/current/data/training.db
  --logs logs/` produces a migrated DB with all rows at version 2.
- Inferred targets match game-log evidence where available; default
  `*_main` elsewhere.
- Loading a 6-way checkpoint into a 12-way trainer raises clear
  error unless `--force`.

**Flags (recommended).** `--reviewers code --isolation worktree`

exists).

**Produces.** Migration function; checkpoint compat check;
operator script; test file.


<!-- autofix-applied: 2026-11-11 -->


### Step E.6: Snapshot to `vN+1` on promotion

- **Status:** HELD — D11 (2026-11-11): under PPO.0 exploration (not parked); PPO.0 keeps, rewrites or retires this step
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** E.5
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original E.6.

**What to build.** Operator step: train 2-3 cycles with the autoreg
policy on the migrated DB, run deterministic eval at difficulty 3,
run cross-version Elo self-play vs prior `vN`. If gates pass, snapshot
`bots/current/` → `bots/vN+1/`.

**Existing context.**
- `scripts/snapshot_bot.py` from Phase 2 handles the snapshot
  mechanics.
- Phase 4 promotion gate (`check_promotion`) handles the Elo + WR
  validation.

**Files to modify/create.**
- None (operator step). Validation per "Done when".

**Done when.**
- Replay inspection shows meaningful target differentiation (not
  always `enemy_main`); document a sample.
- WR holds at difficulty 3 (≥ baseline).
- Elo gain ≥ +10 over 20 self-play games vs prior `vN`.
- `bots/vN+1/` snapshot created via `snapshot_bot.py`.

**Flags.** N/A (operator).


**Produces.** New `bots/vN+1/` directory; promotion log entry.


<!-- autofix-applied: 2026-11-11 -->


### Step K.1: Pool metadata schema + file

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** #223
- **Files:** `src/orchestrator/pool_metadata.py`
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [phase-k-build-plan.md](../archived-plans/phase-k-build-plan.md); original K.1.

- **Problem:** Define `data/pool_metadata.json` schema (§5). Write the
  schema as Python TypedDict in `src/orchestrator/pool_metadata.py`.
  Add load/save helpers with file-locking (concurrent evolve runs
  could write simultaneously).
- **Flags:** `--reviewers deep --isolation worktree`
- **Produces:** `src/orchestrator/pool_metadata.py`,
  `tests/test_pool_metadata.py`, `data/pool_metadata.json` (empty
  initial).
- **Done when:** Round-trip load/save tests pass; file-locking test
  passes (two writers serialize correctly); mypy strict + ruff
  clean.


<!-- autofix-applied: 2026-11-11 -->


### Step K.2: Manifest extension

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** #224
- **Files:** `src/orchestrator/registry.py`
- **Depends on:** K.1
- **Source:** [phase-k-build-plan.md](../archived-plans/phase-k-build-plan.md); original K.2.

- **Problem:** Add `themes` and `notable_moments` to manifest schema.
  `snapshot_current()` writes `themes: []` and `notable_moments: []`
  by default. Add `set_themes()` and `add_notable_moment()` helpers
  in `registry.py`.
- **Flags:** `--reviewers deep --isolation worktree`
- **Produces:** Updates to `src/orchestrator/registry.py`,
  `src/orchestrator/snapshot.py`, `tests/test_registry.py`,
  `tests/test_snapshot.py`.
- **Done when:** Snapshotting `v0 → v1` produces a manifest with the
  new fields; reading an OLD manifest (no `themes` field) doesn't
  crash (default `[]`).



<!-- autofix-applied: 2026-11-11 -->


### Step K.3: Build-order signature

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** #225
- **Files:** `src/orchestrator/build_order_signature.py`
- **Depends on:** K.2
- **Source:** [phase-k-build-plan.md](../archived-plans/phase-k-build-plan.md); original K.3.

- **Problem:** `extract_build_order_signature(game_id) ->
  list[tuple[str, int]]` — returns the first 5 minutes of unit/structure
  production as `[(unit_name, count), ...]` ranked by frequency. Read
  from `transitions` or `decision_audit.json`. Auto-theme function:
  `infer_themes(signature) -> list[str]` with 3-5 hardcoded rules
  (skytoss, ground, cheese-rush, defensive, tech-rush).
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** `src/orchestrator/build_order_signature.py`,
  `src/orchestrator/auto_theme.py`,
  `tests/test_build_order_signature.py`,
  `tests/test_auto_theme.py`.
- **Done when:** Signature extraction tests pass on a fixture game
  ID; auto-theme produces sensible labels on 5 hand-crafted
  signatures.



<!-- autofix-applied: 2026-11-11 -->


### Step K.4: API endpoint + dashboard surface

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** #226
- **Files:** `frontend/src/hooks/usePoolMetadata.ts`
- **Depends on:** K.3
- **Source:** [phase-k-build-plan.md](../archived-plans/phase-k-build-plan.md); original K.4.

- **Problem:** `GET /api/pool/metadata` returns merged
  registry+ladder+metadata view. Decide tab-vs-extend during this
  step (read LadderTab; if extension is clean, extend; otherwise
  new Pool tab as 11th tab). Frontend hook `usePoolMetadata.ts` with
  `cacheKey: 'pool-v1'`.
- **Flags:** `--reviewers deep --isolation worktree`
- **Produces:** Backend endpoint, `frontend/src/hooks/usePoolMetadata.ts`,
  dashboard surface (tab or extension).
- **Done when:** Endpoint returns valid JSON for `v0`-only pool;
  frontend renders without console errors;
  `npm --prefix frontend run test` passes; `npm --prefix frontend
  run build` passes.



### Step K.5: Operator smoke gate

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** operator
- **Issue:** #227
- **Depends on:** K.4
- **Source:** [phase-k-build-plan.md](../archived-plans/phase-k-build-plan.md); original K.5.

- **Problem:** Run `python -m orchestrator list`, verify themes +
  notable_moments fields surface; manually tag `v0` with
  `["baseline"]` theme via `set_themes("v0",
  ["baseline"])`; verify dashboard updates; verify NL-prompt-shaped
  query (`themes contains "baseline"`) returns `v0`. No real
  matchups yet — that's Phase L.
- **Produces:** Manual verification.
- **Done when:** All smoke checks green.



<!-- autofix-applied: 2026-11-11 -->


### Step G.1: Extract race interfaces

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** 6.3
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-g-build-plan.md](../archived-plans/phase-g-build-plan.md); original G.1.

RaceConfig, ProductionAdapter, MicroAdapter, FeatureSpec and RewardTemplate; no Protoss behavior regression. Reconcile original v0-refactor proposal with frozen-version rule: build on a new working version, never rewrite frozen history. Kill/defer if >5% tests break or >500 adapter lines needed.


<!-- autofix-applied: 2026-11-11 -->


### Step G.2: Implement and validate Zerg

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** G.1
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-g-build-plan.md](../archived-plans/phase-g-build-plan.md); original G.2.

Larva/inject, creep, Overlords, morph roster, Bile/Fungal/Burrow/Abduct, hatch/pool/12-pool builds, rewards/features. Add evolve --race and per-race parent chain, same-race mirrors. Train and demonstrate >=50% at difficulty3 over20 games.


<!-- autofix-applied: 2026-11-11 -->


### Step G.3: Implement and validate Terran

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** G.2
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-g-build-plan.md](../archived-plans/phase-g-build-plan.md); original G.3.

SCVs/MULEs, depot walls, addons, siege/stim/medivacs, snipe/EMP/nuke, 1-1-1/2-1-1/mech/bio, rewards/features. Train and demonstrate >=50% at difficulty3 over20 games.


<!-- autofix-applied: 2026-11-11 -->


### Step G.4: Enable cross-race informational ladder

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** G.3
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-g-build-plan.md](../archived-plans/phase-g-build-plan.md); original G.4.

Within-race promotion; separate cross-race informational Elo with stable rankings. Optional cross-race safety gate remains a future decision, not silently required.


<!-- autofix-applied: 2026-11-11 -->


### Step H.0: Conclude mini-game role investigation

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-h-build-plan.md](../archived-plans/phase-h-build-plan.md); original H.0.

Resolve scorecard versus gate/reward and Windows PySC2 feasibility before activating H/I/J. Defer all if scorecard-only value cannot justify substrate cost.


<!-- autofix-applied: 2026-11-11 -->


### Step H.1: Pin PySC2 and verify imports

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.0
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-h-build-plan.md](../archived-plans/phase-h-build-plan.md); original H.1.

Pinned dependency; import-clean with version stack.


<!-- autofix-applied: 2026-11-11 -->


### Step H.2: Define MinigameResult and storage

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.1
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-h-build-plan.md](../archived-plans/phase-h-build-plan.md); original H.2.

Finite score/result contracts, per-version training.db minigame_results migration and round-trip compatibility.


<!-- autofix-applied: 2026-11-11 -->


### Step H.3: Implement minigame runner

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.2
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-h-build-plan.md](../archived-plans/phase-h-build-plan.md); original H.3.

Run PySC2 path for selected version/map, structured score, bounded parallel-safe SC2 lifetime.


<!-- autofix-applied: 2026-11-11 -->


### Step H.4: Expose minigame CLI

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.3
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-h-build-plan.md](../archived-plans/phase-h-build-plan.md); original H.4.

scripts/run_minigame.py --version v0 --map MoveToBeacon; argument/errors/output tests.


<!-- autofix-applied: 2026-11-11 -->


### Step H.5: Observe mini-game end-to-end smoke

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.4
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-h-build-plan.md](../archived-plans/phase-h-build-plan.md); original H.5.

MoveToBeacon finite numeric score and stored row, compare published baseline at implementation time; no invented minimum guarantee.


<!-- autofix-applied: 2026-11-11 -->


### Step I.1: Build custom Protoss mini-game corpus

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.5
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original I.1.

Two to five focused maps from the investigation priority list: blink-stalker micro, attack-concave, force-field choke, storm-clump and phoenix-vs-mutas, scoped by H investigation; score reliability before further maps. Preserve per-race subdirectories for G extensions; kill if custom-map cost is unjustified versus PySC2 alone.


<!-- autofix-applied: 2026-11-11 -->


### Step J.1: Wire approved mini-game role

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.5, H.0
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original J.1.

Investigation selects gate/reward/scorecard; wire only the chosen role(s), with cost and empirical validation: scorecard into per-version manifest, gate into fitness/pre-gate, reward into curriculum pretraining. If none is warranted, collapse into H.


<!-- autofix-applied: 2026-11-11 -->


### Step L.0: Prove replay-tail observer spike

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original L.0.

Spike C: third SC2 process loads an actively written replay, not a third API PlayerBot. Keep normal fog/full observation limitations explicit; >2 days failure kills viewer path.


<!-- autofix-applied: 2026-11-11 -->


### Step L.1: Implement replay exhibition pipeline

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** L.0
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original L.1.

After spike success: replay tailing, game-state bridge/viewer, finite callback/process lifetime, own-process cleanup and recorded fixture. Preserve shipped EV two-pane mode; do not refactor it blindly to a single pane.


<!-- autofix-applied: 2026-11-11 -->


### Step L.2: Observe exhibition fidelity

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** L.1
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original L.2.

Live visual matchup, reliable follow/termination and bounded latency; compare recorded fixture, no gameplay authority for observer.


<!-- autofix-applied: 2026-11-11 -->


### Step M.1: Build NL matchup selector

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** K.5, L.2
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original M.1.

Use K metadata and L exhibitions, parse NL prompts into tagged pool matchups with explicit fallback. Validate three sample prompts; kill/defer if fallback >50%.


<!-- autofix-applied: 2026-11-11 -->


### Step O.0: Prove four-way evolve soak reliability

- **Status:** PARKED — D12 (2026-11-11): revisit once the Phase ES scoreboard shows the single evolution line improving
- **Type:** wait
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original O.0.

Parallelization is code-complete but the scripted Hydra prerequisite is actual four-way soak reliability; record complete evidence before multi-expert architecture.


<!-- autofix-applied: 2026-11-11 -->


### Step O.1: Train themed Hydra experts

- **Status:** PARKED — D12 (2026-11-11): revisit once the Phase ES scoreboard shows the single evolution line improving
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** O.0
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original O.1.

Separate themed experts (Skytoss, Ground, Cheese-rush) with appropriate training distributions, manifests and reproducible checkpoints. Learned Hydra remains cut.


<!-- autofix-applied: 2026-11-11 -->


### Step O.2: Implement scripted expert switching

- **Status:** PARKED — D12 (2026-11-11): revisit once the Phase ES scoreboard shows the single evolution line improving
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** O.1
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original O.2.

Threshold/hysteresis controller, inference integration and dashboard expert trace; preserve same observation/action interfaces.


<!-- autofix-applied: 2026-11-11 -->


### Step O.3: Validate expert use and Elo

- **Status:** PARKED — D12 (2026-11-11): revisit once the Phase ES scoreboard shows the single evolution line improving
- **Type:** wait
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** O.2
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original O.3.

20 self-play games, >=+10 Elo, no expert collapse: each active >=10% steps in >=50% games. Observe comparable replay/use; defer if switching is effectively one expert.


<!-- autofix-applied: 2026-11-11 -->


### Step P.1: Investigate licensed replay pretraining

- **Status:** PARKED — D12 (2026-11-11): revisit once the Phase ES scoreboard shows the single evolution line improving
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original P.1.

Corpus/license/feasibility investigation before distillation or foundational pretraining. No unlicensed corpus assumption; kill if unavailable or no value at current scale.


<!-- autofix-applied: 2026-11-11 -->


### Step Q.0: Conclude harvest skill scope investigation

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding; retain shipped prerequisites
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-q-build-plan.md](../archived-plans/phase-q-build-plan.md); original Q.0.

Compare paper intake versus existing advised workflow; docs-only default, auto-commit only explicit later decision. Collapse to advised extension if indistinguishable.


<!-- autofix-applied: 2026-11-11 -->


### Step Q.1: Draft Mode A paper intake

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** Q.0
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-q-build-plan.md](../archived-plans/phase-q-build-plan.md); original Q.1.

Paper paste/URL/localPDF -> scoped investigation with context, questions, method, findings, criteria, constraints, downstream decisions and references; docs-only, no code commit.


<!-- autofix-applied: 2026-11-11 -->


### Step Q.2: Wire conditional harvest sandbox

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** Q.0, Q.1
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-q-build-plan.md](../archived-plans/phase-q-build-plan.md); original Q.2.

Only if scope investigation explicitly selects auto-commit. Otherwise retain as not-applicable conditional, not work silently authorized.


<!-- autofix-applied: 2026-11-11 -->


### Step Q.3: Review three retroactive harvest cases

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** Q.1
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-q-build-plan.md](../archived-plans/phase-q-build-plan.md); original Q.3.

Compare outputs to three prior investigations using source evidence.


<!-- autofix-applied: 2026-11-11 -->


### Step Q.4: Observe one fresh paper intake

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** Q.1
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-q-build-plan.md](../archived-plans/phase-q-build-plan.md); original Q.4.

Fresh input yields useful investigation passing review; no cron or auto-PR scope.


<!-- autofix-applied: 2026-11-11 -->


### Step Q.5: Consider Mode B suggestion wrapper

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** Q.4
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-q-build-plan.md](../archived-plans/phase-q-build-plan.md); original Q.5.

Optional extension only after ModeA live acceptance; keep v1.1 if not a thin natural wrapper.


<!-- autofix-applied: 2026-11-11 -->


### Step S.1: Investigate build-order entropy metric

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** ES.3
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original S.1.

Deferred build-order entropy measurement; define a diagnostic metric and scoped validation before any policy/reward change.


<!-- autofix-applied: 2026-11-11 -->


### Step S.2: Investigate time-to-win signal

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** ES.3
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original S.2.

Deferred signal study; separate wins from speed effects, avoid rewarding invalid/short matches.


<!-- autofix-applied: 2026-11-11 -->


### Step S.4: Investigate informative priors

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** ES.3
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original S.4.

Deferred prior calibration; retain current Beta(1,1) until evidence justifies change.


<!-- autofix-applied: 2026-11-11 -->


### Step ER.5: Enrich fingerprints after v1 evidence

- **Status:** PARKED — D9 decided 2026-11-16 (T5): parked until Stage 1 passes
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EL.7
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original ER.5.

Build-order/army-composition refinement remains deferred until baseline-result fingerprint proves useful.


<!-- autofix-applied: 2026-11-11 -->


### Step ER.6: Revisit species knobs after scored mini-games

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** H.5, J.1
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original ER.6.

Task ordering/time-in-task are mini-game species concepts, not proxy knobs to invent now.


<!-- autofix-applied: 2026-11-11 -->


### Step ER.7: Consider true concurrent lineage execution

- **Status:** PARKED — D9 decided 2026-11-16 (T5): parked until Stage 1 passes
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EL.7, EH.7, EH.8
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original ER.7.

Deferred separate from intra-generation worker concurrency; preserve global pointer/commit ownership and SC2 two-client constraints.


<!-- autofix-applied: 2026-11-11 -->


### Step ER.8: Repair PFSP seat alternation when activated

- **Status:** HELD — D11 (2026-11-11): under PPO.0 exploration (not parked); PPO.0 keeps, rewrites or retires this step
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** 6.2
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original ER.8.

game_index always0 in one-game PFSP batches means seat_swap alwaysFalse. Retain as latent correctness follow-up with actual caller test.


<!-- autofix-applied: 2026-11-11 -->


### Step ER.9: Repair result-out collision when consumer exists

- **Status:** PARKED — D13 (2026-11-11): diversification is a later strength path; parked, not dropped
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** EI.1
- **Done when:** <TBD — operator fills in>
- **Source:** [phase-e-build-plan.md](../archived-plans/phase-e-build-plan.md); original ER.9.

Concurrent workers collide on logs/selfplay_tmp/game{i}_p*.json, but result-out has no reader at source audit. Conditional follow-up when real consumer is introduced; do not invent usage.


<!-- autofix-applied: 2026-11-11 -->


### Step BL.1: Decide and execute branch landing when authorized

- **Status:** PENDING — D2 (2026-11-11)
- **Decision (2026-11-11):** D2: land the working tree before any `EVO_AUTO` run (ES-A). The EV.4 dependency was removed with the viewer reframe (D10).
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** none outstanding
- **Done when:** <TBD — operator fills in>
- **Source:** [master_plan.md](../archived-plans/master_plan.md); original BL.1.

onbrand-pilot already contained; no mechanical blocker requiring its separate merge. Decide landing with EV acceptance open versus after EV.4. Recheck branches/CI, prefer PR for Linux/Docker signal; merge commit preserves step history, tag actual merged commit, retain phase branch until gates close. No publication authorized by this cleanup.


<!-- autofix-applied: 2026-11-11 -->


### Step T.1: Soften max-supply ATTACK override (PARTIAL)

- **Status:** PENDING
- **Decision (2026-11-11):** D5: ES.11 records whether this still reproduces on the champion. Fix it if it reproduces; retire it with the evidence otherwise.
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** ES.11
- **Done when:** <TBD — operator fills in>
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.1.

`MAX_SUPPLY_ATTACK_THRESHOLD=180` forces ATTACK regardless of
DEFEND/FORTIFY/EXPAND/OPENING state. **Deliberately heavy-handed for now —
get it working, tune later.**

**Problem with the current fix:** At higher difficulties (4-5), a legitimate
defensive stand (e.g., enemy doom-drop into main while own army is across
the map at 180+ supply) would be incorrectly preempted into ATTACK, losing
the defender's advantage. The 180-supply check fires even when defending
is the correct play.

**Softer solution candidates (pick one when tuning):**
- Require `army_supply >= enemy_army_supply_visible * k` before ATTACK
  fires, so max-supply doesn't override when outgunned in the engagement
  area.
- Add a cooldown: override fires only if `supply_used >= 180` has held
  for N seconds, preventing flip-flop with a visible enemy raid.
- Scale the threshold by difficulty (180 at diff 3, 190 at diff 4, 195
  at diff 5).
- Replace with a "production saturation" signal: override when no new
  units can be produced (all warp-gates on cooldown + all production
  full) AND supply at cap — captures the actual "waste" condition
  without false-firing.

**When to tune:** After re-eval at diff 3 confirms win-rate hold, before
pushing to diff 4-5. The hard fix is safe for diff 1-3 where opponents
rarely doom-drop.



Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


<!-- autofix-applied: 2026-11-11 -->


### Step T.3: Tech structures placed on low ground

- **Status:** PENDING
- **Decision (2026-11-11):** D5: ES.11 records whether this still reproduces on the champion. Fix it if it reproduces; retire it with the evidence otherwise.
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** ES.11
- **Done when:** <TBD — operator fills in>
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.3.


**Observed (2026-04-17):** Twilight Council and Forge placed on low
ground outside the main base ramp, with no expansion or defensive
structures nearby. Creates free-kill targets for enemy raids — losing a
Twilight Council resets the tech path and is game-ending at higher
difficulties.

**Rule of thumb the bot should follow:**
- **Pylons** — low ground is fine (cheap, power grid coverage, vision).
- **Tech structures** (Twilight, Robotics Bay, Cybernetics Core, Forge,
  Templar Archives, Fleet Beacon) — place inside the main base perimeter
  OR adjacent to an established expansion with cannons. Never alone on
  low ground.
- **Gateways / Robotics / Stargates** — generally inside main,
  occasionally at natural for warp-in proxy, but only when defended.

**Candidate fixes:**
- Add a `structure_placement_priority` map: each building type gets a
  preferred placement zone (main / natural / proxy / any-powered).
  Default placement logic respects the zone.
- Placement query: before committing to a low-ground pylon-powered spot
  for a tech structure, check if any townhall is within N tiles. Reject
  if not.
- Look at `build_manager.py` / wherever `build_structure` or
  pylon-adjacent placement search lives.

**Related observation:** Repeated "red placement location" error
messages in the log suggest the placement search is retrying invalid
spots. May be a symptom of the same bug (trying to place tech on an
already-taken low-ground spot) or a separate retry-storm issue worth
grepping for.

**Re-observed (2026-04-18, 6:23 game time):** Screenshot shows entire
production complex (multiple Gateways + CyberCore + Robo) built on the
low ground *below* the ramp. User note: "the main buildings you want to
protect on the high ground or next to the natural." This confirms T.3 is
still live post T.2/T.5/T.6/T.7 fixes. Three stacked "Can't find
placement location" errors visible → retry-storm likely firing again.



Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


<!-- autofix-applied: 2026-11-11 -->


### Step T.4: Target priority: engage producers, not their spawns

- **Status:** PENDING
- **Decision (2026-11-11):** D5: ES.11 records whether this still reproduces on the champion. Fix it if it reproduces; retire it with the evidence otherwise.
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** ES.11
- **Done when:** <TBD — operator fills in>
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.4.


**Observed (2026-04-17):** Against Broodlord composition, bot units
engage the ground-spawned Broodlings instead of the flying Broodlords
that produce them. Broodlings respawn continuously as long as the
Broodlord lives; killing the Broodlord collapses the entire threat.
Wasting DPS on Broodlings is a positive-feedback loss (more time spent
on Broodlings = more Broodlings spawn).

**General principle:** For "producer + spawn" enemy unit pairs, target
the producer. Cases:
- Broodlord (air) → produces Broodlings (ground). Kill Broodlord.
- Carrier → produces Interceptors. Kill Carrier.
- Swarm Host (burrowed) → spawns Locusts. Kill Swarm Host.
- Warp Prism (air) + Zealots (warped in) → kill Warp Prism first.

**Candidate fixes:**
- Target priority table in `micro_controller.py` or `tactics/`:
  `{UnitTypeId.BROODLORD: priority_high, UnitTypeId.BROODLING: priority_low}`,
  `{UnitTypeId.CARRIER: priority_high, UnitTypeId.INTERCEPTOR: priority_low}`.
- When selecting attack targets, filter the enemy list by
  `priority_high` first; fall back to closest-enemy only if no
  high-priority targets are in range.
- Unit capability constraint: ground-only units (Zealot, Stalker without
  blink, Immortal) can still hit Broodlord since it's low-ground air —
  but ensure anti-air units (Stalker, Phoenix, Void Ray) prioritize
  flying producers.



Revalidate current code and recorded evidence before implementation: this is a historical unresolved backlog, not a new claim every symptom persists. Capability correction: ground-only Zealots/Immortals cannot attack flying producers; use actual anti-air capability, never the source assertion about low-ground air.


<!-- autofix-applied: 2026-11-11 -->


### Step T.7: Units in melee don't deal damage proportional to count

- **Status:** PENDING
- **Decision (2026-11-11):** D5: ES.11 records whether this still reproduces on the champion. Fix it if it reproduces; retire it with the evidence otherwise.
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** ES.11
- **Done when:** <TBD — operator fills in>
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.7.


**Observed (2026-04-17, game time 13:16):** Large Protoss army
intermixed with Zerg in full melee. Units visibly engaged but the
engagement drags — bot loses what should be winning fights. User
described it as "move, attack move, don't really attack."

**Candidate root causes (need log evidence to diagnose):**
1. **Target priority (duplicate of T.4):** units engage nearest trash
   (Roaches, Zerglings, Banelings) instead of
   Broodlords/Ravagers/priority targets.
2. **Command churn:** `_run_micro` runs every tick, issues attack-move
   every tick, interrupting the unit's current attack animation before
   damage lands. Check: does DispatchGuard (shipped 2026-04-14) apply to
   unit commands or only build commands?
3. **Target switching:** units chase closer enemies mid-attack, never
   finishing a kill.

**Next step:** grab log snippet around game time 13:16 from an eval run
to see which code path is firing. Likely needs T.4 fix + possibly
command-rate-limit on attack commands.



Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


<!-- autofix-applied: 2026-11-11 -->


### Step T.8: Production structures placed inside the mineral line

- **Status:** PENDING
- **Decision (2026-11-11):** D5: ES.11 records whether this still reproduces on the champion. Fix it if it reproduces; retire it with the evidence otherwise.
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** ES.11
- **Done when:** <TBD — operator fills in>
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.8.


**Observed (2026-04-18, ~6:10 game time):** Screenshot shows a Robotics
Facility, Stargate, Forge, and Gateway warp-gated directly on top of
mineral patches. Probes are trying to path through the buildings to
mine. A "Can't find placement location" error is visible. Nexus is
hidden behind the production complex, and the main Robotics Facility
(selected) is sitting where Probe pathing expects crystals.

**Impact:** worker mining throttled, pylon/production placement futures
constrained, cannons/batteries have no room when they're actually
needed, worker rally confused.

**Rule:** only defensive structures belong in the mineral line —
**PhotonCannon** and **ShieldBattery**. Everything else (Gateway,
CyberneticsCore, Forge, Robotics Facility, Stargate, Nexus, Pylon except
worker-rally Pylons) must be placed in the back of the main or at
natural choke points / behind the Nexus.

**Candidate root causes:**
1. `bot._build_structure()` falls back to "any powered Pylon radius"
   when primary placement fails — includes the Nexus-adjacent Pylon
   that covers the mineral line.
2. No keep-out zone around mineral patches / vespene geysers for
   non-defensive structures.

**Next step:** find the placement routine, add a filter that excludes
build positions within N tiles of any `self.mineral_field` or
`self.vespene_geyser` for structures ∉ {PhotonCannon, ShieldBattery,
Pylon-for-expansion}. Probably in `bot.py._build_structure` or
`macro_manager` → `MacroDecision` execution path.



Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


<!-- autofix-applied: 2026-11-11 -->


### Step T.9: Natural expansion undefended; fall-back point softer than main

- **Status:** PENDING
- **Decision (2026-11-11):** D5: ES.11 records whether this still reproduces on the champion. Fix it if it reproduces; retire it with the evidence otherwise.
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** ES.11
- **Done when:** <TBD — operator fills in>
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.9.


**Observed (2026-04-18, ~5:48 game time):** Screenshot shows main with a
cluster of production structures (acceptable fortification given the
stacked gateways/robo/stargate near the Nexus), while the natural
(bottom-right Nexus) has only probes mining. No cannons, no shield
batteries, no units parked. If the bot is pushed off the main or needs
a pull-back, the natural is the logical fall-back — but it's softer
than the main.

**Impact:** a timing attack that forces a retreat has nowhere safe to
retreat to. User called out that the natural is "usually a better
fall-back point than the main" — Protoss tradition is to fortify the
natural because the main ramp funnels but the natural is where the army
lives.

**Rule:** once the natural Nexus is up, queue **at least one**
ShieldBattery next to it, and add a PhotonCannon covering the mineral
line / rally path when Forge is ready. Don't touch the main's existing
fortification logic — per the user, it's "sort of fine for now." Scope
is purely: seed a minimum defense at base #2.

**Candidate touch points:**
1. `fortification.py` — if it runs per-base, extend to include natural
   with a lower target count than main.
2. `macro_manager._check_shield_batteries` — if it only considers main,
   widen to include any Nexus owned ≥ 45 seconds.

**Next step:** read `fortification.py` +
`macro_manager._check_shield_batteries` to see whether the current
logic is base-indexed or hard-coded to main. Simplest fix: add a
"natural defense" check that fires once the 2nd Nexus has been standing
for ~45s and no ShieldBattery is within 10 tiles of it.



Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


<!-- autofix-applied: 2026-11-11 -->


### Step T.10: Army idles inside enemy base instead of finishing the game

- **Status:** PENDING
- **Decision (2026-11-11):** D5: ES.11 records whether this still reproduces on the champion. Fix it if it reproduces; retire it with the evidence otherwise.
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** ES.11
- **Done when:** <TBD — operator fills in>
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.10.


**Observed (2026-04-18, 12:42 game time):** Screenshot shows a full
Protoss army (Stalkers + Zealots + at least 2 Archons visible) standing
dispersed among enemy Zerg structures in the Zerg main base. Supply
85/86, 4270 minerals / 478 gas floating. Three "Can't find placement
location" errors stacked on the left — bot is still trying to place
buildings (probably a Pylon to break supply cap) instead of ordering
the army to clean up.

User note: "It could win in a few seconds if it would attack walk
around."

**Relationship to T.5:** T.5 (`e0ae944`) made `_run_micro` always
attack-move in combat states. This screenshot suggests the problem is
not "move vs attack" — the units are simply **not being issued any
order this tick**. They are inside attack range of buildings/units but
stand idle. Either:
1. `_run_micro` is not running (strategic_state not in
   ATTACK/DEFEND/FORTIFY/LATE_GAME despite visually being in combat),
   or
2. `_run_micro` runs but targets are filtered out (e.g., T.4-style
   priority picks an out-of-range target), or
3. DispatchGuard is rate-limiting attack re-issues from T.7 too
   aggressively and no new command arrives when the current target
   dies — the unit goes idle.

**Test path:** pull the game log around 12:42 and grep for
`Hybrid override:`, `strategic_state`, and the unit-command dispatch
lines. Compare to a winning game's log at the same phase.

**Rule:** if units are in vision of enemy structures AND have not
received a command in the last N ticks AND are idle, issue attack-move
on the nearest enemy structure — regardless of strategic_state.

**Next step:** identify which of the 3 root causes above fires, fix
accordingly. Candidate is (3) — DispatchGuard window may be too wide
when the held target has just died.



Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


<!-- autofix-applied: 2026-11-11 -->


### Step T.11: Split army: engaged force + still-rallying force in parallel

- **Status:** PENDING
- **Decision (2026-11-11):** D5: ES.11 records whether this still reproduces on the champion. Fix it if it reproduces; retire it with the evidence otherwise.
- **Type:** code
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** ES.11
- **Done when:** <TBD — operator fills in>
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.11.


**Observed (2026-04-18, 7:52 game time):** Screenshot shows a Protoss
force (Stalkers + Sentries) engaging Zerg Roaches at the top-right,
while a second group (Zealots) is standing on a rally point
lower-middle, not moving. Game shows 14 in F2 (probably the engaged
squad). Two forces fight at partial strength against the same enemy, so
trades are worse than they need to be.

**Rule (guiding principles §12 coherence, §10 engagement):** when any
owned army unit is in combat, subsequent unit production from Gateway /
WarpGate / Robo must **join the engagement**, not continue to a stale
rally point. Rally targets should be invalidated by a combat event.

**Candidate touch points:**
1. `army_coherence.py` — probably already computes a center-of-mass;
   see whether rally points derive from it.
2. `commands/executor.py` — when a new unit warps in during combat, is
   it issued an attack-move toward the fight, or just inherits a stale
   rally?
3. `bot.py._produce_army` after `wg.warp_in(...)` — maybe issue an
   immediate `newest_unit.attack(engagement_target)` before returning.

**Next step:** read `army_coherence.py` and the warp-in path; determine
whether stragglers get an explicit "move to fight" order or rely on the
WarpGate rally. If the latter, add a post-warp hook that routes new
units to the nearest ally-in-combat.


Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


<!-- autofix-applied: 2026-11-11 -->


### Step T.12: Re-validate Archon morph fix in hybrid mode (DEBUG ONLY)

- **Status:** PENDING
- **Decision (2026-11-11):** D5: ES.11 records whether this still reproduces on the champion. Fix it if it reproduces; retire it with the evidence otherwise.
- **Type:** operator
- **Issue:** not yet assigned; no issue created by cleanup
- **Depends on:** ES.11
- **Done when:** <TBD — operator fills in>
- **Source:** [tactical-bugs.md](../archived-plans/tactical-bugs.md); original T.12.

`bot.py::_produce_army` morph branch. First diagnostic run was done in
**rules** mode at diff 3 (simpler, removes the policy variable while
isolating the engine-level morph dispatch).

Once the root cause is identified and the fix lands, the morph behaviour
must be re-validated in **hybrid mode** (`--decision-mode hybrid
--model-path bots/v0/data/checkpoints/v3.zip --no-claude`) to confirm
the PPO policy doesn't suppress HT warp-ins or starve the gas economy
enough to skip the morph branch in realistic deployment. Archon count
must be non-zero across a 5-game hybrid sample before we consider the
fix shipped.



Revalidate current code and prior closure evidence before building; historic symptom status is retained, not newly reproduced.


<!-- autofix-applied: 2026-11-11 -->


### Step T.2.1: Revisit proactive elevation-aware rally placement

- **Status:** PENDING
- **Decision (2026-11-11):** D5: ES.11 records whether this still reproduces on the champion. Fix it if it reproduces; retire it with the evidence otherwise.
- **Type:** code
- **Issue:** not assigned
- **Depends on:** ES.11
- **Done when:** <TBD — operator fills in>
- **Source:** [tactical backlog](../archived-plans/tactical-bugs.md), original T.2 deferred tail.

Reactive centroid/HP-loss detection shipped c5bd90d/#138. The separately deferred proactive elevation-aware rally-point selection remains a distinct requirement: investigate whether current placement avoids low-ground bleeding before proposing it. Do not reopen the shipped reactive fix. Accept only actual terrain-aware placement with regression evidence; no new gameplay work in this migration.

## 8. Jev proof of concept — closed 2026-11-11

Decision D14 (operator, 2026-11-11): Jev is a separate bot and a completed proof of concept.

**Shipped:** JV 201–206 (graph player and Jev tab); JI 209–211 (hosted Typesafe integration, live acceptance passed 2026-10-08); J2 code 212, 214–221 and 224 (adaptive v2, benchmark, dashboard-first launch); the Observer Rush preview with Archon Auto Caster.

**Not run and not claimed:** operator gates 207, 208, 213, 222 and 223; JDI acceptance (0/9); the JON campaign (225–231). JDI Typesafe spend was $0. Closing the proof of concept is an operator scope decision, not acceptance evidence for those gates.

**Removed from this plan:** 207, 208, 213, 222, 223, J2.0, 225–240, JV.9, JV.10. Their requirements and the campaign/experiment contracts formerly in this section remain in [jev-player-plan.md](../archived-plans/jev-player-plan.md), [jev-v2-plan.md](../archived-plans/jev-v2-plan.md), [jev-overnight-improvement-plan.md](../archived-plans/jev-overnight-improvement-plan.md), [jev-decision-integration-plan.md](../archived-plans/jev-decision-integration-plan.md) and the [archived master](../archived-plans/master_plan.md). The full pre-reset text of this master, including every removed step and the former §8 contracts, is preserved in [master_plan-pre-reset-2026-11-11.md](../archived-plans/master_plan-pre-reset-2026-11-11.md). The ideas worth reusing are in the [Jev harvest seed](../seeds/jev-harvest.md).

**Preserved, unmerged:** build-step-225-b0cc7700 (sibling worktree), build-step-225-jdi-contracts `324d5f0`, build-step-232-jdi `00bc515`, fix/jev-order-repair `f027bc2`. Deleting them is a separate operator decision.

**GitHub:** issues #317, #324, #325, #327, #330, #339, #340 and #346–#363 were closed on 2026-11-11 with the operator-approved comment that Jev work will be incorporated into main bot functionality. #317 and #327 were closed as completed (phases that shipped code); the rest as not planned.

Jev code under `src/jev/` and `bots/jev/` stays in place, and its tests keep running. The frozen v1 snapshot v1-b53039a693cadf75 must never be recaptured.

## 9. Tests and validation

Future code steps retain original review flags, production-caller integration, negative/default-preservation tests and exact Done-when. Re-measure baseline on the actual worktree; historical counts are evidence, not timeless assertions. Run scoped checks while iterating and required full suites at implementation acceptance: uv run pytest, uv run ruff check ., uv run mypy src bots --strict; scripts checks explicitly where EH/EI/JON own them, report pre-existing errors. Frontend npm run test:run (not watch), lint,typecheck,build; actual browser tests separately. Offline proof never closes live gates. Migration validation/source hashes are in ../plan-reconciliation/validation.json.

## 10. Risks and holds

Reference-only exception: this authorized documentation migration repairs the shared-doc resource paths in frozen RL bots/v0 through v12 as well as current v13. Original bytes remain in Git history; gameplay logic, stored data, Jev captured source snapshots and policy bytes are untouched. Historical source coordinates still need the migration map. The earlier alpha4gate.runner skill cleanup is already absent from current skills; it is not reopened.

Open product decisions: EI.0 (approve the reduced evidence layer), BL.1 (branch landing), E.0 (under PPO.0), D9 (evolution lines) and the ES-A frozen-tree exception for the SC2 compatibility shim. PARKED work (D8, D9, D12, D13) is not dispatch-ready. No command here should bypass current local safety rules. Historical code anchors must be rederived. Archives preserve links verbatim for provenance, so historical relative links may be stale; use migration.json to resolve old paths.

## 11. Estimates

[Build estimates](build-estimates.md) are prospective model ranges for every outstanding leaf, active worker and operator attention hours. They exclude unattendedSC2 wall duration, separately named in gate requirements; no measured calibration history exists. Held work is forecast-only, not approved dispatch. Raw master SHA256 binds exact plan bytes; Observatory binds its own evidence work-item fingerprints. The estimates predate the 2026-11-11 reset: they still list the removed Jev, EV, SV, UI and dropped EH rows and have no ES.0, PPO.0 or VW.0 rows. Rebind after Phase ES is planned.

## 12. Completion and rollback

A phase closes only when its original acceptance gates and actual evidence pass; negative experiments remain recorded with target-unmet status when specified. Completed original plans reside under ../completed/, mixed or superseded originals under ../archived-plans/. Source bytes/hash history are preserved, and migration.json records all file paths. Reconciliation is reversible file moves plus references; no runtime data or frozen baseline changed. Implementation rollback follows phase-specific backups/version isolation and never deletes a real promoted version merely because it appears untracked.

## 13. Quickstart for a fresh session

Read project instructions, this master and the exact source for the selected leaf. Inspect live git status/branch, preserved pending worktrees and issue evidence. Choose an unheld ready leaf, re-anchor producer/consumer paths, execute its normal review workflow. Do not use stale overnight/session goals as new authorization. For operator work read docs/operator guides and docs/operator-gate-runbook.md after applying section5 corrections. Stop/report at actual gate boundaries. Since 2026-11-11, start with §16 and the four seeds ([evolve-for-strength](../seeds/evolve-for-strength.md), [ppo-direction](../seeds/ppo-direction.md), [strength-viewer](../seeds/strength-viewer.md), [jev-harvest](../seeds/jev-harvest.md)). ES.0 is complete; the ready leaves are ES.1, ES.2, ES.14, EH.3 and VW.1 (lanes in `docs/lanes/README.md`).

## 14. Source reconciliation

[Merge mapping and review](../plan-reconciliation/merge-decisions.md) describes duplicates, partial overlap, sequencing and intentionally retained scope. [Immutable migration map](../plan-reconciliation/migration.json) includes source hashes and targets. Source reviews/proposals/validation reports are reference evidence under ../plan-reconciliation/source-reviews/, not competing plans. All original append-only decisions and build history remain in full archived sources.

## 15. Appended decision inventory (2026-10-14)

| ID | Origin | Decision | Status |
|---|---|---|---|
| RC1 | Operator | All active-project docs consolidated; one master; preserve distinct requirements | Applied |
| RC2 | Operator | Shared Toolkit scheduling authority retained | Applied |
| RC3 | Agent default | Keep JDI/JON and every live gate separate; source hashes preserve history | Applied |
| RC4 | Agent default | Hold contradictory E cardinality and unowned J2 residual scope | Unresolved product choices |
| RC5 | Agent default | Forecast ranges are model estimates; no invented measured history | Applied |
| RC6 | Operator | Direction reset to measurable playing strength; decision register D1–D15 (§16). Closes the J2 half of RC4; E cardinality moves under PPO.0 | Applied 2026-11-11 |


## 16. Direction reset and decision register (2026-11-11)

**Goal:** the bot measurably gets better at the game. A promoted version must beat the current champion on a fixed external scoreboard, not only its immediate parent in self-play. The evidence behind the reset is in the [evolve-for-strength seed](../seeds/evolve-for-strength.md#why-this-phase-exists-evidence-gathered-2026-11-11).

**Operator answers (2026-11-11):**

1. **Jev:** a proof of concept, now closed (§8). Its ideas carry forward into the main bot.
2. **PPO:** probably the strongest path forward. Explore it actively; do not park it (PPO.0).
3. **Targets:** staged, because a single target would be overcome quickly (ES-C).
4. **Champion challenge:** an older version such as v7 replaces v13 only if it beats v11, v12 and v13 head-to-head (ES.5).
6. **Tension and review answers (2026-11-16):** T1-A split by job, T2-B PPO first after the scoreboard, T3-A spot-check by re-playing live, T4-A PPO.0 owns the legality route, T5-A diversity parked until Stage 1, T6-A both champion conditions, T7-A Stage 1 unchanged; RD1-A worker in `scripts/`, RD2-A EH then ES.6–ES.10 then EI, RD3-A plant a known-weak candidate in ES.12. Source: `docs/decisions/decisions-owed-2026-11-16.html`.
5. **Phase ES planning answers:** maps Simple64/96/128; Stage 0 lean (VeryHard, 10 per race per anchor); a posterior panel gate. Evolution runs headless on WSL because it is much faster, live games (one bot vs the computer, or two bots) are for spot checks and demos, and live evolution is deprecated (D16). Workflows adjust accordingly (ES.6, VW.0).

| ID | Theme | Steps | Decision | Status after reset |
|---|---|---|---|---|
| D1 | Fixed scoreboard | new; R.1, S.3 | Agreed | Phase ES (ES-B, staged ES-C) via ES.0 |
| D2 | SC2 patch compatibility and landing the working tree | #364; BL.1 | Agreed | ES-A; BL.1 PENDING |
| D3 | Promote the code that was tested | ER.2 | Agreed (confirmed 2026-11-11) | ES-E |
| D4 | Panel-gated promotion | EJ.7, EJ.8, ER.3, ER.4 | Agreed (confirmed 2026-11-11) | ES-F |
| D5 | Tactical bugs | T.1–T.12, T.2.1 | Agreed | ES-G |
| D6 | Unattended-run safety | EH.3, EH.6–EH.10 | Agreed | PENDING; EH.10 amended for D7 |
| D7 | Dashboard Stop button | EH.4, EH.5, EV.6 | Drop: duplicate functionality (closing the evolve console, Ctrl+C on a headless run, `--hours`/`--generations` caps) | Removed; the unwired button is handled under VW.0 |
| D8 | Evidence layer | EI.0–EI.14 | Agreed: keep EI.0–EI.4, EI.6 and a smaller EI.13 | EI.5, EI.7–EI.12, EI.14 PARKED |
| D9 | Evolution lines | EL.7, ER.5, ER.7 | Decided 2026-11-16 (T5): parked until Stage 1 passes | PARKED |
| D10 | Viewer | EV.4, EV.5, SV.1–SV.6, UI.7, UI.8 | Reframe, do not park: a Jev-style viewer for candidates, with a two-screen version for bot-vs-bot, limited time | Removed into VW.0 and the [strength-viewer seed](../seeds/strength-viewer.md) |
| D11 | PPO / RL | M1, M2, E.0–E.6, 6.1–6.3, 7.6, ER.8 | Reframe for the strength goal, do not park | HELD under PPO.0 and the [ppo-direction seed](../seeds/ppo-direction.md) |
| D12 | Hydra experts, replay pretraining | O.0–O.3, P.1 | Agreed | PARKED |
| D13 | Diversification and research | G, H, I, J, K, L, M.1, Q, S.1, S.2, S.4, ER.6, ER.9 | Park, not drop: diversification is a later strength path | PARKED |
| D14 | Jev | 207, 208, 213, 222, 223, J2.0, 225–240, JV.9, JV.10 | Separate bot; mark complete; harvest general ideas, then remove | Removed (§8); [Jev harvest seed](../seeds/jev-harvest.md) |
| D15 | "An SC2 version of the judge" | ER.1 | Agreed: the scoreboard answers it | Removed (closed) |
| D17 | Order of work (2026-11-16, T2) | ES.6–ES.13, PPO.0 | PPO first after the scoreboard: build ES.1–ES.5 (and ES.14), then PPO.0 plans a PPO build phase; ES.6–ES.13 held | ES.6–ES.13 HELD |
| D18 | Substrate split by job (2026-11-16, T1) | ES.2, ES.4, ES.5, ES.13, ES.14 | Loop and panel headless; stage scoreboards and champion decisions on Windows | ES.14 added |
| D16 | Where evolution runs | `evolve --viewer`, launcher, scoreboard | Headless on WSL Linux for evolution and measurement; live Windows games for spot checks and demos; deprecate live evolution | ES.1, ES.6; VW.0 reframed |

**D9 in plain language.** Today evolution is a single family tree: v13 has candidate children, and the best child that beats its parent becomes the next champion. *Evolution lines* would run several separate family trees at once (for example one from v13 and one from v10) so they can drift into different styles. Every so often, a line that is both weaker and too similar to another line is retired ("extinction"), freeing its slot. Similarity is measured by a fingerprint: each line's win rates against the frozen anchors. The code shipped in EL.1–EL.6. What remains is a multi-hour soak proving it works (EL.7), richer fingerprints (ER.5) and running lines at the same time (ER.7).
- *Why it could help:* one line can get stuck or chase a rock-paper-scissors loop, and different styles give stronger, more varied sparring partners (the AlphaStar league idea). This is the in-loop version of D13 diversification.
- *Why it might not, yet:* on one machine every line costs games. Three lines give each line about a third of the games, so each improves about three times more slowly, while the single line has not yet shown measurable gains.
- *Suggested test:* once the scoreboard shows the single line climbing, run one line against two lines with the same game budget and compare scoreboard gains.

**Removed on 2026-11-11 (38 steps):** Jev (24, D14), EH.4, EH.5, EV.6 (D7), EV.4, EV.5, SV.1–SV.6, UI.7, UI.8 (D10, folded into VW.0) and ER.1 (D15). Their full pre-reset text is in [master_plan-pre-reset-2026-11-11.md](../archived-plans/master_plan-pre-reset-2026-11-11.md). That archive was rebuilt from the last staged blob of this file (`292a536`, 2026-10-15) plus two Step 232 lines edited later, which were read verbatim during the reset session. It reproduces this file byte for byte when the reset is reapplied. The original source plans are listed in §8 and in [migration.json](../plan-reconciliation/migration.json).

**Next:** Phase ES was authored by `/plan-feature` on 2026-11-11 (§17, ES.1–ES.13; ES.0 is complete). Run plan-review → plan-redline → plan-wrap → repo-sync before any build. PPO.0's design-space survey and VW.0 can run in parallel. **Retired into Phase ES on 2026-11-11:** R.1 and S.3 (ES.3), ER.2 (ES.7), ER.3 and ER.4 (§17.6 decisions), EJ.7 (ES.12) and EJ.8 (ES.13).

## 17. Phase ES — evolve for strength (design, 2026-11-11)

Authored by `/plan-feature` from [evolve-for-strength.md](../seeds/evolve-for-strength.md). Build steps are ES.1–ES.14 in §7.

### 17.1 What this phase does

It makes "the bot got better" a measured fact. Versions are judged against Blizzard's built-in AI on a fixed, staged scoreboard. Evolution ships the exact code it tested, and a promotion that plays worse against the built-in AI is rolled back. Evolution and measurement run headless on WSL Linux for speed; live games are kept for spot checks and demos (D16).

### 17.2 Existing context (verified 2026-11-11)

- **Opponent and races:** every runner plays a Random-race built-in AI. `run_bot` accepts `opponent_race`, but no runner passes it (`bots/v13/connection.py:46-93`, `bots/v13/runner.py:380-387,491-498`). There is no AI-build or seed parameter. Runners for v0, v3, v7, v11 and v12 match v13's apart from module names.
- **Data and replays:**
  - Runner results go to the *current* tree's data directory unless `DATA_DIR` is set (`config.py:63-74`, `src/orchestrator/registry.py:130-151`).
  - Replays are saved to `replays/` without their path being recorded (`connection.py:43,54,96`).
  - The `--batch` path ignores `--decision-mode` and `--model-path` (`runner.py:482-490`).
- **Evolve internals:**
  - Fitness candidates are deleted after scoring (`src/orchestrator/evolve.py:675-681`), and stack-apply re-authors winners from text (`scripts/evolve.py:2219-2221`). No scored code is kept.
  - The baseline gauntlet plays frozen versions only, after the promotion commit (`src/orchestrator/evolve.py:940`, `scripts/evolve.py:4769-4789`).
  - The regression rules and rollback path are at `src/orchestrator/gate_stats.py:48-82` and `scripts/evolve.py:4946-5002`.
  - Self-play has no built-in-AI path (`src/orchestrator/selfplay.py:590,296`).
- **SC2 builds:**
  - Windows is on build 98310, where v0–v12 crash without `orchestrator.sc2_compat` (#364, landed 2026-11-16 as 52f1afd; v13 and Jev install it at import, v0–v12 need ES.14's launcher).
  - WSL Ubuntu-22.04 has Linux SC2 base build 75689 (4.10), with Simple64/96/128 and 2017–2019 ladder maps under `~/StarCraftII/Maps`, a venv at `~/venv-alpha4gate-linux`, and `uv` and `claude` on PATH. The repo is used from `/mnt/c`. Setup rules: `.claude/rules/wsl-evolve.md`.
- **Tools and pointer:**
  - `scripts/selfplay.py --p1 --p2 --games --map` alternates seats automatically (`src/orchestrator/selfplay.py:313-318`).
  - `orchestrator.evolve._restore_pointer` repoints `bots/current` atomically (`src/orchestrator/evolve.py:374-394`).
  - Evolve refuses an uncommitted pointer change (`scripts/evolve.py:770-784`).
- **Parent plan:** this phase implements decisions D1–D6, D8 and D16 (§16) and absorbs R.1, S.3, ER.2, ER.3, ER.4, EJ.7 and EJ.8.

### 17.3 Scope

- **In:**
  - a headless substrate check
  - the scoreboard runner, report and stages
  - the Stage 0 baseline and champion challenge
  - a headless evolve launcher, with `--viewer` deprecated
  - scored-code promotion
  - a built-in-AI panel and posterior gate
  - proposer evidence
  - the loss review and tactical revalidation
  - an armed smoke, a soak and Stage 1
- **Out:**
  - the PPO control surface (PPO.0)
  - the live viewer and Windows spot-check compatibility for older versions (VW.0)
  - Stage 2+ runs (added when Stage 1 passes)
  - ladder-map scoreboards
  - evolution lines (D9)
  - deleting the EV viewer code (deprecation only)
  - a dashboard surface for scoreboard reports (VW.0 may add one)

### 17.4 Impact analysis

| File or interface | Change | Reason | Verified evidence |
|---|---|---|---|
| `src/orchestrator/sc2_compat.py`, `tests/test_sc2_compat.py` | Landed in 52f1afd (#364); no further edit | Worker installs the shim (no-op on 4.10, needed on Windows) | Only `bots/v13/bot.py` and `src/jev/bot.py` import it today |
| New `src/orchestrator/scoreboard.py`, `scripts/scoreboard_worker.py` (RD1), `scripts/scoreboard.py` | New | Scoreboard, report, panel, findings | No existing built-in-AI batch with race control (§17.2) |
| `bots/<v>/connection.run_bot`, `bots/<v>/runner._run_single_game` | Read and called only; frozen trees unedited | Score the production artifact | Identical across v0–v13 |
| `src/orchestrator/evolve.py` `run_fitness_eval`, `generate_pool` | Capture scored files; add an evidence block | ES.7, ES.10 | `:529-681`; parallel path `scripts/evolve_worker.py:455` |
| `scripts/evolve.py` stack-apply, gauntlet, rollback, CLI | `--promote-scored`, `--ai-panel`, `--panel-gate`, `--viewer` deprecation | ES.6–ES.9 | `:2111,2219,4769,4946,505` |
| `src/orchestrator/gate_stats.py` | Two-sample posterior | ES.9 | `:48-82` |
| `scripts/launch-evolve.ps1` | Spawn in WSL; `-EvolveArgs` | ES.6 (D16) | Spawn line `:72`; ASCII pin `tests/test_evolve_cli.py:5167-5172` |
| `Baseline` / `data/baselines.json` | **No change** | The panel lives in scoreboard files | Consumers: `src/orchestrator/{baselines,evolve,fingerprint,population,lineages}.py`, `scripts/{baseline,evolve,lineage}.py` and their tests (search root: `src`, `scripts`, `bots/v13`, `frontend/src`, `tests`) |
| Evolve pool-state JSON | **No change** | Scored files live in `data/evolve_scored/` | Avoids wire-shape drift for the Evolution tab |
| `bots/current/current.txt` | Changed only by ES.5's operator repoint | Champion challenge | `_restore_pointer`; commit required |

### 17.5 New components

- `scripts/scoreboard.py`: the `run`, `report` and `flag` subcommands.
- `src/orchestrator/scoreboard.py`: schedule, rows, resume, `STAGES`, Wilson intervals, panel cache, findings.
- `scripts/scoreboard_worker.py`: one game per subprocess, launched by path (RD1: `src/orchestrator/` must not import `bots.*`).
- Run-scoped stores (all gitignored under `/data/`): `data/scoreboard/`, `data/evolve_scored/`, `data/findings.jsonl`.

`STAGES` (pre-registered; changed only by a recorded operator decision):

| Stage | Difficulty | Games per race | Pass rule (intention-to-test) |
|---|---|---|---|
| 0 | VeryHard (7) | 10, for each anchor v0, v3, v7, v11, v12, v13 | none; measures and lists challengers |
| 1 | VeryHard (7) | 20 | overall ≥ 60% and every race ≥ 50% |
| 2 | CheatVision (8) | 20 | overall ≥ 50%, every race ≥ 40%, and VeryHard still ≥ 60% |
| 3 | CheatMoney (9) | 20 | overall ≥ 50% |
| 4 | CheatInsane (10) | 20 | overall ≥ 50% |
| 5 | beyond the built-in AI | — | frozen-panel Elo plus external opponents (D13); not built in this phase |

### 17.6 Design decisions

| Choice | Decision | Provenance | Rationale | Alternatives / unresolved |
|---|---|---|---|---|
| Substrate | Split by job (2026-11-16, T1): evolution's inner loop and the promotion panel run headless on WSL Linux SC2 4.10; stage scoreboards and champion decisions run on the current Windows build (98310); live Windows games for spot checks and demos; `evolve --viewer` deprecated | operator (D16, T1) | Speed for the loop; real-game fidelity for the verdicts | All headless; all Windows |
| Maps | Simple64/96/128, rotated | operator | Installed on both substrates | Ladder maps (on Linux) later |
| Stage 0 size | VeryHard, 10 per race per anchor (180 games) | operator | About 8 hours; the challenge settles close calls | 20 per race, or Harder too |
| Panel gate | Roll back when P(worse than incumbent) ≥ 0.85 on 12 panel games | operator | Reuses the existing threshold; about 15% false rollback for an equal version | Log-only; strict |
| Champion challenge | ≥ 6/10 against each of v11, v12, v13 and no worse at Stage 0, then repoint and commit | operator (rule); agent default (tools) | About 5% joint false pass for an equal challenger | — |
| Stage pass rule | Point estimate and per-race floor; Wilson intervals reported | agent default | A Wilson lower bound at 60 games is too strict | Wilson-lower-bound rule |
| Stage 1+ sample | 20 games per race | agent default | 60 games per stage verdict, about 2.5 hours | — |
| Frozen trees | Not edited; the worker reuses each tree's own builders | agent default | Measurement validity: score the production artifact | Frozen-tree exception not needed |
| AI build and seed | Default build, not seeded (no parameter exists) | inference | `run_bot` has no build or seed argument | Add a build parameter later |
| Panel storage | Scoreboard files; `Baseline` unchanged | agent default | Avoids fingerprint and lineage consumers | `Baseline.kind` field |
| New flags | `--promote-scored`, `--ai-panel` and `--panel-gate` default off; armed in ES.12/ES.13 | agent default | Bare invocation stays byte-identical | — |
| Self-play fitness bar | Unchanged (strict majority) in this phase; ES.13 records the observed pass rate | agent default | The panel gate guards quality; revisit with evidence (former ER.3) | `--fitness-alpha` 4/5 bar |
| Ancestor regression | Covered by stage scoreboards across anchors, not per-promotion ancestor games (former ER.4) | agent default | Lower game cost | PFSP ancestor sampling |
| Scoreboard concurrency | `--concurrency`, default 1 | agent default | Phase 8 validated 4-way on Linux; RAM-bound | — |

### 17.7 Risks and open questions

| Item | Consequence | Mitigation / owner | Blocking |
|---|---|---|---|
| Linux 4.10 replays may not open in the Windows 98310 client | Loss review cannot use replays | ES.1 checks; ES.11 falls back to live replays of the matchup | No |
| Built-in AI differs between 4.10 and 98310 | Scoreboard numbers are Linux-only; April Windows numbers are not comparable | Report substrate and build with every run | No |
| WSL environment drift since April | Runs fail | ES.1 | Yes, for ES.4 |
| Closing the console may orphan Linux SC2 | Leaked processes | ES.12 checks `pgrep`; fix before ES.13 | Yes, for ES.13 |
| EH.7 lock across Windows and WSL pid spaces | Two runs collide | EH.7 decision note (D16) | Yes, for ES.12 |
| Small samples (10 per race at Stage 0) | Wide intervals | Champion challenge; Stage 1 uses 20 | No |
| No game seed control | "Sealed" means a fixed schedule order, not identical games | Stated in reports | No |
| Panel adds 12 games per promotion | Fewer generations per hour | Incumbent cache; concurrency | No |

### 17.8 Testing strategy

- **Narrow:** scoreboard schedule, rows, resume, Wilson and verdict math, posterior math, capture and normalization.
- **Production callers:** `scripts/scoreboard.py run|report|flag` through the CLI, and `run_loop` with games and dev-apply stubbed only at their process boundaries.
- **Real components:** ES.1 (substrate) and ES.12 (armed launcher run) before long observation. ES.4 and ES.13 are the time-dependent observations.
- **Commands** (verified in CLAUDE.md):
  - `uv run pytest`
  - `uv run ruff check .`
  - `uv run mypy src bots --strict`
  - `uv run mypy scripts/scoreboard.py --strict` (scripts are outside `mypy src bots`; `scripts/evolve.py` has a pre-existing 3-error baseline)

  Frontend is untouched by this phase.

## 18. Appendix

### Decision Inventory

Redline IDs (RP = operator pick, RD = agent default) are distinct from the §16 register IDs D1–D16. Publication 1, 2026-11-11: [proposal](../plan-redline/alpha4gate-proposal.html).

| ID | P/D | Choice | Status |
|---|---|---|---|
| RP1 | P | Goal is measurable playing strength; §16 D1–D6, D8, D12, D15 agreed (D3/D4 confirmed 2026-11-11) | stands |
| RP2 | P | Jev closed as a completed proof of concept; Jev-only steps removed and harvested; 25 issues closed (D14) | stands |
| RP3 | P | PPO explored actively, not parked (D11, PPO.0) | stands |
| RP4 | P | Viewer reframed Jev-style for live spot checks and demos, limited time (D10, VW.0) | stands |
| RP5 | P | Dashboard Stop work dropped as duplicate functionality (D7) | stands |
| RP6 | P | Diversification parked, not dropped (D13); evolution lines parked pending decision (D9) | stands |
| RP7 | P | Staged targets VeryHard → CheatVision → CheatMoney → CheatInsane (§17.5 `STAGES`) | stands |
| RP8 | P | An older champion replaces v13 only by beating v11, v12 and v13 head-to-head (ES.5) | stands |
| RP9 | P | Scoreboard maps Simple64/96/128, rotated | stands |
| RP10 | P | Stage 0 lean: VeryHard, 10 games per race per anchor | stands |
| RP11 | P | Evolution and measurement headless on WSL Linux; live games for spot checks and demos; live evolution deprecated (D16) | stands |
| RP12 | P | Panel gate rolls back at P(worse than incumbent) ≥ 0.85 | stands |
| RD1 | D | Scoreboard worker in `scripts/scoreboard_worker.py`, with a test keeping `src/orchestrator/` free of `bots.*` imports | changed 2026-11-16 |
| RD2 | D | Order on the shared evolve files: EH.3, EH.8, EH.6, EH.7, EH.9, then ES.6–ES.10, then EI | changed 2026-11-16 |
| RD3 | D | ES.12 injects a known-weak candidate and confirms the panel rollback fires | changed 2026-11-16 |
| RD4 | D | Stage pass on point estimate plus per-race floor; Wilson intervals reported | stands |
| RD5 | D | Stage 1+ sample 20 games per race | stands |
| RD6 | D | Calibration before Stage 0: v13 ≥8/9 at Easy, ≤1/9 at CheatInsane | stands |
| RD7 | D | Frozen trees unedited; scoreboard reuses each tree's own builders | stands |
| RD8 | D | Built-in AI default build, unseeded (inference: no build/seed parameter) | stands |
| RD9 | D | Panel of 12 games (4 per race) at stage difficulty; incumbent results reused | stands |
| RD10 | D | New evolve flags default off; armed in ES.12/ES.13 | stands |
| RD11 | D | Self-play fitness bar unchanged (simple majority) during Phase ES | stands |
| RD12 | D | Ancestor regression via stage scoreboards, not per-promotion games | stands |
| RD13 | D | Conflicting scored winners benched, never re-authored | stands |
| RD14 | D | Launcher runs WSL evolve in a foreground console; closing it stops the run | stands |
| RD15 | D | `evolve --viewer` deprecated with a warning, not removed | stands |
| RD16 | D | Champion challenge on Simple64, 10 games per opponent; repoint by one-line `_restore_pointer` call plus commit | stands |
| RD17 | D | Scoreboard `--concurrency` default 1 | stands |
| RD18 | D | Plan-review's non-decision gaps left for plan-wrap and build time | stands |
| RD19 | D | ES.11 reviews at least 12 champion losses, 4 per race | stands |
| RP13 | P | T1-A: split by job — loop and panel headless; stage scoreboards and champion decisions on Windows | stands |
| RP14 | P | T2-B: PPO first after the scoreboard; ES.6–ES.13 held until a PPO build phase is planned | stands |
| RP15 | P | T3-A: spot-check by re-playing a promoted candidate live | stands |
| RP16 | P | T4-A: PPO.0 evaluates Jev's legal-moves graph and records yes or no | stands |
| RP17 | P | T5-A: evolution lines and Jev opponents parked until Stage 1 passes | stands |
| RP18 | P | T6-A: champion challenge needs head-to-head wins and a Stage 0 score no worse than v13's | stands |
| RP19 | P | T7-A: Stage 1 stays VeryHard at 60%, every race at 50% or better | stands |
