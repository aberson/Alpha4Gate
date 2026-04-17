"""Unit tests for the build backlog."""

from __future__ import annotations

from bots.v0.build_backlog import BuildBacklog


class TestBuildBacklogAdd:
    def test_add_entry(self) -> None:
        bl = BuildBacklog()
        added = bl.add("Pylon", (10.0, 20.0), "no_resources", game_time=0.0)
        assert added is True
        assert len(bl) == 1
        assert bl.entries[0].structure_type == "Pylon"

    def test_add_respects_max_size(self) -> None:
        bl = BuildBacklog(max_size=2)
        bl.add("Pylon", (0.0, 0.0), "r1", game_time=0.0)
        bl.add("Gateway", (1.0, 1.0), "r2", game_time=1.0)
        added = bl.add("Forge", (2.0, 2.0), "r3", game_time=2.0)
        assert added is False
        assert len(bl) == 2

    def test_default_max_size_is_6(self) -> None:
        bl = BuildBacklog()
        for i in range(6):
            assert bl.add("Pylon", (float(i), 0.0), "r", game_time=0.0) is True
        assert bl.add("Pylon", (99.0, 0.0), "r", game_time=0.0) is False


class TestBuildBacklogTick:
    def test_tick_retries_oldest_affordable(self) -> None:
        bl = BuildBacklog()
        bl.add("Pylon", (1.0, 1.0), "r1", game_time=0.0)
        bl.add("Gateway", (2.0, 2.0), "r2", game_time=1.0)

        def can_afford(stype: str, loc: tuple[float, float]) -> bool:
            return stype == "Gateway"

        result = bl.tick(game_time=5.0, can_afford=can_afford)
        assert result is not None
        assert result.structure_type == "Gateway"
        assert len(bl) == 1  # Pylon remains

    def test_tick_returns_none_when_nothing_affordable(self) -> None:
        bl = BuildBacklog()
        bl.add("Pylon", (1.0, 1.0), "r1", game_time=0.0)
        result = bl.tick(game_time=5.0, can_afford=lambda s, loc: False)
        assert result is None
        assert len(bl) == 1

    def test_tick_expires_old_entries(self) -> None:
        bl = BuildBacklog(expiry_seconds=10.0)
        bl.add("Pylon", (1.0, 1.0), "r1", game_time=0.0)
        bl.add("Gateway", (2.0, 2.0), "r2", game_time=5.0)

        # At game_time=11, first entry is expired (11 - 0 = 11 >= 10)
        result = bl.tick(game_time=11.0, can_afford=lambda s, loc: True)
        # Should return Gateway (Pylon expired)
        assert result is not None
        assert result.structure_type == "Gateway"
        assert len(bl) == 0

    def test_tick_on_empty_backlog(self) -> None:
        bl = BuildBacklog()
        result = bl.tick(game_time=0.0, can_afford=lambda s, loc: True)
        assert result is None

    def test_tick_prefers_oldest_when_multiple_affordable(self) -> None:
        bl = BuildBacklog()
        bl.add("Pylon", (1.0, 1.0), "r1", game_time=0.0)
        bl.add("Gateway", (2.0, 2.0), "r2", game_time=1.0)

        result = bl.tick(game_time=5.0, can_afford=lambda s, loc: True)
        assert result is not None
        assert result.structure_type == "Pylon"

    def test_entry_expires_at_exact_expiry_boundary(self) -> None:
        """Entry at exactly expiry_seconds age should still be expired (< not <=)."""
        bl = BuildBacklog(expiry_seconds=10.0)
        bl.add("Pylon", (1.0, 1.0), "r1", game_time=0.0)
        # At exactly 10 seconds: 10 - 0 = 10, which is NOT < 10, so expired
        result = bl.tick(game_time=10.0, can_afford=lambda s, loc: True)
        assert result is None
        assert len(bl) == 0


class TestBuildBacklogClear:
    def test_clear_removes_all(self) -> None:
        bl = BuildBacklog()
        bl.add("Pylon", (1.0, 1.0), "r1", game_time=0.0)
        bl.add("Gateway", (2.0, 2.0), "r2", game_time=1.0)
        bl.clear()
        assert len(bl) == 0
