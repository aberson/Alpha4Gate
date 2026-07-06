"""Posterior statistics for the regression rollback gate (Phase EJ.3).

Single source of truth for the *one-sided posterior* rollback rule and the two
constants that parameterize it. The strict-majority gate (``wins_new < games //
2 + 1``) rolls back a truly-neutral promotion ~50 % of the time at ``n=5`` and
counts draws/crashes against the new parent (they shrink the decided count while
the majority bar stays fixed). The one-sided rule instead rolls back **only on
positive evidence of harm** — a high posterior probability that the new parent
is genuinely worse than a coin flip.

Public surface:

- :func:`posterior_prob_worse` — ``P(p < 0.5 | p ~ Beta(1+wins, 1+losses))``,
  the posterior probability the new parent's true win-rate is below 0.5.
- :func:`one_sided_rollback` — the rollback verdict + a human-readable reason,
  fail-open below :data:`MIN_DECIDED` decided games.
- :data:`MIN_DECIDED`, :data:`ROLLBACK_THRESHOLD` — the two tuning constants.
  Callers and tests import these rather than re-literalling ``4`` / ``0.85`` so
  there is exactly one place to change them (Phase R may extend this module).

Implementation notes:

* No :mod:`scipy`, no floating-point beta functions. :func:`posterior_prob_worse`
  uses the exact closed-form binomial-tail identity: with a uniform ``Beta(1,1)``
  prior, ``P(p < 0.5 | wins, losses)`` equals ``P(Binomial(n1, 0.5) >= wins + 1)``
  where ``n1 = wins + losses + 1``. That tail is an integer sum of
  :func:`math.comb` terms over ``2 ** n1``, so the result is an exact rational
  cast to :class:`float` (exact for every ``n1`` whose ``2 ** n1`` fits the
  double mantissa — far beyond any realistic games-per-eval).
* This module is part of the orchestrator substrate and imports nothing from
  ``bots.*`` (no MetaPathFinder risk); it depends only on the stdlib.
"""

from __future__ import annotations

import math

__all__ = [
    "MIN_DECIDED",
    "ROLLBACK_THRESHOLD",
    "one_sided_rollback",
    "posterior_prob_worse",
]

# Minimum number of *decided* games (wins_new + wins_prior; draws/crashes
# excluded) before the posterior is trusted. Below this the gate fails OPEN
# (keeps the promotion) rather than rolling back on a near-empty sample.
MIN_DECIDED = 4

# Posterior-probability-of-worse at or above which the new parent is rolled
# back. 0.85 rolls back only 0-5 (0.984) and 1-4 (0.891) at n=5 decided; the
# genuinely-neutral 2-3 (0.656) is KEPT.
ROLLBACK_THRESHOLD = 0.85


def posterior_prob_worse(wins: int, losses: int) -> float:
    """Return ``P(p < 0.5 | p ~ Beta(1 + wins, 1 + losses))``.

    The posterior probability that the new parent's true win-rate ``p`` is
    below a coin flip, under a uniform ``Beta(1, 1)`` prior updated by *wins*
    and *losses* (decisive games only — draws/crashes are excluded upstream).

    Computed exactly via the binomial-tail identity — no scipy, no float beta
    functions::

        n1 = wins + losses + 1
        P(p < 0.5 | wins, losses) = P(Binomial(n1, 0.5) >= wins + 1)
                                  = sum_{k=wins+1}^{n1} C(n1, k) * 0.5 ** n1

    The ``wins = losses = 0`` case gives ``n1 = 1`` → ``P(X >= 1)`` on
    ``Bin(1, 0.5)`` → exactly ``0.5``.
    """
    if wins < 0 or losses < 0:
        raise ValueError(f"wins/losses must be non-negative; got wins={wins}, losses={losses}")
    n1 = wins + losses + 1
    tail = sum(math.comb(n1, k) for k in range(wins + 1, n1 + 1))
    # 1 << n1 == 2 ** n1 as an exact int; int / int -> float (exact for every
    # power-of-two denominator that fits the double mantissa).
    return tail / (1 << n1)


def one_sided_rollback(
    wins_new: int,
    wins_prior: int,
    *,
    min_decided: int = MIN_DECIDED,
    threshold: float = ROLLBACK_THRESHOLD,
) -> tuple[bool, str]:
    """Decide whether to roll back under the one-sided posterior rule.

    Returns ``(rollback, reason)``. ``decided = wins_new + wins_prior`` counts
    only decisive games. Below *min_decided* the sample is too small to trust,
    so the gate fails OPEN — ``(False, ...)`` keeps the promotion. At or above
    *min_decided*, roll back iff :func:`posterior_prob_worse` (the probability
    the new parent is truly worse than a coin flip) is ``>= threshold``.

    At ``n=5`` decided with the default threshold this rolls back only 0-5
    (0.984) and 1-4 (0.891); the neutral 2-3 (0.656) is kept.
    """
    decided = wins_new + wins_prior
    if decided < min_decided:
        return (
            False,
            f"only {decided} decided games (< min_decided={min_decided}); keep (fail-open)",
        )
    p_worse = posterior_prob_worse(wins_new, wins_prior)
    rollback = p_worse >= threshold
    verdict = "rollback" if rollback else "keep"
    reason = (
        f"P(worse)={p_worse:.4f} {'>=' if rollback else '<'} "
        f"threshold={threshold}; {verdict} "
        f"(new {wins_new}-{wins_prior}, {decided} decided)"
    )
    return (rollback, reason)
