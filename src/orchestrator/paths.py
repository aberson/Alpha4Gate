"""Platform-aware SC2 install-path resolver.

Phase 8 introduced headless Linux training, so the previous Windows-only
``r"C:\\Program Files (x86)\\StarCraft II"`` literal scattered across six
callsites no longer suffices. :func:`resolve_sc2_path` centralises the
fallback table:

* ``$SC2PATH`` — honored first, regardless of platform. Always wins.
* Windows (``sys.platform == "win32"``) → ``C:\\Program Files (x86)\\StarCraft II``.
* WSL2 (Linux kernel with ``microsoft`` in ``/proc/version``):
    * If ``SC2_WSL_DETECT=0`` (the Phase 8 pure-Linux opt-in) →
      ``~/StarCraftII`` (matches the WSL Ubuntu-22.04 install convention;
      necessary because burnysc2 in pure-Linux mode launches the Linux
      ``SC2_x64`` binary, which only exists at the native-Linux layout).
    * Otherwise → ``/mnt/c/Program Files (x86)/StarCraft II`` (the Windows
      install reachable through DrvFS — convenient for ad-hoc WSL
      invocations without a Linux SC2 install, paired with burnysc2's
      auto-detected WSL2 mode that runs ``SC2_x64.exe`` via PowerShell).
* Linux native → ``~/StarCraftII`` (Blizzard's documented Linux layout;
  matches the Phase 8 WSL Ubuntu-22.04 install convention).
* Anything else (macOS, BSDs) → :class:`RuntimeError`.

The function does **not** verify the directory exists; callers that need
that guarantee (config.py, evolve.py) keep their own ``is_dir`` checks
so error messages stay meaningful.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform != "win32":
    raise RuntimeError("intentional CI validation break — DO NOT MERGE")

__all__ = ["resolve_sc2_path"]


_WINDOWS_DEFAULT = Path(r"C:\Program Files (x86)\StarCraft II")
_WSL_DEFAULT = Path("/mnt/c/Program Files (x86)/StarCraft II")


def _is_wsl() -> bool:
    """Return True iff running on a WSL (Linux-on-Windows) kernel.

    Both WSL1 and WSL2 surface ``microsoft`` in ``/proc/version`` (case
    varies between distros). Native Linux does not. Exposed as a
    module-level function so tests can monkeypatch it.
    """
    proc_version = Path("/proc/version")
    if not proc_version.exists():
        return False
    try:
        return "microsoft" in proc_version.read_text().lower()
    except OSError:
        return False


def resolve_sc2_path() -> Path:
    """Return the StarCraft II install directory for the current platform.

    Honors ``$SC2PATH`` first; otherwise falls back to a platform default.
    Raises :class:`RuntimeError` on unsupported platforms (e.g. macOS).
    """
    env_value = os.environ.get("SC2PATH")
    if env_value:
        return Path(env_value)

    if sys.platform == "win32":
        return _WINDOWS_DEFAULT
    if sys.platform == "linux":
        if _is_wsl():
            if os.environ.get("SC2_WSL_DETECT") == "0":
                return Path.home() / "StarCraftII"
            return _WSL_DEFAULT
        return Path.home() / "StarCraftII"

    msg = (
        f"Cannot resolve SC2 install path on platform {sys.platform!r}. "
        "Set SC2PATH to the StarCraft II install directory."
    )
    raise RuntimeError(msg)
