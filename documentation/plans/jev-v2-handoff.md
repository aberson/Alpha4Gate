# Jev v2 build handoff

Preparation is READY. Read [the plan](jev-v2-plan.md), [review](jev-v2-review.md), [wrap](jev-v2-wrap.md) and [issue sync](jev-v2-sync.md). Umbrella: [#327](https://github.com/aberson/Alpha4Gate/issues/327).

Use branch `master-plan/phase-ev` in the Alpha4Gate checkout. The preparation commit includes the real Typesafe integration prerequisite and its validation records; starting from master or the earlier `4ea560e` checkpoint omits that prerequisite. Resolve the current preparation commit with git before creating a build worktree.

## First build span

Execute Step **212 (#328)**, then **224 (#329)** in plan file order. Stop before operator Step **213 (#330)**. Later live gates 222 and 223 are outside this initial goal. No v2 implementation or paid match was run during this preparation.

Step 224 implements the approved dashboard-first launch: open the UI, select the exact run, render its archived policy and acknowledge readiness before starting SC2; keep the same page following a batch. Fail visibly on readiness timeout. Follow the plan's separate LaunchError contract rather than reusing the closed archived-run error parser.

User intends to build with Opus. Apply the build host's worker/reviewer policy. Run from the repository root:

```text
/goal "Jev v2 Steps 212 and 224 are Status: DONE in documentation/plans/jev-v2-plan.md, issues #328 and #329 are closed, and offline Python tests, strict mypy, ruff, frontend tests, lint and build pass. STOP before operator Step 213 (#330); no paid matches are part of this goal."
/build-phase --plan documentation/plans/jev-v2-plan.md
```

Use `uv run pytest -m "not sc2" -q`, `uv run mypy src bots --strict`, `uv run ruff check .`, `npm --prefix frontend run test:run`, `npm --prefix frontend run lint`, and `npm --prefix frontend run build` for the offline checks, plus the specific acceptance checks in each step.

## Prerequisite evidence and limits

The unchanged JI source passed 2774 Python tests (3 skipped, 2 deselected), 283 frontend tests (6 skipped), strict mypy (822 files), ruff, frontend lint/build, and independent code review before this preparation. These are prior JI results, not v2 results. Full commands and real-service evidence are in [Typesafe validation](jev-typesafe-validation.md).

Hosted run `aa57512dd51745c2bcf7fb47e2c15f23` won the basic Terran smoke and recorded 61 model calls. Pending-stop run `068948b1b0d346f6a350fe3d4fb604ab` verified cancellation. Neither establishes performance against harder opponents. Preserve `bots.jev.v1` as the benchmark baseline; implement the new player under `bots.jev.v2`.

Credentials already exist in the operator's encrypted local store; do not copy credentials into source or prompts. Reuse healthy local dashboard services where appropriate and terminate only owned game processes. The tracked derived `.claude/task-state/current.md` and untracked `dev.code-workspace` were excluded from this preparation commit. The former has an existing repository-hygiene conflict with absolute-path task-state fields; do not sweep it into build commits or erase another session's state to pass checks.
