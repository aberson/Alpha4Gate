# FAQ

Quick answers for anyone looking at this system for the first time.

> **At a glance:** This page answers the questions a new observer — human or Claude
> session — would ask when first encountering Alpha4Gate. Start here if you want to
> understand the system without reading every wiki page.

---

## What is this?

Alpha4Gate is an **autonomous improvement platform** that uses StarCraft II as its
testing domain. A Protoss bot plays games, evaluates its performance, trains a neural
policy via PPO, and (eventually) promotes better models — all with full transparency
through a live dashboard.

The SC2 bot is one domain implementation. The training, evaluation, and monitoring
infrastructure is designed to be domain-agnostic (see
[domain-coupling.md](domain-coupling.md)).

## What is happening right now?

**Current state (updated 2026-04-09):**

- The bot plays SC2 at difficulty 1-3 reliably. Struggles at 4-5.
- Training is **manual** — a human runs `--train rl` from the CLI.
- No autonomous loop exists yet. The bot does not run unattended.
- Documentation and planning are in progress for the "always up" autonomous loop.
- Active plan: [always-up-plan.md](../plans/always-up-plan.md)

**To check live status:** If the bot is running, open the dashboard at
`http://localhost:3000` (Live tab shows real-time game state).

## What are the recent accomplishments?

| When | What | Impact |
|------|------|--------|
| Phase 1 | Rule-based bot (10 build steps) | Can play full games, macro + micro |
| Phase 2 | Deep learning pipeline (PPO + imitation) | Neural policy, hybrid mode, 100% win rate at difficulty 1 |
| Improvement 1 | Army coherence system | No more trickle attacks, staging + critical mass |
| Improvement 2 | Natural denial + critical mass | Attack enemy natural instead of ramp |
| Improvement 3 | Neural training pipeline | 83% win rate over 2 training cycles |
| Improvement 4 | Strategic command system | Human/AI commands mid-game, 3 modes |
| Improvement 5 | Defensive fortification | FORTIFY state, cannons + batteries |
| 2026-04-09 | Wiki + always-up plan | Full documentation, 5-phase roadmap |

## What's being worked on to improve over the current state?

The [always-up plan](../plans/always-up-plan.md) has five phases:

1. **Wiki & documentation** — Document everything so future sessions have context (done)
2. **Monitoring gaps** — Persist decision logs, make reward logging default, add
   improvement timelines to dashboard
3. **Autonomous training loop** — Scheduler daemon, training triggers, continuous eval,
   model promotion with rollback
4. **Transparency dashboard** — Training cycle status, recent improvements view,
   per-rule reward trends, alerting
5. **Domain abstraction** — Clean separation so the loop works with any domain

## How does the bot decide what to do?

Six-layer architecture. See [architecture.md](architecture.md) for the full picture.

Short version: A state machine picks a strategic state (OPENING, EXPAND, ATTACK, DEFEND,
LATE_GAME, FORTIFY). A neural policy can override this. Commands from humans or Claude
can force a state. Then macro/micro layers execute the strategy.

## How does training work?

Two stages. See [training-pipeline.md](training-pipeline.md) for details.

1. **Imitation pre-training** — the neural network clones the rule-based bot's decisions
   (behavior cloning, ~95% agreement)
2. **RL training** — PPO fine-tunes on actual game outcomes. Curriculum auto-advances
   difficulty when win rate hits 80%.

Both are CLI-triggered today. The always-up plan targets making this fully automatic.

## How does the bot know if it's improving?

Three mechanisms at different timescales. See
[evaluation-pipeline.md](evaluation-pipeline.md) for details.

- **Per-step:** 14 shaped reward rules (JSON-driven) give dense feedback
- **Per-game:** Win/loss stored in SQLite
- **Cross-game:** Sliding-window win rates (last 10/50/100 games)

Key gap: no per-checkpoint comparison. There's no "model v3 had 70%, v4 has 85%" view.

## What can I see in the dashboard?

Ten tabs. See [frontend.md](frontend.md) for full component breakdown.

- **Live** — real-time game state, resources, units, Claude advice, command input
- **Stats** — win/loss history by map and opponent
- **Replays** — browse replay files (parsing not fully implemented)
- **Decisions** — strategic state transition log
- **Training** — checkpoint list, win rates, reward rule editor
- **Loop** — daemon state, trigger evaluation, full daemon control panel
- **Advisor** — live status, loop controls, strategic hints for /improve-bot-advised runs
- **Improvements** — recent promotions/rollbacks, per-rule reward trends
- **Processes** — live process inventory and health
- **Alerts** — severity-filtered alert list with ack/dismiss

## How does Claude fit in?

Two roles:

1. **Mid-game advisor** — async subprocess call, rate-limited to 1 per 30 game-seconds.
   Provides strategic suggestions + optional commands. Non-blocking. See
   [claude-advisor.md](claude-advisor.md).
2. **Command interpreter** — Claude Haiku parses free-text commands that the regex
   parser can't handle (fallback in the command pipeline).

## Is the bot SC2-specific?

Partially. 66% of modules have zero SC2 imports. The entire learning pipeline (training,
evaluation, rewards, features, checkpoints) is domain-agnostic. SC2 coupling
concentrates in 9 modules (bot, connection, observer, macro, micro, scouting, executor).
See [domain-coupling.md](domain-coupling.md) for the full map.

## What's the tech stack?

| Layer | Tool |
|-------|------|
| Language | Python 3.12 |
| SC2 interface | burnysc2 v7.1.3 |
| AI advisor | Claude CLI (async subprocess) |
| Backend | FastAPI (REST + WebSocket) |
| Frontend | React + TypeScript + Vite |
| Deep learning | PyTorch + Stable Baselines 3 (PPO) |
| Training data | SQLite |
| Testing | pytest (500 tests), ruff, mypy strict |

## How many tests are there?

500 unit tests across 32 files. Zero type errors, zero lint violations. SC2 integration
tests are separate and require a running SC2 client. See [testing.md](testing.md).

## Where do I start if I want to work on this?

1. Read this FAQ (you're here)
2. Read [architecture.md](architecture.md) for the layer diagram
3. Read the wiki page for the specific system you're touching
4. Check [always-up-plan.md](../plans/always-up-plan.md) for the active roadmap
5. Read `CLAUDE.md` at the project root for commands and conventions

## What are the known limitations?

- **Wins at difficulty 1-3, struggles at 4-5.** The bot's decision-making at higher
  levels isn't sophisticated enough yet.
- **Training is slow.** Each RL cycle requires playing full SC2 games (no vectorized
  environments, no GPU parallelism).
- **No self-play.** The bot trains against SC2's built-in AI, not against other bots
  or copies of itself.
- **Claude advisor latency.** Subprocess call can take several seconds; advice often
  arrives after the situation has changed.
- **Dashboard is read-mostly.** You can view training metrics but can't trigger training
  from the UI (the endpoint is a placeholder).
- **Reward logging is opt-in.** Must pass `--reward-log` flag to get per-step reward
  breakdown.
