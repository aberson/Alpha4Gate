# Jev combined Battle View feasibility — 2026-10-11

Result: the fixed Windows layout can display the live SC2 client, the fully expanded
Branch faces graph, decision summaries and a short event log in one captured window.
This is a development probe, not the production launcher feature.

## Verified evidence

Successful run: `655b3d240bf74d57b4af89b0c1ff2242`. Local evidence lives in
`data/jev/runs/655b3d240bf74d57b4af89b0c1ff2242/battle/`:

- `battle-view.mp4`: losslessly remuxed from OBS's MKV; H.264 1920×1080,
  30 fps, 30.1 seconds, AAC game audio.
- `preview.png`: video frame at four seconds, showing the base and fitted layout.
- `composite.png`: end-of-recording capture. The camera points at terrain because
  the automated interaction deliberately clicks the minimap at ten seconds.
- `probe.json`: 292 graph nodes, Army selection, native geometry and OBS samples.
- `camera.jsonl`: initial locked camera `(26.75, 60.00390625)`, changed to
  `(32, 35.4453125)` while unlocked, then remained there after pointer-exit lock.
- The run directory also contains the normal decision trace and SC2 replay.

The SC2 client occupied exactly 1127×790 pixels at screen coordinate `(28, 134)`;
inspection of the saved video confirms that its edges fit the allocated pane.
OBS reported 29.03–30 fps across sampled intervals, one additional skipped render
frame and zero additional encoding skips. Audio was non-silent (mean −38.6 dB,
peak −18 dB). The only audio input was process capture for `SC2_x64.exe`;
desktop and microphone devices were disabled. These measurements establish a short
capture's feasibility, not negligible overhead or a long-session performance bound.

The test used local scripted Jev v2, Simple64, Terran difficulty 3, seed 11, real time.
It made no hosted requests and requested a normal controller stop after recording.
The parent exited with code zero and no SC2 process remained. All visual probes in
this investigation are interrupted experiments: exclude them from win-rate,
Step 222 acceptance and future decision-flattening comparisons.

## Implementation and native-window findings

`?battle=1&run=<id>` selects `JevBattleView`. It uses the existing archived policy
and live evidence hook, rejects mismatched policy hashes and distinguishes starting,
stale and terminal evidence from Live. The graph uses `dots3d`, `faces` and the new
optional `initiallyExpanded` prop. Existing dashboard defaults are unchanged.

`scripts/probes/jev_battle_view.py` owns a fixed 1920×1080 pygame host. A dedicated
Edge app window and the SC2 window are sibling children. Attaching SC2 directly
to Edge gave an unreliable blank rendering surface; the pygame host rendered it.
The host uses system DPI awareness. The game row was made taller and the event log
shortened to three entries to accommodate SC2's effective minimum size.

SC2 processes style and parenting changes asynchronously. The successful approach
waits for live observations, attaches the window, waits for the change to settle,
then compares its actual client origin/size against the browser renderer's screen
origin plus the game slot's CSS bounds. Corrections have a settling delay and a
bounded convergence check; failure aborts rather than recording a misplaced game.
Do not restore the earlier empirical border-offset compensation loops.

A continuous pygame event pump runs while Playwright is awaited. Without it, an
embedded browser request could stall when its parent stopped handling messages.
Native children are detached before the host is destroyed. The probe never kills
SC2; it requests stop through the existing Jev controller, with game/wall limits
as backup. No additional SC2 API client or fog/perception changes are introduced.

OBS process audio must bind while SC2 is still a top-level window. Creating that
input after reparenting produced silent recordings. OBS captures the combined
native window, including the browser and the DirectX surface.

The Record Battle and camera buttons call an injected Playwright binding, not a
new HTTP control endpoint. The probe bot holds the observed camera via its existing
SC2 client, releases it when unlocked and re-locks when the pointer exits the game.
This demonstrates camera control, not a spectator input-safety boundary: ordinary
game input is still enabled.

## Reproduction on this workstation

Prerequisites: existing dashboard on localhost:3000/8765, SC2 and Simple64 installed,
Edge, and the isolated portable OBS profile configured below. No other SC2 client
may be running. The probe refuses a pre-existing recording or a profile other than
`BattleProbe`. It replaces the inputs in this dedicated OBS profile.

```powershell
uv run --extra viewer --with playwright --with obsws-python python scripts/probes/jev_battle_view.py --record-seconds 30
```

The probe waits until this run's graph is rendered before permitting SC2 launch,
tests graph rotation and Army inspection, unlocks/moves the camera at ten seconds,
leaves the game at twelve seconds, stops recording, saves evidence and stops the
game. `--record-seconds` accepts 20–90 seconds. It strips `TYPESAFE_API_KEY` from
both child environments; no gameplay/provider configuration was changed.

Portable OBS 32.2.2 is installed at `%LOCALAPPDATA%/Alpha4Gate/obs-portable`, from
the official Windows x64 zip release. Its `portable_mode.txt` keeps configuration
separate from any normal OBS installation. Launch `bin/64bit/obs64.exe` from that
directory with `--portable --disable-shutdown-check --disable-updater
--minimize-to-tray --profile BattleProbe --collection BattleProbe
--websocketipv4only`. Use a hidden window when launching with PowerShell.

Profile configuration: 1920×1080 canvas/output, 30 fps, Simple output x264 CRF 23
veryfast, MKV, all global audio devices disabled. Websocket listens on port 4457,
with authentication enabled; the local random password is read from
`probe-password.txt` in the portable directory. Do not commit that file or password.
The probe dynamically creates a Windows Graphics Capture window source and a
`wasapi_process_output_capture` input bound to SC2. Production setup should automate
this configuration and scope/control recorder ownership more robustly.

## Validation and remaining work

Frontend build and lint passed (existing `useAlerts.ts` unused-disable warning;
build retains the existing large-chunk warning). All 30 focused Battle View/graph tests passed;
they
cover the complete initial graph, exact policy acknowledgement and stale-state
labeling. Python probe passes Ruff. The live capture supplies the native integration
evidence; no full-suite or gameplay acceptance claim is made.

Next production work: integrate this host into the dashboard-first launcher,
offer automatic recording on launch alongside the manual button, manage OBS
startup/ownership and failures, finalize camera/input policy, preserve recordings
through crashes, and validate long matches plus monitor/DPI changes. The fixed
layout and this monitor are the tested scope; resizing/moving across monitors is
not implemented. The prototype still contains browser app chrome. Hosted decision
text and long-running capture need their own integrated smoke test.

The separate strategy/army choice-flattening experiment remains deferred. Preserve
its before/after policy, traces and benchmark configuration when that work starts.

## Operator launcher follow-up

`scripts/launch-jev-battle.ps1` now starts/reuses dashboard and isolated OBS and
runs the probe's `--interactive` mode. It has manual recording by default and an
`-AutoRecord` option; `-CheckOnly` checks local prerequisites. The two desktop
shortcuts target this script. Interactive mode has no automated graph/camera
movements, uses 900-game-second/1200-wall-second limits and provides **Stop battle**.
This is a convenient local wrapper, not the completed production integration above.

Live launcher smoke `db328a66559344259737f96c776cd777` verified the automatic-record
path and exact pane fit. A native button click on **Stop battle** saved the MKV and
stopped the match; the launcher exited zero and no SC2 client remained. Camera
samples contain one unchanged position throughout, confirming the probe's automatic
camera exercises were disabled. Details: [operator guide](../operator/jev-battle-view.md).

## Typesafe launcher correction ? 2026-10-11

The operator clarified that both desktop launchers must use hosted Typesafe Jev.
The earlier scripted desktop default was an implementation mistake. Both shortcuts
now select `typesafe`, model `jev-1.13.0`, and the production 450-request cap;
only automatic recording differs. The launcher reads the existing encrypted DPAPI
key, keeps it out of dashboard/OBS/browser helpers, and hands it to the game child.
Missing credentials stop launch. The raw development probe remains scripted by
explicit default; historical scripted capture evidence above is unchanged.

The decision panel now shows each recorded source, returned model and request
count. The existing local opening and explicitly labeled service fallbacks remain
part of the production v2 policy; wiring Typesafe does not change its strategy.

Hosted smoke run `9d67fcbdf3ea42448d5d59c0e913ca6b` confirmed a live accepted
`army_mode=attack` response from `jev-1.13.0`, with eight requests visible at capture.
Proof is in that run's `battle/hosted-proof.json` and `hosted-proof.png`; the OBS
recording was saved and Stop battle ended the launcher with exit zero. This one
correction smoke was capped at 20 requests; normal desktop launches retain 450.
Four provider/credential regression tests and four Battle View tests passed;
build/typecheck, lint and Ruff passed (existing warnings unchanged).

## Game hidden after viewer interaction - 2026-10-11

Operator run `6095983ddd2f48408da9c28daae73564` retained its SC2 parent and
pane geometry but showed the browser's waiting placeholder after interaction.
Native reproduction in `f4ba656879894a6fa0e2ea6f672f3f34` confirmed that raising
the full-size Edge sibling covers SC2; raising SC2 restores it. The earlier
synthetic graph clicks did not exercise this native window activation behavior.

The host event pump now restores SC2's sibling order when needed, using
[`SetWindowPos` with `SWP_NOACTIVATE`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos)
to preserve input activation and pane geometry. Maintenance is disabled before
cleanup and only applies to SC2 while attached to this host.

Local display regression run `e2928ef6b7964bf4a00df098b87cdb76` deliberately
raised Edge three times: all three repairs passed with foreground preserved.
All 24 window-order samples had SC2 on top; graph rotation and Army inspection worked,
and the recorded composite visibly contains SC2 and all 292 graph nodes.
Evidence: that run's `battle/probe.json`, `composite.png`, and MKV. The short test
used the scripted developer probe with zero paid requests; both desktop shortcuts
continue to select hosted Typesafe. Four launcher tests and Ruff passed.

## Native input and automatic observer - 2026-10-11

The operator still could not interact with the embedded game. A live inspection
found SC2 enabled and holding keyboard focus, ruling out the focus-only repair
as sufficient. SC2 now remains a borderless, owned top-level popup, aligned to
the game pane; Edge remains a child of the pygame host. This follows Win32's
[owned-window and activation model](https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features):
owned popups stay above their owner, while retaining top-level activation.
The host tracks position changes and clears ownership before cleanup. OBS
explicitly layers the SC2 capture over the dashboard capture in one scene.

The preview and recording are now 1920x1200, with six recent events, more room
for decisions, a scrolling full ancestry inspector, and larger live active dots
and hit targets. A real-browser fixture verified a click outside the visible
active dot selects it and shows `scouting -> scouting.scout -> scouting.move`.

Automatic camera starts enabled. `src/jev/camera.py` chooses among own-unit
positions and existing visible enemy contacts, prioritizing contact, advancing
units, construction and active scouts. It holds routine shots for five game
seconds and can rotate quiet subjects after twelve. The wrapper samples and
moves the camera once per game second. No unit commands, hidden-state queries,
fog changes or new model calls are involved. Unlock disables automatic viewing;
pointer exit locks the resulting view; Auto camera resumes the observer.

Live local run `703c74fdbecf4829b3fd3cb7f107f884` verified the owned popup,
three browser-raising checks, exact 1127x825 pane fit and combined recording.
Its camera log records home, construction and visible contact shots across
11 camera positions. The composite shows the scout at the visible enemy base.
Artifacts are in that run's `battle/` folder. Nine Python camera/launcher tests,
34 targeted frontend tests, camera-module strict mypy, Ruff, frontend build and
lint passed (existing frontend warnings remain). Before the observer addition,
the full frontend suite passed 412 tests with six skipped.

Manual-input acceptance is still pending. The guarded physical-input probe
`1e71ab6b5a7c42928b0761271cc05663` stopped because another application had
foreground focus; it is not evidence of a successful or failed minimap click.
Physical input is now opt-in via `--exercise-input`, verifies the intended
foreground/hit window before clicking, and asserts actual camera movement and
pointer-exit locking. Normal developer probes no longer move the system cursor.
Both desktop shortcuts retain hosted Typesafe defaults. All developer camera
runs above were local scripted tests, not gameplay benchmarks.

## Taller graph and Archon Caster follow-up

Operator confirmed the owned-popup input/camera revision works. The next layout
uses a 2160x1200 host and recording, equal columns, a graph spanning the entire
right column, and the existing 125px ancestry inspector. Events and decisions
share a 160px left row above a 105px caster box. Live run
`3d11274058664f3eac2cceee50c71761` verified the native 1053x788 game pane,
292 nodes, three ownership/foreground checks, and a 65-second recording.
`36b6fe2d91654df5b42e45089534dea5` additionally recorded caster text in the composite.
These were scripted developer display probes, not hosted gameplay acceptance.

Archon Caster uses local Ollama Qwen2.5 1.5B on CPU, without credentials or a
cloud fallback. The installed 7B model exceeded the startup timeout; 0.5B was
fast but invented outcomes in sample commentary. The chosen 1.5B model produced
a sensible summary from archived event evidence, with a roughly 25-second warm
response in that check. This is entertainment, not a correctness guarantee or
performance benchmark. Cold starts can initially show a labeled fallback.

A shuffled 20-entry deck supplies 14 model commentaries, three curated AI facts,
and three dad jokes. Requests run independently from the native pump and Jev;
the viewer cancels the task on exit and discards replies after a run ends or
its evidence becomes stale. Each line names its source and snapshot game time.
The endpoint is fixed loopback, redirects/proxy environment are disabled, and
only bounded game-event fields enter the prompt. There are no game orders or
paid calls from the caster. Generated entries go to `battle/caster.jsonl`.

Validation: seven Battle View frontend tests, six caster/launcher Python tests,
frontend build/lint (existing warning only), strict caster mypy, and scoped Ruff
passed. A browser fixture also checked near-dot selection, the real ancestry
path, six events, and the enlarged graph geometry.

Final chosen-model live probe `2eb7186969154749868a8ec42a26728d` recorded
local Qwen commentary ("Probes are being trained to increase production
capacity."), the full composite, and a 45-second video; it exited normally.
