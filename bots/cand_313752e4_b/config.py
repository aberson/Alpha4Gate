"""Load and validate project configuration from .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Project settings loaded from environment."""

    sc2_path: Path
    log_dir: Path
    replay_dir: Path
    data_dir: Path
    web_ui_port: int
    anthropic_api_key: str
    spawning_tool_api_key: str

    def ensure_dirs(self) -> None:
        """Create log, replay, and data directories if they don't exist."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.replay_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


def load_settings(env_file: Path | None = None) -> Settings:
    """Load settings from .env file and validate SC2PATH exists.

    Args:
        env_file: Path to .env file. If None, searches from cwd upward.

    Returns:
        Validated Settings instance.

    Raises:
        FileNotFoundError: If SC2PATH directory does not exist.
        ValueError: If required settings are missing or invalid.
    """
    if env_file is not None:
        load_dotenv(env_file, override=True)
    else:
        load_dotenv(override=True)

    sc2_path_str = os.getenv("SC2PATH", r"C:\Program Files (x86)\StarCraft II")
    sc2_path = Path(sc2_path_str)

    if not sc2_path.is_dir():
        msg = f"SC2 not found at {sc2_path}. Set SC2PATH in .env"
        raise FileNotFoundError(msg)

    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    replay_dir = Path(os.getenv("REPLAY_DIR", "replays"))
    # Default routes through orchestrator.registry so production flips from
    # legacy ``data/`` to ``bots/<current>/data/`` once Step 1.8 seeds the
    # per-version directory. Explicit DATA_DIR env override still wins — tests
    # and ad-hoc runs can pin the directory as before.
    env_data_dir = os.getenv("DATA_DIR")
    if env_data_dir is not None:
        data_dir = Path(env_data_dir)
    else:
        from orchestrator.registry import get_data_dir

        try:
            data_dir = get_data_dir()
        except (FileNotFoundError, ValueError):
            # No ``bots/current/current.txt`` pointer (e.g. running from a
            # checkout predating Phase 1); fall back to the legacy default.
            data_dir = Path("data")

    port_str = os.getenv("WEB_UI_PORT", "8765")
    try:
        web_ui_port = int(port_str)
    except ValueError:
        msg = f"WEB_UI_PORT must be an integer, got: {port_str!r}"
        raise ValueError(msg) from None

    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
    spawning_tool_api_key = os.getenv("SPAWNING_TOOL_API_KEY", "")

    return Settings(
        sc2_path=sc2_path,
        log_dir=log_dir,
        replay_dir=replay_dir,
        data_dir=data_dir,
        web_ui_port=web_ui_port,
        anthropic_api_key=anthropic_api_key,
        spawning_tool_api_key=spawning_tool_api_key,
    )
