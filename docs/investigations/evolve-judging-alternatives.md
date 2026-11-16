# Evolve judging alternatives — investigation (2026-07-05)

Multi-agent investigation into alternative designs for each judging/gating step of
/improve-bot-evolve (proposal pool, fitness gate, stack-apply, regression gate, judge
architecture). 22 agents: 4 code readers, 4 prior-art researchers (SPRT/fishtest,
AlphaStar leagues, noisy-fitness EC, AlphaEvolve/FunSearch), 5+1 designers, 6 adversarial
statistician critics, 1 synthesis. Every alternative was critiqued for statistical
soundness, wall-clock fit (~3-min games, 4h overnight budgets), and codebase fit; scores
below are the critics' 1-10 fit scores. Status: investigation only — nothing implemented.

## Current pipeline in one paragraph

`/improve-bot-evolve` runs two empirical gates over an LLM-generated pool. Each generation, Claude proposes ~10 dev-type improvements (`generate_pool`, src/orchestrator/evolve.py:1583-1782), each is applied to a scratch parent snapshot by a non-deterministic dev-apply sub-agent and plays 5 candidate-vs-parent games; the fitness bar is strict majority (`pass_threshold = games // 2 + 1`, evolve.py:504-519 — pass ≥3/5, close = 2, else evict), with early-stop once either side hits majority (:614-646). All fitness-passers are re-applied fresh to one vN+1 snapshot (`_stack_apply_and_promote`, scripts/evolve.py:1446-1646), gated only by `python -c "import bots.<vN+1>.bot"`, committed, then vN+1 plays 5 games vs the prior parent; `rolled_back = wins_new < 3` triggers `git revert` (evolve.py:687-808, scripts/evolve.py:4030-4063). Total ~55 games/generation ≈ 1 generation/hour in 4-hour overnight budgets. The documented core defect: a no-effect imp passes the fitness gate 50% per eval — and, correcting the designers' 70.5% figure for the early-stop's truncation of close paths, ~61% eventually through the `_RETRY_CAP=3` bench-retry ladder — and the regression gate rolls back a truly-neutral stack 50% of the time, which is the most parsimonious explanation of the regression-rollback-every-generation runs.

## Proposal generation & pool management

| Alternative | Mechanism | Game cost | Effort | Critic |
|---|---|---|---|---|
| A1 Negative priors (promoted/failed blacklists) | Curator emits promoted-title + failed lists into prompt + mechanical title guard | −6–12/gen wasted | S | 8 |
| A2 Mechanical dedup at admission | Title + difflib similarity vs in-run pool at refresh | −4–6/duplicate | S | 8 |
| A3 Archive inspiration + reflection scratchpad | FunSearch-style sampled exemplars + haiku lesson distillation | same | M | 4 |
| A4 Mutate/crossover operator portfolio | Directed variants of close imps, crossover of passers | same | M | 4 |
| A5 Local-judge admission pre-filter | switchboard `local_judge` no-op/already-present screen | −5–13/gen | M | 6 |
| A6 Proposal-space MAP-Elites-lite | (subsystem, principle) cells + quotas + coverage map | same | L | 4 |
| A7 Adaptive pool sizing + budget fit | Hit-rate-driven pool shrink + fit-last-generation-to-budget | repartitioned | S | 6 |

**Top picks: A1 + A2, shipped together.** A1 closes the single best-documented leak (memory `feedback_evolve_priors_diminishing_returns`: "Splash-readiness" burned 9 games re-proposed against v7 which already contained it) — the curator records `stack_apply_observations` (curate_evolve_favorites.py:77-90) but never filters on them, and priors auto-load whenever `data/evolve_favorites.json` exists (scripts/evolve.py:3271-3278). The critic flagged three must-fix defects: the hard title guard as specced reuses the short-pool retry path which **raises** on persistent shortfall (evolve.py:1740-1744) and would abort an unattended soak — it must accept-short instead; the blacklist must merge cumulatively (evolve_results.jsonl is truncated per run at scripts/evolve.py:1308-1324) and must include **in-run** promotions, which is exactly the Splash-readiness variant. Corrected saving is ~5.9 games per avoided re-proposal (early-stop means a null eval averages ~4.1 games, not 5). A2 covers A1's lexical evasion gap and the zero-dedup refresh site (scripts/evolve.py:4128-4132) with stdlib difflib, exact-title matching against in-run promoted imps, and audit rows — the critic called it the cleanest proposal in the set. First move: add `--exclude-promoted` to `scripts/curate_evolve_favorites.py` and fix the prompt line that says "You may include them verbatim" (evolve.py:1483-1489).

A7's **budget-aware final-generation fit** is separately worth extracting (critic: "the single most defensible mechanism in the entire set") — its hit-rate half is broken because null pools show a healthy ~50% pass rate, so the shrink trigger never fires on the failure it targets.

## Fitness gate

| Alternative | Mechanism | Game cost | Effort | Critic |
|---|---|---|---|---|
| A1 Binomial-alpha bar (`--fitness-alpha`) | Pass at k=4/5 (null 18.75%/eval) instead of 3/5 | same/−1 | S | 8 |
| A2 SPRT per candidate | Wald test p0=.5/p1=.75, cap 16 | ~13/candidate | M | 4 |
| A3 Successive-halving race | 2→6→11 game rounds, top-2 promote | ~50 (neutral) | L | 5 |
| A4 Winprob-trajectory signal | Wire dead `--result-out`/MatchResult; AUC for triage only | −8–12/gen (unmeasured) | M | 7 |
| A5 Mercy-rule early termination | Tighter give-up profile in eval games | minutes not games | S | 6 |
| A6 Rigor rebalance to regression | 13/20 keep bar on promotion | +8–15/promotion | M | 6 |
| A7 Frozen-panel gauntlet gate + Elo | Min-WR panel via `run_baseline_gauntlet` + ladder.py | +24–36/promotion | L | 6 |

**Top pick: A1.** One function (`_fitness_bucket`), zero new games, exact closed-form error control, composes with everything. The critic's corrections are load-bearing: today's eventual null pass is ~61%, not 70.5% (the early-stop at evolve.py:614-646 truncates close paths); and the specced futility stop ("parent wins > games − k locks fail") fires at parent=2 and **evicts** still-reachable closes, cutting real-winner (p=0.7) eventual pass to ~60% vs ~91% today — a one-line fix (lock fail only when close is also unreachable) that must land before shipping. Even fixed, k=4/5 stack-applies E[~1.9] nulls per round on a mostly-null pool — a 3× cut, not elimination. First move: generalize `_fitness_bucket(wins, games, pass_wins=None)` behind `--fitness-alpha`, default None byte-identical.

**Second: A4**, with the critic's amendments. The dead contract is verified real (`args.result_out` parsed at bots/v0/\_\_main\_\_.py:100-107, never consumed; the game{i}_pN.json files selfplay.py:287-325 requests are never written), and the winprob heuristic runs live in every evolve game. But the signal source is candidate-side code inside the dev-apply sandbox — an imp can inflate its own winprob — so the consumer must read **only the parent-side JSON**; futility must bench, not evict; and one flag-off shadow generation must measure actual AUC variance before triage is enabled. Skip A2 (SPRT's indifference zone has negative drift at p=0.6 — it kills exactly the modest gains this project produces).

## Stack-apply + import gate

| Alternative | Mechanism | Game cost | Effort | Critic |
|---|---|---|---|---|
| A Stack smoke gate | Whole-package ruff/mypy + optional 1 pathology-only game | 0 / +1 | S | 7 |
| B Sequential promotion (`--stack-size N`) | Promote top-1, bench-and-requeue the rest | same | S | 7 |
| C Rollback ablation | Prefix bisect on regression failure, n=7/≥5 retest | +7–14 on rollbacks | M | 5 |
| D Patch capture + deterministic replay | Diff the fitness scratch, `git apply` at stack time | 0, saves minutes/tokens | M | 8 |
| E Local-LLM semantic conflict pre-screen | Pairwise judge → reorder/defer | 0 | M | 4 |
| F Composition tournament | 3-game probe stack-all vs top-1 | +6/gen | L | 3 |

**Top pick: D.** It closes the largest silent validity hole: promoted code is never the fitness-tested code — `run_fitness_eval` rmtrees the scratch in `finally` (evolve.py:673-679) and `_stack_apply_and_promote` re-invokes a fresh 900s×3-attempt LLM apply per imp (scripts/evolve.py:1555-1556). Replay attaches the already-noisy 5-game signal to the actual promoted artifact, reclaims 2-30 min of sub-agent wall-clock per promoting generation, and makes real (not declared) conflict detection mechanical via `git apply --check`, replacing the accept-after-one-retry policy (evolve.py:1768-1775). Critic requirements: disambiguate check-fails-against-clean-parent (capture defect → sub-agent fallback) from fails-only-against-the-stack (sibling conflict → defer); exclude `data/` from the baseline mirror (training.db/checkpoints break the <1s copytree claim); persist `wins_parent` (every passer's score is censored to [3,5] by early-stop, so "lower-fitness member" is currently a dead statistic); CRLF round-trip test with fallback-rate alerting. **Second: A's static tier** — verified hole: per-imp mypy checks changed files only (evolve_dev_apply.py:618-671) and cannot see cross-imp signature drift; whole-package `mypy --strict` on vN+1 costs zero games and its failures reuse `_cleanup_on_error` before any commit, never touching the fragile revert path. Add a drop-lowest-rank-member rule on repeat static-fail to avoid the deterministic bench-retry livelock the critic identified. Note B and C are substitutes (at stack-size 1, C never fires).

## Regression gate + promotion

| Alternative | Mechanism | Game cost | Effort | Critic |
|---|---|---|---|---|
| One-sided posterior rollback bar | Roll back only on P(p<0.5) ≥ 0.85 (i.e. 0-5/1-4 at n=5) | −0.6/gen | S | 9 |
| Elo ledger + flat-Elo cycle detector | Feed records to ladder.py; veto on flat head Elo | 0 | S | 5 |
| Grandparent transitivity check | +5 games vs vN−1, armed after rollback | +5 conditional | S | 5 |
| Panel floor via baseline gauntlet | Min-WR sweep floor vs frozen anchors | 0 under `--fitness-mode both` | M | 7 |
| Capped SPRT regression gate | Sequential test, keep-on-cap | +6–10/promotion | M | 4 |
| PFSP/variance-weighted allocation | Adaptive 10-game panel budget | +6/promotion | M | 2 (infeasible) |
| Provisional promotion + delayed confirm | 3-game screen + mirror-funded confirmation | claimed −1 (false) | L | 3 |

**Top pick: the one-sided posterior bar** — the highest-scored alternative in the entire investigation, with every number verified exact by the critic: null false-rollback 50%→18.75%, p=0.60 improvement-kill 31.7%→8.7%, mean games 4.12→3.50, and it fixes the draw/crash-forces-rollback bias (evolve.py:450-463) by conditioning on decided games. The flip is principled: fitness already produced per-imp evidence; regression is a safety net, so rollback should require positive evidence of harm. The honest cost is regression catch dropping 76.5%→42.8% at p=0.35, and the critic demolished the claimed backstop ("caught when it becomes the next prior parent" — it never is; the next gate compares against the weakened bar). So it **must ship paired with the panel floor**: `run_baseline_gauntlet` (evolve.py:847-976) already plays 5 games vs each registered baseline post-promotion under `--fitness-mode both` and wastes them on logging; a min-per-anchor sweep floor (roll back only on 0-5 vs any frozen anchor; 9.1% joint null FP at K=3, joint with the posterior bar 26.2%) makes sustained silent drift impossible because frozen anchors cannot cycle — the AlphaStar max-min rationale. Prerequisites the critic verified: `data/baselines.json` is currently absent (EL.6 smoke deleted its seeds) and default `--fitness-mode` is `parent`, so this requires a one-time baseline registration plus an explicit fail-open policy for gauntlet crashes. First move: `--regression-rule one-sided` threading `rule` through `run_regression_eval`, with the early-stop threshold **derived from the posterior** (the hardcoded "stop at 2 wins" is wrong at n≠5) and a minimum-decided-games floor of 4.

## Judge architecture (cross-cutting)

| Alternative | Mechanism | Game cost | Effort | Critic |
|---|---|---|---|---|
| A Duplicate screen (curator + local_judge) | Promoted-title exclusion + semantic dedup | −4–12/gen | S | 8 |
| B Null-diff gate + diff plausibility judge | Mechanical empty-diff skip; LLM tier log-only | −4/null caught | S | 9 |
| C SH race + capped SPRT funnel | 2→6 games then 25-game SPRT | same | M | 5 |
| D Surrogate ranker (ordering only) | Local judge reorders eval queue | 0 | M | 3 |
| E QD MAP-Elites archive over fingerprints | Cell archive drives parent selection | +15/promotion | L | 3 |
| F Judge-cascade contract + auto-disable | Unified Judge protocol, shadow-first, 0.20 kill | 0 | M | 4 |

**Top pick: B's mechanical tier.** The critic verified the hole at source: evolve_dev_apply.py:309-313 treats a zero-edit sub-agent run as **success** ("the sub-agent made no .py edits… Treat as success; the round will play as-is") — a byte-identical candidate then plays ~4.1 games and passes 50% of the time. An empty diff cannot change win rate; skipping it is pure profit, deterministic, and inside the safety rails by construction. The `changed_py` list is already computed at exactly the right place. Caveats: comment-only edits need an ast-aware differ (load-bearing, not nice-to-have), and the new `screen-null-diff` outcome must not increment `retry_count`. Keep the LLM tier-2 log-only: calibrating any judge against single 5-game outcomes is confounded — a *perfect* null-detector's rejects "fitness-pass" ~50% of the time, so the naive 0.20 kill threshold (switchboard harness.py:41) systematically kills the best judges; this is also why F's auto-disable machinery is deferred. From F, adopt only the two free ideas: a shared `phase='judge'` row shape in evolve_results.jsonl, and the type-level constraint that only the empirical tier can emit advance-to-promotion.

## Gap-fill

| Alternative | Mechanism | Game cost | Effort | Critic |
|---|---|---|---|---|
| Group-testing screen | Stack-all first, bisect on signal | −40/null gen | L | 3 |
| Intra-pool Swiss + in-memory Elo | Candidates play each other; top-2 confirm | ~−25/gen | L | 5 |
| Paired-seed CRN + pentanomial | Mirrored seed pairs, fishtest scoring | 0 (claimed) | M | 6 |
| A/A calibration harness | Mine mirror games; nightly launch-health canary | +0–4/night | S | 7 |
| Null-diff + memo + priors exclusion | Tree-hash no-op gate + promoted-title filter | −4–10/gen | S | 8 |
| Patch-reachability coverage | Trace changed lines in game 1 | 0 | M | 4 |
| Built-in-AI difficulty ratchet | Absolute anchor floor on promotion | +10–20/promotion | M | 5 |

The two winners here **converge with earlier sections**: the null-diff + priors-exclusion bundle (8) is the same fix as judge-arch B + proposal-pool A1, independently derived twice — strong evidence it's the right first move. The **A/A calibration harness** (7) is the unique addition: mirror games (3/gen, evolve.py:1586) are already persisted to data/selfplay_results.jsonl (selfplay.py:663-664) but only ever summarized for the prompt; mining them gives empirical crash/draw/duration nulls for free, and a 1-2 game launch-health canary next to the phantom-promote pre-flight (scripts/evolve.py:3110-3129) fills a real unattended-run gap. Critic catch: seat-bias is **irrecoverable** from historical rows (`_parse_winner` maps seat→version, so mirror-game winners are indistinguishable; selfplay.py:557-580) — add a `winner_seat` field for future analysis, and don't build a WR-threshold canary (P(4-0|fair)=12.5% would kill 1-in-8 healthy runs). Paired-seed CRN is sequenced behind this harness: measure pair correlation before building pentanomial machinery, since SC2 bot-side nondeterminism may leave chess's 15% gain near zero.

## Cross-cutting recommendations

1. **Kill nulls before games, then relax the destructive gate.** The null-diff gate + promoted-title exclusion (zero games) cut the null inflow; A1's k=4/5 fitness bar cuts null passage 3×; the one-sided regression bar then stops destroying whole generations on coin flips. These three compound: fewer nulls entering means the relaxed regression bar's lower catch rate matters less, and the panel floor covers the residual drift.
2. **Patch replay (stack-D) is the enabling layer.** It makes rollback ablation, composition probes, and the actual-files orthogonality re-check coherent and cheap; without it, every stack rebuild re-rolls LLM non-determinism.
3. **Persist `wins_parent`.** Three separate proposals (stack B, D, F) independently died on the same censored statistic — every fitness-passer records [3,5]. One field in `per_item_state` (scripts/evolve.py:1361) unblocks all margin-based ordering.
4. **Baseline registration unlocks two sections at once**: the regression panel floor and meaningful EL fingerprints — and requires fixing the verified `write_lineages`/registration persistence gap (zero calls in scripts/evolve.py).
5. **Pin real dispatcher throughput first.** The constraint text is internally inconsistent (3 min/game serial vs ~55 games/hour implies concurrency ~3); A2/A7-class cost verdicts flip depending on which is true.

## Shortlist: cheapest high-value changes

1. **Promoted-title priors exclusion + prompt fix** — `--exclude-promoted` in scripts/curate_evolve_favorites.py filtering on stack_apply_observations (:77-90), cumulative merge, in-run promotions included; delete "You may include them verbatim" (evolve.py:1483-1489). S, zero games, closes the operator's own recorded suggestion.
2. **Mechanical null-diff gate** — branch on empty `changed_py` at evolve_dev_apply.py:309 → new non-retry-incrementing `screen-null-diff` outcome; skip the eval. S, saves ~4 games per catch.
3. **One-sided posterior rollback bar** — `--regression-rule one-sided` in run_regression_eval (evolve.py:782), posterior-derived early-stop, min 4 decided games. S, −0.6 games/gen, 50%→18.75% null rollback.
4. **Refresh-time proposal dedup** — `_normalize_title` + difflib similarity vs in-run pool at scripts/evolve.py:4118, accept-short-after-retry, `phase='pool_dedup'` audit rows. S.
5. **Budget-aware final-generation fit** — before refresh (scripts/evolve.py:4080), shrink the pool to the largest size fitting a complete evaluate+stack+regression cycle in remaining `--hours`, reusing the :3543-3549 budget arithmetic. S, reclaims up to ~25 stranded games/night.

## Rejected or deferred

- **Fitness A2 / regression SPRT (4/4):** indifference-zone drift kills modest true gains (negative LLR drift at p=0.6); null FP 30.1% at 3× the game cost of the posterior rule — dominated on both axes.
- **Regression PFSP allocation (2, infeasible):** its anchor rollback rule is mathematically unreachable under its own budget/allocator (needs 0-8 vs one anchor; the variance weighting steers games away from decided matchups).
- **Provisional promotion (3):** funding premise false — refresh passes `skip_mirror=True` (scripts/evolve.py:4110), so there are no per-generation mirror games to repurpose; realized statistics indistinguishable from the posterior bar at far higher blast radius.
- **Group-testing screen (3):** dilution/masking cuts true-discovery ~10× (a p=0.6 imp in a 9-null stack passes the compound funnel ~7%); converts evolve into a detector of miracles.
- **Composition tournament (3):** 3-game probes give ~49% correct selection in exactly the motivating case (tie-break holds the wrong side).
- **Surrogate ranker (3):** premise false — the pool is already truncated in Opus's own expected-impact rank order (evolve.py:1356-1357, scripts/evolve.py:3542). Salvage: feed failure history into the proposal prompt.
- **QD MAP-Elites archive (3):** ~1 archive member/night on a 27-cell grid with ~30% correct placement; right idea for a 10× game budget.
- **Archive/reflection scratchpad (4), operator portfolio (4), MAP-Elites proposals (4):** all launder 5-game noise into confident prompt steering or mutate 31%-noise "close" seeds; fix signal quality first.
- **Judge cascade F (4):** calibration labels are 50%-noisy; adopt only the row schema + T4-only-promotes constraint.
- **Patch-reachability coverage (4):** hook targets bots/v0 which production no longer snapshots from; claimed env plumbing doesn't exist (selfplay.py:260-290); false-dead verdicts dominate.
- **Deferred at 5-6:** SH races (fitness A3, judge C) until A1 data shows nulls still bind; mercy rule until resignation frequency is counted in existing logs; grandparent check and Elo cycle detector until the panel floor exists; frozen-panel gauntlet gate (fitness A7) behind the EL persistence fix with shadow verdicts first; paired-seed CRN behind the A/A harness's correlation measurement.