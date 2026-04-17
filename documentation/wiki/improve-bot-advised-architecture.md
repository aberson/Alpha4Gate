# /improve-bot-advised — Autonomous Self-Improvement Architecture

## The Big Idea

An AI agent that teaches itself to get better at a task — with zero human input.
It plays, watches itself fail, figures out why, writes a fix, proves the fix works,
and repeats. The task happens to be StarCraft II, but the loop is general.

---

## The Loop (top-level view)

```
                    ┌──────────────────────────────────────────────────────┐
                    │          /improve-bot-advised                        │
                    │       autonomous learning loop (4+ hours)            │
                    │                                                      │
  ┌─────────┐      │   ┌─────────┐    ┌─────────┐    ┌─────────┐         │
  │         │      │   │         │    │         │    │         │         │
  │   THE   │◄────────►│  PLAY   │───>│  THINK  │───>│  FIX    │         │
  │   TASK  │      │   │         │    │         │    │         │         │
  │         │      │   └─────────┘    └─────────┘    └────┬────┘         │
  │ (SC2)   │◄──┐  │                                      │              │
  │         │   │  │   ┌─────────┐    ┌─────────┐    ┌────▼────┐         │
  │         │   └─────►│  TRAIN  │◄───│ COMMIT  │◄───│  TEST   │         │
  │         │      │   │         │    │         │    │         │         │
  └─────────┘      │   └─────────┘    └─────────┘    └─────────┘         │
                    │         │                                            │
                    │         └──────── loop back to PLAY ─────────────►  │
                    │                   (or stop if time's up / 3 fails)  │
                    └──────────────────────────────────────────────────────┘
```

**Each box in one sentence:**

| Step | What happens | Who does it |
|------|-------------|-------------|
| **THE TASK** | A StarCraft II game runs. The bot plays, wins or loses, produces a replay log. | SC2 engine + bot |
| **PLAY** | Run N games, collect results: win/loss, timelines, army stats, economy stats. | Orchestrator |
| **THINK** | Claude reads the game data + a strategy reference doc, diagnoses what went wrong, proposes ranked fixes. | Claude LLM |
| **FIX** | Pick 1 fix (random from top 3), apply it — either edit config or write code. | Claude Code |
| **TEST** | Run N more games with the fix applied. Did win rate hold or improve? | Orchestrator |
| **COMMIT** | If TEST passed: git commit + push. If TEST failed: revert, try again. | Claude Code |
| **TRAIN** | Short PPO training soak — neural net learns from the new games. Promote checkpoint if better, rollback if worse. | Training daemon |

---

## The Task (one box)

```
 ┌──────────────────────────────────────────────────────────┐
 │  THE TASK — StarCraft II game                            │
 │                                                          │
 │  Input:   bot code + neural policy + reward config       │
 │  Process: bot plays a full 1v1 game against AI opponent  │
 │  Output:  win/loss, replay log, game stats, duration     │
 │                                                          │
 │  The learning loop never touches SC2 internals.          │
 │  It only reads the output and modifies the input.        │
 └──────────────────────────────────────────────────────────┘
```

The loop treats the task as a black box: put code + config in, get a
win/loss + telemetry out. SC2 is the concrete task today, but the same
PLAY → THINK → FIX → TEST → COMMIT → TRAIN structure would work for any
measurable task with machine-readable output.

---

## Detailed Phase Walkthrough

### 0. Bootstrap (one-time, before the loop starts)

```
 You run:  /improve-bot-advised --mode dev --self-improve-code --hours 4

 ┌──────────────────────────────────────┐
 │  Parse flags & resolve defaults      │
 │  Check git is clean (stash if not)   │
 │  Run quality gates (pytest/mypy/ruff)│
 │  Record baseline win rate + SHA      │
 │  Start backend API (port 8765)       │
 │  Play 1 seed game to verify pipeline │
 │  Snapshot dashboard, create git tag  │
 └──────────────────────────────────────┘
```

### 1. PLAY — Run games, collect evidence

```
 ┌──────────────────────────────────────────────────────┐
 │                                                      │
 │   Orchestrator launches N games against THE TASK     │
 │                                                      │
 │   Two speed modes:                                   │
 │    • Fast (default): max-speed, post-hoc analysis    │
 │    • Live (optional): realtime, Claude advises       │
 │                        the bot during gameplay        │
 │                                                      │
 │   Output collected per game:                         │
 │    • Win or loss                                     │
 │    • Timeline (build order, army events, expansions) │
 │    • Economy & army stats at key moments             │
 │    • Game duration                                   │
 │                                                      │
 └──────────────────────────────────────────────────────┘
```

### 2. THINK — Claude diagnoses the weakness

```
 ┌──────────────┐  ┌───────────────┐  ┌──────────────────┐
 │  Game data   │  │ Prior context │  │ Strategy          │
 │  from PLAY   │+ │ (accumulated  │+ │ reference doc     │
 │              │  │  findings)    │  │ (guiding          │
 │              │  │               │  │  principles)      │
 └──────┬───────┘  └───────┬───────┘  └────────┬─────────┘
        └──────────────────┼────────────────────┘
                           v
                   ┌───────────────┐
                   │  Claude LLM   │
                   │               │
                   │  "Here's what │
                   │   went wrong  │
                   │   and how to  │
                   │   fix it"     │
                   └───────┬───────┘
                           v
              Ranked list of improvements:
              ┌──────────────────────────────────┐
              │ #1 "Add anti-air timing"  [dev]  │
              │ #2 "Expand faster"        [dev]  │
              │ #3 "Tune upgrade reward"  [train]│
              │ #4 ...                           │
              └──────────────────────────────────┘
              Each tagged: "training" (config) or "dev" (code)
```

### 3. FIX — Apply one improvement

```
 Randomly pick 1 of top 3 (avoids retrying same failing fix)
        │
        ├─── Config change ─────────────────────────┐
        │    Edit reward_rules.json or               │
        │    hyperparams.json directly               │
        │                                            │
        └─── Code change ──────────────────────┐     │
             (requires --self-improve-code)     │     │
             ┌──────────────────────────────┐   │     │
             │ 1. Create feature branch     │   │     │
             │ 2. Claude Code writes code   │   │     │
             │ 3. Quality gates must pass:  │   │     │
             │    pytest + mypy + ruff      │   │     │
             └──────────────────────────────┘   │     │
                                                v     v
```

### 4. TEST — Validate the fix against THE TASK

```
 Run N games with fix applied
        │
        v
 ┌──────────────────────────────────────┐
 │  Compare win rate vs before          │
 │                                      │
 │  Didn't get worse?  ─── YES ───> PASS  ──> COMMIT
 │        │                  (within 30%
 │        │                   threshold)
 │        NO
 │        v
 │  FAIL ──> revert the fix
 │           increment fail counter
 │           (3 consecutive fails = stop entire run)
 │           loop back to PLAY
 └──────────────────────────────────────┘
```

### 5. COMMIT — Lock in the improvement

```
 ┌──────────────────────────────────────┐
 │  git add + commit + push            │
 │  Log iteration to run report        │
 └──────────────────────────────────────┘
```

### 6. TRAIN — Neural net absorbs the change

```
 ┌──────────────────────────────────────────────────────────┐
 │  Short PPO training soak (2 games)                       │
 │                                                          │
 │  Game ──> PPO gradient update ──> save checkpoint        │
 │                                                          │
 │  Then: Promotion Gate                                    │
 │   • Eval new checkpoint vs current best (inference only) │
 │   • Better?  ──> promote, maybe increase difficulty      │
 │   • Worse?   ──> rollback to previous best               │
 └──────────────────────────────────────────────────────────┘
                    │
                    v
            Loop back to PLAY
            (until wall-clock budget exhausted,
             3 consecutive fails, or dashboard stop signal)
```

### 7. Morning Report (once, at the end)

```
 ┌─────────────────────────────────────────────┐
 │  Final report:                              │
 │   • Duration, iterations attempted/passed   │
 │   • Baseline → final win rate               │
 │   • Table of each iteration + outcome       │
 │   • Unattempted improvements (backlog)      │
 │   • Suggested next action                   │
 │                                             │
 │  Create GitHub issue + final git tag        │
 │  Full process cleanup                       │
 └─────────────────────────────────────────────┘
```

---

## Where Games Are Played (every touchpoint)

The learning loop touches THE TASK at exactly three points:

```
 THE TASK
    ▲
    │
    ├── PLAY   (Phase 1)  Observation games — "how are we doing?"
    │                      N games, collect stats, no changes applied yet
    │
    ├── TEST   (Phase 4)  Validation games — "did the fix help?"
    │                      N games after applying one fix, compare to before
    │
    └── TRAIN  (Phase 6)  Training games — "let the neural net learn"
                           2 games under PPO, gradient updates, checkpoint eval
```

Every other phase — THINK, FIX, COMMIT — never runs a game.
They operate purely on code, config, and data.

---

## Control & Safety

| Rail | What it prevents |
|---|---|
| Quality gates (pytest/mypy/ruff) | Broken code from shipping |
| Auto-rollback | Bad fixes persisting |
| Wall-clock budget | Runaway overnight sessions |
| 3-fail cap | Thrashing on unfixable problems |
| Crash-aware eval | Crashed games silently counted as losses |
| Dashboard override | Operator can pause/stop/tune anytime via browser |
| Deterministic eval | PPO exploration noise doesn't fool the promotion gate |

---

## Key Files

| Component | File | Role |
|---|---|---|
| Outer loop | `.claude/skills/improve-bot-advised/SKILL.md` | Strategic orchestrator, zero-input |
| Inner dev loop | `.claude/skills/improve-bot/SKILL.md` | Branch, build, gate, merge, rollback |
| Game runner | `bots/v0/runner.py` | CLI entry point, launches games |
| Claude Advisor | `bots/v0/claude_advisor.py` | Real-time advice during live games |
| Training Daemon | `bots/v0/learning/daemon.py` | Background PPO training trigger |
| Training Loop | `bots/v0/learning/trainer.py` | PPO cycles: game -> learn -> save |
| Evaluator | `bots/v0/learning/evaluator.py` | Deterministic eval (no gradients) |
| Promotion Gate | `bots/v0/learning/promotion.py` | Compare checkpoints, promote if better |
| Rollback Monitor | `bots/v0/learning/rollback.py` | Detect regression, revert |
| Strategy Reference | `documentation/protoss_sc2_guiding_principles.md` | What Claude judges games against |
