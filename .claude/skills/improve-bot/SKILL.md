---
name: improve-bot
description: Run a long autonomous session to improve the Alpha4Gate bot's performance along a measurable axis. Supports training-only soak runs, plan-based dev work, and (with --self-improve) fully autonomous reactive hybrid loops. Designed for overnight / unattended runs.
user-invocable: true
argument: The improvement suggestion (optional). Examples: "raise win rate vs diff 1 past 50%", "fix orphaned transitions", "run overnight and improve the bot's performance". Append `--self-improve` to allow autonomous code changes during the run.
---

# /improve-bot

Entry point for a long autonomous run that aims to improve the Alpha4Gate bot along some measurable axis. Three flavors:

| Flavor        | What it does                                                        | When to use                                       |
|---------------|---------------------------------------------------------------------|---------------------------------------------------|
| **training**  | Soak run, no code changes, captures metric deltas + findings writeup | You want to observe improvement without code edits |
| **dev**       | Plan doc → `/build-phase` → commits                                 | You already know what to fix                      |
| **hybrid**    | Training reacts to failures, opens issues, spawns `/build-step` work, loops | Overnight unattended self-improvement      |

The `--self-improve` flag gates autonomous code changes. Without the flag, this skill is training + writeup only. With the flag, the hybrid loop is allowed to merge and push to master on its own.

---

## Phase 0: Classify the suggestion

Parse the argument. Separate any flags (`--self-improve`) from the suggestion text. Pick a flavor:

- **training** — suggestion names a metric axis or is absent (e.g. "raise win rate vs diff 1", or empty)
- **dev** — suggestion names a known bug or a concrete code change (e.g. "fix orphaned transitions", references a GH issue)
- **hybrid** — suggestion is open-ended AND `--self-improve` is set (e.g. "run overnight and improve the bot's performance")

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
   - Wall clock — default **4h**
   - Game count — default none
   - Error budget — default **4/5** (matches soak-3 convention)
   - First promotion event — default no
   - Watchdog trip — **always on**

3. **`data/` state** — fresh empty or continue from current? Check the current state and the most recent soak log to propose a default (fresh empty was the soak-4 plan per memory).

4. **Self-improve confirmation** — if flavor is hybrid, confirm `--self-improve` is set and explicitly confirm the user OKs autonomous commit/merge/push/issue-create for the duration of the run.

Print the full run plan (flavor, measures, stops, data state, self-improve on/off) in one message and wait for user OK before entering Phase 2.

---

## Phase 2: Pre-flight (interactive, user at keyboard)

Any failure here stops the run before it goes unattended.

### Git + GitHub

```bash
git status                            # clean, on master
git rev-parse --abbrev-ref HEAD       # == master
git fetch origin && git status -sb    # up to date with origin/master
gh auth status                        # authenticated
gh api user                           # returns 200
```

Dry-run commit (confirms no hook blocks the operations the loop will use):

```bash
TS=$(date +%s)
git checkout -b improve-bot/preflight-$TS
git commit --allow-empty -m "preflight: verify commit works"
git checkout master
git branch -D improve-bot/preflight-$TS
```

Explicitly list the operations the run may perform and have the user approve them as a set:

- `git add`, `commit`, `tag`, `branch`, `checkout`, `merge --no-ff`
- `git push origin <branch>`, `git push origin master`, `git push origin --tags`
- `gh issue create`, `gh issue comment`, `gh issue close`

**Forbidden, ever:** `git push --force`, `reset --hard` to any remote ref, rebase of published history, `--no-verify`, deleting `baseline`/`merged` tags.

### `data/` state

```bash
ls -la data/
```

If fresh was chosen: `mv data data.bak-$TS && mkdir data`. Never `rm -rf data/`.

### Soak-test pre-flight checklists

Canonical pre-flight lives in `documentation/soak-test.md`. Execute both of these in order; stop on any failure:

- **§2 Pre-soak checklist** (§2.1–§2.5): `data/` snapshot-or-reset, daemon config values, disk budget, duration/stop conditions, create the run log.
- **§3 Startup sequence** (§3.1–§3.5): start SC2, start backend with daemon enabled, start frontend, open dashboard, run the **§3.5 synthetic alert pre-flight** (verifies the alerts pipeline end-to-end before the long run begins).

Note that §3.5 specifically is the alerts-pipeline verification — do not conflate it with "the pre-flight" broadly. Both §2 and §3 must pass.

If either document's section numbers drift, read `documentation/soak-test.md` top-to-bottom to find the current checklist boundaries and adapt. Do not invent pre-flight items that aren't in the doc.

### Soak launch command

Read the most recent `documentation/soak-test-runs/soak-*.md` to learn the current soak-launch convention. **Mimic it exactly** — do not invent a new invocation. If the convention is unclear, stop and ask.

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

### Baseline tag (always, for every flavor)

```bash
RUN_TS=$(date +%Y%m%d-%H%M)
git tag "improve-bot/run/$RUN_TS/baseline"
git push origin "improve-bot/run/$RUN_TS/baseline"
```

Write a run-start header to `$LOGFILE`:
- Suggestion text
- Flavor, measures, stop conditions, data state, `--self-improve` on/off
- Baseline tag + SHA
- Baseline metric values (read from dashboard state at start)

### Flavor: training

- Launch the soak run in the background with tee (exact invocation from most recent soak log).
- Use the Monitor tool on `$LOGFILE` to watch stdout lines without polling.
- Trip on any configured stop condition; on trip, capture final metrics, stop the run, move to Phase 5.

### Flavor: dev

- Synthesize a plan doc at `documentation/improvements/self-improve/$RUN_TS-<slug>.md`.
- `/repo-sync --plan <path>` to create issues.
- `/build-phase --plan <path>`.
- Commits/pushes follow normal build-phase behavior.
- Then proceed to Phase 5.

### Flavor: hybrid

- If `--self-improve` is NOT set: degrade to training-only and capture findings to the writeup. Do not touch code.
- If `--self-improve` IS set: enter Phase 4.

---

## Phase 4: Self-improve loop (hybrid + `--self-improve` only)

Inner loop. Alternates a short training window with a reactive dev attempt. Repeats until wall-clock budget exhausted OR 3 consecutive failed attempts OR a watchdog trip during a validation soak.

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
- `--self-improve` is required for ANY autonomous code change. Without it, this skill is strictly training + writeup.
- If the skill hits any situation it can't resolve autonomously (missing soak convention, ambiguous metric, unknown pre-flight item, force-with-lease refusal), STOP and leave state as-is for human review. Do not guess.
- `/improve-bot` is not a substitute for `/build-phase --plan <path>` when the user already has a plan doc. Prefer the plan-based path when one exists.
