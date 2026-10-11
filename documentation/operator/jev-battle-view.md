# Try the Jev Battle View

The current Windows preview combines a local scripted Jev v2 match, the live
Branch faces graph, decision summaries and three recent game events. It does not
use your Typesafe credits. The isolated OBS recorder is already installed on the
development workstation; other machines need the setup described in the
[feasibility report](../investigations/jev-battle-view-feasibility-2026-10-11.md).

## Desktop launch

Two shortcuts are installed on the Windows desktop:

- **Alpha4Gate Battle View**: watch a match and press **Record Battle** whenever
  you want a clip.
- **Alpha4Gate Battle - Record**: start recording automatically when the fitted
  game window is ready.

Each opens a fresh local match against Medium Terran on Simple64, seed 11. The
dashboard and isolated OBS are started or reused automatically. Allow roughly a
minute for startup. The match is bounded to 15 game minutes / 20 wall minutes.
Do not launch another until the current game has finished.

Use **Stop battle** in the top bar to finish early. It saves any active recording
and asks the bot to leave normally. Wait for the viewer to close before closing
the launcher console. The console remains open so errors and the saved folder are
readable. Closing that console during a match is not the supported stop control.

## Things to try

1. Drag the graph to rotate it, scroll to zoom, Shift-drag to pan. **Fit** restores
   a useful overview. Click a dot for its name/description; branch roots are labeled.
2. Watch highlighted nodes and recent events as Jev produces workers, builds and
   prepares its army. This preview's decision source is **Local graph control**.
3. Press **Unlock camera**, then use the minimap to look elsewhere. Moving the
   pointer out of the game automatically locks the camera at its new position.
   The lock prevents camera drift; game clicks can still issue normal SC2 commands.
4. Press **Record Battle**, interact with the graph and game, then **Stop recording**.
   Repeat for another clip or leave recording on until **Stop battle**.

Recordings are MKV files in `data/jev/runs/<run_id>/battle/`, beside `probe.json`
and camera samples. The parent run directory holds `replay.SC2Replay` and the
decision trace. The run ID is displayed below the event log and the folder is
printed in the console on exit. Only SC2 process audio is captured, never microphone
or general desktop audio. Recording starts once gameplay is ready, not during SC2 loading.

## PowerShell alternatives

From the repository root:

```powershell
powershell -NoProfile -File scripts/launch-jev-battle.ps1
```

Automatic recording:

```powershell
powershell -NoProfile -File scripts/launch-jev-battle.ps1 -AutoRecord
```

Prerequisite check, without starting anything:

```powershell
powershell -NoProfile -File scripts/launch-jev-battle.ps1 -CheckOnly
```

For graph exploration without starting SC2, open the
[saved feasibility run](http://localhost:3000/?tab=jev&run=655b3d240bf74d57b4af89b0c1ff2242).
The regular Jev tab offers branch selection, Cards/Nodes, radial layouts and
Dots 3D shapes. A saved run shows its last recorded state; it does not replay
graph activity over time. If the dashboard is stopped, run
`powershell -File scripts/launch-a4g.ps1 -Tab jev` first.

## Current limits

This is a fixed 1920×1080 Windows preview, tested on the development display.
Keep its size and monitor placement unchanged; adaptive resize and mixed-DPI
monitor support are still pending. OBS must use the dedicated `BattleProbe`
profile and authenticated local websocket on port 4457. The launcher will not
replace another active recording or attach to an existing SC2 match.

Hosted Jev choices are available through the separate normal `launch-jev.ps1`
launcher, but are not wired into this combined preview yet. Full production
launcher integration, installation on a fresh machine and long-session validation
remain follow-up work. Interrupted preview matches are not benchmark evidence.
