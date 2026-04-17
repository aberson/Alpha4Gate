---
name: improve-bot-advised
description: Autonomous advisor-driven improvement loop. Runs games, reviews replays with Claude strategic analysis against Protoss guiding principles, prioritizes improvements, and executes them via /improve-bot — repeating for hours to iteratively strengthen the bot. Designed for overnight unattended runs.
user-invocable: true
argument: Optional flags only — no free-text suggestion needed. Flags: `--mode training|dev|both` (default training), `--observe replay|live` (default replay), `--games N` (default 10), `--hours N` (default 4), `--difficulty N` (default current), `--fresh` (discard prior iteration context), `--self-improve-code` (implies --mode both if --mode not set).
---

# /improve-bot-advised

Autonomous improvement loop that combines Claude strategic analysis with the existing `/improve-bot` skill. The outer loop observes games, analyzes replays against the Protoss guiding principles, selects one high-impact improvement, and delegates execution to `/improve-bot`. Repeats until the wall-clock budget is exhausted.

**Design goal:** `/improve-bot-advised` in a fresh context window with no additional input should produce a measurably improved bot after a few hours.

**Zero-input contract:** When invoked with no flags, this skill MUST NOT ask the user any questions. It proceeds immediately with safe defaults (training-only mode, replay observation, 10 games/cycle, 4 hours). Every phase is designed to resolve ambiguity autonomously — pick reasonable defaults, log the choice, and keep going. The only exception is if a hard pre-flight failure makes the run impossible (e.g., SC2 not installed, git dirty with conflicts).

---

## Flags

| Flag | Values | Default | Purpose |
|------|--------|---------|---------|
| `--mode` | `training`, `dev`, `both` | `training` | What kind of improvements to attempt. `training` is safe (config/reward changes only, no source code edits). |
| `--observe` | `replay`, `live` | `replay` | How games are observed (see §Observation Modes) |
| `--games` | integer | `10` | Games per observation cycle |
| `--hours` | integer | `4` | Total wall-clock budget |
| `--difficulty` | integer | current curriculum | SC2 AI difficulty level |
| `--fresh` | flag | off | Discard prior iteration context; start analysis fresh each cycle |
| `--self-improve-code` | flag | off | Enables autonomous source-code changes. Implies `--mode both` if `--mode` is not explicitly set. |
| `--fail-threshold` | integer (%) | `30` | Win rate drop % that triggers a fail. Highly tunable — see §Validation Philosophy |

**Flag resolution (no user interaction — resolve automatically):**
- No flags at all → `--mode training --observe replay --games 10 --hours 4 --fail-threshold 30`. Proceed immediately.
- `--self-improve-code` without explicit `--mode` → `--mode both` (auto-upgrade)
- `--self-improve-code` with `--mode training` → ignore the flag, stay training-only (no conflict, just redundant)
- `--mode dev` or `--mode both` without `--self-improve-code` → **auto-downgrade to `--mode training`**, log: "dev/both mode requires --self-improve-code, falling back to training-only". Do NOT ask the user.
- Ambiguous difficulty → read current curriculum level from `data/checkpoints/manifest.json`, fall back to 1 if not found

---

## Observation Modes

### Mode (i): `replay` (default, fast)

Run games at **maximum speed** (`realtime=False`) **without** the Claude advisor active during gameplay. After the batch completes:

1. Collect JSONL game logs from `logs/` (via `replay_parser.parse_replay_from_log`)
2. Collect `data/decision_audit.json` entries from the games (if any exist from prior runs)
3. Feed game timelines, stats, win/loss outcomes, and any existing decision log entries to Claude for **post-hoc strategic review**

**Why default:** Games finish in ~30-60 seconds each at max speed. A 10-game batch takes ~5-10 minutes. The post-hoc review adds ~2-3 minutes. Total observation cycle: ~10 minutes. This allows 8-12+ improvement iterations in a 4-hour budget.

### Mode (ii): `live` (slow, richer analysis)

Run games at **realtime speed** (`realtime=True`) **with** the Claude advisor active (firing every 30 game-seconds per the existing rate limiter in `claude_advisor.py`). The advisor's suggestions accumulate in `decision_audit.json` during gameplay.

After the batch completes, feed both the game logs AND the advisor's decision log to Claude for review. This gives richer data because the advisor saw and reacted to live game state, but games take 3-8 minutes each wall-clock.

**When to use:** When you want the advisor's real-time tactical perspective, or when debugging advisor behavior itself. Expect ~2-3 iterations in a 4-hour budget with 10 games per cycle.

---

## Phase 0: Bootstrap

### 0.0 Run-start banner

Print this banner at the start of every run (before any other output):

```
🔒 Sandbox active
  I can edit:   bots/current/**
  I cannot edit: src/orchestrator/, pyproject.toml, tests/, frontend/, scripts/
  SC2 required for check_promotion() Elo validation
```

### 0.1 Parse flags

Parse all flags from the argument. Apply the flag resolution rules from the table above — never ask the user, always resolve automatically. Log any auto-downgrades or flag adjustments to `$LOGFILE`.

### 0.2 Pre-flight checks

All pre-flight checks resolve autonomously. Only stop if the situation is truly unrecoverable.

```bash
# Git state
git status                            # check cleanliness
git rev-parse --abbrev-ref HEAD       # check branch
git fetch origin && git status -sb    # check sync

# SC2
ls "C:/Program Files (x86)/StarCraft II/Versions/" # SC2 installed

# Tools
uv run pytest --co -q 2>&1 | tail -1              # tests discoverable
uv run ruff check . --quiet                        # lint clean
uv run mypy src --quiet                            # types clean
```

**Autonomous resolution for common issues:**
- **Git dirty (untracked files only, no conflicts):** `git stash --include-untracked -m "advised-preflight-$RUN_TS"`. Log the stash. Proceed.
- **Git dirty (staged/modified tracked files):** Commit them with message `"chore: auto-stash before advised run $RUN_TS"`. Log the commit SHA. Proceed.
- **Not on master:** `git checkout master`. If that fails (uncommitted changes), stash first, then checkout. Log.
- **Behind origin/master:** `git pull --rebase origin master`. If conflicts, STOP — this is unrecoverable without human judgment.
- **Quality gates fail (pytest/mypy/ruff):** Log failures but proceed anyway — the pre-existing failures are the baseline, not a blocker. The skill will still gate its own changes against these.
- **SC2 not found:** STOP — this is a hard requirement, log and exit.

### 0.3 Record baseline

```bash
LOOP_START=$(date +%s)
RUN_TS=$(date -d @$LOOP_START +%Y%m%d-%H%M)

# Auto-disambiguate log filename
LOGFILE="documentation/soak-test-runs/advised-$(date +%F).md"
i=0; while [ -e "$LOGFILE" ]; do i=$((i+1)); suf=$(printf "\\x$(printf '%x' $((96+i)))"); LOGFILE="documentation/soak-test-runs/advised-$(date +%F)-$suf.md"; done
```

Capture baseline metrics:
- Current win rate (from `training.db` or recent game results)
- Current difficulty level
- Current reward trend
- Checkpoint SHA

Write run header to `$LOGFILE`:
- Flags, mode, observation mode, games-per-cycle, hours budget
- Baseline metrics
- `LOOP_START` timestamp
- Git SHA

### 0.4 Start systems

**Backend lifecycle rule:** Start exactly ONE `--serve` process at the beginning of the run. This process must stay alive for the entire run so the dashboard can monitor progress. **Never start a second `--serve` process.** The runner's `_start_server_background()` already skips spawning a server when port 8765 is occupied, so game invocations won't conflict. For daemon soaks, use `--serve --daemon` on the SAME process (shut down the API-only backend first, start daemon, then restart API-only after soak).

```bash
export PYTHONUNBUFFERED=1

# Check if backend is already running — only start if port is free
python -c "import socket; s=socket.socket(); r=s.connect_ex(('127.0.0.1',8765)); s.close(); exit(0 if r!=0 else 1)" && {
  DEBUG_ENDPOINTS=1 PYTHONUNBUFFERED=1 uv run python -m alpha4gate.runner --serve 2>&1 &
  BACKEND_PID=$!
  echo "Backend started (PID $BACKEND_PID)"
} || echo "Backend already running, skipping"

# Start frontend (optional — skip in headless replay mode unless user wants it)
# cd frontend && npm run dev &
```

**Before daemon soaks:** shut down the existing backend (`/api/shutdown`), wait for port free, then start `--serve --daemon`. After soak completes, shut down daemon, wait for port free, restart `--serve` (API only). Never leave two server processes alive simultaneously.

### 0.5 Seed game

Run 1 game to verify the full pipeline works before entering the loop. If it fails, stop and report — don't burn the budget on a broken setup.

### 0.6 Dashboard check

Run `/a4g-dashboard-check` to verify all dashboard tabs render correctly and reflect the post-seed-game state. This catches stale WebSockets, missing data, duplicate backend processes, and UI regressions before the long loop begins. If the check reports any `✗` (broken) tabs, stop and fix before proceeding. `⚠` (warning) issues should be logged but are not blockers.

### 0.6 Baseline tag

```bash
git tag "advised/run/$RUN_TS/baseline"
git push origin "advised/run/$RUN_TS/baseline"
```

---

## Phase 1: Observe (repeats each iteration)

### Iteration context

Maintain an `ITERATION_CONTEXT` document (in-memory or temp file) that accumulates findings across iterations. Each iteration appends:
- What was tried
- What worked / didn't
- Which principles were addressed

When `--fresh` is set, clear this context at the start of each iteration. When unset (default), the analysis step can see what was already tried and deprioritize repeat suggestions.

### 1.1 Run games

**If `--observe replay`:**
```bash
# Run N games at max speed, no advisor
for i in $(seq 1 $GAMES_PER_CYCLE); do
    uv run python -m alpha4gate.runner --map Simple64 --difficulty $DIFFICULTY 2>&1 | tee -a "$LOGFILE"
done
```

**If `--observe live`:**
```bash
# Run N games at realtime, advisor ON
for i in $(seq 1 $GAMES_PER_CYCLE); do
    uv run python -m alpha4gate.runner --map Simple64 --difficulty $DIFFICULTY --realtime 2>&1 | tee -a "$LOGFILE"
done
```

### 1.2 Collect data

After games complete, gather:
- Game logs from `logs/` (newest N files via `list_replay_logs`)
- `data/decision_audit.json` (advisor entries if live mode was used)
- Win/loss record for the batch
- Game durations
- Key stats (minerals collected, units produced/lost, structures built)

Parse each game log via `replay_parser.parse_replay_from_log` to get structured timeline + stats.

---

## Phase 2: Strategic Analysis

### 2.1 Build the analysis prompt

Construct a Claude prompt that includes:

1. **Game data summary:** Win/loss record, average game duration, per-game timeline highlights, aggregate stats
2. **Decision log entries** (if available from live mode): What the advisor suggested, what the bot did
3. **The full guiding principles document** (`documentation/protoss_sc2_guiding_principles.md` — all 32 sections)
4. **Iteration context** (unless `--fresh`): What was tried in prior iterations and outcomes

The prompt asks Claude to:
- Identify which guiding principles are being violated most frequently
- Find the top 10-20 specific, actionable improvements
- For each improvement, classify as `training` (reward/hyperparam change) or `dev` (code change)
- Rank by estimated impact on win rate
- Provide a concrete description of what to change

### 2.2 Response format

Claude should return structured JSON:

```json
{
  "principle_violations": [
    {"principle_id": 4, "section": "Economic Rules", "description": "Chrono Boost is being wasted — energy capping on Nexuses", "frequency": "every game"}
  ],
  "improvements": [
    {
      "rank": 1,
      "title": "Reward scouting behavior",
      "type": "training",
      "description": "Add a positive reward in reward_rules.json for sending scout units before 3:00 game time",
      "principle_ids": [4, 6],
      "expected_impact": "high",
      "concrete_change": "Add rule: {\"name\": \"early_scout\", \"reward\": 0.5, \"condition\": \"scout_sent_before_180s\"}"
    },
    {
      "rank": 2,
      "title": "Implement Chrono Boost auto-cast",
      "type": "dev",
      "description": "The bot never uses Chrono Boost — add logic to auto-cast on Nexus for probe production, then Warp Gate research",
      "principle_ids": [4],
      "expected_impact": "high",
      "concrete_change": "Add chrono_boost_manager.py with priority queue: probes > warp gate > upgrades"
    }
  ]
}
```

### 2.3 Filter by mode

- `--mode training`: Keep only `type: "training"` improvements
- `--mode dev`: Keep only `type: "dev"` improvements
- `--mode both`: Keep all

If filtering leaves zero candidates, log this and re-run analysis asking specifically for the allowed type(s).

---

## Phase 3: Select

From the filtered improvements list:

1. Take the top 3 by rank
2. **Randomly pick 1** from these 3 (uniform random)
3. Log which was selected and which were passed over

This randomization prevents the loop from getting stuck retrying the same "highest priority" fix if it keeps failing, while still biasing toward high-impact work.

---

## Phase 4: Execute Improvement

### 4.1 Classify execution path

Based on the selected improvement's `type`:

**Training path** (`type: "training"`):
- The improvement targets `data/reward_rules.json`, `data/hyperparams.json`, or training configuration
- Modify the relevant config file(s) as described in `concrete_change`
- Run a training soak: N games with daemon active at max speed
- The daemon will pick up the new config and train PPO accordingly
- Format: direct config edits + soak observation

**Dev path** (`type: "dev"`):
- The improvement requires source code changes
- **Requires `--self-improve-code`** — if not set, skip this improvement and pick the next candidate
- Format the improvement into an `/improve-bot` invocation:
  ```
  /improve-bot <concrete_change description> --self-improve-code
  ```
- Spawn this as a sub-agent on a branch
- `/improve-bot` handles: branch creation, code change, quality gates (pytest/mypy/ruff), merge

### 4.2 Execution budget

Each improvement attempt gets a budget slice:
- **Training:** `--games` count of games (default 10) at max speed (~5-10 min)
- **Dev:** delegated to `/improve-bot` which manages its own timing, but capped at 30 min wall-clock

### 4.3 Quality gates (always, after any change)

```bash
uv run pytest
uv run mypy src
uv run ruff check .
```

If any gate fails on a training change, revert the config file(s) and log the failure.
If any gate fails on a dev change, `/improve-bot` handles rollback per its own Phase 4 logic.

---

## Phase 5: Validate

### 5.1 Validation games

Run N games (same as `--games`) with the improvement applied:
- Compare win rate against the pre-improvement batch
- Compare reward trends
- Compare game duration patterns
- Look for regressions (fewer units, more losses, shorter games due to early deaths)

### 5.2 Pass/fail decision

#### Validation Philosophy

This is the least-certain part of the skill. With small sample sizes (10 games), variance is high — a 2-game swing can be pure noise. The threshold below is a **starting point, not gospel**. Treat it as a tunable knob:

- If the loop is reverting too aggressively (lots of "fail" on changes that seem reasonable), **raise** `--fail-threshold`.
- If the loop is letting regressions through (bot gets worse over multiple iterations), **lower** it.
- If you're unsure, leave the default and review the first run log — the iteration table will show whether the threshold is catching real regressions or just noise.

The skill should **adapt** its own threshold judgment over iterations: if early iterations show high variance (e.g., baseline win rate swings from 5/10 to 8/10 across observation batches with no changes), note this in the iteration context and widen the pass band accordingly.

#### Threshold rules

Use `--fail-threshold` (default 30%) as the primary gate:

**Pass** if ALL of the following:
- Win rate did not drop by more than `--fail-threshold` percent (default 30%: e.g., 7/10 → 5/10 is pass, 7/10 → 4/10 is fail)
- No new test failures
- No obvious behavioral regression (bot stops building units, game crashes, zero units produced, instant death)
- Quality gates still pass (pytest, mypy, ruff)

**Fail** if ANY of the following:
- Win rate dropped by more than `--fail-threshold` percent
- New errors or crashes in game logs that didn't exist in the observation batch
- Quality gates broke
- Bot exhibits degenerate behavior (idle, no production, instant death)

**Borderline** (win rate dropped between `--fail-threshold * 0.66` and `--fail-threshold`): Log as "marginal" — keep the change but flag it in the iteration context so the next iteration can re-evaluate. If two consecutive iterations produce marginal results, treat the second as a fail.

**Hard fail (always, regardless of threshold):** Quality gates broken, degenerate behavior, or crashes. These bypass the percentage threshold entirely.

### 5.4 Cross-version Elo promotion gate

Before committing, run cross-version Elo promotion gate:

1. Resolve the current version name BEFORE any mutation:
   ```bash
   python -c "from pathlib import Path; print(Path('bots/current/current.txt').read_text().strip())"
   ```
2. Run promotion check (requires SC2):
   ```bash
   uv run python -c "
   from src.orchestrator.ladder import check_promotion
   result = check_promotion(candidate='current', parent='<prior_version>', games=20, elo_threshold=10.0)
   print(f'Promoted: {result.promoted}, Reason: {result.reason}')
   "
   ```
3. If `result.promoted` is True: `snapshot_current()` was already called by `check_promotion()`.
   Proceed to commit with `ADVISED_AUTO=1`.
4. If `result.promoted` is False: DO NOT commit. Log "iteration passed WR validation but
   failed Elo gate: <result.reason>". Skip to next iteration.

### 5.3 On failure

- **Training:** revert config files to pre-iteration state
- **Dev:** `/improve-bot` already handles branch cleanup and rollback
- Log the failure, increment consecutive-fail counter
- The failed improvement goes into iteration context so future iterations deprioritize it

---

## Phase 6: Report & Commit

### 6.1 Iteration summary

Append to `$LOGFILE`:
```markdown
## Iteration N

- **Selected improvement:** <title> (rank #R, type: training/dev)
- **Principle(s) addressed:** §X, §Y
- **What changed:** <concrete description>
- **Validation result:** PASS/FAIL
- **Metrics:** win rate X/N → Y/N, avg duration Xs → Ys
- **Wall clock used:** Xm of Yh remaining
```

### 6.2 Repo update

If the iteration passed and produced changes:

Before committing, set the sandbox enforcement env var:
```bash
export ADVISED_AUTO=1
```

Include `[advised-auto]` in the commit message to mark it as an autonomous commit:
```bash
git add -A
git commit -m "[advised-auto] improve-bot-advised: <improvement title> (iteration N)"
git push origin master
```

After committing, unset the env var:
```bash
unset ADVISED_AUTO
```

For significant milestones, run `/repo-update` to update README and docs.

### 6.3 Training soak (2 games)

After a passed iteration, run a quick 2-game training soak so the PPO model sees the new behavior and a checkpoint is created. This keeps the neural policy in sync with rule-based code changes and populates the dashboard Training/Checkpoints tabs.

**Important:** Shut down the existing API-only backend before starting the daemon, then restart API-only after the soak. Never run two server processes simultaneously — duplicate processes cause the dashboard to flicker offline as they fight over port 8765.

```bash
# 1. Shut down existing API-only backend
curl -s -X POST http://localhost:8765/api/shutdown || true
# Wait for port to free (max 10s)
for i in 1 2 3 4 5; do
    python -c "import socket; s=socket.socket(); r=s.connect_ex(('127.0.0.1',8765)); s.close(); exit(0 if r!=0 else 1)" && break
    sleep 2
done

# 2. Start backend with daemon (daemon auto-runs games + trains)
DEBUG_ENDPOINTS=1 PYTHONUNBUFFERED=1 uv run python -m alpha4gate.runner --serve --daemon 2>&1 &
DAEMON_PID=$!

# Wait for 2 games to complete — poll training.db game count
SOAK_START=$(date +%s)
INITIAL_GAMES=$(python -c "import sqlite3; c=sqlite3.connect('data/training.db'); print(c.execute('SELECT COUNT(*) FROM games').fetchone()[0]); c.close()")
while true; do
    SOAK_ELAPSED=$(( $(date +%s) - SOAK_START ))
    if [ $SOAK_ELAPSED -ge 300 ]; then echo "Training soak timeout (5m)"; break; fi
    CURRENT=$(python -c "import sqlite3; c=sqlite3.connect('data/training.db'); print(c.execute('SELECT COUNT(*) FROM games').fetchone()[0]); c.close()")
    if [ $((CURRENT - INITIAL_GAMES)) -ge 2 ]; then break; fi
    sleep 10
done

# --- MANDATORY SOAK CLEANUP (always runs, even on timeout) ---
# 1. Graceful shutdown via API
curl -s -X POST http://localhost:8765/api/shutdown || true
sleep 2

# 2. Force-kill any remaining Python/uv runner processes spawned by the soak.
#    Match on the --daemon flag to avoid killing non-daemon backends.
powershell.exe -Command "Get-WmiObject Win32_Process | Where-Object { \$_.CommandLine -match 'alpha4gate.*--daemon' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }"

# 3. Verify port 8765 is free before continuing
for attempt in 1 2 3 4 5; do
    python -c "import socket; s=socket.socket(); r=s.connect_ex(('127.0.0.1',8765)); s.close(); exit(0 if r!=0 else 1)" && break
    sleep 2
done
```

**Budget guard:** Cap the training soak at 5 minutes wall-clock. The `SOAK_ELAPSED` check inside the loop enforces this.

**Skip condition:** If the iteration was training-only (config changes, no code), skip the soak — the daemon will pick up config changes on its own next time it runs.

**Restart API-only backend after soak:** Once the daemon is confirmed dead and port 8765 is free, restart the API-only backend so the dashboard stays alive for the next iteration:

```bash
# Restart API-only backend (no daemon) for dashboard monitoring
DEBUG_ENDPOINTS=1 PYTHONUNBUFFERED=1 uv run python -m alpha4gate.runner --serve 2>&1 &
BACKEND_PID=$!
# Verify it's up
for i in 1 2 3 4 5; do
    python -c "import socket; s=socket.socket(); r=s.connect_ex(('127.0.0.1',8765)); s.close(); exit(0 if r==0 else 1)" && break
    sleep 1
done
```

**Verification gate:** Do NOT proceed to Phase 7 until the API-only backend is confirmed running on port 8765. A missing backend means the dashboard goes dark for the rest of the run.

---

## Phase 7: Cleanup & Loop

### 7.1 Process cleanup between iterations

Between iterations, ensure **all** processes from the prior iteration are stopped. Orphaned processes cause port conflicts, SC2 connection errors, and leaked memory. Run every step — don't skip even if you think the process is already dead.

```bash
# 1. Stop any backend/daemon via API (idempotent, safe if nothing is listening)
curl -s -X POST http://localhost:8765/api/shutdown || true
sleep 2

# 2. Force-kill any Python processes running alpha4gate (daemon, runner, batch)
#    This catches processes that didn't respond to /api/shutdown.
#    NEVER kill SC2_x64.exe — only Python/uv wrappers.
powershell.exe -Command "Get-WmiObject Win32_Process | Where-Object { \$_.CommandLine -match 'alpha4gate' -and \$_.CommandLine -notmatch 'SC2' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }"

# 3. Kill monitoring helpers (tail, grep, sleep) from log-watching phases
powershell.exe -Command "Get-Process -Name tail,grep,sleep -ErrorAction SilentlyContinue | Stop-Process -Force"

# 4. Use TaskStop on ALL background Bash tasks from this conversation
#    (game runners, log monitors, daemon processes)
```
Use `TaskStop` on every background task ID that was started during this iteration. Don't rely on them having exited — check and stop each one.

```bash
# 5. Verify port 8765 is free — block until it is (max 15 seconds)
for attempt in 1 2 3 4 5; do
    python -c "import socket; s=socket.socket(); r=s.connect_ex(('127.0.0.1',8765)); s.close(); exit(0 if r!=0 else 1)" && break
    echo "Port 8765 still in use, waiting..."
    sleep 3
done

# 6. Final verification — if port is STILL in use, log an error but continue
python -c "import socket; s=socket.socket(); r=s.connect_ex(('127.0.0.1',8765)); s.close(); exit(0 if r!=0 else 1)" || echo "WARNING: port 8765 still occupied — next iteration may fail"
```

**Hard rule:** Do NOT proceed to Phase 1 of the next iteration until step 5 confirms port 8765 is free. A lingering process on that port will cause every subsequent game launch to fail or conflict.

### 7.2 Prepare for next iteration

- Archive the current game logs (move to a dated subfolder or leave in place)
- Clear `data/decision_audit.json` for fresh advisor entries in next live observation
- Update iteration context with this iteration's results

### 7.3 Wall-clock check

```bash
ELAPSED=$(($(date +%s) - LOOP_START))
BUDGET_SECONDS=$((HOURS * 3600))
```

**If `ELAPSED < BUDGET_SECONDS`:** go to Phase 1 for the next iteration.
**If `ELAPSED >= BUDGET_SECONDS`:** proceed to Phase 8 (Morning Report).

### 7.4 Consecutive failure check

If 3 consecutive iterations failed (validation did not pass), stop the loop and proceed to Phase 8. Log that the loop stopped due to repeated failures.

---

## Phase 8: Morning Report

Write the final report to `$LOGFILE` and print to stdout.

### Report contents

```markdown
# Advised Improvement Run: $RUN_TS

## Summary
- **Duration:** Xh Ym (budget: Zh)
- **Iterations attempted:** N
- **Iterations passed:** M
- **Stop reason:** budget exhausted / 3 consecutive failures / user interrupt

## Baseline → Final Metrics
| Metric | Baseline | Final | Delta |
|--------|----------|-------|-------|
| Win rate (diff N) | X/10 | Y/10 | +/-Z |
| Avg game duration | Xs | Ys | +/-Zs |
| Reward trend | X | Y | +/-Z |
| Difficulty level | N | M | +/-K |

## Iteration Log
| # | Improvement | Type | Principles | Result | Win Rate Delta |
|---|-------------|------|------------|--------|----------------|
| 1 | Early scout reward | training | §6 | PASS | +2/10 |
| 2 | Chrono boost auto-cast | dev | §4 | FAIL | -3/10 |
| 3 | ... | ... | ... | ... | ... |

## Improvements Not Attempted
(Top suggestions that were generated but not selected or not reached)

## Suggested Next Action
- "Run another cycle at difficulty N+1"
- "Investigate why Chrono boost changes caused regression"
- etc.

## Tag Cheatsheet
```bash
git tag -l "advised/run/$RUN_TS/*"
git log "advised/run/$RUN_TS/baseline..master" --oneline
git reset --hard "advised/run/$RUN_TS/baseline"   # NUKE whole run
```
```

### GitHub issue

Create an umbrella issue labeled `advised-improvement-run` with the summary for phone-readable progress tracking.

### Final tag

```bash
git tag "advised/run/$RUN_TS/final"
git push origin "advised/run/$RUN_TS/final"
```

### Final cleanup

Shut down **every** process spawned during the run. This is the same sequence as Phase 7.1 but runs at end-of-run when nothing needs to survive.

```bash
# 1. Graceful shutdown via API
curl -s -X POST http://localhost:8765/api/shutdown || true
sleep 2

# 2. Force-kill ALL alpha4gate Python processes (daemon, runner, batch, serve)
powershell.exe -Command "Get-WmiObject Win32_Process | Where-Object { \$_.CommandLine -match 'alpha4gate' -and \$_.CommandLine -notmatch 'SC2' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }"

# 3. Kill monitoring helpers
powershell.exe -Command "Get-Process -Name tail,grep,sleep -ErrorAction SilentlyContinue | Stop-Process -Force"

# 4. Verify port is free
python -c "import socket; s=socket.socket(); r=s.connect_ex(('127.0.0.1',8765)); s.close(); exit(0 if r!=0 else 1)" || echo "WARNING: port 8765 still occupied after final cleanup"
```

5. Use `TaskStop` on **all** remaining background Bash tasks from this conversation.
6. Do NOT kill SC2_x64.exe.

---

## Reverting an Overnight Run

Every run is fully reversible. The baseline tag created in Phase 0.6 is your restore point.

### Quick revert (everything)

Roll back ALL changes from the run — code, config, and training data:

```bash
# 1. Find the run's baseline tag
git tag -l "advised/run/*"

# 2. Reset master to baseline
git reset --hard "advised/run/<RUN_TS>/baseline"
git push origin master --force-with-lease

# 3. Restore training config
cp data/reward_rules.pre-advised-<RUN_TS>.json data/reward_rules.json
cp data/hyperparams.pre-advised-<RUN_TS>.json data/hyperparams.json
```

### Partial revert (keep some iterations)

If iteration 3 was good but iteration 4 broke things, the iteration commits are sequential on master:

```bash
# See what each iteration did
git log "advised/run/<RUN_TS>/baseline..advised/run/<RUN_TS>/final" --oneline

# Revert to the state after iteration N
git revert <commit-sha-of-iteration-4>
# Or reset to the exact post-iteration-3 commit
git reset --hard <commit-sha-after-iteration-3>
git push origin master --force-with-lease
```

### Revert training-only changes

If only config files changed (no code), just restore the backups:

```bash
cp data/reward_rules.pre-advised-<RUN_TS>.json data/reward_rules.json
cp data/hyperparams.pre-advised-<RUN_TS>.json data/hyperparams.json
```

### Morning-after checklist

When reviewing an overnight run:
1. Read `$LOGFILE` (the run log in `documentation/soak-test-runs/advised-*.md`)
2. Check the iteration table — which passed, which failed, which were marginal
3. Run `git log "advised/run/<RUN_TS>/baseline..master" --oneline` to see what landed
4. Run a quick validation: `uv run pytest && uv run mypy src && uv run ruff check .`
5. If unhappy, use the quick revert above. If happy, delete the `.pre-advised-*` backup files

---

## Dashboard Control Panel Bridge

The dashboard has an **Advisor** tab that monitors and controls this loop via two JSON files in `data/`. You MUST read and write these files at the specified phase boundaries.

### State file: `data/advised_run_state.json`

**Write this file after every phase boundary** to keep the dashboard in sync. Overwrite the entire file each time.

```json
{
  "run_id": "$RUN_TS",
  "status": "running",
  "phase": 2,
  "phase_name": "Strategic Analysis",
  "iteration": 3,
  "max_iterations": null,
  "games_per_cycle": 10,
  "difficulty": 1,
  "mode": "training",
  "hours_budget": 4,
  "elapsed_seconds": 3420,
  "baseline_win_rate": 0.7,
  "current_win_rate": 0.8,
  "iterations": [
    {"num": 1, "title": "Reward scouting", "result": "pass", "delta": "+10%"},
    {"num": 2, "title": "Fix supply block", "result": "fail", "delta": "-5%"}
  ],
  "current_improvement": "Chrono boost allocation",
  "fail_streak": 0,
  "updated_at": "2026-04-12T19:15:00Z"
}
```

**When to write:**
- Phase 0.4 (after systems start): `status: "running"`, `phase: 0`, `phase_name: "Bootstrap"`
- Phase 1.1 start: `phase: 1`, `phase_name: "Observing Games"`
- Phase 2.1 start: `phase: 2`, `phase_name: "Strategic Analysis"`
- Phase 3 start: `phase: 3`, `phase_name: "Selecting Improvement"`
- Phase 4 start: `phase: 4`, `phase_name: "Executing"`, set `current_improvement`
- Phase 5 start: `phase: 5`, `phase_name: "Validating"`
- Phase 6 start: `phase: 6`, `phase_name: "Reporting"`
- Phase 7 start: `phase: 7`, `phase_name: "Loop Decision"`
- Phase 8 end: `status: "completed"`, `phase: 8`, `phase_name: "Morning Report"`
- On any stop: `status: "stopped"`

Always update `elapsed_seconds`, `current_win_rate`, `iterations` array, and `updated_at` with each write.

### Control file: `data/advised_run_control.json`

**Read this file at three phase boundaries:**
1. **Before Phase 1** (before starting games) — apply `games_per_cycle`, `difficulty`, `fail_threshold`, `reward_rule_add`
2. **Before Phase 2** (before strategic analysis) — read `user_hint` for injection into the advisor prompt
3. **Phase 7** (loop decision) — check `stop_run`, `reset_loop`

```json
{
  "games_per_cycle": 3,
  "user_hint": "Try attack-walking at 4:00",
  "stop_run": false,
  "reset_loop": false,
  "difficulty": null,
  "fail_threshold": null,
  "reward_rule_add": null,
  "updated_at": "2026-04-12T19:10:00Z"
}
```

**How to apply each control signal:**

- **`games_per_cycle`** (non-null integer): Override `$GAMES_PER_CYCLE` for this and future iterations. Log: "Dashboard override: games_per_cycle set to N".
- **`difficulty`** (non-null integer): Override `$DIFFICULTY` for this and future iterations. Log the change.
- **`fail_threshold`** (non-null integer): Override `--fail-threshold` for this and future iterations.
- **`user_hint`** (non-null string): In Phase 2, prepend to the analysis prompt as: `"\n\n## Human Operator Guidance\n\nThe human operator has provided the following strategic hint. Consider this alongside the game data:\n\n> {user_hint}\n\n"`. After reading, clear the hint by writing `{"user_hint": null}` back to the control file (so it's not re-applied next iteration).
- **`stop_run`** (true): In Phase 7, skip the loop and proceed directly to Phase 8 (Morning Report). Log: "Dashboard requested stop".
- **`reset_loop`** (true): Revert to the baseline git tag (`advised/run/$RUN_TS/baseline`), restore config backups, clear iteration context, reset `fail_streak` to 0, reset `iteration` to 0, and restart from Phase 1. Clear the flag by writing `{"reset_loop": false}` back. Log: "Dashboard requested loop reset".
- **`reward_rule_add`** (non-null object with `id`, `description`, `reward`, `active`): Append the rule to `data/reward_rules.json` before starting games. Clear the signal by writing `{"reward_rule_add": null}` back. Log: "Dashboard added reward rule: {id}".

**If the control file does not exist or is unreadable, proceed with current settings — never block on missing control signals.**

---

## Safety Rails

All safety rails from `/improve-bot` apply here. Additionally:

- **One improvement at a time.** Never attempt multiple improvements in parallel within the same iteration. Clean attribution requires isolated changes.
- **Config backups.** Before modifying `reward_rules.json` or `hyperparams.json`, copy to `*.pre-advised-$RUN_TS.json`. Revert from these on failure.
- **No advisor during fast games.** In `--observe replay` mode, the advisor is OFF during gameplay. The rate limiter's 30-game-second interval would fire every few wall-clock seconds at max speed, overwhelming the Claude CLI subprocess.
- **`--self-improve-code` gates all code changes.** Without this flag, only training-type improvements (config/reward changes) are attempted. The skill will not create branches, merge, or push code without explicit authorization.
- **`--self-improve-code` path restriction.** When `--self-improve-code` is active, only files under `bots/current/` may be edited. Do not modify `src/orchestrator/`, `pyproject.toml`, `tests/`, `frontend/`, or `scripts/`. The pre-commit hook enforces this at commit time, but respect the boundary during editing as well — never open or write files outside `bots/current/` in dev-type improvements.
- **Iteration context is advisory, not authoritative.** If the context says "scouting was fixed in iteration 2" but the current game data still shows no scouting, trust the data.
- **Never kill SC2_x64.exe.** Only restart the Python daemon process if needed.
- **Wall-clock discipline.** Check `ELAPSED` vs `BUDGET_SECONDS` at every phase boundary, not just Phase 7. If the budget is exceeded mid-phase, finish the current atomic operation (e.g., complete a running game) then stop cleanly.

---

## Relationship to /improve-bot

`/improve-bot-advised` is the **outer strategic loop**. `/improve-bot` is a **building block** used for dev-type improvements.

```
/improve-bot-advised (outer loop)
  ├── Phase 1: Observe (run games)
  ├── Phase 2: Analyze (Claude + guiding principles)
  ├── Phase 3: Select (pick 1 improvement)
  ├── Phase 4: Execute
  │     ├── training-type → direct config edit + soak
  │     └── dev-type → /improve-bot <suggestion> --self-improve-code
  ├── Phase 5: Validate (run games, compare)
  ├── Phase 6: Report
  └── Phase 7: Loop or → Phase 8: Morning Report
```

The outer loop provides the **strategic intelligence** (what to improve) while `/improve-bot` provides the **execution machinery** (how to implement code changes safely).
