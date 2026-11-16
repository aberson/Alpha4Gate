# VW.0 draft: candidate spot-check viewer

Date: 2026-11-16. State: **HELD — draft for coordination and review, not build-ready**.
Parent: [VW.0 and D7/D10/D16](../plans/master_plan.md), [strength-viewer seed](../seeds/strength-viewer.md).
Baseline: HEAD `1d6a4ecaef5dd2fff32af574028b2233d0d25684`, dirty shared working tree,
`bots/current/current.txt` = v13. All implementation paths below are proposed assignments;
this drafting lane changes only its seed and investigation documents. No live launch ran.

## What this feature does

The operator launches a named candidate in a separate Windows game, watches the native
SC2 view beside evidence of its decisions, flags a behavior at the observed game time,
and later sees that finding reach the existing improvement proposer through ES.10.
Windows spot checks are demonstrations and diagnostic evidence, never Linux scoreboard
results. This document drafts three code slices and a separate attended acceptance bundle.

## Existing context: what can actually be reused

| Producing source | Present behavior | Planning consequence |
|---|---|---|
| `frontend/src/main.tsx`; `components/JevBattleView.tsx` | `?battle=1` selects the Jev view; exact `run` drives run/policy hooks; native actions use a host bridge | Reuse the page composition and identity discipline; add a separate candidate entry, do not pretend v13 has a Jev graph |
| `scripts/probes/jev_battle_view.py`: `run_battle`, game child | Native host, browser bridge, owned child, readiness and stop control; deeply Jev-specific | Extract only necessary hosting behavior. Never invoke the existing paid Jev launcher as the candidate path |
| `src/jev/launch.py`: `LaunchSession`, `ready_hook`, `accept_ready` | Readiness bound to session/run/policy hash; bounded records; loopback/Host/Origin checks | Reuse the protocol pattern; Jev policy validation is not a candidate-bot contract |
| `src/jev/camera.py`: `CameraDirector`, `camera_subjects` | Presentation-only camera over visible entities; task inputs are Jev types | Optional simple visible-unit camera adapter; no graph/caster dependency required |
| `bots/v13/bot.py`: observation block; `observer.py`: `observe` | Broadcast game time, resources, actions taken and final strategic state | State timeline is feasible; army-target and win-probability fields are not in this payload |
| `bots/v13/audit_log.py`: `record_decision`; `claude_advisor.py` caller | Audit/decision WebSocket is populated by advisor responses | Do not use it as evidence of rules reasoning or PPO intent |
| `bots/v13/web_socket.py`: queues; `api.py`: lifespan and WS routes | In-memory queues local to each interpreter | A separate bot process does not automatically feed the dashboard server |
| `bots/v13/learning/neural_engine.py`, `bot.py:_record_transition` | `last_probabilities`; hybrid DEFEND can produce one-hot effective probabilities; DB recording optional | Label recorded effective probabilities; do not claim raw model output or infer why it chose an action |
| `bots/v13/runner.py:_run_single_game`, `connection.py:run_bot` | Runner creates per-version TrainingDB; connection creates a timestamped replay path internally | Dedicated isolated worker required; do not launch the ordinary runner and contaminate training data |
| `bots/v13/connection.py:run_bot`; installed `sc2/main.py:run_game` | `run_bot` has no seed argument; burnysc2 `run_game` accepts `random_seed` | Worker must pass the actual seed at the SC2 boundary and test it; a seed written only in metadata is not seeded execution |
| `src/selfplay_viewer/{container,overlay,reparent}.py` | Two hosted clients, callbacks, layouts, label/W-L overlay; attach discovery can block up to 15 seconds per slot | Reuse native container; repair responsive discovery in the dual slice |
| `scripts/selfplay.py:_run_h2h_mode`; `container.py:run_with_batch` | Passes a stop event; viewer close requests boundary stop | This differs from evolve's detach-only behavior; preserve the existing callers |
| `scripts/evolve.py:_ViewerSession`, viewer run path | Viewer uses no stop event; batch continues after detach | No new evolve stop protocol; ES.6 owns headless migration |
| `src/orchestrator/selfplay.py:_build_bot_process` | Launches `python -m bots.<version>` without installing the compatibility shim | New opt-in viewer worker wrapper must install shim before importing the chosen tree |
| `frontend/src/components/EvolutionTab.tsx`: `handleStopConfirm` | Sends `stop_run: true` and claims next-generation stop; current runner has no corresponding control-file reader | Replace this evolution affordance with the D7 instruction; do not remove the separately implemented advisor cleanup button |
| Phase ES.10 in the parent plan | Planned flag CLI and findings store; implementation absent at inspection | VW.3 depends on ES.10. Full structured context and retry semantics need an agreed writer contract |

Existing horizontal presets are 2188×948 (top/large), 2118×888 (top/small),
2468×848 (side/large), 2398×808 (side/small); vertical side/large is 1580×1470.
These are `overlay.py` constants, not measured usable display dimensions. Container
validation and the CLI help also disagree in prose about the vertical bar; derive behavior
from the constructor, not the stale help. Native DPI/work-area acceptance remains open.

## Scope

In: single candidate versus built-in AI, separate two-candidate game, a small evidence
panel, exact identity/time, bounded per-run telemetry, normal fog, replay linkage, local
flagging through ES.10, responsive native lifecycle and accurate stop instructions.

Out: displaying headless evolve games, third observer client, graph redesign, replay-tail
research, policy training, new promotion rules, fleet dashboard, paid advice, caster or
recording setup. Recording/commentary may reuse already-working optional hooks later,
but neither blocks this scope. No frozen-tree source edits are assumed.

## Impact analysis and consumers

Search boundary: `bots/v13`, `src`, `scripts`, `frontend/src` for `queue_broadcast`,
`record_decision`, WS routes, stop controls and viewer callers; direct reads of the
candidate runner, connection, observer, launch and native host. Frozen v0–v12 were not
fully audited: compatibility for each supported tree needs worker contract tests. Build
must repeat call-site search against its frozen base before changing shared interfaces.

| File/interface | Proposed change and reason | Found consumers / evidence |
|---|---|---|
| New `scripts/strength_viewer_worker.py` | Single or dual player process; isolated output/config, shim, exact candidate identity, passive telemetry | Reuses selected tree construction/connection and future ES.2 worker pattern; never imports bots from `src/orchestrator` |
| New `src/strength_viewer/{session,telemetry,api,host}.py` and package entry | Run manifest, bounded latest-state/event files, local API/native host, pending flags | New standalone candidate service avoids modifying frozen bot API modules; host takes explicit run path |
| New `scripts/launch-strength-viewer.ps1` | One operator entry; `-CheckOnly` and explicit single/dual mode proposed | Existing Observer Rush launcher supplies UX precedent, not a paid dependency |
| `frontend/src/main.tsx`, new `components/StrengthViewer.tsx` and tests | Proposed `?strength-view=1&run=<id>` route | Preserve `?battle=1` and normal App routes; existing `useGameState`, CommandPanel and DecisionQueue keep their old WS contract |
| `src/selfplay_viewer/container.py`, `overlay.py` | Optional per-player decision strips and asynchronous discovery | `scripts/selfplay.py`, `scripts/evolve.py`, `src/selfplay_viewer/demo.py`; existing callbacks/default behavior preserved |
| `src/orchestrator/selfplay.py` | Opt-in subprocess launch adapter with run/player identity | `scripts/selfplay.py`, `scripts/evolve.py`, evolve workers and fitness callers: defaults must remain unchanged; coordinate with evolve owner |
| `scripts/scoreboard.py flag`, `src/orchestrator/scoreboard.py` | Optional structured viewer context, replay status and idempotency (proposal) | ES.10 CLI, findings store and `generate_pool`; implementation belongs to their assigned owners |
| `frontend/src/components/EvolutionTab.tsx` and tests | Replace misleading Stop Run action with actual stop instruction | Do not alter `AdvisedControlPanel`'s separate cleanup control |
| New `tests/test_strength_viewer.py`; existing viewer tests | Exercise production entry through process/game boundaries | Existing portable layout/reparent/WS tests provide a regression baseline |

No new third-party dependency is proposed. Reuse optional viewer dependencies and current
FastAPI/React. Require installed prerequisites in `-CheckOnly`; no auto-install, model
download or background service outside the owned launch. New package/API/launch names
are **proposed**, not currently implemented commands.

## Design decisions and contracts

| Choice | Decision | Provenance | Rationale / unresolved state |
|---|---|---|---|
| Workload | Separate live Windows games; WSL scoring remains separate | Operator D16 | Preserve the measurement boundary |
| Shape | Three code slices, no graph/caster work | Operator scope cap; agent grouping | Smallest complete watch-to-flag journey |
| First panel | Effective state, game time, version/mode, win-probability estimate; target only when recorded | Agent default from seed | Unknown data reads “not recorded”; raw PPO output is not fabricated |
| Telemetry bridge | Per-run/player atomic latest snapshot plus bounded event history read by local service | Agent default, proposed | Avoid global WS mixing and new frozen-tree APIs; isolate schema from legacy consumers |
| Candidate source | Explicit version and source/config fingerprint, pin current pointer once at launch | Agent default | Never follow “latest” while the game is running |
| Findings writer | ES.10 only; structured optional context, idempotent retry | Proposed shared contract, unresolved | No duplicate findings writer or note-only identity encoding |
| Layout | Fit an existing preset to measured logical work area; fail with guidance if none fits | Agent default | Do not silently shrink/crop SC2 below supported sizes; operator judges readability in UAT |
| Close/stop | Single: owned Stop match; dual close: detach and stop scheduling further games, let current match finish; evolve unchanged | Existing boundary behavior plus seed | No generic process-kill control; no Ctrl+C advice for threaded viewer |
| Legacy telemetry | Explicit capability map per tree; unsupported fields absent | Agent default | Shim fixes ability compatibility, not observation-schema compatibility |

### Minimum telemetry contract (new, versioned, proposed)

Manifest: schema version, run/match IDs, launch token, source/config fingerprints, SC2
build, map, seed, opponent identity/race/difficulty, player-to-seat map, explicit modes
and policy hashes, output root, lifecycle, replay path/status, owned process identities.
Never include service keys. Single and dual use the same identity envelope.

Each sample: schema version, run/match/player IDs, monotonic sequence, game loop,
game seconds, received wall time, final effective state, field availability and provenance.
Additional fields: heuristic win probability, recorded army orders/target summary,
effective action probabilities and command outcomes when the adapter can observe them.
The panel distinguishes decision intent from observed unit orders. No “attack target”
is inferred merely from ATTACK or from rerunning a decision method in the viewer.

Worker observation is passive: preserve original return values/exceptions, never request
hidden information or issue gameplay actions, and do not recompute the decision engine.
Use a version-specific external adapter around existing observation/execution seams;
reject unsupported versions explicitly instead of selecting another candidate. Prefer
ES.2's isolated construction code when it lands; otherwise prove parity against the
actual selected runner for v0 and v13. No source modifications to those trees.

The worker's single-match boundary must explicitly supply `random_seed` to burnysc2's
`run_game` and preserve normal fog, replay and bot construction. Do not simply pass an
unsupported seed keyword to the frozen `run_bot`, or record an unused seed in the manifest.
Use a narrow external connection adapter and test its arguments against the actual
installed burnysc2 signature; this is an intentional viewer-only boundary extension.

For v13, show chosen strategic state and heuristic estimate; capture current unit-order
targets as observed orders when no single army target exists. PPO probabilities require
source attribution; an override's one-hot vector must say “effective override.” Missing
raw probabilities remain missing. A version lacking these features still shows valid
identity/time and recorded fields, with the limitations visible.

Bind readiness to run/match/source fingerprint, not a generic page-loaded signal. Only
the exact rendered run may release game startup. Bound readiness timeout and stop before
launch on mismatch. No “newest run” fallback. Show stale/ended state and retain the last
timestamp; do not animate historical state as live. Limit file sizes/history and use
atomic replace with tolerant readers; telemetry I/O failure must not break gameplay.

Local API is a separate, dynamically selected loopback endpoint whose address is passed
by the launcher to the page; no second dashboard server on 8765. Validate Host/Origin and
an unguessable launch token for ready/stop/flag mutations, with explicit allowed origins.
Run IDs resolve under the configured root only; reject traversal, arbitrary file writes
and shell interpolation. One launcher owns the child processes and their cleanup.

### Findings contract to agree with ES.10

The parent currently specifies:

```text
uv run python scripts/scoreboard.py flag --version vN --replay PATH --game-time MM:SS --class execution|information|composition|strategy|infrastructure [--tactical T.n] --note TEXT
```

That is **planned parent syntax**, not a verified executable in this checkout. Optional
structured extensions should carry `source=windows-spot-check`, run/match/player IDs,
both versions, SC2/map identity, stable event ID and replay status. Names are proposed;
agree the public shape before VW.3. ES.10 remains responsible for validation, deduplication
and the single findings append. Preserve old CLI input and old rows without new fields.

On Flag, freeze the selected player's sample/time immediately; the note may be entered
later. Save a local pending event durably and show “saved locally, awaiting replay” while
the game runs. Forward to ES.10 only when replay is finalized, or through an agreed
explicit unavailable-replay representation after failure. A reserved path is not an
existing replay. Restart/retry uses the same event ID; receipt proves one durable finding.
Failure retains the local event and a retryable error. Never claim proposer delivery
until the production consumer reads the canonical finding. Opening a GitHub issue is out
of scope. Flags never alter gameplay or score a Windows game as a Linux panel case.

## Build steps (proposed IDs; main allocates and assigns ownership)

### Step VW.1: Watch one exact candidate with its recorded decisions

- **Problem:** The operator cannot view a selected main-bot candidate beside trustworthy, run-bound decision evidence.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Files:** new `scripts/launch-strength-viewer.ps1`, `scripts/strength_viewer_worker.py`, `src/strength_viewer/` package; `frontend/src/main.tsx`, new `StrengthViewer.tsx` and tests; new `tests/test_strength_viewer.py`; minimal native-host extraction if needed.
- **Produces:** Single-candidate live spot-check entry, exact-run ready barrier, isolated game/replay/telemetry, state/estimate/order evidence panel, owned Stop match and CheckOnly.
- **Done when:** The production launcher/worker/API/page chain with only the SC2 boundary replaced creates a fixed run, displays that candidate, refuses stale/wrong readiness, streams two samples without cross-run leakage, distinguishes missing fields, and records terminal/replay status after stop. A rules and a hybrid fixture retain mode/model identity; unsupported/missing model fails closed. Original per-version data and frozen source hashes stay unchanged. Stop before readiness launches no game; stop after readiness requests normal leave only for the owned match. UI failure preserves diagnostic evidence and owned-child cleanup. Native live acceptance remains the separate bundle below.
- **Depends on:** none after ownership assignment; coordinate the compatibility shim with ES.2 (already present in the working tree, not evidence of a shipped dependency).

### Step VW.2: Watch both players of one independent spot-check match

- **Problem:** Two live candidates cannot be compared with each side's correctly attributed decision evidence.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Files:** VW.1 launcher/worker/telemetry; `src/selfplay_viewer/container.py`, `overlay.py`; opt-in adapter in `src/orchestrator/selfplay.py`; existing callback/CLI/layout tests plus `tests/test_strength_viewer.py`; `frontend/src/components/EvolutionTab.tsx` and test for the D7 instruction.
- **Produces:** Dual layout from the existing container, a decision strip per player, stable seat mapping, responsive window discovery and accurate lifecycle instructions.
- **Done when:** Through the new production launch route, two fake process-boundary workers emit interleaved records and only their own panes update; same-version opponents remain distinguishable by player ID, and seat reversal preserves identity. Missing/delayed/crashed HWNDs produce responsive placeholders, never block the event pump for 15 seconds. No third client launches. Closing the dual container restores child windows and prevents subsequent matches while the current bounded match completes; teardown and process exit ownership are tested. Existing selfplay and evolve default callback/close behavior remains unchanged. Evolution UI offers the real console-close instruction and no false stop receipt. Native fit, old-version SC2 compatibility and orphan checks remain the manual bundle.
- **Depends on:** VW.1; coordination with evolve owner before any shared selfplay/container change.

### Step VW.3: Flag an observed moment into the existing findings path

- **Problem:** A behavior seen in either viewer cannot be preserved with enough context for ES.10's proposer evidence.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Files:** VW.1 API/telemetry/UI, dual event forwarding in viewer container, `tests/test_strength_viewer.py`; coordinated ES.10 schema/writer/consumer tests in `tests/test_scoreboard.py` and `tests/test_evolve_cli.py` owned outside this lane.
- **Produces:** Flag button and focused-view shortcut, player selection, frozen game-time context, local pending receipt, canonical finding receipt and restart-safe retry through ES.10.
- **Done when:** A test drives the production UI/API handler to the real ES.10 writer using a temporary run/replay and then invokes the real proposer evidence builder with only its external model call stubbed. Exactly one canonical finding contains the selected player/time/version/replay context; it appears in the bounded proposer evidence block. Duplicate submission/restart adds no second finding; stale/cross-run/wrong-seat input is rejected, in-progress replay stays pending, unavailable replay follows the agreed explicit state, and write failure retains the local event. Legacy findings still render. No training DB or scoreboard result row is written. Real behavior capture remains manual acceptance below.
- **Depends on:** VW.1, VW.2, ES.10; structured flag contract agreement is a build blocker.

## Manual acceptance bundle (separate operator work)

Three code slices do not hide human approval in their Done-when fields. Main should
schedule this as an operator acceptance item after VW.3, preserving the three-build-step
cap. It produces observed acceptance, not code. Run only after authorization and reserve
Windows SC2; no games are authorized by this drafting session.

Proposed commands to be implemented and verified by VW.1/VW.2 (not executable today):

```powershell
powershell -NoProfile -File scripts/launch-strength-viewer.ps1 -CheckOnly
powershell -NoProfile -File scripts/launch-strength-viewer.ps1 -Version v13 -OpponentRace Terran -Difficulty 3 -Map Simple64 -Seed 11
powershell -NoProfile -File scripts/launch-strength-viewer.ps1 -P1 v13 -P2 v0 -Map Simple64 -Seed 11
```

1. CheckOnly reports the selected candidate/fingerprint, installed shim, normal-fog
   settings, prerequisites, existing client count and fitting preset without starting
   SC2. Run a full bounded single match plus a separate early-stop case. Confirm exact
   identity before gameplay; compare state/time and a recorded unit order with the game.
   Stop match leaves normally, finalizes evidence and leaves unrelated processes alone.
2. Run a full bounded dual match and a separate mid-match detach case. Capture measured
   monitor work area, DPI and both layouts if needed. Confirm both clients are legible,
   each strip follows its own player, no third client/hidden vision exists, and old v0
   passes the historical roughly 45-second compatibility failure point. Detach restores
   both windows; the owner finishes the match and exits without orphaned children. Do
   not use Ctrl+C as a threaded-viewer test gesture.
3. Flag a real visible behavior from each layout. Note the moment before entering text.
   After game end, reopen the pending/canonical finding and replay; locate the same game
   time and selected player. Verify the next proposer evidence preparation includes it
   without making a paid proposal call. A replay file that cannot open is a failure to
   investigate, not an accepted video substitute.
4. Record run IDs, source/config/SC2 hashes, replay locations, screenshots, observed
   capability limits and owned-process state after stop/detach. Operator accepts readable
   sizing and usefulness of the initial lanes. Fixture and compile passes cannot close
   this bundle.

This is an attended one-match workflow, not a daemon. One complete match plus lifecycle
edge cases exercises time-dependent telemetry/stale-state behavior; an overnight viewer
soak is outside scope. A live deployment seam remains: native HWND/DPI/audio/window
ownership must be observed on Windows, even after all automated tests pass.

## Disposition of superseded work

| Old item | Where its necessary behavior is covered |
|---|---|
| EV.4 / EV.5 | Dual live acceptance above; no renewed live-evolve smoke/soak obligation |
| SV.1 detach/orphans | VW.2 close/restore/owned-process tests and manual detach case |
| SV.2 threaded interrupt ownership | VW.2 worker lifetime and explicit close instructions; no claimed Ctrl+C safety |
| SV.3 display sizing | Existing preset fit, measured work area/DPI and human readability acceptance |
| SV.4 surrender-label leak | Clear labels/evidence at match boundaries; same-version/next-match tests; inspect terminal display |
| SV.5 blocking discovery | VW.2 incremental HWND discovery with responsive placeholder and timeout tests |
| SV.6 legacy leftovers | Review against the above bundle; retain unresolved findings explicitly rather than inherit historical passes |
| UI.7 / UI.8 | Already superseded by D10; provenance stays historical, no new build steps |
| Unwired evolution Stop Run | VW.2 replaces action with real D7 instruction; advisor cleanup unchanged |

## Risks, blockers and next review

| Item | Consequence / mitigation | Owner / blocking state |
|---|---|---|
| No agreed structured ES.10 findings contract | Context loss/duplicate flags; agree schema and replay failure semantics before VW.3 | main plus scoreboard/evolve owners; **blocks VW.3** |
| New implementation paths unassigned; shared files owned by evolve | Concurrent modifications; assign paths and sequencing before implementation | main; **blocks dispatch** |
| Passive adapters across frozen trees | Wrong telemetry or changed behavior; capability maps and same-construction parity tests | future viewer implementer; **must resolve in VW.1** |
| Three-step cap threatened by host generalization | Scope expansion; extract only needed native hosting and keep rich optional features deferred | main/implementer; reassess before adding a fourth code step |
| Real army-order evidence unavailable in a version | Misleading explanation; show availability and observed unit orders instead of guessed target | viewer adapter; explicit capability limit |
| WSL and Windows SC2 differ | Misleading strength claims; separate source/SC2 identity and score namespaces | ES/viewer owners; mandatory invariant |
| Display size unknown, native integration untested | Can pass fixtures but be unusable | operator acceptance remains open |

No product permission is requested merely to save this draft. Consequential changes
to scope, paid behavior or three-step cap require the operator. Main can integrate the
draft after ownership/interface agreement, then use the fixed downstream sequence:
`plan-review --autofix` → `plan-redline --plan` → `plan-wrap --autofix` →
`repo-sync --plan` → `build-phase --plan`. None of those reviews, publication or builds
is claimed by this artifact; canonical `docs/plans/` is untouched by direction.

## Testing strategy and evidence

Use production-caller integration for session readiness, worker isolation, telemetry,
flag persistence and proposer consumption. Stub only SC2/OS/external-model boundaries;
retain real serializers, routes and writers. Test missing/delayed windows, stale feeds,
wrong run/seat/token, default preservation and legacy findings compatibility. Follow
current repository gates when implementation happens; this planning change only checks
existing reuse seams and source syntax.

Verified commands from `frontend/package.json`: `npm run test:run`, `npm run build`,
`npm run lint`. Python uses the project `.venv/Scripts/python.exe -m pytest` (equivalent
installed environment to `uv run pytest` without sync). Planned targeted additions are
`tests/test_strength_viewer.py` and its corresponding frontend test; these files do not
exist yet. Real Windows acceptance stays separate from mock/portable suites.

Actual planning validation and limitations: [viewer validation receipt](viewer-validation-2026-11-16.md).
