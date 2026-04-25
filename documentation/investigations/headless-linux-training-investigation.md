# Headless Linux SC2 as a training-infrastructure platform — investigation

**Date:** 2026-04-24
**Author:** session continuation after the observer-restriction work
**Predecessor docs:**

- [`headless-sc2-investigation.md`](headless-sc2-investigation.md) (2026-04-10) — concluded that headless mode is *not* reachable from burnysc2 v7.1.3 *on Windows retail SC2*. That conclusion stands.
- [`observer-player-viewer-investigation.md`](observer-player-viewer-investigation.md) (2026-04-24)
- [`observer-restriction-workarounds-investigation.md`](observer-restriction-workarounds-investigation.md) (2026-04-24)

**Trigger:** while exploring fallbacks after both observer-spike paths
failed, we surfaced the fact that Blizzard ships a **separate, headless
Linux SC2 package** for ML/AI research. Headless Linux SC2 does **not**
help the self-play viewer (no window to embed), but the user
immediately recognized it as a potential training-infrastructure unlock:
lower per-instance cost, more parallel games per box, cloud
deployability, AlphaStar-style scaling.

This doc evaluates whether Alpha4Gate should adopt headless Linux SC2
as a training platform — with the same primary-source rigor as the
observer investigations. **It is research, not a plan.**

---

## 1. What this doc is and isn't

**Is:** a feasibility audit of running Alpha4Gate's training pipeline
on Linux against Blizzard's headless `SC2.x86_64` package, including:
- primary-source verification of the package's existence, version, and
  maintenance state;
- an audit of burnysc2 v7.1.3's Linux code paths;
- a comparison with pysc2 (DeepMind's Linux-canonical SC2 wrapper);
- a Windows-assumption audit of Alpha4Gate's training-relevant code;
- a scope estimate and a recommended spike sequence.

**Is not:** a plan, a phase, or a scoped piece of work. No phase
number is reserved. No code is proposed. No `uv sync`, no package
installs, no venv changes. The goal is to give the user enough
verified information to decide whether and when to invest.

---

## 2. Status quo of training on Windows SC2

Today's training stack, audited from primary sources:

### 2.1 Single-instance trainer

`bots/v0/learning/environment.py` is the gymnasium↔burnysc2 bridge.
Each `SC2Env` runs **one** SC2 game in a background thread:

```
[bots/v0/learning/environment.py:702-707]
result = run_game(
    sc2.maps.get(self._map_name),
    [Bot(Race.Protoss, bot), Computer(Race.Random, diff)],
    realtime=self._realtime,
    save_replay_as=_replay_path,
)
```

That's `Bot` vs `Computer` (the in-game built-in AI). **`Computer`
does not need a separate SC2 process** — see
[`.venv/lib/site-packages/sc2/player.py:48-50`](../../.venv/lib/site-packages/sc2/player.py#L48-L50)
(`needs_sc2 = not isinstance(self, Computer)`). So one training game
= one `SC2_x64.exe`. The PPO orchestrator runs cycles sequentially,
one game at a time, no vectorized envs:

> "**Still single-machine, single-GPU, serial cycles.** No vectorized
> envs yet." — [`documentation/wiki/training-pipeline.md:70`](../wiki/training-pipeline.md#L70)

### 2.2 Subprocess self-play (Phase 3+)

`src/orchestrator/selfplay.py` is the cross-version self-play runner.
A 2-bot self-play game spawns **two** SC2 processes via burnysc2's
`BotProcess` + `a_run_multiple_games`:

```
[src/orchestrator/selfplay.py:603-607]
"""Run *games* self-play matches between two bot versions.

Each game spawns two subprocesses (one per bot) and two SC2 instances
via burnysc2's ``BotProcess`` + ``a_run_multiple_games``.  Games run
one-at-a-time within the batch (see design 5.2 in the build plan).
```

So self-play games cost ~2x training games per game in SC2 instances,
and the batch runner is also serial within a batch.

### 2.3 Per-instance footprint — partly inferred

The repo does not contain a measured per-instance RAM/CPU number that I
could quote. The closest data is the Phase 0 spike (cited in
[`documentation/wiki/subprocess-selfplay.md`](../wiki/subprocess-selfplay.md))
which reported "1v1 in 22.4s" but did not log SC2's resident memory.

**Inferred (untested in this investigation):** Windows retail SC2 with
the renderer attached typically lands around 1.5–2.5 GB resident per
process based on community reports and casual operator observation in
the soak docs. With self-play spawning two processes, a single 1v1
self-play game is ~3–5 GB peak. **This is the rough order of
magnitude that drives the parallel-game ceiling discussion below; treat
it as inferred, not measured.**

### 2.4 Parallel-game ceiling on a dev box — inferred

The orchestrator runs games one-at-a-time within a batch by design (see
2.2). The reason cited in the build plan is twofold: (a) avoid SC2
client port-collision races (the very bug the Phase 0 patch in
`selfplay.py:76-119` fixes), and (b) keep a clean RAM budget on a
dev box. A 32 GB dev box could *probably* host 4–6 concurrent 1v1
self-play games (8–12 SC2 instances) before hitting RAM limits, but no
soak doc verifies that and the orchestrator has never been driven to
that level. **Treat the "4–6 concurrent" figure as inferred.**

### 2.5 Why scaling matters — Phase 9 evolve

The master plan's Phase 9 (`improve-bot-evolve`) was redesigned
2026-04-21 to do a sibling-tournament evolutionary loop. The validated
soak from 2026-04-24 (
[`project_evolve_2gate_validated.md`](../../C:/Users/abero/.claude/projects/c--Users-abero-dev-Alpha4Gate/memory/project_evolve_2gate_validated.md)
in user memory) produced 2 net promotions in 7h 15m. The bottleneck
in that loop is exactly the "wall-clock per gating game" — a
fitness/regression eval eats real-time, and the eval is gated by SC2
game throughput. Phase 6 (cross-version self-play as PPO signal) and
Phase 9 both share the same `run_batch` primitive in
`selfplay.py:603`, so any per-game speedup or parallelization
multiplies directly into both.

---

## 3. What headless Linux SC2 actually is

Primary-source verification, not recall.

### 3.1 The repo is alive and current

[`https://github.com/Blizzard/s2client-proto`](https://github.com/Blizzard/s2client-proto)
is **not archived**, MIT-licensed, 3,930 stars, last commit
**2025-10-08** with message *"Updating to version 5.0.15.95299.0"*
(API call `repos/Blizzard/s2client-proto/commits` confirmed). Blizzard
shipped 4 commits in 2024–2025, all version bumps. So:

- **Active state:** maintenance-only, but version-tracked. Blizzard is
  still publishing updated proto definitions, including a Linux
  package, in late 2025.
- **Not abandoned.** No archived banner, no deprecation notice.

### 3.2 The README's Linux package list lags the actual versions

The README's "Linux Packages" section (verified by raw fetch) lists
versions:

> "3.17, 3.16.1, 4.0.2, 4.1.2, 4.6, 4.6.1, 4.6.2, 4.7, 4.7.1, 4.8.2,
> 4.8.3, 4.8.4, 4.8.6, 4.9.0, 4.9.1, 4.9.2, 4.9.3, and 4.10"

Each links to `blzdistsc2-a.akamaihd.net/Linux/SC2.[version].zip`.

**4.10 is the newest publicly-listed Linux package** (per the README).
**5.0.15 is the newest proto version** (per the commits). The proto
moves; the Linux *package* download list does not appear to. This is a
real lag — about 5+ years between when 4.10 shipped (2018) and when
5.0.15 shipped (October 2025). Whether 4.10 plays nicely with our
burnysc2 v7.1.3 (which expects modern SC2) is an open question
addressed in §6.

### 3.3 What "headless" means in this context

The README literally describes the Linux offering as:

> *"Self contained headless linux StarCraft II builds."*

Quoted via WebFetch on the live README. So Blizzard themselves call it
headless. The downloadable zip is gated behind:

> *"agree to the AI and Machine Learning License"* and the password
> `iagreetotheeula`

The package extracts into the standard SC2 directory hierarchy
(`StarCraft II/Battle.net/Maps/Replays/SC2Data/Versions/` per the
README), and the binary is `SC2_x64` (no `.exe`) — confirmed by
burnysc2's path table in §4 below.

The strong implication is that this is a **self-contained build with
no graphical/render layer required** — i.e. it's the variant pysc2 has
used for AlphaStar-class training since 2017. We have NOT verified
that it runs without an X server / EGL on a totally bare Linux box;
that's part of Spike 1 (§11).

### 3.4 Map files

The README says map packs span "Ladder 2017 Season 1" through "Ladder
2019 Season 3," plus a Melee collection, and:

> *"Extract the zip file directly into the 'Maps' folder"*

Our current map (Simple64) comes from the Blizzard CDN per
[`CLAUDE.md:64`](../../CLAUDE.md#L64). It's a Blizzard-distributed map,
and the same `.SC2Map` files work cross-platform — they're game-data
files, not platform binaries. **Inferred** (untested): the Simple64
map we use today should drop into the Linux `Maps/` directory
unchanged.

---

## 4. burnysc2's Linux story

The library has explicit Linux branches throughout its low-level
launch and process-management code. Audit:

### 4.1 Path table

[`.venv/lib/site-packages/sc2/paths.py:14-48`](../../.venv/lib/site-packages/sc2/paths.py#L14-L48)
hardcodes 6 platforms:

```
BASEDIR = {
    "Windows":  "C:/Program Files (x86)/StarCraft II",
    "WSL1":     "/mnt/c/Program Files (x86)/StarCraft II",
    "WSL2":     "/mnt/c/Program Files (x86)/StarCraft II",
    "Darwin":   "/Applications/StarCraft II",
    "Linux":    "~/StarCraftII",
    "WineLinux":"~/.wine/drive_c/Program Files (x86)/StarCraft II",
}

BINPATH = {
    ...
    "Linux":    "SC2_x64",
    "WineLinux":"SC2_x64.exe",
    ...
}

CWD = {
    ...
    "Linux":    None,                # no Support64 dir on Linux
    ...
}
```

Linux is a **first-class** target. The default install path
(`~/StarCraftII`) matches Blizzard's package layout. The executable
name is `SC2_x64` (no .exe). The CWD is `None` because the headless
Linux package does not ship a `Support64/` directory.

### 4.2 Platform detection seam

[`paths.py:51-55`](../../.venv/lib/site-packages/sc2/paths.py#L51-L55):

```python
def platform_detect():
    pf = os.environ.get("SC2PF", platform.system())
    if pf == "Linux":
        return wsl.detect() or pf
    return pf
```

There's an `SC2PF` env-var override. So we can force a specific
platform mode via env var without modifying code. WSL is auto-detected
if `platform.system() == "Linux"` and we're inside WSL.

### 4.3 Process startup serializes on Linux

[`.venv/lib/site-packages/sc2/main.py:694-703`](../../.venv/lib/site-packages/sc2/main.py#L694-L703):

```python
if platform.system() == "Linux":
    # Works on linux: start one client after the other
    new_controllers = [await asyncio.wait_for(sc.__aenter__(), timeout=50) for sc in extra]
else:
    # Doesnt seem to work on linux: starting 2 clients nearly at the same time
    new_controllers = await asyncio.wait_for(
        asyncio.gather(*[sc.__aenter__() for sc in extra], return_exceptions=True),
        timeout=50,
    )
```

burnysc2 explicitly serializes SC2 startup on Linux. Comments imply
the developer knew of races on Linux and worked around them. **For
parallel multi-game training this is a 50s/instance ceiling on
startup**, which puts a soft cap on how aggressively we can spin up
processes.

### 4.4 Bot proxy / process management

[`.venv/lib/site-packages/sc2/proxy.py:177-180`](../../.venv/lib/site-packages/sc2/proxy.py#L177-L180):

```python
if platform.system() == "Linux":
    subproc_args["preexec_fn"] = os.setpgrp
elif platform.system() == "Windows":
    subproc_args["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
```

Linux uses POSIX process groups for child cleanup. This is well-trodden
territory.

### 4.5 SC2 process teardown handles Wine

[`.venv/lib/site-packages/sc2/sc2process.py:268-272`](../../.venv/lib/site-packages/sc2/sc2process.py#L268-L272):

```python
# Try to kill wineserver on linux
if paths.PF in {"Linux", "WineLinux"}:
    with suppress(FileNotFoundError), subprocess.Popen(["wineserver", "-k"]) as p:
        p.wait()
```

The teardown path is Linux-aware. The `FileNotFoundError` suppression
means it gracefully no-ops on a real Linux (no Wine), so this is safe
for headless Linux.

### 4.6 EGL render mode is Linux-only

[`.venv/lib/site-packages/sc2/sc2process.py:195-196`](../../.venv/lib/site-packages/sc2/sc2process.py#L195-L196):

```python
if self._render:
    args.extend(["-eglpath", "libEGL.so"])
```

The `-eglpath libEGL.so` flag is only added when `render=True`. This
is the **headless rendering** mode Blizzard supports on Linux — the
client renders into an EGL context (no X server needed) so the API can
return RGB observation data. We don't need this for our current
training (we use raw observations), but it's available if Phase B/D
ever adds RGB features.

### 4.7 Replay path quirk

[`.venv/lib/site-packages/sc2/controller.py:65-78`](../../.venv/lib/site-packages/sc2/controller.py#L65-L78):
on Linux, `start_replay` insists the replay file live in
`~/Documents/StarCraft II/Replays/` and uses just the filename (not
the full path). On Windows it accepts an absolute path. **This is a
real gotcha for cross-platform replay handling** — we'd need to
normalize paths or hardlink replays into the home folder before
replaying.

### 4.8 Other minor Linux-isms

- [`bot_ai_internal.py:760`](../../.venv/lib/site-packages/sc2/bot_ai_internal.py#L760): TODO note
  about `vespene_contents` only fixed in newer Linux clients ≥4.10.
- [`unit.py:474, 485`](../../.venv/lib/site-packages/sc2/unit.py#L474):
  workarounds tagged "remove if a new linux binary (5.0.4 or newer) is
  released." This implies burnysc2's Linux support targets ≥5.0.4 in
  practice.
- [`generate_ids.py:34-40`](../../.venv/lib/site-packages/sc2/generate_ids.py#L34-L40):
  `stableid.json` lookup uses `~/Documents/StarCraft II/stableid.json`
  on Linux. Not blocking but a cross-platform path divergence.

### 4.9 Bottom line

burnysc2 has a real, deliberate Linux support story, written into the
low-level launch/path/teardown code. This is **not** "Linux is an
afterthought." Notably, the WSL1/WSL2 paths share the Windows install
location (`/mnt/c/Program Files (x86)/...`), so a WSL-hosted Python
process running burnysc2 against the **Windows-installed retail SC2**
is something burnysc2 already handles. That's a separate path from
"headless Linux SC2 in `~/StarCraftII`."

**Two viable Linux runtime modes from burnysc2's perspective:**

1. **Pure Linux:** `SC2PF=Linux`, headless SC2.x86_64 in `~/StarCraftII`,
   `SC2_x64` binary, no Support64 cwd. This is what AlphaStar-style
   work uses.
2. **WSL + Windows retail:** `SC2PF=WSL2`, Python in WSL, SC2 binary
   from the Windows retail install. **This is interesting because it
   needs zero Windows-side changes** but still gives us Python on
   Linux. Caveat: it's still the rendering Windows binary, so the
   per-instance memory cost doesn't drop. It's a cheap dev/CI bridge,
   not a training-scale unlock.

---

## 5. pysc2 alternative

Per the README of [`https://github.com/google-deepmind/pysc2`](https://github.com/google-deepmind/pysc2):
"StarCraft II Learning Environment", Apache 2.0, 8,276 stars, 64 open
issues.

### 5.1 Maintenance status

GitHub API on `repos/google-deepmind/pysc2` (verified via fetch):
- `pushed_at: 2024-07-23` (metadata only — likely a CI bump)
- `archived: false`

But the actual **commit** history tells a different story
(`repos/google-deepmind/pysc2/commits?per_page=5`):

1. 2023-04-19 — *"Pass the launch kwargs to subprocess.Popen..."*
2. 2023-04-19 — *"Bump support up to python 3.11."*
3. 2023-04-19 — *"Simplify a comment."*
4. 2023-04-19 — *"Regenerate distinct_colors array..."*
5. 2023-04-19 — *"Fix exception causes in lib/protocol.py"*

**Five commits all on the same day in April 2023, then nothing.**
This is effectively a maintenance-only repo. It's not abandoned (no
archived flag, the repo still works against current SC2 builds because
DeepMind ships it tied to Blizzard's proto), but DeepMind isn't
actively developing it. Compare to s2client-proto (Blizzard) which had
a commit in **October 2025**.

### 5.2 Architecture difference vs burnysc2

From the pysc2 README:

> *"You can enable or disable RGB or feature layer rendering and their
> resolutions with command-line flags."*

> *"PySC2 should work on MacOS and Windows systems running Python 3.8+,
> but has only been thoroughly tested on Linux."*

pysc2's primary API is the **Feature Layer** mode — pre-extracted
spatial maps (minimap-style) plus a handful of scalar features —
designed for image-conv-style RL agents. burnysc2 uses the **Raw
API** — direct unit lists, positions, and abilities with no spatial
encoding. These are fundamentally different agent architectures.
Alpha4Gate's PPO is built around 17–24 scalar features (
[`bots/v0/learning/features.py`](../../bots/v0/learning/features.py),
referenced in
[`documentation/wiki/training-pipeline.md:169`](../wiki/training-pipeline.md#L169))
plus rule-based macro decisions — **very far from pysc2's Feature
Layer / RGB conventions**.

### 5.3 Could we switch to pysc2?

The cost/benefit:

| | burnysc2 | pysc2 |
|---|---|---|
| Linux-native | Supported, secondary target | Primary target, "thoroughly tested on Linux" |
| Active dev | Active (community) | Maintenance-only since April 2023 |
| API style | Raw API (unit lists) | Feature Layer / RGB |
| Alpha4Gate code coupling | Deep (47 modules in `bots/v0`, 50 test files) | None — would require rewrite |
| AlphaStar pedigree | None | Yes (DeepMind's own stack) |
| burnysc2 ↔ pysc2 bridge feasibility | N/A | **No** — APIs are not interchangeable; agent architecture differs |

**Verdict:** pysc2 is *the* canonical Linux ML library, but switching
would mean rewriting Alpha4Gate's PPO observation/action interface
from scratch. That's not a small port — it's a different agent
paradigm. **Not on the table** as a cheap alternative to porting
burnysc2-based code to Linux. The reason headless Linux is interesting
is precisely that **burnysc2 already supports it**, so we get the
infra unlock without a stack-swap.

That said, pysc2's existence is positive evidence that **the headless
Linux SC2 package is the production substrate for SC2-RL on Linux** —
DeepMind ran AlphaStar on it. We're not pioneering anything.

---

## 6. Alpha4Gate Windows-assumption audit

What breaks if we run today's pipeline on Linux. Greps from the repo:

### 6.1 Hardcoded SC2 install path (4 sites)

```
[src/orchestrator/selfplay.py:657]
os.environ.setdefault("SC2PATH", r"C:\Program Files (x86)\StarCraft II")

[scripts/spike_subprocess_selfplay.py:33]
os.environ.setdefault("SC2PATH", r"C:\Program Files (x86)\StarCraft II")

[scripts/evolve.py:284-286]
sc2_path = os.environ.get(
    "SC2PATH", r"C:\Program Files (x86)\StarCraft II"
)

[bots/v0/config.py:49] (also v1, v2)
sc2_path_str = os.getenv("SC2PATH", r"C:\Program Files (x86)\StarCraft II")
```

**All four use `setdefault` / `os.getenv` with a Windows fallback.**
The escape hatch already exists: set `SC2PATH=~/StarCraftII` in the
environment and these all work. The only fix needed is **changing the
fallback** to a platform-aware default (or just refusing to default and
requiring the env var). Trivial: ~4 lines per site. **Low risk.**

### 6.2 `sys.platform == "win32"` branches

```
[src/selfplay_viewer/reparent.py:54]   # Viewer-only — irrelevant for headless training
[scripts/selfplay.py:200]              # CLI wrapper for selfplay; viewer-related
[bots/v0/api.py:1660, 1684]            # /api/shutdown — uses SIGBREAK on win32, SIGINT elsewhere
                                       # Already cross-platform; the elif handles non-win32 correctly.
[tests/test_container_integration.py:32]  # @pytest.mark.skipif win32 only
[tests/test_reparent.py:27]               # @pytest.mark.skipif win32 only
[tests/test_reparent_portable.py:59]      # Tests the portable path
```

**None of these block headless Linux training.** The viewer
(`selfplay_viewer/reparent.py`, `scripts/selfplay.py`) is win32-only
by design — that's the reparenting code, which is irrelevant to
headless training. The api.py shutdown handler already has a
non-win32 branch. The viewer-related tests already skip on non-Windows.

### 6.3 pygame / pywin32

Grep found `pygame` imports in 17 files, all under
`src/selfplay_viewer/`, `tests/test_container_*`, `tests/test_overlay`,
`tests/test_reparent*`, `tests/test_backgrounds`, `tests/test_toast`,
`scripts/probe_sc2_min_size.py`. **No bot or training-pipeline file
imports pygame.**

`pywin32`: zero matches. The viewer uses the
[ctypes](https://docs.python.org/3/library/ctypes.html) win32 API
directly via custom code in `reparent.py`; no `pywin32` Python package
is involved.

**The viewer is correctly isolated as an optional extra
(`pyproject.toml [viewer]`).** Headless Linux training would not need
it and would not import it. No port work needed.

### 6.4 Path separators

I did not find any use of `\\` literal path separators in the bot or
learning code (the repo uses `pathlib.Path` and `Path` consistently —
e.g. [`bots/v0/config.py:50`](../../bots/v0/config.py#L50),
[`src/orchestrator/registry.py:43-52`](../../src/orchestrator/registry.py#L43-L52),
[`bots/v0/learning/checkpoints.py`](../../bots/v0/learning/checkpoints.py)).
The `bots/v0/learning/database.py` uses sqlite3 with relative paths
through Path objects. **No path-separator portability issues found.**

The exceptions are the 4 sites in §6.1 where the *fallback string*
is a Windows literal — but the value flows through `Path(...)` which
DTRT once the string is set correctly.

### 6.5 PPO checkpoint paths

`bots/v0/learning/checkpoints.py` uses `Path` throughout (verified by
grep — no hardcoded backslashes). Checkpoint zips are SB3-format and
are platform-portable (verified by SB3 docs; not re-verified here).
**Cross-platform safe.**

### 6.6 Daemon / evaluator / orchestrator

`bots/v0/learning/daemon.py`, `evaluator.py`, `trainer.py` — grep for
Windows-specific code returned **zero** matches for `win32`,
`platform`, `PowerShell`, `psexec`. The only `.exe` reference in
selfplay.py is `"SC2_x64" in name` (PID-name check at
[`selfplay.py:369`](../../src/orchestrator/selfplay.py#L369)) — burnysc2 itself
adds the `.exe` suffix on Windows but uses bare `SC2_x64` on Linux
(per `paths.BINPATH` table in §4). **`"SC2_x64" in name` matches
both** because it's a substring check. Lucky, but works.

### 6.7 Test markers

[`tests/test_environment.py:1332`](../../tests/test_environment.py#L1332)
runs SC2 integration tests gated on having SC2 installed. The marker
machinery already exists. Linux CI would re-use it.

### 6.8 What's left

The actual blockers are ~5 hardcoded SC2 path defaults, all with env-var
escape hatches. **Replace those defaults with platform-aware fallbacks
or require the env var explicitly.** Everything else either Just Works
or is already isolated under the optional viewer extra.

**This is a remarkably small Windows-assumption surface for a
Windows-developed bot.** The discipline of using `pathlib`, `os.getenv`,
and isolating pygame to the viewer pays off here.

---

## 7. What headless Linux unlocks

### 7.1 Per-instance memory savings

**Inferred (untested):** Blizzard's headless Linux package doesn't run
the renderer (no DirectX, no EGL by default unless `-eglpath` is
added). Community reports for the `SC2.x86_64` package put resident
memory at roughly **400–800 MB per process vs ~1.5–2.5 GB for
Windows retail**. If this holds, that's a **~3x reduction** in
per-instance footprint.

For Phase 9 evolve and Phase 6 self-play, where every game costs 2
SC2 processes, a 3x reduction means ~3x more parallel games per box.

### 7.2 Parallel-game ceiling

A 32 GB Linux box could plausibly host **12–20 concurrent self-play
games** (24–40 SC2 instances at ~600 MB each), vs ~4–6 on Windows.
**This is the biggest training-throughput unlock on the table.**

Caveat: burnysc2 serializes SC2 startup on Linux
([§4.3](#43-process-startup-serializes-on-linux)) so spinning up 40
processes in parallel isn't free. The 50s startup ceiling per process
matters for scale. We'd want to spin up a long-lived pool, not
spin-and-tear-down per game.

### 7.3 Cloud deployment

A headless Linux SC2 worker is a stock Linux container/VM. We can
deploy on:

- AWS EC2 (any compute-optimized instance — c5.large/xlarge for CPU
  workers, g4dn for GPU PPO updates).
- GCP Compute Engine (similar).
- Anywhere with a Linux VM and 16+ GB RAM.

**This is the leap that ends "single dev box" as the training ceiling.**
We can rent 4 cloud workers, run 64 concurrent self-play games, and
turn 1 day of soak into 4 hours. Phase 9 evolve becomes weekly instead
of daily.

### 7.4 Phase 9 / Phase 6 throughput

The user's memory entry
`project_evolve_2gate_validated.md` documents 7h 15m for 2
promotions. Each promotion needs ~20-game fitness + ~20-game
regression evals. At Linux-parallel scale (say 4-way parallel games),
the same 2 promotions could complete in **~2 hours**. Or, at the same
wall clock, we could run a 4x larger pool (12 candidates instead of
3), which is the true scaling lever for evolutionary search.

### 7.5 Master-plan relevance

- **Phase 6** (cross-version self-play as PPO signal) — directly
  consumes `run_batch`. Linear speedup on parallelism.
- **Phase 9** (improve-bot-evolve) — directly consumes `run_batch`.
  Same speedup.
- **Phase D** (PFSP-lineage regression, the next memory entry's "next
  step") — same.
- **Phase B/E/F** (already-shipped or planned PPO observation
  extensions) — orthogonal to runtime; unaffected.

The unlock is real and broad-base for the training-axis phases.

---

## 8. What it does NOT unlock

Be honest:

- **The self-play viewer is still dead on this path.** No window to
  embed; the headless package literally has no GUI. The 2026-04-24
  observer-spike conclusions stand independently.
- **The 1v1 server cap still applies.** Linux SC2 is the same SC2
  client; the "Only 1v1 supported when using multiple agents" cap
  documented in
  [`observer-restriction-workarounds-investigation.md`](observer-restriction-workarounds-investigation.md) §3
  is enforced server-side and is platform-independent.
- **The dashboard, frontend, advisor bridge are unchanged.** Those
  are Python / Node / FastAPI services that already run on any OS.
  No Linux-specific issue to solve there.
- **Dev experience on Windows is not a Linux issue.** Devs would
  still write code on Windows and push to Linux for training. That's
  a CI/dev-loop change, not a port.
- **No magical training improvement.** Headless Linux is a *runtime*
  speedup; it doesn't change what the PPO learns or how rewards work.
  The bot still needs better reward rules, better features, better
  curriculum — those are independent.

---

## 9. Risks and unknowns

### 9.1 Linux SC2 version mismatch with burnysc2

The README's listed Linux packages stop at **4.10** (2018), but
`s2client-proto` is at **5.0.15** (October 2025). burnysc2 has TODO
comments in
[`unit.py:474, 485`](../../.venv/lib/site-packages/sc2/unit.py#L474)
referencing fixes that arrive "in a new linux binary (5.0.4 or newer)".

**The concrete check:** does Blizzard publish 5.0.x Linux packages
somewhere not in the README, or is the latest ML/AI Linux build
genuinely 4.10? If 4.10, our maps (Simple64 from current Blizzard CDN)
might not load — game-data versions matter. **This is the single
biggest risk to the whole investigation.** It needs a 30-min spike to
settle (Spike 1 in §11).

If 4.10 is a hard ceiling, the unlock shrinks materially: training-data
parity between Linux and Windows runs would be impossible (different
patch levels = different unit balance), and we'd have to either freeze
the Windows retail at 4.10 (hard — Battle.net auto-updates) or accept
that Linux is its own self-contained training environment that doesn't
interop with the Windows dev box. That's livable for pure training
infra but eliminates "WSL2 + Windows retail" as a bridge mode.

**Verification path:** download `SC2.4.10.zip` per the README and see
whether the unzipped `Versions/` directory pins to a build number that
matches burnysc2's `versions.json`. Out of scope for this doc.

### 9.2 Linux SC2 maintenance burden

Blizzard hasn't updated the publicly-listed Linux package since 2018.
DeepMind hasn't updated pysc2 since 2023. **The Linux ML/AI ecosystem
around SC2 is in maintenance mode.** Survivable? Yes, IFF:
- We pin a single Linux SC2 version forever and don't try to track
  Battle.net.
- We accept that bug fixes in the Linux client may not arrive.
- We accept that any breaking proto changes in s2client-proto could
  outpace the Linux package.

The risk is "Blizzard takes the Linux package down" or "stops shipping
new patches." Neither has happened in 8 years. Probability: low. But
non-zero.

### 9.3 Cross-platform dev experience

If we develop on Windows and train on Linux, we need:
- Cross-platform CI for the test suite (today's CI is Windows-only —
  not verified by source for this doc; **inferred**).
- Path normalization for any test that asserts on a real path.
- A way to run `pytest -m sc2` on Linux against a headless install.
- Docs explaining when to run what where.

This is real engineering work. **Estimate:** 1–2 days to set up Linux
CI, plus an ongoing tax on "did this PR break Linux?" reviews.

### 9.4 Map files

The README implies the map packs work cross-platform. But if our
current Simple64 came from a *post-4.10* Blizzard CDN, the map binary
might require a newer client. **Inferred:** map files are mostly
forward-compatible because they're game-data. **Untested.** Worth
verifying in Spike 1.

### 9.5 Cost

A 32 GB Linux EC2 spot instance is ~$0.10/hour. A week of 24/7 soak
is ~$17. **Trivial.** This is not a budget risk.

GPU-backed boxes for PPO updates are more (g4dn.xlarge ~$0.50/hour spot)
but PPO updates are seconds-per-cycle compared to minutes-per-game on
SC2 throughput, so a single GPU box can fan out to many CPU game-worker
boxes.

---

## 10. Scope estimate

Concrete ranges with explicit uncertainty:

| Slice | Range | Notes |
|---|---|---|
| Spike 1: download 4.10, run hello-world game in WSL or VM | 2–4 hours | Decisive; settles the 4.10-vs-5.0.x question |
| Spike 2: run AggroBot self-play on headless Linux | 2–4 hours | Decisive; settles "does our code work?" |
| Spike 3: 4-way parallel games on a single Linux box | 4–8 hours | Decisive; measures the actual throughput unlock |
| Pipeline audit + Windows-assumption fixes | 4–8 hours | The 5 SC2PATH defaults + a config-pass pattern |
| Daemon / evaluator port (if needed beyond the SC2PATH fixes) | 0–4 hours | **Inferred ~0** based on §6 audit |
| Linux CI setup (or commitment to Linux-only training, no CI) | 1–3 days | Largest variable; depends on how much we want CI |
| Cloud deployment scaffolding (Dockerfile, basic ECS task or compose) | 1–2 days | Optional; depends on whether we go cloud-first |
| Master-plan re-sequencing + spec doc | 4–8 hours | Once we know it works |
| **Total spike-and-prove (steps 1–4 above)** | **2–4 days** | Decisive go/no-go before any phase work |
| **Total phase-quality build** | **8–15 days** | If we commit and build it out properly |

**Highest uncertainty:** the 4.10-version-mismatch question (§9.1).
If 4.10 is a hard ceiling and our current setup needs ≥5.0.4, the
spike-and-prove slice could grow by a factor of 2–3 (we'd need to
either downgrade our maps or shop for an unlisted Linux build).

---

## 11. Recommended spike sequence

Time-boxed, decisive. Halt at any failure and reassess.

### Spike 1: Hello-world Linux SC2 (2–4 hours) — DECISIVE on 4.10 viability

**Question:** Does Blizzard's listed `SC2.4.10.zip` actually run on a
modern Linux (Ubuntu 22.04 LTS in WSL2 or a fresh VM)? Does our
`Simple64` map load?

**Test:**
1. Install Ubuntu via WSL2 (`wsl --install -d Ubuntu-22.04` from
   PowerShell).
2. In WSL, `wget` `SC2.4.10.zip` from the README's CDN URL; `unzip
   -P iagreetotheeula` per the README.
3. Drop our `Simple64.SC2Map` (from the existing Windows install or
   re-download from current Blizzard CDN) into `~/StarCraftII/Maps/`.
4. Install Python 3.12, uv, burnysc2 v7.1.3 in WSL.
5. Set `SC2PATH=~/StarCraftII` and run a one-off `bots.v0.runner`
   game with `--decision-mode rules --difficulty 1 --map Simple64`.

**Outcomes:**
- Game completes → 4.10 works for us; proceed to Spike 2.
- Map fails to load → version-mismatch confirmed. Either downgrade
  the map or kill the investigation here. Note in master plan that
  Linux training requires a forked map set.
- SC2 fails to launch → check libstdc++/libc compat; possibly need
  a newer Ubuntu base. Likely solvable but a yellow flag.

**Cost-bound:** if it doesn't work in 4 hours, stop. The risk
materialized.

### Spike 2: Existing self-play on headless Linux (2–4 hours) — DECISIVE on code-path viability

**Question:** Does `src/orchestrator/selfplay.py:run_batch` work
unmodified (other than the SC2PATH default fix from §6.1) on headless
Linux?

**Test:**
1. Apply minimal patch to the 5 SC2PATH default sites: respect the env
   var and refuse to default to a Windows path on Linux.
2. From WSL, run `scripts/selfplay.py --p1 v0 --p2 v0 --games 2 --map
   Simple64`.
3. Watch for: port-collision patch effective? signal-handler patch
   effective? SC2 PIDs discovered? game completes?

**Outcomes:**
- 2 games complete cleanly → entire architecture is viable on Linux.
  **This is the critical green light.** Proceed to Spike 3.
- Port collision or signal-thread error → existing patches need
  Linux-specific tweaks. Probably 1-2 day fix.
- Game crashes mid-game → unit/proto incompatibility with Linux 4.10.
  Reassess.

**Cost-bound:** if the 2-game sample is hopeless after 4 hours, stop.

### Spike 3: 4-way parallel self-play (4–8 hours) — DECISIVE on the actual unlock

**Question:** Can we sustainably run 4 concurrent self-play games on a
single 32 GB Linux box, and what's the per-game memory footprint?

**Test:**
1. Spin up 4 background `selfplay.py` invocations, each with `--games
   5 --map Simple64`. (Or modify run_batch to launch in parallel — but
   for the spike, separate processes are simpler.)
2. Monitor `top` / `ps -aux | grep SC2_x64` for resident memory and
   CPU per process.
3. Tally completion time and crash rate vs the Windows serial
   baseline.

**Outcomes:**
- 4 parallel games complete with ~600 MB/process, no crashes → unlock
  is real, scope estimate is accurate, cloud deployment is the next
  obvious step.
- Heavy crashes / port collisions / RAM blowup → the burnysc2 Linux
  serialization warning (§4.3) may bite us; need to architect a long-lived SC2 pool with serial startup
  followed by parallel game-play.
- Parallel works but per-process RAM is comparable to Windows
  (1.5-2 GB) → the unlock is much smaller than estimated. Master-plan
  impact shrinks; reconsider whether to invest.

### Spike 4 (only if 1–3 succeed): Cloud-cost dry run (0.5 day)

**Question:** What does a 24-hour cloud soak actually cost and what
throughput does it produce?

**Test:** Spin up a c5.2xlarge spot instance, install our stack via a
Dockerfile, run a 24-hour Phase 9 evolve cycle, count promotions, sum
spot-instance hours billed.

**Outcomes:** concrete cost-per-promotion number. Settles whether to
make cloud-soak the standard or keep dev-box soak as primary.

---

## 12. Recommendation

**Yes — invest, but spike-first.**

Reasoning:

1. The infrastructure win is large: ~3x per-instance memory reduction
   (inferred) translates directly into ~3x more parallel games per
   box, which compounds across Phase 6 and Phase 9 — every operational
   training phase. That's the bottleneck of the whole training axis.
2. The codebase is **remarkably ready** for the port. The Windows-isms
   are 5 hardcoded fallback paths and an isolated pygame viewer.
   The training-pipeline modules (`bots/v0/learning/*.py`) have **zero**
   `win32` / `platform` references. burnysc2 itself has first-class
   Linux support.
3. The risk surface is small but real: the 4.10-version-mismatch
   question is the dominant unknown. **Spike 1 (~3 hours) settles it
   conclusively** before any phase commitment.
4. pysc2 is not a viable alternative — the API rewrite is too deep.
   But its existence and DeepMind's track record are positive evidence
   that the Linux substrate works for serious RL.
5. The master-plan slot is "**Phase 8** (or 8.5)" — sequenced AFTER the
   current Phase 6 (cross-version self-play as PPO signal) is operating
   on Windows, so we have a working baseline to compare Linux against.
   Fitting it before Phase 9 (improve-bot-evolve) means evolve gets the
   throughput unlock from day one, which is exactly the use case that
   most needs it. Reserving "Phase 8" implies B/D/E/F (Phase 7 already
   exists) push out one slot — minor cost relative to the unlock.

**Concrete recommendation:** allocate 2–4 days for Spikes 1–3 in a
side worktree. If all three pass, draft a Phase 8 plan via
`/plan-feature` and sequence it before Phase 9. If Spike 1 fails on
the version-mismatch issue, the answer is "defer until Blizzard ships
a newer Linux package, or until we accept a forked map set" — which is
a deferral, not a kill.

The user's instinct that this is a major training unlock is correct.
The Windows-assumption surface is small enough that the port is
tractable. The dominant risk is the version-mismatch question, which
is a single download-and-run away from being settled.

---

## 13. What this doc deliberately does not do

- It does not run any spikes. Spike 1 is described, not executed.
- It does not propose code, file diffs, or phase build plans. That's
  for `/plan-feature` after Spike 1–3 settle.
- It does not measure per-instance memory empirically — the ~3x
  reduction is inferred from community reports, marked accordingly.
- It does not commit to a phase number or a master-plan
  re-sequencing — that's the user's call after spike data lands.
- It does not assess whether cloud deployment is right for Alpha4Gate's
  governance/cost model — only that it's *possible*. Operational
  decisions live elsewhere.

This investigation is research. Whether and when to invest is the
user's decision after the spikes settle the dominant unknowns.
