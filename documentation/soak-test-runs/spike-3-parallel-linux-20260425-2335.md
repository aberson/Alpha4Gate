# Spike 3 — 4-way Parallel Self-play on Linux (Phase 8 Step 4)

**Status:** PASSED
**Date:** 2026-04-25 23:35:51 → 23:41:14 local (~5.4 min wall-clock for the successful run)
**Timebox:** 8 hours; used ~25 min total including 2 sampler-debug iterations
**Operator:** Claude Code session (Opus 4.7) under user supervision
**Plan:** [phase-8-build-plan.md §7 Step 4](../plans/phase-8-build-plan.md)
**Halt condition:** RAM blowup or heavy crashes → reassess phase scope. **Did not trigger.**

---

## Summary

Four concurrent `scripts/selfplay.py --p1 v0 --p2 v0 --games 5 --map Simple64` invocations completed in **323 seconds wall-clock** with **0% crash rate** (20/20 games clean, all `error=null`). Peak concurrent SC2 processes: **8** (exactly the expected 4 games × 2 each). Peak total resident memory across all SC2 processes: **6,802,072 kB ≈ 6.5 GB**. Peak per-process RSS: **915,740 kB ≈ 894 MB** — at the high end of the plan's 600 MB ± 200 MB estimate, but still ≈1.7×–2.8× lower than the Windows baseline of 1.5–2.5 GB/instance. Per-game wall-clock under 4-way contention is **~64 s** (vs **~47 s** unloaded in Spike 2 → 37% slowdown per game), but the parallel speedup is **~2.9×** end-to-end (323 s vs an extrapolated 940 s serial baseline). The dominant Spike-3 unknown ("how big is the unlock?") is settled: **the unlock is real and 4-way parallelism is viable on this single Linux host with no contention or memory pressure.** Phase 8 is GO for Step 5 (SC2PATH default fix) and beyond.

## Done-when checklist (per plan §7 Step 4)

- [x] 4 concurrent `selfplay.py` invocations complete (4/4 exit code 0)
- [x] Per-process resident RAM measured (peak 894 MB; sample of 263 readings @ 1 Hz)
- [x] Crash rate < 5% (0/20 = 0%)
- [x] Total wall-clock recorded vs serial baseline (323 s vs 940 s extrapolated → 2.9× speedup)
- [x] `PRAGMA journal_mode` on `bots/v0/data/training.db` checked → already **`wal`** (no Step 5 follow-up needed for SQLite)

## Measurements

### Throughput

| Metric | Value |
|---|---|
| Total games run | 20 (4 invocations × 5 games each) |
| Wall-clock | 323 s (≈ 5.4 min) |
| Per-game wall-clock under 4-way load | ≈ 64.6 s |
| Per-game wall-clock unloaded (Spike 2 baseline) | 47 s |
| Per-game contention slowdown | +37% (47 → 64.6 s) |
| Serial baseline (extrapolated): 20 × 47 s | 940 s ≈ 15.7 min |
| Parallel speedup | **2.9×** |

### Memory

| Metric | Value |
|---|---|
| Peak concurrent SC2 PIDs | 8 (matches plan expectation: 4 × 2) |
| Time spent at 8-PID steady state | 241/263 samples = **92%** of run |
| Peak total RSS (8 procs) | 6,802,072 kB ≈ **6.5 GB** at 23:40:24 |
| Peak per-process RSS | 915,740 kB ≈ **894 MB** at 23:41:09 (5-proc state) |
| Average per-process RSS at 8-proc steady state | ≈ 840 MB (6.5 GB ÷ 8) |
| Plan estimate per-process | 600 MB ± 200 MB |
| Spike 1 single-sample baseline | 862 MB |
| Windows baseline per investigation | 1500–2500 MB/instance |
| **Implied Linux unlock** | **1.7×–2.8× per-instance memory savings** |

### Crash rate

| Per-invocation | Games | error=null | Notes |
|---|---|---|---|
| parallel-1 | 5 | 5 | clean |
| parallel-2 | 5 | 5 | clean |
| parallel-3 | 5 | 5 | clean |
| parallel-4 | 5 | 5 | clean |
| **Total** | **20** | **20** | **0% crash rate** |

All games drew (`winner=null`) — same `game_time_limit=300` ceiling as Spike 2; orthogonal to Spike 3's halt conditions. The aiohttp `ClientConnectionResetError` teardown noise observed in Spike 2 also fires here at game-end and remains benign (no `error` field populated on any record).

## Environment

- Host: Windows 11, WSL2 Ubuntu 22.04 (`VERSION_ID="22.04"`)
- Python: 3.12.13, uv: 0.11.7, burnysc2: 7.1.3
- SC2: `Base75689` (game version 4.10), 4-way concurrent SC2 server processes
- venv: `~/venv-alpha4gate-linux/` (4.8 GB, ext4 native)
- Repo state: `master` @ `503f457`
- DB journal mode: `wal` (already set; no Phase 8 SQLite work needed)

## Operator commands actually executed

The plan-doc inline shell loop was promoted to `scripts/spike3_launch.sh` (now part of this commit) for two reasons: (1) the `wsl bash -lc 'multi-line script'` pattern is fragile per `feedback_wsl_bash_lc_heredoc_fragile.md`; (2) the launcher needed an inline RSS sampler to coexist with the 4 selfplay processes and emit timestamped per-second samples to `/tmp/spike3_rss.csv`. The launcher is reusable for the Step 11 long soak.

```bash
wsl -d Ubuntu-22.04 -- bash -lc 'bash /mnt/c/Users/abero/dev/Alpha4Gate/scripts/spike3_launch.sh'
```

Then analysis via `bash /mnt/c/Users/abero/dev/Alpha4Gate/scripts/spike3_analyze.sh` against `/tmp/spike3_rss.csv`.

## Findings (3 plan-doc / tooling deviations from this spike)

### 1. Plan-doc Step 4 used `--results-out`, but `selfplay.py` argparse exposes `--results-path`. PATCHED.

The spike-3 launcher initially emitted `argparse: unrecognized arguments: --results-out` and exited code 2. Confirmed via `scripts/selfplay.py:72` (`"--results-path"`). Fixed in `documentation/plans/phase-8-build-plan.md` line 257. Ironically a comment in the same plan-doc Step 6 (line 339) already warned: *"do not pass a custom --results-out unless the actual flag exists in argparse"* — yet Step 4 violated it. This is a textbook instance of `feedback_verify_plan_cli_commands.md`.

### 2. `pidof SC2_x64` returned **empty** for the entire 305-sample run when called from a backgrounded subshell of the launcher script — yet works fine when called directly via `wsl -d Ubuntu-22.04 -- bash -lc 'pidof SC2_x64'`.

I never definitively root-caused this. A standalone diagnostic confirmed `pidof SC2_x64` returns the right PIDs against a live `selfplay.py`-spawned SC2 process. But inside the launcher's `( while true; ... ) &` subshell, every `pidof` invocation returned empty, producing 305 zero-rows. Switched detection to direct `/proc/[0-9]*/exe` scan (`readlink "$procdir/exe" → */SC2_x64`) — that matched 8 PIDs reliably from the very first iteration. The /proc-direct approach is also faster (no fork) and is now the documented sampler pattern.

### 3. `ps -p $pids -o rss=` with multiple space-separated PIDs returns **empty** on Ubuntu 22.04 procps-ng.

After the /proc-scan fix, the sampler still wrote zero-rows because `ps -p 27716 27717 27718 ...` returned empty. The single non-zero row in the second run came from a moment when only 1 SC2 PID was alive — `ps -p <single-pid>` works. Replaced `ps -p $pids -o rss=` with a per-PID loop reading `awk '/^VmRSS:/ {print $2}' /proc/$pid/status`. This is also faster (no per-iteration ps fork × N PIDs).

The two sampler bugs combined burned ~15 min of timebox. Both are now fixed in `scripts/spike3_launch.sh`. The reusable lessons are saved as memories.

## Verification of negative claims

- ✅ **No port collisions.** Eight distinct `--GamePort` values appear across the 4 invocations' run logs; the port-collision patch in `scripts/selfplay.py` cleanly retried any contended bindings (Spike 2 already validated this on a single invocation; Spike 3 confirms it scales to 4 concurrent self-play instances on Linux).
- ✅ **No signal-thread errors.** All 4 invocations log `worker-thread signal patch installed` once at startup; no `ValueError: signal only works in main thread` anywhere.
- ✅ **No mid-game crashes.** All 20 records have `error=null`. The aiohttp `ClientConnectionResetError` tracebacks fire on every game's teardown phase (proxy → already-closed bot WS) but are caught by `sc2.proxy.proxy_handler` and don't propagate to the game record.
- ✅ **No orphaned processes.** Sampler shows transition through 8 → 7 → 6 → 5 → 4 → 2 → 1 → 0 SC2 PIDs at the end of the run as games shut down in staggered fashion. Final state: 0 SC2 processes.
- ✅ **No RAM blowup.** Peak total RSS 6.5 GB; well within the typical WSL2 default memory budget (≈ 16 GB on a modern dev machine, ≈ 8 GB minimum). Steady-state ≈ 5.5 GB.

## Halt-condition decision

NONE of the documented halt conditions triggered:
- ✅ No RAM blowup (peak 6.5 GB, well within budget)
- ✅ No heavy crashes (0% crash rate)

Phase 8 proceeds to **Step 5 (SC2PATH default fix — `src/orchestrator/paths.py`)** in a future session.

## Implications for downstream Phase 8 steps

| Step | Implication |
|---|---|
| Step 5 (resolver) | No new findings. Proceed as planned. |
| Step 6 (Linux dev-deps) | No new findings. Proceed as planned. |
| Step 7 (smoke gate) | No new findings. Proceed as planned. |
| Step 8 (Linux CI) | No new findings. Proceed as planned. |
| Step 9 (Dockerfile) | RAM budget per container ≈ 1 GB headroom over 4 SC2 procs (i.e. ~7-8 GB / container if running 4 parallel selfplay invocations); for production cloud workers, `--memory=8g` is a reasonable Docker run limit. |
| Step 11 (long soak) | 4-way parallelism gives ~2.9× throughput. A 24-hour soak at 4-way produces ~2.9× more data than serial. The launcher in `scripts/spike3_launch.sh` is reusable for the soak; just bump `--games 5` to `--games N`. Recommend monitoring RSS via the same /proc/status sampler — peak should stay within 7-8 GB. |
| Step 12 (cloud) | Single-instance Cloud spec: 4-vCPU, 8 GB RAM should comfortably run 4-way parallel selfplay. Halve to 2-way (4 GB RAM) for the cheaper instance class if cost-sensitive. |
| SQLite | Already on `wal`. No follow-up. |

## RSS sampling raw artifacts

- `/tmp/spike3_rss.csv` — 264 rows (1 header + 263 samples), 1 Hz cadence
- `/tmp/spike3_sampler_debug.log` — per-20-iteration debug log confirming the sampler ran and saw the right PIDs
- `/tmp/spike3_run-{1..4}.log` — per-invocation selfplay logs
- `data/selfplay_results.parallel-{1..4}.jsonl` — 5 records each, all `error=null`

CSV peak rows (top 5 by total RSS):

```
23:36:07,8,6704504,880772
23:37:13,8,6700300,884464
23:38:19,8,6734192,898116
23:40:24,8,6802072,911868   ← peak total
23:41:08,8,6599020,915564
```

Distinct pid_count distribution:

```
241 samples × 8 PIDs (steady state — 92% of run)
 11 × 4    1 × 7    1 × 6    1 × 5    3 × 1    1 × 2    4 × 0 (pre-startup)
```

## Next session

Step 5 — SC2PATH default fix. Plan §7 Step 5 spec is solid; no new findings here would change it. Implementation creates `src/orchestrator/paths.py` exposing `resolve_sc2_path()` and replaces 6 hardcoded Windows callsites. The existing `feedback_per_version_vs_cross_version_data_dir.md` memory is relevant if the resolver ends up touching `bots/{v0,v1,v2}/config.py:49`.

## Memory entries created/updated in this spike

- `project_headless_linux_training_opportunity.md` (updated — Spike 3 PASSED, Step 5 next)
- `feedback_msys2_unquoted_path_to_wsl.md` (new — `wsl -- bash <unquoted /mnt/c/...>` gets MSYS-rewritten)
- `feedback_ps_p_multi_pid_returns_empty.md` (new — `ps -p A B C` returns empty on procps-ng; use comma or per-PID /proc/status)
- `feedback_pidof_unreliable_in_subshell.md` (new — pidof failed silently in our launcher subshell; /proc/exe direct scan is the safe pattern for SC2 detection)
- `MEMORY.md` index updated
