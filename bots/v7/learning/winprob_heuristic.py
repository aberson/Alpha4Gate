"""Heuristic win-probability score for a GameSnapshot.

Implements the closed-form heuristic from
``documentation/investigations/win-probability-forecast-investigation.md`` §5
("Heuristic baseline (Option c)").  The function is intentionally small,
pure (no I/O, no DB reads, no side effects), and intended to drive a
debug indicator — not to be used as a classifier on its own.

The investigation showed this heuristic separates win/loss class means
cleanly while only edging out the majority-class baseline on accuracy.
That makes it a good operator-facing signal but not a learned model.
"""

from __future__ import annotations

from bots.v7.decision_engine import GameSnapshot


def score(snapshot: GameSnapshot) -> float:
    """Return a heuristic P(win) in [0.0, 1.0] for ``snapshot``.

    The formula is a fixed linear combination of normalized economy,
    army, tech, and threat features (see investigation §5 for the
    derivation and validation).  The result is clamped to [0, 1] after
    the linear combination so individual saturated terms cannot push
    the output out of probability range.
    """
    # Saturates at 2x our supply (cap = 1.0); treats invisible enemy as winning.
    ratio = min(snapshot.army_supply / max(snapshot.enemy_army_supply_visible, 1), 2.0) / 2.0

    economy = 0.5 * snapshot.worker_count / 50 + 0.5 * snapshot.base_count / 3
    supply = snapshot.supply_used / 200
    production = (snapshot.gateway_count + 2 * snapshot.robo_count) / 6
    upgrades = snapshot.upgrade_count / 4
    static_defense = (snapshot.cannon_count + snapshot.battery_count) / 4
    threat = 1.0 if snapshot.enemy_army_near_base else 0.0

    raw = (
        0.25 * ratio
        + 0.25 * economy
        + 0.15 * supply
        + 0.15 * production
        + 0.10 * upgrades
        + 0.10 * static_defense
        - 0.30 * threat
    )

    return max(0.0, min(1.0, raw))
