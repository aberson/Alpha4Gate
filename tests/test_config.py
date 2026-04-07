"""Unit tests for config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from alpha4gate.config import load_settings


class TestLoadSettings:
    def test_loads_from_env_file(self, tmp_path: Path) -> None:
        sc2_dir = tmp_path / "StarCraft II"
        sc2_dir.mkdir()
        env_file = tmp_path / ".env"
        env_file.write_text(
            f"SC2PATH={sc2_dir}\n"
            "LOG_DIR=my_logs\n"
            "REPLAY_DIR=my_replays\n"
            "DATA_DIR=my_data\n"
            "WEB_UI_PORT=9999\n"
            "ANTHROPIC_API_KEY=sk-test\n"
            "SPAWNING_TOOL_API_KEY=st-test\n"
        )
        settings = load_settings(env_file)
        assert settings.sc2_path == sc2_dir
        assert settings.log_dir == Path("my_logs")
        assert settings.replay_dir == Path("my_replays")
        assert settings.data_dir == Path("my_data")
        assert settings.web_ui_port == 9999
        assert settings.anthropic_api_key == "sk-test"
        assert settings.spawning_tool_api_key == "st-test"

    def test_missing_sc2path_raises(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("SC2PATH=/nonexistent/path\n")
        with pytest.raises(FileNotFoundError, match="SC2 not found"):
            load_settings(env_file)

    def test_invalid_port_raises(self, tmp_path: Path) -> None:
        sc2_dir = tmp_path / "StarCraft II"
        sc2_dir.mkdir()
        env_file = tmp_path / ".env"
        env_file.write_text(f"SC2PATH={sc2_dir}\nWEB_UI_PORT=abc\n")
        with pytest.raises(ValueError, match="WEB_UI_PORT must be an integer"):
            load_settings(env_file)

