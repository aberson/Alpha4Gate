"""Caster isolation and graceful local-service failure."""

import asyncio
from collections import Counter

import httpx

from jev.caster import MODEL, ArchonCaster


def test_editorial_mix(tmp_path):
    caster = ArchonCaster(tmp_path)
    assert Counter(caster.next_kind() for _ in range(20)) == {
        "commentary": 14,
        "fact": 3,
        "joke": 3,
    }


def test_local_request_and_unavailable_fallback(tmp_path):
    caster = ArchonCaster(tmp_path)
    state = {"game_seconds": 12, "active_nodes": ["army.attack"], "secret": "excluded"}

    def respond(request):
        assert str(request.url) == "http://127.0.0.1:11434/api/chat"
        assert MODEL.encode() in request.content
        assert b"excluded" not in request.content
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"message": {"content": "The army has an attack plan."}})

    async def exercise():
        caster.deck = ["commentary"]
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            item = await caster.compose(state, client)
            assert item["source"] == "Local Qwen"
            assert item["game_seconds"] == 12
        caster.deck = ["commentary"]
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503),
            )
        ) as client:
            item = await caster.compose(state, client)
            assert item["source"] == "Local fallback"
            assert "army" in item["text"]

    asyncio.run(exercise())
