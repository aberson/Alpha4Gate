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
- **Step 224** adds dashboard-first launch: real benchmark runs will open the dashboard
  on the exact starting run before SC2 starts, with an explicit `--no-dashboard` for
  headless use. Until then the official panels (`baseline`, `heldout-a`, `heldout-b`,
  `attribution`) only dry-run. A real run of one stops with
  `launch_integration_pending` before it copies, finalizes or plays anything. This
  keeps the v1 baseline from being frozen without Step 224's launch hook.
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
- notes: the key requirement and probe of a real hosted run, what a real run would
  capture and finalize, and that this panel waits for Step 224.

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
| `launch_integration_pending` | A real run of an official panel before Step 224 |
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
- Interrupted cases are **not** replayed unless you ask explicitly:

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

## 9. Hosted panels (after Step 224)

The hosted panels need `TYPESAFE_API_KEY` in the process environment. Load the saved
encrypted key as in [the Jev operator guide](jev-validation.md#reuse-the-encrypted-local-key-from-assisted-setup),
never printing it. A missing key stops the run with `missing_configuration` before
anything is copied or played. No scripted fallback is substituted. Each invocation
then sends one tiny probe. If the service refuses the key or answers as another
model, the run stops before any game, and before the v1 baseline is finalized.
Scripted cases never receive the key. Remove it when finished:

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
