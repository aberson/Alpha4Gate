"""Recovery ends on observable readiness, not on a repeatedly renewed answer."""

from dataclasses import replace

import pytest

from jev.contracts import Entity
from jev.recovery import RecoveryProgress


def army(count=8, position=(0.0, 0.0)):
    return tuple(Entity(i, "Zealot", position, 100, shield=50) for i in range(count))


def update(progress, game, units=None, active=True, threatened=False):
    progress.update(game=game, active=active, army=army() if units is None else units,
                    home=(0, 0), radius=20, minimum=4, threatened=threatened)


def test_completion_stays_latched_after_override_releases_and_rearms_on_departure():
    progress = RecoveryProgress()
    for second in range(6):
        update(progress, second)
    assert progress.completed and progress.facts["completed_game_seconds"] == 5
    update(progress, 50, active=False)
    assert progress.completed and progress.facts["elapsed_game_seconds"] == 5
    update(progress, 51, army(position=(40, 40)), active=False)
    assert not progress.completed


@pytest.mark.parametrize("cause", ["loss", "damage", "threat", "small", "away"])
def test_new_recovery_need_resets_readiness_and_completion(cause):
    progress = RecoveryProgress()
    update(progress, 0)
    update(progress, 4)
    units = army()
    if cause == "loss":
        units = units[:-1]
    elif cause == "damage":
        units = (replace(units[0], shield=20), *units[1:])
    elif cause == "small":
        units = army(3)
    elif cause == "away":
        units = army(position=(40, 40))
    update(progress, 5, units, threatened=cause == "threat")
    assert not progress.completed and progress.facts["settled_game_seconds"] == 0
    update(progress, 6)
    update(progress, 10)
    assert not progress.completed
    update(progress, 11)
    assert progress.completed
    update(progress, 12, units, active=False, threatened=cause == "threat")
    assert not progress.completed


def test_sustained_threat_or_small_army_never_completes_on_a_timer():
    for units, threatened in [(army(3), False), (army(), True)]:
        progress = RecoveryProgress()
        for second in range(150):
            update(progress, second, units, threatened=threatened)
        assert not progress.completed
