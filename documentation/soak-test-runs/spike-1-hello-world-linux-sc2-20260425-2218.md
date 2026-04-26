# Spike 1 — Hello-World Linux SC2 (Phase 8 Step 2)

**Status:** PASSED
**Date:** 2026-04-25 22:18 → 22:42 local (~24 min wall-clock)
**Timebox:** 4 hours; used 24 min (~10%)
**Operator:** Claude Code session (Opus 4.7) under user supervision
**Plan:** [phase-8-build-plan.md §7 Step 2](../plans/phase-8-build-plan.md)
**Halt condition:** SC2 binary fails to launch on libc/libstdc++ mismatch → defer phase. **Did not trigger.**

---

## Summary

The 2018-era SC2 4.10 Linux package (Base 75689) launches cleanly on Ubuntu 22.04 (WSL2, glibc 2.35, libstdc++ 6.0.30). Alpha4Gate's `bots.v0` runner — unmodified except for env-var overrides — completed two consecutive Bot-vs-VeryEasy games on Simple64 with `Result.Victory`. Per-game wall-clock 52–67 seconds. The dominant unknown from investigation §9.1 (4.10-version-mismatch with our Simple64 map) is settled: **compatible.** Phase 8 is GO for Step 3 (Spike 2 — self-play unmodified on Linux).

## Done-when checklist (per plan §7 Step 2)

- [x] `bots.v0.runner` completes one `--difficulty 1` game on Simple64 in WSL with `SC2PATH=~/StarCraftII`
- [x] Game completes with a Result (Victory in both attempts)
- [x] Replay saved by burnysc2
- [x] Peak `SC2_x64` RSS recorded (see Measurements below)
- [x] No libc/libstdc++ "not found" libs from `ldd` on the binary

## Measurements

| Metric | Value |
|---|---|
| Game 1 wall-clock (launch → end) | 52 s |
| Game 2 wall-clock (RSS-sampled run) | 67 s |
| In-game time at end | 8:32 (game 1), ~9 min (game 2) |
| Effective speed factor | ~9-10x (in-game / wall-clock) |
| Game results | 2/2 Victory at difficulty 1 |
| SC2 status timeline (game 1) | launched → init_game → in_game → ended (≈3 s startup, ≈42 s play) |
| `SC2_x64` resident RSS at +8s into a game | **882,956 kB ≈ 862 MB** (single mid-game sample; full sweep deferred to Spike 3) |
| Plan-doc RSS estimate (per investigation §4) | 600 MB ± 200 MB |
| Implied Windows-vs-Linux unlock | 1.7×–2.9× per-instance memory savings (Windows baseline 1.5–2.5 GB per plan) |
| `SC2.4.10.zip` download size | 4.1 GB (plan estimated ~2 GB; reality is 2x) |
| `~/StarCraftII/` extracted size | 8.6 GB |
| `~/venv-alpha4gate-linux/` size | 4.8 GB |
| Total disk used by spike | ~17.5 GB (zip + install + venv) |

## Environment

- Host: Windows 11, WSL2 Ubuntu 22.04 (`VERSION_ID="22.04"`)
- Kernel: WSL2 default (microsoft-standard)
- glibc: 2.35-0ubuntu3.13
- libstdc++: 6.0.30
- Python: 3.12.13 (deadsnakes PPA)
- uv: 0.11.7
- burnysc2: 7.1.3 (from `pyproject.toml`)
- SC2 build: `Base75689` (game version 4.10, August 2019)
- Repo state: `master` @ `7493bbd` + uncommitted plan-doc + `.gitignore` patches

## Operator commands actually executed (with deviations from plan)

```bash
# Pre-flight (~/.profile, NOT ~/.bashrc — see Findings #4):
echo 'export UV_PROJECT_ENVIRONMENT=$HOME/venv-alpha4gate-linux' >> ~/.profile
echo 'export SC2PATH=$HOME/StarCraftII' >> ~/.profile
echo 'export SC2_WSL_DETECT=0' >> ~/.profile

# SC2 install:
mkdir -p ~/StarCraftII && cd ~/StarCraftII
wget https://blzdistsc2-a.akamaihd.net/Linux/SC2.4.10.zip            # 4.1 GB, 30 s
unzip -q -P iagreetotheeula SC2.4.10.zip                              # extracts to ~/StarCraftII/StarCraftII/
shopt -s dotglob && mv StarCraftII/* . && rmdir StarCraftII && shopt -u dotglob   # flatten
cp ~/StarCraftII/Maps/Melee/Simple64.SC2Map ~/StarCraftII/Maps/       # bundled in 4.10 zip; copy to flat Maps/
ln -s ~/StarCraftII/Maps ~/StarCraftII/maps                           # SC2 looks up lowercase `maps/`

# Python env:
cd /mnt/c/Users/abero/dev/Alpha4Gate
uv sync                                                               # 1m 41s, 4.8 GB

# Hello-world game:
uv run python -m bots.v0 --role solo --map Simple64 --difficulty 1 \
  --decision-mode rules --no-claude --no-reward-log --game-time-limit 600
```

## Findings (real deviations from the original plan, all patched into phase-8-build-plan.md)

1. **SC2 4.10 zip is ~4.1 GB, not ~2 GB.** Plan-doc estimate was off by 2x. Same wall-clock impact (CDN throughput is the bottleneck, not size in this range).
2. **Zip extracts into a doubled `~/StarCraftII/StarCraftII/` path.** The 2018-era package puts everything inside its own top-level dir. Need to flatten before `$SC2PATH/Versions/...` resolves.
3. **Simple64.SC2Map is BUNDLED in the 4.10 Linux zip** at `Maps/Melee/Simple64.SC2Map`. The plan-doc instruction to copy from the Windows install (`/mnt/c/Program Files (x86)/StarCraft II/Maps/`) is moot — and would also fail because the Windows install's `Maps/` dir does not contain `Simple64.SC2Map` directly anyway (Windows resolves it from a different path). Use the bundled one; copy to flat `Maps/` to match the standard layout.
4. **Ubuntu's stock `~/.bashrc` has an interactive-only guard** (`case $- in *i*) ;; *) return;; esac`). Vars exported at the END of `.bashrc` never fire for `wsl -d ... -- bash -lc '...'` calls. Use `~/.profile` instead — it runs for every login shell, interactive or not. Saved to `feedback_wsl_bashrc_interactive_guard.md`.
5. **`uv sync` on `/mnt/c/...` venv path crashes with `Operation not permitted (os error 1)`.** uv's atomic-move + chmod operations during package install fail on NTFS-via-DrvFS. Pin `UV_PROJECT_ENVIRONMENT` to a Linux-native ext4 path (`~/venv-alpha4gate-linux`). Repo can stay on `/mnt/c/...`; only the venv needs to be on ext4. Saved to `feedback_uv_venv_must_be_on_ext4.md`.
6. **burnysc2 auto-detects WSL2 and switches to the Windows-binary path** (`/mnt/c/Program Files (x86)/StarCraft II/Support64`, launched via `powershell.exe`). To keep it in pure-Linux mode (using `~/StarCraftII/Versions/Base*/SC2_x64` directly), set `SC2_WSL_DETECT=0` (documented support flag in `sc2/wsl.py:73`). Saved to `feedback_burnysc2_wsl_autodetect_overrides_linux.md`.
7. **SC2 4.10 looks up maps via lowercase `maps/`** (Linux is case-sensitive). Symlink `~/StarCraftII/maps -> ~/StarCraftII/Maps` so both case variants resolve.

None of these deviations changed the spike's outcome — they just turned what should have been ~30 minutes of operator work into ~80 minutes of plan-doc-bug-finding plus operator work. All seven are now in the plan-doc Step 2 patches.

## Verification of negative claims

- `ldd ~/StarCraftII/Versions/Base75689/SC2_x64` resolved every dependency cleanly (libstdc++, libc, libdl, libpthread, librt, libm, libgcc_s, ld-linux). No "not found" lines. Confirms the libc/libstdc++ baseline halt condition is averted on Ubuntu 22.04 stock.
- `printenv UV_PROJECT_ENVIRONMENT SC2PATH SC2_WSL_DETECT` from a fresh `wsl -d Ubuntu-22.04 -- bash -lc` returns all three values. Confirms `.profile` propagation works.

## Halt-condition decision

NONE of the documented halt conditions triggered:

- ✅ Game launched (no library errors)
- ✅ Map loaded (after symlink fix)
- ✅ SC2 binary did not crash on libc/libstdc++ mismatch

Phase 8 proceeds to **Step 3 (Spike 2 — existing self-play unmodified on Linux)** in a future session.

## RSS sampling notes

Three RSS-sampling attempts via the inline `ps -o rss= -C SC2_x64` watcher pattern yielded zero samples each — `ps -C` does not match the `SC2_x64` binary on this WSL2 kernel for reasons unclear (the basename + the binary path in `/proc/<pid>/comm` look correct; `pidof SC2_x64` finds it instantly, but `ps -C SC2_x64` returns nothing). A direct `ps aux | grep SC2_x64` mid-game confirmed the binary's RSS at 882,956 kB / 862 MB ≈ 8 seconds into a game.

The proper full-sweep measurement (peak RSS over a full game; per-process averages over several games; `ps -p <pid> -o rss=` recipe instead of `ps -C`) is **deferred to Spike 3**, which already has "per-process resident RAM measured" as a done-when criterion. Spike 1's lower-bound single-sample is sufficient to confirm the Linux unlock is real and in the right direction.

```
22:45:??  PID=11176  RSS=882956 kB  CMD=/home/abero/StarCraftII/Versions/Base75689/SC2_x64 -listen 127.0.0.1 -port 37873 ...
```

## Next session

Step 3 / Spike 2: `SC2PATH=~/StarCraftII uv run python scripts/selfplay.py --p1 v0 --p2 v0 --games 2 --map Simple64`. Halt condition: 4-hour timebox; if 2-game sample shows port collisions, signal-thread errors, or mid-game crashes, stop and reassess. The `SC2_WSL_DETECT=0` and `~/venv-alpha4gate-linux` lessons from Spike 1 carry forward unchanged.

## Memory entries created/updated in this spike

- `feedback_wsl_bashrc_interactive_guard.md` (new)
- `feedback_uv_venv_must_be_on_ext4.md` (new)
- `feedback_burnysc2_wsl_autodetect_overrides_linux.md` (new)
- `project_headless_linux_training_opportunity.md` (rewritten — Spike 1 PASSED, Spike 2 next)
- MEMORY.md index updated
