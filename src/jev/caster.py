"""Optional, local-only entertainment for the battle viewer; never controls play."""

from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any

import httpx

MODEL = "qwen2.5:1.5b"
FACTS = (
    "A neural network's weights are numbers learned during training, not tiny stored neurons.",
    "A language model's tokens can be words, word fragments, or punctuation.",
    "Inference means running a trained model. Training means adjusting its parameters.",
    "Decision trees can make predictions without a neural network at all.",
    "A model's context window limits how much input it can consider at once.",
)
JOKES = (
    "Why did the Probe bring a ladder? It wanted to take its build order to the next level.",
    "I told my Gateway a dad joke. It warped out of the conversation.",
    "Two Templars walk into a bar. One Archon walks out. The bill was merged.",
    "My army started a band. Unfortunately, it only knows how to rush.",
    "Why are pylons great listeners? They always offer support.",
)


def snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Bounded recorded evidence, excluding secrets and hidden game information."""
    return {
        "run_status": state.get("status", "running"),
        "game_seconds": state.get("game_seconds", 0),
        "active_nodes": state.get("active_nodes", [])[:12],
        "events": [
            {
                "node": event.get("node_id"),
                "activity": str(event.get("reason", "")).split(" for task ")[0][:160],
                "status": event.get("status"),
            }
            for event in state.get("recent_events", [])
            if event.get("kind") in ("command", "task")
        ][-6:],
    }


class ArchonCaster:
    """One background request at a time, with a 14/3/3 shuffled editorial mix."""

    def __init__(self, evidence: Path) -> None:
        self.evidence = evidence
        self.latest: dict[str, Any] = {
            "text": "Two minds. One microphone. Waiting for the battle feed.",
            "source": "Archon Caster",
            "kind": "standby",
            "game_seconds": 0,
        }
        self.deck: list[str] = []

    def next_kind(self) -> str:
        if not self.deck:
            self.deck = ["commentary"] * 14 + ["fact"] * 3 + ["joke"] * 3
            random.shuffle(self.deck)
        return self.deck.pop()

    async def compose(self, state: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
        evidence = snapshot(state)
        kind = self.next_kind()
        # Pick the subject before generation: prose cannot invent graph references.
        candidates = [event["node"] for event in reversed(evidence["events"])]
        candidates.extend(evidence["active_nodes"])
        subject = next((node for node in candidates if isinstance(node, str) and node), None)
        node_ids = [subject] if kind == "commentary" and subject else []
        if node_ids:
            evidence["active_nodes"] = [n for n in evidence["active_nodes"] if n == subject]
            evidence["events"] = [e for e in evidence["events"] if e["node"] == subject]
            evidence["subject_node"] = subject
        source = "AI fact" if kind == "fact" else "Dad joke"
        if kind == "fact":
            text = random.choice(FACTS)
        elif kind == "joke":
            text = random.choice(JOKES)
        else:
            source = "Local Qwen"
            try:
                response = await client.post(
                    "http://127.0.0.1:11434/api/chat",
                    json={
                        "model": MODEL,
                        "stream": False,
                        "keep_alive": "2m",
                        "options": {
                            "num_gpu": 0,
                            "num_thread": 2,
                            "num_ctx": 2048,
                            "num_predict": 64,
                            "temperature": 0.3,
                        },
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are Archon Caster. Write ONE short sentence "
                                "describing only subject_node's activity, at most 30 words. "
                                "Use present tense. Plans and issued commands are NOT completed "
                                "actions. Never claim victory, completion, or destruction. "
                                "Example input: active_nodes=[construction.gateway]. "
                                "Example output: A Gateway is on the agenda; more warp-in "
                                "potential for our glorious Protoss. Plain text only.",
                            },
                            {"role": "user", "content": json.dumps(evidence)},
                        ],
                    },
                )
                response.raise_for_status()
                text = " ".join(response.json()["message"]["content"].split())[:400]
                if not text:
                    raise ValueError("Empty commentary")
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                branches = list(
                    dict.fromkeys(str(n).split(".")[0] for n in evidence["active_nodes"])
                )
                text = (
                    "The graph is working on "
                    + (subject or ", ".join(branches) or "its next move")
                    + ". We await glorious results."
                )
                source = "Local fallback"
        return {
            "text": text,
            "source": source,
            "kind": kind,
            "game_seconds": evidence["game_seconds"],
            "node_ids": node_ids,
        }

    def live_state(self) -> dict[str, Any] | None:
        path = self.evidence.parent / "state.json"
        state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if state.get("status") != "running" or time.time() - path.stat().st_mtime > 15:
            return None
        return state

    async def run(self) -> None:
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
            while True:
                try:
                    state = self.live_state()
                    if state:
                        item = await self.compose(state, client)
                        if self.live_state():
                            self.latest = item
                            with (self.evidence / "caster.jsonl").open(
                                "a", encoding="utf-8"
                            ) as log:
                                log.write(json.dumps(item) + "\n")
                except (OSError, ValueError, TypeError):
                    # Entertainment must never interrupt the viewer or gameplay.
                    pass
                await asyncio.sleep(30)
