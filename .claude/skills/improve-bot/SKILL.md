---
name: improve-bot
description: Take an improvement suggestion for the Alpha4Gate SC2 bot, research and plan the changes, then execute them using /rwl-full for code review and verification.
user-invocable: true
argument: The improvement suggestion (e.g., "better worker defense against early rushes")
---

# Improve Bot

This skill takes an improvement suggestion for the Alpha4Gate bot, turns it into an
actionable plan, then executes it using the rwl-full developer-reviewer loop.

**Input:** The user provides a suggestion as the skill argument
(e.g., `/improve-bot better army composition against Zerg`).

---

## Phase 1: Understand the suggestion

1. Read the suggestion provided as the skill argument.
2. Read the relevant source files to understand the current behavior in that area. Key modules:
   - `Alpha4Gate/src/alpha4gate/bot.py` — main bot orchestration
   - `Alpha4Gate/src/alpha4gate/decision_engine.py` — strategy decisions
   - `Alpha4Gate/src/alpha4gate/macro_manager.py` — economy and production
   - `Alpha4Gate/src/alpha4gate/micro.py` — unit micro
   - `Alpha4Gate/src/alpha4gate/scouting.py` — scouting logic
   - `Alpha4Gate/src/alpha4gate/build_orders.py` — build order definitions
   - `Alpha4Gate/src/alpha4gate/claude_advisor.py` — Claude integration
3. Identify which modules are most relevant to the suggestion.
4. Summarize to the user:
   - What the bot currently does in this area (with file/line references)
   - What gaps or weaknesses exist relative to the suggestion
   - Any dependencies or interactions with other modules

---

## Phase 2: Plan the improvement

Ask the user targeted questions to nail down the plan. Adapt questions to the suggestion,
but always cover:

1. **Scope:** Should this be a minimal targeted fix or a broader capability?
2. **Behavior:** What should the bot do differently? Ask for specific scenarios or triggers.
3. **Testing:** How will we verify the improvement?
   - Unit tests for new logic
   - Game test command (e.g., `uv run python -m alpha4gate.runner --difficulty 5 --realtime`)
   - What does "success" look like in a game?
4. **Risk:** What existing behavior could this break? Which tests should still pass?
5. **Priority:** If the improvement spans multiple changes, what order?

Continue the conversation until you and the user agree on a clear plan. Do not rush — ask
follow-up questions if answers are ambiguous.

---

## Phase 3: Write the improvement plan and execute with rwl-full

Once alignment is reached, write a concrete improvement plan document:

**File:** `Alpha4Gate/documentation/improvements/<slugified-suggestion>.md`

The plan must include:

```markdown
# Improvement: <title>

## Summary
<1-2 sentence description of what changes and why>

## Current behavior
<What the bot does now, with file:line references>

## Proposed changes
<Ordered list of specific code changes>
- [ ] Step 1: ...
- [ ] Step 2: ...

## Files to modify
<List of files that will be touched, with what changes in each>

## Testing plan
- Unit tests: <what to add/modify>
- Game test: <command and what to look for>
- Regression: <existing tests that must still pass>

## Risks and mitigations
<What could go wrong and how to guard against it>
```

Show the plan to the user and confirm they approve it.

---

## Phase 4: Hand off for execution

Once the user approves the plan, run `/con_prep` to prepare the context transition.

The con_prep session must capture:
- The improvement plan location and contents
- The exact `/rwl-full` invocation(s) to run in the next session (see below)
- The verification steps to run after rwl-full passes

### Constructing the rwl-full invocation

The transition prompt must include a ready-to-paste `/rwl-full` command. Construct the
`--problem` argument from the improvement plan. It must include:
- The summary of the improvement
- The ordered list of proposed changes (copy from plan)
- The files to modify and what changes in each
- The testing plan (unit tests + regression)

Example invocation (code-only, no app startup):

```
/rwl-full --problem "Implement improvement: <title>.

Summary: <from plan>

Changes:
1. <step 1 from plan>
2. <step 2 from plan>

Files to modify:
- <file>: <what to change>

Testing:
- Add/modify tests: <details>
- Regression: all existing tests must pass
- Run: uv run pytest
"
```

**Notes for the transition prompt:**
- Use code-only mode (omit `--start-cmd`) — the bot has no web UI to screenshot.
- If the improvement is large (4+ steps), include multiple sequential rwl-full commands,
  one per logical step. Instruct the next session to run them in order.
- After rwl-full passes, the next session should:
  1. Run the full test suite: `uv run pytest`
  2. Run lint: `uv run ruff check .`
  3. Run typecheck: `uv run mypy src`
  4. Update the improvement plan doc — check off completed steps
  5. Summarize what changed and suggest a game test command
