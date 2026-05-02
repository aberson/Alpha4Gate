---
name: improve-bot
description: Run a long autonomous session to improve the Alpha4Gate bot's performance along a measurable axis. Supports demo (no mutations), training-only soak runs (learning self-improvement via the daemon), plan-based dev work, and (with --self-improve-code) fully autonomous reactive hybrid loops. Designed for overnight / unattended runs.
user-invocable: true
argument: The improvement suggestion (optional). Examples: "raise win rate vs diff 1 past 50%", "fix orphaned transitions", "run overnight and improve the bot's performance". Flags - `--demo` for pure observation (no mutations), `--self-improve-code` to allow autonomous code changes during the run.
---

# /improve-bot

Entry point for a long autonomous run that aims to improve the Alpha4Gate bot along some measurable axis.

## Three camps of "improvement"

| Camp | Flavors | What changes | Flag |
|---|---|---|---|
| **Code self-improvement** | `dev`, `hybrid` | source code, branches, master, remote | `--self-improve-code` (gates `hybrid`) |
| **Learning self-improvement** | `training` | `data/hyperparams.json`, `data/reward_rules.json`, checkpoints, `training.db` (the training daemon mutates these during normal operation) | *(no flag — default)* |
| **Demo** | `demo` | nothing (backend runs **without** `--daemon`, pure observation) | `--demo` |

## Flavors

| Flavor        | What it does                                                                 | When to use                                       |
|---------------|------------------------------------------------------------------------------|---------------------------------------------------|
| **demo**      | Backend without `--daemon`, dashboard + alerts observation only, no mutations | Rehearsal, pre-flight drill, dashboard/alert verification |
| **training**  | Soak run with daemon ON. Daemon trains + updates weights. No source edits. Captures metric deltas + findings writeup | You want to observe learning-side improvement without code edits |
| **dev**       | Plan doc → `/build-phase` → commits                                          | You already know what to fix                      |
| **hybrid**    | Training reacts to failures, opens issues, spawns `/build-step` work, loops  | Overnight unattended code self-improvement        |

The `--self-improve-code` flag gates autonomous **source-code** changes (Phase 4 hybrid loop). Without it, the skill will not merge or push code on its own. The `--demo` flag forces the demo flavor and disables all mutation paths (daemon off, `data/` snapshot-verified, no git writes).

---

## Phase 0: Classify the suggestion

Parse the argument. Separate any flags (`--demo`, `--self-improve-code`) from the suggestion text. Pick a flavor in this order:

1. **demo** — `--demo` flag is set (regardless of suggestion text). Daemon OFF, no mutations, rehearsal/observation only.
2. **hybrid** — suggestion is open-ended AND `--self-improve-code` is set (e.g. "run overnight and improve the bot's performance").
3. **dev** — suggestion names a known bug or a concrete code change (e.g. "fix orphaned transitions", references a GH issue). `--self-improve-code` is not required for dev because the user has already authorized a specific fix by naming it.
4. **training** — default: suggestion names a metric axis or is absent (e.g. "raise win rate vs diff 1", or empty). Daemon ON, learning self-improvement via normal training loop.

Flag conflicts: `--demo` + `--self-improve-code` is contradictory — stop and ask which one the user meant. `--demo` silently disables any `--self-improve-code` authorization for the duration of the run.

If the suggestion is ambiguous, ask ONE disambiguating question, then proceed. Do not punt with "too vague" — the whole point of this skill is that "improve the bot's performance" IS a valid request, you just need to pick the measure.

---

## Phase 1: Front-loaded conversation

These questions lock the run down so it can go unattended. Scale them down based on what the suggestion already answered — never re-ask what's already clear.

1. **Performance measure** (the gate). Options:
   - Win rate vs difficulty N
   - Promotion events per N games
   - Reward-trend slope
   - Transition volume per hour
   - Error-budget silence (no watchdog trips)
   - Decision log coverage

   Multi-measure is fine. Default when unspecified: **win rate vs difficulty 1**.

2. **Stop conditions** (whichever fires first):
   - Wall clock — default **2h** (for `demo` flavor, default **30 min**)
   - Game count — default none
   - Error budget — default **4/5** (matches soak-3 convention). **Demo flavor: N/A** (no daemon, no error counter).
   - First promotion event — default no
   - Watchdog trip — **always on** in training/hybrid. **Demo flavor: N/A** (no daemon, no watchdog).

3. **`data/` state**:
   - **demo**: snapshot `data/` at start (`cp -r data data.demo-snapshot-$TS`); at stop, diff/hash-compare to prove nothing mutated. Never mutate `data/`; if a mutation is detected, flag it loudly in the writeup as a demo-contract violation.
   - **training/hybrid**: fresh empty or continue from current? Check the current state and the most recent soak log to propose a default (fresh empty was the soak-4 plan per memory).

4. **Self-improve-code confirmation** — if flavor is `hybrid`, confirm `--self-improve-code` is set and explicitly confirm the user OKs autonomous commit/merge/push/issue-create for the duration of the run. If flavor is `demo`, confirm the user understands no learning or code improvement will happen this run — it is rehearsal/observation only.

Print the full run plan (flavor, measures, stops, data state, flags on/off) in one message and wait for user OK before entering Phase 2.

---

## Phase 2: Pre-flight (interactive, user at keyboard)

Any failure here stops the run before it goes unattended.

### Git + GitHub

```bash
git status                            # clean, on master
git rev-parse --abbrev-ref HEAD       # == master
git fetch origin && git status -sb    # up to date with origin/master
gh auth status                        # authenticated (skip for demo)
gh api user                           # returns 200 (skip for demo)
```

Dry-run commit (confirms no hook blocks the operations the loop will use). **Skip this block for `demo` flavor** — demo performs no git writes, so there's nothing to verify:

```bash
TS=$(date +%s)
git checkout -b improve-bot/preflight-$TS
git commit --allow-empty -m "preflight: verify commit works"
git checkout master
git branch -D improve-bot/preflight-$TS
```

Explicitly list the operations the run may perform and have the user approve them as a set. The set depends on flavor:

- **demo**: none — no git writes, no GitHub writes, no `data/` writes. Approval set is empty; confirm the user understands this.
- **training**: local `data/` mutations only (via the daemon). No git writes beyond the baseline tag (`git tag`, `git push origin <tag>`).
- **dev**: `git add`, `commit`, `tag`, `branch`, `checkout`, `push origin <branch>`, `push origin master`, `push origin --tags`; `gh issue create`, `gh issue comment`, `gh issue close`.
- **hybrid** (requires `--self-improve-code`): same as dev, plus `git merge --no-ff` on master and the automatic-rollback `push origin master --force-with-lease` path documented in Phase 4.

**Forbidden, ever:** `git push --force`, `reset --hard` to any remote ref, rebase of published history, `--no-verify`, deleting `baseline`/`merged` tags.

### `data/` state

```bash
ls -la data/
```

- **demo**: `mkdir -p data-snapshots && cp -r data data-snapshots/data.demo-snapshot-$TS` (the snapshot is the contract — at stop, the skill will diff `data/` against this snapshot; any diff is a demo-contract violation). Never `mv`, never `mkdir` over it.
- **training / dev / hybrid**: if fresh was chosen: `mkdir -p data-snapshots && mv data data-snapshots/data.bak-$TS && mkdir data`. Never `rm -rf data/`.

### Soak-test pre-flight checklists

Canonical pre-flight lives in `documentation/soak-test-runs/README.md`. Execute both of these in order; stop on any failure:

- **§2 Pre-soak checklist** (§2.1–§2.5): `data/` snapshot-or-reset, daemon config values, disk budget, duration/stop conditions, create the run log.
- **§3 Startup sequence** (§3.1–§3.5): start SC2, start backend with daemon enabled, start frontend, open dashboard, run the **§3.5 synthetic alert pre-flight** (verifies the alerts pipeline end-to-end before the long run begins).

Note that §3.5 specifically is the alerts-pipeline verification — do not conflate it with "the pre-flight" broadly. Both §2 and §3 must pass.

For **demo** flavor, §2 still applies (minus daemon config values, since the daemon is off), and §3 still applies except §3.2 runs **without** `--daemon`. Run §3.5 as written — the alerts-pipeline verification is exactly what demo mode is for.

If either document's section numbers drift, read `documentation/soak-test-runs/README.md` top-to-bottom to find the current checklist boundaries and adapt. Do not invent pre-flight items that aren't in the doc.

### Launch command

Read the most recent `documentation/soak-test-runs/soak-*.md` to learn the current soak-launch convention. **Mimic it exactly** — do not invent a new invocation. If the convention is unclear, stop and ask.

For **demo** flavor, take the most recent soak launch command and remove the `--daemon` flag — everything else stays (env vars, tee, DEBUG_ENDPOINTS, etc.). Do not invent a different backend entry point.

### Stdout buffering + tee from start

```bash
export PYTHONUNBUFFERED=1
```

Auto-disambiguate the run log filename (per feedback memory: if `soak-<today>.md` exists, use `-b`, `-c`, … suffix):

```bash
LOGFILE="documentation/soak-test-runs/improve-$(date +%F).md"
i=0; while [ -e "$LOGFILE" ]; do i=$((i+1)); suf=$(printf "\\x$(printf '%x' $((96+i)))"); LOGFILE="documentation/soak-test-runs/improve-$(date +%F)-$suf.md"; done
```

All subsequent soak launches use `2>&1 | tee -a "$LOGFILE"`.

---

## Phase 3: Launch

### Wall-clock budget leak check (mandatory, all flavors)

Before doing anything else in Phase 3, check how long the **interactive** portion of the run has been going. Record `SKILL_START` as the timestamp when Phase 0 first ran (clock-wall time when the user invoked `/improve-bot`). At Phase 3 entry, compute:

```
INTERACTIVE_ELAPSED = now - SKILL_START
```

If `INTERACTIVE_ELAPSED > 30 min`, the Phase 1 + Phase 2 portion has eaten significant wall clock while the daemon was likely running autonomously (backend launched in §3.2). **This is the "wall-clock leak" failure mode from the 2026-04-11 improve-bot run** (run log `documentation/soak-test-runs/improve-2026-04-11.md`, skill-feedback memory `feedback_improve-bot_wall_clock_leak.md`).

When this fires, STOP and ask the user one explicit question with three options:

1. **(a) Reset** — kill the daemon (POST `/api/training/stop`), archive any unsupervised work as a "bonus observation" in the run log, reset the wall-clock budget, and enter Phase 4 fresh.
2. **(b) Absorb** — keep the unsupervised work, reduce the remaining wall-clock budget by `INTERACTIVE_ELAPSED`, and enter Phase 4 with what's left. Only choose this if the daemon's work so far is useful to the current run's goal.
3. **(c) Stop** — the unsupervised work has already produced its primary finding and Phase 4 iteration isn't worth starting. Go directly to Phase 5, report on what the daemon did, and close the run.

Do not silently let the interactive overrun pass through to Phase 4. The 2026-04-11 run (#88) lost its entire 2h Phase 4 budget this way — daemon ran for 4h 44m unsupervised, zero Phase 4 attempts executed.

### Set `PHASE4_T0` (the real wall-clock anchor)

```bash
PHASE4_T0=$(date +%s)
RUN_TS=$(date -d @$PHASE4_T0 +%Y%m%d-%H%M)
```

**All wall-clock stop-condition checks during Phase 4 measure elapsed time from `PHASE4_T0`, NOT from the baseline tag timestamp and NOT from `SKILL_START`.** This is the fix for the leak. The interactive Phase 1/2 portion does not count against the budget. The daemon's unsupervised work during Phase 1/2 is either archived (option a), absorbed (option b), or reported (option c) — it never invisibly consumes the budget.

### Baseline tag (training / dev / hybrid; skipped for demo)

```bash
git tag "improve-bot/run/$RUN_TS/baseline"
git push origin "improve-bot/run/$RUN_TS/baseline"
```

For **demo** flavor, do NOT create or push any tag — demo is a no-git-writes contract. Still set `RUN_TS` as a local var for logging.

Write a run-start header to `$LOGFILE`:
- Suggestion text
- Flavor, measures, stop conditions, data state, `--demo` / `--self-improve-code` flags on/off
- **`SKILL_START`** (when `/improve-bot` was invoked) and **`PHASE4_T0`** (when Phase 4 iteration begins) — both timestamps, so the morning report can show the interactive portion explicitly
- `INTERACTIVE_ELAPSED` at Phase 3 entry, and which option (a/b/c) was chosen if it triggered the leak check
- Baseline tag + SHA (or "n/a (demo)" for demo)
- Baseline metric values (read from dashboard state at start; for demo, these are observation baselines only)

### Flavor: demo

- Launch the backend **without** `--daemon` (e.g. `DEBUG_ENDPOINTS=1 PYTHONUNBUFFERED=1 uv run python -m bots.current.runner --serve 2>&1 | tee -a "$LOGFILE"`). Start frontend as normal.
- Run §3.5 synthetic alert pre-flight — this is the primary value of demo mode.
- Observe the dashboard for the wall-clock window. Log anything notable (alert-pipeline behavior, UI regressions, dashboard drift).
- At stop: diff `data/` against the `data.demo-snapshot-$TS` taken in Phase 2. Any diff is a demo-contract violation — flag loudly in the writeup.
- Move to Phase 5.

### Flavor: training

- Launch the soak run in the background with tee (exact invocation from most recent soak log, **with** `--daemon`).
- Use the Monitor tool on `$LOGFILE` to watch stdout lines without polling.
- Trip on any configured stop condition; on trip, capture final metrics, stop the run, move to Phase 5.

### Flavor: dev

- Synthesize a plan doc at `documentation/improvements/self-improve/$RUN_TS-<slug>.md`.
- `/repo-sync --plan <path>` to create issues.
- `/build-phase --plan <path>`.
- Commits/pushes follow normal build-phase behavior.
- Then proceed to Phase 5.

### Flavor: hybrid

- If `--self-improve-code` is NOT set: degrade to training-only and capture findings to the writeup. Do not touch code.
- If `--self-improve-code` IS set: enter Phase 4.

---

## Phase 4: Self-improve-code loop (hybrid + `--self-improve-code` only)

Inner loop. Alternates a short training window with a reactive dev attempt. Repeats until **wall-clock budget (measured from `PHASE4_T0`, NOT from `SKILL_START` or the baseline tag)** is exhausted OR 3 consecutive failed attempts OR a watchdog trip during a validation soak.

At every iteration boundary, recompute `now - PHASE4_T0` and compare against the configured budget. If it exceeds the budget, stop the loop cleanly and go to Phase 5 regardless of how much work is in flight.

### One attempt

1. **Training window** — run a soak slice (default 30 min) with tee, monitor for findings. On stop condition, read the run log, pick the top actionable finding, synthesize a slug.

2. **Pre-tag + branch**:
   ```bash
   git tag "improve-bot/run/$RUN_TS/$SLUG/pre"
   git push origin "improve-bot/run/$RUN_TS/$SLUG/pre"
   git checkout -b "improve-bot/run/$RUN_TS/$SLUG"
   ```

3. **Open a GitHub issue** (the issue IS the plan — no plan doc needed):
   - Title: `[self-improve] $SLUG`
   - Body: finding from training window + proposed fix + link to baseline tag
   - Label: `self-improve`
   - Capture issue number as `ISSUE`.

4. **Hand off to `/build-step`** on the attempt branch:
   ```
   /build-step --problem "<issue body verbatim>" --issue $ISSUE --reviewers code
   ```

5. **Quality gates** (run at skill level after build-step returns, so the skill has authoritative pass/fail):
   ```bash
   uv run pytest
   uv run mypy src
   uv run ruff check .
   ```

6. **On pass**:
   ```bash
   git checkout master
   git merge --no-ff "improve-bot/run/$RUN_TS/$SLUG" -m "self-improve($SLUG): $SUMMARY (#$ISSUE)"
   git tag "improve-bot/run/$RUN_TS/$SLUG/merged"
   git push origin master
   git push origin --tags
   gh issue comment $ISSUE --body "Merged at $(git rev-parse HEAD)"
   ```
   Then run a **short validation soak** (default 15 min):
   - If validation error count climbs above baseline + 1 → **automatic rollback** (see below), comment on the issue with the regression, move to next attempt.
   - Else → `gh issue close $ISSUE`, move to next attempt with the new merged tag as the new baseline.

7. **On fail** (any gate failed):
   - Master was never touched — no reset needed.
   - `git checkout master`
   - Attempt branch stays on origin for forensics.
   - `gh issue comment $ISSUE --body "Failed: <failure signature>"` then `gh issue close $ISSUE --reason not-planned`
   - Increment consecutive-fail counter. Move to next attempt.

### Automatic rollback

Triggered by: post-merge validation soak regression OR post-merge quality gate failure (shouldn't happen since we gate before merging, but belt-and-suspenders).

```bash
PREV=$(git tag -l "improve-bot/run/$RUN_TS/*/merged" | tail -n 2 | head -n 1)
# If no prior merged tag, use baseline
PREV=${PREV:-"improve-bot/run/$RUN_TS/baseline"}
git reset --hard "$PREV"
git push origin master --force-with-lease   # ONLY allowed for automatic rollback on self-improve runs, ONLY with --force-with-lease, ONLY to a tag the skill itself created
```

`--force-with-lease` is explicitly allowed here (and ONLY here) because the rollback target is a tag the skill itself just created and nobody else can have concurrently pushed. If `--force-with-lease` refuses because someone else pushed, STOP and leave the regression in place for human review — do not escalate to `--force`.

### Loop termination

- Wall-clock budget exhausted → final tag, Phase 5
- 3 consecutive failed attempts → stop, Phase 5
- Any watchdog trip during a validation soak → stop, Phase 5
- Rollback couldn't execute safely (force-with-lease refused) → stop, Phase 5, flag loudly in report

### Final tag

```bash
git tag "improve-bot/run/$RUN_TS/final"
git push origin "improve-bot/run/$RUN_TS/final"
```

---

## Phase 5: Morning report

Append to `$LOGFILE`:

- Run duration + stop reason
- Baseline → final metric deltas (all measures from Phase 1)
- Attempt table: slug, issue #, branch, result (merged / failed / rolled-back), SHA
- Rollback log if any fired
- Links to every GitHub issue opened during the run
- Suggested next action ("soak-N with same config", "fix issue #X and retry", "metric Y regressed, investigate")
- **Tag cheatsheet** (for the human's morning review):
  ```bash
  git tag -l "improve-bot/run/$RUN_TS/*"                         # list everything
  git log "improve-bot/run/$RUN_TS/baseline..master" --oneline   # see what landed
  git reset --hard "improve-bot/run/$RUN_TS/baseline"            # NUKE whole run
  git reset --hard "improve-bot/run/$RUN_TS/<slug>/merged"       # keep up to attempt <slug>
  ```

Post a short summary comment to an umbrella GitHub issue (create one at run start, labeled `self-improve-run`) so the report is phone-readable.

---

## Safety rails (always, all flavors)

- Never `git push --force` except the `--force-with-lease` automatic rollback path defined in Phase 4, which only targets a skill-created tag on `$RUN_TS`.
- Never rebase published history.
- Never bypass hooks (`--no-verify`).
- Never `git reset --hard` to a remote ref — only to a local tag the skill itself created during this run.
- Never delete `baseline`, `merged`, or `final` tags — they are the user's audit trail.
- All autonomous dev work lives under `improve-bot/run/$RUN_TS/` so `git tag -l 'improve-bot/*'` tells the whole story.
- `--self-improve-code` is required for ANY autonomous **source-code** change (Phase 4 hybrid loop). Without it, the skill will not merge or push code on its own. Learning self-improvement (daemon-driven weight/hyperparam updates) happens under plain `training` flavor without any flag.
- `--demo` is a strict no-mutation contract. Demo runs must not write to `data/`, must not create git tags/branches/commits, must not touch GitHub. If the skill cannot verify this (e.g. cannot snapshot `data/`), abort before launching.
- If the skill hits any situation it can't resolve autonomously (missing soak convention, ambiguous metric, unknown pre-flight item, force-with-lease refusal), STOP and leave state as-is for human review. Do not guess.
- `/improve-bot` is not a substitute for `/build-phase --plan <path>` when the user already has a plan doc. Prefer the plan-based path when one exists.
