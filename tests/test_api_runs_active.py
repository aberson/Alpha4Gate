"""Contract tests for the Models tab Step 1c live-runs aggregator.

Covers:

* ``GET /api/runs/active`` — cross-harness aggregator over training
  daemon (in-process), advised state file, and per-worker evolve round
  files. Empty state → ``[]``; per-row defaults; sort-order; multi-worker
  shape.
* ``GET /api/versions/{v}/weight-dynamics`` — file-backed JSONL reader
  with version filter, malformed-line tolerance, and surface-of-failure
  rows.

The fixture redirects ``_REPO_ROOT`` AND wires ``_data_dir`` /
``_evolve_dir`` to the staged tree so the file-scan helpers land in
the same directory tree the test writes into. The training daemon is
swapped in/out via ``monkeypatch.setattr(v10_api, "_daemon", ...)``
with a tiny stub that returns a synthetic ``get_status()`` payload —
the real daemon spawns SC2 subprocesses and is unsuitable for unit
tests.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from bots.v10 import api as v10_api
from bots.v10.api import app, configure
from bots.v10.error_log import get_error_log_buffer
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def staged_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Stage a fake repo root and patch the API's resolvers at it."""
    bots_dir = tmp_path / "bots"
    bots_dir.mkdir()
    (bots_dir / "current").mkdir()
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(v10_api, "_REPO_ROOT", tmp_path)
    yield tmp_path


@pytest.fixture()
def client(
    staged_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Test client wired to the staged repo with split per-version /
    cross-version dirs.

    The aggregator reads:

    * the advised-state file from ``_data_dir`` (per-version state)
    * evolve_round files from ``_evolve_dir`` (cross-version state)

    Production puts these in two different physical dirs; tests must do
    the same so a stale evolve_round file at the per-version dir can't
    falsely satisfy a test for the cross-version glob (or vice versa).
    """
    log_dir = tmp_path / "logs"
    replay_dir = tmp_path / "replays"
    log_dir.mkdir()
    replay_dir.mkdir()
    per_version_data = staged_repo / "bots" / "v0" / "data"
    per_version_data.mkdir(parents=True, exist_ok=True)
    cross_version_data = staged_repo / "data"
    configure(
        per_version_data,
        log_dir,
        replay_dir,
        evolve_dir=cross_version_data,
    )
    # Configure() wires a real TrainingDaemon at ``_daemon`` — overwrite
    # with a stub that always reports "not running" so the aggregator's
    # daemon branch is OFF unless a test overrides it explicitly.
    monkeypatch.setattr(v10_api, "_daemon", _DaemonStub(running=False))
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_error_buffer() -> Iterator[None]:
    """Reset the process-wide error log buffer between tests."""
    get_error_log_buffer().reset()
    yield
    get_error_log_buffer().reset()


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _DaemonStub:
    """Stand-in for ``TrainingDaemon`` that lets tests script a status."""

    def __init__(
        self,
        *,
        running: bool,
        state: str = "idle",
        last_run: str = "",
    ) -> None:
        self._running = running
        self._state = state
        self._last_run = last_run

    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "state": self._state,
            "last_run": self._last_run or None,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_advised_state(repo: Path, payload: dict[str, Any]) -> None:
    """Write ``advised_run_state.json`` into the per-version data dir."""
    path = repo / "bots" / "v0" / "data" / "advised_run_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_evolve_round(
    repo: Path,
    worker_id: int,
    payload: dict[str, Any],
) -> None:
    """Write ``evolve_round_<worker>.json`` into the cross-version data dir."""
    path = repo / "data" / f"evolve_round_{worker_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_evolve_round_raw(
    repo: Path,
    worker_id: int,
    raw_text: str,
) -> None:
    """Write a raw (potentially-malformed) string at the evolve_round path.

    Companion to :func:`_write_evolve_round` for tests that need to
    bypass ``json.dumps`` and stage a corrupted file (e.g. ``"not
    json{"``) on disk to exercise the aggregator's malformed-line
    tolerance.
    """
    path = repo / "data" / f"evolve_round_{worker_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw_text, encoding="utf-8")


def _write_evolve_run_state(repo: Path, payload: dict[str, Any]) -> None:
    """Write ``evolve_run_state.json`` into the cross-version data dir."""
    path = repo / "data" / "evolve_run_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_evolve_current_round(repo: Path, payload: dict[str, Any]) -> None:
    """Write ``evolve_current_round.json`` into the cross-version data dir.

    Single-concurrency evolve runs write the active worker's live state
    to this file (no ``worker_id`` field) — the per-worker round-file
    glob doesn't match for those. Used by the #268 fallback tests.
    """
    path = repo / "data" / "evolve_current_round.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_weight_dynamics(
    repo: Path, lines: list[str | dict[str, Any]]
) -> None:
    """Write ``data/weight_dynamics.jsonl``. Strings are written verbatim
    (used for malformed-line tests); dicts are JSON-encoded.
    """
    path = repo / "data" / "weight_dynamics.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered: list[str] = []
    for entry in lines:
        if isinstance(entry, str):
            rendered.append(entry)
        else:
            rendered.append(json.dumps(entry))
    path.write_text("\n".join(rendered) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# /api/runs/active
# ---------------------------------------------------------------------------
#
# Single endpoint class per Step 1a/1b precedent (``TestVersionsEndpoint``,
# ``TestVersionTrainingHistoryEndpoint``). Tests are grouped by behavior
# inside the class via comment headers — empty / training-daemon /
# advised / evolve / aggregation — but live as flat methods.


class TestRunsActiveEndpoint:
    # ------------------------------------------------------------------
    # Empty / inactive states
    # ------------------------------------------------------------------

    def test_no_harness_active_returns_empty_list(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # Daemon stub is not-running; no advised file; no evolve files.
        resp = client.get("/api/runs/active")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.parametrize(
        "inactive_status",
        ["idle", "completed", "failed", "stopped"],
    )
    def test_advised_inactive_status_does_not_emit_row(
        self,
        client: TestClient,
        staged_repo: Path,
        inactive_status: str,
    ) -> None:
        # Any of the explicit inactive statuses must keep the aggregator
        # silent — only ``running`` / phase-name strings should surface.
        # Per the helper's inactive set: ``{"", "idle", "completed",
        # "stopped", "done"}``. ``failed`` is NOT in that set, so the
        # current contract is "failed surfaces"; if the team chooses to
        # add it later, this parametrize needs to be updated.
        _write_advised_state(staged_repo, {"status": inactive_status})
        body = client.get("/api/runs/active").json()
        if inactive_status == "failed":
            # ``failed`` is currently treated as active (one row). Pin
            # the contract — flipping this behavior is a deliberate
            # change.
            assert len(body) == 1
            assert body[0]["harness"] == "advised"
        else:
            assert body == []

    def test_inactive_evolve_round_file_is_skipped(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # Worker file exists but ``active=False`` — must NOT surface
        # (otherwise stale leftover files would clutter the live list).
        _write_evolve_round(staged_repo, 0, {
            "active": False,
            "worker_id": 0,
            "phase": "fitness",
        })
        body = client.get("/api/runs/active").json()
        assert body == []

    # ------------------------------------------------------------------
    # Training-daemon source
    # ------------------------------------------------------------------

    def test_daemon_running_emits_one_row(
        self,
        client: TestClient,
        staged_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            v10_api,
            "_daemon",
            _DaemonStub(
                running=True,
                state="training",
                last_run="2026-04-30T12:00:00+00:00",
            ),
        )
        body = client.get("/api/runs/active").json()
        assert len(body) == 1
        row = body[0]
        assert row["harness"] == "training-daemon"
        # Version is derived from the configured per-version data dir
        # (``bots/v0/data``).
        assert row["version"] == "v0"
        assert row["phase"] == "training"
        # All numeric fields default to 0 — the daemon doesn't report
        # per-game progress in the same shape evolve does.
        assert row["games_played"] == 0
        assert row["games_total"] == 0
        assert row["score_cand"] == 0
        assert row["score_parent"] == 0
        assert row["current_imp"] == ""
        assert row["started_at"] == "2026-04-30T12:00:00+00:00"
        assert row["updated_at"] == "2026-04-30T12:00:00+00:00"

    def test_daemon_not_running_does_not_emit_row(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # The default fixture stub already reports running=False; assert
        # the aggregator skips it explicitly.
        body = client.get("/api/runs/active").json()
        assert body == []

    # ------------------------------------------------------------------
    # Advised source
    # ------------------------------------------------------------------

    def test_advised_running_emits_one_row(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _write_advised_state(staged_repo, {
            "status": "running",
            "phase_name": "code-iteration",
            "iteration": 3,
            "current_improvement": {"title": "Add chrono boost"},
            "version": "v3",
            "started_at": "2026-04-30T08:00:00+00:00",
            "updated_at": "2026-04-30T09:30:00+00:00",
            "games_played": 4,
            "games_total": 10,
        })
        body = client.get("/api/runs/active").json()
        assert len(body) == 1
        row = body[0]
        assert row["harness"] == "advised"
        assert row["version"] == "v3"
        assert row["phase"] == "code-iteration"
        assert row["current_imp"] == "Add chrono boost"
        assert row["games_played"] == 4
        assert row["games_total"] == 10
        assert row["started_at"] == "2026-04-30T08:00:00+00:00"
        assert row["updated_at"] == "2026-04-30T09:30:00+00:00"

    def test_advised_active_with_only_iteration_uses_iter_label(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _write_advised_state(staged_repo, {
            "status": "validating",
            "iteration": 7,
            "updated_at": "2026-04-30T10:00:00+00:00",
        })
        body = client.get("/api/runs/active").json()
        assert len(body) == 1
        assert body[0]["current_imp"] == "iter7"

    def test_advised_missing_numeric_fields_default_to_zero(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _write_advised_state(staged_repo, {
            "status": "running",
        })
        body = client.get("/api/runs/active").json()
        assert len(body) == 1
        row = body[0]
        # All numeric defaults are 0, not null.
        for key in ("games_played", "games_total", "score_cand", "score_parent"):
            assert row[key] == 0, key
        # current_imp default is empty string, not null.
        assert row["current_imp"] == ""

    # ------------------------------------------------------------------
    # Evolve source
    # ------------------------------------------------------------------

    def test_single_worker_active_emits_one_row(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _write_evolve_run_state(staged_repo, {
            "run_id": "run-1",
            "concurrency": 2,
            "parent_current": "v4",
            "started_at": "2026-04-30T05:00:00+00:00",
        })
        _write_evolve_round(staged_repo, 0, {
            "active": True,
            "worker_id": 0,
            "run_id": "run-1",
            "phase": "fitness",
            "imp_title": "Splash readiness",
            "candidate": "cand_aaaa",
            "games_played": 3,
            "games_total": 9,
            "score_cand": 2,
            "score_parent": 1,
            "updated_at": "2026-04-30T05:30:00+00:00",
        })
        body = client.get("/api/runs/active").json()
        assert len(body) == 1
        row = body[0]
        assert row["harness"] == "evolve"
        assert row["version"] == "v4"
        assert row["phase"] == "fitness"
        # ``current_imp`` carries the worker label + imp title so the
        # Live-Runs UI can disambiguate concurrent workers at a glance.
        assert "worker-0" in row["current_imp"]
        assert "Splash readiness" in row["current_imp"]
        assert row["games_played"] == 3
        assert row["games_total"] == 9
        assert row["score_cand"] == 2
        assert row["score_parent"] == 1
        assert row["started_at"] == "2026-04-30T05:00:00+00:00"
        assert row["updated_at"] == "2026-04-30T05:30:00+00:00"

    def test_multiple_active_workers_emit_multiple_rows(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _write_evolve_run_state(staged_repo, {
            "run_id": "run-2",
            "concurrency": 3,
            "parent_current": "v7",
            "started_at": "2026-04-30T05:00:00+00:00",
        })
        for wid, ts in [
            (0, "2026-04-30T05:30:00+00:00"),
            (1, "2026-04-30T05:35:00+00:00"),
            (2, "2026-04-30T05:25:00+00:00"),
        ]:
            _write_evolve_round(staged_repo, wid, {
                "active": True,
                "worker_id": wid,
                "phase": "regression",
                "imp_title": f"Imp {wid}",
                "updated_at": ts,
            })
        body = client.get("/api/runs/active").json()
        assert len(body) == 3
        # Sorted by updated_at desc → wid 1, then 0, then 2.
        assert [r["current_imp"].split(":")[0] for r in body] == [
            "worker-1",
            "worker-0",
            "worker-2",
        ]

    def test_direct_file_scan_finds_workers_without_run_state(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """The aggregator scans evolve_round_*.json directly so it can
        surface workers even when ``evolve_run_state.json`` is absent or
        has no ``run_id``. This is the parallelization edge case the
        plan §6.3 calls out — the existing ``/api/evolve/running-rounds``
        endpoint requires the run-state file, so the aggregator picks
        up the slack.
        """
        _write_evolve_round(staged_repo, 0, {
            "active": True,
            "worker_id": 0,
            "phase": "stack_apply",
            "imp_title": "Observer scout",
            "updated_at": "2026-04-30T07:00:00+00:00",
        })
        body = client.get("/api/runs/active").json()
        assert len(body) == 1
        assert body[0]["harness"] == "evolve"
        # Empty version when run-state file is missing.
        assert body[0]["version"] == ""
        # started_at also empty under the same condition.
        assert body[0]["started_at"] == ""

    def test_evolve_row_numeric_defaults_to_zero(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _write_evolve_round(staged_repo, 0, {
            "active": True,
            "worker_id": 0,
            "phase": "starting",
            "updated_at": "2026-04-30T05:00:00+00:00",
        })
        body = client.get("/api/runs/active").json()
        assert len(body) == 1
        row = body[0]
        # All numeric defaults are 0, not null.
        for key in ("games_played", "games_total", "score_cand", "score_parent"):
            assert row[key] == 0, key

    def test_evolve_row_with_non_numeric_int_field_defaults_to_zero(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """Defense in depth (finding 9): a corrupted state file with
        ``"games_played": "not-a-number"`` must not 500 the endpoint.

        The ``_safe_int`` helper coerces non-numeric values to 0 so the
        worker row still surfaces with a best-effort numeric default.
        """
        _write_evolve_round(staged_repo, 0, {
            "active": True,
            "worker_id": 0,
            "phase": "fitness",
            "imp_title": "Bad int",
            "games_played": "not-a-number",
            "games_total": "also-bad",
            "score_cand": [1, 2, 3],  # wrong type entirely
            "score_parent": None,
            "updated_at": "2026-04-30T05:00:00+00:00",
        })
        resp = client.get("/api/runs/active")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        row = body[0]
        assert row["harness"] == "evolve"
        for key in ("games_played", "games_total", "score_cand", "score_parent"):
            assert row[key] == 0, key

    def test_malformed_evolve_round_file_yields_n_minus_one_rows(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """Finding 2: one valid + one malformed evolve_round file →
        exactly one row, not zero.

        The aggregator tolerates per-file JSON corruption (each
        ``_read_json_file`` call independently returns ``None`` on
        ``JSONDecodeError``) so a single bad file cannot blank out the
        whole live-runs list.
        """
        _write_evolve_round(staged_repo, 0, {
            "active": True,
            "worker_id": 0,
            "phase": "fitness",
            "imp_title": "Good worker",
            "updated_at": "2026-04-30T05:00:00+00:00",
        })
        _write_evolve_round_raw(staged_repo, 1, "not json{")
        body = client.get("/api/runs/active").json()
        assert len(body) == 1
        assert body[0]["current_imp"].startswith("worker-0")

    def test_sort_with_missing_updated_at_field_places_blank_last(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """Finding 3: a row without ``updated_at`` must sort AFTER one
        that has it (per ``r.get("updated_at") or ""`` empty-string
        fallback semantics — empty string sorts before any ISO ts under
        ascending compare, so descending puts it last).
        """
        _write_evolve_round(staged_repo, 0, {
            "active": True,
            "worker_id": 0,
            "phase": "fitness",
            "imp_title": "With ts",
            "updated_at": "2026-04-30T05:00:00+00:00",
        })
        _write_evolve_round(staged_repo, 1, {
            "active": True,
            "worker_id": 1,
            "phase": "fitness",
            "imp_title": "No ts",
            # No ``updated_at`` field at all.
        })
        body = client.get("/api/runs/active").json()
        assert len(body) == 2
        # Worker-0 (has updated_at) should come BEFORE worker-1 (none).
        labels = [r["current_imp"].split(":")[0] for r in body]
        assert labels == ["worker-0", "worker-1"]
        # And worker-1's updated_at field should be empty string.
        assert body[1]["updated_at"] == ""

    # ------------------------------------------------------------------
    # Single-concurrency current-round fallback (#268)
    # ------------------------------------------------------------------

    def test_current_round_active_surfaces_worker_zero(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """Single-concurrency runs: ``scripts/evolve.py`` writes the
        active worker's live state to ``evolve_current_round.json`` (no
        ``worker_id`` field) instead of ``evolve_round_<wid>.json``.
        The aggregator must synthesize a ``worker-0`` row from it so the
        Live Runs grid is not blank during default-config soaks.
        """
        _write_evolve_run_state(staged_repo, {
            "run_id": "run-single",
            "concurrency": 1,
            "parent_current": "v12",
            "started_at": "2026-05-02T22:06:27+00:00",
        })
        _write_evolve_current_round(staged_repo, {
            "active": True,
            "phase": "fitness",
            "imp_title": "Gas-dump warp priority when gas exceeds 600",
            "candidate": "cand_a8b4b01b",
            "games_played": 2,
            "games_total": 3,
            "score_cand": 1,
            "score_parent": 1,
            "updated_at": "2026-05-02T22:33:00+00:00",
        })
        body = client.get("/api/runs/active").json()
        assert len(body) == 1
        row = body[0]
        assert row["harness"] == "evolve"
        assert row["version"] == "v12"
        assert row["phase"] == "fitness"
        # Synthesized as worker-0 since current_round has no worker_id.
        assert "worker-0" in row["current_imp"]
        assert "Gas-dump warp priority" in row["current_imp"]
        assert row["games_played"] == 2
        assert row["games_total"] == 3
        assert row["score_cand"] == 1
        assert row["score_parent"] == 1
        assert row["started_at"] == "2026-05-02T22:06:27+00:00"
        assert row["updated_at"] == "2026-05-02T22:33:00+00:00"

    def test_current_round_inactive_does_not_emit_row(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """Stale ``evolve_current_round.json`` from a prior run (active
        flipped to ``False`` at run completion) must NOT surface a row.
        """
        _write_evolve_current_round(staged_repo, {
            "active": False,
            "updated_at": "2026-05-02T22:49:33+00:00",
        })
        body = client.get("/api/runs/active").json()
        assert body == []

    def test_per_worker_round_skips_current_round_fallback(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """De-dup: when a parallel run writes BOTH an active per-worker
        round file AND an active current-round file (the latter is
        touched on every phase boundary regardless of concurrency), the
        fallback must skip — otherwise the same worker shows up twice.
        """
        _write_evolve_run_state(staged_repo, {
            "run_id": "run-parallel",
            "concurrency": 2,
            "parent_current": "v12",
            "started_at": "2026-05-02T22:06:27+00:00",
        })
        _write_evolve_round(staged_repo, 0, {
            "active": True,
            "worker_id": 0,
            "run_id": "run-parallel",
            "phase": "fitness",
            "imp_title": "From per-worker file",
            "updated_at": "2026-05-02T22:30:00+00:00",
        })
        _write_evolve_current_round(staged_repo, {
            "active": True,
            "phase": "fitness",
            "imp_title": "From current-round file",
            "updated_at": "2026-05-02T22:30:00+00:00",
        })
        body = client.get("/api/runs/active").json()
        # One row from the per-worker glob; current-round fallback skipped.
        assert len(body) == 1
        assert "From per-worker file" in body[0]["current_imp"]

    # ------------------------------------------------------------------
    # Per-version vs cross-version resolver smoke (finding 1)
    # ------------------------------------------------------------------

    def test_runs_active_uses_cross_version_resolver_for_evolve_rounds(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """Smoke-test the resolver wiring per ``feedback_per_version_vs_
        cross_version_data_dir.md`` and the Step 1a precedent
        ``test_lineage_uses_cross_version_resolver``. If a developer
        accidentally swaps the resolvers (reads evolve rounds from
        per-version dir), this test fails because the WRONG-path file is
        the only one staged and the cross-version glob finds nothing.
        """
        # Stage a "wrong" evolve_round file at a per-version path that a
        # mis-wired endpoint would read from. Production reads from
        # ``<repo>/data/`` (cross-version), NOT ``<repo>/bots/v3/data/``.
        per_v_dir = staged_repo / "bots" / "v3" / "data"
        per_v_dir.mkdir(parents=True, exist_ok=True)
        (per_v_dir / "evolve_round_0.json").write_text(
            json.dumps({
                "active": True,
                "worker_id": 0,
                "phase": "fitness",
                "imp_title": "WRONG-RESOLVER worker",
                "updated_at": "2026-04-30T05:00:00+00:00",
            }),
            encoding="utf-8",
        )
        body = client.get("/api/runs/active").json()
        # Cross-version dir is empty → zero rows. Confirms the WRONG
        # path was never opened.
        assert body == []
        for row in body:
            assert "WRONG-RESOLVER" not in row.get("current_imp", "")

    # ------------------------------------------------------------------
    # Aggregation across all sources
    # ------------------------------------------------------------------

    def test_all_three_harnesses_active_emits_combined_rows(
        self,
        client: TestClient,
        staged_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Daemon
        monkeypatch.setattr(
            v10_api,
            "_daemon",
            _DaemonStub(
                running=True,
                state="checking",
                last_run="2026-04-30T01:00:00+00:00",
            ),
        )
        # Advised
        _write_advised_state(staged_repo, {
            "status": "running",
            "phase_name": "code-iteration",
            "iteration": 1,
            "version": "v3",
            "updated_at": "2026-04-30T10:00:00+00:00",
        })
        # Evolve worker
        _write_evolve_run_state(staged_repo, {
            "run_id": "run-3",
            "concurrency": 1,
            "parent_current": "v4",
        })
        _write_evolve_round(staged_repo, 0, {
            "active": True,
            "worker_id": 0,
            "phase": "fitness",
            "imp_title": "Gas dump",
            "updated_at": "2026-04-30T11:00:00+00:00",
        })
        body = client.get("/api/runs/active").json()
        assert len(body) == 3
        # Sort verified: evolve (11:00) > advised (10:00) > daemon (01:00).
        assert [r["harness"] for r in body] == [
            "evolve",
            "advised",
            "training-daemon",
        ]


# ---------------------------------------------------------------------------
# /api/versions/{v}/weight-dynamics
# ---------------------------------------------------------------------------


class TestVersionWeightDynamicsEndpoint:
    def test_missing_file_returns_empty_list(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        resp = client.get("/api/versions/v0/weight-dynamics")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_filter_by_version(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _write_weight_dynamics(staged_repo, [
            {
                "version": "v3",
                "checkpoint": "ckpt-1",
                "ts": "2026-04-30T01:00:00+00:00",
                "l2_per_layer": {"layer0": 0.123},
                "kl_from_parent": 0.05,
                "canary_source": "diagnostic_states",
                "error": None,
            },
            {
                "version": "v4",
                "checkpoint": "ckpt-2",
                "ts": "2026-04-30T02:00:00+00:00",
                "l2_per_layer": {"layer0": 0.456},
                "kl_from_parent": 0.07,
                "canary_source": "transitions_sample",
                "error": None,
            },
        ])
        resp = client.get("/api/versions/v3/weight-dynamics")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["checkpoint"] == "ckpt-1"
        assert rows[0]["error"] is None
        assert rows[0]["l2_per_layer"] == {"layer0": 0.123}

    def test_failure_rows_surface_unchanged(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        # Failure rows have l2/kl/canary all null and a non-null error.
        _write_weight_dynamics(staged_repo, [
            {
                "version": "v3",
                "checkpoint": "ckpt-bad",
                "ts": "2026-04-30T03:00:00+00:00",
                "l2_per_layer": None,
                "kl_from_parent": None,
                "canary_source": None,
                "error": "RuntimeError: weight load failed",
            },
        ])
        rows = client.get("/api/versions/v3/weight-dynamics").json()
        assert len(rows) == 1
        row = rows[0]
        assert row["l2_per_layer"] is None
        assert row["kl_from_parent"] is None
        assert row["canary_source"] is None
        assert row["error"] == "RuntimeError: weight load failed"

    def test_malformed_lines_are_skipped(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        _write_weight_dynamics(staged_repo, [
            "this is not json",
            {
                "version": "v3",
                "checkpoint": "ckpt-good",
                "ts": "2026-04-30T03:00:00+00:00",
                "l2_per_layer": {"layer0": 1.0},
                "kl_from_parent": 0.0,
                "canary_source": "diagnostic_states",
                "error": None,
            },
            "{also not valid",
        ])
        rows = client.get("/api/versions/v3/weight-dynamics").json()
        assert len(rows) == 1
        assert rows[0]["checkpoint"] == "ckpt-good"

    def test_malformed_version_returns_400(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        resp = client.get("/api/versions/v3@bad/weight-dynamics")
        assert resp.status_code == 400

    def test_weight_dynamics_uses_cross_version_resolver(
        self, client: TestClient, staged_repo: Path
    ) -> None:
        """Symmetric resolver smoke for ``weight_dynamics.jsonl`` —
        the file lives at ``<repo>/data/weight_dynamics.jsonl`` (cross-
        version), not ``<repo>/bots/vN/data/weight_dynamics.jsonl``. A
        sentinel file at the per-version path must NOT bleed through.
        """
        per_v_dir = staged_repo / "bots" / "v3" / "data"
        per_v_dir.mkdir(parents=True, exist_ok=True)
        (per_v_dir / "weight_dynamics.jsonl").write_text(
            json.dumps({
                "version": "v3",
                "checkpoint": "WRONG-RESOLVER-ckpt",
                "ts": "2026-04-30T01:00:00+00:00",
                "l2_per_layer": {"layer0": 9.99},
                "kl_from_parent": 9.99,
                "canary_source": "diagnostic_states",
                "error": None,
            }) + "\n",
            encoding="utf-8",
        )
        resp = client.get("/api/versions/v3/weight-dynamics")
        assert resp.status_code == 200
        rows = resp.json()
        # Cross-version path is empty — nothing must be returned.
        assert rows == []
        for r in rows:
            assert "WRONG-RESOLVER" not in r.get("checkpoint", "")
