Reviewing as: feature plan. Sections 17–21 apply.

Reviewed 2026-10-07 against source HEAD `f42b9b6` and `jev-player-plan.md`. This is a plan review, not a runtime certification. No populated Issue fields; no GitHub mutations.

## Checklist evidence

| Check | Result / evidence |
|---|---|
| 1 Data persistence | Pass: section 6 D5 names immutable policy/metadata, atomic state, rotated events, unique per-run writers and corruption behavior. |
| 2 External dependencies | Pass: installed burnysc2, FastAPI, React and d3-hierarchy; `pyproject.toml` and `frontend/package.json` read. No new remote service. |
| 3 Authentication/secrets | Pass: no new credential or listener; read-only endpoints inherit dashboard boundary. |
| 4 Async/concurrency | Pass: nonblocking graph lanes, per-tick actor/resource reservations, isolated run directories and polling cancellation. |
| 5 Errors/feedback | Pass: section 5 stable errors; section 6 stale heartbeat, task failure, waiting and terminal state. |
| 6 Toolchain | Pass: section 9 enumerates install, dev, Python/frontend build, test, lint and strict typing; scripts checked against package manifests and CI. |
| 7 Decisions | Pass: D1-D6 are chosen defaults, not unresolved alternatives; P1-P8 record user choices. |
| 8 Setup | Pass: section 9 gives SC2/map, uv/Node setup, three-terminal startup and no-key requirement. |
| 9 Idempotency | Pass: task intent keys, graph actor ownership, pending acknowledgement and unique run IDs. |
| 10 Seams | Pass: section 5 typed producer/consumer summaries and API contracts; no legacy schema/signature migration. |
| 11 Scope | Pass: player/viewer only; no evolution, registry overhaul, graph editor or new service. |
| 12 Security | Pass: allowlisted operations, no eval/import expressions, validated run IDs and contained paths. No runtime LLM prompt injection surface. |
| 13 Testing | Pass: scenario, actual writer/router, browser, installed wheel and live SC2 layers distinguished. |
| 14 Operations | Pass: one-match foreground runner, terminal result, bounded game/wall time, clean leave, preserved evidence. |
| 15 End-to-end observation | Pass: Step 208 observes three complete games and classifies functional failure versus tuning. |
| 15.5 Pipeline smoke | Pass: Step 207 uses real SC2, interpreter, disk, API and browser before full matches. |
| 16 Fresh-context readiness | Pass after inline metadata/error and binding clarifications; section 5 contains schemas/IDs, section 9 quickstart. |
| 17 Source validation | Pass: actual bot on_step, neural engine, runner parser, registry discovery, FastAPI app, App tabs, tree component and manifests read. |
| 18 Impact completeness | Pass: new shared package, API mount, frontend deep-link tests, packaging/typecheck and docs listed; `.gitignore` verified read-only. Existing signatures unchanged. |
| 19 Conflicts/conventions | Pass: active v13 confirmed by pointer; EH/EI overlap requires serialized edits and anchor recheck. Master index now reserves JV 201-208. |
| 20 Existing context | Pass: section 2 distinguishes strategic-state predictor, scripted routines, optional advisor and version registry. |
| 21 Step size | Pass: validator/runtime, economy, army, evidence API, inspection UI, validation workflow, smoke and full-match acceptance are separate observable slices. |
| 22 Operator/code split | Pass: Steps 207/208 consume authored tooling and produce evidence only; no code step demands human acceptance. |
| 23 Conditional predicates | N/A: no conditional steps. |
| 24 Reviewer shape | Pass: UI Step 205 uses full + startup command + URL; other implementation steps use deep code review without runtime flag requirements. |
| 25 Build format | Pass: eight numeric headings 201-208 with Problem/Type/Issue/Files/Done when/Depends on; blank issues expected before repo-sync. |
| 26 Substrate smoke | Pass: Step 207 explicitly runs real game/install/CLI/dashboard; unavailable SC2 leaves gate pending. |
| 27 Stakes routing | Pass after routing 203/206 to deep: runtime command/lifecycle and evidence verifier are producer-consumer boundaries. Source: installed `review-deep/core.md` header, high-stakes trigger owner. No model override set. |

## Blockers

None remaining.

## Significant gaps

None remaining. Review clarified metadata/error fields and root-local graph bindings, and routed Steps 203/206 to deep review. These are implementation defaults rather than new operator requirements. Provider high-stakes review tier can be considered at build dispatch; this plan does not prescribe a model.

## Missing items

None remaining. Source files listed as proposed are implementation deliverables, not claims that they already exist.

## Nice-to-haves

None required for this milestone. Themed-window graph and evolution remain explicitly deferred.

## Review edits

- Verified `/data/` ignore coverage and removed the proposed redundant ignore edit.
- Added inline metadata/error shapes and binding ownership.
- Escalated Step 203 and Step 206 reviewer flags for runtime/evidence boundaries.

## Plan-expedite rerun (2026-10-07)

Rechecked sections 1-27 against unchanged source HEAD `f42b9b6`, current plan and existing package/API/frontend producers. All prior fixes remain resolved. Added the repo-sync UI-bundle requirement to Step 204 before issue creation: `--ui`, startup URL and browser evidence through the real Vite proxy. This tests the API/dashboard seam without pretending the Step 205 graph screen exists yet. No user design choice changed. Proposal defaults P1-P8/D1-D6 remain unchanged.

Auto-applied 1 fix: Step 204 UI-bundle declaration and concrete browser acceptance. 0 items need your input. All four finding tiers remain empty after the correction. Live acceptance remains unperformed.

READY (auto-fixed 1 item)
