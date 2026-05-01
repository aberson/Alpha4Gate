"""Process registry: scan and report known Alpha4Gate processes."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Command-line substrings that identify "one of our" processes.
_OUR_CMDLINE_TAGS: tuple[str, ...] = ("bots.v10", "bots.current")


def _is_ours(cmdline: str) -> bool:
    """Return True if ``cmdline`` (already lower-cased) matches any of our tags."""
    return any(tag in cmdline for tag in _OUR_CMDLINE_TAGS)


@dataclass
class ProcessInfo:
    """A known process entry."""

    name: str
    pid: int | None
    status: str  # "running", "stopped", "unknown"
    role: str  # "backend", "daemon", "advisor", "frontend", "sc2", "orphan"
    start_time: str | None = None
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pid": self.pid,
            "status": self.status,
            "role": self.role,
            "start_time": self.start_time,
            "details": self.details,
        }


def _run_ps(command: str) -> str:
    """Run a PowerShell command and return stdout."""
    try:
        result = subprocess.run(
            ["powershell.exe", "-Command", command],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def scan_processes() -> list[ProcessInfo]:
    """Scan system for known Alpha4Gate-related processes."""
    procs: list[ProcessInfo] = []

    # Use PowerShell to get process info in JSON.
    # Format StartTime as ISO 8601 to avoid \/Date(...)\/  serialization.
    ps_cmd = (
        "Get-Process -Name python,uv,node,SC2_x64 "
        "-ErrorAction SilentlyContinue | ForEach-Object { "
        "$cim = Get-CimInstance Win32_Process "
        "-Filter \"ProcessId=$($_.Id)\" -ErrorAction SilentlyContinue; "
        "[PSCustomObject]@{ "
        "Id=$_.Id; ProcessName=$_.ProcessName; "
        "StartTimeISO=if($_.StartTime){$_.StartTime.ToString('o')}else{$null}; "
        "CommandLine=if($cim){$cim.CommandLine}else{$null}; "
        "ParentPid=if($cim){$cim.ParentProcessId}else{$null} "
        "} } | ConvertTo-Json -Compress"
    )
    raw = _run_ps(ps_cmd)
    if not raw:
        return procs

    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return procs

    # Normalize to list (PowerShell returns object for single result)
    if isinstance(entries, dict):
        entries = [entries]

    # Build PID→name map for parent-child detection
    pid_to_name: dict[int | None, str] = {
        e.get("Id"): e.get("ProcessName", "") for e in entries
    }

    for entry in entries:
        pid = entry.get("Id")
        name = entry.get("ProcessName", "")
        cmdline = entry.get("CommandLine") or ""
        start_str = entry.get("StartTimeISO")
        parent_pid = entry.get("ParentPid")

        role = _classify_process(name, cmdline, pid, parent_pid, pid_to_name)
        details = _summarize_cmdline(cmdline)

        procs.append(
            ProcessInfo(
                name=name,
                pid=pid,
                status="running",
                role=role,
                start_time=start_str,
                details=details,
            )
        )

    # Check for daemon thread (internal to backend)
    procs.extend(_check_daemon_status())

    # Check for stale lock files
    procs.extend(_check_lock_files())

    return procs


def _classify_process(
    name: str,
    cmdline: str,
    pid: int | None = None,
    parent_pid: int | None = None,
    pid_to_name: dict[int | None, str] | None = None,
) -> str:
    """Classify a process by its role."""
    lower = cmdline.lower()
    if name == "SC2_x64":
        return "sc2"
    if name == "node":
        return "frontend"
    if _is_ours(lower) and "--serve" in lower:
        if name == "uv":
            return "backend-wrapper"
        # Check parent: if parent is python → this is uvicorn (server).
        # If parent is uv → this is the runner entry point.
        parent_name = (pid_to_name or {}).get(parent_pid, "")
        if parent_name == "python":
            return "backend-server"
        return "backend-runner"
    if _is_ours(lower) and "--batch" in lower:
        return "game-runner"
    if _is_ours(lower):
        return "runner"
    if name in ("python", "uv"):
        return "python"
    return "unknown"


def _summarize_cmdline(cmdline: str) -> str:
    """Create a short summary of a command line."""
    if not cmdline:
        return ""
    # Truncate long paths, keep the meaningful parts
    lower = cmdline.lower()
    if _is_ours(lower):
        # Extract the key flags and label by whichever tag actually matched
        # Label by whichever tag matched (bots.v10 or bots.current).
        parts = cmdline.split()
        flags = [p for p in parts if p.startswith("--")]
        label = next((tag for tag in _OUR_CMDLINE_TAGS if tag in lower), "bots.v10")
        return label + " " + " ".join(flags)
    if "node" in lower and "vite" in lower:
        return "vite dev server"
    if len(cmdline) > 120:
        return cmdline[:120] + "..."
    return cmdline


def _check_daemon_status() -> list[ProcessInfo]:
    """Check if the training daemon is running (via API)."""
    import urllib.request

    procs: list[ProcessInfo] = []
    try:
        req = urllib.request.Request("http://localhost:8765/api/training/daemon")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            is_running = data.get("running", False)
            procs.append(
                ProcessInfo(
                    name="training-daemon",
                    pid=None,
                    status="running" if is_running else "stopped",
                    role="daemon",
                    details=f"interval={data.get('interval_seconds', '?')}s"
                    + (f", cycle={data.get('cycle_count', '?')}" if is_running else ""),
                )
            )
    except Exception:
        # Backend not reachable — daemon status unknown
        pass
    return procs


def _check_lock_files() -> list[ProcessInfo]:
    """Check for stale lock files."""
    procs: list[ProcessInfo] = []
    lock_path = Path(".claude/scheduled_tasks.lock")
    if lock_path.exists():
        try:
            content = lock_path.read_text().strip()
            pid = int(content) if content.isdigit() else None
            # Check if PID is still alive
            alive = False
            if pid:
                check = _run_ps(
                    f"Get-Process -Id {pid} -ErrorAction SilentlyContinue "
                    "| Select-Object -ExpandProperty Id"
                )
                alive = bool(check.strip())
            procs.append(
                ProcessInfo(
                    name="scheduled_tasks.lock",
                    pid=pid,
                    status="running" if alive else "stale",
                    role="lock-file",
                    details="PID alive" if alive else "PID dead — safe to delete",
                )
            )
        except (ValueError, OSError):
            pass
    return procs


def get_port_status() -> list[dict[str, Any]]:
    """Check if known ports are bound."""
    ports = []
    for port, label in [(8765, "Backend API"), (3000, "Frontend Dev")]:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            bound = s.connect_ex(("127.0.0.1", port)) == 0
        ports.append({"port": port, "label": label, "bound": bound})
    return ports


def get_state_files() -> list[dict[str, Any]]:
    """Check state files for stale status."""
    from orchestrator.registry import resolve_data_path

    files = []
    for filename, key in [
        ("advised_run_state.json", "status"),
        ("advised_run_control.json", "stop_run"),
    ]:
        p = resolve_data_path(filename)
        display = str(p)
        if p.exists():
            try:
                data = json.loads(p.read_text())
                files.append({
                    "file": display,
                    "exists": True,
                    "key_field": key,
                    "value": data.get(key),
                    "updated_at": data.get("updated_at"),
                })
            except (json.JSONDecodeError, OSError):
                files.append({"file": display, "exists": True, "error": True})
        else:
            files.append({"file": display, "exists": False})
    return files


def get_temp_file_counts() -> dict[str, int]:
    """Count accumulated temp files."""
    from orchestrator.registry import resolve_data_path

    reward_logs_dir = resolve_data_path("reward_logs")
    counts: dict[str, int] = {}
    for directory, suffix, label in [
        (Path("logs"), ".jsonl", "game_logs"),
        (reward_logs_dir, ".jsonl", "reward_logs"),
        (Path("replays"), ".SC2Replay", "replays"),
    ]:
        if directory.exists():
            counts[label] = sum(
                1 for f in directory.iterdir() if f.name.endswith(suffix)
            )
        else:
            counts[label] = 0
    return counts


def full_status() -> dict[str, Any]:
    """Get full process/system status."""
    return {
        "processes": [p.to_dict() for p in scan_processes()],
        "ports": get_port_status(),
        "state_files": get_state_files(),
        "temp_files": get_temp_file_counts(),
        "scanned_at": datetime.now(UTC).isoformat(),
    }
