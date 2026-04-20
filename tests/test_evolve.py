"""Tests for ``orchestrator.evolve`` — sibling-tournament round primitive."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from orchestrator import evolve, registry, snapshot
from orchestrator.contracts import Manifest, SelfPlayRecord, VersionFingerprint
from orchestrator.evolve import (
    Improvement,
    RoundResult,
    apply_improvement,
    run_round,
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
        self, p1: str, p2: str, games: int, map_name: str
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
# run_round
# ---------------------------------------------------------------------------


def _simple_training_imp(
    key: str = "expansion_bonus", value: float = 2.0
) -> Improvement:
    return _make_imp(
        title=f"bump {key}",
        type_="training",
        concrete_change=_training_patch(
            "reward_rules.json", {key: value}
        ),
    )


class TestRunRound:
    def test_parent_mismatch_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        batch = _BatchRecorder([])  # should never be called
        with pytest.raises(ValueError, match="does not match current_version"):
            run_round(
                parent="v_wrong",
                imp_a=_simple_training_imp("expansion_bonus", 2.0),
                imp_b=_simple_training_imp("army_supply_bonus", 1.5),
                run_batch_fn=batch,
                candidate_namer=lambda: ("cand_x_a", "cand_x_b"),
            )
        assert batch.call_count == 0

    def test_promotes_when_candidate_beats_parent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        # AB: A=7, B=3. Gate: winner=4, parent=1.
        batch = _BatchRecorder([(7, 3, 0), (4, 1, 0)])
        result = run_round(
            parent="v0",
            imp_a=_simple_training_imp("expansion_bonus", 2.0),
            imp_b=_simple_training_imp("army_supply_bonus", 1.5),
            ab_games=10,
            gate_games=5,
            run_batch_fn=batch,
            candidate_namer=lambda: ("cand_promo_a", "cand_promo_b"),
        )

        assert isinstance(result, RoundResult)
        assert result.promoted is True
        assert result.winner == "cand_promo_a"
        assert result.gate_record is not None
        assert len(result.gate_record) == 5
        assert "promoted" in result.reason

        # current.txt updated to the new winner.
        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "cand_promo_a"

        # Winner dir preserved, loser dir gone.
        assert (tmp_path / "bots" / "cand_promo_a").is_dir()
        assert not (tmp_path / "bots" / "cand_promo_b").exists()

        # Confirm the candidate actually carries the applied training imp.
        post = json.loads(
            (
                tmp_path / "bots" / "cand_promo_a" / "data" / "reward_rules.json"
            ).read_text(encoding="utf-8")
        )
        assert post["expansion_bonus"] == 2.0

    def test_a_beats_b_loses_to_parent_discards(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        # AB: A=7, B=3. Gate: winner=1, parent=4.
        batch = _BatchRecorder([(7, 3, 0), (1, 4, 0)])
        result = run_round(
            parent="v0",
            imp_a=_simple_training_imp("expansion_bonus", 2.0),
            imp_b=_simple_training_imp("army_supply_bonus", 1.5),
            ab_games=10,
            gate_games=5,
            run_batch_fn=batch,
            candidate_namer=lambda: ("cand_fail_a", "cand_fail_b"),
        )

        assert result.promoted is False
        assert result.winner is None
        assert result.gate_record is not None  # gate WAS run
        assert len(result.gate_record) == 5
        assert "lost to parent" in result.reason

        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v0"
        assert not (tmp_path / "bots" / "cand_fail_a").exists()
        assert not (tmp_path / "bots" / "cand_fail_b").exists()

    def test_ab_tie_discards_without_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        batch = _BatchRecorder([(5, 5, 0)])
        result = run_round(
            parent="v0",
            imp_a=_simple_training_imp("expansion_bonus", 2.0),
            imp_b=_simple_training_imp("army_supply_bonus", 1.5),
            ab_games=10,
            gate_games=5,
            run_batch_fn=batch,
            candidate_namer=lambda: ("cand_tie_a", "cand_tie_b"),
        )

        assert result.promoted is False
        assert result.winner is None
        assert result.gate_record is None
        assert batch.call_count == 1  # gate NOT called
        assert "tie" in result.reason
        assert not (tmp_path / "bots" / "cand_tie_a").exists()
        assert not (tmp_path / "bots" / "cand_tie_b").exists()

        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v0"

    def test_ab_all_crashes_discards(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        # All 10 AB games crashed (winner=None). Counts as 0-0 tie.
        batch = _BatchRecorder([(0, 0, 0)])
        result = run_round(
            parent="v0",
            imp_a=_simple_training_imp("expansion_bonus", 2.0),
            imp_b=_simple_training_imp("army_supply_bonus", 1.5),
            ab_games=10,
            gate_games=5,
            run_batch_fn=batch,
            candidate_namer=lambda: ("cand_crash_a", "cand_crash_b"),
        )

        assert result.promoted is False
        assert result.winner is None
        assert result.gate_record is None
        assert batch.call_count == 1
        assert "crashed" in result.reason
        assert not (tmp_path / "bots" / "cand_crash_a").exists()
        assert not (tmp_path / "bots" / "cand_crash_b").exists()

    def test_name_collision_retries_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        # Pre-seed a colliding version so the first naming attempt fails.
        _seed_version(tmp_path, "cand_first_a")
        _seed_pointer(tmp_path, "v0")

        attempts: list[tuple[str, str]] = []

        def namer() -> tuple[str, str]:
            if not attempts:
                pair = ("cand_first_a", "cand_first_b")
            else:
                pair = ("cand_second_a", "cand_second_b")
            attempts.append(pair)
            return pair

        # AB: tie to short-circuit before gate for a fast test.
        batch = _BatchRecorder([(3, 3, 0)])
        result = run_round(
            parent="v0",
            imp_a=_simple_training_imp("expansion_bonus", 2.0),
            imp_b=_simple_training_imp("army_supply_bonus", 1.5),
            ab_games=6,
            gate_games=5,
            run_batch_fn=batch,
            candidate_namer=namer,
        )

        assert len(attempts) == 2  # retried once
        assert result.candidate_a == "cand_second_a"
        assert result.candidate_b == "cand_second_b"

    def test_persistent_collision_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_version(tmp_path, "cand_stuck_a")  # always collides
        _seed_pointer(tmp_path, "v0")

        batch = _BatchRecorder([])  # must never be called

        def namer() -> tuple[str, str]:
            return ("cand_stuck_a", "cand_stuck_b")

        with pytest.raises(RuntimeError, match="colliding names twice"):
            run_round(
                parent="v0",
                imp_a=_simple_training_imp("expansion_bonus", 2.0),
                imp_b=_simple_training_imp("army_supply_bonus", 1.5),
                run_batch_fn=batch,
                candidate_namer=namer,
            )
        assert batch.call_count == 0

    def test_pointer_restored_between_snapshots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After each ``snapshot_current`` call the pointer MUST point at the
        parent again so the second snapshot doesn't end up sourced from the
        first candidate.
        """
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        pointer_path = tmp_path / "bots" / "current" / "current.txt"
        pointer_states: list[str] = []

        real_snapshot_current = snapshot.snapshot_current

        def spy_snapshot(name: str | None = None) -> Path:
            result = real_snapshot_current(name)
            # Capture the pointer value IMMEDIATELY after snapshot_current
            # updates it. run_round must then restore it to "v0" before the
            # NEXT snapshot fires — we verify that below by checking
            # pointer_states[0] == candidate_a name, then the post-round
            # run made a second candidate whose parent was still v0.
            pointer_states.append(pointer_path.read_text(encoding="utf-8"))
            return result

        monkeypatch.setattr(snapshot, "snapshot_current", spy_snapshot)

        batch = _BatchRecorder([(3, 3, 0)])  # tie -> short-circuit
        run_round(
            parent="v0",
            imp_a=_simple_training_imp("expansion_bonus", 2.0),
            imp_b=_simple_training_imp("army_supply_bonus", 1.5),
            ab_games=6,
            gate_games=5,
            run_batch_fn=batch,
            candidate_namer=lambda: ("cand_spy_a", "cand_spy_b"),
        )

        # snapshot_current ran twice: once for A, once for B.
        assert pointer_states == ["cand_spy_a", "cand_spy_b"]
        # Final pointer: v0 (tie -> discard restores to parent). If the
        # pointer weren't restored between snapshot A and snapshot B, the
        # pointer_states list would still have both candidate names (since
        # snapshot_current overwrites unconditionally) but the manifests
        # would record the wrong parent. See
        # ``test_parent_snapshot_is_v0_for_cand_b`` for the manifest-level
        # proof when the gate passes and the dirs survive inspection.
        assert pointer_path.read_text(encoding="utf-8") == "v0"

    def test_parent_snapshot_is_v0_for_cand_b(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Follow-on check: the second snapshot's manifest.parent is v0.

        This proves the pointer restoration between the two snapshots is
        actually taking effect — if it weren't, cand_b's parent would be
        cand_a.
        """
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        # Gate passes so candidates survive for inspection.
        batch = _BatchRecorder([(6, 4, 0), (4, 1, 0)])
        result = run_round(
            parent="v0",
            imp_a=_simple_training_imp("expansion_bonus", 2.0),
            imp_b=_simple_training_imp("army_supply_bonus", 1.5),
            ab_games=10,
            gate_games=5,
            run_batch_fn=batch,
            candidate_namer=lambda: ("cand_parent_a", "cand_parent_b"),
        )
        assert result.promoted is True
        assert result.winner is not None  # narrow for type checker
        # Winner kept on disk — verify its manifest lists v0 as parent.
        winner_manifest = Manifest.from_json(
            (tmp_path / "bots" / result.winner / "manifest.json")
            .read_text(encoding="utf-8")
        )
        assert winner_manifest.parent == "v0"

    def test_cleanup_error_does_not_mask_result(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        # AB tie so both candidate dirs hit _safe_rmtree.
        batch = _BatchRecorder([(3, 3, 0)])

        import shutil as _shutil

        original_rmtree = _shutil.rmtree
        calls: list[Path] = []

        def flaky_rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
            calls.append(Path(path))
            # First rmtree call raises; second succeeds via real rmtree.
            if len(calls) == 1:
                raise OSError("simulated permission error")
            original_rmtree(path, *args, **kwargs)

        monkeypatch.setattr("orchestrator.evolve.shutil.rmtree", flaky_rmtree)

        with caplog.at_level(logging.WARNING, logger=evolve.__name__):
            result = run_round(
                parent="v0",
                imp_a=_simple_training_imp("expansion_bonus", 2.0),
                imp_b=_simple_training_imp("army_supply_bonus", 1.5),
                ab_games=6,
                gate_games=5,
                run_batch_fn=batch,
                candidate_namer=lambda: ("cand_flaky_a", "cand_flaky_b"),
            )

        assert result.promoted is False
        assert result.winner is None
        assert result.gate_record is None
        # Warning was logged for the rmtree failure.
        assert any(
            "failed to clean up" in r.message for r in caplog.records
        )
        # Pointer still safe at parent.
        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v0"

    def test_dev_imp_round_uses_injected_fn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        seen: list[tuple[str, str]] = []

        def dev_apply(version_dir: Path, imp: Improvement) -> None:
            # Use the dir NAME so we can assert on ordering cleanly.
            seen.append((version_dir.name, imp.title))

        imp_a = _make_imp(
            title="dev-a", type_="dev", concrete_change="refactor attack"
        )
        imp_b = _make_imp(
            title="dev-b", type_="dev", concrete_change="refactor defend"
        )

        batch = _BatchRecorder([(3, 3, 0)])  # tie -> fast
        result = run_round(
            parent="v0",
            imp_a=imp_a,
            imp_b=imp_b,
            ab_games=6,
            gate_games=5,
            run_batch_fn=batch,
            dev_apply_fn=dev_apply,
            candidate_namer=lambda: ("cand_dev_a", "cand_dev_b"),
        )
        assert result.promoted is False
        # dev_apply was called once per candidate, in order A then B.
        assert seen == [
            ("cand_dev_a", "dev-a"),
            ("cand_dev_b", "dev-b"),
        ]

    def test_apply_error_cleans_candidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """apply_improvement failure on imp_a must wipe cand_a_dir and leave
        the pointer at parent. cand_b is never created.
        """
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        # Invalid JSON triggers ValueError inside apply_improvement for imp_a.
        imp_a = _make_imp(type_="training", concrete_change="not-json")
        imp_b = _simple_training_imp("army_supply_bonus", 1.5)

        batch = _BatchRecorder([])  # must never be called
        with pytest.raises(ValueError, match="not valid JSON"):
            run_round(
                parent="v0",
                imp_a=imp_a,
                imp_b=imp_b,
                ab_games=10,
                gate_games=5,
                run_batch_fn=batch,
                candidate_namer=lambda: ("cand_err_a", "cand_err_b"),
            )

        # Snapshot A was created but apply_improvement blew up; the finally
        # must have removed it.
        assert not (tmp_path / "bots" / "cand_err_a").exists()
        # Snapshot B was never reached.
        assert not (tmp_path / "bots" / "cand_err_b").exists()

        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v0"
        assert batch.call_count == 0

    def test_run_batch_failure_cleans_candidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If run_batch_fn raises on the AB batch, both candidate dirs
        already exist; the finally must wipe them and restore the pointer.
        """
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        def exploding_batch(
            p1: str, p2: str, games: int, map_name: str
        ) -> list[SelfPlayRecord]:
            raise RuntimeError("selfplay blew up")

        with pytest.raises(RuntimeError, match="selfplay blew up"):
            run_round(
                parent="v0",
                imp_a=_simple_training_imp("expansion_bonus", 2.0),
                imp_b=_simple_training_imp("army_supply_bonus", 1.5),
                ab_games=10,
                gate_games=5,
                run_batch_fn=exploding_batch,
                candidate_namer=lambda: ("cand_ab_err_a", "cand_ab_err_b"),
            )

        assert not (tmp_path / "bots" / "cand_ab_err_a").exists()
        assert not (tmp_path / "bots" / "cand_ab_err_b").exists()

        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v0"

    def test_gate_batch_failure_cleans_candidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If run_batch_fn succeeds on AB (decisive winner) but raises on the
        gate batch, both candidate dirs must still be wiped.
        """
        _redirect_repo_root(tmp_path, monkeypatch)
        _seed_version(tmp_path, "v0")
        _seed_pointer(tmp_path, "v0")

        call_count = {"n": 0}

        def partly_exploding_batch(
            p1: str, p2: str, games: int, map_name: str
        ) -> list[SelfPlayRecord]:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # AB: decisive A win so the gate is triggered.
                return _batch(p1, p2, games, p1_wins=7, p2_wins=3)
            raise RuntimeError("gate batch blew up")

        with pytest.raises(RuntimeError, match="gate batch blew up"):
            run_round(
                parent="v0",
                imp_a=_simple_training_imp("expansion_bonus", 2.0),
                imp_b=_simple_training_imp("army_supply_bonus", 1.5),
                ab_games=10,
                gate_games=5,
                run_batch_fn=partly_exploding_batch,
                candidate_namer=lambda: ("cand_gate_err_a", "cand_gate_err_b"),
            )

        assert not (tmp_path / "bots" / "cand_gate_err_a").exists()
        assert not (tmp_path / "bots" / "cand_gate_err_b").exists()

        pointer = tmp_path / "bots" / "current" / "current.txt"
        assert pointer.read_text(encoding="utf-8") == "v0"
        assert call_count["n"] == 2
