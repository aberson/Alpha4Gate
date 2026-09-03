# Branch landing note — `master-plan/phase-ev` → `master`

**Written 2026-09-02.** Git facts below were verified with read-only commands on that date at
`master-plan/phase-ev` = `0ac8caf`. Nothing in this note has been executed; every command is for
the operator to run.

---

## The headline

**The stated blocker no longer applies.** `CLAUDE.md` and the Phase EV plan both say Phase EV is
"not mergeable to `master` until `onbrand-pilot` lands", because `master` still has mainline
pygame (no cp314 wheel) and lacks `launch-a4g.ps1`.

That precondition is already satisfied by containment. `master-plan/phase-ev` **fully contains**
`onbrand-pilot`:

```
git merge-base --is-ancestor onbrand-pilot master-plan/phase-ev   → exit 0
git log --oneline master-plan/phase-ev..onbrand-pilot             → empty
```

So there is no two-step landing. Merging `master-plan/phase-ev` into `master` lands the on-brand
frontend work and Phase EV in a single merge. `onbrand-pilot` needs no separate merge, and its
unpushed local tip is not at risk.

**What is actually undecided is policy, not mechanics:** do you land the branch now, with Phase
EV's acceptance gate (M1, issue #294) still open, or wait for M1 to pass first?

---

## Verified state

| Fact | Value |
|---|---|
| Branch HEAD | `0ac8caf`, equal to `origin/master-plan/phase-ev` — nothing to push |
| `master` HEAD | `6138776`, equal to `origin/master` |
| Merge base | `2c8190f` (EJ.6 checkpoint) |
| Divergence | `master` 1 ahead, branch 18 ahead |
| Content relationship | Branch is a strict **content** superset. `git diff --diff-filter=D master master-plan/phase-ev` is empty — the branch deletes nothing `master` has. |
| Topological relationship | **Not** a superset. `git merge-base --is-ancestor master master-plan/phase-ev` exits 1, so a merge needs a merge commit, not a fast-forward. |
| Conflict prediction | **Zero.** `git merge-tree 2c8190f master master-plan/phase-ev` produces no conflict markers. `README.md` is "changed in both" but resolves — the arena-diagram hunk is textually identical on each side. |
| Delta | 53 files, +5055 / −214 |
| Working tree | Clean except untracked `dev.code-workspace` |
| Stash | One unrelated entry from 2026-04-22 on `master` |

### Why `master` is 1 ahead of a branch that contains everything

`master`'s single extra commit `6138776` (the README arena-diagram change) is **patch-equivalent**
to `afb19d5` on the branch. Both carry the same author, the same date, the same subject and the
same stat block, and decisively the same patch-id:

```
git show 6138776 | git patch-id --stable   → 2e4bd6c678ad96056da7e98a6845b67aeeddff1d
git show afb19d5 | git patch-id --stable   → 2e4bd6c678ad96056da7e98a6845b67aeeddff1d
```

The same work was committed independently onto both lines. This is why `git cherry` reports 17
`+` and one `-`. It costs nothing at merge time — git resolves it — but it does mean the merged
history will contain that change twice by SHA and once by content.

### CI has never run on this branch

Both workflows gate `push` on `master` only (`.github/workflows/linux-tests.yml:3-6`,
`docker-build.yml:8-21`). Neither has ever fired for the 18 commits on this branch. Both
workflow files are byte-identical between `master` and the branch.

Opening a **pull request** triggers both: `linux-tests` has no branch filter on `pull_request`,
and `docker-build`'s paths filter fires because `pyproject.toml` and `uv.lock` are both in the
diff. **This is the strongest argument for landing via a PR rather than a local merge** — it buys
the first CI signal this work has ever had, on Linux and in Docker, before it reaches `master`.

---

## The decision: land now, or wait for M1?

### Land now

- The branch has been the de-facto mainline since 2026-08-10. `master` has stagnated for three
  weeks while doc reconciliation, session wraps and the frontend brand work all landed on the
  branch.
- M1 gates **Phase EV's definition of done**, not the correctness of the other 17 commits. The
  on-brand frontend work, the wiki reconciliation, the seed docs and the test-suite growth are
  unrelated to whether a themed container renders an SC2 match.
- `--viewer` defaults **off** and the headless path is byte-identical, so an unproven viewer on
  `master` cannot regress anything that does not opt in.
- Landing gets CI onto this work for the first time.
- Every day the branch stays unmerged, `[evo-auto]` promotions from the pending soaks accumulate
  on a branch that cannot merge — and gates EV.4(e), EV.5, EJ.7, EJ.8 and EL.7 will all produce
  such commits.

### Wait for M1

- Landing a phase whose own acceptance condition is unmet puts code on `master` that the project
  has explicitly not accepted. If (e1) fails, the fix lands as a follow-up on `master` rather
  than as an amendment on an unmerged branch.
- The plan is unambiguous that (e1) is binding: "If (e1) does not happen, Phase EV has not met
  its definition of done regardless of how many unit tests pass."
- M1 is roughly 1.5 hours of operator time. It is the second gate in the runbook order, so the
  wait is short if the gates are being worked at all.

### Recommendation

**Run M1 first, then land — but only if the gates are actually starting soon.** M1 is short, it
is next in the runbook, and it removes the one real objection. If the gates are not going to be
worked for weeks, land now instead: three weeks of stagnant `master` and CI that has never seen
this code are a larger, more certain cost than shipping an opt-in flag whose default path is
byte-identical.

Do not split the difference by merging only part of the branch. The 18 commits interleave Phase
EV, the on-brand frontend work and doc reconciliation, and unpicking them buys nothing.

---

## Execution

Run in: any PowerShell @ `$env:USERPROFILE\dev\Alpha4Gate` · nothing here is automated

### Step 1 — confirm the state has not moved

```powershell
cd $env:USERPROFILE\dev\Alpha4Gate
git fetch origin
git status --short
git log --oneline -1 master-plan/phase-ev
git log --oneline -1 master
git merge-base --is-ancestor onbrand-pilot master-plan/phase-ev; $LASTEXITCODE
```

| Check | Expected |
|---|---|
| `git status --short` | Only `?? dev.code-workspace`. If an `[evo-auto]` promotion landed since, review it before merging. |
| branch HEAD | `0ac8caf` (or later, if soaks have run) |
| `master` HEAD | `6138776` |
| `$LASTEXITCODE` | `0` — onbrand-pilot still contained |

### Step 2 — push the local `onbrand-pilot` ref (optional, tidiness only)

Local `onbrand-pilot` is 1 commit ahead of `origin/onbrand-pilot` and has no upstream tracking
configured. That commit (`bb2e79c`) is **already durable** off-machine via the pushed phase-ev
branch, so this is housekeeping, not data safety. Skip it if you plan to delete the ref after
the merge.

```powershell
git push origin onbrand-pilot
```

### Step 3 — land it

**Preferred — via a pull request, which buys the first CI run on this work:**

```powershell
gh pr create --base master --head master-plan/phase-ev --title "Phase EV: themed viewer for evolution runs + on-brand frontend" --body-file documentation/branch-landing-phase-ev.md
```

Wait for `linux-tests` and `docker-build` to go green, then merge with a **merge commit** (not
squash — the 18 commits are meaningful checkpoints, and squashing destroys the per-step build
record the plan references by SHA).

**Alternative — local merge, no CI signal until after it is on `master`:**

```powershell
git checkout master
git merge --no-ff master-plan/phase-ev -m "Merge branch 'master-plan/phase-ev' — Phase EV complete (viewer) + on-brand frontend"
git push origin master
```

Either way it is a merge commit; a fast-forward is not available.

### Step 4 — tag

The existing convention is `master-plan/<N>/baseline` and `master-plan/<N>/final`, used for
numeric phases 0–4 only. No letter-phase tag exists under that namespace, and Phase A used a
different one (`alphastar/A/final`). Extending the convention to letter phases:

**On the PR path the merge commit is created on GitHub**, so your local HEAD is still the branch
tip. Fetch the merged `master` first or you will tag the wrong commit:

```powershell
git checkout master
git pull origin master
git log --oneline -1        # must be the merge commit, not 0ac8caf
git tag master-plan/ev/final
git push origin master-plan/ev/final
```

Two notes on the convention, both checked against the actual tag SHAs. `final` points at the
**merge commit** for phases 0, 2 and 4 (and at the branch tip for 1 and 3) — so tagging the merge
commit matches. But the "each `final` is reused as the next `baseline`" pattern holds for
**only one** of the four transitions (`master-plan/0/final` = `master-plan/1/baseline` = `a620f66`);
1→2, 2→3 and 3→4 all point at different commits. Do not assume the reuse.

### Step 5 — clean up and re-anchor the docs

```powershell
git branch -d onbrand-pilot
git push origin --delete onbrand-pilot
```

Then update, in one pass:

- `CLAUDE.md` § Current state — remove the "not mergeable to `master` until `onbrand-pilot`
  lands" clause, which is the sentence this note supersedes.
- `documentation/master_plan.md` — the Phase EV section's **Merge status** subsection.
- `documentation/plans/evolve-viewer-plan.md` § 0 — the P-1/P-2 merge prerequisites.
- `.claude/task-state/current.md` — the Branch line.

Do **not** delete `master-plan/phase-ev` until the pending Phase EV gates are done. M1 and EV.5
run against it, and their `[evo-auto]` commits need somewhere to land if you are still working
the branch.

---

## Verified after first draft

`master` has **no branch protection** — `gh api repos/aberson/Alpha4Gate/branches/master/protection`
returns `404 Branch not protected`. So Step 3's local-merge alternative will not be refused, and
the PR path is genuinely a preference (for the CI signal) rather than a requirement.
