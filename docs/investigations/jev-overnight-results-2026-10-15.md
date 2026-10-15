# Jev overnight results — October 15, 2026

**One test repair completed; no gameplay repair or campaign implementation accepted.** The authorized work began around 23:12 Pacific on October 14. Step 225 exhausted its single permitted review batch and remains held; Step 232 received diagnosis and design only. No hosted calls, paid games, promotion, frozen-v1 recapture, or Steps 226–227 work occurred. Typesafe spend for this window: **$0**.

## Completed baseline repair

The recovery test incorrectly required no macro plan for the entire simulated match. In run `8329fa04ffed48149d2b73219ea32f73`, recovery request 3 was accepted at #1670, issued eight MOVE commands at #1776–1797, completed and released its override at #3885, then legitimately started local tech at #3934–3935. The replacement assertion checks request attribution and absence of macro starts *until completion*, followed by local fallback without hosted attribution.

This changes only `tests/test_jev_v2.py`, +44/−5, in [e41e4b9](https://github.com/aberson/Alpha4Gate/commit/e41e4b9220372e452c00d68b15d9627ebbb28483). It is committed and pushed on `fix/jev-recovery-test-lifetime`, independently reviewed PASS, applied to the root checkout without committing the unrelated migration, and cherry-picked into the existing Step 225 checkout as `324d5f0`. No runtime or policy behavior changed. Review and mechanical receipts are in [the evidence bundle](jev-step225-review-2026-10-15.json).

| Check | Isolated baseline repair | Step 225 candidate | Current root checkout |
|---|---|---|---|
| Python | 3,396 passed; 13 skipped | 3,407 passed; 13 skipped | 3,408 passed; 12 skipped |
| Strict mypy (`src bots`) | PASS, 832 files | PASS, 834 files | PASS, 833 files |
| Ruff | PASS | PASS | FAIL: 306 violations in five untouched migration helper scripts |
| Frontend tests | 417 passed; 6 skipped | Same-byte baseline receipt reused | 417 passed; 6 skipped |
| Frontend build/lint | PASS | Same-byte baseline receipt reused | PASS |

All Python runs deselected two live SC2 tests. Candidate frontend reuse was checked with an empty diff for `frontend`, `pyproject.toml` and `uv.lock`. Root lint failures are confined to `docs/plan-reconciliation/{reconcile,closeout,finalize,repair-historical-links,validate-migration}.py`; they were not swept into this task. This is not a clean-root or gameplay-strength claim. Command logs remain under `.build-step/overnight-2026-10-14` and the two isolated worktrees' `.build-step` directories.

## Step 225: held after the authorized review

Six fresh, read-only lenses plus the required final Sol judge reviewed unchanged campaign bytes. Security and style passed; correctness, bugs, test quality and plan conformance returned NEEDS-WORK. The installed reducer and final judge both returned **NEEDS-WORK**. Before/after file audits found no reviewer mutations. All raw verdicts, the reducer result, exact source hashes and final qualifications are preserved in [the evidence bundle](jev-step225-review-2026-10-15.json). Requested reviewer models were explicit; backend model identity was not independently observable.

The main defects are:

- Champion weights can be wrong when the third champion duplicates the parent (`campaign.py:183`); parsed weights can violate fixed role allocations (`:323`), and duplicate entries are silently collapsed by the scorer (`:327`).
- Preparation omits frozen comparison metadata (`campaign.py:275`), lineage seed exclusions and recorded development seeding (`:514`), and alternating candidate/reference order (`:538`).
- Pure offline scoring lacks the required hosted-participation predicate (`campaign_evaluation.py:316`). Adding a typed offline fact would not authenticate it; execution verification still belongs to Step 227.
- Tests bypass source verification and omit adversarial no-network, weight, ordering and scoring-boundary cases (`test_jev_campaign.py:24,45,65,111`; `test_jev_campaign_evaluation.py:64`).

References above are to the five campaign files in preserved commit `324d5f0` on `build-step-225-jdi-contracts`. Test-coverage findings and overlapping reviewer reports are not extra runtime defects. Some raw plan-review excerpts were paraphrases; the final judge independently supplied source quotations and qualified duplicates. Reports correctly remain unverified/offline and cannot authorize execution or promotion. Thus the old authority-boundary problem is addressed in this candidate, but its scoring/preparation contract is still incomplete. No fixes or additional review loop followed the rejection. The original `5eb0367` attempt remains untouched.

## Step 232: stronger diagnosis, no implementation

An additional production-path offline probe reproduced false recovery completion after tasks were already running. In run `dcd67be496f048dcbc5e9ad219422e4f`, loop 112, tasks acknowledged/succeeded at #1649–1664 and the recovery branch succeeded at #1754–1755, while all eight actors retained ATTACK orders and no replacement command was emitted. This is deliberately injected evidence, not a real-game frequency estimate. See [the portable observation](jev-recovery-lifecycle-evidence-2026-10-15.json) and [the proposed repair](jev-safety-repair-design-2026-10-15.md).

The independent design challenge returned NEEDS_DESIGN. A subsequent, explicitly unreviewed addendum specifies order-state classification, immediate task reconciliation, ownership/budget handling and graph resolution/commit ordering. Candidate `00bc515` was not edited. The smallest next gameplay milestone is an order-aware recovery repair with decisive post-adapter per-unit assertions; that alone would not close all of Step 232. Removing the duplicated branch selector remains a separate part of its safety requirement.

## Recommendation and next scope

Keep model involvement unchanged for now. Repair actual command behavior before broadening choices, and judge broader choices against scripted decisions on the same repaired graph. Tonight provides no evidence of better play. The immediate useful work is a narrowly authorized structural Step 232 repair and a separately authorized correction of Step 225's offline contract; neither needs a new execution-authority framework. Only after these gates clear should work advance through 233/234 or 226/227 and eventually matched games. The $5 cumulative cap remains intact, but this window authorized zero paid spend.

Root HEAD remains `c0836c3`; the finished foreign documentation migration and earlier compatibility/GIF edits remain local and uncommitted. The baseline test patch is staged locally. Held candidates are pushed on their existing branches, not merged. This report and review evidence are preserved separately; preservation is not acceptance.

Final stopping time and the four-lane work-window assessment are recorded in `.build-step/overnight-2026-10-14/closeout-window.json` and the session handoff. No background build is claimed after closeout.
