"""Tests for orchestrator.paths.resolve_sc2_path — platform-aware fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator import paths


class TestEnvOverride:
    def test_sc2path_env_var_wins_over_platform_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("SC2PATH", str(tmp_path))
        # Even on a "platform" the resolver doesn't recognise, env var wins
        # before any platform check runs.
        monkeypatch.setattr(paths.sys, "platform", "darwin")
        assert paths.resolve_sc2_path() == tmp_path

    def test_empty_sc2path_falls_back_to_platform(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SC2PATH", "")
        monkeypatch.setattr(paths.sys, "platform", "win32")
        assert paths.resolve_sc2_path() == paths._WINDOWS_DEFAULT


class TestWindows:
    def test_windows_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SC2PATH", raising=False)
        monkeypatch.setattr(paths.sys, "platform", "win32")
        assert paths.resolve_sc2_path() == Path(
            r"C:\Program Files (x86)\StarCraft II"
        )


class TestLinuxNative:
    def test_linux_native_uses_home_starcraftii(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("SC2PATH", raising=False)
        monkeypatch.setattr(paths.sys, "platform", "linux")
        monkeypatch.setattr(paths, "_is_wsl", lambda: False)
        monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
        assert paths.resolve_sc2_path() == tmp_path / "StarCraftII"


class TestWSL:
    def test_wsl_uses_mnt_c_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SC2PATH", raising=False)
        monkeypatch.setattr(paths.sys, "platform", "linux")
        monkeypatch.setattr(paths, "_is_wsl", lambda: True)
        assert paths.resolve_sc2_path() == Path(
            "/mnt/c/Program Files (x86)/StarCraft II"
        )

    def test_is_wsl_detects_microsoft_in_proc_version(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_proc = tmp_path / "version"
        fake_proc.write_text("Linux version 5.15.0 #1 SMP Microsoft WSL2 ...")
        monkeypatch.setattr(paths, "Path", _PathRedirect(tmp_path / "version"))
        try:
            assert paths._is_wsl() is True
        finally:
            monkeypatch.setattr(paths, "Path", Path)

    def test_is_wsl_false_on_native_linux(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_proc = tmp_path / "version"
        fake_proc.write_text("Linux version 6.5.0-generic Ubuntu SMP")
        monkeypatch.setattr(paths, "Path", _PathRedirect(fake_proc))
        try:
            assert paths._is_wsl() is False
        finally:
            monkeypatch.setattr(paths, "Path", Path)

    def test_is_wsl_false_when_proc_version_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        missing = tmp_path / "does_not_exist"
        monkeypatch.setattr(paths, "Path", _PathRedirect(missing))
        try:
            assert paths._is_wsl() is False
        finally:
            monkeypatch.setattr(paths, "Path", Path)


class TestUnsupported:
    def test_macos_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SC2PATH", raising=False)
        monkeypatch.setattr(paths.sys, "platform", "darwin")
        with pytest.raises(RuntimeError, match="SC2PATH"):
            paths.resolve_sc2_path()

    def test_freebsd_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SC2PATH", raising=False)
        monkeypatch.setattr(paths.sys, "platform", "freebsd14")
        with pytest.raises(RuntimeError, match="SC2PATH"):
            paths.resolve_sc2_path()


class _PathRedirect:
    """Path() factory that redirects ``/proc/version`` to a test file.

    Used to monkeypatch :class:`pathlib.Path` inside ``orchestrator.paths``
    so ``_is_wsl`` reads a fixture file instead of the real
    ``/proc/version``. All other ``Path(...)`` calls behave normally.
    """

    def __init__(self, redirect_target: Path) -> None:
        self._target = redirect_target

    def __call__(self, *args: object, **kwargs: object) -> Path:
        if args == ("/proc/version",) and not kwargs:
            return self._target
        return Path(*args, **kwargs)  # type: ignore[arg-type]
