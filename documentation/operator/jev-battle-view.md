# Try the Jev Battle View

The current Windows preview combines a hosted Typesafe Jev v2 match, the live
Branch faces graph, decision summaries and six recent game events. It uses your saved Typesafe API key and paid credits
(model `jev-1.13.0`, up to 450 requests per match). The isolated OBS recorder is already installed on the
development workstation; other machines need the setup described in the
[feasibility report](../investigations/jev-battle-view-feasibility-2026-10-11.md).

## Desktop launch

Two shortcuts are installed on the Windows desktop:

- **Alpha4Gate Battle View**: watch a match and press **Record Battle** whenever
  you want a clip.
- **Alpha4Gate Battle - Record**: start recording automatically when the fitted
  game window is ready.

Both use the same hosted player; only automatic recording differs. Each opens a fresh match against Medium Terran on Simple64, seed 11. The
dashboard and isolated OBS are started or reused automatically. Allow roughly a
minute for startup. The match is bounded to 15 game minutes / 20 wall minutes.
Do not launch another until the current game has finished.

Use **Stop battle** in the top bar to finish early. It saves any active recording
and asks the bot to leave normally. Wait for the viewer to close before closing
the launcher console. The console remains open so errors and the saved folder are
readable. Closing that console during a match is not the supported stop control.

## Things to try

1. Drag the graph to rotate it, scroll to zoom, Shift-drag to pan. **Fit** restores
   a useful overview. Live active dots are twice as large and have larger click
   targets. Click a dot for the full branch-to-node path and descriptions;
   branch roots are labeled. Long paths scroll in the inspector below the graph.
2. Watch highlighted nodes and recent events as Jev produces workers, builds and
   prepares its army. The decision panel identifies Typesafe responses, their model and request count.
   The fixed opening runs locally until there are eligible choices; pending/error
   fallbacks remain explicitly labeled.
3. **Auto camera** starts enabled. It follows visible enemy contact, advancing
   units, construction and scouting. The top bar names what it is following.
   Routine shots hold for at least five game seconds; new enemy contact can
   interrupt sooner. This changes only the view, never Jev's orders or vision.
   Press **Unlock camera** to disable the observer and use the minimap yourself. Moving the
   pointer out of the game automatically locks the camera at its new position.
   The lock prevents camera drift; game clicks can still issue normal SC2 commands.
   Press **Auto camera** again to resume automatic viewing.
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

This is a fixed 2160×1200 Windows preview and recording, tested on the development display.
Keep its size and monitor placement unchanged; adaptive resize and mixed-DPI
monitor support are still pending. OBS must use the dedicated `BattleProbe`
profile and authenticated local websocket on port 4457. The launcher will not
replace another active recording or attach to an existing SC2 match.

SC2 remains a native top-level window owned by the viewer and follows its pane
when the viewer moves. The recorder combines separate game and dashboard
captures into one video. This avoids the input/activation failures of embedding
SC2 as a child window. To load host changes, stop the old battle and launch again.

Hosted Jev is wired through the same production runner as `launch-jev.ps1`.
A missing key fails the launch; service failures retain the existing labeled
fallback behavior. Installation on a fresh machine, robust production lifecycle
and long-session validation remain follow-up work. Interrupted preview matches are not benchmark evidence.

## Archon Caster

The graph now occupies the full right half, with the same-height path inspector
beneath it. Recent events and current decisions share the shorter left summary
row; Archon Caster occupies the row below. Recordings include all these panels.

The caster is text-only entertainment, separate from paid Typesafe gameplay.
It uses local Ollama at `127.0.0.1:11434`, model `qwen2.5:1.5b`, on two CPU threads.
There are no caster API charges, keys, or cloud fallback. On this workstation
Ollama runs in WSL. On another machine, install Ollama and run
`ollama pull qwen2.5:1.5b`; without that service the panel uses a labeled local fallback.

Every shuffled batch of 20 entries contains 14 game commentaries, three curated
AI facts and three dad jokes (70/15/15). One background request runs at a time,
with a 30-second timeout and 30-second pause between entries. Commentary sees
only a bounded snapshot of recorded active nodes and recent command/task events.
The game-time label identifies that snapshot, not the time generation finishes.
Small-model commentary can be inaccurate; it never directs Jev or the camera.
Cold model startup may show a fallback before subsequent commentary succeeds.
Entries are saved beside the recording in `battle/caster.jsonl`.
