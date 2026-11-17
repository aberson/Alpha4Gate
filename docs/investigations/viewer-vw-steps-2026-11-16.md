# VW.0 plan: strength viewer build steps (VW.1–VW.3)

Date: 2026-11-16. Author: lane evolve-demo, via `/plan-feature`. State: **REVIEW-READY**
(for main to fold into `docs/plans/master_plan.md`; not reviewed, redlined, wrapped or synced).
Parent: master plan Step VW.0, decisions D7, D10, D16 (§16). Seed:
[strength-viewer.md](../seeds/strength-viewer.md). Supersedes the held direction draft
[viewer-plan-2026-11-16.md](viewer-plan-2026-11-16.md), which is kept as background; this
plan reuses its source survey and simplifies its design (see Design decisions).
Baseline: branch `master-plan/phase-ev`, HEAD `aa3b7df`, dirty shared tree (other lanes'
changes untouched). No game, paid call or build ran while planning.

## 1. What this feature does

The operator starts one bot version against the built-in AI, or two bot versions against
each other, in one Observer Rush-style window on Windows: native SC2 beside a decision
panel showing what each bot is doing (strategic state, army target, win probability). When
the operator sees a behavior worth fixing, they press Flag, and the moment (version, game
time, replay, note) reaches the improvement proposer through ES.10. Watching games is how
issues get spotted; this is an input to strength work, kept small.

## 2. Existing context

| Source | What it gives | Consequence |
|---|---|---|
| `scripts/probes/jev_battle_view.py` (908 lines, lane repair) | Proven native host: a pygame window with Edge (Playwright, `--app=` URL) attached as a child (`attach_window`, ~l.487) and SC2 kept as an owned popup at a fixed offset (`pump_native_events`, ~l.371); page-to-host bridge `page.expose_function("battleAction", ...)` (l.482); readiness barrier: host touches `<evidence>/ready` after the page shows the exact run, game child waits up to 60 s (`ready`, l.230); stop via `control.json`. It also hard-requires OBS on port 4457, the Jev runner and the caster | Copy the hosting pattern into a new module; do **not** import or change the probe (repair owns it). OBS, caster and Jev graph are not reused |
| `frontend/src/components/JevBattleView.tsx` (183 lines), `frontend/src/main.tsx:9` | `?battle=1` route; page polls `window.battleAction("status")` every 1 s (l.89-96) | New sibling route and component; same polling-through-bridge shape |
| `src/jev/camera.py` (`CameraDirector`, `camera_subjects(own, enemies, tasks, home)`) | Presentation-only auto camera; `tasks` is a Jev `Task` tuple, empty is valid | Reuse with `tasks=()`; importing `jev.camera` pulls only `jev.contracts` |
| `src/selfplay_viewer/reparent.py` (`attach_window`, `detach_window`, `find_hwnd_for_pid`) | Win32 window hosting helpers, already used by the probe | Reuse unchanged |
| `bots/vN/__main__.py` (all v0–v13) | `--role solo --map M --difficulty N [--realtime]` runs `runner._run_single_game(start_server=False)`; `--role p1/p2` plus `--GamePort/--LadderServer/--StartPort` attaches to a hosted match | The viewer runs each bot through its own unmodified `__main__` |
| `bots/vN/bot.py` (all v0–v13; grep 2026-11-16) | Every ~11 steps: `entry = observe(self, ...)`; `entry["strategic_state"] = state.value`; `queue_broadcast(entry)` (v13 l.537-551). `_resolve_attack_rally(...)` returns the ATTACK rally or target point (all trees). `winprob_heuristic.score(snapshot)` in `on_step` (v13 l.395; present v0, v3–v13, **absent v1, v2**) | Passive telemetry: wrap these three names inside the player process and record their inputs/returns unchanged. No frozen-tree edit, no recomputation |
| `bots/v13/config.py:57-74` | `LOG_DIR`, `REPLAY_DIR`, `DATA_DIR` environment overrides | Per-run isolation: the training DB, reward logs and replays of a viewer game go under the run folder |
| `src/orchestrator/sc2_compat.py` (`install`) | Fixes build-98310 ability ids; v13 and Jev install it at import, v0–v12 do not | Player wrapper calls `install()` before importing `bots.<vN>` |
| `src/orchestrator/selfplay.py` (lane evolve): `run_batch(p1, p2, games, map_name, *, game_time_limit, hard_timeout, seed, results_path, on_game_start, on_game_end, stop_event)` (l.590); `_build_bot_process` (l.260) builds `launch_list=[python, -m, bots.<v>, --role, ...]`; `_build_match` uses `realtime=False` (l.330); `OnGameStart` gives two SC2 PIDs whose pairing with p1/p2 is documented as **not authoritative** (l.40-52) | Dual mode reuses `run_batch` with one opt-in launch prefix; pane-to-player pairing must be established, not assumed |
| `frontend/src/components/EvolutionTab.tsx:1401-1403, 1623` | Stop Run sends `stop_run: true`; no runner reads it (D7) | Replace with the real instruction (direct task, below) |
| Master plan ES.10 | Planned `uv run python scripts/scoreboard.py flag --version vN --replay PATH --game-time MM:SS --class execution\|information\|composition\|strategy\|infrastructure [--tactical T.n] --note TEXT`; not implemented (`scripts/scoreboard.py` absent) | VW.3 calls that command unchanged and depends on ES.10 |
| Operator display (measured 2026-11-16) | One monitor, 3440×1440, work area 3440×1392, 100% scale | Both layouts must fit 3440×1392 |

Operator speed preference: watching runs at the default non-realtime speed (fast); real
time is opt-in only.

## 3. Scope

**In:** single layout (one version vs the built-in AI), dual layout (two versions, one
match), passive per-player telemetry, decision panel per player, auto camera with manual
unlock (single layout), owned Stop for single matches, detach for dual, Flag into ES.10,
older-version compatibility through the shim, `-CheckOnly` launcher.

**Out:** showing headless/WSL evolve games; a third observer client; graph or 3D work;
caster, recording and OBS; PPO action probabilities and hybrid/neural decision modes
(rules mode only in this cut); seed-controlled matches; scoring Windows games as
scoreboard results; changes to the Jev probe, `JevBattleView.tsx`, frozen bot trees, or
the old pygame container's behavior for `scripts/selfplay.py` and `evolve --viewer`.

## 4. Impact analysis

Search root: `src`, `scripts`, `bots`, `tests`, `frontend/src` (excluding `node_modules`).

| File or interface | Change | Reason | Verified evidence |
|---|---|---|---|
| new `src/selfplay_viewer/strength/` (`player.py`, `telemetry.py`, `host.py`, `__main__.py`) | New subpackage | Lives in an already-packaged module (`pyproject.toml:66`, mypy `:97`), so no `pyproject.toml` change; pygame/win32 imported lazily like the parent package | `src/selfplay_viewer/__init__.py`; no `strength_viewer` name exists today |
| new `scripts/launch-strength-viewer.ps1` | Operator entry, `-CheckOnly` | One front door like `launch-jev-battle.ps1` | launcher precedent read |
| `frontend/src/main.tsx` | One more route branch (`?strength=1`) | Page selection lives here | `main.tsx:9`; `?battle=1` stays as is |
| new `frontend/src/components/StrengthViewer.tsx` + `.test.tsx` | New page | Decision panel(s), Stop, Flag | — |
| `src/orchestrator/selfplay.py` `_build_bot_process`, `_build_match`, `run_batch` | Add keyword-only `launch_prefix: list[str] \| None = None`, threaded to `_build_bot_process`; `None` keeps today's `launch_list` byte-identical | Dual mode must run each bot through the player wrapper | Consumers: `_build_match` (l.320, 323), `run_batch` (l.685), `scripts/spike_subprocess_selfplay.py:131` (its own local `_build_match`, unaffected); callers of `run_batch`: `scripts/selfplay.py`, `scripts/evolve.py`, `src/orchestrator/evolve.py` (unchanged, omit the new keyword). **Owned by lane evolve**: main assigns or evolve lands it |
| `frontend/src/components/EvolutionTab.tsx` | Stop Run button → static instruction | D7 | l.1401-1403, 1623 |
| `src/selfplay_viewer/{container,overlay,demo}.py` | None | Old container stays for legacy callers | operator decision 1 |

No new third-party dependency: pygame-ce, pywin32 and psutil come from the `[viewer]` extra;
Playwright is used the way Observer Rush uses it (`uv run --with playwright`).

## 5. New components

- `selfplay_viewer.strength.player`: `python -m selfplay_viewer.strength.player --version vN --run-dir DIR --seat solo|p1|p2 -- <bot __main__ args>`. Sets `DATA_DIR`/`LOG_DIR`/`REPLAY_DIR` under `DIR/<seat>/`, copies the tree's read-only config files (not `training.db`) into that data dir, calls `sc2_compat.install()`, wraps the three bot seams, then calls `bots.<vN>.__main__.main(args)`. Solo seat also reads `control.json` (stop, camera) each step.
- `selfplay_viewer.strength.telemetry`: versioned run manifest (run id, mode, versions, map, difficulty/race, seat map, SC2 build, replay path/status) and per-seat `latest.json` (atomic replace) plus a bounded `events.jsonl`; each field is present or marked `not recorded` per version (v1/v2: no win probability).
- `selfplay_viewer.strength.host`: native window (single 2160×1200 like Observer Rush; dual 3440×1360), Edge page, SC2 owned popups, `strengthAction` bridge, ready barrier, Stop/detach, flag queue.
- `StrengthViewer.tsx`: one or two decision panels: version and seat, game time, strategic-state timeline, army target, win probability, last update age; Stop (single), Flag.

## 6. Design decisions

| Choice | Decision | Provenance | Rationale | Alternatives / unresolved |
|---|---|---|---|---|
| Dual-layout host | One new Observer Rush-style window for both layouts; old pygame container untouched | **operator**, 2026-11-16 | One look; avoids repairing the old container | Extending `src/selfplay_viewer` container (seed text), rejected |
| Flag path | Call ES.10's `scoreboard.py flag` unchanged; VW.3 depends on ES.10 | **operator**, 2026-11-16 | One findings writer; no schema extension | Local flags now + later forwarding step, rejected |
| Telemetry source | Wrap `queue_broadcast`, `_resolve_attack_rally`, `winprob_heuristic.score` in the player process, passing inputs and returns through unchanged | agent default | Same seams exist in v0–v13; no frozen edits, no recomputation | Telemetry bridge over the dashboard WebSockets (process-local queues, rejected) |
| Page data path | Through the Playwright bridge (`strengthAction("status")`), not HTTP | agent default | No new API router in `bots/v13/api.py`, no loopback auth surface | `/api/...` router like `/api/jev` |
| Page hosting | Vite dev server on :3000 via `launch-a4g.ps1 -NoBrowser -NoWait`, as Observer Rush does | agent default | Proven path | — |
| Native host code | Copy-and-trim from the Jev probe into `strength/host.py` | agent default | Repair owns the probe; no cross-lane edit. Known duplication, recorded for later convergence with repair | Extract a shared host module (needs repair) |
| Decision mode | Rules only | agent default | Default games run rules; PPO display is out of scope | Hybrid with probabilities, later |
| Game speed | Non-realtime by default; `-Realtime` switch for single | agent default, per operator speed preference | Faster watching | — |
| Isolation | Per-run `DATA_DIR`/`LOG_DIR`/`REPLAY_DIR`, read-only config seeded from the tree | agent default | No writes into `bots/vN/data` training DBs | — |
| Pane-to-player pairing (dual) | Derived from a recorded per-player SC2 identity inside the dual child, never from PID start order | agent default, mechanism proven in VW.2 | `OnGameStart` pairing is not authoritative | If unprovable: label panes "pairing unverified" and stop before claiming per-side strips |
| Stop | Single: owned Stop leaves the match normally. Dual: closing detaches; the one match runs to its time limit; no second match starts | seed scope cap | Matches D7; no process kill | — |
| Stop Run button (D7) | Replace with "To stop an evolve run, close its console window." | agent default (seed allows remove or replace) | Tells the truth | Remove outright |

## 7. Build steps

### Step VW.1: Watch one bot version against the built-in AI with its decisions
- **Problem:** The operator cannot watch a chosen bot version play the built-in AI beside a live panel of its own recorded decisions.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Files:** new `src/selfplay_viewer/strength/{__init__,__main__,player,telemetry,host}.py`, new `scripts/launch-strength-viewer.ps1`, `frontend/src/main.tsx`, new `frontend/src/components/StrengthViewer.tsx` + `StrengthViewer.test.tsx`, new `tests/test_strength_viewer.py`
- **Produces:** `powershell -File scripts/launch-strength-viewer.ps1 -Version v13 -Difficulty 3 -Race Terran -Map Simple64 [-Realtime] [-CheckOnly]` opens one 2160×1200 window: native SC2 plus a panel with version, game time, strategic-state timeline, army target and win probability, updating while the game runs. Stop leaves the match normally; the run folder `data/strength_viewer/runs/<run_id>/` holds the manifest, telemetry, replay and that seat's isolated data dir.
- **Done when:** a test runs the production player entry (`python -m selfplay_viewer.strength.player ... --seat solo`) with only the SC2 boundary replaced and shows: the shim is installed before the tree import; the three wrapped seams return exactly the original values; `latest.json` gets strategic state, army target and win probability for v13 and `not recorded` win probability for v1; nothing is written under `bots/<vN>/data`. A test drives the host's bridge and ready barrier: the game child does not start until the page reports the exact run id, a wrong or stale run id is refused, Stop before ready starts no game. A vitest renders the page from a status fixture, shows `not recorded` fields and an update age, and hides Stop after the match ends. `-CheckOnly` exits 0 without starting SC2 and fails with a message when SC2 is already running.
- **Depends on:** none (shim present in the tree; ES.2 not required)

### Step VW.2: Watch both bots of one bot-vs-bot match side by side
- **Problem:** The operator cannot watch two bot versions play each other with each side's decisions shown next to that side's own SC2 client.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Files:** `src/selfplay_viewer/strength/{host,telemetry,__main__}.py`, new `src/selfplay_viewer/strength/dual.py`, `scripts/launch-strength-viewer.ps1`, `frontend/src/components/StrengthViewer.tsx` + test, `src/orchestrator/selfplay.py` (opt-in `launch_prefix`, owned by lane evolve), `tests/test_strength_viewer.py`, `tests/test_selfplay.py`
- **Produces:** `powershell -File scripts/launch-strength-viewer.ps1 -P1 v13 -P2 v0 -Map Simple64` opens one 3440×1360 window: both SC2 clients side by side, each with its own decision panel beneath it, for one match run by `run_batch(games=1, launch_prefix=[...player wrapper...])` in a child process. Closing the window detaches both clients and lets the match finish; no further match starts.
- **Done when:** a test calls `run_batch` with `launch_prefix` and asserts each `BotProcess.launch_list` starts with the prefix and still ends with the original bot arguments, and a test asserts `launch_prefix=None` leaves `launch_list` byte-identical to today. A host test feeds two fake seats whose telemetry interleaves and asserts each pane updates only from its own seat, with same-version opponents (v13 vs v13) kept apart by seat. Pane pairing comes from a recorded per-player SC2 identity, and a test fails if it falls back to PID order. A missing or late SC2 window shows a placeholder without blocking the event pump. Closing the host restores both windows and the child exits after the match. Existing `tests/test_selfplay*.py` stay green.
- **Depends on:** VW.1; lane evolve's agreement on the `selfplay.py` change

### Step VW.3: Flag a moment from the viewer into ES.10's findings
- **Problem:** A behavior the operator sees in the viewer cannot be recorded as a finding the proposer will read.
- **Type:** code
- **Issue:** #
- **Flags:** --reviewers code --isolation worktree
- **Files:** `src/selfplay_viewer/strength/host.py`, new `src/selfplay_viewer/strength/flags.py`, `frontend/src/components/StrengthViewer.tsx` + test, `tests/test_strength_viewer.py`
- **Produces:** a Flag button (and `F` key while the page has focus) on each panel that freezes that seat's version and game time at the press, then takes class and note. Flags wait in the run's `flags-pending.jsonl` until the replay is final, then each is sent once as `uv run python scripts/scoreboard.py flag --version vN --replay PATH --game-time MM:SS --class C --note TEXT`. The panel shows "saved, waiting for replay", then "sent" or the command's error.
- **Done when:** a test drives the host's flag handler end to end with a temporary run and replay into the real ES.10 `flag` command and then the real `generate_pool` evidence builder (model call stubbed), and finds exactly one finding with the pressed seat's version, game time and replay in the evidence block. A flag pressed mid-match is not sent before the replay exists; a resend after restart sends nothing new for an already-sent flag; a failed command keeps the flag pending with the error shown. No `training.db` or scoreboard result row is written.
- **Depends on:** VW.1, VW.2, ES.10

### Direct task (outside VW.1–VW.3): replace the unwired Stop Run button
- `frontend/src/components/EvolutionTab.tsx`: replace the Stop Run button and confirm dialog (l.1401-1403, 1623, 1650) with the text "To stop an evolve run, close its console window."; update its vitest. Leave `AdvisedControlPanel`'s separate cleanup control alone. Check: `npm run test:run -- src/components/EvolutionTab` from `frontend/`. Lane evolve-demo can do it if main assigns the file.

### Operator acceptance (after VW.3; separate operator step, no code)
Run on Windows SC2 with no other SC2 client open. No paid calls are involved.

```powershell
powershell -File scripts/launch-strength-viewer.ps1 -CheckOnly
```

```powershell
powershell -File scripts/launch-strength-viewer.ps1 -Version v13 -Difficulty 3 -Race Terran -Map Simple64
```

```powershell
powershell -File scripts/launch-strength-viewer.ps1 -P1 v13 -P2 v0 -Map Simple64
```

| Check | What to look for |
|---|---|
| CheckOnly | Lists version, shim, prerequisites; starts no SC2 |
| Single match | Panel's state/army target/win probability change with what you see; Stop leaves the game normally and nothing else closes |
| Dual match | Both clients readable on the 3440×1440 display; each panel follows its own side; v0 plays past ~45 s (the old 98310 crash point); closing the window restores both clients and leaves no orphan SC2 |
| Flag | Flag a real behavior in each layout; after the game the finding lists the right version, game time and replay, and opening the replay at that time shows the behavior |

## 8. Risks and open questions

| Item | Consequence | Mitigation / owner | Blocking |
|---|---|---|---|
| `selfplay.py` is owned by lane evolve | VW.2 cannot land the `launch_prefix` change alone | main asks evolve to land it or assigns it to VW.2 | blocks VW.2 |
| Authoritative pane-to-player pairing | Wrong panel under a client | VW.2 proves a mechanism (candidate: each SC2 process's `-port` command-line argument against the proxy port burnysc2 uses for that player); otherwise label panes "pairing unverified" | VW.2 |
| ES.10 not built (waits on ES.3) | Flag cannot ship | Operator chose to wait; VW.1/VW.2 stand alone | blocks VW.3 |
| ES.10 flag has no idempotency key | Crash between send and receipt could duplicate a finding | Viewer marks sent only after exit 0; ask ES.10 for a dedupe key if duplicates appear | no |
| Isolated `DATA_DIR` changes behavior (missing build orders/configs) | Viewer game differs from a normal game | Seed the read-only config files; VW.1 test compares the loaded build order with a normal launch | VW.1 |
| VW.1 is the largest step (host + page + player + launcher) | May not fit one worker context | Auto camera is the first thing to defer; plan-review may split, but the cap is three steps | no |
| Copied native-host code drifts from Observer Rush | Two hosts to maintain | Record for convergence with lane repair later | no |
| Native window, DPI and audio behavior untested | Fixtures pass but the window misbehaves | Operator acceptance on the real display | acceptance |

## 9. Testing strategy

- Python: `uv run pytest tests/test_strength_viewer.py tests/test_selfplay.py tests/test_selfplay_callbacks.py tests/test_reparent_portable.py` (portable: SC2, Win32 and Playwright replaced at their boundaries; real serializers, bridge handler, player entry and `run_batch`). Then `uv run ruff check .` and `uv run mypy src bots --strict`.
- Frontend, from `frontend/`: `npm run test:run -- src/components/StrengthViewer`, `npm run lint`, `npm run build` (scripts verified in `frontend/package.json`).
- Production-caller integration: VW.1 through `python -m selfplay_viewer.strength.player` and the host bridge; VW.2 through `run_batch`; VW.3 through the real ES.10 `flag` command and `generate_pool`.
- Classification: an attended one-match tool, not an always-on service; no soak. The native window is a deployment seam observed only in the operator acceptance step.

## Disposition of removed items (D10, D7)

| Item | Disposition |
|---|---|
| EV.4 / EV.5 (`evolve --viewer` smoke, soak) | Retired: live evolve is deprecated (D16, ES.6); the dual-layout acceptance covers watching two bots |
| SV.1 detach/orphans | New host owns its child and restores windows on close (VW.2 Done when); old container unchanged |
| SV.2 threaded Ctrl+C ownership | New design runs `run_batch` on a child process's main thread; the old container's "never Ctrl+C a --viewer run" warning stays |
| SV.3 display sizing | Layouts sized for the measured 3440×1392 work area; operator acceptance judges readability |
| SV.4 surrender-label leak | Retired with the old container; the new panel shows each match's result from its own telemetry |
| SV.5 nonblocking HWND discovery | New host discovers windows without blocking the event pump (VW.2 Done when) |
| SV.6 legacy acceptance residuals | Retired; no historical pass is inherited |
| UI.7 / UI.8 | Closed by supersession (D10) |
| Dashboard Stop Run button | Direct task above |
