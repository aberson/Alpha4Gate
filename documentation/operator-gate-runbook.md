# Operator-gate runbook — the eight pending gates

**Written 2026-09-02.** Every claim here was verified against source or `gh` on that date;
file:line anchors are given so you can re-check rather than trust.

Alpha4Gate has no automated work outstanding on any recently active phase. Phases 7, EL, EJ
and EV are all code-complete and quality-clean. What remains is **eight operator gates across
five phases** (#288, #294, #295, #279, #289, #280, #170, #171), and they share **one machine
and one bot-version pointer**, so they cannot be
run in parallel and have never had a published order. This is that order.

---

## Read this first — three defects that silently invalidate results

These are not warnings about what might go wrong. All three are confirmed at source, and each
one turns a gate into a run that looks successful and proves nothing.

### 1. Every soak stops after ONE generation unless you pass `--generations 0`

`scripts/evolve.py` declares `--generations` with **`default=1`** (`scripts/evolve.py:215-224`;
help text: "0 disables the generation cap (use `--hours` for soaks)"). The stop check is at
`scripts/evolve.py:4229-4235`, setting `stop_reason = "generations-reached"`.

**Not one gate command in any plan file passes it.** Not EV.4, not EV.5's launcher, not EJ.7,
not EL.7. Neither does `scripts/launch-evolve.ps1:72`, which is the script behind the
dev-observatory `run-evolution` button:

```
$evolveCmd = "Set-Location '$root'; uv run --extra viewer python scripts/evolve.py --hours $Hours --viewer"
```

The launcher's own header comment at line 27-29 claims the run "keeps going headless to its
`--hours` budget". That is false as written. The wiki is correct and does pass
`--generations 0` in every soak example (`documentation/wiki/operator-commands.md:61-63`, `:294`).

There is live proof on disk: `data/evolve_run_state.json` holds
`"cli_argv": ["--hours","4","--viewer"]` next to `"generations_target": 1`.

| Gate | What one-generation truncation does to it |
|---|---|
| EL.7 | Fatal, though defect 3 is the bigger problem there. |
| EJ.8 | Fatal. Its A/B table has a "generations completed" row that would always read 1. |
| EV.5 | Fatal. "The 4-hour run completes" becomes a ~20-minute run. |
| EV.4 / M1 | Survivable. M1 only needs a visible match, which happens inside generation 1. |

**Pre-flight decision, and it is yours to make.** Two options:

- **(A) Append `--generations 0` to every soak command** and accept that EV.4 item (e) is then
  no longer exercising the button exactly as the registry invokes it. The plan makes running
  the launcher *bare* its acceptance condition on purpose
  (`documentation/plans/evolve-viewer-plan.md:657`, decision D16), so overriding it voids that
  specific claim while still proving the viewer works.
- **(B) Fix `scripts/launch-evolve.ps1:72`** to pass `--generations 0`, and correct its line-28
  comment in the same change. This is a one-line edit but a real operator-facing behaviour
  change: a 4-hour run becomes four actual hours of SC2 that can promote versions and
  auto-commit `[evo-auto]`. It also makes EV.4 item (e) and EV.5 both valid without an override.

Option B is the better one if you intend to run EV.5 at all, because EV.5 cannot be satisfied
any other way while still using the production launcher. Decide before the first gate, not
between gates, because a mid-sequence launcher change makes the earlier runs
non-comparable to the later ones.

### 2. The frozen-baseline registry does not exist

`data/baselines.json` is **absent** from the working tree. So are `data/lineages.json`,
`data/fingerprints.json` and `data/evolve_crashes.jsonl`; `data/evolve_results.jsonl` exists
but is 0 bytes.

Consequences, all confirmed in source:

- `--fitness-mode baseline` and `--fitness-mode both` degrade to `parent` when the registry is
  empty or absent. Not silently — there is a startup WARNING (`scripts/evolve.py:3920-3930`,
  emitting *"--fitness-mode=%s but no baselines registered at %s; behaving like 'parent' (no
  gauntlet will run)"*). But no error and no failed run: it just quietly proceeds without a
  gauntlet, which is easy to miss in a long log.
- `--panel-floor` is therefore **inert**, with its own startup WARNING
  (`scripts/evolve.py:3938-3950`; the flag's own help text at `:461-463` says the floor "needs the
  gauntlet to have teeth"). EJ.7 exists to test the panel floor. Without anchors, EJ.7 tests
  nothing.
- The diversity fingerprint **is** the per-baseline win-rate vector
  (`src/orchestrator/fingerprint.py:12-13`), so EL.7's "diversity matrix shows non-trivial
  separation" is empty without anchors too.

**Fix, once, before the first gate that needs it** (usage verified at `scripts/baseline.py:7-9`):

```powershell
cd c:\Users\abero\dev\Alpha4Gate
uv run python scripts/baseline.py add v10 v10 --note "frozen anchor"
uv run python scripts/baseline.py add v13 v13 --note "frozen anchor"
uv run python scripts/baseline.py list
```

EJ.7's plan text names this precondition. EL.7's does not, even though `--fitness-mode both` is
in its command line. Do it once and both gates benefit.

### 3. EL.7 is unrunnable as written, and raising `--lineages` does not fix it

Two independent blockers stack here. The second is the one that matters.

**Blocker A — the cap arithmetic.** EL.7's command is `--lineages 3 --population-cap 3`. The
population manager returns keep-all whenever `len(lineages) <= cap`
(`src/orchestrator/population.py:242-243`, docstring at `:24`: "Under cap → keep all"). Three
lineages under a cap of three culls nothing, ever.

**Blocker B — `--lineages N` does not create N lineages.** This is the real problem, and the
obvious fix for blocker A does not survive it. The flag only *engages* multi-lineage scheduling;
the lineage set comes entirely from `data/lineages.json`, which is **absent**. With no registry,
`_load_lineage_registry_if_engaged` (`scripts/evolve.py:3697`, returning at `:3744-3751`) hands
back a single implicit `main` lineage headed at the current version, for **any** value of
`--lineages`:

```python
    # --lineages > 1 but no (or malformed) registry -> implicit single
    # ``main`` lineage.
    head = current_version()
    return {DEFAULT_LINEAGE_ID: Lineage(lineage_id=DEFAULT_LINEAGE_ID, head_version=head)}
```

So `--lineages 4 --population-cap 3` still yields `len(lineages) == 1 <= cap == 3`, still hits
keep-all, and still cannot cull. And "at least two generations per lineage" across three lineages
is unreachable because there is only ever one lineage.

**There is no runtime path that adds a lineage.** The population manager's `repopulate` list is
always empty and `scripts/evolve.py` never reads it; lineage heads are only ever *updated* for an
id that already exists. **And no CLI writes the registry** — `write_lineages`
(`src/orchestrator/lineages.py:215`) has zero production callers. Note that `data/lineage.json`
(**singular**, 4.6 KB, the version DAG written by `scripts/build_lineage.py`) does exist and is a
completely different file. Do not confuse them.

**So EL.7 has exactly two honest paths. Pick one before running it.**

- **(i) Hand-author the registry.** Write `data/lineages.json` as a JSON object keyed by lineage
  id, then run with `--lineages 4 --population-cap 3`. `lineage_id` and `head_version` are the
  only required fields (`src/orchestrator/lineages.py:115-141`); the rest default. Heads must name
  versions that exist on disk:

  ```json
  {
    "main":   {"lineage_id": "main",   "head_version": "v13"},
    "line-2": {"lineage_id": "line-2", "head_version": "v12"},
    "line-3": {"lineage_id": "line-3", "head_version": "v11"},
    "line-4": {"lineage_id": "line-4", "head_version": "v10"}
  }
  ```

  Be aware this is a **one-way default change**: once a non-empty registry exists on disk,
  multi-lineage scheduling is engaged for every subsequent bare invocation, with no flag to
  disengage it. Move the file aside when you are done.

- **(ii) Take the plan's escape hatch.** Extinction may be "documented as not-triggered-by-design
  under the cap" (`documentation/plans/evolution-lines-plan.md:305`), so run EL.7 single-lineage,
  record that extinction did not fire and why, and accept that Step EL.4's feature stays
  unexercised. Cheaper and honest, but it does not test what EL.4 built.

Even on path (i), two further gates sit on the cull path: a lineage is only cull-eligible if it is
*dominated* (strictly less fit **and** fingerprint distance below `--diversity-threshold`, default
0.15), and a lineage with no fitness or fingerprint yet is never culled. Those are populated only
inside the post-promotion gauntlet, so at least two lineages must each promote and be gauntleted
before extinction can fire at all — which loops back to defect 2.

---

## Standing constraints for every gate

**One evolve run at a time, machine-wide.** Two runs collide on `data/evolve_*.json`, both flip
`bots/current/current.txt`, and both `git add` + commit `[evo-auto]` to whatever branch is
checked out. Worse: a second run starting mid-soak calls `_clear_fresh_run_state`
(`scripts/evolve.py:1888-1899`), which **truncates the first run's `evolve_results.jsonl`**.

This is enforced by **convention, not code**. There is no lock file, no pid file, and
`scripts/evolve.py` never reads `data/evolve_run_state.json` — every reference to it is a write.
The only mechanical guard is a Windows CIM process probe inside `scripts/launch-evolve.ps1:54-75`,
and it is launcher-scoped, Windows-only, blind to a WSL run, and explicitly fails open. A direct
`python scripts/evolve.py` bypasses it entirely.

**The branch matters.** You are on `master-plan/phase-ev`. EV.4 item (e) and EV.5 run the
launcher un-flagged, so they can promote a version and auto-commit `[evo-auto]` to that branch.
That is expected, not a defect. It does mean promotions accumulate on a branch that is not yet
on `master` — see the branch landing note.

**The state file currently lies.** `data/evolve_run_state.json` says `"status": "running"` from a
launcher-shaped run started **2026-08-28** that never completed a generation. No evolve or SC2
process is alive. The dashboard's Evolution tab will show this stale run. It will not block a new
launch (the guard is process-based). Note it and move on.

### Pre-flight, run before every single gate

```powershell
cd c:\Users\abero\dev\Alpha4Gate
git branch --show-current
git diff --staged --stat
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, StartTime
Get-Process SC2_x64 -ErrorAction SilentlyContinue | Select-Object Id, StartTime
```

| Check | Expected |
|---|---|
| Branch | `master-plan/phase-ev` |
| Staged diff | **Empty.** `EVO_AUTO=1` commits sweep the entire git index, not just `bots/<v>/` (`.claude/rules/evolve.md` § Pre-launch hygiene). Anything staged rides into the next `[evo-auto]` commit. |
| python processes | No evolve process alive |
| SC2_x64 processes | None. Never kill SC2 by hand if you find one — see `.claude/rules/bot-runtime.md`. |
| Stray candidate dirs | `bots/cand_d18ed8ce/` is **already on disk** (untracked, gitignored) and `list_versions()` already reports it. Any gate check phrased as "no `bots/cand_*` left behind" has a pre-existing false positive — note the baseline before the run, or clear it first. |

### Reading the result of any evolve gate

```powershell
Get-Content data/evolve_run_state.json | ConvertFrom-Json | Select generations_promoted, parent_current, status, stop_reason
Get-Content data/evolve_results.jsonl -Tail 1 | ConvertFrom-Json | Select phase, outcome
```

Only treat a `bots/vN/` directory as a real promotion if `generations_promoted` increased **and**
the last result row shows a terminal-success outcome. An untracked `bots/vN/` is just as likely
to be scratch from a rolled-back attempt.

---

## The order

Cheapest-first, with dependencies respected. The table's own wall-clock column sums to roughly
**28-30 hours**, and **none of it can overlap**. Gates 1-5 alone are ~16.5 h; Phase 7's 8-hour
budget and the two Phase D gates are the rest.

| # | Gate | Issue | Wall clock | Needs | Hard dependency |
|---|---|---|---|---|---|
| 0 | Register frozen baselines | — | 2 min | nothing | — |
| 1 | EJ.7 flags-on smoke | #288 | ~45 min + 15 min control | SC2 + evolve | baselines registered |
| 2 | EV.4 / Manual UAT **M1** | #294 | ~1.5 h | SC2 + evolve + dashboard | — |
| 3 | EV.5 observation soak | #295 | 4 h | SC2 + evolve + dashboard | M1 passes |
| 4 | EL.7 multi-lineage soak | #279 | 6 h | SC2 + evolve | baselines registered **and** the defect-3 path decided |
| 5 | EJ.8 overnight A/B soak | #289 | 4 h | SC2 + evolve | EJ.7 green |
| 6 | Phase 7 Step 6 validation | #280 | 8 h budget | SC2 + a Claude Code session | — |
| 7 | Phase D **M1** | #170 | 2–4 h | SC2 + daemon | evolve daemon stopped |
| 8 | Phase D **M2** | #171 | 20 games | SC2 only | M1 passes |

Why this order: EJ.7 is the cheapest real gate **and** it is the step whose precondition
populates `data/baselines.json`, which EL.7 also needs. M1 comes next because it is Phase EV's
acceptance condition and everything else about Phase EV is blocked behind it. The two long soaks
follow. Phase D sits last because it requires the evolve daemon **stopped** — Phase D edits
`reward_rules.json`, which `[evo-auto]` commits also patch (`documentation/plans/phase-d-build-plan.md:19-31`)
— so it must not interleave with any evolve gate.

---

## Gate 0 — Register frozen baselines (2 minutes)

Run the commands in defect 2 above. Confirm `scripts/baseline.py list` shows two anchors before
proceeding. Skipping this makes gates 1 and 4 measure nothing.

---

## Gate 1 — EJ.7 flags-on smoke (#288)

Plan: `documentation/plans/evolve-judging-plan.md:294-301`.

**Flags-on run** (~45 min). Verbatim from the plan, with `--generations 0` appended per the
pre-flight decision:

```powershell
uv run python scripts/evolve.py --pool-size 3 --games-per-eval 3 --hours 0.75 --generations 0 --priors-exclude-promoted --screen-null-diff --regression-rule one-sided --panel-floor --refresh-dedup --budget-fit --fitness-mode both
```

**Defaults-off control** (15 min). Run this second, separately, and compare:

```powershell
uv run python scripts/evolve.py --pool-size 3 --games-per-eval 3 --hours 0.25 --generations 0
```

| Check | Expected |
|---|---|
| Startup WARNING about the panel floor | **Absent.** If it appears, your baselines did not register and this run proves nothing. Stop and fix gate 0. |
| `--screen-null-diff` catches | At least one candidate evicted before games, or a logged zero-catch with a reason |
| `--regression-rule one-sided` | Rollback decisions cite P(worse) and a decided-game count, not a raw majority |
| Orphan candidate dirs | No `bots/cand_*` **beyond the pre-existing `cand_d18ed8ce`** left behind |
| Pointer integrity | `bots/current/current.txt` names a real version that exists on disk |
| Control run | Row shapes in `evolve_results.jsonl` identical to the flags-on run apart from the gate fields |

---

## Gate 2 — EV.4 / Manual UAT M1 (#294)

**The plan already carries a complete, ordered, copy-paste checklist.** Do not re-derive it from
this runbook. Open `documentation/plans/evolve-viewer-plan.md` § `## Manual UAT` (line 859) and
work the M1 block at line 863 top to bottom. It is ordered cheapest-first: two negative cases
that exit in seconds, then a 30-minute flagged run, then a defaults-off control, then item (e).

Three things this runbook adds to it:

- **Item (e1) is the acceptance condition for the whole phase.** A real SC2 match visibly
  rendered inside the themed container, launched from the dev-observatory button. Not "the window
  opened". Wait for it — pool generation runs a Claude prompt before the first game, so first
  paint can be several minutes out.
- **Item (e) is where the `--generations` decision bites.** The plan requires the launcher run
  **bare**. Under option A you leave it bare and accept one generation, which is fine for M1.
  Under option B you have already fixed the launcher and it just works.
- **Stop the run by closing the evolve CONSOLE window.** Closing the container only detaches.
  The dashboard Stop button is not wired. **Never Ctrl+C a `--viewer` run** — the loop runs off
  the main thread, so burnysc2's SIGINT kill-switch is never armed and Ctrl+C can orphan SC2
  processes.

Record per-item verdicts under `documentation/soak-test-runs/`. If (e1) does not happen, Phase EV
has not met its definition of done regardless of how many unit tests pass, and gate 3 is blocked.

---

## Gate 3 — EV.5 observation soak (#295)

Plan: `documentation/plans/evolve-viewer-plan.md:664-671`. Type `wait` — `/build-phase` will not
resume for it; mark it done in the plan by hand.

**This gate is invalid under option A.** Its done-when is "the 4-hour run completes", and the
plan's command `.\scripts\launch-evolve.ps1 -Hours 4` stops after one generation. Either take
option B and fix the launcher, or run the equivalent directly and note in the record that the
production launcher was bypassed:

```powershell
uv run --extra viewer python scripts/evolve.py --generations 0 --hours 4 --viewer
```

Watch generation 1 attended, then close the container and let it run detached.

| Check | Expected |
|---|---|
| Run survives container close | `evolve_results.jsonl` gains rows after the window closed |
| Generations completed | More than 1. If it reads 1, the `--generations` defect bit you. |
| Crash rows | `data/evolve_crashes.jsonl` empty |
| Memory | No unbounded growth in the evolve process over 4 h |
| HWND-attach stalls | No repeated 15-second stalls per game (`src/selfplay_viewer/container.py:94`) |
| Orphan SC2 | None after the run ends |

**Do not claim a throughput verdict from this run.** Comparing against "the most recent pre-EV
soak" confounds viewer overhead with a different bot version, a different pool and different
machine load. To claim throughput you need the paired control the plan specifies: 2 hours with
the viewer, then 2 hours headless resumed from the same state in the same session. Absent that,
record generations and promotions as **context, explicitly not a verdict**. This gate claims
**survival only**.

---

## Gate 4 — EL.7 multi-lineage soak (#279)

Plan: `documentation/plans/evolution-lines-plan.md:289-308`. Longest single gate, and the one
that needs a decision before you start — **re-read defect 3.** Raising `--lineages` alone changes
nothing; without a hand-authored `data/lineages.json` the run has exactly one lineage regardless.

**Path (i) — real multi-lineage run.** Author `data/lineages.json` first (shape in defect 3), then:

```powershell
uv run python scripts/evolve.py --lineages 4 --population-cap 3 --hours 6 --generations 0 --fitness-mode both
```

**Path (ii) — escape hatch.** Run single-lineage and record extinction as not-triggered-by-design:

```powershell
uv run python scripts/evolve.py --hours 6 --generations 0 --fitness-mode both
```

Check in every ~2 hours by reading `data/evolve_run_state.json`, the recent commits, and the log
tail. Do not attach a debugger or a second client.

| Check | Expected on path (i) | Expected on path (ii) |
|---|---|---|
| Lineages scheduled | 4, from the registry you authored | 1 (`main`) — confirm this in the log before drawing any multi-lineage conclusion |
| Generations per lineage | ≥ 2 for each of the 4 | ≥ 2 for `main` |
| Diversity matrix | Non-trivial separation. Flat or empty means baselines are missing (defect 2) — or that you are reading the disk-backed API, see below | n/a |
| Extinction | Possible once ≥ 2 lineages have each promoted and been gauntleted. Still not guaranteed — the cull also needs dominance plus fingerprint distance under 0.15 | Cannot fire. Record why. |
| Orphan processes | Zero | Zero |
| `[evo-auto]` commits | Clean rows, nothing swept in from a dirty index | Same |

**Known gap that shapes how you observe this one.** `write_lineages` has zero production callers
(`src/orchestrator/lineages.py:215`), so lineage heads and extinction events live only in process
memory and `data/lineages.json` is **never written back**. A run interrupted and resumed
re-branches from stale heads, and extinct lineages revive.

`data/fingerprints.json`, by contrast, **is** written — every gauntleted promotion saves one
whenever `--population-cap > 0` (`scripts/evolve.py:4762`). Seeing that file appear is
expected, not contamination.

Do not lean on the dashboard for this gate. `/api/evolve/lineages` is **disk-backed**, not a live
in-memory view (`bots/v13/api.py:1641` reads the two registries plus `evolve_results.jsonl`), so
with `lineages.json` never written the lineages array and the whole diversity matrix come back
empty no matter when you call it. Observe from the run's own log and `evolve_results.jsonl` rows
instead.

---

## Gate 5 — EJ.8 overnight A/B soak (#289)

Plan: `documentation/plans/evolve-judging-plan.md:302-308`. Its spec is prose only — "the EJ.7
flags minus the reduced sizes" — so no verbatim command exists in the plan or the issue.

**Reconstructed command** (flag names verified; the composition is this runbook's, not the
plan's). "Reduced sizes" means EJ.7's `--pool-size 3 --games-per-eval 3`, so dropping them
restores the defaults of 10 and 5:

```powershell
uv run python scripts/evolve.py --hours 4 --generations 0 --priors-exclude-promoted --screen-null-diff --regression-rule one-sided --panel-floor --refresh-dedup --budget-fit --fitness-mode both
```

Compare against the most recent flags-off soaks on: regression-rollback rate, null-diff and
dedup catch counts, games per generation, generations completed, and promotions.

A negative result is a **valid outcome**. This gate is not gated on improvement; it decides
whether the six EJ flags should become defaults, and "no" is an answer. Record it either way.

---

## Gate 6 — Phase 7 Step 6 end-to-end validation (#280)

Plan: `documentation/plans/phase-7-build-plan.md:222-256`.

First edit a reward rule so the policy is provably stale (the edit must be newer than the newest
checkpoint), then confirm the staleness signal sees it:

```powershell
uv run python -m orchestrator.staleness v13
```

`is_stale` must be true. The CLI is real and verified (`src/orchestrator/staleness.py:344-386`);
`v13` is the current pointer.

**The plan's second command does not exist.** It reads `uv run improve-bot-advised --self-improve-code --hours 8`,
but `pyproject.toml` has no `[project.scripts]` table, so there is no such console script. It is
a **Claude Code skill**, not a shell command. In a Claude Code session at
`c:\Users\abero\dev\Alpha4Gate`, type:

```
/improve-bot-advised --self-improve-code --hours 8
```

The soak improvement is wall-clock-clamped to at most half the budget, so expect 2–4 hours of
actual soak inside the 8-hour window.

---

## Gates 7 and 8 — Phase D M1 (#170) and M2 (#171)

Plan: `documentation/plans/phase-d-build-plan.md` § 3.3 (line 411); M1 at `:415-463`, M2 at
`:465-498`.

**Stop the evolve daemon first.** Phase D edits `reward_rules.json`, which `[evo-auto]` commits
also patch. These two gates must not interleave with any evolve run.

M1 trains 3 cycles with the daemon and measures whether early-game reward variance drops:

```powershell
uv run python -m bots.current.runner --serve --daemon --decision-mode hybrid
```

All three flags are real and the module runs. One caution: the runner's own help for
`--model-path` says it is "required for neural/hybrid mode", and this command (copied from
`phase-d-build-plan.md:429`) omits it. The code treats it as optional, so it may resolve a default
checkpoint — but if the daemon logs a missing-model error, add `--model-path` pointing at the
current version's checkpoint rather than assuming the run is valid.

Measure the first-five-minutes reward standard deviation **before** flipping
`use_build_order_reward` (currently `false` in `bots/v13/data/hyperparams.json`) to get a
baseline, then again after. The plan carries the exact measurement snippet at `:434-446`. Pass
is a ≥30% drop. Then:

```powershell
uv run python scripts/evaluate_model.py --difficulty 3 --games 20
```

M2 snapshots to a new version and runs a head-to-head ladder comparison.

> **Do not copy M2's commands from the Phase D plan.** `phase-d-build-plan.md:476` reads
> `scripts/snapshot_bot.py --from current` and `:481` reads
> `Set-Content bots/current/current.txt 'v14'`. Both are wrong, and together they can point
> production at an empty directory.
>
> `--from` takes a **version**, not the `current` alias. `bots/current/` is the four-file
> MetaPathFinder pointer shim — no `bot.py`, no `data/`. `get_version_dir` does not validate, so
> the copy proceeds, writes `bots/v14/VERSION`, and only then fails looking for a manifest that
> the shim does not have. The command exits non-zero **and leaves a poisoned `bots/v14/` on
> disk** that `list_versions()` now reports as real. The `Set-Content` line then aims
> `bots/current/current.txt` — which every runner, daemon and evolve snapshot resolves through
> — at that empty shell.
>
> Use the bare form. `source_version` defaults to the current version and the target
> auto-increments, and the pointer flip is already part of the snapshot, which makes the
> `Set-Content` line redundant as well as dangerous.

```powershell
uv run python scripts/snapshot_bot.py
```

Use the version name the script prints in the next command — it may not be `v14` if a stray
candidate directory has already claimed that number (see the pre-flight note below).

```powershell
uv run python scripts/ladder.py compare v14 v13 --games 20
```

Pass is ≥ +10 Elo over 20 games with win rate holding at difficulty 3.

> **Do not copy the Ladder CLI examples from the wiki.**
> `documentation/wiki/operator-commands.md:500-503` shows `scripts/ladder.py --list` and
> `--eval-only`. **Neither flag exists.** `scripts/ladder.py` takes subcommands:
> `update`, `show`, `compare`, `replay` (`scripts/ladder.py:31-61`). The forms above are correct.

---

## After the gates

Record each gate's verdict under `documentation/soak-test-runs/` and close its issue with the
evidence. Update the sub-plan's step status and the master plan's index row in the same pass, so
the spine does not drift again.

Three items in this runbook are candidates for the evolve operational-hardening plan rather than
one-off workarounds: the launcher's missing `--generations`, the unwired dashboard Stop button,
and lineage state never being persisted.
