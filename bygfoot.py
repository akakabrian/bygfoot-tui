"""Bygfoot TUI — entry point."""
from __future__ import annotations

import argparse

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
    args = ap.parse_args()
    app = BygfootTUI(
        seed=args.seed,
        country=args.country,
        league=args.league,
        team_idx=args.team,
        autostart=not args.picker,
    )
    app.run()


if __name__ == "__main__":
    main()
