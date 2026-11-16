# Alpha4Gate lanes

A lane is a named, lasting slice of this project's work, worked by one window at a time and coordinated through Passing Notes (project id `alpha4gate`). No path is owned by two lanes. Approved by the operator on 2026-11-16.

| Lane | Purpose | Owns | Host | Hands off or needs |
|---|---|---|---|---|
| main | Coordinate, ship, relay the operator | `docs/plans/`, `docs/plan-redline/`, `docs/lanes/`, `docs/seeds/evolve-for-strength.md`, `docs/seeds/jev-harvest.md`, `README.md`, `CLAUDE.md`, wiki `index.md` / `faq.md` / `project-history.md`, `pyproject.toml`, `uv.lock`, `.gitignore`, `bots/current/current.txt`, all merges and issue hygiene | claude | Hands the synced plan to every lane; ships every lane's branch |
| scoreboard | Measure versions against the built-in AI (ES.2, ES.3) | `src/orchestrator/scoreboard.py`, `scripts/scoreboard.py`, `scripts/scoreboard_worker.py`, `src/orchestrator/sc2_compat.py`, `tests/test_scoreboard.py`, `tests/test_sc2_compat.py`, `data/scoreboard/` | either | Needs RD1 answered and the synced plan; hands `src/orchestrator/scoreboard.py` to evolve after ES.3 merges |
| evolve | Harden evolve, then change how it promotes (EH.3, EH.8, EH.6, EH.7, EH.9, ES.6–ES.10, EI.1–EI.4, EI.6, EI.13) | `scripts/evolve.py`, `scripts/evolve_worker.py`, `scripts/launch-evolve.ps1`, `src/orchestrator/{evolve,evolve_dev_apply,selfplay,gate_stats}.py`, `tests/test_evolve_cli.py`, `tests/test_gate_stats.py`, `tests/test_selfplay*.py`, `.claude/skills/improve-bot-evolve/`, `docs/wiki/operator-commands.md`, `docs/operator-gate-runbook.md` | claude | Needs RD2 answered; needs ES.3 merged before ES.8 |
| direction | PPO and viewer research and plan drafts (PPO.0, VW.0) | `docs/seeds/ppo-direction.md`, `docs/seeds/strength-viewer.md`, `docs/investigations/ppo-*.md`, `docs/investigations/viewer-*.md` | either | Sends plan text to main as notes and never edits `docs/plans/`; needs Stage 0 results for PPO.0's first measurement |

Coordinator: main
Shipper: main
Operator relay: main
Rules: [passing-notes lane rules](https://github.com/aberson/passing-notes/blob/HEAD/docs/lane-rules.md)

Operator-only work (never in a lane): the T1–T7 and RD1–RD3 answers, the Jev archive decision, commit and push authorization, and the operator and wait steps ES.1, ES.4, ES.5, ES.11, ES.12, ES.13 and EH.10.

Shared resources, claimed through Heads Up and never owned by a lane: the Windows SC2 client (at most two API clients), WSL SC2 and machine memory, Claude credits for evolve proposals, `bots/current/current.txt` writes, `data/evolve_*` run state, and the master plan.
