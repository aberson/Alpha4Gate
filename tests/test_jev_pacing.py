"""Controlled acceleration preserves async progress, stop handling and runner defaults."""

from __future__ import annotations

import asyncio

import pytest

from jev.pacing import GamePacer
from jev.runner import MatchOptions, build_parser, match_options_record


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.waits = 0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.waits += 1
        await asyncio.sleep(0)


@pytest.mark.parametrize('speed', [1, 2, 4, 8, 16])
def test_rate_cap_allows_async_work_and_does_not_catch_up_after_slow_frame(speed: float) -> None:
    async def scenario() -> None:
        clock = Clock()
        pacer = GamePacer(speed, clock=clock, sleep=clock.sleep)
        await pacer.wait(10, lambda: False)
        completed = []

        async def response() -> None:
            await asyncio.sleep(0)
            completed.append(True)

        task = asyncio.create_task(response())
        await pacer.wait(11, lambda: False)
        await task
        assert completed and clock.now == pytest.approx(1 / speed)
        # A slow frame consumes its own interval; it cannot earn a later burst.
        clock.now += 3
        await pacer.wait(12, lambda: False)
        after_slow_frame = clock.now
        await pacer.wait(13, lambda: False)
        assert clock.now - after_slow_frame == pytest.approx(1 / speed)

    asyncio.run(scenario())


def test_stop_interrupts_a_pacing_wait() -> None:
    async def scenario() -> None:
        clock = Clock()
        pacer = GamePacer(1, clock=clock, sleep=clock.sleep)
        await pacer.wait(0, lambda: False)
        await pacer.wait(10, lambda: clock.waits >= 2)
        assert clock.now == pytest.approx(0.1)

    asyncio.run(scenario())


@pytest.mark.parametrize('bad', [0, -1, 17, float('nan'), float('inf'), True])
def test_invalid_speed_is_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match='game_speed'):
        MatchOptions(game_speed=bad)


def test_runner_modes_and_archive_compatibility() -> None:
    assert MatchOptions().game_speed is None
    assert 'game_speed' not in match_options_record(MatchOptions())
    assert match_options_record(MatchOptions(game_speed=4))['game_speed'] == 4
    with pytest.raises(ValueError, match='non-realtime'):
        MatchOptions(realtime=True, game_speed=2)
    parser = build_parser('jev')
    assert parser.parse_args(['--game-speed', '2']).game_speed == 2
    with pytest.raises(SystemExit):
        parser.parse_args(['--realtime', '--game-speed', '2'])
