"""Strategy state machine, build order queue, and state transitions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from bots.v9.build_orders import BuildOrder, BuildSequencer, default_4gate

_logger = logging.getLogger(__name__)


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
    zealot_count: int = 0
    stalker_count: int = 0
    sentry_count: int = 0
    immortal_count: int = 0
    colossus_count: int = 0
    archon_count: int = 0
    high_templar_count: int = 0
    dark_templar_count: int = 0
    phoenix_count: int = 0
    void_ray_count: int = 0
    carrier_count: int = 0
    tempest_count: int = 0
    disruptor_count: int = 0
    warp_prism_count: int = 0
    observer_count: int = 0
    enemy_light_count: int = 0
    enemy_armored_count: int = 0
    enemy_siege_count: int = 0
    enemy_support_count: int = 0
    enemy_air_harass_count: int = 0
    enemy_heavy_count: int = 0
    enemy_capital_count: int = 0
    enemy_cloak_count: int = 0


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
    # Max-supply commit: at or above this supply, force ATTACK regardless of
    # other conditions. At 180+/200 supply the bot is near cap and there is
    # zero benefit to waiting — sitting passively at max supply was a live
    # bug where the army refused to engage.
    MAX_SUPPLY_ATTACK_THRESHOLD: int = 180
    DEFEND_STUCK_TIMEOUT_SECONDS: float = 90.0

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
        self._defend_entered_at: float | None = None

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
                    _defend_states = (StrategicState.DEFEND, StrategicState.FORTIFY)
                    old = self._state
                    entry = DecisionEntry(
                        game_step=game_step,
                        game_time_seconds=snapshot.game_time_seconds,
                        from_state=old.value,
                        to_state=override.value,
                        reason="command_override",
                        claude_advice=self._pending_advice,
                    )
                    self._decision_log.append(entry)
                    self._pending_advice = None
                    self._state = override
                    if override in _defend_states and old not in _defend_states:
                        self._defend_entered_at = snapshot.game_time_seconds
                    elif override not in _defend_states and old in _defend_states:
                        self._defend_entered_at = None
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

                _defend_states = (StrategicState.DEFEND, StrategicState.FORTIFY)
                if new_state in _defend_states and old_state not in _defend_states:
                    self._defend_entered_at = snapshot.game_time_seconds
                elif new_state not in _defend_states and old_state in _defend_states:
                    self._defend_entered_at = None
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

            # Track DEFEND/FORTIFY entry and exit for stuck-state timeout
            _defend_states = (StrategicState.DEFEND, StrategicState.FORTIFY)
            if new_state in _defend_states and old_state not in _defend_states:
                self._defend_entered_at = snapshot.game_time_seconds
            elif new_state not in _defend_states and old_state in _defend_states:
                self._defend_entered_at = None

        return self._state

    # Minimum enemy supply near base to interrupt an ongoing attack.
    # Prevents small raids from pulling the entire army back home.
    DEFEND_INTERRUPT_THRESHOLD: int = 8

    def _compute_next_state(self, snapshot: GameSnapshot) -> StrategicState:
        """Determine the next state based on current game conditions."""
        # Max-supply commit: at 180+ supply, there is zero benefit to waiting.
        # Force ATTACK regardless of other conditions so the finisher logic
        # in bot.py can fire and push the army into the enemy base.
        if snapshot.supply_used >= self.MAX_SUPPLY_ATTACK_THRESHOLD:
            return StrategicState.ATTACK

        if (
            self._defend_entered_at is not None
            and snapshot.game_time_seconds - self._defend_entered_at
            >= self.DEFEND_STUCK_TIMEOUT_SECONDS
            and not snapshot.enemy_army_near_base
        ):
            _logger.info("defend-stuck timeout, forcing reassessment")
            self._defend_entered_at = None
            self._recently_retreated = False
            if snapshot.supply_used < 60:
                return StrategicState.EXPAND
            return StrategicState.ATTACK

        # Defend if enemy is near base — but allow counterattack when
        # our army is strong enough to break out of the DEFEND loop.
        if snapshot.enemy_army_near_base:
            enemy_vis = snapshot.enemy_army_supply_visible

            # Hysteresis: when already attacking, only switch to DEFEND if
            # the enemy presence near base is substantial (>= threshold).
            # Small raids shouldn't pull the whole army back.
            if (
                self._state == StrategicState.ATTACK
                and enemy_vis < self.DEFEND_INTERRUPT_THRESHOLD
            ):
                pass  # stay in ATTACK — ignore minor raid
            else:
                can_counterattack = (
                    snapshot.army_supply >= self.ATTACK_ARMY_SUPPLY
                    and (
                        enemy_vis == 0
                        or snapshot.army_supply
                        >= enemy_vis * self._attack_supply_ratio
                    )
                )
                if not can_counterattack:
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

        # After successful defense, counterattack if army is strong enough
        if self._state == StrategicState.DEFEND and not snapshot.enemy_army_near_base:
            if snapshot.army_supply >= self.ATTACK_ARMY_SUPPLY:
                return StrategicState.ATTACK
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
            if from_state in (StrategicState.DEFEND, StrategicState.FORTIFY):
                if self._defend_entered_at is None:
                    return "Defend/fortify stuck timeout, pivoting to expand"
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
