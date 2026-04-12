---
name: improve-bot-triage
description: Triage the findings from the most recent /improve-bot run log into a pickable list, classify each finding by action-type, and emit a ready-to-run /improve-bot invocation for the picked item. Use between /improve-bot runs to decide what to target next without re-reading the full report.
user-invocable: true
argument: Optional flags — `--run <RUN_TS>` to target a specific run log instead of the most recent, or `--file <path>` for an arbitrary markdown file. Without args, reads the most recent `documentation/soak-test-runs/improve-*.md`.
---

# /improve-bot-triage

Short skill that reads the most recent `/improve-bot` run log, extracts findings, classifies them, and lets the user pick one to target with the next `/improve-bot` invocation. Option A from the 2026-04-11 improve-bot design discussion (meta-skill decision logged in #88). v1 reads one file and prints the recommended next invocation — it does NOT auto-invoke anything.

## Scope (v1)

- Read exactly ONE run log (most recent or user-specified).
- Extract findings from well-known `## Primary finding`, `## Secondary finding`, `## Tertiary finding`, `## Skill feedback`, `## Suggested next actions` sections.
- Classify each finding by action type (heuristic).
- Print a numbered pickable list.
- On user pick, emit the `/improve-bot <flavor> <suggestion>` command they should run next (or file a GH issue instead if the finding is research-only).

## Out of scope (v1)

- Multi-run history / cross-run ledger (deferred; consider `/improve-bot-ledger` if v1 proves useful).
- Auto-priority ranking — user picks, no sorting.
- Auto-invocation — always just print the command.
- Parsing GH issue threads or plan docs.
- Multi-pick / batch execution — that's `/improve-bot-sequence`, separate skill.

---

## Phase 1: Locate the source log

Default:
```bash
LOG=$(ls -t documentation/soak-test-runs/improve-*.md 2>/dev/null | head -1)
```

With `--run <RUN_TS>`:
```bash
LOG="documentation/soak-test-runs/improve-${RUN_TS}.md"
```

With `--file <path>`:
```bash
LOG="<path>"
```

If `$LOG` does not exist, STOP and tell the user which directory was searched and what patterns were tried. Do not guess.

Print the resolved path to the user at the top of the output so they know which log is being triaged.

---

## Phase 2: Extract findings

Read `$LOG` top to bottom. A finding is any `## ` heading whose text starts with one of these prefixes (case-insensitive):

- `Primary finding`
- `Secondary finding` (or `Secondary findings`)
- `Tertiary finding`
- `Skill feedback candidate` (or `Skill feedback`)
- `Suggested next actions`

For each finding section:
- **title** = the heading text after the prefix (strip leading ` — ` if present)
- **summary** = the first non-empty paragraph after the heading (stop at blank line or next `##` / `###`)
- **body** = everything between this heading and the next `## ` heading, captured verbatim for downstream use

Also grab any `## Run summary` or top-of-file metadata (T0, flavor, stop reason, error count) so the output header can show the run context.

If fewer than 1 finding is extracted, STOP and report "no findings extracted from $LOG — is the file using the expected headings?" Do not fall back to fuzzy matching.

---

## Phase 3: Classify each finding

Heuristic rules applied to the finding's body text (first-match wins, top to bottom):

1. Body contains any of `research`, `investigation`, `root cause`, `hypothesis`, `candidate root causes`, `experiment` → **`[research]`** — *not /improve-bot material, file as a GH issue with `research` label*
2. Body contains any of `validated`, `HOLDING`, `POSITIVE`, `end-to-end`, `validated end-to-end`, `no action needed` → **`[observation-only]`** — *already confirmed, no action*
3. Body contains any of `wall clock`, `phase 0`, `phase 1`, `phase 2`, `phase 3`, `phase 4`, `skill`, `improve-bot itself`, `meta-skill` → **`[skill-fix]`** — */improve-bot can't fix itself, file as manual skill edit*
4. Body contains any of `producer`, `UI component`, `dashboard`, `tab`, `endpoint`, `API path`, `broken`, `regression`, `fix`, a concrete file path ending in `.py`, `.ts`, `.tsx` → **`[build-step]`** — *concrete /improve-bot --self-improve-code target*
5. Otherwise → **`[ambiguous]`** — *needs human clarification before targeting*

Store the classification alongside each finding.

---

## Phase 4: Present the pickable list

Emit a table to the user:

```markdown
## Triage — <RUN_TS> (<flavor>, <stop reason>)

Source: `<LOG path>`
Run metadata: T0=<...>, duration=<...>, errors=<...>, games=<...>

| # | Type | Title | One-line hook |
|---|---|---|---|
| 1 | [build-step] | Schema mismatch on Decisions tab | Producer writes claude_advisor fields, UI expects state-machine fields; 10-line shim fix |
| 2 | [research] | PPO declining win rate | Cycle win rates 0.5 → 0.167; reward shaping hypothesis — offline investigation, not build-step |
| 3 | [observation-only] | Phase 4.7 Step 3 terminal sentinel holding | 0 failed_games across 50+ training games (vs soak-4b 6/12) |
| 4 | [skill-fix] | Wall-clock leak during interactive phases | Daemon ran unsupervised for 4h+ while Phase 1/2 talked; add Phase 3 leak check |
| ... | ... | ... | ... |

Which finding do you want to target? (1-N, or 'none')
```

Then wait for the user to pick a number.

---

## Phase 5: On pick

Based on the picked finding's classification, produce ONE of the following outputs. Do not execute commands — just print them and explain.

### `[build-step]` → emit `/improve-bot` invocation

```
Recommended next invocation:

    /improve-bot --self-improve-code "<title>: <brief scope from summary>"

Notes:
- This run would target a concrete code fix.
- Estimated scope: <small | medium | large> based on finding body.
- Check that any referenced GH issue is still open before launching.
- Suggested wall clock: 1-2h.
```

### `[research]` → offer to file / comment a GH issue

```
This is research, not /improve-bot material.

Proposed action: file a new issue (or comment on an existing one) with the
finding body and a `research` label. Offer to do this via `gh issue create`
with the following title and body:

    Title: [research] <finding title>
    Labels: research, <axis>
    Body: <the finding body verbatim + a link back to $LOG>

Do NOT try to launch /improve-bot against a research item — the skill has no
offline experimentation facility and /build-step is the wrong tool.
```

### `[observation-only]` → no action

```
This finding records a POSITIVE validation. No further action needed.
If you want to document the validation for future reference, consider:
  - Adding a note to the related plan doc or README.
  - Closing any related GH issue with a `completed` reason and a link
    to $LOG as evidence.
```

### `[skill-fix]` → manual skill edit + memory candidate

```
/improve-bot cannot fix itself. The recommended flow is:
  1. Edit the skill file directly in .claude/skills/<skill>/SKILL.md
  2. Commit + push the fix as its own commit.
  3. Add a feedback memory entry in memory/ so future sessions pick up
     the lesson.

If you want, I can draft the specific SKILL.md edits and the feedback memory
content for this finding right now.
```

### `[ambiguous]` → clarify

```
This finding isn't clearly classifiable. Before acting:
  - Ask the user one sharp question that would disambiguate type.
  - Do NOT guess. Unknown targets waste run budgets.
```

---

## Phase 6: Exit

After printing the recommended invocation, STOP. Do not auto-run anything. The user makes the final call.

If the user responds with "run it", THEN they can invoke `/improve-bot` themselves with the printed command. Future v2 of this skill may add an `--execute` flag, but v1 does not.

---

## Safety rails

- Never rewrite the source run log. Read-only.
- Never open GH issues silently — always show the draft and ask first.
- Never classify more than one finding as `[build-step]` for the same pick prompt — the skill is one-at-a-time by design.
- If the run log is malformed or extraction finds zero findings, STOP and report clearly. Do not fall back to reading different files or fuzzing the headings.
- This skill does not touch `data/`, does not start/stop the backend, does not touch git.
