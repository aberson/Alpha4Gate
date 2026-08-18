# Alpha4Gate — Project Instructions

## Project overview

SC2 Protoss bot: rule-based strategy + PPO neural policy + Claude AI advisor.
Goal: AI-vs-AI competition with transparent model introspection and autonomous self-improvement.

## Stack

- Python >=3.12 (dev venv runs 3.14, Linux CI runs 3.12), uv, burnysc2 v7.1.3, FastAPI, React+TypeScript+Vite
- Deep learning: PyTorch, Stable Baselines 3 (PPO), SQLite for training data
- Optional `[viewer]` extra (Windows only): pygame-ce + pywin32 + psutil — the themed self-play/evolve viewer container
- Testing: pytest (2007 unit tests across 113 files; 2024 with the optional `[viewer]` extra installed) + vitest (234 frontend tests: 228 passing, 6 skipped), ruff, mypy strict mode

## Commands

```bash
uv sync                                    # Install deps
uv run python -m bots.v0 --role solo --map Simple64  # Run game
uv run python -m bots.v0.runner --serve            # Dashboard API only
uv run pytest                              # 2007 unit tests (2024 with --extra viewer)
uv run pytest -m sc2                       # SC2 integration tests (SC2 must be running)
uv run ruff check .                        # Lint
uv run mypy src bots --strict              # Type check
cd frontend && npm run dev                 # Frontend dev server (:3000 -> :8765)
bash scripts/start-dev.sh                  # Start backend + frontend together (used by build-step --ui)
uv sync --extra viewer                     # Install the optional themed-viewer deps (Windows)
uv run --extra viewer python scripts/evolve.py --hours 4 --viewer  # Evolve run rendered in the viewer
```

```powershell
.\scripts\launch-evolve.ps1                # One-click: viewer evolve run + dashboard on the Evolution tab
.\scripts\launch-a4g.ps1 -Tab evolution    # Dashboard only (backend :8765 + frontend :3000)
```

## Directory layout

- `bots/v0/` — 56 Python modules (bot, decision engine, commands/, learning/). The production bot code.
- `bots/current/` — thin pointer package (MetaPathFinder alias to `bots/v0/`)
- `src/orchestrator/` — version registry, contracts, subprocess self-play stubs
- `src/selfplay_viewer/` — themed pygame container that hosts two SC2 clients (background, stats bar, live W-L overlay). Used by `scripts/selfplay.py` and, since Phase EV, by `scripts/evolve.py --viewer`. Imports pygame lazily inside methods, so the package imports fine without the `[viewer]` extra
- `tests/` — 113 test files (all import from `bots.v0.*`)
- `frontend/` — React dashboard (LiveView, CommandPanel, TrainingDashboard, etc.)
- `scripts/` — live-test.sh, analyze_rewards.py, evaluate_model.py, evolve.py, launch-evolve.ps1 / launch-a4g.ps1 (one-click launchers), etc.
- `documentation/wiki/` — project wiki (start with `index.md` for system diagram + page map)
- `documentation/master_plan.md` — single spine + plan index (active sub-plan pointers + archived list)
- `documentation/plans/` — active sub-plans (work remaining)
- `documentation/archived/` — completed/cut plans (Phase 1, Phase 2, improvement cycles)
- `bots/v0/data/` — per-version state: training.db, checkpoints/, reward_rules.json, hyperparams.json
- `data/` — legacy shared state: decision_audit.json, improvement_log.json, phase0_spike/ (gitignored)
- `logs/` — JSONL game logs (gitignored)

## Architecture

Seven layers: Claude Advisor -> Neural Engine -> Strategy (state machine) -> Command System -> Tactics -> Coherence -> Micro. "Tactics" is a grouping (`macro_manager.py`, `fortification.py`, `scouting.py`, build backlog), not a single module — there is no `tactics.py`.
Three command modes: AI-Assisted, Human Only, Hybrid.
WebSocket endpoints: /ws/game, /ws/decisions, /ws/commands.

## Current state

All production bot code lives in per-version trees `bots/vN/` (Phase 1 bots-v0-migration complete); `src/alpha4gate/` no longer exists. `bots/v0` is the FROZEN original — the live tree is whatever `bots/current/current.txt` names (today `v13`, and v0–v13 all exist on disk). Shared-runtime files (daemon, evaluator, promotion, rollback, trainer) are byte-identical across trees apart from import paths, but `features.py` and `database.py` are NOT: v0 is 47-dim / 40 base, v13 is 55-dim / 48 base (Phase D added an 8-slot build-order one-hot). Always name the tree when quoting a dimension.
All Phase 1 (rule-based) and Phase 2 (deep learning) features complete.
Five improvement cycles done: army coherence, natural denial, neural training, strategic commands, defensive fortification.
Wins reliably at difficulty 1-3, struggles at 4-5.
Active plan: `documentation/master_plan.md` — platform + full-stack versioning + AlphaStar-style PPO upgrades. Always-up Phases 1–4.5 (daemon, evaluator, promotion gate, rollback, 10-tab dashboard) are the Baseline; full history in `documentation/archived/always-up-plan.md`.
Master plan Phases A, 0, 1, 2, 3, 4, 5 all COMPLETE. Phase 4 added Elo ladder (`src/orchestrator/ladder.py`), cross-version promotion gate, CLI (`scripts/ladder.py`), `/api/ladder` endpoint, and Ladder dashboard tab (10th). Phase 5 added sandbox enforcement (`scripts/check_sandbox.py` + `.pre-commit-config.yaml`) and wired `check_promotion()` + `[advised-auto]` into `/improve-bot-advised`. Phase 9 (improve-bot-evolve) operational, v0→v1→v2 auto-promoted overnight 2026-04-23; v3→v4 promoted 2026-04-29 after stack-apply unblock (`e7fb758`). Phase 8 (headless Linux training infrastructure) Steps 1-10 SHIPPED 2026-04-29 (Linux CI + SC2PATH resolver + `Dockerfile` + `.dockerignore` + `documentation/wiki/cloud-deployment.md`); Step 11 (24h Linux evolve soak) pending; Step 12 (cloud dry-run) removed. Phase N (winprob heuristic + give-up trigger) COMPLETE 2026-04-27 — `bots/v0/learning/winprob_heuristic.py`, `bots/v0/give_up.py`, `transitions.win_prob` column, every-10-step INFO log, `Alpha4GateBot._maybe_resign`. Live in `bots/v0/` and folded into `bots/v3/`+`v4/` via successive promotions; production runtime via `bots/current` → v13 (authoritative pointer: `bots/current/current.txt`).
Phase 7 (advised loop stale-policy detection) Steps 1–5 SHIPPED 2026-06-20 (#180–184 closed): `src/orchestrator/staleness.py` (`StalenessReport` + `compute_staleness` reading per-version `training.db` via sqlite-direct, no `bots.*` import + `clamp_soak_hours`) and a `soak` improvement type in `/improve-bot-advised` (staleness-gated extended training soak, hybrid mode, wall-clock-clamped). Step 6 operator validation soak (#280) pending. The suite stood at 1799 tests when Phase 7 shipped; today it is 2007 (2024 with the optional `[viewer]` extra).
Phase EL (Evolution Lines) Steps EL.1–EL.6 SHIPPED 2026-06-20 (#273–#278 closed): parallel lineages (`src/orchestrator/lineages.py`), frozen-baseline opponent registry + fitness gauntlet (`baselines.py`, `scripts/baseline.py`), behavioural diversity fingerprint (`fingerprint.py` — v1 fingerprint IS the per-baseline win-rate vector), and diversity-driven extinction (`population.py`), plus dashboard lineage/extinction surfacing. Defaults byte-identical (`--lineages 1`, `--fitness-mode parent`, `--population-cap 0`); EL.7 soak (#279) pending. Known gap: lineage head advances and extinctions are IN-MEMORY ONLY — `write_lineages` has zero production callers, so a resumed run re-branches from stale heads and extinct lineages revive.
Phase EJ (evolve judging noise-floor) Steps EJ.1–EJ.6 SHIPPED 2026-07-06 (#282–#287 closed): promoted-title priors exclusion, mechanical AST null-diff screen, one-sided posterior rollback bar (`src/orchestrator/gate_stats.py` — uniform Beta(1,1), roll back only at P(worse) ≥ 0.85 over ≥ 4 decided games, fails OPEN below that; exact binomial-tail identity, no scipy), frozen-baseline panel floor (sweep-loss backstop for the relaxed bar), refresh-time proposal dedup, and budget-aware fit. All flags default OFF and byte-identical (`--regression-rule majority` is still the default); EJ.7 smoke (#288) + EJ.8 soak (#289) pending. Rationale: strict majority (`games//2+1`) rolls back a truly-neutral promotion ~50% of the time at odd n — raising `--games-per-eval` tightens the estimate, not the null.
Phase EV (evolve `--viewer`) Steps EV.1–EV.3 SHIPPED 2026-08-10 (#291–#293 closed) on branch `master-plan/phase-ev`: an opt-in `--viewer` flag on `scripts/evolve.py` renders an evolution run's SC2 games inside the existing themed container (`src/selfplay_viewer/`), and `scripts/launch-evolve.ps1` — the dev-observatory `run-evolution` button — opts in. Default stays headless and byte-identical. **Not mergeable to `master` until `onbrand-pilot` lands** (`master` still has mainline pygame and no `launch-a4g.ps1`). EV.4 operator smoke (#294) + EV.5 observation soak (#295) pending. Operator safety, new: closing the viewer container only DETACHES (the run continues headless); to STOP a run close the evolve CONSOLE window; **never Ctrl+C a `--viewer` run** — the loop runs off the main thread so burnysc2's SIGINT kill-switch is never armed and Ctrl+C can orphan SC2 processes. The dashboard's Stop button is not wired to the runner.
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
