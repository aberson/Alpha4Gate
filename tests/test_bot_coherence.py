"""Tests for army coherence integration in Alpha4GateBot.

Tests _resolve_attack_rally logic and coherence param logging without
requiring a live SC2 connection. We mock the bot's BotAI-provided attributes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from sc2.ids.unit_typeid import UnitTypeId

from alpha4gate.army_coherence import ArmyCoherenceManager
from alpha4gate.bot import Alpha4GateBot
from alpha4gate.decision_engine import GameSnapshot


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
    bot._defense_rally = Alpha4GateBot._defense_rally.__get__(bot)
    bot._STAGING_RECALC_SECONDS = Alpha4GateBot._STAGING_RECALC_SECONDS

    # Coherence manager
    bot.coherence_manager = ArmyCoherenceManager(seed=seed)
    bot._coherence_params_logged = False
    bot._cached_staging_point = None
    bot._staging_point_time = -999.0

    # Mock BotAI attributes
    start = MagicMock()
    start.x = 10.0
    start.y = 10.0
    bot.start_location = start

    enemy_start = MagicMock()
    enemy_start.x = 90.0
    enemy_start.y = 90.0
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

    return bot


class TestResolveAttackRally:
    """Test the _resolve_attack_rally decision tree."""

    def test_retreat_when_outnumbered(self) -> None:
        bot = _make_bot()
        bot.coherence_manager.retreat_supply_ratio = 0.5
        bot.coherence_manager.retreat_to_staging = False
        army = [_mock_unit(50, 50)]
        snap = GameSnapshot(army_supply=5, enemy_army_supply_visible=30)
        result = bot._resolve_attack_rally(army, snap, bot.coherence_manager)
        # Should retreat to defense rally (retreat_to_staging=False)
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

    def test_gather_when_not_coherent(self) -> None:
        bot = _make_bot()
        bot.coherence_manager.coherence_distance = 2.0
        bot.coherence_manager.coherence_pct = 0.8
        bot.coherence_manager.retreat_supply_ratio = 0.1  # won't retreat
        # Scattered army
        army = [_mock_unit(0, 0), _mock_unit(50, 50), _mock_unit(100, 100)]
        snap = GameSnapshot(army_supply=30, enemy_army_supply_visible=10)
        result = bot._resolve_attack_rally(army, snap, bot.coherence_manager)
        staging = bot._get_staging_point()
        assert result == staging

    def test_push_when_coherent_and_strong(self) -> None:
        bot = _make_bot()
        bot.coherence_manager.coherence_distance = 100.0  # easy coherence
        bot.coherence_manager.coherence_pct = 0.5
        bot.coherence_manager.attack_supply_ratio = 1.0
        bot.coherence_manager.attack_supply_floor = 15
        bot.coherence_manager.retreat_supply_ratio = 0.1
        army = [_mock_unit(50, 50), _mock_unit(51, 50)]
        snap = GameSnapshot(army_supply=30, enemy_army_supply_visible=20)
        result = bot._resolve_attack_rally(army, snap, bot.coherence_manager)
        attack = bot._attack_target()
        assert result == attack

    def test_hold_when_coherent_but_weak(self) -> None:
        bot = _make_bot()
        bot.coherence_manager.coherence_distance = 100.0
        bot.coherence_manager.coherence_pct = 0.5
        bot.coherence_manager.attack_supply_ratio = 2.0  # need 2x enemy
        bot.coherence_manager.attack_supply_floor = 100  # high floor
        bot.coherence_manager.retreat_supply_ratio = 0.1  # won't retreat
        army = [_mock_unit(50, 50), _mock_unit(51, 50)]
        snap = GameSnapshot(army_supply=20, enemy_army_supply_visible=20)
        result = bot._resolve_attack_rally(army, snap, bot.coherence_manager)
        staging = bot._get_staging_point()
        assert result == staging

    def test_push_on_staging_timeout(self) -> None:
        """After 30s at staging without coherence, push anyway."""
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

        # After 30s, should push
        bot.time = 31.0
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


class TestCoherenceParamsLogging:
    def test_bot_has_coherence_manager(self) -> None:
        bot = _make_bot()
        assert isinstance(bot.coherence_manager, ArmyCoherenceManager)

    def test_params_dict_has_all_keys(self) -> None:
        bot = _make_bot()
        params = bot.coherence_manager.get_params_dict()
        assert "attack_supply_ratio" in params
        assert "retreat_to_staging" in params
        assert len(params) == 7

    def test_coherence_params_logged_flag(self) -> None:
        bot = _make_bot()
        assert bot._coherence_params_logged is False


class TestHysteresisIntegration:
    """Test the full retreat → re-engage hysteresis cycle through bot helpers."""

    def test_retreat_then_requires_higher_ratio(self) -> None:
        bot = _make_bot()
        cm = bot.coherence_manager
        cm.attack_supply_ratio = 1.0
        cm.retreat_supply_ratio = 0.5
        cm.attack_supply_floor = 100  # high so floor doesn't interfere
        cm.coherence_distance = 100.0  # easy coherence
        cm.coherence_pct = 0.5

        army = [_mock_unit(50, 50)]

        # Step 1: retreat (5 < 20 * 0.5 = 10)
        snap1 = GameSnapshot(army_supply=5, enemy_army_supply_visible=20)
        bot._resolve_attack_rally(army, snap1, cm)
        assert cm._recently_retreated is True

        # Step 2: rebuild to 20 supply, enemy still 20
        # Without hysteresis 20 >= 20*1.0 would attack. With hysteresis need 20*1.2=24.
        snap2 = GameSnapshot(army_supply=20, enemy_army_supply_visible=20)
        result = bot._resolve_attack_rally(army, snap2, cm)
        # Should NOT be attacking — held at staging
        staging = bot._get_staging_point()
        assert result == staging

        # Step 3: at 24 supply, should now attack
        snap3 = GameSnapshot(army_supply=24, enemy_army_supply_visible=20)
        result = bot._resolve_attack_rally(army, snap3, cm)
        attack = bot._attack_target()
        assert result == attack
