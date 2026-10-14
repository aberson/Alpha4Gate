# Jev decision integration: overnight evidence, 2026-10-14

Status: implementation held after three safety-review rounds; offline baseline and audit completed. No new playing-strength result or hosted expenditure is claimed. The approved scope is [Phase JDI](../plans/jev-decision-integration-plan.md), Steps 232–240; authorization ends at 09:00 Pacific today and paid execution has a $5 cumulative maximum (plan D7 and P6).

## Recorded baseline: did accepted answers differ?

The full v2 archive contains 2,117 answered requests in 19 runs. Comparisons below reconstruct the local branch preference at the recorded state; they do not replay an alternate game. Strategy has independent local lanes, so defense-preempted and empty-army cases are separated rather than forced into a single scripted answer. Source: [answer ledger](jev-model-involvement-2026-10-13-evidence.json:1), with run ID, answer seq, comparison seqs and subsequent events for every row; method in `scripts/analyze_jev_model_involvement.py:24`.

| Family | Accepted | Same preference | Different | Unknown | Separated |
|---|---:|---:|---:|---:|---|
| army_mode | 1,074 | 986 | 59 | 29 | — |
| strategy | 168 | 95 | 2 | 38 | 24 defense-preempted; 9 no army |
| expansion_response | 0 | 0 | 0 | 0 | No participation |
| production_mix | 189 | 27 | 162 | 0 | — |

Within the fixed, censored 30-game-second follow-up, all 59 differing army answers had subsequent army commands and 45 had army-root failures; overlapping windows prevent causal attribution. Both comparable strategy differences chose recovery (`3c5f624b #55608`, `#58516`), followed by movement/attack/expansion events (`#56626`, `#59273`, `#59376`). All production answers were Stalker-heavy; 151 of the 162 differences had later production commands, without identifiable combat benefit. Source: ledger `comparison`, `comparison_seqs` and outcome records; example production acceptance `0eb8a3ce #56604`.

The [complete incident appendix](jev-model-involvement-2026-10-13-incidents.md:15) retains every flagged acceptance and supporting seq. The observed patterns are:

- **Defense suppression:** 57 accepted attack answers across 15 runs; 46 cancelled defense tasks, 38 had army-lane failure, and 14 issued attacks during a home threat (overlapping subsets). Example `24279aee #211147` → cancellations `#211151–211154` → blocked defense `#211233` → attack `#211253–211259`. This establishes the integration defect, not that the enemy army was stronger or the decision lost a fight.
- **Gap in new commands:** `3c5f624b #52107` cancels defense at 189.375 s; army fails at `#52222`; new recovery movement starts at `#53315`, 193.4375 s. The gap is 4.0625 s for new army commands and 5.5804 s for new defense commands (`#53698`), not proof that SC2 cleared existing orders.
- **Recovery without an army:** nine new acceptances in `24279aee`: `#243110`, `#248486`, `#254108`, `#259666`, `#261266`, `#266707`, `#270608`, `#274485`, `#277803`; one earlier held acceptance `#237319` spans the empty-army state `#242880`. These are invalid option exposures, not demonstrated harm relative to the local recovery rule.

No recorded regroup suppression example or reliable “attack into a stronger threat” classification exists. Complete observations are missing; unknown cases remain unknown. A scripted Medium Terran win (`19936d7a #119676`, diagnostics:1) is exploratory and unthrottled, not a matched realtime control.

## Recorded timing and confidence, before implementation

Filter the [existing answer ledger](jev-model-involvement-2026-10-13-evidence.json) to runs with policy version 2, `options.realtime == true`, provider `typesafe`, and no `game_speed`. Eighteen archived runs match; fourteen contain answered requests. Count each `(run_id, question, request)` once. These are answered-request denominators, excluding failed requests and unanswered/censored requests. The ledger gives a run ID and event sequence for every included answer.

| Family | Answers | Accepted | Stale | Below floor | Stale but confidence ≥0.5 |
|---|---:|---:|---:|---:|---:|
| army_mode | 977 | 692 | 118 | 167 | 74 |
| strategy | 159 | 88 | 9 | 62 | 7 |
| production_mix | 99 | 99 | 0 | 0 | 0 |

Derived from `jev-model-involvement-2026-10-13-evidence.json:1`, fields `answers[].reason`, `confidence`, and the run filter above. No expansion-response answers occur in this subset. The observed game-time delay (`game - request_game`) for army answers has median 0.7143 seconds and maximum 1.6518 seconds. This measures request-to-observed-answer delay, not acceptance, command execution, or guaranteed future latency.

All thirteen answered **first army requests** in this subset were marked stale **and** had confidence below 0.5. Holding historical answers and confidence fixed, eliminating their staleness would recover **zero** accepted first answers. The raw diagnostic events are:

| Run prefix | Event seq | Answer | Confidence |
|---|---:|---|---:|
| 0eb8a3ce | 41002 | attack | 0.10 |
| 61658d31 | 41463 | attack | 0.14 |
| 63447b5f | 41009 | attack | 0.14 |
| 7549f76b | 40942 | attack | 0.16 |
| 79acf2bc | 41739 | attack | 0.14 |
| 7f1aa251 | 41663 | attack | 0.06 |
| 8575bdd7 | 41224 | regroup | 0.04 |
| 9d67fcbf | 40970 | attack | 0.22 |
| a434eef7 | 40601 | attack | 0.22 |
| ab2a7eed | 41414 | regroup | 0.04 |
| b47c05df | 41208 | attack | 0.14 |
| e0663781 | 40716 | attack | 0.08 |
| f934c372 | 41649 | attack | 0.08 |

Full run IDs are in the linked ledger and the corresponding `data/jev/runs/<run_id>/events.*.jsonl`. Each listed diagnostic's `answer_request_id` is 1; its `reason` is `stale_answer`. The configured floor and subsequent confidence filtering are in `src/jev/decision.py:97` and `src/jev/strategy.py:1233` at preimplementation commit `62179e8`.

The 74 higher-confidence stale army answers are candidates for offline eligibility checks. They are **not** an expected acceptance gain: changing threats, targets, actors or controlling plans can require rejection. New questions can also change answers and confidence, which old traces cannot predict. Step 234 must separately measure timely, accepted and effective decisions using complete controlled observations, as required by plan D2–D4.

## Implementation and measurement status

Overnight work was interrupted around 02:31-02:33 Pacific. The last completed gate file was saved at 02:29; Windows stopped its event-log service at 02:31:01 and records planned OS-upgrade restarts by TrustedInstaller at 02:32:34 and02:33:19 (System event 1074, records 118357/118514). The inspected 01:30-02:45 interval contains no unexpected-shutdown event; the exact stop time of each worker is unknown. Sources: local captured `.build-step/jev-jdi-overnight/windows-restart-events.json:17`, `:38`, `:52` and `.build-step/jev-jdi-overnight/resume-file-times.json:4`. No matches or paid Typesafe calls were launched (`.build-step/jev-jdi-overnight/window.json:1`).

Step 232 is **held, unaccepted and unmerged**, with its seven-file candidate preserved and pushed at `00bc515` ([issue #355](https://github.com/aberson/Alpha4Gate/issues/355)). Full Python passed 3,419 tests (13 skipped, 2 deselected), strict mypy checked 832 files, and Ruff/prepass passed; unchanged frontend evidence is 417 passed, 6 skipped, build/lint passed. Independent review nevertheless found recovery can succeed while prior attack tasks/orders survive, plus incomplete decisive-tick actor/order assertions. The [comprehensive audit](jev-integration-safety-audit-2026-10-14.md:1) gives every affected site, remaining tests, mechanical receipts and the structural next design. Its controlled offline run `16fad34c92db4e5c8c5ea83ba3fcdcbf #1519/#1611/#1612` reproduces recovery acceptance/success with eight retained attack orders and zero recovery commands. This is mock-HTTP evidence, not a live game.

The third recurrence triggered the build-step stop-and-audit rule; no fourth patch was attempted. The required reducer also failed closed on malformed reviewer metadata, so no successful aggregate is claimed. JDI remains **0/9 accepted**. JON's partial offline contracts are separately preserved at `23c5d8b`, also unaccepted; its interrupted attempt deadline was not reset. Better timing/context questions, broader macro choices and paired games remain pending. The recommendation has not become a claim that more model involvement improves play.

The same recovery defect reproduces against the pre-candidate working source `3f89a4f`: controlled run `ec14a829a8874a498b2289635fe59e69 #1603/#1695/#1696`, with the same eight retained attack orders. It is an unresolved existing defect, not evidence of a new regression or worse play. See the audit's baseline comparison.

The approved measurement plan (Phase JDI D6–D7) uses identical repaired graph/source bytes: **S** scripted, **F** scripted fixed Stalker-heavy, **H** current hosted questions, **I** improved hosted questions. Simple64 × Terran/Protoss/Zerg × Medium/MediumHard × seeds 303/404/505 gives 18 cases per arm, 72 evaluation games plus three development smokes (38 hosted, 37 scripted). First stage is three development smokes plus the 12 Medium/seed-303 arm games. Order rotates by case; J2 held-out panels remain separate.

Pre-registered positive result: I wins at least two more of 18 cases than both S and F, loses no race aggregate, introduces no safety/avoidable-empty-lane or infrastructure failures, and applies at least ten macro decisions across at least three cases including tech and expansion. Missing exposure or incomplete panels remain inconclusive. I versus H measures the combined question changes, not the isolated causal value of broader authority. Native realtime cost estimate is $0.60–$1.20 for the planned hosted games and 6.25–12.5 hours plus launches; conservative reservations may truncate the panel. The **$5 cumulative cap includes prerequisite smokes and retries**, and the 9 a.m. cutoff takes precedence over completion. The JON budget/controller and real smoke gates remain required. No games have been launched.

Recommendation remains provisional: **repair the integration, then test broader choices against scripted decisions on the same improved graph**. The recorded latency supports testing earlier requests, while the first-answer confidence evidence rules out promising an acceptance improvement from timing alone. More, the same, or less model authority remains a measurement outcome; this checkpoint supplies no new evidence of playing strength.

Closeout checkpoint: 08:40 Pacific, 2026-10-14. All four work lanes were assessed; the existing window gate returned `STOP_ALLOWED` at the reserved closure boundary, with about 20 minutes left before 09:00. The audit and both source candidates are preserved; no background worker or game continues. Local receipt: `.build-step/jev-jdi-overnight/closeout-verdict.json:1`.
