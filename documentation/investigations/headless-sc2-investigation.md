# Phase 4.6 Step 6 — Headless SC2 Investigation

**Date:** 2026-04-10
**Worktree:** `worktree_build-step-phase4-6-step6-1775897729`
**Branch base:** master @ `71a94db`
**burnysc2 version:** v7.1.3 (`./.venv\Lib\site-packages\sc2`)
**Question:** Can SC2 be run fully headless (no graphics rendering) to reduce GPU contention, eliminate focus issues, and potentially speed up soak runs?

**TL;DR:** Not feasible on Windows via burnysc2 v7.1.3 without patching the library. The public API (`run_game`, `run_multiple_games`, `SC2Process.__init__`) does **not** expose a launch-arg pass-through, and even the internal `-eglpath` flag that burnysc2 does append is a Linux-only Mesa path. No code committed. **Recommendation: not feasible / not worth it within current dependency surface — defer.**

---

## 1. burnysc2 API surface

### `sc2.main.run_game` — no extra-args seam

`run_game` (src: `.venv/Lib/site-packages/sc2/main.py:473`) accepts:
- `map_settings`, `players`, `realtime`, `portconfig`, `save_replay_as`
- `game_time_limit`, `rgb_render_config`, `random_seed`, `sc2_version`, `disable_fog`

No `args`, `extra_args`, `launch_args`, `sc2_args`, or `sc2_config` kwarg. The function forwards a fixed allow-list to `_host_game`, which constructs `SC2Process(fullscreen=..., render=rgb_render_config is not None, sc2_version=...)`.

### `sc2.main.run_multiple_games` — `sc2_config` exists but is narrow

`GameMatch.sc2_config: list[dict] | None` (`main.py:54`) is the **only** documented per-process kwarg pass-through. It unpacks into `SC2Process(**proc_args)` in `maintain_SCII_count` (`main.py:691`). So whatever `SC2Process.__init__` accepts is the effective surface — nothing else.

### `sc2.sc2process.SC2Process.__init__` — fixed kwargs only

Signature (`sc2process.py:57-68`):
```python
def __init__(
    self,
    host: str | None = None,
    port: int | None = None,
    fullscreen: bool = False,
    resolution: list[int] | tuple[int, int] | None = None,
    placement: list[int] | tuple[int, int] | None = None,
    render: bool = False,
    sc2_version: str | None = None,
    base_build: str | None = None,
    data_hash: str | None = None,
) -> None:
```

The subprocess CLI is hardcoded in `_launch` (`sc2process.py:148-212`). The only args written to `self._arguments` are `-displayMode`, `-windowwidth`, `-windowheight`, `-windowx`, `-windowy`. The only conditional-add is:

```python
if self._render:
    args.extend(["-eglpath", "libEGL.so"])
```

Two things to note:
1. `render=True` **enables** rendering (the opposite of headless) and adds a Linux-only `libEGL.so` path.
2. There is no code path that appends anything like `-HeadlessNoRender`, `-norender`, `-noGraphics`, etc.

**Verified by grep on the entire `sc2` package:** `headless|HeadlessNoRender|no.render|norender|eglpath` — zero matches for headless/norender; only one match for `eglpath` (the one above).

### No monkey-patchable seam

`SC2Process._launch` builds `args` as a local list, shells `subprocess.Popen(args, ...)`, and returns. There is no instance variable we can mutate post-init to inject extra CLI args; the only way to add a flag without forking burnysc2 is to monkey-patch `SC2Process._launch` wholesale or append to `self._arguments` before `__aenter__` runs (which is impractical because `run_game` constructs the process internally).

---

## 2. SC2 CLI flags

**Could not verify empirically** — the investigation task prohibits actually launching SC2, and the Blizzard client does not ship a `--help` output file in `C:\Program Files (x86)\StarCraft II\Versions\Base95841\` (only `SC2.exe` and `SC2_x64.exe` are present; no `.txt` or `.md` help docs).

**What the community has documented (context only, not verified in this worktree):**
- `-HeadlessNoRender` is a flag referenced in **pysc2** (DeepMind's Linux-centric SC2 wrapper), not burnysc2. It requires the Linux SC2 Linux Package (`SC2.x86_64`) which Blizzard ships separately for pysc2 work.
- The retail **Windows** SC2 client (the one installed at `C:\Program Files (x86)\StarCraft II\`) is a DirectX/Windowed app. There is no public documentation that `-HeadlessNoRender` is honored by the Windows `SC2_x64.exe` retail binary.
- The closest window-management options burnysc2 does wire up are `-displayMode 0` (windowed) and window placement — none of which reduce GPU rendering load; the game still renders to the window.

**Bottom line:** even if we found a seam to pass `-HeadlessNoRender` to the Windows SC2 retail binary, there's no documented evidence it would be honored.

---

## 3. Alpha4Gate launch sites

Two call sites invoke `sc2.main.run_game`:

1. **`src/alpha4gate/connection.py:89`** — `run_bot()`, the production path used by `runner.py` for single-game runs (`runner.py:283`, `runner.py:361`).
2. **`src/alpha4gate/learning/environment.py:409`** — `SC2Env._run_game_thread()`, the trainer's per-cycle launch (driven from `_sync_game` which runs `run_game` on a background thread).

Both call `run_game` with a narrow positional+kwarg surface: `map_settings`, `players=[Bot(...), Computer(...)]`, `realtime`, `save_replay_as`. Neither site has a `fullscreen=False` override — burnysc2's `Bot.__init__` already defaults to `fullscreen=False`, which gets us windowed mode but **not** headless.

There is no existing Alpha4Gate config seam for SC2 process arguments. `alpha4gate.config.Settings` exposes `sc2_path`, `replay_dir`, etc. — all filesystem paths, no launch-flag knobs.

---

## 4. Clean-wiring assessment

**There is no clean wiring path in burnysc2 v7.1.3.** Any attempt to make SC2 headless from Alpha4Gate would require one of:

**Option A — Fork/patch burnysc2.** Add an `extra_args: list[str] | None = None` kwarg to `SC2Process.__init__` and `_host_game` / `run_game`, then thread it through. Estimated diff: ~40 lines across 2 files in the library. Blocked by:
- Introduces a dependency fork (either vendor burnysc2 or maintain a patch).
- No evidence the resulting `-HeadlessNoRender` flag is honored by the Windows retail SC2 binary, so the patch may buy us nothing.
- Violates the "do NOT add dependencies" constraint in the step brief.

**Option B — Monkey-patch `SC2Process._launch` in Alpha4Gate.** Replace the method at import time with a version that appends extra args. Estimated ~25 lines. Blocked by:
- Same "flag probably doesn't work on Windows" risk.
- Brittle: any burnysc2 upgrade silently breaks it.
- Hard to cover with a mockable unit test (the subprocess call is at the bottom of a 60-line method).

**Option C — Environment-based launch (windowed-min-size).** Pass `resolution=(320, 240)` via `SC2Process`'s existing kwargs. This **does not eliminate rendering** — SC2 still runs its graphics pipeline — but it reduces pixel count. Blocked by:
- Not actually headless; doesn't address GPU contention or window-focus issues.
- Would still require a seam in `run_game` to plumb `resolution` through (currently unreachable from outside `SC2Process`).

**None of A/B/C meet the "clean wiring, <20 lines, feature-flagged, mockable test" bar in the step brief.**

---

## 5. Measured speedup

Not prototyped in this investigation. The task brief explicitly prohibits launching SC2, and all three candidate paths above would require an end-to-end run against a real SC2 process to measure speedup (unit tests can only verify the flag is *forwarded*, not that SC2 honors it).

**If a future investigation did prototype this**, here's what I'd capture:
- **Baseline:** 5 consecutive `runner --batch 1 --difficulty 1` cycles with current launch; record `game_duration_sec` from `data/stats.json`.
- **Candidate:** same command with `-HeadlessNoRender` appended (via monkey-patch or fork); same N=5.
- **Metrics:** mean/median game duration, GPU utilization (via `nvidia-smi --query-gpu=utilization.gpu --format=csv` logged every 1s), window focus events (manually observed).
- **Null hypothesis:** no difference on Windows retail (flag ignored).

---

## 6. Recommendation

**Not feasible / not worth it within current dependency surface.**

Rationale:
1. burnysc2 v7.1.3 does not expose a pass-through for custom SC2 launch args. Making one requires a library fork or a monkey-patch — both fail the "clean wiring" bar.
2. The supposed `-HeadlessNoRender` flag is a pysc2/Linux artifact; there's no evidence the Windows retail SC2 binary honors it. Without empirical verification (which the task prohibits), any patch is speculative.
3. The original motivation — speeding up eval games (~6 min each) — was misdiagnosed. Phase 4.6 Step 1 already identified that eval slowness was a **teardown bug** (`game_id` reuse across cycles causing hangs), not rendering cost. Training cycles already run at ~12-15 sec with `realtime=False`, which is API-speed — there is no rendering-cost headroom to recover there either.
4. The real wins — GPU contention, window-focus issues, screen-off soaks — can be addressed today with existing Windows tooling: (a) minimize the SC2 window (OS-level, no code change), (b) run soak under a separate user session via `psexec -s`, or (c) use Windows "focus assist" mode during soak windows. None of these need a code change.

**If a future engineer wants to revisit this:** the cheapest next step is a one-off `scripts/try_headless_monkey_patch.py` that monkey-patches `SC2Process._launch` to append `-HeadlessNoRender`, launches a single Simple64 game via `connection.run_bot`, and prints wall-clock + whether the game completes. That's a 30-minute empirical check that answers "does Windows retail SC2 honor the flag?" without touching production code. Keep it out of `src/` and out of CI.

---

## 7. Windows caveats

1. **burnysc2's existing `-eglpath libEGL.so` is Linux-only.** It's only appended when `render=True` (i.e. when an RGB render config is passed). On Windows it would fail silently or be ignored — it's a Mesa/OpenGL ES library path. This confirms burnysc2's headless/render code path was authored with Linux in mind, and there's no Windows-equivalent wired up.

2. **Battle.net launch interaction:** burnysc2 launches `SC2_x64.exe` directly from `Versions/Base<build>/` (`sc2process.py:153`), bypassing Battle.net entirely. That's good — no Battle.net interaction to worry about. But it also means any CLI flags we add must be understood by `SC2_x64.exe` directly, and we have no docs for its arg surface on Windows.

3. **No admin-rights requirement** for existing burnysc2 launches; adding headless flags should not require elevation.

4. **DirectX rendering:** the Windows retail SC2 binary renders via DirectX to a window (or fullscreen). There is no documented Windows-side "null renderer" backend analogous to Linux's EGL-less mode.

---

## Code change committed?

**No.** Report only. No clean-wiring path meets the step's commit criteria.

## Files referenced

- `./.venv/Lib/site-packages/sc2/main.py` (lines 473, 54, 691)
- `./.venv/Lib/site-packages/sc2/sc2process.py` (lines 57-68, 148-212)
- `./.venv/Lib/site-packages/sc2/paths.py` (lines 90-102)
- `~/dev/worktree_build-step-phase4-6-step6-1775897729/src/alpha4gate/connection.py` (line 89)
- `~/dev/worktree_build-step-phase4-6-step6-1775897729/src/alpha4gate/learning/environment.py` (line 409)
