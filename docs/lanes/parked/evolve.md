# Parked lane: evolve (evolve hardening and promotion changes)

Parked 2026-11-17 at the operator's request, relayed by central (note
`20261117T030913Z-central-to-evolve-c773`), after the lane's build run finished and every result
shipped. The lane id `evolve` is reopened with `lane-open` when its held queue is released. This doc
is enough to resume with no other context.

## What the lane is for

Harden the evolve loop, then change how it promotes. The queue order, from the operator's RD2
answer (2026-11-16): EH.3, EH.8, EH.6, EH.7, EH.9, then ES.6–ES.10, then EI.1–EI.4, EI.6 and
EI.13. ES.14 was added under T1 and built first. Plan: master plan Phases EH, ES and EI in
[master_plan.md](../../plans/master_plan.md).

## Done

Each step ran on branch `evolve/build-phase-20261116`, passed on its first round, and was shipped
by central (pushed to `master-plan/phase-ev`, issue closed).

| Item | Issue | Commit on phase-ev | What it does |
|---|---|---|---|
| Landing proposal for the earlier uncommitted edits | — | `8915098` (central's migration commit) | The edits in evolve paths were the `documentation/` to `docs/` migration; landed whole by central |
| ES.14 compat shim in self-play | #370 | `c7d8699` | Self-play launches every version through `scripts/compat_launch.py`, which installs `orchestrator.sc2_compat` first, so v0–v12 run on SC2 build 98310 |
| EH.3 fail loudly when the gauntlet is inert | #308 | `01c9fd1` | `--require-baselines` (default off) exits 1 before any game when the baseline gauntlet or panel floor would be inert |
| Runbook follow-up | #308 | `f535d85` (merged as `5e55972`) | `docs/operator-gate-runbook.md` documents `--require-baselines` |
| EH.8 path-scope the `[evo-auto]` commit | #313 | `f52bda1` (merged as `68d927d`) | Promote and rollback commits include only their own paths; foreign staged paths are logged, never swept or unstaged |
| EH.6 launcher `--generations` passthrough | #311 | `634bdc9` | `launch-evolve.ps1 -Generations` (default 0), so `-Hours N` runs N hours; the skill passes `--generations 0`; evolve warns when `--hours` is set but `--generations` is left at 1 |
| EH.7 single-run lock | #312 | `d59bbd1` | `evolve.py` refuses to start over a confirmed-live same-platform run before anything truncates results; stale records are reaped; a record from the other platform refuses; `--force` overrides |
| Docs for EH.7 and EH.6 | #311 #312 | `8c718ae` | Runbook and operator-commands wiki describe the lock and `-Generations` |
| EH.9 producer to consumer smoke | #314 | `a7102b6` | One chain test drives the lineage seed CLI, `run_loop`, `data/lineages.json` and `GET /api/evolve/lineages`; red-first verified against a disabled EH.2 hop |
| Test-pollution fix | — | `839afee` | `tests/test_evolve_cli.py` no longer writes the real `data/lineage.json` (autouse hook stub plus a module guard, red-first verified) |
| BL.1 landing plan (read-only) | — | not committed | [bl1-landing-plan-2026-11-17.html](../../plan-redline/bl1-landing-plan-2026-11-17.html), handed to central |

The full suite at the end of the run: 3,424 passed, 0 failed (Windows, Python 3.14, run in
foreground chunks). Gate evidence for each step is in the build-step run archives under
`%TEMP%\skill-mesh\build-step-archive\` and in each issue's result comment.

## Pending and why it is parked

| Step | Issue | Status | Blocked by |
|---|---|---|---|
| ES.6 | #372 | HELD | Operator T2 (2026-11-16): PPO comes first after the scoreboard; ES.6–ES.13 wait until PPO.0 plans a PPO build phase |
| ES.7 | #373 | HELD | T2; ES.6 |
| ES.8 | #374 | HELD | T2; ES.7 and ES.2 |
| ES.9 | #375 | HELD | T2; ES.8 |
| ES.10 | #376 | HELD | T2; ES.9 and ES.3 (the scoreboard lane hands `src/orchestrator/scoreboard.py` to evolve after ES.3 merges) |
| EI.1, EI.2, EI.6 | not minted | PENDING | EI.0 (operator approval of the reduced evidence layer), EH.10 (operator gate) and ES.10 |
| EI.3, EI.4 | not minted | PENDING | EI.1 (and EI.2 for EI.3) |
| EI.13 | not minted | PENDING | EI.1–EI.7 |
| `launch_prefix` keyword on `src/orchestrator/selfplay.py` for evolve-demo's VW.2 | #382 (VW.2) | not started | VW.1 is held. ES.14 did not add it. When needed: an opt-in keyword, `None` byte-identical, prepending to the list `_build_bot_process` builds (which now starts with `[python, scripts/compat_launch.py, <version>]`) |

**Not approved, do not do:** EH.7's three review advisories (non-atomic check-then-stamp, the
WSL clock-jump reap, a silent proceed on a corrupt state file). The operator declined that
follow-up on 2026-11-17.

## Operator approvals and standing rules already given

- Lane scope and owned files approved 2026-11-16; ownership widened by main the same day (see
  below).
- Gate memory rule: the operator approved lower-memory gates on 2026-11-16. Run gates in the
  **foreground** at 1 GB or more free; per change, run the focused tests plus ruff and
  `mypy src bots --strict`; where a step's Done-when says "full suite", run it as named
  foreground test-file chunks (6 chunks of about 2–6 minutes each worked). Idle background
  shells get reaped under memory pressure. Seed each child worktree's `.mypy_cache` from the
  lane worktree: a warm cache cuts mypy's peak from about 1.1 GB to about 0.3 GB.
- Shipping: central pushes and closes each step's issue as its handoff lands (operator,
  2026-11-16). Withhold `--issue` from build-step so it does not close issues itself.
- Once central has merged a step, never rebase that commit; fast-forward or merge from
  `origin/master-plan/phase-ev` before the next gate (central, 2026-11-17).
- Not authorized: SC2 games, evolve or `EVO_AUTO` runs, paid calls, pushing, editing other
  lanes' paths.

## Resume

1. Reopen the lane with `lane-open` (lane id `evolve`); register the card, bind doorbells,
   and read the inbox as `docs/lanes/README.md` says.
2. Confirm the hold is lifted: PPO.0 has planned a PPO build phase and the coordinator says
   ES.6 may start (T2). EI work also needs EI.0 approved and EH.10 run.
3. Check free memory (`(Get-Counter '\Memory\Available MBytes').CounterSamples.CookedValue`);
   gates need about 1 GB free.
4. Create a fresh lane worktree off the current `origin/master-plan/phase-ev` (or `master`,
   if BL.1 has landed and the operator has named it the mainline), run `uv sync --extra dev`,
   then from that worktree:

```text
/build-phase --plan docs/plans/master_plan.md --steps ES.6,ES.7,ES.8,ES.9,ES.10
```

   Start each item with `/user-build-route`, re-derive pinned line anchors before each step,
   and hand each finished step to the coordinator (central) as its own handoff note with the
   branch, commit and gate-evidence file.

The old lane worktree `<dev>\worktree_evolve-lane-20261116` (branch
`evolve/build-phase-20261116`, clean, 0 commits beyond `origin/master-plan/phase-ev`) is fully
shipped. Removing it and the branch is safe; the coordinator decides.

## Files the lane owns

`scripts/evolve.py`, `scripts/evolve_worker.py`, `scripts/launch-evolve.ps1`,
`scripts/compat_launch.py`, `scripts/baseline.py`, `scripts/lineage.py`,
`scripts/evolve_inject_one.py`, `src/orchestrator/{evolve,evolve_dev_apply,selfplay,gate_stats,lineages,snapshot}.py`,
`tests/test_evolve_cli.py`, `tests/test_evolve.py`, `tests/test_evolve_parallel.py`,
`tests/test_evolve_round_state.py`, `tests/test_evolve_chain_smoke.py`, `tests/test_gate_stats.py`,
`tests/test_selfplay*.py`, `tests/test_snapshot.py`, `tests/test_lineage_cli.py`, `tests/test_api_evolve_lineages.py`,
`.claude/skills/improve-bot-evolve/`, `.claude/rules/evolve.md`, `docs/wiki/operator-commands.md`
and `docs/operator-gate-runbook.md`. The canonical list is `docs/lanes/README.md`. This file,
`docs/lanes/parked/evolve.md`, is delegated by central, which owns `docs/lanes/` and ships it.
