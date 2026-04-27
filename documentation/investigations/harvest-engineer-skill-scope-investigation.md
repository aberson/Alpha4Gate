# harvest-engineer skill scope investigation

**Status:** OPEN — investigation skeleton, no findings yet.

**Gates:** Phase Q (harvest-engineer skill build).

**Branch:** to be cut from master at investigation start.

**Date opened:** 2026-04-26.

## 1. Problem statement

Today, improvements to the bot enter the system through three channels:

1. **`/improve-bot-advised`** — Claude analyzes bot logs after games,
   proposes improvements, applies them in-loop. Sandbox-restricted to
   `bots/current/**`. Mechanism: heuristic + Claude reasoning.
2. **`/improve-bot-evolve`** — Claude generates a 10-imp pool from
   mirror-seed games, evolve loop A/B-tests them. Sandbox: `bots/**`.
   Mechanism: discrete A/B selection.
3. **Human PRs** — the user (or future contributors) write code
   directly. Mechanism: deliberate engineering.

There is **no channel for ideas from outside the codebase** — RL papers,
SC2 community wisdom, AlphaStar publications, MoE research. The user
proposed a new skill, `harvest-engineer`, that closes this gap. The
plan-shape conversation narrowed it to **reactive** (not cron-driven)
with two operating modes:

- **Mode A — paste-a-paper.** User pastes (or links) a paper. Skill
  reads it, scopes whether it applies to Alpha4Gate, drafts an
  experiment, and either implements or hands the experiment design to
  `/improve-bot-advised`.
- **Mode B — investigate-from-suggestion.** User says "look into X"
  (e.g., "look into MoE for SC2 micro"). Skill does its own literature
  search, finds an applicable paper or technique, scopes the
  experiment, and either implements or hands off.

This investigation answers: how does `harvest-engineer` differ from
`/improve-bot-advised` in mechanism (not just in scope), what's the
right boundary, and what's the minimum-viable v1 that ships in 1-2
days?

## 2. Existing context

**Plan refs.**

- Master plan Track 9 (proposed): Phase Q. Gated on this investigation.
- `/improve-bot-advised` (shipped Phase 5): the closest cousin. Reactive,
  invokes Claude, applies bounded code edits via SKILL.md sandbox.
- `/improve-bot-evolve` (shipped Phase 9): batch generation of pooled
  imps. Different intake (mirror-seed games, not external knowledge).

**Code refs.**

- `.claude/skills/improve-bot-advised/SKILL.md` — template for
  Claude-driven improvement skill. Sandbox declarations, run-start
  banner, `[advised-auto]` commit tag, `ADVISED_AUTO=1` env var,
  promotion-gate wiring.
- `.claude/skills/improve-bot-evolve/SKILL.md` — template for the
  `[evo-auto]` evolution variant.
- `scripts/check_sandbox.py` — pre-commit hook that enforces sandbox
  modes. Adding `harvest-engineer` requires a new mode (`HARVEST_AUTO`?)
  or reuse of `ADVISED_AUTO`.
- `scripts/improve_bot_advised.py` (or analogous) — entry point for
  the advised loop. Pattern to mirror.

**Memory refs.**

- `feedback_verify_primary_source_in_writing.md` — directly relevant.
  harvest-engineer's whole job is to ground recommendations in primary
  sources (the paper). Skill must be designed to *quote inline*, not
  recall.
- `feedback_llm_softening_bias.md` — also relevant. harvest-engineer
  will be tempted to "soften" experiment kill criteria when it
  scopes its own work. Skill prompt must counter this.
- `project_improve_bot_advised.md` — the existing improvement loop;
  validates the skill template pattern works.

**Boundary with `/improve-bot-advised`.**

The user said "similar to improve-bot-advised in them but different in
mechanism." The investigation must articulate the difference precisely.
Candidates:

- **Intake.** Advised reads game logs; harvest reads papers / web.
- **Time horizon.** Advised reasons about local fixes (one game's
  behavior); harvest reasons about architectural changes (whole
  paradigms).
- **Risk profile.** Advised is sandbox-restricted to bug-fix-shaped
  edits. Harvest may propose things outside the sandbox (new
  dependencies, new modules, new training regimes) — closer to a
  "human PR proposal" than a self-applying patch.
- **Output shape.** Advised commits code. Harvest may produce a
  *plan document + investigation* rather than code, deferring to
  `/build-step` or `/build-phase` for implementation.

## 3. Investigation scope

**In scope.**

- Define Mode A and Mode B operating modes precisely. Inputs, tools
  used, output artifacts, sandbox posture.
- Articulate the mechanism difference vs `/improve-bot-advised` in 2-3
  bullet points that survive review.
- Decide on sandbox mode: new `HARVEST_AUTO=1` with widened paths
  (e.g., can write to `documentation/investigations/`), reuse
  `ADVISED_AUTO=1`, or default to "no auto-commit, human-PR only."
- Decide whether harvest-engineer ever auto-implements or always
  hands off to a human / `/improve-bot-advised`.
- Decide on output artifact format. Investigation doc (this file's
  cousin)? Plan stub? Build-step script invocation?
- Identify which existing investigations would have been candidates
  for harvest-engineer to surface autonomously (retroactive feasibility
  test).

**Out of scope.**

- Implementing the skill. Phase Q does the build.
- Deciding which papers harvest-engineer should read first. That's a
  v1 backlog, not a scope question.
- Cron-driven proactive mode. Explicitly deferred per user direction.

## 4. Key questions

1. **What's the precise mechanism difference vs `/improve-bot-advised`?**
   The user noted "similar in them but different in mechanism." The
   most defensible read: advised processes *bot behavior* (logs),
   harvest processes *external knowledge* (papers, articles). But
   that's intake; the *processing* difference matters for skill
   design. Likely: harvest reasons at design-doc resolution; advised
   reasons at code-edit resolution.
2. **Should harvest-engineer auto-commit code?** Two postures: "yes,
   under HARVEST_AUTO sandbox" vs "no, always emit a plan/PR for
   human review." The latter is safer and probably right for v1
   given the higher abstraction level of harvest-engineer's work.
3. **What's the output artifact?** Investigation doc + scoped
   build-step input is one option. New-plan stub is another. Direct
   PR is a third. v1 should pick one.
4. **What tools does the skill need?** Web search, web fetch (already
   available). PDF reading (Read tool supports PDF). arXiv API
   wrapper? Reading existing investigation docs (Glob + Read).
   Analyzing past evolve / advised runs for relevance scoring (Grep).
5. **How does Mode A differ from Mode B in skill prompt?** Mode A is
   "summarize this paper, scope its applicability." Mode B is "go
   find a paper, then do Mode A." Mode B is a strict superset; v1
   could ship as Mode A only with Mode B as a thin wrapper.
6. **What's the kill criterion for a harvest-engineer recommendation?**
   Same as any phase: an experiment design that fails to materialize
   ROI in the time-box becomes a "killed" investigation note. Skill
   must produce kill criteria with the recommendation.
7. **How does harvest-engineer interact with the master plan?** A
   recommendation either (a) slots into an existing phase as new
   scope, (b) creates a new phase (likely a Track 9 letter), or (c)
   spawns its own investigation that defers to a future phase. v1
   should support all three with explicit decision trees in the
   skill prompt.
8. **What stops harvest-engineer from drowning the user?** A poorly-
   scoped harvest run could produce 10 "interesting" papers per
   week with no prioritization. v1 must produce a single
   recommendation per run with a clear "reject everything else"
   posture, not a literature survey.
9. **How does it differ from this very investigation, written by a
   human-Claude conversation?** Honest answer: it doesn't, structurally.
   harvest-engineer is the *skill encoding* of "write an investigation
   doc from a paper" so the user doesn't have to drive a conversation
   each time. The win is throughput, not capability.

## 5. Methodology

**Reading list:**

1. `.claude/skills/improve-bot-advised/SKILL.md` — read end-to-end
   for skill-template patterns.
2. `.claude/skills/improve-bot-evolve/SKILL.md` — second pattern.
3. `scripts/check_sandbox.py` — sandbox mode mechanics.
4. `documentation/investigations/win-probability-forecast-investigation.md`
   — gold-standard format for an investigation. harvest-engineer
   should produce docs of this shape.

**Retroactive feasibility test.**

Pick 3 past investigations or improvements driven by the user. Ask:
"Could harvest-engineer have produced this autonomously from a paper
or web search?" Honest yes/no/partial per case. Examples to consider:

- The win-probability investigation — would harvest have surfaced
  Option (c) heuristic from a paper, or did it require staring at
  our specific data? (Probably the latter — investigation depended on
  soak-artifact data analysis.)
- The Hydra investigation we're about to file — would harvest have
  produced this from "the user said hierarchical PPO", or did the
  conversation framing matter? (Probably the latter — but a Mode A
  paste-a-paper variant clearly works for "user found a Feudal
  Networks paper, scope it.")
- Phase A (LSTM + KL-to-rules) — was this from a paper or from
  internal reasoning? Likely the latter, derived from AlphaStar's
  approach.

**Artifacts produced:**

- This document, with §6 "Findings" appended.
- A 1-page SKILL.md draft for harvest-engineer (Mode A only for v1).
- A sandbox-mode decision: new mode or shared.
- A retroactive-feasibility table (3 cases, yes/no/partial).

## 6. Findings

(To be filled by the investigation. Skeleton only.)

## 7. Success criteria

The investigation is **done** when:

- All 9 key questions in §4 have answers.
- A v1 SKILL.md is drafted (Mode A only).
- A clear "what makes this skill different from /improve-bot-advised"
  one-paragraph statement exists, defensible to a code-reviewer who
  has not seen this conversation.
- Phase Q has a scope statement covering the v1 build.

## 8. Constraints

- **Time-box: 0.5-1 day.** Mostly skill-template work plus the
  retroactive feasibility test.
- **No skill code shipped.** Phase Q builds.
- **Honest about overlap.** If harvest-engineer turns out to be 90%
  the same as /improve-bot-advised with a different intake, say so
  and recommend extending advised instead of building a separate
  skill.

## 9. Downstream decisions gated on this

- **Phase Q scope.** SKILL.md content, sandbox mode, output artifact
  shape.
- **Sandbox hook extension.** If a new mode is recommended,
  `scripts/check_sandbox.py` and the sandbox-modes table in master
  plan Phase 5 must extend (same pattern as `EVO_AUTO=1`).
- **Phase O / Phase P interaction.** Both Hydra and distillation
  investigations are exactly the kind of work harvest-engineer
  could spawn from a paper paste. If Phase Q ships before Phases
  O/P, those investigations may be re-run with harvest assistance.

## Appendix — Cross-references

- Master plan: `documentation/plans/alpha4gate-master-plan.md`
- /improve-bot-advised SKILL: `.claude/skills/improve-bot-advised/SKILL.md`
- /improve-bot-evolve SKILL: `.claude/skills/improve-bot-evolve/SKILL.md`
- Sandbox hook: `scripts/check_sandbox.py`
- Investigation gold standard: `documentation/investigations/win-probability-forecast-investigation.md`
