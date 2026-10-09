# Phase J2: Adaptive Jev player

Status: APPROVED FOR PREPARATION (2026-10-08), including dashboard-first launch
and automatic exact-game selection. Implementation has not started. Steps
212-224 follow JI's 209-211; no prior step is renumbered. Step 224 is ordered
between 212 and 213 because the first baseline tests must use this launch flow.

## 1. What This Is

Build `bots.jev.v2`, an inspectable Protoss player for StarCraft II (SC2) that
opens with four Gateways, scouts, manages its economy, expands to a second base,
and transitions from Zealots to a Zealot/Stalker army. Typesafe Jev makes selected
strategic and execution decisions; the local graph owns the entire plan lifecycle
and the SC2 adapter issues explicit legal commands. Measure progress against the
existing v1 player on harder built-in opponents. More graph nodes are not success.

Proposal: documentation/plans/jev-v2-proposal.html

Progress: [J2 inventory](../jev-v2-progress.html).

Objective: demonstrate an operational adaptive player, then establish whether it
improves on v1 against difficulty 4 (MediumHard). Gameplay success is a separate
gate from implementation completeness; a completed experiment can report failure.

## 2. Existing Context

- `bots/jev/v1/{manifest.json,policy.json,__init__.py,__main__.py}` packages the
  current policy through `importlib.resources`; the manifest identifies family
  `jev`, version 1 and entrypoint `bots.jev.v1`. No legacy `VERSION` file.
- `src/jev/policy.py` validates a bounded acyclic graph, typed operations, binding
  references and reachable outcomes; its SHA-256 policy hash is authoritative.
- `src/jev/runtime.py:JevRuntime` evaluates independent root lanes every 0.25 game
  seconds, with 256 evaluations and 32 commands per tick. Existing task ownership,
  retries, progress deadlines, placement exclusions and target demotions are reused.
  It already reserves minerals/supply for unacknowledged commands; gas and durable
  multi-step plan reservations are missing.
- `src/jev/operations.py` currently permits building Pylons/Gateways and training
  Probes/Zealots. `sc2_adapter.py` only resolves gather targets as minerals and
  build targets as points. Assimilator construction needs a geyser target path.
- `contracts.py:Observation` carries mineral resources, entities, map locations
  and remembered enemy structures. It has no gas, saturation, weapon capability,
  or sighting-age fields. The adapter uses visible observations, not omniscience.
- `decision.py:ArmyDecisions` asynchronously chooses attack/defend/regroup. One
  request is allowed at a time; existing default cadence is two wall seconds,
  timeout 1.5 seconds, freshness five wall/eight game seconds, confidence >=0.5,
  request cap 450. `bot.py` polls it before ticking the graph.
- `telemetry.py`, `api.py`, and `frontend/src/{types/jev.ts,hooks/useJevRun.ts,
  components/JevTab.tsx,components/JevGraph.tsx}` expose archived policy, live
  snapshots and recent events. Polling is one second; active nodes pulse green.
- JI's full realtime service match won against difficulty 1 with actual model
  `jev-1.13.0`. This is connectivity evidence, not harder-opponent evidence.
  See [JI validation](jev-typesafe-validation.md). Its source changes remain
  uncommitted at planning time on top of `4ea560e`; preserve them before an
  isolated build. Do not start from HEAD alone and silently omit JI.

Reuse Python >=3.12, uv (package runner), burnysc2 (SC2 API client), httpx (HTTP),
FastAPI (read-only backend), React/TypeScript and Vite (dashboard). No new runtime
dependencies, RL policy imports, service deployment or database is required.

## 3. Scope

In: frozen v1 comparison, sequential benchmark runner, v2 policy, supply and
production efficiency, grouped reinforcement/recovery, one Probe scout, bounded
enemy memory, gas economy, one natural expansion, Cybernetics Core and Stalkers,
strategic and execution-level Typesafe questions, graph plan lifecycle, live
branch visualization, dashboard-first test launch and real service/game validation.

Out: automatic evolution, graph mutation during games, full Protoss tech tree,
Warp Gate/research upgrades, spells/Chrono Boost, detection and cloaked-unit
counters, air production, more than two bases, multiple independent combat
squads, themed SC2 embedding, replay timeline scrubbing, ladder/self-play
integration, changes to `bots/v13` gameplay or legacy RL training.

V2 must be honest about those limits; an observed loss to missing detection is a
scope limitation, not a reason to quietly grow this phase. Simple64 is the only
acceptance map; no generalization claim to other maps or human players.

## 4. Impact Analysis

Producer reads and repository-wide symbol searches were performed on 2026-10-08.
The lists below enumerate current affected callers/implementations by file; builders
must re-run searches after earlier steps, since new consumers will then exist.

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `src/jev/contracts.py` | extend | Defaulted observation/entity facts; retain stored envelope | `Observation`/`Entity` references: contracts, sc2_adapter, bot, operations, runtime, decision in src/jev; tests/test_jev_{decision,policy,runtime,sc2,validation}.py. Other test_daemon/test_trainer hits are unrelated symbols. Existing Observation construction: adapter.observe and Jev fixture helpers. |
| `src/jev/operations.py` | extend | Gas costs, prerequisites, gather/build targets, graph operations | `Intent`/`RuntimeView` and cost/ability constants searched: operations, runtime, sc2_adapter; tests/test_jev_{economy,policy,runtime,sc2}.py. Runtime `_View` is the production RuntimeView implementation; operation validators consumed by policy.py. |
| `src/jev/runtime.py` | extend | Plan memory, gas reservations, fairness and cancellation | Read `_available_minerals`, `_available_supply`, `_held_*`, internal task construction and `set_army_decision`; production tick/set-army callers bot.py; direct callers in tests/test_jev_{runtime,decision,economy,army,sc2,telemetry,validation}.py. Preserve tick/public task wire shapes. |
| `src/jev/sc2_adapter.py` | extend | Observed gas, saturation, capabilities, real geyser construction and scouting queries | `GamePort` callers/implementations: sc2_adapter, bot, tests/test_jev_validation.py; test_bots_v0_main.py uses an unrelated GamePort. Duck-typed ports/units in test_jev_sc2.py must also grow. Read observe, _issue_one, BurnyGamePort. |
| `src/jev/decision.py` | extend | Typed reusable question boundary; keep v1 army compatibility | `ArmyDecisions`, `DecisionProvider`, `TypesafeProvider`, `DecisionConfig`, `parse_answer`: decision, runner, bot; tests/test_jev_decision.py. V1 path must retain current instructions/default semantics. |
| `src/jev/bot.py`, `src/jev/runner.py` | extend | Version-aware v2 coordinator/graph wiring and metrics | Read JevController._step and runner._play; runner.main called by bots/jev/v1/__main__.py and tests/test_jev_{sc2,validation}.py; controller/sc2 adapter tests also exercise dispatch. Preserve v1 CLI defaults. |
| `src/jev/policy.py` | extend | Validate new operation contracts without executable policy code | Imports operation validators; consumed by runtime, telemetry, runner, v1 loader; tests/test_jev_{policy,runtime,sc2,telemetry,validation}.py and new v2 package. Do not change canonical hash algorithm or graph envelope. |
| `src/jev/telemetry.py`, `src/jev/api.py` | extend only if required by bounded fact validation | Carry new diagnostics through existing Event.facts | RunState/Event envelope preserved. Real readers: api.py, scripts/validate_jev.py, frontend/src/types/jev.ts; tests/test_jev_{telemetry,api,validation}.py. No new route or archive rewrite. |
| `frontend/src/components/JevTab.tsx`, `JevGraph.tsx`, `JevTab.css` | extend | Current plan, branch color, recent path, blockers, source | Read nodeRuntimes and live/last-known styles; graph caller JevTab.tsx; component tests JevTab.test.tsx/JevGraph.test.tsx. Props may add optional activity data; preserve old runs. |
| `frontend/src/types/jev.ts`, `frontend/src/hooks/useJevRun.ts` | extend only if necessary | Typed diagnostic parsing without envelope drift | Existing API consumers and their adjacent tests found with rg; retain one-second state polling and five-second list polling. No full trace streaming. |
| `src/jev/runner.py`, `src/jev/api.py` | extend | Pre-play recorded-run hook and bounded launch-session API | `run_match` callers found in runner.main and tests/test_jev_{api,army,decision,economy,sc2,validation}.py; benchmark is a new caller in 212. `create_router` callers include bots/v13/api.py and tests/test_jev_{api,validation}.py. Keep existing parameters/defaults; derive launch storage beside run_root. |
| `frontend/src/hooks/useJevRun.ts`, `frontend/src/types/jev.ts`, `frontend/src/components/JevTab.tsx` | extend | Exact-run URL selection, session following and rendered-ready acknowledgment | Hook currently initializes null and selects newest only when null; callers JevTab.tsx and useJevRun.test.ts. App.tsx already supports `?tab=jev`; App.test.tsx covers tab selection. Add launch/run parsing in the hook, preserving no-parameter behavior. |
| `scripts/launch-a4g.ps1` | modify | Reusable noninteractive server startup without opening an extra tab | Current caller scripts/launch-evolve.ps1; existing default invocation must retain behavior. Current script probes ports, starts servers and opens only a tab URL; it ends with Read-Host. Add explicit reuse switches for Jev rather than inheriting its prompt. |
| `documentation/master_plan.md`, `README.md`, `CLAUDE.md`, `documentation/operator/jev-validation.md` | modify | Discoverable phase, v2 launch and capability boundaries | Existing JI/JV entries read. Do not relabel JV 207/208 complete from J2 evidence. |

## 5. New Components

- `bots/jev/v2/{__init__.py,__main__.py,manifest.json,policy.json}`: independent
  package, version 2, resource loader and thin existing-runner entrypoint.
- `src/jev/plans.py`: bounded plan ownership/reservations, transitions and
  per-plan execution decisions, used by runtime. No generic workflow framework.
- `src/jev/strategy.py`: v2 state summary, feasible question selection, local
  fallback and hosted decision application. Shared HTTP parsing stays in decision.py.
- `src/jev/benchmark.py` and `scripts/benchmark_jev.py`: sequential production
  runner orchestration, resumable scorecards and provenance checks.
- `src/jev/launch.py`, `scripts/launch-jev.ps1` and `tests/test_jev_launch.py`:
  dashboard-first single-match/batch launch, ready barrier and session following.
- `tests/test_jev_{benchmark,plans,scouting,macro,v2}.py`: focused behavior tests;
  reuse existing fixture helpers where appropriate, avoid copying the engine.
- `documentation/operator/jev-v2-validation.md`: exact smoke/panel procedure
  authored during code steps. `documentation/plans/jev-v2-validation.md` will
  record actual observations, not predicted wins.

Proposed internal shapes (not additions to the stored RunState envelope):

| Type | Fields used by this phase |
|---|---|
| Observation additions | `vespene: int=0`, `vespene_geysers: tuple[Entity,...]=()`, `enemy_sightings: tuple[EnemySighting,...]=()`; bounded and validated like existing collections |
| Entity additions | `assigned_harvesters: int=0`, `ideal_harvesters: int=0`, `mineral_contents: int=0`, `vespene_contents: int=0`, `can_attack_ground: bool=False`, `can_attack_air: bool=False`, `health_max: float=0`, `shield_max: float=0`; adapter fills actual values, safe defaults preserve fixtures |
| EnemySighting | `entity: Entity`, `last_seen_game_seconds: float`, `visible: bool`; keyed by uint64 entity tag, cap 256; units expire after 30 game seconds, structures persist until observed absent; cap evicts oldest |
| Intent addition | `vespene: int=0`; mirror in internal task commitments and per-tick holds; public Task remains unchanged, costs visible in Event.facts |
| PlanRecord | `plan_id`, `root_node_id`, `kind` (opening/pressure/recover/expand/tech), `status` (active/waiting/completed/aborted), `stage_node_id`, `site: Point|null`, `actor_tag: int|null`, `started_game_seconds`, `last_progress_game_seconds`, `deadline_game_seconds`, `reserved_minerals`, `reserved_vespene`, `reason_code`; max one macro plan plus one army plan |
| DecisionQuestion | `question_id`, `node_id`, `plan_id|null`, `instructions`, `criteria: map[str,str]`, `state: bounded JSON object`, `signature: immutable tuple`; model returns one offered enum |
| Decision evidence | `question_id`, `request_id`, `node_id`, `plan_id`, `offered_options`, `choice`, `source`, `reason`, `model`, `confidence`, `probabilities`, `latency_ms`, `age_game_seconds`, `calls`, `input_tokens`, `output_tokens`; use Event.facts, fixed/sanitized reason codes |
| Benchmark manifest | `schema_version:1`, `batch_id`, `claim`, `source_commit`, `source_fingerprint`, `policy_hashes`, `requested_model`, `expected_returned_model`, `cases[]`, `limits`, `created_at`; immutable after starting |
| Benchmark case | `case_id`, `version`, `provider`, `map`, `race`, `difficulty`, `seed`, `status` (pending/running/complete/invalid), `run_id|null`, `result|null`, `reason|null`, `metrics: object`; every planned case retained in report |
| LaunchSession | `schema_version:1`, `session_id`, `active_run_id: str|null`, `state` (preparing/starting/running/between_games/finished/failed/stopped), `case_index: int`, `case_count: int`, `updated_at: UTC string`, `message: bounded string`; launcher-written atomic session.json |
| LaunchReady | `schema_version:1`, `session_id`, `run_id`, `policy_hash`, `updated_at: UTC string`; API-written atomic ready.json, accepted only for the session's exact active starting run |

IDs: run_id and batch_id are lowercase UUID4 hex from the runner and benchmark
module respectively. Existing task IDs remain `run_id:counter`. Plan IDs are
`run_id:plan:counter`, generated monotonically by the plan store. Node IDs are
stable policy keys such as `expansion.site.reconsider`; question IDs are stable
enum names (`strategy`, `army_mode`, `expansion_response`, `production_mix`).
Request IDs are monotonic integers per run, globally across v2 questions.
Case IDs are deterministic `version-provider-map-race-difficulty-seed` strings
within a batch. Entity tags remain integers internally, strings in service JSON.
Launch session IDs are independent lowercase UUID4 hex values generated by launch.py;
they identify one single-match launch or one six-case batch, not the globally latest run.
Point means an `(x,y)` map coordinate pair. Policy identity is canonical SHA-256;
benchmark source fingerprint hashes sorted project-relative runtime/package source
paths and bytes, excluding secrets, data, caches and unrelated project files.

Extend collection-name validation and numeric field validation together with the
new observation fields; do not add fields that selectors cannot access. Preserve
existing public signatures with defaulted additions. For new GamePort queries,
use `async pathing_distance(start: Point, end: Point) -> float | None` (None means
unreachable) and `async available_abilities(actor_tags: tuple[int,...]) ->
Mapping[int,frozenset[str]]`. The adapter owns bounded query caching; populate it
in the controller's asynchronous preparation path before synchronous graph ticks.
Missing query results mean not yet eligible, never implicitly reachable/legal.
Only the adapter maps normalized ability names to burnysc2 enums.

## 6. Design Decisions

### D1. Preserve the working baseline

V1's policy bytes and default scripted behavior remain unchanged during J2. Step
212 prepares immutable source capture; 224 finalizes the JI-enabled baseline after
observation-only metrics and launch instrumentation, before the first baseline
game and before v2 shared-runtime edits. Completed baseline cases are never
rebased onto later source. Production workers run from the
frozen checkout/source snapshot with explicit entrypoint and output root. A
commit plus clean relevant tree is preferred; when JI is still uncommitted, copy
the exact relevant source/package/config files into the ignored benchmark baseline
directory and hash them. Never infer exact executable identity from HEAD alone.
Do not copy credential files or silently substitute current shared runtime for
the frozen baseline. V2 also defaults to scripted; hosted runs explicitly select
`--decision-provider typesafe`. No edits to the legacy current-version pointer.

### D2. Persistent plans, local checks and meaningful service questions

All action selection belongs to Jev: graph nodes own actors, targets, resource
holds, commands and recovery. Local deterministic operations handle exact legality
and fast reactions. Hosted Typesafe chooses both strategic priority and bounded
execution alternatives; it never returns Python, arbitrary graph edits or commands.

Implement v2 questions only when their alternatives are executable:

| Question | Offered alternatives and application |
|---|---|
| strategy | pressure/recover/expand/tech, filtered by current capability and state; chooses macro reservation priority, does not turn off economy or emergency defense |
| army_mode | attack/defend/regroup for a mixed ground army; defend covers both bases; graph selects only weapon-compatible targets |
| expansion_response | continue/delay/abandon when an active expansion faces observed danger or a blocked site; changes that plan's next execution branch |
| production_mix | zealot_heavy/balanced/stalker_heavy once Core/gas/producer conditions support Stalkers; desired Zealot:Stalker ratios 2:1, 1:1, 1:2, gated again by affordability and observed air threats |

The [Typesafe API](https://docs.typesafe.ai/api) accepts typed named questions and
returns corresponding answers; inspected 2026-10-08. Use one Choice question per
HTTP request initially. Include instructions in the question body: its map key
alone is not sent to the underlying model. Validate exact keys, offered choice,
finite probabilities/confidence, model and usage using the existing boundary.

Use one global in-flight request and 450 total requests per match, not per branch.
Maintain >=2 wall seconds between dispatches. Strategy reevaluation minimum is 10
wall seconds; army intent minimum 2; expansion response minimum 5; production mix
minimum 10. Meaningful changes mark a question dirty; unchanged questions refresh
on those intervals. Select highest-priority dirty eligible question (immediate
expansion danger, army, strategy, production), but promote any waiting question
after 10 wall seconds to avoid starvation. Local emergency reactions never wait
for the service. Store per-question answers, no unbounded queue.

Keep the existing 1.5-second request deadline, 0.5 confidence floor, and 5 wall/8
game-second reply freshness for initial acceptance. Signature binds plan identity,
stage, candidate set, relevant threats, actor/site and prerequisites. Revalidate
when applying the answer and before issuing commands. A strategic choice creates
a plan whose lifetime may exceed answer freshness; later plan progress does not
depend on holding that expired reply valid. Newly unsafe preconditions abort or
suspend the plan. Do not bind every strategic answer to every changing unit tag.

Fallback: urgent defense first; opening completion next; after first launch recover
when <4 ready combat units remain, otherwise pressure. With no observed home/site
threat, prefer completing missing Core/gas prerequisites, then expanding after
four Gateways and eight ready combat units; otherwise continue pressure. Production
fallback is balanced once Stalkers are feasible, Stalker-heavy for observed air.
Stale/invalid/timeout answers select this graph logic and are visibly attributed.
401/403 disables calls for the match; 429/network errors respect cadence and cap.
No automatic key refresh or assumed account entitlement. API key stays in process
environment; encrypted local key reuse follows the existing operator guide.

### D3. Execution and economy

Keep the four-ready-Zealot initial attack gate and four-Gateway opening. After
launch, allow one macro plan at a time alongside economy, supply, scouting and army
lanes. Target at most two completed/in-progress Nexuses and four Gateways. Cap
workers by observed base/gas ideal saturation, maximum 44. Reserve one scout only
after the first Gateway is started; replacement scouts at least 60 game seconds
apart. Return a threatened scout to safety; no enemy unit locations through fog.

Supply planning accounts for queued training, in-progress Pylons and reservations.
Maintain a configurable four-supply buffer after the first Pylon; no duplicate
Pylons from repeated ticks. Group reinforcements at home until four combat units
are ready, then join the main group. Once Step 218 supplies maximum-durability
facts, regroup low-durability groups rather than
continuously sending single replacements. Start with a configurable 40% aggregate
health-plus-shield threshold; local retreat/defense overrides stale model intent.
These are initial hypotheses to test, not claims of optimal strategy.

Minerals, gas, supply, actors and sites have single ownership. A macro plan reserves
up to its next required building's cost; transfer that reservation to the issued
task atomically, never charge both. Release holds when prerequisites disappear,
plan aborts or a task acknowledges resource deduction. Supply recovery and home
defense may preempt discretionary macro holds; record why. Plan timeout 120 game
seconds; no-progress timeout 30; at most three recovery attempts per stage then
abort, release and cooldown 15 seconds. Surviving tasks cannot retry commands
from an abandoned branch. No graph back-edges; repeat behavior comes from ticks.

### D4. Natural expansion and limited tech

Choose the nearest reachable unoccupied expansion by SC2 pathing from home;
Euclidean distance only orders bounded path-query candidates, never proves a
natural reachable. Query at most eight candidates and cache static paths per map.
Occupied/threat status comes from visible/remembered data with sighting ages.
Unknown is not safe: scout the site before committing, and recheck before build.
Graph exposes site choice, safety, budget, worker choice, move, legal placement,
issuance and construction acknowledgement. A worker dying releases ownership
and selects a replacement; blocked/threatened sites invoke expansion_response
when alternatives exist. Abandonment stops the unissued plan; it does not imply
a new in-game building-cancel ability. A started Nexus continues unless destroyed.

Gas: add Assimilator construction on a visible geyser unit tag, capped initially
at one per active base, and gather on completed own Assimilators up to ideal
saturation. Move excess workers back to minerals. Add Cybernetics Core construction
and Stalker training from ready powered Gateways with live prerequisites. Central
cost/prerequisite tables remain authoritative for graph planning; verify them
against installed SC2 game data. Adapter checks actual placement/abilities and
reports refusals through existing task lifecycle. Use burnysc2 low-level APIs,
not its high-level expand/distribute/build-strategy helpers. Official API source:
[BotAI](https://burnysc2.github.io/python-sc2/_modules/sc2/bot_ai.html); inspect the
installed version at build time before adding GamePort query methods.

### D5. Evidence and live graph

Retain the version-1 archive/API envelope. New plan/decision/economy diagnostics
use bounded Event.facts with explicit `kind`/field names, stable node IDs and
plan/request/task correlation. Emit transitions immediately plus a one-game-second
summary so the latest plan state survives the bounded recent-event window. Do not
assume an arbitrarily old event remains available to the dashboard.

Existing run-evidence endpoints remain GET-only, with no request bodies. D7 adds
a separate launch-readiness API; it cannot issue gameplay commands.

| Route | Response |
|---|---|
| `/api/jev/runs` | `{schema_version,runs:[{run_id,family,version,status,updated_at,policy_hash}],truncated,omitted}` |
| `/api/jev/runs/{run_id}` | RunState: identity/status/time, active_nodes[], waiting_nodes[], tasks[], recent_events[], result/error, trace retention counters; plus stale boolean and metadata containing source/map/race/difficulty/seed/replay reference |
| `/api/jev/runs/{run_id}/policy` | Archived policy `{schema_version,family,version,roots,parameters,nodes}` plus policy_hash; each node has id, kind, label and kind-specific operation/arguments/children |

Errors remain `{schema_version,error:{code,message}}` (422 invalid ID, 404 absent
run, 503 corrupt archive). Event contains schema_version, run_id, sequence,
game_loop/game_seconds, node_id, task_id|null, kind, status, reason, facts and
action|null. No migration of old archives; new facts absent means unavailable.
RunRecorder remains the single writer, atomically replacing state/metadata and
rotating bounded JSONL event segments. Incomplete traces are marked incomplete.

Color identifies lane (economy green, expansion teal, army red, tech purple);
outline/icon/text identifies active, waiting, failure and fallback. Connecting
edges highlight only observed recent activity; do not invent unrecorded traversal
between one-second polls. A two-second fading trail remains labeled recent, not
currently executing. Optional follow-activity toggle defaults off, keeps manual
pan/selection stable, and respects reduced motion. Show current plan/stage,
blocking condition, next permitted action and model/local source. Multiple lanes
can be active simultaneously. After termination or stale/offline state, stop
animation and label last-known evidence. Preserve v1 presentation and node inspector.

### D6. Benchmarks and stopping rules

Pre-register the claim: **on Simple64 against MediumHard built-in opponents,
v2 wins more of the specified held-out games than frozen v1, without a race-wide
regression**. This is a bounded panel claim, not a population win-rate estimate.

1. Baseline screen: frozen v1 Typesafe, each race Terran/Protoss/Zerg, difficulties
   3 (Medium) and 4 (MediumHard), seed 11: six matches. This reveals failure modes.
2. Development smoke: v2 realtime service plus SC2, observe >=60 seconds after
   a new v2 question first becomes eligible, then at least one full match.
3. Held-out comparison: each race at difficulty 4, seeds 101 and 202; frozen v1
   and frozen candidate v2, both Typesafe: 12 matches, alternating version order
   per case. Do not tune against these seeds before the candidate freeze.
4. Attribution panel: same six v2 cases with scripted provider, six matches.
   This separates whole-player improvement from evidence for hosted decisions.

Initial performance gate: v2 wins >=4/6 hosted held-out games, has more wins than
v1 overall, and no fewer wins within any race's two cases. Ties, timeouts and
crashes never count as wins; report them separately. These small counts justify
only this panel verdict. If v1 already wins 6/6, report ceiling/inconclusive rather
than silently changing target difficulty or seeds. Hard (5) and beyond become a
subsequent explicit panel. Failure to meet the target is a valid result, but does
not mark stronger-play acceptance DONE.

Pin requested model to `jev-1.13.0`, the previously returned model, after a tiny
availability probe; verify actual returned model on every answer. Unavailable
model, model drift, wrong entrypoint, fallback source path/config, corrupt evidence
or missing production configuration aborts the benchmark as invalid. Do not
substitute `jev-latest`. Normal runtime fallback due to timeout/staleness is
measured, not erased. A hosted case without any accepted hosted decision is
invalid for hosted comparison, not a hosted win. Local scripted attribution runs
are separately and explicitly configured, never accidental fallbacks.

All service matches are realtime. Per match: max 900 game seconds, 1200 wall
seconds, 450 requests. Per invocation: max six games, 7200 wall seconds, 2700
requests; the manifest names the exact six cases. A global budget in the parent
is checked before starting each match; never start a 450-cap match with less
than that remaining. Count launched request allowances conservatively after a
crash. Persist progress before/after every match and stop on first infrastructure
failure, authentication failure, provenance mismatch or budget exhaustion.
No endless rerun/tuning loop. Three allowed invocations cover the held-out plus
attribution panels; baseline and smoke are separate named invocations. Tokens
and limits are reported; do not claim a currency cap from tokens without verified
pricing. No new paid games are launched during this planning turn.

Scorecards include win/loss/draw/timeout/error; first attack/Nexus/Core/Stalker
times; supply-blocked game seconds (queued/affordable desired unit cannot fit);
ready powered Gateway idle seconds while resources/supply/prerequisites suffice;
mean mineral/gas bank, worker counts, unit losses from own observed tag
disappearances, command rejections, plan aborts, model latency, accepted/stale
reply counts and token usage. Label incomplete/unknown metrics explicitly.
Measure accumulators during play, not just the last retained trace segment.
Sample at <=1 game-second intervals; missed intervals are missing coverage,
not zero resource bank. Add a compact cumulative diagnostic summary for archives.

Benchmark manifests/results live under ignored `data/jev/benchmarks/<batch_id>/`.
One process owns each batch via an exclusive lock; stale locks require verifying
its process is gone. Atomic result replacement; append attempt records. Resume
skips completed cases, never overwrites them, and labels interrupted cases before
an explicit retry. New source/model/options require a new batch. Compare summary
outcomes against actual terminal RunState and SC2 replay/result. Calibrate report
logic using the known winning JI archive and a deliberately mismatched/invalid
case: the latter must never score as a valid win. No transcript-based judging.

### D7. Dashboard first, exact-game selection and batch following

The operator-facing single-match launcher `scripts/launch-jev.ps1` and all real
benchmark invocations default to dashboard-first mode. `--dry-run` opens no UI.
An explicit `--no-dashboard` benchmark option permits automated headless use;
never silently fall back to headless after a dashboard failure. The raw Python
bot entrypoints remain usable without opening a browser. Single-match PowerShell
parameters: `-Version v1|v2` (default v2), `-DecisionProvider scripted|typesafe`
(default typesafe), `-Difficulty` (default 3), `-Seed` (default 11). Pass through
the existing match limits, map and model defaults defined here. Load an existing
process key or the encrypted local key for the game child only; UI services must
not inherit the API key. Do not print it or put it in process arguments.

Launch sequence:

1. Start or reuse healthy backend/frontend; verify actual Jev API responses as
   well as HTTP readiness, not only occupied ports. Reuse server startup from
   launch-a4g.ps1 with new `-NoBrowser -NoWait` switches; existing invocations
   remain compatible. Background helper windows are hidden; the browser and
   game are intentionally visible. No unrelated process is terminated.
2. Create one launch session in `data/jev/launches/<session_id>/session.json`.
   Open `http://localhost:3000/?tab=jev&launch=<session_id>` once, immediately
   displaying Preparing rather than an old selected match. Browser-open failure
   aborts automatic startup; print the exact URL and permit that same page's
   readiness acknowledgment if the operator opens it within the wait deadline.
3. Prepare the exact match through the production runner. Add an optional
   `on_recorded: Callable[[str], None] | None = None` hook to `run_match`, called
   after recorder.start has archived policy/starting state and before `_play`
   can invoke SC2. The hook publishes that run_id as the session's starting run.
   Frozen v1 gets this launch-only hook before baseline finalization.
4. The page polls its session every second, selects its exact run, loads its
   state and archived policy, and acknowledges readiness only after those match
   and the Jev run view has rendered. A generic frontend health response is not
   proof that the right game is open. Direct `?tab=jev&run=<run_id>` links also
   select exactly that run; launch takes precedence if both parameters occur.
5. Only after matching ready acknowledgment does the hook release SC2 startup.
   Display Starting until live game observations arrive, then Live with game
   clock/opponent and active decisions. Distinguish paused-at-launch from a stale
   running bot; never show an old archive as live.
6. At completion show Finished and the result. For batches update the same
   session with the next exact starting run, repeat the ready barrier and follow
   in the existing tab. Never use global newest-run guessing. Manual selection
   of an archived run pauses following, with a visible Resume live control and
   waiting-for-viewer indication; resuming reselects the session's current run.

Server readiness deadline 60 wall seconds; per-run rendered-ready deadline 60
wall seconds. On timeout stop the batch before gameplay, record a launch failure
in its session/case, finalize any starting run as stopped, and retain diagnostics.
Ctrl+C before acknowledgment does the same clean cancellation. A readiness file
from another run or previous session never releases the barrier. After the first
game starts, losing/closing the browser does not stop gameplay; the next game
still waits for its own ready acknowledgment. No per-game confirmation click.

New API contracts (separate from immutable run evidence):

| Method/path | Request | Response |
|---|---|---|
| GET `/api/jev/launches/{session_id}` | none | LaunchSession as defined in section 5 |
| POST `/api/jev/launches/{session_id}/ready` | `{run_id: UUID4 hex, policy_hash: 64 lowercase hex}` | `{schema_version:1, ready:true, session_id, run_id}` after exact active-run/archive match |

Both use the existing error envelope: 403 disallowed client/origin, 422 invalid identifiers/body, 404 absent
session, 409 wrong run/hash/nonstarting session, 503 corrupt session. Validate
paths exactly as run storage does, rejecting traversal and out-of-root symlinks.
The launch error codes, respectively, are `launch_forbidden`,
`invalid_launch_request`, `launch_not_found`, `launch_not_ready` and
`corrupt_launch`. Define this separate LaunchError code set in launch.py and
its frontend parser; do not add launch-only failures to archived RunState's
ErrorCode union or pass them through the existing strict run-error parser.
Only a preexisting launcher-owned session may be acknowledged. The POST is a
readiness receipt, not arbitrary file writing, a launch command or game control.
Require loopback client and allowed same-origin browser Origin (dashboard
localhost/127.0.0.1 on port 3000, with the Vite proxy preserving it); reject
cross-origin/missing Origin rather than broadening CORS. Host checks follow
actual loopback backend/proxy hosts. No extra login or permission dialog.

Launch records are bounded to 4 KiB each and messages to 200 characters. Single
active launcher owner writes session.json atomically; readiness endpoint writes a
separate bounded ready.json atomically. Same active-run acknowledgment is
idempotent; validate receipt again in the launch hook against the active run and
policy hash. Session polling failure is visible and retries boundedly; it never
falls back to a different run. Preserve legacy archive readers and existing
API routes. Update API route-table tests and hook cancellation/out-of-order tests.

## 7. Build Steps

Execution order is 212, 224, 213, then 214-223. Step IDs are stable; do not sort
the steps numerically and bypass 224's dependency before live tests.

### Step 212: Establish reproducible Jev benchmarks
- **Problem:** Measure the current player through the production runner with immutable provenance.
- **Type:** code
- **Status:** DONE (2026-10-08)
- **Issue:** #328
- **Flags:** --reviewers deep
- **Files:** src/jev/benchmark.py (new), scripts/benchmark_jev.py (new), src/jev/bot.py, src/jev/runner.py, tests/test_jev_benchmark.py (new), documentation/operator/jev-v2-validation.md (new).
- **Produces:** Frozen v1 source capture, sequential bounded benchmark CLI, scorecard/metrics, resume and operator procedure.
- **Done when:** Dry-run resolves exact production entrypoint/policy/model/options; real archive calibration rejects provenance mismatches; interrupted/resumed batches preserve completed cases; no implicit service/config fallback; focused tests and required checks pass. Actual SC2 play is Step 213.
- **Depends on:** JI 209-211 source/evidence present, including uncommitted JI files.

<!-- autofix-applied: 2026-10-08 -->
### Step 224: Open the exact live game before starting SC2
- **Problem:** Let the operator watch every test from startup without selecting a tab or run manually.
- **Type:** code
- **Status:** DONE (2026-10-09)
- **Issue:** #329
- **Flags:** --reviewers full --ui
- **Start-cmd:** bash scripts/start-dev.sh
- **URL:** http://localhost:3000/?tab=jev
- **Files:** src/jev/launch.py (new), scripts/launch-jev.ps1 (new), scripts/launch-a4g.ps1, src/jev/benchmark.py, scripts/benchmark_jev.py, src/jev/runner.py, src/jev/api.py, frontend/src/hooks/useJevRun.ts, frontend/src/hooks/useJevRun.test.ts, frontend/src/types/jev.ts, frontend/src/components/JevTab.tsx, frontend/src/components/JevTab.test.tsx, tests/test_jev_launch.py (new), tests/test_jev_api.py, documentation/operator/jev-v2-validation.md.
- **Produces:** D7 session URLs, dashboard readiness barrier, single-tab batch following, launcher and finalized frozen baseline.
- **Done when:** Production runner hook plus real recorder/API/browser roundtrip proves the exact archived policy and run are rendered before a test launcher may start SC2; sequential runs follow the same session without reopening tabs; unrelated new runs cannot steal selection; failure/timeout stops launch visibly; manual history browsing pauses following until Resume live; legacy no-parameter hook/launcher behavior remains compatible. Browser tests cover cold startup, reused servers, stale readiness, bad session/run IDs, wrong served root and stop-before-start. Step 213 must then prove the ordering with actual SC2 and the real user-visible browser, not only a fake game launcher.
- **Depends on:** 212

### Step 213: Observe the harder-opponent baseline
- **Problem:** Identify the current rush's failure modes against stronger opponents.
- **Type:** operator
- **Status:** PENDING
- **Issue:** #330
- **Files:** data/jev/benchmarks/ and data/jev/runs/ (generated evidence), documentation/plans/jev-v2-validation.md (observation report).
- **Produces:** Six baseline outcomes, replay/run IDs, metric coverage and ranked gameplay findings.
- **Done when:** Dashboard opens on the exact starting run before SC2 launches, visibly changes to Live as play begins, and follows the next case without menu interaction; real service probe and >=60-second production smoke pass before completing the six-case baseline in D6; all outcomes and interruptions are recorded; failures are classified as execution, information, composition, strategy or infrastructure. Missing prerequisites keep this step pending.
- **Depends on:** 224

<!-- autofix-applied: 2026-10-08 -->
### Step 214: Make the v2 opening spend and reinforce reliably
- **Problem:** Execute the four-Gateway opening without avoidable supply stalls or isolated reinforcements.
- **Type:** code
- **Status:** PENDING
- **Issue:** #331
- **Flags:** --reviewers deep
- **Files:** bots/jev/v2/ (new package), src/jev/operations.py, src/jev/runtime.py, tests/test_jev_v2.py (new), tests/test_jev_economy.py, tests/test_jev_army.py.
- **Produces:** Versioned v2 opening, supply planning and group reinforcement/recovery graph.
- **Done when:** Production controller fixtures demonstrate first launch at four Zealots, completion of four Gateways, no duplicate Pylon commitments, bounded repeated failures and grouped replacements; v1 policy hash unchanged; both entrypoints validate from an installed wheel.
- **Depends on:** 213

### Step 215: Scout with bounded, honest enemy memory
- **Problem:** Give v2 fresh observed information for later plan choices.
- **Type:** code
- **Status:** PENDING
- **Issue:** #332
- **Flags:** --reviewers deep
- **Files:** src/jev/contracts.py, src/jev/sc2_adapter.py, src/jev/operations.py, src/jev/runtime.py, bots/jev/v2/policy.json, tests/test_jev_scouting.py (new), tests/test_jev_sc2.py.
- **Produces:** One-Probe scouting branch, timestamped bounded sightings and safety facts.
- **Done when:** Controller-to-adapter tests cover scout loss/retreat/replacement, aged unseen units, removed visible-absent structures and hidden-state exclusion; economy cannot reclaim a scout mid-task; scouting never consumes all root evaluation budget.
- **Depends on:** 214

### Step 216: Execute a gas economy safely
- **Problem:** Collect and reserve gas through visible graph actions.
- **Type:** code
- **Status:** PENDING
- **Issue:** #333
- **Flags:** --reviewers deep
- **Files:** src/jev/contracts.py, src/jev/operations.py, src/jev/runtime.py, src/jev/sc2_adapter.py, bots/jev/v2/policy.json, tests/test_jev_macro.py (new), tests/test_jev_sc2.py, tests/test_jev_runtime.py.
- **Produces:** Geyser-targeted Assimilator construction, gas assignment and gas cost accounting.
- **Done when:** Production-caller tests cover correct unit-tag construction target, saturation, depleted/destroyed gas building, missing worker, concurrent gas spending and release on acknowledgement/failure; no point-target mineral-build shortcut used for Assimilator.
- **Depends on:** 215

### Step 217: Carry out a recoverable natural expansion
- **Problem:** Complete a second base through an inspectable persistent plan.
- **Type:** code
- **Status:** PENDING
- **Issue:** #334
- **Flags:** --reviewers deep
- **Files:** src/jev/plans.py (new), src/jev/runtime.py, src/jev/operations.py, src/jev/sc2_adapter.py, bots/jev/v2/policy.json, tests/test_jev_plans.py (new), tests/test_jev_macro.py.
- **Produces:** Path-checked site selection, single resource/actor reservation and bounded expansion recovery.
- **Done when:** Controller fixtures reach second Nexus acknowledgement and transfer workers; lost worker, unreachable/blocked site, threat, missing budget and timeout each reach a visible recovery/abort outcome; every abort releases holds; no third Nexus or duplicate plan; second-base defense becomes eligible.
- **Depends on:** 216

### Step 218: Produce and control a mixed army
- **Problem:** Transition beyond ground-only Zealots using a bounded tech plan.
- **Type:** code
- **Status:** PENDING
- **Issue:** #335
- **Flags:** --reviewers deep
- **Files:** src/jev/contracts.py, src/jev/operations.py, src/jev/runtime.py, src/jev/sc2_adapter.py, src/jev/plans.py, bots/jev/v2/policy.json, tests/test_jev_macro.py, tests/test_jev_army.py, tests/test_jev_v2.py.
- **Produces:** Core prerequisite plan, Stalker training, composition guards and capability-aware target selection.
- **Done when:** Production fixtures execute Core then Stalker commands with mineral/gas/supply accounting; destroyed/unpowered prerequisites prevent training and recover; Stalkers can engage air while Zealots are never assigned an air unit attack; expansion and tech cannot double-spend.
- **Depends on:** 217

### Step 219: Let Typesafe choose persistent strategic plans
- **Problem:** Use real hosted strategic choices to direct the executable v2 graph.
- **Type:** code
- **Status:** PENDING
- **Issue:** #336
- **Flags:** --reviewers deep
- **Files:** src/jev/strategy.py (new), src/jev/decision.py, src/jev/plans.py, src/jev/bot.py, src/jev/runner.py, src/jev/runtime.py, bots/jev/v2/policy.json, tests/test_jev_decision.py, tests/test_jev_v2.py.
- **Produces:** Strategy and army questions sharing one request budget, plan persistence and fallback diagnostics.
- **Done when:** Actual controller/provider boundary tests demonstrate each feasible strategic choice selecting its graph branch; no unimplemented choice offered; delayed/out-of-order/low-confidence responses cannot affect the wrong plan; emergency defense continues during HTTP wait; v1 contract remains supported.
- **Depends on:** 218

### Step 220: Let Typesafe decide within execution branches
- **Problem:** Apply hosted decisions to expansion recovery and production choices.
- **Type:** code
- **Status:** PENDING
- **Issue:** #337
- **Flags:** --reviewers deep
- **Files:** src/jev/strategy.py, src/jev/decision.py, src/jev/plans.py, src/jev/operations.py, bots/jev/v2/policy.json, tests/test_jev_decision.py, tests/test_jev_plans.py, tests/test_jev_v2.py.
- **Produces:** expansion_response and production_mix nodes with request-to-plan-to-task evidence.
- **Done when:** Controlled replies through the production controller cause continue/delay/abandon and each mix to change subsequent legal commands; late replies after worker/site/stage change are refused; starvation test serves all eligible questions; all four question families share 450 calls and cancel on stop.
- **Depends on:** 219

### Step 221: Visualize active plans and execution paths
- **Problem:** Make the live graph explain what Jev is doing and why progress waits.
- **Type:** code
- **Status:** PENDING
- **Issue:** #338
- **Flags:** --reviewers full --ui
- **Start-cmd:** bash scripts/start-dev.sh
- **URL:** http://localhost:3000/?tab=jev
- **Files:** frontend/src/components/JevTab.tsx, frontend/src/components/JevGraph.tsx, frontend/src/components/JevTab.css, frontend/src/components/JevTab.test.tsx, frontend/src/components/JevGraph.test.tsx, src/jev/runtime.py, src/jev/strategy.py, tests/test_jev_telemetry.py, tests/test_jev_api.py, README.md, CLAUDE.md, documentation/operator/jev-v2-validation.md.
- **Produces:** Colored lanes, observed edge activity, follow toggle, plan/stage/blocker panel and node-attributed source evidence.
- **Done when:** Actual recorder-reader-API roundtrip preserves diagnostic facts; browser fixtures demonstrate simultaneous active lanes, stale/offline/final handling, reduced motion and old v1 archives; no fabricated path transitions; UI tests, lint, types and build pass. Real match observation follows in 222.
- **Depends on:** 220

### Step 222: Prove the full v2 decision cycle in SC2
- **Problem:** Validate real strategic and execution decisions through service, graph, commands and viewer.
- **Type:** operator
- **Status:** PENDING
- **Issue:** #339
- **Files:** data/jev/runs/, data/jev/benchmarks/ (generated), documentation/plans/jev-v2-validation.md (observation report).
- **Produces:** Live screenshots, replays, accepted strategic/execution request IDs correlated with commands, latency/staleness and cleanup evidence.
- **Done when:** The prepared procedure observes >=60 seconds after a v2 question becomes eligible and at least one full match; a real strategy answer and at least one execution-level answer affect commands; actual second Nexus and first Stalker are observed; at least one worker/site recovery and pending-stop cleanup are exercised. Use at most three development matches (Terran, difficulties 3, 4, 5, seed 11, in order) within the per-invocation time/request cap. If natural play omits required evidence, record that gate unverified and pending; do not invent a scenario override or count offline fixtures. No mocks satisfy this gate.
- **Depends on:** 221

### Step 223: Evaluate harder-opponent improvement
- **Problem:** Determine whether v2 earns the bounded stronger-play claim.
- **Type:** wait
- **Status:** PENDING
- **Issue:** #340
- **Files:** data/jev/benchmarks/, data/jev/runs/ (generated), documentation/plans/jev-v2-validation.md (findings), documentation/jev-v2-progress.html (status).
- **Produces:** Held-out paired comparison, scripted attribution panel, explicit performance verdict and prioritized next findings.
- **Done when:** All 18 D6 evaluation games have valid provenance/outcomes or the exact remaining/invalid cases are reported with a hold; performance acceptance is DONE only if the pre-registered target passes. Negative completed evaluation remains recorded as target unmet, not silently promoted. Report execution, information, composition and strategy findings; no unlimited fix/retest campaign.
- **Depends on:** 222

## 8. Risks and Open Questions

No unresolved architectural choices. The following are measured uncertainties,
not hidden build prerequisites.

| Risk | Consequence | Mitigation |
|---|---|---|
| Larger tree without better play | Complexity hides unchanged weaknesses | Baseline first, one bounded composition, measured held-out target |
| Reservations deadlock economy | Idle production and unbuilt expansion | Single owner, transfer not duplication, emergency preemption and deadlines |
| Frequent plan changes | Wasted travel/resources | Persistent plans, gated reconsideration and cooldown |
| Model latency/stale observations | Wrong branch after facts change | Per-question signatures, revalidation, local fallback and explicit attribution |
| More calls without benefit | Token consumption with unchanged commands | Shared cap, summaries, coarse question cadence and scripted attribution panel |
| Small panel/noisy games | False general skill claims | Fixed cases, per-race counts, all failures visible, claim only this panel |
| Service version changes | Confounded comparison | Pin and verify returned model; hold instead of silently substituting |
| Ground composition meets cloak/advanced tech | Legitimate strategic losses | Record capability limitation; defer new tech to evidence-led next phase |
| Shared runtime regresses v1 | Invalid comparison | Frozen executable baseline plus v1 regression suite and old archive smoke |
| Current JI changes are uncommitted | Fresh worktree misses prerequisite | Explicit source checkpoint/snapshot before building; never sweep unrelated files |

## 9. Testing Strategy and Operator Quickstart

Build sequence is serial through the first acceptance; no speculative parallel
implementation across changing contracts. Per step: write the vertical slice,
test through the production caller, run configured independent reviewers, resolve
findings, then checkpoint. Workspace builder pin: `gpt-5.6-sol`, medium effort;
coordinator uses its configured model. No ultra effort required by this plan.
Review routing is per step; deep protects schema/HTTP/ownership boundaries, full
includes browser evidence. At phase completion run full relevant suites; never
label a focused run as the full suite. Record reported model when dispatching.

Offline tests cover graph reachability/no cycles, resource conservation, actor
ownership, lost prerequisites, timeout/retry termination, fog-of-war boundaries,
mixed-unit target legality, stale answer rejection, question fairness and archive
compatibility. Exercise real controller/adapter/runtime boundaries with fake SC2
ports only for repeatable unit tests. Live 213/222 gates and 223 panel separately
prove actual service/game execution. The benchmark is a finite user-invoked job,
not a new daemon; nevertheless timed callbacks, polling and request cancellation
require observation through full games, not just component tests.

Prerequisites: Windows with SC2 installed and Simple64 map available, Python >=3.12,
uv, Node/npm, Git Bash for the combined dashboard launcher. Follow existing
operator guide for installation/map checks. From repository root:

```powershell
uv sync --extra dev --inexact
npm --prefix frontend ci
uv run python -m bots.jev.v1 --validate-policy
# After v2 is implemented:
uv run python -m bots.jev.v2 --validate-policy
```

Configure the process environment by decrypting the already saved local key;
never print its value. The file is Windows-account-bound encrypted data:

```powershell
$jevKeyFile = Join-Path $env:LOCALAPPDATA 'Alpha4Gate\typesafe-key.dpapi'
$jevSecret = ConvertTo-SecureString -String ((Get-Content -LiteralPath $jevKeyFile -Raw).Trim())
$env:TYPESAFE_API_KEY = [System.Net.NetworkCredential]::new('', $jevSecret).Password
$jevSecret.Dispose()
```

After Step 224, the normal visible single-match launch is
`powershell -File scripts/launch-jev.ps1 -Version v2 -DecisionProvider typesafe`.
It starts the dashboard first, selects the exact run and waits for rendered
readiness before opening SC2. Benchmark invocations use the same flow by default.
This replaces the need to start a page and find the run in its menu.

For low-level diagnosis only, start the dashboard separately using
`bash scripts/start-dev.sh` and visit http://localhost:3000/?tab=jev. Reuse healthy
servers; do not kill unrelated processes. The raw single-match command below
does not promise automatic UI selection (use the launcher for attended tests):

```powershell
uv run python -m bots.jev.v2 --decision-provider typesafe --decision-model jev-1.13.0 --decision-max-requests 450 --realtime --map Simple64 --opponent-race Terran --difficulty 3 --seed 11 --max-game-seconds 900 --max-wall-seconds 1200
```

Step 212 adds the following CLI contract, integrated with D7 by Step 224
(not runnable before implementation):
`uv run python scripts/benchmark_jev.py --panel baseline --dry-run`, then remove
`--dry-run` to play. Panels are `baseline`, `heldout-a`, `heldout-b`, `attribution`;
each is exactly six games defined by D6, with heldout-a seed101 and heldout-b
seed202. `--resume <batch_id>` requires the original fingerprint/options/model;
`--dry-run` makes no service calls or launches. Limits are mandatory defaults
above and can only be lowered for initial execution. Baseline source snapshot
creation/finalization is an explicit part of `--panel baseline` preflight, after
Step 224 launch integration and before any game.
Step 212 documents the exact staging run command for substrate checks.

Stop a foreground match with Ctrl+C once; benchmark propagates stop to its
owned match and writes interrupted status. Let burnysc2 clean up its SC2 client;
never kill all SC2 processes. Remove process key when finished:
`Remove-Item Env:TYPESAFE_API_KEY`. Existing `scripts/validate_jev.py --run-id`
compares each archive with backend API; run ID comes from runner output and uses
the UUID format defined above. API default is http://localhost:8765.

Commands for verification from root: `uv run pytest`, `uv run ruff check .`,
`uv run mypy src bots --strict`, `uv build`; from frontend: `npm run test:run`,
`npm run lint`, `npx tsc -b`, `npm run build`. Frontend build emits static assets;
backend runs in Python. No deployment step or external issue mutation is part
of this planning turn. Plan review, proposal and wrap precede issue sync;
build-phase starts only after repo-sync has filled the Issue fields.

## Appendix

### Decision Inventory

| ID | P/D | choice | status |
|---|---|---|---|
| P1 | P | Adaptive four-Gateway opening with expansion/limited tech | operator-picked |
| P2 | P | Jev owns both strategy and execution; hosted questions at multiple levels | operator-picked |
| P3 | P | Inspectable live graph and decision visualization | operator-picked |
| P4 | P | Beat harder opponents through measured improvement | operator-picked |
| P5 | P | Automated evolution and themed embedding remain later work | operator-picked |
| P6 | P | Open the dashboard first on the exact test game; automatically follow live play | operator-picked 2026-10-08 |
| D1 | D | New v2 package, immutable v1 executable comparison | active default |
| D2 | D | Persistent plans; four typed question families; one shared request cap | active default |
| D3 | D | Four-Gateway opening, one scout, bounded reservations/recovery | active default |
| D4 | D | Two bases, Assimilators, Core and Zealot/Stalker composition only | active default |
| D5 | D | Existing event envelope, lane colors, observed activity trail | active default |
| D6 | D | MediumHard target; six-case held-out panel per version plus scripted attribution | active default |
| D7 | D | Session-scoped launch URL with rendered-ready barrier and one-tab batch following | active default |

Planning evidence: local producers read on 2026-10-08, installed burnysc2 Difficulty
enum verified (3=Medium, 4=MediumHard, 5=Hard), and linked primary API references
checked. Defaults can be adjusted by ID before issue sync. No v2 gameplay or
performance claim is made by this document.
