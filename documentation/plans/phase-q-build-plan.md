# Phase Q — harvest-engineer skill (build plan stub)

> **STATUS: STUB.** Phase Q scope is gated on
> [`harvest-engineer-skill-scope-investigation.md`](../investigations/harvest-engineer-skill-scope-investigation.md).
> This stub captures the SKILL-template-level decisions that hold
> regardless of investigation outcome. Per-mode wiring details
> (intake parsers, paper-format handling, retroactive-feasibility test
> design) come from the investigation §6 findings. Promote this stub
> to a full plan once the investigation finishes.

## 1. What this phase ships

A new Claude skill at `.claude/skills/harvest-engineer/SKILL.md` that
takes external knowledge (a pasted paper, a "look into X" suggestion)
as input and produces a scoped investigation document, optionally
hands off to `/improve-bot-advised` or `/build-step` for
implementation. v1 ships **Mode A only** (paste-a-paper); Mode B
(investigate-from-suggestion) is a thin wrapper over Mode A and ships
in v1 if the SKILL prompt comes together cleanly, otherwise as a
v1.1 extension.

Differs from `/improve-bot-advised` by:

- **Intake.** Advised reads game logs; harvest reads papers and
  external write-ups.
- **Resolution.** Advised proposes code-edit-shaped improvements
  (one-game behavior fix). Harvest proposes architectural changes
  that span multiple phases.
- **Output shape.** Advised commits code under `[advised-auto]`.
  Harvest produces a doc (investigation skeleton + scoped plan stub),
  defers code to a separate skill invocation.

## 2. Existing context

Pulled from the master plan's Phase Q pointer-stub. Read the master
plan's Phase Q section first.

**Templates to mirror:**

- `.claude/skills/improve-bot-advised/SKILL.md` — closest cousin.
  Run-start banner, sandbox declarations, commit-tag convention.
- `.claude/skills/improve-bot-evolve/SKILL.md` — second pattern, with
  `[evo-auto]` widened-sandbox precedent.
- `documentation/investigations/win-probability-forecast-investigation.md` —
  gold-standard format for an investigation doc. Harvest's output
  artifacts target this shape.

**Sandbox decision (deferred to investigation):**

Investigation §4 question 2 asks whether harvest auto-commits code or
always emits a plan/PR for human review. Default posture for the
stub: **Mode A v1 emits docs only, no code commits.** Sandbox-mode
implications:

- If docs-only: no new sandbox mode required. Harvest writes to
  `documentation/investigations/` (already not blocked by either
  ADVISED_AUTO or EVO_AUTO sandboxes; falls under default human-PR).
- If auto-commit: needs new `HARVEST_AUTO=1` mode in
  `scripts/check_sandbox.py` with widened paths. Pre-commit hook
  table in master plan Phase 5 needs an extra row.

Investigation §6 will recommend.

## 3. Scope (skeleton)

**In scope (independent of investigation outcome):**

- `.claude/skills/harvest-engineer/SKILL.md` — Mode A skill prompt.
- Mode A intake spec: paper paste, web URL, or local PDF path
  (Read tool already handles PDF). Tools available: WebSearch,
  WebFetch, Read, Glob, Grep.
- Mode A output spec: a new investigation doc at
  `documentation/investigations/<topic-slug>-investigation.md`
  matching the win-probability investigation's section template
  (Problem statement, Existing context, Investigation scope, Key
  questions, Methodology, Findings stub, Success criteria,
  Constraints, Downstream decisions, Cross-references).
- Sandbox-mode decision (per §2 above, deferred to investigation).

**Out of scope (v1):**

- Mode B (investigate-from-suggestion) — ships in v1 only if SKILL
  prompt naturally accommodates it; otherwise v1.1.
- Cron-driven proactive harvest (deferred per master plan "What's NOT
  in this plan").
- Auto-PR creation (deferred until v1 docs-only proves out).

## 4. Build steps (skeleton)

| # | Step | Type | Depends |
|---|------|------|---------|
| Q.1 | Draft SKILL.md (Mode A only, docs-only output) | code | none |
| Q.2 | Sandbox-mode wiring (if investigation says auto-commit) | code | Q.1, investigation conclusion |
| Q.3 | Retroactive-feasibility test on 3 prior investigations | operator | Q.1 |
| Q.4 | Live test: paste 1 paper, harvest produces investigation doc | operator | Q.1 |
| Q.5 | (optional) Mode B wrapper | code | Q.4 |

Per-step Done-when criteria firm up post-investigation.

## 5. Tests

Skill-driven phases don't have unit tests in the conventional sense.
Validation is via the Q.3 retroactive-feasibility test (does harvest
reproduce a known-good past investigation given its inputs?) and Q.4
live test (does it produce something useful on a paper we haven't
investigated yet?).

## 6. Effort

~1-2 days post-investigation. Most of the time is in the SKILL.md
prompt iteration; sandbox wiring is ~0.5 day if needed.

## 7. Validation

- Q.3: Three retroactive cases (papers that inspired existing
  investigations like Phase A's LSTM+KL or the win-prob heuristic)
  produce harvest output that survives reviewer comparison with the
  actual investigation.
- Q.4: One fresh paper input produces an investigation doc that
  passes the same review-prompt skill the user runs on
  human-written investigation drafts.

## 8. Gate

Q.3 retroactive AND Q.4 live both pass.

## 9. Kill criterion

Investigation §6 concludes harvest is functionally indistinguishable
from `/improve-bot-advised` with a different intake. Recommendation:
extend advised's SKILL.md to accept paper paste rather than build a
separate skill. Phase Q collapses to a 1-step extension of the
advised SKILL.

## 10. Rollback

Delete `.claude/skills/harvest-engineer/`. If sandbox mode was added,
revert `scripts/check_sandbox.py` change and the master-plan Phase 5
table entry.

## 11. Cross-references

- Master plan Phase Q pointer: `documentation/plans/alpha4gate-master-plan.md`
- Investigation: `documentation/investigations/harvest-engineer-skill-scope-investigation.md`
- Sibling skill: `.claude/skills/improve-bot-advised/SKILL.md`
- Sibling skill: `.claude/skills/improve-bot-evolve/SKILL.md`
- Investigation gold standard: `documentation/investigations/win-probability-forecast-investigation.md`
