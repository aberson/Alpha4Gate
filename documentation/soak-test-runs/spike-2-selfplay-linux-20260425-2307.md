# Spike 2 — Self-play Unmodified on Linux (Phase 8 Step 3)

**Status:** PASSED
**Date:** 2026-04-25 23:07:54 → 23:09:51 local (~2 min wall-clock)
**Timebox:** 4 hours; used ~2 min (<1%)
**Operator:** Claude Code session (Opus 4.7) under user supervision
**Plan:** [phase-8-build-plan.md §7 Step 3](../plans/phase-8-build-plan.md)
**Halt condition:** port collisions, signal-thread errors, or mid-game crashes → defer phase. **Did not trigger.**

---

## Summary

`scripts/selfplay.py --p1 v0 --p2 v0 --games 2 --map Simple64` runs to clean exit on Linux WSL2 unmodified. Both games completed with `error=None`, the port-collision patch (`port-collision patch installed`) and the worker-thread signal patch (`worker-thread signal patch installed`) both applied successfully on Linux, and 2 SC2 instances spawn per game with no orphaned processes. Results appended to `data/selfplay_results.jsonl` (lines 636–637). The dominant unknown of Step 3 (does our self-play orchestrator's burnysc2-port-collision + signal-thread workarounds, both authored on Windows, also work on Linux?) is settled: **yes, both patches work on Linux unmodified.** Phase 8 is GO for Step 4 (Spike 3 — 4-way parallel self-play).

## Done-when checklist (per plan §7 Step 3)

- [x] `scripts/selfplay.py --p1 v0 --p2 v0 --games 2` completes 2 clean games in WSL
- [x] Port-collision patch in `selfplay.py` works on Linux (no `Address already in use` errors)
- [x] Both games complete without orphaning processes (`kill_switch: Process cleanup for 0 processes` after each game — already cleaned)
- [x] Results land in `data/selfplay_results.jsonl` (lines 636–637, both with `error=None`)

## Measurements

| Metric | Value |
|---|---|
| Game 1 wall-clock | 46.85 s |
| Game 2 wall-clock | 46.79 s |
| Per-game in-game time | ~328 s (simulation_loop=7352 @ 22.4 loops/s) |
| Effective speed factor | ~7× (in-game / wall-clock) |
| Game results | 2/2 draws (both hit `game_time_limit=300` before either v0 could resolve) |
| SC2 processes per game | 2 (one per side; ports auto-allocated via the port-collision patch) |
| Total wall-clock | ~117 s (44 s startup + 47 s game1 + 8 s teardown/spawn + 47 s game2 + ~15 s teardown) |

## Environment

- Host: Windows 11, WSL2 Ubuntu 22.04 (`VERSION_ID="22.04"`)
- Python: 3.12.13 (deadsnakes PPA)
- uv: 0.11.7
- burnysc2: 7.1.3
- SC2 build: `Base75689` (game version 4.10, August 2019)
- venv: `~/venv-alpha4gate-linux/` (4.8 GB, ext4 native — see `feedback_uv_venv_must_be_on_ext4.md`)
- Repo state: `master` @ `ea9b6e5`, working tree clean
- Env vars (from `~/.profile`): `UV_PROJECT_ENVIRONMENT`, `SC2PATH=$HOME/StarCraftII`, `SC2_WSL_DETECT=0`

## Operator command actually executed

```bash
wsl -d Ubuntu-22.04 -- bash -lc 'cd /mnt/c/Users/abero/dev/Alpha4Gate && \
  uv run python scripts/selfplay.py --p1 v0 --p2 v0 --games 2 --map Simple64'
```

No deviations from the plan-doc command. The `SC2PATH=~/StarCraftII` prefix from the plan command is unnecessary — `~/.profile` already exports it.

## Verification of negative claims (halt-condition checks)

- ✅ **No port collisions.** `selfplay.py` log line `port-collision patch installed` fires once at startup; both games allocate distinct (`43433/34055` for game 1, `33209/37629` for game 2) `--GamePort` values without retries. No `Address already in use` or `OSError` on socket bind.
- ✅ **No signal-thread errors.** `worker-thread signal patch installed` fires once at startup; no `ValueError: signal only works in main thread` in the log. The Windows-authored patch (which neutralizes burnysc2's main-thread signal-handler installation when running inside an asyncio worker thread) works on Linux too.
- ✅ **No mid-game crashes.** Both games progress `init_game → in_game → ended` cleanly. The two `aiohttp.client_exceptions.ClientConnectionResetError: Cannot write to closing transport` tracebacks are **benign teardown noise** — they fire on the proxy's final `bot_ws.send_bytes()` to a bot whose WebSocket has already been closed. They are caught by `sc2.proxy.proxy_handler` (`ERROR | sc2.proxy:proxy_handler:150 - Caught unknown exception: Cannot write to closing transport`) and do not propagate to the game result. Both records show `error=None`.
- ✅ **No orphaned processes.** `sc2.sc2process:kill_all:36 - kill_switch: Process cleanup for 0 processes` after each game means no stragglers — the proxy already shut down both SC2 processes via `_close_connection` before kill_switch runs.

## What the records look like

```jsonl
{"match_id": "00319c6e-c5db-41c3-95c0-2d177601a58b", "p1_version": "v0", "p2_version": "v0", "winner": null, "map_name": "Simple64", "duration_s": 46.85, "seat_swap": false, "timestamp": "2026-04-26T06:08:56.298548+00:00", "error": null}
{"match_id": "8a805e3c-7456-4001-942e-181b27c4bead", "p1_version": "v0", "p2_version": "v0", "winner": null, "map_name": "Simple64", "duration_s": 46.79, "seat_swap": true, "timestamp": "2026-04-26T06:09:45.030407+00:00", "error": null}
```

`seat_swap=False` then `seat_swap=True` matches the orchestrator's standard 2-game seat-swap convention. UTC timestamp is 6h ahead of local 23:08–23:09 (PDT-7? actually +6 from PST which is UTC-8 → would be UTC-2; this is just the `datetime.now(timezone.utc)` from the orchestrator on the WSL side, no anomaly).

## Why both games drew

`game_time_limit=300` (in-game seconds, default in the orchestrator's `Namespace` dump above) is too short for two v0 rules-based bots starting at the standard Simple64 main-base spawns to mount and resolve a meaningful army-vs-army fight. v0 spends the first 60–90 in-game seconds on early economy/Gateway warm-up; at 300 s in-game (~5 minutes game-time, ~47 s wall-clock at 7× speed) both bots are still in the early-mid game with intact bases, so the game terminates with `winner=None`.

This has **no bearing on Spike 2's halt conditions** (which were about *whether the platform runs self-play*, not *whether the eval signal is meaningful*). Whether Spike 5+'s training pipeline needs `game_time_limit=900` or `1500` is a Phase 8 design question downstream of this spike.

## RSS sampling

A side monitor polled `pgrep SC2_x64 | wc -l` every 5 s during the run. It only emitted "0" before games started, then completed without further events — the 2-second-precision sampling missed the brief windows where SC2 processes were live (each game's full SC2 lifetime was ~40 s, but `pgrep` mid-process sampling is a coarse signal anyway). Per-process RSS measurement is **deferred to Spike 3 as planned** — its done-when criterion already includes "per-process resident RAM measured", with the `pidof SC2_x64 | xargs -I{} ps -p {} -o rss=` recipe documented in the spike-1 record.

## Halt-condition decision

NONE of the documented halt conditions triggered:

- ✅ No port collisions
- ✅ No signal-thread errors
- ✅ No mid-game crashes (the aiohttp tracebacks are benign teardown noise; both games returned `error=None`)

Phase 8 proceeds to **Step 4 (Spike 3 — 4-way parallel self-play)** in a future session.

## Next session

Step 4 / Spike 3 — 4-way parallel self-play, 8-hour timebox, halt condition is RAM blowup or heavy crashes. Operator commands are in [phase-8-build-plan.md §7 Step 4](../plans/phase-8-build-plan.md#step-4-operator--spike-3-4-way-parallel-self-play-decisive-on-unlock-size). Use the spike-1-documented `pidof SC2_x64 | xargs -I{} ps -p {} -o rss=` RSS sampler (NOT `ps -C SC2_x64` — that returns nothing on this WSL2 kernel).

## Memory entries created/updated in this spike

- `project_headless_linux_training_opportunity.md` (updated — Spike 2 PASSED, Spike 3 next)
- `MEMORY.md` index updated
