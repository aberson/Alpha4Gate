# Current Task State

**Task:** Lane B (Alpha4Gate flagship refinement) of `dev/.claude/task-state/no-build-activities-runlist-2026-09-02.md`, items 1-4. Planning and doc work only — the build toolkit is frozen (`dev/.claude/task-state/freeze.json`).
**Status:** Lane B items 1-4 COMPLETE 2026-09-02. Phase EV itself is UNCHANGED — no code shipped since `59c8cfa`; M1 is still the acceptance gate and still operator-only. The evolve-restructure thread has CONVERGED (rounds 1 and 2 done, round 3 declared unnecessary) and produced a new reviewed plan, **Phase EI**.
**Session SHA:** 0ac8caf (working tree has uncommitted Lane B doc work)
**Last written:** 2026-09-02
**Branch:** `master-plan/phase-ev`. The "not mergeable until onbrand-pilot lands" blocker is DISSOLVED — the branch already contains onbrand-pilot, so landing is one merge. See `documentation/branch-landing-phase-ev.md`.

## Next Action

**Uncommitted Lane B work is on disk** (11 files). Commit it path-scoped, then decide two things:

1. **Run M1** — still the EV.4 acceptance gate, still operator-only. It is now gate 2 of 8 in
   `documentation/operator-gate-runbook.md`, which orders every pending operator gate cheapest-first
   and leads with three defects that would each make a gate look successful while proving nothing.
2. **Answer one letter** to unblock the evolve-restructure thread — the operator's truncated idea (ii)
   is now a six-option decision card in `docs/seeds/evolve-restructure-operator-notes.md`. Phase EI
   does NOT depend on the answer.

Optionally `/repo-sync` the new Phase EI plan to mint its 14 issues. Not urgent: the build toolkit is
frozen, so nothing can be built from it yet.

## Completed

- **2026-09-02 — Lane B items 1-4 (this session, ~60 agents / ~5.5M subagent tokens, 0 errors).**
  - **Item 1, master-plan spine reconciliation.** 39 of 40 relative links in `master_plan.md` were
    broken (28 used a `documentation/` prefix that doubles; 11 used `../` that escapes the folder);
    all now resolve. The same dead parent link sat in 7 of 11 sub-plans, not just phase-6. Added
    narrative sections for Phases EL, EJ and EV (none existed), extended Track structure, Decision
    graph, Glossary and Time budget, corrected 3 stale index rows, resolved 2 contradictions with
    Phase R's own supersession note, gave Phases D and 7 the Status lines every other shipped phase
    has, and added 5 plan-history entries covering everything since 2026-05-19.
  - **Item 2, evolve-restructure thread → Phase EI.** Two adversarial rounds (23 agents). Round 2's
    finding: **gate 1 selects improvement TEXTS, not code, 100% of the time** — commits `f2eb564`
    and `ce8545f` build the same version from the same parent and the same improvement text and
    produce different bots. Round 3 declared unnecessary. Produced
    `documentation/plans/evolve-evidence-layer-plan.md` (Phase EI, 14 steps), plus round-1 and
    round-2 records under `documentation/investigations/`. plan-review found 7 Blockers (all fixed);
    plan-wrap returned READY WITH GAPS, 0 Blockers, and all 7 gaps are closed.
  - **Item 3, operator-gate runbook.** `documentation/operator-gate-runbook.md` — 8 gates,
    cheapest-first, ~28-30 h serialized. Leads with three confirmed defects: the launcher's missing
    generation cap, an absent baselines registry that makes two gates test nothing, and the fact
    that `--lineages N` does not create N lineages, which makes EL.7 unrunnable as its plan
    specifies it.
  - **Item 4, branch landing note.** `documentation/branch-landing-phase-ev.md` — the stated blocker
    is already satisfied by containment, a read-only merge-tree predicts zero conflicts, and CI has
    never run on any of the 18 commits because both workflows gate on pushes to master only.
  - An independent verification pass over the runbook and landing note caught 2 wrong commands
    (one work-destroying), 2 wrong anchors and 3 wrong numbers; all corrected.

- [7e08491] `/repo-wrap` (Rail A, OWNED) → `/repo-update` 2026-08-19, doc-only close-out at unchanged HEAD: tree clean, origin already at `7e08491`, so nothing committed or pushed. Memory updated (Phase EV entry re-anchored `afb19d5`→`7e08491`; NEW `project_evolve_restructure_thread_2026_08.md` for the open evolve-restructure planning thread; MEMORY.md index refreshed). Observatory synced — wrote 5 obs tasks to `dev.code-workspace` IN THE PROJECT ROOT, untracked (see Parked). Drift checks skipped as redundant (full reconciliation was `978ee4f`; zero code changes since). No posterity issue (doc-only), no tour. Branch 17 ahead / 1 behind local `master`; merge still blocked on `onbrand-pilot`.

- [978ee4f] `/repo-update` (doc-only; no Phase EV code touched): reconciled current-state docs against source — 34 stale facts across `CLAUDE.md`, `README.md` and 10 wiki pages. Pointer v4→**v13**; wiki `FEATURE_DIM=24`/`BASE=17` → v13 **55/48**, v0 **47/40**, DB stores **40**; test counts reconciled to one convention; "Six layers" → seven (no `tactics.py`); Phases **EL** and **EJ** added to Current state (both shipped, both previously absent). `.gitignore` gained `.build-step/` + 2 task-state scratch entries. Issue #297 opened+closed. Gates: ruff clean, mypy clean 866 files, 2024 selected, frontend 234.

- [fbd7363] EV.1 `--viewer` flag + degradation gate + concurrency guard: PASS iter 3/3 + polish. `_viewer_enabled` probes `pygame` (not `selfplay_viewer`, which imports fine without the extra) and is total — the whole probe sits in one `try/except Exception`, so no meta-path finder can abort a run. Guard lives in `main()` (D13), gated behind `args.viewer` so the parallel path is byte-identical. 1990 → 2005 collected.
- [5b65afb] EV.2 `_EvolveViewerSession` + `main()` inversion: PASS iter 3/3, `--reviewers deep`. Chains callbacks, never touches `stop_event`; `close()` latch bounds `_event_queue`. Fixed a hang inherited from the plan's own §5 block (viewer failing pre-batch-start left `run_loop` uncalled while `main()` blocked forever claiming the run continued) via a `started` event + lock-guarded one-shot claim. 2005 → 2023 collected.
- [59c8cfa] EV.3 launcher opt-in + docs: PASS iter 2. `launch-evolve.ps1` spawns `uv run --extra viewer ... --viewer` (only that executable line changed; guard + delegation byte-identical, ASCII/no-BOM). Found and fixed two false doc claims: the dashboard Stop button does nothing, and `evolve.py`'s own banner told operators to stop the run with a gesture that only detaches. New tests pin the launcher spawn contract and the SKILL.md row.
- [afb19d5] `/repo-wrap` (Rail A, OWNED per registry `owned = true`) + `/user-wrap` → `session-wrap --end`: pushed `ee47014..afb19d5` to `origin/master-plan/phase-ev` (now 0 ahead). Full `/repo-update` delegation deliberately NOT run — see Parked. Git-verb router anomaly pre-flight triggered checks 3 and 4, so Steps B/C were withheld (`ask-first pending`); the withheld base action was a no-op anyway (session edit set already committed in 7658605, 0 ahead), so nothing is stranded.
- [ee47014] `/build-phase --resume EV.4`: no code steps to run. EV.4 classified pure-observation (its `Produces:` carries no code-shaped artifact) → deferred to the phase-end Manual UAT bundle as **M1** rather than halting mid-run; EV.5 (`Type: wait`) halted the phase per halt class #4. Baseline gates green at HEAD: pytest 2022 passed / 2 skipped / 1 deselected (`--extra viewer`), ruff clean, mypy clean (808 files). Wait-handoff comment posted to #295.

## WIP

**Current:** Nothing in flight. All agent-completable work in Phase EV is done; the phase is
blocked on operator observation (M1). The evolve-restructure planning thread is blocked on the
operator finishing their truncated idea (ii).

**Approach:** Hand off to the operator for M1 (EV.4's smoke gate). Resume the restructure thread
when idea (ii) arrives — full state in `docs/seeds/evolve-restructure-operator-notes.md` and
memory `project_evolve_restructure_thread_2026_08.md`.

## Critical Gotchas

- **Fresh worktrees bind Python 3.12** (`requires-python = ">=3.12"`, no `.python-version`) while the project runs 3.14 — produces a false-red `test_current_pointer.py::test_dash_m_submodule_runs_via_alias`. Always `uv venv --python 3.14 && uv sync --extra dev`.
- **Test count depends on the optional extra**: 2007 selected without pygame, 2024 with it (17 pygame-gated tests), across **113 test files**. Never compare a worktree count to a main-project count. The dev `.venv` currently HAS the viewer deps installed, so a bare `uv run pytest` reports 2024, not 2007 — 2007 is the deps-absent case (e.g. Linux CI, where `pywin32` cannot install).
- **Never run mutation-testing review agents concurrently in one worktree** — they corrupt each other and produce phantom flaky-test findings.
- A piped count over a binary that is not on PATH reads as 0, identical to clean — use `uv run <tool>`, never bare `ruff`/`pytest` in the Bash tool.
- Two `dev/` worktrees are STALE (tips 9 and 6 weeks old): `worktree_skill-iterate-review-dev-gauntlet-1780807852`, `worktree_switchboard-endpoint-launcher`. Not this session's; left alone.
- **This repo uses the single-file task-state model** — no `.claude/task-state/sessions/` dir and no local `task-state-derive.ps1`, and `current.md` is TRACKED here (not gitignored, unlike the workspace contract). Write `current.md` directly; do NOT run the dev-level `Write-DerivedRollup` against this root — it would find zero session files.
- **CLAUDE.md's test counts are CORRECT — do not "fix" them.** Measured 2026-08-10 with `uv run --extra viewer python -m pytest -q`: **2022 passed, 2 skipped, 1 deselected** → 2024 *selected*, 2025 *collected*. CLAUDE.md says "2007 unit tests; 2024 with the optional `[viewer]` extra" and 2024 − 17 pygame-gated = 2007, so both numbers check out. A review lens this session claimed drift by comparing 2007 against 2022-*passing* — conflating **passed** with **selected**. Re-measure before ever writing a new count in. **2026-08-18: this convention was PRESERVED, not overwritten** — `/repo-update` propagated the same 2007/2024 pair out to README + wiki (which carried 1448/1020/~1327) and added only the file count. The frontend number WAS resolved: the tree is now clean of frontend WIP, so a real run measured **234 (228 passing, 6 skipped) across 23 files**; README's 119 and CLAUDE.md's 143 were both wrong.
- **NEVER `git add documentation/images/`** — that directory holds `Recording 2026-04-15 223435.mp4` at 199,528,926 bytes — but it is now GITIGNORED (`.gitignore:88`, `documentation/images/*.mp4`), verified via `git check-ignore`, so adding the directory no longer stages the video. Path-scoping every `git add` remains the standing rule for an unrelated reason: `EVO_AUTO` commits sweep whatever was already staged.
- **The launcher silently truncates soaks.** `scripts/evolve.py --generations` defaults to **1** and `launch-evolve.ps1` (line 72) omits it, so the dev-observatory `run-evolution` button stops after ONE generation regardless of `-Hours`; the launcher's own line-28 comment claims otherwise. `documentation/wiki/operator-commands.md` is correct and does pass `--generations 0`. Fixing the launcher is an operator-facing behaviour change (a 4h run becomes 4 real hours of SC2 that can auto-commit `[evo-auto]`), so it was left as a deliberate decision rather than folded into a doc pass.

## Parked

- **Evolve-restructure planning thread OPEN** (started 2026-08-06): verified pipeline map
  ("11 steps vs. reality", file:line-anchored) + operator ideas live in
  `docs/seeds/evolve-restructure-operator-notes.md` (committed); README § self-play arena now
  renders it as `documentation/images/evolve-arena-{light,dark}.svg`. Next: operator finishes
  the truncated idea (ii) ("Use a SC2 version of the 'judge…") → adversarial-review rounds →
  `/plan-feature`. Memory: `project_evolve_restructure_thread_2026_08.md`.
- **`dev.code-workspace` in the PROJECT root is untracked and of uncertain provenance** —
  `observatory sync` run 2026-08-19 from inside Alpha4Gate wrote "5 obs task(s)" to
  `C:\Users\abero\dev\Alpha4Gate\dev.code-workspace` rather than the dev root's workspace file
  (possibly cwd-derived target resolution in dev-observatory). Decide: gitignore it, commit it,
  or fix the sync target. Left untracked deliberately.
- **Wiki drift that `/repo-update` must NOT auto-fix** (issue #296): stale dashboard/component
  tables in `documentation/wiki/monitoring.md` (a 10-tab table naming 13 components absent from
  disk), `frontend.md` (nonexistent `ImprovementsTab.tsx` x5; tab list disagrees with `App.tsx`),
  and `promotions.md` (4 of 4 "Dashboard Surfaces" rows point at absent files). STILL OPEN after
  the 2026-08-18 pass, which fixed only mechanical numeric/pointer drift.
- **Zero wiki coverage of the Phase EL/EJ `src/orchestrator/` modules** (`gate_stats.py`,
  `lineages.py`, `population.py`, `fingerprint.py`, `baselines.py`). Both phases are now named in
  `CLAUDE.md` Current state (2026-08-18), but they have no wiki page. This needs a NEW page, which
  `/repo-update` does not author -- escalate to the operator to decide where it lives.
- **`documentation/wiki/evaluation-pipeline.md` still carries a 24-row feature table.** Its scalar
  claims were corrected 2026-08-18 and the table is now explicitly labelled the historical
  pre-Phase-B layout with a pointer to `_FEATURE_SPEC`, but the rows themselves are only right for
  indices 0-16: the advisor block moved to the end of the vector (48-54 in v13). Rewriting it to
  55 rows is authoring, not a numeric fix, so it was escalated rather than guessed.
- **Follow-up recorded in the plan's section 8:** teach the runner to read
  `data/evolve_run_control.json` at the generation boundary so the dashboard Stop button actually
  works; add a `dashboard-stop` `stop_reason`.
- **`.build-step/` forensics reports** (`prewrap-ownership.md`, `prewrap-coherence.md`,
  `prewrap-delta.md`, `prewrap-hygiene.md`, `plan-wrap-report.md`, `wiki-check-report.md`) are
  still on disk and safe to delete once read. They are now gitignored, so they no longer pose a
  `git add -A` risk.

RESOLVED 2026-08-18, removed from this list: README self-disagreement on test counts (3 different
Python numbers); the vitest 119-vs-143 disagreement (measured 234 on a now-clean tree);
`.build-step/` not being gitignored; and the parked uncommitted frontend WIP (landed in `82873ab`).
