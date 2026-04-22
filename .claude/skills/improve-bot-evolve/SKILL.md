---
name: improve-bot-evolve
description: Autonomous generation-phase improvement loop. Generates a pool of Claude-proposed improvements, fitness-tests each vs the current parent, stacks the winners for a full-stack composition promotion, and regression-checks against the prior parent — repeating for hours until the pool or wall-clock budget is exhausted. Designed for overnight unattended runs.
user-invocable: true
argument: Optional flags only — no free-text suggestion needed. Flags: `--pool-size N` (default 10), `--games-per-eval N` (default 5), `--hours N` (default 4), `--map NAME` (default Simple64), `--no-commit` (dev/test only), `--results-path PATH`, `--pool-path PATH`, `--state-path PATH`, `--run-log PATH`, `--resume`, `--post-training-cycles N`.
required-env: SC2 installed at `C:/Program Files (x86)/StarCraft II/`, `claude` CLI on PATH and authenticated (OAuth subscription token OR `ANTHROPIC_API_KEY` — whichever the CLI is set up with).
---

# /improve-bot-evolve

Autonomous generation-phase improvement loop. Every generation:

1. **Fitness** — each active pool imp is individually snapshotted, applied, and plays the current parent for `--games-per-eval` games. Strict-majority wins (≥3/5) become fitness-pass; one-short (2/5) become fitness-close (resurrection-eligible, retry cap 2); anything lower evicts immediately.
2. **Composition** — all fitness-pass imps are stacked onto one snapshot and tested vs parent. Pass → the whole stack promotes as one `[evo-auto]` commit. Fail → a top-1 fallback composition runs on just the highest-ranked pass imp.
3. **Regression** — if anything was promoted, new parent plays prior parent for `--games-per-eval` games. On rollback, `git revert` the promote commit (also under `EVO_AUTO=1`) and restore the pointer.
4. **Pool refresh** — close-loss and benched-pass imps go back to active (retry cap enforced; cap=3 total evals before eviction). Claude tops up the active pool back to `--pool-size` with fresh orthogonal imps targeting the new parent.

**Design goal:** `/improve-bot-evolve` in a fresh context window with no additional input should produce a measurably stronger parent version after a few hours, with every promotion recorded as an `[evo-auto]` commit and every regression recorded as an `[evo-auto]` revert.

**Zero-input contract:** When invoked with no flags, this skill MUST NOT ask the user any questions. It proceeds immediately with safe defaults (pool size 10, 5 games per eval, 4-hour budget, Simple64). Every phase is designed to resolve ambiguity autonomously — pick reasonable defaults, log the choice, and keep going. The only exception is if a hard pre-flight failure makes the run impossible (e.g., SC2 not installed, `claude` CLI missing or unauthenticated).

**Relationship to `/improve-bot-advised`:** These two skills are siblings with different inner mechanics:
- `/improve-bot-advised` picks one improvement, applies it, validates win-rate, commits — linear.
- `/improve-bot-evolve` generates a pool of 10, fitness-tests each vs parent, stacks winners for a composition promotion — parallel with a regression safety net.

The outer phase shape (pre-flight → seed → loop → decision → report) is identical. The two skills must NEVER run concurrently (they both mutate `bots/current/current.txt`). The pre-flight explicitly refuses to start if an advised run is in progress.

---

## Flags

| Flag | Type | Default | Purpose |
|------|------|---------|---------|
| `--pool-size` | int | `10` | Number of improvements Claude generates (and tops up to between generations) |
| `--games-per-eval` | int | `5` | Games in each phase evaluation (fitness / composition / regression). Pass threshold = strict majority of this count. |
| `--hours` | float | `4.0` | Wall-clock budget. `0` disables the check (test-only). |
| `--map` | str | `Simple64` | SC2 map name |
| `--game-time-limit` | int | `1800` | SC2 in-game time limit per game, in seconds |
| `--hard-timeout` | float | `2700.0` | Wall-clock timeout per game, in seconds |
| `--no-commit` | flag | off | Skip the EVO_AUTO commit on promote. Dev/test only. |
| `--results-path` | path | `data/evolve_results.jsonl` | JSONL log of every phase outcome |
| `--pool-path` | path | `data/evolve_pool.json` | Pool state file (dashboard reads this) |
| `--state-path` | path | `data/evolve_run_state.json` | Run state file (dashboard reads this) |
| `--current-round-path` | path | `data/evolve_current_round.json` | Live per-game progress file |
| `--crash-log-path` | path | `data/evolve_crashes.jsonl` | Full-traceback crash log |
| `--run-log` | path | auto | Human-readable markdown run log |
| `--resume` | flag | off | Reload pool + per-item statuses from `--pool-path` instead of generating fresh. |
| `--post-training-cycles` | int | `0` | On promoted runs, start the training daemon for exactly N cycles after the loop exits. |

Budget math: pool=10, games-per-eval=5 → ~60 games per generation (50 fitness + 5 composition + 5 regression); with 4-hour budget and ~3-min games, roughly 1 generation per hour.

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
  SC2 required for mirror seed, fitness batches, composition test, regression gate
```

The banner differs from the advised banner in three ways:
- Scope is `bots/**` (any version), not `bots/current/**` (a single version).
- Commit marker is `[evo-auto]`, not `[advised-auto]`.
- Env var is `EVO_AUTO=1`, not `ADVISED_AUTO=1`.

Both env vars must NOT be set simultaneously — the sandbox hook fails loudly on that conflict. `scripts/evolve.py` unsets `ADVISED_AUTO` defensively in its commit subprocess env.

### 0.1 Parse flags

Parse all flags. Never ask the user, always resolve automatically. Log any overrides to `$LOGFILE`.

### 0.2 Pre-flight checks

All pre-flight checks resolve autonomously. Only stop if the situation is truly unrecoverable.

```bash
# Git state
git status --porcelain
git rev-parse --abbrev-ref HEAD
git fetch origin && git status -sb

# SC2
ls "C:/Program Files (x86)/StarCraft II/Versions/"

# Claude CLI on PATH + authenticated
claude --version && claude -p "ping" --model haiku --output-format text --no-session-persistence | head -1

# Tools
uv run pytest --co -q 2>&1 | tail -1
uv run ruff check . --quiet
uv run mypy src --quiet

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
- **Git dirty (untracked files only):** `git stash --include-untracked -m "evolve-preflight-$RUN_TS"`. Log. Proceed.
- **Git dirty (staged/modified tracked files):** Commit them with `"chore: auto-stash before evolve run $RUN_TS"`. Log SHA. Proceed.
- **Not on master:** `git checkout master`. Stash first if needed. Log.
- **Behind origin/master:** `git pull --rebase origin master`. STOP on conflicts.
- **Quality gates fail:** Log but proceed — pre-existing failures are the baseline.
- **SC2 not found:** STOP.
- **`claude` CLI missing/unauthenticated:** STOP.
- **Advised run active:** STOP.
- **Stale evolve run (state file `running` but no process):** if older than 12 hours, rewrite status to `stopped`; otherwise abort.

### 0.3 Record baseline

```bash
LOOP_START=$(date +%s)
RUN_TS=$(date -d @$LOOP_START +%Y%m%d-%H%M)

LOGFILE="documentation/soak-test-runs/evolve-$(date +%F).md"
i=0; while [ -e "$LOGFILE" ]; do i=$((i+1)); suf=$(printf "\\x$(printf '%x' $((96+i)))"); LOGFILE="documentation/soak-test-runs/evolve-$(date +%F)-$suf.md"; done
```

Capture baseline: current parent, recent win rate, Elo, git SHA. Write run header to `$LOGFILE`.

### 0.4 Start systems

**Backend lifecycle:** Start exactly ONE `--serve` process. It stays alive for the entire run so the Evolution dashboard tab can monitor. **Never start a second `--serve` process.**

```bash
export PYTHONUNBUFFERED=1

python -c "import socket; s=socket.socket(); r=s.connect_ex(('127.0.0.1',8765)); s.close(); exit(0 if r!=0 else 1)" && {
  DEBUG_ENDPOINTS=1 PYTHONUNBUFFERED=1 uv run python -m bots.v0.runner --serve 2>&1 &
  BACKEND_PID=$!
  echo "Backend started (PID $BACKEND_PID)"
} || echo "Backend already running on port 8765, skipping"
```

The evolve loop does NOT use `--daemon`; `scripts/evolve.py` drives games directly.

### 0.5 Seed game (sanity check)

Run 1 mirror game (parent-vs-parent) to verify the full subprocess self-play pipeline works before entering the expensive pool generation.

```bash
uv run python scripts/selfplay.py --p1 "$(cat bots/current/current.txt)" --p2 "$(cat bots/current/current.txt)" --games 1 --map "$MAP" 2>&1 | tee -a "$LOGFILE"
```

If this returns non-zero or no SelfPlayRecord, STOP.

### 0.6 Dashboard check

Run `/a4g-dashboard-check`. The Evolution tab should show "idle". Stop and fix any `✗` tabs.

### 0.7 Baseline tag

```bash
git tag "evolve/run/$RUN_TS/baseline"
git push origin "evolve/run/$RUN_TS/baseline"
```

This tag is the restore point for a full revert of the run. `scripts/evolve.py` handles the initial run-state write via `write_run_state()`; the operator does not write it manually.

---

## Phase 1: Seed + Pool

### 1.1 Invoke the evolve runner

The entire generation loop (pool gen + fitness + composition + regression + refresh) is owned by `scripts/evolve.py`. The skill's job is to invoke with the right flags, monitor state files, and handle exit.

```bash
uv run python scripts/evolve.py \
    --pool-size 10 \
    --games-per-eval 5 \
    --hours 4 \
    --map Simple64 \
    2>&1 | tee -a "$LOGFILE"
```

Internally:
1. Runs 3 parent-vs-parent mirror games via `orchestrator.selfplay.run_batch`.
2. Calls `orchestrator.evolve.generate_pool(parent, pool_size=10, ...)` which prompts Claude (Opus by default) with the mirror summary, source tree, and guiding principles. Claude returns 10 orthogonal dev `Improvement` records.
3. If the initial response has file overlaps (two imps touching the same file), re-prompts once with a conflict list. A second-round overlap is accepted — the composition phase surfaces merge failures empirically.
4. Writes `data/evolve_pool.json` with all 10 items marked `status: "active"`.

If pool generation fails (malformed JSON, rate limit, missing API key), the script writes `status: "failed"` to `data/evolve_run_state.json` and exits 1. See **Troubleshooting**.

### 1.2 Pool-state file schema

```json
{
  "generated_at": "2026-04-21T21:33:00+00:00",
  "parent": "v5",
  "generation": 0,
  "pool": [
    {
      "rank": 1,
      "title": "Chrono boost auto-cast",
      "type": "dev",
      "description": "...",
      "principle_ids": ["4"],
      "expected_impact": "high",
      "concrete_change": "Add chrono_boost_manager.py ...",
      "files_touched": ["bots/v5/chrono_boost_manager.py", "bots/v5/bot.py"],
      "status": "active",
      "fitness_score": null,
      "retry_count": 0,
      "first_evaluated_against": null,
      "last_evaluated_against": null
    }
  ]
}
```

Per-item `status` vocabulary (managed by `scripts/evolve.py`):

| Status | Meaning |
|---|---|
| `active` | awaiting fitness eval this generation |
| `fitness-pass` | beat parent ≥ majority this gen; included in composition stack |
| `fitness-close` | one win short of majority; resurrection-eligible (cap=3 total evals) |
| `evicted` | permanent. Either 0/1 wins on fitness, hit the retry cap, or crashed during a phase. |
| `promoted-stack` | part of a stack that passed composition AND regression |
| `promoted-single` | promoted alone as top-1 fallback after stack failed |
| `regression-rollback` | promoted but regression check reverted the commit |

Transitions outside `active` are visible in the dashboard Pool view. At the end of each generation, `fitness-pass` and `fitness-close` imps whose `retry_count` is below the cap flip back to `active` for the next generation.

---

## Phase 2: Generation loop

Each iteration = one generation. `scripts/evolve.py` runs internally; the skill reads state files.

### 2a. Fitness phase

For every imp with `status == "active"`:

1. `snapshot_current()` → scratch `cand_*` dir.
2. `apply_improvement(cand, imp, dev_apply_fn=spawn_dev_subagent)`.
3. `run_batch(cand, parent, games_per_eval, map)` — individual-vs-parent.
4. Classify wins: `>= games // 2 + 1` = `pass`; one short = `close`; else `fail`.
5. `rmtree` scratch dir. Pointer restored to parent.
6. Append a `phase: "fitness"` row to `evolve_results.jsonl`. Update pool state.

### 2b. Composition phase

If ≥1 fitness-pass imp exists:

1. `snapshot_current()` → new scratch `cand_*` dir.
2. Apply ALL fitness-pass imps to the single scratch dir, in rank order.
3. `run_batch(cand, parent, games_per_eval, map)` — stacked-vs-parent.
4. If majority win: `snapshot_current()` promotes the scratch to `vN+1`; rewrite `manifest.parent`; commit `[evo-auto]` with stacked titles.
5. If lose: `rmtree` scratch, pointer restored to parent. **Fallback** — run composition again with just the top-ranked imp.
6. Fallback pass: promote top-1 as `vN+1`, commit with `is_fallback=True`.
7. Fallback fail: no promotion this generation. All winners go to `fitness-close` for next gen.

### 2c. Regression phase

If composition promoted anything:

1. `run_batch(new_parent, prior_parent, games_per_eval, map)` — no snapshots.
2. Majority new-parent win: accept the promotion, log `regression-pass`.
3. Else: `git revert --no-commit <promote_sha>`, commit with `[evo-auto]`, flip promoted imps to `regression-rollback`, restore pointer to `prior_parent`.

### 2d. Pool refresh

1. For each `fitness-pass` or `fitness-close` imp with `retry_count < 3`: flip to `active`.
2. For each at the cap: flip to `evicted`.
3. If `count(active) < pool_size`: call `generate_pool(parent_current, pool_size=delta, skip_mirror=True)` and append the new imps.

### 2.4 Commit format

Stack promotion:
```
evolve: generation 3 promoted stack (3 imps)

- Chrono boost auto-cast
- Forward pylon warp-in
- Archon morph on 4 HTs

[evo-auto]
```

Single-imp fallback promotion:
```
evolve: generation 3 promoted single imp (top-1 fallback)

- Chrono boost auto-cast

[evo-auto]
```

Regression rollback:
```
evolve: generation 3 regression rollback

Reverts abc123def456. regression rollback: new v7 1-4 prior v6 (needed 3); pointer reset

[evo-auto]
```

The `[evo-auto]` marker MUST appear on its own line in the commit body. The sandbox hook reads the env var (`EVO_AUTO=1`), not the marker, but the marker is the human-readable audit trail.

---

## Phase 3: Loop decision (stop conditions)

The loop exits when ANY of:

1. **Wall-clock budget exceeded** — `elapsed_seconds >= hours * 3600`. Disabled when `--hours 0`.
2. **Pool exhausted** — fewer than 1 item with `status: "active"` remains AND pool refresh also produced none.
3. **Dashboard stop request** — control-file flag (not yet enforced mid-generation; future enhancement).

`stop_reason` in the final run log: `"wall-clock"`, `"pool-exhausted"`, or `"dashboard-stop"`.

---

## Phase 4: Morning Report

Write the final report to `$LOGFILE` and print to stdout.

```markdown
# Evolve run — <RUN_TS>

- Parent (start): v5
- Parent (end):   v9
- Wall-clock budget: 4.0h
- Started:  2026-04-21T21:33:00+00:00
- Finished: 2026-04-22T01:12:04+00:00
- Generations completed: 4
- Generations promoted:  2
- Total evictions: 6
- Stop reason: wall-clock

## Generations

| gen | fitness pass/close/fail | composition | regression | outcome |
|---|---|---|---|---|
| 1 | 3/2/5 | stack-pass | pass | promoted v5 → v6 |
| 2 | 1/3/6 | single-pass (fallback) | rollback | ROLLBACK |
| 3 | 2/2/5 (+1 crash) | stack-fail | — | no promote |
| 4 | 4/1/5 | stack-pass | pass | promoted v6 → v7 |
```

Final tag + GitHub issue + cleanup as before.

```bash
git tag "evolve/run/$RUN_TS/final"
git push origin "evolve/run/$RUN_TS/final"
```

Cleanup:
```bash
curl -s -X POST http://localhost:8765/api/shutdown || true
sleep 2
powershell.exe -Command "Get-WmiObject Win32_Process | Where-Object { \$_.CommandLine -match 'bots.v0.runner|scripts/evolve.py|scripts/selfplay.py' -and \$_.CommandLine -notmatch 'SC2' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }"
python -c "import socket; s=socket.socket(); r=s.connect_ex(('127.0.0.1',8765)); s.close(); exit(0 if r!=0 else 1)" || echo "WARNING: port 8765 still occupied"
```

Do NOT kill `SC2_x64.exe`.

---

## Dashboard Control Panel Bridge

### State file: `data/evolve_run_state.json`

```json
{
  "status": "running",
  "parent_start": "v5",
  "parent_current": "v7",
  "started_at": "2026-04-21T21:33:00+00:00",
  "wall_budget_hours": 4.0,
  "generation_index": 3,
  "generations_completed": 2,
  "generations_promoted": 1,
  "evictions": 4,
  "resurrections_remaining": 3,
  "pool_remaining_count": 6,
  "last_result": {
    "generation_index": 3,
    "phase": "composition",
    "imp_title": null,
    "stacked_titles": ["Chrono boost", "Forward pylon"],
    "is_fallback": false,
    "score": [3, 5],
    "outcome": "composition-pass",
    "reason": "..."
  }
}
```

**Status transitions:**
- `"running"` — written on startup and after every phase.
- `"completed"` — written when the loop exits cleanly.
- `"failed"` — written on pool-generation failure.

### Pool file: `data/evolve_pool.json`

See §1.2.

### Results file: `data/evolve_results.jsonl`

One line per phase outcome. Each row's `phase` field is one of `fitness`, `composition`, `regression`, or the crash equivalents. Full schema in `frontend/src/hooks/useEvolveRun.ts`.

### Control file: `data/evolve_run_control.json`

```json
{
  "stop_run": false,
  "pause_after_round": false,
  "updated_at": "2026-04-21T22:00:00+00:00"
}
```

(Dashboard mid-generation stop is a future enhancement — today the stop signal takes effect at the next generation boundary.)

---

## What NOT to do

- **DO NOT run concurrently with `/improve-bot-advised`.** Pre-flight refuses if `data/advised_run_state.json` shows `status: "running"`.
- **DO NOT manually commit during a run.** The pre-commit hook allows `EVO_AUTO=1` commits to touch `bots/**`; manual commits may collide on `bots/current/current.txt`.
- **DO NOT `kill` the SC2 process.** Only restart Python processes.
- **DO NOT set both `EVO_AUTO=1` and `ADVISED_AUTO=1`.** The sandbox hook fails loudly.
- **DO NOT start a `--daemon` backend during an evolve run.** The evolve loop drives games directly.
- **DO NOT modify `scripts/evolve.py` or `src/orchestrator/evolve.py` mid-run.**
- **DO NOT use `--no-commit` for production runs.**
- **DO NOT lower `--games-per-eval` below 5 unless you're running a fast smoke test.** 3 games is statistically too noisy to separate pass from close.

---

## Troubleshooting

### Pool generation fails

Check `$LOGFILE` for the Python exception. Common causes:

- **Claude returned malformed JSON.** `_parse_claude_pool` retries once on short/malformed responses; a second failure raises `ValueError`.
- **`claude` CLI missing/unauthenticated.** Run `claude --version` and `claude -p "ping" --model haiku --output-format text --no-session-persistence` to verify.
- **Rate limit.** Wait a few minutes.
- **Mirror games crashed.** Check `data/evolve_results.jsonl`; re-run Phase 0.5 manually.

### First fitness eval crashes immediately

- **SC2 not running.** Start SC2 first.
- **Port collision.** `Get-NetTCPConnection -LocalPort 8765`, then cleanup block from Phase 4.
- **Stale `bots/<candidate>/`.** `snapshot_current()` picks fresh UUID names; collisions are almost always permission / disk-space issues.

### Composition always fails (`composition-fail` on every stack)

Claude's proposals are probably not orthogonal even after the retry. Check `files_touched` in the most recent pool. If two fitness-pass imps edit the same function, the second imp's apply silently overwrites the first's changes, and the composition candidate ends up running just one of them. Manually prune the conflicting imp from the pool and `--resume`.

### Regression rollback on every generation

New parent is a local regression. The fitness phase is finding imps that beat the *current* parent but not the *prior* parent — often a strong parent has blind spots that an imp exploits but that don't generalise. Watch for this pattern on the dashboard; if it persists for 3 generations, `git reset --hard evolve/run/<TS>/baseline` and re-run with a different seed.

### Dashboard Evolution tab shows stale state

Atomic writes prevent half-written files; stale state is usually a **cache-key mismatch in the frontend** (see `feedback_useapi_cache_schema_break.md`). The hook file bumps its cache key whenever the schema changes — if you're seeing crashes after pulling a new version, hard-refresh the browser.

### Pre-commit hook blocks the evolve commit

The sandbox hook checks `EVO_AUTO=1` in the env, not the commit message. If the commit fails with "SANDBOX VIOLATION — commit blocked", the commit subprocess didn't inherit the env var. Confirm `scripts/evolve.py`'s `git_commit_evo_auto` passes `env=env` where `env["EVO_AUTO"] = "1"`.

### "SANDBOX CONFLICT — commit blocked"

Both `ADVISED_AUTO` and `EVO_AUTO` are set. Close the shell, open a fresh one, retry.

### No promotions after N generations

Expected when the pool is low-quality or the parent is already strong. Check the `composition_outcome` column in the run log. If mostly `single-fail`, the fitness-pass threshold may be too loose for this parent — re-run with `--games-per-eval 7`.

### Stale `data/evolve_run_state.json` blocks a fresh run

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

---

## Reverting an Evolve Run

Every run is fully reversible. The baseline tag from Phase 0.7 is the restore point.

### Quick revert (everything)

```bash
git tag -l "evolve/run/*"
git reset --hard "evolve/run/<RUN_TS>/baseline"
git push origin master --force-with-lease
git clean -fd bots/
```

### Partial revert

Promotion commits and regression-rollback commits are sequential on master. Find the boundary you want to keep:

```bash
git log "evolve/run/<RUN_TS>/baseline..evolve/run/<RUN_TS>/final" --oneline --grep "\[evo-auto\]"
git reset --hard <commit-sha-after-good-promote>
git push origin master --force-with-lease
```

### Morning-after checklist

1. Read `$LOGFILE`.
2. Check the generation table — which promoted, which rolled back.
3. Inspect `bots/current/current.txt` matches the expected final parent.
4. `git log "evolve/run/<RUN_TS>/baseline..master" --oneline`.
5. Quick regression gate: `uv run pytest && uv run mypy src && uv run ruff check .`.
6. If unhappy, use quick revert. If happy, `git clean -fd bots/` to remove orphaned `cand_*` dirs.

---

## Safety Rails

- **One evolve run at a time.** Pre-flight refuses on fresh `running` state (<12h).
- **No concurrent advised runs.** Pre-flight refuses on running advised.
- **Atomic state writes.** All JSON writes use `tmp.replace(path)`.
- **Fitness + composition + regression are non-negotiable.** Never bypass any phase gate; the whole point is that LLM proposals are untrusted and must earn their promotion empirically.
- **EVO_AUTO scope is `bots/**`.** Broader than advised's `bots/current/**` because evolve creates new version dirs.
- **Never kill SC2_x64.exe.**
- **Wall-clock discipline.** Check elapsed at every phase boundary.

---

## Relationship to Other Skills

```
/improve-bot-evolve (outer loop, this skill)
  ├── Phase 0: Bootstrap (sandbox, pre-flight, baseline tag, start backend)
  ├── Phase 1: Seed + Pool (mirror games, generate_pool via Claude)
  ├── Phase 2: Generation loop
  │     ├── 2a Fitness      (run_fitness_eval per active imp)
  │     ├── 2b Composition  (run_composition_eval on stack → fallback)
  │     ├── 2c Regression   (run_regression_eval vs prior parent)
  │     └── 2d Pool refresh (retry bookkeeping + generate_pool delta)
  ├── Phase 3: Loop decision (wall-clock / pool-exhausted / dashboard-stop)
  └── Phase 4: Morning Report (final tag, GitHub issue, generation table)
```

- **`/improve-bot-advised`** — sibling. Linear. Mutually exclusive with evolve.
- **`/improve-bot`** — building block used inside advised's dev path. Not called by evolve.
- **`/improve-bot-triage`** — post-run helper; can read `documentation/soak-test-runs/evolve-*.md`.
- **`/a4g-dashboard-check`** — pre-flight (Phase 0.6) and post-run validation.
- **`/repo-update`** — end-of-cycle docs + README sync after a productive run.
