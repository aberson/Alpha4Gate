"""SC2 build 98310 compatibility: abilities whose ids moved in the October 2026 patch.

Without the shim, burnysc2 drops the moved ids from GameData, and
``UnitOrder.from_proto`` raises ``KeyError`` (4135 when a worker mines gas,
4115 when a Gateway morphs into a WarpGate).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from s2clientprotocol import data_pb2, raw_pb2, sc2api_pb2
from sc2.game_data import GameData
from sc2.ids.ability_id import AbilityId
from sc2.unit import UnitOrder

from orchestrator import sc2_compat

_REPO = Path(__file__).resolve().parents[1]


def _ability(ability_id: int, link: str, button: str = "", remaps: int = 0) -> data_pb2.AbilityData:
    return data_pb2.AbilityData(
        ability_id=ability_id,
        link_name=link,
        button_name=button,
        remaps_to_ability_id=remaps,
        available=True,
    )


#: A slice of build 98310's own ResponseData (ids and names as the build reports them).
_BUILD_98310 = (
    _ability(1518, "UpgradeToWarpGate", "UpgradeToWarpGate"),
    _ability(1519, "UpgradeToWarpGate", "Cancel", remaps=3659),
    _ability(4115, "MorphBuildingGatewayWarpGateFree", "UpgradeToWarpGate"),
    _ability(4117, "MorphBuildingGatewayWarpGateFree"),  # a variant entry nobody aliases
    _ability(4121, "ObserverMorphtoObserverSiege"),  # old id now reused by a variant
    _ability(4124, "MorphToBaneling", "Baneling"),
    _ability(4127, "MothershipCloak", "OracleCloakField"),
    _ability(4132, "SalvageEffect"),  # old gather id now reused by a variant
    _ability(4135, "WorkerStopIdleAbilityVespene", "Gather"),
)


def _game_data(*abilities: data_pb2.AbilityData) -> GameData:
    sc2_compat.install()
    return GameData(sc2api_pb2.ResponseData(abilities=list(abilities)))


def _order(game_data: GameData, ability_id: int) -> UnitOrder:
    proto = raw_pb2.UnitOrder(ability_id=ability_id, target_unit_tag=7)
    return UnitOrder.from_proto(proto, SimpleNamespace(game_data=game_data))  # type: ignore[arg-type]


def test_a_worker_mining_gas_no_longer_raises() -> None:
    order = _order(_game_data(*_BUILD_98310), 4135)

    assert order.ability.id == AbilityId.WORKERSTOPIDLEABILITYVESPENE_GATHER
    assert order.target == 7


def test_a_gateway_morphing_to_a_warpgate_no_longer_raises() -> None:
    game_data = _game_data(*_BUILD_98310)

    assert _order(game_data, 4115).ability.id == AbilityId.MORPH_WARPGATE
    # The build still reports a real ability at 1518, so it keeps its own data.
    assert game_data.abilities[1518]._proto.link_name == "UpgradeToWarpGate"


def test_an_order_reads_with_the_new_builds_meaning() -> None:
    # 4124 is Morph Baneling in build 98310 (burnysc2 calls 4124 OracleCloakField).
    game_data = _game_data(*_BUILD_98310)

    assert _order(game_data, 4124).ability.id == AbilityId.MORPHTOBANELING_BANELING
    assert _order(game_data, 4127).ability.id == AbilityId.MOTHERSHIPCLOAK_ORACLECLOAKFIELD


def test_old_ids_reused_by_variants_point_at_the_moved_ability() -> None:
    game_data = _game_data(*_BUILD_98310)

    assert game_data.abilities[4132]._proto.link_name == "WorkerStopIdleAbilityVespene"
    assert game_data.abilities[4121]._proto.link_name == "MorphToBaneling"


def test_other_unknown_ids_still_fail_loudly() -> None:
    game_data = _game_data(*_BUILD_98310)

    with pytest.raises(KeyError):
        _order(game_data, 4117)


def test_an_older_build_without_the_move_is_unchanged() -> None:
    game_data = _game_data(_ability(4132, "WorkerStopIdleAbilityVespene", "Gather"))

    assert _order(game_data, 4132).ability.id == AbilityId.WORKERSTOPIDLEABILITYVESPENE_GATHER
    assert 4135 not in game_data.abilities


def test_install_is_idempotent() -> None:
    sc2_compat.install()
    patched = GameData.__init__
    sc2_compat.install()

    assert GameData.__init__ is patched
    assert sc2_compat.is_installed()


def test_older_build_reused_number_keeps_its_original_meaning() -> None:
    game_data = _game_data(_ability(4124, "MothershipCloak", "OracleCloakField"))

    assert _order(game_data, 4124).ability.id == AbilityId.MOTHERSHIPCLOAK_ORACLECLOAKFIELD


def test_unknown_semantics_at_a_new_number_are_not_translated() -> None:
    game_data = _game_data(_ability(4135, "FutureAbility", "FutureButton"))

    with pytest.raises(KeyError):
        _order(game_data, 4135)


@pytest.mark.parametrize("module", ["bots.v13.bot", "jev.bot"])
def test_the_production_bots_install_the_shim_on_import(module: str) -> None:
    # A fresh interpreter, so an earlier test's install() can't mask a missing one.
    check = (
        f"import {module}\n"
        "from orchestrator import sc2_compat\n"
        "raise SystemExit(0 if sc2_compat.is_installed() else 3)\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(_REPO / "src"), str(_REPO)])}
    done = subprocess.run([sys.executable, "-c", check], cwd=_REPO, env=env, timeout=120)

    assert done.returncode == 0, f"{module} did not install orchestrator.sc2_compat"
