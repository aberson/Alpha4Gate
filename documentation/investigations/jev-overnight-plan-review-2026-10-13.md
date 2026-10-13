Reviewing as: feature plan. Sections 17?21 apply.

# Overnight plan review ? 2026-10-13

Target: `documentation/plans/jev-overnight-improvement-plan.md`, autofix mode.
Same-session instruction-loaded review; not an independent reviewer verdict.

## Blockers
Resolved: named storage lacked transactional ownership/resume schema (old section 5,
?budget ledger?); pre-budget archived sources could not obey the proposed HTTP guard
(old D1/D2); phase-local headings did not match plan-review section 25's numeric walker.
D7 defines schemas/ledger/leases; D8 excludes incompatible snapshots and starts a new
instrumented epoch; numeric Steps 225-231 follow existing 201-224 without collision.
No remaining blockers.

## Significant gaps
Resolved: missing file/caller inventory and setup/quality commands; vague screen outcome;
unspecified advisor execution boundary and price profile; no distinct incomplete verdict;
protected budget descriptor/child exit handoff unspecified. D7-D9 resolve these.
Steps 226/227/230 escalated to --reviewers deep per the installed review-deep/core.md
high-stakes header (money/provenance/process and generated-code boundaries).
High-stakes review tier is advisable; no model override was auto-selected.
No remaining significant gaps.

## Missing items
Resolved: explicit Type on four code steps, Files/Status/Existing context on seven steps,
exact storage IDs and CLI lifecycle/exit contracts. Step artifacts remain future work.
No remaining missing items.

## Nice-to-haves
Blank Issue fields are expected before repo-sync. No extra dashboard/network API is
introduced. Progress is seven pending units, zero implemented/accepted, not a runtime claim.

## Check coverage
1 Persistence: D7 SQLite ledger + atomic records, corruption blocks.
2 Integrations: pinned Typesafe, bounded Anthropic adapter, no hidden retries.
3 Secrets: existing DPAPI path, no keys in snapshots/model-facing patch process.
4 Concurrency: short ledger transactions off event loop, one desktop owner.
5 Errors: invalid/incomplete/blocked explicit; report never invents a win.
6 Toolchain: D9 install/dev/build/test/lint/typecheck commands.
7 Decisions: agent defaults explicit; runtime credentials/start window are launch inputs.
8 Setup: Windows/SC2/OBS/Edge/dependencies/key prerequisites named.
9 Idempotency: immutable manifest, unique attempts, completed cases never replayed.
10 Seams: budget descriptor, child result, RunExpectation, exact attachment identity.
11 Scope: prepared evaluation first; self-play/pools/default switch deferred.
12 Security: argument lists, path validation, patch allowlist, traces as untrusted data.
13 Testing: real production seam coverage and failure-path tests, no paid prep tests.
14 Operations: prepare/run/resume/stop/report; no automatic schedule installation.
15 Observation: Steps 228/229 then 231, actual runtime acceptance separate.
15.5 Data pipeline: real 60-second hosted smoke before soak.
16 Fresh context: D7-D9 inline contracts and first-run CLI.
17 Existing code: runner.py:253-263/593/708/732; benchmark.py:2340/2533;
   probe.py:264-341; launch-jev-battle.ps1:8-16. Actual defaults match Terran/Simple64.
18 Impact: grep of TypesafeProvider, MatchOptions and run_match consumers enumerated;
   budget outside MatchOptions avoids legacy metadata shape migration.
19 Conflicts: dirty pacing/recovery/viewer changes preserved; J2 and RL gates not altered.
20 Context: module responsibilities and historical compatibility explicit.
21 Sizing: contracts, budget dispatch, sequential host, then advised adapter are distinct slices.
22 Operator/code: 228 manual only; 229/231 waits; code steps have mechanical acceptance.
23 Conditions: none, N/A.
24 Review flags: no runtime/full lane without URL; native acceptance separately in 228.
25 Format: seven numeric headings, required fields, pending status verified mechanically.
26 Deployment seam: real native hosted smoke, not mock-only acceptance.
27 Stakes: deep review routed for 226/227/230; others unchanged.

## Auto-applied fixes (12 grouped changes)
1. Numeric step mapping/master index (all seven steps).
2. Explicit code Type fields (225/226/227/230).
3. File inventories (all seven).
4. Existing-context/status fields (all seven).
5. Stakes routing (226/227/230).
6. Exact screen/assessment verdict rules (225).
7. Transactional records/resume/lease and seed identities (225-227).
8. Historical instrumentation compatibility (225-227).
9. Metered provider/child forwarding contracts (226-227).
10. Bounded patch-only advisor and profile (230).
11. CLI/setup/quality commands and caller inventory (225-227/230).
12. Explicit cache control, stop margin and failure behavior (225-227).

Auto-applied 12 fixes. Plan is ready for `/plan-wrap` and `/repo-sync`.
READY (auto-fixed 12 items)
