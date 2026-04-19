# Self-play viewer soak — 2026-04-18

Operator soak for issue #147 (Step 9 of the self-play viewer plan).

## TL;DR

Four of seven checkpoints pass cleanly. One partial, one fail, one N/A. **Two real bugs surfaced and fixed during the soak** (neither caught by unit tests because both paths were stubbed). Three additional follow-ups logged.

- **Fixed**: `signal.signal` worker-thread bug, `GAME_START_HWND_TIMEOUT_SECONDS` too short.
- **Follow-ups**: orphan SC2 on Esc teardown, "broodwar" cosmetic string, Esc-detach-but-continue-batch semantics, resolution-too-big UX.

Step 9 validated the viewer integration — it was previously untested end-to-end with real SC2 because `batch_fn` was stubbed in every unit test.

## Setup

- Host: Windows 11 Home, 2560×1600 display
- Python: 3.12.13 (dedicated `.venv-py312`, pygame 2.6.1, pywin32 311, psutil 7.2.2)
- SC2: installed at `C:\Program Files (x86)\StarCraft II\`
- Repo: branch `master` @ 558723b (pre-fix) / `HEAD` with two hotfixes described below
- CLI: `--size small` (default `large` is too big for 2560×1600)

## Runs

Four runs were needed to reach a clean checkpoint pass. The first three surfaced bugs that were fixed before the final run:

| Run | Result | Records file |
|---|---|---|
| 1 | All 10 games fail in 170 ms; `signal.signal` off-main-thread | `data/selfplay_results.jsonl.failed-20260418-1642` |
| 2 | Multi-game, pane 0 never attached; HWND timeout 2s but SC2 needs 6s | `data/selfplay_results.jsonl.partial-run2-timeout-bug` |
| 3 | 6 games OK with --size small (Ctrl+C interrupt during debug) | `data/selfplay_results.jsonl.partial-run3-6games` |
| 4 | Final 10-game observation run; Esc during game 8 per checkpoint (g) | `data/selfplay_results.jsonl.soak-run4-7games` (7 records — see checkpoint (g)) |

Pre-existing 4 records moved aside to `data/selfplay_results.jsonl.bak-soak-20260418`.

## Bugs fixed during the soak

### Bug 1: `signal.signal` only works in main thread

`sc2.sc2process.SC2Process.__aenter__` unconditionally calls `signal.signal(SIGINT, ...)`. Python raises `ValueError: signal only works in main thread of the main interpreter` off-main-thread, instantly and synchronously. `SelfPlayViewer.run_with_batch` puts `run_batch` on a `selfplay-batch` worker thread so pygame can own the main thread. Every game failed in ~10 ms before SC2 ever spawned; `asyncio.gather(..., return_exceptions=True)` silently swallowed the exception.

**Fix**: `_install_worker_thread_signal_patch()` in `src/orchestrator/selfplay.py` replaces `sc2.sc2process.signal` with a minimal `types.SimpleNamespace` proxy whose `signal()` attribute no-ops when called off-main-thread. Unit tests in `tests/test_selfplay.py::TestWorkerThreadSignalPatch`.

### Bug 2: `GAME_START_HWND_TIMEOUT_SECONDS = 2.0` — too short

`_handle_game_start` called `attach_pane(slot, pid, ..., hwnd_timeout_s=2.0)`. On this host SC2 takes ~6 seconds from process spawn to the first visible top-level window, so the 2-second timeout always fired on slot 0 (the first iteration in the for-loop). Slot 1 succeeded because the 2-second wait on slot 0 gave slot 1's window time to appear. Symptom: pane 0 visibly unattached (SC2 window with standard Windows chrome floating outside the container).

**Fix**: Bumped `GAME_START_HWND_TIMEOUT_SECONDS` from 2.0 → 15.0 seconds, matching `reparent.find_hwnd_for_pid`'s default. Trade-off documented in the new docstring: this is a hotfix that blocks pygame's main thread during game-start; the proper Step-7-style per-frame deferred HWND lookup is the follow-up.

## Checkpoint results — run 4

### (a) Both SC2 windows slotted into the container every game
- **PASS** — operator confirmed "yep, looks good" for all games in run 4
- Pre-fix: pane 0 never attached; fix validated end-to-end

### (b) Overlay W-L / Wins score updates correctly
- **PASS** — same-version collapse to `Wins: N/10` working. Score stayed at 0 because v0 bot consistently draws at the 300-second game timeout (expected — this is bot behavior, not an overlay bug)

### (c) No orphan SC2_x64.exe processes after the batch
- **FAIL** — one orphan SC2 remained; operator ended via Task Manager
- Root cause: batch thread abandoned mid-game 8 when the 30-second `BATCH_STOP_JOIN_TIMEOUT_SECONDS` fired after Esc. The in-flight SC2 process for game 8 had no cleanup path; see warning line below
- Follow-up: issue TBD

### (d) No HWND leak across 10 reparent cycles
- **N/A** — python.exe exited cleanly before a full 10-cycle measurement could be taken (Esc during game 8). No mid-run User Objects growth was observed operator-side. Deferred to a future soak once (c) and (g) are fixed

### (e) Kill SC2 mid-game during game 5 → placeholder + clean result + game 6 fresh
- **PASS** — operator reports the overlay showed "P2 has rage quit" when one SC2 was ended via Task Manager. Game 5 logged cleanly, game 6 spawned and reparented fresh
- Minor UX note: "rage quit" messaging is clearer than a generic placeholder; this path is working well

### (f) `s`/`b` hotkey spam during a game
- **PASS** — no crash, no layout corruption. Resize toast from Step 8 visible. Both panes remained attached

### (g) Esc during game 8 → detach all, batch finishes headless with 10 records
- **PARTIAL** — Esc detached windows to top-level ✓; batch did NOT continue and finish games 8-10 ✗
- Terminal warning:
  ```
  [selfplay_viewer] warning: batch thread did not exit within 30.0s of viewer close;
  orphaned SC2 processes may remain until the current game finishes.
  ```
- Terminal summary: `total: 0 games` (reads empty `result_box` — records ARE in the jsonl)
- Design mismatch: the plan spec says "run_batch thread should continue and finish games 8-10 with no viewer". The current implementation sets `stop_event` on Esc, which cancels the batch. This is a Step-4 semantics bug; the viewer's Esc should detach-and-continue, not detach-and-cancel
- Follow-up: issue TBD

## Cosmetic observations

- Overlay surrender message reads **"V0 surrendered broodwar"** — "broodwar" looks like a stringified race/expansion enum leaking through the template. Follow-up.

## Resolution / UX

Operator feedback: even at `--size small` (960×720 per pane) the container is overwhelming on a 2560×1600 display. Needs an even smaller preset or parametric sizing. Discussion pending.

## Summary — checkpoint scoreboard

| Checkpoint | Result |
|---|---|
| (a) Both panes reparent | **PASS** |
| (b) Overlay updates | **PASS** |
| (c) No orphan SC2 | **FAIL** |
| (d) No HWND leak | **N/A** |
| (e) Kill mid-game | **PASS** |
| (f) Hotkey spam | **PASS** |
| (g) Esc-detach-continue | **PARTIAL** |

- 4 pass, 1 partial, 1 fail, 1 N/A
- **2 real bugs fixed** via this soak
- **3+ follow-ups opened** (orphan SC2 on Esc, Esc semantics, "broodwar" cosmetic, resolution UX)

## End-of-run artifacts

- `data/selfplay_results.jsonl.soak-run4-7games` — 7 records from run 4 (games 1-7 before Esc)
- `documentation/soak-2026-04-18-selfplay-viewer.md` — this doc
- Two hotfix commits on `master` (see git log)
- New tests in `tests/test_selfplay.py::TestWorkerThreadSignalPatch`
