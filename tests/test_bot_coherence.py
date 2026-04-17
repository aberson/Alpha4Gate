"""Tests for army coherence integration in Alpha4GateBot.

Tests _resolve_attack_rally logic and coherence param logging without
requiring a live SC2 connection. We mock the bot's BotAI-provided attributes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from bots.v0.army_coherence import ArmyCoherenceManager
from bots.v0.bot import Alpha4GateBot
from bots.v0.decision_engine import GameSnapshot
from sc2.ids.unit_typeid import UnitTypeId


def _mock_unit(x: float = 0.0, y: float = 0.0) -> MagicMock:
    u = MagicMock()
    u.position = MagicMock()
    u.position.x = x
    u.position.y = y
    u.type_id = UnitTypeId.STALKER
    u.is_structure = False
    u.is_idle = True
    u.tag = id(u)
    return u


def _make_bot(seed: int = 42) -> Alpha4GateBot:
    """Create a bot with mocked BotAI internals for unit testing.

    Uses a MagicMock spec to avoid BotAI property restrictions.
    """
    bot = MagicMock(spec=Alpha4GateBot)

    # Restore real methods we want to test
    bot._resolve_attack_rally = Alpha4GateBot._resolve_attack_rally.__get__(bot)
    bot._get_staging_point = Alpha4GateBot._get_staging_point.__get__(bot)
    bot._attack_target = Alpha4GateBot._attack_target.__get__(bot)
    bot._enemy_main = Alpha4GateBot._enemy_main.__get__(bot)
    bot._enemy_natural = Alpha4GateBot._enemy_natural.__get__(bot)
    bot._defense_rally = Alpha4GateBot._defense_rally.__get__(bot)
    bot._STAGING_RECALC_SECONDS = Alpha4GateBot._STAGING_RECALC_SECONDS
    bot.PUSH_MAIN_SUPPLY = Alpha4GateBot.PUSH_MAIN_SUPPLY

    # Coherence manager
    bot.coherence_manager = ArmyCoherenceManager(seed=seed)
    bot.decision_engine = MagicMock()
    bot._coherence_params_logged = False
    bot._cached_staging_point = None
    bot._staging_point_time = -999.0
    bot._cached_enemy_natural = None

    # Mock BotAI attributes
    start = MagicMock()
    start.x = 10.0
    start.y = 10.0
    bot.start_location = start

    enemy_start = MagicMock()
    enemy_start.x = 90.0
    enemy_start.y = 90.0
    enemy_start.distance_to = MagicMock(return_value=0.0)  # distance to itself
    bot.enemy_start_locations = [enemy_start]
    bot.enemy_structures = []

    # time property
    bot.time = 0.0

    # Scout manager with no known bases
    bot.scout_manager = MagicMock()
    bot.scout_manager.enemy_base_locations = []

    # Townhalls for _defense_rally
    ramp = MagicMock()
    ramp.bottom_center = MagicMock()
    ramp.bottom_center.x = 15.0
    ramp.bottom_center.y = 10.0
    start.towards = MagicMock(return_value=MagicMock(x=12.0, y=10.0))
    start.distance_to = MagicMock(return_value=5.0)
    bot.main_base_ramp = ramp
    bot.townhalls = [MagicMock()]

    # Expansion locations for _enemy_natural()
    nat_loc = MagicMock()
    nat_loc.x = 80.0
    nat_loc.y = 80.0
    nat_loc.distance_to = MagicMock(side_effect=lambda p: (
        ((80.0 - p.x) ** 2 + (80.0 - p.y) ** 2) ** 0.5
    ))
    far_loc = MagicMock()
    far_loc.x = 50.0
    far_loc.y = 50.0
    far_loc.distance_to = MagicMock(side_effect=lambda p: (
        ((50.0 - p.x) ** 2 + (50.0 - p.y) ** 2) ** 0.5
    ))
    bot.expansion_locations_list = [enemy_start, nat_loc, far_loc]

    # Supply for _attack_target high-supply check
    bot.supply_used = 50

    return bot


class TestResolveAttackRally:
    """Test bot-level wiring of _resolve_attack_rally.

    Core decision logic (retreat/attack/hold thresholds) is tested in
    test_army_coherence.py. These tests verify bot-specific behavior:
    retreat destination, staging flag, and staging timeout.
    """

    def test_retreat_to_defense_rally(self) -> None:
        bot = _make_bot()
        bot.coherence_manager.retreat_supply_ratio = 0.5
        bot.coherence_manager.retreat_to_staging = False
        army = [_mock_unit(50, 50)]
        snap = GameSnapshot(army_supply=5, enemy_army_supply_visible=30)
        result = bot._resolve_attack_rally(army, snap, bot.coherence_manager)
        defense = bot._defense_rally()
        assert result == defense

    def test_retreat_to_staging_when_flag_set(self) -> None:
        bot = _make_bot()
        bot.coherence_manager.retreat_supply_ratio = 0.5
        bot.coherence_manager.retreat_to_staging = True
        army = [_mock_unit(50, 50)]
        snap = GameSnapshot(army_supply=5, enemy_army_supply_visible=30)
        result = bot._resolve_attack_rally(army, snap, bot.coherence_manager)
        staging = bot._get_staging_point()
        assert result == staging

    def test_push_on_staging_timeout(self) -> None:
        """After 60s at staging without coherence, push anyway."""
        bot = _make_bot()
        bot.coherence_manager.coherence_distance = 1.0  # very strict
        bot.coherence_manager.coherence_pct = 1.0
        bot.coherence_manager.attack_supply_ratio = 5.0  # won't trigger attack
        bot.coherence_manager.attack_supply_floor = 200
        bot.coherence_manager.retreat_supply_ratio = 0.01
        army = [_mock_unit(0, 0), _mock_unit(50, 50)]
        snap = GameSnapshot(army_supply=20, enemy_army_supply_visible=10)

        # First call starts the staging timer
        bot.time = 0.0
        bot._resolve_attack_rally(army, snap, bot.coherence_manager)

        # After 60s, should push
        bot.time = 61.0
        bot._staging_point_time = -999.0
        result = bot._resolve_attack_rally(army, snap, bot.coherence_manager)
        attack = bot._attack_target()
        assert result == attack


class TestGetStagingPoint:
    def test_caches_staging_point(self) -> None:
        bot = _make_bot()
        bot.time = 0.0
        pt1 = bot._get_staging_point()
        assert pt1 is not None
        # Second call at same time uses cache
        pt2 = bot._get_staging_point()
        assert pt1 == pt2

    def test_recalculates_after_interval(self) -> None:
        bot = _make_bot()
        bot.time = 0.0
        pt1 = bot._get_staging_point()

        # Add an enemy structure to change the result
        es = MagicMock()
        es.position = MagicMock()
        es.position.x = 70.0
        es.position.y = 70.0
        bot.enemy_structures = [es]

        # Still cached
        bot.time = 15.0
        pt2 = bot._get_staging_point()
        assert pt1 == pt2

        # After 30s, recalculates
        bot.time = 31.0
        pt3 = bot._get_staging_point()
        assert pt3 != pt1  # different because enemy structure changed

    def test_fallback_without_enemy_structures(self) -> None:
        bot = _make_bot()
        bot.enemy_structures = []
        bot.time = 0.0
        pt = bot._get_staging_point()
        # Should be 70% of distance from base (10,10) to enemy start (90,90)
        # 70% of 80 = 56 offset → (66, 66)
        assert pt is not None
        assert abs(pt[0] - 66.0) < 1.0
        assert abs(pt[1] - 66.0) < 1.0


class TestEnemyNatural:
    """Test _enemy_natural() expansion detection."""

    def test_returns_closest_expansion_to_enemy_start(self) -> None:
        bot = _make_bot()
        result = bot._enemy_natural()
        # nat_loc at (80,80) is closer to enemy_start (90,90) than far_loc (50,50)
        assert result == (80.0, 80.0)

    def test_caches_result(self) -> None:
        bot = _make_bot()
        result1 = bot._enemy_natural()
        result2 = bot._enemy_natural()
        assert result1 == result2
        assert result1 is result2  # same cached tuple

    def test_returns_none_without_enemy_start(self) -> None:
        bot = _make_bot()
        bot.enemy_start_locations = []
        result = bot._enemy_natural()
        assert result is None

    def test_returns_none_without_expansions(self) -> None:
        bot = _make_bot()
        bot.expansion_locations_list = [bot.enemy_start_locations[0]]  # only start loc
        result = bot._enemy_natural()
        assert result is None


class TestAttackTargetNatural:
    """Test that _attack_target() returns enemy natural by default."""

    def test_targets_enemy_natural_by_default(self) -> None:
        bot = _make_bot()
        bot.supply_used = 50
        result = bot._attack_target()
        assert result == (80.0, 80.0)  # enemy natural

    def test_targets_enemy_main_at_high_supply(self) -> None:
        bot = _make_bot()
        bot.supply_used = 160
        result = bot._attack_target()
        # Should return enemy main (scouted base or start location)
        main = bot._enemy_main()
        assert result == main

    def test_falls_back_to_main_without_natural(self) -> None:
        bot = _make_bot()
        bot.supply_used = 50
        bot.expansion_locations_list = [bot.enemy_start_locations[0]]  # no natural
        bot._cached_enemy_natural = None
        result = bot._attack_target()
        main = bot._enemy_main()
        assert result == main
