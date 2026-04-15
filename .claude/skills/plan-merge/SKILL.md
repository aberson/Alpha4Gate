---
name: plan-merge
description: Merge two overlapping plan documents into a single coherent plan. Identifies shared goals at different fidelity, resolves sequencing tensions, picks a spine, deletes subsumed phases, and archives originals. Use when two plans target related work and need to be reconciled before execution (e.g., a capability plan plus an infrastructure plan that together deliver the same feature).
user-invocable: true
---

# Plan Merge

Merge two (or more) plan documents into one. The output is a single plan
where overlapping goals are resolved, phases are re-sequenced into a
coherent order, and the originals are archived — not deleted — so their
context remains inspectable.

This skill is **conversational**. It does not auto-merge. It surfaces the
decisions a human has to make (which plan is the spine, how to resolve
overlaps, how to order dependencies) and then writes the merged plan only
after those decisions are settled.

## When to use

- Two plans in `documentation/plans/` target related work and the team
  hasn't decided which one to execute.
- One plan describes a *capability* (e.g. "add feature X") and another
  describes the *infrastructure* that feature needs. They overlap at the
  seam and need reconciliation.
- A plan's phases reference work that is separately planned elsewhere.
- An old plan is partially superseded by a newer one and they need to
  be unified rather than left to rot in parallel.

Do NOT use this skill:

- To merge a plan with its own code — that's `/repo-update` or direct editing.
- To refine a single plan — that's `/plan-review`.
- To split a plan into smaller phases — write a new plan.

## Inputs

- Paths to two or more plan files (ask if not provided).
- Optionally, the target path for the merged plan. Default:
  `documentation/plans/<descriptive-name>-master-plan.md`.

## Process

### 1. Read both plans in full

Do not skim. The overlap that matters is rarely in the introduction — it
hides in phase-level details, schemas, contracts, and kill criteria.

### 2. Surface the overlap

For each plan, extract:

- **Vision / goal** — what problem does it solve?
- **Phase list with one-line summaries**
- **Data contracts** (DB schemas, file formats, APIs)
- **External dependencies** (branches, PRs, other plans, skills)
- **Kill criteria and rollbacks**

Then compare: where do the two plans touch the same concept?

Common overlap patterns:

- **Same goal, different fidelity.** Plan A describes a feature in
  abstract terms; Plan B describes the infrastructure that implements
  it concretely. (This is the most common case.)
- **Same data, different schema.** Both plans add storage for related
  information but chose different formats (SQLite vs JSONL vs JSON).
- **Implicit prerequisites.** Plan A's Phase N requires infrastructure
  that Plan B's Phase M builds.
- **Conflicting invariants.** Plan A declares "append-only X" and
  Plan B implicitly violates that invariant.

### 3. Check for self-inconsistency

A plan is self-inconsistent if its own later phases would break its
earlier phases. This is easy to miss when reading linearly.

Example (from the Alpha4Gate merge that produced this skill):

> AlphaStar Plan Phase C proposed "checkpoint-only" opponents for
> self-play. AlphaStar Plan Phase B then changed the feature spec, which
> would break the frozen Phase C checkpoints' input dimension. Phase C
> was therefore only viable if B, D, E, F never shipped — and shipping
> B–F was the whole point of the plan. The resolution was to delete
> Phase C and let the other plan's full-stack versioning subsume it.

Look for this pattern: **does plan A's later phase invalidate an
assumption its own earlier phase makes?**

### 4. Propose a spine

One plan usually reads as the *substrate* (infrastructure, foundational)
and the other as the *capability* (features built on top). The substrate
is the spine; the capability plan's phases slot in as work that happens
inside the substrate.

State the proposed spine explicitly, with justification. Be direct: "I
propose Plan B as the spine because it's the substrate; Plan A's phases
become per-substrate work."

Alternatives worth calling out:

- Capability-as-spine (when the infrastructure is lightweight and
  exists mainly to support one capability).
- Neither-as-spine (when both plans are equal partners — then the merged
  plan has two tracks that run in parallel and synchronize at named
  points).

### 5. Ask the hard questions before writing

Do NOT write the merged plan yet. Ask the user:

1. **Spine choice.** Confirm or redirect.
2. **Subsumption decisions.** For each overlap, propose one of:
   - Delete phase from plan X (subsumed by phase Y from plan Z)
   - Merge phases X and Y into a combined phase
   - Keep both, with explicit dependency between them
3. **Invariant resolutions.** If plans have conflicting invariants,
   which one wins, and what's the escape hatch for the losing side?
4. **Data format choices.** When both plans specify storage, which
   format survives? (JSONL vs SQLite vs JSON — size of dataset,
   expected access pattern, tooling.)
5. **Sequencing constraints.** Any phase from plan X that must happen
   before/after a specific phase from plan Y?
6. **Target file name.** Default `<topic>-master-plan.md`; confirm.
7. **Archive vs delete originals.** Default archive. Confirm.

Let the user answer all questions before proceeding.

### 6. Write the merged plan

Structure the output with these sections in this order:

1. **Source** — name the two archived originals, link to their paths.
2. **Vision** — single unified goal statement.
3. **Principles** — union of both plans' principles, deduplicated.
   Flag any principles that conflict and cite the resolution.
4. **Glossary** — union of both plans' terms, with per-version /
   per-substrate scoping noted where invariants changed.
5. **How to read this plan** — fields every phase has.
6. **Execution mode** — human-led vs autonomous, branching strategy.
7. **Track structure** — name the tracks (e.g., Validation,
   Infrastructure, Capability, Operational). Shows the reader how phases
   group without forcing a single linear order.
8. **Decision graph** — ASCII diagram of gate dependencies.
9. **Baseline** — current state as of merge date.
10. **Phases** — one section per phase, ordered by track then sequence.
    Each has the canonical fields (goal, prerequisites, scope, tests,
    effort, validation, gate, kill criterion, rollback).
11. **Compute target** (if relevant).
12. **Time budget** — combined table.
13. **What's NOT in this plan** — union of both plans' exclusions, PLUS
    the explicitly-subsumed phases from the merge ("checkpoint-only
    opponent pools — subsumed by ..."). This is important: future
    readers need to see what was considered and rejected.
14. **Tracking** — how phases become issues, which milestone.
15. **Plan history** — append-only log. First entry documents the
    merge itself: what was dropped, what was subsumed, what decisions
    were made.

### 7. Archive the originals

Move originals to `documentation/archived/` (or the project's archive
convention). Do NOT delete — the merged plan's "Source" section must
link to them, and the plan history entry needs to stay verifiable.

If a plan file is tracked in git, use `git mv`. If untracked (still in
working tree only), use plain `mv`. Check `git status` first.

### 8. Update any external references

- `MEMORY.md` entries referencing the old plans — update to the merged
  plan's path.
- GitHub issues / milestones — note in conversation that `/repo-sync`
  may be needed to re-cut issues against the merged plan.
- `CLAUDE.md` or `AGENTS.md` references — update if they name the old plans.

Do not silently rewrite memories or docs — list what needs updating and
confirm with the user before editing non-plan files.

## Output format

After the merge is complete, produce a short summary:

- **Merged plan written to:** `<path>`
- **Originals archived to:** `<path1>`, `<path2>`
- **Phases dropped:** `<phase>` — reason
- **Phases merged:** `<phase A> + <phase B>` → `<new phase>` — reason
- **Invariants changed:** `<old rule>` → `<new rule>` — reason
- **Open follow-ups:** references needing update (memories, issues, docs)

Keep this summary under 200 words. The plan document itself is the
deliverable; the summary is just the handoff.

## Example invocation

```
/plan-merge documentation/plans/alphastar-upgrade-plan.md documentation/plans/bot-versioning-selfplay-plan.md
```

The skill reads both, proposes a spine, asks clarifying questions about
overlaps and invariants, and only after answers writes the merged plan +
archives the originals.

## Anti-patterns

- **Silent-concatenation merge.** Copying both plans into one file with
  a "Plan A" and "Plan B" section header is not a merge — it's a
  staple. A real merge identifies overlap and makes a choice.
- **Auto-merge without questions.** The whole value is forcing the
  subsumption and sequencing decisions to be explicit. If you skip the
  questions, the output is a larger plan with the same inconsistencies
  as the inputs.
- **Deleting the originals.** Archive, always. The merged plan's
  history log refers to the originals, and readers may need to see the
  un-merged version to understand a decision.
- **Ignoring self-inconsistency.** If Plan A's Phase N breaks its own
  Phase K, note it in the merge rationale. Don't just replicate the
  contradiction.
- **Merging without reading fully.** Overlap hides in phase-level
  schemas and kill criteria, not intros. Skim and you miss the real
  decisions.

## Relation to other skills

- `/plan-review` — review a single plan for gaps. Complementary; run
  before or after `/plan-merge`.
- `/plan-init` — start a new plan from scratch. Use when there's
  nothing to merge yet.
- `/plan-feature` — scope a feature plan inside an existing project.
  Use when one of the "plans" you'd merge is actually just a feature
  inside a larger plan.
- `/repo-sync` — run after the merged plan is written to re-cut GitHub
  issues against the new phase structure.
- `/session-check` — run on the merged plan to confirm a fresh model
  can act on it without prior context.
