# Evolve Parallelization Sub-Plan

**Track:** Capability research (Track 9 — master plan)
**Blocks:** Phase O (Hydra meta-controller, paused 2026-04-28)
**Source issue:** GitHub #235
**Author:** /plan-feature 2026-04-29
**Status:** READY-FOR-BUILD

---

## 1. What This Feature Does

Run multiple imp fitness evals concurrently per generation in `scripts/evolve.py`, fanning out over a configurable concurrency window (default 4), and surface every in-flight eval in the dashboard's Evolution tab as a grid of progress cards. Today the fitness phase iterates `for idx in active_idxs:` strictly serially, which makes evolve's wall-clock dominated by single-threaded SC2 game time. Phase 8 Spike 3 already proved 4-way concurrent self-play works on the WSL/Linux substrate; that throughput is sitting idle.

Why now: Phase O (Hydra) generates 4-6 themed expert versions, each requiring its own `improve-bot --theme=X` run. Sequentially that's days of wall-clock; with 4-way evolve it's hours. Per user direction 2026-04-28 (`memory/project_post_phase_n_direction_2026_04_28.md`), parallelization ships before Phase O scripted v1.

---

## 2. Existing Context

A fresh-context model needs these landmarks to build this plan without re-reading the codebase end-to-end:

- **Two strict-serial loops today:**
  - `scripts/evolve.py:1881` — `for idx in sorted(active_idxs):` runs one fitness eval at a time per generation.
  - `src/orchestrator/selfplay.py:671` — `for i in range(games):` runs games-within-batch sequentially. **This loop is OUT OF SCOPE.** Parallelization happens at the imp-eval boundary (one level up), not the game-within-eval boundary.
- **`bots/current/current.txt`:** A one-line text file holding the active bot version (e.g., `v3`). Read by `bots.current` (a MetaPathFinder shim) and by `runner.py --serve` to resolve which `bots/<vN>/` directory backs the dashboard's per-version data dir. It's a process-global mutable — every Python process in the repo shares the same file — which is the parallel-unsafe surface this plan retires (see Decision D-2).
- **Fitness eval primitive:** `src/orchestrator/evolve.py::run_fitness_eval` (line ~470) snapshots parent → applies imp into `bots/cand_<uuid>/` → flips `bots/current/current.txt` to the candidate → calls `run_batch(cand, parent, games=5)` → restores pointer in `finally:`. The pointer-flip is the parallel-unsafe primitive (see Decision D-2).
- **Substrate baseline — Phase 8 Spike 3:** A 4-way parallelism validation run on 2026-04-26 (`scripts/spike3_launch.sh`). Launched 4 separate `python scripts/selfplay.py` shell-children in parallel from a `for i in 1 2 3 4; do … & done` bash loop, each playing 5 games of `v0 vs v0` and writing to its own `data/selfplay_results.parallel-{1..4}.jsonl`. A 1Hz RSS sampler reading `/proc/[0-9]*/exe` for `SC2_x64` confirmed 8 SC2_x64 processes coexisted on a single WSL2 / Ubuntu-22.04 host; peak total RSS bounded. Wall-clock for 4 parallel × 5 games ≈ 7 min vs ~28 min serial. **This is the only multi-worker SC2 topology validated on this codebase.** Every parallelization decision in this plan defers to its evidence.
- **State files written by evolve:**
  - `data/evolve_run_state.json` — run-level metadata (read-modify-write).
  - `data/evolve_pool.json` — pool of imps + per-imp state (read-modify-write).
  - `data/evolve_current_round.json` — single in-flight round payload (replaced on each phase event).
  - `data/evolve_results.jsonl` — append-only phase-result log (parallel-safe via O_APPEND).
  - `data/evolve_crash_log.jsonl` — append-only crash log (parallel-safe).
- **API surface:** `bots/v3/api.py:1289` — `GET /api/evolve/current-round` returns single `EvolveCurrentRound` object. Frontend hook `frontend/src/hooks/useEvolveRun.ts:249` polls at 2s. Component `frontend/src/components/EvolutionTab.tsx` (994 lines) renders one progress card.
- **Recently shipped pre-reqs (this session, 2026-04-29):**
  - DrvFS-safe `_drvfs_safe_rmtree` (`9aa2a64`) — parallel rollback paths now safe under `/mnt/c`.
  - Aggressive snapshot rewriter (`5f146bf`) — N concurrent candidate snapshots all rename string-literal self-references cleanly.
  - These two fixes were prerequisites for parallelization; they are NOT this plan's scope.

---

## 3. Scope

### In scope

- Fan-out at the **imp-eval boundary** (one fitness eval = one worker subprocess).
- Configurable `--concurrency N` flag in `scripts/evolve.py`. Default `1` preserves byte-identical behavior to the current serial path (Decision D-1).
- A new worker entry-point `scripts/evolve_worker.py` (or equivalent CLI mode of `scripts/evolve.py`) that runs ONE fitness eval and writes its result JSON to a known path.
- Per-worker live-state files `data/evolve_round_<worker_id>.json` with atomic replace-on-write.
- New API endpoint `GET /api/evolve/running-rounds` returning `list[EvolveCurrentRound]`.
- Backwards-compat shim on `/api/evolve/current-round`: returns first non-idle running round, or idle skeleton if none.
- Frontend `useEvolveRun.ts` extension: new `runningRounds` field; cache key bumped.
- Frontend `EvolutionTab.tsx` grid render of N progress cards. Existing single-card render becomes the N=1 case.
- Smoke-gate step (60s, `--concurrency 2 --pool-size 2`) before observation soak.
- Observation step (≥4h, `--concurrency 4`) confirming end-to-end correctness on a real WSL host.

### Out of scope

- Parallelizing games-within-an-imp's-batch (the inner `for i in range(games):` in `selfplay.run_batch`). Adds another layer of fan-out without solving the gating bottleneck.
- Cross-generation parallelism (running gen N+1's pool while gen N is mid-flight). Generation-level synchronization is load-bearing for the regression gate.
- Multi-host / cloud-worker fan-out. This plan targets single-host WSL, matching Spike 3.
- Parallelizing the regression phase (regression is single-pair vs prior parent — naturally serial).
- Parallelizing pool generation (Claude pool-gen call runs once per generation, not on the hot path).

---

## 4. Impact Analysis

| File | Nature | Reason |
|---|---|---|
| `scripts/evolve.py` | modify | Replace fitness-phase `for idx in active_idxs:` with a concurrency-window dispatcher (subprocess.Popen pool). Out-of-order completion handler updates `pool_state`/`per_item_state` as workers finish. Adds `--concurrency N` arg. |
| `scripts/evolve_worker.py` | NEW | One-shot CLI: takes `--parent --imp-json --worker-id --result-path` args, runs `run_fitness_eval` for one imp, writes result JSON, exits. Subprocess target for the dispatcher. |
| `src/orchestrator/evolve.py::run_fitness_eval` | modify | Drop the `bots/current/current.txt` pointer flip (Decision D-2). Caller passes the candidate version explicitly via `run_batch(p1=cand_name, p2=parent_name)`; SC2 child processes get the version from argv, not the pointer. |
| `src/orchestrator/selfplay.py::run_batch` | no change | Already takes `p1`/`p2` as explicit version strings. The pointer flip was a workaround; removing it is a no-op for run_batch. |
| `bots/v3/api.py` | extend | Add `GET /api/evolve/running-rounds`. Backwards-compat shim on `/api/evolve/current-round` to return first running slot or idle. |
| `bots/v3/process_registry.py:13` | modify | `_OUR_CMDLINE_TAGS = ("bots.v3", "bots.current")` plus the `_is_ours()` predicate (line 18) and the `label` resolver (line 165) need a `bots.cand_*` prefix match so per-worker SC2 children show up labelled in the dashboard's WSL processes panel. Resolved in this plan (was "possibly modify" in draft, now committed). Folded into Step 4 because the API endpoint is the dashboard consumer of process labels. |
| `frontend/src/hooks/useEvolveRun.ts` | modify | Add `runningRounds: EvolveCurrentRound[]` field; bump `CACHE_KEY_SUFFIX` from `evolve-v4` → `evolve-v5` (per `feedback_useapi_cache_schema_break.md`). |
| `frontend/src/components/EvolutionTab.tsx` | modify | Refactor the single-card render to a grid; existing renderer becomes the per-card subcomponent. Existing N=1 path must produce a visually-identical layout to today (single card centered) to avoid UX regression. |
| `tests/test_evolve.py` | extend | Add tests for `--concurrency 1` byte-identical behavior; out-of-order completion handling; budget-breach during parallel dispatch. |
| `tests/test_evolve_parallel.py` | NEW | Integration test for the dispatcher (mocked subprocess workers); per-worker state file write/merge; API endpoint shape. |
| `tests/test_api.py` | extend | New endpoint contract test for `/api/evolve/running-rounds`. |
| `frontend/src/__tests__/EvolutionTab.test.tsx` (or equivalent) | extend | Grid-render snapshot test with N=1, N=2, N=4. |
| `documentation/wiki/operator-commands.md` | modify | Document `--concurrency N` flag and the smoke-gate / soak invocation patterns. |
| `documentation/plans/alpha4gate-master-plan.md` §"Phase O" | modify | Replace the "paused pending sub-plan" note with a one-line link to this plan once it ships. |

---

## 5. New Components

### `scripts/evolve_worker.py`

A one-shot CLI invoked by the parent dispatcher. Accepts:
- `--parent <version>` — the version this candidate is evaluated against
- `--imp-json <path>` — JSON file with the serialized `Improvement` dataclass
- `--worker-id <int>` — slot identifier (0..N-1) for state file naming
- `--result-path <path>` — where to write the final `FitnessResult` JSON
- `--games-per-eval`, `--map`, `--game-time-limit`, `--hard-timeout` — passed through to `run_fitness_eval`

Writes:
- `data/evolve_round_<worker_id>.json` — live progress, replaced atomically on each phase event
- `<result-path>` — final result JSON when fitness eval completes
- Exits 0 on completion (regardless of fitness pass/fail), non-zero on crash

This is a thin wrapper around `orchestrator.evolve.run_fitness_eval` — the orchestration logic stays in the parent.

### `data/evolve_round_<worker_id>.json`

Per-worker live-state file. Concrete shape (extension of today's `EvolveCurrentRound` interface at `frontend/src/hooks/useEvolveRun.ts:187-207`):

```json
{
  "run_id": "8a7b6c5d",
  "worker_id": 0,
  "active": true,
  "generation": 3,
  "phase": "fitness",
  "imp_title": "Splash-readiness gate on attack decisions",
  "imp_rank": 2,
  "imp_index": 4,
  "candidate": "cand_a1b2c3d4",
  "parent": "v3",
  "stacked_titles": [],
  "new_parent": null,
  "prior_parent": null,
  "games_played": 2,
  "games_total": 5,
  "score_cand": 1,
  "score_parent": 1,
  "updated_at": "2026-04-29T14:32:11Z"
}
```

Written via temp-file + `os.replace()` for atomicity. The `run_id` field is a uuid generated by the parent dispatcher at startup; the API filters on it (Decision D-6) so stale files from prior aborted runs cannot pollute today's dashboard.

### `GET /api/evolve/running-rounds`

Concrete response shape:

```json
{
  "active": true,
  "concurrency": 4,
  "run_id": "8a7b6c5d",
  "rounds": [
    {
      "worker_id": 0,
      "active": true,
      "phase": "fitness",
      "imp_title": "Splash-readiness gate on attack decisions",
      "candidate": "cand_a1b2c3d4",
      "parent": "v3",
      "games_played": 2,
      "games_total": 5,
      "score_cand": 1,
      "score_parent": 1,
      "updated_at": "2026-04-29T14:32:11Z"
    },
    {
      "worker_id": 1,
      "active": false,
      "phase": null,
      "imp_title": null,
      "candidate": null,
      "parent": null,
      "games_played": null,
      "games_total": null,
      "score_cand": null,
      "score_parent": null,
      "updated_at": null
    }
  ]
}
```

**Idle-slot semantics (pinning #16 from review):** the endpoint always returns exactly `concurrency` entries in `rounds`, indexed `0..concurrency-1`. Slots with no matching round file (or with `active=false` content) get the all-null skeleton above. This way the frontend grid renderer always knows how many cards to draw.

Reads `data/evolve_round_*.json` files, filters by current `run_id` (per Decision D-6), pads to `concurrency` length with idle skeletons.

---

## 6. Design Decisions

> This section is the reference for the low-level choices below. If a future maintainer needs to revisit any of these, the rationale + alternatives + reversibility are captured here.

### D-1. Default `--concurrency 1` preserves byte-identical serial behavior

**Decision:** When `--concurrency 1`, the dispatcher takes the same code path as today's serial loop — no subprocess spawning, in-process call to `run_fitness_eval`. Behavior is byte-identical to the current implementation.

**Why:** The user has accumulated soak-history baselines (run `20260423-2052` produced 2 promotions in 7h 15m at serial cadence; `20260429-0610` validated priors + WSL fixes). Diverging from the current code path at `--concurrency 1` would invalidate that history as a comparison baseline. Adding parallelism should be opt-in.

**What we considered:** A "unified" path that always uses subprocess workers, even at concurrency=1. Rejected: adds ~2-3s subprocess startup overhead per imp eval (~30s per generation × 20 generations = ~10 min wasted on a long soak), and means every regression in the current serial path is also a regression in concurrency=1, doubling the test surface unnecessarily.

**Reversibility:** Trivial. Toggle by removing the `if concurrency == 1: serial_path()` branch in `scripts/evolve.py::run_loop`.

### D-2. Per-worker scratch import via `bots.cand_<uuid>` (kill the pointer flip)

**Decision:** Each worker's candidate is identified by the explicit version string `cand_<uuid>` passed as `p1` to `run_batch`. The `bots/current/current.txt` pointer is NOT flipped during fitness eval. SC2 child processes resolve the bot via the explicit `--bot bots.cand_<uuid>` argv path.

**Why:** `bots/current/current.txt` is a process-global mutable shared by every Python process running in the repo. With N parallel workers, each trying to flip the pointer to its own candidate during fitness eval, the workers will trample each other — and any SC2 child process that imports `bots.current` between the flip and the restore gets the wrong code. The aggressive snapshot rewriter (`5f146bf`) already rewrites all `bots.<src>` references inside the candidate dir to `bots.cand_<uuid>`, so the candidate is fully self-contained without needing the pointer.

**What we considered:**
- **(b) Single mutex around the pointer flip:** workers serialize on the flip, parallelize on the games. Defeats most of the throughput win — fitness eval's wall-clock is dominated by games, but the pointer-flip-and-restore window is held for the whole eval (because the SC2 child process imports it on launch and may re-import mid-game). With a mutex you serialize the whole eval, just with more ceremony. Rejected.
- **Per-worker `bots/current/current.txt` via env-var override:** plumbing every code path that reads the pointer to consult `EVOLVE_WORKER_CURRENT_ENV` first. High blast radius, and `runner.py`'s `--serve` mode for the dashboard reads the pointer too — would need parallel-safe semantics there as well. Rejected as over-scoped.

**Reversibility:** Medium. The pointer-flip code in `run_fitness_eval`'s `try/finally:` block is ~10 lines; restoring it is a small revert. But callers that have come to rely on the no-flip behavior would also need touching. Best to commit to this direction.

### D-3. Process-level fan-out via `subprocess.Popen`, not threads

**Decision:** The dispatcher in `scripts/evolve.py` spawns `python scripts/evolve_worker.py` subprocesses, one per concurrency slot. No `threading` or `concurrent.futures.ThreadPoolExecutor`.

**Why:** Three burnysc2 / SC2 process-global mutables would each need thread-safety auditing if we went thread-level, and none have been:
1. `_install_port_collision_patch()` (`selfplay.py:77`) mutates `portpicker.pick_unused_port` globally. Idempotent per the comment but the global-state mutation isn't necessarily thread-safe under concurrent `run_batch` calls.
2. `_kill_sc2()` (`selfplay.py:769`) calls burnysc2's `KillSwitch.kill_all()`, which kills ALL SC2 children spawned by burnysc2 in this Python process — not just one worker's. In thread-level fan-out, one worker's timeout nukes every other worker's in-flight games.
3. `asyncio.run(...)` inside `run_batch` is technically supported on non-main threads (Python 3.10+), but burnysc2's signal-handler installation patches only the obvious case (`selfplay.py:163`); subtle main-thread assumptions inside burnysc2 internals haven't been audited.

Process-level isolation makes all three concerns moot — each worker has its own copy of every process-global, and crashes are contained.

Spike 3 (`scripts/spike3_launch.sh`) already validated this exact topology: 4 separate Python processes, 8 SC2_x64 children, peak RSS bounded. **We have evidence for this; we don't have evidence for threading.**

**What we considered:** Thread-level fan-out via `concurrent.futures.ThreadPoolExecutor`. Theoretical advantages: ~2-3s less Python startup per worker per generation (~10-20s wasted on a multi-hour soak); shared in-memory state simplifies IPC. Rejected because the throughput cost is small (<1% of a multi-hour run) and the unvalidated risks are large.

**Reversibility:** Hard — estimated ~1 week of rework plus a separate spike phase. The work would mean: rewriting the dispatcher; auditing every burnysc2 process-global named above; replacing the per-worker state file pattern with shared-memory; potentially patching upstream burnysc2. Not impossible, but a serious refactor. Locking process-level in is a deliberate choice to defer that complexity until/unless evidence demands it.

### D-4. Per-worker state file pattern (no locks)

**Decision:** Each worker writes `data/evolve_round_<worker_id>.json` via temp-file + `os.replace()`. The API endpoint reads all matching files with no lock, accepts that any single read might catch a partial replace (replaces are atomic on POSIX, and on Windows `os.replace()` is atomic for same-volume operations).

**Why:** Locks add coordination overhead and a deadlock surface. Per-slot files mirror the JSONL-append pattern Spike 3 used (`data/selfplay_results.parallel-{1..4}.jsonl`) — naturally concurrent-safe via filesystem semantics, and the read side just scans a glob.

**What we considered:** A single shared `evolve_running_rounds.json` written by all workers under a fcntl/msvcrt lock. Rejected: lock acquisition serializes the writes, and Windows fcntl semantics differ enough from POSIX that the abstraction would need its own fixture.

**Reversibility:** Easy. A future "merge into one file" change would be a file-system layout migration, not a code rewrite — the API endpoint already does the merge.

### D-5. Out-of-order completion: finish in-flight on budget breach

**Decision:** When the wall-clock budget is exceeded mid-fitness-phase, the dispatcher refuses to dispatch new workers but lets in-flight workers finish naturally (up to `hard_timeout` per game).

**Why:** Cancellation of an in-flight SC2 game requires SIGKILL on the SC2 process tree, which leaves replay files in an inconsistent state and may strand SC2's lobby state. Letting in-flight finish is at most `hard_timeout × games_per_eval` of overrun (~10 min worst case). The current serial implementation has the same overrun characteristic at the imp boundary; this is consistent.

**Reversibility:** Trivial. Add a `--budget-breach=cancel` flag if a future user wants hard cancellation.

### D-6. Run-ID epoch + startup cleanup of stale slot files

**Decision:** Parent dispatcher generates `run_id = uuid.uuid4().hex[:8]` at startup, writes it to every `evolve_round_<worker_id>.json` and into the `/api/evolve/running-rounds` response. On startup, the dispatcher also unlinks any pre-existing `data/evolve_round_*.json` files left by prior runs. The API endpoint filters round files by current `run_id` so a slow/stale write from an aborted run can't pollute the live dashboard.

**Why:** Without this, the API endpoint globs `data/evolve_round_*.json` and would surface files from previous aborted runs — e.g. yesterday's interrupted 8-way run leaves `evolve_round_4..7.json` on disk; today's 4-way run shows 8 cards in the dashboard, 4 of them ghosts. Belt-and-braces design: the startup unlink is the primary guard, the `run_id` filter is the safety net for races where a dying prior worker writes its file *after* our cleanup but before our first dispatch.

**What we considered:**
- **Just unlink, no `run_id`:** simpler, but a worker subprocess from a previous-but-not-quite-dead `evolve.py` invocation could re-create its slot file after our unlink. Cheap to add the filter; rules out the race definitively.
- **Lock the data dir:** lockfile on `data/evolve.lock` to prevent two `evolve.py` invocations at once. Solves the bigger "two evolves running" problem but is a separate feature; we'd want it eventually but not as part of this plan.

**Reversibility:** Trivial. Drop the `run_id` field and the startup unlink; the API falls back to glob-only.

### D-7. Worker failure taxonomy — four distinct exit modes

**Decision:** The parent dispatcher distinguishes three worker-failure modes and counts each separately in the run summary:

1. **dispatch-fail** — `subprocess.Popen()` itself raises (bad args, OS fork failure, file-not-found). Treated as a crash for the imp; the imp is evicted, retry_count incremented; logged with the Popen exception. Counted in `fitness_counts["dispatch-fail"]`.
2. **worker-crash** — Worker exited non-zero. The result JSON may or may not exist; if it does, parent reads it for partial info; otherwise treated as opaque crash. Counted in `fitness_counts["crash"]` (existing bucket).
3. **result-malformed** — Worker exited 0 but `--result-path` file is missing or fails `FitnessResult.from_json`. Treated as crash for accounting; logged with the JSON parse error. Counted in `fitness_counts["malformed"]`.
4. **worker-hang** — Worker neither completed nor exited within `hard_timeout × games_per_eval × 1.5`. Parent SIGKILLs the worker process group and treats as crash. Counted in `fitness_counts["hang"]`.

**Why:** Today's serial fitness path can't distinguish these because the eval runs in-process — a bad arg becomes a Python exception, full-stop. Subprocess fan-out introduces all four modes. Without a taxonomy the operator can't tell "evolve is broken" from "one bad imp crashed" from "host is overloaded and workers are timing out." Separating the buckets preserves debuggability of multi-hour soaks.

**What we considered:** Lumping all four into the existing `crash` bucket. Rejected: dashboard would lose signal value, and post-soak diagnosis would always require log diving.

**Reversibility:** Trivial. Collapse the buckets back into `crash` if the operator finds the granularity unhelpful.

---

## 7. Build Steps

### Step 1: Refactor pointer flip out of `run_fitness_eval`

- **Problem:** Drop the `bots/current/current.txt` pointer flip from `src/orchestrator/evolve.py::run_fitness_eval`. The candidate version is identified by its `bots.cand_<uuid>` import path; SC2 child processes get the version explicitly via `run_batch(p1=cand_name, ...)`. Update tests that assert pointer state during fitness. This step lands BEFORE any parallel dispatch — at concurrency=1 the result must still pass all existing evolve tests byte-identically. See Decision D-2 in the plan for rationale.
- **Status:** DONE (2026-04-30) — code-review gauntlet 4/4 NO ISSUES; pytest 1339+20 (+2 vs baseline); mypy strict + ruff clean. Byte-identical JSONL diff gate deferred to operator post-merge.
- **Issue:** #241
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** Pointer-flip-free `run_fitness_eval`; updated tests in `tests/test_evolve.py`; no behavior change at concurrency=1.
- **Done when:**
  - `uv run pytest tests/test_evolve.py tests/test_evolve_cli.py -q` green; mypy strict + ruff clean.
  - **Byte-identical verification:** Capture a baseline `data/evolve_results.jsonl` from a 2-generation `python scripts/evolve.py --hours 0 --pool-size 2 --no-commit` run on `master` BEFORE step 1 lands. Run the same command against the step-1 branch with the same RNG seed (add `--seed 42` if not present) and the same Claude advisor cassette/mock. Diff the two JSONL files after stripping the per-line `timestamp` and `match_id` fields. The remaining content (per-imp result, wins, parent, candidate, bucket) must match line-for-line. Document the diff command in the step's commit message.
  - Existing `tests/test_evolve.py::test_run_fitness_eval_restores_pointer_*` tests are replaced with `test_run_fitness_eval_does_not_touch_pointer` assertions (asserting `bots/current/current.txt` mtime is unchanged across the eval call).
- **Depends on:** none

### Step 2: Per-worker live-state file shape + `evolve_worker.py` CLI

- **Problem:** Add `scripts/evolve_worker.py` — a one-shot CLI taking `--parent --imp-json --worker-id --result-path` and pass-throughs for game-eval args. It calls `orchestrator.evolve.run_fitness_eval` for one imp, writes live-progress to `data/evolve_round_<worker_id>.json` via temp+rename atomically, writes the final result JSON to `--result-path`. Exits 0 on success, non-zero on crash. New module `scripts/evolve_round_state.py` (or extension to existing) for the file-write helpers used by both this worker and the dispatcher in step 3.
- **Status:** DONE (2026-04-30) — code-review gauntlet 4/4, iter 2 after gauntlet flagged narrow-catch + run_id falsy-fallback + main-return-int convention + over-pinning trims; pytest 1355+20 (+16 vs baseline); mypy strict + ruff clean. Real-SC2 e2e validation deferred to operator post-merge.
- **Issue:** #242
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** `scripts/evolve_worker.py`; round-state helpers; unit tests in `tests/test_evolve_worker.py` covering arg parsing, state-file atomicity, and crash exit codes.
- **Done when:** `python scripts/evolve_worker.py --parent v3 --imp-json <fixture> --worker-id 0 --result-path /tmp/r0.json --games-per-eval 1` runs end-to-end on a real SC2 substrate; `data/evolve_round_0.json` updates during fitness games; result JSON is valid.
- **Depends on:** Step 1

### Step 3: Concurrency-window dispatcher in `scripts/evolve.py`

- **Problem:** Add `--concurrency N` arg to `scripts/evolve.py` (default 1). When N=1, take the existing serial code path verbatim (Decision D-1: byte-identical at concurrency=1). When N>1:
  - **Startup cleanup (Decision D-6):** Generate `run_id = uuid.uuid4().hex[:8]`. Unlink any pre-existing `data/evolve_round_*.json` files. Pass `run_id` as an env var or argv to every spawned worker; workers stamp it into their slot files.
  - **Dispatcher loop:** Replace the fitness-phase `for idx in sorted(active_idxs):` loop with a `subprocess.Popen` dispatcher of size N: pop next idx, spawn `evolve_worker.py` subprocess, track in-flight set keyed by Popen handle. Poll `Popen.poll()` at 0.5s cadence on the in-flight set; on completion read `--result-path` JSON, update `per_item_state`/`pool`, dispatch next. **The parent process is the SOLE writer of `pool.json`/`run_state.json`/`evolve_current_round.json`** — workers only write their own `evolve_round_<worker_id>.json` slot file and their `--result-path` result JSON.
  - **Worker timeout (Blocker #3 fix):** Each worker carries a wall-clock cap of `hard_timeout × games_per_eval × 1.5` seconds. After that, the parent calls `Popen.kill()` (SIGKILL on the process group) and counts the failure as `fitness_counts["hang"]` per Decision D-7.
  - **Signal propagation (Blocker #2 fix):** Parent installs `signal.SIGINT` and `signal.SIGTERM` handlers that iterate the in-flight Popen set and forward the same signal to each worker. Workers in turn rely on burnysc2's existing `_kill_sc2()` exception path. The handler must be reentrant — if a second Ctrl+C arrives before workers finish dying, escalate to SIGKILL on every in-flight worker.
  - **Failure taxonomy (Decision D-7):** Catch `OSError`/`FileNotFoundError` around `subprocess.Popen()` for `dispatch-fail`. After a worker exits, distinguish: (a) exit non-zero → `crash`; (b) exit zero, result file missing or `FitnessResult.from_json` raises → `malformed`; (c) timeout → `hang`. Each gets its own counter bucket and log line; treat all four as imp-evicted-with-retry-incremented.
  - **Budget-breach (Decision D-5):** On wall-clock breach, set a "stop dispatching" flag; let in-flight workers finish naturally (up to their per-worker timeout). Don't SIGTERM in-flight workers on budget breach — only on operator interrupt.
  - **Justification comments:** Add inline `# Decision D-1` / `# Decision D-2` / `# Decision D-3` / `# Decision D-5` / `# Decision D-6` / `# Decision D-7` markers at each implementation site, referencing §6 of this plan.
- **Issue:** #243
- **Status:** DONE (2026-04-30) — code-review gauntlet 4/4 with iter 2 after gauntlet flagged SIGINT-halts-dispatcher + signal-handler-half-install-leak + in-flight-cleanup-on-exception + imp_json-stage-leak + 3 test gaps + 1 test-trim; pytest 1375+20 (+20 vs Step-2 baseline); mypy strict + ruff clean. Real-SC2 e2e (Ctrl+C test, two-workers-spawn, stale-file on real disk) deferred to operator post-merge.
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** `--concurrency` flag; dispatcher loop; signal handlers; integration tests in `tests/test_evolve_parallel.py` using a mocked-subprocess fixture (real subprocesses are too slow for unit tests). The fixture must cover: out-of-order completion (worker 3 finishes before worker 1), each of the four failure modes from D-7, signal-during-dispatch (SIGINT mid-flight), budget-breach mid-flight, and the run_id stale-file-cleanup path.
- **Done when:**
  - `uv run pytest tests/test_evolve_parallel.py -q` green.
  - `python scripts/evolve.py --concurrency 1 --hours 0 --pool-size 2 --no-commit --seed 42` produces a `data/evolve_results.jsonl` byte-equivalent (per Step 1's diff procedure) to the no-flag run from Step 1's verification baseline.
  - `python scripts/evolve.py --concurrency 2 --hours 0 --pool-size 2 --no-commit` shows two workers spawning concurrently in logs (timestamp delta < 5s between worker 0 and worker 1 startup lines).
  - **Ctrl+C test:** During the `--concurrency 2` run above, hit Ctrl+C while workers are mid-game. Within 10 seconds, both worker subprocesses + their SC2 children must exit. Verify with `ps aux | grep -E '(evolve_worker|SC2_x64)'` returning empty.
  - **Stale-file test:** Manually `touch data/evolve_round_0.json data/evolve_round_99.json` before launch; confirm both files are unlinked at parent startup.
- **Depends on:** Step 2

### Step 4: API endpoint `/api/evolve/running-rounds` + process-detection update

- **Problem:** Two surgical changes that ship together because both surface per-worker SC2 children to the dashboard:

  **(a) Add `GET /api/evolve/running-rounds` to `bots/v3/api.py`:** Reads `data/evolve_round_*.json`, **filters by current `run_id`** (read from a sentinel file `data/evolve_run_state.json::run_id` written by the parent dispatcher in Step 3), pads to `concurrency` length with idle skeletons (per §5's idle-slot semantics), returns the full shape documented in §5. Add backwards-compat shim on `/api/evolve/current-round`: returns first non-idle running round, or idle skeleton if none. **Cross-version data-dir gotcha:** the round-state files live at `data/evolve_round_*.json` (repo-root cross-version), NOT under `bots/<vN>/data/`. The endpoint must use `_evolve_dir` (cross-version), not `_data_dir` (per-version) — confusing the two has caused silent endpoint-returns-idle-skeleton bugs before. The pattern: backends with both per-version state (`training.db`) and cross-version state (`evolve/`, `ladder/`) need separate dir resolvers; sharing one resolver silently breaks either.

  **(b) Update `bots/v3/process_registry.py:13`:** Change `_OUR_CMDLINE_TAGS = ("bots.v3", "bots.current")` to also recognize `bots.cand_*` workers. Easiest implementation: keep the tuple but extend `_is_ours()` (line 18) and the label resolver (line 165) with an explicit `cmdline.contains("bots.cand_")` check. Worker SC2 children carry `--bot bots.cand_<uuid>` in their argv, so the substring match suffices. Without this, the dashboard's WSL processes panel shows worker SC2 PIDs as "Other" instead of "bots.cand_*".
- **Issue:** #244
- **Status:** DONE (2026-04-30) — code-review gauntlet 4/4, iter 2 after gauntlet trimmed asymmetric edit scope (reverted v1/v2 api.py, extended v0 process_registry for symmetry) + filled v3/v4 endpoint test gaps + v4 process_registry test gaps + full idle/legacy shape assertions; pytest 1406+20 (+31 vs Step-3 baseline); mypy strict + ruff clean. Real-SC2 e2e (curl /api/evolve/running-rounds during real parallel run) deferred to operator post-merge.
- **Flags:** `--reviewers code`
- **Produces:** New endpoint; backwards-compat shim; updated process detection; contract tests asserting endpoint shape with 0, 1, 4 round files present AND with stale (wrong-`run_id`) files present (must NOT appear in the response).
- **Done when:**
  - `curl -s http://localhost:8765/api/evolve/running-rounds` returns valid JSON matching §5's schema, with idle-slot padding to `concurrency` length.
  - Existing `/api/evolve/current-round` consumers (frontend) see no behavior change at N=1.
  - Contract test: `tests/test_api.py::test_running_rounds_filters_stale_run_id` writes 4 round files (2 with current run_id, 2 with `"stale"`) and asserts only the 2 current ones surface in the response.
  - Process-detection: a unit test in `tests/test_process_registry.py` (or extend existing) asserts `_is_ours("python -m bots.cand_a1b2c3d4 --role solo …")` returns `True` and the label resolver returns `"bots.cand_a1b2c3d4"` (or the prefix, your call).
- **Depends on:** Step 2

### Step 5: Frontend hook `useEvolveRun.ts` extension

- **Problem:** Add `runningRounds: UseApiResult<{active, concurrency, run_id, rounds}>` to `useEvolveRun()` in `frontend/src/hooks/useEvolveRun.ts`. Polls `/api/evolve/running-rounds` at 2s. **Bump `CACHE_KEY_SUFFIX` from `evolve-v4` → `evolve-v5`.** *Why bump:* `useApi` persists the last-seen response in `localStorage` keyed by `CACHE_KEY_SUFFIX`. After the schema change, a returning user's browser still has the old-shape v4 response cached; the hook destructures it before the first network round-trip and crashes React with `Cannot read properties of undefined (reading 'rounds')`. Bumping the suffix invalidates the stale cache so the new render path always sees the new shape. (This is the project's standard cache-break pattern; see also evolve gate-reduction's v2→v3 bump.) Existing `currentRound` field stays for the single-card render fallback.
- **Issue:** #245
- **Flags:** `--reviewers code`
- **Produces:** Extended hook; vitest coverage in `frontend/src/hooks/__tests__/useEvolveRun.test.ts`.
- **Done when:** `cd frontend && npm test` green; manual probe with the dashboard running at `http://localhost:3000` shows the new field populated when N>1 workers active.
- **Depends on:** Step 4

### Step 6: Frontend `EvolutionTab.tsx` grid render

- **Problem:** Refactor `frontend/src/components/EvolutionTab.tsx` so the existing single-card render becomes a per-card subcomponent rendered inside a CSS grid. Grid auto-sizes: 1 column at width<800px, 2 columns at 800-1200px, 4 columns at >1200px. At N=1 the layout must be visually identical to today (single centered card) — this is the byte-identical UX promise that pairs with Decision D-1 on the engine side. Add per-card "worker N" label badge and "phase: fitness/idle" status indicator. Keep all existing Pool / Results sections of the tab unchanged.
- **Issue:** #246
- **Flags:** `--reviewers code --ui`
- **Produces:** Grid-rendering tab; updated vitest snapshots; Playwright evidence for N=1 (single card matches today), N=2 (two cards), N=4 (four cards) rendered states.
- **Done when:** vitest green; Playwright evidence shows the three render states; N=1 visual diff vs current `master` is empty.
- **Depends on:** Step 5

### Step 7: Operator docs + skill plumbing + master-plan link update

- **Problem:** Three docs/skill changes shipped together:

  **(a) `documentation/wiki/operator-commands.md`:** Add a `--concurrency N` section documenting the flag, the smoke-gate invocation pattern (`--concurrency 2 --pool-size 2 --hours 0`), and the parallel-run idempotence note (per-worker `evolve_round_<id>.json` files clean themselves on parent startup, per Decision D-6).

  **(b) `improve-bot-evolve` skill:** The skill at `.claude/skills/improve-bot-evolve/SKILL.md` is the operator's normal invocation path; if it doesn't pass `--concurrency` through to `scripts/evolve.py`, the new capability is functionally unreachable from habitual workflow. **Decision: plumb it through.** Add an optional `--concurrency N` arg to the skill, defaulting to `1`. The skill's templated invocation appends `--concurrency $CONCURRENCY` to the `scripts/evolve.py` command line. Document the new arg in the skill body alongside `--hours` and `--games-per-eval`.

  **(c) `documentation/plans/alpha4gate-master-plan.md` §"Phase O":** Replace the "paused 2026-04-28 pending evolve parallelization" block with a one-line link to this plan + a note that Phase O is unblocked once steps 1-9 of this plan ship.

  **Operator quickstart (closes review item #13):** Add this 6-line recipe to `operator-commands.md` so a fresh-context operator can launch their first 4-way run end-to-end:

  ```
  # 1. Backend already running on port 8765? If not, start in a separate Windows shell:
  uv run python -m bots.v3.runner --serve

  # 2. Frontend already running on port 3000? If not, start in another Windows shell:
  cd frontend && npm run dev

  # 3. Launch parallel evolve from inside Ubuntu-22.04 WSL (interactive, NOT one-shot):
  wsl -d Ubuntu-22.04
  cd /mnt/c/Users/abero/dev/Alpha4Gate
  SC2_WSL_DETECT=0 nohup uv run --project . python scripts/evolve.py \
    --concurrency 4 --hours 4 --no-commit \
    > logs/evolve-parallel-$(date +%Y%m%d-%H%M).log 2>&1 &
  exit  # detach the WSL shell; the nohup'd job survives
  ```

  Open `http://localhost:3000/evolution` to watch the 4 cards populate.

- **Issue:** #247
- **Type:** code
- **Flags:** `--reviewers code`
- **Produces:** `operator-commands.md` `--concurrency` section + quickstart; `improve-bot-evolve` skill `--concurrency` arg; one-line master-plan diff.
- **Done when:** `grep -n "paused 2026-04-28" documentation/plans/alpha4gate-master-plan.md` returns no matches; `operator-commands.md` has a `--concurrency` section AND the quickstart block; the `improve-bot-evolve` skill body documents `--concurrency` (verify with `grep -n concurrency .claude/skills/improve-bot-evolve/SKILL.md`).
- **Depends on:** Step 3

### Step 8: Smoke gate — 60-second parallel cycle

- **Problem:** Producer→consumer smoke gate per the plan-feature skill's quality bar. Surfaces engine-storage-API-frontend wiring drift in 60s, before committing to a multi-hour soak. **Pass/fail of fitness outcomes is OUT OF SCOPE for this step; only "the pipeline can complete one real cycle without crashing" matters.** Concrete invocation, copy-pasteable:

  ```bash
  # Pre-reqs: backend on :8765, frontend on :3000, both already running.
  # Drop into Ubuntu-22.04 (NOT default Ubuntu — the Phase 8 setup
  # only exists in the 22.04 distro):
  wsl -d Ubuntu-22.04

  cd /mnt/c/Users/abero/dev/Alpha4Gate
  # SC2_WSL_DETECT=0 forces burnysc2 to use ~/StarCraftII (Linux SC2)
  # instead of auto-detecting and falling through to the Windows binary.
  SC2_WSL_DETECT=0 uv run --project . python scripts/evolve.py \
    --concurrency 2 --pool-size 2 --hours 0 \
    --games-per-eval 1 --no-commit
  ```

  Open `http://localhost:3000/evolution` while the run is in flight. Verify in roughly this order:
  1. Two `data/evolve_round_*.json` slots populate within 10s of launch.
  2. `curl -s http://localhost:8765/api/evolve/running-rounds | jq '.rounds | length'` returns `2`.
  3. The Evolution tab shows two progress cards side-by-side.
  4. Run completes (script exits) within ~60s.
  5. Post-run: `ls bots/cand_* 2>/dev/null | wc -l` returns 0 (per Decision D-6 cleanup).
- **Issue:** #248
- **Type:** operator
- **Flags:** (none)
- **Produces:** Operator screenshot of dashboard showing 2 cards; brief notes file `documentation/soak-test-runs/parallel-smoke-<date>.md`.
- **Done when:** All 5 verifications above pass.
- **Depends on:** Steps 1, 2, 3, 4, 5, 6, 7

### Step 9: Observation soak — 4-way parallel evolve, ≥4 hours

- **Problem:** Autonomous-system observation step required by the plan-feature skill. Unit tests prove component correctness; only a multi-hour real-workload run can prove emergent correctness (Spike 3 only ran 5 games per worker — this is the first multi-hour autonomous parallel evolve cycle). Concrete invocation:

  ```bash
  wsl -d Ubuntu-22.04
  cd /mnt/c/Users/abero/dev/Alpha4Gate

  # PREREQ: if running with --commit, verify WSL git identity is set
  # for THIS distro. Without it, every evolve commit returns 128 and
  # benched winners hit r3 retry-cap → show as 'evicted' on the
  # dashboard. The Windows host's git identity does NOT propagate.
  # (Documented after the 2026-04-30 8h soak that produced 0 promotions
  # despite ~10 strong winners.)
  git config --global user.email "$(git config --global --get user.email)"  # verify non-empty
  git config --global user.name "$(git config --global --get user.name)"

  SC2_WSL_DETECT=0 nohup uv run --project . python scripts/evolve.py \
    --concurrency 4 --hours 4 --no-commit \
    > logs/evolve-parallel-$(date +%Y%m%d-%H%M).log 2>&1 &
  echo "PID: $!"
  exit  # detach the WSL shell; the nohup'd job survives
  ```

  (Switch to `--commit` if you want to land any promotions; operator's call. The git-identity prereq above only matters for `--commit` runs.)

  Watch the run via `http://localhost:3000/evolution` and the per-worker `data/evolve_round_*.json` files. Capture peak resource usage via a 1Hz RSS sampler (mirror `scripts/spike3_launch.sh`'s pattern):

  ```bash
  while true; do
    count=$(ls /proc/[0-9]*/exe 2>/dev/null | xargs -I{} readlink {} 2>/dev/null | grep -c SC2_x64)
    echo "$(date +%H:%M:%S),$count" >> /tmp/parallel-soak-rss.csv
    sleep 1
  done &
  ```
- **Issue:** #249
- **Type:** wait
- **Flags:** (none)
- **Produces:** Soak-run notes file `documentation/soak-test-runs/evolve-parallel-<timestamp>.md` summarizing wall-clock, generations completed, promotions, peak SC2 process count (from the RSS sampler), any anomalies.
- **Done when:**
  - ≥1 generation promoted end-to-end (or all generations rolled back cleanly with no infrastructure crashes — pipeline reliability matters more than fitness outcomes for this step's success criterion).
  - Orphan `bots/cand_*` count == 0 post-run (`ls bots/cand_* 2>/dev/null | wc -l` returns `0`).
  - Peak SC2_x64 process count from the RSS sampler ≤ 8 (confirms `4 × 2` worker topology held; spikes above 8 indicate worker-leak).
  - `data/evolve_run_state.json` and `data/evolve_pool.json` remain readable (`python -c "import json; json.load(open('data/evolve_run_state.json'))"` exits 0) throughout the run AND after.
  - No `Traceback` in evolve logs that wasn't already present in the serial path (compare `grep -c Traceback logs/evolve-parallel-*.log` against a same-length serial baseline).
- **Depends on:** Step 8

---

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| burnysc2 process-global state under N concurrent subprocess workers | Low — Spike 3 already validated this exact topology with 4 workers on WSL | None needed beyond observation step 9 |
| 4 × 2 = 8 SC2 processes exceed WSL host's RAM headroom on weaker boxes | Medium — Spike 3 measured peak total RSS but didn't characterize ceiling | Default to `--concurrency 4` but expose flag; Step 9's RSS sampler captures peak total + peak per-process so this is empirically known after first soak |
| `bots/current/current.txt` pointer removal breaks downstream consumers we haven't found | Low — grep confirms `runner.py --serve` and the test suite are the only readers | Step 1's byte-identical verification (Step 1 done-when) catches functional regressions; if a hidden consumer surfaces, it needs the explicit-version path treatment too |
| `os.replace()` atomicity on Windows for cross-volume cases | Low — WSL `/mnt/c` is the same volume; same-volume `os.replace()` is atomic on Windows | Document in code comment; if it bites, fall back to `tempfile + os.rename` with retry-on-EACCES (same pattern that fixed the dashboard atomic-replace race) |
| Frontend grid layout looks bad at edge widths (e.g. N=3) | Low — visual nit only | Step 6 snapshots cover N=1,2,4 explicitly; N=3 is graceful-degradation |
| Out-of-order completion exposes a regression in `pool_state` update logic | Medium — current code assumes sorted iteration | Step 3's mocked-subprocess test fixture deliberately delivers results in reverse order; addressed |
| Worker subprocess cold-start cost (~2-3s × N workers per generation) eats throughput | Low — even at 20 generations × 4 workers × 3s = 240s wasted on a 4-hour soak (~1.7%) | Acceptable cost; if it bites, persistent worker pool is a future optimization |
| Stale round-state files from prior runs pollute the live API (review #1) | High if unaddressed → Low after Decision D-6 | Decision D-6 + Step 3 startup unlink + Step 4 `run_id` filter + Step 4 contract test |
| Operator Ctrl+C orphans 4 worker Pythons + 8 SC2 instances (review #2) | High if unaddressed → Low after Step 3 signal handlers | Step 3 SIGINT/SIGTERM propagation; Step 3 done-when includes the Ctrl+C verification test |
| Worker subprocess hangs indefinitely with no exit (review #3) | Medium — possible after burnysc2 internal deadlock under concurrent load | Step 3 enforces parent-side `hard_timeout × games_per_eval × 1.5` cap, SIGKILL the worker process group on breach, count as `fitness_counts["hang"]` per Decision D-7 |
| Conflated worker failure modes hide diagnosability (review #4) | Medium — would surface as "evolve broken" without enough info to act | Decision D-7 separates `dispatch-fail` / `crash` / `malformed` / `hang` into distinct counters and log lines |
| `improve-bot-evolve` skill doesn't expose `--concurrency`, making the feature unreachable from habitual workflow (review #6) | Resolved | Step 7(b) plumbs `--concurrency` through the skill |

---

## 9. Testing Strategy

### Unit + integration tests added per step

- **Step 1:** Existing evolve tests must pass byte-identically post pointer-flip removal. Specifically: `test_run_fitness_eval_restores_pointer_on_success` and `test_run_fitness_eval_restores_pointer_on_crash` are replaced with `test_run_fitness_eval_does_not_touch_pointer` (asserts `bots/current/current.txt` mtime is unchanged across the eval call). The byte-identical claim is verified empirically per Step 1's done-when: capture a 2-generation `data/evolve_results.jsonl` baseline on master with `--seed 42` BEFORE the change, run the same command after, diff the two files line-for-line after stripping `timestamp` + `match_id` fields. The diff must be empty.
- **Step 2:** `tests/test_evolve_worker.py` — arg parsing; state-file atomicity (a `tempfile + os.replace` write is observed by a concurrent reader as either fully old or fully new content, never partial); crash exit codes for each failure path; JSON output schema validation against `FitnessResult.from_json`.
- **Step 3:** `tests/test_evolve_parallel.py` — mocked-subprocess dispatcher fixture covering: out-of-order completion (worker 3 finishes before worker 1, pool_state/per_item_state still update correctly); each of the four failure modes from Decision D-7 (`dispatch-fail`, `crash`, `malformed`, `hang`); SIGINT-during-dispatch propagation (parent signals all in-flight workers, second SIGINT escalates to SIGKILL); budget-breach mid-flight (in-flight finishes, no new dispatch); the `run_id` stale-file-cleanup path (pre-existing `evolve_round_*.json` files are unlinked at startup); concurrency=1 byte-identical assertion mirroring Step 1's diff procedure.
- **Step 4:** `tests/test_api.py` extension — endpoint shape with 0/1/4 round files; backwards-compat shim returns expected single-round at N=1; `test_running_rounds_filters_stale_run_id` writes 4 round files (2 with current run_id, 2 with `"stale"`) and asserts only the 2 current ones surface. `tests/test_process_registry.py` — extend to cover `_is_ours("python -m bots.cand_a1b2c3d4 ...")` returning `True`, and the label resolver returning the candidate prefix.
- **Step 5:** `frontend/src/hooks/__tests__/useEvolveRun.test.ts` — new `runningRounds` field populated; cache-key bump prevents stale-v4-shape crash (regression fixture: seed `localStorage` with the old v4-shape response, mount the hook, assert no `Cannot read properties of undefined` exception, assert the new key fetches a fresh response).
- **Step 6:** vitest snapshots N=1, N=2, N=4 + Playwright evidence pinned per the `--ui` flag. The N=1 snapshot must be visually identical to a master-baseline screenshot of the current single-card render — automated diff via Playwright's image comparison.

### Existing tests at risk

- `tests/test_evolve.py::test_run_fitness_eval_restores_pointer_*` — must be replaced (Step 1).
- `tests/test_api.py` tests for `/api/evolve/current-round` — backwards-compat shim must keep them green.
- `frontend/src/hooks/__tests__/useEvolveRun.test.ts` — cache-key bump may invalidate snapshot fixtures.

### End-to-end verification

The smoke gate (Step 8) and observation soak (Step 9) ARE the end-to-end verification. Both are operator/wait steps because the build-step infrastructure can't reliably stand up a 4-way SC2 environment in CI. This matches the project's existing pattern: `tests/test_*` covers component contracts, and `documentation/soak-test-runs/` covers emergent behavior under real workload.

### Validation target (from issue #235)

> 4-way parallel evolve cycle on WSL completes ≥1 promotion (validates the engine + storage + dashboard end-to-end)

Step 9's `done when` clause is the canonical pass criterion.

---

## Kill Criterion

If Step 9's 4-hour soak shows the parent process accumulates state corruption (run_state.json or pool.json become unreadable) or the per-worker subprocess approach exhibits unfixable burnysc2-side conflicts, abandon process-level fan-out and either revisit thread-level (with a separate spike phase to audit the three burnysc2 process-globals named in Decision D-3) OR accept evolve as a single-host serial system and explore multi-host fan-out instead. Under no circumstances do we ship parallel evolve that's less reliable than today's serial path — Phase O can wait.

---

## Rollback (per step)

- **Steps 1-3 (engine):** `git revert` of the merge commit. The evolve algorithm itself is unchanged; only the dispatch shape differs. Evolve continues to function serially after revert.
- **Steps 4-5 (API + hook):** `git revert`. The endpoint is additive; removing it only affects the new dashboard surface.
- **Step 6 (frontend grid):** `git revert`. The single-card render returns.
- **Step 7 (docs):** `git revert`. No behavior change.
- **Steps 8-9 (smoke + soak):** Operator/wait steps produce notes files only; no rollback required.

The whole plan is structured so each step is independently revertible, and steps 1-3 are the only ones with semantic risk. Steps 4-7 are additive plumbing.
