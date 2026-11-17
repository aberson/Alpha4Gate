# Parked lane: evolve-demo (strength viewer, Phase VW)

Parked 2026-11-17 by the operator's request to close lanes waiting on memory. The lane id
`evolve-demo` is reopened with `lane-open` when memory frees up. This doc is enough to
resume with no other context.

## What the lane is for

A Jev Observer Rush-style viewer for watching one bot version against the built-in AI, or
two bot versions against each other, with each bot's decisions beside its SC2 client, and
(later) flagging a moment into the improvement loop. Seed:
[strength-viewer.md](../../seeds/strength-viewer.md). Plan steps: master plan Phase VW
(umbrella issue #380) in [master_plan.md](../../plans/master_plan.md).

## Done

| Item | Evidence |
|---|---|
| VW.0: planned the viewer into VW.1–VW.3 (VW.4 is operator acceptance, added by main) | [viewer-vw-steps-2026-11-16.md](../../investigations/viewer-vw-steps-2026-11-16.md), committed in `4659148`; folded into the master plan in `3901b04`; issues minted by repo-sync (`6ba39e3`) |
| Seed updated with the plan pointer and the operator's decisions | `docs/seeds/strength-viewer.md`, committed in `4659148` |

No code was written. There is no lane branch, no worktree and no unpushed commit.

## Pending and why it is parked

| Step | Issue | Status | Blocked by |
|---|---|---|---|
| VW.1 single layout (one version vs the built-in AI) | #381 | PENDING, ready to build | Machine memory only (below) |
| VW.2 dual layout (bot vs bot) | #382 | PENDING | VW.1, plus an opt-in `launch_prefix` keyword in `src/orchestrator/selfplay.py` (lane evolve owns that file; ES.14 merged in `c7d8699` without it; it would prepend to the list `_build_bot_process` builds, which now starts with `scripts/compat_launch.py`) |
| VW.3 flag into ES.10 | #383 | PARKED | ES.10, held under the operator's T2 answer (PPO first) |
| VW.4 operator acceptance | #384 | PENDING | VW.1, VW.2 |
| Direct task: replace the unwired Stop Run button on the Evolution tab with "To stop an evolve run, close its console window." | none | not started | Nothing; small, can be done any time |

**Memory gate.** On 2026-11-16 only about 1.3 GB was available (85% committed), mostly held
by open Claude and VS Code windows. A build-phase run adds reviewer agents (~270 MB each),
mypy, vitest and Playwright. Resume VW.1 when **available memory is above about 4 GB**, or
when lane evolve's build-phase has finished, so only one build runs at a time. Check it,
read-only, in PowerShell:

```powershell
(Get-Counter '\Memory\Available MBytes').CounterSamples.CookedValue
```

## Operator approvals already given

- One Observer Rush-style window for both layouts; the old pygame container in
  `src/selfplay_viewer/` stays as it is for `scripts/selfplay.py` and `evolve --viewer`
  (2026-11-16).
- Flagging goes through ES.10's `scoreboard.py flag` command unchanged, so VW.3 waits for
  ES.10 (2026-11-16).
- Build VW.1 with build-phase, steps VW.1 only (2026-11-16). Then, on the memory question:
  "Wait for evolve", start once evolve's build-phase finishes or free memory is above
  about 4 GB, and later "Hold until later". **VW.1 is held by the operator's "Hold until
  later"**: reopen the lane only when the operator or the coordinator says go.
- Not authorized: paid Typesafe matches, killing SC2 processes, live SC2 games for VW.1
  (its tests replace the SC2 boundary), pushing, and editing lane repair's
  `scripts/probes/jev_battle_view.py`.

## Resume

1. Reopen the lane with `lane-open` (lane id `evolve-demo`); register the card, bind
   doorbells, and read the inbox as `docs/lanes/README.md` says.
2. Run the memory check above; continue only above about 4 GB.
3. Create a fresh worktree off the current `origin/master-plan/phase-ev`, then
   `uv sync --extra dev --extra viewer` and `npm install` in `frontend/`.
4. From that worktree run:

```text
/build-phase --plan docs/plans/master_plan.md --steps VW.1
```

The plan step carries its flags (`--reviewers code --isolation worktree --ui`). UI
evidence: a Playwright screenshot of `?strength=1` with `window.strengthAction` stubbed
to the status fixture, on free ports if the operator's dashboard holds :3000/:8765, waiting
on load plus explicit content (never `networkidle`). Commits stay on the worktree branch;
hand the finished step to the coordinator (central) as a handoff note with the branch,
commit and gate-evidence file. The coordinator ships.

## Files the lane owns

`docs/seeds/strength-viewer.md`, `docs/investigations/viewer-*.md`, `src/selfplay_viewer/`
(including the new `strength/` package), `scripts/launch-strength-viewer.ps1`,
`frontend/src/components/StrengthViewer.tsx` and its test, `tests/test_strength_viewer.py`,
`frontend/src/main.tsx` (one route branch), and `frontend/src/components/EvolutionTab.tsx`
(the Stop button direct task). This file, `docs/lanes/parked/evolve-demo.md`, is
delegated by central, which owns `docs/lanes/` and ships it.
