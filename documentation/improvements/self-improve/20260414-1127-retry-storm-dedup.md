# Retry-storm dedup — plan

Run: `improve-bot/run/20260414-1127/`
Target: reduce duplicate command dispatches per 5-min window by ≥90%.

## Problem

In timeout games at difficulty 4, the Protoss bot issues an enormous number of duplicate command dispatches:

| action | target | count | rate (/min) | physical cap (/min) |
|---|---|---:|---:|---:|
| WarpIn | STALKER | 840 | 53.1 | ~5 |
| build | Assimilator | 187 | 23.1 | <4 |
| build | Pylon | 133 | 26.5 | <4 |
| build | Gateway | 97 | 30.9 | <4 |
| build | Forge | 19 | 16.1 | <4 |

Source evidence: [logs/game_2026-04-14T07-21-03.jsonl](logs/game_2026-04-14T07-21-03.jsonl).

Impact: decision bandwidth wasted, logs polluted, offensive pressure never materializes in timeout games, PPO reward signal noisy.

## Root causes (from code inspection)

Two dispatch points in [src/alpha4gate/bot.py](src/alpha4gate/bot.py) lack any cooldown / dedup:

1. **WarpIn loop** — [bot.py:661-701](src/alpha4gate/bot.py#L661-L701): `_produce_army` iterates warpgates every `on_step`. Calls `wg.warp_in(unit_id, pos)` whenever ability is available + can_afford + supply_left ≥ 2. If placement fails or SC2 has not yet registered the cooldown, the same warpgate re-dispatches next step. Logged unconditionally.
2. **Build backlog retry** — [bot.py:633-655](src/alpha4gate/bot.py#L633-L655): `_drain_backlog` pulls the top backlog entry each tick. If `self.build()` returns without the structure actually starting (blocked placement, worker travel, SC2 silently dropped it), the entry is not drained and re-fires.

Secondary contributor: [bot.py:501-531](src/alpha4gate/bot.py#L501-L531) `_build_structure` returns `True` as soon as `await self.build(...)` is called, regardless of whether SC2 accepted the command.

## Fix

Add a per-(action, target, source) dispatch cooldown that suppresses re-dispatch until either:
- A configurable cooldown window has elapsed (default: 2 game-seconds for unit warp-ins, 5 game-seconds for structure builds), OR
- The expected outcome has been observed (a new warp-in-progress unit appears / a new structure with that type appears in `self.structures`).

Implementation sketch (leave specifics to build-step):

- New module `src/alpha4gate/commands/dispatch_guard.py` with a `DispatchGuard` class keyed on `(action, target)` plus optionally a source identifier (warpgate tag, worker tag). Methods: `should_dispatch(key, game_time) -> bool`, `mark_dispatched(key, game_time)`.
- Construct one `DispatchGuard` on `Alpha4GateBot.__init__`.
- Wrap the two dispatch sites (`_produce_army` WarpIn, `_drain_backlog` build retry) to consult the guard before calling `wg.warp_in` / `self.build`, and log a suppressed-dispatch event at DEBUG when skipped.
- Optional: also wrap `_build_structure` so build-order progression benefits.

## Test plan

### Unit tests (new)

`tests/commands/test_dispatch_guard.py`:
- `test_first_dispatch_allowed`
- `test_second_dispatch_within_cooldown_suppressed`
- `test_dispatch_after_cooldown_allowed`
- `test_different_keys_independent`
- `test_reset_on_observed_outcome` (if the observed-outcome path is implemented)

### Integration / regression test

`tests/test_retry_storm_regression.py`: parse `logs/game_2026-04-14T07-21-03.jsonl` with the replay parser, count `(action, target)` pairs, assert counts exceed a threshold (canary for the bug) — this is the pre-fix evidence. Then add a fixture or synthetic test that exercises `_produce_army` against a stub warpgate with ability always available and can_afford always true, and assert that after N steps of dispatch, `wg.warp_in` was called ≤ `N / cooldown_ticks` + tolerance times.

### Type + lint

`uv run mypy src` and `uv run ruff check .` must pass.

## Acceptance criteria (quality gate)

1. `uv run pytest` passes (all existing 829 unit tests + the new ones).
2. `uv run mypy src` passes (strict mode).
3. `uv run ruff check .` passes.
4. The new module + guard wraps at minimum the WarpIn site in `_produce_army` and the build site in `_drain_backlog`.

Post-merge validation (outside build-step): replay-parse a fresh timeout game after running the bot for one match at difficulty 4, compute per-(action, target) rates, and confirm all listed actions drop to ≤10% of baseline rate. This validation happens in Phase 5 of the `/improve-bot` run, not inside `/build-phase`.

## Step 1: Add DispatchGuard + wire into WarpIn and build dispatch

- **Issue:** #104
- **Problem:** Implement `DispatchGuard` with `should_dispatch(key, now)` / `mark_dispatched(key, now)` keyed on `(action_name, target_name)` with a per-action cooldown policy (default 2s for WarpIn, 5s for build). Wire it into `_produce_army` (WarpIn path in [bot.py:661-701](src/alpha4gate/bot.py#L661-L701)) and `_drain_backlog` (build retry path in [bot.py:633-655](src/alpha4gate/bot.py#L633-L655)). Skip the dispatch (and the `_actions_this_step.append`) when the guard denies. Write unit tests covering first-dispatch-allowed, within-cooldown-suppressed, after-cooldown-allowed, and independent-keys.
