from __future__ import annotations

from alpha4gate.commands.dispatch_guard import DispatchGuard


def test_first_dispatch_allowed() -> None:
    guard = DispatchGuard()
    assert guard.should_dispatch("WarpIn", "STALKER", 0.0) is True


def test_within_cooldown_suppressed() -> None:
    guard = DispatchGuard()
    guard.mark_dispatched("WarpIn", "STALKER", 0.0)
    assert guard.should_dispatch("WarpIn", "STALKER", 1.0) is False


def test_after_cooldown_allowed() -> None:
    guard = DispatchGuard()
    guard.mark_dispatched("WarpIn", "STALKER", 0.0)
    assert guard.should_dispatch("WarpIn", "STALKER", 3.0) is True


def test_independent_keys() -> None:
    guard = DispatchGuard()
    guard.mark_dispatched("WarpIn", "STALKER", 0.0)
    assert guard.should_dispatch("WarpIn", "ZEALOT", 0.5) is True
    assert guard.should_dispatch("build", "Pylon", 0.5) is True
    assert guard.should_dispatch("WarpIn", "STALKER", 0.5) is False


def test_custom_cooldown_mapping() -> None:
    guard = DispatchGuard(per_action_cooldown={"WarpIn": 10.0})
    guard.mark_dispatched("WarpIn", "STALKER", 0.0)
    assert guard.should_dispatch("WarpIn", "STALKER", 5.0) is False
    assert guard.should_dispatch("WarpIn", "STALKER", 11.0) is True


def test_unknown_action_uses_default_cooldown() -> None:
    guard = DispatchGuard(per_action_cooldown={"WarpIn": 2.0}, default_cooldown=4.0)
    guard.mark_dispatched("custom_action", "thing", 0.0)
    assert guard.should_dispatch("custom_action", "thing", 3.0) is False
    assert guard.should_dispatch("custom_action", "thing", 5.0) is True
