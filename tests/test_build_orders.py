"""Unit tests for build order sequencing and persistence."""

from __future__ import annotations

import json
from pathlib import Path

from alpha4gate.build_orders import (
    BuildOrder,
    BuildSequencer,
    BuildStep,
    default_4gate,
    load_build_orders,
    save_build_orders,
    slug_from_name,
)


class TestBuildStep:
    def test_to_dict(self) -> None:
        step = BuildStep(supply=14, action="build", target="Pylon")
        d = step.to_dict()
        assert d == {"supply": 14, "action": "build", "target": "Pylon"}

    def test_from_dict(self) -> None:
        data = {"supply": 16, "action": "train", "target": "Stalker"}
        step = BuildStep.from_dict(data)
        assert step.supply == 16
        assert step.action == "train"
        assert step.target == "Stalker"

    def test_roundtrip(self) -> None:
        step = BuildStep(supply=20, action="research", target="Blink")
        assert BuildStep.from_dict(step.to_dict()) == step


class TestBuildOrder:
    def test_to_dict(self) -> None:
        order = BuildOrder(
            id="test",
            name="Test",
            steps=[BuildStep(supply=14, action="build", target="Pylon")],
        )
        d = order.to_dict()
        assert d["id"] == "test"
        assert len(d["steps"]) == 1

    def test_from_dict(self) -> None:
        data = {
            "id": "test",
            "name": "Test",
            "source": "spawning_tool",
            "steps": [{"supply": 14, "action": "build", "target": "Pylon"}],
        }
        order = BuildOrder.from_dict(data)
        assert order.id == "test"
        assert order.source == "spawning_tool"
        assert len(order.steps) == 1

    def test_default_source(self) -> None:
        data = {"id": "x", "name": "X", "steps": []}
        order = BuildOrder.from_dict(data)
        assert order.source == "manual"


class TestBuildSequencer:
    def _make_sequencer(self) -> BuildSequencer:
        order = BuildOrder(
            id="test",
            name="Test",
            steps=[
                BuildStep(supply=14, action="build", target="Pylon"),
                BuildStep(supply=16, action="build", target="Gateway"),
                BuildStep(supply=20, action="build", target="CyberneticsCore"),
            ],
        )
        return BuildSequencer(order)

    def test_starts_at_zero(self) -> None:
        seq = self._make_sequencer()
        assert seq.current_index == 0
        assert not seq.is_complete

    def test_current_step(self) -> None:
        seq = self._make_sequencer()
        step = seq.current_step
        assert step is not None
        assert step.target == "Pylon"

    def test_should_execute_below_supply(self) -> None:
        seq = self._make_sequencer()
        assert not seq.should_execute(10)

    def test_should_execute_at_supply(self) -> None:
        seq = self._make_sequencer()
        assert seq.should_execute(14)

    def test_should_execute_above_supply(self) -> None:
        seq = self._make_sequencer()
        assert seq.should_execute(20)

    def test_advance_returns_completed_step(self) -> None:
        seq = self._make_sequencer()
        completed = seq.advance()
        assert completed is not None
        assert completed.target == "Pylon"
        assert seq.current_index == 1

    def test_advance_through_all_steps(self) -> None:
        seq = self._make_sequencer()
        targets = []
        while not seq.is_complete:
            step = seq.advance()
            assert step is not None
            targets.append(step.target)
        assert targets == ["Pylon", "Gateway", "CyberneticsCore"]
        assert seq.is_complete

    def test_advance_when_complete_returns_none(self) -> None:
        seq = self._make_sequencer()
        for _ in range(3):
            seq.advance()
        assert seq.advance() is None

    def test_current_step_none_when_complete(self) -> None:
        seq = self._make_sequencer()
        for _ in range(3):
            seq.advance()
        assert seq.current_step is None

    def test_should_execute_false_when_complete(self) -> None:
        seq = self._make_sequencer()
        for _ in range(3):
            seq.advance()
        assert not seq.should_execute(100)

    def test_reset(self) -> None:
        seq = self._make_sequencer()
        seq.advance()
        seq.advance()
        seq.reset()
        assert seq.current_index == 0
        assert not seq.is_complete

    def test_total_steps(self) -> None:
        seq = self._make_sequencer()
        assert seq.total_steps == 3


class TestSlugFromName:
    def test_simple(self) -> None:
        assert slug_from_name("4 Gate") == "4-gate"

    def test_multi_word(self) -> None:
        assert slug_from_name("4-Gate Timing Push") == "4-gate-timing-push"

    def test_already_slug(self) -> None:
        assert slug_from_name("stalker-expand") == "stalker-expand"


class TestPersistence:
    def test_save_and_load(self, tmp_path: Path) -> None:
        orders = [default_4gate()]
        path = tmp_path / "build_orders.json"
        save_build_orders(orders, path)
        loaded = load_build_orders(path)
        assert len(loaded) == 1
        assert loaded[0].id == "4gate"
        assert len(loaded[0].steps) == 9

    def test_load_nonexistent_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.json"
        assert load_build_orders(path) == []

    def test_saved_file_is_valid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "build_orders.json"
        save_build_orders([default_4gate()], path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "orders" in data
        assert len(data["orders"]) == 1


class TestDefault4Gate:
    def test_has_9_steps(self) -> None:
        order = default_4gate()
        assert len(order.steps) == 9

    def test_starts_with_pylon(self) -> None:
        order = default_4gate()
        assert order.steps[0].target == "Pylon"

    def test_id_is_4gate(self) -> None:
        order = default_4gate()
        assert order.id == "4gate"
