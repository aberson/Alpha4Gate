"""Unit tests for config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from alpha4gate.config import Settings, load_settings


class TestSettings:
    def test_ensure_dirs_creates_directories(self, tmp_path: Path) -> None:
        settings = Settings(
            sc2_path=tmp_path,
            log_dir=tmp_path / "logs",
            replay_dir=tmp_path / "replays",
            data_dir=tmp_path / "data",
            web_ui_port=8765,
            anthropic_api_key="",
            spawning_tool_api_key="",
        )
        settings.ensure_dirs()
        assert (tmp_path / "logs").is_dir()
        assert (tmp_path / "replays").is_dir()
        assert (tmp_path / "data").is_dir()

    def test_ensure_dirs_idempotent(self, tmp_path: Path) -> None:
        settings = Settings(
            sc2_path=tmp_path,
            log_dir=tmp_path / "logs",
            replay_dir=tmp_path / "replays",
            data_dir=tmp_path / "data",
            web_ui_port=8765,
            anthropic_api_key="",
            spawning_tool_api_key="",
        )
        settings.ensure_dirs()
        settings.ensure_dirs()  # Should not raise


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

    def test_defaults_applied(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sc2_dir = tmp_path / "StarCraft II"
        sc2_dir.mkdir()
        env_file = tmp_path / ".env"
        env_file.write_text(f"SC2PATH={sc2_dir}\n")
        # Clear any existing env vars to test defaults
        monkeypatch.delenv("LOG_DIR", raising=False)
        monkeypatch.delenv("REPLAY_DIR", raising=False)
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.delenv("WEB_UI_PORT", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("SPAWNING_TOOL_API_KEY", raising=False)
        settings = load_settings(env_file)
        assert settings.log_dir == Path("logs")
        assert settings.replay_dir == Path("replays")
        assert settings.data_dir == Path("data")
        assert settings.web_ui_port == 8765
        assert settings.anthropic_api_key == ""
        assert settings.spawning_tool_api_key == ""
