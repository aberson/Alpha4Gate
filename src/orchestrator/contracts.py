"""Frozen cross-version contracts.

Phase 1.2 fills in the real implementation. These dataclasses define the
interfaces that every `bots/vN/` must honor (bot-spawn CLI, result JSON,
manifest schema). Changing any of these is a human-PR event per the master
plan — they are NOT inside the `/improve-bot-advised` sandbox.
"""
