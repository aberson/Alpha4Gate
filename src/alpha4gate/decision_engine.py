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
    FORTIFY = "fortify"


# Single source of truth for the RL action space.
#
# The trainer (SC2Env), the imitation learner, the neural decision engine,
# and any future component that maps PPO action indices to strategic states
# must import THIS list — never define their own copy. Phase 4.5 found two
# bugs (F6, F9) where duplicate copies of this list drifted out of sync.
#
# Adding or removing an entry changes the model's action space; any
# previously-trained checkpoint becomes incompatible and must be re-trained.
ACTION_TO_STATE: list[StrategicState] = [
    StrategicState.OPENING,
    StrategicState.EXPAND,
    StrategicState.ATTACK,
    StrategicState.DEFEND,
    StrategicState.LATE_GAME,
    StrategicState.FORTIFY,
]

NUM_ACTIONS: int = len(ACTION_TO_STATE)


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
    gateway_count: int = 0
    robo_count: int = 0
    forge_count: int = 0
    upgrade_count: int = 0
    enemy_structure_count: int = 0
    cannon_count: int = 0
    battery_count: int = 0


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
    ATTACK_ARMY_SUPPLY: int = 12
    LATE_GAME_BASE_COUNT: int = 3
    LATE_GAME_TIME_SECONDS: float = 480.0  # 8 minutes

    def __init__(
        self,
        build_order: BuildOrder | None = None,
        fortify_trigger_ratio: float = 1.5,
        attack_supply_ratio: float = 1.2,
    ) -> None:
        self._state = StrategicState.OPENING
        order = build_order if build_order is not None else default_4gate()
        self._sequencer = BuildSequencer(order)
        self._decision_log: list[DecisionEntry] = []
        self._pending_advice: str | None = None
        self._override_state: StrategicState | None = None
        self._override_expires: float = 0.0
        self._override_source: str | None = None
        self._override_duration: float = 0.0
        self._override_active: bool = False
        self._fortify_trigger_ratio = fortify_trigger_ratio
        self._attack_supply_ratio = attack_supply_ratio
        self._recently_retreated: bool = False

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

    def notify_retreat(self) -> None:
        """Signal that the army has recently retreated."""
        self._recently_retreated = True

    def set_command_override(
        self, state: StrategicState, source: str, duration: float = 120.0
    ) -> None:
        """Force strategic state for duration game-seconds.

        Command override has highest priority and will be checked before
        normal state evaluation.
        """
        self._override_state = state
        self._override_duration = duration
        self._override_source = source
        self._override_active = False  # becomes True on first evaluate

    def evaluate(self, snapshot: GameSnapshot, game_step: int = 0) -> StrategicState:
        """Evaluate the game state and potentially transition to a new strategic state.

        Args:
            snapshot: Current game state summary.
            game_step: Current game loop step for audit logging.

        Returns:
            The (possibly new) strategic state.
        """
        # --- Command override: highest priority ---
        if self._override_state is not None:
            # Activate override on first evaluate after set_command_override()
            if not self._override_active:
                self._override_expires = (
                    snapshot.game_time_seconds + self._override_duration
                )
                self._override_active = True

            if snapshot.game_time_seconds < self._override_expires:
                override = self._override_state
                if override != self._state:
                    entry = DecisionEntry(
                        game_step=game_step,
                        game_time_seconds=snapshot.game_time_seconds,
                        from_state=self._state.value,
                        to_state=override.value,
                        reason="command_override",
                        claude_advice=self._pending_advice,
                    )
                    self._decision_log.append(entry)
                    self._pending_advice = None
                    self._state = override
                return self._state

            # Override expired — clear it and log transition back
            self._override_state = None
            self._override_active = False
            self._override_source = None

            # Fall through to normal evaluation below, which will
            # compute the correct next state and log the transition
            # if the state changes from the override state.
            old_state = self._state
            new_state = self._compute_next_state(snapshot)
            if new_state != old_state:
                reason = (
                    f"command_override_expired, "
                    f"{self._transition_reason(old_state, new_state, snapshot)}"
                )
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
        # Defend takes highest priority if enemy is near base
        if snapshot.enemy_army_near_base:
            return StrategicState.DEFEND

        # FORTIFY: enter when outgunned and recently retreated (lower priority than DEFEND)
        own_supply = snapshot.army_supply
        enemy_vis = snapshot.enemy_army_supply_visible
        if (
            self._recently_retreated
            and enemy_vis > own_supply * self._fortify_trigger_ratio
        ):
            return StrategicState.FORTIFY

        # FORTIFY exit: leave when own supply recovers
        if self._state == StrategicState.FORTIFY:
            if enemy_vis == 0 or own_supply >= enemy_vis * self._attack_supply_ratio:
                self._recently_retreated = False
                return StrategicState.EXPAND
            return StrategicState.FORTIFY

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

        if self._state in (
            StrategicState.EXPAND,
            StrategicState.ATTACK,
            StrategicState.LATE_GAME,
        ):
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
        if to_state == StrategicState.FORTIFY:
            return (
                f"Fortifying: enemy visible supply {snapshot.enemy_army_supply_visible}"
                f" > own {snapshot.army_supply} * {self._fortify_trigger_ratio:.2f}"
            )
        return f"Transition from {from_state.value} to {to_state.value}"
