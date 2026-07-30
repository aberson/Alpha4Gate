# Seed: add a confirm-gated `run-improvement` launcher (deferred from 2026-07-29 UAT)

## What to build

Add a dev-observatory launcher verb `run-improvement` for Alpha4Gate that kicks off
the **`improve-bot-advised`** skill (the advised strategic improvement loop), as the
sibling of the already-shipped `run-evolution` verb (which runs `improve-bot-evolve`
via `scripts/evolve.py`).

Deferred deliberately during the 2026-07-29 launcher UAT — `run-evolution` shipped;
`run-improvement` was held because it is messier (see "The catch").

## Context (from the 2026-07-29 research)

- `run-selfplay` / `run-match` are **show-matches only** (no evolution). The real
  evolution lives in two Claude Code **skills**:
  - `improve-bot-evolve` → has a real headless runner `scripts/evolve.py`
    (`uv run python scripts/evolve.py --hours 4`). That's why `run-evolution` was
    easy to buttonize.
  - `improve-bot-advised` → **NO standalone runner script.** Claude itself drives the
    loop (delegating dev-type improvements to the `/improve-bot` sub-skill). It runs
    N games → reviews replays vs the guiding-principles doc → picks ONE improvement
    (training / dev / soak) → applies + validates win-rate + cross-version Elo gate →
    commits. Default `--hours 4`, fire-and-forget (zero-input contract), destructive
    (auto-commits `[advised-auto]` to master, optionally edits `bots/current/**` under
    `--self-improve-code`).
- The dev-observatory launcher runs any shell command in a detached PowerShell window
  (`launch.build_argv` → `powershell -NoExit -Command <cmd>`), and generates a VS Code
  `tasks.json` entry per verb. A `{ ..., confirm = true }` launch mode makes the served
  button prompt (the VS Code task always runs visible + cancellable).

## The catch (why it was deferred)

`improve-bot-advised` has no runner script, so a `run-improvement` verb would have to
launch a **headless Claude session**: `claude -p "/improve-bot-advised --hours 4"`.
That is heavier + riskier than `run-evolution` (`scripts/evolve.py`):
- It needs Claude auth in the launched shell (prefer `CLAUDE_CODE_OAUTH_TOKEN`; see
  the workspace memory `feedback_prefer_oauth_for_claude_code`).
- It is a full autonomous agentic session that edits code + auto-commits to master.
- Whether `claude -p` reliably runs a project skill headless for 4h wants a smoke test
  first.

## Recommended approach

1. **Preferred:** first give `improve-bot-advised` a thin headless runner
   `scripts/improve.py` (mirroring `scripts/evolve.py`) that shells `claude -p` with the
   skill + flags and owns the exit/monitoring — then the verb command is just
   `uv run python scripts/improve.py --hours 4` (no raw `claude -p` in the registry).
2. **Or, minimal:** point the verb straight at
   `claude -p "/improve-bot-advised --hours 4"` and rely on ambient auth.
3. Register it exactly like `run-evolution` (confirm-gated), in
   `dev/.claude/observatory/registry.toml` under Alpha4Gate:

   ```toml
   [project.launch.run-improvement]
   verb = "run-improvement"
   command = "uv run python scripts/improve.py --hours 4"   # or the claude -p form
   confirm = true
   ```

4. `observatory sync` to regenerate the VS Code task; launch it from the **VS Code
   task** (visible + cancellable), not the fire-and-forget web button.

## Guardrails

- `improve-bot-advised` and `improve-bot-evolve` MUST NOT run concurrently (both mutate
  `bots/current/current.txt`; evolve pre-flight refuses if an advised run is active).
- Keep it confirm-gated; it auto-commits to master.

## Paste-ready kickoff prompt

> Add a confirm-gated `run-improvement` dev-observatory launcher for Alpha4Gate that
> starts the `improve-bot-advised` loop, as the sibling of the existing `run-evolution`
> verb. First decide: write a thin `scripts/improve.py` headless runner (mirrors
> `scripts/evolve.py`, shells `claude -p "/improve-bot-advised"` and owns exit/monitor),
> or point the verb straight at `claude -p "/improve-bot-advised --hours 4"`. Smoke-test
> that the headless invocation actually runs the skill for a short `--hours` before
> wiring the button. Register it in `dev/.claude/observatory/registry.toml` under
> Alpha4Gate with `confirm = true`, run `observatory sync`, and confirm the VS Code task
> appears. Keep the concurrency guard (advised + evolve must not run together) in mind.
