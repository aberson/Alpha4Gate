---
name: improve-bot-evolve
description: Autonomous (1+λ)-ES sibling-tournament improvement loop. Generates a pool of Claude-proposed improvements, plays them off in pairs (A vs B), gates winners against the current parent, and promotes decisive winners — repeating for hours until the pool or wall-clock budget is exhausted. Designed for overnight unattended runs.
user-invocable: true
argument: Optional flags only — no free-text suggestion needed. Flags: `--pool-size N` (default 10), `--ab-games N` (default 10), `--gate-games N` (default 5), `--hours N` (default 4), `--map NAME` (default Simple64), `--no-commit` (dev/test only), `--seed N` (RNG seed for sampling), `--results-path PATH`, `--pool-path PATH`, `--state-path PATH`, `--run-log PATH`.
required-env: SC2 installed at `C:/Program Files (x86)/StarCraft II/`, `claude` CLI on PATH and authenticated (OAuth subscription token OR `ANTHROPIC_API_KEY` — whichever the CLI is set up with).
---

# /improve-bot-evolve

Autonomous evolutionary improvement loop: Claude proposes a pool of candidate improvements targeting the current parent version; pairs are sampled and played off head-to-head; decisive A-vs-B winners are gated against the parent; gate passers are promoted to become the new parent. Repeats until the pool or wall-clock budget is spent.

**Design goal:** `/improve-bot-evolve` in a fresh context window with no additional input should produce a measurably stronger parent version after a few hours, with every promotion recorded as a `[evo-auto]` commit that passes the sandbox pre-commit hook.

**Zero-input contract:** When invoked with no flags, this skill MUST NOT ask the user any questions. It proceeds immediately with safe defaults (pool size 10, A-vs-B 10 games, gate 5 games, 4-hour budget, Simple64). Every phase is designed to resolve ambiguity autonomously — pick reasonable defaults, log the choice, and keep going. The only exception is if a hard pre-flight failure makes the run impossible (e.g., SC2 not installed, `claude` CLI missing or unauthenticated).

**Relationship to `/improve-bot-advised`:** These two skills are siblings with different inner mechanics:
- `/improve-bot-advised` picks one improvement, applies it, validates win-rate, commits — linear.
- `/improve-bot-evolve` generates a pool of 10, plays them pairwise, promotes only tournament-gated winners — parallel/competitive.

The outer phase shape (pre-flight → seed → loop → decision → report) is identical. The two skills must NEVER run concurrently (they both mutate `bots/current/current.txt`). The pre-flight explicitly refuses to start if an advised run is in progress.

---

## Flags

| Flag | Type | Default | Purpose |
|------|------|---------|---------|
| `--pool-size` | int | `10` | Number of improvements Claude generates up-front |
| `--ab-games` | int | `10` | Games per A-vs-B batch |
| `--gate-games` | int | `5` | Games in the candidate-vs-parent safety gate |
| `--hours` | float | `4.0` | Wall-clock budget. `0` disables the check (test-only). |
| `--map` | str | `Simple64` | SC2 map name |
| `--no-commit` | flag | off | Skip the EVO_AUTO commit on promote. Dev/test only — production runs always commit. |
| `--seed` | int | none | RNG seed for pair sampling. Default nondeterministic. |
| `--results-path` | path | `data/evolve_results.jsonl` | JSONL log of every `RoundResult` |
| `--pool-path` | path | `data/evolve_pool.json` | Pool state file (dashboard reads this) |
| `--state-path` | path | `data/evolve_run_state.json` | Run state file (dashboard reads this) |
| `--run-log` | path | `documentation/soak-test-runs/evolve-<ts>.md` | Human-readable markdown run log |
| `--return-loser` | flag | off | **Reserved for v2** — returns the AB loser to the pool. Raises `NotImplementedError` in v1. |

**Flag resolution (no user interaction — resolve automatically):**
- No flags at all → defaults above. Proceed immediately.
- `--hours 0` → wall-clock disabled; the loop can still stop on pool-exhaustion. Log the override.
- `--return-loser` → hard fail with exit 2 (`NotImplementedError`). Do NOT silently ignore; the user explicitly asked for an unimplemented feature.
- Conflicting results/pool/state paths that point at an in-flight advised run's directory → refuse and stop (pre-flight check 0.2 below).

---

## Phase 0: Bootstrap

### 0.0 Run-start banner

Print this banner at the start of every run (before any other output):

```
🔒 Sandbox active (EVO_AUTO scope)
  I can edit:   bots/** (any version dir, including new ones from snapshot_current)
  I cannot edit: src/orchestrator/, pyproject.toml, tests/, frontend/, scripts/
  Commit marker: [evo-auto] on its own line in the commit message body
  Env: EVO_AUTO=1 must be set in the commit subprocess env (scripts/evolve.py handles this)
  SC2 required for mirror seed, A-vs-B batches, and parent-safety gate
```

The banner differs from the advised banner in three ways:
- Scope is `bots/**` (any version), not `bots/current/**` (a single version).
- Commit marker is `[evo-auto]`, not `[advised-auto]`.
- Env var is `EVO_AUTO=1`, not `ADVISED_AUTO=1`.

Both env vars must NOT be set simultaneously — the sandbox hook fails loudly on that conflict. `scripts/evolve.py` unsets `ADVISED_AUTO` defensively in its commit subprocess env.

### 0.1 Parse flags

Parse all flags. Apply the flag resolution rules from the table above — never ask the user, always resolve automatically. Log any overrides to `$LOGFILE`.

### 0.2 Pre-flight checks

All pre-flight checks resolve autonomously. Only stop if the situation is truly unrecoverable.

```bash
# Git state
git status --porcelain                # check cleanliness
git rev-parse --abbrev-ref HEAD       # check branch
git fetch origin && git status -sb    # check sync

# SC2
ls "C:/Program Files (x86)/StarCraft II/Versions/"

# Claude CLI on PATH + authenticated (OAuth or API key — either works)
claude --version && claude -p "ping" --model haiku --output-format text --no-session-persistence | head -1

# Tools
uv run pytest --co -q 2>&1 | tail -1              # tests discoverable
uv run ruff check . --quiet                        # lint clean
uv run mypy src --quiet                            # types clean

# Concurrent-run interlock — refuse if an advised run is active
python -c "
import json, sys
from pathlib import Path
p = Path('data/advised_run_state.json')
if not p.exists():
    sys.exit(0)
state = json.loads(p.read_text())
if state.get('status') == 'running':
    print(f'Advised run {state.get(\"run_id\")} is active; evolve will not start.')
    sys.exit(1)
"
```

**Autonomous resolution for common issues:**
- **Git dirty (untracked files only, no conflicts):** `git stash --include-untracked -m "evolve-preflight-$RUN_TS"`. Log the stash. Proceed.
- **Git dirty (staged/modified tracked files):** Commit them with message `"chore: auto-stash before evolve run $RUN_TS"`. Log the commit SHA. Proceed.
- **Not on master:** `git checkout master`. If that fails (uncommitted changes), stash first, then checkout. Log.
- **Behind origin/master:** `git pull --rebase origin master`. If conflicts, STOP — this is unrecoverable without human judgment.
- **Quality gates fail (pytest/mypy/ruff):** Log failures but proceed anyway — pre-existing failures are the baseline, not a blocker. The skill only gates its own commits against these.
- **SC2 not found:** STOP — this is a hard requirement, log and exit.
- **`claude` CLI missing or unauthenticated:** STOP — pool generation cannot run without it. Log and exit. Operator should run `claude setup-token` (subscription) or set `ANTHROPIC_API_KEY` (whichever auth mode the CLI is configured for).
- **Advised run active:** STOP — never run two auto-commit skills concurrently against `bots/current/current.txt`. Wait for the advised run to finish, or stop it via its dashboard control first.
- **Stale evolve run (state file says `running` but no process holds it):** inspect `data/evolve_run_state.json.started_at`. If older than 12 hours, rewrite `status: "stopped"` and proceed. If fresher, assume another operator is running evolve and abort.

### 0.3 Record baseline

```bash
LOOP_START=$(date +%s)
RUN_TS=$(date -d @$LOOP_START +%Y%m%d-%H%M)

# Auto-disambiguate log filename
LOGFILE="documentation/soak-test-runs/evolve-$(date +%F).md"
i=0; while [ -e "$LOGFILE" ]; do i=$((i+1)); suf=$(printf "\\x$(printf '%x' $((96+i)))"); LOGFILE="documentation/soak-test-runs/evolve-$(date +%F)-$suf.md"; done
```

Capture baseline metrics:
- Current parent version (from `bots/current/current.txt`)
- Parent's recent win rate (from most recent `data/training.db` entries, if any)
- Current Elo rating (from `src/orchestrator/ladder.py`, if any)
- Git SHA

Write run header to `$LOGFILE`:
- Flags, pool size, batch sizes, hours budget, map
- Baseline metrics
- `LOOP_START` timestamp
- Git SHA
- Parent-start version name

### 0.4 Start systems

**Backend lifecycle rule:** Start exactly ONE `--serve` process at the beginning of the run. This process must stay alive for the entire run so the Evolution dashboard tab can monitor progress. **Never start a second `--serve` process.** `scripts/evolve.py` does NOT auto-spawn a backend — we do it once here, explicitly.

```bash
export PYTHONUNBUFFERED=1

# Check if backend is already running — only start if port is free
python -c "import socket; s=socket.socket(); r=s.connect_ex(('127.0.0.1',8765)); s.close(); exit(0 if r!=0 else 1)" && {
  DEBUG_ENDPOINTS=1 PYTHONUNBUFFERED=1 uv run python -m bots.v0.runner --serve 2>&1 &
  BACKEND_PID=$!
  echo "Backend started (PID $BACKEND_PID)"
} || echo "Backend already running on port 8765, skipping"
```

The evolve loop does NOT use `--daemon`; each round is driven by `scripts/evolve.py` calling `run_round()`, which shells out to the selfplay runner per batch. Do NOT start a daemon process during an evolve run.

### 0.5 Seed game (sanity check)

Run 1 mirror game (parent-vs-parent) to verify the full subprocess self-play pipeline works before entering the expensive pool generation. If it fails, stop and report — don't burn the budget on a broken setup.

```bash
uv run python scripts/selfplay.py --p1 "$(cat bots/current/current.txt)" --p2 "$(cat bots/current/current.txt)" --games 1 --map "$MAP" 2>&1 | tee -a "$LOGFILE"
```

If this returns non-zero or no SelfPlayRecord, STOP.

### 0.6 Dashboard check

Run `/a4g-dashboard-check` to verify all dashboard tabs render correctly and reflect the post-seed-game state. The Evolution tab specifically should show "idle" with no active run. If the check reports any `✗` (broken) tabs, stop and fix before proceeding. `⚠` (warning) issues are logged but not blockers.

### 0.7 Baseline tag

```bash
git tag "evolve/run/$RUN_TS/baseline"
git push origin "evolve/run/$RUN_TS/baseline"
```

This tag is the restore point for a full revert of the run.

**Write initial run state** (signals to the dashboard that a run is starting):
```
data/evolve_run_state.json = {
  "status": "running",
  "parent_start": "<current parent>",
  "parent_current": "<current parent>",
  "started_at": "<RUN_TS iso>",
  "wall_budget_hours": 4.0,
  "rounds_completed": 0,
  "rounds_promoted": 0,
  "no_progress_streak": 0,
  "pool_remaining_count": 0,
  "last_result": null
}
```

`scripts/evolve.py` handles this write via `write_run_state()`; the operator/agent does not write it manually.

---

## Phase 1: Seed + Pool

### 1.1 Invoke the evolve runner

The entire pool generation + round loop is owned by `scripts/evolve.py`. The skill's job is to invoke it with the right flags, monitor its state files, and handle its exit.

```bash
uv run python scripts/evolve.py \
    --pool-size 10 \
    --ab-games 10 \
    --gate-games 5 \
    --hours 4 \
    --map Simple64 \
    2>&1 | tee -a "$LOGFILE"
```

The script internally:
1. Runs 3 parent-vs-parent mirror games via `orchestrator.selfplay.run_batch`.
2. Calls `orchestrator.evolve.generate_pool(parent, pool_size=10, ...)` which prompts Claude (Opus by default) with the mirror summary, source tree listing, and guiding principles. Claude returns exactly 10 `Improvement` records.
3. Writes `data/evolve_pool.json` with all 10 items marked `status: "active"`.

If pool generation fails (Claude returned malformed JSON, rate limit, API key missing), the script writes `status: "failed"` to `data/evolve_run_state.json` and exits 1. See **Troubleshooting** below.

### 1.2 Pool-state file schema

After Phase 1 completes, `data/evolve_pool.json` looks like:

```json
{
  "generated_at": "2026-04-19T21:33:00+00:00",
  "parent": "v5",
  "pool": [
    {
      "rank": 1,
      "title": "Chrono boost auto-cast",
      "type": "dev",
      "description": "...",
      "principle_ids": [4],
      "expected_impact": "high",
      "concrete_change": "Add chrono_boost_manager.py with priority queue: probes > warp gate > upgrades",
      "status": "active"
    },
    ...
  ]
}
```

Status transitions (managed by `scripts/evolve.py`, not by the agent):
- `"active"` → improvement is still eligible for sampling
- `"consumed-won"` → this improvement won its A-vs-B and was promoted
- `"consumed-lost"` → lost A-vs-B, OR won A-vs-B but failed the parent gate
- `"consumed-tie"` → A-vs-B tied, OR the round crashed

---

## Phase 2: Evolution loop

Each iteration of this phase = one round. `scripts/evolve.py` runs this loop internally; the skill's job is to let it run and read the state files.

### 2.1 Sample (inside `scripts/evolve.py`)

Collect indexes of pool items with `status: "active"`. If fewer than 2 remain, stop (pool-exhausted). Otherwise sample 2 distinct indexes uniform-at-random (via `random.Random.sample`).

### 2.2 Execute one round

`orchestrator.evolve.run_round(parent, imp_a, imp_b, ab_games=10, gate_games=5, map_name="Simple64")` does:

1. `snapshot_current()` twice — produces `candidate_a` and `candidate_b` version dirs.
2. `apply_improvement(candidate_a, imp_a)` and `apply_improvement(candidate_b, imp_b)`.
3. `run_batch(candidate_a, candidate_b, 10, map_name)` — the A-vs-B tournament.
4. If one side strictly wins (>5/10), run the parent-safety gate: `run_batch(winner, parent, 5, map_name)`.
5. If the gate passes (≥3/5), rewrite `bots/current/current.txt` to point at the winner. Return a `RoundResult` with `promoted=True`.
6. Otherwise, restore the pointer to `parent` and return `RoundResult(promoted=False, reason=...)`.

Both candidate directories are kept on disk even on discard so `git log` shows the full exploration history. Only `current.txt` is rolled back.

### 2.3 Update state files

After every round, `scripts/evolve.py` writes (atomically via temp+rename):

- **`data/evolve_results.jsonl`** — appends one JSON line per `RoundResult`. Durable even if the process crashes mid-write of the state file.
- **`data/evolve_pool.json`** — updated with the two improvements' new consumed statuses.
- **`data/evolve_run_state.json`** — overwritten with the new `rounds_completed`, `rounds_promoted`, `no_progress_streak`, `pool_remaining_count`, and a `last_result` snapshot.

Atomic writes use `tmp.replace(path)`; dashboard readers never see a half-written file.

### 2.4 Promote + commit (only on `result.promoted == True`)

`scripts/evolve.py` stages `bots/<winner>/` plus `bots/current/current.txt` and commits with an env that sets `EVO_AUTO=1` and unsets `ADVISED_AUTO`:

```
evolve: round N promoted <imp_title>

[evo-auto]
```

The `[evo-auto]` marker must appear on its own line in the commit body — the sandbox hook (`scripts/check_sandbox.py`) reads the env var, not the marker, but the marker is the human-readable audit trail. If the commit fails (e.g., sandbox hook blocks it), the script logs a WARNING but does NOT crash — the operator reconciles out-of-band.

### 2.5 Discard

On `result.promoted == False`, no commit is created. The candidate directories remain on disk (untracked). `no_progress_streak` increments by 1.

---

## Phase 3: Loop decision (stop conditions)

The `scripts/evolve.py` loop exits when ANY of the following trips, checked in priority order at the top of each round:

1. **Wall-clock budget exceeded** — `elapsed_seconds >= hours * 3600`. Disabled when `--hours 0`.
2. **Pool exhausted** — fewer than 2 items with `status: "active"` remain.
3. **Dashboard stop request** — `data/evolve_run_control.json.stop_run == true` (see Dashboard Control Panel Bridge).
4. **Dashboard pause request** — `data/evolve_run_control.json.pause_after_round == true` — the script finishes the current round, then enters a paused state. Resume by clearing the flag.

`stop_reason` in the final run log will be one of `"wall-clock"`, `"pool-exhausted"`, `"dashboard-stop"`, or `"dashboard-pause-timeout"`.

`no_progress_streak` is still tracked in the run state (so the dashboard can surface it as a "warning: many discards in a row" signal) but it is **not** a stop condition — crashed or discarded rounds can't silently truncate a run.

---

## Phase 4: Morning Report

Write the final report to `$LOGFILE` and print to stdout.

### 4.1 Report contents

```markdown
# Evolve Run: $RUN_TS

## Summary
- **Duration:** Xh Ym (budget: Zh)
- **Parent (start):** v5
- **Parent (end):** v9
- **Pool size:** 10
- **Rounds completed:** 7
- **Rounds promoted:** 3
- **Stop reason:** wall-clock / pool-exhausted / no-progress / dashboard-stop

## Promotion chain
v5 → v6 (round 2, "Chrono boost auto-cast")
v6 → v8 (round 4, "Forward pylon warp-in")
v8 → v9 (round 7, "Archon morph on 4 HTs")

## Rounds
| # | candidate A | candidate B | A-B | gate | outcome | reason |
|---|---|---|---|---|---|---|
| 1 | cand_x | cand_y | 5-5 | — | discarded-tie | AB tied 5/10 |
| 2 | cand_z | cand_w | 7-3 | 4-1 | promoted | gate passed |
| ... |

## Consumed improvements
- "Chrono boost auto-cast" — consumed-won (round 2)
- "Early scout reward" — consumed-lost (round 2, lost AB 3-7)
- ...

## Still active (pool remainder)
- "Reduce probe cut on third base"
- ...

## Suggested next action
- "Run another evolve cycle against v9 with --pool-size 10"
- "Review the 3 still-active improvements and promote one manually if they look high-value"
- etc.

## Tag cheatsheet
git tag -l "evolve/run/$RUN_TS/*"
git log "evolve/run/$RUN_TS/baseline..master" --oneline
git reset --hard "evolve/run/$RUN_TS/baseline"   # NUKE whole run
```

### 4.2 Final tag

```bash
git tag "evolve/run/$RUN_TS/final"
git push origin "evolve/run/$RUN_TS/final"
```

### 4.3 GitHub issue

Create an umbrella issue labeled `evolve-run` with the summary + promotion chain for phone-readable progress tracking.

### 4.4 Final cleanup

Shut down the API-only backend and kill any orphaned processes from the run:

```bash
# 1. Graceful shutdown via API
curl -s -X POST http://localhost:8765/api/shutdown || true
sleep 2

# 2. Force-kill any remaining Python/uv processes from the run
powershell.exe -Command "Get-WmiObject Win32_Process | Where-Object { \$_.CommandLine -match 'bots.v0.runner|scripts/evolve.py|scripts/selfplay.py' -and \$_.CommandLine -notmatch 'SC2' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }"

# 3. Kill monitoring helpers
powershell.exe -Command "Get-Process -Name tail,grep,sleep -ErrorAction SilentlyContinue | Stop-Process -Force"

# 4. Verify port 8765 is free
python -c "import socket; s=socket.socket(); r=s.connect_ex(('127.0.0.1',8765)); s.close(); exit(0 if r!=0 else 1)" || echo "WARNING: port 8765 still occupied after final cleanup"
```

Do NOT kill `SC2_x64.exe`. Use `TaskStop` on every background Bash task from this conversation.

---

## Dashboard Control Panel Bridge

The dashboard has an **Evolution** tab (Phase 9 Step 6) that monitors and controls this loop via two JSON files in `data/`.

### State file: `data/evolve_run_state.json` (skill writes)

`scripts/evolve.py` writes this file at every phase boundary via `write_run_state()`. The agent does NOT write it manually.

```json
{
  "status": "running",
  "parent_start": "v5",
  "parent_current": "v7",
  "started_at": "2026-04-19T21:33:00+00:00",
  "wall_budget_hours": 4.0,
  "rounds_completed": 3,
  "rounds_promoted": 1,
  "no_progress_streak": 2,
  "pool_remaining_count": 4,
  "last_result": {
    "round_index": 3,
    "candidate_a": "cand_abc",
    "candidate_b": "cand_def",
    "imp_a_title": "Chrono boost auto-cast",
    "imp_b_title": "Forward pylon warp-in",
    "ab_score": [5, 5],
    "gate_score": [0, 0],
    "outcome": "discarded-tie",
    "reason": "discarded: ab tie 5-5"
  }
}
```

**Status transitions:**
- `"running"` — written on startup and after every round
- `"completed"` — written when the loop exits cleanly (any stop reason)
- `"failed"` — written if pool generation raises (pre-round failure)
- `"stopped"` — written if the skill is interrupted mid-run by a dashboard stop

### Pool file: `data/evolve_pool.json` (skill writes)

Written once at the end of Phase 1, then rewritten after every round with updated `status` fields per item. Dashboard reads this to render the 10-item pool with consumed/active markers.

### Results file: `data/evolve_results.jsonl` (skill appends)

One JSON line per `RoundResult`, appended atomically after each round. Durable across process crashes — if the state file write fails, the JSONL still has the result.

### Control file: `data/evolve_run_control.json` (skill reads)

`scripts/evolve.py` polls this file at the top of each round. Dashboard writes it.

```json
{
  "stop_run": false,
  "pause_after_round": false,
  "updated_at": "2026-04-19T22:00:00+00:00"
}
```

**How to apply each control signal:**

- **`stop_run: true`** — Finish the current round (don't abort mid-batch), then skip straight to Phase 4 (Morning Report). `stop_reason` in the run log becomes `"dashboard-stop"`. Log: "Dashboard requested stop".
- **`pause_after_round: true`** — Finish the current round, then enter a polling pause: sleep 30s, re-read the control file; resume when the flag clears, or exit with `stop_reason: "dashboard-pause-timeout"` if the pause exceeds 1 hour. Log: "Dashboard requested pause".

**If the control file does not exist or is unreadable, proceed with current settings — never block on missing control signals.**

---

## What NOT to do

- **DO NOT run concurrently with `/improve-bot-advised`.** Both skills auto-commit changes to `bots/current/current.txt`. Pre-flight 0.2 refuses to start if `data/advised_run_state.json` shows `status: "running"`. If you see that file stale (process long dead), manually rewrite its `status` to `"stopped"` before retrying evolve.
- **DO NOT manually commit during a run.** The pre-commit hook allows `EVO_AUTO=1` commits to touch `bots/**` — if you commit with `EVO_AUTO=1` unset, the hook passes through (human mode), but any concurrent `scripts/evolve.py` commit may collide on `bots/current/current.txt`. Wait for the run to exit before manual commits.
- **DO NOT `kill` the SC2 process** (per `feedback_sc2_process_management.md`). Only restart the Python daemon/runner processes. SC2 keeps state between games and is expensive to relaunch.
- **DO NOT set both `EVO_AUTO=1` and `ADVISED_AUTO=1`** in the same commit env. The sandbox hook fails that commit loudly. `scripts/evolve.py` defensively `pop()`s `ADVISED_AUTO` from the commit subprocess env.
- **DO NOT start a `--daemon` backend during an evolve run.** The evolve loop drives games directly via `scripts/selfplay.py`; a concurrent daemon queues its own games and fights for port 8765 and SC2 slots.
- **DO NOT modify `scripts/evolve.py` or `src/orchestrator/evolve.py` mid-run.** The running subprocess has already loaded the module; edits only take effect on the next invocation.
- **DO NOT use `--no-commit` for production runs.** It disables the promotion commit entirely, so the promoted version lives only in the working tree until something overwrites it.
- **DO NOT lower `--gate-games` below 5 unless you're running a fast smoke test.** The safety gate exists because some Claude-generated improvements are actively harmful; a 3-game gate is statistically too noisy to catch them.

---

## Troubleshooting

### Pool generation fails ("evolve: pool generation failed")

Check `$LOGFILE` for the Python exception. Common causes:

- **Claude returned malformed JSON.** `orchestrator.evolve._parse_claude_pool` strips markdown fences and retries once if the item count is short; a second short/malformed response raises `ValueError`. Re-run the script — the retry usually succeeds on transient JSON hiccups.
- **`claude` CLI missing or unauthenticated.** `_default_claude_fn` shells out to `claude -p ... --model opus --output-format text --no-session-persistence` (matches `bots/v0/claude_advisor.py`). If the binary is not on PATH you get `RuntimeError("claude CLI not found on PATH...")`; if it's unauthenticated you get a non-zero exit code with the CLI's error message. Run `claude --version` to confirm it's installed, then `claude -p "ping" --model haiku --output-format text --no-session-persistence` to confirm auth.
- **Rate limit from Anthropic.** Wait a few minutes; the prompt is a single call so retries are cheap.
- **Mirror games crashed.** If the 3 parent-vs-parent games fail, `generate_pool` still has the failure summary in its prompt, but if no games completed at all the prompt is degenerate. Check `data/evolve_results.jsonl` — if empty, re-run the seed-game step in Phase 0.5 manually to diagnose SC2/subprocess issues before retrying evolve.

### First round crashes immediately

- **SC2 not running.** Start SC2 first, then re-run. The evolve script does NOT auto-launch SC2 (it's too heavyweight to babysit).
- **Port collision on the self-play subprocess.** Check for orphaned Python processes holding 127.0.0.1:8765 or the self-play ports:
  ```bash
  powershell.exe -Command "Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue"
  ```
  Kill any stragglers with Phase 4.4's cleanup block, then re-run.
- **`bots/<candidate>/` already exists.** If a prior run crashed mid-round, candidate dirs may linger. `snapshot_current()` picks a fresh name, so this is usually a disk-space or permissions issue, not a name collision.

### Dashboard Evolution tab shows stale state

Atomic writes (`tmp.replace(path)`) prevent half-written files, so stale state is almost always a **cache-key mismatch in the frontend** (see `feedback_useapi_cache_schema_break.md`). When `scripts/evolve.py` changes the `evolve_run_state.json` schema, the frontend's `useApi` cache key for `/api/evolve/state` must be bumped to invalidate the cached old shape. Check `frontend/src/components/EvolutionTab.tsx` for the `cacheKey` argument.

If the cache key is current but the tab still shows stale data, verify the backend is actually reading `data/evolve_run_state.json` — a wrong CWD silently breaks this (see `feedback_backend_wrong_cwd_silent.md`). The API endpoint should resolve the path via an absolute repo-root base, not a relative path.

### Pre-commit hook blocks the evolve commit

The sandbox hook checks `EVO_AUTO=1` in the env, not in the commit message. If the commit fails with "SANDBOX VIOLATION — commit blocked", the commit subprocess didn't inherit the env var. Confirm `scripts/evolve.py`'s `git_commit_evo_auto` passes `env=env` where `env["EVO_AUTO"] = "1"`. If the hook is blocking a manual recovery commit, either:

- Run the commit with `EVO_AUTO=1 git commit -m "... [evo-auto]"` from the shell, OR
- Skip the autonomous scope entirely: unset `EVO_AUTO` and make a human commit (which passes through unconditionally).

Never commit with `--no-verify` — that bypasses ALL hooks, not just sandbox enforcement.

### "SANDBOX CONFLICT — commit blocked"

Both `ADVISED_AUTO` and `EVO_AUTO` are set. The fix is to unset whichever one you don't want; the scripts pop the opposing var defensively, so this usually means an advised run leaked its env into the evolve shell. Close the shell, open a fresh one, and retry.

### No promotions after N rounds

Expected outcome when the pool is low-quality or the parent is already strong. Check the `outcome` column in the round log:

- Many `discarded-tie` (AB ties): the pool items are too similar to each other or too weak to break the mirror. Consider regenerating the pool with a different seed or on a different map.
- Many `discarded-gate` (AB winner loses to parent): improvements are local regressions. The run will still exhaust the pool — watch `no_progress_streak` on the dashboard as a warning signal and abort via the Stop button if all N remaining rounds are also likely to regress.
- Many `discarded-crash`: the improvements are breaking the bot's runtime. Check `logs/` for traceback patterns and consider raising `--pool-size` so the crashers get consumed faster.

### Stale `data/evolve_run_state.json` blocks a fresh run

Pre-flight 0.2 refuses to start if the state file says `status: "running"` but no process holds it. If you're confident the prior run is dead (no `python scripts/evolve.py` in `ps`), manually edit the file:

```bash
python -c "
import json
from pathlib import Path
p = Path('data/evolve_run_state.json')
state = json.loads(p.read_text())
state['status'] = 'stopped'
p.write_text(json.dumps(state, indent=2, sort_keys=True) + '\n')
"
```

Then re-run evolve.

---

## Reverting an Evolve Run

Every run is fully reversible. The baseline tag created in Phase 0.7 is your restore point.

### Quick revert (everything)

Roll back ALL changes — every promote commit, every candidate version dir, and the `bots/current/current.txt` pointer:

```bash
# 1. Find the run's baseline tag
git tag -l "evolve/run/*"

# 2. Reset master to baseline
git reset --hard "evolve/run/<RUN_TS>/baseline"
git push origin master --force-with-lease

# 3. Clean untracked candidate dirs (snapshot_current creates untracked bots/ subdirs
#    for discarded candidates)
git clean -fd bots/
```

### Partial revert (keep some promotions)

Promote commits are sequential on master. If promotion 1 was good but promotion 2 regressed:

```bash
git log "evolve/run/<RUN_TS>/baseline..evolve/run/<RUN_TS>/final" --oneline --grep "\[evo-auto\]"

# Reset to the state after the first good promote
git reset --hard <commit-sha-after-good-promote>
git push origin master --force-with-lease
```

### Morning-after checklist

1. Read `$LOGFILE` (the markdown run log in `documentation/soak-test-runs/evolve-*.md`).
2. Check the round table — which promoted, which discarded, and why.
3. Inspect the promotion chain in the report — does the final parent's name show up in `bots/current/current.txt`?
4. Run `git log "evolve/run/<RUN_TS>/baseline..master" --oneline` to see what landed.
5. Quick regression gate: `uv run pytest && uv run mypy src && uv run ruff check .`.
6. If unhappy, use the quick revert above. If happy, the untracked candidate dirs for discarded rounds can be cleaned with `git clean -fd bots/` (but they're small and harmless to leave around as a history of what was tried).

---

## Safety Rails

- **One evolve run at a time.** Pre-flight refuses if another evolve run's state file is `running` and fresh (<12 hours).
- **No concurrent advised runs.** Pre-flight refuses if `data/advised_run_state.json` shows `status: "running"`.
- **Atomic state writes.** All JSON writes use `tmp.replace(path)` to prevent partial reads by the dashboard.
- **Promotion gate is non-negotiable.** Never bypass the candidate-vs-parent safety gate, even for "obviously good" improvements. The whole point of the evolve loop is that LLM-proposed improvements are untrusted and must earn their promotion empirically.
- **EVO_AUTO scope is `bots/**`.** This is broader than advised's `bots/current/**` scope because evolve creates new version directories via `snapshot_current()`. The sandbox hook enforces `bots/**` at commit time.
- **Never kill SC2_x64.exe.** Only restart Python processes.
- **Wall-clock discipline.** `scripts/evolve.py` checks the budget at the top of every round. Check the elapsed time against the budget in `$LOGFILE` at every phase boundary in case the script hangs mid-round — the hang itself shouldn't silently burn the budget past the next round.

---

## Relationship to Other Skills

```
/improve-bot-evolve (outer loop, this skill)
  ├── Phase 0: Bootstrap (sandbox, pre-flight, baseline tag, start backend)
  ├── Phase 1: Seed + Pool (mirror games, generate_pool via Claude)
  ├── Phase 2: Round loop (sample, run_round, commit-or-discard)
  │     └── run_round = snapshot × 2 + apply × 2 + run_batch(A,B) + optional gate
  ├── Phase 3: Loop decision (wall-clock, pool-exhausted, dashboard-stop)
  └── Phase 4: Morning Report (final tag, GitHub issue, promotion chain)
```

- **`/improve-bot-advised`** — sibling. Linear one-improvement-at-a-time loop. Mutually exclusive with evolve.
- **`/improve-bot`** — building block used inside advised's dev path. Not called by evolve (evolve uses `apply_improvement` + `run_batch` directly instead of spawning sub-agents).
- **`/improve-bot-triage`** — post-run helper. Can read `documentation/soak-test-runs/evolve-*.md` and produce a picklist of next targets.
- **`/a4g-dashboard-check`** — pre-flight check (Phase 0.6) and post-run validation.
- **`/repo-update`** — end-of-cycle docs + README sync after a productive run.
