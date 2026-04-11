"""Unit tests for connection.build_replay_path.

The full ``run_bot`` entrypoint launches SC2 via burnysc2 and is exercised
under the ``sc2`` integration marker. These tests cover the pure filename
helper that guarantees per-game uniqueness.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from alpha4gate.connection import build_replay_path


def test_build_replay_path_format_matches_expected_pattern(tmp_path: Path) -> None:
    """Filename follows game_<map>_<YYYYMMDDTHHMMSS>.SC2Replay."""
    now = datetime(2026, 4, 10, 14, 30, 15)

    path = build_replay_path(tmp_path, "Simple64", now=now)

    assert path.parent == tmp_path
    assert path.name == "game_Simple64_20260410T143015.SC2Replay"


def test_build_replay_path_name_regex(tmp_path: Path) -> None:
    """Default (no injected clock) still produces the expected shape."""
    path = build_replay_path(tmp_path, "AcropolisLE")

    pattern = re.compile(r"^game_AcropolisLE_\d{8}T\d{6}\.SC2Replay$")
    assert pattern.match(path.name), f"unexpected filename: {path.name}"


def test_build_replay_path_different_times_produce_distinct_filenames(
    tmp_path: Path,
) -> None:
    """Two games launched at different seconds must not collide."""
    first = build_replay_path(
        tmp_path, "Simple64", now=datetime(2026, 4, 10, 14, 30, 15)
    )
    second = build_replay_path(
        tmp_path, "Simple64", now=datetime(2026, 4, 10, 14, 30, 16)
    )

    assert first != second
    assert first.name != second.name


def test_build_replay_path_preserves_map_name_in_filename(tmp_path: Path) -> None:
    """Map name is embedded verbatim so different maps never collide."""
    now = datetime(2026, 4, 10, 14, 30, 15)

    a = build_replay_path(tmp_path, "Simple64", now=now)
    b = build_replay_path(tmp_path, "AcropolisLE", now=now)

    assert a.name != b.name
    assert "Simple64" in a.name
    assert "AcropolisLE" in b.name
