# Phase JI: Connect the player to Typesafe Jev

Status: COMPLETE; LIVE ACCEPTANCE PASSED (2026-10-08). User authorized plan adjustment and implementation together.
This corrects the provider misunderstanding in Phase JV; that phase built a local
scripted player, not Typesafe's model. Historical live acceptance evidence is not
inferred from the user's positive play report. Steps 207/208 retain their recorded status.

## 1. What This Is

Proposal: documentation/plans/jev-typesafe-proposal.html

Progress: [Phase JI inventory and evidence](../jev-typesafe-progress.html).

Give the actual hosted Typesafe Jev model control of the existing player's army
intent: attack, defend, or regroup. Retain the working four-Gateway Zealot rush,
game adapter, task lifecycle and inspectable graph. Establish the connection and
prove model answers affect issued commands before adding more model decisions or evolution.

## 2. Existing Context

`src/jev/bot.py:JevController._step` observes, ticks and issues commands;
`runtime.py` synchronously interprets four root lanes every 0.25 game seconds.
`operations.py` validates allowlisted predicates/actions. The packaged v1 policy
contains defense, attack and rally branches, including a four-ready-Zealot launch
gate. `telemetry.py` records node-attributed diagnostic facts using the existing
Event schema. `JevTab.tsx` already displays the archived graph and recent events.
`pyproject.toml` already declares httpx; no new dependency is needed.

## 3. Scope

In: opt-in Typesafe provider, compact visible state, typed Choice request, bounded
async scheduling, graph branch guards, fallback attribution, dashboard decision
panel, offline boundary tests and live validation instructions.

Out: model-selected production, expansion, placement or individual actors;
evolution; embedded SC2 viewer; legacy RL changes; general-purpose graph authoring
by the runtime model. Local legality checks remain authoritative.

## 4. Impact Analysis

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `src/jev/runner.py` | extend | Provider/model/request-budget CLI options and credential preflight | Read parser, MatchOptions, run_match, _play; callers in bots/jev/v1/__main__.py and tests/test_jev_{sc2,army,economy,api,telemetry,validation}.py; defaults retained |
| `src/jev/bot.py` | extend | Poll decisions before each tick; close pending task on leave/end | JevController production constructor in runner._play; test constructors in test_jev_sc2.py and test_jev_army.py; optional keyword preserves callers |
| `src/jev/runtime.py` | extend | Store army mode and node-attributed evidence | Tick signature unchanged; _View is RuntimeView implementation; runtime callers are bot and test_jev_{runtime,policy,sc2}.py |
| `src/jev/operations.py` | extend | army_mode_is predicate | Read RuntimeView, predicate registry, graph validator; new method implemented in _View |
| `bots/jev/v1/policy.json` | extend | Explicit army branch guards; regroup after launch | Loader/validator, runtime, policy/economy/army tests and frontend JevTab tests consume packaged policy; archived policies are immutable |
| `frontend/src/components/JevTab.tsx` | extend | Source, answer and fallback evidence | Existing recent_events facts are already parsed as bounded JSON; no API or wire shape change |
| operator guide, README, CLAUDE, JV/master plans | modify | Correct provider naming and give launch/verification procedure | Read existing CLI documentation and stale JV status entries |

## 5. New Components

`src/jev/decision.py`: Typesafe HTTP boundary, strict response validation, state
summary and per-match request coordinator. Tests cover service contract, timing,
graph execution, persistence and dashboard presentation.

## 6. Design Decisions

**Operator choices:** use the actual Typesafe model; keep the four-Gateway rush,
first attack at four Zealots; first connect army intent; prove it works before
evolution. LLM coding tools may author the graph between matches; Typesafe Jev
evaluates the live state during matches.

**Implementation defaults:** retain `bots.jev.v1` with explicit
`--decision-provider scripted|typesafe` (default scripted), `--decision-model
jev-latest` and `--decision-max-requests 450`. Read `TYPESAFE_API_KEY` from the
environment only. No credential in policy, logs or run artifacts. Missing key
fails before SC2 launches. The policy hash identifies the executable graph;
decision events separately identify requested/returned model and provider.

Use the documented [HTTP API](https://docs.typesafe.ai/api) with the existing
httpx dependency instead of adding the optional SDK. POST `/v1/systemone`, one
`army_mode` Choice question, fixed strategy instructions, available options and
fog-of-war-safe state. Validate choice, probabilities, confidence, model and usage.
No retries inside a request; 401/403 disables subsequent requests for that match.

At most one request in flight, at least two wall seconds between starts, total
deadline 1.5 wall seconds, cap 450 calls. Accept replies only while their strategic
signature (army, home structures, nearby threats, launch latch, available choices)
matches and the observation is <=8 game seconds and <=5 wall seconds old.
Confidence below 0.5, timeout, malformed responses and expired replies use the
scripted branches. Current commands continue while waiting; no network await on
the gameplay callback. Cancel requests when leaving or ending. Accelerated SC2
may outrun answers: recommend `--realtime` for initial live validation and record
stale replies honestly. These timing/confidence values are initial defaults, not
measured optimal settings.

Attack stays gated by the existing first-wave requirement. Defend is offered only
with ready Zealots and visible enemy ground units (excluding structures) within
the defense radius of the home `start_location`, excluding targets the runtime
has demoted as unreachable, matching the graph's defense selection. Regroup returns to the
existing home rally point, including after launch. Target selection and command
acknowledgment remain local. Every branch remains visible in the archived graph.

Evidence uses the existing diagnostic Event facts rather than changing the API
schema: provider, effective source, current intent, last response, question,
offered options, confidence/probabilities, returned model, latency, age, request
count and token usage. Tokens are not a currency bill; no guessed pricing.

## 7. Build Steps

<!-- autofix-applied: 2026-10-08 -->
### Step 209: Connect Typesafe to graph execution (JI.1)
- **Problem:** Make a bounded live model choice actually control army commands.
- **Type:** code
- **Status:** DONE (2026-10-08; direct implementation, offline verification)
- **Issue:** not minted for this direct user-authorized implementation
- **Flags:** --reviewers deep
- **Files:** src/jev/decision.py (new), src/jev/runner.py, src/jev/bot.py, src/jev/runtime.py, src/jev/operations.py, bots/jev/v1/policy.json, tests/test_jev_decision.py (new), existing tests/test_jev_*.py regression coverage.
- **Produces:** decision module, CLI/controller wiring, graph guards and integration tests.
- **Done when:** Slow requests do not block game steps; only one is pending; bad/stale answers fall back; request cap and stop cleanup work; model choices change issued army commands through the production controller; scripted regression suite passes.
- **Depends on:** Phase JV automated Steps 201-206

<!-- autofix-applied: 2026-10-08 -->
### Step 210: Inspect and document the connection (JI.2)
- **Problem:** Make model control and fallback unmistakable in the dashboard.
- **Type:** code
- **Status:** DONE (2026-10-08; direct implementation, offline verification)
- **Issue:** not minted for this direct user-authorized implementation
- **Flags:** --reviewers full --ui
- **Files:** frontend/src/components/JevTab.tsx, frontend/src/components/JevTab.css, frontend/src/components/JevTab.test.tsx, tests/test_jev_decision.py (new), documentation/operator/jev-validation.md, README.md, CLAUDE.md, documentation/master_plan.md, documentation/plans/jev-player-plan.md.
- **Start-cmd:** bash scripts/start-dev.sh
- **URL:** http://localhost:3000/?tab=jev
- **Produces:** dashboard panel, evidence round-trip tests, guide and updated project docs.
- **Done when:** Provider evidence survives actual recorder/reader/API handling; UI distinguishes scripted/model/fallback, pending and last-known state; old runs still load; backend and frontend checks are recorded with their scope.
- **Depends on:** JI.1

<!-- autofix-applied: 2026-10-08 -->
### Step 211: Validate actual Typesafe gameplay (JI.3)
- **Problem:** Prove the hosted service and SC2 complete the live decision cycle.
- **Type:** operator
- **Status:** DONE (2026-10-08; real service/gameplay and pending-stop validation)
- **Issue:** not minted
- **Files:** data/jev/runs/ (generated run evidence), documentation/operator/jev-validation.md (execute existing procedure; no code authoring).
- **Produces:** Run ID, archived policy hash, returned model, accepted model-decision event and corresponding command, token counts, screenshot and match finding report.
- **Done when:** With a locally configured key, observe at least 60 seconds after the army becomes eligible; verify a real reply affects a graph branch and command, then complete a full match. Verify stop during a pending request, inspect fallback when encountered, and record response latency/stale rate. Do not claim scripted-only play or mocks prove the connection. Keep pending if credentials/service/SC2 prevent validation.
- **Depends on:** JI.2

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| Latency / accelerated game time | Answer is obsolete before receipt | Dual age limits, signature check, realtime first smoke |
| Fallback masks disconnected model | Working bot appears model-driven | Explicit provider/source, calls, errors and accepted-command evidence |
| Model intent oscillates | Repeated retreats / lost pressure | Two-second cadence and existing task deduplication; inspect live findings before tuning |
| Credentials / access unavailable | Cannot prove hosted inference | Finish offline code verification and report live gate pending |
| New regroup after launch | Old attack task retries could override new intent | Test mode transitions and ensure old conflicting army tasks are cancelled |

## 9. Testing Strategy

Iterate with focused decision and existing Jev tests, ruff and strict mypy.
Boundary tests use httpx transport fixtures; integration scenarios use the real
controller, packaged graph, adapter/task execution and real telemetry writer and
reader. These substitute only for repeatable offline validation, not JI.3.
Run full declared Python/frontend suites for the completion gate and report
pre-existing failures separately. Live smoke precedes a full match; the match is
the deliberate observation phase for this async gameplay feature.

Planning review, operator/default decision inventory and self-containedness check
are recorded alongside this plan. No historical issue is closed by this change.

## 10. Contracts, storage and first run

StarCraft II (SC2) is the game; burnysc2 is the existing Python game adapter.
httpx is the existing asynchronous HTTP client. React/TypeScript render the
dashboard, Vite runs its development server, pytest/vitest test Python/frontend,
ruff/eslint lint them, and mypy/tsc check their types. Keep these existing tools.

The request goes only to `https://api.typesafe.ai/v1/systemone` with bearer auth.
Request fields are `model: string`, `state: object`, and
`questions.army_mode: {type: "choice", instructions: string, criteria: map<string,string>}`.
Response fields are `model: string`, `answers.army_mode: {type: "choice", choice: string,
confidence: number, probabilities: map<string,number>}`, and
`usage: {input_tokens: integer, output_tokens: integer}`. This is the service's
[documented API](https://docs.typesafe.ai/api), checked 2026-10-08.
Service content is untrusted data: accept only the offered enum, never instructions
or executable graph changes. Bound response reads and record sanitized failure codes.
401/403 requires fixing the local key and starting a new match; no refresh protocol.
429 and service/network failures fall back under the same cadence and request cap.
No account-specific rate entitlement is assumed.

`state` contains numeric game time/resources/supply, launch boolean, first-wave
threshold, ready-Zealot count, structure counts, home/enemy-start coordinates and
bounded arrays of observed/remembered entities. Each entity contains string tag/type,
position `[number,number]`, health/shield numbers and flying boolean; arrays cap at 64.
No hidden enemy state or player chat enters the prompt.

Existing `Event` shape (src/jev/contracts.py) is retained:
| Fields | Type / meaning |
|---|---|
| schema_version, sequence, game_loop | integers: schema and ordered event identity |
| run_id, node_id | strings: match and graph-node identity |
| game_seconds | number: game time |
| task_id | string or null: issued-task correlation |
| kind, status, reason | strings: event classification and safe explanation |
| facts, action | JSON object; action may be null |

Decision facts carry the evidence listed in section 6. `run_id` is lowercase UUID4
hex generated by runner.run_match; task IDs are `run_id:counter` from runtime;
node IDs are stable policy keys; policy hash is the existing SHA-256 archive identity.
The single writer RunRecorder creates a fresh `data/jev/runs/<run_id>/` each match,
archives policy once, atomically replaces state/metadata, and appends bounded rotating
event JSONL segments. Its reader rejects corrupt records and incomplete trace tails;
reuse the existing failure/partial-write handling. Never rewrite old run archives.

The read-only dashboard routes have no request body:
`GET /api/jev/runs` returns `{schema_version,runs,truncated,omitted}` with run identity,
version, status, update time and policy hash per summary; `GET /api/jev/runs/{run_id}`
returns RunState (identity, tasks, recent_events, trace and node status) plus stale and
metadata; `GET /api/jev/runs/{run_id}/policy` returns archived graph plus policy_hash.
Errors retain `{schema_version,error:{code,message}}`. No new route or migration.

Prerequisites: Windows, Python >=3.12, uv, Node/npm, Git Bash for the combined launcher,
installed SC2 and Simple64 map. Install with `uv sync` at repository root and `npm ci`
inside frontend. Configure TYPESAFE_API_KEY locally without committing or printing it.
Start dashboard with `bash scripts/start-dev.sh`; visit http://localhost:3000/?tab=jev.
Launch `uv run python -m bots.jev.v1 --map Simple64 --opponent-race Terran --difficulty 1 --seed 1 --realtime --decision-provider typesafe`.
Stop with Ctrl+C once in the match terminal; verify terminal run state and cancelled
request. Dashboard stop controls are not the gameplay stop mechanism.

Commands from repository root: `uv run pytest`, `uv run ruff check .`,
`uv run mypy src bots --strict`, and `uv build`. In frontend: `npm run test:run`,
`npm run lint`, `npx tsc -b`, and `npm run build`. Production backend is Python;
frontend build emits static assets. Record actual results and baseline failures,
not inherited test counts. Use real producer/consumer imports for offline wiring
smoke, then Step 211's 60-second live smoke before the complete observed match.
If no accepted reply appears, record the failed gate and diagnose before claiming
completion. Group match findings into blockers, tuning, UX and deferred evolution;
fix blockers and repeat the live gate when necessary.

Build sequence: implement 209, independently review/test; implement 210 and verify
recorder-to-dashboard evidence; execute 211 only with service/game prerequisites.
The direct user instruction authorizes this work now. Numeric headings support the
plan walker; JI.1–JI.3 remain aliases. No issue creation is requested; use repo-sync
only if later routing this plan through the issue-driven build-phase pipeline.

## Appendix

### Decision Inventory

| ID | P/D | choice | status |
|---|---|---|---|
| P1 | P | Actual Typesafe Jev evaluates live army intent | operator-picked |
| P2 | P | Four-Gateway rush, first launch at four ready Zealots | operator-picked |
| P3 | P | Preserve local legality, targets and task routines | operator-picked |
| P4 | P | Prove connection and dashboard evidence before evolution | operator-picked |
| P5 | P | Adjust plan and implement now | operator-picked |
| D1 | D | Opt-in typesafe provider, scripted default, jev-latest model | active default |
| D2 | D | Direct HTTP with existing httpx; key only in environment | active default |
| D3 | D | One pending request; 2s cadence, 1.5s deadline, 450-call cap | active default |
| D4 | D | 8 game/5 wall-second freshness, signature guard, confidence >=0.5 | active default |
| D5 | D | Existing Event facts and archived graph hold evidence; no migration | active default |
| D6 | D | Realtime 60-second live smoke then full match; stop gate | active default |
| D7 | D | Deep independent code review for hosted-service producer/consumer seam | active default |

## Implementation verification (2026-10-08)

Steps 209/210 are implemented and verified in the working tree. See [validation results](jev-typesafe-validation.md) and the [independent code review](jev-typesafe-code-review.md). This was a direct implementation, not a build-phase/review-deep protocol invocation. Step 211 subsequently passed actual hosted gameplay, dashboard evidence and pending-request stop cleanup; see the live acceptance section of the validation report. Physical keyboard Ctrl+C delivery was not tested. No merge, release or external issue closure is claimed.
