"""REST API over aiohttp for remote/LLM agents.

Endpoints:
    GET  /state                — full game state snapshot
    GET  /table                — user league table
    GET  /fixtures             — user fixtures + results so far
    GET  /squad                — user squad with stats
    GET  /transfers            — current transfer listing
    POST /tactic  {tactic}     — change tactic (4-4-2 / 4-3-3 / ...)
    POST /training {regime}    — change training intensity
    POST /advance {weeks:int}  — advance N weeks (default 1)
    POST /buy  {seller, name}  — buy a player
    POST /sell {name}          — sell a player

Enabled via `--agent` flag in bygfoot.py. Default port 7655.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from . import engine


def state_snapshot(gs: engine.GameState) -> dict[str, Any]:
    """Compact JSON-safe snapshot — suitable for a 4k-context LLM call."""
    t = gs.my_team
    nxt = gs.my_next_fixture()
    return {
        "country": gs.country_sid,
        "league": gs.my_league.name,
        "league_sid": gs.my_league_sid,
        "season": gs.season,
        "week": gs.week,
        "max_week": gs.max_week,
        "season_over": gs.season_over(),
        "my_team": {
            "name": t.name,
            "tactic": t.tactic,
            "cash": t.cash,
            "played": t.played,
            "won": t.won,
            "drew": t.drew,
            "lost": t.lost,
            "gf": t.gf,
            "ga": t.ga,
            "points": t.points,
            "form": list(t.form),
            "squad_size": len(t.players),
        },
        "next_fixture": (
            {
                "home": nxt[0].name,
                "away": nxt[1].name,
                "is_home": nxt[2],
            } if nxt else None
        ),
        "table_position": next(
            (i + 1 for i, x in enumerate(gs.my_league.table())
             if x.is_user), 0
        ),
        "training_regime": gs.training_regime,
    }


def _player_to_dict(p: engine.Player) -> dict[str, Any]:
    return {
        "name": p.name, "pos": engine.POS_NAMES[p.position],
        "age": p.age, "skill": p.skill, "talent": p.talent,
        "fitness": p.fitness, "goals": p.goals, "assists": p.assists,
        "yellow": p.yellow, "red": p.red, "games": p.games,
        "value": p.value, "wage": p.wage,
        "injury_weeks": p.injury_weeks, "available": p.available,
    }


def table_snapshot(gs: engine.GameState) -> list[dict[str, Any]]:
    return [
        {
            "rank": i + 1, "name": t.name, "played": t.played,
            "won": t.won, "drew": t.drew, "lost": t.lost,
            "gf": t.gf, "ga": t.ga, "gd": t.gd, "points": t.points,
            "form": list(t.form), "is_user": t.is_user,
        }
        for i, t in enumerate(gs.my_league.table())
    ]


def fixtures_snapshot(gs: engine.GameState) -> list[dict[str, Any]]:
    lg = gs.my_league
    out = []
    for wk, fx in enumerate(lg.fixtures, start=1):
        results_this_wk = (lg.results[wk - 1] if wk - 1 < len(lg.results)
                           else [])
        res_by_pair = {(r.home.name, r.away.name):
                       (r.home_goals, r.away_goals)
                       for r in results_this_wk}
        for h, a in fx:
            if gs.my_team_idx not in (h, a):
                continue
            home = lg.teams[h].name
            away = lg.teams[a].name
            score = res_by_pair.get((home, away))
            out.append({
                "week": wk,
                "home": home,
                "away": away,
                "is_home": (h == gs.my_team_idx),
                "result": (f"{score[0]}-{score[1]}" if score else None),
            })
    return out


class AgentServer:
    """Lightweight aiohttp server — owns the runner so we can shut down
    cleanly when the TUI exits."""

    def __init__(self, gs: engine.GameState, host: str = "127.0.0.1",
                 port: int = 7655) -> None:
        self.gs = gs
        self.host = host
        self.port = port
        self.app = web.Application()
        self._setup_routes()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    def _setup_routes(self) -> None:
        r = self.app.router
        r.add_get("/state", self._state)
        r.add_get("/table", self._table)
        r.add_get("/fixtures", self._fixtures)
        r.add_get("/squad", self._squad)
        r.add_get("/transfers", self._transfers)
        r.add_post("/tactic", self._tactic)
        r.add_post("/training", self._training)
        r.add_post("/advance", self._advance)
        r.add_post("/buy", self._buy)
        r.add_post("/sell", self._sell)

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

    async def stop(self) -> None:
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()

    # ---- routes ----

    async def _state(self, _req) -> web.Response:
        return web.json_response(state_snapshot(self.gs))

    async def _table(self, _req) -> web.Response:
        return web.json_response(table_snapshot(self.gs))

    async def _fixtures(self, _req) -> web.Response:
        return web.json_response(fixtures_snapshot(self.gs))

    async def _squad(self, _req) -> web.Response:
        return web.json_response(
            [_player_to_dict(p) for p in self.gs.my_team.players]
        )

    async def _transfers(self, _req) -> web.Response:
        listings = engine.transfer_listing(self.gs, size=30)
        return web.json_response([
            {"seller": s.name, **_player_to_dict(p)}
            for s, p in listings
        ])

    async def _tactic(self, req) -> web.Response:
        data = await req.json()
        tactic = data.get("tactic", "")
        if tactic not in engine.TACTICS:
            return web.json_response(
                {"ok": False, "reason": f"unknown tactic {tactic!r}"},
                status=400,
            )
        self.gs.my_team.tactic = tactic
        return web.json_response({"ok": True, "tactic": tactic})

    async def _training(self, req) -> web.Response:
        data = await req.json()
        regime = data.get("regime", "")
        if regime not in engine.TRAINING_REGIMES:
            return web.json_response(
                {"ok": False, "reason": f"unknown regime {regime!r}"},
                status=400,
            )
        self.gs.training_regime = regime
        return web.json_response({"ok": True, "regime": regime})

    async def _advance(self, req) -> web.Response:
        data = await req.json() if req.can_read_body else {}
        weeks = int(data.get("weeks", 1))
        results = []
        for _ in range(weeks):
            if self.gs.season_over():
                self.gs.end_season()
            res = self.gs.play_current_week()
            # Store results into leagues so the fixtures endpoint reflects them
            wk = self.gs.week - 2  # just-played week (gs.week is now NEXT)
            for lg in self.gs.leagues.values():
                while len(lg.results) <= wk:
                    lg.results.append([])
            for r in res:
                lg = next(l for l in self.gs.leagues.values()
                          if r.home in l.teams and r.away in l.teams)
                lg.results[wk].append(r)
            user_res = next((r for r in res
                             if r.home.is_user or r.away.is_user), None)
            if user_res:
                results.append({
                    "week": self.gs.week - 1,
                    "home": user_res.home.name,
                    "away": user_res.away.name,
                    "score": f"{user_res.home_goals}-{user_res.away_goals}",
                })
        return web.json_response({
            "advanced": weeks,
            "week": self.gs.week,
            "results": results,
            "state": state_snapshot(self.gs),
        })

    async def _buy(self, req) -> web.Response:
        data = await req.json()
        seller_name = data.get("seller", "")
        player_name = data.get("name", "")
        # Find seller team.
        seller = None
        for lg in self.gs.leagues.values():
            for t in lg.teams:
                if t.name == seller_name:
                    seller = t
                    break
            if seller:
                break
        if seller is None:
            return web.json_response(
                {"ok": False, "reason": f"unknown seller {seller_name!r}"},
                status=400,
            )
        player = next((p for p in seller.players
                       if p.name == player_name), None)
        if player is None:
            return web.json_response(
                {"ok": False, "reason": f"{player_name!r} not in "
                                        f"{seller_name} roster"},
                status=400,
            )
        ok, reason = engine.buy_player(self.gs, seller, player)
        return web.json_response({"ok": ok, "reason": reason})

    async def _sell(self, req) -> web.Response:
        data = await req.json()
        player_name = data.get("name", "")
        player = next((p for p in self.gs.my_team.players
                       if p.name == player_name), None)
        if player is None:
            return web.json_response(
                {"ok": False, "reason": f"{player_name!r} not on your squad"},
                status=400,
            )
        ok, reason = engine.sell_player(self.gs, player)
        return web.json_response({"ok": ok, "reason": reason})
