# Operator commands — Alpha4Gate cheat sheet

The commands you'll actually need, organized by what you're trying to do.
Copy-paste targets, not exhaustive reference. For full details, follow the
links into build docs, plan docs, and per-skill SKILL.md files.

**Conventions in this doc:**
- Lines starting `PS>` are PowerShell on Windows.
- Lines starting `$` are bash inside WSL or a Linux container.
- All paths assume the repo is at `$env:USERPROFILE\dev\Alpha4Gate` (Windows
  view) / `/mnt/c/Users/x/dev/Alpha4Gate` (WSL view).

---

## Quick orientation

```powershell
PS> Set-Location $env:USERPROFILE\dev\Alpha4Gate
PS> git status                                 # tree clean?
PS> git log --oneline -5                       # recent activity
PS> Get-Content data\evolve_run_state.json     # is an evolve run going?
PS> cat bots\current\current.txt               # which bot version is current?
```

---

## Common activities (start here)

The three things you actually do day-to-day. Pick the path that matches the
context you're in (Claude Code session vs. raw shell). Detailed mechanics
for each are linked into the deeper sections below.

### 1. Improve-bot-evolve — autonomous evolutionary loop

Generates 10 Claude-proposed improvements per generation, fitness-tests each
vs the current parent, stacks winners into a new `vN+1` snapshot, regression-
checks, repeats until budget exhausted. Promoted versions land as `[evo-auto]`
commits on master.

**Path A — slash command in Claude Code (canonical, autonomous):**

Type one of these in Claude Code; the skill handles pre-flight, baseline tag,
backend boot, and the morning report.

```
/improve-bot-evolve                                        # 4h, serial, default
/improve-bot-evolve --hours 8                              # longer soak
/improve-bot-evolve --concurrency 4 --hours 4              # 4-way parallel
/improve-bot-evolve --resume                               # pick up an interrupted pool
```

Skill spec: [.claude/skills/improve-bot-evolve/SKILL.md](../../.claude/skills/improve-bot-evolve/SKILL.md).

**Path B — direct script, Windows shell (no Claude Code):**

You drive pre-flight yourself; useful when running detached overnight without
an open Claude Code window. See [§Evolve — Windows soak (canonical)](#evolve--windows-soak-canonical) below for the full
detached-process incantation, but the minimal foreground call is:

```powershell
PS> uv run python scripts/evolve.py --generations 0 --hours 8
PS> uv run python scripts/evolve.py --concurrency 4 --generations 0 --hours 4   # parallel
PS> uv run --extra viewer python scripts/evolve.py --generations 0 --hours 8 --viewer
```

> `--generations 0` disables the per-run generation cap (default `1` is for
> single-generation test runs). Pair it with `--hours N` for soaks.
> `--viewer` (default OFF) renders the run's SC2 games inside the themed
> container — see [§Evolve with the themed viewer](#evolve-with-the-themed-viewer)
> for the platform gate and, importantly, the stop gestures.

**Path B2 — one-click launcher (evolve + dashboard):**

```powershell
PS> .\scripts\launch-evolve.ps1 -Hours 8
```

Starts `scripts/evolve.py --viewer` in its own console window, then brings up
the dashboard on the Evolution tab. This is the command behind the
dev-observatory **run-evolution** button. See
[§Evolve with the themed viewer](#evolve-with-the-themed-viewer) and
[§One-click launchers](#one-click-launchers).

**Path C — direct script, WSL/Linux (Phase 8):**

Open `wsl -d Ubuntu-22.04` interactively first (one-shot `wsl bash -lc 'nohup ... &'`
silently fails — memory `feedback_wsl_bash_lc_background_fails.md`):

```powershell
PS> wsl -d Ubuntu-22.04
```
```bash
$ cd /mnt/c/Users/x/dev/Alpha4Gate
$ EVO_AUTO=1 nohup uv run python scripts/evolve.py \
      --concurrency 4 --hours 4 --pool-size 12 \
      > logs/evolve-parallel-$(date +%Y%m%d-%H%M).log 2>&1 &
$ echo "PID: $!"; exit
```

(`SC2_WSL_DETECT=0`, `SC2PATH`, `UV_PROJECT_ENVIRONMENT` should already be in
your `~/.profile`. See [§Evolve — Linux soak](#evolve--linux-soak-phase-8-step-11) and
[§WSL specifics](#wsl-specifics).)

**Path D — debug a single imp (bypass fitness):**

```powershell
PS> uv run python scripts/evolve_inject_one.py --title "DEFEND/FORTIFY"
```

Drives one named favorite straight through stack-apply + regression. ~10 min
cycle, real `[evo-auto]` commits. Use when stack-apply repeatedly fails and
you want to isolate from fitness noise.

**Watch it:** open the dashboard at <http://localhost:3000/?tab=evolution>
(start the backend + frontend first per
[§Running the bot](#running-the-bot-windows), or just run
`.\scripts\launch-a4g.ps1 -Tab evolution`), or tail the log per
[§Evolve log tail](#evolve-log-tail-windows-runs).
**Stop it:** see [§Killing a running task](#killing-a-running-task) — never
kill `SC2_x64.exe` directly, and never Ctrl+C a `--viewer` run.

### 2. Improve-bot-advised — Claude advisor + linear improvement loop

Runs games, has Claude review the replays against Protoss guiding principles,
picks one high-impact improvement, applies it, validates win-rate, commits.
Linear (one imp at a time, no pool, no stacking). Lands `[advised-auto]`
commits scoped to `bots/current/**`.

**This skill is Claude-Code-only — there is no equivalent shell script.**
The advisor reasoning happens inside the model, so you must invoke from Claude
Code:

```
/improve-bot-advised                                       # 4h, training-only, replay-mode
/improve-bot-advised --self-improve-code                   # allow source-code edits (auto --mode both)
/improve-bot-advised --hours 8 --self-improve-code         # longer code-changing soak
/improve-bot-advised --backlog                             # pick from "Tactical refinements backlog"
/improve-bot-advised --observe live --games 5              # live mode (slower, richer signal)
```

Mode summary:
- `--mode training` (default, safe): only edits reward-rules JSON / hyperparams.
- `--mode dev` / `--mode both`: edits source. **Requires `--self-improve-code`** —
  without that flag the skill auto-downgrades to training-only.
- `--observe replay` (default): max-speed games, post-hoc Claude review. ~10 min/iter.
- `--observe live`: realtime games with advisor firing every 30 game-seconds. ~3-8 min/game.

Skill spec: [.claude/skills/improve-bot-advised/SKILL.md](../../.claude/skills/improve-bot-advised/SKILL.md).
**Mutually exclusive with `/improve-bot-evolve`** — pre-flight refuses to
start if the other is running.

### 3. Demo viewer — watch a self-play game in the themed pygame container

Two-pane themed window with stats overlay; reparents the SC2 client windows
into the panes. **Windows-only** (the viewer no-ops on Linux per
`scripts/selfplay.py:_viewer_enabled`).

**Pre-req: the optional `viewer` extra on the main `.venv`.** Since `844ae57`
the extra pulls `pygame-ce` (drop-in `import pygame`) instead of mainline
`pygame`; pygame-ce ships cp314 wheels, so it installs straight onto the main
Py3.14 `.venv` — no side venv, no `UV_PROJECT_ENVIRONMENT` juggling:

```powershell
PS> uv sync --extra viewer
```

Then prefix each viewer command with `--extra viewer` (`uv run --extra viewer
python ...`) — the same form `scripts/launch-evolve.ps1` uses. The old
Py3.12 side-venv recipe (`.venv-py312`) is superseded; delete it if you built
one, or it will drift from what the launchers actually run.

**Path A — empty container only (no SC2, fastest smoke test):**

```powershell
PS> uv run --extra viewer python -m selfplay_viewer.demo
PS> uv run --extra viewer python -m selfplay_viewer.demo --background brazil --bar side --size small
PS> uv run --extra viewer python -m selfplay_viewer.demo --attach-pids 1234,5678   # reparent two real Win32 windows
```

Two grey placeholder rectangles + the themed background. Useful for tweaking
layout / backgrounds without burning a 3-min SC2 game.

**Path B — real self-play with the viewer attached (the actual demo):**

```powershell
PS> uv run --extra viewer python scripts/selfplay.py --p1 v0 --p2 v0 --games 2 --map Simple64
PS> uv run --extra viewer python scripts/selfplay.py --p1 v0 --p2 v7 --games 5 --layout vertical --background random
PS> uv run --extra viewer python scripts/selfplay.py --sample pfsp --pool v0,v3,v4,v7 --games 10
```

The viewer is on by default; pass `--no-viewer` for batch/CI runs that don't
need the window. Layout options: `--layout horizontal` (default) or `vertical`,
`--bar top`/`side`, `--size large`/`small`, `--background <key>` (run
`python -m selfplay_viewer.demo --help` to see your installed background keys).

**Path C — current pointer plays itself for a quick sanity demo:**

```powershell
PS> uv run --extra viewer python scripts/selfplay.py --p1 (Get-Content bots\current\current.txt) `
       --p2 (Get-Content bots\current\current.txt) --games 1 --map Simple64
```

Useful when you just want to see what `bots/current` looks like in motion.

**Path D — an entire evolve run in the same container (Phase EV):** the same
themed viewer now attaches to `scripts/evolve.py` via `--viewer`. Its stop
gestures differ from the demo's — see
[§Evolve with the themed viewer](#evolve-with-the-themed-viewer).

---

## Running the bot (Windows)

### One-click launchers

Two PowerShell launchers replace the manual multi-terminal bring-up below.
Both reuse anything already listening instead of double-starting it, and both
leave the servers running in their own windows when the launcher exits.

```powershell
PS> .\scripts\launch-a4g.ps1                      # backend :8765 + frontend :3000, opens the dashboard
PS> .\scripts\launch-a4g.ps1 -Tab evolution       # ...and deep-links to a tab via /?tab=<name>
PS> .\scripts\launch-evolve.ps1 -Hours 8          # evolve run (--viewer) + dashboard on the Evolution tab
```

- **`scripts/launch-a4g.ps1`** — starts the backend (`bots.current.runner
  --serve` on :8765) and the frontend (Vite on :3000) in separate persistent
  windows, waits for :3000 to answer, then opens the dashboard. `-Tab <name>`
  appends the `/?tab=<name>` deep link; valid names are
  `advisor` | `evolution` | `models` | `observable` | `processes` | `help`.
  It does **not** start a game. (Distinct from `scripts/start-dev.sh`, which
  is the `build-step --ui` capture harness and kills the backend when its
  foreground exits.)
- **`scripts/launch-evolve.ps1`** — the command behind the dev-observatory
  **run-evolution** button. Starts `uv run --extra viewer python
  scripts/evolve.py --hours <N> --viewer` in its own console window, then
  delegates to `launch-a4g.ps1 -Tab evolution`. If a `python …evolve.py`
  process is already running it skips the spawn and just opens the dashboard,
  so two runs never race on `data/evolve_*.json`. `-Hours` defaults to 4.
  **Read [§Evolve with the themed viewer](#evolve-with-the-themed-viewer)
  before using it** — it auto-commits `[evo-auto]` promotions to master, and
  its stop gesture is not the one you'd guess.

### Solo game vs SC2 built-in AI

```powershell
PS> uv run python -m bots.v0 --role solo --map Simple64 --difficulty 1 --decision-mode rules
```

`--decision-mode` is `rules` | `hybrid` | `neural`. `--difficulty` is 1-7.

### Backend API + WebSockets (for the dashboard)

```powershell
PS> uv run python -m bots.current.runner --serve    # backend only on :8765 (auto-tracks current.txt)
PS> bash scripts/start-dev.sh                       # backend + frontend together (used by build-step --ui)
```

### Frontend dev server (in another terminal)

```powershell
PS> cd frontend
PS> npm run dev                                     # :3000 -> proxies to :8765
```

Stop the dev server: close the PowerShell window or Ctrl+C. Verify port 8765
is free if backend won't start: `Get-NetTCPConnection -LocalPort 8765`.

### Headless (no SC2 client) — Phase 8 Docker worker

See [cloud-deployment.md](cloud-deployment.md). One-liner:

```powershell
PS> docker run --rm alpha4gate-worker     # default: solo vs VeryEasy on Simple64
```

---

## Self-play & evolve

### Self-play — short head-to-head (Windows)

```powershell
PS> uv run python scripts/selfplay.py --p1 v0 --p2 v0 --games 2 --map Simple64
```

### Evolve — Windows soak (canonical)

```powershell
PS> Set-Location $env:USERPROFILE\dev\Alpha4Gate
PS> $ts = Get-Date -Format 'yyyyMMdd-HHmm'
PS> $logfile = "logs\evolve-$ts.log"
PS> $proc = Start-Process -FilePath "$env:USERPROFILE\.local\bin\uv.exe" `
       -ArgumentList "run","python","scripts/evolve.py","--generations","0","--hours","8" `
       -WorkingDirectory "$env:USERPROFILE\dev\Alpha4Gate" `
       -RedirectStandardOutput $logfile `
       -RedirectStandardError "$logfile.err" `
       -PassThru -WindowStyle Hidden
PS> "PID: $($proc.Id)  log: $logfile"
```

Detached; survives the launching window closing. Tail with
`Get-Content $logfile -Wait -Tail 30`.

### Evolve with the themed viewer

`scripts/evolve.py --viewer` (Phase EV) renders the run's SC2 games inside the
themed self-play container instead of raw, unmanaged SC2 windows. Same
container as [§Demo viewer](#3-demo-viewer--watch-a-self-play-game-in-the-themed-pygame-container),
now driven by the evolve loop.

```powershell
PS> uv run --extra viewer python scripts/evolve.py --generations 0 --hours 8 --viewer
PS> .\scripts\launch-evolve.ps1 -Hours 8          # same thing + the dashboard
```

- **Default OFF.** Without `--viewer` the run is headless and byte-identical
  to a pre-EV run.
- **Windows + the `[viewer]` extra only.** Invoke through
  `uv run --extra viewer` (see the pre-req in
  [§Demo viewer](#3-demo-viewer--watch-a-self-play-game-in-the-themed-pygame-container)).
  Off Windows, or with pygame not importable, the flag **degrades to a WARNING
  and the run continues headless** — it never crashes the soak.
- **Requires `--concurrency 1`.** `--viewer` with `--concurrency > 1` is
  rejected at argparse time: parallel fitness games run in
  `scripts/evolve_worker.py` subprocesses this process's viewer callbacks can
  never render.

#### Stop gestures — the two are NOT the same

| Gesture | Effect |
|---|---|
| Close the **viewer container** | **DETACHES the display only.** The run keeps going headless to its `--hours` budget. Deliberate — dismissing the window must not cost hours of evolution. |
| Close the **evolve console window** | **STOPS the run.** This is the only stop gesture. |
| Dashboard **Stop** button | **Not wired to this runner.** It writes `data/evolve_run_control.json`, which nothing in `scripts/evolve.py` or `src/orchestrator/` reads, so the run ignores it. |

**Never press Ctrl+C on a `--viewer` run.** Under `--viewer` the evolution loop
runs off the main thread, so burnysc2's SIGINT kill-switch is never armed and
Ctrl+C can leave **orphaned SC2 processes** behind — which
`.claude/rules/bot-runtime.md` forbids cleaning up by killing `SC2_x64.exe` by
hand. Close the console window instead. `scripts/evolve.py` prints this same
warning to stderr at run start.

The Evolution dashboard tab only *observes* the run (via the
`data/evolve_*.json` state files the runner writes) — closing the dashboard
does not stop anything either.

### Evolve — Linux soak (Phase 8 Step 11)

**Two-step launch:** open interactive WSL shell, run nohup inside it.
Don't use a `wsl bash -lc 'nohup ... &'` one-liner — it silently fails
to background (memory `feedback_wsl_bash_lc_background_fails.md`).

```powershell
PS> wsl -d Ubuntu-22.04                         # drops you into bash
```

```bash
$ cd /mnt/c/Users/x/dev/Alpha4Gate
$ SC2PATH=$HOME/StarCraftII \
  SC2_WSL_DETECT=0 \
  UV_PROJECT_ENVIRONMENT=$HOME/venv-alpha4gate-linux \
  EVO_AUTO=1 \
  nohup uv run python scripts/evolve.py \
      --hours 8 --games-per-eval 9 --pool-size 4 \
      > logs/evolve-linux-8h-$(date +%Y%m%d-%H%M).log 2>&1 &
$ echo "PID: $!"                                # save this number
$ exit                                          # nohup keeps the soak alive
```

Verify alive (anytime):

```powershell
PS> wsl -d Ubuntu-22.04 bash -lc "ps -ef | grep evolve.py | grep -v grep"
```

Should show TWO lines: parent `uv run python scripts/evolve.py` and child
`python3 scripts/evolve.py`. The python child has high CPU% (the actual loop).

### Evolve — parallel concurrency (`--concurrency N`)

`scripts/evolve.py` accepts `--concurrency N` (default `1`). At `N=1` the
behaviour is byte-identical to the historical serial path (Decision D-1 in
`documentation/archived/evolve-parallelization-plan.md`). At `N>1` the parent
spawns N worker subprocesses that each run a fitness eval against the
shared parent in parallel. Stack-apply + regression remain serial in the
parent process; only the fitness fan-out parallelises.

**Smoke-gate invocation** (60-second cycle, used by Step 8 of the
parallelization plan to verify a parallel run completes a generation):

```powershell
PS> uv run python scripts/evolve.py --concurrency 2 --pool-size 2
```

The default `--generations 1` makes this exit after a single generation —
ideal for CI / smoke checks. For longer runs add `--generations 0 --hours N`.

**Parallel-run idempotence (Decision D-6).** Each worker writes
`data/evolve_round_<wid>.json` for its own per-game progress; the parent
writes `data/evolve_round.json` (the singular file the dashboard reads).
On every parent startup, the parent sweeps stale `evolve_round_*.json`
files left behind by a crashed prior run before launching new workers —
no manual cleanup required between runs.

**Failure-mode buckets (Decision D-7).** When a worker fails to deliver
a fitness verdict, the parent classifies it into one of four buckets.
`evolve_results.jsonl` carries the bucket label so the dashboard and the
morning report can distinguish them. All four share the same on-disk
accounting path (`_record_parallel_failure`):

| Bucket | Meaning | Worker outcome |
|---|---|---|
| `dispatch-fail` | Worker subprocess never started (fork/exec error, missing arg, etc.) | imp evicted; `retry_count++` (subject to retry-cap) |
| `crash` | Worker started, ran a game, then crashed (Python traceback in `evolve_crashes.jsonl`) | imp evicted; `retry_count++` (subject to retry-cap) |
| `malformed` | Worker exited 0 but the verdict JSON is missing/unparseable | imp evicted; `retry_count++` (subject to retry-cap) |
| `hang` | Worker exceeded `--hard-timeout`; parent SIGKILLs it | imp evicted; `retry_count++` (subject to retry-cap) |

The bucket label is preserved in `evolve_results.jsonl` for diagnostics,
but the policy is uniform: every parallel failure increments the imp's
retry counter, and the imp is dropped permanently once `retry_count`
reaches `_RETRY_CAP` (default 3) — the same retry-cap path the serial
crash branch uses.

**Parallel-run launch (Linux, 4-way).**

```powershell
PS> wsl -d Ubuntu-22.04                         # drops you into bash
```

```bash
$ cd /mnt/c/Users/x/dev/Alpha4Gate
$ SC2PATH=$HOME/StarCraftII \
  SC2_WSL_DETECT=0 \
  UV_PROJECT_ENVIRONMENT=$HOME/venv-alpha4gate-linux \
  EVO_AUTO=1 \
  nohup uv run python scripts/evolve.py \
      --concurrency 4 --hours 4 --pool-size 12 \
      > logs/evolve-parallel-$(date +%Y%m%d-%H%M).log 2>&1 &
$ echo "PID: $!"
$ exit
```

Open `http://localhost:3000/?tab=evolution` to watch the 4 cards populate
(one card per worker; parent process owns the run-state header).

### Operator quickstart — first 4-way parallel run

The minimal recipe a fresh-context operator runs to launch their first
parallel evolve run end-to-end. Copy-paste each block in order.

```
# 1. Backend already running on port 8765? If not, start in a separate Windows shell:
uv run python -m bots.current.runner --serve

# 2. Frontend already running on port 3000? If not, start in another Windows shell:
cd frontend && npm run dev

# 3. Launch parallel evolve from inside Ubuntu-22.04 WSL (interactive, NOT one-shot):
wsl -d Ubuntu-22.04
cd /mnt/c/Users/x/dev/Alpha4Gate
SC2_WSL_DETECT=0 nohup uv run --project . python scripts/evolve.py \
  --concurrency 4 --hours 4 --no-commit \
  > logs/evolve-parallel-$(date +%Y%m%d-%H%M).log 2>&1 &
exit  # detach the WSL shell; the nohup'd job survives
```

Then open `http://localhost:3000/?tab=evolution` to watch the 4 cards populate.
(There is no router — the dashboard is a single page and `?tab=<name>` is the
deep-link contract; an unknown or absent value falls back to the Advisor tab.)

`--no-commit` is for the first shakeout — drop it once you're confident
the pipeline is healthy and you want EVO_AUTO commits to land for real.

### Evolve inject-one — debug stack-apply by injecting a known-good imp

Bypasses fitness, drives one named favorite straight through stack-apply +
regression. ~10 min cycle, real `[evo-auto]` commits. Use when stack-apply
fails repeatedly to isolate from fitness noise.

```powershell
PS> uv run python scripts/evolve_inject_one.py --title "DEFEND/FORTIFY"
PS> uv run python scripts/evolve_inject_one.py --title "Observer escort" --no-commit
PS> uv run python scripts/evolve_inject_one.py --title "Gas-dump" --skip-regression
```

Title match is case-insensitive substring against `data/evolve_favorites.json`.

### Curate the favorites file (after a soak)

```powershell
PS> uv run python scripts/curate_evolve_favorites.py
```

Mines `data/evolve_results.jsonl` for imps with ≥1 fitness-pass and writes
`data/evolve_favorites.json`. Idempotent. Re-run after each soak.

### Ladder

```powershell
PS> uv run python scripts/ladder.py --list                # current rankings
PS> uv run python scripts/ladder.py --eval-only           # cross-version games without promotion
```

---

## Watching a running task

### Evolve run state (from anywhere)

```powershell
PS> Get-Content $env:USERPROFILE\dev\Alpha4Gate\data\evolve_run_state.json
```

Fields to read:
- `status`: "running" | "completed"
- `parent_current` / `parent_start`: bot version (v0, v1, ...)
- `generation_index`: current generation
- `generations_promoted`: count of successful promotions (the headline metric)
- `pool_remaining_count`: imps still to evaluate
- `last_result.outcome`: `fitness-pass` / `fitness-fail` / `stack-apply-pass` / `regression-pass` / `regression-rollback` / `crash`

### Evolve log tail (Windows runs)

```powershell
PS> Get-Content (Get-ChildItem logs\evolve-*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName -Tail 50 -Wait
```

### Evolve log tail (Linux runs)

```powershell
PS> wsl -d Ubuntu-22.04 bash -lc "tail -f \$(ls -t /mnt/c/Users/x/dev/Alpha4Gate/logs/evolve-linux-*.log | head -1)"
```

### Find new evolve commits

```powershell
PS> git log --oneline --since="2 hours ago" | Select-String "evo-auto"
```

### Process check (Windows)

```powershell
PS> Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
       Where-Object { $_.CommandLine -like '*evolve*' } |
       Select-Object ProcessId, ParentProcessId, CreationDate
```

### Process check (Linux/WSL)

```powershell
PS> wsl -d Ubuntu-22.04 bash -lc "ps -ef | grep evolve.py | grep -v grep"
```

---

## Killing a running task

### Stop a `--viewer` evolve run

Close the **evolve console window**. That is the only stop gesture — closing
the themed viewer container merely detaches the display and the run continues
headless. **Do not press Ctrl+C**: it can orphan SC2 processes. Full detail in
[§Evolve with the themed viewer](#evolve-with-the-themed-viewer).

### Stop an evolve daemon (Windows)

```powershell
PS> taskkill /PID <pid> /T /F                   # also kills SC2 children
```

If you don't have the PID:

```powershell
PS> Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'uv.exe'" |
       Where-Object { $_.CommandLine -like '*evolve.py*' } |
       Select-Object ProcessId, CommandLine | Format-List
```

### Stop a Linux evolve

```powershell
PS> wsl -d Ubuntu-22.04 bash -lc "pkill -f scripts/evolve.py"
PS> wsl -d Ubuntu-22.04 bash -lc "pgrep -af SC2_x64"        # wait for empty
```

### Don't kill SC2_x64.exe directly

Per memory `feedback_sc2_process_management.md`: kill the orchestrator
process tree (`taskkill /T`); SC2 children clean up themselves. Killing
SC2 alone leaves the daemon hanging.

---

## Build, test, lint

```powershell
PS> uv sync                                              # install/refresh deps
PS> uv run pytest -q                                     # 1397+ unit tests, ~70s
PS> uv run pytest -m sc2                                 # SC2 integration tests (needs SC2 running)
PS> uv run pytest tests/test_evolve.py -q                # one file
PS> uv run pytest tests/test_evolve.py::TestX -q         # one class
PS> uv run ruff check .
PS> uv run mypy src bots --strict                        # 292 source files
PS> cd frontend; npm run test                            # 234 vitest
PS> cd frontend; npm run lint
```

### Pre-commit hook test (without committing)

```powershell
PS> uv run python scripts/check_sandbox.py               # default mode (no env)
PS> $env:EVO_AUTO=1; uv run python scripts/check_sandbox.py; Remove-Item Env:EVO_AUTO
PS> $env:ADVISED_AUTO=1; uv run python scripts/check_sandbox.py; Remove-Item Env:ADVISED_AUTO
```

---

## Docker (Phase 8 Step 9)

See [cloud-deployment.md](cloud-deployment.md) for the full runbook.

```powershell
PS> docker build -t alpha4gate-worker .                  # ~13 min first time
PS> docker run --rm alpha4gate-worker                    # smoke gate (default CMD)
PS> docker run --rm alpha4gate-worker scripts/selfplay.py --p1 v0 --p2 v0 --games 2 --map Simple64
PS> docker run --rm alpha4gate-worker -m bots.current --difficulty 3 --role solo
PS> docker run --rm -it --entrypoint /bin/bash alpha4gate-worker    # shell in image
PS> docker images alpha4gate-worker                      # size + last build
```

License: image is **not redistributable**. Build locally on each host.
Don't push to public registries.

---

## WSL specifics

### Always use `-d Ubuntu-22.04`

The default `Ubuntu` distro is empty (no uv, no SC2, no venv). All Phase 8
setup lives in `Ubuntu-22.04`. Forgetting `-d` is the #1 silent failure mode
(memory `feedback_wsl_distro_ubuntu_22_04_specific.md`).

```powershell
PS> wsl --list --verbose                                 # see your distros
PS> wsl -d Ubuntu-22.04                                  # interactive shell
PS> wsl -d Ubuntu-22.04 bash -lc "<one-shot command>"    # one-shot synchronous
PS> wsl --terminate Ubuntu-22.04                         # shut down the distro
PS> wsl --shutdown                                       # shut down ALL distros (rare)
```

### One-shot WSL pattern that works

```powershell
PS> wsl -d Ubuntu-22.04 bash -lc "command -v uv && uv --version"
PS> wsl -d Ubuntu-22.04 bash -lc "pgrep -af SC2_x64 || echo 'no SC2'"
```

### Pattern that does NOT work for backgrounded tasks

```powershell
# DON'T — silently fails to actually background, $! prints empty
PS> wsl -d Ubuntu-22.04 bash -lc 'nohup cmd &'
```

For backgrounded launches: open `wsl -d Ubuntu-22.04` interactively first.

### Useful Linux env vars (set in WSL `~/.profile`, not `~/.bashrc`)

`~/.bashrc` short-circuits for non-interactive shells; export-vars there
won't propagate to `wsl bash -lc` calls (memory
`feedback_wsl_bashrc_interactive_guard.md`). Put exports in `~/.profile`:

```bash
# In ~/.profile inside Ubuntu-22.04:
export SC2PATH=$HOME/StarCraftII
export SC2_WSL_DETECT=0
export UV_PROJECT_ENVIRONMENT=$HOME/venv-alpha4gate-linux
```

`SC2_WSL_DETECT=0` forces burnysc2 into pure-Linux mode (else it
auto-detects WSL2 and tries to launch the Windows SC2 binary).

---

## Memory / git / sandbox

### See current memory entries

```
[just ask Claude to read $env:USERPROFILE/.claude/projects/c--Users-x-dev-Alpha4Gate/memory/MEMORY.md]
```

The MEMORY.md index lives at:
`$env:USERPROFILE\.claude\projects\c--Users-x-dev-Alpha4Gate\memory\MEMORY.md`

### Pre-stage hygiene before EVO_AUTO commits

EVO_AUTO commits sweep all staged content (memory
`feedback_evo_auto_commits_sweep_staged.md`). Before launching evolve:

```powershell
PS> git status --short                                   # any " M" / "A " / " D" rows?
PS> git diff --staged --stat                             # what would land in the next commit
```

If anything's staged that isn't `bots/<vN>/*` or `bots/current/current.txt`,
unstage with `git reset HEAD <path>` before launching.

### Branch sanity

```powershell
PS> git branch --show-current                            # should be `master` for normal work
```

If you started a Plan Mode session, the IDE may have flipped you to a
feature branch — verify before committing (memory
`feedback_git_branch_drift_alpha4gate.md`).

---

## Skills (slash commands)

The `/` slash-skills installed in this project (run inside Claude Code):

```
/improve-bot                 # autonomous bot-improvement run (long; PPO-driven)
/improve-bot-advised         # advised loop (Claude advisor + reward-rules edits)
/improve-bot-evolve          # Phase 9 evolve runner (sibling tournament)
/improve-bot-triage          # triage findings from prior /improve-bot run
/a4g-dashboard-check         # dashboard health check
/a4g-ui-test                 # UI Playwright tests with 3 reviewers
/build-step                  # single build step (problem statement → diff)
/build-phase                 # multi-step build phase (full-feature land)
/plan-feature                # plan a new feature/phase
/plan-review                 # audit a plan for gaps
/repo-update                 # post-phase docs+git wrap-up
/repo-sync                   # sync GitHub issues to current plan structure
/session-wrap                # prepare context handoff to next session
/review-pr <num>             # review a PR (multi-pass gauntlet)
/review-prompt               # improve a rough prompt
```

### Built-in Claude Code commands

Shipped with the Claude Code CLI itself (not installed as skills):

```
/help                        # list available commands + usage
/clear                       # clear the current conversation context
/config                      # adjust simple settings (theme, model, etc.)
/fast                        # toggle Fast Mode (Opus 4.6 only)
/ultrareview                 # multi-agent cloud review of current branch
                             #   /ultrareview <PR#> reviews a GitHub PR instead.
                             #   User-triggered + billed; Claude can't launch it.
```

Run `/help` inside Claude Code to see the canonical list.

---

## Where to find things

- **Active plans:** `documentation/plans/` (`documentation/master_plan.md` is the spine)
- **Build docs:** the per-phase `*-build-plan.md` files in
  `documentation/plans/` (completed ones move to `documentation/archived/`)
- **Soak records:** `documentation/soak-test-runs/`
- **Wiki:** `documentation/wiki/index.md` — system diagram + deep-dive pages
- **Investigations:** `documentation/investigations/` — pre-plan analysis
- **Per-version state:** `bots/v<N>/data/` (training.db, checkpoints, reward_rules.json)
- **Cross-version state:** `data/` (evolve state, snapshots, ladder)
- **Logs:** `logs/` (gitignored)
- **Memory:** `$env:USERPROFILE\.claude\projects\c--Users-x-dev-Alpha4Gate\memory\`

---

## When something goes wrong

| Symptom | First check |
|---|---|
| Backend won't start (port 8765 in use) | `Get-NetTCPConnection -LocalPort 8765` then taskkill the PID |
| WSL evolve launch silently failed | Open `wsl -d Ubuntu-22.04` interactively, run nohup there (don't use bash -lc) |
| Evolve says "phantom-promote state detected" | `git checkout bots/current/current.txt` to accept HEAD |
| `uv sync` says "Operation not permitted" in WSL | venv must be on ext4 (`~/venv-alpha4gate-linux`), not `/mnt/c/...` |
| EVO_AUTO commit included unrelated files | `git diff --staged` audit before next launch; consider `git reset` |
| Sub-agent edits files outside candidate dir | The path-sanitize fix is in `e7fb758`; verify it's still in evolve_dev_apply.py |
| Docker build fails on `uv sync --frozen` | `uv lock` on host first, commit, rebuild |
| Container can't read SC2 maps (PermissionError) | Dockerfile chmod step is in sc2-base; rebuild from scratch if missing |
| Two evolve runs racing on git tree | Kill the duplicate via `taskkill /PID <id> /T /F` |
