"""API QA — spin up AgentServer on a free port and exercise every route."""

from __future__ import annotations

import asyncio
import socket
import sys

import aiohttp

from bygfoot_tui.engine import GameState
from bygfoot_tui.agent_api import AgentServer


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def main() -> int:
    gs = GameState.new("england", "england1", 0, seed=42)
    port = _free_port()
    server = AgentServer(gs, port=port)
    await server.start()
    base = f"http://127.0.0.1:{port}"
    fails = 0

    async def check(name, coro) -> None:
        nonlocal fails
        try:
            await coro
            print(f"  \033[32m✓\033[0m {name}")
        except AssertionError as e:
            print(f"  \033[31m✗\033[0m {name}: {e}")
            fails += 1
        except Exception as e:
            print(f"  \033[31m✗\033[0m {name}: {type(e).__name__}: {e}")
            fails += 1

    try:
        async with aiohttp.ClientSession() as s:
            async def test_state():
                r = await s.get(base + "/state")
                assert r.status == 200
                j = await r.json()
                for k in ("country", "league", "season", "week",
                          "my_team", "next_fixture", "table_position"):
                    assert k in j, f"missing {k}: {j}"
                assert j["my_team"]["name"] == "Chelsea"
                assert j["week"] == 1
            await check("GET /state", test_state())

            async def test_table():
                r = await s.get(base + "/table")
                j = await r.json()
                assert isinstance(j, list)
                assert len(j) == 20
                assert all("name" in row for row in j)
            await check("GET /table", test_table())

            async def test_fixtures():
                r = await s.get(base + "/fixtures")
                j = await r.json()
                assert isinstance(j, list)
                assert len(j) == 38
            await check("GET /fixtures", test_fixtures())

            async def test_squad():
                r = await s.get(base + "/squad")
                j = await r.json()
                assert isinstance(j, list)
                assert len(j) >= 22
                assert all("pos" in p and "skill" in p for p in j)
            await check("GET /squad", test_squad())

            async def test_transfers():
                r = await s.get(base + "/transfers")
                j = await r.json()
                assert isinstance(j, list)
                assert len(j) > 0
                assert all("seller" in p for p in j)
            await check("GET /transfers", test_transfers())

            async def test_tactic_change():
                r = await s.post(base + "/tactic", json={"tactic": "3-5-2"})
                j = await r.json()
                assert j.get("ok") is True
                assert gs.my_team.tactic == "3-5-2"
                # Bad tactic → 400
                r2 = await s.post(base + "/tactic", json={"tactic": "junk"})
                assert r2.status == 400
            await check("POST /tactic", test_tactic_change())

            async def test_training_change():
                r = await s.post(base + "/training", json={"regime": "hard"})
                j = await r.json()
                assert j.get("ok") is True
                assert gs.training_regime == "hard"
            await check("POST /training", test_training_change())

            async def test_advance():
                week_before = gs.week
                r = await s.post(base + "/advance", json={"weeks": 3})
                j = await r.json()
                assert j["advanced"] == 3
                assert gs.week == week_before + 3
                assert isinstance(j["results"], list)
                assert len(j["results"]) == 3
            await check("POST /advance", test_advance())

            async def test_buy():
                gs.my_team.cash = 100000  # rich
                r = await s.get(base + "/transfers")
                listings = await r.json()
                # Pick the first affordable player.
                target = None
                for t in listings:
                    if int(t["value"] * 1.2) <= gs.my_team.cash:
                        target = t
                        break
                assert target is not None
                r2 = await s.post(base + "/buy", json={
                    "seller": target["seller"],
                    "name": target["name"],
                })
                j = await r2.json()
                assert j.get("ok") is True, j
            await check("POST /buy", test_buy())

            async def test_sell():
                # Sell our worst player — may fail (stochastic). Accept
                # either ok or a reasonable reason.
                lowest = min(gs.my_team.players, key=lambda p: p.skill)
                r = await s.post(base + "/sell", json={"name": lowest.name})
                j = await r.json()
                assert "ok" in j
                assert "reason" in j
            await check("POST /sell", test_sell())

            async def test_404_seller():
                r = await s.post(base + "/buy", json={
                    "seller": "Nonexistent FC", "name": "Ghost",
                })
                j = await r.json()
                assert j.get("ok") is False
                assert r.status == 400
            await check("POST /buy with bad seller", test_404_seller())

    finally:
        await server.stop()

    print(f"\n{11 - fails}/11 passed, {fails} failed")
    return fails


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
