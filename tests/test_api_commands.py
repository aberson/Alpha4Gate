"""Unit tests for command REST API endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from bots.v0 import api as api_module
from bots.v0.api import app, configure
from bots.v0.commands import (
    CommandMode,
    get_command_queue,
    get_command_settings,
)
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """Create a test client with temporary data directories."""
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    replay_dir = tmp_path / "replays"
    data_dir.mkdir()
    log_dir.mkdir()
    replay_dir.mkdir()
    configure(data_dir, log_dir, replay_dir)

    # Reset module-level state between tests
    api_module._command_history.clear()

    # Reset command settings to defaults
    settings = get_command_settings()
    settings.mode = CommandMode.AI_ASSISTED
    settings.claude_interval = 30.0
    settings.lockout_duration = 5.0
    settings.muted = False

    # Clear the queue
    get_command_queue().clear()

    return TestClient(app)


class TestSubmitCommand:
    def test_structured_command_queued(self, client: TestClient) -> None:
        """POST /api/commands with structured text returns 'queued'."""
        resp = client.post("/api/commands", json={"text": "build stalkers at natural"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["text"] == "build stalkers at natural"
        assert len(data["parsed"]) == 1
        assert data["parsed"][0]["action"] == "build"
        assert data["parsed"][0]["target"] == "stalkers"
        assert data["parsed"][0]["location"] == "natural"
        assert data["parsed"][0]["source"] == "human"
        assert data["id"]  # non-empty

    def test_unrecognized_command_returns_parsing(self, client: TestClient) -> None:
        """POST /api/commands with unrecognized text returns 'parsing'."""
        resp = client.post(
            "/api/commands",
            json={"text": "please make some units to defend my base"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "parsing"
        assert data["id"]  # non-empty UUID

    def test_structured_command_added_to_queue(self, client: TestClient) -> None:
        """Structured commands are pushed to the command queue."""
        client.post("/api/commands", json={"text": "attack enemy_main"})
        queue = get_command_queue()
        assert queue.size == 1

    def test_structured_command_added_to_history(self, client: TestClient) -> None:
        """Structured commands appear in history immediately."""
        client.post("/api/commands", json={"text": "build zealots"})
        resp = client.get("/api/commands/history")
        cmds = resp.json()["commands"]
        assert len(cmds) == 1
        assert cmds[0]["text"] == "build zealots"
        assert cmds[0]["status"] == "queued"


class TestCommandHistory:
    def test_empty_history(self, client: TestClient) -> None:
        """GET /api/commands/history returns empty list initially."""
        resp = client.get("/api/commands/history")
        assert resp.status_code == 200
        assert resp.json()["commands"] == []

    def test_history_accumulates(self, client: TestClient) -> None:
        """History accumulates multiple commands."""
        client.post("/api/commands", json={"text": "build pylon"})
        client.post("/api/commands", json={"text": "expand natural"})
        resp = client.get("/api/commands/history")
        assert len(resp.json()["commands"]) == 2


class TestCommandMode:
    def test_get_mode(self, client: TestClient) -> None:
        """GET /api/commands/mode returns current mode."""
        resp = client.get("/api/commands/mode")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "ai_assisted"
        assert data["muted"] is False

    def test_set_mode(self, client: TestClient) -> None:
        """PUT /api/commands/mode changes the mode."""
        resp = client.put("/api/commands/mode", json={"mode": "human_only"})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "human_only"

        # Verify persisted
        resp2 = client.get("/api/commands/mode")
        assert resp2.json()["mode"] == "human_only"

    def test_set_invalid_mode(self, client: TestClient) -> None:
        """PUT /api/commands/mode with invalid mode returns 400."""
        resp = client.put("/api/commands/mode", json={"mode": "invalid_mode"})
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "valid_modes" in data

    def test_mode_switch_clears_queue(self, client: TestClient) -> None:
        """Switching mode clears the command queue."""
        # Add some commands to queue
        client.post("/api/commands", json={"text": "build stalkers"})
        client.post("/api/commands", json={"text": "build zealots"})
        assert get_command_queue().size == 2

        # Switch mode
        resp = client.put("/api/commands/mode", json={"mode": "hybrid_cmd"})
        assert resp.status_code == 200
        assert resp.json()["queue_cleared"] is True
        assert get_command_queue().size == 0


class TestCommandSettings:
    def test_update_settings(self, client: TestClient) -> None:
        """PUT /api/commands/settings updates settings."""
        resp = client.put(
            "/api/commands/settings",
            json={
                "claude_interval": 15.0,
                "lockout_duration": 3.0,
                "muted": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["claude_interval"] == 15.0
        assert data["lockout_duration"] == 3.0
        assert data["muted"] is True

    def test_partial_update(self, client: TestClient) -> None:
        """Only provided fields are updated."""
        resp = client.put("/api/commands/settings", json={"muted": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["muted"] is True
        # Defaults unchanged
        assert data["claude_interval"] == 30.0
        assert data["lockout_duration"] == 5.0


class TestGetCommandSettings:
    def test_get_settings_defaults(self, client: TestClient) -> None:
        """GET /api/commands/settings returns current defaults."""
        resp = client.get("/api/commands/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["claude_interval"] == 30.0
        assert data["lockout_duration"] == 5.0
        assert data["muted"] is False

    def test_get_settings_reflects_put(self, client: TestClient) -> None:
        """GET /api/commands/settings returns values after PUT."""
        client.put(
            "/api/commands/settings",
            json={"claude_interval": 10.0, "muted": True},
        )
        resp = client.get("/api/commands/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["claude_interval"] == 10.0
        assert data["muted"] is True
        assert data["lockout_duration"] == 5.0  # unchanged


class TestCommandPrimitives:
    def test_get_primitives(self, client: TestClient) -> None:
        """GET /api/commands/primitives returns vocabulary."""
        resp = client.get("/api/commands/primitives")
        assert resp.status_code == 200
        data = resp.json()
        assert "build" in data["actions"]
        assert "attack" in data["actions"]
        assert "build" in data["targets"]
        assert "main" in data["locations"]
        assert "enemy_main" in data["locations"]
