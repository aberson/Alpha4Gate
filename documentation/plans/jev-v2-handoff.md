# Jev v2 build handoff

Preparation is READY and the first build span (Steps 212 and 224) is DONE; **next is operator Step 213 (#330)**. See [Next](#next-operator-step-213-330). Read [the plan](jev-v2-plan.md), [review](jev-v2-review.md), [wrap](jev-v2-wrap.md), [issue sync](jev-v2-sync.md) and the [Jev v2 operator guide](../operator/jev-v2-validation.md). Umbrella: [#327](https://github.com/aberson/Alpha4Gate/issues/327).

Use branch `master-plan/phase-ev` in the Alpha4Gate checkout. The preparation commit includes the real Typesafe integration prerequisite and its validation records; starting from master or the earlier `4ea560e` checkpoint omits that prerequisite. Resolve the current preparation commit with git before creating a build worktree.

## First build span (complete)

Steps **212 (#328)** and **224 (#329)** are DONE (`9b2136e`, `28dc15d`, 2026-10-08/09; both issues closed), and the build stopped before operator Step **213 (#330)** as planned. No SC2 launch, hosted Typesafe call or new run archive was part of the span. Gates at `28dc15d`: pytest 2974 passed / 9 skipped / 2 deselected (the main `.venv` has the `[viewer]` extra; 6 of the skips are the opt-in real-browser tests gated on `JEV_BROWSER_TESTS=1`), vitest 359 passed / 6 skipped, strict mypy (824 files), ruff, eslint (0 errors; 1 pre-existing warning), vite build, and the real-browser suite 6/6 with `JEV_BROWSER_TESTS=1`. The text below is the span's original brief, kept for the record.

Step 224 implements the approved dashboard-first launch: open the UI, select the exact run, render its archived policy and acknowledge readiness before starting SC2; keep the same page following a batch. Fail visibly on readiness timeout. Follow the plan's separate LaunchError contract rather than reusing the closed archived-run error parser.

The span was built with Opus under the build host's worker/reviewer policy, from the repository root, with this goal (satisfied; do not re-run):

```text
/goal "Jev v2 Steps 212 and 224 are Status: DONE in documentation/plans/jev-v2-plan.md, issues #328 and #329 are closed, and offline Python tests, strict mypy, ruff, frontend tests, lint and build pass. STOP before operator Step 213 (#330); no paid matches are part of this goal."
/build-phase --plan documentation/plans/jev-v2-plan.md
```

Use `uv run pytest -m "not sc2" -q`, `uv run mypy src bots --strict`, `uv run ruff check .`, `npm --prefix frontend run test:run`, `npm --prefix frontend run lint`, and `npm --prefix frontend run build` for the offline checks, plus the specific acceptance checks in each step.

## Next: operator Step 213 (#330)

Step 213 is the first live gate. It needs real SC2, the hosted Typesafe service (paid, realtime) and the operator's visible browser. Its Done-when is in [the plan](jev-v2-plan.md#step-213-observe-the-harder-opponent-baseline), and the procedure is [section 12 of the Jev v2 validation guide](../operator/jev-v2-validation.md#12-step-213-run-the-harder-opponent-baseline): Stage 1 prerequisites and dry run, Stage 2 staging, Stage 3 hosted smoke, Stage 4 baseline, each passing before the next starts. Run it before any Step 214 work. The first non-dry-run `--panel baseline` preflight captures and finalizes the frozen v1 baseline (plan section 9), so it must freeze today's v1, launch hook included.

The Done-when requires a >=60-second production smoke before the six-case baseline completes. The guide's Stage 3 runs it as one attended hosted v1 match at Terran difficulty 1, seed 1 with the single-match launcher below: one paid match outside the batch, and not one of the six baseline cases (the launcher's defaults, Terran difficulty 3 seed 11, would replay baseline case 1). Counting the first baseline case, watched live for >=60 s, is an alternative only if Step 213's report in `documentation/plans/jev-v2-validation.md` records that choice.

Run in: fresh PowerShell window @ Alpha4Gate · Model: any (no build work)

```powershell
uv run python scripts/benchmark_jev.py --panel baseline --dry-run
```

The dry run sends no request, starts nothing and writes nothing. On 2026-10-09 it resolved six cases with v1 `state=capture_pending`. Stage 2 is the scripted staging match (no key, no service call); a staging failure stops Step 213.

```powershell
uv run python scripts/benchmark_jev.py --panel staging --max-game-seconds 120 --max-wall-seconds 300
```

Stage 3 is the hosted smoke. The launcher loads the saved key itself and hands it to the game process only.

```powershell
powershell -File scripts/launch-jev.ps1 -Version v1 -DecisionProvider typesafe -Difficulty 1 -Seed 1
```

Stage 4: load the saved key into the window as in [the Jev operator guide](../operator/jev-validation.md#reuse-the-encrypted-local-key-from-assisted-setup), then play the baseline: dashboard-first by default, one invocation of at most six games, 7200 s and 2700 requests.

```powershell
uv run python scripts/benchmark_jev.py --panel baseline
```

Remove the key when finished with `Remove-Item Env:TYPESAFE_API_KEY`.

Watch items from the build reviews:

- A `provenance_mismatch` naming unexpected folders comes from the post-case `verify_snapshot` (`src/jev/benchmark.py:3311`, raise at `:1163`). Children run with their working directory inside the snapshot, and the check rejects any folder the snapshot does not list.
- `verify_session_served` has its own 60-second window after `wait_for_dashboard` (`src/jev/launch.py:1253` then `:1266`). Dashboard readiness can therefore take up to about 130 s, against D7's 60 s.
- A paused page during a running game quotes the launcher's last message.
- `launch-jev.ps1 -Version` defaults to `v2`, which is not packaged until Step 214. Pass `-Version v1`.
- The real-browser tests are opt-in (`JEV_BROWSER_TESTS=1`) and are not in CI.

Local review evidence (git-ignored): `.build-step/jev-v2-212/` and `.build-step/jev-v2-224/`.

## Prerequisite evidence and limits

The unchanged JI source passed 2774 Python tests (3 skipped, 2 deselected), 283 frontend tests (6 skipped), strict mypy (822 files), ruff, frontend lint/build, and independent code review before this preparation. These are prior JI results, not v2 results. Full commands and real-service evidence are in [Typesafe validation](jev-typesafe-validation.md).

Hosted run `aa57512dd51745c2bcf7fb47e2c15f23` won the basic Terran smoke and recorded 61 model calls. Pending-stop run `068948b1b0d346f6a350fe3d4fb604ab` verified cancellation. Neither establishes performance against harder opponents. Preserve `bots.jev.v1` as the benchmark baseline; implement the new player under `bots.jev.v2`.

Credentials already exist in the operator's encrypted local store; do not copy credentials into source or prompts. Reuse healthy local dashboard services where appropriate and terminate only owned game processes. The tracked derived `.claude/task-state/current.md` and untracked `dev.code-workspace` were excluded from this preparation commit. The former has an existing repository-hygiene conflict with absolute-path task-state fields; do not sweep it into build commits or erase another session's state to pass checks.
