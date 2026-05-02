"""Contract tests for the Models tab Step 1a endpoints in ``bots/v10/api.py``.

Covers:

* ``GET /api/versions`` — registry shape, derived ``race`` + ``harness_origin``,
  ``current`` flag.
* ``GET /api/versions/{v}/config`` — three-key shape, malformed-version 400,
  missing-file → empty object (does not 500).

The tests redirect the ``_REPO_ROOT`` module-level constant to a
``tmp_path`` fixture root so we can stage controlled ``bots/`` trees
plus cross-version ``data/`` files without touching the real repo.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from bots.v10 import api as v10_api
from bots.v10.api import app, configure
from bots.v10.error_log import get_error_log_buffer
from fastapi.testclient import TestClient


def _write_manifest(version_dir: Path, **overrides: object) -> None:
    """Create ``bots/vN/manifest.json`` with sensible defaults + overrides."""
    version_dir.mkdir(parents=True, exist_ok=True)
    # ``VERSION`` file is what registry helpers look for; harmless here
    # but mirrors the real layout.
    (version_dir / "VERSION").write_text(version_dir.name, encoding="utf-8")
    payload: dict[str, object] = {
        "version": version_dir.name,
        "parent": None,
        "git_sha": "deadbeef0000000000000000000000000000beef",
        "timestamp": "2026-04-16T21:17:28.858723+00:00",
        "best": None,
        "previous_best": None,
        "elo": 0.0,
        "fingerprint": {
            "action_space_size": 6,
            "feature_dim": 24,
            "obs_spec_hash": "abc123",
        },
        "extra": {},
    }
    payload.update(overrides)
    (version_dir / "manifest.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


@pytest.fixture()
def staged_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Stage a fake repo root with ``bots/`` + ``data/`` and patch the API.

    Yields the staged repo root. Tests place files inside
    ``<root>/bots/v{N}/`` and ``<root>/data/`` directly; the fixture
    handles redirecting ``_REPO_ROOT`` and pointing the per-version /
    cross-version data-dir resolvers at the staged tree.
    """
    bots_dir = tmp_path / "bots"
    bots_dir.mkdir()
    (bots_dir / "current").mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(v10_api, "_REPO_ROOT", tmp_path)
    yield tmp_path


@pytest.fixture()
def client(
    staged_repo: Path, tmp_path: Path
) -> TestClient:
    """Test client wired to the staged repo + a private per-version dir."""
    log_dir = tmp_path / "logs"
    replay_dir = tmp_path / "replays"
    log_dir.mkdir()
    replay_dir.mkdir()
    # The legacy ``configure(...)`` resolver still drives the older
    # endpoints, but the Step 1a endpoints use ``_REPO_ROOT`` directly
    # via the per-version resolver — no DB or per-version state needed
    # for these tests.
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


class TestVersionsEndpoint:
    """``GET /api/versions`` — derived race + harness_origin + current."""

    def test_empty_registry_returns_empty_list(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # bots/ exists but has no versioned dirs -> empty list.
        resp = client.get("/api/versions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_single_version_default_origin_is_manual(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _write_manifest(staged_repo / "bots" / "v0", parent=None)
        # Set current pointer
        (staged_repo / "bots" / "current" / "current.txt").write_text(
            "v0", encoding="utf-8"
        )

        resp = client.get("/api/versions")
        assert resp.status_code == 200
        rows = resp.json()
        assert isinstance(rows, list)
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "v0"
        assert row["race"] == "protoss"
        assert row["parent"] is None
        # No improvement_log / evolve_results → manual fallback.
        assert row["harness_origin"] == "manual"
        assert row["timestamp"] == "2026-04-16T21:17:28.858723+00:00"
        assert row["sha"] == "deadbeef0000000000000000000000000000beef"
        assert isinstance(row["fingerprint"], dict)
        assert row["fingerprint"]["feature_dim"] == 24
        assert row["current"] is True

    def test_current_flag_only_set_on_pointer_match(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _write_manifest(staged_repo / "bots" / "v0")
        _write_manifest(staged_repo / "bots" / "v3", parent="v0")
        (staged_repo / "bots" / "current" / "current.txt").write_text(
            "v3", encoding="utf-8"
        )
        resp = client.get("/api/versions")
        rows = resp.json()
        names = {r["name"]: r["current"] for r in rows}
        assert names == {"v0": False, "v3": True}

    def test_harness_origin_evolve_via_new_version_match(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # v3 was promoted by an evolve generation — log that.
        _write_manifest(staged_repo / "bots" / "v0")
        _write_manifest(staged_repo / "bots" / "v3", parent="v0")
        evolve_path = staged_repo / "data" / "evolve_results.jsonl"
        evolve_path.write_text(
            json.dumps({
                "phase": "stack_apply",
                "outcome": "stack-apply-pass",
                "new_version": "v3",
                "parent": "v0",
            }) + "\n",
            encoding="utf-8",
        )
        resp = client.get("/api/versions")
        rows = {r["name"]: r["harness_origin"] for r in resp.json()}
        assert rows["v3"] == "evolve"
        assert rows["v0"] == "manual"

    def test_harness_origin_advised_via_files_changed_match(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # v0 was touched by an advised iteration. files_changed contains
        # ``bots/v0/...`` — that's our derivation hook.
        _write_manifest(staged_repo / "bots" / "v0")
        imp_log = staged_repo / "data" / "improvement_log.json"
        imp_log.write_text(
            json.dumps({
                "improvements": [
                    {
                        "id": "advised-x",
                        "title": "tune mineral floats",
                        "type": "training",
                        "files_changed": ["bots/v0/data/reward_rules.json"],
                    }
                ]
            }),
            encoding="utf-8",
        )
        resp = client.get("/api/versions")
        rows = {r["name"]: r["harness_origin"] for r in resp.json()}
        assert rows["v0"] == "advised"

    def test_harness_origin_self_play_via_new_version_match(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """A version listed in ``selfplay_results.jsonl`` (forward-compat
        ``new_version`` key) and NOT claimed by advised/evolve resolves
        to ``"self-play"``. Today's ``SelfPlayRecord`` schema uses
        ``p1_version`` / ``p2_version`` instead — so this code path is
        unreachable in production until a future self-play harness
        emits promotion rows. We test the wiring against the documented
        contract from plan §5.
        """
        _write_manifest(staged_repo / "bots" / "v0")
        _write_manifest(staged_repo / "bots" / "v3", parent="v0")
        # Self-play "promoted" v3 — forward-compat row with new_version.
        (staged_repo / "data" / "selfplay_results.jsonl").write_text(
            json.dumps({
                "new_version": "v3",
                "outcome": "selfplay-promote",
            }) + "\n",
            encoding="utf-8",
        )
        resp = client.get("/api/versions")
        rows = {r["name"]: r["harness_origin"] for r in resp.json()}
        assert rows["v3"] == "self-play"
        # v0 has no claim from any harness — manual.
        assert rows["v0"] == "manual"

    def test_evolve_takes_precedence_over_advised(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """Evolve wraps advised iterations, so its claim wins."""
        _write_manifest(staged_repo / "bots" / "v3", parent="v0")
        # Advised touched bots/v3/...
        (staged_repo / "data" / "improvement_log.json").write_text(
            json.dumps({
                "improvements": [
                    {
                        "id": "advised-y",
                        "files_changed": ["bots/v3/decision_engine.py"],
                    }
                ]
            }),
            encoding="utf-8",
        )
        # ...AND evolve also claims v3.
        (staged_repo / "data" / "evolve_results.jsonl").write_text(
            json.dumps({"new_version": "v3", "outcome": "stack-apply-pass"}) + "\n",
            encoding="utf-8",
        )
        resp = client.get("/api/versions")
        rows = {r["name"]: r["harness_origin"] for r in resp.json()}
        assert rows["v3"] == "evolve"

    def test_response_shape_keys(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _write_manifest(staged_repo / "bots" / "v0")
        rows = client.get("/api/versions").json()
        expected = {
            "name", "race", "parent", "harness_origin",
            "timestamp", "sha", "fingerprint", "current",
        }
        assert set(rows[0].keys()) == expected

    def test_skips_non_version_dirs_and_invalid_manifests(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # bots/current is excluded by name; bots/scratch is excluded by
        # the ``^v\d+$`` filter; bots/v9 is excluded because no manifest
        # exists.
        (staged_repo / "bots" / "scratch").mkdir()
        (staged_repo / "bots" / "v9").mkdir()
        _write_manifest(staged_repo / "bots" / "v0")
        rows = client.get("/api/versions").json()
        assert [r["name"] for r in rows] == ["v0"]


class TestVersionConfigEndpoint:
    """``GET /api/versions/{v}/config`` — three keys, missing → ``{}``,
    malformed version → 400."""

    def test_returns_three_keys_with_real_files(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        per_v = staged_repo / "bots" / "v0" / "data"
        per_v.mkdir(parents=True, exist_ok=True)
        (per_v / "hyperparams.json").write_text(
            json.dumps({"learning_rate": 0.0003}), encoding="utf-8"
        )
        (per_v / "reward_rules.json").write_text(
            json.dumps({"version": 1, "rules": []}), encoding="utf-8"
        )
        (per_v / "daemon_config.json").write_text(
            json.dumps({"max_runs": 5}), encoding="utf-8"
        )

        resp = client.get("/api/versions/v0/config")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"hyperparams", "reward_rules", "daemon_config"}
        assert body["hyperparams"] == {"learning_rate": 0.0003}
        assert body["reward_rules"] == {"version": 1, "rules": []}
        assert body["daemon_config"] == {"max_runs": 5}

    def test_returns_empty_dict_for_each_missing_file(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # No per-version data dir at all → all three values are ``{}``.
        resp = client.get("/api/versions/v0/config")
        assert resp.status_code == 200
        assert resp.json() == {
            "hyperparams": {},
            "reward_rules": {},
            "daemon_config": {},
        }

    def test_partial_files_present(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        per_v = staged_repo / "bots" / "v3" / "data"
        per_v.mkdir(parents=True)
        (per_v / "hyperparams.json").write_text(
            json.dumps({"foo": 1}), encoding="utf-8"
        )
        # reward_rules + daemon_config absent.

        resp = client.get("/api/versions/v3/config")
        body = resp.json()
        assert body["hyperparams"] == {"foo": 1}
        assert body["reward_rules"] == {}
        assert body["daemon_config"] == {}

    @pytest.mark.parametrize(
        "bad_v",
        ["v3@bad", "vX", "v3;rm", "V0", "3", "v"],
    )
    def test_malformed_version_returns_400(
        self, client: TestClient, bad_v: str
    ) -> None:
        resp = client.get(f"/api/versions/{bad_v}/config")
        assert resp.status_code == 400, resp.text
        # Error body should include a brief reason.
        assert "Invalid version" in resp.text or "must match" in resp.text

    def test_malformed_json_in_per_version_file_returns_empty(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        per_v = staged_repo / "bots" / "v0" / "data"
        per_v.mkdir(parents=True, exist_ok=True)
        (per_v / "hyperparams.json").write_text(
            "{not-json:::}", encoding="utf-8"
        )

        resp = client.get("/api/versions/v0/config")
        assert resp.status_code == 200
        # Malformed file is treated as missing per ``_read_json_file``
        # contract — never 500.
        assert resp.json()["hyperparams"] == {}

    def test_config_uses_per_version_resolver(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """Symmetric to the lineage resolver test: stage a "wrong" file
        at the cross-version path and verify ``/api/versions/v3/config``
        does NOT pick it up. If a developer accidentally swaps the
        resolvers, this test fails because the cross-version-staged
        sentinel content leaks through.
        """
        # Sentinel content at the WRONG path (cross-version dir).
        (staged_repo / "data" / "hyperparams.json").write_text(
            json.dumps({"sentinel": "WRONG_PATH"}), encoding="utf-8"
        )
        # Real per-version path is empty (no hyperparams.json there).
        per_v = staged_repo / "bots" / "v3" / "data"
        per_v.mkdir(parents=True, exist_ok=True)

        resp = client.get("/api/versions/v3/config")
        assert resp.status_code == 200
        body = resp.json()
        # Endpoint correctly read from <repo>/bots/v3/data/ (missing) →
        # empty dict; the cross-version sentinel must NOT have leaked.
        assert body["hyperparams"] == {}
        assert "sentinel" not in json.dumps(body)


class TestResolverHelpers:
    """Direct unit tests for the dual data-dir resolvers + validator.

    These guard against the silent-breakage failure mode recorded in
    ``feedback_per_version_vs_cross_version_data_dir.md``: sharing one
    resolver for both data classes returns 200 with idle skeletons even
    though the file exists at a different absolute path.
    """

    def test_per_version_resolver_returns_bots_vN_data(
        self, staged_repo: Path
    ) -> None:
        path = v10_api._per_version_data_dir("v3")
        assert path == staged_repo / "bots" / "v3" / "data"

    def test_cross_version_resolver_returns_repo_data(
        self, staged_repo: Path
    ) -> None:
        path = v10_api._cross_version_data_dir()
        assert path == staged_repo / "data"

    def test_validate_version_accepts_well_formed(self) -> None:
        for good in ("v0", "v3", "v10", "v999"):
            assert v10_api._validate_version(good) == good

    def test_validate_version_rejects_malformed(self) -> None:
        from fastapi import HTTPException

        for bad in ("v", "V0", "3", "v3a", "v 3", "v3;rm", "../v0", ""):
            with pytest.raises(HTTPException) as exc_info:
                v10_api._validate_version(bad)
            assert exc_info.value.status_code == 400
