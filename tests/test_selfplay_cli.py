"""CLI-level tests for ``scripts/selfplay.py``.

Covers the argparse paths (viewer flag wiring, ``--background``
validator, ``--seed`` flow) without pulling in pygame. The
``_background_key`` validator and the ``SelfPlayViewer`` construction
site both live in the script / ``selfplay_viewer.container`` module,
which has lazy pygame imports — safe to import on Linux CI.

Step 6 added seed-based deterministic random background selection;
the ``test_seed_*`` cases pin that behaviour against regressions.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_cli_module() -> ModuleType:
    """Import ``scripts/selfplay.py`` as a module named ``selfplay_cli``.

    The script isn't part of a package, so ``importlib.util`` is the
    idiomatic way to load it (mirrors the pattern in
    ``tests/test_selfplay.py::TestSelfPlayCLI``).
    """
    spec = importlib.util.spec_from_file_location(
        "selfplay_cli", str(_REPO_ROOT / "scripts" / "selfplay.py")
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Argparse surface
# ---------------------------------------------------------------------------


def test_default_flags() -> None:
    """Minimal h2h invocation pins all viewer-related defaults."""
    mod = _load_cli_module()
    args = mod.build_parser().parse_args(
        ["--p1", "v0", "--p2", "v0", "--games", "1"]
    )

    assert args.bar == "top"
    assert args.size == "large"
    assert args.background == "random"
    assert args.no_viewer is False
    assert args.seed is None


def test_explicit_viewer_flags() -> None:
    """``--bar``, ``--size`` flow to the namespace."""
    mod = _load_cli_module()
    args = mod.build_parser().parse_args(
        [
            "--p1", "v0", "--p2", "v0", "--games", "1",
            "--bar", "side", "--size", "small",
        ]
    )

    assert args.bar == "side"
    assert args.size == "small"


def test_size_tiny_flag() -> None:
    """``--size tiny`` is accepted (640x480 per pane preset)."""
    mod = _load_cli_module()
    args = mod.build_parser().parse_args(
        ["--p1", "v0", "--p2", "v0", "--games", "1", "--size", "tiny"]
    )

    assert args.size == "tiny"


def test_no_viewer_flag() -> None:
    """``--no-viewer`` sets the flag to True."""
    mod = _load_cli_module()
    args = mod.build_parser().parse_args(
        ["--p1", "v0", "--p2", "v0", "--games", "1", "--no-viewer"]
    )

    assert args.no_viewer is True


def test_background_unknown_key_exits_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unknown ``--background`` key exits with code 2 and lists valid keys.

    Points the backgrounds discovery at a tmp dir with two fake PNGs so
    the validator has a concrete ``valid`` set to compare against.
    """
    import selfplay_viewer.backgrounds as bg

    (tmp_path / "protoss_themed_sf2_brazil_background.png").touch()
    (tmp_path / "protoss_themed_sf2_china_background.png").touch()
    monkeypatch.setattr(bg, "_DEFAULT_BACKGROUND_DIR", tmp_path)

    mod = _load_cli_module()
    with pytest.raises(SystemExit) as excinfo:
        mod.build_parser().parse_args(
            [
                "--p1", "v0", "--p2", "v0", "--games", "1",
                "--background", "nonsense",
            ]
        )

    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    # argparse writes the validator error message to stderr; it must
    # mention the offending key AND the discovered valid keys.
    assert "nonsense" in captured.err
    assert "brazil" in captured.err
    assert "china" in captured.err


def test_background_random_always_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--background random`` parses even when the dir is populated."""
    import selfplay_viewer.backgrounds as bg

    (tmp_path / "protoss_themed_sf2_brazil_background.png").touch()
    (tmp_path / "protoss_themed_sf2_china_background.png").touch()
    monkeypatch.setattr(bg, "_DEFAULT_BACKGROUND_DIR", tmp_path)

    mod = _load_cli_module()
    args = mod.build_parser().parse_args(
        [
            "--p1", "v0", "--p2", "v0", "--games", "1",
            "--background", "random",
        ]
    )

    assert args.background == "random"


def test_background_validator_no_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``selfplay_viewer`` is unavailable the validator no-ops.

    Simulates the no-extras install by forcing
    ``from selfplay_viewer.backgrounds import list_backgrounds`` to
    raise at import time inside ``_background_key``. The validator's
    broad ``except Exception`` should swallow that and return the value
    unchanged so non-Windows / no-pygame callers can still parse the CLI.
    """
    mod = _load_cli_module()

    # Stash and remove the real module so the inner import inside
    # ``_background_key`` re-imports and hits our sentinel. ``_loader``
    # hook isn't needed; replacing the cached module with a stub whose
    # attribute access raises is the simplest path.
    real = sys.modules.get("selfplay_viewer.backgrounds")
    monkeypatch.delitem(sys.modules, "selfplay_viewer.backgrounds", raising=False)
    monkeypatch.delitem(sys.modules, "selfplay_viewer", raising=False)

    class _Finder:
        @classmethod
        def find_spec(cls, name: str, *args: object, **kwargs: object) -> None:
            if name == "selfplay_viewer" or name.startswith("selfplay_viewer."):
                raise ImportError(f"simulated missing extra: {name}")
            return None

    monkeypatch.setattr(sys, "meta_path", [_Finder, *sys.meta_path])
    try:
        # The validator should swallow the ImportError and echo the
        # input back — even for a key that would normally be rejected.
        assert mod._background_key("anything") == "anything"
        assert mod._background_key("random") == "random"
    finally:
        # Restore the real module if we had one cached; monkeypatch
        # tears down the meta_path patch on exit automatically.
        if real is not None:
            sys.modules["selfplay_viewer.backgrounds"] = real


# ---------------------------------------------------------------------------
# Seed-deterministic background selection
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_backgrounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the backgrounds scanner at a tmp dir with three fake PNGs.

    Empty files are fine — ``_resolve_background_path`` never opens the
    file, only checks the directory listing via ``list_backgrounds``.
    """
    import selfplay_viewer.backgrounds as bg

    (tmp_path / "protoss_themed_sf2_brazil_background.png").touch()
    (tmp_path / "protoss_themed_sf2_china_background.png").touch()
    (tmp_path / "protoss_themed_sf2_japan_background.png").touch()
    monkeypatch.setattr(bg, "_DEFAULT_BACKGROUND_DIR", tmp_path)
    return tmp_path


def test_seed_selects_deterministic_random_background(
    temp_backgrounds: Path,
) -> None:
    """Same seed -> same random pick; different seeds -> different picks.

    Covers the Step 6 contract: ``SelfPlayViewer(background="random",
    seed=S)`` must be reproducible across construction boundaries so a
    CLI ``--seed`` run is stable. The inequality across seeds is
    probabilistic but reliable with 3 choices and two hand-picked seeds.
    """
    from selfplay_viewer.container import SelfPlayViewer

    v1 = SelfPlayViewer(background="random", seed=42)
    v2 = SelfPlayViewer(background="random", seed=42)
    v3 = SelfPlayViewer(background="random", seed=43)

    assert v1._resolve_background_path() == v2._resolve_background_path()
    # With 3 backgrounds and these two seeds, Random(42) and Random(43)
    # land on different keys; see random.choice(["brazil","china","japan"]).
    assert v1._resolve_background_path() != v3._resolve_background_path()


def test_seed_cache_stable_across_calls(
    temp_backgrounds: Path,
) -> None:
    """Repeat calls to ``_resolve_background_path`` return the cached pick.

    Regression guard for the S/B hotkey path —
    ``_apply_layout_change`` calls ``_resolve_background_path_for`` on
    every layout change, which delegates to
    ``_resolve_background_path``. Without the cache each call would
    consume a fresh draw from the seeded RNG and flash a new background
    every resize.
    """
    from selfplay_viewer.container import SelfPlayViewer

    viewer = SelfPlayViewer(background="random", seed=42)

    first = viewer._resolve_background_path()
    # Five extra draws mirror the worst-case S/B spam during a demo.
    for _ in range(5):
        assert viewer._resolve_background_path() == first


def test_no_seed_still_resolves(temp_backgrounds: Path) -> None:
    """Without a seed the viewer still resolves a random pick and caches it."""
    from selfplay_viewer.container import SelfPlayViewer

    viewer = SelfPlayViewer(background="random")
    first = viewer._resolve_background_path()
    second = viewer._resolve_background_path()

    assert first == second
    assert first.parent == temp_backgrounds
