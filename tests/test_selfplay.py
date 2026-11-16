"""Tests for the subprocess self-play batch runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.contracts import SelfPlayRecord
from orchestrator.selfplay import run_batch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_result(p1_name: str, p2_name: str, p1_wins: bool) -> list[dict[Any, Any]]:
    """Build a fake return value for ``a_run_multiple_games``.

    Returns ``[{player1: Result, player2: Result}]`` mimicking burnysc2.
    """
    victory = MagicMock()
    victory.__eq__ = lambda self, other: other is _VICTORY_SENTINEL
    defeat = MagicMock()

    p1 = MagicMock()
    p1.name = p1_name
    p2 = MagicMock()
    p2.name = p2_name

    if p1_wins:
        return [{p1: _VICTORY_SENTINEL, p2: defeat}]
    return [{p1: defeat, p2: _VICTORY_SENTINEL}]


# Sentinel so our mock Result comparison works.
_VICTORY_SENTINEL = object()


@pytest.fixture()
def tmp_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up a minimal fake repo with v0 and v1 version dirs."""
    # Create bots/v0/ and bots/v1/ with VERSION files.
    for v in ("v0", "v1"):
        d = tmp_path / "bots" / v
        d.mkdir(parents=True)
        (d / "VERSION").write_text(v)
    # Create bots/current/current.txt
    cur = tmp_path / "bots" / "current"
    cur.mkdir(parents=True)
    (cur / "current.txt").write_text("v0")

    # Create data/ and logs/ directories.
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()

    # Redirect registry's _repo_root to our tmp_path.
    monkeypatch.setattr("orchestrator.registry._repo_root", lambda: tmp_path)
    monkeypatch.setattr("orchestrator.selfplay._repo_root", lambda: tmp_path)

    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunBatch:
    """Unit tests for :func:`run_batch` with mocked SC2 layer."""

    @patch("orchestrator.selfplay._install_port_collision_patch")
    @patch("orchestrator.selfplay._build_match")
    @patch("orchestrator.selfplay._run_single_game", new_callable=AsyncMock)
    def test_batch_produces_correct_count(
        self,
        mock_run: AsyncMock,
        mock_build: MagicMock,
        mock_patch: MagicMock,
        tmp_repo: Path,
    ) -> None:
        """A batch of 4 games produces 4 SelfPlayRecords."""
        # Mock _build_match to return (match, seat_swap).
        mock_build.side_effect = [
            (MagicMock(), False),
            (MagicMock(), True),
            (MagicMock(), False),
            (MagicMock(), True),
        ]
        # Mock _run_single_game to return None (draw).
        mock_run.return_value = None

        results_path = tmp_repo / "data" / "selfplay_results.jsonl"
        records = run_batch("v0", "v1", 4, "Simple64", results_path=results_path)

        assert len(records) == 4
        # Check JSONL file has 4 lines.
        lines = results_path.read_text().strip().split("\n")
        assert len(lines) == 4

    @patch("orchestrator.selfplay._install_port_collision_patch")
    @patch("orchestrator.selfplay._build_match")
    @patch("orchestrator.selfplay._run_single_game", new_callable=AsyncMock)
    def test_seats_alternate(
        self,
        mock_run: AsyncMock,
        mock_build: MagicMock,
        mock_patch: MagicMock,
        tmp_repo: Path,
    ) -> None:
        """Even-indexed games: no swap; odd-indexed: swapped."""
        mock_build.side_effect = [
            (MagicMock(), False),  # game 0: no swap
            (MagicMock(), True),   # game 1: swap
            (MagicMock(), False),  # game 2: no swap
            (MagicMock(), True),   # game 3: swap
        ]
        mock_run.return_value = None

        records = run_batch(
            "v0", "v1", 4, "Simple64",
            results_path=tmp_repo / "data" / "selfplay_results.jsonl",
        )

        assert records[0].seat_swap is False
        assert records[1].seat_swap is True
        assert records[2].seat_swap is False
        assert records[3].seat_swap is True

        # When not swapped: p1=v0, p2=v1.
        assert records[0].p1_version == "v0"
        assert records[0].p2_version == "v1"
        # When swapped: p1=v1, p2=v0.
        assert records[1].p1_version == "v1"
        assert records[1].p2_version == "v0"

    @patch("orchestrator.selfplay._install_port_collision_patch")
    @patch("orchestrator.selfplay._build_match")
    @patch("orchestrator.selfplay._run_single_game", new_callable=AsyncMock)
    def test_results_are_valid_json(
        self,
        mock_run: AsyncMock,
        mock_build: MagicMock,
        mock_patch: MagicMock,
        tmp_repo: Path,
    ) -> None:
        """Each JSONL line is a valid SelfPlayRecord."""
        mock_build.return_value = (MagicMock(), False)
        mock_run.return_value = None

        results_path = tmp_repo / "data" / "selfplay_results.jsonl"
        run_batch("v0", "v1", 2, "Simple64", results_path=results_path)

        lines = results_path.read_text().strip().split("\n")
        for line in lines:
            record = SelfPlayRecord.from_json(line)
            assert record.map_name == "Simple64"
            assert record.p1_version in ("v0", "v1")
            assert record.p2_version in ("v0", "v1")
            assert record.timestamp  # non-empty ISO string

    @patch("orchestrator.selfplay._install_port_collision_patch")
    def test_unknown_version_rejected(
        self,
        mock_patch: MagicMock,
        tmp_repo: Path,
    ) -> None:
        """run_batch rejects versions not in the registry."""
        with pytest.raises(ValueError, match="v99.*not found"):
            run_batch("v0", "v99", 1, "Simple64")

    @patch("orchestrator.selfplay._install_port_collision_patch")
    @patch("orchestrator.selfplay._build_match")
    @patch("orchestrator.selfplay._run_single_game", new_callable=AsyncMock)
    def test_null_result_recorded_as_draw(
        self,
        mock_run: AsyncMock,
        mock_build: MagicMock,
        mock_patch: MagicMock,
        tmp_repo: Path,
    ) -> None:
        """When burnysc2 returns None, winner is None (draw)."""
        mock_build.return_value = (MagicMock(), False)
        mock_run.return_value = None

        records = run_batch(
            "v0", "v1", 1, "Simple64",
            results_path=tmp_repo / "data" / "selfplay_results.jsonl",
        )
        assert records[0].winner is None
        assert records[0].error is None


class TestCrashHandling:
    """Tests for crash/timeout handling in run_batch."""

    @patch("orchestrator.selfplay._kill_sc2")
    @patch("orchestrator.selfplay._install_port_collision_patch")
    @patch("orchestrator.selfplay._build_match")
    @patch("orchestrator.selfplay._run_single_game", new_callable=AsyncMock)
    def test_timeout_recorded_as_error(
        self,
        mock_run: AsyncMock,
        mock_build: MagicMock,
        mock_patch: MagicMock,
        mock_kill: MagicMock,
        tmp_repo: Path,
    ) -> None:
        """TimeoutError → record with error set, winner=None."""
        mock_build.return_value = (MagicMock(), False)
        mock_run.side_effect = TimeoutError("timed out")

        records = run_batch(
            "v0", "v1", 1, "Simple64",
            results_path=tmp_repo / "data" / "selfplay_results.jsonl",
        )
        assert len(records) == 1
        assert records[0].winner is None
        assert records[0].error is not None
        assert "timeout" in records[0].error
        mock_kill.assert_called_once()

    @patch("orchestrator.selfplay._kill_sc2")
    @patch("orchestrator.selfplay._install_port_collision_patch")
    @patch("orchestrator.selfplay._build_match")
    @patch("orchestrator.selfplay._run_single_game", new_callable=AsyncMock)
    def test_exception_recorded_as_error(
        self,
        mock_run: AsyncMock,
        mock_build: MagicMock,
        mock_patch: MagicMock,
        mock_kill: MagicMock,
        tmp_repo: Path,
    ) -> None:
        """Generic exception → record with error string, winner=None."""
        mock_build.return_value = (MagicMock(), False)
        mock_run.side_effect = RuntimeError("SC2 crashed")

        records = run_batch(
            "v0", "v1", 1, "Simple64",
            results_path=tmp_repo / "data" / "selfplay_results.jsonl",
        )
        assert len(records) == 1
        assert records[0].winner is None
        assert records[0].error == "SC2 crashed"
        mock_kill.assert_called_once()

    @patch("orchestrator.selfplay._kill_sc2")
    @patch("orchestrator.selfplay._install_port_collision_patch")
    @patch("orchestrator.selfplay._build_match")
    @patch("orchestrator.selfplay._run_single_game", new_callable=AsyncMock)
    def test_batch_continues_after_crash(
        self,
        mock_run: AsyncMock,
        mock_build: MagicMock,
        mock_patch: MagicMock,
        mock_kill: MagicMock,
        tmp_repo: Path,
    ) -> None:
        """One crashed game doesn't abort the batch."""
        mock_build.side_effect = [
            (MagicMock(), False),
            (MagicMock(), True),
            (MagicMock(), False),
        ]
        # Game 1 crashes, games 2 and 3 succeed.
        mock_run.side_effect = [
            RuntimeError("boom"),
            None,  # draw
            None,  # draw
        ]

        records = run_batch(
            "v0", "v1", 3, "Simple64",
            results_path=tmp_repo / "data" / "selfplay_results.jsonl",
        )
        assert len(records) == 3
        assert records[0].error == "boom"
        assert records[1].error is None
        assert records[2].error is None

    @patch("orchestrator.selfplay._kill_sc2")
    @patch("orchestrator.selfplay._install_port_collision_patch")
    @patch("orchestrator.selfplay._build_match")
    @patch("orchestrator.selfplay._run_single_game", new_callable=AsyncMock)
    def test_crash_results_written_to_jsonl(
        self,
        mock_run: AsyncMock,
        mock_build: MagicMock,
        mock_patch: MagicMock,
        mock_kill: MagicMock,
        tmp_repo: Path,
    ) -> None:
        """Crash records are persisted to the JSONL file."""
        mock_build.return_value = (MagicMock(), False)
        mock_run.side_effect = TimeoutError("hung")

        results_path = tmp_repo / "data" / "selfplay_results.jsonl"
        run_batch("v0", "v1", 1, "Simple64", results_path=results_path)

        lines = results_path.read_text().strip().split("\n")
        assert len(lines) == 1
        record = SelfPlayRecord.from_json(lines[0])
        assert record.error is not None


class TestSelfPlayCLI:
    """Argparse tests for scripts/selfplay.py."""

    def test_h2h_parse(self) -> None:
        """Head-to-head mode parses correctly."""
        # Import build_parser from the script.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "selfplay_cli",
            str(Path(__file__).resolve().parent.parent / "scripts" / "selfplay.py"),
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        parser = mod.build_parser()
        args = parser.parse_args(["--p1", "v0", "--p2", "v1", "--games", "10"])
        assert args.p1 == "v0"
        assert args.p2 == "v1"
        assert args.games == 10
        assert args.map == "Simple64"

    def test_pfsp_parse(self) -> None:
        """PFSP mode parses correctly."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "selfplay_cli",
            str(Path(__file__).resolve().parent.parent / "scripts" / "selfplay.py"),
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        parser = mod.build_parser()
        args = parser.parse_args([
            "--sample", "pfsp", "--pool", "v0,v1,v2",
            "--games", "40", "--map", "CatalystLE",
        ])
        assert args.sample == "pfsp"
        assert args.pool == "v0,v1,v2"
        assert args.games == 40
        assert args.map == "CatalystLE"


class TestSelfPlayRecordContract:
    """Verify SelfPlayRecord JSON round-trip."""

    def test_round_trip(self) -> None:
        record = SelfPlayRecord(
            match_id="abc-123",
            p1_version="v0",
            p2_version="v1",
            winner="v0",
            map_name="Simple64",
            duration_s=22.5,
            seat_swap=False,
            timestamp="2026-04-16T12:00:00+00:00",
            error=None,
        )
        restored = SelfPlayRecord.from_json(record.to_json())
        assert restored == record

    def test_round_trip_with_error(self) -> None:
        record = SelfPlayRecord(
            match_id="def-456",
            p1_version="v0",
            p2_version="v1",
            winner=None,
            map_name="Simple64",
            duration_s=600.0,
            seat_swap=True,
            timestamp="2026-04-16T12:00:00+00:00",
            error="timeout after 600s",
        )
        restored = SelfPlayRecord.from_json(record.to_json())
        assert restored == record

    def test_jsonl_line_is_single_line(self) -> None:
        record = SelfPlayRecord(
            match_id="ghi-789",
            p1_version="v0",
            p2_version="v1",
            winner="v1",
            map_name="Simple64",
            duration_s=30.0,
            seat_swap=False,
            timestamp="2026-04-16T12:00:00+00:00",
        )
        line = record.to_json()
        assert "\n" not in line
        parsed = json.loads(line)
        assert parsed["winner"] == "v1"


class TestWorkerThreadSignalPatch:
    """Regression for the viewer-owned-batch bug.

    ``SC2Process.__aenter__`` calls ``signal.signal(SIGINT, ...)`` which
    raises ``ValueError`` off-main-thread. Step 4's
    ``SelfPlayViewer.run_with_batch`` puts the batch on a worker thread,
    so unpatched burnysc2 fails every game in ~10ms before SC2 spawns.
    """

    def test_patch_allows_signal_call_from_worker_thread(self) -> None:
        import signal
        import threading

        from orchestrator.selfplay import _install_worker_thread_signal_patch

        _install_worker_thread_signal_patch()

        import sc2.sc2process as sc2p

        errors: list[BaseException] = []

        def worker() -> None:
            try:
                sc2p.signal.signal(sc2p.signal.SIGINT, signal.SIG_DFL)
                sc2p.signal.signal(sc2p.signal.SIGINT, signal.SIG_DFL)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=worker, name="selfplay-batch")
        t.start()
        t.join()

        assert not errors, f"signal proxy raised in worker thread: {errors!r}"

    def test_patch_preserves_main_thread_behaviour(self) -> None:
        import signal

        from orchestrator.selfplay import _install_worker_thread_signal_patch

        _install_worker_thread_signal_patch()

        import sc2.sc2process as sc2p

        prev = sc2p.signal.signal(sc2p.signal.SIGINT, signal.SIG_DFL)
        # Real signal.signal returns the previous handler — either a
        # callable or one of the SIG_* constants. ``None`` would mean the
        # proxy short-circuited on the main thread, which is the bug.
        assert prev is not None
        # Restore default so the test leaves no side effects.
        sc2p.signal.signal(sc2p.signal.SIGINT, prev)


# ---------------------------------------------------------------------------
# ES.14: every version launches through the SC2 compatibility launcher
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_LAUNCHER = _REPO / "scripts" / "compat_launch.py"


class TestCompatLaunch:
    """Self-play launches each version as ``[python, compat_launch.py, <v>, ...]``."""

    @pytest.mark.parametrize("game_index", [0, 1])
    def test_build_match_launches_every_version_through_the_wrapper(
        self, tmp_path: Path, game_index: int
    ) -> None:
        import sys

        from orchestrator.selfplay import _build_match

        with patch("sc2.maps.get", return_value=MagicMock(name="map")):
            match, seat_swap = _build_match(
                "v0", "v13", "Simple64", game_index, tmp_path, 3600, None
            )

        expected = ["v13", "v0"] if seat_swap else ["v0", "v13"]
        assert seat_swap is (game_index == 1)
        assert len(match.players) == 2
        for player, version, role in zip(match.players, expected, ("p1", "p2"), strict=True):
            launch = player.launch_list
            assert launch[:3] == [sys.executable, str(_LAUNCHER.resolve()), version]
            assert Path(launch[1]).is_absolute()
            assert Path(launch[1]).is_file()
            assert "-m" not in launch
            # The original bot arguments follow unchanged.
            assert launch[3:] == [
                "--role",
                role,
                "--map",
                "Simple64",
                "--result-out",
                str(tmp_path / f"game{game_index}_{role}.json"),
            ]
            # The child still starts in the repo root, as the -m launch did.
            assert Path(player.path) == _REPO

    def test_shim_is_installed_before_bots_v0_entry_runs(self, tmp_path: Path) -> None:
        """Run the real wrapper on bots.v0 with argparse stubbed so no game starts.

        The stub replaces ``ArgumentParser.parse_args`` (the first thing
        ``bots.v0.__main__.main`` does), records whether the shim was installed
        at that moment and which module called it, then exits. The working
        directory is a temp dir to prove the wrapper does not rely on cwd.
        """
        import json as _json
        import subprocess
        import sys

        out = tmp_path / "probe.json"
        probe = f"""
import argparse, json, runpy, sys
from sc2.game_data import GameData
before = bool(getattr(GameData.__init__, "_alpha4gate_sc2_compat", False))
def _stub(self, args=None, namespace=None):
    from orchestrator import sc2_compat
    caller = sys._getframe(1).f_globals
    spec = caller.get("__spec__")
    with open({str(out)!r}, "w", encoding="utf-8") as fh:
        json.dump({{
            "before": before,
            "installed": sc2_compat.is_installed(),
            "caller": spec.name if spec else None,
            "caller_name": caller.get("__name__"),
            "args": list(args) if args is not None else None,
            "argv": sys.argv[1:],
        }}, fh)
    raise SystemExit(0)
argparse.ArgumentParser.parse_args = _stub
sys.argv = [{str(_LAUNCHER)!r}, "v0", "--role", "p1", "--map", "Simple64"]
runpy.run_path({str(_LAUNCHER)!r}, run_name="__main__")
"""
        done = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert done.returncode == 0, done.stderr
        seen = _json.loads(out.read_text(encoding="utf-8"))
        assert seen["before"] is False
        assert seen["installed"] is True
        assert seen["caller"] == "bots.v0.__main__"
        assert seen["caller_name"] == "__main__"
        assert seen["args"] == ["--role", "p1", "--map", "Simple64"]
        assert seen["argv"] == ["--role", "p1", "--map", "Simple64"]

    def test_wrapper_runs_bots_v0_help_from_any_cwd(self, tmp_path: Path) -> None:
        """No-SC2 path: ``--help`` reaches bots.v0's own parser and exits 0."""
        import subprocess
        import sys

        done = subprocess.run(
            [sys.executable, str(_LAUNCHER), "v0", "--help"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert done.returncode == 0, done.stderr
        assert "--role" in done.stdout
        assert "--result-out" in done.stdout

    @pytest.mark.parametrize("argv", [[], ["bots.v0"], ["../v0"]])
    def test_wrapper_rejects_a_missing_or_dotted_version(self, argv: list[str]) -> None:
        import subprocess
        import sys

        done = subprocess.run(
            [sys.executable, str(_LAUNCHER), *argv],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert done.returncode == 2
        assert "usage" in done.stderr


def test_orchestrator_imports_no_bots_package() -> None:
    """src/orchestrator/ must not import bots.* (MetaPathFinder loop)."""
    import ast

    offenders: list[str] = []
    for path in sorted((_REPO / "src" / "orchestrator").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute | ast.Name)
                and (node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id)
                in ("import_module", "__import__")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names = [node.args[0].value]
            for name in names:
                if name == "bots" or name.startswith("bots."):
                    offenders.append(f"{path.relative_to(_REPO)}:{node.lineno}: {name}")
    assert offenders == []
