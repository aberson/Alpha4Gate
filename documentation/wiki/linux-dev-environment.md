# Linux Dev Environment (WSL Ubuntu 22.04)

How to set up a working Alpha4Gate dev environment on WSL2 Ubuntu 22.04. This is the platform validated by Phase 8 Spikes 1–3 and used by the Phase 8 headless training infrastructure.

> **At a glance:** Repo lives on `/mnt/c/Users/abero/dev/Alpha4Gate` (shared with the Windows host). The Python venv MUST live on ext4 (`~/venv-alpha4gate-linux`) — `uv sync` crashes on `/mnt/c`. Three env vars in `~/.profile` (not `~/.bashrc`): `UV_PROJECT_ENVIRONMENT`, `SC2PATH`, `SC2_WSL_DETECT=0`. Install dev tools with `uv sync --extra dev`. Verify with `pytest --collect-only`, `mypy --strict`, `ruff check`.

## Purpose & Design

Phase 8 of the master plan moves training off Windows onto headless Linux for the per-instance memory savings (1.7×–2.9× vs Windows; see [phase 8 build plan](../plans/phase-8-build-plan.md)). The repo stays cross-platform — Windows and Linux developers share `/mnt/c/Users/abero/dev/Alpha4Gate` — but the runtime env (venv + SC2 install + env vars) is Linux-native.

The "shared repo, per-OS venv" split exists because:

- The repo on `/mnt/c/...` is fine for source code (cross-platform).
- `uv sync` performs atomic-move + chmod operations that NTFS-via-DrvFS rejects with `Operation not permitted`. The venv must be on ext4.
- SC2 itself ships separate Linux and Windows builds. The Linux build (`~/StarCraftII`, version 4.10) and the Windows build (`C:\Program Files (x86)\StarCraft II`, current) coexist; `SC2PATH` selects which one each shell uses, and the [SC2PATH resolver](../../src/orchestrator/paths.py) honors it at runtime.

## Quickstart

Run these as a one-time setup from a fresh `wsl -d Ubuntu-22.04` shell. The whole thing takes ~2 minutes after Python is installed (the venv is ~4.8 GB; SC2 install adds ~8.6 GB; see [Spike 1 measurements](../soak-test-runs/spike-1-hello-world-linux-sc2-20260425-2218.md)).

### 1. Confirm distro

```bash
cat /etc/os-release | grep VERSION_ID
# VERSION_ID="22.04"
```

> **Two WSL distros coexist** on this host: `Ubuntu` (24.04, default) and `Ubuntu-22.04` (Phase 8 platform). Always launch with `wsl -d Ubuntu-22.04`; bare `wsl` opens 24.04.

### 2. Install Python 3.12

The `pyproject.toml` requires Python 3.12. Ubuntu 22.04 ships 3.10, so use the deadsnakes PPA:

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
```

### 3. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.cargo/env  # or open a new shell
uv --version  # confirm 0.11+ (Spike 1 used 0.11.7)
```

### 4. Set env vars in `~/.profile`

```bash
cat >> ~/.profile <<'EOF'
export UV_PROJECT_ENVIRONMENT=$HOME/venv-alpha4gate-linux
export SC2PATH=$HOME/StarCraftII
export SC2_WSL_DETECT=0
EOF
```

Reload (or open a fresh `wsl` shell — these only need to fire on login):

```bash
source ~/.profile
printenv UV_PROJECT_ENVIRONMENT SC2PATH SC2_WSL_DETECT
```

> **Why `~/.profile`, not `~/.bashrc`:** Ubuntu's stock `.bashrc` returns early for non-interactive shells (`case $- in *i*) ;; *) return;; esac`), so vars exported there are invisible to `wsl -d ... -- bash -lc '...'` calls. `~/.profile` runs for every login shell. (See [`feedback_wsl_bashrc_interactive_guard.md`](../../C:/Users/abero/.claude/projects/c--Users-abero-dev-Alpha4Gate/memory/feedback_wsl_bashrc_interactive_guard.md) for the original incident.)
>
> **Why `SC2_WSL_DETECT=0`:** burnysc2's `sc2.paths.platform_detect` calls `wsl.detect()` from a Linux Python interpreter, sees `/proc/version` contains "microsoft", and silently flips to WSL2 mode — which then runs the **Windows** SC2 binary at `/mnt/c/.../Support64/SC2_x64.exe` via `powershell.exe`. We want pure-Linux mode (the binary at `~/StarCraftII/Versions/Base*/SC2_x64`). The `SC2_WSL_DETECT` env var is a documented opt-out in burnysc2's `sc2/wsl.py`.

### 5. Sync the venv with dev dependencies

```bash
cd /mnt/c/Users/abero/dev/Alpha4Gate
uv sync --extra dev
```

The `--extra dev` flag is required: `pytest`, `ruff`, `mypy`, `httpx`, and `pre-commit` live in `[project.optional-dependencies] dev` in `pyproject.toml`. A bare `uv sync` produces a runtime-only venv; you'll get `ModuleNotFoundError: pytest` when you try to run tests.

> **Why the venv MUST be on ext4:** `uv sync` performs atomic moves and `chmod` calls during package install. Both fail on `/mnt/c` (NTFS-via-DrvFS) with `Operation not permitted (os error 1)`. The repo can stay on `/mnt/c`; only the venv path needs to be Linux-native. `UV_PROJECT_ENVIRONMENT` (set in step 4) tells uv where to put it.

### 6. Install SC2 (only needed for integration tests / spikes)

For unit tests + lint + typecheck, you can skip this section. For anything that actually plays a game, install SC2 4.10 Linux per the [Spike 1 commands](../soak-test-runs/spike-1-hello-world-linux-sc2-20260425-2218.md#operator-commands-actually-executed-with-deviations-from-plan).

### 7. Configure git identity (required for evolve commits)

`scripts/evolve.py` runs `git commit` inside the WSL distro to land `[evo-auto]` promotions. WSL has its own `~/.gitconfig` separate from the Windows `%USERPROFILE%\.gitconfig`. Without identity set, every commit fails with exit 128 (`fatal: empty ident name (for <user@host.localdomain>) not allowed`) and strong fitness winners silently retry-cap into `evicted` after three benched rounds.

```bash
git config --global user.email "you@example.com"
git config --global user.name "Your Name"
git config --global --get user.email  # confirm non-empty
git config --global --get user.name   # confirm non-empty
```

> **Symptom on the Evolution dashboard:** strong fitness winners (5/9, 6/9, 7/9 wins) reappear across consecutive generations marked `r1`, `r2`, `r3`, then flip to `evicted` with no promotions. Diagnosed on the [2026-04-30 soak failure](../soak-test-runs/evolve-2026-04-30T05-30-07+00-00.md) — 7 generations, ~10 strong winners, 0 promotions, 100% `stack-apply-commit-fail` rate.

## Verification

These three commands are the Phase 8 Step 6 done-when. All must succeed against the freshly-synced WSL venv:

```bash
cd /mnt/c/Users/abero/dev/Alpha4Gate
uv run pytest --collect-only 2>&1 | tail -3   # pytest can find dev deps + collect ~1327 tests
uv run mypy src bots --strict 2>&1 | tail -3  # 178 files, no issues
uv run ruff check .                            # All checks passed
```

For a more aggressive verification (full test run + paths.py round-trip), run [`scripts/step5_wsl_verify.sh`](../../scripts/step5_wsl_verify.sh) — it's reusable, sourced from a file (per [`feedback_wsl_bash_lc_heredoc_fragile.md`](../../C:/Users/abero/.claude/projects/c--Users-abero-dev-Alpha4Gate/memory/feedback_wsl_bash_lc_heredoc_fragile.md)), and exits non-zero on any failure.

## Gotchas

A consolidated list of foot-guns documented in memory and in the Spike 1 findings. Read these before debugging anything weird.

| # | Symptom | Cause | Fix |
|---|---------|-------|-----|
| 1 | `uv sync` fails with `Operation not permitted (os error 1)` | Venv on `/mnt/c` (NTFS-via-DrvFS rejects atomic-move + chmod) | Pin `UV_PROJECT_ENVIRONMENT` to `$HOME/venv-alpha4gate-linux` (ext4) |
| 2 | `printenv VAR` empty in `wsl -d ... -- bash -lc` calls | Var exported in `~/.bashrc`, which has an interactive-only guard | Move exports to `~/.profile` |
| 3 | SC2 launches the Windows binary instead of the Linux one | burnysc2 `wsl.detect()` flips to WSL2 mode and runs `SC2_x64.exe` via `powershell.exe` | `SC2_WSL_DETECT=0` |
| 4 | `pytest`, `mypy`, `ruff` all `ModuleNotFoundError` on a fresh venv | `uv sync` (no flags) produces a runtime-only venv | `uv sync --extra dev` |
| 5 | Multi-line `wsl -- bash -lc 'heredoc'` produces syntax errors | WSL's `bash -lc` strips newlines from the inlined script | Write to a `.sh` file, then `wsl -- bash <path>` |
| 6 | `wsl -- bash <unquoted /mnt/c/...>` resolves to `C:/Program Files/Git/mnt/c/...` | MSYS2 path-mangling rewrites unquoted Unix paths | Quote inside `bash -lc '...'` |
| 7 | Bare `wsl` opens the wrong distro | Default distro is Ubuntu 24.04, not Ubuntu-22.04 | Always pass `-d Ubuntu-22.04` |
| 8 | `pidof SC2_x64` returns empty inside a backgrounded subshell | Cause not isolated; observed 305 consecutive empty results during Spike 1 RSS sampling | Scan `/proc/[0-9]*/exe` directly for SC2 detection |
| 9 | `ps -p A B C -o rss=` (space-separated PIDs) returns empty on procps-ng | procps-ng quirk | Use comma-separated (`ps -p A,B,C`) or per-PID `/proc/$pid/status` |
| 10 | Every evolve generation logs `stack-apply-commit-fail` despite passing fitness winners; dashboard shows benched winners flipping to `evicted` at `r3` | WSL `~/.gitconfig` has empty `user.email` / `user.name`; `git commit` returns 128 (`Author identity unknown`) | Set `git config --global user.email`/`user.name` inside the distro (step 7) |

## What this DOES NOT cover

- **Native Linux (non-WSL).** The `SC2PATH` resolver supports a native-Linux fallback (`~/StarCraftII`), and `SC2_WSL_DETECT=0` is harmless on real Linux — but the path conventions and Python 3.12 source above are WSL-Ubuntu-22.04 specific.
- **Docker.** Phase 8 Step 9+ adds a Dockerfile; until then, the host venv is the only supported runtime.
- **macOS / arm64.** burnysc2's SC2 launcher is x86_64 + Windows/Linux only.

## References

- [Phase 8 build plan, Step 6](../plans/phase-8-build-plan.md) — the spec this page satisfies
- [Spike 1 soak run](../soak-test-runs/spike-1-hello-world-linux-sc2-20260425-2218.md) — first end-to-end Linux SC2 game; the source of every gotcha in the table above
- [Headless Linux training investigation](../investigations/headless-linux-training-investigation.md) — why Phase 8 exists at all
- [`scripts/step5_wsl_verify.sh`](../../scripts/step5_wsl_verify.sh) — reusable verify harness
- [`src/orchestrator/paths.py`](../../src/orchestrator/paths.py) — the `SC2PATH` resolver these env vars feed into
