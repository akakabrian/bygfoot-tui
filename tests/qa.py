"""Headless QA for Bygfoot TUI.

Runs each scenario in a fresh `BygfootTUI` via `App.run_test()`, saves an
SVG screenshot, and reports pass/fail. Exit code is #failures.

    python -m tests.qa           # all
    python -m tests.qa tactic    # substring match
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from textual.widgets import DataTable, RichLog, Static, TabbedContent

from bygfoot_tui.app import BygfootTUI
from bygfoot_tui.engine import GameState, POS_NAMES, TACTICS


OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)


@dataclass
class Scenario:
    name: str
    fn: Callable[[BygfootTUI, "object"], Awaitable[None]]


# ---- scenarios ----


async def s_mount_clean(app, pilot):
    assert app.gs is not None, "GameState not initialised"
    assert app.gs.my_team.name, "team has no name"
    assert len(app.gs.my_team.players) >= 20
    # All five tabs exist.
    tc = app.query_one(TabbedContent)
    tab_ids = {pane.id for pane in tc.query("TabPane")}
    for needed in ("tab_table", "tab_fixtures", "tab_squad",
                   "tab_match", "tab_finance"):
        assert needed in tab_ids, f"missing tab: {needed}"


async def s_status_bar_populated(app, pilot):
    txt = app._last_status_text
    assert app.gs.my_team.name in txt, txt
    assert "Season 1" in txt, txt
    assert "Week 1/38" in txt, txt


async def s_table_has_20_teams(app, pilot):
    dt = app.query_one("#league_table", DataTable)
    assert dt.row_count == 20, f"table rows = {dt.row_count}, expected 20"
    # 10 columns (#, Team, P, W, D, L, GF, GA, GD, Pts)
    assert len(dt.columns) == 10, f"columns = {len(dt.columns)}"


async def s_fixtures_show_user_team(app, pilot):
    dt = app.query_one("#fixtures_table", DataTable)
    assert dt.row_count == 38, f"fixtures rows = {dt.row_count}, expected 38"


async def s_squad_22_rows(app, pilot):
    dt = app.query_one("#squad_table", DataTable)
    assert dt.row_count == 22, f"squad = {dt.row_count}, expected 22"


async def s_play_week_advances_week(app, pilot):
    week_before = app.gs.week
    await pilot.press("w")
    await pilot.pause()
    assert app.gs.week == week_before + 1, (
        f"week {week_before} → {app.gs.week}"
    )


async def s_play_week_logs_user_match(app, pilot):
    log = app.query_one("#match_log", RichLog)
    assert len(log.lines) == 0
    await pilot.press("w")
    await pilot.pause()
    # Should contain a header line mentioning the user's team.
    joined = "\n".join(str(l) for l in log.lines)
    assert app.gs.my_team.name in joined, (
        f"user team not mentioned in match log\n{joined[:400]}"
    )
    assert "FULL TIME" in joined, joined[:400]


async def s_play_week_updates_table(app, pilot):
    dt = app.query_one("#league_table", DataTable)
    # Before any play, everyone has 0 points and 0 played.
    await pilot.press("w")
    await pilot.pause()
    # After one week, every team should have played exactly 1 match.
    played_sum = sum(t.played for t in app.gs.my_league.teams)
    assert played_sum == 20, f"played sum = {played_sum}, expected 20"


async def s_tactic_change(app, pilot):
    before = app.gs.my_team.tactic
    app.gs.my_team.tactic = "3-5-2"
    app.refresh_all()
    assert app.gs.my_team.tactic == "3-5-2"
    assert "3-5-2" in app._last_status_text
    app.gs.my_team.tactic = before


async def s_tab_keys(app, pilot):
    tc = app.query_one(TabbedContent)
    await pilot.press("3")
    await pilot.pause()
    assert tc.active == "tab_squad", tc.active
    await pilot.press("5")
    await pilot.pause()
    assert tc.active == "tab_finance", tc.active
    await pilot.press("1")
    await pilot.pause()
    assert tc.active == "tab_table", tc.active


async def s_finance_shows_cash(app, pilot):
    dt = app.query_one("#finance_table", DataTable)
    assert dt.row_count >= 5, dt.row_count


async def s_full_season(app, pilot):
    """Play a whole 38-week season — league must end coherent."""
    # Drive the sim directly; we don't need TUI refreshes for every week.
    gs = app.gs
    for _ in range(gs.max_week):
        gs.play_current_week()
    # Season is over.
    assert gs.season_over(), f"season not over: week={gs.week}"
    table = gs.my_league.table()
    # Champion has the most points.
    assert table[0].points >= table[-1].points
    # Every team played 38 matches.
    for t in gs.my_league.teams:
        assert t.played == gs.max_week, (
            f"{t.name} played {t.played}, expected {gs.max_week}"
        )
    # Total goals scored = total goals conceded across the league.
    assert sum(t.gf for t in gs.my_league.teams) == sum(
        t.ga for t in gs.my_league.teams
    )


async def s_end_season_promotes(app, pilot):
    """After a full season + end_season, user stays in the system.
    Promotion/relegation shouldn't lose our user pointer."""
    gs = app.gs
    while not gs.season_over():
        gs.play_current_week()
    user_team = gs.my_team
    lines = gs.end_season()
    # Season counter advanced.
    assert gs.season == 2, f"season = {gs.season}"
    assert gs.week == 1
    # User pointer still resolves.
    assert gs.my_team is user_team, (
        f"user pointer lost: {gs.my_team.name} vs {user_team.name}"
    )
    # Everyone's stats reset.
    for t in gs.my_league.teams:
        assert t.played == 0, f"{t.name} played {t.played}"
    # Lines returned is either empty or non-empty, both fine — just
    # sanity check it's a list of strings.
    assert isinstance(lines, list)
    for l in lines:
        assert isinstance(l, str)


async def s_match_sim_scores_reasonable(app, pilot):
    """Sanity check: over a season, average goals per match should be
    between 1.5 and 5.0 (real football averages ~2.7)."""
    gs = app.gs
    goals = 0
    matches = 0
    while not gs.season_over():
        for r in gs.play_current_week():
            goals += r.home_goals + r.away_goals
            matches += 1
    avg = goals / matches
    assert 1.5 <= avg <= 5.0, f"avg goals/match = {avg:.2f}"


async def s_injuries_progress(app, pilot):
    """A player put on the treatment table should count down each week
    and eventually return."""
    gs = app.gs
    p = gs.my_team.players[0]
    p.injury_weeks = 3
    assert not p.available
    for _ in range(3):
        gs.play_current_week()
    assert p.injury_weeks == 0, f"injury still pending: {p.injury_weeks}"
    assert p.available


async def s_commentary_loads(app, pilot):
    """At least a handful of pre-match flavour templates should be ready."""
    assert len(app.gs.commentary_templates) > 0, "no commentary loaded"


async def s_all_positions_present(app, pilot):
    """Generated squad must contain at least one GK, 4+ DEF, 4+ MID, 2+ FWD."""
    counts = [0, 0, 0, 0]
    for p in app.gs.my_team.players:
        counts[p.position] += 1
    assert counts[0] >= 1, f"no GK: {counts}"
    assert counts[1] >= 4, f"too few DEF: {counts}"
    assert counts[2] >= 4, f"too few MID: {counts}"
    assert counts[3] >= 2, f"too few FWD: {counts}"


async def s_xi_has_11(app, pilot):
    xi = app.gs.my_team.starting_xi()
    assert len(xi) == 11, f"XI has {len(xi)}"
    # Exactly one GK.
    gks = [p for p in xi if p.position == 0]
    assert len(gks) == 1, f"XI GK count = {len(gks)}"


async def s_player_name_not_empty(app, pilot):
    for p in app.gs.my_team.players:
        assert p.name.strip(), f"empty player name: {p}"
        assert " " in p.name, f"no space in name: {p.name!r}"


async def s_view_match_populates_log(app, pilot):
    """action_view_match should stream at least a full-time line into the
    match log."""
    await app.action_view_match()
    log = app.query_one("#match_log", RichLog)
    joined = "\n".join(str(l) for l in log.lines)
    assert "FULL TIME" in joined, joined[:400]
    # Week advanced.
    assert app.gs.week == 2


async def s_multiple_weeks_no_duplicate_fixtures(app, pilot):
    """Regression: advancing multiple weeks must not double-record results
    or lose the week slot."""
    gs = app.gs
    for _ in range(5):
        await pilot.press("w")
        await pilot.pause()
    # Each played team should have exactly 5 played.
    for t in gs.my_league.teams:
        assert t.played == 5, f"{t.name} played {t.played}"


async def s_play_week_past_season_end_safe(app, pilot):
    """Pressing w after season end shouldn't crash or decrement anything."""
    gs = app.gs
    # Jump to just past the season.
    while not gs.season_over():
        gs.play_current_week()
    # Now press w — should run end_season + reset.
    await pilot.press("w")
    await pilot.pause()
    assert gs.season >= 2


async def s_unknown_tactic_does_not_crash(app, pilot):
    """Robustness: accept_tactic with a garbage value ignored silently."""
    before = app.gs.my_team.tactic
    app._on_tactic(None)               # no-op cancel
    app._on_tactic("not-a-tactic")     # invalid
    assert app.gs.my_team.tactic == before


async def s_squad_with_all_injured_still_selects_xi(app, pilot):
    """Robustness: if every player is injured, XI returns what's available
    without crashing (even if short)."""
    for p in app.gs.my_team.players:
        p.injury_weeks = 2
    xi = app.gs.my_team.starting_xi()
    # With everyone injured, XI is empty — that's fine, no crash is the contract.
    assert isinstance(xi, list)


async def s_view_match_deterministic_with_seed(app, pilot):
    """Same seed should produce identical first-match results."""
    from bygfoot_tui.engine import quickstart, simulate_match
    a = quickstart(seed=999)
    b = quickstart(seed=999)
    ra = simulate_match(a.my_league.teams[0], a.my_league.teams[1],
                        a.rng, a.commentary_templates)
    rb = simulate_match(b.my_league.teams[0], b.my_league.teams[1],
                        b.rng, b.commentary_templates)
    assert ra.home_goals == rb.home_goals
    assert ra.away_goals == rb.away_goals


async def s_picker_autostart_off_opens(app, pilot):
    """A fresh app with autostart=False pushes the picker and pauses the
    game-state until a selection is made. Start it manually mid-test."""
    fresh = BygfootTUI(autostart=False)
    async with fresh.run_test(size=(180, 50)) as pilot2:
        await pilot2.pause()
        assert fresh.screen.__class__.__name__ == "TeamPickerScreen"
        # Hit escape to quit cleanly — esc is bound to app.quit in picker.


SCENARIOS: list[Scenario] = [
    Scenario("mount_clean", s_mount_clean),
    Scenario("status_bar_populated", s_status_bar_populated),
    Scenario("table_has_20_teams", s_table_has_20_teams),
    Scenario("fixtures_show_38_weeks", s_fixtures_show_user_team),
    Scenario("squad_has_22_rows", s_squad_22_rows),
    Scenario("play_week_advances_week", s_play_week_advances_week),
    Scenario("play_week_logs_user_match", s_play_week_logs_user_match),
    Scenario("play_week_updates_table", s_play_week_updates_table),
    Scenario("tactic_change_reflects_in_status", s_tactic_change),
    Scenario("tab_keys_switch_tabs", s_tab_keys),
    Scenario("finance_shows_lines", s_finance_shows_cash),
    Scenario("full_season_completes", s_full_season),
    Scenario("end_season_promotes_and_rolls", s_end_season_promotes),
    Scenario("match_sim_scores_reasonable", s_match_sim_scores_reasonable),
    Scenario("injuries_progress_and_recover", s_injuries_progress),
    Scenario("commentary_templates_load", s_commentary_loads),
    Scenario("all_positions_present_in_squad", s_all_positions_present),
    Scenario("xi_has_11_with_one_gk", s_xi_has_11),
    Scenario("player_names_nonempty_and_spaced", s_player_name_not_empty),
    Scenario("view_match_streams_to_log", s_view_match_populates_log),
    Scenario("multiple_weeks_no_dup_fixtures", s_multiple_weeks_no_duplicate_fixtures),
    Scenario("play_week_past_season_end_safe", s_play_week_past_season_end_safe),
    Scenario("unknown_tactic_does_not_crash", s_unknown_tactic_does_not_crash),
    Scenario("all_injured_xi_no_crash", s_squad_with_all_injured_still_selects_xi),
    Scenario("seed_determinism", s_view_match_deterministic_with_seed),
    Scenario("picker_screen_opens_when_autostart_off", s_picker_autostart_off_opens),
]


# ---- driver ----

async def run_one(scn: Scenario) -> tuple[str, bool, str]:
    app = BygfootTUI()
    try:
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            try:
                await scn.fn(app, pilot)
            except AssertionError as e:
                app.save_screenshot(str(OUT / f"{scn.name}.FAIL.svg"))
                return (scn.name, False, f"AssertionError: {e}")
            except Exception as e:
                app.save_screenshot(str(OUT / f"{scn.name}.ERROR.svg"))
                return (scn.name, False,
                        f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
            app.save_screenshot(str(OUT / f"{scn.name}.PASS.svg"))
            return (scn.name, True, "")
    except Exception as e:
        return (scn.name, False,
                f"harness error: {type(e).__name__}: {e}\n{traceback.format_exc()}")


async def main(pattern: str | None = None) -> int:
    scenarios = [s for s in SCENARIOS if not pattern or pattern in s.name]
    if not scenarios:
        print(f"no scenarios match {pattern!r}")
        return 2
    results = []
    for scn in scenarios:
        name, ok, msg = await run_one(scn)
        mark = "\033[32m✓\033[0m" if ok else "\033[31m✗\033[0m"
        print(f"  {mark} {name}")
        if not ok:
            for line in msg.splitlines():
                print(f"      {line}")
        results.append((name, ok, msg))
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n{passed}/{len(results)} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    pattern = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(asyncio.run(main(pattern)))
