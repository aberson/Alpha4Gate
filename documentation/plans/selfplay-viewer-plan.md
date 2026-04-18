# Self-play viewer — windowed container

## 1. What This Feature Does

Embed the two SC2 windows spawned during a self-play match into a single
themed container window so the user can watch round-robin self-play
without juggling scattered top-level windows on the desktop. The
container is built with **pygame**, uses Win32 `SetParent` to reparent
the two `SC2_x64.exe` windows as child windows of the pygame surface,
paints a themed background (SF2-inspired fight-select art) around them,
and overlays live match stats (version labels, game N-of-M, running
score).

**Why now.** Phase 3 shipped subprocess self-play (`src/orchestrator/selfplay.py`
`run_batch`) which already spawns two real SC2 processes per game. The
project trajectory is to outgrow fixed-difficulty SC2-AI ladders and rely
on round-robin self-play between `bots/vN/` versions as the primary
improvement signal (Phase 6). SC2 on Windows cannot run truly headless —
windows pop up regardless — so making those windows into a single themed
pair strictly dominates the default experience. Getting the viewer in
before Phase 6 ramps means the first long self-play soaks are already
watchable.

**Not scope.** Dashboard integration, automatic launch from the Phase 6
daemon, multi-pane global window management across multiple concurrent
`run_batch` calls, Linux/macOS support, or any SC2-process lifecycle
changes.

## 2. Existing Context

**Project.** Alpha4Gate — StarCraft II Protoss bot with rule-based
strategy + PPO neural policy + Claude advisor. Python 3.12, uv,
burnysc2 v7.1.3, FastAPI + React dashboard. Production bot lives in
`bots/v0/`; versioning infrastructure (Phases 1–5) is complete.

**How self-play works today.**

- `src/orchestrator/selfplay.py` — `run_batch(p1, p2, games, map_name, ...)`
  is the entry point. It installs a port-collision monkey-patch for
  burnysc2 7.1.3, validates versions against the registry, and runs
  games serially inside an `asyncio` event loop.
- Each game calls `_run_single_game`, which builds a `GameMatch` with
  two `BotProcess` instances (one per version) and awaits
  `sc2.main.a_run_multiple_games`. Under the hood, burnysc2 spawns
  two `SC2_x64.exe` processes via `SC2Process`; each gets its own
  top-level Windows window with `fullscreen=False`.
- Per-game results append to `data/selfplay_results.jsonl`.
- `scripts/selfplay.py` is the CLI front-end: `--p1 v3 --p2 v5 --games 20
  --map Simple64 [--sample pfsp --pool v0,v1,v2,v3]`.

**Assets already in place.** [`img_backgrounds/`](../img_backgrounds/)
contains two SF2-themed PNGs (`protoss_themed_sf2_brazil_background.png`,
`protoss_themed_sf2_china_background.png`). The user plans to generate
more at 2560×1600.

**Windows platform constraints.**

- SC2 is DirectX, windowed when `fullscreen=False`. The process creates
  its own top-level `HWND`; we find it by enumerating top-level windows
  and matching `GetWindowThreadProcessId` against the `SC2Process.pid`.
  The window does not appear instantly — it takes 3–10s after spawn.
- `SetParent(hwnd, container_hwnd)` reparents; `SetWindowLong(GWL_STYLE,
  WS_CHILD | WS_VISIBLE)` converts it to a child window first.
- pywin32 provides `win32gui`, `win32con`, `win32process`.
- pygame gets its native window HWND via `pygame.display.get_wm_info()['window']`.

**Relevant memory.**

- [feedback_sc2_process_management.md](../../.claude/projects/alpha4gate-project/memory/feedback_sc2_process_management.md)
  — never kill `SC2_x64.exe`. Closing the container must detach the
  reparenting (`SetParent(hwnd, NULL)`), **never** terminate the
  process.
- [feedback_user_powershell.md](../../.claude/projects/alpha4gate-project/memory/feedback_user_powershell.md)
  — operator steps use PowerShell commands.
- [feedback_worktree_venv_incomplete.md](../../.claude/projects/alpha4gate-project/memory/feedback_worktree_venv_incomplete.md)
  — worktree-isolated build steps must `uv sync` **and** `uv pip install
  -e .[dev]` to get pytest/mypy/ruff available.

## 3. Scope

**In scope**

- pygame container window with themed PNG background
- Win32 reparenting of two SC2 windows into the container
- Hooks into `run_batch` / `run_single_game` to learn PIDs as games start
  and to detect game completion / SC2 crash
- Stats overlay (top-bar default, side-bar alternative): p1 vs p2 labels,
  game N of M, running W-L
- Random background default with `--background <key>` override
- Runtime hotkey toggles: `s` large/small size, `b` top/side bar
- Placeholder paint when an SC2 window disappears mid-match, with a
  small pool of flavor lines
- `--no-viewer` escape hatch on `scripts/selfplay.py`
- Soak observation (Step 9) — 10-game real self-play batch watched
  end-to-end on Windows to verify no orphan processes, no HWND leaks,
  crash placeholder works

**Explicitly out of scope**

- Dashboard integration (no React component, no API endpoint)
- Auto-launch from the Phase 6 daemon, the training loop, or
  `/improve-bot-advised`. CLI-invoked `scripts/selfplay.py` only.
- Multiple concurrent `run_batch` calls sharing one container. Each
  batch gets its own viewer window.
- Linux / macOS support. Viewer is Windows-only; `--no-viewer` is
  forced on other platforms with a log line.
- Advisor commentary overlay (deferred to a v2 pass once the basic
  container is stable)
- Elo / rating deltas on the overlay
- Killing, restarting, or otherwise managing SC2 processes from the
  viewer

## 4. Impact Analysis

| File / module | Change type | Nature |
|---|---|---|
| `src/orchestrator/selfplay.py` | **Extend** | Add optional `on_game_start(game_index, p1_pid, p2_pid)` and `on_game_end(result)` callbacks to `run_batch` and thread them into `_run_single_game`. Callbacks are no-ops when `None` (preserves existing callers). |
| `scripts/selfplay.py` | **Extend** | Construct a `SelfPlayViewer` unless `--no-viewer`; wire the viewer's callbacks into `run_batch`. Add flags: `--no-viewer`, `--background {brazil,china,random,...}`, `--bar {top,side}`, `--size {large,small}`. On non-Windows, force `--no-viewer` with an info log. |
| `src/selfplay_viewer/__init__.py` | **NEW** | Package init, re-exports public API (`SelfPlayViewer`, `run_with_viewer`). |
| `src/selfplay_viewer/container.py` | **NEW** | pygame window, layout engine, background loader, overlay renderer, event loop, hotkey handling. |
| `src/selfplay_viewer/reparent.py` | **NEW** | Win32 primitive: `attach_window(pid, container_hwnd, rect, timeout_s) -> hwnd`; `detach_window(hwnd)`; `move_window(hwnd, rect)`. Isolated so it can be unit-tested against `notepad.exe`. |
| `src/selfplay_viewer/backgrounds.py` | **NEW** | Enumerate `img_backgrounds/*.png`, derive key from filename (strip `protoss_themed_sf2_` / `_background` boilerplate, else use stem), pick random or by key. |
| `src/selfplay_viewer/overlay.py` | **NEW** | Stats bar renderer (top-bar + side-bar layouts), placeholder-paint renderer with message pool, font loading. |
| `src/selfplay_viewer/demo.py` | **NEW** | `python -m selfplay_viewer.demo` — opens the container with placeholders, no SC2, for visual smoke-testing during development. |
| `tests/test_reparent.py` | **NEW** | pytest marker `win32` — spawn `notepad.exe`, attach/detach/move, verify HWND belongs to that PID, verify `WS_CHILD` style flag set, cleanup on test teardown. |
| `tests/test_backgrounds.py` | **NEW** | Filename-to-key extraction, random selection stability under seed, unknown-key error path. |
| `tests/test_overlay.py` | **NEW** | Layout math — `large + top-bar`, `small + top-bar`, `large + side-bar`, `small + side-bar` produce expected container size and pane rects. Renders against an off-screen pygame surface, inspects pixels only for presence (not exact text). |
| `tests/test_selfplay_callbacks.py` | **NEW** | `run_batch` fires `on_game_start(idx, p1_pid, p2_pid)` and `on_game_end(result)` in order; both `None` is accepted (back-compat); exceptions in callbacks don't abort the batch. |
| `pyproject.toml` | **Extend** | Add `[project.optional-dependencies] viewer = ["pygame>=2.5", "pywin32>=306"]`. Keep base deps Linux-safe. `pytest` marker `win32` added to `[tool.pytest.ini_options]`. |
| `img_backgrounds/` | **Read-only** | No schema changes; directory contents are asset-only. |

No changes to `bots/v0/`, the Phase 3 port-collision patch, the dashboard,
`data/selfplay_results.jsonl`, the registry, or the Elo ladder.

## 5. New Components

### `src/selfplay_viewer/container.py` — `SelfPlayViewer`

Main class. Constructed with `(bar="top", size="large", background="random")`.
Owns the pygame window, layout state, callback queue, and event loop.

Public API:

```python
class SelfPlayViewer:
    def __init__(self, bar: str = "top", size: str = "large",
                 background: str = "random") -> None: ...

    # Callbacks to pass into run_batch. Thread-safe — internally push
    # events onto a queue drained on the pygame thread.
    def on_game_start(self, game_index: int, total: int,
                      p1_pid: int, p2_pid: int,
                      p1_label: str, p2_label: str) -> None: ...
    def on_game_end(self, result: SelfPlayRecord) -> None: ...

    # Main thread entry. Blocks until the batch completes OR the user
    # closes the window. Runs run_batch on a background thread.
    def run_with_batch(self, batch_fn: Callable[[], Coroutine]) -> Any: ...
```

Layout state machine handles the four combinations:

| bar × size | container W | container H | p1 rect | p2 rect | overlay rect |
|---|---|---|---|---|---|
| top + large | 2188 | 948 | (40, 140, 1024, 768) | (1124, 140, 1024, 768) | (0, 0, 2188, 100) |
| top + small | 2060 | 900 | (40, 140, 960, 720) | (1060, 140, 960, 720) | (0, 0, 2060, 100) |
| side + large | 2468 | 848 | (40, 40, 1024, 768) | (1124, 40, 1024, 768) | (2188, 0, 280, 848) |
| side + small | 2340 | 800 | (40, 40, 960, 720) | (1060, 40, 960, 720) | (2060, 0, 280, 800) |

Hotkeys:

- `s` — toggle large/small. Resizes the container window, recomputes
  rects, calls `MoveWindow` on both attached SC2 HWNDs.
- `b` — toggle top/side bar. Same recompute path.
- `Esc` or window-close — detach SC2 HWNDs via `SetParent(hwnd, NULL)`
  then quit pygame. Does NOT terminate SC2. The background `run_batch`
  thread continues (user can Ctrl-C the script).

### `src/selfplay_viewer/reparent.py`

```python
def find_hwnd_for_pid(pid: int, timeout_s: float = 15.0) -> int | None:
    """Poll EnumWindows filtered by GetWindowThreadProcessId == pid.
    Returns None on timeout. 100ms tick. Skips invisible / tool windows."""

def attach_window(hwnd: int, container_hwnd: int,
                  rect: tuple[int, int, int, int]) -> None:
    """Set WS_CHILD style, SetParent, MoveWindow. Idempotent."""

def move_window(hwnd: int, rect: tuple[int, int, int, int]) -> None:
    """Reposition an already-attached window."""

def detach_window(hwnd: int) -> None:
    """SetParent(hwnd, 0), restore top-level style. Never terminates the process."""
```

All functions marshal onto the caller thread; caller is responsible for
invoking them from the pygame main thread (enforced by assertion).

### `src/selfplay_viewer/backgrounds.py`

```python
BACKGROUND_DIR = Path(__file__).resolve().parents[2] / "img_backgrounds"

def list_backgrounds() -> dict[str, Path]:
    """Enumerate *.png; derive key via:
       stem = path.stem
       if stem starts with 'protoss_themed_sf2_' and ends with '_background':
           key = stem[len('protoss_themed_sf2_'):-len('_background')]
       else:
           key = stem
       Return {key: path}."""

def pick_background(key: str, rng: random.Random | None = None) -> Path:
    """'random' -> random.choice; else lookup. Raises KeyError on unknown."""
```

### `src/selfplay_viewer/overlay.py`

Top-bar: centered `{p1_label}  VS  {p2_label}` in large font (48pt),
sub-row `Game {n} / {total}   •   W-L: {p1_wins} - {p2_wins}` (24pt).

Side-bar: stacked version labels, vertical score, optional extension
hooks (advisor feed) laid out so v2 can add rows without a layout
rewrite.

Placeholder pane: full-pane semi-transparent dark overlay, centered
message from the pool:

```python
PLACEHOLDER_MESSAGES = [
    "{label} has rage-quit",
    "{label} is refusing to come out of the base",
    "{label} crashed into the void",
    "{label} forgot how to SC2",
    "{label} took a coffee break",
    "{label} surrendered to Brood War",
]
```

## 6. Design Decisions

### D1. Container framework — pygame

Considered: pygame, tkinter, PyQt/PySide, raw Win32 + GDI. pygame wins
because (a) themed PNG backgrounds and overlay text render cleanly with
`blit` and `Font.render`, (b) runtime resize / layout-swap is one `set_mode`
call, (c) the hotkey event model is trivial, (d) no native widget theming
fights. Cost: one extra dep, gated behind the `[viewer]` optional
dependency so Linux CI doesn't install Windows-only wheels. tkinter is
dep-free but its Canvas renders the themed background amateurishly. Qt
is overkill for a three-image window. Raw Win32 means writing GDI text
layout by hand.

### D2. Reparenting mechanism — Win32 `SetParent` with `WS_CHILD` style conversion

Only reliable cross-process window embedding path on Windows. The
alternative (DirectX swap-chain capture) would stream frames into the
pygame surface without reparenting but adds a full capture pipeline,
latency, and GPU load for zero gain — we don't need pixel-level access,
we just need layout. `SetParent` keeps SC2 rendering natively.

### D3. Threading — pygame on main thread, `run_batch` on background thread

Both pygame and Win32 want the main thread. `run_batch` is async and
runs happily inside `asyncio.run()` on a spawned `threading.Thread`.
Callbacks from the batch thread push `(event, payload)` tuples onto a
`queue.Queue`; the pygame loop drains the queue each frame and performs
all `SetParent` / `MoveWindow` calls itself. This sidesteps the
pygame-event-loop-from-bg-thread crash class and the pywin32-STA-apartment
class simultaneously.

### D4. Layout — top-bar default, 1024×768 panes, 40/60/40 margins, 100px stats bar

Top-bar chosen as default because it matches the SF2 "VS" screen
aesthetic (banner on top). Side-bar available via flag or hotkey. 1024×768
per pane is the only hard constraint from the user (SC2 readable at that
size). 40px outer margin + 60px inter-pane gutter is the tightest
spacing that still visually frames the panes as "two fighters." 100px
top bar leaves room for a large version label plus a sub-row, without
dominating the window. All numbers exposed as module constants so tuning
is a one-line edit.

### D5. Overlay v1 — minimal

Version labels, game N/M, running W-L score. Deferred to v2: advisor
commentary feed, per-game duration, map name, Elo deltas, spoken
commentary. Shipping minimal overlay first keeps the layout math clean;
v2 additions plug into the side-bar layout slot without rearranging.

### D6. Crash placeholder — SC2 disappears → paint placeholder, keep container alive

If a SC2 window vanishes (PID dead or HWND invalid on `IsWindow`) the
container paints the pane with a semi-transparent overlay + a random
flavor line. Pane rect stays reserved so the next game's SC2 window
slots into the same space. Alternative (close container on crash) was
rejected because the user wants to see the rest of the batch finish,
and the Phase 3 cleanup path already handles the orphan SC2 case at the
process level.

### D7. Close behavior — detach, never terminate

`Esc` or window-close triggers `SetParent(hwnd, NULL)` on both attached
SC2 windows, restoring them as top-level desktop windows. The `run_batch`
thread keeps running; user Ctrl-Cs the script to abort the batch. This
respects [feedback_sc2_process_management.md](../../.claude/projects/alpha4gate-project/memory/feedback_sc2_process_management.md)
and means closing the viewer is always safe.

### D8. Platform guard — Windows-only, `--no-viewer` forced elsewhere

`sys.platform != "win32"` → `scripts/selfplay.py` logs an info line and
runs as if `--no-viewer` were passed. No import of pywin32 on non-Windows.
The `[viewer]` optional dependency keeps Linux CI clean.

### D9. One viewer per `run_batch` call

Matches user's intent (each fighter pair gets its own window) and keeps
the design scope-bounded. A future multi-batch orchestrator can spawn N
viewers, each owning its own pair. No global window manager in v1.

### D10. Background image size — 2560×1600 native, composition guidance

User generates backgrounds at 2560×1600 (matches monitor native; pygame
downscales cleanly for smaller layouts). **Composition rule of thumb:**
the two SC2 panes occupy roughly `x ∈ [40, 2188]`, `y ∈ [140, 908]` in
top-bar large. Interesting art belongs in the outer ~200px frame and the
top 100px banner zone; the center will be mostly hidden. Think SF2 VS
screen — fighter portraits and stage details wrap around where the SC2
windows sit.

### D11. Background discovery — filename-to-key, random default

Enumerate `img_backgrounds/*.png`. Key extraction: strip
`protoss_themed_sf2_` prefix and `_background` suffix if both present;
else use stem. Accepts future drops like `japan.png`, `tokyo.png`
without code changes. `--background random` picks uniformly from the
discovered pool; `--background <key>` selects by derived key; unknown
key raises a clear `KeyError` listing the available options.

## 7. Build Steps

### Step 1: Scaffold pygame container + background loader
- **Status:** DONE (2026-04-18)
- **Problem:** Build the `selfplay_viewer` package skeleton: pygame
  window with configurable bar (top/side) and size (large/small), PNG
  background loading from `img_backgrounds/`, and a `demo.py` entry
  point that opens the window with placeholder grey rects where the SC2
  panes will go. No SC2, no reparenting. The layout math in D4 is
  authoritative. Add `pygame` and `pywin32` under a `[viewer]` optional
  dependency in `pyproject.toml` so Linux CI doesn't try to install
  them; add a `win32` pytest marker. Include `tests/test_backgrounds.py`
  (filename-to-key extraction, random selection, unknown key) and
  `tests/test_overlay.py` (layout math for all four bar × size
  combinations).
- **Issue:** #139
- **Flags:** --reviewers code --isolation worktree
- **Produces:** `src/selfplay_viewer/{__init__,container,backgrounds,overlay,demo}.py`,
  `tests/test_backgrounds.py`, `tests/test_overlay.py`, updated
  `pyproject.toml`.
- **Done when:** `python -m selfplay_viewer.demo` opens a 2188×948
  themed window on Windows with two grey placeholder rects at the
  correct positions and a visible stats bar stub. `uv run pytest
  tests/test_backgrounds.py tests/test_overlay.py` passes. `uv run ruff
  check src/selfplay_viewer tests/test_backgrounds.py
  tests/test_overlay.py` and `uv run mypy src/selfplay_viewer --strict`
  clean.
- **Depends on:** none

### Step 2: Win32 reparenting primitive (TDD)
- **Problem:** Implement `src/selfplay_viewer/reparent.py` with
  `find_hwnd_for_pid`, `attach_window`, `move_window`, `detach_window`.
  Use TDD: tests spawn `notepad.exe` via `subprocess.Popen`, wait for
  its window, attach it into a hidden test-owned parent HWND, verify
  the `WS_CHILD` style flag and correct parent, move it, detach, and
  verify it's restored to top-level. Tests are marked `@pytest.mark.win32`
  and skipped on non-Windows CI. Handle the 3–10s window-ready delay
  with a polling loop and a clear timeout error.
- **Issue:** #140
- **Flags:** --reviewers code --isolation worktree
- **Produces:** `src/selfplay_viewer/reparent.py`, `tests/test_reparent.py`.
- **Done when:** TDD cycle completes with all tests green on Windows.
  Notepad is spawned, attached to a test parent, moved, detached,
  verified restored, and cleaned up. Process never terminated by the
  reparent code. `uv run pytest tests/test_reparent.py -m win32` passes
  on a Windows dev box. `uv run ruff check src/selfplay_viewer/reparent.py
  tests/test_reparent.py` and `uv run mypy src/selfplay_viewer
  --strict` clean.
- **Depends on:** Step 1

### Step 3: Wire reparent primitive into the container
- **Problem:** `SelfPlayViewer` gains `attach_pane(slot, pid, label)`
  and `detach_pane(slot)` methods that look up the container's own
  HWND via `pygame.display.get_wm_info()['window']`, call
  `find_hwnd_for_pid` → `attach_window` with the correct pane rect
  from the layout table, store the HWND + label in viewer state, and
  repaint. The `s` and `b` hotkeys must call `move_window` on both
  attached HWNDs when the layout recomputes. Test manually by spawning
  two `notepad.exe` processes and attaching them via a dev-only
  `--attach-notepad-pids A,B` demo flag. No SC2 yet.
- **Issue:** #141
- **Flags:** --reviewers code --isolation worktree
- **Produces:** Updates to `src/selfplay_viewer/container.py`; possibly
  a small `tests/test_container_integration.py` with a notepad-based
  integration test (marked `win32`).
- **Done when:** `python -m selfplay_viewer.demo --attach-notepad-pids
  <A>,<B>` shows two notepad windows slotted into the container. `s`
  toggles large/small and both notepads resize with the container. `b`
  toggles top/side bar and both notepads reposition. Closing the
  container returns both notepads to desktop as top-level windows.
- **Depends on:** Step 2

### Step 4: Integrate with `run_batch` + `scripts/selfplay.py`
- **Problem:** Add optional `on_game_start(game_index, total, p1_pid,
  p2_pid, p1_label, p2_label)` and `on_game_end(result)` callbacks to
  `run_batch` in `src/orchestrator/selfplay.py`, threading through
  `_run_single_game`. Callbacks are no-ops when `None` (back-compat for
  existing callers). In `_run_single_game`, after the `BotProcess`
  objects are constructed and SC2 is spawned, extract each side's PID
  and fire `on_game_start`. On game exit, fire `on_game_end` with the
  `SelfPlayRecord`. Update `scripts/selfplay.py` to construct a
  `SelfPlayViewer` unless `--no-viewer`, and to pass its callbacks into
  `run_batch`. Add `--no-viewer` / `--background KEY` / `--bar
  {top,side}` / `--size {large,small}` flags. On non-Windows, force
  `--no-viewer` with an info log. Add `tests/test_selfplay_callbacks.py`
  verifying callbacks fire in order, `None` is accepted, and exceptions
  inside callbacks don't abort the batch. Verify end-to-end with a real
  SC2 1-game batch on Windows.
- **Issue:** #142
- **Flags:** --reviewers code --isolation worktree
- **Produces:** Updated `src/orchestrator/selfplay.py`, updated
  `scripts/selfplay.py`, `tests/test_selfplay_callbacks.py`.
- **Done when:** `python scripts/selfplay.py --p1 v0 --p2 v0 --games 1
  --map Simple64` launches the container with two real SC2 windows
  slotted inside; when the game ends the container stays up for a
  post-game beat then closes cleanly; `data/selfplay_results.jsonl`
  has one new record. `--no-viewer` path works identically to
  pre-plan behavior (no viewer, just SC2). `uv run pytest
  tests/test_selfplay_callbacks.py` passes. Existing Phase 3 tests
  (`tests/test_selfplay.py`, `tests/test_pfsp_sampling.py`,
  `tests/test_selfplay_transition_hand_off.py`) still pass.
- **Depends on:** Step 3

### Step 5: Overlay — version labels, game N/M, running W-L score
- **Problem:** Implement the overlay content renderer in
  `src/selfplay_viewer/overlay.py`. Top-bar variant: centered
  `{p1_label}  VS  {p2_label}` at 48pt in the top 60px of the bar;
  sub-row `Game {n} / {total}   •   W-L: {p1_wins} - {p2_wins}` at 24pt
  below. Side-bar variant: stacked version labels at top, vertical
  score below, leave a designated empty region for the v2 advisor
  feed. Use a bundled font (freesans or similar — ships with pygame,
  no new asset). Update `SelfPlayViewer` to repaint the overlay
  whenever `on_game_start` or `on_game_end` fires. Extend
  `tests/test_overlay.py` with a "rendered surface has non-background
  pixels in the overlay rect" assertion for each of the four layouts.
- **Issue:** #143
- **Flags:** --reviewers code --isolation worktree
- **Produces:** Updates to `src/selfplay_viewer/overlay.py`,
  `src/selfplay_viewer/container.py`, `tests/test_overlay.py`.
- **Done when:** `python -m selfplay_viewer.demo` shows the overlay
  populated with mock values. A real 2-game SC2 run (`python
  scripts/selfplay.py --p1 v0 --p2 v0 --games 2`) shows the W-L score
  update after game 1. `s` and `b` hotkeys repaint the overlay in the
  new layout with no missing text. `uv run pytest tests/test_overlay.py`
  passes.
- **Depends on:** Step 4

### Step 6: `--background` flag + random default
- **Problem:** Wire `--background {brazil,china,random,...}` in
  `scripts/selfplay.py` into `SelfPlayViewer(background=...)`. Default
  is `random`. Unknown key prints the list of available keys (derived
  from `img_backgrounds/`) and exits with code 2. `backgrounds.py`
  was already implemented in Step 1 — this step is wiring + CLI
  error-message quality + a couple of CLI-parsing tests.
- **Issue:** #144
- **Flags:** --reviewers code --isolation worktree
- **Produces:** Updates to `scripts/selfplay.py`, possibly
  `tests/test_selfplay_cli.py` for the argparse paths.
- **Done when:** `python scripts/selfplay.py --background brazil ...`
  uses the Brazil background every game. `--background random` picks
  a different one per run (deterministic under a `--seed` for testing).
  `--background nonsense` exits with code 2 and lists valid keys.
- **Depends on:** Step 1 (for the loader); Step 4 (for the CLI wiring)

### Step 7: Crash placeholder with flavor-message pool
- **Problem:** In `SelfPlayViewer`, poll each attached HWND each frame
  via `IsWindow(hwnd)` (or check `PROCESS_QUERY_LIMITED_INFORMATION` on
  the PID). When a pane's HWND becomes invalid, transition the pane to
  "placeholder" state: paint a semi-transparent dark overlay + a random
  message from `PLACEHOLDER_MESSAGES` (format with the slot's label).
  Placeholder holds until the next `on_game_start` fires, at which
  point the new SC2 HWND is attached and normal rendering resumes.
  Add `tests/test_placeholder.py` with a headless pygame surface: stub
  an "invalid HWND" state, render, assert dark overlay pixels + message
  text rendered.
- **Issue:** #145
- **Flags:** --reviewers code --isolation worktree
- **Produces:** Updates to `src/selfplay_viewer/overlay.py` and
  `container.py`, `tests/test_placeholder.py`.
- **Done when:** `uv run pytest tests/test_placeholder.py` passes
  (headless pygame surface renders the placeholder pixels + message
  text when the pane state is stubbed to "invalid HWND"). Code review
  confirms the HWND-validity polling runs in the pygame main loop on a
  ~500ms tick, placeholder state transitions on first detected invalid
  HWND, and new `attach_pane` calls correctly replace the placeholder
  with the live window. Runtime validation of the kill-mid-game flow
  is deferred to Step 9 checkpoint (e).
- **Depends on:** Step 5

### Step 8: Runtime resize / bar-toggle polish
- **Problem:** `s` and `b` hotkeys already work from Step 3. This step
  is the polish pass: (a) ensure pygame `set_mode` + `MoveWindow` are
  sequenced correctly so the container and both SC2 panes stay
  coherent during the resize (no one-frame tearing of HWND positions),
  (b) add a brief (~200ms) on-screen toast like "Large layout" /
  "Side bar" so the user knows the hotkey fired, (c) preserve
  placeholder state across resize (a placeholder'd pane stays
  placeholder'd post-resize).
- **Issue:** #146
- **Flags:** --reviewers code --isolation worktree
- **Produces:** Updates to `src/selfplay_viewer/container.py`.
- **Done when:** Code review confirms: (a) `set_mode` and `MoveWindow`
  are sequenced inside a single pygame frame so HWND positions never
  tear, (b) a toast overlay with a 200ms fade is implemented in the
  overlay renderer and covered by a unit test that asserts toast
  pixels render in the expected rect, (c) the placeholder state
  dictionary survives the layout recompute path (unit-tested with a
  stubbed placeholder state + forced resize). Runtime validation of
  mid-game resize is deferred to Step 9 checkpoint (f).
- **Depends on:** Step 7

### Step 9: Soak observation — 10-game real self-play, watched end-to-end
- **Problem:** Windows-only OS-integration smoke gate. Run `python
  scripts/selfplay.py --p1 v0 --p2 v0 --games 10 --map Simple64` with
  the viewer on and **watch it end to end**. Check for: (a) both SC2
  windows correctly slotted in each of the 10 games, (b) overlay W-L
  score updates correctly after each game, (c) no orphan `SC2_x64.exe`
  processes in Task Manager after the batch completes, (d) no
  accumulating HWND leaks over 10 reparent cycles (Task Manager →
  Details → User Objects column on the python.exe process stays flat,
  within +/- 20 from start), (e) deliberately kill one SC2 process
  mid-game via Task Manager during game 5 — verify placeholder
  triggers, game 5 result is logged cleanly, game 6 spawns fresh
  windows and reparents them into the same panes, (f) hit `s` and `b`
  twice each during a game — no crashes or layout corruption, (g)
  close the container during game 8 via `Esc` — both SC2 windows
  should return to top-level, `run_batch` thread should continue and
  finish games 8-10 with no viewer, `data/selfplay_results.jsonl`
  should have 10 records at the end. Record observations in a
  `documentation/soak-2026-04-XX-selfplay-viewer.md` file.
- **Issue:** #147
- **Type:** operator
- **Flags:** (none — manual observation step)
- **Produces:** `documentation/soak-<date>-selfplay-viewer.md` with
  pass/fail for each checkpoint plus any surprises.
- **Done when:** All 7 checkpoints (a–g) pass in a single run OR a
  triage doc lists each failure with a follow-up issue number.
- **Depends on:** Step 8

## 8. Risks and Open Questions

| Item | Risk | Mitigation |
|---|---|---|
| SC2 window ready-delay | The 3–10s gap between `SC2Process` spawn and window appearance could race callbacks | `find_hwnd_for_pid` polls with 15s timeout; on timeout, pane shows placeholder instead of crashing |
| DirectX surface inside child window | Reparenting a DirectX window via `SetParent` has known quirks on some GPU drivers | Soak step 9 checks all 10 games; if rendering glitches appear, fall back to window-capture + texture streaming (larger design change, defer to v2) |
| HWND leak from repeated attach/detach | 10+ reparent cycles over a long batch could leak GDI/User handles | Step 9 explicitly checks User Objects in Task Manager over 10 cycles |
| `pygame.display.get_wm_info()` surface recreation on `set_mode` | pygame may replace the container HWND on `s`/`b` resize, invalidating attached SC2 parent pointers | Step 8 validates this sequence; if pygame recreates the HWND, resize path must re-parent both panes into the new HWND |
| Multi-monitor DPI scaling | Per-monitor DPI on Windows can make pixel-coordinate math wrong | User runs 2560×1600; if scaling issues arise, call `SetProcessDpiAwarenessContext` at startup |
| Background image aspect mismatch | Future drops at non-2560×1600 could look stretched | `backgrounds.py` respects source aspect ratio; layout engine letterboxes rather than stretches. Document this in the loader docstring |
| Phase 3 callback integration touches frozen contracts | `_run_single_game` signature change ripples to other callers | Callbacks are `Optional[Callable] = None`; existing callers unchanged. Step 4 explicitly reruns the Phase 3 test suite |
| Linux/macOS CI imports | `pywin32` wheels don't exist for non-Windows | `[viewer]` optional dep + `sys.platform` guard in `scripts/selfplay.py` + `@pytest.mark.win32` for all viewer tests |

**Open questions (answerable during the build, not blocking):**

- Does SC2's DirectX swap-chain cope with arbitrary parent HWND sizes
  below 1024×768? Step 8 will tell us whether small-mode SC2 panes at
  960×720 actually render or go black. If they fail, the small preset
  becomes 1024×768 in a tighter container layout.
- Should the placeholder include a snapshot of the last rendered frame
  of the dead SC2 window? Nice-to-have, requires GDI bit-blit of the
  last HWND state before it dies. Defer to v2 unless cheap.
- Do we want a hotkey to take a screenshot of the full container?
  Handy for sharing but trivial to add post-v1 (`F12` → `pygame.image.save`).

## 9. Testing Strategy

**Unit tests (Linux-CI-safe, no pywin32 import):**

- `tests/test_backgrounds.py` — filename-to-key extraction, random
  selection under a seed, unknown key raises with available-keys
  listed.
- `tests/test_overlay.py` — layout math for all four `bar × size`
  combinations produces expected container size and pane rects;
  overlay surface rendering produces non-background pixels in the
  overlay rect.
- `tests/test_placeholder.py` — headless pygame surface, stub
  "invalid HWND" state, render assert dark overlay + message.
- `tests/test_selfplay_callbacks.py` — `run_batch` fires callbacks in
  order with correct args, `None` callbacks are accepted, callback
  exceptions don't abort the batch.
- `tests/test_selfplay_cli.py` — argparse accepts / rejects flag
  combinations; unknown background key exits 2 with a useful message.

**Windows-only integration tests (`@pytest.mark.win32`):**

- `tests/test_reparent.py` — notepad spawn / attach / move / detach
  cycle; `WS_CHILD` flag set; process never terminated.
- `tests/test_container_integration.py` (optional) — two notepads
  attached via `--attach-notepad-pids` demo path; `s` / `b` hotkeys
  resize both.

**Manual observation (Step 9):**

- 10-game real self-play batch watched end-to-end on the user's
  Windows machine. Seven-checkpoint script (a–g in Step 9). Failures
  logged in `documentation/soak-<date>-selfplay-viewer.md`.

**Regression safety for Phase 3:**

- Step 4 must re-run `tests/test_selfplay.py`,
  `tests/test_pfsp_sampling.py`, and
  `tests/test_selfplay_transition_hand_off.py` to confirm the callback
  wiring didn't break the existing batch runner. These tests exist
  today and must stay green.

**CI strategy:**

- Linux CI runs the full unit suite with `viewer` deps **not**
  installed. Viewer modules must `import pygame` / `import win32gui`
  lazily (inside functions or guarded by `sys.platform == "win32"`)
  so the package imports cleanly on Linux.
- Windows dev-box runs the `win32`-marked tests manually before the
  Step 9 soak.

## Appendix — Post-v1 extensions (reference, not scope)

1. **Advisor commentary overlay.** Subscribe to `/ws/commands` or the
   advisor bridge, render the last N advisor messages as a scrolling
   side-bar feed.
2. **Phase 6 auto-launch.** When the cross-version self-play daemon
   runs, optionally spawn a viewer so the user can attach and watch at
   any time without interrupting the loop.
3. **Elo / rating deltas on overlay.** Read `data/bot_ladder.json` at
   game start and show the current Elo for each side + projected delta.
4. **Match replay mode.** Given a `.SC2Replay` path + result record,
   play back the game inside the container using SC2's built-in
   replay. Same reparent primitive, different spawn path.
5. **Screenshot hotkey (`F12`).** `pygame.image.save(full_surface,
   "selfplay_<timestamp>.png")`.
6. **Per-match background selection.** Random per game instead of per
   batch; opt-in flag.
