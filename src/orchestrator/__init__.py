"""Alpha4Gate orchestrator — cross-version infrastructure.

Hosts the frozen substrate for subprocess self-play between `bots/vN/` versions:

- `registry` — version discovery and per-version data-path resolution
- `contracts` — typed dataclasses for bot-spawn args, match results, manifests
- `snapshot` — full-stack snapshot tool (Phase 2)
- `selfplay` — subprocess self-play runner with port-collision workaround (Phase 3)
- `ladder` — Elo ladder + cross-version promotion gate (Phase 4)

Everything under this package is the "orchestrator substrate" — the only code
`/improve-bot-advised` is not allowed to touch once the Phase 5 sandbox lands.
"""
