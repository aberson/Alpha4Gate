# Soak Test Procedure

## Why this exists

This is the first end-to-end validation of the Alpha4Gate autonomous improvement
loop against a real SC2 client. Phases 2-4 built the daemon, evaluator, promotion
gate, rollback monitor, and dashboard; all of them have unit tests, none have been
observed running unattended for hours against real game data. This procedure is the
deliverable of Phase 4.5 Step 1: a recipe precise enough that someone (or a future
model) with no prior context can execute a soak run, collect evidence, and hand the
findings off to triage. Scope of "no prior context": the fresh-context model is
assumed to have access to the running Alpha4Gate checkout and may read any source
file it needs, but should not need to ask the user any clarifying questions to
execute this procedure end to end. **The pass/fail of the run itself is not the
deliverable — the documented observations are.** Even "the loop died after five minutes for reason
X" is a valuable outcome if the evidence is captured.

---

## 1. Prerequisites

All of these must be true before you touch the pre-soak checklist.

| Requirement | How to verify |
|---|---|
| StarCraft II client installed | `C:\Program Files (x86)\StarCraft II\Versions\` contains a `Base*` folder |
| Maps downloaded from Blizzard CDN | `C:\Program Files (x86)\StarCraft II\Maps\` contains map files **larger than a few KB**. If any map in the project is ~1 KB it is a Git LFS pointer stub and will silently fail to load — redownload from Blizzard. |
| Python environment installed | `uv sync` runs cleanly from the project root (`./`) |
| Frontend dependencies installed | `cd frontend && npm install` has been run at least once after the last `package.json` change |
| Node / npm available | `npm --version` works in a terminal with the project root on `PATH` |
| Dashboard port 3000 free | Nothing else is bound to `localhost:3000` (vite is pinned with `strictPort: true`) |
| Backend port 8765 free | Nothing else is bound to `localhost:8765` (default `web_ui_port`) |
| Disk space | At least ~5 GB free on the drive that holds `data/` (see disk budget below) |

If any row above is unchecked, stop and fix it before continuing. Do not run a soak
test against a half-set-up environment — the findings will be noise.

---

## 2. Pre-soak checklist

### 2.1 Snapshot or reset `data/`

The daemon reads and writes several files inside `data/`. A soak test should start
from a known state so that the postmortem can attribute every change to the run.

Conventions used in this repo:

- `data-pre-soak-<YYYY-MM-DD>/` — a cold snapshot taken before a run. One such
  snapshot already exists at the repo root: `data-pre-soak-2026-04-10/`.
- `data-soak-test-<YYYY-MM-DD>.zip` — the full `data/` directory captured after a
  run, for postmortem (see Section 5).

Choose one of these two starting states:

**Option A — Start from the existing `data-pre-soak-2026-04-10/` snapshot.**
Copy its contents over `data/` (replacing everything) so the run begins from the
same baseline as any other run that used this snapshot.

**Option B — Start from a fresh clean `data/`.**
Move the current `data/` aside to `data-pre-soak-<today>/` first, then let the
daemon recreate what it needs. Use this option if the existing snapshot is stale or
if a config/schema change has invalidated the old data.

"Fresh clean `data/`" means an **empty directory** (`data/`) with nothing inside
it. The daemon and trainer create the following files and subdirectories lazily
on first use — you do not need to pre-create any of them (confirmed by reading
`src/alpha4gate/config.py`, `src/alpha4gate/learning/daemon.py`,
`src/alpha4gate/learning/trainer.py`, and `src/alpha4gate/learning/rewards.py`):

| Path | Created by | When |
|---|---|---|
| `data/` (the top-level dir itself) | `Settings.ensure_dirs()` via `runner.main()` | At server/runner startup if missing |
| `data/training.db` | `TrainingDB` (SQLite) | On first write — i.e. when the first transition is logged during a training or game run |
| `data/checkpoints/` | `TrainingOrchestrator` via `checkpoint_dir.mkdir(parents=True, exist_ok=True)` | At the start of the first training cycle |
| `data/reward_logs/` | `RewardCalculator` via `log_dir.mkdir(parents=True, exist_ok=True)` | At the start of the first training cycle (once per training env) |
| `data/promotion_history.json` | `PromotionManager` / `RollbackMonitor` / daemon curriculum logger | When the first promotion, rollback, or curriculum advancement happens |
| `data/daemon_config.json` | `save_daemon_config()` | When the daemon first persists config (after a training run records `final_difficulty`, after a curriculum advancement, or after `set_curriculum()` is called) — **not created at startup** |

Files the daemon expects to exist but does **not** create:
- `data/reward_rules.json` (optional — if absent, trainer uses built-in defaults)
- `data/hyperparams.json` (optional — if absent, trainer uses built-in defaults)

So for Option B the minimal starting state is literally an empty `data/` dir
(or no dir at all — `Settings.ensure_dirs()` will create it). Optional tuning
files (`reward_rules.json`, `hyperparams.json`) can be copied over from the
previous snapshot if you want the soak to match a prior reward/hyperparam tune;
otherwise the defaults compiled into the code apply.

Never run the soak test directly on a dirty `data/` without snapshotting it first.

### 2.2 Daemon config values

The daemon reads its config from `data/daemon_config.json` if that file exists;
otherwise it uses the `DaemonConfig` dataclass defaults from
`src/alpha4gate/learning/daemon.py`. At the time of writing, **no
`daemon_config.json` file exists in the repo**, so the defaults below are what
will be in effect unless you create one.

| Field | Default | What it controls |
|---|---|---|
| `check_interval_seconds` | 60 | How often the daemon loop wakes up to re-evaluate triggers |
| `min_transitions` | 500 | Transition-count trigger: new transitions since last run before training fires |
| `min_hours_since_last` | 1.0 | Time trigger: hours since last training run before training fires |
| `cycles_per_run` | 5 | PPO cycles per triggered training run |
| `games_per_cycle` | 10 | Self-play games per PPO cycle |
| `current_difficulty` | 1 | Starting built-in AI difficulty (1-10) |
| `max_difficulty` | 10 | Curriculum ceiling |
| `win_rate_threshold` | 0.8 | Eval win rate required to auto-advance difficulty |

Training fires whenever **either** the transition trigger *or* the time trigger is
met, subject to safety gates (no training if `total_transitions == 0` or if a
training run is already active). Before starting the run, note the effective values
in the "Daemon config effective values" section of the run log
(`documentation/soak-test-runs/soak-<YYYY-MM-DD>.md`, created in 2.5) — if you
override them via a `data/daemon_config.json` file or through the
`/api/training/daemon/config` endpoint, record the delta there.

### 2.3 Expected disk budget

The loop writes to several paths under `data/` and `logs/`. Rough per-hour
estimates (revise these after the first run so future soaks have better numbers):

| Path | Growth driver | Rough budget for a 4-hour run |
|---|---|---|
| `data/training.db` | One row per transition + one row per game | 100-500 MB |
| `data/reward_logs/` (JSONL per game) | Per-step reward events | 50-300 MB |
| `data/checkpoints/` | PPO checkpoint files, one per cycle | 200 MB - 1 GB |
| `data/promotion_history.json` | One entry per promotion/rollback | negligible (<1 MB) |
| `logs/` | Per-game JSONL game logs | 50-200 MB |

Plan for **~2-5 GB of growth** on the drive holding the repo for a 4-hour run. If
free space drops below ~2 GB during the run, stop the run — out-of-disk mid-train
is a known failure mode worth documenting but not worth causing on purpose twice.

### 2.4 Duration and stop conditions

Default stop condition: **4 hours of wall-clock unattended runtime**. Also accept
either of the following as a valid early stop:

- **N promotions observed.** E.g. "stop after 3 promotions." This is useful when
  the first goal is to prove the promote/rollback gate works end-to-end, not to
  run the clock out.
- **A hard blocker is clearly reproducible.** If the loop is stuck in the same
  state for 30+ minutes and you have captured the evidence for a finding, stop
  the run and file the finding — don't sit and watch a hung process for four
  hours.

Pick one stop condition before you start and write it in the run log (see 2.5).
Don't silently change it partway through.

### 2.5 Create the run log

The run log is the primary deliverable of the soak. Every observation,
screenshot reference, alert, and finding lands here. It lives in the repo so it
survives the run even if the artifact directory is deleted.

**Path:** `documentation/soak-test-runs/soak-<YYYY-MM-DD>.md`

The `documentation/soak-test-runs/` directory does **not** exist yet in the
repo — create it the first time you run a soak:

```bash
mkdir -p documentation/soak-test-runs
```

Create the run log file with this template before starting the backend:

```markdown
# Soak run — <YYYY-MM-DD>

- Start time (T0): <HH:MM local, <HH:MM> UTC>
- Stop condition chosen: <4h | N promotions | hard blocker>
- Starting `data/` state: <Option A: data-pre-soak-2026-04-10 | Option B: fresh empty>
- Daemon config effective values:
  - check_interval_seconds: <value>
  - min_transitions: <value>
  - min_hours_since_last: <value>
  - cycles_per_run: <value>
  - games_per_cycle: <value>
  - current_difficulty: <value>
  - max_difficulty: <value>
  - win_rate_threshold: <value>
  - (note any delta from dataclass defaults; paste full override JSON if you
    created `data/daemon_config.json` or POSTed to `/api/training/daemon/config`)
- Backend command: <exact command line including tee path>
- Baseline Alerts tab state at T0: <empty | N existing alerts — list them>

## Timeline

One line per observation, newest at the bottom. Format: `HH:MM — <what happened>`.

## Alerts fired

| Timestamp | Severity | Rule | Accurate? (y/n/unknown) | Notes |
|---|---|---|---|---|

## Daemon state transitions

| Timestamp | State | Notes |
|---|---|---|

## Findings

(Per-finding rows use the same schema documented in Section 5.3.)

| Description | Severity | Category | Evidence |
|---|---|---|---|
```

Every reference below to "the run log" means this file. It is the file the
triage step (Phase 4.5 Step 4, issue [#64](https://github.com/aberson/Alpha4Gate/issues/64))
reads.

---

## 3. Startup sequence

Execute these steps in order, in separate terminals. All paths assume the project
root is `./`.

### 3.1 Start the SC2 client (manual)

SC2 cannot be started via a script in a way that the bot can reliably attach to,
so this step is manual.

1. Launch **StarCraft II** from the Battle.net client (or wherever it is pinned).
2. Wait until the client is at the **main menu** (the one with "Play", "Co-op",
   "Versus AI", etc. visible). Do **not** enter a game, tutorial, or custom
   lobby — the daemon assumes it can launch games via the SC2 API, and an
   in-progress game will block that.
3. Confirm no other SC2 processes are running (`Task Manager` -> look for stale
   `SC2_x64.exe` processes from previous runs and kill them).

### 3.2 Start the backend with the daemon enabled

In a terminal at the project root:

```bash
uv run python -m alpha4gate.runner --serve --daemon
```

Verified against `src/alpha4gate/runner.py`: the flag is `--daemon` (not
`--daemon-mode`, not `--with-daemon`). It is a plain `store_true` flag on the
argument parser and is only honored when `--serve` is also set. Running with
`--daemon` alone (without `--serve`) is silently ignored — the bot will run a
single game and exit without ever starting the training daemon. The server
binds to `0.0.0.0:<web_ui_port>` (default `8765`).

Expected log output within the first few seconds:

- `Training daemon started (interval=60s)` — from `TrainingDaemon.start()`
- `Uvicorn running on http://0.0.0.0:8765` — from uvicorn
- No stack traces before the uvicorn banner

If the daemon log line does **not** appear, the daemon did not start — stop, check
that `--daemon` is really on the command line, and check `daemon_config.json` for
a parse error.

### 3.3 Start the frontend dev server

In a second terminal at the project root:

```bash
cd frontend && npm run dev
```

Vite is pinned to port **3000** in `frontend/vite.config.ts` with
`strictPort: true`, and it proxies `/api` and `/ws` to `http://localhost:8765`. If
port 3000 is taken, vite will fail to start rather than picking a random port.

### 3.4 Open the dashboard

Open `http://localhost:3000` in a browser (Chromium-based recommended so the
DevTools console log can be exported at the end). Confirm:

- The header shows **Alpha4Gate Dashboard** and a nav row with **nine** tabs:
  Live, Stats, Build Orders, Replays, Decisions, Training, Loop, Improvements,
  Alerts.
- The **Loop** tab loads without a red error banner and shows daemon status.
- The Alerts tab badge (if any) is recorded in the "Baseline Alerts tab state at
  T0" field of the run log (`documentation/soak-test-runs/soak-<YYYY-MM-DD>.md`) —
  this is your baseline for "what alerts existed before the run started."

Once the dashboard is up and happy, the startup sequence is complete. Log the
wall-clock time as `T0` in the "Start time" field of the run log.

---

## 4. Observation protocol

The operating principle for the whole run is: **do not fix bugs during the run.**
Collect evidence, note findings, keep the loop running. Triage happens in Phase 4.5
Step 4 (issue #64), not mid-soak.

### 4.1 Per-tab watch list

The dashboard has nine tabs (as enumerated in `frontend/src/App.tsx`). Not every
tab needs constant attention — the Loop, Improvements, and Alerts tabs are the
primary ones during a soak.

| Tab | What to watch | Primary or secondary |
|---|---|---|
| Live | Per-game state stream while a game is running; confirm WebSocket isn't stalled | Secondary |
| Stats | Running win/loss counts update as games complete | Secondary |
| Build Orders | Do not edit during a soak | Ignore |
| Replays | New replays should appear as games finish | Secondary |
| Decisions | Decision queue length and throughput while games run | Secondary |
| Training | `TrainingDashboard`, `ModelComparison`, `ImprovementTimeline`, `CheckpointList` — watch for new checkpoints and eval results after each training run | Primary |
| Loop | `LoopStatus` and `TriggerControls` — daemon state (`idle` / `checking` / `training`), `next_check`, `last_run`, `runs_completed`, current trigger state | **Primary** |
| Improvements | `RecentImprovements` (promotions / rollbacks) and `RewardTrends` (reward aggregator output) | Primary |
| Alerts | `AlertsPanel` + `AlertToast` — every alert that fires, with timestamp and severity | **Primary** |

### 4.2 What counts as a successful cycle

A successful cycle is one full pass through the loop:

1. Daemon state transitions `idle -> checking -> training` (visible on Loop tab).
2. `runs_completed` on `GET /api/training/daemon` increments by 1.
3. A new checkpoint file appears under `data/checkpoints/` and shows up in the
   `CheckpointList` on the Training tab.
4. The promotion gate runs on the new checkpoint — the log line
   `Promotion decision: <checkpoint> (promoted=<bool>, reason=<...>)` appears in
   the backend stdout, and the Improvements tab shows a new entry if a
   promotion fired.
5. The rollback monitor runs without raising (the log line
   `Rollback check failed` should **not** appear).
6. Daemon returns to `state == "idle"` and schedules the next `next_check`.

If all six happen end-to-end at least once during the run, the loop has been
observed working. If any one of them never happens, record which one and where it
got stuck in the Timeline section of the run log
(`documentation/soak-test-runs/soak-<YYYY-MM-DD>.md`).

### 4.3 Failure modes to watch for

Treat any of the following as a finding to capture. Do not try to fix them during
the run.

All "run log" references below mean the file you created in 2.5
(`documentation/soak-test-runs/soak-<YYYY-MM-DD>.md`).

| Failure mode | What it looks like | How to capture |
|---|---|---|
| Daemon stuck in a state | Loop tab `state` stays on `checking` or `training` for many times `check_interval_seconds` | Screenshot Loop tab; add a Timeline line in the run log with wall-clock; copy the last ~200 lines of backend stdout into the run log |
| Training run never starts | Triggers never fire despite transitions piling up in `training.db` | Hit `GET /api/training/daemon` and paste the full JSON into the run log Timeline |
| Training run fails | Backend log shows `Daemon: training failed` with a stack trace | Paste the full traceback into the run log Timeline |
| Dashboard goes stale | Counters stop updating even though backend is clearly still alive | Note the tab in the run log Timeline, do a **full browser reload** (F5 or Ctrl+R — not a tab close/reopen, not a React HMR refresh), then record whether the reload fixed it |
| Frontend disconnects | WebSocket errors in the browser DevTools console | Export the console log at end of run (see Section 5) |
| Browser console errors | Red entries in DevTools console | Screenshot the console; add a run log Timeline line naming which tab triggered them |
| Backend log errors | Any `ERROR` or `exception` line in backend stdout | Paste the offending lines into the run log Timeline |
| Disk usage runaway | `data/` or `logs/` growing faster than the budget in Section 2.3 | Record sizes and rate in the run log Timeline; stop the run if free space hits ~2 GB |
| SC2 client crash | SC2 window gone, or stuck at an error dialog | Screenshot; log wall-clock in the run log Timeline; do not relaunch until you've decided whether to continue |
| Bot crash mid-game | Game log shows a Python traceback, or SC2 returns the bot to menu unexpectedly | Save the game log JSONL from `logs/`; add a run log Timeline line pointing at the saved file |

### 4.4 When to intervene vs let it run

**Let it run** if:

- The daemon is in `training` and progressing (log lines still moving).
- Alerts are firing but the loop is still cycling.
- Disk usage is tracking the budget.

**Intervene (stop the run)** if:

- The same state has been stuck for more than 30 minutes with no log activity.
- Disk free space drops below the threshold in Section 2.3.
- SC2 client is unrecoverable (crash dialog, frozen).
- A finding is clearly reproducible and continuing won't add information.

**Do not intervene to**:

- Hand-edit `training.db`.
- Restart only the daemon thread without restarting the backend.
- Push new code.
- Change `daemon_config.json` partway through.

If you have to do any of those, the run is over — stop cleanly (Section 5) and
start a new run with the new config, counted as a separate soak. Record the
reason in the run log Timeline before you stop.

### 4.5 Screenshot checklist

Take screenshots at **three checkpoints** during the run: start (`T0`), midpoint
(`T0 + duration/2`), and end (`T_final`). At each checkpoint, capture the
following tabs:

| Tab | Why |
|---|---|
| Loop | Daemon state + trigger counters + next check time |
| Improvements | Promotions and reward trend at that moment |
| Alerts | Cumulative alert history at that moment |
| Training | Checkpoint list and model comparison state |
| Stats | Win/loss totals |

Also screenshot **any** failure mode from Section 4.3 **as it happens** — a
screenshot taken 10 minutes after the fact is worth much less than one taken at
the moment the state went bad. Save screenshots into the artifact directory
(see Section 5.2) under `screenshots/<tab-name>-<checkpoint>.png`, e.g.
`screenshots/loop-Tmid.png`. Reference the filename from the matching run log
Timeline entry so triage can walk screenshot <-> observation both ways.

---

## 5. Stop and post-soak protocol

### 5.1 Stop the run cleanly

Two options, in order of preference:

**Option A (preferred) — Daemon stop button in the dashboard.**
On the Loop tab, use the `TriggerControls` stop button. This calls the daemon's
`stop()` method, which sets a stop event and joins the thread with a 10-second
timeout. Once it returns, the daemon state should settle to `idle` and
`running: false`.

**Option B — `Ctrl+C` on the backend process.**
If the dashboard is unresponsive, send `Ctrl+C` to the backend terminal. Uvicorn
will shut down; because `TrainingDaemon` is a Python daemon thread, it will be
killed when the process exits — this is less clean than Option A but is the
correct fallback if the UI is gone.

**Option C — Force-kill the backend process tree (last resort).**
If both Option A and Option B fail (backend hung, dashboard unresponsive, terminal
unresponsive), find the backend PID with `netstat -ano | grep ":8765" | grep LISTENING`
and kill the entire process tree. **In Git Bash on Windows, you must wrap `taskkill`
in `cmd.exe //c "..."` because Git Bash's MSYS path translation will mangle `/T`
into `T:/` if you call `taskkill` directly:**

```bash
cmd.exe //c "taskkill /T /F /PID <pid>"
```

The `//c` is the Git Bash escape for `/c` to cmd.exe. The `/T` flag kills the
entire process tree (backend python + any SC2 child processes the evaluator
spawned). The `/F` forces termination. This will also kill any SC2 instances
the daemon's evaluator launched, but **not** an SC2 instance you launched
manually for the soak — those are separate process trees.

After stopping via any option:

- Wait until the backend process has fully exited before touching `data/`.
- Confirm there are no orphan `SC2_x64.exe` processes left behind (Task Manager
  or `tasklist | grep SC2_x64`). Kill any you find using the same `cmd.exe //c`
  wrapper if needed. Your manually-launched SC2 (the one at the main menu) will
  have a distinct PID — don't kill it unless you mean to.
- Close the frontend dev server (`Ctrl+C` in its terminal).

### 5.2 Capture evidence for postmortem

All of these should happen **before** you reset `data/` for the next run.

**Artifact directory convention:** stash everything for one soak under
`~/soak-artifacts/<YYYY-MM-DD>/` (outside the repo so it survives
branch switches and `git clean`). Create it up front:

```bash
mkdir -p ~/soak-artifacts/<YYYY-MM-DD>/screenshots
```

(Use the same `<YYYY-MM-DD>` as the run log filename so the two pair by date.)

The run log file itself stays **inside** the repo at
`documentation/soak-test-runs/soak-<YYYY-MM-DD>.md` — that's the deliverable
the triage step reads. The artifact directory holds the raw evidence the run
log points at.

Target layout for a single run:

```
~/soak-artifacts/<YYYY-MM-DD>/
├── training.db
├── data-snapshot.zip
├── backend.log
├── frontend-console.log
└── screenshots/
    ├── loop-T0.png
    ├── loop-Tmid.png
    ├── loop-Tfinal.png
    └── ... (one per tab per checkpoint)
```

Collect the artifacts in this order:

1. **Copy `training.db`** to `<artifact-dir>/training.db`. This is the single
   most important artifact — it holds every transition, every game record, and
   every model version label from the run.
2. **Snapshot the full `data/` directory** as `<artifact-dir>/data-snapshot.zip`.
   `data/` is gitignored either way, but zipping it into the out-of-repo
   artifact directory means it survives branch switches and `git clean`.
3. **Save the backend log** to `<artifact-dir>/backend.log`. The daemon uses the
   root Python logger configured in `runner.main()` via
   `logging.basicConfig(level=INFO, ...)` — log output goes to **stdout**, not
   a file. The clean way is to tee stdout at startup so capture is automatic;
   this is the recommended backend command form (also cross-referenced from
   section 3.2):

   ```bash
   uv run python -m alpha4gate.runner --serve --daemon 2>&1 | tee logs/soak-<YYYY-MM-DD>-backend.log
   ```

   Then at stop time, copy `logs/soak-<YYYY-MM-DD>-backend.log` to
   `<artifact-dir>/backend.log`. If you forgot to tee, fall back to
   scrollback-copying the backend terminal into `<artifact-dir>/backend.log`
   before closing the terminal.
4. **Save per-game JSONL game logs** from `logs/` — these are written by
   `GameLogger` independently of stdout and contain per-game bot state. Copy
   any relevant game log files into `<artifact-dir>/` (or snapshot the whole
   `logs/` dir alongside `data-snapshot.zip` if the run was short).
5. **Export the browser DevTools console log** to
   `<artifact-dir>/frontend-console.log`. In Chromium: right-click in the
   Console tab, `Save as...`. Do this before reloading the tab.
6. **Final-state screenshots.** Take one screenshot of each of the nine tabs in
   their final state, saved to
   `<artifact-dir>/screenshots/<tab-name>-Tfinal.png`. Yes, even the tabs you
   weren't watching — the Build Orders tab being empty is itself evidence.
   The three-checkpoint screenshots from 4.5 (`-T0`, `-Tmid`, `-Tfinal`) also
   live under the same `screenshots/` subdirectory.

### 5.3 File findings

The output of the soak test is a list of findings that feed Phase 4.5 Step 4
([#64](https://github.com/aberson/Alpha4Gate/issues/64)) for triage. Findings
land in the **Findings** section of the run log
(`documentation/soak-test-runs/soak-<YYYY-MM-DD>.md`, created in 2.5). Each
finding gets one row using this schema (already templated into the run log
scaffold in 2.5):

| Field | Example |
|---|---|
| Description | "Loop tab `last_run` timestamp stopped updating after ~90 min" |
| Severity | `blocker` / `major` / `minor` / `cosmetic` |
| Category | `dashboard` / `daemon` / `training` / `alert tuning` / `docs` / `unknown` |
| Evidence | Path into `~/soak-artifacts/<YYYY-MM-DD>/` (screenshot, log excerpt) or a run-log Timeline timestamp |

Keep the description to one sentence. Detailed analysis belongs in triage, not
here. The point of this step is to make sure nothing observed during the run
gets lost before Step 4 can sort it into the six buckets (blockers, alert tuning,
dashboard polish, daemon tuning, documentation gaps, Phase 5 inputs).

Plan Step 3 (issue #63) originally anticipated a separate
`documentation/soak-test-<YYYY-MM-DD>-results.md` file for findings;
consolidating findings into the run log's Findings section instead keeps one
file per run and avoids two parallel documents that can drift.

---

## Quick reference — one-line summary

> Snapshot `data/`, start SC2 at main menu, run
> `uv run python -m alpha4gate.runner --serve --daemon`, start
> `cd frontend && npm run dev`, open `http://localhost:3000`, watch Loop /
> Improvements / Alerts for 4 hours, collect evidence, stop via dashboard, ZIP
> `data/`, file findings. Do not fix bugs during the run.
