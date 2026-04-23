"""Tests for ``orchestrator.evolve`` — sibling-tournament round primitive."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, cast

import pytest

from orchestrator import evolve, registry, snapshot
from orchestrator.contracts import Manifest, SelfPlayRecord, VersionFingerprint
from orchestrator.evolve import (
    FitnessResult,
    Improvement,
    RegressionResult,
    apply_improvement,
    generate_pool,
    run_fitness_eval,
    run_regression_eval,
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _seed_version(root: Path, name: str) -> Path:
    """Create a minimal versioned bot dir at ``<root>/bots/<name>/``.

    Includes reward_rules.json and hyperparams.json under ``data/`` so
    training-imp patches have something to target.
    """
    version_dir = root / "bots" / name
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "VERSION").write_text(name, encoding="utf-8")

    data_dir = version_dir / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "training.db").write_text("fake-db", encoding="utf-8")
    checkpoints = data_dir / "checkpoints"
    checkpoints.mkdir(exist_ok=True)
    (checkpoints / "best.zip").write_text("fake-checkpoint", encoding="utf-8")

    (data_dir / "reward_rules.json").write_text(
        json.dumps(
            {"expansion_bonus": 1.0, "army_supply_bonus": 0.5}, indent=2
        ),
        encoding="utf-8",
    )
    (data_dir / "hyperparams.json").write_text(
        json.dumps({"learning_rate": 1e-4, "clip_range": 0.2}, indent=2),
        encoding="utf-8",
    )

    manifest = Manifest(
        version=name,
        best="best",
        previous_best=None,
        parent=None,
        git_sha="abc1234",
        timestamp="2026-04-19T00:00:00Z",
        elo=1000.0,
        fingerprint=VersionFingerprint(
            feature_dim=24, action_space_size=6, obs_spec_hash="deadbeef"
        ),
    )
    (version_dir / "manifest.json").write_text(
        manifest.to_json(), encoding="utf-8"
    )
    return version_dir


def _seed_pointer(root: Path, version: str) -> None:
    pointer = root / "bots" / "current" / "current.txt"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(version, encoding="utf-8")


def _redirect_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redirect every caller of ``_repo_root`` at *tmp_path*.

    ``snapshot``, ``registry``, and ``evolve`` all resolve filesystem paths
    through ``_repo_root``; monkeypatching each module's binding keeps the
    round self-contained in the tmp tree.
    """
    monkeypatch.setattr(registry, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(snapshot, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(evolve, "_repo_root", lambda: tmp_path)


def _make_imp(
    *,
    rank: int = 1,
    title: str = "test-imp",
    type_: Literal["training", "dev"] = "training",
    description: str = "desc",
    principle_ids: list[str] | None = None,
    expected_impact: str = "impact",
    concrete_change: str = "",
) -> Improvement:
    return Improvement(
        rank=rank,
        title=title,
        type=type_,
        description=description,
        principle_ids=principle_ids or [],
        expected_impact=expected_impact,
        concrete_change=concrete_change,
    )


def _training_patch(file: str, patch: dict[str, Any]) -> str:
    """Encode a training-imp concrete_change."""
    return json.dumps({"file": file, "patch": patch})


def _record(
    p1: str, p2: str, winner: str | None, match_id: str = "m"
) -> SelfPlayRecord:
    return SelfPlayRecord(
        match_id=match_id,
        p1_version=p1,
        p2_version=p2,
        winner=winner,
        map_name="Simple64",
        duration_s=10.0,
        seat_swap=False,
        timestamp="2026-04-19T00:00:00+00:00",
        error=None,
    )


def _batch(
    p1: str,
    p2: str,
    count: int,
    *,
    p1_wins: int,
    p2_wins: int,
    ties: int = 0,
) -> list[SelfPlayRecord]:
    """Build a deterministic ``list[SelfPlayRecord]``.

    Any remainder of ``count - (p1_wins + p2_wins + ties)`` is filled with
    ``winner=None`` (crash/draw) records, so callers can model partial
    crashes explicitly.
    """
    assert p1_wins + p2_wins + ties <= count
    out: list[SelfPlayRecord] = []
    for i in range(p1_wins):
        out.append(_record(p1, p2, p1, f"p1-win-{i}"))
    for i in range(p2_wins):
        out.append(_record(p1, p2, p2, f"p2-win-{i}"))
    for i in range(ties):
        out.append(_record(p1, p2, None, f"tie-{i}"))
    while len(out) < count:
        out.append(_record(p1, p2, None, f"crash-{len(out)}"))
    return out


class _BatchRecorder:
    """Stateful mock for ``run_batch_fn`` that returns scripted batches.

    Each call pops the next ``(p1_wins, p2_wins, ties)`` spec from the
    queue; games count comes from the real ``games`` arg. ``calls`` records
    every invocation for assertions.
    """

    def __init__(self, specs: list[tuple[int, int, int]]) -> None:
        self._specs = list(specs)
        self.calls: list[tuple[str, str, int, str]] = []

    def __call__(
        self,
        p1: str,
        p2: str,
        games: int,
        map_name: str,
        **kwargs: Any,
    ) -> list[SelfPlayRecord]:
        self.calls.append((p1, p2, games, map_name))
        if not self._specs:
            raise AssertionError(
                f"_BatchRecorder: unexpected extra call "
                f"({p1}, {p2}, games={games}); no spec left in queue"
            )
        p1_wins, p2_wins, ties = self._specs.pop(0)
        return _batch(
            p1, p2, games, p1_wins=p1_wins, p2_wins=p2_wins, ties=ties
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)


# ---------------------------------------------------------------------------
# apply_improvement
# ---------------------------------------------------------------------------


class TestApplyImprovement:
    def test_training_patch_reward_rules(self, tmp_path: Path) -> None:
        version_dir = _seed_version(tmp_path, "v0")
        imp = _make_imp(
            type_="training",
            concrete_change=_training_patch(
                "reward_rules.json", {"expansion_bonus": 3.5}
            ),
        )

        apply_improvement(version_dir, imp)

        after = json.loads(
            (version_dir / "data" / "reward_rules.json").read_text(
                encoding="utf-8"
            )
        )
        assert after["expansion_bonus"] == 3.5
        # Unrelated keys preserved.
        assert after["army_supply_bonus"] == 0.5

    def test_training_patch_multi_key_replacement(self, tmp_path: Path) -> None:
        """Patch with multiple top-level keys replaces each in place.

        ``concrete_change`` only supports flat top-level key replacement —
        a nested dict VALUE is replaced wholesale, not merged.
        """
        version_dir = _seed_version(tmp_path, "v0")
        # Add a nested-value key to exercise wholesale replacement semantics.
        rewards_path = version_dir / "data" / "reward_rules.json"
        original = json.loads(rewards_path.read_text(encoding="utf-8"))
        original["nested_rule"] = {"inner": 1, "other": 2}
        rewards_path.write_text(
            json.dumps(original, indent=2), encoding="utf-8"
        )

        imp = _make_imp(
            type_="training",
            concrete_change=_training_patch(
                "reward_rules.json",
                {
                    "expansion_bonus": 2.0,
                    "nested_rule": {"inner": 99},  # wholesale replacement
                },
            ),
        )
        apply_improvement(version_dir, imp)

        after = json.loads(rewards_path.read_text(encoding="utf-8"))
        assert after["expansion_bonus"] == 2.0
        # Wholesale replace: original "other": 2 is gone.
        assert after["nested_rule"] == {"inner": 99}
        # Unrelated top-level key preserved.
        assert after["army_supply_bonus"] == 0.5

    def test_training_patch_hyperparams(self, tmp_path: Path) -> None:
        version_dir = _seed_version(tmp_path, "v0")
        imp = _make_imp(
            type_="training",
            concrete_change=_training_patch(
                "hyperparams.json", {"learning_rate": 3e-4}
            ),
        )
        apply_improvement(version_dir, imp)
        after = json.loads(
            (version_dir / "data" / "hyperparams.json").read_text(
                encoding="utf-8"
            )
        )
        assert after["learning_rate"] == 3e-4
        assert after["clip_range"] == 0.2

    def test_training_invalid_json(self, tmp_path: Path) -> None:
        version_dir = _seed_version(tmp_path, "v0")
        imp = _make_imp(type_="training", concrete_change="not-json")
        with pytest.raises(ValueError, match="not valid JSON"):
            apply_improvement(version_dir, imp)

    def test_training_missing_file_key(self, tmp_path: Path) -> None:
        version_dir = _seed_version(tmp_path, "v0")
        imp = _make_imp(
            type_="training",
            concrete_change=json.dumps({"patch": {"x": 1}}),
        )
        with pytest.raises(ValueError, match="'file'"):
            apply_improvement(version_dir, imp)

    def test_training_target_missing(self, tmp_path: Path) -> None:
        version_dir = _seed_version(tmp_path, "v0")
        imp = _make_imp(
            type_="training",
            concrete_change=_training_patch(
                "does_not_exist.json", {"x": 1}
            ),
        )
        with pytest.raises(FileNotFoundError):
            apply_improvement(version_dir, imp)

    def test_dev_imp_dispatches_to_fn(self, tmp_path: Path) -> None:
        version_dir = _seed_version(tmp_path, "v0")
        imp = _make_imp(
            type_="dev", concrete_change="rewrite defend() to attack-move"
        )
        seen: list[tuple[Path, Improvement]] = []

        def _apply(vd: Path, im: Improvement) -> None:
            seen.append((vd, im))

        apply_improvement(version_dir, imp, dev_apply_fn=_apply)

        assert len(seen) == 1
        assert seen[0][0] == version_dir
        assert seen[0][1] is imp

    def test_dev_imp_without_fn_raises(self, tmp_path: Path) -> None:
        version_dir = _seed_version(tmp_path, "v0")
        imp = _make_imp(type_="dev", concrete_change="do a thing")
        with pytest.raises(NotImplementedError, match="dev_apply_fn"):
            apply_improvement(version_dir, imp, dev_apply_fn=None)

    def test_non_dict_target_file(self, tmp_path: Path) -> None:
        """A target data file whose JSON root is a list (not an object) must
        be rejected — the patcher only knows how to merge into dict roots.
        """
        version_dir = _seed_version(tmp_path, "v0")
        # Overwrite reward_rules.json with a top-level array.
        (version_dir / "data" / "reward_rules.json").write_text(
            json.dumps([{"expansion_bonus": 1.0}]), encoding="utf-8"
        )
        imp = _make_imp(
            type_="training",
            concrete_change=_training_patch(
                "reward_rules.json", {"expansion_bonus": 2.0}
            ),
        )
        with pytest.raises(ValueError, match="must contain a JSON object"):
            apply_improvement(version_dir, imp)

    def test_unknown_imp_type(self, tmp_path: Path) -> None:
        """apply_improvement must raise ValueError for imp.type values that
        are neither 'training' nor 'dev'.
        """
        version_dir = _seed_version(tmp_path, "v0")
        # Bypass the Literal on _make_imp to construct an invalid type.
        imp = Improvement(
            rank=1,
            title="bogus",
            type=cast(Literal["training", "dev"], "bogus-type"),
            description="d",
            principle_ids=[],
            expected_impact="i",
            concrete_change="",
        )
        with pytest.raises(ValueError, match="bogus-type"):
            apply_improvement(version_dir, imp)

    def test_improvement_json_round_trip(self) -> None:
        imp = _make_imp(
            rank=2,
            title="tune LR",
            type_="training",
            description="raise LR so PPO learns faster",
            principle_ids=["lr-decay-1", "warmup-2"],
            expected_impact="+3% WR at diff 3",
            concrete_change=_training_patch(
                "hyperparams.json", {"learning_rate": 5e-4}
            ),
        )
        restored = Improvement.from_json(imp.to_json())
        assert restored == imp

# ---------------------------------------------------------------------------
# Phase primitives (fitness / regression)
# ---------------------------------------------------------------------------


def _simple_training_imp(
    key: str = "expansion_bonus",
    value: float = 2.0,
    *,
    title: str | None = None,
) -> Improvement:
    return _make_imp(
        title=title or f"bump {key}",
        type_="training",
        concrete_change=_training_patch(
            "reward_rules.json", {key: value}
        ),
    )


def _single_namer(name: str) -> Any:
    """Return a zero-arg callable that yields *name* — one-shot."""
    calls = {"n": 0}

    def namer() -> str:
        calls["n"] += 1
        return name

    namer.calls = calls  # type: ignore[attr-defined]
    return namer


class TestRunFitnessEval:
    def test_parent_mismatch_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        batch = _BatchRecorder([])  # should never be called
        with pytest.raises(ValueError, match="does not match current_version"):
            run_fitness_eval(
                parent="v_wrong",
                imp=_simple_training_imp("expansion_bonus", 2.0),
                games=5,
                run_batch_fn=batch,
                candidate_namer=_single_namer("cand_x"),
            )
        assert batch.call_count == 0

    def test_pass_bucket_beats_parent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        # 3-2 candidate win → pass bucket.
        batch = _BatchRecorder([(3, 2, 0)])
        result = run_fitness_eval(
            parent="v0",
            imp=_simple_training_imp("expansion_bonus", 2.0),
            games=5,
            run_batch_fn=batch,
            candidate_namer=_single_namer("cand_pass"),
        )

        assert isinstance(result, FitnessResult)
        assert result.bucket == "pass"
        assert result.wins_candidate == 3
        assert result.wins_parent == 2
        assert result.candidate == "cand_pass"
        assert "pass" in result.reason
        # Scratch dir MUST be cleaned up even on pass.
        assert not (tmp_path / "bots" / "cand_pass").exists()
        # Pointer restored to parent.
        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v0"

    def test_close_bucket_one_win_short(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        batch = _BatchRecorder([(2, 3, 0)])
        result = run_fitness_eval(
            parent="v0",
            imp=_simple_training_imp("expansion_bonus", 2.0),
            games=5,
            run_batch_fn=batch,
            candidate_namer=_single_namer("cand_close"),
        )
        assert result.bucket == "close"
        assert result.wins_candidate == 2
        assert not (tmp_path / "bots" / "cand_close").exists()

    def test_fail_bucket_blowout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        # 1 win or less → fail.
        batch = _BatchRecorder([(1, 4, 0)])
        result = run_fitness_eval(
            parent="v0",
            imp=_simple_training_imp("expansion_bonus", 2.0),
            games=5,
            run_batch_fn=batch,
            candidate_namer=_single_namer("cand_fail"),
        )
        assert result.bucket == "fail"
        assert not (tmp_path / "bots" / "cand_fail").exists()

    def test_fail_bucket_all_crashes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        # 0-0 all-crashes → fail bucket.
        batch = _BatchRecorder([(0, 0, 0)])
        result = run_fitness_eval(
            parent="v0",
            imp=_simple_training_imp("expansion_bonus", 2.0),
            games=5,
            run_batch_fn=batch,
            candidate_namer=_single_namer("cand_crash"),
        )
        assert result.bucket == "fail"

    def test_dev_imp_uses_injected_fn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        seen: list[tuple[str, str]] = []

        def dev_apply(version_dir: Path, imp: Improvement) -> None:
            seen.append((version_dir.name, imp.title))

        imp = _make_imp(
            title="dev-alpha", type_="dev", concrete_change="refactor attack"
        )
        batch = _BatchRecorder([(3, 2, 0)])
        result = run_fitness_eval(
            parent="v0",
            imp=imp,
            games=5,
            run_batch_fn=batch,
            dev_apply_fn=dev_apply,
            candidate_namer=_single_namer("cand_dev"),
        )
        assert result.bucket == "pass"
        assert seen == [("cand_dev", "dev-alpha")]

    def test_apply_error_cleans_scratch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        imp = _make_imp(type_="training", concrete_change="not-json")
        batch = _BatchRecorder([])  # must never be called
        with pytest.raises(ValueError, match="not valid JSON"):
            run_fitness_eval(
                parent="v0",
                imp=imp,
                games=5,
                run_batch_fn=batch,
                candidate_namer=_single_namer("cand_err"),
            )
        assert not (tmp_path / "bots" / "cand_err").exists()
        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v0"
        assert batch.call_count == 0

    def test_run_batch_failure_cleans_scratch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        def exploding_batch(
            p1: str, p2: str, games: int, map_name: str, **kwargs: Any
        ) -> list[SelfPlayRecord]:
            raise RuntimeError("selfplay blew up")

        with pytest.raises(RuntimeError, match="selfplay blew up"):
            run_fitness_eval(
                parent="v0",
                imp=_simple_training_imp(),
                games=5,
                run_batch_fn=exploding_batch,
                candidate_namer=_single_namer("cand_boom"),
            )
        assert not (tmp_path / "bots" / "cand_boom").exists()
        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v0"

    def test_on_event_fires_fitness_lifecycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        events: list[dict[str, Any]] = []

        def fake_batch(
            p1: str,
            p2: str,
            games: int,
            map_name: str,
            **kwargs: Any,
        ) -> list[SelfPlayRecord]:
            on_game_end = kwargs.get("on_game_end")
            # 3-1 cand wins over 4 games.
            records = [
                _record(p1, p2, p1, "g-0"),
                _record(p1, p2, p1, "g-1"),
                _record(p1, p2, p2, "g-2"),
                _record(p1, p2, p1, "g-3"),
            ]
            if on_game_end is not None:
                for r in records:
                    on_game_end(r)
            return records

        result = run_fitness_eval(
            parent="v0",
            imp=_simple_training_imp(),
            games=4,
            run_batch_fn=fake_batch,
            candidate_namer=_single_namer("cand_evt"),
            on_event=lambda e: events.append(dict(e)),
        )
        assert result.bucket == "pass"
        types = [e["type"] for e in events]
        assert types == [
            "fitness_start",
            "fitness_game_end",
            "fitness_game_end",
            "fitness_game_end",
            "fitness_game_end",
        ]
        assert events[0]["candidate"] == "cand_evt"
        assert events[0]["total"] == 4
        # Running score: (1,0) (2,0) (2,1) (3,1)
        assert (events[1]["wins_cand"], events[1]["wins_parent"]) == (1, 0)
        assert (events[2]["wins_cand"], events[2]["wins_parent"]) == (2, 0)
        assert (events[3]["wins_cand"], events[3]["wins_parent"]) == (2, 1)
        assert (events[4]["wins_cand"], events[4]["wins_parent"]) == (3, 1)

    def test_on_event_callback_error_does_not_abort(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        def exploding_cb(event: dict[str, Any]) -> None:
            raise RuntimeError(f"dashboard write failed on {event['type']}")

        batch = _BatchRecorder([(3, 2, 0)])
        result = run_fitness_eval(
            parent="v0",
            imp=_simple_training_imp(),
            games=5,
            run_batch_fn=batch,
            candidate_namer=_single_namer("cand_cb"),
            on_event=exploding_cb,
        )
        assert result.bucket == "pass"



class TestRunRegressionEval:
    def test_pass_no_rollback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_version(tmp_path, "v1")
        _seed_pointer(tmp_path, "v1")

        batch = _BatchRecorder([(3, 2, 0)])
        result = run_regression_eval(
            new_parent="v1",
            prior_parent="v0",
            games=5,
            run_batch_fn=batch,
        )
        assert isinstance(result, RegressionResult)
        assert result.rolled_back is False
        assert result.wins_new == 3
        assert result.wins_prior == 2
        # Pointer unchanged.
        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v1"

    def test_rollback_leaves_pointer_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Primitive must NOT rewrite the pointer on rollback.

        The caller (``scripts/evolve.py``) runs ``git revert`` first and
        relies on the revert commit to restore the pointer via its
        reverse diff; the primitive dirtying the working tree in advance
        caused the rollback-order bug observed in run 20260422-0824.
        See the docstring on ``run_regression_eval`` for the contract.
        """
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_version(tmp_path, "v1")
        _seed_pointer(tmp_path, "v1")

        batch = _BatchRecorder([(1, 4, 0)])
        result = run_regression_eval(
            new_parent="v1",
            prior_parent="v0",
            games=5,
            run_batch_fn=batch,
        )
        assert result.rolled_back is True
        pointer = tmp_path / "bots" / "current" / "current.txt"
        # Pointer stays at v1 (the caller's responsibility to flip).
        assert pointer.read_text(encoding="utf-8") == "v1"

    def test_identical_parents_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        batch = _BatchRecorder([])
        with pytest.raises(ValueError, match="distinct new/prior parents"):
            run_regression_eval(
                new_parent="v0",
                prior_parent="v0",
                games=5,
                run_batch_fn=batch,
            )
        assert batch.call_count == 0

    def test_on_event_fires_regression_lifecycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_version(tmp_path, "v1")
        _seed_pointer(tmp_path, "v1")

        events: list[dict[str, Any]] = []

        def fake_batch(
            p1: str,
            p2: str,
            games: int,
            map_name: str,
            **kwargs: Any,
        ) -> list[SelfPlayRecord]:
            on_game_end = kwargs.get("on_game_end")
            records = [_record(p1, p2, p1, f"g-{i}") for i in range(games)]
            if on_game_end is not None:
                for r in records:
                    on_game_end(r)
            return records

        run_regression_eval(
            new_parent="v1",
            prior_parent="v0",
            games=3,
            run_batch_fn=fake_batch,
            on_event=lambda e: events.append(dict(e)),
        )
        types = [e["type"] for e in events]
        assert types == [
            "regression_start",
            "regression_game_end",
            "regression_game_end",
            "regression_game_end",
        ]
        assert events[0]["new_parent"] == "v1"
        assert events[0]["prior_parent"] == "v0"
        # Running count after each game: (1,0), (2,0), (3,0).
        assert (events[1]["wins_new"], events[1]["wins_prior"]) == (1, 0)
        assert (events[2]["wins_new"], events[2]["wins_prior"]) == (2, 0)
        assert (events[3]["wins_new"], events[3]["wins_prior"]) == (3, 0)

# ---------------------------------------------------------------------------
# generate_pool
# ---------------------------------------------------------------------------


def _well_formed_imp_dict(
    *,
    rank: int = 1,
    type_: Literal["training", "dev"] = "training",
    title: str | None = None,
) -> dict[str, Any]:
    """Return a dict that passes generate_pool's schema validator.

    Per-item ``concrete_change`` text names a rank-specific filename so the
    regex-based orthogonality fallback doesn't fire between distinct items
    — the default pool is orthogonal.
    """
    if type_ == "training":
        concrete: Any = json.dumps(
            {"file": "reward_rules.json", "patch": {"expansion_bonus": 2.0}}
        )
    else:
        concrete = (
            f"rewrite bots/v0/commands/imp_{rank}.py to attack-move"
        )
    return {
        "rank": rank,
        "title": title or f"imp-{rank}",
        "type": type_,
        "description": "detailed rationale here",
        "principle_ids": ["core-strategic-objective", "global-priorities"],
        "expected_impact": "+5% WR at diff 3",
        "concrete_change": concrete,
    }


def _well_formed_pool(n: int) -> list[dict[str, Any]]:
    """Return n well-formed imp dicts, all dev-type.

    Dev-only matches the post-filter applied by :func:`generate_pool`
    (training imps are dropped). Tests that want to exercise the filter
    itself should construct a mixed pool inline — the default is
    dev-only so the round-trip through ``generate_pool`` doesn't get
    short-circuited by the filter unexpectedly.
    """
    out: list[dict[str, Any]] = []
    for i in range(1, n + 1):
        out.append(
            _well_formed_imp_dict(
                rank=i,
                type_="dev",
                title=f"pool-imp-{i}",
            )
        )
    return out


class _ScriptedClaude:
    """Mock claude_fn that returns pre-scripted responses in order.

    Records every prompt it receives so tests can assert on prompt content.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError(
                "_ScriptedClaude ran out of responses; unexpected extra call"
            )
        return self._responses.pop(0)

    @property
    def call_count(self) -> int:
        return len(self.prompts)


def _seed_principles(root: Path, text: str = "placeholder principles") -> None:
    """Seed ``documentation/sc2/protoss/guiding-principles.md`` under root."""
    principles_dir = root / "documentation" / "sc2" / "protoss"
    principles_dir.mkdir(parents=True, exist_ok=True)
    (principles_dir / "guiding-principles.md").write_text(text, encoding="utf-8")


def _seed_parent_tree(root: Path, parent: str) -> None:
    """Seed a minimal ``bots/<parent>/`` tree with a couple of .py files."""
    parent_dir = root / "bots" / parent
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "bot.py").write_text("# bot\n", encoding="utf-8")
    (parent_dir / "__init__.py").write_text("", encoding="utf-8")
    commands_dir = parent_dir / "commands"
    commands_dir.mkdir(exist_ok=True)
    (commands_dir / "tactics.py").write_text("# tactics\n", encoding="utf-8")
    # Ensure data/ and __pycache__/ are present to verify exclusion.
    (parent_dir / "data").mkdir(exist_ok=True)
    (parent_dir / "data" / "reward_rules.json").write_text(
        "{}", encoding="utf-8"
    )
    cache = parent_dir / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "bot.cpython-312.pyc").write_text("junk", encoding="utf-8")


class TestGeneratePool:
    def _setup(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        parent: str = "v0",
        principles: str = "Parent must do X and Y. See principle-id: prd-42.",
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, parent)
        _seed_pointer(tmp_path, parent)
        _seed_parent_tree(tmp_path, parent)
        _seed_principles(tmp_path, principles)

    def test_happy_path_ten_items(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        claude = _ScriptedClaude([json.dumps(_well_formed_pool(10))])
        batch = _BatchRecorder([(1, 1, 1)])  # 3 mirror games: 1-1-1

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
        )

        assert len(pool) == 10
        assert all(isinstance(imp, Improvement) for imp in pool)
        # Ranks preserved from the scripted response.
        assert [imp.rank for imp in pool] == list(range(1, 11))
        # Alternating training / dev.
        # Pool is dev-only after the filter: training-type imps are
        # dropped in generate_pool and handled by the post-evolve PPO
        # training step instead.
        assert all(imp.type == "dev" for imp in pool)
        # principle_ids is a list of strings (schema compliance).
        assert pool[0].principle_ids == [
            "core-strategic-objective",
            "global-priorities",
        ]
        # Exactly one Claude call + one batch call.
        assert claude.call_count == 1
        assert batch.call_count == 1
        assert batch.calls[0][:3] == ("v0", "v0", 3)

    def test_markdown_fences_are_stripped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        body = json.dumps(_well_formed_pool(10))
        fenced = f"```json\n{body}\n```"
        claude = _ScriptedClaude([fenced])
        batch = _BatchRecorder([(1, 1, 1)])

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
        )
        assert len(pool) == 10
        assert pool[0].title == "pool-imp-1"

    def test_prose_preamble_is_stripped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: Opus routinely prefixes the JSON with prose.

        Observed in the 2026-04-20 Step 8 soak (commit 533a02a + timeout
        bump): response began "Now I have all the data I need. Here's
        the pool of 10 candidate improvements based on the mirror-game
        analysis.\\n\\n[...]". `json.loads` fails because the first
        char isn't '['.
        """
        self._setup(tmp_path, monkeypatch)
        body = json.dumps(_well_formed_pool(10))
        prefaced = (
            "Now I have all the data I need. Here's the pool of 10 "
            "candidate improvements based on the mirror-game analysis.\n\n"
            f"{body}\n\nLet me know if you'd like me to refine any."
        )
        claude = _ScriptedClaude([prefaced])
        batch = _BatchRecorder([(1, 1, 1)])

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
        )
        assert len(pool) == 10
        assert pool[0].title == "pool-imp-1"

    def test_short_pool_triggers_single_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        first = json.dumps(_well_formed_pool(5))  # too few
        second = json.dumps(_well_formed_pool(10))  # good
        claude = _ScriptedClaude([first, second])
        batch = _BatchRecorder([(1, 1, 1)])

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
        )

        assert len(pool) == 10
        assert claude.call_count == 2
        # Retry prompt must include the "return exactly N" instruction.
        retry_prompt = claude.prompts[1]
        assert "exactly 10" in retry_prompt
        assert "previous response" in retry_prompt.lower()

    def test_persistent_short_pool_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        claude = _ScriptedClaude(
            [
                json.dumps(_well_formed_pool(5)),
                json.dumps(_well_formed_pool(5)),
            ]
        )
        batch = _BatchRecorder([(1, 1, 1)])

        with pytest.raises(ValueError, match="5 dev improvements on retry"):
            generate_pool(
                "v0",
                mirror_games=3,
                pool_size=10,
                run_batch_fn=batch,
                claude_fn=claude,
            )
        assert claude.call_count == 2

    def test_training_imps_are_filtered_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Claude occasionally emits training imps despite the prompt —
        the filter drops them and the caller retries to hit ``pool_size``."""
        self._setup(tmp_path, monkeypatch)
        # Mixed: 5 training + 5 dev. After filter → only 5 dev → retry fires.
        mixed = []
        for i in range(1, 11):
            mixed.append(
                _well_formed_imp_dict(
                    rank=i,
                    type_="training" if i % 2 == 1 else "dev",
                    title=f"mix-{i}",
                )
            )
        # Retry returns 10 dev imps — this clears the shortfall.
        retry_pool = _well_formed_pool(10)
        claude = _ScriptedClaude(
            [json.dumps(mixed), json.dumps(retry_pool)]
        )
        batch = _BatchRecorder([(1, 1, 1)])

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
        )
        assert len(pool) == 10
        assert all(imp.type == "dev" for imp in pool)
        # Retry was needed because the filter cut the first response to 5.
        assert claude.call_count == 2

    def test_training_imps_filtered_even_after_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If both the initial AND retry responses are mixed such that
        dev count < pool_size, the final raise fires."""
        self._setup(tmp_path, monkeypatch)
        mixed_first = [
            _well_formed_imp_dict(
                rank=i,
                type_="training" if i % 2 == 1 else "dev",
                title=f"a-{i}",
            )
            for i in range(1, 11)
        ]
        mixed_second = [
            _well_formed_imp_dict(
                rank=i,
                type_="training" if i % 2 == 1 else "dev",
                title=f"b-{i}",
            )
            for i in range(1, 11)
        ]
        claude = _ScriptedClaude(
            [json.dumps(mixed_first), json.dumps(mixed_second)]
        )
        batch = _BatchRecorder([(1, 1, 1)])

        with pytest.raises(ValueError, match="5 dev improvements on retry"):
            generate_pool(
                "v0",
                mirror_games=3,
                pool_size=10,
                run_batch_fn=batch,
                claude_fn=claude,
            )

    def test_on_pool_gen_event_emits_mirror_and_claude_lifecycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """generate_pool fires mirror_start / mirror_game_end / claude_start
        / pool_ready in order, with running game counts from run_batch's
        on_game_end hook."""
        self._setup(tmp_path, monkeypatch)
        claude = _ScriptedClaude([json.dumps(_well_formed_pool(10))])
        events: list[dict[str, Any]] = []

        # Custom batch fn that fires on_game_end like the real run_batch.
        def fake_batch(
            p1: str,
            p2: str,
            games: int,
            map_name: str,
            **kwargs: Any,
        ) -> list[SelfPlayRecord]:
            on_game_end = kwargs.get("on_game_end")
            records = [_record(p1, p2, p1, f"mirror-{i}") for i in range(games)]
            if on_game_end is not None:
                for r in records:
                    on_game_end(r)
            return records

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=fake_batch,
            claude_fn=claude,
            on_pool_gen_event=lambda e: events.append(dict(e)),
        )
        assert len(pool) == 10

        types = [e["type"] for e in events]
        assert types == [
            "mirror_start",
            "mirror_game_end",
            "mirror_game_end",
            "mirror_game_end",
            "claude_start",
            "pool_ready",
        ]

        assert events[0]["total"] == 3
        assert events[0]["parent"] == "v0"
        # Running game count after each mirror game.
        assert events[1]["games_played"] == 1
        assert events[2]["games_played"] == 2
        assert events[3]["games_played"] == 3
        assert events[4]["pool_size"] == 10
        assert events[5]["pool_size"] == 10

    def test_on_pool_gen_event_callback_error_does_not_abort_generation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A raising on_pool_gen_event must be swallowed; the pool is still
        returned so a broken dashboard writer can't tank pool generation."""
        self._setup(tmp_path, monkeypatch)
        claude = _ScriptedClaude([json.dumps(_well_formed_pool(10))])
        batch = _BatchRecorder([(1, 1, 1)])

        def exploding_cb(event: dict[str, Any]) -> None:
            raise RuntimeError(f"dashboard write failed on {event['type']}")

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
            on_pool_gen_event=exploding_cb,
        )
        assert len(pool) == 10

    def test_malformed_json_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        claude = _ScriptedClaude(["this is definitely not json"])
        batch = _BatchRecorder([(1, 1, 1)])

        with pytest.raises(ValueError, match="not valid JSON"):
            generate_pool(
                "v0",
                mirror_games=3,
                pool_size=10,
                run_batch_fn=batch,
                claude_fn=claude,
            )

    def test_missing_field_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        items = _well_formed_pool(10)
        del items[3]["concrete_change"]
        claude = _ScriptedClaude([json.dumps(items)])
        batch = _BatchRecorder([(1, 1, 1)])

        with pytest.raises(ValueError, match="concrete_change") as excinfo:
            generate_pool(
                "v0",
                mirror_games=3,
                pool_size=10,
                run_batch_fn=batch,
                claude_fn=claude,
            )
        # Error should name the offending index too.
        assert "[3]" in str(excinfo.value)

    def test_wrong_type_for_principle_ids_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        items = _well_formed_pool(10)
        items[2]["principle_ids"] = "should-be-a-list"
        claude = _ScriptedClaude([json.dumps(items)])
        batch = _BatchRecorder([(1, 1, 1)])

        with pytest.raises(ValueError, match="principle_ids"):
            generate_pool(
                "v0",
                mirror_games=3,
                pool_size=10,
                run_batch_fn=batch,
                claude_fn=claude,
            )

    def test_invalid_type_value_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        items = _well_formed_pool(10)
        items[0]["type"] = "refactor"
        claude = _ScriptedClaude([json.dumps(items)])
        batch = _BatchRecorder([(1, 1, 1)])

        with pytest.raises(ValueError, match="refactor"):
            generate_pool(
                "v0",
                mirror_games=3,
                pool_size=10,
                run_batch_fn=batch,
                claude_fn=claude,
            )

    def test_invalid_rank_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        # Negative int rank.
        items_neg = _well_formed_pool(10)
        items_neg[0]["rank"] = -1
        claude_neg = _ScriptedClaude([json.dumps(items_neg)])
        batch_neg = _BatchRecorder([(1, 1, 1)])
        with pytest.raises(ValueError, match="rank"):
            generate_pool(
                "v0",
                mirror_games=3,
                pool_size=10,
                run_batch_fn=batch_neg,
                claude_fn=claude_neg,
            )

        # Non-int rank on a fresh setup.
        items_str = _well_formed_pool(10)
        items_str[0]["rank"] = "one"
        claude_str = _ScriptedClaude([json.dumps(items_str)])
        batch_str = _BatchRecorder([(1, 1, 1)])
        with pytest.raises(ValueError, match="rank"):
            generate_pool(
                "v0",
                mirror_games=3,
                pool_size=10,
                run_batch_fn=batch_str,
                claude_fn=claude_str,
            )

    def test_missing_logs_dir_does_not_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """logs/ dir doesn't exist — generate_pool still succeeds."""
        self._setup(tmp_path, monkeypatch)
        # Ensure there is no logs/ directory under tmp_path.
        assert not (tmp_path / "logs").exists()

        claude = _ScriptedClaude([json.dumps(_well_formed_pool(10))])
        batch = _BatchRecorder([(1, 1, 1)])

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
        )
        assert len(pool) == 10
        # Prompt acknowledges missing logs explicitly.
        assert "no logs/" in claude.prompts[0] or "no logs" in claude.prompts[0]

    def test_prompt_contains_key_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spy on claude_fn and verify the prompt has all required context."""
        distinctive = "AGENT-GUIDE-UNIQUE-SENTINEL-74823"
        principles_text = (
            "# Distinctive principle doc\n\n"
            f"Sentinel: {distinctive}\n\n"
            "Principle prd-42: keep bank under 500 minerals.\n"
        )
        self._setup(tmp_path, monkeypatch, principles=principles_text)
        claude = _ScriptedClaude([json.dumps(_well_formed_pool(10))])
        batch = _BatchRecorder([(1, 1, 1)])

        generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
        )

        prompt = claude.prompts[0]
        # Parent name + mirror-games count + schema field names.
        assert "v0" in prompt
        assert "mirror games" in prompt.lower()
        # Specifically check the mirror-games line — the loose "3" substring
        # would match the mirror-games count coincidentally; this pins the
        # format string location.
        assert "parent vs parent): 3" in prompt
        assert "principle_ids" in prompt
        assert "concrete_change" in prompt
        assert "expected_impact" in prompt
        # Exactly-N instruction.
        assert "EXACTLY 10" in prompt or "exactly 10" in prompt.lower()
        # Guiding-principles sentinel is pasted verbatim.
        assert distinctive in prompt
        # Source tree shows .py files and excludes __pycache__ / data/.
        assert "bots/v0/bot.py" in prompt
        assert "bots/v0/commands/tactics.py" in prompt
        assert "__pycache__" not in prompt
        # data/reward_rules.json is under data/ which is excluded; the .py
        # listing must not contain it.
        assert "reward_rules.json" not in prompt.split("## Output schema")[0]

    def test_claude_dict_concrete_change_is_coerced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If Claude returns concrete_change as a dict (not a JSON string),
        generate_pool coerces it to a JSON string so the dataclass stays typed.
        """
        self._setup(tmp_path, monkeypatch)
        items = _well_formed_pool(10)
        # Swap item 0's concrete_change for a raw dict.
        items[0]["concrete_change"] = {
            "file": "reward_rules.json",
            "patch": {"expansion_bonus": 9.0},
        }
        claude = _ScriptedClaude([json.dumps(items)])
        batch = _BatchRecorder([(1, 1, 1)])

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
        )
        assert isinstance(pool[0].concrete_change, str)
        decoded = json.loads(pool[0].concrete_change)
        assert decoded["patch"]["expansion_bonus"] == 9.0

    def test_source_tree_includes_files_at_max_depth(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A .py file sitting at exactly the source-tree depth cap must appear
        in the prompt. The previous off-by-one silently dropped these.
        """
        self._setup(tmp_path, monkeypatch)
        # _SOURCE_TREE_MAX_DEPTH is 5. Parts are counted from the version
        # root (``bots/v0``) so depth=5 means 5 path segments under it. Build
        # ``bots/v0/a/b/c/d/e/deep_leaf.py`` — the file's parent directory is
        # at depth 5 relative to version_root.
        parent_dir = tmp_path / "bots" / "v0"
        deep_dir = parent_dir / "a" / "b" / "c" / "d" / "e"
        deep_dir.mkdir(parents=True, exist_ok=True)
        (deep_dir / "deep_leaf.py").write_text("# deep\n", encoding="utf-8")

        claude = _ScriptedClaude([json.dumps(_well_formed_pool(10))])
        batch = _BatchRecorder([(1, 1, 1)])

        generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
        )

        prompt = claude.prompts[0]
        assert "bots/v0/a/b/c/d/e/deep_leaf.py" in prompt

    def test_extra_field_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unknown key on an improvement item triggers ValueError.

        Strict schema — we want advisor drift surfaced early, not silently
        dropped by ``Improvement(**kwargs)``.
        """
        self._setup(tmp_path, monkeypatch)
        items = _well_formed_pool(10)
        # files_touched is now an allowed optional field; use an actually
        # unknown field name so the schema-strictness check still fires.
        items[3]["notes"] = "some advisor preamble"
        claude = _ScriptedClaude([json.dumps(items)])
        batch = _BatchRecorder([(1, 1, 1)])

        with pytest.raises(ValueError, match="notes") as exc:
            generate_pool(
                "v0",
                mirror_games=3,
                pool_size=10,
                run_batch_fn=batch,
                claude_fn=claude,
            )
        assert "unknown field" in str(exc.value)

    def test_non_array_top_level_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Claude returns an OBJECT (``{"pool": [...]}``) instead of an array."""
        self._setup(tmp_path, monkeypatch)
        wrapped = {"pool": _well_formed_pool(10)}
        claude = _ScriptedClaude([json.dumps(wrapped)])
        batch = _BatchRecorder([(1, 1, 1)])

        with pytest.raises(ValueError, match="JSON array at top level"):
            generate_pool(
                "v0",
                mirror_games=3,
                pool_size=10,
                run_batch_fn=batch,
                claude_fn=claude,
            )

    def test_non_string_principle_ids_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """principle_ids is a list with non-string elements."""
        self._setup(tmp_path, monkeypatch)
        items = _well_formed_pool(10)
        items[2]["principle_ids"] = [1, 2, 3]
        claude = _ScriptedClaude([json.dumps(items)])
        batch = _BatchRecorder([(1, 1, 1)])

        with pytest.raises(ValueError, match="principle_ids"):
            generate_pool(
                "v0",
                mirror_games=3,
                pool_size=10,
                run_batch_fn=batch,
                claude_fn=claude,
            )

    def test_non_string_concrete_change_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """concrete_change is a number — neither a string nor a JSON object,
        so the coercion branch does not apply.
        """
        self._setup(tmp_path, monkeypatch)
        items = _well_formed_pool(10)
        items[5]["concrete_change"] = 42
        claude = _ScriptedClaude([json.dumps(items)])
        batch = _BatchRecorder([(1, 1, 1)])

        with pytest.raises(ValueError, match="concrete_change"):
            generate_pool(
                "v0",
                mirror_games=3,
                pool_size=10,
                run_batch_fn=batch,
                claude_fn=claude,
            )

    def test_non_dict_pool_item_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An array element that isn't an object (e.g. null) fails fast."""
        self._setup(tmp_path, monkeypatch)
        items: list[Any] = list(_well_formed_pool(10))
        items[4] = None  # type: ignore[assignment,unused-ignore]
        claude = _ScriptedClaude([json.dumps(items)])
        batch = _BatchRecorder([(1, 1, 1)])

        with pytest.raises(ValueError, match="JSON object"):
            generate_pool(
                "v0",
                mirror_games=3,
                pool_size=10,
                run_batch_fn=batch,
                claude_fn=claude,
            )

    def test_over_delivery_is_truncated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Claude returns MORE items than requested — truncate to pool_size.

        Ordering (rank ascending) must be preserved.
        """
        self._setup(tmp_path, monkeypatch)
        claude = _ScriptedClaude([json.dumps(_well_formed_pool(15))])
        batch = _BatchRecorder([(1, 1, 1)])

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
        )
        assert len(pool) == 10
        assert [imp.rank for imp in pool] == list(range(1, 11))
        # Only one Claude call (no retry needed since we got >= pool_size).
        assert claude.call_count == 1

    def test_files_touched_accepted_as_optional_field(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """files_touched is an optional schema field; present is OK."""
        self._setup(tmp_path, monkeypatch)
        items = _well_formed_pool(10)
        for i, item in enumerate(items):
            item["files_touched"] = [f"bots/v0/module_{i}.py"]
        claude = _ScriptedClaude([json.dumps(items)])
        batch = _BatchRecorder([(1, 1, 1)])

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
        )
        assert len(pool) == 10
        assert pool[0].files_touched == ["bots/v0/module_0.py"]
        # No retry — orthogonal files mean no conflict.
        assert claude.call_count == 1

    def test_orthogonality_conflict_triggers_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two imps touching the same file triggers ONE retry with a
        conflict list prefixed to the prompt.
        """
        self._setup(tmp_path, monkeypatch)
        # First attempt: imps 0 and 1 both edit the same file.
        conflicting = _well_formed_pool(10)
        conflicting[0]["files_touched"] = ["bots/v0/bot.py"]
        conflicting[1]["files_touched"] = ["bots/v0/bot.py"]
        for i, item in enumerate(conflicting[2:], start=2):
            item["files_touched"] = [f"bots/v0/module_{i}.py"]

        # Retry: all orthogonal.
        clean = _well_formed_pool(10)
        for i, item in enumerate(clean):
            item["files_touched"] = [f"bots/v0/clean_{i}.py"]

        claude = _ScriptedClaude(
            [json.dumps(conflicting), json.dumps(clean)]
        )
        batch = _BatchRecorder([(1, 1, 1)])

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
        )
        assert len(pool) == 10
        assert claude.call_count == 2
        # Retry prompt names the specific conflict file.
        retry_prompt = claude.prompts[1]
        assert "bots/v0/bot.py" in retry_prompt
        assert "orthogonality" in retry_prompt.lower()

    def test_orthogonality_conflict_accepted_after_second_round(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the retry ALSO has conflicts, accept the pool anyway and log."""
        self._setup(tmp_path, monkeypatch)
        conflicting = _well_formed_pool(10)
        conflicting[0]["files_touched"] = ["bots/v0/bot.py"]
        conflicting[1]["files_touched"] = ["bots/v0/bot.py"]
        still_conflicting = _well_formed_pool(10)
        still_conflicting[2]["files_touched"] = ["bots/v0/shared.py"]
        still_conflicting[3]["files_touched"] = ["bots/v0/shared.py"]

        claude = _ScriptedClaude(
            [json.dumps(conflicting), json.dumps(still_conflicting)]
        )
        batch = _BatchRecorder([(1, 1, 1)])

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
        )
        assert len(pool) == 10
        assert claude.call_count == 2

    def test_orthogonality_conflict_uses_concrete_change_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When files_touched is omitted, regex-extracted filenames from
        concrete_change still trigger the conflict check.
        """
        self._setup(tmp_path, monkeypatch)
        conflicting = _well_formed_pool(10)
        conflicting[0]["concrete_change"] = "edit bots/v0/shared.py to do X"
        conflicting[1]["concrete_change"] = "tweak bots/v0/shared.py in method Y"

        clean = _well_formed_pool(10)

        claude = _ScriptedClaude(
            [json.dumps(conflicting), json.dumps(clean)]
        )
        batch = _BatchRecorder([(1, 1, 1)])

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=10,
            run_batch_fn=batch,
            claude_fn=claude,
        )
        assert len(pool) == 10
        assert claude.call_count == 2
        assert "bots/v0/shared.py" in claude.prompts[1]

    def test_skip_mirror_skips_mirror_games(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """skip_mirror=True (pool refresh) omits the mirror-games phase."""
        self._setup(tmp_path, monkeypatch)
        claude = _ScriptedClaude([json.dumps(_well_formed_pool(5))])
        batch = _BatchRecorder([])  # must never be called

        pool = generate_pool(
            "v0",
            mirror_games=3,
            pool_size=5,
            run_batch_fn=batch,
            claude_fn=claude,
            skip_mirror=True,
        )
        assert len(pool) == 5
        assert batch.call_count == 0
        assert claude.call_count == 1


class TestDefaultClaudeFn:
    """The real subprocess wrapper — unit-tested via monkeypatched subprocess.run."""

    def test_prompt_piped_via_stdin_not_argv(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: on Windows a 40 KiB prompt in argv trips WinError 206.

        Pool prompts routinely hit 40 KiB (guiding-principles.md + source tree
        + log tails). Passing via argv used to raise FileNotFoundError that was
        mis-reported as "claude CLI not found". Verify the prompt is now
        piped via stdin and argv carries only the flags.
        """
        from orchestrator import evolve as evolve_mod

        captured: dict[str, Any] = {}

        class _FakeCompleted:
            returncode = 0
            stdout = "[]"
            stderr = ""

        def _fake_run(argv: list[str], **kwargs: Any) -> _FakeCompleted:
            captured["argv"] = argv
            captured["input"] = kwargs.get("input")
            return _FakeCompleted()

        import subprocess as _sub

        monkeypatch.setattr(_sub, "run", _fake_run)

        long_prompt = "x" * 40000
        evolve_mod._default_claude_fn(long_prompt)

        argv = captured["argv"]
        assert "claude" in argv
        assert "-p" in argv
        # Critical: the prompt must NOT be in argv (would exceed Windows limit).
        assert long_prompt not in argv
        for token in argv:
            assert len(token) < 1000, (
                f"argv token length {len(token)} suggests prompt leaked to argv"
            )
        # And it MUST be piped via stdin.
        assert captured["input"] == long_prompt


# ---------------------------------------------------------------------------
# Primitive tests for scripts/evolve._stack_apply_and_promote
# ---------------------------------------------------------------------------
#
# These exercise the REAL helper against a tmp-path-redirected filesystem,
# using the real snapshot_current, real apply_improvement, real pointer
# flips, and real manifest rewrites. The CLI tests in tests/test_evolve_cli.py
# mock the helper wholesale; the tests below cover what mocks can't — that
# the rollback branches actually clean up disk state, and that the happy
# path produces a committable bots/<new>/ tree with the correct manifest
# lineage and patched reward_rules.


_SCRIPTS_EVOLVE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "evolve.py"
)


def _load_evolve_cli_module() -> ModuleType:
    """Import ``scripts/evolve.py`` as module ``evolve_cli``.

    Mirrors the loader in ``tests/test_evolve_cli.py``: registers in
    sys.modules BEFORE exec so Python 3.14 ``@dataclass`` can resolve
    ``cls.__module__`` during KW_ONLY detection.
    """
    if "evolve_cli" in sys.modules:
        return sys.modules["evolve_cli"]
    spec = importlib.util.spec_from_file_location(
        "evolve_cli", str(_SCRIPTS_EVOLVE_PATH)
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["evolve_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


def _redirect_stack_apply_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    """Redirect every ``_repo_root`` site used by the stack-apply helper.

    The helper calls into ``orchestrator.snapshot`` (snapshot_current),
    ``orchestrator.evolve`` (_rewrite_manifest_parent, _safe_rmtree,
    apply_improvement, _restore_pointer), and reads its own
    ``evolve_cli._REPO_ROOT`` module constant. All four need to point
    at *tmp_path* for the test to exercise real filesystem effects
    in isolation.
    """
    _redirect_repo_root(tmp_path, monkeypatch)
    cli = _load_evolve_cli_module()
    monkeypatch.setattr(cli, "_REPO_ROOT", tmp_path)
    return cli


class TestStackApplyAndPromote:
    """Primitive tests for the real :func:`_stack_apply_and_promote`."""

    def test_stack_apply_promotes_all_fitness_pass_imps_into_new_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Happy path: 2 training imps apply + import-check passes + promote.

        Uses the REAL helper with tmp-path-redirected filesystem. Verifies:
        * ``bots/<new>/`` exists on disk after the call,
        * manifest parent is ``v0`` (not the pre-existing snapshot default),
        * all imp patches landed in reward_rules.json,
        * pointer points at the new version.
        """
        cli = _redirect_stack_apply_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        imp_a = _make_imp(
            rank=1,
            title="imp-a",
            type_="training",
            concrete_change=_training_patch(
                "reward_rules.json", {"expansion_bonus": 2.5}
            ),
        )
        imp_b = _make_imp(
            rank=2,
            title="imp-b",
            type_="training",
            concrete_change=_training_patch(
                "reward_rules.json", {"army_supply_bonus": 1.75}
            ),
        )

        # import_check_fn returns None on success; real subprocess import
        # cannot run under tmp_path so inject the pass signal directly.
        result = cli._stack_apply_and_promote(
            "v0",
            [imp_a, imp_b],
            dev_apply_fn=None,
            import_check_fn=lambda _new_version: None,
        )

        assert result.promoted is True
        assert result.outcome == "stack-apply-pass"
        new_version = result.new_version
        assert new_version is not None
        assert new_version != "v0"

        new_dir = tmp_path / "bots" / new_version
        assert new_dir.is_dir(), f"expected snapshot dir at {new_dir}"

        # Manifest parent must be v0 (helper rewrites it post-snapshot).
        manifest = json.loads(
            (new_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["parent"] == "v0"
        assert manifest["version"] == new_version

        # Both patches landed.
        rewards = json.loads(
            (new_dir / "data" / "reward_rules.json").read_text(encoding="utf-8")
        )
        assert rewards["expansion_bonus"] == 2.5
        assert rewards["army_supply_bonus"] == 1.75

        # Pointer flipped to new version (snapshot_current's side effect).
        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == new_version

    def test_stack_apply_import_check_failure_rolls_back_snapshot_and_pointer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Import-check failure → snapshot dir removed + pointer restored.

        Passes a failing ``import_check_fn`` and verifies the real helper
        rmtree's the candidate directory and writes the parent back to
        ``bots/current/current.txt``.
        """
        cli = _redirect_stack_apply_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        imp = _make_imp(
            rank=1,
            title="imp-fails-import",
            type_="training",
            concrete_change=_training_patch(
                "reward_rules.json", {"expansion_bonus": 99.0}
            ),
        )

        # Predict the new version name the helper will pick.
        # snapshot._next_version_name scans the registry; under this
        # tmp_path only ``v0`` exists, so the next name is ``v1``.
        result = cli._stack_apply_and_promote(
            "v0",
            [imp],
            dev_apply_fn=None,
            import_check_fn=lambda _new_version: (
                "ModuleNotFoundError: simulated failure"
            ),
        )

        assert result.promoted is False
        assert result.outcome == "stack-apply-import-fail"
        assert result.new_version is None
        assert "simulated failure" in result.reason

        # Snapshot dir must be gone.
        new_dir = tmp_path / "bots" / "v1"
        assert not new_dir.exists(), (
            f"expected {new_dir} to be cleaned up after import-check fail; "
            "it still exists"
        )

        # Pointer must be back at v0.
        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v0"

    def test_stack_apply_error_during_apply_improvement_cleans_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """apply_improvement raising mid-stack triggers rollback + re-raise.

        Injects an imp whose concrete_change is invalid JSON; real
        ``apply_improvement`` raises ``ValueError``. The helper must
        rmtree the candidate dir and restore the pointer before the
        exception propagates, so the caller's crash-row log reflects the
        ORIGINAL ``ValueError`` and the disk is clean for the next run.
        """
        cli = _redirect_stack_apply_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        bad_imp = _make_imp(
            rank=1,
            title="imp-bad-json",
            type_="training",
            concrete_change="{not-valid-json",
        )

        with pytest.raises(ValueError):
            cli._stack_apply_and_promote(
                "v0",
                [bad_imp],
                dev_apply_fn=None,
                import_check_fn=lambda _: None,
            )

        # Predicted new version dir must be removed.
        new_dir = tmp_path / "bots" / "v1"
        assert not new_dir.exists(), (
            f"expected {new_dir} to be cleaned up after apply-improvement "
            "crash; it still exists"
        )

        # Pointer must be back at v0.
        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v0"

    def test_stack_apply_commit_failure_rolls_back_and_returns_commit_fail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """commit_fn returning (False, None) triggers rollback + commit-fail.

        The H3 fix makes the helper responsible for the commit step. A
        commit failure after a successful import-check must remove the
        candidate dir, restore the pointer to parent, and return
        ``promoted=False`` with outcome ``stack-apply-commit-fail``.
        This prevents the in-process state from diverging from git.
        """
        cli = _redirect_stack_apply_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        imp = _make_imp(
            rank=1,
            title="imp-commit-fails",
            type_="training",
            concrete_change=_training_patch(
                "reward_rules.json", {"expansion_bonus": 4.0}
            ),
        )

        def failing_commit(
            new_version: str,
            generation: int,
            stacked_titles: list[str],
        ) -> tuple[bool, str | None]:
            return False, None

        result = cli._stack_apply_and_promote(
            "v0",
            [imp],
            dev_apply_fn=None,
            import_check_fn=lambda _: None,
            commit_fn=failing_commit,
            generation=1,
        )

        assert result.promoted is False
        assert result.outcome == "stack-apply-commit-fail"
        assert result.new_version is None
        assert result.promote_sha is None

        # Snapshot dir must be gone.
        new_dir = tmp_path / "bots" / "v1"
        assert not new_dir.exists(), (
            "commit-fail rollback failed to rmtree the snapshot dir; "
            "in-process state would diverge from git HEAD"
        )

        # Pointer restored to v0.
        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v0"
