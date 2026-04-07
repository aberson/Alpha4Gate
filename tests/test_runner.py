"""Tests for runner server startup."""

from __future__ import annotations

import socket
import time
from pathlib import Path

import httpx
import pytest

from alpha4gate.config import Settings
from alpha4gate.runner import _start_server_background


def _free_port() -> int:
    """Find a free TCP port using bind-then-close."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _make_settings(tmp_path: Path, port: int) -> Settings:
    """Build a Settings instance pointing at tmp_path with the given port."""
    for subdir in ("logs", "replays", "data"):
        (tmp_path / subdir).mkdir(exist_ok=True)
    return Settings(
        sc2_path=tmp_path,
        log_dir=tmp_path / "logs",
        replay_dir=tmp_path / "replays",
        data_dir=tmp_path / "data",
        web_ui_port=port,
        anthropic_api_key="",
        spawning_tool_api_key="",
    )


class TestStartServerBackground:
    def test_server_starts_and_responds(self, tmp_path: Path) -> None:
        """Verify the background server starts and /api/commands/mode returns 200."""
        port = _free_port()
        settings = _make_settings(tmp_path, port)

        _start_server_background(settings)

        # Poll until server is ready (max 5s)
        url = f"http://localhost:{port}/api/commands/mode"
        resp = None
        for _ in range(50):
            try:
                resp = httpx.get(url, timeout=0.5)
                if resp.status_code == 200:
                    break
            except httpx.ConnectError:
                time.sleep(0.1)
        else:
            pytest.fail("Server did not start within 5 seconds")

        assert resp is not None
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" in data

