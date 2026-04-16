# Claude Advisor

Async AI strategic advice **during gameplay** (inside THE TASK, not the outer learning loop).

> **At a glance:** Claude runs as an async subprocess (fire-and-forget), rate-limited to
> 1 request per 30 game-seconds. Advice includes a text suggestion + optional structured
> commands that flow into the command queue. Non-blocking — the bot never waits for a
> response. In HYBRID mode, human commands trigger an AI lockout period.

> **Three different roles for Claude in Alpha4Gate — don't conflate them:**
>
> 1. **In-game advisor** (this page) — async subprocess during a live SC2 game, gives the bot strategic nudges mid-match.
> 2. **Outer-loop strategist** — the `/improve-bot-advised` skill uses Claude to diagnose played games and write code/config fixes (THINK + FIX phases). See [improve-bot-advised-architecture.md](improve-bot-advised-architecture.md).
> 3. **Command interpreter** — Claude Haiku parses free-text commands the regex parser can't handle. See [command-system.md](command-system.md).
>
> This page covers only role #1. A thread-safe variant of this advisor (`learning/advisor_bridge.py`) is used during RL training — see [training-pipeline.md](training-pipeline.md).

## Purpose & Design

The Claude advisor provides strategic suggestions mid-game by analyzing the current game
state. It's designed to be completely non-blocking — the bot fires a request, continues
playing, and checks for the response on subsequent steps.

### Request cycle

```
Every step in on_step():
  1. collect_response() — check if pending async task is done
     If done: parse response, extract commands → command queue
  2. If rate_limiter.can_call(game_time):
     Build prompt from current state (snapshot, composition, decisions)
     request_advice(prompt, game_time) — fire async subprocess
     (returns immediately)
```

### Rate limiting

`RateLimiter(interval_game_seconds=30.0)` — ensures at most 1 Claude call per 30
game-seconds. Configurable via `/api/commands/settings` (claude_interval field).

### AI lockout (HYBRID mode)

When a human issues a command in HYBRID mode:
1. `set_ai_lockout()` → `_ai_lockout_until = game_time + lockout_duration`
2. Claude advice requests blocked until lockout expires
3. Default lockout: 5 seconds (configurable via settings)

This prevents AI from immediately overriding human tactical decisions.

---

## Key Interfaces

**ClaudeAdvisor:**
- `request_advice(prompt, game_time) → bool` — fires async, returns True if sent
- `collect_response() → AdvisorResponse | None` — non-blocking check
- `enabled`, `has_pending`, `last_response` properties

**AdvisorResponse:** suggestion (str), urgency (low/medium/high), reasoning (str),
commands (list[CommandPrimitive])

**Prompt template** includes: game time, strategic state, resources, supply, army
composition, enemy composition, recent decisions, build order progress.

---

## Implementation Notes

The async call spawns `claude -p <prompt> --model <model> --output-format text
--no-session-persistence` as a subprocess. Response is parsed as JSON (with markdown
fence handling). Commands are validated against the same `CommandAction` set used by the
parser.

Default model: `sonnet`. The subprocess is killed on exception.

**File:** `src/alpha4gate/claude_advisor.py`
