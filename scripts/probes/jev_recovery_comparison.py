"""Offline recovery arbitration probe using the decision suite's shared fixtures.

Run separately per preserved source; no network, SC2, or viewer is started.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import runpy
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
source = args.source.resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(source / "src"))
sys.path.insert(0, str(source))
sys.path.append(str(root / "tests"))
# A v2 capture intentionally omits v1. The shared test module also imports v1
# fixtures; allow that frozen package from the checkout without replacing v2.
importlib.import_module("bots.jev").__path__.append(str(root / "bots/jev"))
fixture = root / "tests/test_jev_decision.py"
t = runpy.run_path(str(fixture))
provider = t["_QuestionProvider"]({
    "army_mode": [t["_army"](choice="attack")],
    "strategy": [t["_strategic"]("recover")],
})
clock = t["_Clock"]()
questions = t["HostedQuestions"](
    provider, t["DecisionConfig"](timeout=10, max_requests=100),
    t["V2_POLICY"], clock=clock,
)
polled = t["_drive"](
    questions, t["_FakeRuntime"](), clock, seconds=140,
    observe=lambda i: t["_v2_observation"](10 + i * 0.5, zealots=16),
)
holding = [i for i, d in enumerate(polled) if d.strategy["army"].choice == "recover"]
print(json.dumps({
    "source": str(source),
    "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
    "ready_combat_units": 16, "position": "home", "visible_enemies": 0,
    "window_game_seconds": 140,
    "recovery_contiguous": bool(holding) and holding == list(range(holding[0], holding[-1] + 1)),
    "recovery_span_game_seconds": (holding[-1] - holding[0]) * 0.5 if holding else 0,
    "final_army_intent": polled[-1].army_facts["choice"],
    "final_strategic_posture": polled[-1].strategy["army"].choice,
    "progress": polled[-1].strategy_facts.get("recovery_progress"),
    "network_requests": 0,
}, indent=2))
