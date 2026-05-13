# Alpha4Gate — Project Instructions

## Project overview

SC2 Protoss bot: rule-based strategy + PPO neural policy + Claude AI advisor.
Goal: AI-vs-AI competition with transparent model introspection and autonomous self-improvement.

## Stack

- Python 3.12, uv, burnysc2 v7.1.3, FastAPI, React+TypeScript+Vite
- Deep learning: PyTorch, Stable Baselines 3 (PPO), SQLite for training data
- Testing: pytest (1361 unit tests) + vitest (143 frontend tests), ruff, mypy strict mode

## Commands

```bash
uv sync                                    # Install deps
uv run python -m bots.v0 --role solo --map Simple64  # Run game
uv run python -m bots.v0.runner --serve            # Dashboard API only
uv run pytest                              # 1361 unit tests
uv run pytest -m sc2                       # SC2 integration tests (SC2 must be running)
uv run ruff check .                        # Lint
uv run mypy src bots --strict              # Type check
cd frontend && npm run dev                 # Frontend dev server (:3000 -> :8765)
bash scripts/start-dev.sh                  # Start backend + frontend together (used by build-step --ui)
```

## Directory layout

- `bots/v0/` — 46 Python modules (bot, decision engine, commands/, learning/). The production bot code.
- `bots/current/` — thin pointer package (MetaPathFinder alias to `bots/v0/`)
- `src/orchestrator/` — version registry, contracts, subprocess self-play stubs
- `tests/` — 50 test files (all import from `bots.v0.*`)
- `frontend/` — React dashboard (LiveView, CommandPanel, TrainingDashboard, etc.)
- `scripts/` — live-test.sh, analyze_rewards.py, evaluate_model.py, etc.
- `documentation/wiki/` — project wiki (start with `index.md` for system diagram + page map)
- `documentation/plans/` — active plans (alpha4gate-master-plan.md)
- `documentation/archived/` — completed plans (Phase 1, Phase 2, improvement cycles)
- `bots/v0/data/` — per-version state: training.db, checkpoints/, reward_rules.json, hyperparams.json
- `data/` — legacy shared state: decision_audit.json, improvement_log.json, phase0_spike/ (gitignored)
- `logs/` — JSONL game logs (gitignored)

## Architecture

Six layers: Claude Advisor -> Neural Engine -> Strategy (state machine) -> Command System -> Tactics -> Coherence -> Micro.
Three command modes: AI-Assisted, Human Only, Hybrid.
WebSocket endpoints: /ws/game, /ws/decisions, /ws/commands.

## Current state

All production code lives in `bots/v0/` (Phase 1 bots-v0-migration complete). `src/alpha4gate/` no longer exists.
All Phase 1 (rule-based) and Phase 2 (deep learning) features complete.
Five improvement cycles done: army coherence, natural denial, neural training, strategic commands, defensive fortification.
Wins reliably at difficulty 1-3, struggles at 4-5.
Active plan: `documentation/plans/alpha4gate-master-plan.md` — platform + full-stack versioning + AlphaStar-style PPO upgrades. Always-up Phases 1–4.5 (daemon, evaluator, promotion gate, rollback, 10-tab dashboard) are the Baseline; full history in `documentation/archived/always-up-plan.md`.
Master plan Phases A, 0, 1, 2, 3, 4, 5 all COMPLETE. Phase 4 added Elo ladder (`src/orchestrator/ladder.py`), cross-version promotion gate, CLI (`scripts/ladder.py`), `/api/ladder` endpoint, and Ladder dashboard tab (10th). Phase 5 added sandbox enforcement (`scripts/check_sandbox.py` + `.pre-commit-config.yaml`) and wired `check_promotion()` + `[advised-auto]` into `/improve-bot-advised`. Phase 9 (improve-bot-evolve) operational, v0→v1→v2 auto-promoted overnight 2026-04-23; v3→v4 promoted 2026-04-29 after stack-apply unblock (`e7fb758`). Phase 8 (headless Linux training infrastructure) Steps 1-10 SHIPPED 2026-04-29 (Linux CI + SC2PATH resolver + `Dockerfile` + `.dockerignore` + `documentation/wiki/cloud-deployment.md`); Step 11 (24h Linux evolve soak) pending; Step 12 (cloud dry-run) removed. Phase N (winprob heuristic + give-up trigger) COMPLETE 2026-04-27 — `bots/v0/learning/winprob_heuristic.py`, `bots/v0/give_up.py`, `transitions.win_prob` column, every-10-step INFO log, `Alpha4GateBot._maybe_resign`. Live in `bots/v0/` and folded into `bots/v3/`+`v4/` via successive promotions; production runtime via `bots/current` → v4.
Wiki: `documentation/wiki/index.md` — system diagram and deep-dive pages.

**Important:** Do NOT import `bots.current` or `bots.<version>` from `src/orchestrator/` — triggers MetaPathFinder loop. Registry reads paths via pathlib.

## SC2 requirements

- StarCraft II must be installed at `C:\Program Files (x86)\StarCraft II\`
- Maps from Blizzard CDN (not GitHub — those are Git LFS pointers)
- SC2 client must be running for integration tests (`pytest -m sc2`)

## Rules

- [`.claude/rules/frontend-ui.md`](.claude/rules/frontend-ui.md) — dashboard UI conventions.
- [`.claude/rules/bot-runtime.md`](.claude/rules/bot-runtime.md) — backend `--serve` and daemon lifecycle, SC2 client invariants (process management, 2-client cap, perception-affecting debug flags), burnysc2 combineable abilities, per-version vs cross-version data dirs.
- [`.claude/rules/evolve.md`](.claude/rules/evolve.md) — reading evolve run state, pre-launch hygiene, snapshot import isolation, dev-apply sub-agent sanitization, fitness noise floor, training-imp pool restriction.
- [`.claude/rules/wsl-evolve.md`](.claude/rules/wsl-evolve.md) — eight setup gotchas for Linux-SC2 evolve substrate. Each one breaks evolve differently; applying only a subset gives partial-success symptoms.
