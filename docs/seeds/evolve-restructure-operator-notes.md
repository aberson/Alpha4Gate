# Evolve restructure — operator notes (seed)

---
provenance: USER — verbatim operator writing, captured 2026-08-06
status: seed for a plan-feature conversation on restructuring /improve-bot-evolve
note: message arrived truncated mid-sentence at idea (ii); completion pending
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
provenance: AGENT — verified against primary source 2026-08-06 (5 parallel readers, file:line anchored)
rendered: README.md § "The self-play arena" (documentation/images/evolve-arena-{light,dark}.svg, added 2026-08-10)
---

Corrections to the operator's 11-step model, keyed to their numbering:

1. **Seed games — partially right.** 3 parent-vs-parent mirror games before the initial pool prompt (`src/orchestrator/evolve.py:1786`); pool refreshes skip them (`skip_mirror=True`, `scripts/evolve.py:4862`).
2. **In-game advisor — wrong (biggest miscorrection).** Evolve's proposer is a one-shot `claude -p` call after mirror games, NOT the live in-game advisor (that's improve-bot-advised; disjoint code paths sharing only guiding-principles.md). Prompt inputs: mirror summary, 4KB log tails, paths-only source tree (no code contents), guiding-principles.md, optional priors block (`evolve.py:1827-1848`). Never reads training.db or source. Model: `EVOLVE_POOL_MODEL` env → `opus` default.
3. **Top-n — partially right.** Exactly `--pool-size` (10) ranked imps requested; no top-n cut — every active imp is fitness-tested each generation. Rank drives stack order + optional `--budget-fit` trim.
4. **One per version — roughly right.** Each imp → throwaway scratch snapshot (`cand_<uuid>`, pointer untouched), applied by an Opus dev sub-agent (≤3 attempts, ruff + mypy-strict + out-of-scope gates).
5. **Games vs parent — right.** 5 games, early-stop at strict majority.
6. **Quality gate — right.** Pass ≥3/5, exactly-one-short = "close" (retries ≤3 generations), else evicted. Raw ~50% noise floor — EJ fixes cover the regression gate, not this one.
7. **Aggregation — second big correction.** No diff merge: fresh vN+1 snapshot (flips bots/current pointer as side effect), then each winner RE-WRITTEN from text by the same Opus sub-agent, sequentially in rank order, onto the drifting snapshot (`scripts/evolve.py:1889-2089`). Only immediate check: 30s `import bots.vN+1.bot`.
8. **Inverted.** Promotion commits BEFORE regression: `[evo-auto]` commit, then regression (vN+1 ⚔ vN ×5); failure → `git revert` of the promote SHA. Commit-then-revert, not gate-then-commit. Default majority rule; EJ.3 one-sided posterior (P(worse)≥0.85, fail-open <4 decided) opt-in.
9. **Uncertain step — exists, opt-in.** Baseline gauntlet (EL.2) plays registered frozen previous versions from data/baselines.json, post-promotion pre-regression. LOG-ONLY by default; `--panel-floor` arms sweep-loss (0 wins vs any anchor) rollback.
10–11. **Right**, modulo the inversion. Pool refresh tops to 10 (EJ.1 priors-exclusion, EJ.5 title-dedup opt-in); loop ends on wall-clock / pool exhaustion / dashboard stop.

Cross-cutting: all six EJ noise-floor flags default OFF (byte-identical bare invocation).

## Agent assessment of idea (i) — high-powered model at stack-apply

- Seam exists: `spawn_dev_subagent(model='opus')` keyword threaded to `claude -p --model` (`src/orchestrator/evolve_dev_apply.py:237`); no caller sets it. Stack-apply is serial, main-process, once per promoted generation → escalation is a ~5-line `functools.partial` at the stack-apply call site (mirrors the `screen_null_diff` partial pattern, `scripts/evolve.py:3591-3607`).
- Sharper version: model escalation alone doesn't fix the structural weakness — stack-apply is N blind sequential re-writes, not an aggregation. Proposed: `--stack-mode sequential|unified` where unified hands ALL winners to one high-tier model to write one coherent merged change (+ per-imp manifest to preserve attribution).

## Other levers noted for adversarial-review rounds

- Proposer is starved (paths-only tree, 3 mirror games) — feeding it code/history may beat any gate change.
- Gate 1 keeps the raw strict-majority-of-5 noise floor (EJ.3 covers gate 2 only).
- Gauntlet toothless by default — could be promoted to a standing anti-degenerate gate.
- Six per-run EJ flags → a restructure could pick a blessed default profile.

## Pending

- Operator's idea (ii) — message truncated at "Use a SC2 version of the 'judge" — still awaiting completion.
