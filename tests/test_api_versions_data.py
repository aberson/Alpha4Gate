"""Contract tests for the Models tab Step 1b per-version data endpoints.

Covers:

* ``GET /api/versions/{v}/training-history`` — rolling-WR series shape,
  rolling_10/50 windows, missing-DB → empty skeleton.
* ``GET /api/versions/{v}/actions`` — histogram shape + sort + empty.
* ``GET /api/versions/{v}/improvements`` — files_changed-derived
  filtering: ``bots/vN/`` direct match; ``bots/current/...`` resolved
  via mocked ``git show``; malformed sha skipped without crash.
* Malformed ``v`` returns 400 for all three.

The fixture redirects ``_REPO_ROOT`` to a ``tmp_path`` root so we can
stage controlled ``bots/`` trees + cross-version ``data/`` files. The
git-show subprocess is patched on a per-test basis with
``monkeypatch.setattr(subprocess, "run", ...)`` so the test never
actually shells out.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from bots.v10 import api as v10_api
from bots.v10.api import app, configure
from bots.v10.error_log import get_error_log_buffer
from bots.v10.learning.database import TrainingDB
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def staged_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Stage a fake repo root with ``bots/`` + ``data/`` and patch the API.

    Mirrors the fixture from ``test_api_versions.py`` (Step 1a) — same
    pattern keeps the two test files producing equivalent staged trees
    even though the data classes under test differ.
    """
    bots_dir = tmp_path / "bots"
    bots_dir.mkdir()
    (bots_dir / "current").mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(v10_api, "_REPO_ROOT", tmp_path)
    yield tmp_path


@pytest.fixture()
def client(staged_repo: Path, tmp_path: Path) -> TestClient:
    """Test client wired to the staged repo."""
    log_dir = tmp_path / "logs"
    replay_dir = tmp_path / "replays"
    log_dir.mkdir()
    replay_dir.mkdir()
    per_version_data = staged_repo / "bots" / "v0" / "data"
    per_version_data.mkdir(parents=True, exist_ok=True)
    configure(per_version_data, log_dir, replay_dir)
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_error_buffer() -> Iterator[None]:
    """Reset the process-wide error log buffer between tests."""
    get_error_log_buffer().reset()
    yield
    get_error_log_buffer().reset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_training_db(
    repo: Path,
    version: str,
    games: list[tuple[str, str]],
    timestamps: list[str] | None = None,
) -> Path:
    """Stage a ``training.db`` with synthetic ``games`` rows for ``version``.

    ``games`` is a list of ``(game_id, result)`` tuples. ``result`` is
    one of ``"win"`` / ``"loss"``. Rows are inserted in order; the
    ``created_at`` default DOES NOT give us a stable monotonic
    timestamp (rows can land in the same millisecond), so we override
    it to a fixed pattern by using ``store_game`` and then patching
    ``created_at`` post-insert. ``timestamps`` lets a test pin explicit
    per-game ``created_at`` values for chronological-order assertions.
    """
    db_dir = repo / "bots" / version / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "training.db"
    db = TrainingDB(db_path)
    for i, (game_id, result) in enumerate(games):
        db.store_game(
            game_id=game_id,
            map_name="Simple64",
            difficulty=3,
            result=result,
            duration_secs=120.0,
            total_reward=0.0,
            model_version=version,
        )
        # Force monotonic ``created_at`` so ORDER BY is deterministic
        # across SQLite implementations that share the same datetime()
        # millisecond.
        if timestamps is not None:
            ts = timestamps[i]
        else:
            ts = f"2026-04-30 {12 + i // 60:02d}:{i % 60:02d}:00"
        db._conn.execute(  # noqa: SLF001 — test-only direct write
            "UPDATE games SET created_at = ? WHERE game_id = ?",
            (ts, game_id),
        )
    db._conn.commit()  # noqa: SLF001
    db.close()
    return db_path


def _add_transitions(
    repo: Path,
    version: str,
    game_id: str,
    actions: list[int],
) -> None:
    """Append ``actions`` to ``transitions`` table for ``game_id``."""
    import numpy as np

    db_path = repo / "bots" / version / "data" / "training.db"
    db = TrainingDB(db_path)
    state = np.zeros(40, dtype=np.float32)
    for step_index, action in enumerate(actions):
        db.store_transition(
            game_id=game_id,
            step_index=step_index,
            game_time=float(step_index),
            state=state,
            action=action,
            reward=0.0,
        )
    db.close()


def _write_unified_files(
    repo: Path,
    advised: list[dict[str, Any]] | None = None,
    evolve_rows: list[dict[str, Any]] | None = None,
) -> None:
    """Stage cross-version ``improvement_log.json`` + ``evolve_results.jsonl``.

    Either argument may be ``None`` (skip writing that file). Empty
    lists DO write the file with an empty entries array — useful for
    tests that need the file present but no rows.
    """
    if advised is not None:
        path = repo / "data" / "improvement_log.json"
        path.write_text(
            json.dumps({"improvements": advised}), encoding="utf-8"
        )
    if evolve_rows is not None:
        path = repo / "data" / "evolve_results.jsonl"
        path.write_text(
            "\n".join(json.dumps(r) for r in evolve_rows) + "\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# /api/versions/{v}/training-history
# ---------------------------------------------------------------------------


class TestVersionTrainingHistoryEndpoint:
    def test_missing_db_returns_empty_skeleton(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        resp = client.get("/api/versions/v0/training-history")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "rolling_10": [],
            "rolling_50": [],
            "rolling_overall": [],
        }

    def test_db_with_no_games_for_version_returns_empty(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # DB exists but only has rows for v0; we query v3.
        _make_training_db(staged_repo, "v0", [("g1", "win"), ("g2", "loss")])
        # Stage a separate empty DB for v3 to confirm the SELECT ... WHERE
        # model_version = ? path is hit, not just the file-existence check.
        _make_training_db(staged_repo, "v3", [])
        resp = client.get("/api/versions/v3/training-history")
        assert resp.status_code == 200
        assert resp.json() == {
            "rolling_10": [],
            "rolling_50": [],
            "rolling_overall": [],
        }

    def test_overall_rolling_wr_is_running_average(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # 4 games: W, L, W, W → cumulative WR 1.0, 0.5, 0.66.., 0.75
        _make_training_db(
            staged_repo,
            "v0",
            [("g1", "win"), ("g2", "loss"), ("g3", "win"), ("g4", "win")],
        )
        resp = client.get("/api/versions/v0/training-history")
        body = resp.json()
        wrs = [round(p["wr"], 4) for p in body["rolling_overall"]]
        assert wrs == [1.0, 0.5, round(2 / 3, 4), 0.75]
        # Rolling-10 / rolling-50 require >=10 / >=50 games respectively.
        assert body["rolling_10"] == []
        assert body["rolling_50"] == []

    def test_rolling_10_window_emits_only_when_threshold_met(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # 10 games: alternating W/L → cumulative WR over the window is
        # 1, 0.5, 0.66.., 0.5, 0.6, 0.5, 0.5714, 0.5, 0.5555, 0.5
        results = ["win" if i % 2 == 0 else "loss" for i in range(10)]
        _make_training_db(
            staged_repo, "v0",
            [(f"g{i}", r) for i, r in enumerate(results)],
        )
        resp = client.get("/api/versions/v0/training-history")
        body = resp.json()
        assert len(body["rolling_10"]) == 10
        # Every entry has the expected keys.
        for point in body["rolling_10"]:
            assert set(point.keys()) == {"game_id", "ts", "wr"}
        # Final rolling-WR over 10 alternating games is 0.5.
        assert body["rolling_10"][-1]["wr"] == pytest.approx(0.5)
        # rolling_50 still empty (only 10 games).
        assert body["rolling_50"] == []

    def test_rolling_50_window_emits_only_for_50_games(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # 60 games: 40 wins then 20 losses → tail of 50 covers
        # the last 30 wins and 20 losses → cumulative wins / cumulative
        # games over the window terminates at 30/50 = 0.6.
        games: list[tuple[str, str]] = []
        for i in range(40):
            games.append((f"w{i}", "win"))
        for i in range(20):
            games.append((f"l{i}", "loss"))
        _make_training_db(staged_repo, "v0", games)
        resp = client.get("/api/versions/v0/training-history")
        body = resp.json()
        assert len(body["rolling_50"]) == 50
        # Final point's WR over the 50-game tail.
        assert body["rolling_50"][-1]["wr"] == pytest.approx(30 / 50)

    def test_rolling_overall_is_chronological_oldest_first(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """``rolling_overall`` must order points oldest-first.

        The endpoint sorts by ``created_at`` ASC; the frontend relies on
        this for left-to-right time-axis rendering. We seed games in
        non-chronological insert order with explicit ``created_at``
        values and assert the response sorts them ascending.
        """
        # Insert order intentionally NOT matching chronology: g_late is
        # inserted first but pinned to 12:00 (newest); g_early second at
        # 10:00 (oldest); g_mid last at 11:00. Output ordering must be
        # chronological (g_early, g_mid, g_late), not insert-order.
        _make_training_db(
            staged_repo,
            "v3",
            [("g_late", "win"), ("g_early", "loss"), ("g_mid", "win")],
            timestamps=[
                "2026-04-01 12:00:00",
                "2026-04-01 10:00:00",
                "2026-04-01 11:00:00",
            ],
        )
        body = client.get("/api/versions/v3/training-history").json()
        rolling_ts = [p["ts"] for p in body["rolling_overall"]]
        assert rolling_ts == sorted(rolling_ts), (
            "rolling_overall must be chronologically ascending"
        )
        rolling_ids = [p["game_id"] for p in body["rolling_overall"]]
        assert rolling_ids == ["g_early", "g_mid", "g_late"]


# ---------------------------------------------------------------------------
# /api/versions/{v}/actions
# ---------------------------------------------------------------------------


class TestVersionActionsEndpoint:
    def test_missing_db_returns_empty_list(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        resp = client.get("/api/versions/v0/actions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_histogram_sorted_by_count_desc(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _make_training_db(staged_repo, "v0", [("g1", "win")])
        # 5x action 0, 3x action 2, 1x action 1
        _add_transitions(staged_repo, "v0", "g1", [0, 0, 0, 0, 0, 2, 2, 2, 1])
        resp = client.get("/api/versions/v0/actions")
        rows = resp.json()
        assert isinstance(rows, list)
        assert [r["action_id"] for r in rows] == [0, 2, 1]
        assert [r["count"] for r in rows] == [5, 3, 1]
        # Names from ACTION_TO_STATE: 0='opening', 2='attack', 1='expand'.
        names = {r["action_id"]: r["name"] for r in rows}
        assert names[0] == "opening"
        assert names[1] == "expand"
        assert names[2] == "attack"
        # pct sums to ~1.
        total_pct = sum(r["pct"] for r in rows)
        assert total_pct == pytest.approx(1.0)
        # Response shape: every row has the four expected keys.
        assert set(rows[0].keys()) == {"action_id", "name", "count", "pct"}

    def test_only_counts_target_version_transitions(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _make_training_db(staged_repo, "v0", [("g0", "win")])
        _make_training_db(staged_repo, "v3", [("g3", "win")])
        _add_transitions(staged_repo, "v0", "g0", [0, 0, 0])
        _add_transitions(staged_repo, "v3", "g3", [1, 2])
        # Each DB lives in its own version dir, so v0/actions only sees
        # rows joined against v0's own games table.
        rows_v0 = client.get("/api/versions/v0/actions").json()
        assert len(rows_v0) == 1
        assert rows_v0[0]["action_id"] == 0
        assert rows_v0[0]["count"] == 3
        rows_v3 = client.get("/api/versions/v3/actions").json()
        action_ids_v3 = {r["action_id"] for r in rows_v3}
        assert action_ids_v3 == {1, 2}

    def test_unknown_action_id_falls_back_to_action_n(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _make_training_db(staged_repo, "v0", [("g1", "win")])
        # Action id 99 is out of range — the resolver must fall back.
        _add_transitions(staged_repo, "v0", "g1", [99])
        rows = client.get("/api/versions/v0/actions").json()
        assert rows[0]["name"] == "action_99"

    def test_blob_action_values_decoded(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """Historical rows store ``action`` as np.int64.tobytes() blobs.

        Per memory ``feedback_sqlite_numpy_blob.md``, sqlite3's default
        adapter writes numpy int64 scalars as BLOB. The endpoint's
        defensive bytes/memoryview decoder must decode them; otherwise
        GROUP BY treats the same logical id as multiple keys.
        """
        _make_training_db(staged_repo, "v0", [("g1", "win")])
        # Insert one row via the normal INTEGER path, plus three rows
        # whose ``action`` column carries an 8-byte little-endian blob
        # encoding action id 5 (matching np.int64(5).tobytes()).
        _add_transitions(staged_repo, "v0", "g1", [5])
        db_path = staged_repo / "bots" / "v0" / "data" / "training.db"
        conn = sqlite3.connect(str(db_path))
        try:
            blob_action = struct.pack("<q", 5)
            for step in range(1, 4):
                conn.execute(
                    "INSERT INTO transitions ("
                    "game_id, step_index, game_time, "
                    "supply_used, supply_cap, minerals, vespene, "
                    "army_supply, worker_count, base_count, "
                    "enemy_near, enemy_supply, action, reward"
                    ") VALUES (?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?, 0.0)",
                    ("g1", step, float(step), blob_action),
                )
            conn.commit()
        finally:
            conn.close()
        rows = client.get("/api/versions/v0/actions").json()
        # All four rows (1 INTEGER + 3 BLOB) should bucket into action_id 5.
        assert len(rows) == 1
        assert rows[0]["action_id"] == 5
        assert rows[0]["count"] == 4


# ---------------------------------------------------------------------------
# /api/versions/{v}/improvements
# ---------------------------------------------------------------------------


def _stub_git_show(
    sha_to_version: dict[str, str | None],
) -> Any:
    """Build a ``subprocess.run`` stub that maps SHAs to current.txt content.

    Each entry maps a sha → either the version string (e.g. ``"v3"``)
    or ``None`` (simulate ``git show`` failure with non-zero exit).
    Anything not in the map raises so tests fail loudly on unexpected
    invocations.
    """

    def _run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        # First positional arg is the cmd list.
        cmd = args[0]
        assert isinstance(cmd, list), "subprocess.run must be called with list-form"
        assert cmd[0] == "git" and cmd[1] == "show"
        assert kwargs.get("shell", False) is False
        assert kwargs.get("timeout") == 5
        # cmd[2] looks like ``"<sha>:bots/current/current.txt"``.
        sha_token = cmd[2].split(":", 1)[0]
        if sha_token not in sha_to_version:
            msg = f"Unexpected git-show invocation for sha {sha_token!r}"
            raise AssertionError(msg)
        result = sha_to_version[sha_token]
        if result is None:
            return subprocess.CompletedProcess(
                args=cmd, returncode=128, stdout="", stderr="fatal: bad object"
            )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=result + "\n", stderr=""
        )

    return _run


class TestVersionImprovementsEndpoint:
    def test_no_log_files_returns_empty(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        resp = client.get("/api/versions/v3/improvements")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_advised_entry_attributed_via_bots_vN_path(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _write_unified_files(
            staged_repo,
            advised=[
                {
                    "id": "advised-X-iter1",
                    "title": "tune mineral floats",
                    "type": "training",
                    "result": "pass",
                    "files_changed": ["bots/v3/decision_engine.py"],
                    "timestamp": "2026-04-25T10:00:00Z",
                },
                {
                    "id": "advised-Y-iter1",
                    "title": "unrelated v0 change",
                    "type": "training",
                    "result": "pass",
                    "files_changed": ["bots/v0/macro_manager.py"],
                    "timestamp": "2026-04-25T11:00:00Z",
                },
            ],
        )
        rows = client.get("/api/versions/v3/improvements").json()
        assert len(rows) == 1
        entry = rows[0]
        assert entry["id"] == "advised-X-iter1"
        assert entry["source"] == "advised"
        assert entry["files_changed"] == ["bots/v3/decision_engine.py"]
        # Internal bookkeeping field must NOT leak.
        assert "_commit_sha" not in entry

    def test_evolve_rollup_attributed_via_files_touched(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _write_unified_files(
            staged_repo,
            evolve_rows=[
                {
                    "phase": "stack_apply",
                    "generation": 5,
                    "parent": "v0",
                    "imp": {
                        "rank": 1,
                        "title": "Splash readiness gate",
                        "type": "dev",
                        "files_touched": ["bots/v3/macro_manager.py"],
                    },
                    "candidate": "cand_abc12345",
                    "wins_cand": 4,
                    "wins_parent": 1,
                    "outcome": "stack-apply-pass",
                    "timestamp": "2026-04-26T12:00:00Z",
                },
            ],
        )
        rows = client.get("/api/versions/v3/improvements").json()
        assert len(rows) == 1
        entry = rows[0]
        assert entry["source"] == "evolve"
        assert entry["title"] == "Splash readiness gate"
        assert entry["files_changed"] == ["bots/v3/macro_manager.py"]
        # Internal bookkeeping field must NOT leak (parity with advised entry).
        assert "_commit_sha" not in entry

    def test_bots_current_path_resolved_via_mocked_git_show(
        self,
        client: TestClient,
        staged_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``bots/current/...`` paths resolve via ``git show <sha>:...``.

        We mock ``subprocess.run`` so the test never actually shells out.
        Two SHAs: one resolves to ``v3`` (entry should match a v3 query),
        one resolves to ``v0`` (entry should NOT match a v3 query).
        """
        sha_v3 = "abc123def456abc123def456abc123def456abc1"  # 40-char hex
        sha_v0 = "0123456789abcdef0123456789abcdef01234567"
        monkeypatch.setattr(
            v10_api.subprocess,
            "run",
            _stub_git_show({sha_v3: "v3", sha_v0: "v0"}),
        )
        _write_unified_files(
            staged_repo,
            advised=[
                {
                    "id": "advised-current-v3",
                    "title": "patched via bots/current",
                    "type": "training",
                    "result": "pass",
                    "files_changed": ["bots/current/decision_engine.py"],
                    "git_sha": sha_v3,
                    "timestamp": "2026-04-27T10:00:00Z",
                },
                {
                    "id": "advised-current-v0",
                    "title": "patched via bots/current (v0 era)",
                    "type": "training",
                    "result": "pass",
                    "files_changed": ["bots/current/decision_engine.py"],
                    "git_sha": sha_v0,
                    "timestamp": "2026-04-27T11:00:00Z",
                },
            ],
        )
        rows = client.get("/api/versions/v3/improvements").json()
        ids = [r["id"] for r in rows]
        assert ids == ["advised-current-v3"]

    def test_malformed_sha_skipped_other_entries_continue(
        self,
        client: TestClient,
        staged_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A malformed sha must NOT 500 the endpoint.

        The entry with the malformed sha is skipped (warning logged);
        sibling entries continue to flow through. We patch ``subprocess.run``
        to assert it's never invoked with the malformed sha — validation
        must short-circuit before the subprocess call. ``caplog`` captures
        the WARNING the resolver emits when ``_validate_sha`` raises.
        """
        sha_good = "deadbeef0000000000000000000000000000beef"
        bad_sha = "; rm -rf /"
        seen: list[str] = []

        def _run(
            cmd: list[str], **_kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            sha_token = cmd[2].split(":", 1)[0]
            seen.append(sha_token)
            if sha_token == sha_good:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="v3\n", stderr=""
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=128, stdout="", stderr="fatal"
            )

        monkeypatch.setattr(v10_api.subprocess, "run", _run)
        _write_unified_files(
            staged_repo,
            advised=[
                {
                    "id": "advised-malformed",
                    "title": "malformed sha entry",
                    "type": "training",
                    "result": "pass",
                    "files_changed": ["bots/current/decision_engine.py"],
                    "git_sha": bad_sha,
                    "timestamp": "2026-04-27T10:00:00Z",
                },
                {
                    "id": "advised-ok",
                    "title": "good entry",
                    "type": "training",
                    "result": "pass",
                    "files_changed": ["bots/current/decision_engine.py"],
                    "git_sha": sha_good,
                    "timestamp": "2026-04-27T11:00:00Z",
                },
            ],
        )
        # No crash; the good entry comes through, malformed skipped.
        with caplog.at_level("WARNING", logger="bots.v10.api"):
            resp = client.get("/api/versions/v3/improvements")
        assert resp.status_code == 200
        rows = resp.json()
        ids = [r["id"] for r in rows]
        assert ids == ["advised-ok"]
        # Malformed sha was NEVER passed to subprocess.
        assert bad_sha not in seen
        # The skip emitted a WARNING — match the actual log message.
        assert any(
            "Skipping improvement entry" in rec.message
            for rec in caplog.records
        ), f"expected skip-warning in caplog; got {[r.message for r in caplog.records]!r}"

    def test_failed_git_show_skipped_silently(
        self,
        client: TestClient,
        staged_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-zero ``git show`` exit doesn't break the response."""
        sha_404 = "9999999999999999999999999999999999999999"
        monkeypatch.setattr(
            v10_api.subprocess,
            "run",
            _stub_git_show({sha_404: None}),
        )
        _write_unified_files(
            staged_repo,
            advised=[
                {
                    "id": "advised-missing-sha",
                    "title": "sha not in repo",
                    "type": "training",
                    "result": "pass",
                    "files_changed": ["bots/current/decision_engine.py"],
                    "git_sha": sha_404,
                    "timestamp": "2026-04-27T10:00:00Z",
                },
            ],
        )
        resp = client.get("/api/versions/v3/improvements")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_git_show_cache_avoids_duplicate_calls(
        self,
        client: TestClient,
        staged_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two entries with the same sha should hit ``git show`` once."""
        sha = "abc123def456abc123def456abc123def4561234"
        call_count = {"n": 0}

        def _run(
            cmd: list[str], **_kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            call_count["n"] += 1
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="v3\n", stderr=""
            )

        monkeypatch.setattr(v10_api.subprocess, "run", _run)
        _write_unified_files(
            staged_repo,
            advised=[
                {
                    "id": f"advised-{i}",
                    "title": f"entry {i}",
                    "type": "training",
                    "result": "pass",
                    "files_changed": ["bots/current/decision_engine.py"],
                    "git_sha": sha,
                    "timestamp": f"2026-04-27T1{i}:00:00Z",
                }
                for i in range(3)
            ],
        )
        rows = client.get("/api/versions/v3/improvements").json()
        assert len(rows) == 3
        # Exactly one subprocess call for three entries with the same sha.
        assert call_count["n"] == 1

    def test_sorted_by_timestamp_descending(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _write_unified_files(
            staged_repo,
            advised=[
                {
                    "id": "advised-old",
                    "files_changed": ["bots/v3/foo.py"],
                    "result": "pass",
                    "timestamp": "2026-04-20T10:00:00Z",
                },
                {
                    "id": "advised-new",
                    "files_changed": ["bots/v3/bar.py"],
                    "result": "pass",
                    "timestamp": "2026-04-25T10:00:00Z",
                },
            ],
        )
        rows = client.get("/api/versions/v3/improvements").json()
        assert [r["id"] for r in rows] == ["advised-new", "advised-old"]

    def test_ignores_legacy_data_paths(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """Entries whose files are all in legacy ``data/`` / ``src/``
        paths (not ``bots/<v>/``) don't attribute to any version."""
        _write_unified_files(
            staged_repo,
            advised=[
                {
                    "id": "advised-legacy",
                    "files_changed": ["data/reward_rules.json", "src/foo.py"],
                    "result": "pass",
                    "timestamp": "2026-04-25T10:00:00Z",
                },
            ],
        )
        rows = client.get("/api/versions/v3/improvements").json()
        assert rows == []


# ---------------------------------------------------------------------------
# Cross-endpoint version-validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint",
    ["training-history", "actions", "improvements"],
)
@pytest.mark.parametrize(
    "bad_v",
    [
        "V0",  # uppercase
        "v3a",  # trailing alpha
        "v;rm",  # injection
        "..%2Fv0",  # path traversal (encoded)
        "",  # empty (routes to a different URL → 404)
    ],
)
def test_malformed_version_returns_400_or_404(
    client: TestClient, endpoint: str, bad_v: str
) -> None:
    """All three per-version endpoints must reject malformed ``v``.

    Empty-string and ``..%2Fv0`` route through Starlette's normalization
    and may surface as 404 rather than 400; either is acceptable defense
    — what matters is that no 200 with leaked data flows through.
    """
    resp = client.get(f"/api/versions/{bad_v}/{endpoint}")
    assert resp.status_code in (400, 404), resp.text
    if resp.status_code == 400:
        assert "Invalid version" in resp.text or "must match" in resp.text


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestValidateShaHelper:
    """``_validate_sha`` is a separate helper from ``_validate_version``
    because malformed SHAs are handled by skip-and-warn at the call
    site (not 400'd)."""

    @pytest.mark.parametrize(
        "good",
        [
            "aabbccd",  # 7 chars (short SHA)
            "1234567890ab",  # 12 chars
            "a" * 40,  # full 40-char SHA
        ],
    )
    def test_accepts_well_formed_sha(self, good: str) -> None:
        assert v10_api._validate_sha(good) == good

    @pytest.mark.parametrize(
        "bad",
        [
            "abc1234; rm -rf /",  # injection (covers special chars / spaces)
            "ABCDEFG",  # uppercase (regex requires lowercase)
            "abcdef",  # too short (6 chars; min is 7)
        ],
    )
    def test_rejects_malformed_sha(self, bad: str) -> None:
        with pytest.raises(ValueError, match="Invalid git sha"):
            v10_api._validate_sha(bad)


class TestActionNameResolver:
    """``_action_name_for`` resolves PPO action ids to ``StrategicState``
    string values; falls back to ``action_N`` for out-of-range ids."""

    def test_known_ids(self) -> None:
        # ACTION_TO_STATE = [opening, expand, attack, defend, late_game, fortify]
        assert v10_api._action_name_for(0) == "opening"
        assert v10_api._action_name_for(2) == "attack"
        assert v10_api._action_name_for(5) == "fortify"

    def test_unknown_id_falls_back(self) -> None:
        assert v10_api._action_name_for(99) == "action_99"
        assert v10_api._action_name_for(-1) == "action_-1"
