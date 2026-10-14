# Alpha4Gate — Project Instructions

## Project overview

SC2 Protoss bot: rule-based strategy + PPO neural policy + Claude AI advisor.
Goal: AI-vs-AI competition with transparent model introspection and autonomous self-improvement.
Second player family: Jev (`src/jev/` + `bots/jev/v1/` + `bots/jev/v2/`), a decision-graph Protoss player whose graph owns command legality and execution. v1 (frozen) is a one-base four-Gateway Zealot rush; v2 (packaged since J2 Step 214) adds scouting, gas, persistent tech and natural-expansion plans and a Zealot/Stalker army. An opt-in hosted Typesafe model chooses army intent for v1, and for v2 chooses among the graph's own branches through four question families (strategy, army_mode, expansion_response, production_mix). No PPO and no Claude play in-game. Phase J2's code steps are done; its live gates remain (operator Step 222 next, then Step 223; Step 213 deferred).

## Stack

- Python >=3.12 (dev venv runs 3.14, Linux CI runs 3.12), uv, burnysc2 v7.1.3, FastAPI, React+TypeScript+Vite
- Deep learning: PyTorch, Stable Baselines 3 (PPO), SQLite for training data
- Optional `[viewer]` extra (Windows only): pygame-ce + pywin32 + psutil — the themed self-play/evolve viewer container
- Testing: pytest (131 test files; 3345 passing + 12 skipped with the optional `[viewer]` extra installed, 3310 passing + 32 skipped without it, 2026-10-10; `sc2`-marked tests are deselected by default; 9 of the skips are the opt-in real-browser Jev tests (6 launch, 3 live graph), which run only with `JEV_BROWSER_TESTS=1` and `uv run --with playwright`) + vitest (415 frontend tests: 409 passing, 6 skipped (2026-10-11)), ruff, mypy strict mode

## Commands

```bash
uv sync                                    # Install deps
uv run python -m bots.v0 --role solo --map Simple64  # Run game
uv run python -m bots.current.runner --serve       # Dashboard API only (current tree; the frozen v0 app does not serve /api/jev)
uv run pytest                              # 3345 passing + 12 skipped with --extra viewer (3310 + 32 without); sc2 tests deselected
uv run pytest -m sc2                       # SC2 integration tests (SC2 must be running)
uv run ruff check .                        # Lint
uv run mypy src bots --strict              # Type check
cd frontend && npm run dev                 # Frontend dev server (:3000 -> :8765)
bash scripts/start-dev.sh                  # Start backend + frontend together (used by build-step --ui)
uv sync --extra viewer                     # Install the optional themed-viewer deps (Windows)
uv run --extra viewer python scripts/evolve.py --hours 4 --viewer  # Evolve run rendered in the viewer
uv run python scripts/lineage.py add alt v13   # Seed data/lineages.json (add|list|remove); required before --lineages N does anything
uv run python scripts/baseline.py add anchor-v13 v13  # Seed data/baselines.json (add <name> <version>); required before --fitness-mode baseline|both or --panel-floor
```

```powershell
.\scripts\launch-evolve.ps1                # One-click: viewer evolve run + dashboard on the Evolution tab
.\scripts\launch-a4g.ps1 -Tab evolution    # Dashboard only (backend :8765 + frontend :3000)
```

```powershell
uv run python -m bots.jev.v1 --validate-policy  # Jev: validate the packaged policy and print its hash (no SC2)
uv run python -m bots.jev.v2 --validate-policy  # Jev v2: the same for the v2 package
uv run python -m bots.jev.v1 --map Simple64 --opponent-race Terran --difficulty 1 --seed 1 --max-game-seconds 900 --max-wall-seconds 1800  # Jev: one match; evidence in data\jev\runs\<run_id>
$runId = (Get-ChildItem data\jev\runs -Directory | Sort-Object CreationTime | Select-Object -Last 1).Name  # Jev: the newest run's ID
uv run python scripts\validate_jev.py --run-id $runId --api-base http://localhost:8765  # Jev: verify a run's disk evidence against the API (backend: bots.current.runner --serve)
uv run python scripts/benchmark_jev.py --panel baseline --dry-run  # Jev v2: resolve the baseline panel's exact entrypoint, policy hash, model and options (no service call, no SC2, nothing written)
powershell -File scripts/launch-jev.ps1 -Version v2 -DecisionProvider scripted -Difficulty 1 -Seed 1  # Jev: dashboard-first single match (plays SC2): opens ?tab=jev&launch=<session_id>, starts SC2 only after the page renders that run; defaults -Version v2 -DecisionProvider typesafe (a paid match)
```

Typesafe integration: opt in with `uv run python -m bots.jev.v1 --decision-provider typesafe --realtime`; requires `TYPESAFE_API_KEY` in the process environment. Default remains local scripted. `--decision-model` defaults to `jev-latest`; `--decision-max-requests` defaults to 450. Model chooses army intent for v1; for v2 (`uv run python -m bots.jev.v2 --decision-provider typesafe --realtime`) four question families (`strategy`, `army_mode`, `expansion_response`, `production_mix`) share that one budget, and the **Plans and decisions** panel and node sources attribute each plan and command to its request or fallback. Graph owns command legality/execution. Dashboard Army decision panel distinguishes model and fallback.

**Attended Jev investigations use Observer Rush** (the combined SC2/graph/caster
viewer, formerly Battle View), not separate React and SC2 windows. Launch with
`scripts/launch-jev-battle.ps1`; use its Stop battle control. The default remains
real-time; `-GameSpeed 2`/`4` enables measured pacing. For frozen-source speed
experiments use `scripts/measure_jev_speed.py` and
`documentation/operator/jev-speed-measurement.md`. Keep viewer/recording workload
consistent across comparisons. Desktop shortcuts are Alpha4Gate Observer Rush
and Alpha4Gate Observer Rush - Record.
The panel is named **Archon Auto Caster**. **Follow decisions** inspects newly lit
path endpoints (random ties); clicking a node pauses following. Hosted v2 recovery
now completes on settled home readiness, with progress in strategy evidence and
Observer Rush. See `documentation/investigations/jev-recovery-completion-2026-10-12.md`
for thresholds, before/after evidence, and the limited live validation.

Jev live smoke/acceptance procedure (start, stop, Step 207/208 checklists, report template, section 13 "Typesafe Jev integration (Phase JI)" gate): `documentation/operator/jev-validation.md`.

Jev v2 benchmark and dashboard-first launcher (Steps 212/224; the Step 213 baseline runbook, limits, resume, exit codes), and the v2 player, its live graph, hypothesis values and the Step 222 prerequisites (section 13): `documentation/operator/jev-v2-validation.md`.

## Directory layout

- `bots/v0/` — 56 Python modules (bot, decision engine, commands/, learning/). The FROZEN original tree; promoted snapshots `bots/v1/`–`bots/v13/` sit beside it, and production runs whatever `bots/current/current.txt` names.
- `bots/current/` — thin pointer package (MetaPathFinder alias to the tree named in `bots/current/current.txt`, today `bots/v13/`)
- `src/orchestrator/` — version registry, contracts, subprocess self-play stubs
- `src/jev/` + `bots/jev/v1/` + `bots/jev/v2/` — the Jev decision-graph player (Phases JV/JI/J2): runtime, SC2 adapter, runner, run evidence, v2's persistent plans (`plans.py`, Steps 217-220) and hosted question coordinator (`strategy.py`, Steps 219-220), the benchmark (`benchmark.py`, Step 212), dashboard-first launch sessions (`launch.py`, Step 224) and the `/api/jev` router (read-only run evidence plus `GET /launches/{session_id}` and the loopback, same-origin `POST /launches/{session_id}/ready` receipt) in `src/jev`; the packaged policies in `bots/jev/v1` and `bots/jev/v2` (no `VERSION`, so legacy discovery ignores them); runs land in `data/jev/runs/`, launch sessions in `data/jev/launches/`, benchmark batches and frozen sources in `data/jev/benchmarks/`
- `src/selfplay_viewer/` — themed pygame container that hosts two SC2 clients (background, stats bar, live W-L overlay). Used by `scripts/selfplay.py` and, since Phase EV, by `scripts/evolve.py --viewer`. Imports pygame lazily inside methods, so the package imports fine without the `[viewer]` extra
- `tests/` — 131 test files (130 `tests/test_*.py` + `tests/commands/test_dispatch_guard.py`); most bot-tree tests import from `bots.v0.*` (16 import `bots.current`, `bots.v10`, `bots.v13`, `bots.v3` or `bots.v4` directly), the 16 `test_jev_*.py` files import `jev` / `bots.jev`
- `frontend/` — React dashboard, 7 tabs (`AdvisedControlPanel`, `EvolutionTab`, `ModelsTab`, `ObservableTab`, Processes panels, `HelpTab`, `JevTab`); tab list is `frontend/src/App.tsx:18-26`
- `scripts/` — live-test.sh, analyze_rewards.py, evaluate_model.py, evolve.py, launch-evolve.ps1 / launch-a4g.ps1 (one-click launchers), launch-jev.ps1 (dashboard-first Jev match), benchmark_jev.py / validate_jev.py (Jev benchmark and run verifier), etc.
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
Jev (Phases JV/JI/J2) is a separate player family outside these seven layers. The packaged graph (`bots/jev/vN/policy.json`, validated by `jev.policy` against the `jev.operations` allowlist) is the policy; `jev.runtime` interprets it and `jev.sc2_adapter`, driven by `jev.bot`, issues commands through burnysc2. The only model in the loop is the opt-in hosted Typesafe provider (`--decision-provider typesafe`, `jev.decision` over httpx), which picks army intent for v1 and, through `jev.strategy`, answers v2's strategy, army, expansion-response and production-mix questions on one request budget, while the graph owns command legality and execution. Plan, slot and decision diagnostics travel in the existing event facts (D5); the Jev tab draws lanes, recorded edge activity, plans and node sources from them. The dashboard reads Jev over HTTP only (`GET /api/jev/runs`, `/runs/{run_id}`, `/runs/{run_id}/policy`, `/launches/{session_id}`, plus the loopback `POST /launches/{session_id}/ready`); there is no Jev WebSocket.

## Current state

All production bot code lives in per-version trees `bots/vN/` (Phase 1 bots-v0-migration complete); `src/alpha4gate/` no longer exists. `bots/v0` is the FROZEN original — the live tree is whatever `bots/current/current.txt` names (today `v13`, and v0–v13 all exist on disk). Shared-runtime files (daemon, evaluator, promotion, rollback, trainer) are byte-identical across trees apart from import paths, but `features.py` and `database.py` are NOT: v0 is 47-dim / 40 base, v13 is 55-dim / 48 base (Phase D added an 8-slot build-order one-hot). Always name the tree when quoting a dimension.
All Phase 1 (rule-based) and Phase 2 (deep learning) features complete.
Five improvement cycles done: army coherence, natural denial, neural training, strategic commands, defensive fortification.
Wins reliably at difficulty 1-3, struggles at 4-5.
Active plan: `documentation/master_plan.md` — platform + full-stack versioning + AlphaStar-style PPO upgrades. Always-up Phases 1–4.5 (daemon, evaluator, promotion gate, rollback, then-10-tab dashboard — **7 tabs today**: Advisor, Evolution, Models, Observable, Processes, Help, Jev, per `frontend/src/App.tsx:18-26`) are the Baseline; full history in `documentation/archived/always-up-plan.md`.
Master plan Phases A, 0, 1, 2, 3, 4, 5 all COMPLETE. Phase 4 added Elo ladder (`src/orchestrator/ladder.py`), cross-version promotion gate, CLI (`scripts/ladder.py`), `/api/ladder` endpoint, and Ladder dashboard tab (10th). Phase 5 added sandbox enforcement (`scripts/check_sandbox.py` + `.pre-commit-config.yaml`) and wired `check_promotion()` + `[advised-auto]` into `/improve-bot-advised`. Phase 9 (improve-bot-evolve) operational, v0→v1→v2 auto-promoted overnight 2026-04-23; v3→v4 promoted 2026-04-29 after stack-apply unblock (`e7fb758`). Phase 8 (headless Linux training infrastructure) Steps 1-10 SHIPPED 2026-04-29 (Linux CI + SC2PATH resolver + `Dockerfile` + `.dockerignore` + `documentation/wiki/cloud-deployment.md`); Step 11 (24h Linux evolve soak) pending; Step 12 (cloud dry-run) removed. Phase N (winprob heuristic + give-up trigger) COMPLETE 2026-04-27 — `bots/v0/learning/winprob_heuristic.py`, `bots/v0/give_up.py`, `transitions.win_prob` column, every-10-step INFO log, `Alpha4GateBot._maybe_resign`. Live in `bots/v0/` and folded into `bots/v3/`+`v4/` via successive promotions; production runtime via `bots/current` → v13 (authoritative pointer: `bots/current/current.txt`).
Phase 7 (advised loop stale-policy detection) Steps 1–5 SHIPPED 2026-06-20 (#180–184 closed): `src/orchestrator/staleness.py` (`StalenessReport` + `compute_staleness` reading per-version `training.db` via sqlite-direct, no `bots.*` import + `clamp_soak_hours`) and a `soak` improvement type in `/improve-bot-advised` (staleness-gated extended training soak, hybrid mode, wall-clock-clamped). Step 6 operator validation soak (#280) pending. The suite stood at 1799 tests when Phase 7 shipped; on 2026-10-10 it is 3310 passing (3345 with the optional `[viewer]` extra).
Phase EL (Evolution Lines) Steps EL.1–EL.6 SHIPPED 2026-06-20 (#273–#278 closed): parallel lineages (`src/orchestrator/lineages.py`), frozen-baseline opponent registry + fitness gauntlet (`baselines.py`, `scripts/baseline.py`), behavioural diversity fingerprint (`fingerprint.py` — v1 fingerprint IS the per-baseline win-rate vector), and diversity-driven extinction (`population.py`), plus dashboard lineage/extinction surfacing. Defaults byte-identical (`--lineages 1`, `--fitness-mode parent`, `--population-cap 0`); EL.7 soak (#279) pending — now **runnable**, because EH.1 added `scripts/lineage.py add` to seed the registry. **The in-memory-only known gap is CLOSED by Phase EH** (EH.1 `4eb7836`, EH.2 `5fb4213`): `write_lineages` has production callers, and `run_loop` persists advanced heads plus `status="extinct"` records to `data/lineages.json` at every generation boundary — **but only when the registry was loaded from disk.** A bare run with no registry file still persists nothing, deliberately, so the hop can never create or overwrite an operator-authored registry.
Phase EJ (evolve judging noise-floor) Steps EJ.1–EJ.6 SHIPPED 2026-07-06 (#282–#287 closed): promoted-title priors exclusion, mechanical AST null-diff screen, one-sided posterior rollback bar (`src/orchestrator/gate_stats.py` — uniform Beta(1,1), roll back only at P(worse) ≥ 0.85 over ≥ 4 decided games, fails OPEN below that; exact binomial-tail identity, no scipy), frozen-baseline panel floor (sweep-loss backstop for the relaxed bar), refresh-time proposal dedup, and budget-aware fit. All flags default OFF and byte-identical (`--regression-rule majority` is still the default); EJ.7 smoke (#288) + EJ.8 soak (#289) pending. Rationale: strict majority (`games//2+1`) rolls back a truly-neutral promotion ~50% of the time at odd n — raising `--games-per-eval` tightens the estimate, not the null.
Phase EV (evolve `--viewer`) Steps EV.1–EV.3 SHIPPED 2026-08-10 (#291–#293 closed) on branch `master-plan/phase-ev`: an opt-in `--viewer` flag on `scripts/evolve.py` renders an evolution run's SC2 games inside the existing themed container (`src/selfplay_viewer/`), and `scripts/launch-evolve.ps1` — the dev-observatory `run-evolution` button — opts in. Default stays headless and byte-identical. **The branch already contains `onbrand-pilot`; no separate prerequisite merge is needed.** Landing the broader branch on `master` remains a separate decision while acceptance gates are open; see `documentation/branch-landing-phase-ev.md`. EV.4 operator smoke (#294) + EV.5 observation soak (#295) pending. Operator safety, new: closing the viewer container only DETACHES (the run continues headless); to STOP a run close the evolve CONSOLE window; **never Ctrl+C a `--viewer` run** — the loop runs off the main thread so burnysc2's SIGINT kill-switch is never armed and Ctrl+C can orphan SC2 processes. The dashboard's Stop button is not wired to the runner.
Phase EH (evolve operational hardening) is **IN PROGRESS — EH.1 and EH.2 of 10 SHIPPED 2026-10-04**. The dev-workspace toolkit freeze is LIFTED (the skill-mesh descope record of 2026-09-05 removed `PhaseRdActivationSealV1` as the lift gate), `/repo-sync` minted umbrella #305 + steps #306-#315, and the plan is at `documentation/plans/evolve-operational-hardening-plan.md` with per-step checkpoints.
**EH.1** (`4eb7836`, #306 closed) added `scripts/lineage.py` (`add`/`list`/`remove` + `--path`) and `register_lineage` in `src/orchestrator/lineages.py`, giving `write_lineages` its first production caller — so `data/lineages.json` is creatable without hand-editing JSON, which unblocks operator gate EL.7 (#279). `register_lineage` merges with `dataclasses.replace`: the `register_baseline` mirror does NOT transfer, because `Baseline` has 3 fields its CLI fully expresses while `Lineage` has 6 and the CLI expresses 2.
**EH.2** (`5fb4213`, #307 closed) added the generation-boundary persist hop, so lineage heads and extinctions survive process exit instead of evaporating. `_load_lineage_registry_if_engaged` now returns `(registry, from_disk)` and the hop is gated on `from_disk` — `if _lineage_heads:` alone is truthy for the synthesized implicit `main`, which would create or overwrite a hand-authored registry and permanently engage multi-lineage scheduling. `next_lineage` filters to `status == "active"` with a non-raising all-extinct fallback. **Non-obvious and load-bearing:** extinct records are re-homed into `_extinct_lineages`, never filtered out — `write_lineages` is a whole-file replace, so a record absent from the persist payload is DELETED from disk.
**EH.3-EH.10 remain**, all with minted issues (#308-#315). EH.3 and EH.4 are dependency-free and touch disjoint regions, so they are parallel-safe. **Every later step must re-derive its pinned anchors**: EH.1 and EH.2 have moved `src/orchestrator/lineages.py` and `scripts/evolve.py` substantially (EH.2's own anchors into `lineages.py` had already drifted 33-113 lines from EH.1 and were re-derived before dispatch). §6 D-8 requires this between Phases EH and EI; it applies step-to-step inside a phase too, which D-8 does not say.
Jev (`src/jev/` + `bots/jev/v1/` + `bots/jev/v2/`): Phase JV automated Steps 201-206 implemented 2026-10-08, operator live gates 207/208 (#324/#325) still pending. Phase JI (opt-in hosted Typesafe army-intent provider, Steps 209-211) is COMPLETE, live acceptance passed 2026-10-08. **Phase J2 (adaptive Jev v2, `documentation/plans/jev-v2-plan.md`, umbrella #327) is IN PROGRESS: 10 of 13 steps DONE (code Steps 212, 224 and 214-221).** Step 212 (`9b2136e`, #328 closed) added `src/jev/benchmark.py` + `scripts/benchmark_jev.py`: a sequential, bounded, resumable benchmark of the production runner with frozen-source provenance and D6 scorecards. Step 224 (`28dc15d`, #329 closed) added `src/jev/launch.py` + `scripts/launch-jev.ps1`: dashboard-first launch, where SC2 starts only after `/?tab=jev&launch=<session_id>` has rendered the exact archived run and POSTed `{run_id, policy_hash}`. Steps 214-221 (`0b46350`..`f52d4f6`, #331-#338 closed, 2026-10-09/10, on `master-plan/phase-ev`, not merged to master) packaged `bots/jev/v2` (policy hash `a1c73ea8...`, 292 nodes, nine root lanes): planned supply and grouped reinforcement, one-Probe scouting with bounded enemy memory, a gas economy, persistent expansion and tech plans with bounded recovery (`src/jev/plans.py`), a Cybernetics Core and Zealot/Stalker army, the hosted question coordinator (`src/jev/strategy.py`), and the live graph's lanes, observed edge activity and **Plans and decisions** panel. v1's policy hash `510a72df...` is unchanged and the frozen v1 baseline `v1-b53039a693cadf75` is preserved (gitignored, primary checkout only; never recapture v1). No SC2 match or hosted call was made during 214-221. Step 213 (#330) stays operator-deferred, not passed. **Next is operator Step 222 (#339, plan `## Manual UAT` M1)**: at most three paid dashboard-first v2 matches (Terran, difficulties 3, 4, 5, seed 11); `powershell -File scripts/launch-jev.ps1` defaults to v2, Typesafe, Terran, difficulty 3, seed 11. Procedure: `documentation/operator/jev-v2-validation.md` section 13, "Before Step 222". Step 223 (#340, wait) follows; run `uv run python scripts/benchmark_jev.py --freeze-candidate v2` only once the candidate is final, before its first held-out game.
Wiki: `documentation/wiki/index.md` — system diagram and deep-dive pages.

**Important:** Do NOT import `bots.current` or `bots.<version>` from `src/orchestrator/` — triggers MetaPathFinder loop. Registry reads paths via pathlib. The Jev boundary is stricter: `src/jev/` has no `bots.*` import statement (`jev.launch` only probes `bots.jev.<version>` with `importlib.util.find_spec`), and no Jev code imports `bots.current` / `bots.vN`; nothing in Jev imports torch, SB3, gymnasium or an LLM SDK (anthropic, openai, claude_agent_sdk, langchain, transformers). The only first-party import outside Jev is `orchestrator.paths`, and the only non-stdlib gameplay externals are `sc2` and `httpx` (`fastapi` only in `jev.api`). `tests/test_jev_army.py` and `tests/test_jev_policy.py` enforce this.

## SC2 requirements

### Jev Battle View preview

`powershell -File scripts/launch-jev-battle.ps1` opens a hosted Typesafe v2 game
beside the expanded 3D Branch faces graph. `-AutoRecord` records automatically;
`-CheckOnly` checks local prerequisites without launching. Windows-only fixed
2160×1200 preview; needs Edge and the isolated portable OBS `BattleProbe` profile
(authenticated websocket port 4457), installed on the development workstation.
The launcher loads the saved DPAPI key; only the game child receives it.
Uses `jev-1.13.0`, capped at 450 requests; missing key stops launch.
Desktop matches have no game/wall timer (zero disables each runner timer); the
request budget still falls back to the local graph after exhaustion. Developer
probes retain their short bounds. SC2 is an owned popup, Edge a child; optional
camera-status writes must not crash gameplay when Windows readers hold the file.
`frontend/src/components/JevBattleView.tsx` is selected with `?battle=1`;
`scripts/probes/jev_battle_view.py` owns native hosting and camera/capture controls.
`src/jev/camera.py` supplies the automatic observer; `src/jev/caster.py` supplies
text-only local commentary through Ollama (`127.0.0.1:11434`, `qwen2.5:1.5b`, CPU).
Caster has no paid fallback or gameplay authority. Its 70/15/15 commentary/facts/
jokes mix carries recorded subject IDs for purple graph highlights. Graph rotation
and highlights have separate toggles; rotation pauses for hover/node focus.
Use **Stop battle**, not console termination. 36 focused frontend tests, 141 runner/
launcher tests, three caster tests and live launch/record/stop checks passed;
this is not J2 acceptance or a full production host. README media provenance is
`documentation/media/README.md`; only optimized showcase media is committed.
Guide: `documentation/operator/jev-battle-view.md`. Native geometry settling,
continuous event pumping and audio-before-reparenting findings are in
`documentation/investigations/jev-battle-view-feasibility-2026-10-11.md`.

- StarCraft II must be installed at `C:\Program Files (x86)\StarCraft II\`
- Maps from Blizzard CDN (not GitHub — those are Git LFS pointers)
- SC2 client must be running for integration tests (`pytest -m sc2`)

## Rules

- [`.claude/rules/frontend-ui.md`](.claude/rules/frontend-ui.md) — dashboard UI conventions.
- [`.claude/rules/bot-runtime.md`](.claude/rules/bot-runtime.md) — backend `--serve` and daemon lifecycle, SC2 client invariants (process management, 2-client cap, perception-affecting debug flags), burnysc2 combineable abilities, per-version vs cross-version data dirs.
- [`.claude/rules/evolve.md`](.claude/rules/evolve.md) — reading evolve run state, pre-launch hygiene, snapshot import isolation, dev-apply sub-agent sanitization, fitness noise floor, training-imp pool restriction.
- [`.claude/rules/wsl-evolve.md`](.claude/rules/wsl-evolve.md) — eight setup gotchas for Linux-SC2 evolve substrate. Each one breaks evolve differently; applying only a subset gives partial-success symptoms.

Jev continuation (2026-10-11): 214-221 DONE; browser fixtures repaired in `03c552a`; compact/radial/3D graph views and combined Battle View preview shipped. Step 213 stays deferred, 222/223 pending. Current UX follow-up is production Battle View integration. Recovery completion and measured strategy/army choice flattening remain separate, unbuilt follow-ups; preserve before/after evidence. See the Battle View guide and `documentation/investigations/jev-recovery-stall-2026-10-11.md`. Resolve D3/section-5 scope before freezing v2; interrupted visual tests are not acceptance matches.
