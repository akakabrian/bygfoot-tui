"""Perf baseline for Bygfoot TUI.

Unlike a tile-map game, Bygfoot's hot paths are:
  1. GameState.new() — parse XML + generate 5 leagues × 20 teams × 22 players
  2. play_current_week() — simulate ~58 matches across all loaded leagues
  3. simulate_match() — 90-minute per-match
  4. LeagueState.table() — sort 20 teams (called on every refresh)
  5. TUI refresh_all() — rebuild 4 DataTables

Run:
    python -m tests.perf
"""

from __future__ import annotations

import asyncio
import time

from bygfoot_tui.engine import GameState, simulate_match, quickstart


def bench(label: str, fn, n: int = 1) -> float:
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    dt = (time.perf_counter() - t0) / n
    print(f"  {label:<40s}  {dt*1000:8.2f} ms  (×{n})")
    return dt


def main() -> None:
    print("=== Bygfoot TUI perf baseline ===\n")

    # 1. Cold start
    def _new():
        return GameState.new("england", "england1", 0, seed=42)
    bench("GameState.new (cold)", _new, n=1)

    gs = _new()

    # 2. Single match
    home = gs.my_league.teams[0]
    away = gs.my_league.teams[1]
    bench("simulate_match (one)", lambda: simulate_match(
        home, away, gs.rng, gs.commentary_templates), n=50)

    # 3. Whole week (5 leagues × ~10 matches each)
    # Use a throwaway GS since play_current_week mutates state.
    def _week():
        local = _new()
        local.play_current_week()
    bench("play_current_week (all leagues)", _week, n=10)

    # 4. Table sort
    bench("LeagueState.table() sort", lambda: gs.my_league.table(), n=5000)

    # 5. TUI refresh_all (needs an App)
    from bygfoot_tui.app import BygfootTUI

    async def _refresh_bench() -> float:
        app = BygfootTUI()
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            t0 = time.perf_counter()
            for _ in range(20):
                app.refresh_all()
            return (time.perf_counter() - t0) / 20

    dt = asyncio.run(_refresh_bench())
    print(f"  {'refresh_all (4 tables)':<40s}  {dt*1000:8.2f} ms  (×20)")

    # 6. Full season (stress)
    def _season():
        local = _new()
        while not local.season_over():
            local.play_current_week()
    bench("full season (38 weeks, all leagues)", _season, n=1)


if __name__ == "__main__":
    main()
