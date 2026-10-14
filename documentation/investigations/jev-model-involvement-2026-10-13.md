# Jev model involvement — Phase 1, 2026-10-13

**Phase 1 descriptive audit complete; behavioral counterfactual comparison incomplete.**
Revised after the [independent adversarial review](jev-model-involvement-2026-10-13-adversarial-review.md).
Remain at the requested Phase 1 boundary.
No matches, hosted calls, credential access, gameplay edits or baseline captures were
performed. Phases 2–4 remain pending; their implementation, replay checks and priced
A/B proposal are not claimed complete. Source inspected: `5eb036786bd1a473f07a26c9ec0b09ef0868f0c9`,
branch `master-plan/phase-ev`.

**Revised recommendation: keep the current experimental scope, repair integration,
then test broader executable decisions.** Remove the accidental veto of emergency
defense. The archive contains
57 accepted v2 attack answers that suppressed an available defense branch, including
38 with an observed army-lane failure. That is evidence of a control defect, not
evidence that every such decision lost a fight. Keep the other families experimental:
production choices often differ from local preferences, but these recordings cannot
establish a strength advantage or a ceiling on the model's capabilities. Greater
useful authority after graph improvements remains an open hypothesis; participation
alone is not the success criterion. [Every flagged condition and its event sequences](jev-model-involvement-2026-10-13-incidents.md);
control mechanism: `src/jev/runtime.py:1374`, `src/jev/operations.py:1525`.

## Baseline and method

The scan covers 61 run directories, including incomplete/non-game runs. There are
2,117 answered requests in 19 v2 runs and 95 in two v1 runs. The table excludes v1.
Answers are deduplicated by **run, question, request ID**, using the first answer
diagnostic; subsequent expiry does not retroactively make an accepted answer stale.
All available diagnostic answer/acceptance/staleness/confidence totals reconcile.
Sources: [per-answer CSV](jev-model-involvement-2026-10-13-evidence.csv),
[JSON with trace hashes, run inventory, comparisons and subsequent events](jev-model-involvement-2026-10-13-evidence.json),
`scripts/analyze_jev_model_involvement.py:45`. Each evidence row cites its run and
acceptance/response `seq`; `comparison_seqs` and outcome records supply supporting events.

**Comparison means reconstructed local branch preference at the same recorded state,
not a simulated alternate game or proof that a command changed.** Local strategy has
independent macro/army lanes, not one scripted four-way answer. Defense-preempted
strategy cases and empty-army recovery cases are therefore separated. Army labels
compare defend, count-based regroup, and attack intent; an attack still needs an
eligible group. Existing hosted posture, assignments and plans can affect execution.
Sources: `bots/jev/v2/policy.json:3194`, `:3608`, `:3798`;
`src/jev/strategy.py:962`, `src/jev/operations.py:1525`.

The trace retains node status/branch changes but **omits unchanged numeric facts**.
The audit reconstructs evaluated paths from those changes, uses current exact counts
or evaluated threshold predicates, and leaves unavailable comparisons unknown. It
never carries an old numeric army count forward. Full observation/strength/loss
histories are absent, so exact counterfactual command and combat-outcome attribution
cannot be completed from these files. Sources: `src/jev/telemetry.py:734`,
`src/jev/runtime.py:1387`, `:2169`, `src/jev/strategy.py:1531`;
audit implementation `scripts/analyze_jev_model_involvement.py:24`.

| Question | Answers | Accepted | Stale | Below floor | Same local preference | Different | Unknown | Separated cases |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| army_mode | 1,659 | 1,074 | 223 | 362 | 986 | 59 | 29 | — |
| strategy | 269 | 168 | 15 | 86 | 95 | 2 | 38 | 24 defense-preempted; 9 no army |
| expansion_response | 0 | 0 | 0 | 0 | 0 | 0 | 0 | No recorded participation |
| production_mix | 189 | 189 | 0 | 0 | 27 | 162 | 0 | — |

These are **intent-label comparisons, not measured changes to scripted commands**.
Do not use their disagreement rates to infer advice value. In particular, all
38 unknown strategy comparisons are `recover`, whereas all 95 agreements are
`pressure`: the hosted recovery branch skips the local predicate needed to compare
it. Only two of 72 accepted recovery answers enter the comparable denominator.
Unknown and separated cases must not be counted as agreement. See the
[answer-conditioned table and counterexample](jev-model-involvement-2026-10-13-adversarial-review.md).
Table source: the linked
CSV/JSON, grouped by `question`, `reason` and `comparison` (each row has run + seq).

What followed, using a fixed **30-game-second window**, censored at the last trace:

- **Army:** 57 differences were attack versus defend; one was attack versus regroup,
  one regroup versus attack. All 59 had a subsequent army command; 45 had an army-root
  failure in the window. These overlapping windows include other decisions and do
  not establish causation. Sources: CSV rows with `army_mode/different`; detailed
  command/failure sequences in JSON. The two non-defense cases are
  `24279aee #133568` and `3c5f624b #57519`.
- **Strategy:** the two comparable differences both chose recover: instead of
  pressure at `3c5f624b #55608`, and instead of expansion priority at `#58516`.
  Recovery movement followed the first (`#56626`); attack and expansion-worker
  movement followed later (`#59273`, `#59376`). Both windows include lane failures.
  Neither observation proves that recovery helped or hurt the match.
- **Production:** every accepted answer was Stalker-heavy; 162 differed from the
  balanced fallback. Of those, 151 had a production command within the observed
  window; 143 had a full 30 seconds of follow-up. No combat benefit or harm is
  identifiable from composition preference alone. Example: `0eb8a3ce #56604`;
  complete command sequences are in the corresponding JSON rows.
- **Expansion response:** no answered requests, so no effectiveness conclusion.
  Source: all response rows in the evidence inventory.

## Control defects, option-quality flags and corrections

The [incident appendix](jev-model-involvement-2026-10-13-incidents.md) enumerates
**67 accepted v2 answers with a flagged condition: 57 defense-suppression cases and
10 empty-army recovery exposures.** The latter are not demonstrated harm. Every
accepted answer also gets a classification in the ledger: 67 flagged conditions,
309 temporal associations
within 30 seconds, and 1,055 with no established listed mechanism in that window.
The latter is not a safety verdict. Temporal associations include unrelated question
families and are explicitly not blamed for another family's defect. The original
machine-readable category `direct_mechanism` is retained for reproducibility; it
means an observed listed condition, **not proved damage or a worse model decision**.

1. **Defense suppression: 57 attack answers across 15 runs.** Forty-six cancelled
   recorded defense tasks immediately; renewals can sustain suppression without new
   cancellations. Thirty-eight include army-lane failure; fourteen include attack
   commands during a home threat (categories overlap). There are **no observed
   regroup examples** in this set, although the same mode guard permits that failure.
   Example: `24279aee #211147` accepts attack, `#211151–211154` cancel defense,
   `#211233` blocks defense, `#211253–211259` send attack commands. This is an attack
   **during a home threat**, not proof of attacking into a stronger enemy formation.
   The latter needs observations absent from these traces.
2. **Order gap: corrected.** At `3c5f624b #52107` (189.375 s), attack cancels six
   defense tasks (`#52112–52117`); the army root fails (`#52222`). New recovery MOVE
   commands begin at **193.4375 s**, `#53315`: **4.0625 s without new army commands**.
   Defense resumes at **194.9554 s**, `#53698`: **5.5804 s without new defense commands**.
   Runtime task cancellation does not prove SC2 cleared existing unit orders.
3. **Recovery with no ready army: nine new acceptances, plus one held answer.**
   `24279aee #243110, #248486, #254108, #259666, #261266, #266707, #270608,
   #274485, #277803` confirm the nine starting at 779.286 s. The earlier accepted
   recovery at `#237319` was still held when the army became empty (`#242880`).
   After launch, available army options become only regroup: the request builder
   requires a ready army for attack/defend (`src/jev/strategy.py:672`). The local
   few-units rule can also attempt useless recovery; these are not nine demonstrated
   degradations relative to scripted play. Recovery remains offered by
   `src/jev/strategy.py:962` without an army-presence check.
4. **First-request race confirmed, denominator qualified.** All 17 v2 runs whose
   request #1 answered dispatched it on the launch tick and rejected it as stale.
   Example: `24279aee` launch `#41398`, response `#41555`. Two additional v2 runs
   failed request #1 (`28643358 #41430`, `3c5f624b #41212`) and accepted request #2
   (`#41680`, `#41720`). All first-answer/launch pairs are in JSON `first_army`.
   Poll precedes tick; the signature includes launch state:
   `src/jev/bot.py:804`, `src/jev/strategy.py:707`, `:1228`.
5. **Earlier aggregate reproduced.** Excluding the October 13 run `f934c372`, there
   are exactly 2,211 answers: 1,515 accepted (68.5%), 248 stale (11.2%), 448 below
   floor (20.3%). Its one stale answer (`#41649`) makes today's total 2,212/1,515/249/448.
   These totals mix v1/v2 and native realtime/paced games; they are not a controlled
   realtime v2 baseline. The inventory retains each available pacing configuration.
6. **Confidence formula confirmed.** Typesafe documents Choice confidence as
   `(p_max - 1/n)/(1 - 1/n)`. For two options this equals `p1 - p2`; a 0.5 floor
   requires top probability at least 0.75. Across 1,753 recorded two-option answers,
   discrepancies are at most 0.01, consistent with displayed rounding. This statistic
   is not an empirically calibrated chance of winning a fight.
   [Typesafe confidence documentation](https://docs.typesafe.ai/confidence#how-confidence-is-calculated),
   checked 2026-10-13; local floor `src/jev/decision.py:97`; probabilities in each JSON row.
7. **No demonstrated strength improvement.** The scripted Medium Terran win is
   confirmed: `19936d7a #119676` reports scripted, zero calls;
   `data/jev/runs/19936d7a70254d7dab43925303f7ea87/diagnostics.json:1` reports win at 360.179 s.
   This was unthrottled scripted play, not a matched realtime control. J2 Steps
   222/223 remain pending (`documentation/plans/jev-v2-plan.md:772`, `:782`).

## Handoff at the Phase 1 boundary

The adversarial review found no response-count errors, but the behavioral attribution
gap remains open. Future comparisons must use the **same repaired graph** for
scripted and hosted controls; include a fixed Stalker-heavy control to distinguish
adaptive advice from a better constant default. These are measurement requirements
for the later proposal, not authorization to run matches.

Phase 2 should address both the guard and task-cancellation paths, filter empty-army
recovery, and ensure a non-defending intent cannot strand the army during a threat.
The production test entry point already exists: `tests/test_jev_v2.py:1300` uses
`run_match` with a mocked HTTP transport and simulated game port. Add regression
scenarios for both cited incidents, ground/air threats, held answers, and loss of
the last combat unit there; these tests have **not** been added or run in Phase 1.
Sources for the proposed scope: `src/jev/runtime.py:1374`,
`src/jev/operations.py:1525`, `src/jev/strategy.py:962`.

Phase 3's earlier questions, narrower signatures and richer snapshots need offline
validation before an acceptance-rate forecast. Phase 4's three-arm match count,
cost estimate, cap and decision rule will be prepared after that stop. Reuse
[umbrella #346](https://github.com/aberson/Alpha4Gate/issues/346) and its existing
$5 reservation/evaluation design, rather than create another campaign harness.
That budget mechanism is still pending, not an enforced allowance or launch approval
(`documentation/plans/jev-overnight-improvement-plan.md:93`, `:149`, `:396`).
Preserve J2's held-out panels and frozen v1; no capture or game is needed to continue
the offline phases (`documentation/plans/jev-v2-plan.md:982`).

Reproduce the audit (offline):

```powershell
.venv/Scripts/python.exe scripts/analyze_jev_model_involvement.py --output documentation/investigations/jev-model-involvement-2026-10-13-evidence
.venv/Scripts/ruff.exe check scripts/analyze_jev_model_involvement.py
```

Validation performed: lint; complete scan; unique response IDs and ordered event
sequences; reconciliation against every available run's diagnostic decision totals;
both known defense incidents recovered; stale answers excluded from accepted-incident
counts. No gameplay test or causal strength comparison is claimed.
