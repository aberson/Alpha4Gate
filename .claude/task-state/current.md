# Current Task State

**Task:** Phase EL (Evolution Lines) — build the parallel-lineage / baseline-DB / diversity-extinction evolve substrate
**Status:** BUILDING (EL.1 DONE; EL.2 next)
**Last written:** 2026-06-19T01:00:00Z
**Session SHA:** 0c5d7dc

## Next Action

Continue `/build-phase` for Phase EL — EL.2 (Baseline opponent DB + fitness gauntlet, #274) is next. Goal-scoped to EL.1–EL.5, halt at EL.6.

```
/build-phase --plan documentation/plans/evolution-lines-plan.md --resume EL.2
```

Steps largely sequential: EL.1 ✅ → EL.2 (shares scripts/evolve.py with EL.1) → EL.3 → EL.4 → EL.5 (frontend, --ui). EL.6 (operator smoke) + EL.7 (wait soak) NOT agent-completable.

## Completed
- [pending-sha] EL.1 Lineage registry + round-robin scheduler: PASS iter 2/3 (+ new tests; 1678→ baseline holds). lineages.py overlay; current.txt shape unchanged; back-compat --lineages 1 byte-identical.

## WIP

**Current:** Plan authored, reviewed, wrapped, and synced to GitHub. Plan-expedite chain complete. Ready to build.

**Approach:** New Phase EL on Track 9 captures the four "lines of evolution" ideas from the 2026-04-27 plan-shape notes that never made it into the master plan: (1) parallel lineages, (2) baseline opponent DB, (3) diversity fingerprint + extinction events. Species knobs (task-ordering, time-in-task) DEFERRED — blocked on Track 7 mini-games. Design spine: `current.txt` pointer shape unchanged; lineages are an overlay registry, scheduler flips the existing pointer between heads (keeps all 40 `current_version()` consumers working).

## Completed (this session)

- Audited the 2026-04-27 plan-shape notes against the master plan + investigations — 10/14 ideas already incorporated; 4 gaps found (parallel lineages, extinction events, baseline DB, monolith task-ordering/time-in-task knobs).
- Authored `documentation/plans/evolution-lines-plan.md` (Phase EL, 7 steps).
- /plan-review --autofix → READY (auto-fixed 6: Type:code on EL.1–EL.5, SKILL.md impact row).
- /plan-wrap --autofix → READY WITH GAPS: 1 gap (EL.5 /api/evolve/lineages response shape — non-blocking, refine at build).
- /repo-sync → umbrella #272 + steps #273–#279 created; plan **Issue:** lines backfilled.

## Dead Ends

- Regex backfill of **Issue:** lines failed twice (multi-line Problem bullets broke the bullet-eater; bare `#` didn't match `#\d+`). Line-based scan with `startswith("- **Issue:**")` worked.

## Critical Gotchas

- EL.1 + EL.2 both modify `scripts/evolve.py` (loop-wrap vs `--fitness-mode` argparse) — sequential recommended to avoid worktree merge conflict, even though logically parallel.
- EL.5 is frontend-touching → `--ui` + Playwright evidence mandatory (already in issue #277 flags).
- New `phase: "extinction"` row in `evolve_results.jsonl` (EL.4) — audit existing readers per `feedback_audit_wire_shape_on_storage_change`.
- `_evolve_dir` resolver already exists in `bots/v13/api.py:53` — EL.5 reuses it (do NOT use per-version `_data_dir`).

## Key Files

- Plan: `documentation/plans/evolution-lines-plan.md`
- Master plan: `documentation/plans/alpha4gate-master-plan.md` (Phase EL slots into Track 9 at merge time)
- Evolve substrate: `src/orchestrator/evolve.py`, `scripts/evolve.py`
- Hydra investigation (species framing): `documentation/investigations/hydra-hierarchical-ppo-investigation.md`
- Issues: umbrella #272, steps #273–#279
