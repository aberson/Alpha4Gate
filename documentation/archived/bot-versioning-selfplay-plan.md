# Alpha4Gate — Bot Versioning & Self-Play Plan (Big-Box)

## Vision

Let `/improve-bot-advised --self-improve-code` compound autonomously by
snapshotting each passing iteration as a full-stack, immutable bot
version, and validating new versions against prior ones via subprocess
self-play.

A "bot version" is a complete copy of the Alpha4Gate stack — bot code,
learning pipeline, API, reward engine, PPO checkpoint — not just the
strategic play surface. The skill can edit any of it inside its
sandbox. The only thing the skill cannot touch is the minimal
orchestrator that brokers self-play between versions.

## Why big-box

Smaller ring-fence was a false economy: whenever the skill wanted to
improve observation features, tweak the trainer, or change PPO
architecture, it would bottleneck on a human PR. Big-box means the
skill can explore the full design space and only asks for human
review when it wants to change the orchestrator contract itself
(rare) or upgrade shared Python deps (rare).

## Principles

- **Versions are full stacks.** Each `bots/vN/` is a self-contained
  Python package: runner, api, learning, bot, commands, reward engine,
  checkpoint, data dir. A version is deleted by `rm -rf bots/vN/`.
- **Orchestrator is the only frozen substrate.** Thin subprocess
  manager; defines the bot-spawn and result-reporting contracts.
  Changes require human PR.
- **Shared deps are frozen too.** Repo-level `pyproject.toml` is pinned.
  The skill cannot autonomously bump `torch` or add a new dependency —
  that needs human PR. Everything downstream of the dep set is fair
  game.
- **Self-play is the truth signal.** Elo vs prior versions is the
  promotion gate. SC2-AI win rate is a secondary indicator.
- **Subprocess-per-version is the only self-play mode.** No in-process
  option. Clean isolation beats the small performance cost.

## Scope decisions

1. **PPO checkpoint** versioned inside its bot version — already decided.
2. **Subprocess self-play** is the only self-play path — no in-process
   fallback.
3. **The skill can propose dep changes / orchestrator changes** but
   cannot auto-merge them. These are the two human-PR escape hatches.
4. **Tests stay shared** under `tests/`; always test `bots/current/`.
   Old versions are validated by recorded Elo.
5. **Each version has its own `data/` dir** (`bots/vN/data/`). Shared
   `data/` only holds cross-version state (ladder, selfplay results).
6. **Dashboard talks to `bots/current/` only.** Versions drift on API
   schemas; dashboard compat is only maintained for current.

## The frozen substrate

**`src/orchestrator/`** (the only code the skill cannot touch):

- `orchestrator.py` — spawns subprocess per version, manages SC2
  lifecycle, collects results
- `selfplay.py` — batch self-play between two versions
- `ladder.py` — Elo + ladder management
- `registry.py` — `list_versions()`, `get_version_dir()`, metadata
- `contracts.py` — frozen schemas for bot-spawn args + result-reporting
  JSON
- `snapshot.py` — tool that forks `bots/current/` → `bots/vN/`

**Root-level frozen files:**

- `pyproject.toml`, `uv.lock` — dep set
- `scripts/dev-serve.{ps1,sh}`, `scripts/start-dev.sh` — dev startup
  (these talk to `bots/current/` only)
- `frontend/` — dashboard (compatible with `bots/current/` API only)
- `tests/` — shared test suite

**The bot-spawn contract** (every `bots/vN/` must honor):

```
python -m bots.vN --role {p1|p2|solo} \
                  --map Simple64 \
                  --sc2-connect <protocol-arg> \
                  --result-out bots/vN/data/selfplay/<match-id>.json \
                  --seed <int>
```

**The result-reporting contract:**

```json
{
  "version": "vN",
  "match_id": "...",
  "outcome": "win|loss|draw|crash",
  "duration_s": 612,
  "error": null
}
```

Both contracts versioned under `orchestrator/contracts.py`. Breaking
them is an orchestrator PR.

## Versioned surface (inside every `bots/vN/`)

Everything currently under `src/alpha4gate/`:
- `bot.py`, `decision_engine.py`, `runner.py`, `api.py`
- `build_orders.py`, `build_backlog.py`, `macro_manager.py`
- `army_coherence.py`, `fortification.py`, `scouting.py`, `micro.py`
- `commands/`, `learning/`, `observer.py`, `replay_parser.py`
- `claude_advisor.py`, `config.py`, `console.py`, `web_socket.py`
- `batch_runner.py`, `connection.py`, `logger.py`, `audit_log.py`
- `error_log.py`, `process_registry.py`, `macro_manager.py`
- `reward_rules.json`, `hyperparams.json`, `checkpoint.zip`
- `data/` (per-version state)
- `VERSION`, `manifest.json` (parent, timestamp, SHA, Elo snapshot)

## Phases

### Phase 0 — Subprocess self-play orchestration spike (BLOCKING)

**Goal:** confirm two Python subprocesses can each host a Bot and play
each other headlessly on Simple64, coordinated by an external
orchestrator.

**Spike deliverables:**

1. `scripts/spike_subprocess_selfplay.py` — launches two subprocesses
   from two minimal bot stubs (one each side), brokers SC2 connection,
   collects results.
2. Verify: both sides produce actions, game completes with a winner,
   no hangs, results file well-formed.
3. Investigate burnysc2's ladder/AI-arena protocol — confirm which
   mechanism lets two separate processes play. Document the approach.

**Kill criteria:** if subprocess self-play requires unreasonable
plumbing (e.g., a custom SC2 replay server, or only works via paid
AI-Arena infra), escalate. Options: (a) drop self-play and keep
versioning alone, (b) accept shared-process self-play only and shrink
the version scope back to narrow — a significant plan revision.

**Budget:** 2 hours (up from 1 — subprocess orchestration is a bigger
unknown than in-process was).

### Phase 1 — Move full stack to `bots/v0/`; define orchestrator

**Goal:** `src/alpha4gate/` becomes `bots/v0/`. Orchestrator scaffolded.
Everything still works exactly as today, just loaded via the registry.

Work:

1. Scaffold `src/orchestrator/` with `registry.py`, `contracts.py`,
   `snapshot.py` stubs.
2. Move `src/alpha4gate/` → `bots/v0/` wholesale. Update all internal
   imports (`from alpha4gate.bot import Bot` → `from bots.v0.bot import Bot`).
3. Entry-point wrapper: `python -m bots.v0` implements the bot-spawn
   contract.
4. Write `bots/v0/VERSION`, `bots/v0/manifest.json`.
5. Symlink or copy `bots/v0/` → `bots/current/` so live workflows
   (dashboard, daemon, existing scripts) keep working.
6. Update `pyproject.toml` entry points and packaging to include
   `bots/` and `orchestrator/`.
7. Update `runner.py` invocations in scripts to use the orchestrator
   for spawning.

**Risks:**
- **Import graph churn.** ~46 modules to rename. Likely the biggest
  mechanical task in the plan. Estimate 6–10h of careful refactor.
- **Daemon/API pathing.** Anything that hard-codes `data/` or
  `src/alpha4gate/` paths needs updating to resolve relative to
  `bots/current/`.
- **Test imports.** `tests/` imports from `alpha4gate.*` — update to
  `bots.current.*` (or a test-time alias).

**Acceptance:**
- `uv run pytest` green
- Full SC2 game runs via `python -m bots.current` (live path)
- Dashboard connects to `bots/current/` API and renders normally

### Phase 2 — Registry + full-stack snapshot tool

Work:

1. `src/orchestrator/registry.py`:
   - `list_versions() -> list[str]`
   - `get_version_dir(v) -> Path`
   - `get_manifest(v) -> Manifest`
2. `src/orchestrator/snapshot.py` (+ `scripts/snapshot_bot.py` CLI):
   - Copies `bots/current/` → `bots/vN/` (full tree)
   - Writes `VERSION`, `manifest.json` with parent, git SHA, timestamp,
     Elo snapshot
   - Copies current best checkpoint into `bots/vN/checkpoint.zip`
3. Registry CLI: `python -m orchestrator.registry list`,
   `python -m orchestrator.registry show vN`.
4. `bots/current/` semantics defined: it's a working copy, either a
   symlink to `bots/vN/` or a fresh fork. Snapshot = promote current
   to a new vN+1 and leave current as a fork of it.

**Acceptance:**
- `scripts/snapshot_bot.py --name v1` produces a full self-contained
  version dir
- `python -m bots.v1` boots and plays a game
- `bots/v0/` and `bots/v1/` can both be run, independently

### Phase 3 — Subprocess self-play runner

Work:

1. `src/orchestrator/selfplay.py` + `scripts/selfplay.py`:
   `--p1 v3 --p2 v5 --games 20 --map Simple64`
2. Launches two subprocesses per game, each implementing the
   bot-spawn contract. Orchestrator coordinates SC2 handshake (exact
   mechanism TBD in Phase 0).
3. Per-game result → `data/selfplay_results.jsonl` (shared, not
   per-version).
4. Alternating P1/P2 seats across the batch.
5. Crash handling: subprocess timeout → draw or excluded, logged.

**Acceptance:**
- 20-game self-play batch completes without hangs
- Results well-formed and seat-alternated
- If one subprocess crashes, the other is cleaned up; no orphan SC2s

### Phase 4 — Elo ladder

Work:

1. `data/bot_ladder.json`: `{version: {elo, games_played, last_updated}}`
2. `src/orchestrator/ladder.py` + `scripts/ladder.py`:
   - `update` — round-robin between top-N + current
   - `show` — standings
   - `compare vA vB --games 20`
3. Standard Elo, K=32, new versions start at parent's Elo.
4. Dashboard tab: ladder + head-to-head grid. Frontend reads
   `data/bot_ladder.json` (shared data, schema-stable).
5. Unit-tested Elo math.

**Acceptance:**
- Ladder updates reproducibly on a known scenario
- Dashboard tab renders

### Phase 5 — Sandbox enforcement + skill integration

Work:

1. **Sandbox hook** (`scripts/check_sandbox.py`, wired pre-commit):
   Commits tagged `[advised-auto]` may touch:
   - `bots/current/**` — freely
   - Nothing else. Not `src/orchestrator/`, not `pyproject.toml`,
     not `tests/`, not `frontend/`, not `scripts/`.
   Hard fail if violated.
2. **`/improve-bot-advised` updates:**
   - Only edits `bots/current/**`.
   - Proposing orchestrator or dep changes → opens PR with label
     `advised-proposed-substrate`; no auto-merge.
   - After passing iteration: snapshot to `vN+1`, commit tagged
     `[advised-auto]`, push.
3. **Validation rewire:** new `current` plays prior best via
   `selfplay.py`. Promotion on Elo gain ≥ threshold (default +10 Elo
   over 20 games).
4. **Run-start banner:** skill prints "I can edit: bots/current/**. I
   cannot edit: src/orchestrator/, pyproject.toml, tests/, frontend/,
   scripts/" at session start.

**Acceptance:**
- Skill editing `pyproject.toml` → hook blocks
- Skill editing `bots/current/learning/trainer.py` → allowed,
  snapshot happens, Elo validates

### Phase 6 — Self-play-driven improvement loop

Work:

1. `/improve-bot-advised --self-improve-code --opponent v5`
2. Curriculum opponent selection (advance when +N Elo cleared)
3. Stretch: pool sampling from top-K versions for mixed-style
   validation signal (AlphaStar-lite league at single-box scale).

**Acceptance:**
- Multi-hour run produces v1 → v2 → v3 with monotonically rising Elo
- At least one version beats SC2 AI at a higher difficulty than v0

## Sizing (revised for big-box)

| Phase | Estimate | Notes |
|---|---|---|
| 0 | 2 h | Subprocess spike — gates everything. |
| 1 | 8–12 h | Full stack move; largest mechanical task. |
| 2 | 3–4 h | Registry + snapshot. |
| 3 | 4–6 h | Subprocess self-play; depends on Phase 0 outcome. |
| 4 | 3–4 h | Ladder + dashboard tab. |
| 5 | 3–5 h | Hook + skill rewire. |
| 6 | 2 h code + open-ended soak. |

Total build work: ~25–35 h before the payoff phase. ~10h more than
the narrow plan, buying full-stack autonomy.

## Rollback / safety

- Each version is a directory. `rm -rf bots/vN/` deletes one.
- Git tags `bot/vN` pin the code state.
- Sandbox hook is the primary rail; skill's own quality gates back
  it up.
- The `[advised-auto]` commit tag is searchable: `git log --grep
  '\[advised-auto\]'` shows every autonomous change.

## Open decisions deferred

- **Per-version venvs.** Deferred. Starting with shared repo deps. If
  a version ever genuinely needs different deps, that's a
  `advised-proposed-substrate` PR and we revisit.
- **Multi-map ladders.** Scoped to Simple64 for now.
- **League-style pool sampling** (Phase 6 stretch) — decide after
  first clean v0 → v3 Elo trajectory.

## Out of scope

- Distributed self-play across machines
- Race-specific bot versioning (Protoss-only)
- Human-vs-bot interactive play
