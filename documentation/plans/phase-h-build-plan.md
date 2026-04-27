# Phase H — Mini-game substrate (build plan stub)

> **STATUS: STUB.** Phase H scope is gated on
> [`mini-game-role-investigation.md`](../investigations/mini-game-role-investigation.md).
> This stub captures the substrate-level decisions that hold regardless
> of role outcome (scorecard / gate / reward). Per-role wiring lives in
> Phase J's build plan. Promote this stub to a full plan once the
> investigation finishes.

## 1. What this phase ships

A mini-game runner primitive in `src/orchestrator/` that can launch a
single SC2 mini-game (PySC2 canonical or custom-mapped from Phase I)
with a snapshot of the bot's relevant capability code, record results,
and return a structured score. Substrate phase — does not change rated
play, does not change the evolve loop yet (Phase J does that).

## 2. Existing context

Pulled from the master plan's Phase H pointer-stub. Read the master
plan's Phase H section first.

**Key constraints surfaced by the investigation skeleton:**

- Mini-games run as short SC2 sessions (1-5 min each) per candidate.
  Wall-clock cost compounds quickly — need batched execution and
  parallel-safe SC2 process management.
- Score schema must be flexible: PySC2 mini-games emit per-frame reward
  scalars; custom Protoss maps may need bespoke scoring (e.g., "did the
  concave form within 8 seconds of contact?").
- DB column placement: per-version score history goes in
  `bots/<version>/data/training.db` next to existing transitions, so
  scorecard data is per-version-isolated like every other capability.

## 3. Scope (skeleton)

**In scope (independent of role decision):**

- PySC2 dependency add to `pyproject.toml` (pinned version, no `extras`
  bloat).
- `src/orchestrator/minigames.py` — `run_minigame(version, map_name) ->
  MinigameResult` primitive.
- `MinigameResult` dataclass — score schema with PySC2 + custom-map
  fields.
- `bots/<version>/data/training.db` schema migration: new `minigame_results`
  table.
- `scripts/run_minigame.py` CLI entry — `python scripts/run_minigame.py
  --version v0 --map MoveToBeacon`.
- Smoke gate: 1 PySC2 mini-game (`MoveToBeacon`) end-to-end on `v0`.

**Out of scope (Phase J or later):**

- Wiring mini-game results into evolve gates / rewards / scorecards.
- Custom Protoss maps (Phase I).
- Dashboard rendering of mini-game scores.

## 4. Build steps (skeleton)

| # | Step | Type | Depends |
|---|------|------|---------|
| H.1 | Add PySC2 dep, verify import-clean on `bots/v0` | code | none |
| H.2 | `MinigameResult` dataclass + schema migration | code | H.1 |
| H.3 | `run_minigame()` primitive — PySC2 path only | code | H.2 |
| H.4 | `scripts/run_minigame.py` CLI | code | H.3 |
| H.5 | Smoke gate: 1 PySC2 mini-game on `v0`, verify DB row | operator | H.4 |

Per-step problem statements + Done-when criteria to be drafted on
investigation conclusion.

## 5. Tests

`tests/test_minigames.py` (mocked PySC2),
`tests/test_minigame_results_schema.py` (DB migration round-trip),
`tests/test_run_minigame_cli.py` (argparse).

## 6. Effort

~2-3 days (post-investigation). Scope may grow if PySC2 integration
hits Windows-specific friction.

## 7. Validation

`bots/v0` mini-game run on `MoveToBeacon` returns a score within the
published PySC2 baseline range (random agent ~1-3, trained agent
~25+). Smoke gate produces one `minigame_results` row in
`bots/v0/data/training.db` with finite numeric score.

## 8. Gate

Smoke gate green AND the score is in the published baseline range
(rules out integration-bug "all zeros" failure modes).

## 9. Kill criterion

Investigation concludes mini-games are scorecard-only AND substrate
cost (~3 days build + ongoing maintenance) is not justified by the
analytical value at our scale. Phase H deferred indefinitely; Phase
I/J also deferred.

## 10. Rollback

Delete `src/orchestrator/minigames.py`, `scripts/run_minigame.py`, the
DB migration (training.db drop column). PySC2 dep stays in
`pyproject.toml` as a no-op (cheap; cleaner than re-removing).

## 11. Cross-references

- Master plan Phase H pointer: `documentation/plans/alpha4gate-master-plan.md`
- Investigation: `documentation/investigations/mini-game-role-investigation.md`
- Phase I (custom maps): blocked on this phase
- Phase J (role wiring): blocked on this phase + investigation
