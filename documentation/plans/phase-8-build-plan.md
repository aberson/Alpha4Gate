# Phase 8 — Headless Linux SC2 Training Infrastructure

**Track:** Operational substrate.
**Prerequisites:** Phase 5 (sandbox + skill integration) complete. Independent of Phase 9 evolve, Phase 6 self-play, Phase 7 advised-staleness, and capability phases B/D/E.
**Slot:** Reclaims the previously-empty Phase 8 slot in `documentation/plans/alpha4gate-master-plan.md` (Phase 8 was intentionally skipped during the 2026-04-19 renumber to align Phase 9 with issue titles `#154-#161`; this plan reclaims it).
**Date drafted:** 2026-04-24.

---

## 1. What this feature does

Phase 8 ports Alpha4Gate's training pipeline to run on **Blizzard's headless Linux SC2 package** (`SC2.x86_64`, distributed at `https://blzdistsc2-a.akamaihd.net/Linux/`). Headless Linux SC2 has no renderer, runs in 400-800 MB of RAM per process (vs ~1.5-2.5 GB for Windows retail), and is the substrate AlphaStar used for production-scale RL on SC2.

**Why now:** Phase 9 evolve (operational since 2026-04-24) and Phase 6 cross-version self-play both consume `src/orchestrator/selfplay.py:run_batch`. Per-game wall-clock is the binding constraint on both — the validated Phase 9 soak `20260423-2052` produced 2 promotions in 7h 15m, and the loop is bottlenecked by SC2 game throughput, not algorithm. A ~3x per-instance memory reduction translates directly into ~3x more parallel games per box, which compounds across every operational training phase.

**Why now is safe:** the [headless-linux-training investigation](../investigations/headless-linux-training-investigation.md) audited the codebase and found a remarkably small Windows-assumption surface (5 hardcoded `SC2PATH` defaults, all with `os.getenv` escape hatches; pygame fully isolated under the optional `[viewer]` extra; zero `win32`/`platform` references in `bots/v0/learning/`). burnysc2 v7.1.3 has first-class Linux paths in [`paths.py`](../../.venv/lib/site-packages/sc2/paths.py#L14-L48), [`main.py`](../../.venv/lib/site-packages/sc2/main.py#L694-L703), [`proxy.py`](../../.venv/lib/site-packages/sc2/proxy.py#L177-L180), and [`sc2process.py`](../../.venv/lib/site-packages/sc2/sc2process.py#L268-L272).

**Why spike-first:** the previous viewer plan was written from a code-only investigation (no spike) and burned 2 sessions + 7 closed GitHub issues before discovering its foundation was rotten (server-side 1v1-only multi-agent cap; show_map perception-affecting). Phase 8 Spike 1 (download Blizzard's `SC2.4.10.zip` and run a hello-world game in WSL) is a 2-4 hour decisive test that settles the dominant unknown — does a 2018-era 4.10 Linux package play nicely with modern burnysc2 v7.1.3 + our Simple64 map. **No production code touches master until Spikes 1, 2, 3 all pass.**

---

## 2. Existing context

A fresh-context model needs this orientation:

**Training pipeline shape.** `bots/v0/learning/environment.py:702-707` runs one SC2 game per `SC2Env` instance (`Bot` vs `Computer`). The PPO orchestrator runs cycles serially. Self-play (`src/orchestrator/selfplay.py:603-607`) spawns two SC2 processes per game via burnysc2's `BotProcess` + `a_run_multiple_games`. Phase 9 evolve and Phase 6 self-play both invoke `run_batch`. The orchestrator runs games one-at-a-time within a batch by design.

**Per-version stack layout.** Production code lives in `bots/v0/` (Phase 1 migration shipped 2026-04-16). `bots/current/` is a MetaPathFinder alias to the active version. `src/orchestrator/` is the cross-version frozen substrate (registry, contracts, snapshot, selfplay, ladder). Sandbox enforcement (Phase 5, `scripts/check_sandbox.py`) restricts autonomous-mode commits — `ADVISED_AUTO=1` to `bots/current/**`, `EVO_AUTO=1` to `bots/**`. No autonomous mode currently writes to `src/orchestrator/`, `tests/`, `scripts/`, `pyproject.toml`, or `frontend/`.

**burnysc2 Linux story.** `paths.py:14-48` declares 6 platforms (`Windows`, `WSL1`, `WSL2`, `Darwin`, `Linux`, `WineLinux`). Linux uses `~/StarCraftII` install path, `SC2_x64` binary (no `.exe`), no `Support64/` cwd. Linux serializes SC2 startup (`main.py:694-703`, comment: *"Doesnt seem to work on linux: starting 2 clients nearly at the same time"*). EGL render mode is Linux-only (`-eglpath libEGL.so` flag, only added when `render=True`). Replay path quirk: on Linux, `start_replay` insists the file live in `~/Documents/StarCraft II/Replays/` and uses just the filename; on Windows it accepts an absolute path.

**The 4.10 risk.** Blizzard's `s2client-proto` README lists Linux packages up to **4.10** (2018) but the proto repo is at **5.0.15** (October 2025). burnysc2 has TODO comments in `unit.py:474, 485` referencing fixes "in a new linux binary (5.0.4 or newer)." If our Simple64 map (from the current Blizzard CDN) requires a post-4.10 client, training-data parity between Linux and Windows is impossible. Spike 1 settles this in one download.

**pysc2 is not on the table.** DeepMind's `pysc2` is Linux-canonical but maintenance-only since April 2023, and its Feature Layer / RGB API is incompatible with Alpha4Gate's burnysc2-based scalar-feature PPO. Switching would mean rewriting the agent paradigm. Rejected by investigation §5.3.

**Viewer thread is paused indefinitely.** The 2-pane `src/selfplay_viewer/` ships and works, but the observer-based refactor was BLOCKED 2026-04-24 by the SC2 server's 1v1-only multi-agent cap (`InvalidPlayerSetup` error) and `debug_show_map()` being perception-affecting. Phase 8 does not revive viewer work. If headless Linux ships and changes viewer requirements, that's a future plan.

---

## 3. Scope

**In scope:**

- Spike 1, 2, 3 (and conditional Spike 4) per investigation §11.
- WSL2 Ubuntu 22.04 dev environment setup as the spike platform.
- Replace 5 hardcoded Windows `SC2PATH` defaults with platform-aware fallbacks.
- Document Linux dev-environment recipe (uv, Python 3.12, dev-dep follow-up).
- One smoke-gate game on Linux end-to-end.
- GitHub Actions Linux CI workflow for unit tests + ruff + mypy (no `-m sc2`).
- Dockerfile for a headless-Linux SC2 worker image (no orchestration scaffolding).
- 24-hour Phase 9 evolve soak on Linux as the long-observation step.
- Master-plan Phase 8 section + plan-history entry + reclamation note.
- Conditional Spike 4: cloud-cost dry run on AWS spot instance.

**Explicitly out of scope:**

- pysc2 migration (rejected by investigation).
- Self-play viewer revival on Linux (no headless renderer; out of scope by design).
- AWS-specific orchestration (ECS/Fargate task definitions, IAM roles, secrets management). Dockerfile only in Phase 8; cloud orchestration becomes a future plan if Step 12 cost-dry-run shows it's worth it.
- `pytest -m sc2` on a self-hosted Linux CI runner. Unit tests only.
- Cross-platform replay file normalization (the Linux replay-path quirk in burnysc2 §4.7) — deferred unless the soak surfaces it.
- Per-version Linux venvs. Shared repo deps stay shared.
- Linux desktop dev (devs still write code on Windows; Linux is for training).
- Reward / feature / PPO architectural changes (orthogonal).
- GPU support (still excluded per master-plan compute target).
- Multi-race work (Phase G).

---

## 4. Impact analysis

| File / module | Change type | Detail |
|---|---|---|
| [src/orchestrator/selfplay.py:657](../../src/orchestrator/selfplay.py#L657) | Modify | Replace `setdefault("SC2PATH", r"C:\Program Files (x86)\StarCraft II")` with platform-aware fallback |
| [scripts/spike_subprocess_selfplay.py:33](../../scripts/spike_subprocess_selfplay.py#L33) | Modify | Same fallback pattern |
| [scripts/evolve.py:284-286](../../scripts/evolve.py#L284-L286) | Modify | Same |
| [bots/v0/config.py:49](../../bots/v0/config.py#L49) | Modify | Same |
| [bots/v1/config.py:49](../../bots/v1/config.py#L49) | Modify | Same |
| [bots/v2/config.py:49](../../bots/v2/config.py#L49) | Modify | Same |
| `src/orchestrator/paths.py` | New | Single platform-aware `resolve_sc2_path()` resolver imported by all 6 callsites |
| `src/orchestrator/` (rest) | No change | Audit confirmed cross-platform |
| `bots/v0/learning/*.py` | No change | Zero `win32`/`platform` references confirmed |
| `src/selfplay_viewer/` | No change | win32-by-design; isolated under `[viewer]` extra |
| `bots/v0/api.py:1660, 1684` | No change | Already has cross-platform `elif` for non-win32 |
| `frontend/` | No change | Already cross-platform |
| `pyproject.toml` | No change | Investigation confirmed no missing extras |
| `documentation/plans/alpha4gate-master-plan.md` | Extend | Add Phase 8 section between Phase 5 and Phase B; remove "intentionally skipped" note; add plan-history entry; bump time-budget table |
| `tests/test_sc2path_fallback.py` | New | Platform-conditional tests for the SC2PATH fallback |
| `documentation/wiki/linux-dev-environment.md` | New | uv + Python 3.12 + dev-deps recipe for WSL/Linux |
| `documentation/wiki/cloud-deployment.md` | New | Dockerfile usage + spot-instance run-book |
| `.github/workflows/linux-tests.yml` | New | Ubuntu 22.04, Python 3.12, pytest + ruff + mypy |
| `Dockerfile` | New | Repo-root file producing the headless-Linux worker image |
| `.dockerignore` | New | Excludes `.venv*/`, `data/`, `logs/`, `bots/v*/data/`, `frontend/node_modules/`, replays |
| `documentation/soak-test-runs/spike-{1,2,3}-<TS>.md` | New (per spike attempt) | Operator records; `<TS>` = `YYYYMMDD-HHMM` so re-runs after a halt-and-fix don't overwrite |
| `documentation/soak-test-runs/phase-8-smoke-gate-<TS>.md` | New | Smoke-gate record |
| `documentation/soak-test-runs/evolve-linux-24h-<TS>.md` | New | 24-hour soak report |

No migrations. No backwards-compat shims. No new Python dependencies. The five `SC2PATH` defaults all use `setdefault`/`os.getenv` — the env-var override mechanism already exists.

---

## 5. New components

- **Platform-aware `SC2PATH` resolver.** New module `src/orchestrator/paths.py` exposing `resolve_sc2_path() -> Path`. Returns `~/StarCraftII` on Linux, `C:\Program Files (x86)\StarCraft II` on Windows, `/mnt/c/Program Files (x86)/StarCraft II` on WSL2, raises `RuntimeError("set SC2PATH env var")` on unknown platforms. Imported by all 6 callsites (`src/orchestrator/selfplay.py`, `scripts/spike_subprocess_selfplay.py`, `scripts/evolve.py`, `bots/{v0,v1,v2}/config.py`). Honors `os.getenv("SC2PATH")` first, falls back to platform default — preserves the existing env-var escape hatch on every callsite. Located in `src/orchestrator/` (not `bots/v0/`) so v1/v2 don't need duplicate copies and the import direction stays `bots/* → src/orchestrator/*` (consistent with the master-plan rule "do NOT import `bots.current` or `bots.<version>` from `src/orchestrator/`" — the reverse direction is the allowed one).
- **`tests/test_sc2path_fallback.py`.** Platform-conditional tests using `monkeypatch` on `platform.system()` to verify each branch. No actual SC2 install needed. Includes "unknown platform raises" assertion.
- **`Dockerfile` (repo root).** Multi-stage build for layer-cache efficiency:
    - Stage 1 (`sc2-base`): Ubuntu 22.04 + apt deps (`curl`, `unzip`, `libstdc++6`). Downloads + extracts `SC2.4.10.zip` to `/opt/StarCraftII`. This stage rebuilds only when the SC2 version pin changes — typically never within a phase.
    - Stage 2 (`python-base`): builds on stage 1, installs Python 3.12 + uv. Rebuilds on Python/uv version bump.
    - Stage 3 (`app`): builds on stage 2, copies `pyproject.toml` + `uv.lock` first (`RUN uv sync --frozen`), THEN copies the rest of the repo. This ordering means dep changes invalidate the cache from stage 3 onward; code-only changes only invalidate the final COPY layer.
    - `ENV SC2PATH=/opt/StarCraftII`.
    - Non-root user `alpha4gate` created and `USER alpha4gate` set before the entrypoint.
    - `ENTRYPOINT ["uv", "run", "python", "-m"]` + `CMD ["bots.v0", "--role", "solo", "--map", "Simple64", "--difficulty", "1", "--decision-mode", "rules"]` — default behavior is the smoke-gate game; `docker run <image> scripts.selfplay --p1 v0 --p2 v0 --games 5` overrides cleanly. Operators can also pass `scripts.evolve --hours 24 ...` for soak runs.
    - **License caveat:** the Dockerfile downloads the headless package at build time per Blizzard's AI/ML license; the image is not redistributable, must be built locally on each host, and must NOT be pushed to any public registry.
    - **Optional CDN cache:** if Blizzard ever takes the 4.10 zip down (low-but-nonzero risk per investigation §9.2), operators can set `SC2_PACKAGE_URL=file:///path/to/local/SC2.4.10.zip` as a build-arg; the Dockerfile checks `${SC2_PACKAGE_URL:-https://blzdistsc2-a.akamaihd.net/Linux/SC2.4.10.zip}`. Operators are responsible for archiving the zip on first successful build.
- **`.dockerignore`.** Standard Python + Node exclusions plus `data*/`, `logs/`, `bots/v*/data/checkpoints/`, replay zips, `documentation/soak-test-runs/`.
- **`.github/workflows/linux-tests.yml`.** GitHub-hosted Ubuntu 22.04 runner; checkout + setup-python 3.12 + install-uv + `actions/cache` keyed on `uv.lock` with cache path `~/.cache/uv` + `uv sync --frozen` + `uv run pytest -m "not sc2"` + `uv run ruff check .` + `uv run mypy src bots --strict`. **No SC2 install on the runner** — `pytest -m sc2` deselected via marker, not file-ignore (more reliable across future test relocations).
- **`.github/workflows/docker-build.yml`.** Separate workflow that runs `docker build` on PRs touching `Dockerfile`, `.dockerignore`, `pyproject.toml`, or `uv.lock`. Catches Dockerfile regressions before deployment. No image push — build-only.
- **`documentation/wiki/linux-dev-environment.md`.** Recipe for new contributors: install WSL2 Ubuntu 22.04 → install Python 3.12 + uv → `uv sync` → fix dev-deps gap (per memory `feedback_worktree_venv_incomplete.md`) → `SC2PATH=~/StarCraftII` → smoke check.
- **`documentation/wiki/cloud-deployment.md`.** Three sections:
    1. **Build:** `docker build -t alpha4gate-worker .` on a Linux host (or `docker buildx` from any host); estimated build time + size; how to optionally pre-cache the SC2 package via `SC2_PACKAGE_URL`.
    2. **Run locally:** `docker run --rm -v $PWD/data-snapshots:/app/data-snapshots alpha4gate-worker ...` — sample invocations for solo, selfplay, and evolve modes; how to recover output from the volume mount.
    3. **Run on AWS spot:** `c5.2xlarge` recommended; how to provision via the AWS console (no Terraform/CDK in Phase 8); credential handling (`aws configure` on the issuing host, never bake into the image); cost expectation (~$0.10/hour spot, ~$2.40 for a 24-hour soak); how to retrieve results from the spot instance before termination.

---

## 6. Design decisions

**Resolver lives in `src/orchestrator/paths.py`, not `bots/v0/config.py`.** A single new module avoids 3 divergent copies in `bots/{v0,v1,v2}/config.py`. The import direction `bots/* → src/orchestrator/*` is the master-plan-allowed direction (the rule "do NOT import `bots.current` or `bots.<version>` from `src/orchestrator/`" is the *reverse* — preventing MetaPathFinder loops). Locating the resolver in `src/orchestrator/` is a write to the "frozen substrate," but `paths.py` is a NEW file (additive, not a modification of contracts/registry/snapshot/selfplay/ladder), so the freeze rule's spirit isn't violated. Phase 8 ships as a human PR (no autonomous mode), so the sandbox hook permits the write either way. Alternative considered: place in `bots/v0/config.py` and import from v1/v2. Rejected — guarantees future drift across version configs.

**Spike-first, hard-gated.** Build steps 5+ are only authorized if Spikes 1-3 all pass. Spike 1 is decisive on the 4.10-version-mismatch question (the dominant unknown per investigation §9.1) — if 4.10 fails to launch our Simple64 map or won't run on Ubuntu 22.04, the phase defers, not builds. Memory entry `feedback_spike_before_build.md` is the controlling rule. Alternative considered: code-first with the 5 SC2PATH fixes as a no-spike PR. Rejected — verified by the prior viewer plan's failure that this approach can burn weeks before discovering an unfixable foundation.

**WSL2 over a native Linux VM for spikes.** The user's primary box runs Windows 11 with WSL2 already installed; a fresh VM adds ~half a day of setup per spike for no diagnostic benefit. WSL2's `paths.py` branch (`SC2PF=WSL2`) is a first-class burnysc2 mode, so spike results transfer cleanly. Caveat: WSL2 file I/O across the `/mnt/c/...` boundary is slower than native — the spike measures should be interpreted as a lower bound on a true native-Linux box. Alternative considered: Hyper-V Ubuntu VM. Rejected — additional setup time without diagnostic gain at spike scale.

**`phase-8-build-plan.md` filename, not `headless-linux-training-plan.md`.** All other numbered phases use `phase-N-build-plan.md` (Phases 1-9 inclusive), so this convention is strong. The original brief's `headless-linux-training-plan.md` was a topic-name rather than a phase-name; the convention wins here for symmetry with the master plan's pointer-style links.

**Reclaim Phase 8 slot rather than renumber.** Master plan currently states "There is no Phase 8 — that slot is intentionally skipped, similar to how Phase C was dropped in an earlier merge" — created during the 2026-04-19 renumber to align Phase 9 with pre-existing issue titles `#154-#161`. The slot is genuinely available; reclaiming it is cheaper than renumbering Phase 9 (and renaming 8 issue titles). Plan-history entry will note the reclamation.

**CI: unit tests only on Linux, no `pytest -m sc2`.** A self-hosted Linux runner with the headless package preinstalled is feasible but adds ongoing maintenance (image updates, runner uptime). Phase 8 takes the cheap path: GitHub-hosted Ubuntu runner, unit tests + lint + type-check only. `-m sc2` integration tests stay on Windows for now. If Phase 9 evolve starts gating on Linux-specific behavior, a self-hosted runner becomes a future plan. Alternative considered: self-hosted runner. Rejected for Phase 8 scope; defer until justified.

**Cloud scaffolding: Dockerfile only, no AWS task def.** A buildable Docker image is the smallest unit that proves "this can run in the cloud" without committing to any specific cloud provider. ECS/Fargate task definitions, IAM scaffolding, secrets management, and orchestration are deferred to a future plan that would be triggered if Step 12 (conditional cloud cost dry-run) shows the unlock is worth the operator overhead. Alternative considered: full AWS scaffolding. Rejected — premature for Phase 8; the Dockerfile + cloud run-book is sufficient to prove viability.

**Smoke gate before any soak.** Step 7 is a mandatory one-game end-to-end run on Linux with real components (no mocks) before Step 11's 24-hour soak. Catches the producer/consumer drift class of bug — "SC2PATH fix shipped, but the daemon doesn't see it"; "DB writer works in unit tests but the new platform path breaks SQLite locking" — that mocked unit tests miss by definition. Per plan-feature skill rule.

**24-hour observation soak on real Linux.** Phase 8 is observability-adjacent infrastructure for autonomous behavior (Phase 9 evolve). Per plan-feature skill rule, the build steps include a deliberate observation phase with realistic inputs, run long enough to expose Linux-specific time-dependent failures: parallel-startup races (per burnysc2's serialization warning in `main.py:694-703`), EGL teardown bugs, signal-handler differences, replay-path quirks. 24 hours matches the wall-clock of a non-trivial Phase 9 evolve run with multiple promotion attempts.

**No data migration.** Per-version `training.db`, `reward_rules.json`, `hyperparams.json`, and checkpoints are all `pathlib.Path` + relative paths + cross-platform formats. SB3 checkpoints zip cleanly cross-platform. SQLite DBs move cross-platform via file copy. **The 5 SC2PATH defaults are the entire migration surface.**

---

## 7. Build steps

### Step 1: Operator — WSL2 Ubuntu 22.04 dev environment

- **Type:** operator
- **Issue:** #
- **Produces:** working WSL2 Ubuntu 22.04 with Python 3.12, uv, build-essential
- **Done when:** `wsl -d Ubuntu-22.04 -- python3.12 --version` returns `Python 3.12.x` AND `wsl -d Ubuntu-22.04 -- uv --version` succeeds
- **Depends on:** none

**Operator commands (PowerShell, on Windows host):**

```powershell
wsl --install -d Ubuntu-22.04
# Reboot if prompted, then on first WSL launch set username/password.
# python3.12 is NOT in Ubuntu 22.04's default repos (Jammy ships 3.10), so the deadsnakes PPA is required:
wsl -d Ubuntu-22.04 -- bash -lc 'sudo apt-get update && sudo apt-get install -y software-properties-common && sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv build-essential curl unzip'
wsl -d Ubuntu-22.04 -- bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
wsl -d Ubuntu-22.04 -- bash -lc 'source ~/.local/bin/env 2>/dev/null || source ~/.cargo/env 2>/dev/null; uv --version'
```

What to look for: Ubuntu prompt appears, `python3.12` resolves, `uv --version` returns a version string. Expect ~30 minutes wall-clock including the WSL kernel install and reboot. The modern uv installer (0.4+) writes to `~/.local/bin/env`; the older `~/.cargo/env` path is kept as a fallback.

**Cleanup (if needed):** `wsl --unregister Ubuntu-22.04` from PowerShell removes the distro entirely; re-run Step 1 to start fresh. Use this if Spike 1 fails on a libc/SC2 mismatch and you want to retry on a different Ubuntu version.

### Step 2: Operator — Spike 1: hello-world Linux SC2 (DECISIVE)

- **Type:** operator
- **Issue:** #
- **Produces:** `documentation/soak-test-runs/spike-1-hello-world-linux-sc2-<TS>.md` (record; `<TS>` = `YYYYMMDD-HHMM`)
- **Done when:** `bots.v0.runner` completes one `--difficulty 1` game on Simple64 in WSL with `SC2PATH=~/StarCraftII`, OR a documented failure with halt decision
- **Depends on:** 1
- **HALT CONDITION:** 4-hour timebox. If the game fails to launch, the map fails to load, or the SC2 binary crashes on libc/libstdc++ mismatch, **stop here**. Update master plan as "Phase 8 deferred until Blizzard ships newer Linux package." Do not proceed to Step 3.

**Pre-flight (one-time, four env vars).** Three classes of problem need preempting before any spike command runs: (a) WSL's auto-detected mode in burnysc2 silently runs the WINDOWS SC2 binary; (b) uv on `/mnt/c/...` fails on Linux chmod ops; (c) Ubuntu's stock `.bashrc` early-returns for non-interactive shells, so vars there don't reach `wsl -d ... -- bash -lc '...'`. All three are fixed by writing the right four env vars to `~/.profile` (NOT `~/.bashrc`).

**Why each var:**

- `UV_PROJECT_ENVIRONMENT=$HOME/venv-alpha4gate-linux` — pin uv's venv to a Linux-native ext4 path. The repo lives on `/mnt/c/...` (NTFS via DrvFS); uv's atomic-move + chmod operations during package install fail there with `Operation not permitted (os error 1)`. **Tested: simply naming `.venv-linux` (relative, on /mnt/c) crashes on the very first non-trivial wheel install.** Putting the venv on `~/...` (ext4 native) bypasses DrvFS entirely.
- `SC2PATH=$HOME/StarCraftII` — burnysc2's `paths.py:14-48` Linux branch defaults to this; setting explicitly insulates against future package changes.
- `SC2_WSL_DETECT=0` — bypass burnysc2's auto-detection. Without this, `sc2/wsl.py:73` returns `"WSL2"` (since `WSL_DISTRO_NAME` is set), which forces burnysc2 into WSL2 mode (Windows binary at `/mnt/c/Program Files (x86)/StarCraft II/Support64`, launched via `powershell.exe`). With it, `wsl.detect()` returns `None`, `platform_detect()` falls through to `"Linux"`, and burnysc2 uses `~/StarCraftII/Versions/Base*/SC2_x64` directly inside WSL.
- `.bashrc` vs `.profile`: Ubuntu's default `.bashrc` has `case $- in *i*) ;; *) return;; esac` near the top, so any `export` at the END of `.bashrc` never executes for `wsl -- bash -lc '...'` calls. `.profile` runs for every login shell (interactive AND non-interactive via `-l`).
- **Verification gotcha:** do NOT verify with `wsl -- bash -lc 'echo $UV_PROJECT_ENVIRONMENT'`. The outer (Windows-side) shell may expand `$UV_PROJECT_ENVIRONMENT` to empty BEFORE wsl sees it, returning a false-empty result even when the var is set inside wsl. Use `printenv` (named lookup, no expansion) instead.

```bash
echo 'export UV_PROJECT_ENVIRONMENT=$HOME/venv-alpha4gate-linux' >> ~/.profile
echo 'export SC2PATH=$HOME/StarCraftII' >> ~/.profile
echo 'export SC2_WSL_DETECT=0' >> ~/.profile
# Verify in a FRESH login shell using printenv (NOT echo $VAR):
wsl -d Ubuntu-22.04 -- bash -lc 'printenv UV_PROJECT_ENVIRONMENT SC2PATH SC2_WSL_DETECT'
```

**Operator commands (in WSL):**

```bash
mkdir -p ~/StarCraftII && cd ~/StarCraftII
wget https://blzdistsc2-a.akamaihd.net/Linux/SC2.4.10.zip   # ~4.1 GB (plan originally said ~2 GB; reality is 2x)
unzip -P iagreetotheeula SC2.4.10.zip
# The zip extracts into a SECOND `StarCraftII/` subdir — flatten so $SC2PATH resolves directly:
shopt -s dotglob && mv StarCraftII/* . && rmdir StarCraftII && shopt -u dotglob
# Simple64.SC2Map IS bundled in the 4.10 package at Maps/Melee/. Copy to flat Maps/ to match Windows-install layout:
cp ~/StarCraftII/Maps/Melee/Simple64.SC2Map ~/StarCraftII/Maps/
# Linux SC2 4.10 looks up maps via lowercase `maps/` (case-sensitive FS); symlink so both cases resolve:
ln -s ~/StarCraftII/Maps ~/StarCraftII/maps
# Quick libc sanity check — if this lists "not found" libs, halt the spike (libc/libstdc++ mismatch):
ldd ~/StarCraftII/Versions/Base*/SC2_x64 | grep -E "not found" && echo HALT
cd /mnt/c/Users/abero/dev/Alpha4Gate
uv sync                         # Creates ~/venv-alpha4gate-linux (~4.8 GB, ~2 min on warm cache)
uv run python -m bots.v0 --role solo --map Simple64 --difficulty 1 --decision-mode rules --no-claude --game-time-limit 600
```

What to look for: SC2 binary launches (no missing-library errors), Simple64 loads, bot enters game, game completes with a Result. Record peak `SC2_x64` resident memory via `pgrep SC2_x64 | xargs -I{} ps -o rss= -p {}` sampled every 1s during the game (the spike-1 record markdown documents the script). The `SC2PATH` env var honors the existing `setdefault`/`os.getenv` escape hatch in `bots/v0/config.py:49` — Step 5's resolver fix isn't needed for the spike to run.

Spike 1 deliberately uses `--no-claude` (advisor not relevant to "does SC2 launch on Linux") and `--game-time-limit 600` (10 in-game minutes is enough for a difficulty-1 game; default 1800 is wasteful for a smoke test).

### Step 3: Operator — Spike 2: existing self-play unmodified on Linux (DECISIVE)

- **Type:** operator
- **Issue:** #
- **Produces:** `documentation/soak-test-runs/spike-2-selfplay-linux-<TS>.md` (record)
- **Done when:** `scripts/selfplay.py --p1 v0 --p2 v0 --games 2` completes 2 clean games in WSL with the SC2PATH fix from Step 5 backported as a manual env-var override
- **Depends on:** 2
- **HALT CONDITION:** 4-hour timebox. If the 2-game sample is hopeless (port collisions, signal-thread errors, mid-game crashes), stop and reassess. The architecture may need Linux-specific tweaks before any Step 5+ code work.

**Operator commands (in WSL):**

```bash
cd <repo path>
SC2PATH=~/StarCraftII uv run python scripts/selfplay.py --p1 v0 --p2 v0 --games 2 --map Simple64
```

What to look for: 2 SC2 PIDs spawn per game (4 total observed in `ps -aux | grep SC2_x64`), the port-collision patch in `selfplay.py` works on Linux, both games complete without orphaning processes, results land in `data/selfplay_results.jsonl`.

### Step 4: Operator — Spike 3: 4-way parallel self-play (DECISIVE on unlock size)

- **Type:** operator
- **Issue:** #
- **Produces:** `documentation/soak-test-runs/spike-3-parallel-linux-<TS>.md` (record), measured per-process RAM table, SQLite mode check
- **Done when:** 4 concurrent `selfplay.py` invocations complete; per-process resident RAM measured; crash rate < 5%; total wall-clock recorded vs serial baseline; `sqlite3 bots/v0/data/training.db 'PRAGMA journal_mode;'` checked — if not `wal`, note as a Step 5 follow-up (parallel SQLite writes on Linux benefit from WAL mode; existing Windows runs have not needed it because games are serial)
- **Depends on:** 3
- **HALT CONDITION:** 8-hour timebox. If RAM blowup or heavy crashes, the unlock is smaller than estimated. Reassess phase scope — possibly drop Steps 9, 11, 12 and ship only the SC2PATH fix + CI as a partial Phase 8.

**Operator commands (in WSL, 4 background invocations):**

```bash
for i in 1 2 3 4; do
  SC2PATH=~/StarCraftII uv run python scripts/selfplay.py --p1 v0 --p2 v0 --games 5 --map Simple64 \
    --results-path data/selfplay_results.parallel-$i.jsonl &
done
wait
# Monitor in another shell: ps -aux | grep SC2_x64 | awk '{sum+=$6} END {print sum/1024 " MB"}'
```

What to look for: 8 SC2 processes coexist (4 games × 2 each), per-process RAM ≈ 600 MB ± 200 MB (Linux unlock is real), no port collisions, all 20 games (4×5) complete. If per-process RAM ≈ 1.5 GB the unlock shrinks to 1.5x — still useful but reassess Step 11 soak parallelism.

### Step 5: SC2PATH default fix — platform-aware fallback

- **Problem:** Create `src/orchestrator/paths.py` exposing `resolve_sc2_path() -> Path` that honors `os.getenv("SC2PATH")` first then falls back to a platform-specific default (Linux: `~/StarCraftII`; Windows: `C:\Program Files (x86)\StarCraft II`; WSL2: `/mnt/c/Program Files (x86)/StarCraft II`; unknown: raises `RuntimeError` with a "set SC2PATH" message). Replace the 6 hardcoded Windows callsites with imports of this resolver: `src/orchestrator/selfplay.py:657`, `scripts/spike_subprocess_selfplay.py:33`, `scripts/evolve.py:284-286`, and `bots/{v0,v1,v2}/config.py:49`. If Spike 3 surfaced SQLite contention, also add `PRAGMA journal_mode=WAL` to the DB-open path in `bots/v0/learning/database.py` (one-line follow-up; idempotent on Windows where existing serial runs continue working).
- **Issue:** #
- **Flags:** --reviewers code
- **Produces:** 1 new module (`src/orchestrator/paths.py`) + 6 callsite updates + `tests/test_sc2path_fallback.py` (new). Optional: `bots/v0/learning/database.py` WAL-mode line if Spike 3 found contention. No behavior change on Windows (env var honored first; default unchanged).
- **Done when:** `uv run pytest tests/test_sc2path_fallback.py` green; `uv run pytest` green on Windows host; `uv run pytest` green in WSL Ubuntu; `uv run mypy src bots --strict` green; grep confirms zero remaining `r"C:\\Program Files"` literals in the 6 callsites.
- **Depends on:** 4

### Step 6: Linux dev-deps install + uv sync verification

- **Problem:** Verify `uv sync` produces a working dev environment in WSL Ubuntu 22.04, including pytest/mypy/ruff. May need `uv pip install` follow-up per memory `feedback_worktree_venv_incomplete.md`. Document the recipe.
- **Issue:** #
- **Flags:** --reviewers code
- **Produces:** `documentation/wiki/linux-dev-environment.md` (new) covering venv recipe + dev-deps install + `SC2PATH=~/StarCraftII` env var convention
- **Done when:** `uv run pytest --collect-only` succeeds in WSL; `uv run mypy src bots --strict` succeeds in WSL; `uv run ruff check .` succeeds in WSL.
- **Depends on:** 5

### Step 7: SMOKE GATE — one-game pipeline on Linux, no mocks

- **Type:** operator
- **Issue:** #
- **Produces:** `documentation/soak-test-runs/phase-8-smoke-gate-<TS>.md`
- **Done when:** one real `--difficulty 1` game played end-to-end via `uv run python -m bots.v0 --role solo --map Simple64` in WSL with the SC2PATH fix applied (env var unset, fallback used); game writes a row to `bots/v0/data/training.db`; row is readable by the daemon (verify with a `sqlite3 ... 'SELECT count(*) FROM games WHERE ...'`)
- **Depends on:** 6
- **MANDATORY pre-soak gate per plan-feature skill rules.** Catches producer/consumer drift between SC2PATH fix + venv + DB writer + daemon reader that unit tests can't see.

### Step 8: Linux CI — unit tests, ruff, mypy, Docker build

- **Problem:** Add two GitHub Actions workflows: (a) `.github/workflows/linux-tests.yml` runs `pytest -m "not sc2"`, `ruff check`, `mypy --strict` on Ubuntu 22.04 + Python 3.12 with `actions/cache` keyed on `uv.lock` (cache path `~/.cache/uv`); (b) `.github/workflows/docker-build.yml` runs `docker build` on PRs touching `Dockerfile`, `.dockerignore`, `pyproject.toml`, or `uv.lock`. Both trigger on `push` and `pull_request`.
- **Issue:** #
- **Flags:** --reviewers code
- **Produces:** `.github/workflows/linux-tests.yml` (new); `.github/workflows/docker-build.yml` (new); README badge update
- **Done when:** Both workflows green on a draft PR; intentional break in cross-platform code (e.g. add `if sys.platform != "win32": raise` to a runtime path) is caught by linux-tests; intentional break in Dockerfile (e.g. typo in apt package name) is caught by docker-build; remove the intentional breaks before merging.
- **Depends on:** 7

### Step 9: Containerization — Dockerfile for headless-Linux worker

- **Problem:** Write a multi-stage Dockerfile per the spec in §5 New Components (`sc2-base` → `python-base` → `app` stages for layer-cache efficiency). Non-root `alpha4gate` user. `ENTRYPOINT ["uv", "run", "python", "-m"]` + `CMD ["bots.v0", "--role", "solo", "--map", "Simple64", "--difficulty", "1", "--decision-mode", "rules"]` so default invocation runs the smoke-gate game and operators override CMD for selfplay/evolve. Build-arg `SC2_PACKAGE_URL` defaults to the Blizzard CDN but accepts `file:///...` for offline builds. No orchestration code — just a buildable, runnable image.
- **Issue:** #
- **Flags:** --reviewers code
- **Produces:** `Dockerfile` (new); `.dockerignore` (new); `documentation/wiki/cloud-deployment.md` (new) per §5 outline
- **Done when:** (1) `docker build -t alpha4gate-worker .` succeeds; (2) `docker run --rm alpha4gate-worker` (no CMD override) completes one smoke-gate game; (3) `docker run --rm alpha4gate-worker scripts.selfplay --p1 v0 --p2 v0 --games 2 --map Simple64` completes via CMD override; (4) container runs as `alpha4gate` not root (`docker run --rm alpha4gate-worker -c "import os; print(os.geteuid())"` returns nonzero); (5) rebuild after a code-only change reuses the `python-base` and `sc2-base` cache layers (verify via `docker build` output showing `CACHED` for early stages).
- **Depends on:** 8

### Step 10: Master-plan Phase 8 section + cross-references

- **Problem:** Add Phase 8 section to `documentation/plans/alpha4gate-master-plan.md` (pointer-style, between Phase 5 and Phase B). Update Track structure (Phase 8 in Track 5 Operational), decision graph (Phase 8 as a parallel substrate branch off Phase 5), time-budget table (add Phase 8 row, recompute totals). Remove the "Phase 8 is intentionally skipped" note in Phase 9's preamble. Add plan-history entry dated 2026-04-2x explaining the slot reclamation. Update `CLAUDE.md` master-plan summary line if it mentions phase counts.
- **Issue:** #
- **Flags:** --reviewers code
- **Produces:** master-plan diff + 1-2 line `CLAUDE.md` update
- **Done when:** master plan reads coherently; Phase 8 pointer links to `phase-8-build-plan.md`; no stale "Phase 8 skipped" claims; time-budget totals recompute correctly; `documentation/wiki/index.md` updated if it lists phases.
- **Depends on:** 9

### Step 11: WAIT — 24-hour Phase 9 evolve soak on Linux

- **Type:** wait
- **Issue:** #
- **Produces:** `documentation/soak-test-runs/evolve-linux-24h-<TS>.md`; measured promotion count; per-game wall-clock vs Windows baseline; per-instance RAM under sustained load
- **Done when:** 24-hour Phase 9 evolve cycle completes on a Linux box (WSL or Dockerfile per Step 9); morning report shows promotion count, per-game wall-clock, and parallel throughput; no orphaned SC2 processes (`pgrep SC2_x64` returns empty after teardown); pre-commit hook still rejects out-of-sandbox commits during the run.
- **Depends on:** 10
- **MANDATORY long-observation step per plan-feature skill rules.** Only way to expose Linux-specific time-dependent failures (parallel startup races per burnysc2's `main.py:694-703` warning, EGL teardown bugs, signal-handler differences, replay-path quirks per `controller.py:65-78`).

**Output path convention:** match the existing Phase 9 evolve convention (`scripts/evolve.py` writes per-version snapshots under `data-snapshots/` per memory `project_evolve_redesigned.md`). Verify by reading `scripts/evolve.py --help` and the actual argparse before running — do NOT guess the flag name. The soak-record markdown lives at `documentation/soak-test-runs/evolve-linux-24h-<TS>.md` regardless.

**Flag semantics:** `--hours 24` (wall-clock budget; evolve.py's `DaemonConfig.max_runs` and pool-exhaustion guard handle early termination), `--games-per-eval 9` (matches the validated baseline soak `20260423-2052`), `--pool-size 4` (4 generated improvements per round). See `documentation/plans/phase-9-build-plan.md` Step 4 + `scripts/evolve.py --help` for full semantics.

**Hang behavior:** the existing watchdog in `bots/v0/learning/environment.py` (`SC2Env.step` 30m soft / 45m hard per memory `feedback_orchestrator_hang_blocks_everything.md`) bounds individual game wall-clock. If `evolve.py` itself hangs at the orchestrator level, kill the Python process (`pkill -f scripts/evolve.py`); `pgrep SC2_x64` should return empty within 30 seconds as child processes drain. Document any hang in the morning report.

**Operator commands (in WSL or container):**

```bash
SC2PATH=~/StarCraftII EVO_AUTO=1 uv run python scripts/evolve.py \
  --hours 24 --games-per-eval 9 --pool-size 4
# Output dir comes from scripts/evolve.py defaults — do not pass a custom --results-out
# unless the actual flag exists in argparse.
```

What to look for in the morning report: ≥ 2 promotion attempts (matches Windows baseline soak `20260423-2052`); per-game wall-clock < 60% of Windows baseline (the parallel-throughput unlock); zero `pgrep SC2_x64` processes after run; pre-commit hook log shows `[evo-auto]` commits accepted, no out-of-sandbox attempts.

**Graceful degradation:** if per-game wall-clock lands in the 60-100% range of Windows baseline (the unlock is real but smaller than the 3x estimate), accept the partial unlock and **skip Step 12** — cloud is not justified at < 2x throughput improvement. Ship Phase 8 with the SC2PATH fix + CI + Dockerfile as the deliverable; the dev-loop and CI improvements are independently valuable. Document the degradation in the master-plan plan-history entry.

### Step 12 (conditional): Cloud cost dry-run (Spike 4 from investigation)

- **Type:** conditional
- **Issue:** #
- **Produces:** cost-per-promotion benchmark; `documentation/soak-test-runs/evolve-cloud-c5-2xlarge-<TS>.md`
- **Done when:** 24-hour cloud soak on `c5.2xlarge` spot instance completes via the Dockerfile from Step 9; cost ($spot × hours) and promotion count tallied; recommendation appended to master plan ("cloud-soak primary" vs "stay dev-box primary").
- **Depends on:** 11
- **Conditional predicate:** **only run** if Step 11 shows ≥ 2x throughput improvement vs Windows baseline. Otherwise the cloud unlock isn't worth the operator overhead — defer to a future plan triggered by separate motivation (e.g. parallel evolve at scale).

**Credentials:** use `aws configure` on the host issuing the spot-instance request to inject credentials into the AWS CLI's local config (`~/.aws/credentials`). Do NOT bake credentials into the Docker image or environment variables. Do NOT commit credentials to the repo. The instance itself runs the prebuilt worker image (built locally per Step 9), no AWS API calls happen from inside the container.

---

## 8. Risks and open questions

| Item | Risk | Mitigation |
|---|---|---|
| 4.10 Linux package incompatible with burnysc2 v7.1.3 + Simple64 | Phase dies; biggest risk per investigation §9.1 | Spike 1 settles in 2-4 hours. If dead, defer phase to "wait for newer Linux package" with master-plan note. |
| Per-instance RAM saving smaller than estimated (~1.5 GB instead of ~600 MB) | Throughput unlock shrinks 3x → 1.5x; cloud cost-effectiveness questionable | Spike 3 measures actual numbers. If shrinks, drop Step 12 (cloud dry run); ship Steps 5-10 + Step 11 as a partial Phase 8 (still useful for CI + dev-loop hygiene). |
| burnysc2 Linux serialization (50s/instance startup) bottlenecks parallel scaling | Spike 3 looks bad; the 4-way unlock is theoretical not practical | Spike 3 is the definitive test. Mitigation: long-lived SC2 pool with serial startup + parallel game-play (architecture change, not in Phase 8 scope; would be its own future plan). |
| Map version mismatch — Simple64 from current Blizzard CDN doesn't load on 4.10 client | Training-data parity broken; Linux is its own siloed env | Spike 1 settles. If broken: either source a 4.10-era Simple64 or accept Linux trains on a forked map set (master-plan note required). |
| Linux replay-path quirk (burnysc2 §4.7) breaks replay-saving in self-play | Replays unusable in 24h soak | Verified in Spike 2; if broken, hardlink replays into `~/Documents/StarCraft II/Replays/` or normalize paths in a post-Spike step (small follow-up, not blocking). |
| Parallel SQLite writes on Linux behave differently than Windows (lock contention, SQLITE_BUSY) | Spike 3 hits DB errors; 24h soak corrupts training.db | Spike 3 done-when checks `PRAGMA journal_mode`; if not WAL, Step 5 adds `PRAGMA journal_mode=WAL` to the DB-open path in `bots/v0/learning/database.py` (idempotent on Windows). |
| Blizzard CDN takes the 4.10 zip down between builds | `docker build` fails on every fresh host; cloud workers can't be (re)deployed | Step 9 Dockerfile accepts `--build-arg SC2_PACKAGE_URL=file:///...` for offline builds; operators archive the zip on first successful build. Documented in `cloud-deployment.md`. |
| Dockerfile changes regress without anyone noticing | Production deploy fails silently weeks later | Step 8 adds `docker-build.yml` workflow that runs `docker build` on PRs touching `Dockerfile`/`pyproject.toml`/`uv.lock`. |
| Container running as root in cloud | Hardening gap if image is ever exposed | Step 9 Dockerfile creates non-root `alpha4gate` user; verified in Step 9 done-when. |
| `pre-commit` hook misbehaves on Linux (line endings, shebang) | Sandbox enforcement bypassed during 24h soak | Step 11 explicitly verifies pre-commit hook still rejects out-of-sandbox commits during the run; if broken, fix in a Step 11.1 hot-patch before declaring soak success. |
| GitHub Actions Linux CI pip cache thrashing makes runs slow | CI runs > 10 min, devs ignore | Use `uv sync --frozen` + GitHub `actions/cache` keyed on `uv.lock`; benchmark on first 10 runs. |
| Dockerfile redistribution license issue (SC2 ToS) | Image cannot be pushed to public registry | Document in `cloud-deployment.md`: image is local-build only; no registry pushes. Build instructions only ship; binaries don't. |
| Windows devs can't run the Linux test suite locally | Cross-platform regressions slip past local testing | WSL2 is the dev recipe (Step 6). Docs make this clear. CI catches anything missed. |
| Phase 9 evolve soak on Linux behaves differently than on Windows due to subtle PRNG / threading divergence | Promotion outcomes don't transfer | Step 11 records per-game wall-clock + promotion count; if outcomes diverge wildly from Windows baseline, file a follow-up issue but don't block phase ship — the throughput unlock is independent of behavioral parity. |
| **Open Q1 (resolved):** CI ambition | — | Decided: Linux unit tests only; `pytest -m sc2` deferred. |
| **Open Q2 (resolved):** Cloud scaffolding scope | — | Decided: Dockerfile only; no AWS task def. |
| **Open Q3 (resolved):** Filename | — | Decided: `phase-8-build-plan.md` (matches convention). |

---

## 9. Testing strategy

**Unit tests:**

- `tests/test_sc2path_fallback.py` (new): platform-conditional tests for the new resolver — Linux branch returns `~/StarCraftII`, Windows returns Program Files path, WSL2 returns `/mnt/c/...`, unknown raises with helpful message. Uses `monkeypatch` on `platform.system()`. Doesn't need actual SC2 install.
- `tests/test_sandbox_hook.py` (existing, possibly extend): no changes expected unless the SC2PATH fallback touches `scripts/check_sandbox.py`'s deny set, which it should not.
- All existing 1313 tests must pass on both Windows and WSL Ubuntu (Step 5 done-when).

**Integration tests:**

- `pytest -m sc2` on Windows: must still pass after Step 5 (no behavior change on Windows).
- `pytest -m sc2` on Linux: best-effort — out of Phase 8 scope to gate on, but if it works in Spike 2 / Step 7, document it as a stretch.

**Smoke gate (Step 7):** one real `bots.v0.runner` game on Linux, end-to-end, no mocks. The deliverable is "the pipeline can complete one real cycle without crashing" — pass/fail of any business logic is out of scope. Per plan-feature skill rule.

**Long observation (Step 11):** 24-hour Phase 9 evolve on Linux. Proves the entire autonomous training loop survives sustained Linux operation. Per plan-feature skill rule for autonomous-behavior infrastructure.

**CI gating (Step 8):** the new Linux workflow becomes a required check on PRs. Catches future Windows-only regressions in cross-platform code paths.

**Verification of negative claims:**

- "No `bots/v0/learning/*.py` references `win32`" — Step 5 ensures this remains true (regression check via grep in Step 8 CI workflow).
- "Pygame is fully isolated under `[viewer]` extra" — verified by `uv sync` (no `[viewer]` extra) succeeding cleanly in Step 6.
- "5 SC2PATH defaults are the entire migration surface" — verified by Spike 2 and Step 7 (one game plays end-to-end with only the SC2PATH fix).

---

## 10. Master-plan integration (Step 10 detail)

Phase 8 inserts into `documentation/plans/alpha4gate-master-plan.md` between Phase 5 and Phase B. Specifically:

- **Track 5 (Operational)** gains Phase 8 ahead of Phase 9. Updated reading: `Track 5 — Operational [9, 8, 6, 7]` becomes `[8, 9, 6, 7]` (8 first because it's substrate, 9 was already substrate — both before capability work).
- **Decision graph** gains a Phase 8 box between Phase 5 and Phase 9, marked "(substrate, optional but throughput-multiplying)". Phase 9 stays unblocked without Phase 8 (it already runs on Windows); Phase 8 is a throughput multiplier for any subsequent operational phase.
- **Phase 9 preamble** drops the "There is no Phase 8 — that slot is intentionally skipped" sentence; replaces it with "Phase 8 (headless Linux training infrastructure) precedes this phase as an optional throughput substrate."
- **Time-budget table** adds Phase 8 row: optimistic `2 d spike-and-prove`, realistic `8-15 d full build`, pessimistic `3 w (Spike 1 fails, defer)`.
- **What's NOT in this plan** — remove any line about "Linux training" or "Docker / containerized training" if present (the latter is currently listed at line ~994 as out-of-scope per "Bare Windows per root CLAUDE.md"); update to "containerized cloud training infra (Phase 8)."
- **Plan history** appends a new dated entry explaining the slot reclamation and the spike-first structure motivation.
- **Track structure code-block** updated to reflect Phase 8 in Track 5 Operational alongside Phase 9.

The diff produced in Step 10 is the canonical master-plan integration — all of these touch points must be addressed in one PR for consistency.

---

## 11. References

- Investigation: [`documentation/investigations/headless-linux-training-investigation.md`](../investigations/headless-linux-training-investigation.md) — primary input; spike sequence, Windows-assumption audit, scope estimate.
- Trigger context: [`documentation/investigations/observer-restriction-workarounds-investigation.md`](../investigations/observer-restriction-workarounds-investigation.md) — why headless Linux became a priority after observer-spike failure.
- Trigger context: [`documentation/soak-2026-04-24-observer-spike.md`](../soak-2026-04-24-observer-spike.md) — full spike findings that triggered the strategic pivot.
- Master plan: [`documentation/plans/alpha4gate-master-plan.md`](alpha4gate-master-plan.md) — Phase 8 slot, decision graph, time budget.
- Phase 9 (evolve) build doc: [`documentation/plans/phase-9-build-plan.md`](phase-9-build-plan.md) — the operational phase Phase 8 most directly accelerates.
- burnysc2 v7.1.3 Linux paths: `.venv/lib/site-packages/sc2/paths.py`, `main.py`, `proxy.py`, `sc2process.py`, `controller.py` — primary-source citations in investigation §4.
- Memory: `feedback_spike_before_build.md` — controlling rule for spike-first structure.
- Memory: `feedback_py312_venv_recipe_for_soaks.md` — Py3.12 venv recipe (informs Step 6 dev-environment doc).
- Memory: `project_evolve_2gate_validated.md` — Phase 9 baseline soak that Phase 8 will compare against in Step 11.
