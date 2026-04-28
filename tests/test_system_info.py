"""Tests for bots.v3.system_info — substrate / WSL / resources helpers."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from bots.v3 import system_info


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Reset the module-level TTL cache before every test."""
    system_info.reset_cache()


# ---------------------------------------------------------------------------
# WSL availability + shell-out wrapper
# ---------------------------------------------------------------------------


class TestWslShellOut:
    def test_run_wsl_returns_none_when_binary_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No ``wsl`` on PATH → return None, never raise."""
        monkeypatch.setattr(system_info.shutil, "which", lambda _: None)
        assert system_info._run_wsl(["uname"]) is None

    def test_run_wsl_returns_none_on_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Timeout in subprocess.run is swallowed; caller never sees the exc."""
        monkeypatch.setattr(system_info.shutil, "which", lambda _: "/usr/bin/wsl")

        def _raise(*_a: Any, **_kw: Any) -> Any:
            raise subprocess.TimeoutExpired(cmd="wsl", timeout=3.0)

        monkeypatch.setattr(system_info.subprocess, "run", _raise)
        assert system_info._run_wsl(["uname"]) is None

    def test_run_wsl_returns_none_on_nonzero_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-zero exit (e.g. distro not installed) → None, no raise."""
        monkeypatch.setattr(system_info.shutil, "which", lambda _: "/usr/bin/wsl")
        monkeypatch.setattr(
            system_info.subprocess,
            "run",
            lambda *a, **kw: subprocess.CompletedProcess(
                args=a, returncode=1, stdout="", stderr="distro not found"
            ),
        )
        assert system_info._run_wsl(["uname"]) is None

    def test_run_wsl_returns_stdout_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(system_info.shutil, "which", lambda _: "/usr/bin/wsl")
        monkeypatch.setattr(
            system_info.subprocess,
            "run",
            lambda *a, **kw: subprocess.CompletedProcess(
                args=a, returncode=0, stdout="5.15.0-microsoft\n", stderr=""
            ),
        )
        assert system_info._run_wsl(["uname", "-r"]) == "5.15.0-microsoft\n"


# ---------------------------------------------------------------------------
# Substrate info
# ---------------------------------------------------------------------------


class TestSubstrateInfo:
    def test_returns_skeleton_when_wsl_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(system_info.shutil, "which", lambda _: None)
        info = system_info.get_substrate_info()
        assert info["backend_platform"] == "windows"
        assert info["wsl"]["available"] is False
        assert info["wsl"]["distro"] is None

    def test_populates_wsl_section_when_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mocked WSL responses populate kernel + sc2 path + binary check."""
        monkeypatch.setattr(system_info.shutil, "which", lambda _: "/usr/bin/wsl")

        def _fake_run_wsl(cmd: list[str], timeout: float = 3.0) -> str | None:
            if cmd == ["uname", "-r"]:
                return "5.15.0-microsoft-WSL2\n"
            if cmd[:2] == ["bash", "-lc"] and "$SC2PATH" in cmd[2]:
                return "/home/abero/StarCraftII"
            if cmd[:2] == ["bash", "-lc"] and "test -f" in cmd[2]:
                return "yes\n"
            return None

        monkeypatch.setattr(system_info, "_run_wsl", _fake_run_wsl)
        info = system_info.get_substrate_info()
        assert info["wsl"]["available"] is True
        assert info["wsl"]["kernel"] == "5.15.0-microsoft-WSL2"
        assert info["wsl"]["sc2_path"] == "/home/abero/StarCraftII"
        assert info["wsl"]["sc2_binary_present"] is True


# ---------------------------------------------------------------------------
# WSL processes
# ---------------------------------------------------------------------------


class TestWslProcesses:
    def test_unavailable_when_wsl_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(system_info.shutil, "which", lambda _: None)
        out = system_info.get_wsl_processes()
        assert out == {"available": False, "processes": []}

    def test_filters_to_sc2_and_relevant_python(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only SC2_x64 + python invocations of bots.v* / evolve / selfplay
        survive the filter; bash, ps, etc. are excluded.
        """
        monkeypatch.setattr(system_info.shutil, "which", lambda _: "/usr/bin/wsl")
        # Pad columns so the split-by-whitespace logic works (5 columns
        # total; last column is the full args line).
        sc2_args = "/home/abero/StarCraftII/Versions/Base75689/SC2_x64 -listen 127.0.0.1"
        v3_args = (
            "/home/abero/venv-alpha4gate-linux/bin/python3 "
            "-m bots.v3 --role p1 --map Simple64"
        )
        sp_args = (
            "/home/abero/venv-alpha4gate-linux/bin/python3 "
            "scripts/selfplay.py --p1 v3 --p2 v3"
        )
        ps_output = (
            f"  101 SC2_x64              02:31  524288 {sc2_args}\n"
            f"  102 python3.12           00:45  131072 {v3_args}\n"
            "  103 bash                 03:12   12288 -bash\n"
            f"  104 python3.12           00:10   65536 {sp_args}\n"
            "  105 ps                   00:00    4096 ps -eo pid,comm,etime\n"
        )
        monkeypatch.setattr(
            system_info, "_run_wsl", lambda *a, **kw: ps_output
        )
        out = system_info.get_wsl_processes()
        assert out["available"] is True
        pids = sorted(p["pid"] for p in out["processes"])
        # Must include SC2 (101), v3 bot (102), selfplay (104).
        # Must exclude bash (103), ps (105).
        assert pids == [101, 102, 104]
        labels = {p["pid"]: p["label"] for p in out["processes"]}
        assert labels[101] == "SC2_x64"
        assert labels[102] == "bots.v3"
        assert labels[104] == "selfplay.py"


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class TestResources:
    def test_host_section_always_populated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """psutil-derived host RAM/disk fields must be set even when WSL is down."""
        monkeypatch.setattr(system_info.shutil, "which", lambda _: None)
        out = system_info.get_resources()
        host = out["host"]
        assert host["available"] is True
        assert host["ram_total_gb"] > 0
        assert host["ram_free_gb"] >= 0
        assert 0 <= host["ram_pct_used"] <= 100
        assert host["disk_total_gb"] is not None
        assert host["disk_free_gb"] is not None

    def test_wsl_section_skeleton_when_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(system_info.shutil, "which", lambda _: None)
        out = system_info.get_resources()
        wsl = out["wsl"]
        assert wsl["available"] is False
        assert wsl["ram_total_gb"] is None
        assert wsl["load_avg_5m"] is None

    def test_wsl_section_parses_free_and_loadavg(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``free -b`` + ``/proc/loadavg`` outputs parse into the gauge fields."""
        monkeypatch.setattr(system_info.shutil, "which", lambda _: "/usr/bin/wsl")

        def _fake_run_wsl(cmd: list[str], timeout: float = 3.0) -> str | None:
            if cmd == ["free", "-b"]:
                # Stock Ubuntu 22.04 layout: total used free shared buff available
                header = (
                    "        total      used      free   shared  buff/cache  available\n"
                )
                mem = "Mem:  8053063680 408944640 7497416704 2097152 146702336 7493857280\n"
                swap = "Swap: 2147483648  76677120 2070806528\n"
                return header + mem + swap
            if cmd == ["cat", "/proc/loadavg"]:
                return "0.68 2.61 4.02 1/123 4567\n"
            return None

        monkeypatch.setattr(system_info, "_run_wsl", _fake_run_wsl)
        out = system_info.get_resources()
        wsl = out["wsl"]
        assert wsl["available"] is True
        assert wsl["ram_total_gb"] == pytest.approx(7.5, abs=0.1)
        assert wsl["ram_used_gb"] == pytest.approx(0.38, abs=0.1)
        assert wsl["swap_used_gb"] == pytest.approx(0.07, abs=0.05)
        assert wsl["load_avg_5m"] == pytest.approx(2.61)


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


class TestCache:
    def test_within_ttl_returns_cached_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second call inside the TTL window must NOT re-invoke the producer."""
        calls = {"n": 0}

        def _producer() -> dict[str, int]:
            calls["n"] += 1
            return {"value": calls["n"]}

        first = system_info._cached("k", ttl=10.0, fn=_producer)
        second = system_info._cached("k", ttl=10.0, fn=_producer)
        assert first == second == {"value": 1}
        assert calls["n"] == 1

    def test_after_ttl_refreshes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When monotonic clock advances past the TTL, value re-computes."""
        calls = {"n": 0}

        def _producer() -> dict[str, int]:
            calls["n"] += 1
            return {"value": calls["n"]}

        # Stub time.monotonic so we control the clock.
        clock = {"t": 1000.0}
        monkeypatch.setattr(system_info.time, "monotonic", lambda: clock["t"])

        system_info._cached("k", ttl=5.0, fn=_producer)
        clock["t"] += 6.0  # past TTL
        system_info._cached("k", ttl=5.0, fn=_producer)
        assert calls["n"] == 2
