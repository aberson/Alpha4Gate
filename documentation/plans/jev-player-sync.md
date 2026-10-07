# Phase JV issue sync and build handoff

Status: READY. 2026-10-07. Repository: `aberson/Alpha4Gate`; working branch: `master-plan/phase-ev`; default branch: `master`.

Plan review, decision-inventory verification and plan-wrap completed before issue creation. Plan-review corrected Step 204's UI-bundle declaration; operator choices remain unchanged. Eight of eight steps have rich fields, and all eight remote issue bodies plus umbrella cross-references were read back and verified. Both create and enrich footer shapes were recognized; no existing Phase JV issue or cross-plan collision was found.

## Issue inventory

| Step | Issue | Type | Depends on |
|---|---|---|---|
| Umbrella | [#317](https://github.com/aberson/Alpha4Gate/issues/317) | Phase | None |
| 201 Policy runtime | [#318](https://github.com/aberson/Alpha4Gate/issues/318) | code | None |
| 202 Economy | [#319](https://github.com/aberson/Alpha4Gate/issues/319) | code | #318 |
| 203 Complete rush | [#320](https://github.com/aberson/Alpha4Gate/issues/320) | code | #319 |
| 204 Evidence API | [#321](https://github.com/aberson/Alpha4Gate/issues/321) | code | #320 |
| 205 Dashboard | [#322](https://github.com/aberson/Alpha4Gate/issues/322) | code | #321 |
| 206 Validation workflow | [#323](https://github.com/aberson/Alpha4Gate/issues/323) | code | #322 |
| 207 Real pipeline smoke | [#324](https://github.com/aberson/Alpha4Gate/issues/324) | operator | #323 |
| 208 Full-match acceptance | [#325](https://github.com/aberson/Alpha4Gate/issues/325) | operator | #324 |

Created one umbrella and eight steps; enriched zero; updated umbrella checklist; closed zero. Every issue includes fresh-context contracts, files, acceptance, dependency and explicit sequential-execution rationale. Steps 204/205 require browser evidence. Source links pin the published plan at `80da7797c230f502cfe55a56ff70dbf7214c62f9` because the plan is on `master-plan/phase-ev`, not yet on default branch `master`; canonical footer paths retain the resolved default branch for eventual landing.

## Build boundary

Run in `C:\Users\x\dev\Alpha4Gate`. Begin with Step 201; no Jev implementation has run. Do not auto-run operator Steps 207/208. Do not resume the prior EH task simply because older state mentions it. EH.3-EH.10 remain separate unfinished work (#308-#315), as do EV live gates.

```text
/goal "Jev Phase JV automated Steps 201-206 are all marked Status: DONE in documentation/plans/jev-player-plan.md (issues #318-#323 closed), and uv run pytest / uv run mypy src bots --strict / uv run ruff check . plus frontend npm run test:run / npm run lint / npm run build exit 0. STOP before operator Steps 207-208 (issues #324-#325); those are an operator handoff, not part of this goal."
/build-phase --plan documentation/plans/jev-player-plan.md
```

No goal or Stop hook was armed by this preparation. The commands above are operator-entered continuation instructions. Existing unrelated `dev.code-workspace` and prior task-state changes are not part of the plan commits. Code tests and SC2 games were not run for this documentation/issue-sync task; the documentation commit's existing sandbox pre-commit check passed.

Plan pipeline: plan-review + plan-wrap -> repo-sync (step 4 of 5) -> build-phase.
