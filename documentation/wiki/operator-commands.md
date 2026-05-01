# Operator commands — Alpha4Gate cheat sheet

The commands you'll actually need, organized by what you're trying to do.
Copy-paste targets, not exhaustive reference. For full details, follow the
links into build docs, plan docs, and per-skill SKILL.md files.

**Conventions in this doc:**
- Lines starting `PS>` are PowerShell on Windows.
- Lines starting `$` are bash inside WSL or a Linux container.
- All paths assume the repo is at `C:\Users\abero\dev\Alpha4Gate` (Windows
  view) / `/mnt/c/Users/abero/dev/Alpha4Gate` (WSL view).

---

## Quick orientation

```powershell
PS> Set-Location C:\Users\abero\dev\Alpha4Gate
PS> git status                                 # tree clean?
PS> git log --oneline -5                       # recent activity
PS> Get-Content data\evolve_run_state.json     # is an evolve run going?
PS> cat bots\current\current.txt               # which bot version is current?
```

---

## Running the bot (Windows)

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
PS> Set-Location C:\Users\abero\dev\Alpha4Gate
PS> $ts = Get-Date -Format 'yyyyMMdd-HHmm'
PS> $logfile = "logs\evolve-$ts.log"
PS> $proc = Start-Process -FilePath "C:\Users\abero\.local\bin\uv.exe" `
       -ArgumentList "run","python","scripts/evolve.py","--hours","8" `
       -WorkingDirectory "C:\Users\abero\dev\Alpha4Gate" `
       -RedirectStandardOutput $logfile `
       -RedirectStandardError "$logfile.err" `
       -PassThru -WindowStyle Hidden
PS> "PID: $($proc.Id)  log: $logfile"
```

Detached; survives the launching window closing. Tail with
`Get-Content $logfile -Wait -Tail 30`.

### Evolve — Linux soak (Phase 8 Step 11)

**Two-step launch:** open interactive WSL shell, run nohup inside it.
Don't use a `wsl bash -lc 'nohup ... &'` one-liner — it silently fails
to background (memory `feedback_wsl_bash_lc_background_fails.md`).

```powershell
PS> wsl -d Ubuntu-22.04                         # drops you into bash
```

```bash
$ cd /mnt/c/Users/abero/dev/Alpha4Gate
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
`documentation/plans/evolve-parallelization-plan.md`). At `N>1` the parent
spawns N worker subprocesses that each run a fitness eval against the
shared parent in parallel. Stack-apply + regression remain serial in the
parent process; only the fitness fan-out parallelises.

**Smoke-gate invocation** (60-second cycle, used by Step 8 of the
parallelization plan to verify a parallel run completes a generation):

```powershell
PS> uv run python scripts/evolve.py --concurrency 2 --pool-size 2 --hours 0
```

`--hours 0` disables the wall-clock budget so the loop exits as soon as
the pool is exhausted. Useful for CI / smoke checks; never use for
production runs.

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
$ cd /mnt/c/Users/abero/dev/Alpha4Gate
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

Open `http://localhost:3000/evolution` to watch the 4 cards populate
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
cd /mnt/c/Users/abero/dev/Alpha4Gate
SC2_WSL_DETECT=0 nohup uv run --project . python scripts/evolve.py \
  --concurrency 4 --hours 4 --no-commit \
  > logs/evolve-parallel-$(date +%Y%m%d-%H%M).log 2>&1 &
exit  # detach the WSL shell; the nohup'd job survives
```

Then open `http://localhost:3000/evolution` to watch the 4 cards populate.

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
PS> Get-Content C:\Users\abero\dev\Alpha4Gate\data\evolve_run_state.json
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
PS> wsl -d Ubuntu-22.04 bash -lc "tail -f \$(ls -t /mnt/c/Users/abero/dev/Alpha4Gate/logs/evolve-linux-*.log | head -1)"
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
PS> cd frontend; npm run test                            # 143 vitest
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
[just ask Claude to read C:/Users/abero/.claude/projects/c--Users-abero-dev-Alpha4Gate/memory/MEMORY.md]
```

The MEMORY.md index lives at:
`C:\Users\abero\.claude\projects\c--Users-abero-dev-Alpha4Gate\memory\MEMORY.md`

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

- **Active plans:** `documentation/plans/` (alpha4gate-master-plan.md is the spine)
- **Build docs:** `documentation/plans/phase-N-build-plan.md`
- **Soak records:** `documentation/soak-test-runs/`
- **Wiki:** `documentation/wiki/index.md` — system diagram + deep-dive pages
- **Investigations:** `documentation/investigations/` — pre-plan analysis
- **Per-version state:** `bots/v<N>/data/` (training.db, checkpoints, reward_rules.json)
- **Cross-version state:** `data/` (evolve state, snapshots, ladder)
- **Logs:** `logs/` (gitignored)
- **Memory:** `C:\Users\abero\.claude\projects\c--Users-abero-dev-Alpha4Gate\memory\`

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
