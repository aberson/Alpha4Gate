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
- **Step 213** is the first real baseline panel (six paid, realtime matches). Section
  12 is its runbook; nothing before section 12 plays a paid match. The operator froze
  v1 (`v1-b53039a693cadf75`) and deferred the rest of the baseline on 2026-10-09.
- Ready (Steps 214-221): **`bots.jev.v2` is packaged** -- the adaptive player with
  persistent plans and four hosted question families -- and the Jev tab's **live
  graph** explains what it is doing and why progress waits (section 13). Section 13
  also lists the hypothesis values to tune and what Step 222 needs before its first
  match. v2 is not frozen yet: freeze it only when the candidate is final (section 1).

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
`v1-typesafe-simple64-terran-3-11`. `bots.jev.v2` is packaged (Step 214), but the
panels that need it play only a **frozen** v2, so until `--freeze-candidate v2` they
fail resolution with `candidate_not_frozen` and name the reason. A panel never falls
back to v1 or to the current code.

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

A panel that needs v2 fails until the v2 candidate is frozen, with exit code 1. That
failure is expected:

```powershell
uv run python scripts/benchmark_jev.py --panel heldout-a --dry-run
```

The stderr output reads `candidate_not_frozen` and names the command that freezes v2
(`--freeze-candidate v2`). In a checkout whose v1 baseline is not frozen either, it
names that too.

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

1. After `jev launch: dashboard http://localhost:3000/?tab=jev&launch=<session_id>` (with
   `--no-dashboard` it is the first line), a line names the batch:
   `jev benchmark: batch <batch_id> panel=staging cases=1 dir=...`.
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
  resources and supply suffice; mean mineral and vespene bank; worker final/max/mean; unit losses
  from own tag disappearances; accepted and rejected commands; model calls, answers,
  accepted and stale answers, failures, returned models, latency and tokens.
- **v1 limitations:** v1 never builds the last three milestones, so they read
  `not_reached`. Plan aborts read `unavailable`, because v1 has no plans. The frozen
  v1 baseline's gas bank also reads `unavailable` (its frozen source observes no gas);
  the current runtime observes gas since Step 216, so v2 runs measure it.
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
powershell -File scripts/launch-jev.ps1 -Version v2 -DecisionProvider scripted -Difficulty 1 -Seed 1
```

The defaults are `-Version v2 -DecisionProvider typesafe -Difficulty 3 -Seed 11`
(`-OpponentRace Terran`): with no arguments the launcher plays `bots.jev.v2`, packaged
since Step 214, as a **paid** hosted match. `-DecisionProvider scripted` (above) makes
no service call; `-Version v1` plays the frozen-era v1 player. Map, limits and model
are fixed: Simple64, 900 game seconds, 1200 wall seconds, `jev-1.13.0`, 450 requests,
realtime. For `typesafe` the launcher
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
   decision nodes and army intent, then **Finished** with the result. For a v2 run the
   live graph and the **Plans and decisions** panel (section 13) show what each lane
   is doing and why a plan waits.

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
shown. To be safe during Steps 213 and 222, keep the dashboard window visible (for
example on a second monitor) and do not let the browser put the tab to sleep.

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

Step 213's staging and hosted smoke (2026-10-09) proved the same order with real SC2 and
a visible browser (section 12). Step 222 repeats it for v2 (section 13); same-tab
multi-game following stays unverified until the deferred baseline runs. The live
graph's own real-browser fixtures are in section 13.

## 12. Step 213: run the harder-opponent baseline

Step 213 (issue #330) plays the plan's six-case baseline screen (plan D6, item 1) and
proves the dashboard-first order of section 11 with real SC2 and your visible browser.
It only observes: it changes no code and estimates no win rate. Its report goes in
`documentation/plans/jev-v2-validation.md` (template at the end of this section). A
missing prerequisite keeps the step pending.

**Status (2026-10-09): deferred, and v1 is already frozen.** The first real (not
`--dry-run`) `--panel baseline` invocation captured the v1 runtime source and finalized
it, write-once, as `data\jev\benchmarks\baselines\v1.baseline.json` (section 4),
snapshot `v1-b53039a693cadf75`, before any Step 214 edit. The operator then stopped the
baseline (batch `94ac2c35b17f4cbaa80020b1c859ba00`: one interrupted case, five pending)
and authorized the v2 build. Steps 214-221 changed the shared runtime since; the frozen
snapshot is unaffected, so a resumed baseline still plays exactly that v1. The stages
below are kept as the procedure for finishing it, which needs a new operator request
(stage 4 makes up to six paid matches). Finishing means resuming that batch, not
starting a new one: stage 4 runs `--resume 94ac2c35b17f4cbaa80020b1c859ba00
--retry-interrupted`, which replays the interrupted case 1 and plays the five pending
cases from the frozen snapshot. A new `--panel baseline` batch would also play the
frozen v1, but it would leave batch `94ac2c35...` behind as a second, unfinished record.

Run the stages in this order. Each must pass before the next starts. The paid stages
come last. Stage 3 is an operator choice (see stage 3).

| Stage | Paid | Proves |
|---|---|---|
| 1. Prerequisites and dry run | no | the exact six cases, the model pin and the limits |
| 2. Staging match | no (scripted) | the dashboard-first order and the benchmark path with real SC2 |
| 3. Hosted smoke, at least 60 seconds | one match | the production runner gets answers from `jev-1.13.0` |
| 4. Baseline: probe, six games (v1 already frozen) | six matches | the service probe and six outcomes from the frozen v1 baseline |

The service probe has no command of its own. It is the first service request of every
hosted benchmark invocation (section 9), so it runs at the start of stage 4, before any
game. When you run stage 3, it is Step 213's first real service contact.

### Stage 1: prerequisites and dry run

Run in: fresh PowerShell window @ Alpha4Gate · Model: any (no build work)

You need SC2 with Simple64 (see the Jev operator guide), nothing else using SC2 (no
evolve run, daemon or other Jev match), and the dashboard where you can see it while
SC2 runs (section 11, Batches). Allow up to two and a half hours: the baseline alone may
use its whole 7200-second invocation budget.

Before v1 was frozen, the v1 runtime source had to be exactly what Step 224 left, and
this printed nothing on a clean checkout. v1 is frozen now, so Steps 214-221's changes
here are expected and do not matter to the baseline (expect `state=finalized` below):

```powershell
git status --porcelain -- src/jev src/orchestrator bots/__init__.py bots/jev pyproject.toml uv.lock
```

```powershell
uv run python -m bots.jev.v1 --validate-policy
```

Expected: `jev policy valid: v1.jev policy_hash=510a72dfb167b7200123cbc8436c912f02d3b0fb5798657db3c62e3f5f8f68ee ...`.

The saved encrypted key must exist. This checks the file without reading it:

```powershell
Test-Path (Join-Path $env:LOCALAPPDATA 'Alpha4Gate\typesafe-key.dpapi')
```

Expected: `True`. Then resolve the panel:

```powershell
uv run python scripts/benchmark_jev.py --panel baseline --dry-run
```

Check for `model: requested jev-1.13.0, every answer must be jev-1.13.0`, the `v1:`
line with the policy hash above, `state=finalized` and
`fingerprint=b53039a693cadf756e6cec4fc44c10534bcd9194079410a98b59a9e441bb1bfd` (frozen
2026-10-09 by batch `94ac2c35...`), `launch: dashboard`, and six cases in this order:
`v1-typesafe-simple64-terran-3-11`, `...-protoss-3-11`, `...-zerg-3-11`,
`...-terran-4-11`, `...-protoss-4-11`, `...-zerg-4-11`. Copy the `v1:` line's
`fingerprint=` value into the report. If it reads `state=capture_pending`, this checkout
lacks the gitignored frozen baseline: stop. A real run would capture today's shared
runtime, which Steps 214-221 changed, as v1. Copy `data\jev\benchmarks\baselines\` from
the primary checkout instead (the panel verifies the snapshot against its fingerprint
before playing).

### Stage 2: staging match (no service call)

```powershell
uv run python scripts/benchmark_jev.py --panel staging --max-game-seconds 120 --max-wall-seconds 300
```

Watch the dashboard (see "What you are looking for" below; staging is `Game 1 of 1` and
not realtime) and check section 3's four points. This is the first time the post-case
snapshot check (watch item 1) meets real SC2. Staging always captures the current
source, so since Steps 214-221 it runs from a new `v1-<fingerprint prefix>` snapshot,
not the frozen baseline's folder. It still proves the benchmark and dashboard path with
real SC2. A staging failure stops Step 213 here. The plan's Step 213 note 3 lists staging as
optional; this runbook runs it because it is the only unpaid real-SC2 check of the
snapshot and dashboard path.

### Stage 3: hosted smoke, at least 60 seconds

```powershell
powershell -File scripts/launch-jev.ps1 -Version v1 -DecisionProvider typesafe -Difficulty 1 -Seed 1
```

`-Version v1` is required (watch item 4). This stage is option (a) of the plan's Step
213 note 4, an operator decision. It runs at difficulty 1 seed 1 instead of the
launcher's default 3/11 so that it does not replay baseline case 1. The alternative,
option (b), is to skip this stage and watch the first baseline case live for at least
60 seconds after its first Typesafe answer, and record that as the smoke. Record which
you chose in the report. Terran difficulty 1, seed 1 is the
configuration of the known hosted JI run (61 calls and a win, section 8), so the smoke
is cheap and is not one of the six baseline cases. The launcher loads the saved key
itself, for the game process only. Its first line is
`=== Jev dashboard-first launch: v1 typesafe, Terran difficulty 1 seed 1 ===`.

The army asks the service only once more than one army option is feasible, so the first
answer comes minutes into the game (about three game minutes in the JI run). From the
first **Source: Typesafe response** in the **Army decision** panel, watch at least 60
seconds of live play:

- **Configured provider** is `typesafe` and **Requests made** keeps rising.
- Under **Typesafe request and last response details**, **Requested model** and
  **Actual response model** are both `jev-1.13.0`.
- Production and economy continue while a request is pending.

Let the match finish. It ends with `jev match finished: result=... run_id=...` and
`jev launch: session ... finished: ...`. If you must stop it, press Ctrl+C once, and only
after the 60 seconds (the run records `stopped`). Record the run ID and the seconds you
watched. If the service refuses the key, never answers or names another model, stop
here: that is an infrastructure finding and Step 213 stays pending.

### Stage 4: the six-case baseline

Run in: the same PowerShell window @ Alpha4Gate · Model: any (no build work)

Load the key into this window without printing it (section 9):

```powershell
$jevKeyFile = Join-Path $env:LOCALAPPDATA 'Alpha4Gate\typesafe-key.dpapi'
$jevSecret = ConvertTo-SecureString -String ((Get-Content -LiteralPath $jevKeyFile -Raw).Trim())
$env:TYPESAFE_API_KEY = [System.Net.NetworkCredential]::new('', $jevSecret).Password
$jevSecret.Dispose()
```

Resume the deferred batch (see the status above). Pass no limit flags: the batch's own
limits, the plan's, apply (section 7).

```powershell
$batch = '94ac2c35b17f4cbaa80020b1c859ba00'
```

Check it first. This makes no service call and writes nothing:

```powershell
uv run python scripts/benchmark_jev.py --resume $batch --dry-run
```

Expect stage 1's six cases, model pin and `v1:` line (`state=finalized`, the same
fingerprint). Then play it; `--retry-interrupted` replays the interrupted case 1:

```powershell
uv run python scripts/benchmark_jev.py --resume $batch --retry-interrupted
```

The console must show these lines in this order:

1. `jev launch: dashboard http://localhost:3000/?tab=jev&launch=<session_id>`. The tab
   opens on **Preparing**, `Game 1 of 6`.
2. `jev benchmark: probe ok: model=jev-1.13.0 latency_ms=<n>`. The service probe passed.
   A failed probe prints `authentication_failed`, `model_unavailable` or `model_drift`
   instead, exits 1, and plays nothing.
3. No `jev benchmark: batch ...` line: a resume reuses the batch. v1 is already
   finalized, and the batch plays snapshot `v1-b53039a693cadf75`.
4. When the batch ends, one line per case, then
   `jev benchmark complete: batch=<batch_id> valid_wins=<n> results={...} invalid={}`,
   with exit code 0 when all six cases complete. A batch that played every case but
   holds an invalid one (for example `no_accepted_hosted_decision`, which does not stop
   the batch) prints `jev benchmark incomplete: ... invalid={...}`, then
   `jev benchmark: <reason>: <n> case(s) not complete: ...`, with exit code 1. That is an
   outcome to record and classify (below), not a failed procedure. The console stays
   quiet while the games play (each game's output
   goes to its attempt logs, section 4); follow them on the dashboard.

Print the record:

```powershell
uv run python scripts/benchmark_jev.py --report $batch
```

Confirm the frozen baseline is unchanged. The dry run still reads `state=finalized` with
stage 1's fingerprint:

```powershell
uv run python scripts/benchmark_jev.py --panel baseline --dry-run
```

Remove the key:

```powershell
Remove-Item Env:TYPESAFE_API_KEY
```

If the batch stops, resume it from a window with the key loaded (above): a resume opens
the dashboard and probes again (section 7). An `interrupted` or `launch_failed` case is
replayed only on request:

```powershell
uv run python scripts/benchmark_jev.py --resume $batch --retry-interrupted
```

Exit code 3 (`budget_exhausted`) resumes with plain `--resume $batch`. Any other stop
reason is a finding. Classify it (below) and fix its cause before resuming; the
stopped case stays recorded as invalid, and a resume plays only the remaining cases.

### What you are looking for

| Moment | On the dashboard | Not acceptable |
|---|---|---|
| Launch | The browser opens the Jev tab by itself on `?tab=jev&launch=<session_id>`: **Preparing**, `Game 1 of N` | An old run shown as current; picking the tab or a run by hand |
| Before SC2 starts | The banner shows `run <run_id>` and turns **Starting**; only then does the SC2 window open | SC2 opening while the page reads **Preparing** or shows another run |
| Play begins | **Live**, with the game clock, opponent, active decision nodes and army intent | **Live** before the game reports; **Stale**; **Launcher not responding** |
| Hosted play (stages 3 and 4) | **Army decision**: Source: Typesafe response, Actual response model `jev-1.13.0`, Requests made rising | Scripted fallback for the whole game; any other model |
| Game end | **Finished** with the result | |
| Next case (stage 4) | With no menu click, `Game N+1 of 6` and **Preparing** with a new run ID, then **Starting** and **Live** | The view staying on the previous run; a new tab |
| Afterwards | Each game's banner run ID equals that case's `run_id` in `--report $batch` | Any mismatch |

### Classifying failures

Give every case that is not a valid win exactly one primary class, with its evidence:
a run ID plus a game time, a trace event or a section 5 metric. A win can still carry
findings.

| Class | Meaning | Typical evidence | Plan owner |
|---|---|---|---|
| execution | The intended action was right but was not carried out | Supply-blocked seconds, idle Gateway seconds, a high mineral bank, rejected commands, reinforcements arriving one at a time | Steps 214, 216 |
| information | The player acted on missing or stale knowledge | An attack into an unseen army, no reaction to enemy tech, stale or late service answers | Step 215 |
| composition | The army's unit mix could not beat the opponent's | Zealots only against ranged, air or armored units | Steps 216, 218 |
| strategy | The plan or its timing was wrong | A one-base attack held at difficulty 4, no expansion, a poor attack or regroup choice | Steps 217, 219, 220 |
| infrastructure | Not the player: the substrate failed | Any invalid case reason that stops a batch (section 5), `dashboard_unavailable`, an `error` result from an SC2 or host crash | Fix it before trusting the panel |

- v1 never reaches the expansion Nexus, Cybernetics Core or Stalker milestones
  (section 5). That alone is not a finding; cite it only as evidence for what lost a game.
- `no_accepted_hosted_decision` does not stop the batch, but the case is not a hosted
  comparison. Classify it by cause: service errors are infrastructure, answers that all
  went stale are information.
- An `error` result from the Jev process itself (its attempt's stderr log names the
  exception) is execution, not infrastructure.
- For an `interrupted` case, record who stopped it and why.

### Watch items

1. **`provenance_mismatch` naming unexpected folders or files.** Each game process runs
   with its working directory inside the snapshot folder, and the snapshot is verified
   again after every case. Anything a game creates there fails that case: a new empty
   folder reads `snapshot v1-<prefix> has unexpected folders: [...]`, and a new file
   reads `snapshot v1-<prefix> files changed (extra: [...], missing: [])`. Before Step
   214, staging and the baseline shared that folder; they no longer do (stage 2), so a
   staging failure of this kind is a code finding about the current source, not about
   the frozen baseline. Record the named folders or files and stop. Do not edit or
   delete anything in `data\jev\benchmarks\baselines\`; this needs a code fix.
2. **Dashboard start can take about 130 seconds.** The launcher waits up to 60 seconds
   for healthy servers, then up to another 60 seconds for both servers to serve the new
   session. That is longer than D7's 60-second deadline. Wait for
   `jev launch: dashboard ...` or `dashboard_unavailable`, and record a start that took
   more than 60 seconds as an observation.
3. **A paused page quotes an old launcher message.** Choosing another run from the Run
   menu during a game shows **Following paused**, and its `Launcher: ...` line repeats the
   launcher's last message, which is not the game's live state. Click **Resume live**.
   Better, do not browse runs during Step 213.
4. **`-Version v1`.** `scripts/launch-jev.ps1` defaults to `-Version v2`, which is
   packaged since Step 214: without `-Version v1` the stage 3 smoke would play the v2
   player instead of the v1 baseline's.

### Cost and limits

- Stage 3: one realtime match, at most 450 requests, 900 game seconds and 1200 wall
  seconds.
- Stage 4: one invocation with a budget of six realtime matches, 2700 requests (the
  probe included) and 7200 wall seconds. Each match is limited to 450 requests, 900
  game seconds and 1200 wall seconds.
- Before each match the benchmark requires a full 450-request allowance and 1200 wall
  seconds to remain. Long early games can therefore push the last match into a resumed
  invocation (exit code 3). A resume gets a fresh budget and spends one more probe
  request.
- Token counts are reported. No currency cost is claimed (section 5).

### Report template

Copy into `documentation/plans/jev-v2-validation.md` and fill it in. Record IDs, not
local paths.

```markdown
# Jev v2 validation: Step 213 baseline - <date>

- Issue: #330 (umbrella #327). Operator: <name>
- Checkout commit: <git rev-parse HEAD>. Runtime source clean: yes / no
- v1 policy hash: <64 hex>. v1 fingerprint (dry run): <64 hex>
- Frozen baseline: written 2026-10-09 by batch `94ac2c35b17f4cbaa80020b1c859ba00` (snapshot `v1-b53039a693cadf75`); dry run still reads `state=finalized` with that fingerprint: yes / no

## Stages

| Stage | Run or batch ID | Outcome | Notes |
|---|---|---|---|
| 1. Dry run | none | resolved / unresolved | six cases in plan order: yes / no |
| 2. Staging | run <id>, batch <id> | complete result=<result> | |
| 3. Hosted smoke | run <id> | finished / stopped, result=<result> | seconds watched after the first Typesafe answer: <n> |
| 4. Probe | batch <id> | probe ok: model=<model> latency_ms=<n> | |
| 4. Baseline | batch <id> | jev benchmark <status>, exit <code> | invocations: <n> |

- Smoke (plan Step 213 note 4): (a) stage 3 separate match / (b) baseline case 1 watched >= 60 s

## Dashboard-first order (real SC2, visible browser)

| Check | Staging | Smoke | Baseline games 1-6 |
|---|---|---|---|
| Opened by itself on the exact run before SC2 started | | | |
| Starting, then Live with the game clock | | | |
| Followed the next game with no menu click | n/a | n/a | |
| Banner run ID equals the reported run_id | | | |

## Baseline outcomes

| # | Case | Run ID | Status | Result | Reason | Calls (accepted / stale) | Tokens in / out | Primary class |
|---|---|---|---|---|---|---|---|---|
| 1 | v1-typesafe-simple64-terran-3-11 | | | | | | | |
| 2 | v1-typesafe-simple64-protoss-3-11 | | | | | | | |
| 3 | v1-typesafe-simple64-zerg-3-11 | | | | | | | |
| 4 | v1-typesafe-simple64-terran-4-11 | | | | | | | |
| 5 | v1-typesafe-simple64-protoss-4-11 | | | | | | | |
| 6 | v1-typesafe-simple64-zerg-4-11 | | | | | | | |

Valid wins: <n> of 6. Draws, timeouts, errors: <n>, <n>, <n>. A failure-mode screen, not a win rate.

## Metric coverage

| # | First attack | Supply-blocked s | Idle Gateway s | Mean mineral bank | Workers final / max | Unit losses | Commands accepted / rejected | Model latency |
|---|---|---|---|---|---|---|---|---|

Label each value as results.json does: observed or measured, not_reached, unavailable.

## Invocations and interruptions

| Invocation | Probe | Games | Requests charged | Ended | Stop reason |
|---|---|---|---|---|---|

## Findings, ranked

| Rank | Finding | Class | Cases | Evidence (run ID, game time, metric or event) | Plan owner |
|---|---|---|---|---|---|

## Watch items

- Unexpected snapshot folders or files: not seen / seen (<names>)
- Dashboard start over 60 s: not seen / <seconds>
- Paused page quoting an old launcher message: not seen / seen
- Smoke played with -Version v1: yes / n/a (option b)

## Verdict

Step 213: complete / pending (<missing prerequisite or open infrastructure finding>).
```

## 13. The v2 player and the live graph (Steps 214-221)

`bots.jev.v2` is a separate package beside the frozen v1 (`bots/jev/v2/manifest.json` and
`policy.json`, 292 nodes, policy hash `a1c73ea8...` at Step 220). Validate it without SC2:

```powershell
uv run python -m bots.jev.v2 --validate-policy
```

It prints the policy hash and the nine root lanes. v1's hash stays
`510a72dfb167b7200123cbc8436c912f02d3b0fb5798657db3c62e3f5f8f68ee`.

### Lanes

Every root of the graph is a lane, evaluated every tick in this order with a fair share
of the node and command budgets. Several lanes are normally active at once.

| Lane | What it does |
|---|---|
| `economy` | Idle probes mine; gas stays saturated (up to 3 per Assimilator) |
| `supply` | The opening Pylon, then planned free supply above the buffer |
| `construction` | Re-power a Gateway, Gateways to four, then gas (one Assimilator) |
| `tech` | The tech plan: one Cybernetics Core (runs before the expansion lane) |
| `expansion` | The expansion plan: the natural, two bases at most |
| `scouting` | One Probe scout; retreats when threatened; replaced at most every 60 s |
| `production` | Stalkers within the Zealot:Stalker mix, else Zealots, then probes |
| `army` | Defend (both bases), regroup when few remain, attack in groups, else rally |
| `reserve` | After the launch, new combat units gather at home until a group forms |

### Plans

The `tech` and `expansion` lanes each run one **persistent plan**: a record with a
stable `plan_id` (`<run_id>:plan:<n>`), a stage (the lane's frontier node), a status
(`active`, `waiting`, `completed`, `aborted`), a reason code, a blocker while it waits,
its site, worker and held minerals/gas, and who started it (`decided_by`,
`decision_request_id`). Both are **macro** plans and share one slot: only one is open
at a time, and after an abort the slot owes the other kind a turn. Limits (plan D3):
120 game seconds to start its building and 120 to finish, 30 seconds without progress is
a recovery attempt, 3 recovery attempts per stage, then abort, release and a 15-second
cooldown; after an abort the slot yields to the other kind for 5 seconds past the
cooldown; a site aborted over (or refused by SC2) is avoided for 120 seconds; the third
abort of a kind disables it for the match. A hosted abandon is not an abort and has its
own cap of 3.

### Hosted questions

With `--decision-provider typesafe` four question families share one request budget
(`--decision-max-requests`, 450 by default), one request at a time, at least 2 wall
seconds apart. The graph keeps every command and legality check; an answer only
chooses which offered branch runs.

| Question | Choices | Asked | Local fallback |
|---|---|---|---|
| `strategy` | pressure, recover, expand, tech | after the first launch, at most every 10 s, only choices the graph can execute now | the graph's own order: tech prerequisite, then the expansion after 4 Gateways and 8 ready combat units, recovery under 4 units, otherwise pressure |
| `army_mode` | attack, defend, regroup | at most every 2 s | the graph's defaults (defense first) |
| `expansion_response` | continue, delay, abandon | while an unissued expansion faces an enemy at its site or no free site, at most every 5 s | delay |
| `production_mix` | zealot_heavy (2:1), balanced (1:1), stalker_heavy (1:2) | once a Core, gas and a ready Gateway allow Stalkers, at most every 10 s | balanced; stalker_heavy after an armed air unit was seen within 30 s |

A question waiting 10 wall seconds is served next, so none starves. A stale,
low-confidence, invalid or timed-out reply never applies; the fallback is used and
visibly attributed. Without `--realtime` most hosted replies arrive stale, so hosted
runs are always realtime.

### Hypothesis values to tune at Steps 222 and 223

These are the build's starting guesses (plan Build notes), not measured optima. Record
any that Step 222's matches put in doubt; do not tune against the held-out seeds.

| Value | Now | Where |
|---|---|---|
| Pressure/recover posture hold without a renewed answer | 30 game s | `jev.strategy.POSTURE_HOLD_SECONDS` |
| Macro slot's wait for a strategy answer before the local fallback | 28 wall s (24 with two questions) | `jev.strategy.answer_wait_seconds` |
| Expansion-response interval | 5 wall s | `jev.strategy.EXPANSION_RESPONSE_INTERVAL` |
| Production-mix interval | 10 wall s | `jev.strategy.PRODUCTION_MIX_INTERVAL` |
| Accepted execution choice hold | 30 game s | `jev.strategy.EXECUTION_HOLD_SECONDS` |
| Flying types never counted as an air threat | Overlord, Overseer, Observer (and their modes) | `jev.strategy.HARMLESS_AIR_TYPES` |
| Armed-air memory behind the stalker_heavy fallback | 30 game s | v2 `policy.json` `air_threat_age` |
| Supply buffer; reinforcement group; regroup below | 4; 4; 4 units | v2 `policy.json` parameters |
| Expansion trigger (local); farthest site taken | 8 combat units; 1.5 x the nearest path | v2 `policy.json` parameters |

Known gaps (Build notes 217 and 218): the worker target stays at 16 and the natural gets
no Assimilator; max health/shield facts and D3's 40% low-durability regroup are not
built; SC2 path queries and the central Core/Stalker prices are unverified live.

### The live graph

Open `http://localhost:3000/?tab=jev&run=<run_id>`, or follow a launch. For every run:

- **Branch overview** (since `57f7274`). One card per root lane, in its colour
  (economy green, expansion teal, army red, tech purple, and one each for the others):
  its status (active, waiting or idle), current action, latest recorded blocker and
  active/waiting counts, under a line listing the lanes active now. Click a card to
  focus the graph on that branch. The graph opens on the first lane with an active or
  waiting node and stays on the branch you chose while it polls. **Full graph
  (debugging)** shows every archived root (fitted once on entry); **Fullscreen**
  enlarges the graph where the browser allows it. In the graph, the lane's colour is a
  band behind it and a stripe on each node. Status is still the outline, glyph and
  words.
- **Observed edge activity.** Only recorded edges are drawn. A solid edge joins a node
  and its child that both run in the latest state: that tick's path. A dashed edge
  fades over 2 game seconds: an event named that child as the node's deciding branch,
  and it is labeled recent, not current. Nothing between two one-second refreshes is
  invented. A live run animates its current path.
- **Follow activity** (toolbar, off by default) keeps the newest running node of the
  focused branch in view (of any root in the full graph), expanding its branch. It
  never changes the selection, and a manual pan stays until the followed node changes.
  It pauses while the run is not live.
- **Plans and decisions** (v2 runs): each plan's lane, status, stage (**Show in graph**
  switches to its branch and expands to it), blocker or outcome, who decided it and which request, and the
  response in force; each slot's open plan, cooldown and owed turn, and per kind
  whether it may start now or why not (the plan store's own check); the strategy
  choices the graph can execute now; and per question the source (Typesafe, local
  fallback, local graph or awaiting the answer), the choice in force, its request,
  the reason and any pending request.
- **Node sources.** A node whose recorded events name a decision shows it on its box
  (for example `Typesafe req #12`, `local fallback`); the inspector lists that
  evidence (a task or command created under a hosted choice, a plan started or answered
  by a request, a guard's source) and the guards on the node's path with their latest
  evaluations.
- **Recent decisions** (since `57f7274`): the last six recorded events that name a
  question with a choice or a source, newest first: question, choice, source, game time
  and event number.
- **Stale, offline and final.** Nothing animates; the graph, branch overview, plans,
  recent decisions and sources are labeled last known (the branch overview of an ended
  run says when the run ended). Reduced motion (the operating system setting) stops
  every animation even for a live run.

v1 runs and older archives record no plan or v2 decision facts: they keep their
presentation (no plans panel) and gain the lane colours, the recorded edges and, on the
`army` node only, the source its recorded `Army decision source` names (as the Army
decision panel already does).

The automated checks need no SC2 and no paid call:

```powershell
uv run pytest tests/test_jev_api.py tests/test_jev_telemetry.py -p no:cacheprovider
```

The real-browser fixtures (simultaneous lanes; live, offline, stale and final; reduced
motion; an old v1 archive) are opt-in, like section 11's. They passed at `f52d4f6` but
predate `57f7274`'s branch-focused tab and need updating before they are run again: at
least two of the three read lane-legend counts (`data-active`) that the focused page no
longer renders. These failures leave full browser verification incomplete; they do
not by themselves establish whether other viewer regressions exist. Once updated, set
the switch, and optionally an evidence folder for screenshots and a `timeline.jsonl`:

```powershell
$env:JEV_BROWSER_TESTS = '1'
$env:JEV_BROWSER_EVIDENCE_DIR = "$env:TEMP\jev-live-graph"
```

```powershell
uv run --with playwright pytest tests/test_jev_live_graph.py -p no:cacheprovider
```

```powershell
Remove-Item Env:JEV_BROWSER_TESTS, Env:JEV_BROWSER_EVIDENCE_DIR
```

### Before Step 222

Step 222 (issue #339) proves the full v2 decision cycle with real SC2 and the service
(plan Step 222). Its prerequisites:

1. Windows with SC2 and the Simple64 map, `uv sync --extra dev --inexact` and
   `npm --prefix frontend ci` done, and both policies valid
   (`uv run python -m bots.jev.v1 --validate-policy`, then `... bots.jev.v2 ...`).
2. First adapt the live-graph fixtures for `57f7274`'s branch-focused tab (see the
   [handoff](../plans/jev-v2-handoff.md#immediate-action-adapt-the-browser-fixtures)).
   Then run the automated checks above and all nine browser fixtures: section 11's
   six launch tests plus the three live-graph tests. This is a repair verification
   prerequisite for this viewer change; the original M1's once-per-machine browser
   check remains optional for subsequent unchanged runs.

   ```powershell
   $env:JEV_BROWSER_TESTS = '1'
   try {
       uv run --with playwright pytest tests/test_jev_live_graph.py tests/test_jev_launch.py -k "LiveGraph or RealBrowser" -p no:cacheprovider
   } finally {
       Remove-Item Env:JEV_BROWSER_TESTS
   }
   ```
3. The Typesafe key is saved encrypted (`%LOCALAPPDATA%\Alpha4Gate\typesafe-key.dpapi`,
   [the Jev operator guide](jev-validation.md)); the launcher loads it for the game
   process only.
4. v2 is **not** frozen: do not run `--freeze-candidate v2` until the Step 222/223
   candidate is final.
5. Budget: at most three development matches, Terran at difficulties 3, 4 and 5, seed 11,
   in that order, each within the per-match limits (450 requests, 900 game seconds,
   1200 wall seconds).

Each match is one dashboard-first launch. The first uses the launcher's defaults
(v2, Typesafe, Terran, difficulty 3, seed 11):

Run in: fresh PowerShell window @ Alpha4Gate · Model: any (no build work)

```powershell
powershell -File scripts/launch-jev.ps1
```

```powershell
powershell -File scripts/launch-jev.ps1 -Difficulty 4
```

```powershell
powershell -File scripts/launch-jev.ps1 -Difficulty 5
```

Play match 2 and match 3 only if evidence is still missing. After each match, verify its
archive against the API (dashboard backend on :8765):

```powershell
$runId = (Get-ChildItem data\jev\runs -Directory | Sort-Object CreationTime | Select-Object -Last 1).Name
uv run python scripts\validate_jev.py --run-id $runId --api-base http://localhost:8765
```

Watch, and record with run IDs and game times: at least 60 seconds after a v2 question
first becomes eligible, and at least one full match; an accepted `strategy` answer and at least one accepted
execution answer (`expansion_response` or `production_mix`) whose request ID appears on
a plan and on the commands that followed (node sources and **Plans and decisions**);
the second Nexus and the first Stalker; at least one worker or site recovery; and a
pending request cancelled cleanly at the match's end or a Ctrl+C. Every answer must
name `jev-1.13.0`. If natural play omits a required piece of evidence, record that gate
as unverified and pending; never stage a scenario to force it. Also confirm live what
offline tests could not: SC2 `query_pathing` from the start location finds the natural
(Step 217), and the Core 150 / Stalker 125/50/2 prices (Step 218; check with burnysc2's
`calculate_cost`). Note any hypothesis value (table above) that play puts in doubt.
