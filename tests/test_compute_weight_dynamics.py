"""Tests for ``scripts/compute_weight_dynamics.py`` (Models tab Step 9).

Coverage:

* CLI input validation rejects malformed ``--version`` BEFORE any heavy
  import (notably ``torch`` and ``stable_baselines3``).
* Canary source resolution: ``diagnostic_states.json`` wins when present;
  ``training.db`` fallback fires when it's absent.
* The transitions-fallback path exercises ``_coerce_action`` from
  ``bots.v10.learning.database`` (per Step 9 spec).
* A per-checkpoint ``RuntimeError`` produces a failure-row with the
  exception class name in ``error`` and the script keeps processing
  the next checkpoint instead of crashing.
* The advisory lockfile is acquired before EACH JSONL append and
  released after.
* Idempotency: a success-row with a matching ``(version, checkpoint,
  hash)`` is not re-written; a failure-row with the same key IS retried
  (success appended; the original failure row stays for audit).

Tests run on CPU and never load a real ``.zip`` checkpoint — the
``stable_baselines3.PPO.load`` seam is monkeypatched to a stub that
returns whatever the test author handed it.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ``scripts/compute_weight_dynamics.py`` isn't installed as a package, so
# load it the same way ``tests/test_build_lineage.py`` loads its sibling.
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "compute_weight_dynamics.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "compute_weight_dynamics",
    _SCRIPT_PATH,
)
assert _SPEC is not None and _SPEC.loader is not None
cwd_module = importlib.util.module_from_spec(_SPEC)
sys.modules["compute_weight_dynamics"] = cwd_module
_SPEC.loader.exec_module(cwd_module)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> Path:
    """Stage a repo-shaped tmp dir: ``data/`` + ``bots/`` directories."""
    (tmp_path / "data").mkdir()
    (tmp_path / "bots").mkdir()
    return tmp_path


def _make_version_dir(
    repo: Path,
    version: str,
    *,
    parent: str | None,
    checkpoints: list[str],
) -> Path:
    """Stage ``bots/<version>/`` with manifest + checkpoints dir + zip files."""
    vdir = repo / "bots" / version
    (vdir / "data" / "checkpoints").mkdir(parents=True)
    (vdir / "manifest.json").write_text(
        json.dumps({"version": version, "parent": parent, "best": version})
    )
    for cp in checkpoints:
        # The .zip files don't need real PPO content because tests
        # monkeypatch ``PPO.load`` — they exist only so the script's
        # ``glob("*.zip")`` finds them.
        (vdir / "data" / "checkpoints" / cp).write_bytes(b"fake-zip")
    return vdir


_STATE_COL_NAMES = [
    "supply_used", "supply_cap", "minerals", "vespene", "army_supply",
    "worker_count", "base_count", "enemy_near", "enemy_supply",
    "game_time_secs", "gateway_count", "robo_count", "forge_count",
    "upgrade_count", "enemy_structure_count", "cannon_count", "battery_count",
    "zealot_count", "stalker_count", "sentry_count", "immortal_count",
    "colossus_count", "archon_count", "high_templar_count",
    "dark_templar_count", "phoenix_count", "void_ray_count", "carrier_count",
    "tempest_count", "disruptor_count", "warp_prism_count", "observer_count",
    "enemy_light_count", "enemy_armored_count", "enemy_siege_count",
    "enemy_support_count", "enemy_air_harass_count", "enemy_heavy_count",
    "enemy_capital_count", "enemy_cloak_count",
]


def _seed_training_db(db_path: Path, n_rows: int = 250) -> None:
    """Create a tiny ``training.db`` with ``games`` + ``transitions`` rows."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE games (game_id TEXT PRIMARY KEY, map_name TEXT, "
            "difficulty INTEGER, result TEXT, duration_secs REAL, "
            "total_reward REAL, model_version TEXT, "
            "created_at TEXT DEFAULT (datetime('now')))"
        )
        # Build a transitions table whose first 40 data columns are the
        # _STATE_COLS in the same order as production. The script reads
        # ``SELECT <state_cols>, action FROM transitions`` so only those
        # 41 columns matter for its slicing logic.
        cols_def = ", ".join(f"{name} INTEGER" for name in _STATE_COL_NAMES)
        # ``game_time_secs`` is REAL in production; redefine it.
        cols_def = cols_def.replace(
            "game_time_secs INTEGER", "game_time_secs REAL"
        )
        conn.execute(
            "CREATE TABLE transitions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "game_id TEXT, step_index INTEGER, game_time REAL, "
            f"{cols_def}, action INTEGER, reward REAL)"
        )
        conn.execute(
            "INSERT INTO games (game_id, map_name, difficulty, result, "
            "duration_secs, total_reward, model_version) VALUES "
            "(?, ?, ?, ?, ?, ?, ?)",
            ("g1", "Simple64", 3, "win", 600.0, 1.0, "v3"),
        )

        col_names = (
            ["game_id", "step_index", "game_time"]
            + _STATE_COL_NAMES
            + ["action", "reward"]
        )
        placeholders = ", ".join("?" * len(col_names))
        for i in range(n_rows):
            values: list[Any] = ["g1", i, float(i)]
            values.extend([i % 7] * len(_STATE_COL_NAMES))
            values.append(i % 5)  # action
            values.append(0.0)  # reward
            conn.execute(
                f"INSERT INTO transitions ({', '.join(col_names)}) "
                f"VALUES ({placeholders})",
                values,
            )
        conn.commit()
    finally:
        conn.close()


def _make_stub_model(*, obs_dim: int = 4, n_actions: int = 6) -> MagicMock:
    """Build a stub PPO model with the minimal surface the script touches.

    Mirrors the calls in ``_compute_l2_per_layer`` (named_parameters
    iteration), ``_compute_kl_from_parent`` (observation_space.shape,
    action_space.n, policy.get_distribution(obs).distribution.probs).
    """
    import torch

    model = MagicMock()
    model.observation_space.shape = (obs_dim,)
    model.action_space.n = n_actions

    # Two named "parameters" — torch tensors so torch.linalg.norm works.
    p1 = torch.tensor([1.0, 2.0, 2.0])  # norm = 3.0
    p2 = torch.tensor([3.0, 4.0])       # norm = 5.0
    model.policy.named_parameters = MagicMock(
        return_value=iter([
            ("policy_net.0.weight", p1),
            ("policy_net.0.bias", p2),
        ]),
    )

    def _get_distribution(obs_t: Any) -> Any:
        # Uniform categorical over n_actions, batched over obs_t.
        b = obs_t.shape[0] if hasattr(obs_t, "shape") else len(obs_t)
        probs = torch.full((b, n_actions), 1.0 / n_actions)
        inner = MagicMock()
        inner.probs = probs
        wrapper = MagicMock()
        wrapper.distribution = inner
        return wrapper

    model.policy.get_distribution = _get_distribution
    return model


@pytest.fixture
def staged_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Stage a repo dir + redirect ``_repo_root`` to point at it."""
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(cwd_module, "_repo_root", lambda: repo)
    yield repo


@pytest.fixture
def stub_ppo(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Install a stub ``stable_baselines3`` so ``PPO.load`` doesn't load real .zip.

    Returns the ``PPO`` MagicMock so tests can drive its ``.load``
    behaviour (return values, side effects, etc.). The default ``load``
    returns a fresh stub model on every call.
    """
    sb3 = MagicMock()

    def _default_load(path: str, device: str = "cpu") -> MagicMock:
        return _make_stub_model()

    sb3.PPO.load = MagicMock(side_effect=_default_load)
    monkeypatch.setitem(sys.modules, "stable_baselines3", sb3)
    return sb3.PPO


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_invalid_version_arg_rejected(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--version v3@bad`` exits nonzero and never imports torch."""
    # Sentinel-check: torch must not appear in sys.modules from this run.
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.delitem(sys.modules, "stable_baselines3", raising=False)

    rc = cwd_module.main(["--version", "v3@bad"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "Invalid version" in captured.err

    assert "torch" not in sys.modules, (
        "torch was imported during a malformed-version run — validation "
        "must reject the input BEFORE any heavy import"
    )


def test_canary_source_diagnostic_states(
    staged_repo: Path,
    stub_ppo: MagicMock,
) -> None:
    """When diagnostic_states.json exists, the row records that source."""
    _ = stub_ppo  # imported for side effect (PPO.load monkeypatch).
    _make_version_dir(staged_repo, "v3", parent=None, checkpoints=["v3.zip"])
    diag_path = staged_repo / "bots" / "v3" / "data" / "diagnostic_states.json"
    diag_path.write_text(json.dumps([[0.1] * 4 for _ in range(3)]))

    rc = cwd_module.main(["--version", "v3"])
    assert rc == 0

    rows = [
        json.loads(line)
        for line in (
            staged_repo / "data" / "weight_dynamics.jsonl"
        ).read_text().splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["canary_source"] == "diagnostic_states"
    assert rows[0]["error"] is None
    assert rows[0]["l2_per_layer"]["policy_net.0.weight"] == pytest.approx(3.0)
    assert rows[0]["l2_per_layer"]["policy_net.0.bias"] == pytest.approx(5.0)


def test_canary_source_transitions_fallback(
    staged_repo: Path,
    stub_ppo: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No diagnostic_states.json → sample transitions; ``_coerce_action`` runs."""
    _ = stub_ppo
    _make_version_dir(staged_repo, "v3", parent=None, checkpoints=["v3.zip"])
    db_path = staged_repo / "bots" / "v3" / "data" / "training.db"
    _seed_training_db(db_path, n_rows=200)

    # Spy on ``_coerce_action`` to confirm it's exercised.
    from bots.v10.learning import database as db_module

    coerce_calls: list[Any] = []
    original_coerce = db_module._coerce_action

    def _spy(v: Any) -> int:
        coerce_calls.append(v)
        return original_coerce(v)

    monkeypatch.setattr(db_module, "_coerce_action", _spy)

    rc = cwd_module.main(["--version", "v3"])
    assert rc == 0

    rows = [
        json.loads(line)
        for line in (
            staged_repo / "data" / "weight_dynamics.jsonl"
        ).read_text().splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["canary_source"] == "transitions_sample"
    assert rows[0]["error"] is None
    assert coerce_calls, (
        "_coerce_action was never called — the canary read path must "
        "exercise it per the Step 9 spec"
    )


def test_per_checkpoint_failure_emits_failure_row(
    staged_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RuntimeError on first checkpoint → failure row; second succeeds."""
    _make_version_dir(
        staged_repo, "v3", parent=None, checkpoints=["a.zip", "b.zip"],
    )
    diag_path = staged_repo / "bots" / "v3" / "data" / "diagnostic_states.json"
    diag_path.write_text(json.dumps([[0.1] * 4 for _ in range(3)]))

    sb3 = MagicMock()
    call_state = {"calls": 0}

    def _load_first_fails(path: str, device: str = "cpu") -> MagicMock:
        call_state["calls"] += 1
        # The two checkpoints are sorted alphabetically by name, so
        # ``a.zip`` is processed first.
        if "a.zip" in path:
            msg = "boom"
            raise RuntimeError(msg)
        return _make_stub_model()

    sb3.PPO.load = MagicMock(side_effect=_load_first_fails)
    monkeypatch.setitem(sys.modules, "stable_baselines3", sb3)

    rc = cwd_module.main(["--version", "v3"])
    assert rc == 0  # Failure row, NOT a script crash.

    rows = [
        json.loads(line)
        for line in (
            staged_repo / "data" / "weight_dynamics.jsonl"
        ).read_text().splitlines()
        if line.strip()
    ]
    assert len(rows) == 2
    by_cp = {r["checkpoint"]: r for r in rows}
    assert by_cp["a.zip"]["error"].startswith("RuntimeError:")
    assert by_cp["a.zip"]["l2_per_layer"] is None
    assert by_cp["a.zip"]["kl_from_parent"] is None
    # Per plan §5: failure rows record canary_source as null (not the
    # originating source label) and still include a stable hash string.
    assert by_cp["a.zip"]["canary_source"] is None
    assert isinstance(by_cp["a.zip"]["hash"], str)
    assert by_cp["a.zip"]["hash"]
    assert by_cp["b.zip"]["error"] is None
    assert by_cp["b.zip"]["l2_per_layer"] is not None


def test_advisory_lockfile_acquired(
    staged_repo: Path,
    stub_ppo: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``acquire_lock`` is called before each append and released after."""
    _ = stub_ppo
    _make_version_dir(
        staged_repo, "v3", parent=None, checkpoints=["a.zip", "b.zip"],
    )
    diag_path = staged_repo / "bots" / "v3" / "data" / "diagnostic_states.json"
    diag_path.write_text(json.dumps([[0.1] * 4 for _ in range(3)]))

    events: list[str] = []
    real_acquire = cwd_module.acquire_lock

    def _spy(lock_path: Path) -> Any:
        # Wrap the real generator so we still get a working append.
        gen = real_acquire(lock_path)

        class _Wrapped:
            def __enter__(self_inner: Any) -> None:
                events.append("acquire")
                gen.__enter__()

            def __exit__(self_inner: Any, *exc: Any) -> None:
                gen.__exit__(*exc)
                events.append("release")

        return _Wrapped()

    monkeypatch.setattr(cwd_module, "acquire_lock", _spy)

    rc = cwd_module.main(["--version", "v3"])
    assert rc == 0

    # Two checkpoints → at least two acquire/release pairs. The HIGH-2
    # inside-lock recheck may take additional locks (e.g. for the dedup
    # scan), so don't pin to an exact list — assert the invariant:
    # equal counts AND every acquire is paired with a release before
    # the next acquire (no overlapping locks, no orphans).
    acquires = [e for e in events if e == "acquire"]
    releases = [e for e in events if e == "release"]
    assert len(acquires) == len(releases) >= 2
    # Strict alternation: acquire/release/acquire/release/...
    assert events == ["acquire", "release"] * len(acquires)


def test_idempotency_skips_existing_success(
    staged_repo: Path,
    stub_ppo: MagicMock,
) -> None:
    """A pre-existing success row for (v, cp, hash) is not re-appended."""
    _ = stub_ppo
    _make_version_dir(staged_repo, "v3", parent=None, checkpoints=["v3.zip"])
    diag_path = staged_repo / "bots" / "v3" / "data" / "diagnostic_states.json"
    diag_path.write_text(json.dumps([[0.1] * 4 for _ in range(3)]))

    # First run populates the JSONL.
    rc = cwd_module.main(["--version", "v3"])
    assert rc == 0
    jsonl = staged_repo / "data" / "weight_dynamics.jsonl"
    rows_after_first = jsonl.read_text().splitlines()
    assert len(rows_after_first) == 1

    # Second run with identical inputs → no new row.
    rc2 = cwd_module.main(["--version", "v3"])
    assert rc2 == 0
    rows_after_second = jsonl.read_text().splitlines()
    assert rows_after_second == rows_after_first


def test_idempotency_retries_failure_row(
    staged_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-existing failure row IS retried; success appended fresh."""
    _make_version_dir(staged_repo, "v3", parent=None, checkpoints=["v3.zip"])
    diag_path = staged_repo / "bots" / "v3" / "data" / "diagnostic_states.json"
    diag_path.write_text(json.dumps([[0.1] * 4 for _ in range(3)]))

    # Run #1: PPO.load raises → emits a failure row.
    sb3 = MagicMock()
    sb3.PPO.load = MagicMock(side_effect=RuntimeError("boom"))
    monkeypatch.setitem(sys.modules, "stable_baselines3", sb3)
    rc = cwd_module.main(["--version", "v3"])
    assert rc == 0
    jsonl = staged_repo / "data" / "weight_dynamics.jsonl"
    failure_rows = [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]
    assert len(failure_rows) == 1
    assert failure_rows[0]["error"].startswith("RuntimeError:")

    # Run #2: PPO.load now succeeds → a NEW row should be appended.
    sb3.PPO.load = MagicMock(side_effect=lambda *a, **kw: _make_stub_model())
    rc2 = cwd_module.main(["--version", "v3"])
    assert rc2 == 0
    all_rows = [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]
    assert len(all_rows) == 2, (
        "Expected the failure row to be retried and a fresh success row "
        "appended, leaving both for audit"
    )
    success_rows = [r for r in all_rows if r["error"] is None]
    failure_rows_again = [r for r in all_rows if r["error"] is not None]
    assert len(success_rows) == 1
    assert len(failure_rows_again) == 1
    # Same hash on both — they describe the same (v, cp) tuple.
    assert success_rows[0]["hash"] == failure_rows_again[0]["hash"]
    # ts must differ — proves a fresh compute happened on the retry,
    # rather than the success row being an echo of the original failure.
    assert success_rows[0]["ts"] != failure_rows_again[0]["ts"]


def test_kl_null_on_obs_dim_mismatch(
    staged_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mismatched obs_dim between child + parent → kl_from_parent: null.

    Documents the tolerance promised in the module docstring: legacy
    24-dim parents vs current 47-dim children must produce a SUCCESS
    row with ``kl_from_parent is None`` and ``error is None`` (not a
    failure row). Action-space mismatch covered by the same code path.
    """
    # Child v3 with obs_dim=4; parent v2 with obs_dim=24 (mismatch).
    _make_version_dir(staged_repo, "v2", parent=None, checkpoints=["v2.zip"])
    _make_version_dir(staged_repo, "v3", parent="v2", checkpoints=["v3.zip"])
    diag_path = staged_repo / "bots" / "v3" / "data" / "diagnostic_states.json"
    diag_path.write_text(json.dumps([[0.1] * 4 for _ in range(3)]))

    sb3 = MagicMock()

    def _load_dispatch(path: str, device: str = "cpu") -> MagicMock:
        if "v2.zip" in path:
            return _make_stub_model(obs_dim=24, n_actions=6)
        return _make_stub_model(obs_dim=4, n_actions=6)

    sb3.PPO.load = MagicMock(side_effect=_load_dispatch)
    monkeypatch.setitem(sys.modules, "stable_baselines3", sb3)

    rc = cwd_module.main(["--version", "v3"])
    assert rc == 0

    rows = [
        json.loads(line)
        for line in (
            staged_repo / "data" / "weight_dynamics.jsonl"
        ).read_text().splitlines()
        if line.strip()
    ]
    v3_rows = [r for r in rows if r["version"] == "v3"]
    assert len(v3_rows) == 1
    row = v3_rows[0]
    # Success row (no error) with null KL — not a failure row.
    assert row["error"] is None, (
        "Obs-dim mismatch must surface as a SUCCESS row with null KL, "
        "not a failure row"
    )
    assert row["kl_from_parent"] is None
    # L2 norms still computed against the child weights.
    assert row["l2_per_layer"] is not None


def test_concurrent_append_does_not_duplicate(
    staged_repo: Path,
) -> None:
    """Inside-lock recheck blocks duplicate success rows on parallel runs.

    Simulates the TOCTOU race: pre-build a success row in the JSONL,
    then call ``_append_row`` directly with another success row that has
    the SAME ``(version, checkpoint, hash)``. The recheck inside the
    lock must short-circuit the second append, leaving exactly 1
    success row on disk (not 2).

    Failure rows are explicitly NOT deduped here so retry semantics
    keep working — a separate assertion confirms a failure row with the
    same key still appends.
    """
    jsonl_path = staged_repo / "data" / "weight_dynamics.jsonl"
    lock_path = staged_repo / "data" / ".weight_dynamics.lock"

    # Use the script's actual hash function so the dedup key matches
    # what production would compute.
    row_hash = cwd_module._row_hash(
        version="v3", checkpoint="v3.zip", canary_signature="sig",
    )

    base_row = {
        "version": "v3",
        "checkpoint": "v3.zip",
        "ts": "2026-05-01T00:00:00Z",
        "l2_per_layer": {"a": 1.0},
        "kl_from_parent": 0.1,
        "hash": row_hash,
        "canary_source": "diagnostic_states",
        "error": None,
    }

    # Pre-seed: simulate the "first parallel writer" having already
    # appended a success row.
    cwd_module._append_row(jsonl_path, lock_path, base_row)
    rows = jsonl_path.read_text().splitlines()
    assert len(rows) == 1

    # Second writer attempts the same row, with dedup key supplied.
    # The recheck inside the lock must skip the append.
    second_row = dict(base_row)
    second_row["ts"] = "2026-05-01T00:00:01Z"  # would-be fresh write
    cwd_module._append_row(
        jsonl_path,
        lock_path,
        second_row,
        success_dedup_key=("v3", "v3.zip", row_hash),
    )

    rows_after = jsonl_path.read_text().splitlines()
    assert len(rows_after) == 1, (
        "Inside-lock recheck must block the duplicate success row; "
        f"saw {len(rows_after)} rows after the second append"
    )

    # Sanity: a FAILURE row with the same key still appends (retry).
    failure_row = dict(base_row)
    failure_row["error"] = "RuntimeError: boom"
    failure_row["l2_per_layer"] = None
    failure_row["kl_from_parent"] = None
    failure_row["ts"] = "2026-05-01T00:00:02Z"
    cwd_module._append_row(jsonl_path, lock_path, failure_row)
    rows_after_failure = jsonl_path.read_text().splitlines()
    assert len(rows_after_failure) == 2, (
        "Failure rows must NOT be deduped — retry semantics depend on "
        "each attempt appending a fresh row"
    )
