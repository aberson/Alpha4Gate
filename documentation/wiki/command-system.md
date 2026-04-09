# Command System

How human and AI input becomes bot actions.

> **At a glance:** Five-stage pipeline: text → parse (regex) → interpret (Claude Haiku
> fallback) → queue (priority, TTL, max 10) → execute (SC2 API calls). Three modes
> control who can issue commands. Tech recipes auto-expand high-level requests into
> prerequisite build chains. All modules except executor.py are domain-agnostic.

## Purpose & Design

The command system lets humans and Claude issue strategic instructions mid-game. It's
designed so commands from any source (typed text, Claude advice, API call) flow through
the same pipeline.

### Three command modes

| Mode | Who executes | AI lockout |
|------|-------------|------------|
| AI_ASSISTED | AI + human commands | No lockout |
| HUMAN_ONLY | Human commands only (AI filtered) | N/A |
| HYBRID_CMD | Both, but human commands trigger AI lockout (default 5s) | Yes |

### Pipeline

```
Input text
  │
  ├─ StructuredParser.parse() ── regex: action [target] [at location]
  │     │ success          │ failure
  │     ▼                  ▼
  │  CommandPrimitive    CommandInterpreter.interpret()
  │     │                  │ Claude Haiku, 5s timeout, max 128 tokens
  │     │                  │ Returns parsed primitives or None
  │     └──────┬───────────┘
  │            ▼
  │     CommandQueue.push()
  │       max_depth=10, evicts lowest-priority (AI before human at equal)
  │            │
  │     CommandQueue.drain(game_time)
  │       filters expired (TTL default 60s), sorts by priority (highest first)
  │            │
  │     CommandExecutor.execute(cmd)
  │       ├─ BUILD → train unit or build structure
  │       ├─ TECH → expand_tech() → recursive prerequisite builds
  │       ├─ EXPAND → build nexus at next expansion
  │       ├─ ATTACK/DEFEND → set decision engine override (120s)
  │       ├─ SCOUT → force scout to target location
  │       ├─ UPGRADE → research at forge/cybernetics
  │       └─ RALLY → update army staging point
  │            │
  └─── ExecutionResult {success, message, primitives_executed}
```

### Tech recipe expansion

High-level commands like "tech voidrays" auto-expand into prerequisite builds:

```
"tech voidrays" → expand_tech("voidrays")
  → [BUILD stargate (priority 6), BUILD voidray (priority 5)]

"tech colossi" → expand_tech("colossi")
  → [BUILD robotics_facility (priority 6), BUILD robotics_bay (priority 6), BUILD colossus (priority 5)]
```

11 tech recipes defined covering: voidrays, colossi, high_templar, dark_templar,
blink, charge, phoenix, carrier, tempest, disruptor, archon.

---

## Key Interfaces

**Actions:** BUILD, EXPAND, DEFEND, ATTACK, SCOUT, TECH, UPGRADE, RALLY

**CommandPrimitive:** action, target, location, priority (1-10, default 5), source
(AI/HUMAN), id (UUID), timestamp, ttl (default 60s)

**CommandQueue:** push(cmd), drain(game_time), clear(source), clear_conflicting(action)

**Regex pattern:** `^(build|expand|defend|attack|scout|tech|upgrade|rally)\s+(target)?\s+(at|to location)?$`

**Location resolution:** main, natural, third, fourth, enemy_main, enemy_natural,
enemy_third. Falls back to action-dependent defaults.

---

## Implementation Notes

| File | Purpose | SC2-specific? |
|------|---------|---------------|
| `commands/primitives.py` | Enums + dataclasses | No |
| `commands/parser.py` | Regex parser | No |
| `commands/interpreter.py` | Claude Haiku NLP fallback | No (prompt mentions SC2) |
| `commands/queue.py` | Priority queue with TTL | No |
| `commands/recipes.py` | Tech prerequisite chains | No (content is SC2) |
| `commands/executor.py` | SC2 API calls | **Yes** — UnitTypeId maps |

Executor maps: `_UNIT_MAP` (15 units), `_STRUCTURE_MAP` (15 structures),
`_PRODUCTION_MAP` (unit → building), `_UPGRADE_MAP` (5 upgrades).
