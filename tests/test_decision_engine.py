"""Unit tests for the strategic state machine."""

from __future__ import annotations

from alpha4gate.build_orders import BuildOrder, BuildStep
from alpha4gate.decision_engine import DecisionEngine, GameSnapshot, StrategicState


def _simple_build_order(num_steps: int = 3) -> BuildOrder:
    """Create a simple build order with N steps."""
    steps = [BuildStep(supply=10 + i * 2, action="build", target="Pylon") for i in range(num_steps)]
    return BuildOrder(id="test", name="Test Order", steps=steps)


class TestDecisionEngineInit:
    def test_starts_in_opening(self) -> None:
        engine = DecisionEngine()
        assert engine.state == StrategicState.OPENING

    def test_uses_default_4gate_when_no_order_given(self) -> None:
        engine = DecisionEngine()
        assert engine.sequencer.order.id == "4gate"

    def test_uses_custom_build_order(self) -> None:
        order = _simple_build_order()
        engine = DecisionEngine(build_order=order)
        assert engine.sequencer.order.id == "test"


class TestStateTransitions:
    def test_stays_in_opening_while_build_order_incomplete(self) -> None:
        order = _simple_build_order(3)
        engine = DecisionEngine(build_order=order)
        snap = GameSnapshot(supply_used=5)
        assert engine.evaluate(snap) == StrategicState.OPENING

    def test_transitions_to_expand_when_build_order_complete(self) -> None:
        order = _simple_build_order(1)
        engine = DecisionEngine(build_order=order)
        # Complete the build order
        engine.sequencer.advance()
        snap = GameSnapshot(supply_used=20)
        assert engine.evaluate(snap) == StrategicState.EXPAND

    def test_transitions_to_defend_when_enemy_near_base(self) -> None:
        order = _simple_build_order(1)
        engine = DecisionEngine(build_order=order)
        snap = GameSnapshot(enemy_army_near_base=True)
        assert engine.evaluate(snap) == StrategicState.DEFEND

    def test_defend_overrides_opening(self) -> None:
        engine = DecisionEngine()
        snap = GameSnapshot(enemy_army_near_base=True)
        assert engine.evaluate(snap) == StrategicState.DEFEND

    def test_transitions_to_attack_with_enough_army(self) -> None:
        order = _simple_build_order(1)
        engine = DecisionEngine(build_order=order)
        engine.sequencer.advance()
        # First evaluate to get out of opening
        engine.evaluate(GameSnapshot(supply_used=20))
        # Now with enough army supply
        snap = GameSnapshot(army_supply=25, supply_used=30)
        assert engine.evaluate(snap) == StrategicState.ATTACK

    def test_falls_back_to_expand_when_army_depleted(self) -> None:
        order = _simple_build_order(1)
        engine = DecisionEngine(build_order=order)
        engine.sequencer.advance()
        engine.evaluate(GameSnapshot(supply_used=20))
        # Get to attack state
        engine.evaluate(GameSnapshot(army_supply=25, supply_used=30))
        assert engine.state == StrategicState.ATTACK
        # Army depleted
        snap = GameSnapshot(army_supply=5, supply_used=10)
        assert engine.evaluate(snap) == StrategicState.EXPAND

    def test_transitions_to_late_game(self) -> None:
        order = _simple_build_order(1)
        engine = DecisionEngine(build_order=order)
        engine.sequencer.advance()
        engine.evaluate(GameSnapshot(supply_used=20))
        snap = GameSnapshot(base_count=3, game_time_seconds=500.0, supply_used=50)
        assert engine.evaluate(snap) == StrategicState.LATE_GAME

    def test_defend_clears_to_expand(self) -> None:
        order = _simple_build_order(1)
        engine = DecisionEngine(build_order=order)
        engine.sequencer.advance()
        engine.evaluate(GameSnapshot(supply_used=20))
        # Go to defend
        engine.evaluate(GameSnapshot(enemy_army_near_base=True))
        assert engine.state == StrategicState.DEFEND
        # Threat cleared
        snap = GameSnapshot(enemy_army_near_base=False, supply_used=15)
        assert engine.evaluate(snap) == StrategicState.EXPAND


class TestDecisionLog:
    def test_logs_state_transition(self) -> None:
        order = _simple_build_order(1)
        engine = DecisionEngine(build_order=order)
        engine.sequencer.advance()
        snap = GameSnapshot(supply_used=20, game_time_seconds=120.0)
        engine.evaluate(snap, game_step=1000)
        assert len(engine.decision_log) == 1
        entry = engine.decision_log[0]
        assert entry.from_state == "opening"
        assert entry.to_state == "expand"
        assert entry.game_step == 1000
        assert "complete" in entry.reason.lower()

    def test_no_log_when_state_unchanged(self) -> None:
        engine = DecisionEngine()
        snap = GameSnapshot(supply_used=5)
        engine.evaluate(snap)
        engine.evaluate(snap)
        assert len(engine.decision_log) == 0

    def test_claude_advice_included_in_log(self) -> None:
        order = _simple_build_order(1)
        engine = DecisionEngine(build_order=order)
        engine.sequencer.advance()
        engine.set_claude_advice("Consider double forge")
        snap = GameSnapshot(supply_used=20)
        engine.evaluate(snap)
        assert engine.decision_log[0].claude_advice == "Consider double forge"

    def test_claude_advice_cleared_after_transition(self) -> None:
        order = _simple_build_order(1)
        engine = DecisionEngine(build_order=order)
        engine.sequencer.advance()
        engine.set_claude_advice("Some advice")
        engine.evaluate(GameSnapshot(supply_used=20))
        # Second transition should not carry the old advice
        engine.evaluate(GameSnapshot(enemy_army_near_base=True))
        assert engine.decision_log[1].claude_advice is None

    def test_log_entry_serialization(self) -> None:
        order = _simple_build_order(1)
        engine = DecisionEngine(build_order=order)
        engine.sequencer.advance()
        engine.evaluate(GameSnapshot(supply_used=20, game_time_seconds=60.0), game_step=500)
        d = engine.decision_log[0].to_dict()
        assert d["game_step"] == 500
        assert d["from_state"] == "opening"
        assert d["to_state"] == "expand"
        assert "reason" in d


class TestFortifyState:
    """Tests for FORTIFY strategic state transitions."""

    def _make_engine(
        self,
        fortify_trigger_ratio: float = 1.5,
        attack_supply_ratio: float = 1.2,
    ) -> DecisionEngine:
        """Create a DecisionEngine past the OPENING phase."""
        order = _simple_build_order(1)
        engine = DecisionEngine(
            build_order=order,
            fortify_trigger_ratio=fortify_trigger_ratio,
            attack_supply_ratio=attack_supply_ratio,
        )
        engine.sequencer.advance()
        engine.evaluate(GameSnapshot(supply_used=20))  # → EXPAND
        assert engine.state == StrategicState.EXPAND
        return engine

    def test_enters_fortify_when_outgunned_and_retreated(self) -> None:
        engine = self._make_engine(fortify_trigger_ratio=1.5)
        engine._recently_retreated = True
        # enemy 30 > own 10 * 1.5 = 15
        snap = GameSnapshot(army_supply=10, enemy_army_supply_visible=30)
        assert engine.evaluate(snap) == StrategicState.FORTIFY

    def test_does_not_enter_fortify_without_retreat(self) -> None:
        engine = self._make_engine(fortify_trigger_ratio=1.5)
        # Not retreated — should not fortify
        snap = GameSnapshot(army_supply=10, enemy_army_supply_visible=30)
        result = engine.evaluate(snap)
        assert result != StrategicState.FORTIFY

    def test_does_not_enter_fortify_when_supply_not_exceeded(self) -> None:
        engine = self._make_engine(fortify_trigger_ratio=1.5)
        engine._recently_retreated = True
        # enemy 14 is NOT > own 10 * 1.5 = 15
        snap = GameSnapshot(army_supply=10, enemy_army_supply_visible=14)
        result = engine.evaluate(snap)
        assert result != StrategicState.FORTIFY

    def test_exits_fortify_when_supply_recovers(self) -> None:
        engine = self._make_engine(fortify_trigger_ratio=1.5, attack_supply_ratio=1.2)
        engine._recently_retreated = True
        # Enter fortify
        engine.evaluate(
            GameSnapshot(army_supply=10, enemy_army_supply_visible=30)
        )
        assert engine.state == StrategicState.FORTIFY

        # Supply recovers: own 36 >= enemy 30 * 1.2 = 36
        snap = GameSnapshot(army_supply=36, enemy_army_supply_visible=30)
        result = engine.evaluate(snap)
        assert result != StrategicState.FORTIFY

    def test_exits_fortify_when_no_enemy_visible(self) -> None:
        engine = self._make_engine(fortify_trigger_ratio=1.5)
        engine._recently_retreated = True
        engine.evaluate(
            GameSnapshot(army_supply=10, enemy_army_supply_visible=30)
        )
        assert engine.state == StrategicState.FORTIFY

        # Enemy disappears
        snap = GameSnapshot(army_supply=10, enemy_army_supply_visible=0)
        result = engine.evaluate(snap)
        assert result == StrategicState.EXPAND

    def test_defend_overrides_fortify(self) -> None:
        engine = self._make_engine(fortify_trigger_ratio=1.5)
        engine._recently_retreated = True
        engine.evaluate(
            GameSnapshot(army_supply=10, enemy_army_supply_visible=30)
        )
        assert engine.state == StrategicState.FORTIFY

        # Enemy near base → DEFEND takes priority
        snap = GameSnapshot(
            army_supply=10,
            enemy_army_supply_visible=30,
            enemy_army_near_base=True,
        )
        assert engine.evaluate(snap) == StrategicState.DEFEND

    def test_stays_in_fortify_while_outgunned(self) -> None:
        engine = self._make_engine(fortify_trigger_ratio=1.5, attack_supply_ratio=1.2)
        engine._recently_retreated = True
        engine.evaluate(
            GameSnapshot(army_supply=10, enemy_army_supply_visible=30)
        )
        assert engine.state == StrategicState.FORTIFY

        # Still outgunned: own 20 < enemy 30 * 1.2 = 36
        snap = GameSnapshot(army_supply=20, enemy_army_supply_visible=30)
        assert engine.evaluate(snap) == StrategicState.FORTIFY

    def test_fortify_transition_reason(self) -> None:
        engine = self._make_engine(fortify_trigger_ratio=1.5)
        engine._recently_retreated = True
        engine.evaluate(
            GameSnapshot(army_supply=10, enemy_army_supply_visible=30)
        )
        assert len(engine.decision_log) >= 2  # opening→expand, expand→fortify
        fortify_entry = engine.decision_log[-1]
        assert fortify_entry.to_state == "fortify"
        assert "Fortifying" in fortify_entry.reason


