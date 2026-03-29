"""Strategy state machine, build order queue, and state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from alpha4gate.build_orders import BuildOrder, BuildSequencer, default_4gate


class StrategicState(StrEnum):
    """Top-level strategic states for the bot."""

    OPENING = "opening"
    EXPAND = "expand"
    ATTACK = "attack"
    DEFEND = "defend"
    LATE_GAME = "late_game"


@dataclass
class GameSnapshot:
    """Minimal game state needed for decision-making."""

    supply_used: int = 0
    supply_cap: int = 0
    minerals: int = 0
    vespene: int = 0
    army_supply: int = 0
    worker_count: int = 0
    base_count: int = 1
    enemy_army_near_base: bool = False
    enemy_army_supply_visible: int = 0
    game_time_seconds: float = 0.0


@dataclass
class DecisionEntry:
    """A single decision audit log entry."""

    game_step: int
    game_time_seconds: float
    from_state: str
    to_state: str
    reason: str
    claude_advice: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "game_step": self.game_step,
            "game_time_seconds": self.game_time_seconds,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "reason": self.reason,
            "claude_advice": self.claude_advice,
        }


class DecisionEngine:
    """Strategic state machine that drives top-level bot decisions.

    The engine manages state transitions between opening, expand, attack,
    defend, and late_game based on the current game state. During the
    opening state, it follows the active build order.
    """

    # Thresholds for state transitions
    ATTACK_ARMY_SUPPLY: int = 20
    LATE_GAME_BASE_COUNT: int = 3
    LATE_GAME_TIME_SECONDS: float = 480.0  # 8 minutes

    def __init__(self, build_order: BuildOrder | None = None) -> None:
        self._state = StrategicState.OPENING
        order = build_order if build_order is not None else default_4gate()
        self._sequencer = BuildSequencer(order)
        self._decision_log: list[DecisionEntry] = []
        self._pending_advice: str | None = None

    @property
    def state(self) -> StrategicState:
        """Current strategic state."""
        return self._state

    @property
    def sequencer(self) -> BuildSequencer:
        """The active build order sequencer."""
        return self._sequencer

    @property
    def decision_log(self) -> list[DecisionEntry]:
        """History of state transitions."""
        return self._decision_log

    def set_claude_advice(self, advice: str) -> None:
        """Store pending Claude advice for the next decision."""
        self._pending_advice = advice

    def evaluate(self, snapshot: GameSnapshot, game_step: int = 0) -> StrategicState:
        """Evaluate the game state and potentially transition to a new strategic state.

        Args:
            snapshot: Current game state summary.
            game_step: Current game loop step for audit logging.

        Returns:
            The (possibly new) strategic state.
        """
        old_state = self._state
        new_state = self._compute_next_state(snapshot)

        if new_state != old_state:
            reason = self._transition_reason(old_state, new_state, snapshot)
            entry = DecisionEntry(
                game_step=game_step,
                game_time_seconds=snapshot.game_time_seconds,
                from_state=old_state.value,
                to_state=new_state.value,
                reason=reason,
                claude_advice=self._pending_advice,
            )
            self._decision_log.append(entry)
            self._pending_advice = None
            self._state = new_state

        return self._state

    def _compute_next_state(self, snapshot: GameSnapshot) -> StrategicState:
        """Determine the next state based on current game conditions."""
        # Defend takes priority in any state if enemy is near base
        if snapshot.enemy_army_near_base:
            return StrategicState.DEFEND

        # Opening: follow build order until complete
        if self._state == StrategicState.OPENING:
            if self._sequencer.is_complete:
                return StrategicState.EXPAND
            return StrategicState.OPENING

        # Late game check: 3+ bases and enough time elapsed
        if (
            snapshot.base_count >= self.LATE_GAME_BASE_COUNT
            and snapshot.game_time_seconds >= self.LATE_GAME_TIME_SECONDS
        ):
            return StrategicState.LATE_GAME

        # Attack if army is large enough
        if snapshot.army_supply >= self.ATTACK_ARMY_SUPPLY:
            return StrategicState.ATTACK

        # Default to expand when not attacking or defending
        if self._state == StrategicState.DEFEND and not snapshot.enemy_army_near_base:
            return StrategicState.EXPAND

        if self._state in (StrategicState.EXPAND, StrategicState.ATTACK, StrategicState.LATE_GAME):
            # Stay in current state unless conditions change
            is_attack = self._state == StrategicState.ATTACK
            if is_attack and snapshot.army_supply < self.ATTACK_ARMY_SUPPLY:
                return StrategicState.EXPAND
            return self._state

        return StrategicState.EXPAND

    def _transition_reason(
        self,
        from_state: StrategicState,
        to_state: StrategicState,
        snapshot: GameSnapshot,
    ) -> str:
        """Generate a human-readable reason for a state transition."""
        if to_state == StrategicState.DEFEND:
            vis = snapshot.enemy_army_supply_visible
            return f"Enemy army detected near base (visible supply: {vis})"
        if to_state == StrategicState.ATTACK:
            threshold = self.ATTACK_ARMY_SUPPLY
            return f"Army threshold met (army supply: {snapshot.army_supply} >= {threshold})"
        if to_state == StrategicState.EXPAND:
            if from_state == StrategicState.OPENING:
                return "Build order complete, transitioning to expand"
            if from_state == StrategicState.DEFEND:
                return "Threat cleared, resuming expansion"
            if from_state == StrategicState.ATTACK:
                army = snapshot.army_supply
                return f"Army depleted (army supply: {army}), falling back to expand"
            return "Expanding economy"
        if to_state == StrategicState.LATE_GAME:
            bases = snapshot.base_count
            secs = snapshot.game_time_seconds
            return f"Late game conditions met ({bases} bases, {secs:.0f}s)"
        return f"Transition from {from_state.value} to {to_state.value}"
