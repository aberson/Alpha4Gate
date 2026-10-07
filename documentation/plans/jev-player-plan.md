# Phase JV: Jev player and decision-graph viewer

Status: PLANNED. Authored 2026-10-07 against HEAD `f42b9b6`; no implementation or live validation has occurred.

## 1. What This Is

**Objective:** Establish a complete, inspectable Jev player that plays a one-base, four-Gateway Zealot rush through an executable decision graph, with live inspection in the existing dashboard. Prove it works before connecting it to evolution.

LLMs (large language models) author the policy and supporting code between games. During a game, Jev evaluates live facts and maintains tasks; neither an LLM call nor a reinforcement-learning (RL) policy makes its decisions. The graph is the policy, not a diagram reconstructed from unrelated gameplay code.

Proposal: `documentation/plans/jev-player-proposal.html`

This document is the implementation source of truth. Agent-selected details are recorded separately from operator choices in the Appendix. Proposed interfaces below do not exist yet.

## 2. Existing Context

- `bots/current/current.txt` selects `v13`. `bots/v13/bot.py:372` evaluates a scripted strategic state, then optionally replaces it with a neural prediction. Subsequent code performs macro, scouting, production and combat. Merely replacing the neural predictor would not give Jev full control.
- `bots/v13/learning/neural_engine.py` maps model predictions to strategic states. `bots/v13/runner.py:98` defaults to `rules`; the legacy family is not necessarily using RL in every match. An enabled Claude advisor can enqueue commands, but that integration is not required for Jev.
- `bots/v13/macro_manager.py:_check_expansion` already makes gameplay choices; `bots/v13/bot.py:_execute_macro` delegates expansion to burnysc2. Jev must not inherit those decision makers.
- burnysc2 is the installed Python StarCraft II (SC2) API client. Jev uses its `BotAI` lifecycle and explicit unit commands directly. The project already depends on it, FastAPI, and React. No new graph engine or visualization dependency is required.
- `src/orchestrator/registry.py:list_versions` discovers immediate children of `bots/` containing `VERSION`. Existing contracts, snapshots and evolution assume that legacy layout. Jev's nested package avoids entering that registry prematurely.
- `bots/v13/api.py:184` owns the current FastAPI dashboard app. `frontend/src/App.tsx` owns tab navigation; `frontend/vite.config.ts` proxies `/api` to port 8765 from port 3000. `frontend/src/components/LineageView.tsx` demonstrates installed `d3-hierarchy` tree layout and SVG rendering.
- Phase EH and Phase EI remain separate active efforts. Recheck current-pointer and API ownership before implementing the mount. Serialize any overlapping API/frontend edits with those efforts. Do not edit their evolution runner or state schemas.

## 3. Scope

### Included

- New independently launched `v1.jev` player, one Protoss base, four Gateways, Zealots only.
- First attack at four ready Zealots; continuously reinforce afterward. Gateway completion does not gate attack launch.
- Economy, supply, powered construction, unit production, basic defense, attack, enemy search and loss recovery all owned by the graph.
- Bounded graph execution, persistent in-match tasks, deterministic selection, explicit failure branches and visible stalled-task reporting.
- Read-only Jev dashboard tab with graph overview, branch expansion, node inspection, active/waiting highlighting and recent execution history.
- Solo matches against a fixed built-in opponent; recorded policy, trace, result and replay provenance.
- Real pipeline smoke before full-match acceptance.

### Excluded / future enhancements

- Automated evolution, mutation, promotion, fitness gates, cross-family self-play and ladder integration.
- Renaming `bots/v13`, changing legacy version IDs, or claiming legacy matches necessarily consult PPO (the existing RL algorithm).
- Graph embedded beside SC2 in the themed viewer window; explicitly retained as a later enhancement.
- In-browser editing, dashboard start/stop commands, hot policy reload, live LLM advice, neural fallback.
- Expansion, gas, upgrades, other combat units, sophisticated micro, other races and a general-purpose graph programming language.

## 4. Impact Analysis

All paths are repository-relative. Existing API signatures, shared constants and stored legacy identifiers remain unchanged. There are therefore no changed-signature callers to migrate. New interfaces and their complete intended consumers are listed in section 5. Before changing this boundary, grep and enumerate every affected caller here.

| File | Change Type | Reason | Verified |
|---|---|---|---|
| `pyproject.toml` | extend | Package `src/jev` in the wheel; include Jev in default strict typing | Read wheel `packages` and mypy `packages`; neither includes Jev |
| `bots/v13/api.py` | extend | Mount a shared read-only Jev router once | Read module-level FastAPI `app` declaration at line 184; no existing router mount required by this feature |
| `frontend/src/App.tsx` | extend | Add `jev` tab/deep link and component | Read `TAB_NAMES`, `initialTab`, navigation and render branches; all local consumers of this private tab union are in this file |
| `frontend/src/App.test.tsx` | extend | Cover Jev deep-link selection and existing fallback | `rg 'tab|Advisor|Help'` found navigation tests |
| `documentation/master_plan.md` | extend | Register Phase JV and its new numeric range 201-208 | Read plan index and phase headings; no reserved Jev phase exists |
| `README.md` / `CLAUDE.md` | extend | Document distinct family, launch and inspection workflow after implementation | Both read; currently describe legacy player and six dashboard tabs |
| `.gitignore` | read-only | Existing `/data/` rule already covers Jev evidence | Read root data exclusion; no edit needed |

Read-only dependencies: `src/orchestrator/registry.py`, `src/orchestrator/contracts.py`, `bots/current/current.txt`, `bots/v13/runner.py`, `frontend/src/hooks/useApi.ts`, `frontend/src/components/LineageView.tsx`, `frontend/package.json`, `frontend/vite.config.ts`, `scripts/launch-a4g.ps1`, `.github/workflows/linux-tests.yml`. No modification is planned. The launcher accepts an arbitrary `-Tab` string already. Existing CI invokes `ruff check .`, `mypy src bots --strict`, and non-SC2 pytest tests, covering added source/tests without a new workflow.

## 5. New Components

| Proposed path | Responsibility / consumers |
|---|---|
| `src/jev/contracts.py` | Typed graph, observation, task, event and run records; consumed by loader/runtime/adapter/telemetry/API |
| `src/jev/policy.py` | Strict JSON loader, structural validation, canonical policy hash; consumed by CLI, runtime and policy snapshot writer |
| `src/jev/runtime.py` | Deterministic tick scheduler and node interpreter; consumed by bot lifecycle and validation scenarios |
| `src/jev/operations.py` | Allowlisted fact predicates, selectors and explicit action specifications; consumed by interpreter; no hidden strategy |
| `src/jev/sc2_adapter.py` | Normalize visible SC2 state, answer mechanical queries and issue graph-selected commands; consumed only by Jev bot |
| `src/jev/bot.py` | `JevBot(BotAI)` lifecycle driving runtime and telemetry; consumed by runner |
| `src/jev/runner.py` | Single-match CLI, run lifecycle, result/replay capture, bounded stop; consumed by nested entry point |
| `src/jev/telemetry.py` / `src/jev/api.py` | Process-independent run persistence / read-only router; consumed by runner and existing dashboard app respectively |
| `bots/jev/v1/__main__.py`, package initializers | Thin entry point to shared runner with packaged v1 policy |
| `bots/jev/v1/policy.json` / `manifest.json` | Executable rush policy and immutable identity/schema metadata |
| `frontend/src/components/JevTab.tsx`, `JevGraph.tsx`, `JevTab.css` | Run selector, graph, inspection panel and trace; imported by App |
| `frontend/src/hooks/useJevRun.ts` / `frontend/src/types/jev.ts` | Typed polling and contract validation; used by JevTab |
| `tests/test_jev_*.py` / `frontend/src/components/Jev*.test.tsx` | Behavioral coverage described in section 9 |
| `scripts/validate_jev.py` / `documentation/operator/jev-validation.md` | Production-path smoke/report tooling and repeatable live procedure |

### Contract summaries

Schema version is integer `1` for all new documents; reject unsupported versions. JSON is data only: no `eval`, embedded Python, dynamic imports or executable expressions.

| Record | Fields and rules |
|---|---|
| Policy | `schema_version`, `family="jev"`, `version=1`, `roots: string[]`, `parameters: object`, `nodes: Node[]`; roots are ordered behavior lanes |
| Node | `id`, `label`, `kind`, `children: string[]`, `operation: string|null`, `args: object`; kinds `sequence`, `selector`, `condition`, `select`, `action`, `wait`; operation-specific typed arguments |
| Manifest | `schema_version`, `family`, `version`, `entrypoint="bots.jev.v1"`, `policy_file="policy.json"`; load policy relative to package, never caller cwd |
| Observation | `game_loop`, `game_seconds`, `minerals`, `supply_used`, `supply_cap`, `own_units`, `own_structures`, `visible_enemies`, `remembered_enemy_structures`, `start_location`, `enemy_start_locations`, `expansion_locations`; entity record contains tag/type/position/health/build_progress/orders; own production structures also contain readiness, idle and powered flags |
| Task | `id`, `node_id`, `intent_key`, `actor_tag`, `target`, `status`, `created_game_seconds`, `deadline_game_seconds`, `attempts`, `last_progress_game_seconds`, `reason`; status `pending`, `issued`, `running`, `succeeded`, `failed`, `cancelled`; target is entity tag or `[x,y]` according to operation |
| Event | `schema_version`, `run_id`, `sequence`, `game_loop`, `game_seconds`, `node_id`, `task_id|null`, `kind`, `status`, `reason`, `facts: object`, `action: object|null`; kind `node`, `command`, `task`, `diagnostic`; action specifies ability, actor tags and target |
| RunState | `schema_version`, `run_id`, `family`, `version`, `policy_hash`, `status`, `updated_at`, `game_seconds`, `last_sequence`, `active_nodes`, `waiting_nodes`, `tasks`, `recent_events`, `result|null`, `error|null`; status `starting`, `running`, `finished`, `stopped`, `failed`; result `win`, `loss`, `draw`, `timeout` |
| Run metadata | `schema_version`, `run_id`, `created_at`, `family`, `version`, `policy_hash`, `source_commit`, `map`, `opponent_race`, `difficulty`, `seed`, `max_game_seconds`, `max_wall_seconds`, `replay_path|null`; UTC timestamps use ISO 8601; replay path is relative to the run directory |
| Error | `code: string`, `message: string`; stable codes include `invalid_policy`, `invalid_run_id`, `corrupt_run`, `persistence_failed`, `game_timeout`, `wall_timeout`, `sc2_unavailable` |

IDs: node IDs are authored unique dotted slugs such as `economy.supply.choose_worker`; run IDs are lowercase UUID4 hex generated by runner; task IDs are `run_id:monotonic_counter` generated by runtime; event sequence is a per-run increasing integer. `intent_key` combines node ID and semantic target so reevaluation does not duplicate an active task. Unit tags are serialized as decimal strings to avoid JavaScript integer precision loss. Policy hash is SHA-256 of canonical sorted-key JSON. Archive the loaded policy bytes in each run; the viewer uses that snapshot, not a subsequently edited source policy.

Operations declare typed arguments and permitted binding outputs in one registry. Conditions include count comparisons, resources/supply, powered/idle checks, threat distance and task state. Selection includes filter/sort/limit on entity collections and bounded placement candidates. Actions are explicit gather/build/train/move/attack commands; waits check a condition or game-time deadline. Selection binds named actor/target values into a per-root tick context; reject unbound references at validation. Cross-tick memory is explicit task state plus the `attack_launched` latch, never an invisible local variable in an action helper. Bindings are root-local, so economy cannot accidentally reuse an army target. The viewer shows operation names and argument values from the policy itself.

### Read-only API (new)

Base `/api/jev`. No new auth system, credentials, outbound service or listener. Reuse the dashboard's access boundary. IDs are validated as UUID4 hex and resolved strictly inside the configured Jev run root.

| Method/path | Request | Response |
|---|---|---|
| `GET /api/jev/runs` | No body | `{schema_version:1,runs:[{run_id,family,version,status,updated_at,policy_hash}],truncated:boolean}`; newest 50 by creation metadata |
| `GET /api/jev/runs/{run_id}` | UUID4 hex path ID | `RunState` plus computed `stale:boolean` |
| `GET /api/jev/runs/{run_id}/policy` | UUID4 hex path ID | Archived `Policy` plus `policy_hash` |

Missing run returns 404, invalid ID 422, malformed stored record 503 with stable error code; absence of runs is an empty successful list. Poll selected run every second and run list every five seconds while tab is mounted; cancel on unmount. Stale means nonterminal heartbeat older than five wall-clock seconds. UI must show stale/offline explicitly and never animate cached nodes as live. Final run remains inspectable. No fetching unbounded JSONL through an endpoint.

## 6. Design Decisions

### D1: Family identity without legacy migration

Place policy in `bots/jev/v1`, runtime in `src/jev`; display `v1.jev`. Keep `bots/jev/VERSION` absent so legacy discovery ignores it. Retain the existing `v13` identifier and current pointer. The requested conceptual family separation is delivered; global `v13.rl` aliases and mixed-family registry migration belong to subsequent integration. No import of `bots.current` from `src/orchestrator` is introduced. Verify wheel includes policy JSON and manifest using an installed-wheel test.

### D2: Hierarchical behavior forest with bounded ticks

Use ordered roots for economy, construction, production and army. Every root is serviced each tick even if another returns `running`. Structural child edges form a forest: reject cycles, missing/unreachable nodes, duplicate parents, invalid arity, unknown operations and unhandled outcomes. Runtime statuses are success/failure/running; sequence advances on success, selector advances on failure, both propagate running. Stateless conditions/selectors reevaluate each tick; side effects belong exclusively to deduplicated tasks. Wait nodes yield immediately. Repeat behavior comes from ticking, not graph back-edges; recovery/retry edges are labeled runtime transitions in the viewer.

Default cadence: at most one policy tick per 0.25 game seconds, 256 node evaluations per tick, 32 commands per tick. Reset resource and actor reservations each tick; subtract accepted commitments before later nodes spend. A budget breach emits a diagnostic and yields without spinning. Programmatic selection operators may sort/filter, but node arguments specify the criterion and limits. No concealed `manage_economy`, `rush`, `expand_now`, `distribute_workers` or inherited combat policy. Every command carries the originating node/task IDs.

### D3: Basic strategy defaults

The operator fixed four Gateways and a four-Zealot first attack. Additional tuning defaults: 16 probes, no gas, one base, no upgrades, one Nexus. Economy priority is emergency supply, first powered Gateway, first four Zealots, additional Gateways to four, then continuous Zealots; train probes toward target when that tick's higher-priority reservations permit. Supply trigger is four free supply or fewer with no pending Pylon. Rebuild lost Gateways toward four and replace missing power Pylons. A destroyed Nexus ends economy recovery; surviving army continues searching/attacking until SC2 ends the match or time limit expires.

Worker selection: available mining/idle probe nearest graph-selected build site, tie by tag; preserve an active construction task's chosen worker. Mineral assignments use visible nearby patches, two-worker target per patch, stable distance/tag tie-breaks. Graph chooses placement candidates near the main within valid Pylon power for Gateways; API tests placement legality. Try at most eight candidates per task attempt. Record placement coordinates and selected probe in trace.

Army: defend when visible ground enemies are within 20 game-distance units of the main; target nearest threat, tie by tag. Otherwise gather at a graph-chosen point eight units toward map center. First attack latches once four ready Zealots exist; all ready Zealots attack the first known enemy start and new ones reinforce, even if fewer than four remain later. After clearing a target, prioritize visible enemy structures, remembered structures not disproven by vision, then cycle expansion locations in distance order. Revalidate remembered targets on sight. No hidden-map information; map starting/expansion locations are permitted metadata. No advanced retreat or kiting in v1.

### D4: Task lifecycle and progress

Command issuance is not success. Confirm build start from observed structure progress, training from queue/new unit, and movement/attack from orders/position/target changes. Production completion must not rely solely on a command return value. Prevent duplicate actors and resource overspending across roots. Retry rejected or unconfirmed commands at most three times, at least one game second apart, with a five-game-second acknowledgement timeout. Construction after acknowledgement has a 120-game-second deadline; unit training 60; army movement/search checks progress every ten game seconds and replans after 30 without progress. Dead actors invalidate tasks immediately. An interrupted army task is cancelled before defense takes command; recovery reselects actor/site/target from current facts.

No feasible action is a visible waiting state with reason, not a busy retry loop. Retire finished tasks from active memory; maintain bounded history. A runtime can prevent structural loops but cannot prove strategic success. Consecutive failed intents trigger a 10-game-second cooldown with diagnostic, then fresh evaluation; whole matches have a hard time limit.

### D5: Disk bridge and dashboard

One game process writes a unique directory `data/jev/runs/{run_id}/`: immutable `policy.json`, `metadata.json`, atomic `state.json`, rotated `events.N.jsonl` and replay. Derive repository root from module path, not cwd; honor one explicit `--run-root` absolute-path override for tests. API router factory accepts a root argument without changing existing `configure()` signatures. Mount on `bots/v13/api.py:app` using repository-root `data/jev/runs`; rederive active mount if pointer has changed before implementation.

Write state at most twice per wall-clock second plus terminal state; use temp file and same-directory atomic replace, with bounded Windows sharing-error retries. Append trace only for status/branch/action changes plus one game-second evaluation summaries; rotate at 10 MiB, retain five segments per run, expose dropped/rotated counts. Keep 200 recent events and at most 128 active tasks in state; failure to persist mandatory evidence stops the run with a clear error rather than claiming trace completeness. Unique run directories avoid a shared latest-file writer race. No automatic deletion of prior runs.

Reuse React, SVG and installed `d3-hierarchy` for the structural forest beneath a synthetic display root. Add pan/zoom, branch collapse, keyboard-accessible node selection and textual status labels. A side panel shows last evaluation facts, status, action/result and task deadline. Runtime transitions appear as labeled overlays/history, not fake structural cycles. Read-only controls select runs and presentation state. Do not invent an explanatory LLM narrative.

### D6: Launch and acceptance boundaries

New command: `uv run python -m bots.jev.v1 --map Simple64 --opponent-race Terran --difficulty 1 --seed 1 --max-game-seconds 900 --max-wall-seconds 1800`. CLI supports `--realtime`, `--run-root`, `--validate-policy` (validate and exit without SC2). Defaults match this command. Resolve map/install through existing burnysc2 setup; fail early if unavailable. Runner invokes one built-in-AI match, no daemon, no automatic restart. Register cancellation with the same main-thread game lifecycle used by burnysc2; Ctrl+C requests clean leave and records stopped. Do not blanket-kill SC2 processes. Return nonzero for crash, timeout or infrastructure failure; ordinary win/loss returns zero with explicit result.

One manually invoked match is not a new unattended evolution service. Nevertheless the polling/telemetry/task lifecycles require real observation: Step 207 runs a short production smoke and Step 208 observes three full sequential games. Closing the dashboard must not stop gameplay. No win-rate claim from three games; wins are recorded, not required for functional acceptance.

## 7. Build Steps

Phase JV owns numeric Steps 201-208, newly reserved in the master plan. All are pending. Issue fields intentionally remain blank until repo-sync. Execute in order through build-step review gates; each step has a production caller and its own acceptance. Re-read source anchors before edits. Runtime-review startup below is from repository root; honor the existing Windows backend/worktree launch constraints and never reuse a stale backend as evidence of a changed checkout.

### Step 201: Validate and execute a bounded policy

- **Problem:** Make the real Jev CLI validate packaged graph definitions and execute deterministic observation scenarios through the production interpreter.
- **Type:** code
- **Status:** PENDING
- **Issue:**
- **Flags:** --reviewers deep
- **Files:** `src/jev/contracts.py`, `policy.py`, `runtime.py`, `operations.py`; `bots/jev/v1` package and initial policy/manifest; `pyproject.toml`; `tests/test_jev_policy.py`, `tests/test_jev_runtime.py`.
- **Produces:** Typed contracts, interpreter, validator, `--validate-policy` path, packaged data and bounded scenario tests; operation implementations are explicit, not placeholder success stubs.
- **Done when:** CLI validates the shipped policy; invalid cycles/missing references/unknown operations fail with node-specific errors; one running lane cannot starve another; tick budget bounds a pathological graph; installed-wheel validation finds bundled JSON. Scenario events identify the executed nodes.
- **Depends on:** none

### Step 202: Run the four-Gateway economy in SC2

- **Problem:** Drive a real Jev bot's economic actions entirely through graph-selected operations.
- **Type:** code
- **Status:** PENDING
- **Issue:**
- **Flags:** --reviewers deep
- **Files:** `src/jev/sc2_adapter.py`, `bot.py`, `runner.py`, `operations.py`, `runtime.py`; `bots/jev/v1/policy.json`; `tests/test_jev_economy.py`, `tests/test_jev_sc2.py`.
- **Produces:** Single-match entry point, visible-state adapter, worker/supply/powered-building/Zealot production lanes, task acknowledgement/reservation/recovery.
- **Done when:** Entry-point integration scenarios issue only graph-attributed actions; scarce-resource fixtures cannot overspend or double-assign a probe; rejected placement, lost probe, lost power and delayed build acknowledgement produce bounded recovery; four-Gateway target and no gas/expansion are encoded in policy. Live SC2 test is marked for the later real smoke, not silently counted as passed in unit runs.
- **Depends on:** 201

<!-- autofix-applied: 2026-10-07 -->
### Step 203: Complete the rush player

- **Problem:** Make the army complete matches through Jev-controlled attack, defense, reinforcement and search behavior.
- **Type:** code
- **Status:** PENDING
- **Issue:**
- **Flags:** --reviewers deep
- **Files:** `bots/jev/v1/policy.json`; `src/jev/operations.py`, `sc2_adapter.py`, `runtime.py`, `runner.py`; `tests/test_jev_army.py`.
- **Produces:** Full v1 policy and terminal-result lifecycle.
- **Done when:** Production-runtime scenarios launch with four ready Zealots before all Gateways complete, reinforce below four after attack latches, interrupt/resume for defense, invalidate dead targets, search after clearing the enemy start, and terminate within CLI time limits. Import/call audit finds no legacy gameplay, PPO inference or LLM client in Jev's gameplay call graph.
- **Depends on:** 202

<!-- autofix-applied: 2026-10-07 -->
### Step 204: Expose inspectable run evidence

- **Problem:** Deliver consistent graph and execution state from the game process through the existing dashboard API.
- **Type:** code
- **Status:** PENDING
- **Issue:**
- **Flags:** --reviewers deep --ui
- **Start-cmd:** bash scripts/start-dev.sh
- **URL:** http://localhost:3000/
- **Files:** `src/jev/telemetry.py`, `api.py`, `runner.py`, `bot.py`; `bots/v13/api.py`; `tests/test_jev_telemetry.py`, `tests/test_jev_api.py`.
- **Produces:** Atomic run snapshots, bounded trace, archived policy, read-only route mount and replay/result references.
- **Done when:** Real writer/reader/router integration preserves graph hash and large unit tags, serves two isolated run directories correctly, bounds history/rotation, exposes stale/crashed producer evidence, rejects path traversal, and tolerates partial JSONL tail without corrupting state. Existing API responses remain unchanged. Legacy version discovery excludes Jev. Capture a browser dashboard smoke with the actual telemetry-written fixture served through the Vite `/api/jev` proxy, checking response schema/hash and existing dashboard navigation; the new graph screen arrives in Step 205.
- **Depends on:** 203

### Step 205: Inspect Jev from the dashboard

- **Problem:** Let a user follow the actual graph and inspect its live decisions from a Jev tab.
- **Type:** code
- **Status:** PENDING
- **Issue:**
- **Flags:** --reviewers full --ui
- **Start-cmd:** bash scripts/start-dev.sh
- **URL:** http://localhost:3000/?tab=jev
- **Files:** `frontend/src/App.tsx`, `App.test.tsx`; new Jev components, CSS, hook, types and component tests listed in section 5.
- **Produces:** Read-only graph/run browser with node detail and recent trace.
- **Done when:** Browser evidence covers active, waiting, failed, stale, empty and finished states; selected node displays matching archived-policy ID and event; collapse/pan/zoom remain usable; run switching cannot mix hashes/events; polling stops on unmount; old tab deep links still pass. Use persisted fixtures produced by the actual telemetry writer for UI review; this does not substitute for Step 207.
- **Depends on:** 204

<!-- autofix-applied: 2026-10-07 -->
### Step 206: Prepare reproducible live validation

- **Problem:** Provide one documented production workflow for smoke and full-match evidence collection.
- **Type:** code
- **Status:** PENDING
- **Issue:**
- **Flags:** --reviewers deep
- **Files:** `scripts/validate_jev.py`, `documentation/operator/jev-validation.md`, `README.md`, `CLAUDE.md`; `tests/test_jev_validation.py`.
- **Produces:** `validate_jev.py --run-id UUIDHEX --api-base http://localhost:8765` verifier that compares disk/API hashes, sequence and result without mocks; operational guide, acceptance report template and updated launch docs.
- **Done when:** Verifier returns nonzero on mismatched hash, missing policy, malformed terminal result or stale running state; it accepts a real writer-produced fixture through actual HTTP routes. Guide contains all install/start/stop commands, Step 207/208 checks, evidence locations and cleanup instructions. Full relevant backend/frontend gates pass.
- **Depends on:** 205

### Step 207: Observe the live pipeline smoke

- **Problem:** Prove SC2 observation through Jev execution, persistence, API and browser completes a real cycle before longer matches.
- **Type:** operator
- **Status:** PENDING
- **Issue:**
- **Files:** Existing procedure from Step 206; no authored code in this step.
- **Produces:** Smoke evidence: run ID, policy hash, command/result event, verifier output and dashboard screenshot.
- **Done when:** After SC2 startup, observe 60 seconds of actual gameplay with graph-driven commands and live dashboard updates; disk/API/browser agree on run/hash/node IDs; close/reopen the tab and recover current state; stop cleanly and verify terminal stopped state. No mocks or replayed fixtures. Failures leave this gate incomplete.
- **Depends on:** 206

### Step 208: Accept the basic player and viewer

- **Problem:** Establish whether Jev reliably plays the intended rush through full matches with useful inspection evidence.
- **Type:** operator
- **Status:** PENDING
- **Issue:**
- **Files:** Existing procedure from Step 206; no authored code in this step.
- **Produces:** Three sequential match evidence bundles and observed findings classified as functional blockers, strategy tuning or later enhancements.
- **Done when:** Run seeds 1, 2 and 3 against Terran difficulty 1 on Simple64, 900 game-second / 1800 wall-second limits per match. All three terminate normally with SC2 outcomes and complete trace provenance; no unbounded loop, orphaned run or duplicate-spend failure. At least one match visibly reaches four Gateways and sends the first four-Zealot attack, with reinforcement evidence. Inspect one waiting task and one completed command/result in browser; record observed recovery when it occurs, and explicitly mark unobserved recovery cases as scenario-tested only. A timeout/crash or absent rush behavior is a functional blocker; losses alone are tuning findings. Report wins without an estimated general win rate. Phase remains incomplete until blockers are fixed and affected gates rerun.
- **Depends on:** 207

## 8. Risks and Open Questions

No operator-owned design question remains unresolved. These are implementation risks to investigate and report, not permission gates.

| Item | Risk | Mitigation |
|---|---|---|
| Hidden policy in helpers | Jev diagram misrepresents decisions | Explicit selectors/arguments, action attribution, prohibit legacy policy delegation |
| SC2 timing / legality | Accepted call is mistaken for completed action | Observation-based acknowledgement, reservations and deadline recovery |
| Strategic deadlock | Structurally valid graph waits forever | Nonblocking roots, progress diagnostics, retry cooldown and match limits |
| Simplicity loses games | Rush is weak against some opponents | Functional acceptance before performance tuning; no inflated win-rate claim |
| Current API ownership moves | Jev tab exists but routes vanish after pointer switch | Verify pointer at Step 204; record mount in docs; carry router registration into any later version/pointer migration |
| Policy grows extensive | Graph becomes unreadable or expensive | Hierarchical roots, bounded interpreter, collapsible graph, node-ID search/detail |
| UI looks live after crash | Cached data misleads operator | Heartbeat and explicit stale status independent of successful HTTP polling |
| Hard interruption | Final record missing | Reader exposes stale starting/running run; preserves last valid snapshot and trace |
| SC2 setup unavailable | Cannot substantiate playability | Fail preflight visibly; keep live steps pending rather than substitute mocks |

## 9. Testing Strategy and Quickstart

Python >=3.12 with uv; existing React/TypeScript/Vite toolchain with Node/npm. Windows SC2 installed at the existing configured location and Simple64 map available. Use normal fog of war. No API keys or live LLM credentials are required for Jev. Existing dashboard setup remains as documented; only one backend process may own 8765.

Commands after implementation, from repository root unless specified:

```powershell
uv sync --extra dev
# In frontend/: npm ci
uv run python -m bots.jev.v1 --validate-policy
uv run python -m bots.current.runner --serve
# In a second terminal, frontend/: npm run dev
# Open http://localhost:3000/?tab=jev
# In a third terminal:
uv run python -m bots.jev.v1 --map Simple64 --opponent-race Terran --difficulty 1 --seed 1 --max-game-seconds 900 --max-wall-seconds 1800
```

Validation: `uv run pytest -m "not sc2"`, `uv run ruff check .`, `uv run mypy src bots --strict`; in `frontend/`: `npm run test:run`, `npm run lint`, `npm run build`. Build Python distribution with `uv build`; verify an installed wheel can validate its packaged policy. Mark live tests `sc2` and invoke explicitly on the SC2 host; default pytest excludes them. No deployment changes or new ports.

Test boundaries: pure observation fixtures verify policy behavior and adversarial task timing; real filesystem and real FastAPI router tests verify wire contracts; component/browser checks verify graph navigation and stale/error states; live smoke and three full matches verify actual game mechanics. Test graph semantics and failures rather than merely snapshotting implementation. Existing `App.test.tsx`, API tests, registry discovery tests and package wheel checks are regression gates. Policy hash, schema version and node IDs must agree across actual producer and consumer; do not independently hand-author a second wire schema in fixtures.

Stop only the processes started for this workflow. Ctrl+C targets the Jev foreground runner; do not kill all SC2 processes. Run artifacts persist for diagnosis. A new match gets a fresh run ID and task state. Browser closure affects only inspection. Disk-full or corrupt policy errors must remain visible and actionable.

## Appendix

### Decision Inventory

| ID | P/D | choice | status |
|---|---|---|---|
| P1 | P | Separate Jev family with independent version history | Confirmed in conversation |
| P2 | P | LLM authors graph between games; no gameplay LLM | Confirmed |
| P3 | P | Jev owns every gameplay choice from first version | Confirmed |
| P4 | P | Basic one-base four-Gateway Zealot rush | Confirmed |
| P5 | P | First attack at four Zealots; continuously reinforce | Confirmed |
| P6 | P | Working player and viewer before evolution | Confirmed |
| P7 | P | Existing dashboard first; themed-window graph later | Confirmed |
| P8 | P | View executable decision structure and detect getting stuck | Confirmed |
| D1 | D | Nested family package; postpone legacy rename/registry migration | Proposed default; tweak migration scope |
| D2 | D | JSON behavior forest with bounded ticks and allowlisted operations | Proposed default; tweak graph grammar/budgets |
| D3 | D | 16 probes, no gas, basic defense/search and explicit selection defaults | Proposed default; tweak gameplay parameters |
| D4 | D | Observation-confirmed tasks, finite retries and cooldown | Proposed default; tweak deadlines/recovery |
| D5 | D | Atomic disk snapshots, bounded JSONL, read-only SVG dashboard | Proposed default; tweak inspection/retention |
| D6 | D | Solo Simple64/Terran/easy smoke then three games; wins not required | Proposed default; tweak benchmark/acceptance |

Next pipeline: plan-review -> plan-redline -> plan-wrap -> repo-sync -> build-phase. Review and wrap occur before issue creation. Use `/plan-expedite --plan documentation/plans/jev-player-plan.md` for pre-build preparation in `C:\Users\abero\dev\Alpha4Gate`, followed by `/build-phase --plan documentation/plans/jev-player-plan.md` only after READY and issue population. This planning task does not launch matches or start implementation.
