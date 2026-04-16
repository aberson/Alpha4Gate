"""Full-stack snapshot tool (Phase 2).

Copies `bots/current/` (currently a pointer to `bots/vN/`) to `bots/vN+1/`
with a new `VERSION` file and a fresh `manifest.json` carrying the parent,
git SHA, timestamp, Elo snapshot, and feature/action-space fingerprints.
"""
