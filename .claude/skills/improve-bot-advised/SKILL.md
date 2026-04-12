---
name: improve-bot-advised
description: Autonomous advisor-driven improvement loop. Runs games, reviews replays with Claude strategic analysis against Protoss guiding principles, prioritizes improvements, and executes them via /improve-bot — repeating for hours to iteratively strengthen the bot. Designed for overnight unattended runs.
user-invocable: true
argument: Optional flags only — no free-text suggestion needed. Flags: `--mode training|dev|both` (default both), `--observe replay|live` (default replay), `--games N` (default 10), `--hours N` (default 4), `--difficulty N` (default current), `--fresh` (discard prior iteration context), `--self-improve-code` (required for dev mode code changes).
---

# /improve-bot-advised

Autonomous improvement loop that combines Claude strategic analysis with the existing `/improve-bot` skill. The outer loop observes games, analyzes replays against the Protoss guiding principles, selects one high-impact improvement, and delegates execution to `/improve-bot`. Repeats until the wall-clock budget is exhausted.

**Design goal:** `/improve-bot-advised` in a fresh context window with no additional input should produce a measurably improved bot after a few hours.

---

## Flags

| Flag | Values | Default | Purpose |
|------|--------|---------|---------|
| `--mode` | `training`, `dev`, `both` | `both` | What kind of improvements to attempt |
| `--observe` | `replay`, `live` | `replay` | How games are observed (see §Observation Modes) |
| `--games` | integer | `10` | Games per observation cycle |
| `--hours` | integer | `4` | Total wall-clock budget |
| `--difficulty` | integer | current curriculum | SC2 AI difficulty level |
| `--fresh` | flag | off | Discard prior iteration context; start analysis fresh each cycle |
| `--self-improve-code` | flag | off | **Required** for `--mode dev` or `--mode both`. Gates autonomous source-code changes. Without it, only `training` mode is allowed. |
| `--fail-threshold` | integer (%) | `30` | Win rate drop % that triggers a fail. Highly tunable — see §Validation Philosophy |

**Flag validation:**
- `--mode dev` or `--mode both` without `--self-improve-code` → error, stop and explain
- `--mode training` ignores `--self-improve-code` (no code changes in training-only mode)

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

### 0.1 Parse flags

Parse all flags from the argument. Apply validation rules. If `--mode` is `dev` or `both` and `--self-improve-code` is not set, stop with a clear error message.

### 0.2 Pre-flight checks

```bash
# Git state
git status                            # must be clean
git rev-parse --abbrev-ref HEAD       # should be master
git fetch origin && git status -sb    # up to date

# SC2
ls "C:/Program Files (x86)/StarCraft II/Versions/" # SC2 installed

# Tools
uv run pytest --co -q 2>&1 | tail -1              # tests discoverable
uv run ruff check . --quiet                        # lint clean
uv run mypy src --quiet                            # types clean
```

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

```bash
export PYTHONUNBUFFERED=1

# Start backend (headless by default, daemon ON for training)
DEBUG_ENDPOINTS=1 PYTHONUNBUFFERED=1 uv run python -m alpha4gate.runner --serve --daemon 2>&1 | tee -a "$LOGFILE" &
BACKEND_PID=$!

# Start frontend (optional — skip in headless replay mode unless user wants it)
# cd frontend && npm run dev &
```

### 0.5 Seed game

Run 1 game to verify the full pipeline works before entering the loop. If it fails, stop and report — don't burn the budget on a broken setup.

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
```bash
git add -A
git commit -m "improve-bot-advised: <improvement title> (iteration N)"
git push origin master
```

For significant milestones, run `/repo-update` to update README and docs.

---

## Phase 7: Cleanup & Loop

### 7.1 Prepare for next iteration

- Archive the current game logs (move to a dated subfolder or leave in place)
- Clear `data/decision_audit.json` for fresh advisor entries in next live observation
- Update iteration context with this iteration's results

### 7.2 Wall-clock check

```bash
ELAPSED=$(($(date +%s) - LOOP_START))
BUDGET_SECONDS=$((HOURS * 3600))
```

**If `ELAPSED < BUDGET_SECONDS`:** go to Phase 1 for the next iteration.
**If `ELAPSED >= BUDGET_SECONDS`:** proceed to Phase 8 (Morning Report).

### 7.3 Consecutive failure check

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

## Safety Rails

All safety rails from `/improve-bot` apply here. Additionally:

- **One improvement at a time.** Never attempt multiple improvements in parallel within the same iteration. Clean attribution requires isolated changes.
- **Config backups.** Before modifying `reward_rules.json` or `hyperparams.json`, copy to `*.pre-advised-$RUN_TS.json`. Revert from these on failure.
- **No advisor during fast games.** In `--observe replay` mode, the advisor is OFF during gameplay. The rate limiter's 30-game-second interval would fire every few wall-clock seconds at max speed, overwhelming the Claude CLI subprocess.
- **`--self-improve-code` gates all code changes.** Without this flag, only training-type improvements (config/reward changes) are attempted. The skill will not create branches, merge, or push code without explicit authorization.
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
