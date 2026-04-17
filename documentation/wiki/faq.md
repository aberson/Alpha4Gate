# FAQ

Quick answers for anyone looking at this system for the first time.

> **At a glance:** This page answers what a new observer — human or Claude
> session — would ask when first encountering Alpha4Gate. Start here if you
> want to understand the system without reading every wiki page.

---

## What is this?

Alpha4Gate is an **autonomous AI agent that teaches itself to get better at a
task**. The task happens to be StarCraft II, but the loop is general:

- It plays games.
- Claude watches them and diagnoses what's going wrong.
- Claude writes a fix — either a config tweak or actual code.
- It validates the fix against new games.
- It commits, trains the neural policy, and loops.

Two loops run at different timescales:
- **Outer loop** (hours): improves the bot's code and config via `/improve-bot-advised`.
- **Inner loop** (minutes): improves the neural policy via the `TrainingDaemon`, with automated promotion/rollback.

See [index.md](index.md) for the system diagram, [improve-bot-advised-architecture.md](improve-bot-advised-architecture.md) for the outer loop.

## What is happening right now?

**To check live status:** if the bot is running, open `http://localhost:3000`. The nav bar shows a green dot when an advised run is active.

- **Live tab** — current game
- **Advisor tab** — current loop phase, iteration, win-rate delta vs baseline
- **Processes tab** — is everything actually alive

## What are the recent accomplishments?

| When | What |
|------|------|
| Phase 1 | Rule-based bot (10 build steps) — full games, macro + micro |
| Phase 2 | Deep learning pipeline (PPO + imitation) — hybrid mode |
| Phase 3 | Always-up autonomous training loop — daemon, promotion gate, rollback |
| Phase 4 | Transparency dashboard — 10 tabs (incl. Ladder), alerts |
| Phase A | Imitation-init + LSTM + KL-to-rules (on branch `feat/lstm-kl-imitation`) |
| 2026-04 | `/improve-bot-advised` skill — hours-long autonomous sessions that write code |

First diff-5 hybrid win landed mid-April after a curriculum retrain.

## What's being worked on now?

The active plan is [alpha4gate-master-plan.md](../plans/alpha4gate-master-plan.md). Current focus: AlphaStar-style PPO upgrades (LSTM memory, KL regularization to rule-based policy, imitation initialization) via branch `feat/lstm-kl-imitation`. The always-up baseline (Phases 1–4.5) is done.

## How does the bot decide what to do?

Six layers ([architecture.md](architecture.md)):
1. Claude Advisor (optional, mid-game)
2. Neural Engine (PPO, optional override)
3. Decision Engine (state machine — OPENING/EXPAND/ATTACK/DEFEND/LATE_GAME/FORTIFY)
4. Commands (human/AI text commands → actions)
5. Tactics (macro, fortification, build backlog)
6. Coherence + Micro (army grouping, per-unit combat)

## How does training work?

Two stages ([training-pipeline.md](training-pipeline.md)):

1. **Imitation pre-training** — clone the rule-based bot via behavior cloning (~95% agreement)
2. **RL training** — PPO fine-tunes on game outcomes; curriculum auto-advances difficulty at 80% win rate

The `TrainingDaemon` background thread triggers RL cycles autonomously based on
transitions-since-last-run and time-since-last-run. Each cycle ends with a
promotion gate: the new checkpoint is evaluated against the current best on a
deterministic inference-only eval, and promoted only if strictly better.
`RollbackMonitor` reverts a regression.

## How does the bot know if it's improving?

Four mechanisms at different timescales ([evaluation-pipeline.md](evaluation-pipeline.md)):

- **Per-step:** 63 shaped reward rules (JSON-driven) give dense feedback
- **Per-game:** Win/loss stored in SQLite (`bots/v0/data/training.db`)
- **Cross-game:** Sliding-window win rates (last 10/50/100 games)
- **Per-checkpoint:** `ModelEvaluator` runs N inference-only eval games; `PromotionManager` compares new vs best

## What can I see in the dashboard?

Ten tabs ([frontend.md](frontend.md)):

| Tab | What it shows |
|---|---|
| Live | Real-time game state, resources, units, Claude advice |
| Stats | Win rates by difficulty, browsable game history, per-game reward timeline |
| Decisions | Strategic state transition log |
| Training | Checkpoint list, win rates, reward rule editor |
| Loop | Daemon state + trigger evaluation |
| Advisor | `/improve-bot-advised` phase, iteration, operator control panel |
| Improvements | Promotions / rollbacks, per-rule reward trends, iteration log |
| Processes | Live process inventory, port bindings, state-file contents |
| Alerts | Severity-filtered alerts (7 client-side rules) |
| Ladder | Elo standings + head-to-head grid across bot versions |

## How does Claude fit in?

Three roles:

1. **Outer-loop strategist** — the `/improve-bot-advised` skill uses Claude to diagnose games against the Protoss guiding principles and write fixes (THINK + FIX phases).
2. **Mid-game advisor** — async subprocess call, rate-limited to 1 per 30 game-seconds, optional live-mode commentary during PLAY. See [claude-advisor.md](claude-advisor.md).
3. **Command interpreter** — Claude Haiku parses free-text commands the regex parser can't handle.

## Is the bot SC2-specific?

Partially. The learning pipeline (training, evaluation, rewards, features, checkpoints, promotion, rollback) is domain-agnostic. SC2 coupling concentrates in a handful of modules (bot, connection, observer, macro, micro, scouting, executor). See [domain-coupling.md](domain-coupling.md).

The `/improve-bot-advised` loop treats SC2 as an opaque task: code + config go in, win/loss + stats come out. The same loop would work for any measurable task with machine-readable output.

## What's the tech stack?

| Layer | Tool |
|-------|------|
| Language | Python 3.12, uv |
| SC2 interface | burnysc2 v7.1.3 |
| AI | Claude CLI (async subprocess) + Claude Code skills |
| Backend | FastAPI (REST + WebSocket) |
| Frontend | React + TypeScript + Vite |
| Deep learning | PyTorch, Stable Baselines 3 (PPO), recurrent PPO + custom KL variants |
| Training data | SQLite |
| Testing | pytest (864 unit tests), ruff, mypy strict |

## How many tests are there?

864 unit tests across 48 test files. Zero type errors, zero lint violations. SC2 integration tests are separate (`pytest -m sc2`) and require a running SC2 client. See [testing.md](testing.md).

## Where do I start if I want to work on this?

1. Read this FAQ (you're here)
2. Read [improve-bot-advised-architecture.md](improve-bot-advised-architecture.md) for the autonomous loop
3. Read [architecture.md](architecture.md) for the SC2 bot layers
4. Read the wiki page for the specific system you're touching
5. Check [alpha4gate-master-plan.md](../plans/alpha4gate-master-plan.md) for the active roadmap
6. Read `CLAUDE.md` at the project root for commands and conventions

## What are the known limitations?

- **Wins at difficulty 1-3 reliably; struggles at 4-5.** Pure-PPO regressed to 5% WR on a recent diff-5 soak.
- **Training is slow.** Each RL cycle plays full SC2 games (no vectorized envs, no GPU parallelism).
- **No self-play.** The bot trains against SC2's built-in AI.
- **Claude advisor latency.** Subprocess call can take several seconds; advice often arrives after the situation has changed.
- **Claude advisor fires only once per game/cycle.** Known issue, still under investigation.
- **No email/push alerting.** All alerts are dashboard-only; overnight failures become visible only after the morning report issue is created.
- **Training WR is noisy.** PPO exploration noise means training win rate is not a learning signal; use the deterministic `ModelEvaluator` output instead.
