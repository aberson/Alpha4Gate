# Alpha4Gate — Project Instructions

## Project overview

SC2 Protoss bot: rule-based strategy + PPO neural policy + Claude AI advisor.
Goal: AI-vs-AI competition with transparent model introspection and autonomous self-improvement.

## Stack

- Python 3.12, uv, burnysc2 v7.1.3, FastAPI, React+TypeScript+Vite
- Deep learning: PyTorch, Stable Baselines 3 (PPO), SQLite for training data
- Testing: pytest (829 unit tests) + vitest (126 frontend tests), ruff, mypy strict mode

## Commands

```bash
uv sync                                    # Install deps
uv run python -m alpha4gate.runner --map Simple64  # Run game
uv run python -m alpha4gate.runner --serve         # Dashboard API only
uv run pytest                              # 682 unit tests
uv run pytest -m sc2                       # SC2 integration tests (SC2 must be running)
uv run ruff check .                        # Lint
uv run mypy src                            # Type check
cd frontend && npm run dev                 # Frontend dev server (:3000 -> :8765)
bash scripts/start-dev.sh                  # Start backend + frontend together (used by build-step --ui)
```

## Directory layout

- `src/alpha4gate/` — 46 Python modules (bot, decision engine, commands/, learning/)
- `tests/` — 45 test files
- `frontend/` — React dashboard (LiveView, CommandPanel, TrainingDashboard, etc.)
- `scripts/` — live-test.sh, analyze_rewards.py, evaluate_model.py, etc.
- `documentation/wiki/` — project wiki (start with `index.md` for system diagram + page map)
- `documentation/plans/` — active plans (alpha4gate-master-plan.md)
- `documentation/archived/` — completed plans (Phase 1, Phase 2, improvement cycles)
- `data/` — stats, training.db, checkpoints (gitignored)
- `logs/` — JSONL game logs (gitignored)

## Architecture

Six layers: Claude Advisor -> Neural Engine -> Strategy (state machine) -> Command System -> Tactics -> Coherence -> Micro.
Three command modes: AI-Assisted, Human Only, Hybrid.
WebSocket endpoints: /ws/game, /ws/decisions, /ws/commands.

## Current state

All Phase 1 (rule-based) and Phase 2 (deep learning) features complete.
Five improvement cycles done: army coherence, natural denial, neural training, strategic commands, defensive fortification.
Wins reliably at difficulty 1-3, struggles at 4-5.
Active plan: `documentation/plans/alpha4gate-master-plan.md` — platform + full-stack versioning + AlphaStar-style PPO upgrades. Always-up Phases 1–4.5 (daemon, evaluator, promotion gate, rollback, 9-tab dashboard) are the Baseline; full history in `documentation/archived/always-up-plan.md`.
Wiki: `documentation/wiki/index.md` — system diagram and deep-dive pages.

## SC2 requirements

- StarCraft II must be installed at `C:\Program Files (x86)\StarCraft II\`
- Maps from Blizzard CDN (not GitHub — those are Git LFS pointers)
- SC2 client must be running for integration tests (`pytest -m sc2`)
