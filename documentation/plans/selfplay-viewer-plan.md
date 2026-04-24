# Self-play viewer — observer-based single-window

## 1. What This Feature Does

Refactor the existing Alpha4Gate self-play viewer
(`src/selfplay_viewer/`, shipped 2026-04-18) from a **two-pane
reparenting** design to a **single-pane observer** design. Spawn a third
SC2 client as a `sc2.player.Observer` with full-map vision (no fog),
reparent **only that one window** into the themed pygame container, and
let the two bot clients continue running in the background
minimized/offscreen where they're not watched.

**Why now.** The two-pane viewer works, but each pane shows one bot's
fog-of-war-limited camera; the two cropped perspectives don't combine into
a useful strategic view, and 2×1024×768 of SC2 crammed side-by-side is
awkward on a single monitor. An observer client gives a neutral
omniscient camera on the full map — exactly the watching experience the
viewer was built for. The cost is one extra SC2 process (~2 GB RAM) and a
small burnysc2 patch; the payoff is a strictly better single-screen
experience and a viewer that's safe to run during rated training soaks
(the observer sees everything but the bots still play with fog, so
decisions are unaffected).

**Not scope.** Elo / rating overlay, advisor commentary overlay, dashboard
integration, auto-launch from the Phase 6 daemon, multi-batch window
management, Linux/macOS support, SC2 lifecycle changes, replay-viewing mode.

## 2. Existing Context

**Investigation.** Full feasibility writeup at
[`documentation/investigations/observer-player-viewer-investigation.md`](../investigations/observer-player-viewer-investigation.md)
(2026-04-24). Confirmed: `sc2.player.Observer` is a first-class player
type at the proto and Python class level
([`.venv/Lib/site-packages/sc2/player.py:89-94`](../../.venv/Lib/site-packages/sc2/player.py#L89-L94));
`sc2.client.Client.join_game` supports observers via `observed_player_id`
([`client.py:89-95`](../../.venv/Lib/site-packages/sc2/client.py#L89-L95));
`Controller.create_game` accepts Observer slots at the proto level
([`controller.py:34-40`](../../.venv/Lib/site-packages/sc2/controller.py#L34-L40)).
**But** `sc2.main._play_game` does not wire Observer through — it reads
`player.race` and `player.ai` unconditionally
([`main.py:217-225`](../../.venv/Lib/site-packages/sc2/main.py#L217-L225)),
both missing on `Observer()`. A surgical monkey-patch dispatches Observer
instances to our own coroutine; same pattern as the port-collision patch
at [`selfplay.py:76-118`](../../src/orchestrator/selfplay.py#L76-L118).

**What already exists (DONE, to be partially reused).**

- `src/selfplay_viewer/container.py` — pygame window + layout engine.
  **Refactor:** replace 2-pane layout table with single-pane dimensions;
  drop `--bar {top,side}` since there's no side real-estate to trade.
- `src/selfplay_viewer/reparent.py` — Win32 `SetParent` / `MoveWindow`
  primitives. **Reuse as-is.** Generic enough that single-pane is a
  trivial caller.
- `src/selfplay_viewer/backgrounds.py` — SF2-themed asset loader. **Reuse.**
- `src/selfplay_viewer/overlay.py` — version labels + W-L overlay.
  **Refactor:** refit layout to the new container dimensions; `--bar`
  branching removed.
- `src/orchestrator/selfplay.py` — `run_batch` + `_run_single_game`.
  **Extend:** add observer coroutine, add `_play_game` dispatch patch,
  change `GameMatch` players list from 2→3, extend PID discovery and
  callback signature to pass the observer PID.
- `scripts/selfplay.py` — CLI entry. **Extend:** add `--observer` /
  `--no-observer` (default on), drop `--bar`. Keep `--no-viewer`,
  `--background`, `--size`.

**Relevant memory.**

- `feedback_sc2_process_management.md` — never kill `SC2_x64.exe`. Applies
  to all three processes now, including the observer.
- `project_selfplay_viewer_code_complete.md` — Step 9 soak ran 2026-04-18;
  two-pane viewer is shipped and working. This plan supersedes that
  design.
- `feedback_user_powershell.md` — operator commands in PowerShell.
- `feedback_worktree_venv_incomplete.md` — `uv sync` + `uv pip install -e
  .[dev]` in worktrees.
- `feedback_py312_venv_recipe_for_soaks.md` — main `.venv` is Py3.14 and
  has no pygame wheels; use the side `.venv-py312` for viewer work.

**Not-yet-addressed open questions from the investigation (§6)** — answered
for this plan:

1. *Neutral omniscient camera vs fog-disabled bot camera?* **Neutral
   omniscient** (third-process observer).
2. *Dev-only or safe during training?* **Safe during training** — the
   observer is a non-playing spectator, so bots continue with their normal
   fog-of-war decisions.
3. *Keep themed backgrounds + overlays?* **Yes**, refitted to single-pane
   dimensions.

## 3. Scope

**In scope**

- Observer coroutine (`_play_observer`) that drives an observer client:
  join, observation+step loop, clean leave on `_game_result`.
- `sc2.main._play_game` monkey-patch: when the player is an `Observer`,
  route to `_play_observer` instead of the default AI/human path.
- `GameMatch` construction updated to include `Observer()` as a third
  player in `_build_match` (conditional on the `--observer` flag).
- `run_batch` PID discovery widened from 2→3 PIDs; the observer PID
  routed through the `on_game_start` callback signature.
- Observer camera init — `RequestDebug` to center camera on map
  midpoint immediately after join.
- `selfplay_viewer` layout refactor to single-pane, keeping SF2
  background and overlay.
- Soak gate: 10-game real self-play batch, watched end-to-end, verify
  observer renders full map, no orphan SC2 processes, no HWND leaks, RAM
  stays under 8 GB.

**Explicitly out of scope**

- Fog-disabled-bot-camera (`disable_fog=True`) variant. The investigation
  noted this as cheaper but unsafe during training; we're committing to
  the observer path instead. `disable_fog` is not added as a flag here.
- Dashboard integration, Phase 6 daemon auto-launch, concurrent-batch
  multi-viewer — same exclusions as the prior plan.
- Upstreaming the `_play_game` patch to BurnySc2 — nice-to-have, not
  blocking.
- Letting the user swap which client they're watching at runtime
  (observer vs P1-camera vs P2-camera). With a dedicated observer we
  already see everything; swapping framing has no value.
- Minimizing the 2 bot SC2 windows programmatically — we let Windows
  handle stacking; if they cover the pygame container the user moves
  them manually. (If this turns out to be annoying in practice, a
  follow-up issue can add `ShowWindow(SW_MINIMIZE)` on the 2 bot HWNDs.)

## 4. Impact Analysis

| File / module | Change type | Nature |
|---|---|---|
| `src/orchestrator/selfplay.py` | **Extend** | Add `_play_observer` coroutine. Add `_install_observer_dispatch_patch()` (monkey-patch `sc2.main._play_game` to branch on `isinstance(player, Observer)`). Extend `_build_match` to optionally append `Observer()` to the players list, gated on an `enable_observer` kwarg threaded from `run_batch`. Widen the PID snapshot-diff logic in `_run_single_game` from "expect 2 new PIDs" to "expect 3 new PIDs"; pass the observer PID into `on_game_start`. |
| `src/orchestrator/selfplay.py` (type alias) | **Change** | `OnGameStart` signature gains `observer_pid: int` after `p2_pid`: `Callable[[int, int, int, int, int, str, str], None]`. All existing callers updated; `-1` sentinel if observer is disabled or discovery timed out. |
| `scripts/selfplay.py` | **Extend** | Add `--observer / --no-observer` (default `--observer`). Drop `--bar` (no longer meaningful). Thread `enable_observer` through to `run_batch`. |
| `src/selfplay_viewer/container.py` | **Refactor** | Remove two-pane layout table + `bar` state. Add single-pane layout (see D1). `attach_pane(slot, pid, label)` becomes `attach_observer(pid, label)`; `attach_pane(slot=...)` deleted. Hotkeys: `s` (size toggle) kept; `b` (bar toggle) removed; `Esc` / close unchanged. |
| `src/selfplay_viewer/overlay.py` | **Refactor** | Drop side-bar variant. Single top-bar overlay only; refitted to new container width. |
| `src/selfplay_viewer/demo.py` | **Extend** | `--attach-notepad-pids A` (single PID) rather than `A,B` for the dev-only smoke test. |
| `tests/test_observer_coroutine.py` | **NEW** | Unit test `_play_observer` against a mocked `Client`: verifies it calls `join_game(race=None, observed_player_id=...)`, loops `observation` + `step` at the expected cadence, exits on `_game_result`, calls `leave()` once. |
| `tests/test_selfplay_callbacks.py` | **Extend** | Callback signature change — `observer_pid` added. Existing callback-ordering and exception-swallowing tests extended. |
| `tests/test_overlay.py` | **Refactor** | Drop 4-variant layout matrix (two bar × two size); keep 2 variants (large / small). |
| `tests/test_container_integration.py` | **Refactor** | Single notepad attach / move / detach; drop two-notepad path. |
| `tests/test_selfplay.py`, `tests/test_pfsp_sampling.py`, `tests/test_selfplay_transition_hand_off.py` | **Read-only (regression)** | Must stay green after the dispatch patch lands. The patch is idempotent and only activates when an `Observer` is in the player list; pure-2-bot paths must behave identically. |
| `pyproject.toml` | **Unchanged** | `[viewer]` extra already has pygame + pywin32 + psutil. No new deps. |

No changes to `bots/v0/`, the Phase 3 port-collision patch, the
dashboard, `data/selfplay_results.jsonl`, the registry, or the Elo
ladder.

## 5. New Components

### `src/orchestrator/selfplay.py` — `_play_observer` coroutine

```python
async def _play_observer(
    client: Client,
    observed_player_id: int,
    portconfig: Portconfig,
    realtime: bool,
    game_step: int = 8,
) -> Result | None:
    """Drive an observer client: join, loop, leave.

    - join_game(race=None, observed_player_id=observed_player_id)
    - frame loop: await client.observation(); if client._game_result,
      return that result. Otherwise await client.step(game_step).
    - on clean exit: await client.leave() with ConnectionAlreadyClosedError
      suppressed (bots may have torn down first).

    Never raises — returns None on unexpected disconnect so run_match's
    gather() doesn't flag this coroutine as a game-aborting failure.
    """
```

Notes:

- `observed_player_id=1` by default (watch P1). The specific ID is
  cosmetic — fog is *server*-side controlled by the bots' own client
  options; a neutral observer sees everything regardless of which player
  it nominally "observes."
- `game_step` matches the bots' default step so the observer doesn't
  block the server on quorum. Bots run at `game_step=8` (22.4 game-loops
  per real-second / 8 = 2.8 steps per sec) per current
  `bots/v0/bot_ai.py` config.

### `src/orchestrator/selfplay.py` — `_install_observer_dispatch_patch()`

```python
def _install_observer_dispatch_patch() -> None:
    """Idempotent monkey-patch of sc2.main._play_game to recognise Observer.

    Installed exactly once per process (idempotent flag), alongside the
    port-collision patch.
    """
    global _OBS_PATCH_INSTALLED
    if _OBS_PATCH_INSTALLED: return

    import sc2.main as _sc2_main
    from sc2.player import Observer

    orig_play = _sc2_main._play_game

    async def _dispatch(player, client, realtime, portconfig, **kwargs):
        if isinstance(player, Observer):
            return await _play_observer(
                client, observed_player_id=1,
                portconfig=portconfig, realtime=realtime
            )
        return await orig_play(player, client, realtime, portconfig, **kwargs)

    _sc2_main._play_game = _dispatch
    _OBS_PATCH_INSTALLED = True
```

Installed from `_install_port_collision_patch` sibling call site at
module init of `run_batch`.

### `src/orchestrator/selfplay.py` — `_build_match` extension

```python
# Before:
players=[p1_bot, p2_bot]
# After:
players = [p1_bot, p2_bot]
if enable_observer:
    from sc2.player import Observer
    players.append(Observer())
match = _GameMatch(map_sc2=maps.get(map_name), players=players, ...)
```

### `src/selfplay_viewer/container.py` — single-pane layout

Replace the 2-pane layout table with:

| size | container W | container H | observer rect | overlay rect |
|---|---|---|---|---|
| large | 2080 | 1250 | (80, 140, 1920, 1080) | (0, 0, 2080, 100) |
| small | 1780 | 1070 | (80, 140, 1620, 910) | (0, 0, 1780, 100) |

- Large preset targets 1920×1080 SC2 inside a 2080×1250 container — fits
  a 2560×1600 monitor with room for the Windows taskbar + title bar.
- Small preset targets 1620×910 — for 1920×1200 or 2560×1440 without
  spilling.
- 80 px side margin each side, 140 px top offset (40 px above the 100 px
  bar), 30 px below bar before the SC2 window starts.
- These numbers go into module constants so tuning is one-line.

Hotkeys after refactor:

- `s` — toggle large/small. `set_mode` + `MoveWindow` on the one
  attached SC2 HWND.
- `Esc` / window-close — `SetParent(hwnd, NULL)` on the observer HWND;
  pygame quit. Does NOT terminate any SC2 process (same rule as before,
  now applies to all 3).

### `src/selfplay_viewer/container.py` — observer-camera init hook

After `attach_observer(pid, label)` succeeds, send a one-shot
`RequestDebug` with `debug_game_state.action_raw.debug_camera_move` (or
whichever field name is current in `s2clientprotocol`) aimed at the map
midpoint. This is sent via a thin helper in the observer coroutine since
only it has the `Client` handle — the pygame side just triggers the
request via a shared asyncio-safe queue.

Actually simpler: run camera-centering inside `_play_observer` on the
first `observation()` response (we have `map_size` at that point — center
is `map_size / 2`). No pygame→asyncio bridge needed.

## 6. Design Decisions

### D1. Layout — single pane, drop `--bar`

With one window there's no bar-side real-estate trade-off. Overlay lives
above (top-bar only). `--bar` removed from CLI; existing users get a
deprecation warning for one release, then the flag is deleted. Less
surface, fewer test matrices, same look.

### D2. Monkey-patch `_play_game` vs fork `run_match`

Monkey-patch is surgical (~15 LOC) and idempotent. Forking `run_match`
means maintaining ~100 LOC of burnysc2-internal setup code that drifts on
library upgrades. We already monkey-patch `Portconfig.contiguous_ports`
and `portpicker.pick_unused_port` in the same file, so the pattern is
established. If burnysc2 ships observer support upstream later, this
patch goes to zero LOC without touching anything else.

### D3. Observer sees all — no `observed_player_id` experimentation

Per `client.py:89-92`, an observer with `race=None` is required to pass
`observed_player_id`. Protocol-wise this is the "this observer is
following player N" hint; the actual visibility is full-map regardless.
We hardcode `observed_player_id=1`. If we later want a "follow P1
camera" hotkey, we'd change that param per-join but that's a v2 feature.

### D4. Third SC2 process is **always on** when `--no-viewer` is not set

`--observer` default on. Running `scripts/selfplay.py ...` without flags
gets the viewer + observer. `--no-viewer` turns off BOTH the pygame
container and the 3rd SC2 process (there's no reason to pay 2 GB for a
non-rendered observer). `--observer --no-viewer` explicitly spawns the
observer with no container (useful for recording replays or debugging
the observer coroutine in isolation).

### D5. Hard commit to observer; no `disable_fog` fallback

Even though `GameMatch(disable_fog=True)` is a cheaper path (per the
investigation), it changes `self.enemy_units` and the like for the two
playing bots, which poisons training. We accept the ~2 GB RAM cost of a
third process in exchange for training-safety and a neutral camera. If
the observer path turns out to be unworkable after the spike (Step 1),
we revisit.

### D6. Camera init inside the observer coroutine, not inside pygame

First `observation()` response carries `game_info.start_locations` and
map dimensions. The coroutine already has the `Client`; sending a
`RequestDebug` is 3 lines. No need to pipe a request from pygame into
asyncio.

### D7. PID discovery — widen existing diff-based snapshot

Current code at `_sc2_pid_snapshot` + the delta logic in `_run_single_game`
waits until `len(new_pids - baseline) >= 2`. Change to `>= 3`. We cannot
distinguish which of the new PIDs is the observer from PID alone — all
three are `SC2_x64.exe`. The ORDER they come up is not deterministic
either. See R1 below for the reconciliation approach.

### D8. Hotkey simplification

Keep `s` (size), `Esc` (close). Drop `b` (bar). Reason: bar variant was
only useful to reclaim horizontal space for a 2nd pane; with 1 pane the
wide layout is always right.

### D9. Overlay stays minimal (unchanged from v1 intent)

Version labels + `Game N/M` + running W-L. No advisor feed, no Elo, no
map name. These are Appendix items for v2 just as in the prior plan.

## 7. Build Steps

### Step 1: Observer-support spike — standalone script, blocking gate

- **Problem:** Verify the full observer flow end-to-end before committing
  to the refactor. Write `scripts/spike_observer.py` that spawns a
  3-player `GameMatch` with `[Bot(P1), Bot(P2), Observer()]` via a local
  copy of `_install_observer_dispatch_patch` + a minimal
  `_play_observer`. Run one 1v1 game on `Simple64` with AI-Easy bots (no
  bots/v0/ required — this is proving burnysc2 plumbing). Confirm: (a)
  three `SC2_x64.exe` processes spawn, (b) the observer's SC2 window
  visibly renders the full map with no fog (manual check, no
  screenshot infra yet), (c) the game completes normally with no
  hang/timeout, (d) `client._game_result` is populated on the observer
  at game end, (e) RAM peak recorded (expect ~6 GB total for 3 SC2
  clients). Write the observations to
  `documentation/soak-2026-04-XX-observer-spike.md`.
- **Type:** operator
- **Issue:** #195
- **Flags:** none — user writes the spike script by hand, runs it, reports
  a green/red signal to build-phase which resumes (or halts) at Step 2.
- **Produces:** `scripts/spike_observer.py`,
  `documentation/soak-<date>-observer-spike.md`.
- **Done when:** The spike script runs a full AI-Easy vs AI-Easy 1v1 game
  with an observer, the observer window renders the full map, no SC2
  process is orphaned after the script exits, and the soak doc records
  pass/fail per checkpoint (a-e). On failure: triage the specific
  breakage (likely candidates: join_game asserts, game_loop desync,
  `_play_game` dispatch edge case) and fix inside the spike before
  committing to the refactor.
- **Depends on:** none

### Step 2: Promote the spike — observer coroutine + dispatch patch in selfplay.py

- **Problem:** Move the working `_play_observer` and
  `_install_observer_dispatch_patch` from `spike_observer.py` into
  `src/orchestrator/selfplay.py`. Call
  `_install_observer_dispatch_patch` from the same site as
  `_install_port_collision_patch`. Add
  `tests/test_observer_coroutine.py` with unit tests against a mocked
  `Client`: (a) `join_game` called with `race=None, observed_player_id=1`,
  (b) loop exits on `_game_result` populated, (c) `leave()` called
  exactly once, (d) `step(game_step=8)` called each iteration, (e)
  `ConnectionAlreadyClosedError` from `leave()` is swallowed.
- **Type:** code
- **Issue:** #196
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** Updates to `src/orchestrator/selfplay.py`,
  `tests/test_observer_coroutine.py`.
- **Done when:** Unit tests pass. `uv run pytest tests/test_selfplay.py
  tests/test_pfsp_sampling.py tests/test_selfplay_transition_hand_off.py
  tests/test_observer_coroutine.py` all green (patch must be transparent
  to existing 2-bot paths). `uv run ruff check src/orchestrator/selfplay.py`
  and `uv run mypy src/orchestrator --strict` clean.
- **Depends on:** Step 1 (greenlit)

### Step 3: Wire observer into `_build_match` + PID callback

- **Problem:** Add `enable_observer: bool = True` kwarg to `run_batch`
  and thread it through `_build_match` and `_run_single_game`. When
  enabled, `_build_match` appends `Observer()` to the players list.
  `_run_single_game`'s PID snapshot-diff widens to expect 3 new PIDs.
  The observer PID is identified heuristically — the **last** of the
  three to appear, or (more robustly) diff vs the baseline and pick the
  single PID not associated with a `BotProcess`'s stdout log file (the
  bot PIDs can be recovered from `proc.pid` on the proxy subprocesses;
  the remaining PID is the observer). Change `OnGameStart` signature to
  `Callable[[int, int, int, int, int, str, str], None]` adding
  `observer_pid` after `p2_pid`. Update all call sites; `-1` sentinel
  when `enable_observer=False`. Extend `tests/test_selfplay_callbacks.py`
  with the new signature.
- **Type:** code
- **Issue:** #197
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** Updates to `src/orchestrator/selfplay.py`,
  `tests/test_selfplay_callbacks.py`.
- **Done when:** `uv run pytest tests/test_selfplay_callbacks.py` passes.
  Real SC2 1-game integration check (manual, not in pytest):
  `python scripts/selfplay.py --p1 v0 --p2 v0 --games 1 --map Simple64
  --no-viewer` completes, three `SC2_x64.exe` processes were observed in
  Task Manager during the run, none remain after exit, `data/selfplay_results.jsonl`
  has one new record. `uv run ruff check` + `uv run mypy --strict` clean.
- **Depends on:** Step 2

### Step 4: Container refactor — single-pane layout

- **Problem:** In `src/selfplay_viewer/container.py`, replace the 2-pane
  layout table with the single-pane table in D1. Delete the `bar` state
  machine and the `b` hotkey. Rename `attach_pane(slot, pid, label)` to
  `attach_observer(pid, label)` and make it a single-HWND reparent.
  `detach_pane(slot)` becomes `detach_observer()`. Update `demo.py` to
  take `--attach-notepad-pids <PID>` (single value). Refit
  `overlay.py` to the new container width; drop side-bar overlay.
  Update `tests/test_overlay.py` (4-variant → 2-variant).
- **Type:** code
- **Issue:** #198
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** Updates to `src/selfplay_viewer/{container,overlay,demo}.py`,
  `tests/test_overlay.py`, `tests/test_container_integration.py`.
- **Done when:** `python -m selfplay_viewer.demo` opens a themed 2080×1250
  window with one grey placeholder rect at the correct position and an
  overlay stub. `python -m selfplay_viewer.demo --attach-notepad-pids <PID>`
  slots one notepad into the container; `s` toggles large/small and
  notepad resizes with it. `Esc` returns notepad to desktop as
  top-level. `uv run pytest tests/test_overlay.py
  tests/test_container_integration.py -m win32` passes on Windows.
- **Depends on:** Step 3

### Step 5: End-to-end viewer wiring — observer PID → pygame reparent

- **Problem:** In `scripts/selfplay.py`, when `--observer` (default on)
  and `--no-viewer` is NOT set, wire `SelfPlayViewer.attach_observer(observer_pid, label)`
  into the `on_game_start` callback. Label is `"{p1_label} vs {p2_label}"`.
  Add `--no-observer` flag for the no-viewer-no-observer mode. On
  non-Windows, force `--no-viewer` AND `--no-observer` with an info log
  (the observer doesn't gain us anything without a viewer). Verify
  end-to-end: real SC2 1-game run with viewer shows the observer window
  slotted inside the themed container with full-map vision.
- **Type:** code
- **Issue:** #199
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** Updates to `scripts/selfplay.py`.
- **Done when:** `python scripts/selfplay.py --p1 v0 --p2 v0 --games 1
  --map Simple64` launches the container with ONE SC2 window (the
  observer) slotted inside, showing full-map neutral vision. The two
  bot SC2 windows exist but are not embedded — visible on the desktop
  behind / alongside the container. Overlay shows `v0 VS v0 • Game 1/1
  • W-L: 0-0`; after the game ends, W-L increments and the container
  stays up for the post-game beat, then closes cleanly.
  `data/selfplay_results.jsonl` has one new record.
- **Depends on:** Step 4

### Step 6: Camera centering + small polish

- **Problem:** Inside `_play_observer`, after the first `observation()`
  response, read map dimensions from `game_info.start_locations` / map
  size and send a one-shot `RequestDebug` camera-move to the map
  midpoint. Without this the observer spawns with camera at (0,0). Also
  ensure the observer's `game_step` matches the bots' (default 8);
  mismatch would stall the quorum. Add a unit test that
  `_play_observer` issues exactly one camera-center request on the
  first frame and none after.
- **Type:** code
- **Issue:** #200
- **Flags:** `--reviewers code --isolation worktree`
- **Produces:** Updates to `src/orchestrator/selfplay.py`, extension to
  `tests/test_observer_coroutine.py`.
- **Done when:** Real SC2 1-game run: observer camera is centered on the
  map at game start (not at origin). No game-loop stalls (bot step
  timing vs wall clock matches pre-observer behavior within 5%). Unit
  test green.
- **Depends on:** Step 5

### Step 7: Soak observation — 10-game real self-play, watched end-to-end

- **Problem:** Windows-only OS-integration smoke gate. Run `python
  scripts/selfplay.py --p1 v0 --p2 v0 --games 10 --map Simple64` with
  viewer + observer on and **watch it end to end**. Checkpoints: (a)
  observer window correctly slotted in each of the 10 games, (b) full
  map rendered with no fog, camera centered at start, (c) overlay W-L
  score updates correctly after each game, (d) no orphan
  `SC2_x64.exe` processes after batch completes (three must spawn per
  game, three must exit per game), (e) no accumulating HWND leaks over
  10 reparent cycles (User Objects stays within +/- 20 of start), (f)
  RAM peak stays under 8 GB (3× 2 GB SC2 + Python ~500 MB + OS + Chrome
  ≤ 1 GB headroom on a 16 GB box), (g) deliberately kill the observer
  SC2 process mid-game during game 5 — placeholder triggers, the bots'
  game continues and completes with a correct record in
  `selfplay_results.jsonl`, game 6 spawns fresh with a new observer
  reparented into the container, (h) deliberately kill one BOT process
  mid-game during game 7 — that game records a crash result, game 8
  spawns fresh. (i) hit `s` twice during game 9 — no layout
  corruption, observer window resizes with container. (j) close the
  container via `Esc` during game 10 — observer HWND returns to
  top-level, batch continues and finishes with 10 records in the
  jsonl. Record observations in
  `documentation/soak-2026-04-XX-observer-viewer.md`.
- **Type:** wait
- **Issue:** #201
- **Flags:** none (halt-and-hand-off step; build-phase stops here and
  the user runs the soak manually, then resumes in a fresh session to
  mark Step 7 done)
- **Produces:** `documentation/soak-<date>-observer-viewer.md` with
  pass/fail per checkpoint.
- **Done when:** All 10 checkpoints (a–j) pass in a single run OR a
  triage doc lists each failure with a follow-up issue number. After
  soak completes, manually update this step to `Status: DONE
  (YYYY-MM-DD)` in the plan — build-phase does not auto-mark `wait`
  steps.
- **Depends on:** Step 6

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| Observer PID identification | The 3 new SC2 PIDs are indistinguishable by name alone; wrong pick → the pygame container embeds a bot's fog-limited window instead of the observer | D7: cross-reference the bot PIDs via the `BotProcess` proxy subprocesses (their PIDs are knowable) and pick the one remaining. If that fails, Step 7 (a) fails loudly — not a silent wrong-window bug |
| Port pressure with 3 clients | The burnysc2 7.1.3 port-collision bug gets worse with a 3rd client competing for LAN ports | Existing blocklist patch at [`selfplay.py:76-118`](../../src/orchestrator/selfplay.py#L76-L118) should handle this; Step 1 spike is the proving ground. If contention shows up, widen `attempts=40` to higher |
| Game-loop quorum stall | Observer not calling `step()` at the right cadence stalls both bots waiting on 3-way quorum | D3/Step 6: `_play_observer` calls `step(game_step=8)` every iteration, matching bot cadence. Unit test in Step 2 verifies `step` is called per-iter. Integration signal in Step 6 wall-clock check |
| ~2 GB extra RAM on 16 GB dev boxes | 3 SC2 clients + Chrome + dashboard could push the box into swap | Step 7 (f) explicitly measures RAM peak. If we go over 8 GB, `--no-observer` is the fallback and the plan adjusts to "observer is a workstation-class feature only" |
| Dispatch patch breaks 2-bot paths | The `_play_game` monkey-patch could subtly regress the pure-2-bot flow | Step 2 explicitly re-runs the existing 3-file Phase 3 test suite; patch is idempotent + type-guarded; delegation to original function when not an Observer |
| burnysc2 version drift | If burnysc2 upgrades and `_play_game` signature changes, the patch silently breaks | Patch installs are logged; add a `try/except AttributeError` around the patch install and surface a loud error if the target function moved |
| Observer camera snap-to-origin | Without camera-centering, observer spawns at (0,0) which is map-edge | Step 6 sends a one-shot `RequestDebug` camera move on first frame |
| DirectX reparent for the observer window | Same quirk class as the old 2-pane plan; single-pane doesn't eliminate it | Step 7 checkpoints (a), (i) cover it |

**Open questions (answerable during the build, not blocking):**

- Does the observer coroutine need to handle mid-game `_game_result`
  arriving before the bots have finished their final `step()`? Probably
  yes; Step 6 integration run will expose any stall.
- Should we silently minimize the 2 bot SC2 windows via `ShowWindow(SW_MINIMIZE)`
  after the observer is attached? Slight quality-of-life improvement, but
  adds UI surface. Defer; revisit after Step 7 if desktop clutter is
  annoying in practice.
- Does the camera-move `RequestDebug` persist after the user interacts
  with the observer window (clicks to drag the camera)? If yes, we do it
  once at start and trust the user. If no (SC2 snaps back), we send it
  every N frames. Empirical during Step 6.

## 9. Testing Strategy

**Unit tests (Linux-CI-safe, no pywin32):**

- `tests/test_observer_coroutine.py` — mocked `Client` verifies join
  semantics, step cadence, leave once, camera-center-on-first-frame.
- `tests/test_selfplay_callbacks.py` (extended) — new `observer_pid`
  signature; existing callback-ordering and exception-swallowing tests
  pass.
- `tests/test_overlay.py` (refactored) — 2-variant layout matrix
  (large / small), single-pane dimensions, overlay pixel presence in
  overlay rect.

**Windows-only integration (`@pytest.mark.win32`):**

- `tests/test_reparent.py` — unchanged, single-child reparent was
  already the primitive.
- `tests/test_container_integration.py` — one-notepad version of the
  existing two-notepad test.

**Manual observation (Step 1 + Step 7):**

- Step 1 spike: 1 game, 3 processes, observer renders full map, writeup.
- Step 7 soak: 10 games, 10-point checkpoint list, writeup.

**Regression safety:**

- Step 2 re-runs `tests/test_selfplay.py`,
  `tests/test_pfsp_sampling.py`,
  `tests/test_selfplay_transition_hand_off.py`. Dispatch patch must be
  transparent to 2-bot-only flows.

**CI strategy:**

- Linux CI runs the full unit suite with `viewer` extra not installed;
  `selfplay_viewer` modules keep their lazy `import pygame` /
  `import win32gui` guards.
- Windows dev-box runs `win32`-marked tests manually before Step 7
  soak.

## Appendix — Post-v1 extensions (reference, not scope)

1. **Advisor commentary overlay** — same idea as v1 deferral.
2. **Phase 6 auto-launch** — when the cross-version self-play daemon
   runs, optionally spawn the viewer.
3. **Elo / rating deltas on overlay** — read `data/bot_ladder.json`.
4. **Follow-P1-camera / follow-P2-camera hotkey** — reconfigure observer
   mid-match by swapping `observed_player_id`; neat but not essential.
5. **Minimize-bot-windows-on-attach** — `ShowWindow(SW_MINIMIZE)` on the
   two bot HWNDs once the observer is attached.
6. **Upstream the `_play_game` dispatch patch to burnysc2** — submit a
   PR so the monkey-patch can eventually go to zero LOC.
7. **Replay-viewer reuse** — feed a `.SC2Replay` into the same pygame
   container via `ObserverAI` + `run_replay` (burnysc2 already
   supports this — wiring only).
