# Jev live validation: smoke and acceptance procedure

The one documented workflow for collecting live evidence that the Jev player (`v1.jev`)
and its dashboard viewer work: the **Step 207** pipeline smoke and the **Step 208**
three-match acceptance of [the Phase JV plan](../plans/jev-player-plan.md). Every command
below is real and runs in **Windows PowerShell** from the repository root
(e.g. `C:\Users\<you>\dev\Alpha4Gate`) unless a step says otherwise.

What runs where:

| Terminal | Process | Stops with |
|---|---|---|
| 1 | Dashboard backend (`bots.current.runner --serve`, port 8765) | Ctrl+C in terminal 1 |
| 2 | Dashboard frontend (Vite dev server, port 3000) | Ctrl+C in terminal 2 |
| 3 | One Jev match (`bots.jev.v1`), which launches SC2 itself | Ctrl+C **once** in terminal 3 |
| 4 | `scripts\validate_jev.py`, inspection and cleanup commands | exits by itself |

PowerShell variables exist only in the terminal that set them: `$evidence` (section 2)
and `$runId` (section 6) are set in **terminal 4**, and every command that uses them runs
there.

## 1. Prerequisites

- Windows with StarCraft II at `C:\Program Files (x86)\StarCraft II\` (or `SC2PATH` set),
  and `Simple64.SC2Map` under its `Maps` folder (from the Blizzard CDN map pack, not GitHub).
- [uv](https://docs.astral.sh/uv/) with Python 3.12 or newer, and Node.js with npm.
- No API keys or Claude credentials: Jev makes no LLM call.
- Ports 8765 and 3000 free; only one backend may own 8765. No output from this command
  means both are free:

```powershell
Get-NetTCPConnection -LocalPort 8765,3000 -State Listen -ErrorAction SilentlyContinue
```

- Run the match, the backend and the verifier **from the same checkout**: each one derives
  its run root, `<repository>\data\jev\runs`, from its own code location, never from the
  working directory.

## 2. Install and prepare the evidence folder

```powershell
uv sync --extra dev
```

```powershell
cd frontend
npm ci
cd ..
```

In terminal 4, from the repository root, create today's evidence folder (under the
git-ignored `data\`) and keep its path in `$evidence`; verifier reports, screenshots and
the acceptance report go there:

```powershell
$evidence = "data\jev\evidence\$(Get-Date -Format yyyy-MM-dd)"
New-Item -ItemType Directory -Force $evidence
```

## 3. Validate the policy

```powershell
uv run python -m bots.jev.v1 --validate-policy
```

Expected (exit code 0), one line:

```text
jev policy valid: v1.jev policy_hash=<64 hex digits> nodes=<n> roots=economy,construction,production,army source=<path>\bots\jev\v1\policy.json
```

Write the `policy_hash` into the acceptance report: every run of this checkout must carry
it. An invalid policy prints `jev: invalid_policy: ...` and exits 1; stop there.

## 4. Start the dashboard

Terminal 1, backend (the Jev routes are mounted on the current dashboard app, so use
`bots.current`, not an older version's runner):

```powershell
uv run python -m bots.current.runner --serve
```

Terminal 2, frontend:

```powershell
cd frontend
npm run dev
```

Open <http://localhost:3000/?tab=jev>. With no runs yet the tab shows **No Jev runs yet.**
Check the API directly from terminal 4 (expected
`{"schema_version":1,"runs":[...],"truncated":false,"omitted":0}`):

```powershell
curl.exe -s http://localhost:8765/api/jev/runs
```

`.\scripts\launch-a4g.ps1 -Tab jev` starts both servers in their own windows and opens the
same page; it reuses a server already answering on its port, so make sure that server is
this checkout's `bots.current` backend.

## 5. Run one match

Terminal 3 (the plan's launch command; these are also the defaults):

```powershell
uv run python -m bots.jev.v1 --map Simple64 --opponent-race Terran --difficulty 1 --seed 1 --max-game-seconds 900 --max-wall-seconds 1800
```

Other flags: `--realtime` plays at real-time speed (not needed for observation);
`--run-root <absolute directory>` writes evidence elsewhere, but the dashboard only reads
the default root, so do not use it for Steps 207/208.

When the match ends the runner prints one line, then exits:

```text
jev match finished: result=win run_id=<32 hex digits> game_seconds=<n> commands_accepted=<n> commands_rejected=<n>
```

| Exit code (`$LASTEXITCODE`) | Meaning |
|---|---|
| 0 | `finished` with an SC2 result: `win`, `loss` or `draw` |
| 1 | `failed`: crash (`match_crashed`), SC2 unavailable (`sc2_unavailable`), evidence not persisted (`persistence_failed`), or no result |
| 2 | command-line error |
| 3 | a time limit ended the match: result `timeout` (`game_timeout` or `wall_timeout`) |
| 130 | `stopped` after Ctrl+C |

Exit codes 1, 3 and 130 also print `jev: <code>: <message>` on stderr (for a stop the code
is `stopped`).

## 6. Find the run ID and run directory

The run ID is the `run_id=` value on the runner's last line. While the match is still
running (its run directory exists before SC2 has finished launching), take the newest run
from the dashboard's run selector, or in terminal 4 from the API or the disk:

```powershell
curl.exe -s http://localhost:8765/api/jev/runs
```

```powershell
Get-ChildItem data\jev\runs -Directory | Sort-Object CreationTime -Descending | Select-Object -First 5 Name, CreationTime
```

Keep it in a variable in terminal 4 for the commands below, and set it again for every
new match. This takes the newest run directory, the match you started last (to pick an
older run, assign its ID in quotes instead):

```powershell
$runId = (Get-ChildItem data\jev\runs -Directory | Sort-Object CreationTime | Select-Object -Last 1).Name
```

The run directory is `data\jev\runs\<run id>\`:

| File | Content |
|---|---|
| `policy.json` | the exact policy bytes the run executed (never rewritten) |
| `metadata.json` | provenance: created_at, policy hash, source commit, map, opponent, difficulty, seed, limits, `replay_path` |
| `state.json` | the latest run state (heartbeat at most twice per second, then the terminal state) |
| `events.N.jsonl` | the trace, rotated at 10 MiB, newest 5 segments kept |
| `replay.SC2Replay` | the SC2 replay, when SC2 saved one (named in `metadata.json`) |

## 7. Verify a run

Terminal 4:

```powershell
uv run python scripts\validate_jev.py --run-id $runId --api-base http://localhost:8765
```

For an evidence bundle, also write the JSON report into `$evidence` (section 2):

```powershell
uv run python scripts\validate_jev.py --run-id $runId --api-base http://localhost:8765 --json "$evidence\verify-$runId.json"
```

| Flag | Meaning |
|---|---|
| `--run-id RUN_ID` | required; the 32-hex-digit run ID |
| `--api-base API_BASE` | required; the backend origin `http(s)://host[:port]`, no path |
| `--run-root RUN_ROOT` | optional absolute run root (default: this checkout's `data\jev\runs`) |
| `--json PATH` | optional; also write the report as JSON to `PATH` |

The verifier reads the run directory through the same validating readers the API uses,
fetches the run detail and archived policy from the API over HTTP, and checks that they
agree: run ID, family, version and schema versions; the policy hash in `policy.json`
(recomputed), `metadata.json`, `state.json`, the API run detail and metadata, and the API
policy; `last_sequence` and the recent events against the retained trace segments
(sequence numbers strictly increase; rotation and dropped counts are taken as the writer
reports them); the status / result / error combination; the API's `stale` flag; and the
replay `metadata.json` names. It never writes to the run directory.

Exit codes: **0** every check agrees, **1** a check failed (or the JSON report could not be
written), **2** usage error: a malformed command line, or an invalid `--run-id` /
`--api-base`, which prints `FAIL invalid_run_id` / `FAIL invalid_api_base`, reads nothing
and writes no report. A passing run prints (values vary):

```text
validate_jev: run <run id> at <repository>\data\jev\runs\<run id>
validate_jev: API http://localhost:8765
validate_jev: status=finished result=win error=None game_seconds=900.0 last_sequence=48211 seed=1 policy_hash=<64 hex digits>
validate_jev: trace segment 1, 5123 retained of 5123 event(s), complete=True
validate_jev: PASS - disk and API agree on this run
```

A failing run prints one `FAIL <code>: <reason>` line per failed check, for example after
`policy.json` was deleted:

```text
FAIL policy_missing: on disk, policy.json is missing
FAIL policy_missing: the API served no policy: GET /api/jev/runs/<run id>/policy answered HTTP 503 corrupt_run: 'policy.json is missing'
validate_jev: FAIL - 2 failed check(s): policy_missing
```

**PASS means the evidence is consistent, not that the match went well**: read the
`status=` and `result=` line. A run that is still playing passes with
`note: the run is live ...`; verify it again once it has ended. A note
`the trace is incomplete` means the writer dropped events (rotation past five segments, or a
failed append) and says how many; Step 208 needs `complete=True`. On Windows,
`http://localhost:8765` can add about two seconds to the first request (IPv6 is tried
first); `http://127.0.0.1:8765` avoids it.

| Failure code | Meaning |
|---|---|
| `invalid_run_id` | usage error (exit 2): `--run-id` is not a lowercase UUID4 hex run ID |
| `invalid_api_base` | usage error (exit 2): `--api-base` is not `http(s)://host[:port]` (no path, query, fragment or credentials) |
| `run_not_found` | no run directory with `metadata.json` under the run root (wrong ID or root) |
| `corrupt_run` | a run record on disk fails validation (malformed, unsupported schema, or identity mismatch) |
| `policy_missing` | `policy.json` is absent, or the API served no policy for the run |
| `hash_mismatch` | two policy hashes disagree anywhere (disk records, API detail, API metadata, API policy) |
| `schema_mismatch` | an API document's `schema_version` is not 1 |
| `sequence_mismatch` | sequence numbers do not increase, or exceed or disagree with `last_sequence` |
| `corrupt_trace` | a line of a retained `events.N.jsonl` segment fails validation |
| `trace_mismatch` | the retained trace disagrees with the counts or recent events `state.json` reports |
| `malformed_terminal_result` | a status / result / error combination the run contract does not allow |
| `stale_run` | the run is not in a terminal status and its heartbeat is older than 5 seconds |
| `replay_missing` | `metadata.json` names a replay that is absent or empty |
| `api_unreachable` | no HTTP answer from `--api-base` (backend not running, wrong port, timeout) |
| `api_error` | the API answered with an unexpected status, a redirect or a malformed body |
| `api_mismatch` | the API's run detail or metadata disagrees with the disk records |

`stale_run` on a run whose producer is gone means it crashed or was killed without a final
record. A run that is still launching SC2 also reads stale until its first game step, so
wait for the game to appear before verifying a fresh run.

## 8. Stop cleanly

1. **Stop a match**: press **Ctrl+C once** in terminal 3, the Jev runner's own terminal.
   The bot leaves the game, the runner writes the terminal state `stopped`, prints
   `jev match stopped: result=None run_id=...` and exits 130. A second Ctrl+C falls back to
   burnysc2's own handler, which cleans up only the SC2 it launched.
2. **Never kill SC2 processes wholesale**: no `Stop-Process -Name SC2_x64`, no
   `taskkill /IM SC2_x64.exe`. Another SC2 client (an evolve run, a second match) may be
   running, and killing SC2 under the runner turns a clean stop into a crash.
3. Closing the browser or the dashboard never stops gameplay; it only stops inspection.
4. **Stop the servers**: Ctrl+C in terminal 2 (frontend), then in terminal 1 (backend). If
   they were started by `launch-a4g.ps1`, close their windows.
5. **Verify the ports are free** (no output means free; closing a terminal does not always
   stop the backend):

```powershell
Get-NetTCPConnection -LocalPort 8765,3000 -State Listen -ErrorAction SilentlyContinue
```

If a port is still owned, look at its owner before stopping anything:

```powershell
Get-NetTCPConnection -LocalPort 8765,3000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Get-Process -Id $_.OwningProcess } | Select-Object Id, ProcessName, Path
```

Stop it only if it is the `python`/`uv` backend or `node` frontend this procedure started:
run `Stop-Process -Id` followed by the `Id` that command printed.

## 9. Step 207: live pipeline smoke checklist

Start the dashboard (section 4), open <http://localhost:3000/?tab=jev>, start one match
with the section 5 command, and set `$runId` in terminal 4 to its run ID (section 6). One
row per sentence of Step 207's *Done when*:

| # | Step 207 *Done when* | Do | Passes when |
|---|---|---|---|
| 1 | "After SC2 startup, observe 60 seconds of actual gameplay with graph-driven commands" | Once the SC2 window shows the game, watch for at least 60 seconds; in the Jev tab select the new run and read **Recent trace** | Probes mine and build in SC2; the trace shows `command` events whose node IDs are policy nodes (e.g. `economy.assign`, `construction.supply.build`) |
| 2 | "and live dashboard updates" | Keep the run selected | **Status: running** with the **Live** badge and no **Stale** badge; game time and the event number advance about every second |
| 3 | "disk/API/browser agree on run/hash/node IDs" | Run the section 7 verifier during the match; compare the tab's **Run ID** and **Policy hash** with the verifier's line and the section 3 hash; select a node in the graph and find its node ID in the API detail (`curl.exe -s http://localhost:8765/api/jev/runs/$runId`) | The verifier passes (with the live note); the run ID and hash are identical in all three places; the selected node ID appears in the API's `active_nodes`, `waiting_nodes` or `recent_events` |
| 4 | "close/reopen the tab and recover current state" | Close the browser tab, wait about 10 seconds, reopen <http://localhost:3000/?tab=jev> and select the run | The tab shows the current state, with a later game time than before closing, **Live** and not stale; gameplay never paused |
| 5 | "stop cleanly and verify terminal stopped state" | Press Ctrl+C once in terminal 3 (section 8), then run the verifier again with `--json` | The runner prints `jev match stopped: result=None ...` and `$LASTEXITCODE` is 130; the tab shows **Status: stopped**, not stale; the verifier prints `status=stopped` and PASS |
| 6 | "No mocks or replayed fixtures." | Check provenance | The run ID is the one this match's runner printed; `metadata.json` `created_at` is the time of this smoke |
| 7 | "Failures leave this gate incomplete." | Record any failed row as a finding (section 11) | Step 207 is complete only when rows 1-6 all pass |

Step 207 evidence: the run ID, the policy hash, one `command` event and its task result from
the trace (screenshot), the verifier output and JSON report, and a dashboard screenshot.

## 10. Step 208: three-match acceptance checklist

Play the three matches **one after another**, never two at once. Each match runs in
terminal 3; once it has ended, terminal 4 takes its run ID (the newest run directory) and
writes its report into `$evidence` (section 2).

Seed 1, terminal 3:

```powershell
uv run python -m bots.jev.v1 --map Simple64 --opponent-race Terran --difficulty 1 --seed 1 --max-game-seconds 900 --max-wall-seconds 1800
```

Seed 1 report, terminal 4:

```powershell
$runId = (Get-ChildItem data\jev\runs -Directory | Sort-Object CreationTime | Select-Object -Last 1).Name
uv run python scripts\validate_jev.py --run-id $runId --api-base http://localhost:8765 --json "$evidence\verify-$runId.json"
```

Seed 2, terminal 3:

```powershell
uv run python -m bots.jev.v1 --map Simple64 --opponent-race Terran --difficulty 1 --seed 2 --max-game-seconds 900 --max-wall-seconds 1800
```

Seed 2 report, terminal 4:

```powershell
$runId = (Get-ChildItem data\jev\runs -Directory | Sort-Object CreationTime | Select-Object -Last 1).Name
uv run python scripts\validate_jev.py --run-id $runId --api-base http://localhost:8765 --json "$evidence\verify-$runId.json"
```

Seed 3, terminal 3:

```powershell
uv run python -m bots.jev.v1 --map Simple64 --opponent-race Terran --difficulty 1 --seed 3 --max-game-seconds 900 --max-wall-seconds 1800
```

Seed 3 report, terminal 4:

```powershell
$runId = (Get-ChildItem data\jev\runs -Directory | Sort-Object CreationTime | Select-Object -Last 1).Name
uv run python scripts\validate_jev.py --run-id $runId --api-base http://localhost:8765 --json "$evidence\verify-$runId.json"
```

One row per sentence of Step 208's *Done when*:

| # | Step 208 *Done when* | Do | Passes when |
|---|---|---|---|
| 1 | "Run seeds 1, 2 and 3 against Terran difficulty 1 on Simple64, 900 game-second / 1800 wall-second limits per match." | The three commands above | Each verifier report's `run` shows its `seed` (1, 2, 3), `map` `Simple64`, `opponent_race` `Terran`, `difficulty` 1, `max_game_seconds` 900.0 and `max_wall_seconds` 1800.0 |
| 2 | "All three terminate normally with SC2 outcomes and complete trace provenance;" | Read each runner's last line and exit code, and each verifier report | Each runner exits 0 with `finished` and `result=win`, `loss` or `draw`; each verifier passes with `trace` `complete` true and `replay_path` `replay.SC2Replay` |
| 3 | "no unbounded loop, orphaned run or duplicate-spend failure." | Watch each match to its end; list the session's runs (section 6) | Every match ends by itself within its limits; every run directory of the session verifies with a terminal status (none `stale_run`); SC2 shows no repeated "not enough minerals" or supply errors, and `commands_rejected` stays small next to `commands_accepted` |
| 4 | "At least one match visibly reaches four Gateways and sends the first four-Zealot attack, with reinforcement evidence." | Watch SC2 and the graph; select `army.attack.launch.first_wave.latch`, `army.attack.launch.latched` and `army.attack.go` | Four Gateways stand in at least one match; the first attack leaves when four Zealots are ready (the latch node succeeds once, `army.attack.launch.latched` succeeds from then on); Zealots trained later join the attack (new `army.attack.go` `command` events name them, even when fewer than four Zealots remain) |
| 5 | "Inspect one waiting task and one completed command/result in browser;" | In the Jev tab, select a node listed under **Waiting**, then a node whose trace has a `command` event followed by its task succeeding | The node panel shows the waiting reason and deadline; the command's task shows status `succeeded` |
| 6 | "record observed recovery when it occurs, and explicitly mark unobserved recovery cases as scenario-tested only." | Fill the recovery table of the report (section 12) | Every recovery case has a run ID, game time and event as evidence, or reads "scenario-tested only" |
| 7 | "A timeout/crash or absent rush behavior is a functional blocker; losses alone are tuning findings." | Classify each finding (section 11) | Exit code 3 (`timeout`), 1 (crash or other failure) or missing row 4 behavior is a functional blocker; a loss alone is strategy tuning |
| 8 | "Report wins without an estimated general win rate." | Fill the per-match table | Wins and losses are listed per match; no win-rate percentage is claimed |
| 9 | "Phase remains incomplete until blockers are fixed and affected gates rerun." | Review the findings | No functional blocker remains open, or Step 208 stays incomplete and is rerun after the fix |

## 11. Evidence locations and findings

| Evidence | Location |
|---|---|
| Run directories | `data\jev\runs\<run id>\` (section 6) |
| Replays | `data\jev\runs\<run id>\replay.SC2Replay` (the `replay_path` in `metadata.json` and in the verifier report) |
| Verifier reports | `$evidence\verify-<run id>.json` (the `--json` path) |
| Screenshots | save into `$evidence\` (e.g. `seed1-four-gateways.png`, `smoke-dashboard.png`) |
| Runner output | copy each runner's last line (and stderr line, if any) into the report |
| Acceptance report | `$evidence\acceptance-report.md` from the template below |

Classify every finding as exactly one of: **functional blocker** (the player or viewer
does not do what the phase requires: timeout, crash, orphaned or stale run, no rush, a
verifier failure, a viewer that misleads), **strategy tuning** (it works but plays poorly,
such as a loss or a slow attack), or **later enhancement** (out of this phase's scope).

## 12. Acceptance report template

Copy into `$evidence\acceptance-report.md` and fill in:

```markdown
# Jev acceptance report - <date>

- Checkout commit: <git rev-parse HEAD>
- Policy hash (section 3): <64 hex digits>
- Operator: <name>

## Step 207 smoke

- Run ID: <run id>   Verifier: PASS (status=stopped)   Report: verify-<run id>.json
- Command/result event inspected: <node id>, event <n>, task <task id> succeeded
- Rows 1-7 of the smoke checklist: <pass / finding per row>

## Step 208 matches

| Seed | Run ID | Policy hash | Result | Duration (game s) | Four Gateways reached? | First four-Zealot attack? | Reinforcement evidence | Waiting task inspected | Command/result inspected | Recovery observed |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | | | | | yes / no | yes / no | <event or screenshot> | <node id, reason> | <node id, event> | <case> or "scenario-tested only" |
| 2 | | | | | | | | | | |
| 3 | | | | | | | | | | |

Wins: <n> of 3 (no win rate is estimated from three games).

## Recovery cases

| Case | Observed? (run ID, game time, event) |
|---|---|
| Rejected Gateway/Pylon placement | <evidence> or "scenario-tested only" |
| Lost probe during construction | |
| Lost power (Pylon destroyed) | |
| Delayed build acknowledgement | |
| Lost Gateway rebuilt | |
| Army task interrupted for defense | |

## Findings

| # | Finding | Evidence | Classification |
|---|---|---|---|
| 1 | | | functional blocker / strategy tuning / later enhancement |

## Verdict

Step 207: complete / incomplete. Step 208: complete / incomplete (open functional blockers: <n>).
```

## 13. Cleanup

Run directories are **never deleted automatically**: every match adds one under
`data\jev\runs\`, and the dashboard lists the newest 50. Keep the runs that are evidence
in a report until the report is filed. To remove one run, set `$runId` in terminal 4 to
it (section 6), check that it is the run you mean and that its match is over (the verifier
shows a terminal status), then remove it. The guard refuses an empty or malformed `$runId`,
which would otherwise target the whole run root:

```powershell
Get-Item "data\jev\runs\$runId"
```

```powershell
if ($runId -match '^[0-9a-f]{32}$') { Remove-Item -Recurse -Force "data\jev\runs\$runId" } else { Write-Host "Set `$runId to a 32-hex-digit run ID first." }
```

To clear every run (only after archiving the evidence you need, and with no match running):

```powershell
Remove-Item -Recurse -Force data\jev\runs
```

The evidence folder (`data\jev\evidence\`) is yours to keep or remove the same way. Never
delete files inside a run directory to "fix" a verifier failure: record the failure instead.
