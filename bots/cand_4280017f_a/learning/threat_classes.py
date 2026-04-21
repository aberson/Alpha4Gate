"""Unit-type-to-threat-class mapping for enemy army composition tracking.

Maps SC2 UnitTypeId values to one of 8 threat-class bucket field names used
by GameSnapshot's enemy_*_count fields.  Units not in the map are simply
not counted (uncommon or non-combat units).
"""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId

# Maps UnitTypeId values to threat-class bucket field names.
# The field names match GameSnapshot, _FEATURE_SPEC, and _STATE_COLS.
THREAT_CLASS_MAP: dict[int, str] = {
    # ---------------------------------------------------------------
    # Light units — fragile, high-DPS-per-cost, often massed
    # ---------------------------------------------------------------
    # Terran
    UnitTypeId.MARINE.value: "enemy_light_count",
    UnitTypeId.REAPER.value: "enemy_light_count",
    UnitTypeId.HELLION.value: "enemy_light_count",
    UnitTypeId.HELLIONTANK.value: "enemy_light_count",
    # Zerg
    UnitTypeId.ZERGLING.value: "enemy_light_count",
    UnitTypeId.BANELING.value: "enemy_light_count",
    UnitTypeId.HYDRALISK.value: "enemy_light_count",
    # Protoss
    UnitTypeId.ZEALOT.value: "enemy_light_count",
    UnitTypeId.ADEPT.value: "enemy_light_count",
    # ---------------------------------------------------------------
    # Armored units — tanky frontline, anti-armor
    # ---------------------------------------------------------------
    # Terran
    UnitTypeId.MARAUDER.value: "enemy_armored_count",
    UnitTypeId.CYCLONE.value: "enemy_armored_count",
    # Zerg
    UnitTypeId.ROACH.value: "enemy_armored_count",
    UnitTypeId.RAVAGER.value: "enemy_armored_count",
    # Protoss
    UnitTypeId.STALKER.value: "enemy_armored_count",
    UnitTypeId.SENTRY.value: "enemy_armored_count",
    # ---------------------------------------------------------------
    # Siege units — long-range positional damage
    # ---------------------------------------------------------------
    # Terran
    UnitTypeId.SIEGETANK.value: "enemy_siege_count",
    UnitTypeId.SIEGETANKSIEGED.value: "enemy_siege_count",
    UnitTypeId.LIBERATOR.value: "enemy_siege_count",
    UnitTypeId.LIBERATORAG.value: "enemy_siege_count",
    # Zerg
    UnitTypeId.LURKERMP.value: "enemy_siege_count",
    UnitTypeId.LURKERMPBURROWED.value: "enemy_siege_count",
    UnitTypeId.SWARMHOSTMP.value: "enemy_siege_count",
    # Protoss
    UnitTypeId.COLOSSUS.value: "enemy_siege_count",
    UnitTypeId.DISRUPTOR.value: "enemy_siege_count",
    UnitTypeId.TEMPEST.value: "enemy_siege_count",
    # ---------------------------------------------------------------
    # Support units — transports, healers, utility
    # ---------------------------------------------------------------
    # Terran
    UnitTypeId.MEDIVAC.value: "enemy_support_count",
    UnitTypeId.RAVEN.value: "enemy_support_count",
    # Zerg
    UnitTypeId.OVERLORD.value: "enemy_support_count",
    UnitTypeId.OVERSEER.value: "enemy_support_count",
    UnitTypeId.QUEEN.value: "enemy_support_count",
    UnitTypeId.VIPER.value: "enemy_support_count",
    # Protoss
    UnitTypeId.WARPPRISM.value: "enemy_support_count",
    UnitTypeId.WARPPRISMPHASING.value: "enemy_support_count",
    UnitTypeId.OBSERVER.value: "enemy_support_count",
    # ---------------------------------------------------------------
    # Air harass units — fast, hit-and-run air
    # ---------------------------------------------------------------
    # Terran
    UnitTypeId.VIKINGFIGHTER.value: "enemy_air_harass_count",
    UnitTypeId.VIKINGASSAULT.value: "enemy_air_harass_count",
    UnitTypeId.WIDOWMINE.value: "enemy_siege_count",
    # Zerg
    UnitTypeId.MUTALISK.value: "enemy_air_harass_count",
    UnitTypeId.CORRUPTOR.value: "enemy_air_harass_count",
    # Protoss
    UnitTypeId.PHOENIX.value: "enemy_air_harass_count",
    UnitTypeId.ORACLE.value: "enemy_air_harass_count",
    UnitTypeId.VOIDRAY.value: "enemy_air_harass_count",
    # ---------------------------------------------------------------
    # Heavy units — high HP, high damage, expensive
    # ---------------------------------------------------------------
    # Terran
    UnitTypeId.THOR.value: "enemy_heavy_count",
    UnitTypeId.THORAP.value: "enemy_heavy_count",
    # Zerg
    UnitTypeId.ULTRALISK.value: "enemy_heavy_count",
    # Protoss
    UnitTypeId.ARCHON.value: "enemy_heavy_count",
    UnitTypeId.HIGHTEMPLAR.value: "enemy_heavy_count",
    UnitTypeId.IMMORTAL.value: "enemy_heavy_count",
    # ---------------------------------------------------------------
    # Capital units — game-ending flying units
    # ---------------------------------------------------------------
    # Terran
    UnitTypeId.BATTLECRUISER.value: "enemy_capital_count",
    # Zerg
    UnitTypeId.BROODLORD.value: "enemy_capital_count",
    # Protoss
    UnitTypeId.CARRIER.value: "enemy_capital_count",
    UnitTypeId.MOTHERSHIP.value: "enemy_capital_count",
    # ---------------------------------------------------------------
    # Cloak units — invisible or burrowed threats
    # ---------------------------------------------------------------
    # Terran
    UnitTypeId.BANSHEE.value: "enemy_cloak_count",
    UnitTypeId.GHOST.value: "enemy_cloak_count",
    # Zerg
    UnitTypeId.INFESTOR.value: "enemy_cloak_count",
    UnitTypeId.INFESTORBURROWED.value: "enemy_cloak_count",
    # Protoss
    UnitTypeId.DARKTEMPLAR.value: "enemy_cloak_count",
}
