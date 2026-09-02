# Evolve restructure — adversarial round 1

---
provenance: AGENT — 12-agent adversarial workflow, 2026-09-02 (5 divergent restructure
  proposals, each refuted by an independent four-lens critic, plus a dedicated study
  completing the operator's truncated idea (ii), then synthesized). ~1.8M subagent tokens,
  0 errors. Every load-bearing claim is anchored to file:line and was re-verified during
  synthesis.
status: ROUND 1 COMPLETE — feeds `/plan-feature`. Round 2 agenda is section 5.
seed: `docs/seeds/evolve-restructure-operator-notes.md` (operator's verbatim 11-step model,
  the verified pipeline map, ideas (i) and (ii), and the idea-(ii) decision card)
blocked_on: one letter from the operator — see the decision card in the seed, section 4 here
---

> ## ⚠ Corrected by round 2 — read that first
>
> **[`evolve-restructure-round2.md`](evolve-restructure-round2.md) is authoritative wherever it
> disagrees with this document.** Round 2 re-checked this record's citations and found **19 of
> them wrong**; its section 3 is the ONE owner of file:line truth for both documents. Do not
> scrape anchors from here without checking that table.
>
> Round 2 also overturned four substantive conclusions below:
>
> - **Fault line 1 is RESOLVED, against this document's framing.** Gate 1 selects improvement
>   *texts*, not code — 100% of the time, at every stack size. Row A6's "at K=1 the promoted tree
>   *is* the tested tree" is **false**; rows A6 and N1 are **complements, not substitutes**.
> - **Row A8 is not zero-cost and not byte-identical.** It adds ~4 games per generation and
>   changes the fail/close bucket on ~10% of evals. It moves behind a flag.
> - **Row N1's pre-registered X-threshold decision rule is DELETED.** Both of its branches point
>   the same way, so it was never a gate; N1 is re-scoped to a log-only artifact.
> - **Fault line 3's numbers are invalid.** They were computed over a corpus where 36.7% of
>   candidate traces predate the log line the features are read from. The fault line stays open.
>
> Round 2's verdict is that the conversation has converged and **round 3 is unnecessary**. The
> final `/plan-feature` scope is round 2's section 4, not this document's section 3.

---

## Why this round exists

The operator opened a planning thread on 2026-08-06 to restructure `/improve-bot-evolve`.
Plan of record: iterate understanding → operator suggestions → **adversarial-reviewer
rounds** → `/plan-feature`. The verified pipeline map landed 2026-08-06; the thread then
stalled because the operator's second improvement idea arrived truncated mid-sentence.

This is round 1 of the adversarial rounds. It was run without waiting for that idea, on the
reasoning that four of the five angles are independent of it and the fifth — the SC2 judge —
is better served by enumerating the readings than by guessing one.

**The round's headline finding is not a proposal. It is that the evolve path records almost
nothing about the games it plays**, so five proposals argued past each other with no
measurement that could decide between them. That is what the recommended scope buys first.

---

**Status:** synthesis of 5 proposals + 5 adversarial critiques + the idea-(ii) completion study. Feeds `/plan-feature`.
**Anchors:** every load-bearing claim below was re-verified against source in this session unless marked *(unverified — from input)*.

## 0. What I checked, and where the inputs conflict

Re-verified independently (all hold exactly):

| Claim | Anchor |
|---|---|
| Pool refills to `--pool-size` every generation | `scripts/evolve.py:5053-5056` — `active_after_refresh = _count_active(...)` / `delta = args.pool_size - active_after_refresh` / `if delta > 0:` |
| Fitness scratch is destroyed | `src/orchestrator/evolve.py:675-681` — `finally:` … `_safe_rmtree(cand_dir)`, comment *"Scratch is always discarded. Composition re-snapshots from parent."* |
| `wins_parent` is NOT persisted | `PerItemState` (`scripts/evolve.py:826-839`) has `status, fitness_score, retry_count, first/last_evaluated_against, consecutive_null_diffs` — no `wins_parent`; `_apply_fitness_outcome` writes `st.fitness_score = [result.wins_candidate, result.games]` (`:1935`) |
| EJ.5 already implements the dedup predicate | `scripts/evolve.py:1114` `_dedup_fresh_imps` — *"ratio >= threshold against the normalized title of ANY imp already in pool (active/stacked/evicted — it was already tried this run)"* |
| Refresh generations have no mirror games | `scripts/evolve.py:5084` `"skip_mirror": True` |
| Proposer sees a 4KB tail of one shared log | `src/orchestrator/evolve.py:1374-1396` `_read_log_tails`, `max_bytes=4000`, glob `selfplay_{parent}_*.log`; written to `logs/selfplay_{version}_{role}.log` at `src/orchestrator/selfplay.py:292` |
| Self-play bot gets no `GameLogger` | `bots/v13/__main__.py:276` — `bot = Alpha4GateBot()` |
| Prompt/tree contract contradicts itself | `src/orchestrator/evolve.py:1461-1464` demands *"Name the file and the specific function or line"*; `_list_source_tree` (`:1345`) returns bare paths |
| Stack sizes | `git log --grep=evo-auto`: 18 promoted stacks {1×7, 2×4, 3×4, 4×1, 5×2} → mean **2.28**, **11/18 (61%) are N≥2** |
| Model-escalation seam is unwired | `spawn_dev_subagent(..., model: str = "opus")` at `evolve_dev_apply.py:234`; the only `model=` in the evolve path is its own internal pass-through at `:334`. Single injection site: `stack_apply_fn(` at `scripts/evolve.py:4609` |
| Gauntlet has no `on_game_end` | zero matches in `src/orchestrator/evolve.py:940-1050` |
| `--resume` compares the wrong field | `scripts/evolve.py:4061` `if pool_parent != parent_start:` |
| State files | `data/evolve_results.jsonl` = **0 bytes**; `data/evolve_run_control.json` = `{"pause_after_round": false, "stop_run": true}` (armed, stale); `data/baselines.json` and `data/lineages.json` **absent**; `scripts/lineage.py` **absent** |
| No `--stack-size` / `--fitness-alpha` exist | six EJ flags at `scripts/evolve.py:322/364/379/450/468/485` only |
| The LLM-in-gate rail | `.claude/skills/improve-bot-evolve/SKILL.md:574` *"Fitness + stack-apply import gate + regression are non-negotiable"*; `evolve-judging-plan.md` §1 *"nothing here adds an LLM judge to the gate path"* |

**Conflicts in the inputs, and which side I believe:**

1. **Waste figure (signal-quality).** Proposer: 91/175 = 52%, 9 distinct ideas. Critic: 11/22 = 50% at the proposal's own `difflib ≥ 0.85` rule, and 76/175 = 43% net of designed `_RETRY_CAP` retries. **Believe the critic** — it applied the pre-registered rule mechanically; the proposer fuzzy-merged pairs its own threshold does not merge (0.557, 0.281).
2. **Stack-apply wall clock (aggregation).** Proposer: 5 imps × 3 attempts × 900s ≈ 3.75h. Critic + investigation: 2-30 min per promoting generation; 900.0 and 3 are ceilings, mean stack is 2.28. **Believe the critic.**
3. **Replay corpus (gate-1).** Proposer specified `evolve_results.jsonl`; it is 0 bytes and truncated per fresh run (`scripts/evolve.py:1888-1890`). **Believe the critic** — `selfplay_results.jsonl` (240 reconstructable evals) is the only runnable source.
4. **Interruption rate (architecture).** Proposer: ~330 games reclaimable. Critic: three abnormal terminations in the whole corpus, all with zero promotions, no `evolve: resumed from` line anywhere. **Believe the critic**, with the caveat that this is absence-of-evidence over a small corpus — the operator can falsify it from memory (see decision point 4).
5. **Seed-doc anchors are stale.** `docs/seeds/evolve-restructure-operator-notes.md:47` cites `skip_mirror` at `scripts/evolve.py:4862`; it is at **:5084**. Line 62 cites the partial pattern at `:3591-3607`; it is at **:3821-3828**. The verified pipeline map's *content* is right; its line numbers need a refresh before `/plan-feature` scrapes them.
6. **`documentation/investigations/soak-volume-statistical-robustness-investigation.md` is a skeleton** — line 3: *"Status: OPEN — investigation skeleton, no findings yet."* Nothing in this round may cite it as evidence. It is a required read that supplies zero facts.

---

## 1. Decisions table

Sorted ADOPT → ADOPT-NARROWED → DEFER → NEEDS-OPERATOR → REJECT.
Column *Δgames* = extra SC2 games per generation.

| # | Change | Angle | Verdict | Strongest FOR | Strongest AGAINST | Size | Δdefault? | Δgames | Unblocks |
|---|---|---|---|---|---|---|---|---|---|
| A1 | Per-game self-play log paths (kill the shared `logs/selfplay_{version}_{role}.log` handle) | signal-quality | **ADOPT** | Two concurrent workers share one path that burnysc2 opens `"w+"` (`selfplay.py:292` + `sc2/proxy.py:187`), so every game destroys the previous game's only trace — evidence destruction independent of any prompt or gate question | It edits the production self-play path used by solo/batch too; log volume grows and needs a retention cap | S | Yes (filenames, volume) | 0 | Trace parser, A/A harness, every game-side reading of idea (ii) |
| A2 | `src/orchestrator/trace.py` — pure stdlib trace parser + mechanical feature extraction | sc2-judge | **ADOPT** | The only change in the entire round with **zero objections from any critic**; leaf module, no `bots.*` import (MetaPathFinder rule), changes no behaviour | Worthless until A1 lands — today there is one surviving trace per version/role | S | No | 0 | A3, N4, D4 |
| A3 | Replace the 4KB byte-tail with a correctly-labelled, side-labelled evidence block; state honestly that refresh generations have no mirror games | sc2-judge | **ADOPT** | The most expensive LLM call in the pipeline is fed a tail of one overwritten parent-side log, and on refresh (`skip_mirror=True`, `:5084`) that tail is a leftover fitness game against some prior candidate, presented as mirror evidence. Correctness, not enhancement — needs no noise floor cleared | Default-visible prompt change: every prompt golden updates (the EJ.1 precedent for a deliberate golden update applies) | S | **Yes** (one deliberate golden) | 0 | The "proposer is starved" lever without a 5× prompt |
| A4 | Persist `wins_parent` in `PerItemState` | gate-1 + architecture | **ADOPT** | Verified censored — every passer records `[wins_candidate, games]`; three independent designs (stack B, D, F) died on the same dead statistic (`evolve-judging-alternatives.md:103`) | No consumer ships in this scope, so it is speculative persistence | S | No (additive, defaulted) | 0 | All margin-based ordering |
| A5 | `--stack-static-gate`: whole-package `mypy --strict` on vN+1 before the commit | aggregation | **ADOPT** | Per-imp mypy validates changed files only (`evolve_dev_apply.py:820/847`), so cross-imp signature drift is invisible; failures reuse `_cleanup_on_error` before any commit and never touch the fragile revert path. Investigation's "Second" pick, deterministic, zero LLM | Needs a drop-lowest-rank rule on repeat static-fail or it livelocks the bench-retry ladder | S | No (flag off) | 0 | Safe multi-imp stacks; the deterministic tier of any "aggregation reviewer" |
| A6 | `--stack-size K` (default 0 = unlimited) | gate-1 + aggregation | **ADOPT** | The one change that bounds junk-per-commit independently of any unmeasured base rate; at K=1 the promoted tree *is* the tested tree for the 61% of promotions that are currently N≥2 — closing the validity hole without building replay | At K=1 it cuts imps promoted per generation, and promotions-per-hour is the binding throughput constraint on a single-flight machine | S | No | 0 | Fault line 1 without capture/replay |
| A7 | `--stack-model MODEL` (operator idea (i), literal) | aggregation | **ADOPT** | Seam verified and unwired: no production caller sets `model=`; one `functools.partial` at the single `stack_apply_fn(` call site (`:4609`) leaves the fitness path at `:4434` untouched | Escalation alone does not fix the structure — stack-apply is N blind sequential re-writes, not an aggregation (the operator's own notes say this at seed line 63) | S | No | 0 | Idea (i), cheaply |
| A8 | Derive gate-1's early-stop from the active rule (lock only when accept AND close are both unreachable) | gate-1 | **ADOPT** (prerequisite) | The hardcoded stop-at-majority (`evolve.py:622-631`) evicts still-reachable `close` candidates and is wrong at any n≠5; already published as must-land-before-shipping | It is a prerequisite of a bar change this scope does not ship, so alone it buys nothing today | S | No under `majority` | 0 | Any future `--fitness-alpha` |
| A9 | Step-0 zero-game replay over `data/selfplay_results.jsonl` (240 evals), not `evolve_results.jsonl` | gate-1 | **ADOPT** | `evolve_results.jsonl` is 0 bytes and self-truncating; the critic already ran the corrected replay (majority 92/240 = 38.3%, k=4/5-equivalent 31/240 = 12.9%, dead-generation rate 0.008 → 0.251) | It is analysis, not code — an afternoon's work that does not need a plan step | S | No | 0 | D2's go/no-go |
| N1 | Patch capture, **log-only**, baseline = candidate **pre-edit** vs post-edit | aggregation | **ADOPT-NARROWED** | The only instrument that answers fault line 1: what fraction of the time is the re-authored imp the same change that was measured? Zero games, zero gate effect | As originally specced (parent→candidate diff) it fails `git apply --check` on 33 of 58 files every time — `_rewrite_imports` puts 183 `bots.v13` tokens across the tree and the normalization is one-sided | M | No | 0 | D1, D2, FL1 |
| | *Narrowing:* reuse `_collect_candidate_py_content` (`evolve_dev_apply.py:629`, already collected for EJ.2); add `patch_path` to `to_json`/`from_json` **and** the worker's dispatcher-visible path, with an integration test through the `--concurrency>1` entry point (`code-quality.md` § new components / § key-shape consumers) | | | | | | | | |
| N2 | Trace capture at the two **existing** `on_game_end` seams (fitness `:626`, regression `:827`), fail-open, retention-capped | sc2-judge | **ADOPT-NARROWED** | Fail-open by construction (`selfplay.py:744-752` swallows); prerequisite for every game-side reading of idea (ii) | The repo-global path is keyed on (version, role), so under `--concurrency > 1` the **parent** channel is spliced across concurrent workers — a plausible-looking fabricated trace, the dangerous failure mode | S | No | 0 | N4, D4, idea (ii) A/B/D |
| | *Narrowing:* hard-disable under `--concurrency > 1`; drop the gauntlet seam entirely (see R10) | | | | | | | | |
| N3 | `--proposer-context signatures` — `ast`-derived signature index (~8KB) of the decision-core files | signal-quality | **ADOPT-NARROWED** | Repairs a verified self-contradicting contract: the prompt demands a function or line name while the tree block supplies 58 bare paths. ~8KB fits inside the hardcoded `timeout=600` with margin | No evidence that a better-specified `concrete_change` yields better imps — it fixes a contract bug, it does not buy a measured win | S | No (flag off) | 0 | — |
| N4 | Fitness-denominator correction: stop counting no-trace games as played | sc2-judge | **ADOPT-NARROWED** | Tautology-free, mechanical, zero-LLM, and has a clean known-good anchor (a complete trace) — it corrects a gate *input* without inventing a quality judgment | The "no result line" half mislabels 8.3% of candidate and ~30% of parent traces as crashes *(unverified — from critic's measurement)* | S | Yes if armed | 0 | Honest 5-game denominators |
| | *Narrowing:* key on status chunks / terminal-state markers only, never the result line; ship log-only first | | | | | | | | |
| N5 | `--resume` reads `parent_current` from `data/evolve_run_state.json`; extend that payload with `promoted_titles_in_run`, `lineage_heads`, `phase_marker` | architecture | **ADOPT-NARROWED** | SKILL.md's documented "prune the conflicting imp and `--resume`" recovery is genuinely broken after any promotion — an operator-workflow defect independent of crash rate | Measured zero post-promotion interruptions in the entire recorded corpus, so the games-reclaimed economics are exactly zero | S | No | 0 | Resume after promotion |
| N6 | Queue-unblock adjunct: `scripts/lineage.py` (create/list/retire/**reset**) + launcher `--generations 0 --budget-fit` | architecture | **ADOPT-NARROWED** (adjunct) | `data/lineages.json` is absent and there is **no creation path**, so EL.7 (#279) — one of the seven queued soaks — is unrunnable as written; the launcher omits `--generations`, truncating observatory soaks to one generation | A non-empty registry latches multi-lineage scheduling ON for every subsequent bare invocation — a one-way default change with no disengage flag | S | **Yes** (operator-initiated) | 0 | EL.7; the observatory run-evolution button |
| D1 | Deterministic `git apply` replay at stack time | aggregation | **DEFER** | Makes the single-imp promotion path byte-faithful and reclaims 2-30 min of sub-agent wall-clock per promoting generation | Does nothing for the 61% of promotions that are N≥2, where "byte-identical" is not even computable — the metric degenerates to "did `git apply` exit 0" | M | No | 0 | Defer until N1's fidelity histogram exists |
| D2 | `--fitness-alpha k` (gate-1 bar at k=4/5) | gate-1 | **DEFER** | Already designed, scored 8, the fitness-lane top pick, and the EJ plan's own declared *"next candidate after this phase"* — one function, zero new games | Replayed on 240 historical evals: accept 38.3% → 12.9%, and P(a 10-imp generation yields **zero** passers) 0.008 → 0.251, a 30× rise in dead generations on the scarce machine | S | No (flag off) | 0 / −1 | Defer behind FL1 + a pre-registered dead-generation ceiling |
| D3 | History block (failed-title list) in the proposer prompt | signal-quality | **DEFER** | Investigation A1's descoped *failed*-list half; EJ.1 shipped only the promoted half | EJ.5 `--refresh-dedup` already drops near-duplicates pool-wide using the identical `difflib ≥ 0.85` predicate and returns ~51% of the frozen soak's games. The only residual is accept-short pool shrinkage | S | No | 0 | Defer until measured against `--refresh-dedup` on `slots_short_at_dispatch`, not against legacy |
| D4 | LLM tier run **offline** over captured traces after a soak | sc2-judge | **DEFER** | Removes the entire wall-clock objection and the entire gate-path rail objection at once | No calibratable label exists (fault line 3), so it can only ever be operator-facing narration | M | No | 0 | — |
| D5 | `--stack-mode unified` — one high-tier model writes one merged change (+ per-imp manifest) | operator notes | **DEFER** | The operator's own sharper version of idea (i); directly attacks "N blind sequential re-writes" rather than escalating a blind rewrite | Uncosted, and it moves the promoted artifact *further* from anything a game scored | M-L | No | 0 | See decision point 5 |
| O1 | Which reading of idea (ii) ships | idea-(ii) study | **NEEDS-OPERATOR** | Code-side (C/F) works on evidence that exists today | Game-side (A/B/D) all require a Step 0 that builds the evidence first | — | — | 0 | The whole judge lane |
| O2 | Arm `--stack-size 1` as the profile | aggregation critic | **NEEDS-OPERATOR** | It is the cheapest complete closure of the composition validity hole | It trades promotions-per-generation, the binding throughput constraint | S | Yes if armed | 0 | FL1 |
| O3 | Attach a `GameLogger` (or per-seat telemetry sink) to the self-play entry point | idea-(ii) study | **NEEDS-OPERATOR** (contingent on O1) | The format already exists and is produced on the solo/batch path (`runner.py:336,477`); it is ~1 line at `bots/v13/__main__.py:276` plus a per-seat filename | Mandatory only if the operator answers A, B or D; pure cost if they answer C or F | S | Yes | 0 | Idea (ii) game-side |
| R1 | Full-source inlining into the proposer prompt (`--proposer-code-budget` 160k, graph-distance ordering) | signal-quality | **REJECT** | — | **Killed by the implementation-feasibility refutation:** 23 of 24 largest closure files sit at graph-distance 1 from `bot.py` (one enormous tie set, no tiebreak); `bot.py` alone is 41% of the budget, and the budget is exhausted before `micro.py`, `fortification.py`, `scouting.py`, `army_coherence.py`, `give_up.py` — the files the pool actually targets | L | — | 0 | — |
| R2 | Decided-games posterior at gate 1 (new module, `MIN_DECIDED_FITNESS = 3`) | gate-1 | **REJECT** | — | **Killed by the implementation-feasibility refutation:** the flagship 2-0-3 example fails **closed** and is evicted under the proposal's own floor, where today it buckets `close` and gets three retries. Compounded by the measurement-validity refutation: it changes behaviour on <4% of evals and inverts on 3-1-1 (P = 0.8125 < 0.85) | M | — | 0 | — |
| R3 | `--fitness-margin` from `winprob_heuristic` | gate-1 | **REJECT** | — | **Killed by the implementation-feasibility refutation:** the instrument lives inside the tree being evolved — `snapshot_current` copies `winprob_heuristic` into every candidate and every promotion — so margins are not comparable across a run, and the hash-and-disable guard kills the channel at the first promotion touching `learning/` | M | — | 0 | — |
| R4 | Rank-1 rollback granularity (return non-rank-1 members to active) | aggregation | **REJECT** | — | **Killed twice.** Cost-and-scarcity: reclaims exactly **zero** games — `delta = args.pool_size - active_after_refresh` refills the freed slots one-for-one (`:5053-5056`). Measurement-validity: `rank` is Opus's own expected-impact self-report, so the actually-toxic member is re-activated and re-promotes at the ~50-61% null rate | M | — | 0 | — |
| R5 | Arming a mechanical judge on the `close` bucket (eviction) | sc2-judge | **REJECT** | — | **Killed by the cost-and-scarcity refutation** (same refill arithmetic, plus a net wall-clock *loss*: the replacement imp costs a fresh dev-apply of up to 3 × 900s that a retry does not) **and the redundancy refutation** (the adopted A4 amendment is *"futility must bench, not evict"*, and `_apply_retry_bookkeeping` + `_RETRY_CAP = 3` already implement bench-with-cap) | S | — | 0 | — |
| R6 | `ever_attacked` / DEGENERATE semantic pre-gate | sc2-judge | **REJECT** | — | **Killed by the measurement-validity refutation:** the frozen parent never enters ATTACK in 12 of 30 of its own traces (40%) vs candidates' 49% — the known-good anchor fails `score(good) > score(garbage)` on data already on disk, for free | S | — | 0 | — |
| R7 | Inline `claude -p` judge tier in the generation loop | sc2-judge | **REJECT** | — | **Killed by the cost-and-scarcity refutation:** +18% to +88% wall-clock per generation at `timeout=600`, on the one machine with seven queued soaks — and it sits outside the fail-open `on_game_end` try/except, so a hang aborts someone else's soak. Also collides with `SKILL.md:574` and the EJ plan's own gate-path constraint | M | — | 0 | — |
| R8 | Gate-first promotion reorder (commit after regression) | architecture | **REJECT** | — | **Killed by the implementation-feasibility refutation:** under gate-first there *is* no promote SHA, so the phantom-promote guard prints `git revert <promote-sha>` — an instruction the operator cannot follow — and the phantom signature (disk pointer ≠ HEAD pointer) becomes the **default** state for an entire generation | M | — | 0 | *Cheap half survives:* record `last_promote_sha` in run state so the guard prints a real SHA |
| R9 | New 16-field run-checkpoint artifact | architecture | **REJECT** | — | **Killed by the implementation-feasibility refutation:** `data/evolve_run_state.json` already carries 9 of the 16 fields and is written beside every `write_pool_state`; a fourth durable copy of `parent_current` is exactly the drift `.claude/rules/code-quality.md` § One source of truth forbids. Narrowed form survives as N5 | M | — | 0 | — |
| R10 | Gauntlet trace-capture ("same three lines") | sc2-judge | **REJECT as specced** | — | **Killed by the implementation-feasibility refutation:** `run_baseline_gauntlet` contains **zero** `on_game_end` references (verified) — the callback must be created and threaded through first, a different and larger change | S→M | — | 0 | Re-scope as its own step if ever wanted |
| R11 | Judge panel replacing the win-count gate (study option E) | idea-(ii) study | **REJECT** | — | **Killed by `SKILL.md:574` and `measurement-validity.md` § "Score the production artifact, not a proxy"** — a judged diff is precisely the scored-transcript failure that picked a coder producing 100% no-diff output | L | — | −45 | — |
| R12 | `--conflict-policy merge` / semantic conflict deferral | aggregation | **REJECT** | — | **Killed by dependency:** it hangs entirely off the capture-layer detector that the feasibility refutation broke; the investigation independently scored the LLM conflict pre-screen 4/10 | M | — | 0 | — |

**One pattern the table makes visible:** the Δgames column is `0` on 28 of 30 rows. Every proposal that claimed to *save* games was refuted by the same refill arithmetic. Whatever the plan optimizes, it is not SC2 games per generation.

---

## 2. The three fault lines

### FL1 — Is gate 1 measuring the artifact that ships, or only selecting imp *texts*?

**Nobody in this repo knows.** This single unmeasured number decides A6, N1, D1, D2 and O2 — five of the most contested rows.

| Side | Evidence |
|---|---|
| **Gate-1 precision governs the tree** (proposers) | Gate 1 is the only per-imp empirical filter; its output list is exactly what `_stack_apply_and_promote` iterates. If the bar is a coin flip, junk is what gets re-authored |
| **Gate 1 is a text filter** (critics) | `run_fitness_eval` rmtrees the scored candidate in `finally` (`evolve.py:675-681`, comment *"Scratch is always discarded"*), then a fresh non-deterministic 900s × 3-attempt LLM apply per imp runs onto a *different, drifting* snapshot (`scripts/evolve.py:2219-2220`), gated only by a 30s `import`. Even a gate 1 with zero false accepts would commit code that was never played |
| **…and replay does not fix it either** (aggregation critic) | 11/18 promoted stacks are N≥2 (mean 2.28). For N≥2 the promoted tree is `parent + imp1..impN`, which no game ever scored, and "byte-identical to the tested code" is not even computable once imp 1 edits imp 2's file |

**Round-2 must resolve:** does the plan buy the measurement (N1's fidelity histogram, M, zero games) or the structural fix (A6 at K=1, S, zero games)? They are substitutes, not complements — at K=1, replay becomes unnecessary rather than enabling.

### FL2 — Is "games saved" a real currency at all?

Three independent proposals booked game savings; the same arithmetic killed all three.

| Side | Evidence |
|---|---|
| **Games are the scarce resource** (task premise, all proposers) | ~4 min/game, one run machine-wide, 5 games per eval, seven soaks queued |
| **Games per generation are structurally invariant** (critics) | `scripts/evolve.py:5053-5056`: any imp removed from the active set raises `delta` and a fresh imp refills the slot at the same ~4.1-game cost **plus** a new dev-apply of up to 3 × 900s. Evicting/deferring is a *reallocation with a wall-clock penalty*, never a saving |
| **But total games are still bounded** | `--budget-fit` (EJ.6) and pool exhaustion do cap a run; a shorter pool genuinely does return games at the *run* level even though it does not at the *generation* level — which is precisely the disagreement between the signal-quality proposer and its critic over EJ.5 |

**Round-2 must resolve:** the unit. If the currency is wall-clock LLM time and slot-allocation quality rather than games, then every cost column in this table is mislabelled, `--stack-model` and `--stack-mode unified` get *more* attractive (they spend LLM time at the rarest step), and every eviction-based proposal gets *less*. This also decides the soak-slot question: only the flag-OFF shadow half of a change is orthogonal to EJ.8, whose five declared comparison metrics (rollback rate, screen/dedup catch counts, games per generation, generations completed, promotions) are four-fifths perturbed by anything that touches stack composition or gating.

### FL3 — Can any new evolve signal be calibrated against a label this system produces?

| Side | Evidence |
|---|---|
| **Yes, calibrate against fitness buckets** (sc2-judge proposer) | 418 joined traces; `ever_attacked` shows a 0.225 bucket spread; 132 resign-marked traces at WR 0.000 as a garbage anchor |
| **No — the label is structurally poisoned** (critic) | The early-stop (`evolve.py:622-637`) means the last game is deterministically won by the clinching side, so 209/418 traces come from the deciding game and a trivial *who-won-this-game* bit predicts the bucket at 0.484 spread — double any semantic feature. Stratified, `ever_attacked` is +0.302 on wins and **−0.036 on losses** — null exactly in the close/fail stratum where an arm would fire. The known-good anchor also fails: the frozen parent never attacks in 40% of its own traces vs candidates' 49% |
| **And the investigation said so first** | *"a perfect null-detector's rejects 'fitness-pass' ~50% of the time, so the naive 0.20 kill threshold systematically kills the best judges"* (`evolve-judging-alternatives.md`, Judge architecture) |

**Round-2 must resolve:** if no calibratable label exists, then *every* judge in this project is advisory forever and the plan must say so in its first paragraph — which also means the honest home for idea (ii) is proposer input + operator observability + tautology-free mechanical classes (no status chunks, missing terminal state, duration outliers), not a gate.

---

## 3. Recommended `/plan-feature` scope

### Feature brief — "Evolve evidence layer" (suggested phase letter: **EI**; operator assigns)

**Problem statement.** `/improve-bot-evolve` spends ~45 SC2 games per generation and records almost nothing about them. Nine fields per game reach `data/selfplay_results.jsonl`; the per-seat console log is truncated by the next game (`selfplay.py:292` + `sc2/proxy.py:187`); the fitness-winning candidate is rmtree'd before anything reads it (`evolve.py:675-681`); `data/evolve_results.jsonl` is 0 bytes because it truncates every fresh run; and the proposer — the most expensive call in the loop — is fed a 4000-byte tail of the *wrong* game and told it is mirror evidence even on refresh generations where `skip_mirror=True`. As a result, five separate restructure proposals all failed at the same point: **there is no measurement against which any of them could be decided.** This phase buys the measurements, fixes the one outright correctness defect in the proposer's inputs, and wires the two zero-game structural levers whose value does not depend on an unmeasured base rate.

**In scope (10 items, all S/M, all zero added SC2 games):**

1. **A1** — per-game self-play log paths + retention cap.
2. **A2** — `src/orchestrator/trace.py`, pure stdlib parser.
3. **N2** — trace capture at the two existing `on_game_end` seams, fail-open, hard-disabled under `--concurrency > 1`.
4. **A3** — correct, side-labelled proposer evidence block; honest "no mirror games this generation" on refresh. *(the one deliberate default-visible change)*
5. **N1** — patch capture, log-only, candidate-pre-edit baseline, with `patch_path` threaded through `to_json`/`from_json` **and** the parallel worker, plus a `git apply --check` agreement row at stack time.
6. **A4** — persist `wins_parent`.
7. **A5** — `--stack-static-gate` (whole-package `mypy --strict` pre-commit) with a drop-lowest-rank rule.
8. **A6** — `--stack-size K`, default 0 = today.
9. **A7** — `--stack-model MODEL`, default = today (operator idea (i)).
10. **A8** — rule-derived gate-1 early-stop (byte-identical under `majority`).

Plus **A9** as a Step-0 analysis input (not a build step): the corrected replay table over `selfplay_results.jsonl`, printed into the plan.

**Optional adjunct (operator's call, decision point 6):** **N6** — `scripts/lineage.py` with a `reset` verb + launcher `--generations 0 --budget-fit`. Not part of the measurement thesis; included only because it makes EL.7 (#279) runnable and stops the observatory button truncating soaks to one generation.

**Explicitly OUT:**

- Any gate-1 bar change (`--fitness-alpha`, posterior conditioning, margin ordering) — D2/R2/R3.
- Deterministic replay as a behaviour — D1 (the capture is log-only; replay is a later decision gated on the fidelity number).
- Any LLM judge anywhere in a gate path, inline or otherwise — R7, R11, `SKILL.md:574`.
- Any semantic play-quality metric (`ever_attacked`, DEGENERATE) — R6.
- Full-source proposer inlining — R1. (`--proposer-context signatures`, N3, is *deferred out* of this scope too: it is a real contract bug but it does not serve this phase's thesis.)
- Rank-1 rollback granularity, close-bucket arming, gate-first reorder, new checkpoint artifact, gauntlet capture — R4, R5, R8, R9, R10.
- Trace capture under `--concurrency > 1`.
- **Flipping any default ON** — that remains a post-EJ.8 operator decision, per `evolve-judging-plan.md` §3.

**Acceptance criterion (operator-checkable, no new soak):** after the next queued soak completes with `--capture-patch --trace-capture --stack-static-gate` armed in their *log-only* forms:

1. `logs/` contains one trace file per game, not one per (version, role).
2. `data/evolve_results.jsonl` carries one `phase="patch_fidelity"` row per stacked imp with a `git apply --check` verdict, yielding a **fidelity histogram** — the fault-line-1 number this project has never had.
3. The pool prompt for at least one refresh generation says in words that no mirror games were played, and the evidence block names the games and seats it came from.
4. Every fitness row carries a non-null `wins_parent`.
5. At least one `mypy --strict` whole-package result is recorded for a promoted vN+1 (pass or fail — either is evidence).

Pre-register the decision rule before the soak: *"If ≥X% of stacked imps' stack-time output applies cleanly against their captured fitness-time patch, gate-1 bar work (`--fitness-alpha`) proceeds; below X, gate 1 is a text filter and the bar work is cut."* **X is an operator choice** — the measurement is the deliverable either way.

**Game budget: zero added SC2 games.** Validation rides the **flag-OFF shadow half** of an already-queued soak (EJ.7 #288, EJ.8 #289, or EL.7 #279). This is the only orthogonal fold: capture and static-analysis rows have no effect on stack composition, gating, or games per generation, so none of EJ.8's five declared comparison metrics move. The **behaviour-changing half** — arming `--stack-size K`, `--stack-model`, or the static gate as an actual gate — perturbs four of those five and therefore needs its own slot. The plan must say so rather than quietly spending someone else's experiment.

---

## 4. Operator decision points

Each answerable in one line.

1. **Idea (ii) — code or game?** *"Use a SC2 version of the judge"*: **C** (judge each candidate's diff before it plays), **F** (judge the merged stack at step 7), **A** (watch rendered frames), **B** (read per-step telemetry), **D** (an anti-degenerate referee) — or a pair, e.g. `F+B`. **If A, B, or D: the plan gains a mandatory Step 0 (attach a `GameLogger`/per-seat sink at `bots/v13/__main__.py:276`), because none of those three can be built until an evolve game leaves a trace.**
2. **Currency.** When a slot is freed mid-run, would you rather it go to a fresh Opus hypothesis or to re-testing a known passer against the new parent? (Decides whether "reallocation" is a benefit or a cost — fault line 2.)
3. **Soak slot.** Ride the flag-OFF shadow half of a queued soak (zero new machine time, measurement only), or grant this restructure its own slot so the behaviour-changing flags can be validated?
4. **Interruptions.** Has an evolve run ever been interrupted *after* it promoted a stack in that same run? (Three abnormal terminations are on disk, all with zero promotions; a single counter-example from memory re-opens the durability lane.)
5. **Idea (i) shape.** `--stack-model` (escalate the model on the existing N-sequential-rewrite path) or `--stack-mode unified` (one high-tier model writes one merged change from all winners)? The seed notes call the second the "sharper version".
6. **Adjunct.** Ship the queue-unblock pair (`scripts/lineage.py` + launcher `--generations 0 --budget-fit`) inside this plan, accepting that a lineage registry latches multi-lineage scheduling on for every subsequent bare run — or keep it separate?
7. **Prompt golden.** Is one deliberate default-visible proposer-prompt change acceptable in this phase (the EJ.1 precedent), or must even the corrected evidence block sit behind a flag?

---

## 5. Round-2 agenda

Five propositions. Each must be defended or refuted with file:line or measured data — not argued from plausibility.

**P1.** *"`--stack-size 1` closes the composition validity hole more completely and more cheaply than patch capture plus replay, and its only real cost — promotions per hour — is measurable from games already being played."*
Defender must show that 61% of promotions being N≥2 is the dominant hole. Refuter must show that per-hunk fidelity, not composition, is what breaks — or that promotions-per-hour is unaffordable to lose.

**P2.** *"Under the pool-refill mechanic at `scripts/evolve.py:5053-5056`, no change proposed in this round can reduce SC2 games per generation. The only real currencies are wall-clock LLM time and slot-allocation quality."*
If true, every cost column in §1 is mislabelled and `--stack-model` / `--stack-mode unified` become the highest-leverage items in the set. Refuter must exhibit a change that lowers games per generation without shrinking the pool.

**P3.** *"No semantic signal in this pipeline can be calibrated against a fitness-bucket label, because the deciding-game selection effect makes a trivial who-won bit the strongest available predictor. Therefore every judge in Alpha4Gate must be advisory forever, and idea (ii) must be scoped to tautology-free mechanical classes."*
Refuter must produce a label — any label — that is not contaminated by the early-stop and not definitional.

**P4.** *"The proposer's game-evidence block is a correctness defect, not an enhancement: it is a 4000-byte tail of one overwritten log, taken from the parent seat only, spliced across workers under concurrency, and presented as mirror evidence on refresh generations where `skip_mirror=True`. Fixing it outranks every gate change in this restructure."*
Defender must show that proposal quality, not gate precision, is the binding constraint. Refuter must show the block is load-bearing enough for its mislabelling to matter.

**P5.** *"Gate-1 bar work (`--fitness-alpha`) must be sequenced strictly behind the patch-agreement measurement, because tightening a bar on an artifact that is deleted and re-authored is the exact prohibition in `measurement-validity.md` § Score the production artifact."*
Refuter must argue that selecting better imp *texts* is itself worth the 30× rise in dead generations that the corrected replay predicts (0.008 → 0.251).

---

# Appendix — idea (ii) completion study

*The full study behind section 4's decision point 1 and the decision card in the seed doc.*

## 1. What "judge" means in this operator's vocabulary — evidence, not inference

**It is a named skill family, not a generic word.** Three independent confirmations:

- **`.claude/skills/judge-ui/core.md:17-24`** — "the generic engine for the **visual tier** of UAT… dispatches an **independent vision-judge sub-agent** that views the pixels, cross-checks them against a structured read-back (API JSON / DB query), and returns a per-stage verdict with evidence."
- **`.claude/skills/_shared/judge-core.md:11-28`** puts Alpha4Gate on the *other* end of the same spectrum: "**Advisor** — forward-looking, heavily-grounded recommendation of what to do next (Alpha4Gate `improve-bot-advised`/`-triage`…)." So in this workspace's own doctrine, Alpha4Gate already has an **advisor**; it has no **judge**. "A SC2 version of the judge" is a request for the missing half.
- **The operator has already built an Alpha4Gate adapter for one** — `Alpha4Gate/.claude/skills/a4g-motion/SKILL.md:2` ("Alpha4Gate project adapter for the generic /judge-motion engine"), committed as `7422057`. And `dev` memory `project_judge_motion.md` records that judge-motion's **Step 11 = operator pilot on Alpha4Gate**.

**The load-bearing traits of "the judge" as this operator has experienced it** (verbatim from `project_judge_motion.md`, and mirrored in `judge-ui/core.md:106-125`):

> "deterministic mechanical pre-gates … run BEFORE any vision spend; diff-spike-selected filmstrip … judged by a **separate** Sonnet sub-agent via **binary per-defect questions**; frozen smooth/janky **calibration fixtures … BLOCK real verdicts** (vision judges are unvalidated on temporal content — the instrument must prove separation first). **Never a build-phase gate** — on-demand UAT sibling."

Four traits carry over: (1) mechanical checks first, (2) an *independent* sub-agent, (3) calibration against known-good/known-garbage before any verdict is trusted, (4) **advisory — never the gate**. Trait (4) matters here because `Alpha4Gate/.claude/skills/improve-bot-evolve/SKILL.md:574` already forbids the alternative: *"Fitness + stack-apply import gate + regression are non-negotiable. Never bypass any phase gate; the whole point is that LLM proposals are untrusted and must earn their promotion empirically."* And `documentation/plans/evolve-judging-plan.md` §1 restates it: *"nothing here adds an LLM judge to the gate path (safety-rail constraint from the SKILL.md)."*

**LLM-as-judge in Alpha4Gate today: none.** Every `judge` hit in the repo is in planning docs, the a4g-motion adapter, or `.claude/task-state/current.md:84` (which is this same truncated sentence). The only `claude -p` calls in the evolve path are the pool proposer (`src/orchestrator/evolve.py:1680-1722`, `--model` from `EVOLVE_POOL_MODEL` → opus) and the dev-apply sub-agent (`src/orchestrator/evolve_dev_apply.py:234-241`, `model: str = "opus"`).

---

## 2. The finding that reframes the whole card: **the evolve path records almost nothing about its games**

This is the single most decision-relevant thing I verified, and it gates 4 of 6 candidates.

| Evidence channel | Exists for **evolve** games? | Anchor |
|---|---|---|
| Win/loss/draw + duration | **YES** — and that is nearly all | `src/orchestrator/contracts.py:121-137` — `SelfPlayRecord` = match_id, p1/p2_version, winner, map_name, duration_s, seat_swap, timestamp, error. Persisted to `data/selfplay_results.jsonl` |
| Rich per-step telemetry (units, structures, `actions_taken` **with reasons**, score, `strategic_state`, coherence params) | **NO** — format exists, is never attached in evolve | `bots/v13/__main__.py:276` — `bot = Alpha4GateBot()`, **no `logger=`**, no `training_db=`, no `reward_calculator=`. Signature default is `logger: GameLogger \| None = None` (`bots/v13/bot.py:235`). `bots/v13/runner.py:336,477` (solo/batch) DO attach one → `logs/game_2026-08-27T18-59-16.jsonl`, 188 rows for a 367-s game, exactly the JSON a judge would want |
| Per-seat console log (step, minerals, gas, supply, score, unit count, `winprob=`, `state=`) | **YES, but destroyed every game** | `src/orchestrator/selfplay.py:292` writes `logs/selfplay_{version}_{role}.log`; burnysc2 opens it `"w+"` (`.venv/Lib/site-packages/sc2/proxy.py:187`) → **each game truncates the previous one.** The proposer's context is a 4000-byte tail of that single surviving file (`src/orchestrator/evolve.py:1374-1396`) |
| Per-bot result JSON (`game{i}_p1.json`) | **NO — dead contract** | `--result-out` declared `bots/v13/__main__.py:98-107`, passed `src/orchestrator/selfplay.py:264,287-288`, **consumed nowhere** |
| SC2Replay of an evolve game | **NO** | `GameMatch` has no `save_replay_as` field (`.venv/.../sc2/main.py:40-55`; `host_game_kwargs` :71-79) and `run_match` (:590-633) never calls `Client.save_replay`. The 839 files in `replays/` all come from the **solo** path (`bots/v13/connection.py:85-96`). Newest is `20260729`, i.e. not from evolve |
| Rendered frames | **NO** | The viewer reparents SC2's own top-level HWND into a pygame container (`src/selfplay_viewer/container.py:294-361`, `reparent.py`) — the game pixels are in a child HWND, **not** in any pygame surface. `--viewer` is opt-in and Windows-only |
| Candidate source diff | **Computable, never persisted** | Scratch `bots/cand_<uuid>` is rmtree'd in `finally` (`src/orchestrator/evolve.py:680-681`). `--screen-null-diff` already snapshots pre-edit content (`evolve_dev_apply.py:323`) — a diff is one step away |
| Frozen-anchor panel results | Machinery exists, **registry absent** | `run_baseline_gauntlet` `src/orchestrator/evolve.py:940`; call site `scripts/evolve.py:4709-4727`; explicitly log-only (:4701-4707). `data/baselines.json`, `data/fingerprints.json`, `data/lineages.json` **all absent on disk today** |
| Replay parser | Exists, **zero production consumers**, and it parses JSON not binary | `bots/v13/replay_parser.py` (`import json`); sole importer is `tests/test_replay_parser.py` |

**Cost baseline (measured, not assumed).** `data/selfplay_results.jsonl`, n=1792: mean **164.8 s/game**, median 118.3 s, p90 287 s, 2.1 % undecided. Defaults: `--pool-size 10`, `--games-per-eval 5`, `--concurrency 1` (`scripts/evolve.py:189-197,352-354`). A generation ≈ 45 games ≈ **~2.1 h of SC2**. There are already ~11-15 `claude -p` calls per generation. **Games are ~500× more expensive than judge calls.** Any judge that removes even one eval pays for a hundred of itself.

---

## 3. Six candidate completions

| # | Reading of "a SC2 version of the judge" | Evidence it consumes → exists? | Attach point | Cost / generation | Lets us stop | Dominant failure mode | Fit |
|---|---|---|---|---|---|---|---|
| **A** | **Frame judge** — literal judge-ui transplant: an independent sub-agent *watches rendered frames* of the game and returns PASS/FAIL/ESCALATE | Rendered frames — **NO.** Needs `--viewer` forced on + per-HWND `PrintWindow`/BitBlt capture that doesn't exist; Windows-only, kills the Phase 8 Linux/Docker substrate | Post-game, advisory | 0 games; capture I/O; 1 vision call per game (≤45) | nothing empirical | SC2's camera is bot-controlled and mostly parked at the Nexus; a headless-speed filmstrip shows little. Operator's own memory: *"vision judges are unvalidated on temporal content"* | **2** |
| **B** | **Telemetry / loss-autopsy judge** — reads structured per-step game state and rules on **why** the game went the way it did; feeds the proposer | `logs/game_*.jsonl` — **format exists, not produced in evolve.** Fix is ~1 line at `bots/v13/__main__.py:276` + a per-seat filename (today's `game_<ts>.jsonl` carries no version or role → two seats collide) | Step 2, proposer input — replaces the single truncated 4 KB stdout tail | 0 games; 1-2 claude calls | feeding the proposer one overwritten stdout tail as its entire empirical picture | Launders 5-game noise into confident prompt steering — the exact reason the investigation scored "archive/reflection scratchpad" **4** and said *"fix signal quality first"* | **4** |
| **C** | **Code judge on the candidate diff** — rules on what the dev sub-agent actually wrote, *before* games are spent | Candidate diff — **computable today** (`evolve_dev_apply.py:323` already snapshots before-content) | Between dev-apply and fitness — the seat EJ.2's mechanical null-diff screen already occupies | 0 games; ~10 calls; **saves ~4.1 games (~11 min) per rejected null** | playing ~4 games on candidates that cannot change behaviour | Uncalibratable against 5-game labels: *"a perfect null-detector's rejects 'fitness-pass' ~50 % of the time, so the naive 0.20 kill threshold systematically kills the best judges"* (`evolve-judging-alternatives.md` § Judge architecture). As a **gate** it also violates SKILL.md:574 | **4** (advisory) / **2** (gate) |
| **D** | **Anti-degenerate referee** — a standing judge that vetoes a promotion that wins by a pathological route (the operator's own step-9 concern, verbatim: *"ensure we don't optimize for a degenerate behavior"*) | Needs composition/build-order → **NO** (same gap as B). Frozen-anchor games **already played and thrown away** under `--fitness-mode both` | Post-promotion, at the rollback decision — where `--panel-floor` (EJ.4) sits | **0 extra games**; 1 call per promotion | treating a coin-flip regression gate as the only drift backstop | "Degenerate" has no ground truth here, so there is no golden set to calibrate against; and the deterministic sweep-loss floor already does this job with zero LLM. Registry `data/baselines.json` is **absent**, so the substrate is inert until someone registers anchors | **3** |
| **E** | **Judge panel replaces the win-count gate** — N judges vote instead of playing 5 games | diff and/or telemetry | replaces gate 1 and/or gate 2 | **−45 games/gen (~2 h)** — by far the biggest saving on the board | playing SC2 | Head-on collision with SKILL.md:574 *and* `measurement-validity.md` § "Score the production artifact, not a proxy" — a judged diff is exactly the scored-transcript failure that picked a coder producing 100 % no-diff output | **1** |
| **F** | **Aggregation judge at step (7)** — reviews the merged vN+1 tree before the `[evo-auto]` commit; the *reviewer* half of the operator's own idea (i) | vN+1 tree + per-imp attribution + `git diff bots/vN bots/vN+1` — **exists** | Immediately after `_stack_apply_and_promote` (`scripts/evolve.py:2111`), before commit. Today the **only** check there is a 30 s `import bots.vN+1.bot` (`_default_import_check`, `scripts/evolve.py:2072`) | 0 games; **~1 call/generation** — the rarest, highest-leverage step, which is precisely idea (i)'s own argument | committing a stack whose members silently clobbered each other, then paying ~4 regression games + a `git revert` to discover it | An LLM verdict that blocks a commit is an LLM in the gate path. Mitigation is known and cheap: keep it advisory and pair it with the **deterministic** whole-package `mypy --strict` tier the investigation scored **7** (per-imp mypy sees changed files only — `evolve_dev_apply.py:618-671` — so cross-imp signature drift is invisible today) | **5** |

Two candidates I considered and folded, so the operator isn't offered false variety: *"judge that ranks the pool before fitness"* — the investigation already killed it (surrogate ranker, score **3**: the pool is **already** in the proposer's own expected-impact rank order, `src/orchestrator/evolve.py:1356-1357`). *"pairwise judge that picks between two candidates"* — that is the sibling-A/B structure `evolve-algorithm-redesign-investigation.md` §7 deliberately deleted.

---

## 4. My single best guess

**F, with B as its evidence supply.** An independent judge sub-agent that reviews the **aggregated stack-apply output at step (7)** — advisory PASS/FAIL/ESCALATE, mechanical gates first, calibrated against a frozen good/garbage pair — is what "a SC2 version of the judge" most likely completes to.

Why: idea (i) is *about step 7*, and idea (ii) is the very next clause. Idea (i) said the aggregation step is *"done infrequently and the most impactful step, so could be worth using a high powered model at this point."* The identical economic argument applies to a judge there, and only there: **once per generation, at the one step whose only current check is a 30-second import.** It also inherits every judge-family trait cleanly (mechanical `mypy --strict` pre-gate, independent reviewer, advisory not gating) without needing evidence that does not exist.

**Confidence: ~45 %.** Not high — which is exactly why this is a card rather than a recommendation. The two live competitors are **D** (the operator raised the degenerate-behaviour worry themselves, in their own step 9) and **A** (the judge skills they actually used are *visual*, so "SC2 version" may mean *watch the game*, and they may not know that the evolve path renders and records nothing). If the operator meant A, the honest answer is that the prerequisite does not exist and B is the affordable substitute.

---

## 5. Operator decision card

**One fact to carry into the choice:** evolve games currently persist **nine fields and no gameplay** (`contracts.py:121-137`). The bot is constructed with no logger in the self-play path (`bots/v13/__main__.py:276`), no replay is saved, and the one console log is overwritten by the next game (`sc2/proxy.py:187`). **A, B and D all require building the evidence first**; C and F work on evidence that exists today.

| Pick | The judge is… | Watches | Verdict changes | Extra SC2 games | Default behaviour changes? | Blocked on |
|---|---|---|---|---|---|---|
| **A** | a vision judge on rendered frames | pixels | nothing (advisory) | 0 | forces `--viewer` on → yes, and Windows-only | frame capture does not exist; `--viewer` opt-in |
| **B** | a loss-autopsy judge feeding the proposer | per-step telemetry | proposal quality | 0 | no (new flag, default off) | GameLogger not attached in evolve; per-seat log naming |
| **C** | a code judge screening candidate diffs | the diff | which imps get 5 games | **−4 per catch** | no | nothing — evidence exists |
| **D** | an anti-degenerate referee | frozen-anchor panel results | rollback decision | 0 (reuses gauntlet games) | no | `data/baselines.json` absent; "degenerate" has no golden set |
| **E** | a panel replacing the win-count gate | diff/telemetry | **promotion itself** | **−45** | yes, fundamentally | SKILL.md:574 safety rail; measurement-validity |
| **F** | a reviewer of the merged stack before commit | the vN+1 diff | advisory; pairs with `mypy --strict` | 0 | no | nothing — evidence exists |

> ### The question
>
> **When you wrote "use a SC2 version of the judge" — was the judge meant to look at the *code* (C = each candidate's diff before it plays, F = the merged stack at step 7) or at the *game* (A = rendered frames, B = per-step telemetry, D = a degenerate-behaviour referee)? Reply with one letter — or two if you meant a pair (e.g. `F+B`).**

Answer that and Round 2 can go straight to adversarial critique of the chosen shape. If the answer is A, B, or D, the plan gains a mandatory Step 0 — *attach a `GameLogger` (or an equivalent per-seat telemetry sink) to the self-play entry point* — because none of those three can be built, let alone calibrated, until an evolve game leaves a trace behind.