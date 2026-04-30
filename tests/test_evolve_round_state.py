"""Tests for ``scripts/evolve_round_state.py`` — shared per-worker payload.

The module houses the ``CurrentRoundPayload`` + ``write_current_round_state`` +
``clear_current_round_state`` helpers that previously lived inline in
``scripts/evolve.py``. Step 2 of the evolve-parallelization plan extends
them with two optional fields (``worker_id`` / ``run_id``) so the
parallel dispatcher (Step 3+) can disambiguate worker state files.

These tests pin:

- The byte-identical JSON shape when ``worker_id`` / ``run_id`` are unset
  (so single-flight ``evolve_current_round.json`` output is unchanged).
- The new fields appear (and only appear) when set.
- Round-trip of write_current_round_state + clear_current_round_state.
- The dashboard-contract keyset matches the
  ``EvolveCurrentRound`` TypeScript interface.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module() -> ModuleType:
    """Import ``scripts/evolve_round_state.py`` as ``evolve_round_state``.

    Mirrors the module-loader pattern used by ``tests/test_evolve_cli.py``
    so the file under test lives in ``scripts/`` (not in a package).
    """
    if "evolve_round_state" in sys.modules:
        return sys.modules["evolve_round_state"]
    spec = importlib.util.spec_from_file_location(
        "evolve_round_state",
        str(_REPO_ROOT / "scripts" / "evolve_round_state.py"),
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["evolve_round_state"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod() -> ModuleType:
    return _load_module()


# ---------------------------------------------------------------------------
# CurrentRoundPayload.to_dict
# ---------------------------------------------------------------------------


class TestCurrentRoundPayloadToDict:
    def test_to_dict_active_default_omits_optional_fields(
        self, mod: ModuleType
    ) -> None:
        """When worker_id and run_id are None, JSON must NOT contain them.

        Pre-extraction ``evolve_current_round.json`` did not have these
        keys; legacy single-flight runs must produce byte-identical output.
        """
        payload = mod.CurrentRoundPayload(generation=3, phase="fitness")
        out = payload.to_dict()
        assert out["active"] is True
        assert out["generation"] == 3
        assert out["phase"] == "fitness"
        assert "worker_id" not in out
        assert "run_id" not in out
        # Spot-check a couple of always-present fields.
        assert out["imp_title"] is None
        assert out["games_played"] == 0
        assert out["games_total"] == 0
        assert out["stacked_titles"] == []
        # updated_at is the only field whose value depends on time.
        assert "updated_at" in out

    def test_to_dict_includes_worker_id_and_run_id_when_set(
        self, mod: ModuleType
    ) -> None:
        payload = mod.CurrentRoundPayload(
            generation=2,
            phase="fitness",
            worker_id=4,
            run_id="abcdef12",
        )
        out = payload.to_dict()
        assert out["worker_id"] == 4
        assert out["run_id"] == "abcdef12"

    def test_to_dict_inactive_flag(self, mod: ModuleType) -> None:
        payload = mod.CurrentRoundPayload()
        out = payload.to_dict(active=False)
        assert out["active"] is False


# ---------------------------------------------------------------------------
# write_current_round_state / clear_current_round_state
# ---------------------------------------------------------------------------


class TestStateFileWriters:
    def test_write_then_clear_round_trip(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        path = tmp_path / "evolve_round_0.json"
        payload = mod.CurrentRoundPayload(
            generation=1,
            phase="fitness",
            imp_title="bump expansion",
            imp_rank=1,
            candidate="cand_abc",
            games_total=5,
            games_played=2,
            score_cand=1,
            score_parent=1,
            worker_id=0,
            run_id="run0001",
        )
        mod.write_current_round_state(path, payload)
        assert path.exists()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["active"] is True
        assert loaded["candidate"] == "cand_abc"
        assert loaded["worker_id"] == 0
        assert loaded["run_id"] == "run0001"
        assert loaded["games_played"] == 2

        mod.clear_current_round_state(path)
        cleared = json.loads(path.read_text(encoding="utf-8"))
        assert cleared == {
            "active": False,
            "updated_at": cleared["updated_at"],
        }
        # And updated_at is well-formed.
        assert isinstance(cleared["updated_at"], str)
        assert "T" in cleared["updated_at"]

    def test_write_creates_parent_dirs(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        nested = tmp_path / "deep" / "nested" / "evolve_round_3.json"
        payload = mod.CurrentRoundPayload(generation=0)
        mod.write_current_round_state(nested, payload)
        assert nested.exists()

    def test_atomic_write_round_trips_payload(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        path = tmp_path / "rt.json"
        body: dict[str, Any] = {"x": 1, "nested": {"y": [1, 2, 3]}}
        mod.atomic_write_json(path, body)
        assert json.loads(path.read_text(encoding="utf-8")) == body


# ---------------------------------------------------------------------------
# Dashboard contract — frontend interface keyset
# ---------------------------------------------------------------------------


def test_to_dict_keys_match_frontend_evolve_current_round_interface(
    mod: ModuleType,
) -> None:
    """Pin the keyset of the active-default ``to_dict`` output.

    The shape is consumed by ``frontend/src/hooks/useEvolveRun.ts`` —
    specifically the ``EvolveCurrentRound`` interface around lines
    187-207. Drift on the Python side silently breaks the dashboard.

    Mirrors plan §5 (``documentation/plans/evolve-parallelization-plan.md``).
    Optional dispatcher-only fields (``worker_id`` / ``run_id``) are
    omitted from the active-default keyset because the TypeScript
    interface does not declare them — the frontend reads them only via
    fan-in dispatcher state, not the per-round file.
    """
    EXPECTED_KEYS = {
        "active",
        "generation",
        "phase",
        "imp_title",
        "imp_rank",
        "imp_index",
        "candidate",
        "stacked_titles",
        "new_parent",
        "prior_parent",
        "games_played",
        "games_total",
        "score_cand",
        "score_parent",
        "updated_at",
    }
    payload = mod.CurrentRoundPayload()
    out = payload.to_dict()
    actual_keys = set(out.keys())
    missing = EXPECTED_KEYS - actual_keys
    extra = actual_keys - EXPECTED_KEYS
    assert not missing, (
        f"to_dict() missing dashboard-contract keys: {sorted(missing)}"
    )
    assert not extra, (
        f"to_dict() has unexpected keys not in EvolveCurrentRound "
        f"interface: {sorted(extra)}"
    )
