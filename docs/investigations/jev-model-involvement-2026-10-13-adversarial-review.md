# Adversarial review: model capability versus graph integration

2026-10-13. Independent reviewer: `/root/adversarial_audit`. Review scope: the
Phase 1 report, analysis script, evidence ledger, production graph/control paths,
and raw recorded events. Read-only investigation; no hosted calls or games.

**Verdict: repair the integration; the recordings do not establish a ceiling on
Jev's ability to make useful decisions.** The defense restriction is justified by
actual command evidence. The hypothesis that a better graph could support more
useful hosted decisions remains open. This corrects the emphasis of the original
[Phase 1 recommendation](jev-model-involvement-2026-10-13.md).

## Findings

**High: the strategy comparison is selectively observable.** All 38 unknown
comparisons are recovery answers; all 95 agreements are pressure answers. Only
two of 72 accepted recovery answers enter the comparable denominator. Consequently,
`2/97 = 2.1%` is a selected-sample label statistic, not evidence that strategy advice
is redundant. The hosted recovery guard precedes the local few-units predicate,
so the answer itself affects what the trace lets the audit observe.
Sources: `bots/jev/v2/policy.json:3587`, `scripts/analyze_jev_model_involvement.py:175`,
and [CSV](jev-model-involvement-2026-10-13-evidence.csv) grouped by answer/comparison:

| Accepted strategy answer | Same label | Different label | Unknown | Defense-preempted | No army | Total |
|---|---:|---:|---:|---:|---:|---:|
| pressure | 95 | 0 | 0 | 1 | 0 | 96 |
| recover | 0 | 2 | 38 | 23 | 9 | 72 |

**High: label disagreement is not marginal behavioral influence.** At
`3c5f624b #57519`, the audit labels an army answer as regroup versus local attack,
but `#57520` records a strategic recovery already in force; `#57609` records that
recovery branch running. Removing only the army answer does not establish an
attack counterfactual. Conversely, strategic posture also prevents new macro plans:
`src/jev/strategy.py:1504`. At `3c5f624b #56525`, eight ready combat units and an
offered expansion coexist with the macro slot held by recovery request 27.
The original table therefore does not finish the requested behavioral comparison.

**Medium: empty-army recovery is an exposure flag, not demonstrated harm.** The
ten flagged answers all belong to the single paced 2× run `24279aee`; nine were
accepted after the army became empty, one was already held. The model is told
recovery includes rebuilding (`src/jev/strategy.py:327`), while the graph's local
rule also attempts recovery below four units (`bots/jev/v2/policy.json:3608`). At
`24279aee #243110`, its only offered alternative is pressure. Retain the user's
requested no-empty-army option filter for Phase 2, but do not count this as evidence
that the model chose worse than scripted play. Evidence: the ten recovery rows in
the [incident appendix](jev-model-involvement-2026-10-13-incidents.md), and
`24279aee #243109–243110`.

**Medium: optimized-graph comparisons need a matching scripted control.**
Hosted-current versus hosted-improved measures the combined graph/question change.
To attribute value to the model after optimization, scripted and hosted controls
must use the same improved graph, observations, execution safeguards and match
conditions. Also test a fixed local Stalker-heavy preference: every one of the
189 accepted production answers chose that option. A useful composition default
and useful state-dependent model advice are different hypotheses. Sources:
all production rows in the CSV; `src/jev/strategy.py:404`.

## Findings that survive review

The reviewer independently deduplicated the raw traces: all **2,212 response keys,
sequences, answers and first reasons** match the saved ledger. No correction to
those totals is needed. Defense suppression also survives: task cancellation at
`src/jev/runtime.py:1374` and branch exclusion at `src/jev/operations.py:1525`
explain `24279aee #211147–211259` and `3c5f624b #52107–52222`. Within native realtime
runs alone, 34 accepted answers have the suppression flag, including 21 with an
observed army-root failure. Thus the defect is not confined to accelerated games.
Sources: raw `events.N.jsonl` and the ledger, joined to `runs.options.realtime`.

The coordinator additionally checked question exposure directly in the raw traces:
131 accepted strategy answers offered only pressure/recover, and 37 also offered
expand (all 37 chose recover). All 18 answered four-option questions were rejected
for low confidence. Examples: `0eb8a3ce #76224`, `24279aee #60264`, `#65768`.
These narrow, filtered exposures do not test an optimized strategic decision system.

## Revised recommendation and next discriminator

**Keep the model's present experimental scope while fixing integration defects;
reduce only its accidental ability to suppress emergency behavior. Then explicitly
test broader executable choices.** This preserves the user's hypothesis without
assuming its conclusion. The current recordings show coordination defects and
limited decision exposure; they establish neither that more authority will help
nor that the model cannot use it effectively.

For offline validation, inject identical answers through the existing production
entry point into current and repaired control paths. Exercise every offered option,
including deliberately poor answers; measure defense continuity, executable options,
command changes, and macro-slot effects. This tests the graph's contract, not the
model's intelligence. The existing mock-transport/production-runner seam is at
`tests/test_jev_v2.py:1285` and `:1317`.

Here, useful graph optimization means better decision timing, consistent meanings
for options, accurate feasibility checks, independent execution safeguards, and
avoiding conflicting posture/intent controls. Reducing node count alone does not
test those properties. This is a proposed direction grounded in the findings above,
not an implemented design or measured improvement.

The original phase stops remain: no Phase 2 gameplay changes or Phase 3 experiments
were made in this review. The later paid proposal must specify count and cap and
receive explicit approval; reuse #346's budget/evaluation work. No paid experiment
is authorized by this review.
