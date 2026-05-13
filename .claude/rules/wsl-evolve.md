---
description: WSL2 + Linux-SC2 substrate setup for evolve soaks. Eight layered gotchas each cost a session.
paths:
  - "scripts/evolve*.py"
  - "scripts/evolve*.sh"
  - "scripts/start-dev.sh"
  - "scripts/live-test.sh"
---

# Linux/WSL evolve substrate

Alpha4Gate runs evolve on Windows-SC2 (canonical) or Linux-SC2 in WSL (Phase 8+). The Linux path has eight layered setup gotchas — each one breaks evolve in a different way, and applying only a subset gives partial-success symptoms that are harder to diagnose than full failures.

## The eight required configs

1. **Always `wsl -d Ubuntu-22.04`**, never bare `wsl`. The default `Ubuntu` distro is empty and silently fails with `uv: command not found`.

2. **Env vars belong in `~/.profile`**, not `~/.bashrc`. Ubuntu's bashrc returns early for non-interactive shells. Set:
   ```bash
   export SC2PATH=$HOME/StarCraftII
   export SC2_WSL_DETECT=0
   export UV_PROJECT_ENVIRONMENT=$HOME/venv-alpha4gate-linux
   ```
   Verify with `printenv VAR`, never `echo $VAR` (outer shell expands first; you'd be reading the wrong shell's env).

3. **Venv must live on ext4** (`~/venv-alpha4gate-linux`), never on `/mnt/c/...`. `uv sync` crashes on DrvFS chmod. The repo can stay on `/mnt/c`.

4. **burnysc2 needs `SC2_WSL_DETECT=0`** or it auto-detects WSL2 and runs the Windows SC2 binary — silent foundation rot where the bot trains against the wrong client.

5. **Set git identity inside the distro**:
   ```bash
   git config --global user.email "<your@email>"
   git config --global user.name  "<your name>"
   ```
   Without it every evolve commit fails with exit 128 and benched winners get evicted.

6. **Git config for the cross-FS repo:**
   ```bash
   git config --global safe.directory /mnt/c/Users/abero/dev/Alpha4Gate
   git config --global core.autocrlf input
   ```

7. **Mask systemd-binfmt:**
   ```bash
   sudo systemctl mask systemd-binfmt.service
   wsl --shutdown
   ```
   Otherwise the WSLInterop race kills launched `.exe` processes.

8. **`chmod -R a+rX ~/StarCraftII`** after unzipping. The Blizzard zip preserves 700 perms on map subdirs, blocking non-root reads.

## Launch recipes

**Windows-SC2 (canonical):** PowerShell + `Start-Process` driving `C:\Users\abero\.local\bin\uv.exe`. Simplest path, no interop layer.

**Linux-SC2 (Phase 8 Step 11+):** Open an **interactive** shell with `wsl -d Ubuntu-22.04`, then run `nohup uv run python scripts/evolve.py ... &` from the prompt, then `exit`. The PowerShell one-liner `wsl -d Ubuntu-22.04 bash -lc 'nohup ... &'` silently fails to background — bash teardown kills the orphaned child before nohup completes its session-detach.

## WSL → script execution

Multi-line bash through `wsl -- bash -lc '<script>'` loses newlines in heredocs; write the script to a file and invoke as `wsl -d Ubuntu-22.04 -- bash -lc 'bash /mnt/c/path/to/script.sh'`. Quote `/mnt/c/...` arguments inside `bash -lc '...'` from Git Bash on Windows or MSYS2 rewrites them to `C:/Program Files/Git/mnt/c/...` before WSL sees them.

## Source memories

`feedback_wsl_distro_ubuntu_22_04_specific`, `feedback_wsl_bashrc_interactive_guard`, `feedback_wsl_bash_lc_background_fails`, `feedback_wsl_bash_lc_heredoc_fragile`, `feedback_wsl_git_identity_blocks_evolve`, `feedback_uv_venv_must_be_on_ext4`, `feedback_burnysc2_wsl_autodetect_overrides_linux`, `feedback_evolve_drvfs_copy2_fails`, `feedback_sc2_zip_perms_block_non_root`, `feedback_msys2_unquoted_path_to_wsl`.
