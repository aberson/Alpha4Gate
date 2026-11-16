"""Compatibility shim for StarCraft II builds newer than burnysc2's ability table.

Blizzard's October 2026 patch (base build 98310) inserted new abilities into
the middle of the ability-id range, so several existing abilities moved up by
three ids (e.g. the worker "gather vespene" ability went from 4132 to 4135),
and a free gateway-to-warpgate morph appeared at 4115. The installed
burnysc2 7.1.3 ability table uses the old ids, so:

* ``GameData`` drops the moved ids, and ``UnitOrder.from_proto`` raises
  ``KeyError`` as soon as a unit carries one (a Probe mining gas at ~45 game
  seconds; a Gateway morphing to a WarpGate a few minutes later);
* some old ids are now reused by minor variant entries, so burnysc2 would
  misread them as the abilities that used to live there.

:func:`install` makes ``GameData`` resolve each moved id as the ability
burnysc2 knows, so unit orders read exactly as they did on the pre-patch
build. Any *other* unknown id still raises, so the next renumbering fails
loudly instead of misplaying. Only incoming orders are translated: neither
bots/v13 nor Jev issues any of these abilities as a command.

Install it once per bot process, before the game starts (bots/v13 and Jev do
this at import). It is idempotent and patches nothing but ``GameData``.
"""

from __future__ import annotations

import functools
from typing import Any

#: Ability id in SC2 build 98310 -> the id burnysc2 uses for the same ability.
#: Derived on 2026-10-14 by matching build 98310's own ResponseData names
#: (link_name + button_name) against burnysc2 7.1.3's AbilityId names.
ABILITY_ALIASES: dict[int, int] = {
    4115: 1518,  # MorphBuildingGatewayWarpGateFree / UpgradeToWarpGate -> MORPH_WARPGATE
    4116: 1519,  # MorphBuildingGatewayWarpGateFree / Cancel -> warpgate morph cancel
    4124: 4121,  # MorphToBaneling / Baneling
    4125: 4122,  # MorphToBaneling / Cancel
    4127: 4124,  # MothershipCloak / OracleCloakField
    4129: 4126,  # EnergyRecharge / EnergyRecharge
    4131: 4128,  # SalvageEffect / Salvage
    4133: 4130,  # SalvageSensorTowerRefund / Salvage
    4135: 4132,  # WorkerStopIdleAbilityVespene / Gather
}

_MARK = "_alpha4gate_sc2_compat"

# IDs alone are not a version discriminator: older builds already use some
# of these numbers for different abilities. Match the server's semantics too.
_EXPECTED_NAMES = {
    4115: ("MorphBuildingGatewayWarpGateFree", "UpgradeToWarpGate"),
    4116: ("MorphBuildingGatewayWarpGateFree", "Cancel"),
    4124: ("MorphToBaneling", "Baneling"),
    4125: ("MorphToBaneling", "Cancel"),
    4127: ("MothershipCloak", "OracleCloakField"),
    4129: ("EnergyRecharge", "EnergyRecharge"),
    4131: ("SalvageEffect", "Salvage"),
    4133: ("SalvageSensorTowerRefund", "Salvage"),
    4135: ("WorkerStopIdleAbilityVespene", "Gather"),
}


def install() -> None:
    """Patch ``sc2.game_data.GameData`` to apply :data:`ABILITY_ALIASES`."""
    from sc2.game_data import GameData

    original = GameData.__init__
    if getattr(original, _MARK, False):
        return

    @functools.wraps(original)
    def __init__(self: GameData, data: Any) -> None:
        original(self, data)
        _apply_aliases(self, data)

    setattr(__init__, _MARK, True)
    GameData.__init__ = __init__  # type: ignore[method-assign]


def is_installed() -> bool:
    """True once :func:`install` has patched ``GameData`` in this process."""
    from sc2.game_data import GameData

    return bool(getattr(GameData.__init__, _MARK, False))


def _apply_aliases(game_data: Any, data: Any) -> None:
    from sc2.game_data import AbilityData

    reported = {proto.ability_id: proto for proto in data.abilities}
    for new_id, known_id in ABILITY_ALIASES.items():
        proto = reported.get(new_id)
        if proto is None or not AbilityData.id_exists(known_id):
            continue  # an older build without the move, or an unknown target
        if (proto.link_name, proto.button_name) != _EXPECTED_NAMES[new_id]:
            continue
        alias = type(proto)()
        alias.CopyFrom(proto)
        alias.ability_id = known_id
        ability = AbilityData(game_data, alias)
        # An order carrying the new id means the moved ability.
        game_data.abilities[new_id] = ability
        # Lookups by burnysc2's id get the moved ability too, unless that id is
        # itself a new id (4124) or the build still reports a real, primary
        # ability there (1518/1519 keep their own data). Old ids now reused by
        # minor variant entries (no button name) are taken over.
        if known_id in ABILITY_ALIASES:
            continue
        current = reported.get(known_id)
        if current is None or not current.button_name:
            game_data.abilities[known_id] = ability
