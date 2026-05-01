"""Tests for ``scripts/evolve_worker.py`` — per-worker fitness-eval CLI.

The worker is a thin wrapper around
:func:`orchestrator.evolve.run_fitness_eval`. We don't run real SC2 here
(that's the real-SC2 e2e operator action). Instead we monkeypatch
``run_fitness_eval`` in the worker's module namespace and pin:

- argparse contract (required args, defaults).
- Success path: result is serialized to ``--result-path`` via
  ``FitnessResult.to_json()``; round-state file written during the eval
  and cleared on exit.
- Crash path: any exception from ``run_fitness_eval`` writes a
  ``{"crash": True, ...}`` payload, exits 1, and clears round-state.
- Default ``run-id`` generation (uuid hex[:8]).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from orchestrator.contracts import SelfPlayRecord
from orchestrator.evolve import FitnessResult, Improvement

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module() -> ModuleType:
    if "evolve_worker" in sys.modules:
        return sys.modules["evolve_worker"]
    spec = importlib.util.spec_from_file_location(
        "evolve_worker",
        str(_REPO_ROOT / "scripts" / "evolve_worker.py"),
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["evolve_worker"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    # Ensure each test sees a fresh module so monkeypatched globals don't
    # leak across tests.
    sys.modules.pop("evolve_worker", None)
    return _load_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_imp(title: str = "bump expansion") -> Improvement:
    return Improvement(
        rank=1,
        title=title,
        type=cast(Any, "training"),
        description="d",
        principle_ids=["econ-1"],
        expected_impact="impact",
        concrete_change=json.dumps(
            {"file": "reward_rules.json", "patch": {"expansion_bonus": 3.0}}
        ),
    )


def _record(p1: str, p2: str, winner: str | None) -> SelfPlayRecord:
    return SelfPlayRecord(
        match_id="m",
        p1_version=p1,
        p2_version=p2,
        winner=winner,
        map_name="Simple64",
        duration_s=1.0,
        seat_swap=False,
        timestamp="2026-04-30T00:00:00+00:00",
        error=None,
    )


def _write_imp(tmp_path: Path, imp: Improvement) -> Path:
    p = tmp_path / "imp.json"
    p.write_text(imp.to_json(), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


class TestArgparse:
    def test_missing_required_args_exits(self, worker: ModuleType) -> None:
        parser = worker.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_valid_args_parse(
        self, worker: ModuleType, tmp_path: Path
    ) -> None:
        parser = worker.build_parser()
        ns = parser.parse_args(
            [
                "--parent",
                "v0",
                "--imp-json",
                str(tmp_path / "x.json"),
                "--worker-id",
                "2",
                "--result-path",
                str(tmp_path / "r.json"),
            ]
        )
        assert ns.parent == "v0"
        assert ns.worker_id == 2
        assert ns.games_per_eval == 5
        assert ns.map_name == "Simple64"
        assert ns.game_time_limit == 1800
        assert ns.hard_timeout == pytest.approx(2700.0)
        assert ns.run_id is None

    def test_overrides_picked_up(
        self, worker: ModuleType, tmp_path: Path
    ) -> None:
        parser = worker.build_parser()
        ns = parser.parse_args(
            [
                "--parent",
                "v3",
                "--imp-json",
                str(tmp_path / "x.json"),
                "--worker-id",
                "1",
                "--result-path",
                str(tmp_path / "r.json"),
                "--games-per-eval",
                "1",
                "--map",
                "AcropolisLE",
                "--game-time-limit",
                "600",
                "--hard-timeout",
                "900",
                "--run-id",
                "abcd1234",
                "--state-dir",
                str(tmp_path / "state"),
            ]
        )
        assert ns.games_per_eval == 1
        assert ns.map_name == "AcropolisLE"
        assert ns.game_time_limit == 600
        assert ns.hard_timeout == pytest.approx(900.0)
        assert ns.run_id == "abcd1234"
        assert ns.state_dir == tmp_path / "state"


# ---------------------------------------------------------------------------
# main() — success path
# ---------------------------------------------------------------------------


class TestRunSuccess:
    def test_writes_fitness_result_and_clears_state(
        self,
        worker: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        imp = _make_imp()
        imp_path = _write_imp(tmp_path, imp)
        result_path = tmp_path / "result.json"
        state_dir = tmp_path / "state"

        # Capture the on_event callback so we can drive a write to the
        # round-state file mid-eval.
        captured: dict[str, Any] = {}

        def fake_run_fitness_eval(
            parent: str,
            imp_arg: Improvement,
            **kwargs: Any,
        ) -> FitnessResult:
            captured["parent"] = parent
            captured["imp"] = imp_arg
            captured["kwargs"] = kwargs
            on_event = kwargs.get("on_event")
            assert on_event is not None
            on_event(
                {
                    "type": "fitness_start",
                    "candidate": "cand_xyz",
                    "imp_title": imp_arg.title,
                    "parent": parent,
                    "total": kwargs["games"],
                }
            )
            on_event(
                {
                    "type": "fitness_game_end",
                    "wins_cand": 1,
                    "wins_parent": 0,
                }
            )
            # Confirm round-state file exists with the live progress.
            state_path = state_dir / "evolve_round_2.json"
            assert state_path.exists()
            live = json.loads(state_path.read_text(encoding="utf-8"))
            assert live["active"] is True
            assert live["worker_id"] == 2
            assert live["run_id"] == "deadbeef"
            assert live["games_played"] == 1
            assert live["score_cand"] == 1
            return FitnessResult(
                parent=parent,
                candidate="cand_xyz",
                imp=imp_arg,
                record=[_record("cand_xyz", parent, "cand_xyz")],
                wins_candidate=1,
                wins_parent=0,
                games=1,
                bucket="pass",
                reason="fitness pass: 1-0",
            )

        monkeypatch.setattr(
            worker, "run_fitness_eval", fake_run_fitness_eval
        )

        rc = worker.main(
            [
                "--parent",
                "v0",
                "--imp-json",
                str(imp_path),
                "--worker-id",
                "2",
                "--result-path",
                str(result_path),
                "--games-per-eval",
                "1",
                "--run-id",
                "deadbeef",
                "--state-dir",
                str(state_dir),
            ]
        )
        assert rc == 0
        assert captured["parent"] == "v0"
        assert captured["imp"].title == imp.title
        assert captured["kwargs"]["games"] == 1
        assert captured["kwargs"]["map_name"] == "Simple64"
        # Worker MUST pass dev_apply_fn so dev-type imps reach the real
        # sub-agent. Without it, ``apply_improvement`` raises
        # ``NotImplementedError`` for any dev imp and the worker exits 1.
        # Caught only after the first parallel smoke gate ran with two
        # dev-type imps, both crashing — fixed by importing
        # ``spawn_dev_subagent`` and threading it through.
        from orchestrator.evolve_dev_apply import spawn_dev_subagent

        assert captured["kwargs"]["dev_apply_fn"] is spawn_dev_subagent

        # Result file is a round-trippable FitnessResult.
        result_text = result_path.read_text(encoding="utf-8")
        result = FitnessResult.from_json(result_text)
        assert result.parent == "v0"
        assert result.candidate == "cand_xyz"
        assert result.bucket == "pass"
        assert result.wins_candidate == 1

        # Round-state file is cleared (active=False).
        state_path = state_dir / "evolve_round_2.json"
        assert state_path.exists()
        cleared = json.loads(state_path.read_text(encoding="utf-8"))
        assert cleared["active"] is False

    def test_default_run_id_generated_when_omitted(
        self,
        worker: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        imp = _make_imp()
        imp_path = _write_imp(tmp_path, imp)
        result_path = tmp_path / "result.json"
        state_dir = tmp_path / "state"

        seen_run_ids: list[str] = []

        def fake_run_fitness_eval(
            parent: str,
            imp_arg: Improvement,
            **kwargs: Any,
        ) -> FitnessResult:
            on_event = kwargs["on_event"]
            on_event(
                {
                    "type": "fitness_start",
                    "candidate": "cand",
                    "imp_title": imp_arg.title,
                    "parent": parent,
                    "total": 1,
                }
            )
            state_path = state_dir / "evolve_round_0.json"
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            seen_run_ids.append(payload["run_id"])
            return FitnessResult(
                parent=parent,
                candidate="cand",
                imp=imp_arg,
                record=[],
                wins_candidate=0,
                wins_parent=0,
                games=0,
                bucket="fail",
                reason="ok",
            )

        monkeypatch.setattr(
            worker, "run_fitness_eval", fake_run_fitness_eval
        )

        rc = worker.main(
            [
                "--parent",
                "v0",
                "--imp-json",
                str(imp_path),
                "--worker-id",
                "0",
                "--result-path",
                str(result_path),
                "--state-dir",
                str(state_dir),
            ]
        )
        assert rc == 0
        assert len(seen_run_ids) == 1
        # uuid.uuid4().hex[:8] is 8 lowercase hex chars.
        assert len(seen_run_ids[0]) == 8
        assert all(c in "0123456789abcdef" for c in seen_run_ids[0])


# ---------------------------------------------------------------------------
# main() — crash path
# ---------------------------------------------------------------------------


class TestRunCrash:
    def test_crash_payload_written_and_state_cleared(
        self,
        worker: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        imp = _make_imp()
        imp_path = _write_imp(tmp_path, imp)
        result_path = tmp_path / "result.json"
        state_dir = tmp_path / "state"

        def fake_run_fitness_eval(*args: Any, **kwargs: Any) -> FitnessResult:
            raise RuntimeError("synthetic SC2 crash")

        monkeypatch.setattr(
            worker, "run_fitness_eval", fake_run_fitness_eval
        )

        rc = worker.main(
            [
                "--parent",
                "v0",
                "--imp-json",
                str(imp_path),
                "--worker-id",
                "1",
                "--result-path",
                str(result_path),
                "--state-dir",
                str(state_dir),
            ]
        )
        assert rc == 1
        crash_payload = json.loads(result_path.read_text(encoding="utf-8"))
        assert crash_payload["crash"] is True
        assert crash_payload["error_type"] == "RuntimeError"
        assert "synthetic SC2 crash" in crash_payload["error_message"]
        assert "Traceback" in crash_payload["traceback"]

        # Round-state cleared even though the eval crashed.
        state_path = state_dir / "evolve_round_1.json"
        assert state_path.exists()
        cleared = json.loads(state_path.read_text(encoding="utf-8"))
        assert cleared["active"] is False

    def test_invalid_imp_json_writes_crash_and_exits_1(
        self,
        worker: ModuleType,
        tmp_path: Path,
    ) -> None:
        bad_imp = tmp_path / "broken.json"
        bad_imp.write_text("not-json-at-all", encoding="utf-8")
        result_path = tmp_path / "result.json"

        rc = worker.main(
            [
                "--parent",
                "v0",
                "--imp-json",
                str(bad_imp),
                "--worker-id",
                "0",
                "--result-path",
                str(result_path),
                "--state-dir",
                str(tmp_path / "state"),
            ]
        )
        assert rc == 1
        assert result_path.exists()
        crash = json.loads(result_path.read_text(encoding="utf-8"))
        assert crash["crash"] is True
        # JSONDecodeError is the most likely error_type; allow any subclass.
        assert "Decode" in crash["error_type"] or "Value" in crash["error_type"]

    def test_unwritable_result_path_returns_1(
        self,
        worker: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Result-write failure (PermissionError) still clears round-state.

        Covers the inner crash-write-after-success-eval branch. Without
        the outer try/finally in main() this would leave the round-state
        file with ``active=True`` and the dispatcher would misclassify
        the slot as alive forever.
        """
        imp = _make_imp()
        imp_path = _write_imp(tmp_path, imp)
        result_path = tmp_path / "result.json"
        state_dir = tmp_path / "state"

        # Successful eval — returns a real FitnessResult.
        def fake_run_fitness_eval(
            parent: str, imp_arg: Improvement, **kwargs: Any
        ) -> FitnessResult:
            return FitnessResult(
                parent=parent,
                candidate="cand",
                imp=imp_arg,
                record=[],
                wins_candidate=1,
                wins_parent=0,
                games=1,
                bucket="pass",
                reason="ok",
            )

        monkeypatch.setattr(
            worker, "run_fitness_eval", fake_run_fitness_eval
        )

        # Make atomic_write_json blow up specifically on the result-path
        # write. The round-state writes (which happen for a different
        # path) must keep working so we can verify the slot is cleared.
        original_atomic_write = worker.atomic_write_json

        def flaky_atomic_write(path: Path, payload: Any) -> None:
            if Path(path) == result_path:
                raise PermissionError(
                    f"synthetic perm denied on {path}"
                )
            original_atomic_write(path, payload)

        monkeypatch.setattr(
            worker, "atomic_write_json", flaky_atomic_write
        )

        rc = worker.main(
            [
                "--parent",
                "v0",
                "--imp-json",
                str(imp_path),
                "--worker-id",
                "3",
                "--result-path",
                str(result_path),
                "--state-dir",
                str(state_dir),
            ]
        )
        assert rc == 1

        # Round-state file must be cleared (active=False) even though
        # both the result-write AND the crash-write hit the flaky helper.
        state_path = state_dir / "evolve_round_3.json"
        assert state_path.exists()
        cleared = json.loads(state_path.read_text(encoding="utf-8"))
        assert cleared["active"] is False
