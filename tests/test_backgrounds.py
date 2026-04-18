"""Tests for ``selfplay_viewer.backgrounds``.

Pure-Python — does not import pygame. Uses ``tmp_path`` so tests work
without real PNG content (filename parsing is the only behaviour
exercised here).
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from selfplay_viewer.backgrounds import list_backgrounds, pick_background

_LONG_NAMES = (
    "protoss_themed_sf2_brazil_background.png",
    "protoss_themed_sf2_china_background.png",
)


def _populate(tmp_path: Path) -> Path:
    """Create the three test backgrounds (2 long-named + tokyo)."""
    for name in _LONG_NAMES:
        (tmp_path / name).touch()
    (tmp_path / "tokyo.png").touch()
    return tmp_path


def test_list_backgrounds_strips_long_filename_prefix(tmp_path: Path) -> None:
    """Long-form filenames map to short keys; short filenames pass through.

    Full-dict equality so both the derived keys *and* the resolved paths
    are verified in a single assertion — any regression on either side
    (key derivation or path mapping) fails this test.
    """
    _populate(tmp_path)

    assert list_backgrounds(backgrounds_dir=tmp_path) == {
        "brazil": tmp_path / "protoss_themed_sf2_brazil_background.png",
        "china": tmp_path / "protoss_themed_sf2_china_background.png",
        "tokyo": tmp_path / "tokyo.png",
    }


def test_list_backgrounds_missing_dir_returns_empty(tmp_path: Path) -> None:
    """A missing backgrounds directory is not an error here — picker handles it."""
    result = list_backgrounds(backgrounds_dir=tmp_path / "does_not_exist")

    assert result == {}


def test_list_backgrounds_raises_on_key_collision(tmp_path: Path) -> None:
    """Two filenames that derive the same key must raise ValueError."""
    short_path = tmp_path / "brazil.png"
    long_path = tmp_path / "protoss_themed_sf2_brazil_background.png"
    short_path.touch()
    long_path.touch()

    with pytest.raises(ValueError) as excinfo:
        list_backgrounds(backgrounds_dir=tmp_path)

    message = str(excinfo.value)
    assert "brazil" in message
    assert str(short_path) in message
    assert str(long_path) in message


def test_pick_background_random_is_deterministic_under_seed(tmp_path: Path) -> None:
    """Same seed -> same key, twice in a row."""
    _populate(tmp_path)

    first = pick_background("random", rng=random.Random(42), backgrounds_dir=tmp_path)
    second = pick_background("random", rng=random.Random(42), backgrounds_dir=tmp_path)

    assert first == second
    assert first.stem in {
        "protoss_themed_sf2_brazil_background",
        "protoss_themed_sf2_china_background",
        "tokyo",
    }


def test_pick_background_specific_key(tmp_path: Path) -> None:
    """Explicit key returns the matching path."""
    _populate(tmp_path)

    path = pick_background("brazil", backgrounds_dir=tmp_path)

    assert path == tmp_path / "protoss_themed_sf2_brazil_background.png"


def test_pick_background_unknown_key_lists_available(tmp_path: Path) -> None:
    """Unknown keys raise KeyError listing every available key sorted."""
    _populate(tmp_path)

    with pytest.raises(KeyError) as excinfo:
        pick_background("nonsense", backgrounds_dir=tmp_path)

    message = str(excinfo.value)
    for expected in ("brazil", "china", "tokyo"):
        assert expected in message
    # Regression guard: the message must contain the sorted-list
    # repr so an accidental switch to insertion-order would fail.
    assert "['brazil', 'china', 'tokyo']" in message


def test_pick_background_empty_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="No backgrounds"):
        pick_background("random", backgrounds_dir=tmp_path)
