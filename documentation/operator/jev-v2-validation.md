# Jev v2 validation: reproducible benchmark procedure

This guide covers the benchmark command added in Jev v2 Step 212 (issue #328),
`scripts/benchmark_jev.py`. It plays fixed panels of built-in-AI matches **one at a
time**, each through the production entrypoint (`python -m bots.jev.vN`) as a child
process, from a frozen copy of the exact runtime source, and scores every match from
its recorded evidence. The design is in
[the J2 plan](../plans/jev-v2-plan.md) (sections D1 and D6). The single-match
procedure and the Typesafe key setup remain in [the Jev operator guide](jev-validation.md).

**What is ready now and what is not:**

- Ready (Step 212): dry runs of every panel, the staging substrate check, resume and
  interrupt handling, scorecards and archive calibration.
- Ready (Step 224): **dashboard-first launch** (section 11). Every real benchmark run
  and the single-match launcher `scripts/launch-jev.ps1` open the dashboard on the
  exact starting run, and SC2 starts only after that page has rendered the run. A
  batch is followed in the same browser tab. `--no-dashboard` is the explicit headless
  mode. The official panels (`baseline`, `heldout-a`, `heldout-b`, `attribution`) now
  play. The v1 baseline is frozen by the first real `--panel baseline` preflight, so
  its frozen source includes the launch hook.
- **Step 213** is the first real baseline panel (six paid, realtime matches). Nothing
  in this guide plays a paid match.

Run every command from the Alpha4Gate repository root in Windows PowerShell.

## 1. Panels

| Panel | Cases (exactly as planned) | Provider | Realtime |
|---|---|---|---|
| `baseline` | frozen v1; Terran, Protoss, Zerg at difficulty 3 then 4; seed 11 | Typesafe | yes |
| `heldout-a` | each race at difficulty 4, seed 101; frozen v1 and frozen v2, alternating order | Typesafe | yes |
| `heldout-b` | as `heldout-a` with seed 202, opposite starting version | Typesafe | yes |
| `attribution` | the six v2 held-out cases | scripted | yes |
| `staging` | one v1 match, Terran difficulty 1, seed 1 (substrate check only) | scripted | no |

Each official panel is exactly six games on Simple64. Case IDs are
`version-provider-map-race-difficulty-seed`, for example
`v1-typesafe-simple64-terran-3-11`. `bots.jev.v2` does not exist yet, so every panel
that needs it fails resolution with `version_not_packaged` and names the reason. It
never falls back to v1 or to the current code.

The held-out and attribution panels play **frozen** sources only. v1 is frozen by the
`--panel baseline` preflight (section 2). A candidate is frozen once, explicitly, when
it is ready (plan D6: before any held-out game, and never tuned against the held-out
seeds afterwards):

```powershell
uv run python scripts/benchmark_jev.py --freeze-candidate v2
```

This copies the candidate's current runtime source into a snapshot and records it as
`baselines\v2.baseline.json`, write-once. Until a needed version is frozen, a panel
fails with `candidate_not_frozen`, and the message names the command that freezes it.

Hosted cases request model `jev-1.13.0` (`--decision-model jev-1.13.0`, never
`jev-latest`). Before the first hosted case of every invocation, one tiny probe must be
answered by exactly that model, and every answer recorded in a match must name it too.

## 2. Dry run: resolve the exact plan

Run in: this window @ Alpha4Gate · Model: any (no build work)

```powershell
uv run python scripts/benchmark_jev.py --panel baseline --dry-run
```

A dry run sends no service request, starts no process and writes no file. It prints:

- the claim, the requested model and the model every answer must name;
- the limits (section 6), including the child's `--max-wall-seconds 1080`;
- for v1: entrypoint `bots.jev.v1`, policy hash
  `510a72dfb167b7200123cbc8436c912f02d3b0fb5798657db3c62e3f5f8f68ee`, the
  source state, the source fingerprint and the snapshot folder it runs from;
- for each case, the full child command line and its working directory;
- the launch mode: `launch: dashboard` (each child gets
  `--launch-session <session_id assigned at start>`) or `launch: headless` with
  `--no-dashboard`;
- notes: the key requirement and probe of a real hosted run, what a real run would
  capture and finalize, and the dashboard URL a real run opens first. A dry run never
  opens the dashboard.

The source state is one of the following:

- `capture_pending`: nothing has been copied yet, and a real baseline preflight would
  copy the files and finalize them.
- `finalized`: the frozen baseline already exists, and this panel runs from it whatever
  the current source is.
- `captured`: a snapshot of the current source exists but has not been finalized.

The child environment shown for each case comes from the same function a real run
uses. It holds `PYTHONPATH` (the snapshot first), `PYTHONDONTWRITEBYTECODE`,
`PYTHONNOUSERSITE` and a per-attempt `PYTHONPYCACHEPREFIX` (created fresh at launch). The key is shown only
as inherited (hosted cases) or removed (scripted cases).

To keep the plan as a file, give `--json` an absolute path:

```powershell
$plan = Join-Path (Get-Location) 'data\jev\baseline-plan.json'
```

```powershell
uv run python scripts/benchmark_jev.py --panel baseline --dry-run --json $plan
```

A panel that needs v2 fails today, with exit code 1. That failure is expected:

```powershell
uv run python scripts/benchmark_jev.py --panel heldout-a --dry-run
```

The stderr output reads `version_not_packaged` and names both blockers:
`bots.jev.v2 is not packaged` and the v1 baseline that is not yet frozen.

## 3. Staging substrate check (exact command)

This check plays one real, short, **scripted** v1 match through the whole benchmark
path: source capture, child process, run archive, scoring, results and the lock. It
makes no service call. The child's environment never contains `TYPESAFE_API_KEY`. The
match is not realtime. It launches SC2 through the production runner, so run it on the
SC2 host with nothing else playing.

Run in: fresh PowerShell window @ Alpha4Gate · Model: any (no build work)

```powershell
uv run python scripts/benchmark_jev.py --panel staging --max-game-seconds 120 --max-wall-seconds 300
```

The run is dashboard-first (section 11): the Jev tab opens on the staging game before
SC2 starts. For a headless substrate check (automation, no browser), add
`--no-dashboard`:

```powershell
uv run python scripts/benchmark_jev.py --panel staging --max-game-seconds 120 --max-wall-seconds 300 --no-dashboard
```

Check the following:

1. The first line names the batch: `jev benchmark: batch <batch_id> panel=staging cases=1 dir=...`.
2. The case line reads `v1-scripted-simple64-terran-1-1: complete result=<win|loss|draw|timeout|error>`
   with a run ID. With the 120-second cap the usual result is `timeout`, which is fine for
   a substrate check.
3. The final line is `jev benchmark complete: ...`, and the exit code is 0.
4. `data\jev\runs\<run_id>\diagnostics.json` exists. Its `runtime_root` is the snapshot
   folder under `data\jev\benchmarks\baselines\`, not the repository.

If SC2 is missing, the case reads `invalid ... reason=infrastructure_failure`, the
batch stops, and the exit code is 1. Fix the install (see the Jev operator guide) and
start a new staging batch.

Staging is never performance evidence. The limits may only be lowered (section 6).

## 4. Where outputs land

All benchmark outputs are under the git-ignored `data\` folder:

| Path | Content |
|---|---|
| `data\jev\benchmarks\<batch_id>\manifest.json` | Immutable batch definition: schema_version 1, batch_id, claim, source_commit (informational), source_fingerprint, sources, policy_hashes, requested and expected returned model, the exact cases, limits, created_at. Written once and verified by hash on every resume. |
| `data\jev\benchmarks\<batch_id>\results.json` | Every case's status, run ID, result, reason, detail, attempts, metrics, and the latest attempt's child process ID and start time. Also the invocations (each with its probe answer) and the scorecard. It is replaced atomically before and after every match. Records with missing or unexpected fields are refused. |
| `data\jev\benchmarks\<batch_id>\attempts.jsonl` | Append-only record of each invocation event: created, started, ended, interrupted, budget_stop and so on. |
| `data\jev\benchmarks\<batch_id>\attempts\<case_id>.<n>.stdout.log` / `.stderr.log` | The child's output for attempt `n`. A `jev: diagnostics not written: ...` line in the stderr log explains a missing `diagnostics.json`. |
| `data\jev\benchmarks\<batch_id>\lock.json` | Present only while a benchmark process owns the batch. |
| `data\jev\benchmarks\baselines\<vN>-<fingerprint prefix>\` | Byte-for-byte copy of an explicit allowlist of runtime files, with `snapshot.json` (files, digests, fingerprint). The allowlist is every `src\jev` module, the SC2 path resolver, the `bots` package markers, `pyproject.toml`, `uv.lock`, and from the policy package only `__init__.py`, `__main__.py`, `manifest.json` and the policy file the manifest names. A file whose name looks like a credential stops the capture. Any bytecode cache, unexpected folder, link or special file fails its verification. So does a tree with more than 2048 entries, which is rejected rather than truncated. Children never write a cache: bytecode writing is off, and each attempt's `PYTHONPYCACHEPREFIX` is a new, empty, randomly named folder created just before launch. |
| `data\jev\benchmarks\baselines\vN.baseline.json` | A finalized frozen source, written once and never replaced. v1 is written by the first real `--panel baseline` preflight (after Step 224); a candidate by `--freeze-candidate vN`. |
| `data\jev\runs\<run_id>\` | The match's normal run archive, viewable in the dashboard, plus `diagnostics.json`: identity, exact options, outcome and the observation-only metrics. |
| `data\jev\launches\<session_id>\session.json` | One dashboard-first launch session (one per invocation): its state (`preparing`, `starting`, `running`, `between_games`, `finished`, `failed`, `stopped`), the exact active run, the game position (`case_index` of `case_count`) and a short message. At most 4 KiB, replaced atomically. |
| `data\jev\launches\<session_id>\ready.json` | The page's readiness receipt: the session, run and policy hash it rendered. Written only by the dashboard API, only for the session's exact starting run. |
| `logs\dashboard-backend.log`, `logs\dashboard-frontend.log` | Output of dashboard servers the launcher started in hidden helper windows. |

Print a batch's state without changing it. Set `$batch` to the batch ID from the
first output line:

```powershell
$batch = '<batch_id>'
```

```powershell
uv run python scripts/benchmark_jev.py --report $batch
```

## 5. How cases are scored and labeled

A case is `pending`, `running`, `complete` (valid evidence with a result) or
`invalid` (with a reason). Results are `win`, `loss`, `draw`, `timeout` (the game-time
limit) or `error` (a crashed match). Draws, timeouts and errors are reported
separately. **Only a
complete `win` counts as a win. An invalid case is never a win**, whatever SC2
reported (`reported_result` keeps that for the record).

The following must all agree:

- the child's summary line and exit code;
- the terminal run state;
- the run metadata and archived policy;
- the replay reference (a finished match must have its replay);
- `diagnostics.json`, which must name the expected entrypoint, policy hash, every
  option and the snapshot it ran from.

| Reason | Meaning | Stops the batch |
|---|---|---|
| `interrupted` | Ctrl+C during the case, or a case found `running` when a batch resumes | yes |
| `infrastructure_failure` | SC2 unavailable, evidence not persisted, no run ID reported, the child exceeded its hard wall bound, or the match's own wall-clock limit ended it (the host could not play it in time) | yes |
| `launch_failed` | Dashboard-first launch: the page did not render and acknowledge the case's exact run within 60 seconds, so SC2 was never started and nothing was spent (the run records `stopped`). A resume replays it only with `--retry-interrupted` (section 7) | yes |
| `authentication_failed` | The service refused the credentials, at the probe or during a match | yes |
| `corrupt_evidence` | A missing or malformed record (including unexpected fields), disagreeing outcomes, a missing replay or diagnostics, or unverifiable answers | yes |
| `provenance_mismatch` | Wrong policy hash, entrypoint, version, options or requested model; ran outside its snapshot; snapshot modified; manifest edited after the batch started | yes |
| `model_unavailable` | The probe failed, or every request of a match failed | yes |
| `model_drift` | An answer named a model other than `jev-1.13.0` | yes |
| `no_accepted_hosted_decision` | A hosted case with no accepted hosted answer: not a hosted comparison | no |
| `budget_exhausted` | The next match could exceed this invocation's budget (batch-level) | yes |
| `missing_configuration` | A hosted panel without `TYPESAFE_API_KEY` (preflight; nothing is played) | yes |

A batch that attempted every case but holds an invalid one ends `incomplete`.

The command itself can stop with one of these codes (exit 1 unless noted):

| Code | Meaning |
|---|---|
| `usage` | A malformed command line, an unknown batch, or a raised limit (exit 2) |
| `lock_held` | Another live process owns the batch, its lock cannot be verified, or a match a dead benchmark left running may still be playing |
| `dashboard_unavailable` | Dashboard-first launch could not start: the servers were not healthy within 60 seconds, or the running dashboard serves another data root (another checkout) or predates the launch API. Nothing was probed, captured or played; use `--no-dashboard` only for a deliberately headless run |
| `launch_integration_pending` | Only a build without the dashboard-first launch (before Step 224) raises it |
| `version_not_packaged` | A panel needs a version that has no package (today `bots.jev.v2`) |
| `candidate_not_frozen` | A panel needs a version that has no frozen source yet (section 1) |

Metrics are measured during play from every policy tick's observation, at intervals
of 1 game second or less. They are not taken from the retained trace. A gap longer
than one game second is missing coverage, not zero. Every metric is labeled
`observed`/`measured`, `not_reached` or `unavailable`, so an unknown never reads as zero.

- **Measured:** first attack, first expansion Nexus, first Cybernetics Core and first
  Stalker times; supply-blocked seconds; ready, powered, idle Gateway seconds while
  resources and supply suffice; mean mineral bank; worker final/max/mean; unit losses
  from own tag disappearances; accepted and rejected commands; model calls, answers,
  accepted and stale answers, failures, returned models, latency and tokens.
- **v1 limitations:** v1 never builds the last three milestones, so they read
  `not_reached`. Gas bank and plan aborts read `unavailable`, because v1 observes no
  gas and has no plans.
- **Cost:** token counts are reported, but no currency cost is claimed. The
  scorecard's `service` totals sum **every attempt** of every case, valid or invalid.
  An interrupted attempt that was retried still counts, because each case's `spent`
  is added to by every attempt and never reset. The totals are split into `complete`
  (the attempts that produced complete cases) and `other`.
  `unrecorded_hosted_attempts` counts hosted attempts with unknown spend. Each
  `ended` attempt record carries that attempt's `spend`, and each invocation's probe
  is listed in `results.json`.

## 6. Limits and budget

The mandatory defaults below may only be lowered. A higher value is a usage error,
exit code 2.

| Flag | Default (maximum) | Scope |
|---|---|---|
| `--max-game-seconds` | 900 | per match |
| `--max-wall-seconds` | 1200 | per match: the child process tree's hard bound |
| `--max-requests` | 450 | per match service requests |
| `--max-games` | 6 | per invocation |
| `--invocation-wall-seconds` | 7200 | per invocation |
| `--invocation-requests` | 2700 | per invocation, the probe included |

The child is told `--max-wall-seconds` minus 120. The 120 seconds cover SC2's launch,
leaving and replay save, so the whole child tree fits the match bound. A child still
alive at the bound has **its own process tree** ended. Other SC2 processes are never
touched.

Before each match the benchmark checks the invocation budget, and it never starts a
hosted match unless a full 450-request allowance remains. A completed hosted match is
charged its verified calls. A crashed or unverifiable one is charged its whole
allowance. When the budget runs out, the batch stops with exit code 3. Resume it in a
later invocation, which gets a fresh per-invocation budget.

## 7. Interrupt and resume

Press **Ctrl+C once**. The console delivers it to both the benchmark and its match.
The match leaves the game cleanly and records `stopped`. The benchmark labels the case
`invalid` with reason `interrupted`, saves the results and exits with code 130. If the
match has not left after 90 seconds, its own process tree is ended. Do not press Ctrl+C
repeatedly, and never kill all SC2 processes.

Resume with the original batch ID:

```powershell
$batch = '<batch_id>'
```

```powershell
uv run python scripts/benchmark_jev.py --resume $batch
```

A resume works as follows:

- Complete cases are skipped and never overwritten.
- A case still marked `running` (its benchmark process died) is first labeled
  `interrupted` in the attempt log. If that case's match process is still alive, the
  resume stops with `lock_held` instead. Wait for the match to end (at most its wall
  bound) before resuming, so two matches never play at once.
- A Ctrl+C after a case was recorded never changes that record. A case not launched
  yet stays `pending`.
- Interrupted cases, and `launch_failed` cases (their game never started), are
  **not** replayed unless you ask explicitly:

```powershell
uv run python scripts/benchmark_jev.py --resume $batch --retry-interrupted
```

A resume runs the original batch only:

- The same frozen snapshots (re-hashed), the same pinned model, the same panel
  definition and the same limits.
- Any changed option, model or source is refused with `provenance_mismatch`. New
  source, model or options need a new batch.
- Omit the limit flags when resuming, because the batch's own limits apply.

One process owns a batch at a time. A resume refuses a batch whose lock belongs to a
running process. A lock left by a process that is verifiably gone (same host) is
retired to `lock.stale-*.json` and taken over.

## 8. Calibrating against a real archive

`--calibrate-run` scores an existing run directory with the same logic, offline. It
plays no match and makes no service call. Archives older than `diagnostics.json` are
scored from their complete trace (`evidence=legacy_trace`). The known winning JI run
calibrates the logic:

```powershell
$jiRun = (Resolve-Path 'data\jev\runs\aa57512dd51745c2bcf7fb47e2c15f23').Path
```

```powershell
uv run python scripts/benchmark_jev.py --calibrate-run $jiRun --expect-race Terran --expect-difficulty 1 --expect-seed 1 --expect-requested-model jev-latest --expect-max-wall-seconds 1800
```

Expected output: `VALID ... result=win counted_as_win=True evidence=legacy_trace`,
with 61 calls, 61 answers (57 accepted, 4 stale) and every answer from `jev-1.13.0`.
Those numbers match the JI live record.

The same archive against the benchmark's pin must be rejected:

```powershell
uv run python scripts/benchmark_jev.py --calibrate-run $jiRun --expect-race Terran --expect-difficulty 1 --expect-seed 1 --expect-max-wall-seconds 1800
```

Expected output: `INVALID ... reported_result=win counted_as_win=False
reason=provenance_mismatch`, because JI requested `jev-latest`. A wrong difficulty, a
different policy hash (`--expect-policy-hash`) or a modified copy of the archive is
rejected the same way. Exit codes: 0 valid, 1 invalid.

## 9. Hosted panels

The hosted panels need `TYPESAFE_API_KEY` in the process environment. Load the saved
encrypted key as in [the Jev operator guide](jev-validation.md#reuse-the-encrypted-local-key-from-assisted-setup),
never printing it. A missing key stops the run with `missing_configuration` before
anything is copied or played. No scripted fallback is substituted. A real invocation
then opens the dashboard (section 11) and sends one tiny probe. If the dashboard
cannot be used, or the service refuses the key or answers as another model, the run
stops before any game, and before the v1 baseline is finalized. The baseline preflight
order is: resolve, open the dashboard, probe, capture and finalize the v1 snapshot
(it includes `src/jev/launch.py` and the runner's launch hook), then the first game.
Scripted cases never receive the key, and the dashboard servers and the browser never
inherit it. Remove it when finished:

```powershell
Remove-Item Env:TYPESAFE_API_KEY
```

## 10. Exit codes

| Code | Meaning |
|---|---|
| 0 | Batch complete, dry run resolved, report printed, or calibration valid |
| 1 | Failure: unresolved panel, invalid or stopped batch, refused resume, invalid calibration |
| 2 | Usage error (including a raised limit) |
| 3 | Invocation budget exhausted; resume later |
| 130 | Interrupted |

## 11. Dashboard-first launch (Step 224)

Every attended test opens the Jev tab on the **exact** game before SC2 starts. You
never pick a tab or a run by hand.

### Single match

Run in: fresh PowerShell window @ Alpha4Gate · Model: any (no build work)

```powershell
powershell -File scripts/launch-jev.ps1 -Version v1 -DecisionProvider scripted -Difficulty 1 -Seed 1
```

The defaults are `-Version v2 -DecisionProvider typesafe -Difficulty 3 -Seed 11`
(`-OpponentRace Terran`); `bots.jev.v2` does not exist yet, so until it does use
`-Version v1`. Map, limits and model are fixed: Simple64, 900 game seconds, 1200
wall seconds, `jev-1.13.0`, 450 requests, realtime. For `typesafe` the launcher
uses `TYPESAFE_API_KEY` if this process has it, otherwise the saved encrypted key
(`%LOCALAPPDATA%\Alpha4Gate\typesafe-key.dpapi`). It never prints the key or puts it
in a command line. Only the game process receives it (and the SC2 client that process
starts, as before Step 224): the dashboard servers and the browser never inherit it.
Run as `.\scripts\launch-jev.ps1` in your own window, the script puts back the key
your session had when it ends.

### What happens, in order

1. The dashboard servers are reused when they already answer the Jev API (backend
   `http://localhost:8765` and the frontend proxy on `http://localhost:3000`).
   Otherwise `scripts/launch-a4g.ps1 -NoBrowser -NoWait` starts them in hidden helper
   windows, and the launcher waits up to 60 seconds for real Jev API answers. Open
   ports alone are not accepted.
2. One launch session is created (`data\jev\launches\<session_id>\`). The launcher
   checks that the running servers serve **this** session. A dashboard started from
   another checkout (another data root) or an older backend without the launch API is
   refused with `dashboard_unavailable`, and nothing starts.
3. The browser opens `http://localhost:3000/?tab=jev&launch=<session_id>` once and
   shows **Preparing**. If no browser can be opened, the exact URL is printed: open it
   yourself within 60 seconds.
4. The production runner records the run (archived policy, `starting` state) and
   publishes it to the session. The page loads that exact run and its archived
   policy, renders it, and then acknowledges. Only then does SC2 start.
5. The page shows **Starting** while SC2 launches (never Live before the game reports
   its first observation), then **Live** with the game clock, opponent, active
   decision nodes and army intent, then **Finished** with the result.

A session the page cannot read at all (a mistyped or old link, a dashboard serving
another checkout) shows **Launch unavailable** with the error code, never Preparing.

### How the page tells a stopped launch from a slow one

The page reads the session's `updated_at` stamp. Only one process writes the session
at a time, and the stamp means something different in each state, so the page asks
exactly one question per state:

| Session state | Written by | How often | The page's one question | Shown when the answer is yes |
|---|---|---|---|---|
| `preparing` | the launcher | once, when it prepares a game | Is the session older than 120 seconds? | **Launcher not responding** |
| `starting` | the game process, waiting for this page | at once, then every 5 seconds (a heartbeat) | Is the session older than 20 seconds? | **Launcher not responding** |
| `running` | the game process, when this page acknowledged its run | once | Never the session's age. Asked only while the page shows that game: a run still `starting` (SC2 launching) asks whether its record was written more than 300 seconds ago; a game in progress asks whether its own heartbeat is older than 5 seconds | **Stale** |
| `between_games` | the launcher | once, after it scored the game | Is the session older than 120 seconds? | **Launcher not responding** |
| `finished`, `failed`, `stopped` | whoever ended it | once, never again | none: the launch has ended | no alarm |

- **Launcher not responding** shows the last recorded state; nothing there is live.
  The 120 seconds cover the launcher's own gaps: starting the game process and
  recording its run, scoring a game, and the benchmark's preflight.
- While SC2 launches, the page reads **Starting** for up to 300 seconds after the run
  was recorded. That is the 60-second barrier, plus the 180 seconds burnysc2 waits for
  SC2 to accept a connection, plus 60 seconds to create the game. After that the page
  reads **Stale**: "SC2 has not reported a first game observation N s after the run was
  recorded". During a game, **Stale** means the game process stopped writing its
  heartbeat.
- In `running` the session's stamp dates from the game's start, so it is old in any
  long game. The page never reads that as a dead launcher. It follows the game itself:
  **Live** while the game writes its heartbeat, **Stale** once it stops.
- While following is paused (**Following paused**, or **Waiting for this page** in
  `starting`), the page shows no verdict about the game. It still answers the
  launcher's questions for `preparing`, `starting` and `between_games`. When the
  answer is yes, a notice under the paused banner reads "The launcher has not updated
  this launch for N s; it may have been closed." In `running` a paused page makes no
  claim, so a long game never raises that notice.

### Batches

A real benchmark invocation does the same with one session for all its games. The
tab shows `Game N of 6`, moves to the next game's exact run in the same tab, and the
next game again waits for its own acknowledgment. A newer unrelated run never takes
over the view. Closing the browser after a game started does not stop that game; the
next game then waits up to 60 seconds and fails with `launch_failed` if no page shows
it. Reopen the session URL the launcher printed to continue watching. A
`launch_failed` case played nothing: resume the batch with `--retry-interrupted` to
replay it.

The page keeps following while its window is hidden behind the fullscreen game: the
session is polled from a worker timer, which Chrome and Edge do not throttle the way
they throttle a hidden page's own timers, and the page polls again the moment it is
shown. To be safe during Step 213, keep the dashboard window visible (for example on a
second monitor) and do not let the browser put the tab to sleep.

### Browsing history while following

Choosing another run in the Run menu pauses following: the tab shows **Following
paused** and a **Resume live** button. While paused, the page does not acknowledge a
new game, and a launcher waiting for it shows **Waiting for this page**. Click **Resume
live** within 60 seconds, or that game fails with `launch_failed` and SC2 is not
started. A launcher that stops while you browse is still reported, in a notice under
the paused banner (see "How the page tells a stopped launch from a slow one").

### Failures and stopping

- No acknowledgment within 60 seconds: the session and the page show **Failed**, the
  run records `stopped`, SC2 is not started, and a batch stops with `launch_failed`.
- Ctrl+C before the acknowledgment: the session shows **Stopped** and the run records
  `stopped`. SC2 is not started. After the game started, Ctrl+C makes the bot leave
  cleanly as before.
- The launcher never falls back to a headless game after a dashboard failure. Fix the
  dashboard, or deliberately choose `--no-dashboard` for a benchmark.
- Hidden servers the launcher started keep running for the next test and log to
  `logs\dashboard-backend.log` and `logs\dashboard-frontend.log`. Stop one by its
  port, for example `Get-NetTCPConnection -LocalPort 8765 -State Listen` gives the
  owning process ID for `Stop-Process -Id <that id>`. Never stop processes you did not
  start, and never stop SC2 that way.
- The dashboard backend (unchanged by this step) listens on all network interfaces,
  hidden or not. Stop it when you are done on an untrusted network.
- A real batch's per-game hard wall bound is the match bound plus the 60-second
  barrier, because waiting for the page is not play.

### Links

`http://localhost:3000/?tab=jev&run=<run_id>` shows exactly that run. With both
`launch` and `run`, the launch wins. An invalid ID shows an error and selects no run.
Without parameters the tab behaves as before: the newest run, until you pick one.

### Verifying it offline

The automated checks prove the order without SC2 or a paid call, using a stand-in
game: `uv run pytest tests/test_jev_launch.py tests/test_jev_api.py`. The real-browser
roundtrip (Chromium against a production build of the frontend) is opt-in:

```powershell
$env:JEV_BROWSER_TESTS = '1'
```

```powershell
uv run --with playwright pytest tests/test_jev_launch.py -k RealBrowser -p no:cacheprovider
```

```powershell
Remove-Item Env:JEV_BROWSER_TESTS
```

Step 213 then proves the same order with real SC2 and your visible browser.
