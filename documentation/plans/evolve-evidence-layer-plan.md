# Phase EI — Evolve evidence layer

**Track:** 10 (Statistical robustness / evolve substrate). **Status:** Planned 2026-09-02.
**Prerequisites:** Phase 9 (improve-bot-evolve) operational. Phase EJ shipped (this phase arms
one of its flags and mirrors its flag-threading patterns). Phase EL shipped (its gauntlet is the
only producer of the fingerprints referenced here). No other phase gates it.

> Slots into the master plan as **Phase EI** on Track 10, alongside Phases EJ and R. Step IDs are
> `EI.1 … EI.14` (letter.number form, matching Phases B/D/E/G/EL/EJ/EV) so they never collide with
> numeric-track step numbering. The letter `EI` was checked free across `documentation/` and
> `CLAUDE.md` on 2026-09-02.
>
> **Provenance.** This plan is the output of a two-round adversarial design conversation on the
> operator's evolve-restructure thread, and its scope is round 2's adjudicated final scope:
>
> - [`../investigations/evolve-restructure-round2.md`](../investigations/evolve-restructure-round2.md)
>   — **authoritative**; section 3 is the one owner of file:line truth, section 4 is this plan's brief.
> - [`../investigations/evolve-restructure-round1.md`](../investigations/evolve-restructure-round1.md)
>   — superseded on 19 citations and 4 conclusions; read the banner first.
> - [`../../docs/seeds/evolve-restructure-operator-notes.md`](../../docs/seeds/evolve-restructure-operator-notes.md)
>   — the operator's own words, the verified pipeline map, and the open idea-(ii) decision card.
>
> **This plan does not depend on idea (ii).** The operator's truncated second improvement idea is
> still open, and three of its six readings need exactly the evidence this phase builds. Whatever
> the answer, this phase is the right thing to build first.

---

## 1. What This Feature Does

`/improve-bot-evolve` spends roughly two hours of real StarCraft II per generation and keeps almost
nothing from it. Nine fields per game reach `data/selfplay_results.jsonl`. The per-seat console log
is truncated by the next game, and under concurrency it is spliced between workers. The
fitness-winning candidate tree is deleted in a `finally` before anything reads it.
`data/evolve_results.jsonl` is zero bytes on disk because it self-truncates every fresh run. And
the most expensive call in the loop — the improvement proposer — is handed two four-kilobyte tails
of two unrelated games with nothing saying which games they were.

Round 2 established the consequence precisely: **gate 1 selects improvement *texts*, not code.**
Two commits in this repo's own history (`f2eb564` and `ce8545f`) build `bots/v4` from parent `v3`,
hours apart, from the same improvement text, and produce materially different bots. One of them was
a single-improvement stack. So the games that justify a promotion score a candidate that is then
deleted, and a second independent authoring is what ships. Separately, in the shipped `v8`→`v9`
diff a later stacked improvement deleted an earlier one's fitness-tested code; `v9` was not rolled
back and is an ancestor of production `v13`.

**This phase does not fix that.** It buys the instruments that make it decidable, repairs the one
outright correctness defect in the proposer's inputs, and wires the zero-game structural levers
whose value does not depend on an unmeasured base rate. It deliberately settles nothing about
gate-1 bars, judges, or deterministic replay.

Five separate restructure proposals argued past each other in round 1 because there was no
measurement that could decide between them. That is the gap this phase closes.

---

## 2. Existing Context

Enough for a fresh-context model to orient without reading the whole codebase.

**The evolve loop.** `scripts/evolve.py` is the orchestrator (~5,500 lines) and
`src/orchestrator/evolve.py` holds the primitives. One generation: three parent-vs-parent mirror
games, then a one-shot `claude -p` proposer call that returns up to `--pool-size` (default 10)
ranked improvements, then per-improvement fitness evaluation (5 games each against the parent, pass
at strict majority), then stack-apply of the winners onto a fresh snapshot, then an `[evo-auto]`
commit, then a regression evaluation that can `git revert` it.

**What is already shipped and relevant.** Phase EJ added six flags, **all default OFF**, including
`--screen-null-diff` (a mechanical pre-fitness screen that evicts a candidate whose patch changes
no behaviour) and `--regression-rule one-sided`. Phase EL added lineages, a frozen-baseline
gauntlet, fingerprints and extinction. Phase EV added an opt-in themed viewer.

**Patterns this phase must follow.**

- **Flag threading is two-path.** A flag bound with a `functools.partial` in `scripts/evolve.py`
  reaches the *serial* path only. The parallel path (`--concurrency > 1`) runs the primitive in a
  **separate process** via `scripts/evolve_worker.py`, which constructs its own callables from
  argv. The in-repo statement of this is the comment at `scripts/evolve.py:3818-3824`, which says
  the `--screen-null-diff` wrap is "serial-only" and "the PARALLEL path threads the flag via the
  worker argv instead". Every flag in this phase must do both, or it is a silent no-op under
  concurrency.
- **`Improvement` is the forward-compat exemplar.** `Improvement.to_json` uses
  `dataclasses.asdict` and `from_json` carries a `setdefault`, with a comment at
  `src/orchestrator/evolve.py:199-202` saying the compat concern "lives in exactly one place".
  `FitnessResult.to_json` and `SelfPlayRecord.from_json` do **not** follow it, and both are in this
  phase's blast radius.
- **`bots/current` is a pointer, not a version.** Never import `bots.current` or `bots.<version>`
  from `src/orchestrator/` — it triggers a MetaPathFinder loop.

**Baseline on disk today** (measured 2026-09-02, all read-only): `data/selfplay_results.jsonl` has
**1,792 rows, every one carrying exactly the same nine keys**, zero unparseable.
`data/evolve_results.jsonl` is **0 bytes**. `logs/` holds **535** `selfplay_*.log` files — 30
version-named (two per version, one per seat) and 504 candidate-named — which is one surviving file
per (version, seat) no matter how many games were played. `data/baselines.json`,
`data/lineages.json` and `data/fingerprints.json` are **absent**; `data/lineage.json` (singular, the
version DAG) exists and is a different file.

---

## 3. Scope

### In scope

Eleven changes in three groups, delivered as fourteen steps (twelve code, one chain smoke, one
observation soak). Every one of them is zero-added-SC2-games in the form this phase ships it.

**Group A — evidence capture.** Per-game log identity plus retention; a pure-stdlib trace parser;
trace capture at the two existing game-end callbacks; honest provenance on the proposer's evidence
block; and patch capture as a log-only artifact.

**Group B — cheap normalizations that make the open fault line answerable.** Persist the parent's
win count, which is currently censored out of the pool state; and stamp an evaluation id plus a
game index onto each persisted game record, so games belonging to one 5-game evaluation can be
grouped without timestamp clustering.

**Group C — structural levers, all flags, all default OFF.** A stack-size cap; a stack-apply model
override (the operator's idea (i), literally); a whole-package type check before the promotion
commit; and a selectable gate-1 early-stop rule.

### Explicitly out

| Item | Why |
|---|---|
| Any change to the gate-1 **bar** (`--fitness-alpha`, posterior conditioning, margin ordering) | The threshold is unidentifiable until the selectable early stop lands and a post-change corpus exists. Becomes a roughly one-function follow-on. |
| Deterministic replay of a captured patch | This phase's **declared successor**. Round 2 resolved that a stack cap plus replay is the only pair that closes both halves of the validity hole, but replay needs the capture to exist first. |
| Any LLM judge in a gate path | `.claude/skills/improve-bot-evolve/SKILL.md:574` — "Fitness + stack-apply import gate + regression are non-negotiable". |
| Semantic play-quality metrics | Round 2 measured the candidates (attack-state, duration, step count, terminal marker) at AUC ~0.47 against a shuffled-label anchor of 0.50. Null. |
| Arming any new flag as the default | A post-EJ.8 operator decision, per the EJ plan's own section 3. |
| Full-source inlining into the proposer prompt | Round 1 rejected it on feasibility: 23 of the 24 largest closure files sit at graph-distance 1 from the bot module, so there is no usable ordering. |
| The lineage-registry adjunct and the launcher generation-cap fix | Operator decision point 6, and they belong to the separate evolve operational-hardening plan. The launcher defect is already recorded in [`../operator-gate-runbook.md`](../operator-gate-runbook.md). |
| Fixing the colliding `logs/selfplay_tmp/game{i}_p*.json` result files | Real sibling defect (batch-local index, concurrent workers collide) but `--result-out` is a **dead contract** — declared in `bots/v13/__main__.py:100` and read by nothing — so the collision is currently silent. Recorded in Risks; fix it when a consumer appears. |

---

## 4. Impact Analysis

Verified 2026-09-02 by five parallel read-only tracing agents. The `Verified` column records the
grep and its result, enumerating call sites — required by
[`code-quality.md`](../../../.claude/rules/code-quality.md) § "Grep all downstream consumers when
changing a key/id shape", because this phase changes both a filename format and a persisted schema.

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `src/orchestrator/selfplay.py` | modify | The log-path producer at `:292`, inside `_build_bot_process` (def `:260`). Must take a per-game discriminator and expose the shape as ONE importable constant instead of an inline f-string. Also stamps the new evaluation id onto `SelfPlayRecord` at `:730`. | `rg -n "selfplay_[a-z{%*]"` → the only interpolated occurrences repo-wide are `selfplay.py:292` (producer) and `evolve.py:1381`,`:1383` (consumer). `_build_bot_process` called at `:320` (p1) and `:323` (p2), no other callers. `rg -A3 'SelfPlayRecord\($'` → 14 construction sites, all keyword-only, exactly ONE in production: `selfplay.py:730`. |
| `src/orchestrator/evolve.py` | modify | Four separate reasons: `_read_log_tails` (`:1374-1396`) is the ONLY machine reader of the log filename; `FitnessResult.from_json` at `:205` does `SelfPlayRecord(**r)`, bypassing the compat path; the two game-end callbacks at `:626` and `:827` are the trace-capture seams; and the hardcoded early stop at `:631-632` becomes rule-derived. | `grep -rn "_read_log_tails"` → def `:1374`, exactly ONE caller `:1830`, **zero** references in `tests/`. `rg -n 'SelfPlayRecord\(\*\*r\)'` → exactly one hit, `evolve.py:205`, sitting directly under the `:199-202` comment that reasons about forward-compat for a *different* dataclass. `grep -rn "_fitness_bucket"` → exactly 2 hits: `:506` def, `:658` sole call. |
| `src/orchestrator/contracts.py` | extend | Add `fitness_eval_id` and `eval_game_index` to `SelfPlayRecord` (fields `:129-137`) and harden `from_json` (`:143`) to filter unknown keys. | `rg -n 'SelfPlayRecord'` → decorator `:120`, class `:121`, nine fields `:129-137`, `to_json` `:139`, `from_json` `:143` doing a strict `cls(**json.loads(data))`. `git log --oneline -- src/orchestrator/contracts.py` → 4 commits; this dataclass has **never** gained a field, so no migration precedent exists. |
| `src/orchestrator/ladder.py` | modify | `replay_from_jsonl` at `:359` calls `SelfPlayRecord.from_json` over every line of the 1,792-row historical file. A required new field breaks it on line 1. | `rg -n 'SelfPlayRecord.from_json' src/` → `ladder.py:359` only. Disk check: `wc -l data/selfplay_results.jsonl` = 1792; a key-tuple census over the whole file returns exactly ONE tuple, the nine current keys. |
| `scripts/ladder.py` | modify | A **second, independent** replay loop over the same file (`cmd_compare --dry-run`, `:139-146`). Nothing imports it, so a break here is invisible to `uv run pytest`. | `rg -n 'SelfPlayRecord.from_json' scripts/` → `scripts/ladder.py:142`; import `:129`, path `:132`. Reachable only via the CLI documented at `scripts/ladder.py:8`. |
| `scripts/evolve.py` | modify | The orchestrator: five argparse flags; the `--stack-size` truncation at `winner_idxs` (`:4559-4562`); the retry-cap exemption (`_RETRY_CAP` `:125`, `retry_count += 1` `:1930`, `_apply_retry_bookkeeping` `:1917-1918`); the `promoted_imp_idxs` fix at `:4676`; `PerItemState` (`:827-838`); log retention; and worker-argv threading at `:2543-2565`. | `rg -n "stack_apply_fn"` → 4 non-test hits, all here: `:3764` param, `:3791-3792` default binding, `:4609` THE call. `rg -n "dev_apply_fn"` → exactly 2 consumer bindings, `:4434` (fitness) and `:4612` (stack). `rg -n "fitness-stop\|fitness_stop"` → **zero** source hits; the flag does not exist. |
| `scripts/evolve_worker.py` | modify | Every flag in this phase must reach the parallel path through argv, or it is a silent no-op at `--concurrency > 1`. Also forwards the evaluation id and any new `FitnessResult` field. | `run_fitness_eval(` call at `:455`; existing `--games-per-eval` parse at `:173-177`; mirror `selfplay.run_batch` at `:316`; result serialization at `:489`. `rg -n "stack"` → **zero** hits, so stack-only changes need no edit here. |
| `scripts/evolve_inject_one.py` | modify | **Second production caller of `_stack_apply_and_promote`**, reached through an importlib module alias so it does not match the `stack_apply_fn` grep at all. Zero test coverage. | `rg -n "_stack_apply_and_promote"` → production callers are `scripts/evolve.py:3792` AND `scripts/evolve_inject_one.py:211`; alias built at `:44-56` off a `spec_from_file_location` load. `ls tests/ \| grep -i inject` → **nothing**. |
| `src/orchestrator/evolve_dev_apply.py` | extend | `_collect_candidate_py_content` (`:323-325`) already snapshots pre-edit content for the null-diff screen and then discards it; the patch is produced at the same point. Also home of the per-improvement mypy whose narrowness motivates the static gate. | `rg -n "_collect_candidate_py_content"` → exactly ONE call site repo-wide, `:323-325`, taken once before the retry loop; sole consumer `:369`. `_run_mypy` at `:835`, argv `:846-848` — scoped to changed files only. |
| `src/orchestrator/trace.py` | **create** | The trace parser. Pure stdlib, leaf module, no `bots.*` import (MetaPathFinder rule). | New file; no consumers until EI.3. |
| `frontend/src/hooks/useEvolveRun.ts` | modify | The `EvolveOutcome` union is a hand-maintained duplicate of the Python outcome literal and is **already** missing `screen-null-diff` from Phase EJ.2. A new token must be added and the existing drift repaired. | `rg -n "stack-apply-import-fail"` → `:48-50` inside `export type EvolveOutcome` (`:44-53`); consumed at `:71` as `EvolveOutcome \| string`, so TypeScript does **not** enforce it — which is exactly why it drifted. Compare `scripts/evolve.py:957-968`. |
| `frontend/src/components/TimelineList.tsx` | modify | A new stack-apply failure token must join `WARNING_OUTCOMES` beside its import-fail and commit-fail siblings, or it renders unstyled. | `SUCCESS_OUTCOMES` `:47-52`, `FAILURE_OUTCOMES` `:53-58`, `WARNING_OUTCOMES` `:59-62`. `classifyOutcome` `:64-71` falls through to `"neutral"`, so this is cosmetic, not a crash. |
| `.claude/skills/improve-bot-evolve/SKILL.md` | modify | Project convention: a new evolve flag does not ship until the skill an operator invokes documents it. **And** a new run-log outcome token fails a graded skill eval unless documented. | Flag-table precedent at `:72`. Enforced by two live tests: `tests/test_evolve_cli.py:4261-4283` and `:4286-4300`. Eval coupling: `.claude/skills/improve-bot-evolve/evals/evals.json:61` grades FALSE on "a free-text or undocumented stack-apply value". Existing internal inconsistency to repair: `:13` says retry cap 2 while `:271`/`:319` and `scripts/evolve.py:125` say 3. |
| `documentation/wiki/operator-commands.md` | modify | Third copy of the outcome-token list, already stale by three tokens. | `:521` lists six tokens, missing `fitness-close`, `stack-apply-import-fail`, `stack-apply-commit-fail` and `screen-null-diff` versus `scripts/evolve.py:957-968`. |
| `bots/v13/process_registry.py` | extend | Owned by EI.12. `get_temp_file_counts()` is suffix-filtered to `.jsonl`, so the dashboard cannot even **count** the new `.log` and trace files. | `:268-285` — the loop is three `(dir, suffix, label)` tuples, all `.jsonl` or `.SC2Replay`, matched with `f.name.endswith(suffix)`. Surfaced via `/api/processes`. |
| `tests/` (7 files) | extend | See section 9. The load-bearing negative finding: the current suite would go **fully green** on a change that breaks the log producer/consumer relationship in production. | `grep -rn "_read_log_tails" --include=*.py .` → zero test hits. `grep -rn "_build_bot_process" tests` → zero hits. `rg -n 'SelfPlayRecord' tests/test_contracts.py` → **zero hits**, in the file that owns contract round-trip tests. There are **no prompt goldens**. |

---

## 5. New Components

**`src/orchestrator/trace.py`** — a pure-stdlib parser over one game's console log, plus mechanical
feature extraction. Its record shape and output filename are defined here so EI.2 and EI.3 do not
each invent one:

`GameTrace` shape (the parser's return value; extend only additively):

| field | type | note |
|---|---|---|
| `match_id` | `str` | uuid4 string, taken from the log filename EI.1 defines |
| `version` | `str` | the version or `cand_<hash>` this seat played |
| `role` | `str` | `"p1"` or `"p2"` |
| `has_terminal_marker` | `bool` | a terminal result line is present — absence means the game did not finish cleanly, NOT a loss |
| `n_steps` | `int \| None` | highest step number observed; `None` if the log has no step lines |
| `duration_s` | `float \| None` | from the log's own timestamps; `None` if unavailable |
| `status_chunk_count` | `int` | count of status lines; **0 is a legitimate value** for the 36.7% of historical traces that predate the status line |
| `parse_errors` | `list[str]` | non-fatal parse complaints; a populated list never raises |

Output filename, exported as a single importable constant from `trace.py` (the same one-source-of-truth
treatment EI.1 gives the log name): `logs/traces/trace_{version}_{role}_{match_id}.json`. Leaf module: it imports nothing from `bots.*` (MetaPathFinder rule) and nothing
from `evolve.py`, so it is unit-testable in isolation and cannot break a run by existing. Its
extracted features are **mechanical only** — presence of a terminal-result marker, step count,
duration, status-chunk counts. It deliberately computes no play-quality judgement; round 2 measured
that whole class at chance.

**`_SELFPLAY_LOG_NAME` (or equivalent single constant/helper)** in `src/orchestrator/selfplay.py` —
one source of truth for the log filename shape, replacing the current inline f-string at `:292` and
the two duplicate glob patterns at `evolve.py:1381` and `:1383`.

**`_prune_selfplay_logs(logs_dir, keep_n)`** in `scripts/evolve.py` — the retention helper, called at
the generation boundary inside `run_loop`. Deliberately NOT part of `_cleanup_stale_round_files`,
whose sole caller is concurrency-gated and dispatcher-scoped (D-3).

**Six new CLI flags on `scripts/evolve.py`**, all default-off / default-today:
`--trace-capture` (EI.3), `--capture-patch` (EI.7), `--fitness-stop rule|majority` (default
`majority`, EI.8), `--stack-size K` (0 = unlimited, EI.9), `--stack-model MODEL` (EI.10), and
`--stack-static-gate observe|enforce` (EI.11). The three fitness-path flags (`--trace-capture`,
`--capture-patch`, `--fitness-stop`) must also be threaded through `scripts/evolve_worker.py`'s
argv; the three stack-path flags must not, because the worker has no stack path.

---

## 6. Design Decisions

**D-1 — The log filename is keyed on `match_id`, not a game index.** A per-game name keyed on
`game_index` silently fixes nothing for one caller: `scripts/selfplay.py:256` calls `run_batch(...,
1, ...)` with **games=1** inside its own Python loop, so `game_index` is **always 0** for every game
of a 100-game PFSP sweep. Such a scheme would truncate on every game exactly as today while passing
every unit test. `match_id` is already minted per game at `selfplay.py:684`, one line before the
match is built, and is already persisted into `SelfPlayRecord.match_id` — so keying on it needs **no
new caller state and no schema change**, and it makes each log joinable to the result row that
records the winner and duration. It does add one `match_id` parameter to each of `_build_match`
(`selfplay.py:296-303`) and `_build_bot_process` (`:260`), neither of which has a test caller, so
the signature change is contained. **Pin the literal template `selfplay_{version}_{role}_{match_id}.log`**
— keeping the version-and-seat prefix is what lets the consumer's version-scoped glob keep matching. *(Free corollary, out of scope: `seat_swap =
game_index % 2 == 1` is therefore always False in PFSP mode. Recorded in Risks.)*

**D-2 — The reader must be re-anchored AND budget-bounded, in the same step.** `_read_log_tails`
applies its 4,000-byte cap **per file** inside a loop, then joins. Fixing only the glob turns an
~8 KB prompt section into games × 4 KB, and because nothing prunes `logs/`, into generations ×
games × 4 KB over a soak. A reviewer checking "does the glob still match?" would approve a change
that quietly wrecks the proposer's prompt budget. The step therefore ships a newest-N bound as well
as the new pattern.

**D-3 — Retention ships in the same step as per-game naming.** There is **no retention consumer for
these logs anywhere in the repo**: three `*.log` globs exist and none of them deletes; the evolve
sweeper handles only `evolve_*.json` and candidate directories; the dashboard's cleanup endpoint is
suffix-filtered to `.jsonl`. 535 files have already accumulated under the current one-per-seat
scheme. Per-game naming without retention ships an unbounded-growth defect, so the two are one step. **And the retention must not go in `_cleanup_stale_round_files`:** its sole caller sits behind `if concurrency > 1` (`scripts/evolve.py:3873`), so it never runs at the default `--concurrency 1` — mandatory for every `--viewer` run — and even above that it fires once at dispatcher startup rather than per generation. It is also a cross-run sweeper that unlinks unconditionally, so a `--resume` would delete the prior run's traces: the exact evidence this phase exists to keep. Retention gets its own helper on a generation-boundary call site.

**D-4 — `SelfPlayRecord.from_json` filters unknown keys, not just supplies defaults.** Everyone
frames this as "1,792 old rows lack the new field". The unstated half is the forward direction:
`cls(**payload)` also explodes on **extra** keys. Once this phase writes ten-key rows into the
shared append-only file, checking out any pre-EI commit and running the ladder replay raises a
`TypeError` on the first new line — and `bots/` version pinning does not help, because
`src/orchestrator/` is a single shared tree. Filtering unknown keys makes the file roll-forward
safe, and roll-back safe **from this phase onward**. Be precise about what that does not cover: a
genuine checkout of a pre-EI commit still raises on the first wider line, because the filter does
not exist there. That residue is an accepted limitation, not something this phase proves away.

**D-5 — The compat fix must also cover `evolve.py:205`.** `FitnessResult.from_json` splats a
persisted dict straight into the constructor (`SelfPlayRecord(**r)`) instead of going through
`SelfPlayRecord.from_json`. A guard added only to `from_json` protects ladder replay and the
parallel mirror path but **not** the parallel fitness path. This is the identical structural shape
to the Phase 4.6 miss that cost a 70-minute soak, and it sits two lines below a comment reasoning
about exactly this concern for a different dataclass.

**D-6 — `PerItemState`'s field list is duplicated four times and the pool loader fails closed.** The
copies: the dataclass fields, `to_json`, `from_json`, and — the dangerous one — a hardcoded
pop-tuple in `load_pool_state` (`scripts/evolve.py:928-935`) that pops exactly six keys and then
constructs a **frozen** `Improvement`. If `to_json` emits a new field and the pop-tuple is not
updated **in the same commit**, the surplus key reaches the frozen dataclass and raises
`TypeError`, which is swallowed by a broad `except` and turned into exit 1. Net effect: **every
`--resume` of a run started after the change dies at startup, with one log line.** No unit test of
`to_json`/`from_json` in isolation can see it; only the full write-then-load round trip can. All
four copies change together, and the step's done-when names the round-trip test.

**D-7 — A stack-size cap needs three changes, not one.** Truncating `winner_idxs` alone is
unshippable. (a) `promoted_imp_idxs = list(winner_idxs)` at `:4676` would stamp deferred winners
`_PROMOTED` without their code ever being applied, which then feeds the promoted-titles exclusion
list (so the proposer blacklists work that never shipped) and the rollback path. (b) A deferred
winner keeps a pass status that `_apply_retry_bookkeeping` flips back to active, and `retry_count`
increments on every evaluation against `_RETRY_CAP = 3` — so a winner that passed gate 1 is
**evicted after two more evaluations**. (c) Parking deferred winners in a new non-active status
makes them invisible to `_count_active`, which drives `delta = pool_size - active_after_refresh`,
so the pool tops up past `--pool-size` every generation and grows without bound. The in-repo
pattern for exactly this is EJ.2's separate counter alongside the status.

**D-8 — The early stop is not bucket-neutral, and the plan says so where the source does not.** The
bucket's numerator counts only games actually played while its denominator is the untouched
declared `games`. Exhaustive enumeration over all outcome sequences gives **12.5% of evaluations at
the default of 5 games** a different bucket than full play — always pass-adjacent collapsing to
fail, never the reverse. Because fail is terminal and the near-miss bucket is retry-eligible, the
early stop **permanently kills roughly one improvement in nine that a full series would have
resurrected**. The two existing early-stop tests cannot see this: both use clean sweeps where the
flip cannot occur. The in-source comment claiming the stop "saves 2-3 games per evaluation (no
effect on bucket assignment)" is false on both halves, and this phase corrects it.

**D-9 — `--fitness-stop rule` requires a play counter that does not exist yet.** The regression
sibling keeps an explicit `played` counter incremented on **every** game including draws, precisely
so its remaining-games arithmetic is right. The fitness callback counts only decisive games and has
no play counter at all. Porting the regression rule naively would be wrong under draws and crashes,
because the live tally excludes both. The counter lands first, in the same step.

**D-10 — The static gate is injectable and default-off, because a second caller exists.**
`scripts/evolve_inject_one.py` reaches `_stack_apply_and_promote` through an importlib alias, so it
is invisible to the obvious grep and has zero tests. An unconditional gate inside the helper body
would fire in that debug tool and change the ~10-minute iteration cycle the operator relies on. The
gate takes an injectable callable defaulted off, mirroring the existing import-check seam.

**D-11 — Anything crossing the worker boundary goes into both hand-written serializers.**
`FitnessResult.to_json` enumerates nine keys by hand rather than using `dataclasses.asdict`. Add a
field to the dataclass and forget those lines, and serial runs carry it while parallel runs silently
lose it — no exception, no log, a concurrency-dependent data loss of the same shape as the Phase 4.6
miss. Every step that adds a field to a ferried dataclass must touch both directions and assert a
non-default value in the round trip.

**D-12 — A new outcome token is a documentation change, not just a code change.** A stack-apply
outcome reaches a **graded skill eval** through a three-hop path no code grep follows: the run-log
generations table, then `evals.json:61`, which grades FALSE on an undocumented stack-apply value.
The token must be documented in the skill and the wiki in the same step that emits it.

---

## 7. Build Steps

**Quality gates — binding on every `Type: code` step below.** The gate that flips a step DONE runs
the FULL suite, per [`CLAUDE.md`](../../CLAUDE.md), and names which suites ran:

- `uv run pytest` — full suite. Baseline **2,007 collected** without the optional extra, **2,024
  with it** (17 pygame-gated tests). The dev `.venv` currently has the viewer deps installed, so a
  bare run reports 2,024. Never compare a worktree count to a main-project count. Subsets are fine
  while iterating, never at the gate.
- `uv run ruff check .` — **applies to `scripts/`.** `[tool.ruff] line-length = 100`.
- `uv run mypy src bots --strict` — **does NOT cover `scripts/`.** Verified at `pyproject.toml:97`:
  `packages = ["orchestrator", "bots.v0", "selfplay_viewer"]`. **Most of this phase's diff lands in
  `scripts/evolve.py`, which is outside mypy's scope** — a type error there will not be caught by
  the typecheck gate. Do not spend a build iteration expecting it to be. Run mypy anyway to prove no
  regression in the packages that are covered.
- `uv run mypy scripts/evolve.py scripts/evolve_worker.py --strict` — **added by this phase**, because
  `scripts/evolve.py` carries most of its diff and is otherwise ungated. Declared baseline:
  **3 pre-existing errors** (measured 2026-09-02) — two `import-not-found` on `evolve_round_state`
  and one `attr-defined` on a non-re-exported `current_version`. The gate is "no NEW errors", not
  zero. A step may fix the baseline errors, in which case it lowers the number for later steps.
- `cd frontend && npm run test:run` — for the two frontend steps only. Baseline 234 (228 passing, 6
  skipped). **Not `npm run test`**, which is bare `vitest` (watch mode) and never exits in a
  non-interactive shell.

**Step-heading notation.** Steps below are `### Step EI.N:` (letter.number), matching Phases
B/D/E/G/EL/EJ/EV. A strict reading of `/plan-review` §25(a)'s `^#{3,4} Step \d+:` regex does not
match that form, but it is the established convention here: `/repo-sync` documents letter and
sub-phase notation, and Phases EJ and EV both shipped through this exact pipeline with `EJ.N` /
`EV.N` headings. Renumbering to bare digits would risk the cross-plan step-number collision the
letter form exists to prevent.

**Reviewer routing and the freeze.** Three steps carry `--reviewers deep` — EI.1, EI.5 and EI.6, the key-shape and persisted-schema
changes whose consumers have no test coverage.
That lane is **currently inoperative mesh-wide** and halts with `required_tool_missing` until the
review-deep restoration seals — which is the same freeze that blocks building this plan at all. By
the time a build is legal, the lane will be back. Do not downgrade those three to `code` to get a
build moving early; that would defeat the routing.

**Base branch.** Build this phase on **`master-plan/phase-ev`**, the de-facto mainline, NOT on
`master`. `master` is 18 commits behind and lacks the Phase EV and on-brand work this repo has been
developing against since 2026-08-10. If the branch has landed by build time (see
[`../branch-landing-phase-ev.md`](../branch-landing-phase-ev.md)), build on `master` instead.

**Fresh-worktree note.** A fresh worktree binds Python 3.12 (`requires-python = ">=3.12"`, no
`.python-version`) while the project runs 3.14, producing a false-red pointer test. Always
`uv venv --python 3.14 && uv sync --extra dev` before the first gate.

### Step EI.1: Per-game log identity + retention
- **Problem:** `src/orchestrator/selfplay.py:292` writes `logs/selfplay_{version}_{role}.log`, which burnysc2 opens `"w+"`, so every game truncates the previous game's only trace. Four coupled changes. **(a) Filename.** Use the literal template `selfplay_{version}_{role}_{match_id}.log`, keying on the `match_id` already minted at `selfplay.py:684`. **Keep the `selfplay_{version}_{role}` prefix** — the only machine consumer globs on the parent version (`src/orchestrator/evolve.py:1381`), so a name that drops the version can never match and the function silently returns its fallback string straight into the proposer prompt. Do NOT key on `game_index`: it is always 0 in PFSP mode (D-1). Threading `match_id` to the producer adds one parameter to **each** of `_build_match` (`selfplay.py:296-303`) and `_build_bot_process` (`:260`) — neither has a test caller, so the signature change is contained. **(b) One source of truth.** Expose the shape as a single importable format helper (not a raw string) that BOTH producer and consumer import, replacing the inline f-string at `:292` and the two duplicate glob patterns at `evolve.py:1381` and `:1383`. **(c) Reader budget.** Re-anchor `_read_log_tails` and bound it to a newest-N set — its 4,000-byte cap is applied **per file** inside the loop, so per-game naming turns an ~8 KB prompt section into games × 4 KB and then generations × games × 4 KB (D-2). **(d) Retention.** Add a dedicated `_prune_selfplay_logs(logs_dir, keep_n)` called at the **generation boundary inside `run_loop`**. Do **NOT** put it in `_cleanup_stale_round_files`: that function's sole caller sits behind `if concurrency > 1` at `scripts/evolve.py:3873`, so retention placed there never runs at the default `--concurrency 1` — which is also mandatory for every `--viewer` run — and even at higher concurrency it runs once at dispatcher startup, not per generation. Retention must never unlink a file belonging to the live run, and must not delete a prior run's retained logs on `--resume`.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `src/orchestrator/selfplay.py` (log-name helper; `_build_match` and `_build_bot_process` signatures), `src/orchestrator/evolve.py` (`_read_log_tails` re-anchor + newest-N bound), `scripts/evolve.py` (`_prune_selfplay_logs` + its generation-boundary call site); a new producer→consumer round-trip test
- **Done when:** full `uv run pytest` green at or above the 2,007/2,024 baseline, ruff clean, mypy clean in both scopes (packages, and the `scripts/` gate at or below its 3-error baseline), with new tests asserting (1) two games in one batch produce two distinct log files, neither truncating the other; (2) an **integration test through the production caller** per [`code-quality.md`](../../../.claude/rules/code-quality.md) — generate logs via the real producer, then read them back via the real `_read_log_tails`, asserting non-empty content for a known match, because a pattern-only unit test passes while production silently degrades to the empty-glob fallback string; (3) the shared name helper is asserted `is`-identical between producer and consumer, per `code-quality.md` § "One source of truth for data-shape constants"; (4) the newest-N bound holds the tails section under a fixed byte ceiling given 50 files; (5) **retention actually fires through `run_loop` at `--concurrency 1`** — a direct call to the retention helper does NOT satisfy this clause, because that is exactly the silent-wiring shape this step exists to avoid; (6) retention never unlinks a file whose mtime is inside the live run's window, and a `--resume` does not delete the prior run's retained logs. The two existing prompt assertions at `tests/test_evolve.py:1841` and `:1872` still pass unmodified.
- **Depends on:** none

### Step EI.2: `src/orchestrator/trace.py` — stdlib trace parser
- **Problem:** Add a leaf module that parses one game's console log into a structured record and extracts **mechanical** features only (terminal-result marker present, step count, duration, status-chunk counts). No `bots.*` import, no `evolve.py` import, no play-quality judgement — round 2 measured that whole class at chance. Pure stdlib so it is unit-testable in isolation and cannot break a run by existing.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** new `src/orchestrator/trace.py`; `tests/test_trace.py`
- **Done when:** full suite green, mypy clean (this module IS in mypy's scope), with tests covering a complete log, a truncated log, an empty file, and a log that predates the status-log line entirely — the last case matters because 36.7% of the existing candidate corpus has no status line at all, and the parser must report absence rather than inferring a value.
- **Depends on:** none

### Step EI.3: Trace capture at the two existing game-end seams
- **Problem:** Wire `trace.py` into the fitness and regression game-end callbacks (`src/orchestrator/evolve.py:626` and `:827`), writing one parsed trace per game, fail-open so any parse error logs and continues rather than aborting a soak. **Capture is gated on a new `--trace-capture` flag, default OFF**, so the bare invocation stays byte-identical; thread it through the worker argv as well as the serial path, since the fitness callback runs inside the worker at `--concurrency > 1`. Traces are written to `logs/traces/` under a filename shape exported as a single importable constant from `trace.py`, mirroring EI.1's treatment of the log name, and pruned by the same `_prune_selfplay_logs`-style generation-boundary helper with its own `keep_n`. Sequenced after EI.1 so the per-worker splice cause is already removed; do NOT disable under concurrency, which would make the layer inert on the most recently recorded soak configuration.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `src/orchestrator/evolve.py`, `scripts/evolve.py` (flag + generation-boundary prune), `scripts/evolve_worker.py` (argv), `src/orchestrator/trace.py` (output-name constant); capture tests
- **Done when:** full suite green; a test drives a fake batch through the **production** callback path and asserts a trace lands per game; a test asserts a raising parser does not propagate and does not prevent the caller's own callback from running; an assertion that with `--trace-capture` absent the callback chain is byte-identical; and an assertion that the flag reaches the worker argv.
- **Depends on:** EI.1, EI.2

### Step EI.4: Proposer evidence provenance
- **Problem:** The proposer is handed log tails with nothing saying which games they are. Label each tail with its file, seat, opponent and outcome. **Join on the `match_id` that EI.1 puts into the filename, against the persisted `data/selfplay_results.jsonl`** — NOT against the in-scope mirror `records` at `src/orchestrator/evolve.py:1823`, which cover mirror games only while most tails are fitness games and would therefore go unlabelled. Specify the degraded label for a tail whose `match_id` has no matching row (e.g. `(unjoined — no result row for this match)`) so a missing join fails honestly rather than silently. **Do not** add a "no mirror games this generation" line: shipped code already prints `Mirror games run (parent vs parent): 0` on refresh generations, so round 1's framing of this as a false label was wrong; the defect is missing provenance only. **This step depends on EI.1** — before per-game log identity exists there is exactly one truncated file per (version, seat) and nothing to join a tail to, so labelling it would stamp false provenance onto the most expensive call in the loop.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `src/orchestrator/evolve.py` prompt assembly
- **Done when:** full suite green; a test asserts the assembled prompt names the seat and outcome for a known record; a test asserts the degraded label is emitted for an unjoinable tail rather than a wrong one; the two existing prompt assertions still pass. There are **no prompt goldens** in this repo (verified: zero test references to the prompt builder or log-tail reader), so this step has no golden-update cost.
- **Depends on:** EI.1

<!-- autofix-applied: 2026-09-02 -->
### Step EI.5: `SelfPlayRecord` evaluation id + bidirectional compat
- **Problem:** Add `fitness_eval_id: str | None = None` and `eval_game_index: int | None = None` to `SelfPlayRecord` as **optional with defaults**, stamp them at the single production construction site (`src/orchestrator/selfplay.py:730`), thread the id through `run_batch` as a keyword-only argument and pass it at the **four** `run_batch_fn(` call sites in `src/orchestrator/evolve.py` — `:650` (fitness), `:858` (regression), `:1017` (baseline gauntlet), `:1819` (mirror). **`fitness_eval_id` is a `str(uuid.uuid4())` minted once per `run_batch` invocation by the CALLER**, mirroring how `match_id` is minted per game at `selfplay.py:684`; `eval_game_index` is the zero-based index of the game within that batch. Callers that do not supply an id leave both fields `None`, which is what every historical row already means. Consumers: the two replay loops (`src/orchestrator/ladder.py:359`, `scripts/ladder.py:142`), the parallel mirror rehydration in `scripts/evolve.py`, the worker result path, and any later analysis that needs to group a 5-game evaluation without timestamp clustering — which is the whole point of the field. and harden `from_json` to **filter unknown keys** rather than only supplying defaults (D-4). Apply the same tolerance at `src/orchestrator/evolve.py:205`, which bypasses `from_json` entirely (D-5), and in lockstep at both replay loops (`src/orchestrator/ladder.py:359` and `scripts/ladder.py:142`) and the parallel mirror rehydration.

  **`SelfPlayRecord` current shape** (`src/orchestrator/contracts.py:121-145`, verified 2026-09-02) — the nine keys every one of the 1,792 existing rows carries:

  | Field | Type | Note |
  |---|---|---|
  | `match_id` | `str` | `str(uuid.uuid4())`, minted per game at `selfplay.py:684`; the EI.1 filename key and the EI.4 join key |
  | `p1_version` | `str` | |
  | `p2_version` | `str` | |
  | `winner` | `str \| None` | version string of the winning side; `None` for draw/crash |
  | `map_name` | `str` | |
  | `duration_s` | `float` | |
  | `seat_swap` | `bool` | always `False` in PFSP mode (D-1 corollary) |
  | `timestamp` | `str` | ISO 8601 |
  | `error` | `str \| None = None` | the only field carrying a default today |

  Frozen dataclass. `to_json` is `dataclasses.asdict`; `from_json` is a strict `cls(**json.loads(data))` — which is what D-4 hardens. The two new fields are additive optional-with-default, making a ten-key row.

- **Type:** code
- **Issue:** #
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `src/orchestrator/contracts.py`, `selfplay.py`, `evolve.py`, `ladder.py`; `scripts/ladder.py`, `scripts/evolve.py`, `scripts/evolve_worker.py`; a NEW tracked fixture `tests/fixtures/selfplay_results_legacy.jsonl` (~50 rows copied verbatim from the live file); extended `tests/test_selfplay.py`, new `SelfPlayRecord` coverage in `tests/test_contracts.py`
- **Done when:** full suite green; tests assert (1) a legacy nine-key line loads and yields defaults; (2) a dataclass constructed **without** the new fields accepts a ten-key payload — this simulates an older build and is the honest form of the roll-back claim (see D-4); (3) a round trip that **sets the new field to a non-default value** — dataclass equality passes trivially when both sides hold equal defaults, so a default-valued round trip cannot detect the drift; (4) the **tracked fixture** replays without error through **both** replay loops. Do NOT assert against `data/selfplay_results.jsonl`: it is gitignored (`.gitignore:42`) and this step runs `--isolation worktree`, so the file does not exist there and the assertion would be silently skipped or fabricated. The full 1,792-row replay moves to EI.14's operator checklist, where the file exists. (5) the id is actually stamped by `run_batch` and groups a 5-game evaluation.
- **Depends on:** none

<!-- autofix-applied: 2026-09-02 -->
### Step EI.6: Persist `wins_parent` and games-played
- **Problem:** `PerItemState` records `fitness_score = [wins_candidate, games]` and drops the parent's win count, and the denominator is the **declared** games rather than games played. Persist both **as NEW keys (`wins_parent`, `games_played`)** — do **not** change the arity of `fitness_score` or of the results-row `score`. Both are rendered as fixed-arity pairs by the dashboard (`frontend/src/hooks/useEvolveRun.ts:29` types `fitness_score` as `[number, number] | null`; `EvolutionTab.tsx:886` renders `{item.fitness_score[0]}/{item.fitness_score[1]}`), so widening either list in place silently mislabels wins and games in the Evolution tab — the wire-shape failure class in [`code-quality.md`](../../../.claude/rules/code-quality.md) § "Audit wire shape when storage representation changes". **All four copies of the pool-state field list must change in the same commit** — the dataclass, `to_json`, `from_json`, and the hardcoded pop-tuple in `load_pool_state` (`scripts/evolve.py:928-935`) — or every `--resume` of a run started after this change dies at startup with a single swallowed log line (D-6).

  **`PerItemState` current field list** (`scripts/evolve.py:827-838`, verified 2026-09-02) — the six keys that must stay in lockstep across all four copies: `status` (default `_ACTIVE`), `fitness_score` (`list[int] | None`, the fixed-arity `[wins_candidate, games]` pair), `retry_count` (`int`), `first_evaluated_against` (`str | None`), `last_evaluated_against` (`str | None`), `consecutive_null_diffs` (`int`, added by Phase EJ.2 — the in-repo precedent for an additive defaulted field here). The fourth copy is the hardcoded pop-tuple in `load_pool_state` (`scripts/evolve.py:928-935`), which pops exactly these six and splats whatever remains into the frozen `Improvement`.

- **Type:** code
- **Issue:** #
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** modified `scripts/evolve.py`; pool-state round-trip tests; a frontend render assertion in `frontend/src/components/EvolutionTab.test.tsx`
- **Done when:** full suite green; a **full write-then-load round-trip test** (not isolated `to_json`/`from_json` unit tests, which cannot see this failure) proves a pool file written by the new build loads; a pool file written by the **old** build still loads; a fitness row carries both win counts plus games played alongside declared games; and `cd frontend && npm run test:run` green at or above 234 with an assertion that the Evolution tab's wins/games render is **unchanged** — the new values are additive keys, not a widened pair.
- **Depends on:** none

### Step EI.7: Patch capture, log-only
- **Problem:** Capture the fitness-time patch and the stack-time patch as a **log-only artifact**, behind a new `--capture-patch` flag (default OFF). **The pre-edit content is NOT collected on the default path.** `_collect_candidate_py_content` is guarded at `src/orchestrator/evolve_dev_apply.py:323-325` on `screen_null_diff`, which defaults OFF — so today `content_before` is an empty dict and there is nothing to diff against. Widen that guard to `if screen_null_diff or capture_patch`, so patch capture does not silently depend on an unrelated Phase EJ flag being armed. Dev-apply runs **inside the worker**, so `--capture-patch` genuinely crosses the subprocess boundary and must be threaded through the worker argv, not only bound serially. Store the patch in an applicable form so deterministic replay becomes a follow-on rather than a rebuild. **This is not a decision input**: round 2 deleted round 1's pre-registered threshold rule, because both of its branches point the same way and a clean-apply check cannot separate "same idea, differently implemented" from "different idea". If the captured value crosses the worker boundary it must be added to **both** hand-written `FitnessResult` serializers (D-11).
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `src/orchestrator/evolve_dev_apply.py` (widened guard), `src/orchestrator/evolve.py`, `scripts/evolve.py` (`--capture-patch` argparse + serial binding), `scripts/evolve_worker.py` (argv parse + forward)
- **Done when:** full suite green; a test asserts a patch is captured **with `--capture-patch` ON and `--screen-null-diff` OFF** — the flag-off-sibling case is the one that matters, because a test that arms both would pass over a production no-op; a test asserts the capture is absent-not-crashing for a candidate that changed no files; a test asserts the default path is byte-identical with the flag absent; a test asserts the flag reaches the worker argv; and if the value is ferried, a round-trip test sets it to a non-default value and asserts it survives the worker result file.
- **Depends on:** none

### Step EI.8: `--fitness-stop rule|majority`
- **Problem:** The gate-1 early stop is hardcoded to a strict majority and is **not** bucket-neutral (D-8). Add a `played` counter to the fitness callback — it does not exist, and the regression sibling's arithmetic cannot be ported without it (D-9) — then extract a pure `_fitness_stop_locked(...)` helper (the sibling of `_regression_stop_locked` at `src/orchestrator/evolve.py:689`) and add `--fitness-stop rule|majority` defaulting to `majority`, which must be byte-identical to today. Thread it through the **worker argv** as well as the serial path, or it is a silent no-op under concurrency. Correct the false in-source comment claiming the stop saves 2-3 games with no effect on bucket assignment, and the copies of that claim in the viewer wrapper docstring (`scripts/evolve.py:600`) and `documentation/plans/evolve-viewer-plan.md:249`.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `src/orchestrator/evolve.py`, `scripts/evolve.py`, `scripts/evolve_worker.py`, `.claude/skills/improve-bot-evolve/SKILL.md`, `documentation/plans/evolve-viewer-plan.md`
- **Done when:** full suite green, which requires the three clauses below to be **mutually satisfiable** — they are only if the property is scoped as stated. (1) An exhaustive soundness property parametrized over **`--fitness-stop rule` ONLY** (not over `majority`), asserting the rule-derived stop never changes a bucket versus full play. Run it against the extracted `_fitness_stop_locked` **helper**, not against `run_fitness_eval` — the primitive does a real `snapshot_current()` tree copy per call, measured at ~34 ms against the regression twin's ~56 µs, so a literal mirror of that test's parametrization would add roughly 25 minutes to the gate that flips every later step DONE. Budget: the new property adds **under 30 seconds**. Pair it with a handful of end-to-end `run_fitness_eval` cases proving the helper is actually wired. (2) A separate **characterization** test pinning the majority stop's known non-neutrality by enumerating the five-game sequences where it collapses the near-miss bucket into fail — so the defect stays codified as a known deviation rather than being silently re-blessed or silently fixed. (3) A test asserting `--fitness-stop majority` is byte-identical to today, and one asserting the flag reaches the worker argv, which the existing `--regression-rule` test pair does **not** cover. **The two existing early-stop tests are clean sweeps and remain valid unmodified** — neither stops one win short, so neither codifies the collapse; this step ADDS a near-miss case they cannot reach. Any diff that modifies either existing test is suspect and must be justified, per [`code-quality.md`](../../../.claude/rules/code-quality.md) § "Audit wire shape".
- **Depends on:** EI.6

### Step EI.9: `--stack-size K`
- **Problem:** Cap how many gate-1 winners are stacked into one promotion (`winner_idxs`, `scripts/evolve.py:4559-4562`), default 0 = unlimited = today. Three changes are mandatory together (D-7): exempt deferred winners from `_RETRY_CAP` so a winner that passed gate 1 is not evicted after two more evaluations; stop `promoted_imp_idxs = list(winner_idxs)` at `:4676` from stamping deferred winners as promoted, which would poison the promoted-titles exclusion and the rollback path; and keep deferred winners visible to `_count_active` so the pool does not top up past `--pool-size` every generation.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/evolve.py`, `.claude/skills/improve-bot-evolve/SKILL.md`
- **Done when:** full suite green; tests assert (1) at `K=0` behaviour is byte-identical; (2) at `K=1` only the rank-1 winner is stacked and the rest are deferred, **not** evicted and **not** marked promoted; (3) a deferred winner survives more than `_RETRY_CAP` generations; (4) `_count_active` includes deferred winners so `delta` is unchanged; (5) an **integration test through `run_loop`** proving the cap reaches the real stack-apply call rather than being unit-tested in isolation.
- **Depends on:** EI.6

### Step EI.10: `--stack-model MODEL`
- **Problem:** The operator's idea (i), literally. `spawn_dev_subagent` takes a `model` keyword that **no production caller sets**. Bind a `functools.partial` at the single `stack_apply_fn(` call site (`scripts/evolve.py:4609`), leaving the fitness binding at `:4434` untouched. State explicitly in the step whether `scripts/evolve_inject_one.py:211` — the second production caller, invisible to the obvious grep — is in or out of the flag's reach (D-10).
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/evolve.py`, `.claude/skills/improve-bot-evolve/SKILL.md`
- **Done when:** full suite green; an integration test through `run_loop` (template: the existing null-diff integration test) proves the escalated model reaches the **stack** dev-apply and **not** the fitness one; a test asserts the **fitness** dev-apply callable at `scripts/evolve.py:4434` stays the bare identity when the flag is absent, preserving the two existing byte-identical-default assertions. Note `scripts/evolve_worker.py` has **zero** stack involvement (`rg -n "stack"` returns nothing), so this step needs no worker edit and no worker-argv assertion.
- **Depends on:** none

### Step EI.11: `--stack-static-gate`
- **Problem:** Per-improvement mypy validates changed files only, so cross-improvement signature drift is invisible. Add a whole-package `mypy --strict` on the new version **before** the `[evo-auto]` commit. Ship it with **two modes**: `--stack-static-gate=observe` records the verdict and the outcome token but never blocks a promotion or drops a rank, and `--stack-static-gate=enforce` reuses the existing cleanup path on failure (never the fragile revert path) with a drop-lowest-rank rule so a repeated static failure cannot livelock the retry ladder. Default is OFF. The observe mode exists because a blocking gate changes stack composition and can suppress a promotion — which suppresses the regression evaluation gated on `promoted_imp_idxs` at `scripts/evolve.py:4857`, removing both a promotion and `--games-per-eval` games from a generation. That is a real perturbation and must not be armed inside another phase's measurement (see EI.14). The gate takes an **injectable callable defaulted off**, mirroring the existing import-check seam, because an unconditional gate would fire in the debug tool and change the operator's ~10-minute iteration cycle (D-10). **The new outcome token literal is `stack-apply-static-fail`**, joining the ten existing members of the `PhaseOutcome` literal at `scripts/evolve.py:957-968` alongside its `stack-apply-import-fail` and `stack-apply-commit-fail` siblings. That exact string must appear identically in `scripts/evolve.py`, the frontend union, `SKILL.md` and the wiki token list, or a graded skill eval fails (D-12).
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `scripts/evolve.py`, `src/orchestrator/evolve_dev_apply.py`, `.claude/skills/improve-bot-evolve/SKILL.md`, `documentation/wiki/operator-commands.md`
- **Done when:** full suite green; a primitive test mirrors the existing import-fail rollback test's assertion set for a static failure **in `enforce` mode**; a test asserts `observe` mode records a verdict and leaves `promoted_imp_idxs`, the commit and the regression phase untouched; a test asserts the gate does not run when the flag is off, so the four existing stack-apply primitive tests do not shell out to a real mypy against a synthetic tree; the outcome token is present in the skill flag table and the wiki token list, and the two live SKILL.md assertion tests still pass.
- **Depends on:** EI.9

### Step EI.12: Frontend token repair + dashboard file counts
- **Problem:** The `EvolveOutcome` union in `frontend/src/hooks/useEvolveRun.ts` is a hand-maintained duplicate of the Python outcome literal and is **already** missing `screen-null-diff` from Phase EJ.2 — the drift is invisible because the value is consumed as a widened type. Add `stack-apply-static-fail`, repair the existing drift, and add the token to `WARNING_OUTCOMES` in `TimelineList.tsx` beside its import-fail and commit-fail siblings. **Also extend `get_temp_file_counts()`** (`bots/v13/process_registry.py:268-285`): its loop is suffix-filtered to `.jsonl` and `.SC2Replay`, so the dashboard cannot even count the `.log` and trace files this phase creates. Add rows for `logs/*.log` and `logs/traces/`, so the operator can see growth on the Processes tab rather than discovering it on disk.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** modified `frontend/src/hooks/useEvolveRun.ts`, `frontend/src/components/TimelineList.tsx`, `bots/v13/process_registry.py`
- **Done when:** `cd frontend && npm run test:run` green at or above 234; a test asserts the union matches the Python `PhaseOutcome` literal member-for-member, so this drift cannot recur silently; `npm run build` clean; full `uv run pytest` green with a test asserting the new file-count rows appear in `get_temp_file_counts()`.
- **Depends on:** EI.1, EI.3, EI.11

### Step EI.13: Producer→consumer smoke gate
- **Problem:** An end-to-end run of the evidence chain with real components, exercising producer→consumer drift that mock-bounded unit tests cannot see — each such test mocks the boundary it would have asserted on. **The ONLY substitution permitted is the `GameMatch` runner inside `_build_match`**, replaced by a fake that writes a real recorded console log to the production path via the production log-name helper. Everything downstream runs unmocked: the log write, the trace parse, `_read_log_tails`, prompt assembly with provenance, the pool-state write-then-load round trip, and both `SelfPlayRecord` replay loops. **No SC2 process launches and no games are played**, which is what keeps this step inside the phase's zero-added-games claim and runnable in a worktree. Do not reach for a real batch: the log file is written only by burnysc2's bot process, so "real batch" would mean a real StarCraft II game, which cannot run in the default suite (`addopts = "-m 'not sc2'"`), cannot finish in seconds, and would add games.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Produces:** a chain-smoke test in the standard suite exercising the full evidence chain; recorded output
- **Done when:** the chain completes with no exception; the log files produced are distinct per game and carry the production name shape; the trace parser reads every one; the proposer evidence block is non-empty and carries provenance including the degraded label for an unjoinable tail; the pool file round-trips; and the **tracked fixture** `tests/fixtures/selfplay_results_legacy.jsonl` replays clean through both replay loops. Do NOT assert against `data/selfplay_results.jsonl` — it is gitignored and absent from a worktree. Run it **before** EI.14.
- **Depends on:** EI.1, EI.2, EI.3, EI.4, EI.5, EI.6, EI.7

### Step EI.14: Shadow-half observation soak
- **Problem:** The evolve loop is unattended, multi-hour, wall-clock-dependent behaviour, and this phase adds capture that runs inside it. Time-dependent failures — unbounded log growth, retention deleting a live file, prompt-budget creep across generations, a fail-open path that silently swallows every trace — are invisible to the EI.13 chain smoke.

  **Host: EL.7 (#279), the 6-hour multi-lineage soak.** Round 1's "ride the flag-OFF shadow half of a queued soak" is not runnable as written — none of EJ.7, EJ.8 or EL.7 has a multi-hour flag-off half. EJ.7's only flags-off leg is a 15-minute smoke, and EJ.8 and EL.7 are each a single flags-on run compared against *historical* controls. EL.7 is chosen because it is the longest queued gate and its recorded observations (generations per lineage, diversity matrix, extinction, orphan processes, commit hygiene) do not include any metric this phase's capture can move.

  **Arming set — log-only, and nothing else.** Arm exactly `--trace-capture`, `--capture-patch`, and the EI.1 log naming and retention, which are unconditional. Do **NOT** arm `--stack-static-gate` in `enforce`, `--stack-size`, or `--fitness-stop rule` on a host run: each changes stack composition or games per generation. `observe` mode of the static gate is permitted because it records without blocking. **EI claims nothing from EL.7's own result table**, and EL.7's record must note the co-tenancy.

  **The command.** EL.7's own invocation with this phase's log-only flags appended. Read the
  runbook's EL.7 section first — it carries two blocking preconditions (register frozen baselines,
  and decide the lineage-registry path, because `--lineages N` does not create N lineages):

  ```powershell
  cd c:\Users\abero\dev\Alpha4Gate
  git branch --show-current          # expect master-plan/phase-ev
  git diff --staged --stat           # MUST be empty - evo-auto commits sweep the whole index
  uv run python scripts/evolve.py --lineages 4 --population-cap 3 --hours 6 --generations 0 --fitness-mode both --trace-capture --capture-patch --stack-static-gate observe
  ```

  **Zero added SC2 games**, and that claim is now true rather than assumed: the armed set writes files and records rows, and touches neither gating nor composition. Serialize against the other pending soaks — one evolve run at a time, machine-wide. See [`../operator-gate-runbook.md`](../operator-gate-runbook.md), which also carries EL.7's own two blocking preconditions (the absent baselines registry, and the fact that `--lineages N` does not create N lineages).
- **Type:** wait
- **Issue:** #
- **Produces:** an observation report under `documentation/soak-test-runs/`
- **Done when:** the soak completes and the report records: one trace file per game and worker, not one per version and seat; a bounded `logs/` directory after retention, with no file from the live run unlinked; the proposer evidence block naming each tail's game, seat, opponent and outcome for at least one refresh generation; every fitness row carrying a non-null parent win count and evaluation id; at least one whole-package type-check verdict recorded in **observe** mode (advisory, non-blocking — not a blocked promotion); the gate-1 accept rate reported with an exact binomial p-value against the 0.5 null; and the full `data/selfplay_results.jsonl` replaying clean through both replay loops in the main project tree, where the file exists. **Claims survival and instrumentation only** — no throughput verdict, which would need a paired control.
- **Depends on:** EI.11, EI.12, EI.13

---

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| Log filename keyed on a non-unique discriminator | A `game_index` key is always 0 in PFSP mode, so it would truncate every game while passing every test | D-1: key on `match_id`, already minted per game and already persisted |
| Prompt-budget blowup | Per-game logs multiply the proposer's evidence section by games × generations; a reviewer checking only the glob would approve it | D-2: newest-N bound ships in the same step, with a byte-ceiling test |
| Unbounded disk growth | No retention consumer for these logs exists anywhere in the repo; 535 files already accumulated | D-3: retention is part of EI.1, not a follow-up |
| `--resume` dies at startup | The pool-state field list is duplicated four times and the loader fails closed into a swallowed `TypeError` | D-6: all four copies change together; done-when names the write-then-load round trip |
| Silent data loss under concurrency | `FitnessResult.to_json` is hand-written; a new field is dropped in parallel runs with no error | D-11: both directions change together, and the round trip must set a non-default value |
| A flag that does nothing at `--concurrency > 1` | The parallel path builds its callables from argv in a separate process | The three **fitness-path** flag steps (EI.3, EI.7, EI.8) carry a worker-argv assertion. EI.9–EI.11 are stack-path-only and correctly need none |
| A graded skill eval fails | A new outcome token reaches `evals.json` through a three-hop path no code grep follows | D-12: the token is documented in the same step that emits it |
| The debug tool's iteration cycle changes | `evolve_inject_one.py` is a second production caller invisible to the obvious grep, with zero tests | D-10: injectable gate, defaulted off; EI.10 states its reach explicitly |
| Suspect test diffs | A developer may adjust an existing early-stop test to match new behaviour | Neither existing test codifies the collapse — both are clean sweeps and stay unmodified. EI.8 ADDS a near-miss case they cannot reach; any diff touching either existing test is suspect |
| Historical corpus is not uniform | 36.7% of candidate traces predate the status-log line | EI.2 reports absence rather than inferring; any historical study must exclude them |
| `--reviewers deep` is frozen | Three steps route to a lane that currently halts with `required_tool_missing` | Same freeze that blocks building this plan at all. By the time a build is legal the lane is back; do not downgrade to get moving early |
| Worktree cannot see runtime data | `data/` and `logs/` are gitignored, so no `--isolation worktree` step can assert against real run output | EI.5 ships a tracked ~50-row fixture; the full-file replay moves to EI.14 in the main tree |
| **Open — idea (ii)** | The operator's second improvement idea is still truncated; three of its six readings need this phase's evidence | Decision card in the seed doc. This phase is correct either way. |
| **Open — latent, out of scope** | `seat_swap = game_index % 2 == 1` is therefore always False in PFSP mode, so PFSP never swaps seats; and `logs/selfplay_tmp/game{i}_p*.json` collides across concurrent workers | Both recorded here rather than fixed. The second is currently silent because `--result-out` has no reader. |

---

## 9. Testing Strategy

**The load-bearing negative finding.** The current 2,007-test suite would go **fully green** on a
change that breaks the log producer→consumer relationship in production. Verified: zero test
references to the log-tail reader, zero to the bot-process builder, no prompt goldens, and the file
that owns contract round-trip tests has **no `SelfPlayRecord` test at all**. This is the precondition
for the Phase 4.6 regression reproduced exactly, which is why every step below names an integration
test through the production caller rather than a unit test of the new component alone.

**New tests, by class:**

- **Producer→consumer round trips** (EI.1, EI.5, EI.6) — generate through the real producer, read
  through the real consumer. The bug lives in the relationship, not either endpoint.
- **Byte-identical-default assertions** (EI.8, EI.9, EI.10, EI.11) — every new flag must prove the
  off path is unchanged, including the worker's callable identity.
- **Worker-argv threading** (EI.3, EI.7, EI.8) — the existing `--regression-rule` test pair does not
  cover this, so the pattern is new here. **EI.9–EI.11 are stack-path-only:** `scripts/evolve_worker.py`
  has zero stack involvement, so they need no worker edit and no worker-argv assertion.
- **Bidirectional schema compat** (EI.5) — legacy line loads on the new build, new line loads on the
  old build, and a round trip that sets the field to a **non-default** value.
- **An exhaustive soundness property** (EI.8) — the fitness twin of the regression side's
  parametrized early-stop test. It is RED today, which is the finding.
- **Integration through `run_loop`** (EI.9, EI.10) — required by
  [`code-quality.md`](../../../.claude/rules/code-quality.md) § "New components require an
  integration test through the production caller".

**Existing tests that will need attention.** Two early-stop tests must be re-authored (EI.8). The
`SelfPlayRecord` contract tests need explicit legacy and new-line cases, because dataclass equality
passes trivially when both sides carry equal defaults. Two mirror-record test factories hand-build
nine-key dicts. Two SKILL.md assertion tests pin the flag table. Treat any diff that merely adjusts
an assertion to match new behaviour as suspect and re-derive the intent.

**End-to-end verification** is EI.13 (smoke gate, 60 seconds, real components) then EI.14
(shadow-half soak, zero added games).

---

## Review status

- **`/plan-review --autofix` — run 2026-09-02.** Four independent lenses under proof discipline.
  Seven distinct Blockers found and fixed, several caught by more than one lens: retention placed in
  a concurrency-gated seam that never runs at the default; a smoke gate that required real
  StarCraft II; a patch-capture premise that was false because the collection it reuses is guarded
  on an unrelated default-off flag; two done-whens asserting against a gitignored file from inside a
  worktree; an internally unsatisfiable done-when; a soak host that does not exist; and an issue
  placeholder shape that would have silently corrupted every issue number at sync time. Three steps
  were escalated to `--reviewers deep` under stakes-aware routing.
- **`/plan-wrap` — run 2026-09-02.** Verdict `READY WITH GAPS: 7 gaps`, **0 Blockers**. Two schema
  summaries auto-applied (marked in place). All seven gaps have since been closed: the trace record
  shape and filename, the evaluation id's format and consumers, the outcome-token literal, the
  dashboard file-count owner, the base branch, EI.14's composed command, and the frontend seam's
  live observation.

## Next steps

`/repo-sync` to mint issues, which back-fills the 14 bare `**Issue:** #` fields.

**Do not dispatch a build.** `build-step`, `build-phase`, `build-queue` and `review-deep` are frozen
until the skill-mesh review-deep restoration seals; the marker is
`dev/.claude/task-state/freeze.json`. `/repo-sync` itself is not frozen, but minting issues for a
phase nobody can build is an operator call — the plan is equally valid synced later.
