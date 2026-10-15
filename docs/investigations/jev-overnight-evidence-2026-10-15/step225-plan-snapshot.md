### Step 225: Freeze campaign contracts and weighted evaluation

- **Status:** HELD â€” original three-review authenticity/execution-authority failure; bounded prospective round only after reorientation
- **Type:** code
- **Issue:** #347
- **Depends on:** none outstanding; retain shipped prerequisites
- **Source:** [jev-overnight-improvement-plan.md](../archived-plans/jev-overnight-improvement-plan.md); original 225.

- **Problem:** Implement schema-versioned manifests, champion references, exact case matrices and deterministic scoring described in D2/D3.
- **Files:** src/jev/campaign.py; src/jev/campaign_evaluation.py; scripts/run_jev_campaign.py; tests/test_jev_campaign.py; tests/test_jev_campaign_evaluation.py
- **Existing context:** Sections 2 and 6 define the production boundaries; D7 assigns offline contracts/scoring here and execution authority to 227. Preserve the held original attempt and official J2 panels/desktop defaults.
- **Flags:** --reviewers deep --isolation worktree
- **Produces:** `campaign_evaluation.py`, manifest portions of `campaign.py`, focused tests, dry-run/report entrypoint skeleton.
- **Done when:** Dry-run writes/validates an immutable no-network manifest; tests cover duplicate anchors, missing strata, unchanged candidate, weighted improvement masking race regression, invalid evidence and J2 panel isolation. The production CLI labels offline scores unverified and refuses live verdicts/promotion until 227 supplies verified execution facts; caller claims cannot enable an authoritative report.

<!-- autofix-applied: 2026-10-13 -->
