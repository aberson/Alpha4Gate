"""System info helpers for the Processes / Live dashboard panels.

Surfaces three pieces of WSL-aware substrate state the dashboard didn't
expose before the 2026-04-28 WSL migration:

1. ``get_substrate_info()`` — host platform + WSL distro detection so the
   Live tab can show a "Substrate: WSL2 / Ubuntu-22.04" badge alongside
   the connection indicator.
2. ``get_wsl_processes()`` — list of SC2_x64 / python processes inside
   the WSL VM so the Processes tab no longer undercounts when evolve runs
   on Linux. The Windows-side ``Get-Process SC2_x64`` enumeration cannot
   see processes inside the WSL2 VM.
3. ``get_resources()`` — Windows host RAM/disk + WSL VM RAM gauge.  The
   2026-04-28 evolve substrate-migration session lost ~1h to a 0.3 GB
   Windows host RAM starvation that the dashboard didn't surface; this
   panel makes that condition visible before it causes timeouts.

All shell-outs to ``wsl --`` are wrapped with a hard 3 s timeout and
catch every exception so a transient WSL hiccup never propagates an HTTP
500.  Results are cached with a TTL (3 s for high-churn data, 30 s for
substrate/distro discovery) so the dashboard's poll cadence does not
flood the WSL bridge.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import psutil  # type: ignore[import-untyped]

_WSL_DISTRO_DEFAULT = "Ubuntu-22.04"
_WSL_TIMEOUT_SECS = 3.0
_SHORT_TTL = 3.0
_LONG_TTL = 30.0


@dataclass
class _CacheEntry:
    """Tiny TTL cache entry — value + monotonic deadline."""

    value: Any
    expires_at: float


_cache: dict[str, _CacheEntry] = {}


def _cached[T](key: str, ttl: float, fn: Callable[[], T]) -> T:
    """Return cached value for ``key`` or refresh via ``fn`` if stale."""
    now = time.monotonic()
    entry = _cache.get(key)
    if entry is not None and entry.expires_at > now:
        return cast(T, entry.value)
    value = fn()
    _cache[key] = _CacheEntry(value=value, expires_at=now + ttl)
    return value


def _wsl_available() -> bool:
    """Return True iff the ``wsl`` binary is on PATH."""
    return shutil.which("wsl") is not None


def _run_wsl(cmd: list[str], timeout: float = _WSL_TIMEOUT_SECS) -> str | None:
    """Run a command inside the default WSL distro.

    Returns stdout on success or ``None`` on any failure (binary missing,
    timeout, non-zero exit, decode error).  Callers map ``None`` to an
    empty / "unavailable" payload — the dashboard must not 500 because
    WSL is briefly slow.
    """
    if not _wsl_available():
        return None
    try:
        result = subprocess.run(
            ["wsl", "-d", _WSL_DISTRO_DEFAULT, "--", *cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


# ---------------------------------------------------------------------------
# Substrate info
# ---------------------------------------------------------------------------


def get_substrate_info() -> dict[str, Any]:
    """Return a small dict describing where the backend (and SC2) runs.

    Cached 30 s — these values change essentially never within a session
    (host platform is fixed; WSL distro is configured once).
    """
    return _cached("substrate", _LONG_TTL, _build_substrate_info)


def _build_substrate_info() -> dict[str, Any]:
    backend_platform = "windows"  # bots/v3/api.py only ever runs on the host
    wsl: dict[str, Any] = {
        "available": False,
        "distro": None,
        "kernel": None,
        "sc2_path": None,
        "sc2_binary_present": False,
    }
    if _wsl_available():
        wsl["available"] = True
        wsl["distro"] = _WSL_DISTRO_DEFAULT
        kernel = _run_wsl(["uname", "-r"])
        if kernel is not None:
            wsl["kernel"] = kernel.strip()
        # ``$SC2PATH`` parameter expansion silently returns empty through
        # ``wsl -- bash -lc "echo $SC2PATH"`` even when the var IS set;
        # single-quoted awk also breaks (the wsl bridge strips the quotes).
        # ``env | grep | cut`` survives because it needs no shell quoting.
        sc2_path = _run_wsl(
            ["bash", "-lc", "env | grep ^SC2PATH= | cut -d= -f2-"]
        )
        if sc2_path and sc2_path.strip():
            sc2_path_clean = sc2_path.strip()
            wsl["sc2_path"] = sc2_path_clean
            # ``find`` survives Blizzard publishing newer Linux SC2 versions
            # (Base75689 was 4.10; future bumps would silently break a
            # hardcoded path check).
            binary = _run_wsl(
                [
                    "bash",
                    "-lc",
                    f"find {sc2_path_clean} -maxdepth 4 -name SC2_x64 "
                    "-type f -print -quit 2>/dev/null",
                ]
            )
            wsl["sc2_binary_present"] = bool(binary and binary.strip())
    return {
        "backend_platform": backend_platform,
        "wsl": wsl,
    }


# ---------------------------------------------------------------------------
# WSL processes
# ---------------------------------------------------------------------------


def get_wsl_processes() -> dict[str, Any]:
    """Return SC2 + python processes running inside the WSL VM.

    Cached 3 s.  Returns ``{"available": False, "processes": []}`` when
    WSL is unreachable so the frontend can render an "unavailable"
    sub-panel rather than throwing.
    """
    return _cached("wsl_procs", _SHORT_TTL, _build_wsl_processes)


def _build_wsl_processes() -> dict[str, Any]:
    if not _wsl_available():
        return {"available": False, "processes": []}
    # ``ps`` columns: pid, comm, etime (elapsed wall), rss (KB), args.
    # ``args`` is last so it can contain spaces.
    raw = _run_wsl(
        ["ps", "-eo", "pid,comm:20,etime,rss,args", "--no-headers"]
    )
    if raw is None:
        return {"available": False, "processes": []}
    procs: list[dict[str, Any]] = []
    for line in raw.splitlines():
        # Match SC2 binary or any python invocation (evolve / bots.v4 / claude)
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        pid, comm, etime, rss, args = parts
        comm_low = comm.lower()
        args_low = args.lower()
        is_sc2 = comm_low.startswith("sc2") or "sc2_x64" in args_low
        is_relevant_py = (
            comm_low in {"python", "python3", "python3.12"}
            and any(tag in args_low for tag in ("bots.v", "evolve", "selfplay"))
        )
        if not (is_sc2 or is_relevant_py):
            continue
        try:
            procs.append({
                "pid": int(pid),
                "comm": comm,
                "etime": etime,
                "rss_kb": int(rss),
                "label": _label_for(args),
            })
        except ValueError:
            continue
    return {"available": True, "processes": procs}


def _label_for(args: str) -> str:
    """Compress a long process command line into a short dashboard label."""
    if "SC2_x64" in args:
        return "SC2_x64"
    if "scripts/evolve.py" in args:
        return "evolve.py"
    if "scripts/selfplay.py" in args:
        return "selfplay.py"
    if "-m bots.v" in args:
        # Pull out the version: e.g. "python -m bots.v4 --role p1"
        for token in args.split():
            if token.startswith("bots.v"):
                return token
    return args[:60]


# ---------------------------------------------------------------------------
# Resources (RAM + disk)
# ---------------------------------------------------------------------------


def get_resources() -> dict[str, Any]:
    """Return Windows host + WSL VM RAM and ``/mnt/c`` disk-free.

    Cached 3 s.  Each section has its own ``available`` flag so the UI can
    render partial data when one substrate is unreachable.
    """
    return _cached("resources", _SHORT_TTL, _build_resources)


def _build_resources() -> dict[str, Any]:
    # --- Windows host -----------------------------------------------------
    vm = psutil.virtual_memory()
    host: dict[str, Any] = {
        "available": True,
        "ram_total_gb": round(vm.total / (1024**3), 2),
        "ram_used_gb": round(vm.used / (1024**3), 2),
        "ram_free_gb": round(vm.available / (1024**3), 2),
        "ram_pct_used": round(vm.percent, 1),
    }
    # Disk on the drive the repo lives on.
    try:
        repo_root = Path(__file__).resolve().parents[2]
        disk = psutil.disk_usage(str(repo_root))
        host["disk_total_gb"] = round(disk.total / (1024**3), 2)
        host["disk_free_gb"] = round(disk.free / (1024**3), 2)
        host["disk_pct_used"] = round(disk.percent, 1)
    except OSError:
        host["disk_total_gb"] = None
        host["disk_free_gb"] = None
        host["disk_pct_used"] = None

    # --- WSL VM -----------------------------------------------------------
    wsl = _wsl_resources()

    return {"host": host, "wsl": wsl}


def _wsl_resources() -> dict[str, Any]:
    skeleton = {
        "available": False,
        "ram_total_gb": None,
        "ram_used_gb": None,
        "ram_free_gb": None,
        "ram_pct_used": None,
        "swap_used_gb": None,
        "swap_total_gb": None,
        "load_avg_5m": None,
    }
    if not _wsl_available():
        return skeleton
    # ``free`` outputs in KB; parse Mem: + Swap: rows.  ``--`` is essential
    # so wsl.exe doesn't try to interpret -b as a wsl flag.
    raw = _run_wsl(["free", "-b"])
    if raw is None:
        return skeleton
    mem_total = mem_used = mem_free = swap_used = swap_total = None
    for line in raw.splitlines():
        if line.startswith("Mem:"):
            cols = line.split()
            if len(cols) >= 4:
                mem_total = int(cols[1])
                mem_used = int(cols[2])
                mem_free = int(cols[6]) if len(cols) >= 7 else int(cols[3])
        elif line.startswith("Swap:"):
            cols = line.split()
            if len(cols) >= 4:
                swap_total = int(cols[1])
                swap_used = int(cols[2])
    if mem_total is None or mem_used is None:
        return skeleton
    # Load average from /proc/loadavg ("0.68 2.61 4.02 ..." — we want 5m)
    loadavg_raw = _run_wsl(["cat", "/proc/loadavg"])
    load_5m = None
    if loadavg_raw:
        parts = loadavg_raw.split()
        if len(parts) >= 2:
            try:
                load_5m = float(parts[1])
            except ValueError:
                load_5m = None
    return {
        "available": True,
        "ram_total_gb": round(mem_total / (1024**3), 2),
        "ram_used_gb": round(mem_used / (1024**3), 2),
        "ram_free_gb": round((mem_free or 0) / (1024**3), 2),
        "ram_pct_used": round(mem_used / mem_total * 100, 1) if mem_total else None,
        "swap_used_gb": round((swap_used or 0) / (1024**3), 2),
        "swap_total_gb": round((swap_total or 0) / (1024**3), 2),
        "load_avg_5m": load_5m,
    }


def reset_cache() -> None:
    """Clear the TTL cache.  Used by tests."""
    _cache.clear()
