"""Elo ladder + cross-version promotion gate (Phase 4).

Reads/writes `data/bot_ladder.json`, runs round-robin comparisons between
top-N registry versions plus `bots/current`, and gates cross-version
promotion on Elo gain ≥ +10 over 20 self-play games AND WR non-regression
vs SC2 AI at the current curriculum difficulty.
"""
