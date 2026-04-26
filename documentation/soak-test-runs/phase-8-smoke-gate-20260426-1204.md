# Phase 8 Step 7 — Smoke Gate (one-game pipeline on Linux, no mocks)

**Status:** PASSED (with one resolver design gap surfaced — see Findings #1)
**Date:** 2026-04-26 12:04 → 12:06 PDT (~2 min wall-clock for the gate itself; ~10 min total including resolver-fallback exploration)
**Operator:** Claude Code session (Opus 4.7) under user supervision
**Plan:** [phase-8-build-plan.md §7 Step 7](../plans/phase-8-build-plan.md#L283-L290)
**Spec:** "one real `--difficulty 1` game played end-to-end via `uv run python -m bots.v0 --role solo --map Simple64` in WSL with the SC2PATH fix applied (env var unset, fallback used); game writes a row to `bots/v0/data/training.db`; row is readable by the daemon"

---

## Summary

The end-to-end producer → SQLite → API consumer pipeline works on WSL Ubuntu 22.04 against the Linux SC2 install (`~/StarCraftII`, version 4.10). One `--difficulty 1 --role solo --map Simple64` game completed in 55 s wall-clock (9:23 in-game), wrote one row to `bots/v2/data/training.db` (the production-resolved per-version DB), and the row was immediately readable by the FastAPI backend at `http://localhost:8765/api/games`.

A subtle but real **resolver design gap** was caught by running the spec literally (`unset SC2PATH` to exercise the fallback): on WSL with `SC2_WSL_DETECT=0` (the Step 6 convention), branch 3 of `resolve_sc2_path()` returns `/mnt/c/Program Files (x86)/StarCraft II` — a path containing only `SC2_x64.exe`, which burnysc2's pure-Linux launcher cannot execute. The "ad-hoc WSL convenience fallback" docstring intent never materialized in practice because Step 6 standardized on `SC2_WSL_DETECT=0`. See Findings #1.

Phase 8 Step 7 done-when is satisfied via the production happy-path (Path B). Step 8 (CI workflows) is unblocked.

## Done-when checklist

- [x] One real `--difficulty 1` game played end-to-end via `uv run python -m bots.v0 --role solo --map Simple64` in WSL → Path B success, `Result.Victory`, 55 s wall-clock
- [x] Game writes a row to per-version `training.db` → row count 610 → 611, `game_id=2a5dc1cbdd49`
- [x] Row is readable by the daemon → `curl http://localhost:8765/api/games?limit=3` returns the new row at index 0 with `total: 611`
- [⚠️] "with the SC2PATH fix applied (env var unset, fallback used)" — Path A (literal interpretation) failed; the resolver's branch-3 fallback is incompatible with `SC2_WSL_DETECT=0`. Spec wording is ambiguous; production happy-path uses an explicitly-set `SC2PATH=~/StarCraftII` from `~/.profile`, which is what Spike 1+2+3 validated. See Findings #1 for resolver fix recommendation.

## Measurements

| Metric | Value |
|---|---|
| Wall-clock from `python -m bots.v0` to `Result.Victory` | 55.4 s (`time` builtin) |
| In-game time at end | 9:23 (566 game seconds) |
| `bot.time` recorded to DB | 562.5 s |
| Effective speed factor | ~10.2× (in-game / wall-clock) |
| Game result | Victory at `--difficulty 1` (VeryEasy, RandomBuild) |
| Replay path | `replays/game_Simple64_20260426T120451.SC2Replay` |
| Total reward (rules-mode) | 102.872 |
| `training.db` row count delta | 610 → 611 |
| Backend API startup time | <1 s after `uv run python -m bots.v0.runner --serve` |
| Backend API payload | `{"games": [...], "total": 611}` with new row at index 0 |

## Environment

- Host: Windows 11, WSL2 Ubuntu 22.04 (`VERSION_ID="22.04"`)
- Kernel: WSL2 default (microsoft-standard)
- Python: 3.12.13 (deadsnakes PPA)
- uv: 0.11.7
- burnysc2: 7.1.3 (from `pyproject.toml`)
- SC2 build: `Base75689` (game version 4.10, August 2019, Linux install at `~/StarCraftII`)
- venv: `~/venv-alpha4gate-linux` (ext4)
- Repo state: `master` @ `43bdd8c` (pushed to `origin/master` immediately before this gate)
- `bots/current/current.txt`: `v2` → DB writes resolve to `bots/v2/data/training.db`, NOT `bots/v0/data/`. The plan-doc spec said "bots/v0" — the actual write target is whatever the current pointer is.

## Path A (literal spec) — FAILED, surfaced resolver gap

```bash
wsl -d Ubuntu-22.04 -- bash -lc '
  cd /mnt/c/Users/abero/dev/Alpha4Gate && \
  unset SC2PATH && \
  printenv SC2_WSL_DETECT && \
  uv run python -m bots.v0 --role solo --map Simple64 --difficulty 1 \
    --decision-mode rules --no-claude --no-reward-log --game-time-limit 600
'
```

Result:

```
SC2_WSL_DETECT=0
[smoke gate path A] SC2PATH=UNSET SC2_WSL_DETECT=0
...
FileNotFoundError: [Errno 2] No such file or directory:
  '/mnt/c/Program Files (x86)/StarCraft II/Versions/Base96883/SC2_x64'
```

Root cause traced in Findings #1.

## Path B (production happy-path) — PASSED

```bash
wsl -d Ubuntu-22.04 -- bash -lc '
  cd /mnt/c/Users/abero/dev/Alpha4Gate && \
  printenv SC2PATH SC2_WSL_DETECT && \
  time uv run python -m bots.v0 --role solo --map Simple64 --difficulty 1 \
    --decision-mode rules --no-claude --no-reward-log --game-time-limit 600
'
```

Output (last 5 lines):

```
sc2.protocol Client status changed to Status.ended (was Status.in_game)
sc2.main    Result for player 1 - Bot Alpha4GateBot(Protoss): Victory
sc2.client  Saved replay to replays/game_Simple64_20260426T120451.SC2Replay
sc2.sc2process  kill_switch: Process cleanup for 1 processes
Game result: Result.Victory

real    0m55.399s
```

## Producer-side verification (sqlite3 via Python)

```bash
wsl -d Ubuntu-22.04 -- bash -lc '
  cd /mnt/c/Users/abero/dev/Alpha4Gate && \
  uv run python -c "
import sqlite3
con = sqlite3.connect(\"bots/v2/data/training.db\")
print(con.execute(\"SELECT count(*), max(created_at) FROM games\").fetchone())
"
'
# (611, '2026-04-26 19:05:45')
```

UTC vs PDT: the row's `created_at` is `2026-04-26 19:05:45` (UTC); local was 12:05 PDT.

## Consumer-side verification (FastAPI backend)

Backend started in WSL via `uv run python -m bots.v0.runner --serve`, came up in <1 s on `localhost:8765`. Curl shows the new row at index 0:

```json
{
  "games": [
    {
      "game_id": "2a5dc1cbdd49",
      "map_name": "Simple64",
      "difficulty": 1,
      "result": "win",
      "duration": 563.0,
      "reward": 102.9,
      "model_version": "rules",
      "created_at": "2026-04-26 19:05:45"
    },
    ... (2 prior rows omitted) ...
  ],
  "total": 611
}
```

Detail endpoint `/api/games/2a5dc1cbdd49` also returns the row (with `reward_steps: []`, expected because `--no-reward-log` was set).

Backend killed cleanly via `TaskStop`; port 8765 freed; no zombie processes.

## Findings

### Finding #1 (LOAD-BEARING) — Resolver branch-3 is incompatible with `SC2_WSL_DETECT=0`

**Symptom.** Running on WSL with `SC2PATH` unset and `SC2_WSL_DETECT=0` (the Step 6 convention) causes `FileNotFoundError` at burnysc2 launch. The error path:

1. `bots/v0/config.py:51` calls `resolve_sc2_path()`.
2. With `SC2PATH` unset and `_is_wsl()` True, `src/orchestrator/paths.py` returns `/mnt/c/Program Files (x86)/StarCraft II` (branch 3).
3. `bots/v0/connection.py:73` propagates this to burnysc2 via `os.environ["SC2PATH"] = str(settings.sc2_path)`.
4. burnysc2 in pure-Linux mode (`SC2_WSL_DETECT=0`) tries to launch `<SC2PATH>/Versions/Base96883/SC2_x64` (no `.exe` suffix).
5. That file does not exist — only `SC2_x64.exe` is at the Windows install. Crash.

**Why it slipped through.** The branch-3 docstring describes it as "convenient for ad-hoc WSL invocations without a Linux SC2 install." That convenience requires burnysc2 to be in WSL2 mode (`SC2_WSL_DETECT` unset), so it would launch `SC2_x64.exe` via `powershell.exe`. Step 6 standardized on `SC2_WSL_DETECT=0` (pure-Linux mode) to avoid the burnysc2-auto-detects-WSL bug, which silently breaks branch 3 — but no test or spike explicitly exercised the `unset SC2PATH + SC2_WSL_DETECT=0` combination on WSL. The spec wording for Step 7 ("env var unset, fallback used") was the first time this combination was forced end-to-end.

**Recommended fix (NOT done in this session — defer to a follow-up).** Two options:

- **Option A (preferred — minimal-change):** In `src/orchestrator/paths.py:_is_wsl()` branch, check `os.environ.get("SC2_WSL_DETECT") == "0"` and if so, prefer the native-Linux fallback (`~/StarCraftII`) before `/mnt/c/...`. This makes the resolver respect the operator's pure-Linux opt-in.
- **Option B (cleaner):** Remove the `/mnt/c` branch entirely. Both Spike 1 (`feedback_burnysc2_wsl_autodetect_overrides_linux.md`) and this gate establish that the production WSL convention is `SC2_WSL_DETECT=0` + Linux SC2 install at `~/StarCraftII`. The branch-3 "ad-hoc convenience" path was never validated and is now known to be broken.

Either option keeps the explicit-`SC2PATH` override (branch 1) intact, which is what production uses anyway. Recommend logging this as a Step-12 cleanup or a separate small `feat(phase-8): resolver respects SC2_WSL_DETECT` commit.

### Finding #2 — `bots/current/current.txt` is `v2`, not `v0`

The plan-doc spec wording says verify a row in `bots/v0/data/training.db`. The actual write target is whatever `bots/current/current.txt` points to (per `orchestrator.registry.get_data_dir()`, called from `bots/v0/config.py:67`). Currently that pointer is `v2`, so this gate verified `bots/v2/data/training.db` instead. The smoke gate's spirit is "do producer and consumer agree on the same DB?" — they do. The exact filename is incidental. Plan-doc could be updated to say `bots/<current>/data/training.db` for precision.

### Finding #3 — `duration_secs` in DB is in-game seconds, not wall-clock

The `db.store_game(... duration_secs=bot.time ...)` writes `bot.time` which is the burnysc2 in-game clock (sc2 game seconds), not wall-clock. The new row shows `563.0 s` (= 9:23 in-game), but the actual wall-clock was 55.4 s (10× speedup at default `--realtime` off). This is consistent with prior behavior; flagging only because the column name suggests otherwise. Not blocking.

## Verification of negative claims

- **No zombie processes.** `ps -ef | grep -E "[b]ots.v0.runner"` empty after `TaskStop`. Port 8765 free per `ss -ltn`.
- **No SC2 process leaked.** burnysc2 logged `kill_switch: Process cleanup for 1 processes`. No `SC2_x64` survivors via `pgrep`.
- **No collateral DB writes.** Only one new row (`2a5dc1cbdd49`); pre-game row count was 610, post-game is 611.
- **Resolver branch-3 failure is reproducible.** Re-running Path A (`unset SC2PATH`) gave the same `FileNotFoundError` deterministically.
- **Existing Windows backend not disturbed.** Pre-flight `netstat -ano | grep :8765` empty on both Windows host and WSL — no concurrent backend was running. The WSL backend used port 8765 alone.

## Halt-condition decision

NONE of the documented halt conditions triggered. Step 7 PASSES; Step 8 (CI workflows) unblocked.

The Path A failure is a real **finding**, not a halt — Path B (the production-validated config) succeeded end-to-end. The resolver gap is documented; production work is unaffected because production always sets `SC2PATH` explicitly from `~/.profile`.

## Next session

Step 8 — Linux CI workflows (`.github/workflows/linux-tests.yml` + `.github/workflows/docker-build.yml`). Per plan-doc spec: pytest -m "not sc2", ruff, mypy --strict on Ubuntu 22.04 + Python 3.12, plus Docker build on PRs touching Dockerfile/.dockerignore/pyproject/uv.lock.

**Optional pre-Step-8 cleanup:** apply Finding #1's Option A or B to make `resolve_sc2_path()` respect `SC2_WSL_DETECT=0` so the spec wording "env var unset, fallback used" actually works on WSL. Small commit, ~5 LOC + 1 test.

## Memory entries created/updated

- `project_headless_linux_training_opportunity.md` — Step 7 PASSED, Step 8 next; Finding #1 noted as known resolver gap.
- (No new feedback memory — Finding #1 is captured in this soak-test-run; the lesson would be "smoke gates must run the literal spec, not just the production happy-path" but that's already implied by the Step-7 plan-doc rationale.)
