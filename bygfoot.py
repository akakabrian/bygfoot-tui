"""Bygfoot TUI — entry point."""
from __future__ import annotations

import argparse
import asyncio

from bygfoot_tui.app import BygfootTUI


def main() -> None:
    ap = argparse.ArgumentParser(description="Bygfoot TUI")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--country", default="england")
    ap.add_argument("--league", default="england1")
    ap.add_argument("--team", type=int, default=0,
                    help="team index within chosen league (0 = first)")
    ap.add_argument("--picker", action="store_true",
                    help="launch the team picker instead of auto-starting")
    ap.add_argument("--agent", action="store_true",
                    help="enable the REST agent API on --agent-port")
    ap.add_argument("--agent-port", type=int, default=7655)
    ap.add_argument("--headless", action="store_true",
                    help="run engine + agent API only, no TUI (requires "
                         "--agent)")
    args = ap.parse_args()
    if args.headless:
        asyncio.run(_run_headless(args))
        return
    app = BygfootTUI(
        seed=args.seed,
        country=args.country,
        league=args.league,
        team_idx=args.team,
        autostart=not args.picker,
        agent=args.agent,
        agent_port=args.agent_port,
    )
    app.run()


async def _run_headless(args) -> None:
    from bygfoot_tui.engine import GameState
    from bygfoot_tui.agent_api import AgentServer
    gs = GameState.new(args.country, args.league, args.team, seed=args.seed)
    server = AgentServer(gs, port=args.agent_port)
    await server.start()
    print(f"[bygfoot-tui] agent API up on http://127.0.0.1:{args.agent_port}")
    print(f"    team: {gs.my_team.name}  league: {gs.my_league.name}")
    print(f"    Ctrl-C to stop.")
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        await server.stop()


if __name__ == "__main__":
    main()
