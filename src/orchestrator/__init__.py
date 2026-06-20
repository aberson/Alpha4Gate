"""Alpha4Gate orchestrator — cross-version infrastructure.

Hosts the frozen substrate for subprocess self-play between `bots/vN/` versions:

- `registry` — version discovery and per-version data-path resolution
- `contracts` — typed dataclasses for bot-spawn args, match results, manifests
- `snapshot` — full-stack snapshot tool (Phase 2)
- `selfplay` — subprocess self-play runner with port-collision workaround (Phase 3)
- `ladder` — Elo ladder + cross-version promotion gate (Phase 4)
- `evolve` — sibling-tournament round primitive for the evolve loop (Phase 9)
- `staleness` — per-version policy-staleness signal (Phase 7)

Everything under this package is the "orchestrator substrate" — the only code
`/improve-bot-advised` is not allowed to touch once the Phase 5 sandbox lands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestrator.staleness import StalenessReport, compute_staleness

__all__ = [
    "StalenessReport",
    "compute_staleness",
]


def __getattr__(name: str) -> Any:
    """Lazily expose selected submodule symbols at package level (PEP 562).

    Importing the submodule eagerly here would make ``python -m
    orchestrator.staleness`` emit a runpy double-import ``RuntimeWarning`` (the
    package init imports the module, then runpy re-executes it as ``__main__``).
    Resolving on first attribute access keeps ``from orchestrator import
    compute_staleness`` working without that side effect.
    """
    if name in ("StalenessReport", "compute_staleness"):
        from orchestrator import staleness

        return getattr(staleness, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
