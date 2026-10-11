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
