# Evolve restructure — operator notes (seed)

---
provenance: USER — verbatim operator writing, captured 2026-08-06
status: seed for a plan-feature conversation on restructuring /improve-bot-evolve
note: idea (ii) arrived truncated; round 1 (2026-09-02) turned it into a six-option decision card
  at the end of this file — the thread now needs ONE letter from the operator, not an essay
---

## Operator's understanding of the current process (verbatim)

> This is a rough version of my understanding of how this works, feel free to correct or clarify:
> (1) Seed games
> (2) A learned advisor Claude observers during the games and write suggestions during the games for improvements.
> (3) Those improvements are listed and sorted, the top n are chosen
> (4) These improvements are implemented via a testing harness, one for each version
> (5) The evolution starts and bots play a series of games against the parent
> (6) The bots that win enough (meet the quality gate) are move on and the ones that don't are discarded
> (7) These bots are aggregated into an update and then play against the parent
> (8) If the new version wins, it becomes a parent candidate, if it loses, it's discarded and the process starts again.
> (9) (uncertain on this step) The new purposed parent then plays against a pervious parent (multiple previous parents?) to ensure we don't optimize for a degenerate behavior.
> (10) If the parent candidate passes all of these, it gets promoted and becomes the new parent.
> (11) End this round and go back to the beginning.

## Operator's improvement ideas (verbatim)

> The idea I wanted to try:
> (i) Escalating at step (7) to have a high powered model aggregate and write the change. This is done infrequently and the most impactful step, so could be worth using a high powered model at this point.
> (ii) Use a SC2 version of the "judge
>
> [message truncated here — idea (ii) incomplete]

## Process notes

- Plan of record for this thread: iterate understanding → operator suggestions → adversarial-reviewer suggestions → repeat a few rounds → `/plan-feature` to finalize the build plan.

---

## Verified pipeline map — "11 steps vs. reality"

---
provenance: AGENT — verified against primary source 2026-08-06 (5 parallel readers, file:line anchored);
  re-verified 2026-09-02 — every mechanism claim still holds. `src/orchestrator/*` anchors are
  byte-unchanged; the three `scripts/evolve.py` anchors drifted +222 lines from the Phase EV
  commits and have been corrected in place
rendered: README.md § "The self-play arena" (documentation/images/evolve-arena-{light,dark}.svg, added 2026-08-10)
---

Corrections to the operator's 11-step model, keyed to their numbering:

1. **Seed games — partially right.** 3 parent-vs-parent mirror games before the initial pool prompt (`src/orchestrator/evolve.py:1786`); pool refreshes skip them (`skip_mirror=True`, `scripts/evolve.py:5084`).
2. **In-game advisor — wrong (biggest miscorrection).** Evolve's proposer is a one-shot `claude -p` call after mirror games, NOT the live in-game advisor (that's improve-bot-advised; disjoint code paths sharing only guiding-principles.md). Prompt inputs: mirror summary, 4KB log tails, paths-only source tree (no code contents), guiding-principles.md, optional priors block (`evolve.py:1827-1848`). Never reads training.db or source. Model: `EVOLVE_POOL_MODEL` env → `opus` default.
3. **Top-n — partially right.** Exactly `--pool-size` (10) ranked imps requested; no top-n cut — every active imp is fitness-tested each generation. Rank drives stack order + optional `--budget-fit` trim.
4. **One per version — roughly right.** Each imp → throwaway scratch snapshot (`cand_<uuid>`, pointer untouched), applied by an Opus dev sub-agent (≤3 attempts, ruff + mypy-strict + out-of-scope gates).
5. **Games vs parent — right.** 5 games, early-stop at strict majority.
6. **Quality gate — right.** Pass ≥3/5, exactly-one-short = "close" (retries ≤3 generations), else evicted. Raw ~50% noise floor — EJ fixes cover the regression gate, not this one.
7. **Aggregation — second big correction.** No diff merge: fresh vN+1 snapshot (flips bots/current pointer as side effect), then each winner RE-WRITTEN from text by the same Opus sub-agent, sequentially in rank order, onto the drifting snapshot (`_stack_apply_and_promote`, `scripts/evolve.py:2111`). Only immediate check: 30s `import bots.vN+1.bot`.
8. **Inverted.** Promotion commits BEFORE regression: `[evo-auto]` commit, then regression (vN+1 ⚔ vN ×5); failure → `git revert` of the promote SHA. Commit-then-revert, not gate-then-commit. Default majority rule; EJ.3 one-sided posterior (P(worse)≥0.85, fail-open <4 decided) opt-in.
9. **Uncertain step — exists, opt-in.** Baseline gauntlet (EL.2) plays registered frozen previous versions from data/baselines.json, post-promotion pre-regression. LOG-ONLY by default; `--panel-floor` arms sweep-loss (0 wins vs any anchor) rollback.
10–11. **Right**, modulo the inversion. Pool refresh tops to 10 (EJ.1 priors-exclusion, EJ.5 title-dedup opt-in); loop ends on wall-clock / pool exhaustion / dashboard stop.

Cross-cutting: all six EJ noise-floor flags default OFF (byte-identical bare invocation).

## Agent assessment of idea (i) — high-powered model at stack-apply

- Seam exists: `spawn_dev_subagent(model='opus')` keyword threaded to `claude -p --model` (`src/orchestrator/evolve_dev_apply.py:237`); no caller sets it. Stack-apply is serial, main-process, once per promoted generation → escalation is a ~5-line `functools.partial` at the stack-apply call site (mirrors the `screen_null_diff` partial pattern, `scripts/evolve.py:3817-3828`; the single injection site is the `stack_apply_fn(` call at `scripts/evolve.py:4609`).
- Sharper version: model escalation alone doesn't fix the structural weakness — stack-apply is N blind sequential re-writes, not an aggregation. Proposed: `--stack-mode sequential|unified` where unified hands ALL winners to one high-tier model to write one coherent merged change (+ per-imp manifest to preserve attribution).

## Other levers noted for adversarial-review rounds

- Proposer is starved (paths-only tree, 3 mirror games) — feeding it code/history may beat any gate change.
- Gate 1 keeps the raw strict-majority-of-5 noise floor (EJ.3 covers gate 2 only).
- Gauntlet toothless by default — could be promoted to a standing anti-degenerate gate.
- Six per-run EJ flags → a restructure could pick a blessed default profile.

## Adversarial rounds 1 and 2 — done 2026-09-02; converged

Round 1: five independent restructure proposals (proposer signal quality, gate-1 noise floor,
stack-apply aggregation, SC2 judge, pipeline architecture), each refuted by an independent
four-lens critic, then synthesized into a 30-row decisions table and three fault lines.
Round 2: each of the five resulting propositions argued by an independent defender AND refuter
under a rule that arguments must **compute, not assert**, then adjudicated.

- Round 1 record: [`documentation/investigations/evolve-restructure-round1.md`](../../documentation/investigations/evolve-restructure-round1.md)
- Round 2 record, **authoritative where the two disagree**, and the source of the final
  `/plan-feature` scope: [`documentation/investigations/evolve-restructure-round2.md`](../../documentation/investigations/evolve-restructure-round2.md)

The adjudicator's explicit verdict is that the conversation has **converged and round 3 is
unnecessary**. Round 2 also re-checked round 1's citations and found 19 wrong; its section 3 is
now the one owner of file:line truth.

**Round 2's decisive finding — gate 1 selects improvement *texts*, not code, 100% of the time.**
Two commits (`f2eb564` and `ce8545f`) both create `bots/v4` from parent `v3`, hours apart on
2026-04-29, from the **same improvement text** — and produce materially different bots. One was a
single-improvement stack. The fitness games score a candidate that is then deleted; a second,
independent authoring is what ships. Worse, in the shipped `v8`→`v9` diff a later stacked
improvement **deleted an earlier one's fitness-tested code**. `v9` was not rolled back; it is an
ancestor of production `v13`.

**Round 1's central finding, which still stands and reframes idea (ii):** the evolve path records
almost nothing about the games it plays.

- Per-game persistence is nine fields — win/loss, versions, map, duration, seat swap
  (`src/orchestrator/contracts.py:121-137`). No gameplay at all.
- The rich per-step telemetry format **exists and is never attached** in self-play:
  `bots/v13/__main__.py:276` constructs `Alpha4GateBot()` with no `logger=`. The solo and
  batch paths do attach one and produce ~188 rows for a 6-minute game.
- The one per-seat console log is **destroyed every game**: `src/orchestrator/selfplay.py:292`
  writes `logs/selfplay_{version}_{role}.log` and burnysc2 opens it `"w+"`, so each game
  truncates the last. The proposer's entire empirical picture is a 4000-byte tail of that
  single surviving file.
- No replay is saved for an evolve game, `--result-out` is a dead contract with zero
  consumers, and the fitness-winning candidate is `rmtree`'d before anything reads it.

So five proposals argued past each other because **there is no measurement against which any
of them could be decided.** Buying those measurements is what the final scope plans first.

Also confirmed, and load-bearing: **post-fitness** reallocation never saves games. The pool
refills to `--pool-size` every generation (`scripts/evolve.py:5052-5056`), so evicting a
candidate just costs a fresh dev-apply. Three separate proposals booked "games saved" and the
same arithmetic killed all three. Round 2 found the one real exception: a **pre-fitness** screen
does reduce games, because it fires before any game is played and leaves the slot count
unchanged — and `--screen-null-diff` already ships in exactly that seat, default OFF, never yet
run armed.

## Idea (ii) — decision card, awaiting ONE letter

The message was truncated at *"(ii) Use a SC2 version of the 'judge"*. Rather than guess, round 1
enumerated six concrete readings and costed each against what evidence actually exists today.

| Pick | The judge is… | Watches | Extra SC2 games | Blocked on |
|---|---|---|---|---|
| **A** | a vision judge on rendered frames | pixels | 0 | frame capture does not exist; the viewer reparents SC2's own HWND, so the pixels are never in a pygame surface |
| **B** | a loss-autopsy judge feeding the proposer | per-step telemetry | 0 | `GameLogger` not attached in the self-play path |
| **C** | a code judge screening each candidate diff before it plays | the diff | **−4 per catch** — round 2 confirmed this is the *only* seat on the board that genuinely reduces games, and `--screen-null-diff` already ships in it (default OFF, never yet run armed) | nothing — evidence exists today |
| **D** | an anti-degenerate referee (the operator's own step-9 worry) | frozen-anchor panel results | 0 | `data/baselines.json` absent (no anchors registered); and "degenerate" has no golden set — round 2 measured duration, step-count and terminal-marker classes at AUC ~0.47 against a shuffled-label anchor of 0.50, i.e. null |
| **E** | a judge panel replacing the win-count gate | diff/telemetry | **−45** | collides head-on with the SKILL.md safety rail and measurement-validity |
| **F** | a reviewer of the merged stack before the commit | the vN+1 diff | 0 | nothing — evidence exists today |

**Agent's best guess: F, with B as its evidence supply — confidence ~45%.** Reasoning: idea (i)
is *about step 7* and idea (ii) is the very next clause; idea (i)'s own economic argument
("done infrequently and the most impactful step") applies identically to a judge there, and
step 7's only current check is a 30-second `import`. The live competitors are **D** (the
operator raised degenerate behaviour themselves, in their own step 9) and **A** (the judge
skills the operator has actually used are *visual*).

> ### The question
>
> **When you wrote "use a SC2 version of the judge" — was the judge meant to look at the
> *code* (C, F) or at the *game* (A, B, D)? One letter, or two for a pair (e.g. `F+B`).**

**If the answer is A, B or D, the plan gains a mandatory Step 0** — attach a per-seat telemetry
sink to the self-play entry point — because none of those three can be built, let alone
calibrated, until an evolve game leaves a trace behind. That Step 0 is already the first item
of the round-1 recommended scope, so the plan is being built in the right order either way.

**One rail that constrains every answer:** `.claude/skills/improve-bot-evolve/SKILL.md:574` —
"Fitness + stack-apply import gate + regression are non-negotiable" — and
`.claude/rules/measurement-validity.md` § score the production artifact. Any judge here is
**advisory**, never a gate, unless the operator explicitly retires that rail.

## Where the thread goes next

1. **Operator answers the one letter above.** That is the only hard blocker left on idea (ii).
   Round 2 answered two of round 1's other operator questions by measurement and withdrew them.
2. `/plan-feature` on the idea-(ii)-independent core (round 2 section 4), which is a prerequisite
   for three of the six readings anyway. Round 3 is **not** needed — the adjudicator's finding is
   that the two remaining disagreements cannot be settled by more argument, only by data the plan
   is designed to collect.

**Five smaller operator questions**, each a one-liner, all in round 2 section 5: the soak slot
(ride a queued soak's flag-off shadow half, or take a slot); whether to arm a stack-size cap of
one; whether idea (i) ships as model escalation or as the sharper `--stack-mode unified`; whether
an evolve run has ever been interrupted *after* promoting in that same run; and whether the
lineage-registry adjunct ships inside this plan.

## Superseded

- The original "Pending" note ("idea (ii) still awaiting completion") is now the decision card
  above. The thread is no longer blocked on an essay — it is blocked on one letter.
