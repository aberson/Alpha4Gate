# Phase JON: Jev overnight evaluation and advised improvement

Status: HELD AT STEP 225, 2026-10-14. Umbrella #346, steps #347-#353. No campaign implementation accepted or running. The original three-round attempt remains held; the bounded prospective simplification below does not pass or erase it.
**Objective:** Run budget-bounded, resumable Jev improvement campaigns in Observer Rush.
Owner decisions: $5 maximum per overnight campaign; Observer Rush for games;
preserve history to reduce specialization; advised iteration before evolution.
Agent defaults below are explicit and can be revised before implementation.
Preparation does not authorize an unattended paid launch or a desktop-default change. Read [gate history](../investigations/jev-improvement-gates-2026-10-12.md).

## 1. What This Feature Does

Proposal: `documentation/plans/jev-overnight-improvement-proposal.html` (publication 1, 2026-10-14).

Run sequential, bounded Jev evaluation campaigns in Observer Rush, preserve exact
before/after artifacts, and produce a morning report. First prove a frozen-parent
versus prepared-candidate campaign; then add one-hypothesis advised improvements.
This improves the harness, instructions, graph and execution code around pinned
Typesafe weights; it is not PPO training or Typesafe model fine-tuning.

Glossary: Observer Rush is the combined native StarCraft II (SC2) game and decision
viewer; Jev is Typesafe's hosted decision model; a champion is an immutable previously
accepted player snapshot; an epoch is a fixed comparison environment/panel generation.
PPO means proximal policy optimization, the separate RL (reinforcement learning) family.
OBS is the local recording application; Playwright drives the embedded Edge browser.
SQLite supplies transactions for the budget; JSON files keep manifests/reports inspectable.
The existing Python/React toolchain is reused rather than introducing a new service.

## 2. Existing Context

- `src/jev/benchmark.py` already captures/verifies source snapshots, scores production
  evidence, persists resumable cases and pins the hosted model. Its official J2
  panels and dashboard launcher are separate from the desired Observer Rush path.
- `scripts/measure_jev_speed.py` launches one frozen v2 case through Observer Rush,
  checks runtime provenance, and saves diagnostics. It has no campaign controller,
  dollar ledger, automatic candidate generation, or historical promotion gate.
- `scripts/launch-jev-battle.ps1` loads the existing DPAPI key and starts the native
  viewer. `scripts/probes/jev_battle_view.py` owns SC2 hosting and graceful stop.
  Current wrapper defaults are unbounded desktop matches, Terran, Simple64.
- `src/jev/decision.py::TypesafeProvider` is the paid HTTP boundary. Strategy and
  army use it; usage arrives after a response. Existing request counts alone are
  insufficient for a hard managed-run dollar allowance.
- Existing RL evolve has snapshotting, historical baselines, pool bookkeeping and
  regression checks, but assumes RL bot directories and its own self-play launcher.
  Its viewer is not Observer Rush. Do not invoke it unchanged for Jev.

At the original October 13 review, pacing/recovery/viewer fixes were uncommitted; those fixes are now included in ancestor `5eb0367`. Preserve any newer work and capture
candidate bytes explicitly; HEAD alone does not identify a dirty candidate. Existing Jev Steps 201-224 and deferred acceptance remain unchanged. Reserve
numeric Steps 225-231 for Phase JON, continuing the master plan numbering.
The former draft phase-local labels map in order to 225-231.

## 3. Scope

In: persistent campaign manifests, budget enforcement, immutable source/history,
weighted reference evaluation, sequential Observer Rush launches, pause/resume,
morning reports, then a bounded advised adapter in an isolated candidate workspace.

Out: headless play, parallel games, 8x/16x acceleration, PPO, model-weight averaging,
automatic desktop promotion, automatic merges/pushes, graph flattening, multi-change
evolution pools, and direct Jev self-play. Direct historical-opponent matches and
evolve-style pools follow once this controller is reliable.

## 4. Impact Analysis

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `src/jev/benchmark.py` | reuse, no signature changes | Source capture and scoring/provenance primitives | Read module contract and `capture_source`, `verify_snapshot`, `score_run`, `CaseSpec`; new campaign uses separate records/panels |
| `src/jev/decision.py` | extend | Optional budget guard at HTTP boundary, default behavior preserved | `TypesafeProvider` construction in `src/jev/runner.py`, `src/jev/benchmark.py`, `tests/test_jev_decision.py`, `tests/test_jev_benchmark.py`; grep confirms callers. Keep positional constructor and `decide` signatures; new guard keyword-only |
| `src/jev/runner.py` | extend | Optional campaign budget configuration; explicit limit stop evidence | Provider construction in `_play` at runner.py:732; existing options validated in MatchOptions and CLI; MatchOptions consumers: src/jev/{runner,benchmark,launch}.py, scripts/probes/jev_battle_view.py, tests/test_jev_{api,army,benchmark,economy,launch,live_graph,pacing,macro,plans,sc2,validation,v2}.py. Budget identity stays outside MatchOptions; match_options_record remains byte-compatible |
| `scripts/launch-jev-battle.ps1` | extend | Explicit campaign arguments including opponent race | Read full param/forwarding blocks; called by desktop shortcuts and `scripts/measure_jev_speed.py`; preserve unflagged desktop behavior |
| `scripts/probes/jev_battle_view.py` | extend | Forward race and budget descriptor to game child; campaign stop | Read `probe`, CLI, snapshot bootstrapping, control.json writer/reader. Existing tests in `tests/test_jev_battle_launcher.py`; update all internal positional probe call arguments together |
| `scripts/measure_jev_speed.py` | unchanged | Prior evidence remains reproducible | Single-case wrapper inspected; do not overload it into the new controller |
| `src/orchestrator/evolve.py`, `baselines.py`, `selfplay.py` | unchanged/reference | Reuse concepts, not RL dispatch | `run_baseline_gauntlet` equal mean; `pfsp_sample` adaptive sampling; no shared schema migration proposed |
| `documentation/master_plan.md` | extend | Register JON scope/IDs | Existing Jev range 201-224 inspected; JON namespace search returned no collision |

No existing telemetry wire field is redefined. Campaign-only files carry new schema
versions; any added optional runtime fields require enumerating their readers before
editing, including diagnostics, benchmark option comparison and tests.

## 5. New Components

- `src/jev/campaign.py`: manifest, lease, case ordering, graceful stop and resume.
- `src/jev/campaign_budget.py`: durable integer-cost reservations and reconciliation.
- `src/jev/campaign_evaluation.py`: historical registry, fixed weights, score/floors.
- `src/jev/campaign_advisor.py`: bounded proposal/apply/check adapter, added last.
- `scripts/run_jev_campaign.py`: dry-run, run, resume, stop-after-game, report CLI.
- `scripts/launch-jev-overnight.ps1`: operator launcher; dry-run by default until smoke
  acceptance. No scheduled task is installed automatically.
- `documentation/operator/jev-overnight.md`: exact start/stop/resume and morning workflow.
- Campaign-local `manifest.json`, `state.json`, append-only attempts, budget ledger,
  champion registry and report under `data/jev/campaigns/<campaign-id>/`.
- Focused tests for budget, campaign lifecycle, evidence scoring and advised isolation.

## 6. Design Decisions

### D1. $5 total, with reservations before spending

Operator ceiling: USD 5.00 per campaign, including gameplay, availability probes,
advice and coding APIs. Resume retains the same spent/reserved total. A new process
must never reset it. Subscription CLI usage is reported separately as quota usage;
unknown billing mode is not assumed free. Initial prepared-candidate mode makes no
advisor/coder calls. Steps 225-227 remain entirely offline. Later adapter uses only a billing mode it can bound and audit.

Pricing snapshot: official https://docs.typesafe.ai/models checked 2026-10-12 lists
$0.042/M input, free output for pinned `jev-1.13.0`. Reserve an entire maximum-size
request conservatively (use 65,536 input tokens until exact vendor units are verified)
before each send; release surplus only with verified usage. Probes use the same
ledger. Missing usage, timeout, cancellation and crashes retain the full reservation.
Every retry is a new reservation. Disable hidden retries or route them through it.
Use integer nanodollars with upward rounding, atomic durable reservation and one
owner. Reject reservations that would take committed-plus-reserved above $5.

The live provider key must not be available to a candidate-editing process. Keep
ledger/rates/evaluator outside its writable scope. Guard construction/launcher
coverage must be verified for strategy, army, probes and recovery after interruption.
No unmetered hosted fallback is permitted. Unsupported model/pricing or unavailable
ledger means stop, not a guessed cost. If the provider's billing rules cannot support
a conservative bound, paid launch stays disabled with that explicit reason.

This is a hard managed-campaign dispatch cap under recorded billing terms, not a
promise to cap unrelated account activity, taxes or provider-side billing changes.
Investigate a provider-side project/key allowance as defense in depth, without
claiming the account exposes one. Never log credentials.

### D2. Historical champions and weighted evidence

Interpret the operator's weighted-average request as historical evaluation, not
averaging code or neural weights. Freeze original champion, current parent and a
behaviorally distinct past champion. Agent-default weights: current parent 50%,
original anchor 25%, distinct champion 25%. Coalesce duplicate fingerprints; if no
third champion exists allocate its 25% to the original. Freeze membership and weights
before testing. Never replace the original anchor with a moving chain of winners.

Initially each version plays the SAME built-in-AI cases. For case stratum s, compute
reference R(s) = sum(weight[h] * win_rate[h,s]); report candidate difference from R
and separately from current parent. Average strata equally; do not let abundant easy
games dominate. This does not claim candidate-versus-champion head-to-head wins.
Direct Jev self-play is deferred and must get a separate adapter/acceptance plan.

Cache reference evidence only for exact fingerprint, panel, model, speed, environment
and viewer configuration matches. Record provider date/batch; play one parent control case per race at Medium at each new night, outside the
promotion panel. Any outcome disagreement with its archived control marks the epoch
non-comparable and blocks cache reuse pending a fresh reference panel. These checks
detect obvious drift, not statistical equivalence; no cross-epoch reuse is allowed. A provider
version change or incompatible environment starts a new evaluation epoch.

Development can focus on failures; promotion cases/weights cannot change after seeing
the candidate's results. An archive of previously winning cases and per-race/difficulty
floors protects skills a single weighted number could hide. A newly exposed promotion
panel becomes development data for later candidates; draw fresh sealed cases next time.

### D3. Small screening gate, explicit experimental promotion

Preserve the cheap 3/5 idea only as historical context, not as automatic strength proof.
Agent default development screen: three paired cases (one per opponent race at
Medium), six games total, fixed beforehand. Advance only when all evidence is valid,
critical scenarios pass and candidate wins at least as many cases as parent. Fewer
wins rejects this candidate; a tie merely admits the promotion panel.

Promotion panel default: Simple64, Medium and MediumHard, all three opponent races,
three new seeds each = 18 cases per version, alternating candidate/reference order.
Use separate seed lists from J2's frozen held-out panels. Materialize exact seeds and
all cases in the manifest before launch. Preparing missing reference results can take
multiple nights; incomplete evidence stays incomplete instead of reducing the gate.

Operational experimental promotion requires all of:
- Complete valid evidence and passing correctness/hosted participation checks.
- Candidate wins more cases than current parent on the same panel.
- Candidate weighted score exceeds historical reference by >=0.10 (10 percentage points).
- No fewer wins than current parent in each race and each difficulty aggregate.
- No regression on a separately frozen critical-behavior scenario suite.

Report regressed for a failed correctness floor, fewer overall wins than parent, or
a lost race/difficulty floor; report inconclusive for all other non-passing complete
panels (including tied/ceiling results). Incomplete panels have status incomplete.
Include raw counts; never silently loosen
thresholds. This is a bounded panel verdict, not a population/statistical significance
claim. No endless retry-until-pass. Each candidate gets one screen and one promotion
panel; a changed candidate gets a new fingerprint and assessment record.

Timeouts/draws are non-wins; bot errors fail reliability. Infrastructure interruptions
make the assessment incomplete; retain attempts and allow explicit bounded resume.
Normal stale-answer fallback remains measured. Zero accepted hosted decisions cannot
support a hosted improvement claim. Record strategy overrides and fallback duration;
request counts do not establish which component caused a win.

### D4. Difficulty and pacing

Start Medium/MediumHard assessments; retain easier regression cases when moving up.
Agent default advancement: >=80% wins over two predeclared 15-case batches on current
difficulty (five seeds per race), with >=3/5 wins for every race in each batch.
Advance one level only, at a campaign boundary. A challenge game does not change the
curriculum. Map expansion and higher difficulty are separate experiments.

First overnight trial uses a fixed 2x cap as a conservative agent default. Prior 4x
cases demonstrate feasibility, not model-effectiveness equivalence. Keep speed fixed
within comparisons and record achieved speed/decision age. Validate a faster profile
separately before switching. No adaptive mid-game speed changes during evaluation.

### D5. Overnight lifecycle

Agent default: six-hour window, one game at a time, 900 game seconds / 1200 wall
seconds / 450 requests per game, recording off, camera and local caster on. Explicit
stop-after-game and stop-now use Observer Rush's graceful stop path. Do not kill SC2.
Check a match's full time allowance fits the remaining window before starting it;
reserve evaluation time before creating another candidate. End by the configured
deadline through an earlier graceful-stop margin; an unresponsive child blocks further
dispatch and is reported, not hidden behind a successful shutdown claim.

One campaign lease prevents overlapping launches. Keep the Windows interactive session
available; verify actual overnight behavior rather than assuming locked/sleeping desktop
support. No silent fallback to separate windows or headless play. Host each exact run
before SC2 starts. Initially reopen the existing combined viewer per case for every case;
continuous single-window reuse is optional later UX work.

Morning report: completed/remaining cases, run IDs/replays/traces, source fingerprints,
actual and reserved costs, model participation, scores/floors, failure reasons and next
safe action. A terminal chat response never implies a background runner exists: only
report a campaign running after verifying its real process, heartbeat and first case.

### D6. Advised before evolve

After the fixed-candidate overnight acceptance, propose and implement one hypothesis
at a time in an isolated worktree with an explicit allowlist. Freeze/test the exact
resulting bytes; do not ask a second model to regenerate a supposedly identical patch.
The advisor can change player instructions/graph/execution via reviewed boundaries;
it cannot modify budgets, rates, gates, sealed seeds, credentials or viewer infrastructure.
Some protected infrastructure and player execution currently share `src/jev`; enforce
file-level scope rather than giving the entire package write access.

Store accepted experiments in a separate Jev champion registry. Desktop default and
RL current pointer stay unchanged. Promotion is local experimental selection, not
automatic git merge/push. Multi-change pools and composition need a later plan after
one-change iterations can run and resume reliably.

### D7. Persistence and launch contracts (review clarification)

**Implementation ownership (prospective clarification, 2026-10-14):** Step 225
owns immutable preparation contracts and pure D2/D3 scoring over typed offline
facts. Its reports must say `unverified/offline`, cannot authorize promotion, and
must refuse authoritative live verdicts. A caller-authored exit code, timestamp,
content hash or copyable object seal is never proof that a process ran. Step 226
owns durable paid-request reservations. Step 227 owns execution records, exact
run attachment, window lifecycle and resume; only its verifier may supply live
facts and authoritative reports. It must retain actual process/test output and
check source, case, attempt, window and archive identity, reject replay and
invalid critical-test execution, and invalidate epoch cache reuse on control
disagreement. These are acceptance requirements, not a mandate for a new MAC
service or a second evidence ledger. Registry changes still require the complete
D2/D3 verdict. The D7 live records below are the completed controller contract,
not a claim that Step 225 already authenticates execution.

The original Step 225 checkout and three failed review rounds remain preserved.
A fresh campaign-only simplification may receive one bounded implementation
attempt (continuation round 4/10), at most two hours and one required review batch.
If execution authority is still conflated with offline scoring, hold this attempt
and resolve that implementation in Step 227; do not add another verifier layer.
Serialize changes to shared benchmark, runner, launch and v2 tests against JDI.
JDI paid work still requires reviewed Steps 225-227 and the real Step 228 smoke.
All paid prerequisite work for the approved JDI investigation shares its single
$5 cumulative allowance; a new campaign ID does not replenish that allowance.

New records use `schema_version: 1`. Campaign/run/reservation/attempt IDs are random
UUID4 lowercase 32-hex strings, generated once and retained. Fingerprints are SHA-256
hex over source bytes; money is nonnegative integer nanodollars (ceiling 5,000,000,000).
Times persisted across processes are UTC ISO8601; elapsed deadlines use a monotonic
clock within a process. Reject unknown schemas and paths outside the selected root.

| Record | Required fields and invariants |
|---|---|
| `manifest.json` | campaign_id, created_at, mode (`prepared`/`advised`), model, pricing snapshot/hash, budget_nanodollars, window_seconds, source fingerprints/directories, environment fingerprint, viewer hashes/settings, cases, champion weights, scoring rule version, critical-suite hash. Write once; persist its digest in state. |
| Case | case_id, role (`screen`/`promotion`/`control`), source fingerprint, map/race/difficulty/seed, requested speed, game/wall/request limits, assessment_id and order. Generate development seeds from a recorded random seed; generate promotion seeds with secrets.randbelow(MAX_SEED+1), exclude 11/101/202 and all previously exposed seeds in this campaign lineage. Freeze before requests. |
| `state.json` | campaign_id, manifest_digest, status (`prepared`/`running`/`pausing`/`paused`/`complete`/`blocked`/`failed`), owner PID/start identity, heartbeat, current case, per-case attempts/results, stop reason, window deadline. Atomic same-directory replace; corrupt state blocks resume. |
| `budget.sqlite3` | metadata(manifest_digest, ceiling, pricing_hash); reservations(id, action_id UNIQUE, max_cost, actual_cost nullable, state); events(sequence, reservation_id, action, timestamp). SQLite BEGIN IMMEDIATE transactions, synchronous FULL, durable commit before HTTP. Nonterminal reservations count at maximum; settle at most once; reject actual usage above the bound and block all further dispatch. |
| `attempts.jsonl` | attempt_id, case_id, run_id, start/end, outcome/error, evidence paths and source verification. Append/flush; trailing torn record is quarantined and reconciled against state/ledger, never silently counted successful. |
| champion registry | fingerprint, path, parent fingerprint, assessment ID, original flag, promotion timestamp, weight-role. Atomic local update only after full verdict; no RL pointer or desktop mutation. |
| `report.json` / `report.md` | manifest digest, raw case counts, per-stratum and aggregate scores, comparison verdict, costs/reservations, participation diagnostics, incomplete work and next action. Derived from verified artifacts, not editable inputs to scoring. |
| budget descriptor | schema_version, campaign_id, manifest_digest, absolute ledger path and pricing_hash. Contains no key. Only the campaign owner writes it; consumers verify against the immutable manifest. |
| campaign-result artifact | schema_version, campaign_id, case_id, run_id, host_exit_code, child_exit_code, attached_run_id, runtime_root, stop_reason, completed_at. Missing/inconsistent identity makes evidence invalid. |
| advisor profile | schema_version, provider=`anthropic`, versioned model_id, verified_at, pricing_source_url, input/output nanodollars per token, max_input_tokens, max_output_tokens, timeout_seconds. No key; load ANTHROPIC_API_KEY only into controller API calls. Disable SDK retries; each explicitly retried call reserves separately. |
| advisor response | hypothesis text, predicted metric/direction, unified_patch text, touched_paths array. Reject absent/extra fields, path mismatch and overlarge responses; no tool calls. |

`assessment_id` is also UUID4 hex. A lineage ID is the original source fingerprint;
its append-only exposed-seed inventory lives outside model input alongside the registry.
Stratum means one race/difficulty pair; win_rate is wins divided by all complete valid
cases in that stratum, including draws/timeouts as non-wins. Source references contain
version=2, absolute directory, fingerprint and policy_hash. Environment fingerprint
covers OS/Python, SC2 build, map hash, uv.lock hash and installed dependency versions.

Use an OS-held exclusive lock on a campaign-root lock file plus PID/start metadata.
The OS releases ownership on crash; missing heartbeat alone never permits takeover.
A root-wide launch lock prevents a second campaign sharing this SC2 desktop. On
restart reconcile running attempts first; mark them interrupted, preserve spend,
and require `--retry-interrupted` for one replacement attempt per case. Never replay
completed outcomes. A retry has a new run/attempt ID and the same case identity.

The controller launches the existing PowerShell wrapper with an argument list (no
shell-built interpolation), forwarding explicit opponent race, source snapshot,
run ID, limits, speed and budget descriptor. Add `-OpponentRace` / `--opponent-race`
through wrapper -> probe -> game-child -> existing MatchOptions.opponent_race.
Add `-CampaignBudget` / `--campaign-budget` through the same path; this is an absolute
descriptor file path checked against manifest/root, not a credential or rate override.
Keep MatchOptions and its existing metadata shape unchanged: use a separate optional
campaign execution context on the runner entry path. Existing launches pass none.
If adding a keyword-only context to run_match, update its internal _play path;
verified call-site files are runner.py, scripts/probes/jev_battle_view.py and
tests/test_jev_{validation,v2,sc2,plans,macro,live_graph,launch,economy,decision,benchmark,api,army}.py.

Runner-created TypesafeProvider receives an optional keyword-only budget guard.
Its async decide reserves before POST, reconciles even low-confidence/stale answers,
and preserves the reservation on ambiguous failure. Campaign probes construct that
same guarded provider directly; do not reuse unguarded benchmark.probe_model.
SQLite work stays short and runs off the event loop where it can wait for a lock.
Budget exhaustion emits a stable `campaign_budget_exhausted` control reason and
requests graceful match termination; it must not silently keep scoring scripted play.
Campaign-level reason is separate from existing JevError/RunStatus wire enums.

Use benchmark.score_run with a RunExpectation matching the exact frozen source and
MatchOptions; independently check native host exit, attached run ID, verified game-child
exit and source hashes. Do not supply fabricated ChildExit flags (that structure has
legacy forced-termination semantics). Host must emit a small campaign-result artifact
with real child exit and attachment identity for the controller. Losing the viewer
blocks further dispatch; no benchmark terminate_process_tree call is reused.

Stop control is an atomic campaign `control.json` with action `stop_after_game` or
`stop_now` and monotonically increasing revision. The controller polls during launch
and gameplay; stop_now forwards the existing battle stop control without replacing
unrelated camera fields. At 60 seconds before the wall deadline request stop; allow
60 seconds for graceful exit, then block and report a lingering owned process rather
than killing SC2. No new game unless its entire 1200-second allowance fits. Record
overshoot if a process fails to cooperate; never report guaranteed termination.

### D8. Historical compatibility and advised isolation

Freeze the first JON parent only AFTER budget instrumentation lands. Older snapshots
without the guarded path are archived evidence, not executable campaign baselines;
never retrofit bytes under their old fingerprint. Start a new JON epoch with the
instrumented v2 parent as original anchor, then freeze a prepared v2 candidate on the
same infrastructure. Existing pre-JON speed/recovery evidence remains separate. If
parent/candidate fingerprints coincide, report no-change and do not spend on a strength
comparison. The prepared candidate must be selected explicitly at campaign creation.

Critical scenario suite for the initial epoch: recovery completion/renewal, emergency
defense preemption, production while hosted decisions are pending, stale-answer
revalidation, and legal cancellation of resource reservations. Reuse the corresponding
decision/runtime/plans tests with exact test IDs materialized in the manifest. Run both
sources against the same frozen suite; a missing test/fixture is invalid, not skipped.
Archived “winning” games never supply tests or seeds to a supposedly sealed panel.

Step 230 uses the existing `anthropic` SDK through a new explicit paid API adapter,
not a general-purpose coding CLI with unknown subscriptions/retries. A runtime advisor
profile supplies a versioned model ID, official dated price evidence, context/output
limits and a computable worst-case charge. Missing profile/key disables advised mode.
No provider/model choice is hardcoded from a stale price list; validate the selected
profile before spending and count all tool/context tokens under the same budget.

The model returns structured hypothesis plus unified patch, with no tools or local
filesystem access. The controller applies it in an isolated worktree only after path,
symlink and protected-file checks. Initial allowlist: `bots/jev/v2/policy.json`,
`src/jev/strategy.py`, `src/jev/recovery.py`, `src/jev/runtime.py`. Protect the imported
HTTP boundary, runner, budget modules, pricing, evaluator, tests and dependency files.
Reject edits outside that list, external service calls, credential/environment access,
dynamic import/subprocess changes, and no-op patches before executing candidate tests.
Tests run with provider credentials removed; gameplay injects only the Typesafe key.
This is reviewed trusted-code isolation, not a security sandbox for hostile generated
Python. If the adapter cannot establish its boundary, reject the candidate rather than
weakening the guard. Game traces and external text are evidence, never instructions.

### D9. Build environment and commands

Existing stack: Windows interactive desktop, Python >=3.12, uv, Node/npm, SC2 with
Simple64 map, Edge/Playwright, optional viewer dependencies, isolated OBS profile
BattleProbe on loopback 4457. Dashboard uses existing 8765/3000; no new network
listener or API/WS schema. DPAPI means Windows encrypted local secret storage:
reuse `%LOCALAPPDATA%/Alpha4Gate/typesafe-key.dpapi`, never copy it into source snapshots.
The existing operator battle-view guide owns OBS setup; prerequisites failing means
stop with their names, not automatic alterations to another OBS profile.

Commands from repository root (frontend commands use npm --prefix):
- Install: `uv sync --extra dev --extra viewer`; `npm --prefix frontend ci`.
- Dev dashboard: `powershell -NoProfile -File scripts/launch-a4g.ps1 -NoBrowser -NoWait`.
- Build: `uv build`; `npm --prefix frontend run build`.
- Tests for the automated span: `uv run --extra viewer pytest tests/test_jev_campaign.py tests/test_jev_campaign_budget.py tests/test_jev_campaign_evaluation.py tests/test_jev_decision.py tests/test_jev_benchmark.py tests/test_jev_battle_launcher.py`.
- Typecheck new modules: `uv run mypy src/jev/campaign.py src/jev/campaign_budget.py src/jev/campaign_evaluation.py --strict`.
- Lint new modules/CLI: `uv run ruff check src/jev/campaign.py src/jev/campaign_budget.py src/jev/campaign_evaluation.py scripts/run_jev_campaign.py`.
- Also lint touched existing files and run their affected tests. Capture existing
  probe typing debt before edits and reject new diagnostics rather than claim that
  legacy probe has a clean strict-mypy baseline. No frontend changes are planned.
- Step 230 extends these commands with campaign_advisor.py and its test module.

New CLI contract to implement (not available before Step 227):
`prepare --parent SNAPSHOT --candidate SNAPSHOT --root PATH` writes a no-network
manifest and emits campaign ID plus planned limits/cost upper bound; default six-hour
window, $5 ceiling, 2x, Simple64. `run --campaign DIR` explicitly launches prepared
work; `resume --campaign DIR [--retry-interrupted]` preserves budget and results but
starts a new six-hour window after lease reconciliation. `stop --campaign DIR
--after-game` and `stop --campaign DIR --now` set control actions. `report --campaign
DIR` is read-only. `--dry-run` on prepare is equivalent to prepare; a bare invocation
prints usage with no network/process launch. The PowerShell overnight launcher defaults
to prepare; require `-Start` plus a prepared directory to launch, including after UAT.

Exit codes: 0 for prepare/report or terminal clean completion; 2 invalid input/schema;
3 paused/budget/deadline with resumable work; 1 failure/blocked evidence or infrastructure.
Auth failure and model drift stop the campaign without retry. Unknown billing remains
blocked. Automatic evolution scheduling is outside this phase; the operator launches
each night after confirming the desktop is available. No paid action occurs in prep.

## 7. Build Steps

<!-- autofix-applied: 2026-10-13 -->
### Step 225: Freeze campaign contracts and weighted evaluation
- **Problem:** Implement schema-versioned manifests, champion references, exact case matrices and deterministic scoring described in D2/D3.
- **Type:** code
- **Status:** BLOCKED (2026-10-13)
- **Files:** src/jev/campaign.py; src/jev/campaign_evaluation.py; scripts/run_jev_campaign.py; tests/test_jev_campaign.py; tests/test_jev_campaign_evaluation.py
- **Existing context:** Sections 2 and 6 define the production boundaries; D7 assigns offline contracts/scoring here and execution authority to 227. Preserve the held original attempt and official J2 panels/desktop defaults.
- **Issue:** #347
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** `campaign_evaluation.py`, manifest portions of `campaign.py`, focused tests, dry-run/report entrypoint skeleton.
- **Done when:** Dry-run writes/validates an immutable no-network manifest; tests cover duplicate anchors, missing strata, unchanged candidate, weighted improvement masking race regression, invalid evidence and J2 panel isolation. The production CLI labels offline scores unverified and refuses live verdicts/promotion until 227 supplies verified execution facts; caller claims cannot enable an authoritative report.
- **Depends on:** none

<!-- autofix-applied: 2026-10-13 -->
### Step 226: Enforce the managed $5 ceiling
- **Problem:** Add durable reservations at every campaign-paid request boundary, including probe and failure paths; validate vendor bounds before enabling paid mode.
- **Type:** code
- **Status:** PENDING
- **Files:** src/jev/campaign_budget.py; src/jev/decision.py; src/jev/runner.py; tests/test_jev_campaign_budget.py; tests/test_jev_decision.py; tests/test_jev_benchmark.py
- **Existing context:** Sections 2 and 6 define the production boundaries; implement only this step, preserving official J2 panels and desktop defaults.
- **Issue:** #348
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** `campaign_budget.py`, optional provider integration, budget descriptor plumbing, focused boundary/crash tests.
- **Done when:** Concurrent/restarted reservation attempts cannot overspend; missing usage retains reservation; probes/retries are charged; unsupported pricing blocks before HTTP; unflagged desktop tests remain valid. Document verified pricing/billing assumptions and any account-side cap availability without inventing it.
- **Depends on:** 225

<!-- autofix-applied: 2026-10-13 -->
### Step 227: Sequential Observer Rush controller and launcher
- **Problem:** Run frozen parent/candidate cases through the combined viewer with explicit race, speed, limits, budget identity and graceful lifecycle.
- **Type:** code
- **Status:** PENDING
- **Files:** src/jev/campaign.py; scripts/run_jev_campaign.py; scripts/launch-jev-overnight.ps1; scripts/launch-jev-battle.ps1; scripts/probes/jev_battle_view.py; tests/test_jev_campaign.py; tests/test_jev_battle_launcher.py; documentation/operator/jev-overnight.md
- **Existing context:** Sections 2 and 6 define the production boundaries; implement only this step, preserving official J2 panels and desktop defaults.
- **Issue:** #349
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** `campaign.py`, `run_jev_campaign.py`, `launch-jev-overnight.ps1`, additive native-wrapper forwarding, operator guide.
- **Done when:** Offline lifecycle tests cover pause/restart/no duplicate cases, stale leases, deadline/budget refusal, wrong snapshot and viewer failure. CLI default is dry-run; real runs require explicit start. Code validates exact run attachment and preserves existing desktop defaults. Controller-owned execution records bind actual process exits and retained output to source/case/attempt/window/run identity; caller wrappers or prior archives cannot be rebound into valid evidence. Missing, skipped, xfailed or zero-collected critical tests cannot pass. Control disagreement invalidates epoch cache reuse; only verified facts can produce authoritative D2/D3 reports.
- **Depends on:** 226

<!-- autofix-applied: 2026-10-13 -->
### Step 228: Real pipeline smoke before unattended work
- **Problem:** Prove a real Typesafe -> budget -> Jev -> Observer Rush -> report cycle before a long soak.
- **Type:** operator
- **Status:** PENDING
- **Files:** Read-only: documentation/operator/jev-overnight.md; data/jev/campaigns/<campaign-id>/; data/jev/runs/<run-id>/
- **Existing context:** Sections 2 and 6 define the production boundaries; implement only this step, preserving official J2 panels and desktop defaults.
- **Issue:** #350
- **Produces:** Recorded smoke evidence and operator acceptance, no code artifacts.
- **Done when:** At least 60 seconds after first accepted hosted response, exact game is visible and ledger/trace/report agree; stop-after-game and resume are exercised across two cases without duplicate results or budget reset. Stay inside one shared $5 campaign ceiling; record spend. Any required code fixes return to 226/227 before retry.
- **Depends on:** 227

<!-- autofix-applied: 2026-10-13 -->
### Step 229: Observe the first fixed-candidate overnight campaign
- **Problem:** Exercise six hours of realistic unattended lifecycle, or terminate earlier for budget/exhausted work/failure, and preserve the outcome honestly.
- **Type:** wait
- **Status:** PENDING
- **Files:** Read-only: campaign manifest/ledger/report and run archives; no shipped source edits
- **Existing context:** Sections 2 and 6 define the production boundaries; implement only this step, preserving official J2 panels and desktop defaults.
- **Issue:** #351
- **Produces:** Morning report, process/heartbeat evidence, source-verified case archives and operational verdict.
- **Done when:** Real campaign is monitored at start and completion; multiple case transitions occur, terminal state reconciles reservations, no orphaned match dispatch or overwritten evidence, and a resume test preserves unfinished work. Early failure is recorded as failure, not passed overnight acceptance. Strength improvement is not required for operational success.
- **Depends on:** 228

<!-- autofix-applied: 2026-10-13 -->
### Step 230: Add bounded one-change advised iterations
- **Problem:** Adapt the advised pattern to isolated Jev candidates and the tested campaign controller, within the same total spending ceiling.
- **Type:** code
- **Status:** PENDING
- **Files:** src/jev/campaign_advisor.py; src/jev/campaign.py; scripts/run_jev_campaign.py; tests/test_jev_campaign_advisor.py; documentation/operator/jev-overnight.md
- **Existing context:** Sections 2 and 6 define the production boundaries; implement only this step, preserving official J2 panels and desktop defaults.
- **Issue:** #352
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** `campaign_advisor.py`, allowlist and billing adapter, CLI advised mode, report extensions and isolation tests.
- **Done when:** One proposal yields one exact frozen artifact; policy/graph correctness tests precede games; rejected/malformed/out-of-scope/no-op proposals do not launch evaluations; every paid advisor action has a conservative bound. Unknown billing mode disables advised dispatch while fixed-candidate evaluation remains available.
- **Depends on:** 229

<!-- autofix-applied: 2026-10-13 -->
### Step 231: Observe a complete advised cycle
- **Problem:** Verify automatic diagnosis, candidate construction, evaluation and checkpointing with real components and budgets.
- **Type:** wait
- **Status:** PENDING
- **Files:** Read-only: advised campaign evidence and isolated candidate artifacts; no shipped source edits
- **Existing context:** Sections 2 and 6 define the production boundaries; implement only this step, preserving official J2 panels and desktop defaults.
- **Issue:** #353
- **Produces:** Real advised campaign evidence and measured verdict; no automatic default-player switch.
- **Done when:** At least one full cycle reaches improved/regressed/inconclusive with exact artifact provenance, followed by a safe next-cycle boundary; overnight duration tests reservation/lifecycle behavior. Failure remains visible and blocks evolution-pool rollout.
- **Depends on:** 230

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| Cost bounds | Token estimates and failed calls can undercount | Reserve documented maximum, keep ambiguous charges, refuse unsupported billing; confirm terms before paid launch |
| Coding model billing | Gameplay cents do not predict development cost | Prepared-candidate mode first; bounded adapter required before advised mode |
| Historical specialization | Weighted average hides a lost skill | Permanent anchor, per-stratum floors, critical scenarios and new assessment epochs |
| Historical self-play | Existing RL gauntlet cannot simply host Jev | Initial shared-case reference scores; direct head-to-head explicitly deferred |
| Small samples | False winners and repeated-testing bias | Bounded panel claims, no rerun-until-pass, retain inconclusive outcomes |
| Night-time Windows state | Sleep, locked desktop or window loss can halt native hosting | Real smoke/overnight evidence, checkpoint, fail closed on viewer loss |
| Existing dirty work | HEAD omits runtime changes | Frozen byte fingerprints, isolated candidate workspace, no broad stash/reset |
| Pricing drift | Saved rate may become stale | Dated official-source verification and pinned model before campaigns; stop if bounds unavailable |

No operator answer blocks drafting/building the offline preparation. Actual first
night start time and confirmed interactive-desktop availability are needed at launch,
not guessed by scheduling a task now. Advisor billing choice is resolved during
230 implementation and cannot bypass the total $5 limit.

## 9. Testing Strategy

Use adversarial behavioral tests for budget exhaustion, simultaneous reservations,
lost responses, restart with pending spend, panel incompleteness, hidden race regression,
duplicate cases, snapshot mismatch, lease ownership and graceful cancellation.
Run existing decision/benchmark/launcher tests when their seams change. Test no-network
dry-run by making network dispatch fail if called. Do not add tests that only mirror
JSON field literals. Report scoped checks accurately; do not claim full-suite success
unless run. Real smoke precedes deliberate overnight observation (228 -> 229).

Next preparation pipeline: `/plan-expedite --plan documentation/plans/jev-overnight-improvement-plan.md`.
Review/wrap and issue sync completed 2026-10-13. Build Steps 225-227, then stop before
operator Step 228 (#350). Reports: [review](../investigations/jev-overnight-plan-review-2026-10-13.md),
[wrap](../investigations/jev-overnight-plan-wrap-2026-10-13.md),
[sync](../investigations/jev-overnight-repo-sync-2026-10-13.md). No paid games
are required for plan review or offline steps 225-227.

## Appendix

### Decision Inventory

| ID | P/D | Choice | Status |
|---|---|---|---|
| P1 | P | $5 maximum; the JDI investigation uses one cumulative allowance including prerequisites | Approved in session, 2026-10-14 |
| P2 | P | Observer Rush; preserve history; advised improvement before evolution | Recorded operator choices |
| P3 | P | Work on the approved JDI dependency through 9 a.m. Pacific October 14 | Approved in session |
| D1 | D | Immutable manifests and weighted D2/D3 panels | Existing agent default |
| D2 | D | Durable reservation before each paid dispatch; fail closed when billing is unknown | Existing agent default |
| D3 | D | Sequential controller with exact source/run identity and resumable state | Existing agent default |
| D4 | D | Step 225 offline contracts; 227 execution authority; no authoritative fixture reports | Clarified 2026-10-14; original hold preserved |
| D5 | D | One simplified continuation attempt, two hours and one required review batch | Selected 2026-10-14 after independent necessity challenge |
| D6 | D | Real native smoke, then fixed-candidate observation, then bounded advised work | Existing agent default; live gates remain |
