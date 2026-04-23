"""Headless playtest — drives the TUI through a realistic user flow and
saves an SVG at each checkpoint.

Steps (mirror a first-time player's first session):
    1. boot with autostart=False → team picker
    2. pick first country / first league / first team (enter)
    3. view fixtures tab
    4. play a week (advances sim + logs user's match)
    5. open match tab and read ticker output
    6. view squad tab
    7. quit

Each checkpoint produces tests/out/playtest_NN_<name>.svg.

Run:
    python -m tests.playtest
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

from textual.widgets import DataTable, RichLog

from bygfoot_tui.app import BygfootTUI, TeamPickerScreen


OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)


def _snap(app, n: int, name: str) -> Path:
    p = OUT / f"playtest_{n:02d}_{name}.svg"
    app.save_screenshot(str(p))
    return p


async def playtest() -> int:
    """Walk the full flow. Returns 0 on success, 1 on failure."""
    # Autostart off → the team picker modal is pushed on mount.
    app = BygfootTUI(autostart=False)
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.pause()

        # Step 1 — picker showing.
        snap = _snap(app, 1, "picker_shown")
        print(f"  [01] picker shown               -> {snap.name}")

        # Step 2 — pick first country / league / team (all highlighted by
        # default) and confirm. The ListView swallows enter, so invoke
        # the picker's action directly (what the binding would do).
        picker = app.screen
        assert isinstance(picker, TeamPickerScreen), (
            f"expected picker, got {type(picker).__name__}"
        )
        picker.action_confirm()
        await pilot.pause()
        assert app.gs is not None, "GameState did not initialise after picker"
        snap = _snap(app, 2, "team_selected")
        print(f"  [02] team selected              -> {snap.name}  "
              f"({app.gs.my_team.name} / {app.gs.my_league.name})")

        # Step 3 — view fixtures tab.
        await pilot.press("2")
        await pilot.pause()
        dt = app.query_one("#fixtures_table", DataTable)
        assert dt.row_count == 38, (
            f"fixtures expected 38 rows, got {dt.row_count}"
        )
        snap = _snap(app, 3, "fixtures_tab")
        print(f"  [03] fixtures tab               -> {snap.name}  "
              f"({dt.row_count} fixtures)")

        # Step 4 — play a week.
        week_before = app.gs.week
        await pilot.press("w")
        await pilot.pause()
        assert app.gs.week == week_before + 1, (
            f"week didn't advance: {week_before} -> {app.gs.week}"
        )
        snap = _snap(app, 4, "played_week_1")
        print(f"  [04] played week 1              -> {snap.name}  "
              f"(week now {app.gs.week})")

        # Step 5 — Match tab already auto-switched by play_week; check log
        # contains the FULL TIME line.
        await pilot.press("4")
        await pilot.pause()
        log = app.query_one("#match_log", RichLog)
        joined = "\n".join(str(line) for line in log.lines)
        assert "FULL TIME" in joined, "match log missing FULL TIME"
        assert app.gs.my_team.name in joined, (
            "user team not mentioned in match log"
        )
        snap = _snap(app, 5, "match_ticker")
        print(f"  [05] match ticker filled        -> {snap.name}  "
              f"({len(log.lines)} lines)")

        # Step 6 — squad tab.
        await pilot.press("3")
        await pilot.pause()
        squad = app.query_one("#squad_table", DataTable)
        assert squad.row_count >= 20, (
            f"squad shrunk below 20: {squad.row_count}"
        )
        snap = _snap(app, 6, "squad_tab")
        print(f"  [06] squad tab                  -> {snap.name}  "
              f"({squad.row_count} players)")

        # Step 7 — back to table tab, then quit.
        await pilot.press("1")
        await pilot.pause()
        snap = _snap(app, 7, "table_after_week_1")
        played_sum = sum(t.played for t in app.gs.my_league.teams)
        assert played_sum == 20, f"played sum = {played_sum}, expected 20"
        print(f"  [07] league table after week 1  -> {snap.name}")

        await pilot.press("q")
        # The quit action tears the app down; pilot may or may not return
        # immediately, so we don't assert further.

    return 0


async def main() -> int:
    print("bygfoot-tui playtest")
    print("-" * 60)
    try:
        return await playtest()
    except AssertionError as e:
        print(f"\n  FAIL: {e}")
        return 1
    except Exception as e:
        print(f"\n  ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
