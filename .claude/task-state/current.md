# Current Task State

**Task:** Phase EV (evolve `--viewer`) — themed viewer for evolution runs, `documentation/plans/evolve-viewer-plan.md`
**Status:** IN_PROGRESS — EV.1–EV.3 DONE and pushed. `/build-phase --resume EV.4` ran 2026-08-10 and halted at EV.5 (wait step, halt class #4). EV.4 is now Manual UAT **M1**.
**Session SHA:** ee47014
**Last written:** 2026-08-10T15:42:38Z
**Branch:** `master-plan/phase-ev` (NOT mergeable to `master` until `onbrand-pilot` lands — `master` lacks pygame-ce and `launch-a4g.ps1`)

## Next Action

Run **M1** — the EV.4 real-SC2 smoke gate, now written out as a copy-paste checklist in
`documentation/plans/evolve-viewer-plan.md` § Manual UAT (blocks ordered cheapest-first;
per-check expected-outcome table). It is the phase's acceptance gate and it is not
agent-completable — it needs the operator running the real SC2 stack on Windows.

The hard criterion is item **(e1)**: a **real SC2 match visibly rendered inside the themed
container, launched from the dev-observatory `run-evolution` button** (or the identical bare
`.\scripts\launch-evolve.ps1`). Wait for it — pool generation runs a Claude prompt before the
first mirror-calibration game, so first paint can be several minutes out. If (e1) does not
happen, Phase EV has not met its definition of done regardless of unit tests.

After M1 passes, EV.5 (#295, `Type: wait`) is the 4-hour observation run:
`.\scripts\launch-evolve.ps1 -Hours 4` — watch generation 1 attended, then close the container
and let it run detached. Claims **survival only**; throughput needs a paired 2h/2h control (D12).
`/build-phase` will not resume for it — mark EV.5 done in the plan by hand when the run completes.

Serialize against the other pending evolve soaks (EJ.7 #288, EJ.8 #289, EL.7 #279,
Phase 7 Step 6 #280) — one evolve run at a time, machine-wide.

**Operator safety:** stop a `--viewer` run by closing the **console** window. Closing the viewer
container only DETACHES it (the run continues headless). **Never Ctrl+C a `--viewer` run** — the
loop runs off the main thread, so burnysc2's SIGINT kill-switch is never armed and Ctrl+C can
orphan SC2 processes. The dashboard's Stop button is **not wired** to the runner.

**M1/EV.5 are real runs.** The launcher passes no `--no-commit`, so either can promote a version,
flip `bots/current`, and auto-commit `[evo-auto]` to `master-plan/phase-ev`. Expected per D16 —
do not "fix" it by adding `--no-commit`, because the point is to exercise the button as the
registry invokes it.

## Completed

- [fbd7363] EV.1 `--viewer` flag + degradation gate + concurrency guard: PASS iter 3/3 + polish. `_viewer_enabled` probes `pygame` (not `selfplay_viewer`, which imports fine without the extra) and is total — the whole probe sits in one `try/except Exception`, so no meta-path finder can abort a run. Guard lives in `main()` (D13), gated behind `args.viewer` so the parallel path is byte-identical. 1990 → 2005 collected.
- [5b65afb] EV.2 `_EvolveViewerSession` + `main()` inversion: PASS iter 3/3, `--reviewers deep`. Chains callbacks, never touches `stop_event`; `close()` latch bounds `_event_queue`. Fixed a hang inherited from the plan's own §5 block (viewer failing pre-batch-start left `run_loop` uncalled while `main()` blocked forever claiming the run continued) via a `started` event + lock-guarded one-shot claim. 2005 → 2023 collected.
- [59c8cfa] EV.3 launcher opt-in + docs: PASS iter 2. `launch-evolve.ps1` spawns `uv run --extra viewer ... --viewer` (only that executable line changed; guard + delegation byte-identical, ASCII/no-BOM). Found and fixed two false doc claims: the dashboard Stop button does nothing, and `evolve.py`'s own banner told operators to stop the run with a gesture that only detaches. New tests pin the launcher spawn contract and the SKILL.md row.
- [ee47014] `/build-phase --resume EV.4`: no code steps to run. EV.4 classified pure-observation (its `Produces:` carries no code-shaped artifact) → deferred to the phase-end Manual UAT bundle as **M1** rather than halting mid-run; EV.5 (`Type: wait`) halted the phase per halt class #4. Baseline gates green at HEAD: pytest 2022 passed / 2 skipped / 1 deselected (`--extra viewer`), ruff clean, mypy clean (808 files). Wait-handoff comment posted to #295.

## WIP

**Current:** Nothing in flight. All agent-completable work in Phase EV is done; the phase is
blocked on operator observation.

**Approach:** Hand off to the operator for M1 (EV.4's smoke gate).

## Critical Gotchas

- **Fresh worktrees bind Python 3.12** (`requires-python = ">=3.12"`, no `.python-version`) while the project runs 3.14 — produces a false-red `test_current_pointer.py::test_dash_m_submodule_runs_via_alias`. Always `uv venv --python 3.14 && uv sync --extra dev`.
- **Test count depends on the optional extra**: 2007 selected without pygame, 2024 with it (17 pygame-gated tests). Never compare a worktree count to a main-project count.
- **Never run mutation-testing review agents concurrently in one worktree** — they corrupt each other and produce phantom flaky-test findings.
- A piped count over a binary that is not on PATH reads as 0, identical to clean — use `uv run <tool>`, never bare `ruff`/`pytest` in the Bash tool.
- Two `dev/` worktrees are STALE (tips 9 and 6 weeks old): `worktree_skill-iterate-review-dev-gauntlet-1780807852`, `worktree_switchboard-endpoint-launcher`. Not this session's; left alone.
- **This repo uses the single-file task-state model** — no `.claude/task-state/sessions/` dir and no local `task-state-derive.ps1`, and `current.md` is TRACKED here (not gitignored, unlike the workspace contract). Write `current.md` directly; do NOT run the dev-level `Write-DerivedRollup` against this root — it would find zero session files.

## Parked

- **Pre-existing wiki drift, escalated not fixed** (full detail in issue #296): stale
  dashboard/component tables in `documentation/wiki/monitoring.md` (a 10-tab table naming
  13 components absent from disk), `frontend.md` (nonexistent `ImprovementsTab.tsx` ×5; tab
  list disagrees with `App.tsx`), and `promotions.md` (4 of 4 "Dashboard Surfaces" rows point
  at absent files). Plus zero wiki coverage of the Phase EL/EJ `src/orchestrator/` modules —
  that one likely needs a NEW page, which `/repo-update` does not author.
- **vitest count disagreement:** `README.md` says 119, `CLAUDE.md` says 143. Unresolved
  deliberately — the tree carries uncommitted frontend WIP, so a local run would measure that
  rather than HEAD. Resolve after the frontend WIP lands.
- **Follow-up recorded in the plan's §8:** teach the runner to read
  `data/evolve_run_control.json` at the generation boundary so the dashboard Stop button
  actually works; add a `dashboard-stop` `stop_reason`.
- **Uncommitted frontend WIP is parked on purpose** (`frontend/src/App.tsx`,
  `frontend/src/App.test.tsx`, `docs/seeds/*`). Plan §0 says it must NOT ride along with Phase EV
  commits — keep every EV-related `git add` path-scoped.
