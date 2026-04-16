"""Subprocess self-play runner (Phase 3).

Will absorb the port-collision workaround from the Phase 0 spike
(`scripts/spike_subprocess_selfplay.py`) and expose a batch runner:

    python -m orchestrator.selfplay --p1 v3 --p2 v5 --games 20 --map Simple64

Per-game results go to `data/selfplay_results.jsonl` (shared, append-only).
The Phase 0 spike's `Portconfig.contiguous_ports` monkey-patch lives here
once this module is filled in.
"""
