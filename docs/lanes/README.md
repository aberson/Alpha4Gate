# Alpha4Gate lanes

A lane is a named, lasting slice of this project's work, worked by one window at a time and coordinated through Passing Notes (project id `alpha4gate`). No path is owned by two lanes. Approved by the operator on 2026-11-16.

| Lane | Purpose | Owns | Host | Hands off or needs |
|---|---|---|---|---|
| central | Coordinate, ship, relay the operator (took over from `main` on 2026-11-16 at the operator's request; also coordinates shake-spear and career-ops) | `docs/plans/`, `docs/plan-redline/`, `docs/lanes/`, `docs/seeds/evolve-for-strength.md`, `docs/seeds/jev-harvest.md`, `README.md`, `CLAUDE.md`, wiki `index.md` / `faq.md` / `project-history.md`, `pyproject.toml`, `uv.lock`, `.gitignore`, `bots/current/current.txt`, all merges and issue hygiene | claude | Hands the synced plan to every lane; ships every lane's branch |
| scoreboard | Measure versions against the built-in AI (ES.2, ES.3) | `src/orchestrator/scoreboard.py`, `scripts/scoreboard.py`, `scripts/scoreboard_worker.py`, `src/orchestrator/sc2_compat.py`, `tests/test_scoreboard.py`, `tests/test_sc2_compat.py`, `data/scoreboard/` | either | Needs RD1 answered and the synced plan; hands `src/orchestrator/scoreboard.py` to evolve after ES.3 merges |
| evolve | Harden evolve, then change how it promotes (EH.3, EH.8, EH.6, EH.7, EH.9, ES.6–ES.10, EI.1–EI.4, EI.6, EI.13) | `scripts/evolve.py`, `scripts/evolve_worker.py`, `scripts/launch-evolve.ps1`, `src/orchestrator/{evolve,evolve_dev_apply,selfplay,gate_stats}.py`, `tests/test_evolve_cli.py`, `tests/test_gate_stats.py`, `tests/test_selfplay*.py`, `.claude/skills/improve-bot-evolve/`, `docs/wiki/operator-commands.md`, `docs/operator-gate-runbook.md`; added 2026-11-16: `tests/test_evolve.py`, `tests/test_evolve_parallel.py`, `tests/test_evolve_round_state.py`, `tests/test_snapshot.py`, `tests/test_lineage_cli.py`, `tests/test_api_evolve_lineages.py`, `tests/test_evolve_chain_smoke.py` (new), `scripts/baseline.py`, `scripts/lineage.py`, `scripts/evolve_inject_one.py`, `scripts/compat_launch.py` (new, ES.14), `src/orchestrator/lineages.py`, `src/orchestrator/snapshot.py`, `.claude/rules/evolve.md` | claude | RD2 order (EH, then ES.6–ES.10, then EI); ES.6–ES.13 held (T2); builds ES.14 for ES.5 and evolve-demo; adds evolve-demo's opt-in `launch_prefix` to `selfplay.py` |
| direction | PPO research and plan drafts (PPO.0) | `docs/seeds/ppo-direction.md`, `docs/investigations/ppo-*.md` | either | Sends plan text to main as notes and never edits `docs/plans/`; needs Stage 0 results for PPO.0's first measurement. VW.0 moved to evolve-demo on 2026-11-16 |
| repair | Fix the front doors: desktop shortcuts and launchers that open but never start a game | `scripts/launch-jev-battle.ps1`, `scripts/launch-jev.ps1`, `scripts/launch-a4g.ps1`, `scripts/probes/jev_battle_view.py`, `frontend/src/components/JevBattleView.tsx`, `docs/operator/jev-battle-view.md`, `docs/investigations/repair-*.md`, the Alpha4Gate desktop shortcuts (outside the repo) | claude | `launch-evolve.ps1` stays with evolve; hands evolve-demo any Observer Rush change it asks for |
| evolve-demo | A Jev-style viewer for watching two bots, or a bot against the built-in AI (VW.0 and its build steps) | `docs/seeds/strength-viewer.md`, `docs/investigations/viewer-*.md`, `src/selfplay_viewer/` (including the new `strength/` package), `scripts/launch-strength-viewer.ps1`, `frontend/src/components/StrengthViewer.tsx` and its test, `tests/test_strength_viewer.py`, `frontend/src/main.tsx` (one route branch), `frontend/src/components/EvolutionTab.tsx` (Stop button direct task) | claude | Reuses Observer Rush by reading it or asking repair for changes; sends plan text to main; needs the SC2 compatibility shim for older versions on Windows |

Coordinator: central
Shipper: central
Operator relay: central

`main` closed on 2026-11-16 and handed over to `central`. Notes that say "send to main" now go to `central`.
Rules: [passing-notes lane rules](https://github.com/aberson/passing-notes/blob/HEAD/docs/lane-rules.md)

Operator-only work (never in a lane): the T1–T7 and RD1–RD3 answers, the Jev archive decision, commit and push authorization, and the operator and wait steps ES.1, ES.4, ES.5, ES.11, ES.12, ES.13 and EH.10.

Shared resources, claimed through Heads Up and never owned by a lane: the Windows SC2 client (at most two API clients), WSL SC2 and machine memory, Claude credits for evolve proposals, `bots/current/current.txt` writes, `data/evolve_*` run state, and the master plan.
