"""Tests for the per-step heuristic-winprob log helper in ``bots.v0.bot``.

Phase N Step 3 wires a periodic operator-facing debug log line into
``Alpha4GateBot.on_step``.  The cadence and format are tested here against
the pure module-level helper ``_maybe_log_winprob`` so we don't have to
stand up a burnysc2 ``BotAI`` to pin the contract.
"""

from __future__ import annotations

import logging
import re

import pytest
from bots.v0.bot import _maybe_log_winprob
from bots.v0.decision_engine import GameSnapshot
from bots.v0.learning.winprob_heuristic import score

_LOGGER_NAME = "bots.v0.bot"


def _midrange_snapshot() -> GameSnapshot:
    """A snapshot whose score lands strictly inside (0, 1) without clamping.

    Mirrors the values from ``test_winprob_heuristic`` so the formatted
    score is non-trivial (not 0.00 or 1.00) and the format check is
    actually exercising both decimal places.
    """
    return GameSnapshot(
        supply_used=100,
        army_supply=20,
        worker_count=25,
        base_count=2,
        enemy_army_near_base=False,
        enemy_army_supply_visible=10,
        gateway_count=4,
        robo_count=1,
        upgrade_count=2,
        cannon_count=2,
        battery_count=0,
    )


@pytest.fixture
def logger() -> logging.Logger:
    """Return the ``bots.v0.bot`` logger used by the helper.

    Tests pass this logger explicitly so caplog's default capture (which
    attaches a handler to the root logger) sees the records.
    """
    return logging.getLogger(_LOGGER_NAME)


def test_logs_at_iteration_zero(
    caplog: pytest.LogCaptureFixture, logger: logging.Logger
) -> None:
    """Iteration 0 fires the cadence (``0 % 10 == 0``) and emits one record."""
    caplog.set_level(logging.INFO, logger=_LOGGER_NAME)

    _maybe_log_winprob(0, _midrange_snapshot(), "attack", logger)

    records = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO


def test_logs_every_tenth_iteration(
    caplog: pytest.LogCaptureFixture, logger: logging.Logger
) -> None:
    """Across iterations 0..30 inclusive, exactly 4 records (0, 10, 20, 30)."""
    caplog.set_level(logging.INFO, logger=_LOGGER_NAME)
    snap = _midrange_snapshot()

    for i in range(31):
        _maybe_log_winprob(i, snap, "attack", logger)

    records = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert len(records) == 4


def test_does_not_log_off_cadence(
    caplog: pytest.LogCaptureFixture, logger: logging.Logger
) -> None:
    """Off-cadence iterations (1, 5, 7, 9, 11) produce zero records."""
    caplog.set_level(logging.INFO, logger=_LOGGER_NAME)
    snap = _midrange_snapshot()

    for i in (1, 5, 7, 9, 11):
        _maybe_log_winprob(i, snap, "attack", logger)

    records = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert records == []


def test_log_format_matches_spec(
    caplog: pytest.LogCaptureFixture, logger: logging.Logger
) -> None:
    """The formatted message matches ``winprob=N.NN state=NAME`` exactly."""
    caplog.set_level(logging.INFO, logger=_LOGGER_NAME)
    snap = _midrange_snapshot()
    expected_prob = score(snap)

    _maybe_log_winprob(0, snap, "attack", logger)

    records = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert len(records) == 1

    # Verify the rendered output: regex pins the shape, exact-text pins the
    # precision (%.2f) and state-name substitution.
    assert re.fullmatch(r"winprob=\d\.\d{2} state=attack", records[0].getMessage())
    expected_text = f"winprob={expected_prob:.2f} state=attack"
    assert expected_text in caplog.text


def test_uses_strategic_state_name(
    caplog: pytest.LogCaptureFixture, logger: logging.Logger
) -> None:
    """A non-default ``state_name`` (e.g. ``"defend"``) appears verbatim."""
    caplog.set_level(logging.INFO, logger=_LOGGER_NAME)

    _maybe_log_winprob(0, _midrange_snapshot(), "defend", logger)

    records = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert len(records) == 1
    assert "state=defend" in records[0].getMessage()
