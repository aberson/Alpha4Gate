# Current Task State

**Task:** Phase EV (evolve `--viewer`) — themed viewer for evolution runs, `documentation/plans/evolve-viewer-plan.md`
**Status:** EV.1–EV.3 DONE (automated build complete). EV.4 + EV.5 are operator/wait steps.
**Last written:** 2026-08-10
**Session SHA:** 59c8cfa
**Branch:** `master-plan/phase-ev` (NOT mergeable to `master` until `onbrand-pilot` lands — `master` lacks pygame-ce and `launch-a4g.ps1`)

## Next Action

The remaining steps are NOT agent-completable — they need the operator running the real SC2 stack on Windows.

- **EV.4 (#294, operator smoke):** the phase's acceptance gate. Run
  `uv run --extra viewer python scripts/evolve.py --pool-size 2 --games-per-eval 3 --hours 0.5 --no-commit --viewer`
  then the launcher itself, bare: `.\scripts\launch-evolve.ps1`.
  The hard criterion is item (e1): a **real SC2 match visibly rendered inside the themed container, launched from the dev-observatory button**. Wait for it — pool generation runs a Claude prompt before the first mirror-calibration game, so first paint can be several minutes out.
- **EV.5 (#295, wait soak):** 4-hour run via `.\scripts\launch-evolve.ps1 -Hours 4`; watch generation 1 attended, then close the container and let it run detached. Claims survival only; throughput needs a paired 2h/2h control.

Serialize against the other pending evolve soaks (EJ.7 #288, EJ.8 #289, EL.7 #279, Phase 7 Step 6 #280) — one evolve run at a time, machine-wide.

**Operator safety, new this phase:** stop a `--viewer` run by closing the **console** window. Closing the viewer container only DETACHES it (the run continues headless). **Never Ctrl+C a `--viewer` run** — the loop runs off the main thread, so burnysc2's SIGINT kill-switch is never armed and Ctrl+C can orphan SC2 processes. The dashboard's Stop button is **not wired** to the runner.

Resume with `/build-phase --plan documentation/plans/evolve-viewer-plan.md --resume EV.4` after the smoke passes (build-phase will halt at the wait step).

## Completed

- [fbd7363] EV.1 `--viewer` flag + degradation gate + concurrency guard: PASS iter 3/3 + polish. `_viewer_enabled` probes `pygame` (not `selfplay_viewer`, which imports fine without the extra) and is total — the whole probe sits in one `try/except Exception`, so no meta-path finder can abort a run. Guard lives in `main()` (D13), gated behind `args.viewer` so the parallel path is byte-identical. 1990 → 2005 collected.
- [5b65afb] EV.2 `_EvolveViewerSession` + `main()` inversion: PASS iter 3/3, `--reviewers deep`. Chains callbacks, never touches `stop_event`; `close()` latch bounds `_event_queue`. Fixed a hang inherited from the plan's own §5 block (viewer failing pre-batch-start left `run_loop` uncalled while `main()` blocked forever claiming the run continued) via a `started` event + lock-guarded one-shot claim. 2005 → 2023 collected.
- [59c8cfa] EV.3 launcher opt-in + docs: PASS iter 2. `launch-evolve.ps1` spawns `uv run --extra viewer ... --viewer` (only that executable line changed; guard + delegation byte-identical, ASCII/no-BOM). Found and fixed two false doc claims: the dashboard Stop button does nothing, and `evolve.py`'s own banner told operators to stop the run with a gesture that only detaches. New tests pin the launcher spawn contract and the SKILL.md row.

## Critical Gotchas

- **Fresh worktrees bind Python 3.12** (`requires-python = ">=3.12"`, no `.python-version`) while the project runs 3.14 — produces a false-red `test_current_pointer.py::test_dash_m_submodule_runs_via_alias`. Always `uv venv --python 3.14 && uv sync --extra dev`.
- **Test count depends on the optional extra**: 2007 selected without pygame, 2024 with it (17 pygame-gated tests). Never compare a worktree count to a main-project count.
- **Never run mutation-testing review agents concurrently in one worktree** — they corrupt each other and produce phantom flaky-test findings.

## WIP

**Current:** Nothing in flight. The automated build is complete and committed on `master-plan/phase-ev`; nothing is pushed yet (`/repo-update` pending).

**Approach:** Hand off to the operator for EV.4's smoke gate.
