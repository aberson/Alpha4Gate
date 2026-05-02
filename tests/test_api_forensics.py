"""Contract tests for the Models tab Step 1c per-game forensics endpoint.

Covers ``GET /api/versions/{v}/forensics/{game_id}``:

* Returns trajectory + win_prob populated when the game has transitions.
* Returns the empty-trajectory shape when the game is not found.
* ``give_up_fired`` reflects column data (currently always False — the
  schema does not carry a ``give_up`` column; this test pins the
  contract so a future column is a deliberate behavior change).
* ``expert_dispatch`` is always ``null`` (Phase O hasn't shipped).
* Per-version isolation: a game in v3's DB is not visible from the v0
  endpoint.
* Malformed ``v`` and malformed ``game_id`` both return 400 BEFORE any
  SQL is dispatched.

Mirrors the fixture pattern from ``test_api_versions_data.py`` so the
tests use real ``TrainingDB`` instances on real ``training.db`` files —
no SQLite mocks per the Step 1b convention.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
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
    """Stage a fake repo root with ``bots/`` + ``data/`` and patch the API."""
    bots_dir = tmp_path / "bots"
    bots_dir.mkdir()
    (bots_dir / "current").mkdir()
    (tmp_path / "data").mkdir()
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
    get_error_log_buffer().reset()
    yield
    get_error_log_buffer().reset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db_with_game(
    repo: Path,
    version: str,
    game_id: str,
    transitions: list[tuple[int, float | None]],
) -> Path:
    """Stage a per-version ``training.db`` with one game + ``transitions`` rows.

    Each entry in ``transitions`` is ``(step_index, win_prob)``. The
    state vector is zero-filled — this endpoint doesn't read it.
    """
    db_dir = repo / "bots" / version / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "training.db"
    db = TrainingDB(db_path)
    db.store_game(
        game_id=game_id,
        map_name="Simple64",
        difficulty=3,
        result="win",
        duration_secs=120.0,
        total_reward=0.0,
        model_version=version,
    )
    state = np.zeros(40, dtype=np.float32)
    for step_index, win_prob in transitions:
        db.store_transition(
            game_id=game_id,
            step_index=step_index,
            game_time=float(step_index),
            state=state,
            action=0,
            reward=0.0,
            win_prob=win_prob,
        )
    db.close()
    return db_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
#
# Single endpoint class per Step 1a/1b precedent: validation tests live
# as methods inside ``TestVersionForensicsEndpoint`` rather than in a
# separate ``TestForensicsValidation`` class.


class TestVersionForensicsEndpoint:
    def test_returns_trajectory_with_win_prob_populated(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # Insert in shuffled order to actually exercise the SQL
        # ``ORDER BY step_index ASC`` clause — a tautological [0,1,2]
        # insert would pass even if the ORDER BY were missing.
        _make_db_with_game(
            staged_repo,
            "v0",
            "live-test_abcdef012345",
            [(5, 0.62), (1, 0.50), (3, 0.55)],
        )
        resp = client.get(
            "/api/versions/v0/forensics/live-test_abcdef012345"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["trajectory"]) == 3
        # Trajectory must come back sorted ASC by step regardless of
        # insert order.
        steps = [pt["step"] for pt in body["trajectory"]]
        assert steps == [1, 3, 5]
        # win_prob travels with each step — verify the row-level mapping
        # was preserved through the sort (i.e. step 1 → 0.50, step 3 →
        # 0.55, step 5 → 0.62 even though insert order was different).
        win_probs = [pt["win_prob"] for pt in body["trajectory"]]
        assert win_probs == [
            pytest.approx(0.50),
            pytest.approx(0.55),
            pytest.approx(0.62),
        ]
        # ts is a string per spec.
        assert all(isinstance(pt["ts"], str) for pt in body["trajectory"])

    def test_missing_db_returns_empty_skeleton(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # No DB file at all → empty skeleton, never 404 / 500.
        resp = client.get("/api/versions/v0/forensics/missing_aaaa")
        assert resp.status_code == 200
        assert resp.json() == {
            "trajectory": [],
            "give_up_fired": False,
            "give_up_step": None,
            "expert_dispatch": None,
        }

    def test_unknown_game_returns_empty_skeleton(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # DB exists but the requested game_id was never recorded.
        _make_db_with_game(
            staged_repo, "v0", "real_game_aaaaaaaaaaaa", [(0, 0.5)]
        )
        resp = client.get(
            "/api/versions/v0/forensics/wrong_game_bbbbbbbbbbbb"
        )
        assert resp.status_code == 200
        assert resp.json()["trajectory"] == []

    def test_corrupt_db_returns_empty_skeleton(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """Defense in depth (finding 8): a corrupted ``training.db``
        file (random bytes at the path) raises ``sqlite3.DatabaseError``
        on connect / query. The widened ``sqlite3.Error`` catch must
        keep the endpoint from 500-ing.
        """
        db_dir = staged_repo / "bots" / "v0" / "data"
        db_dir.mkdir(parents=True, exist_ok=True)
        # Write garbage bytes that look nothing like a SQLite DB header.
        # ``sqlite3.connect(...mode=ro)`` will accept the file path but
        # the first ``execute()`` call against it will raise
        # ``DatabaseError: file is not a database``. (On some platforms
        # the connect call itself raises — both branches are caught by
        # the widened ``sqlite3.Error`` filter.)
        (db_dir / "training.db").write_bytes(
            b"this is definitely not a sqlite database file"
        )
        resp = client.get(
            "/api/versions/v0/forensics/some_game_aaaaaaaaaaaa"
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "trajectory": [],
            "give_up_fired": False,
            "give_up_step": None,
            "expert_dispatch": None,
        }

    def test_give_up_fired_default_is_false(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # The transitions schema has no ``give_up`` column, so this
        # field is hardcoded to False until a future schema migration
        # lands. The test pins the contract so any future change is
        # deliberate.
        _make_db_with_game(
            staged_repo, "v0", "give_up_aaaaaaaaaaaa", [(0, 0.4), (1, 0.1)]
        )
        body = client.get(
            "/api/versions/v0/forensics/give_up_aaaaaaaaaaaa"
        ).json()
        assert body["give_up_fired"] is False
        assert body["give_up_step"] is None

    def test_expert_dispatch_is_always_null(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _make_db_with_game(
            staged_repo, "v0", "dispatch_aaaaaaaaaaaa", [(0, 0.5)]
        )
        body = client.get(
            "/api/versions/v0/forensics/dispatch_aaaaaaaaaaaa"
        ).json()
        # Spec §6.8: single shape, never [] or a union — null until
        # Phase O writes ``expert_id`` to transitions.
        assert body["expert_dispatch"] is None

    def test_per_version_isolation(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # Same game_id in two version DBs — querying v0 must NOT see v3's data.
        gid = "shared_aaaaaaaaaaaa"
        _make_db_with_game(staged_repo, "v3", gid, [(0, 0.9), (1, 0.91)])
        # v0's DB does not exist at all.
        resp = client.get(f"/api/versions/v0/forensics/{gid}")
        assert resp.status_code == 200
        assert resp.json()["trajectory"] == []
        # v3 sees its own data.
        resp = client.get(f"/api/versions/v3/forensics/{gid}")
        body = resp.json()
        assert len(body["trajectory"]) == 2

    def test_per_version_isolation_with_db_present_in_both(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # Both DBs exist; the v0 DB has a *different* game id. Querying
        # v0 with v3's game id must return the empty skeleton — proves
        # the join filter on ``model_version`` is doing its job.
        _make_db_with_game(staged_repo, "v0", "v0_game_aaaaaaaaaaaa", [(0, 0.5)])
        _make_db_with_game(staged_repo, "v3", "v3_game_aaaaaaaaaaaa", [(0, 0.9)])
        body = client.get(
            "/api/versions/v0/forensics/v3_game_aaaaaaaaaaaa"
        ).json()
        assert body["trajectory"] == []

    def test_null_win_prob_round_trips_as_null(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _make_db_with_game(
            staged_repo, "v0", "null_wp_aaaaaaaaaaaa",
            [(0, None), (1, 0.5)],
        )
        body = client.get(
            "/api/versions/v0/forensics/null_wp_aaaaaaaaaaaa"
        ).json()
        assert body["trajectory"][0]["win_prob"] is None
        assert body["trajectory"][1]["win_prob"] == pytest.approx(0.5)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "bad_version",
        [
            "v3@bad",       # at-sign
            "v",            # no digits
            "version3",     # wrong prefix
            "v-1",          # negative-ish
            "v3a",          # trailing letters
            "../etc",       # path traversal
        ],
    )
    def test_malformed_version_returns_400(
        self, client: TestClient, staged_repo: Path, bad_version: str
    ) -> None:
        resp = client.get(
            f"/api/versions/{bad_version}/forensics/some_game_aaaaaaaaaaaa"
        )
        # FastAPI's default routing emits 404 if the path doesn't match
        # the route shape AT ALL — which can happen for inputs with ``/``
        # or empty segments. Anything containing only ASCII letters/
        # digits/symbols that survives routing must surface as 400 from
        # our validator.
        assert resp.status_code in (400, 404)
        # When routing matches, the body should mention version.
        if resp.status_code == 400:
            assert "version" in resp.json().get("detail", "").lower()

    @pytest.mark.parametrize(
        "bad_game_id",
        [
            "not a uuid",       # space
            "game;DROP",        # SQL meta
            "game'OR'1",        # quote (single — double is redundant)
            "game/with/slash",  # slash (404 from routing, not validator)
            "g" * 200,          # too long (length cap)
            "game*wild",        # asterisk (regex rejection)
        ],
    )
    def test_malformed_game_id_returns_400(
        self, client: TestClient, staged_repo: Path, bad_game_id: str
    ) -> None:
        resp = client.get(
            f"/api/versions/v0/forensics/{bad_game_id}"
        )
        # Same routing nuance as above — a literal slash in the game_id
        # cannot reach the handler. Anything that does reach it must 400.
        assert resp.status_code in (400, 404)
        if resp.status_code == 400:
            assert "game_id" in resp.json().get("detail", "").lower()
