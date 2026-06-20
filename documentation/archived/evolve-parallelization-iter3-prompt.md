# Iter-3 hardening prompt — paste into a clean Claude Code session

> **OBSOLETE 2026-05-03.** Fixes 3.1, 3.2, 3.3 shipped in `d2a1e85`. Fix 3.4 was diagnostic-only by this prompt's own §3.4 deferral (root-cause investigation deferred). Steps 8 + 9 of the parallelization plan are CLOSED empirical-pass via the May 1-2 real soaks (production progressed v7 → v12). See `documentation/plans/evolve-parallelization-plan.md` Steps 8 + 9 status lines and issues #248 / #249 for the closeout. Do not act on the instructions below — they are kept for historical context only.

You are Claude Code on Alpha4Gate (`c:\Users\abero\dev\Alpha4Gate`). Master at HEAD has the
evolve-parallelization plan Steps 1-7 merged. Step 8 (operator smoke gate) ran on
2026-04-30 ~22:00 PT, BLOCKED on 4 defects — 1 was fixed mid-session, 3 deferred to this
iter-3 pass.

Magic word for this project: **AmaRocket** (acknowledge in your first response).

## Goal

Land the 4 deferred fixes, then re-run the Step 8 smoke gate. If it passes, Step 9
(4h observation soak) is the final operator step.

## Context

- Plan: `documentation/plans/evolve-parallelization-plan.md` (master)
- Umbrella issue: #235; step issues: #241–#249
- Step 8 BLOCKED comment with full diagnosis: https://github.com/aberson/Alpha4Gate/issues/248
- Two prior fix commits to read first: `5997dee` (worker `dev_apply_fn`) + `6cdec81`
  (`-m bots.current.runner`)
- Master tip on session start should be `91f4316` ("docs(plan): mark step 8 BLOCKED")
- `bots/current/current.txt` → `v7` (production runtime — 4 promotions during this plan
  came from the user's other-window evolve runs)

## Defects to fix (in this order — they unblock each other)

### Fix 3.1 — `bots/cand_*` startup cleanup

**File:** `scripts/evolve.py::_cleanup_stale_round_files` (around line ~1597–1640).

Today the helper unlinks `evolve_round_*.json`, `evolve_imp_*.json`, `evolve_result_*.json`
files but NOT the `bots/cand_<uuid>/` snapshot directories themselves. After 3 partial
runs the operator had 33 leftover scratch dirs.

**Action:** extend the helper to also `_drvfs_safe_rmtree` every `bots/cand_*` directory
under `_repo_root() / "bots"`. Use the existing `_drvfs_safe_rmtree` from
`orchestrator.snapshot` (imported at scripts/evolve.py top — verify). Guard: do NOT
remove the in-flight worker dirs (none exist at startup; this only runs before the
dispatcher loop begins, so safe).

**Test:** `tests/test_evolve_parallel.py::test_cleanup_stale_round_files_unlinks_pre_existing`
exists; extend it to also pre-create `bots/cand_a1b2/` and `bots/cand_dead1/` directories
and assert they're rmtree'd. Add a sibling test `test_cleanup_preserves_versioned_dirs`
that pre-creates `bots/v0/`, `bots/v7/`, `bots/v99/` and asserts they're left alone.

### Fix 3.2 — Worker subprocess group isolation

**File:** `scripts/evolve.py::_run_fitness_phase_parallel` — the `subprocess.Popen(argv, ...)`
call.

The dispatcher's signal handler calls `proc.send_signal(SIGINT)` / `proc.kill()` on the
worker Python process directly. SIGINT/SIGKILL on the immediate child does NOT cascade to
grandchildren (the worker's `claude -p` CLI subprocess + SC2_x64 burnysc2 spawned).
On Linux/WSL this leaves orphan grandchildren. On Windows, same.

**Action:** spawn workers with their own process group:

```python
import os, signal, subprocess
import sys

if sys.platform == "win32":
    popen_kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
else:
    popen_kwargs = {"start_new_session": True}

proc = subprocess.Popen(argv, **popen_kwargs)
```

Then in the signal handler, replace `proc.send_signal(sig)` and `proc.kill()` with
process-group equivalents:

```python
def _sigkill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        # CTRL_BREAK_EVENT to the process group
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            pass
        # then hard-kill via taskkill /T /F
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True, check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
```

Apply at the hang-cap path AND the second-SIGINT escalation path. Inline-comment Decision
D-3 since this is operationally what makes the process-level fan-out actually work.

**Test:** add `tests/test_evolve_parallel.py::test_sigkill_tree_calls_killpg_on_posix`
and a Windows sibling that mock `os.killpg` / `subprocess.run` and assert they're called
with the right args. Pin the new Popen kwargs via `subprocess.Popen.call_args` capture.

### Fix 3.3 — Surface worker crash payload in dispatcher's crash log

**File:** `scripts/evolve.py` around line 2018-2046 (the `if rc != 0:` branch in
`_run_fitness_phase_parallel`'s reaper loop).

Current code creates its own `RuntimeError("worker exited non-zero: returncode=N")` and
calls `_record_parallel_failure` with that. The worker's actual traceback (written via
`evolve_worker._write_crash` to `--result-path`) is at `dispatched.result_path` and gets
unlinked in the cleanup loop without ever being read.

**Action:** before classifying as `crash`, attempt to read `result_path` JSON. If it
parses and has `"crash": True` shape (worker-written crash payload from
`evolve_worker._write_crash`), fold its `error_type`, `error_message`, `traceback` into
the recorded crash. Schema:

```json
{
  "crash": true,
  "error_type": "NotImplementedError",
  "error_message": "...",
  "traceback": "Traceback (most recent call last):\n  ..."
}
```

Concrete shape:

```python
if rc != 0:
    # Try to read the worker's own crash payload for the real traceback.
    worker_crash: dict[str, Any] | None = None
    try:
        text = dispatched.result_path.read_text(encoding="utf-8")
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("crash") is True:
            worker_crash = parsed
    except (OSError, json.JSONDecodeError):
        pass

    if worker_crash:
        crash_exc = _make_crash_exc(rc, worker_crash=worker_crash)  # or similar
    else:
        crash_exc = _make_crash_exc(rc)
    # ... rest of existing crash-bucket bookkeeping
```

Update `_make_crash_exc` to optionally accept a worker_crash dict and embed
`error_type`+`error_message` into the exception message. The traceback gets logged via
`_log.error(..., exc_info=False)` plus a separate `_log.error("worker traceback:\n%s",
worker_crash["traceback"])` line.

Update `append_crash_log` (or call it twice) so `data/evolve_crashes.jsonl` carries the
worker's traceback under a new `worker_traceback` field, alongside the dispatcher's own
field.

**Test:** add `tests/test_evolve_parallel.py::test_worker_crash_payload_surfaces_in_log`.
Use the existing `_FakePopen` fixture; have the fake worker write a crash payload to
`result_path` before exiting nonzero. Assert the recorded JSONL row has
`worker_traceback` containing the fake traceback string AND `error_type` matches the
worker's reported type, NOT the dispatcher's generic `RuntimeError`.

### Fix 3.4 — Dispatcher signal-handler reliability investigation

**The hardest one.** Symptom from the smoke gate: operator pressed Ctrl+C twice in the
dispatcher's WSL terminal, but the dispatcher kept dispatching new generations
(`run_id c6b7f509` was a fresh uuid generated 26 min after start). Eventually killed by
direct `kill -9 <PID>`. Workers ended up reparented to the bash shell, not the
dispatcher.

Possible root causes (investigate in order):

1. **Signal delivery to Python while in `subprocess.Popen.poll()` busy-loop:** if the
   poll loop is in C (which it isn't — `time.sleep(0.5)`), signals queue but don't
   interrupt. With `time.sleep`, signals SHOULD be delivered between sleeps. Verify
   the handler actually fires by adding `_log.warning("SIGINT received, count=%d",
   interrupt_count)` at the top of `_signal_handler` and re-running.
2. **`pending.clear()` inside the handler doesn't actually clear** because `pending` is
   captured by reference but the dispatcher loop holds a different binding (e.g. a
   list passed in vs reassigned). Check that `pending` is mutated in-place not rebound.
3. **`stop_dispatching = True` via `halt_state["stop_dispatching"]`** — verify the loop
   actually reads the shared state on every iteration, not just at start.
4. **Reparenting clue:** workers' parent PID was `231894` (bash) not the dispatcher
   `233852`. That suggests the dispatcher python died at some point but its `subprocess`
   children survived (re-parented to init/bash). If the dispatcher python is dead but
   somehow workers spawned NEW (run_id c6b7f509 was fresh), there's a deeper flow
   issue. Maybe two evolve.py processes ran concurrently from the operator's two Ctrl+C
   attempts? Investigate `ps -ef` history if reproducible.

**Action:** start by adding diagnostic logging to the signal handler (above) and
re-running the smoke gate. Once the actual failure mode is clear from logs, write the
fix.

If the issue is hard to reproduce quickly, defer this to a follow-up issue and ship
fixes 3.1-3.3 alone. Defect #1 was masking #4's severity — with worker crashes resolved,
the dispatcher's run-to-completion flow should be much shorter and the signal-handling
edge may not bite as hard.

## Re-run procedure (after fixes land)

1. Operator opens 3 terminals:
   - PowerShell: `uv run python -m bots.current.runner --serve`
   - PowerShell: `cd frontend; npm run dev`
   - WSL: `wsl -d Ubuntu-22.04`, `cd /mnt/c/Users/abero/dev/Alpha4Gate`
2. WSL: `SC2_WSL_DETECT=0 uv run --project . python scripts/evolve.py --concurrency 2 --pool-size 2 --hours 0 --games-per-eval 1 --no-commit`
3. Watch dashboard at `http://localhost:3000/evolution` — expect mirror_games → claude_prompt → fitness phase with 2-card grid (`[W0]` + `[W1]` badges).
4. Each worker now actually runs `spawn_dev_subagent` (real Claude API call, ~1-3 min each)
   then a 1-game SC2 match. Total wall-clock ~5-10 min.
5. Run completes; assert:
   - Both workers' `data/evolve_round_*.json` flipped to `active=False` post-run
   - `bots/cand_*` count is 0 post-run
   - If any worker crashed, `data/evolve_crashes.jsonl` has the worker's actual
     traceback (Fix 3.3)

If clean: Step 8 PASS. If still BLOCKED: report findings + iterate.

## Context to load on session start

```bash
cd c:\Users\abero\dev\Alpha4Gate
git log --oneline -8                          # confirm 91f4316 at top
gh issue view 248                             # the BLOCKED comment
uv run pytest -q | tail -3                    # baseline (~1411+20)
uv run mypy src bots --strict | tail -3       # baseline clean
uv run ruff check . | tail -3                 # baseline clean
ls bots/cand_* 2>/dev/null | wc -l            # operator may have left some; clean if so
```

## Memories to honor

- `feedback_evolve_powershell_launch_canonical.md` — operator uses interactive WSL shell, not `wsl -- bash -lc`
- `feedback_wsl_distro_ubuntu_22_04_specific.md` — Ubuntu-22.04 is the only distro with the working venv
- `feedback_dont_commit_evolve_scratch.md` — `bots/cand_*` orphans don't mean anything was promoted
- `feedback_verify_primary_source_in_writing.md` — quote actual file/log content; don't reason from memory
- `feedback_user_powershell.md` — user prefers PowerShell for operator commands; bash fine for WSL/git internals

## Don't

- Don't try to run the smoke gate from `/build-step` itself (real-SC2 requires WSL + active backend + active frontend)
- Don't use `pkill -f "scripts/evolve"` casually — it matches both dispatcher and worker; intent matters
- Don't skip the `start_new_session=True` change just because tests look passable; that's the actual operational fix
- Don't leave `bots.cand_*` cleanup as a "nice to have" — it accumulated 33 dirs in one session; this is real

End of prompt. Acknowledge with the magic word, then start with Fix 3.1.
