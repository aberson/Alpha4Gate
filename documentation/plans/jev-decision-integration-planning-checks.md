Reviewing as: feature plan. Sections 17–21 apply.

## Current amendment: 2026-10-14

Operator approved the proposal, the $5 cumulative maximum with flexible allocation, and an overnight build until 09:00 Pacific (16:00 UTC). The $1 first-stage subcap and repeat budget approvals are superseded. All prerequisite probes/smokes undertaken for this investigation count against the same allowance. Phase reports and quality/safety gates remain; no games have run at this checkpoint.

Delta plan-review: no remaining blockers or significant gaps. Corrected the prerequisite-smoke circular wording: its budget guard must pass before that smoke, while the smoke must pass before JDI evaluation. Step 235 now declares required UI evidence and explicit frontend start command/URL, per repo-sync and build-step's UI contract. The nine-step dependency graph, $5 ceiling, sealed-panel scoring, worktree isolation and independent review lanes are preserved. The proposal is republished at the same locator as v2; D1–D6 accepted, D7 amended, P5/P6 appended without renumbering.

Delta plan-wrap: sections 1–13 rechecked against the amended plan. Schemas/IDs/stack/process/setup/scope remain defined; no new API contract; no conditional steps; operator/code boundaries and substrate smoke remain intact. Approval is recorded authority, not a future unresolved implementation choice. **READY** for issue synchronization. The review below is the historical 2026-10-13 snapshot; its statements that spending was unapproved describe that earlier checkpoint only.

## Historical planning review: 2026-10-13

2026-10-13. Target: [JDI plan](jev-decision-integration-plan.md). These are plan-review, proposal and clean-context checks performed in the planning session, not an independent implementation review. No product tests, implementation, hosted calls or matches were performed. The earlier [adversarial investigation review](../investigations/jev-model-involvement-2026-10-13-adversarial-review.md) remains a separate evidence source.

## Technical plan review

**Blockers:** None remaining in the authored plan. JON Step 225's existing BLOCKED status is an explicit downstream dependency, not a claim that JDI's live stage is ready.

**Significant gaps:** Resolved before proposal publication:

1. The draft impact table and Step 233 named `src/jev/diagnostics.py` and `validation.py`, which do not exist. `rg --files src/jev` confirms this; actual diagnostics live in `bot.py:243` (`MatchMetrics`), with reading/validation in `benchmark.py:2449`, `:2705`, and `scripts/validate_jev.py`. Corrected those paths. Also corrected #346's owner to `aberson` from `git remote get-url origin`.
2. Draft §6 D5 separated macro/army but left the precedence between strategic recovery and army attack implicit. `_apply`/`_slots` at `strategy.py:1273`, `:1504` and the overlap at run `3c5f624b #57519–57609` make that omission material. Defined emergency → recovery → army intent → fallback, shared eligibility and same-tick invalidation.
3. Step 234's Files omitted policy/runtime files named by its Produces; Step 235's mix setting omitted launcher/serialization consumers. Added these files and the exact shared-contract consumer inventory in §4. Verified `MatchOptions` in `runner.py:250`, serialization and callsites with `rg -l` across source/scripts/tests/frontend.
4. Draft persistence and experiment records lacked corruption/ownership rules, and “exact paired-discordance intervals” lacked a method. §5 now specifies atomic immutable writes, JON lease ownership, wire types and untrusted response boundaries. §6 D6 defines missing versus failed games and the descriptive Clopper–Pearson interval denominator.

**Missing items:** Resolved before final wrap:

5. Toolchain coverage omitted development startup and literal frontend lint. Added backend/frontend dev commands, existing ports, prerequisites and `npm run lint`; checked `CLAUDE.md` and `frontend/package.json`.
6. Added-context “bounded” and future experiment CLI were underspecified. Defined a 31-sample ring, 16 KiB additional summary bound, deterministic sighting omission, and explicit prepare/run/resume/report flags with a no-write/no-network dry run in §9.

**Nice-to-haves:** Blank Issue fields deliberately await synchronization. First-milestone scope, timing, panel size and thresholds remain visible agent defaults in the proposal rather than disguised operator choices.

Auto-applied 6 fixes. Plan is ready for `/plan-wrap` and `/repo-sync`.

Review coverage: persistence/dependencies/secrets/async/errors/toolchain/setup/idempotency/seams/security/tests/operations examined; no new API route or deployment service. Existing HTTP transport remains the paid boundary and JON owns spending enforcement. Steps 237–239 provide real substrate and sustained observation gates. Steps 232–236 use deep review because shared runtime, producer/consumer and schema boundaries match `review-deep/core.md`'s trigger paragraph; Step 240 is a documentation report. All nine step headings and Problem/Type/Status/Files/Issue/Flags/Produces/Done when/Depends on fields were mechanically checked. Code/operator/wait artifacts are separated. No conditional steps or runtime/full reviewer flags require additional predicates or review URLs.

The paid comparison is intentionally staged and uncertain: 75 games total, 38 hosted; $1 first-stage then $5 cumulative proposed cap. The larger conservative token bound exceeds the cap, so the plan explicitly permits a partial/inconclusive result. No fixed completion promise or paid authorization is implied.

## Proposal publication

[Standalone proposal](jev-decision-integration-proposal.html), publication v1. P1–P4 trace operator constraints; D1–D7 are agent defaults. Each default has what/why/tweak text. The decision inventory and stable proposal locator are in the canonical plan. No external publication or messages were sent. A separate [progress view](../jev-decision-integration-progress.html) uses the workspace starter with nine pending inventory units and no implementation acceptance.

## Clean-context wrap

Completion gate: no consistent completion markers found -- running full check (fail-safe default).

§1 Schemas and data structures — pass: §5 summarizes all new records, wire types, storage and null/unknown behavior; existing Observation/Entity fields are summarized in §4.

§2 Identifiers — pass: §5 defines run/opportunity/question/experiment/case/arm IDs, event sequences and hashes; JON owns campaign IDs as an explicit dependency.

§3 Acronyms and tool names — pass: graph/model/Observer Rush/SC2 are introduced; project paths and tools have defined roles. UUID and SHA-256 identify conventional formats explicitly.

§4 Stack decisions with rationale — pass: reuse the existing Python/httpx/pytest/React toolchain, benchmark and JON lifecycle; no additional service.

§5 Unresolved decisions — pass: defaults are concrete; paid approval is an intentional future gate, not an unresolved implementation alternative.

§6 API contracts — N/A: no new route or external API shape; existing provider parser remains in use.

§7 Development process — pass: §9 states worktree, producer/consumer checks, review lane, phase stops and issue-sync prerequisites.

§8 Quickstart / how to run — pass: §9 includes install/dev/build/test/lint/typecheck, offline checks and the planned CLI syntax, clearly distinguished from commands available today.

§9 Referenced external files — pass: current source producers were inspected; proposed new modules/runbook/fixtures are explicitly marked future. JON and historical evidence are summarized inline; the key-loading mechanism is named without reading credentials.

§10 Scope and constraints — pass: §3 excludes new capabilities and autonomous promotion, preserves local command authority, immutable v1 and explicit paid gates.

§11 Operator/code step-shape integrity (Blocker if violated) — pass: operator/wait steps produce approval/run evidence; runbook and implementation belong to code Step 236.

§12 Conditional steps must declare a Condition: predicate (Blocker) — N/A: no conditional steps.

§13 Substrate-smoke step present when the plan touches deployment seams (Significant Gap) — pass: Step 237 follows accepted JON smoke and precedes the longer panel.

**Blocker:** None.

**Gap:** None in plan self-containment. Runtime readiness still depends on implementation, required independent code reviews, JON dependencies and explicit paid approval.

**Minor:** Issue synchronization pending by design. Proposal/progress received static structure/link checks; browser visual behavior has not been validated in this planning turn. Progress retains the workspace template's tab/search/print code.

READY


## Issue synchronization and build preflight: 2026-10-14

Target verified as aberson/Alpha4Gate, default branch master. Created umbrella #354 and nine rich step issues #355-#363; verified returned titles, umbrella links and acceptance fields. Plan Issue fields were backfilled. No existing issue was modified or closed. Plan pipeline: plan-review + plan-wrap -> repo-sync (step 4 of 5) -> build-phase.

Required host capability probes passed: explicit no-history siblings, calibrated Conversation challenge v2, rejection of a child write_stdin against a disposable parent service, private service-held key, data-only summary storage, fail-closed corruption and per-open key rotation. All probe services closed and disposable sidecar removed. No probe secret, service handle or signed payload is included here. This establishes workflow capability, not product acceptance.
