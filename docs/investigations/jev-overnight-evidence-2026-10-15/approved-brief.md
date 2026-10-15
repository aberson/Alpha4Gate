# Proposed overnight execution brief — October 14–15, 2026

Status: APPROVED by operator; active since October 14 at approximately 23:12 Pacific.
Authorized deadline: October 15, 09:00 America/Los_Angeles (16:00 UTC).
This is an execution brief for existing master-plan work, not a new feature plan.

Verified state: root c0836c3; documentation migration and local compatibility/media edits remain uncommitted. Preserve them. Offline Step225 candidate84c8525 and compatibility candidatefc66956 are pushed on separate branches. JDI232 candidate00bc515 remains held and unmerged.

Primary target:
1. Diagnose the existing recovery test failure in isolation. Establish the exact accepted-request, completion and local-fallback timeline through production entry points. The full-match assertion may be stale after shipped recovery completion; that is a hypothesis, not permission to weaken it. Make a minimal separate baseline repair only if evidence establishes the correct contract; preserve assertions about behavior while the request is active. Do not fold in the held232 implementation.
2. Re-run appropriate focused checks and full declared suites. Bring the verified baseline repair into the existing225 checkout without overwriting its five-file candidate.
3. Run the ONE remaining required independent225 review batch only after mechanical prerequisites pass. Retain prior failure history. If it passes all gates, prepare the accepted scoped result for landing without sweeping the root migration. If it fails, preserve it and report; no reset or extra review loop.

Independent next task if225 finishes or is held:
4. Diagnose/design the structural232 repair from the existing audit: actor ownership, cancellation versus actual SC2 orders, and position-only recovery success. Produce a bounded repair proposal with decisive-tick regression requirements. No fourth runtime patch round under this brief.

Limits:
- Hosted/API match spend: $0. Existing cumulative$5 approval is unchanged and unspent by this brief.
- No226/227 implementation, paid matches, promotion, frozen-v1 recapture, or broad runtime rewrite.
- Preserve original stopped worktrees and the operator's documentation migration.
- Checkpoint after each gate; report exact tests, reviewer verdicts, commits and remaining blockers by the deadline.

Master-plan next steps after these holds clear:
232 ->233 offline behavioral evidence ->234 timely/richer army questions ->235 independent macro choices.
225 ->226 durable budget ->227 controller ->228 attended smoke.
Both streams feed236 experiment tooling; hosted measurement follows237-239.

## Live checkpoint — 2026-10-15 06:22 UTC

Root remains c0836c3. Recovery failure reproduced in unchanged Step225 checkout (exit 1, same test/line). New isolated baseline worktree: ../worktree_jev-recovery-test-lifetime, branch fix/jev-recovery-test-lifetime, starts at c0836c3. No existing candidate overwritten. Baseline repair uses build-step automated gates with a bounded independent test-quality review before acceptance; Step225 retains its required full deep review. Full Python is a required rerun after the repair; frontend unchanged from root c0836c3, but full declared suites will be recorded for final integration. No review of Step225 has yet consumed the one remaining batch. Hosted spend remains $0.

## Gate checkpoint — October 14, 23:36 Pacific

Baseline repair committed/pushed as e41e4b9 on fix/jev-recovery-test-lifetime. Sole file tests/test_jev_v2.py, +44/-5. Full isolated Python3396passed13skipped2deselected, strict mypy832, Ruff/scoped format pass; frontend417passed6skipped/buildlint0. Fresh strong Sol test review PASS, no findings; root and baseline inventories unchanged during review. Repaired test applied surgically to root (not sweeping staged migration), cherry-picked into existing225 as324d5f0; five campaign source hashes remain unchanged. Root and225 full Python checks running. No Step225 reviewer dispatched yet.

Root strict mypy833passed. Root Ruff306violations all belong to five untouched docs/plan-reconciliation helper scripts from completed foreign migration: reconcile145, closeout71, finalize70, repair-historical-links11, validate-migration9. Do not fix/sweep this separate work or claim root fully green. Isolated candidate required gates remain distinct.

Step232: additional offline task-lifecycle probe dcd67be496f048dcbc5e9ad219422e4f confirms position-only ack/completion, #1649–1664 and#1754–1755 with8retainedATTACKorders/0replacementcommands. Source00bc515unchanged. Independent necessity/design challenge NEEDS_DESIGN; one bounded addendum proposed with order-state/reconciliation tables and commit ordering, not independently accepted and not implemented. docs/investigations/jev-safety-repair-design-2026-10-15.md. $0 hosted spend.
