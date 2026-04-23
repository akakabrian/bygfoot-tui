"""Bygfoot TUI — tabbed football-manager UI over the Python sim.

Layout:
    ┌ status bar ────────────────────────────────────────────────────┐
    │ Team • Season N • Week W / 38 • Cash £Xk • Next: vs OPP (H/A)  │
    ├ tabs ──────────────────────────────────────────────────────────┤
    │ Table / Fixtures / Squad / Match / Finance                     │
    │                                                                │
    │  (DataTable or RichLog + controls per tab)                     │
    │                                                                │
    └────────────────────────────────────────────────────────────────┘

The Match tab is the only non-static view: during a simulated match the
commentary streams into a RichLog at a tunable speed, with pause/play.
Every other tab is a snapshot of the current GameState — refreshed
whenever the user switches tabs or advances a week.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from . import data
from .engine import (
    GameState,
    MatchEvent,
    MatchResult,
    POS_NAMES,
    TACTICS,
    Team,
    simulate_match,
)
from .screens import (
    HelpScreen,
    LoadScreen,
    SaveScreen,
    SellScreen,
    TrainingScreen,
    TransferScreen,
    load_game,
)


# ----- team picker -----

class TeamPickerScreen(ModalScreen[tuple[str, str, int]]):
    """Pick country → league → team. Returns (country_sid, league_sid,
    team_idx) on accept. Uses three ListViews side-by-side — the user
    cycles columns with tab."""

    BINDINGS = [
        Binding("escape", "app.quit", "Quit", show=True),
        Binding("enter", "confirm", "Start", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._countries = data.country_list()
        self._country_idx = 0
        self._league_idx = 0
        self._team_idx = 0

    def compose(self) -> ComposeResult:
        with Container(id="picker_panel"):
            yield Static("⚽  BYGFOOT TUI  —  pick a team", id="picker_title")
            with Horizontal():
                with Vertical():
                    yield Label("Country", classes="bright")
                    yield ListView(
                        *[ListItem(Label(f"{c.name} ({c.rating})"))
                          for c in self._countries],
                        id="country_list",
                    )
                with Vertical():
                    yield Label("League", classes="bright")
                    yield ListView(id="league_list")
                with Vertical():
                    yield Label("Team", classes="bright")
                    yield ListView(id="team_list")
            yield Static(
                "  ↑↓ navigate   tab next column   enter start   esc quit",
                classes="dim",
            )

    def on_mount(self) -> None:
        self._refresh_leagues()
        self._refresh_teams()
        self.query_one("#country_list", ListView).focus()

    def _refresh_leagues(self) -> None:
        lv = self.query_one("#league_list", ListView)
        lv.clear()
        c = self._countries[self._country_idx]
        for lg in c.leagues:
            lv.append(ListItem(Label(f"{lg.name} — {len(lg.team_names)}t")))
        self._league_idx = 0

    def _refresh_teams(self) -> None:
        lv = self.query_one("#team_list", ListView)
        lv.clear()
        c = self._countries[self._country_idx]
        if not c.leagues:
            return
        lg = c.leagues[self._league_idx]
        for tn in lg.team_names:
            lv.append(ListItem(Label(tn)))
        self._team_idx = 0

    def on_list_view_highlighted(self, event) -> None:  # type: ignore[override]
        lv_id = event.list_view.id
        if lv_id == "country_list" and event.list_view.index is not None:
            self._country_idx = event.list_view.index
            self._refresh_leagues()
            self._refresh_teams()
        elif lv_id == "league_list" and event.list_view.index is not None:
            self._league_idx = event.list_view.index
            self._refresh_teams()
        elif lv_id == "team_list" and event.list_view.index is not None:
            self._team_idx = event.list_view.index

    def action_confirm(self) -> None:
        c = self._countries[self._country_idx]
        if not c.leagues:
            return
        lg = c.leagues[self._league_idx]
        if not lg.team_names:
            return
        self.dismiss((c.sid, lg.sid, self._team_idx))


# ----- tactic picker modal -----

class TacticScreen(ModalScreen[str]):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        with Container(id="picker_panel"):
            yield Static("Choose tactic", id="picker_title")
            yield ListView(
                *[ListItem(Label(k)) for k in TACTICS.keys()],
                id="tactic_list",
            )
            yield Static("  ↑↓ + enter   esc cancel", classes="dim")

    def on_list_view_selected(self, event) -> None:  # type: ignore[override]
        label = event.item.query_one(Label).renderable
        self.dismiss(str(label))


# ----- main app -----

class BygfootTUI(App):
    CSS_PATH = "tui.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("w", "play_week", "Play Week"),
        Binding("m", "view_match", "Match"),
        Binding("t", "choose_tactic", "Tactic"),
        Binding("b", "transfer_market", "Buy"),
        Binding("x", "sell_player", "Sell"),
        Binding("r", "training", "Training"),
        Binding("s", "save_game", "Save"),
        Binding("l", "load_game", "Load"),
        Binding("question_mark", "help", "Help"),
        Binding("1", "tab('tab_table')",    "Table",    show=False),
        Binding("2", "tab('tab_fixtures')", "Fixtures", show=False),
        Binding("3", "tab('tab_squad')",    "Squad",    show=False),
        Binding("4", "tab('tab_match')",    "Match",    show=False),
        Binding("5", "tab('tab_finance')",  "Finance",  show=False),
    ]

    TITLE = "Bygfoot TUI"

    # Seed is injectable for reproducible tests.
    def __init__(self, seed: int = 42, country: str = "england",
                 league: str = "england1", team_idx: int = 0,
                 autostart: bool = True) -> None:
        super().__init__()
        self._seed = seed
        self._initial = (country, league, team_idx)
        self._autostart = autostart
        self.gs: GameState | None = None
        self._playing_match = False

    # ---- layout ----

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("Loading…", id="status_bar")
        with TabbedContent(initial="tab_table"):
            with TabPane("1 Table", id="tab_table"):
                yield DataTable(id="league_table", zebra_stripes=True,
                                cursor_type="row")
            with TabPane("2 Fixtures", id="tab_fixtures"):
                yield DataTable(id="fixtures_table", zebra_stripes=True,
                                cursor_type="row")
            with TabPane("3 Squad", id="tab_squad"):
                yield DataTable(id="squad_table", zebra_stripes=True,
                                cursor_type="row")
            with TabPane("4 Match", id="tab_match"):
                yield Static("", id="match_scoreboard")
                yield RichLog(id="match_log", max_lines=500, markup=True,
                              wrap=True, highlight=False)
                yield Static(
                    "  m = play match   space = pause/resume   ",
                    id="match_controls",
                )
            with TabPane("5 Finance", id="tab_finance"):
                yield DataTable(id="finance_table", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        if self._autostart:
            c, lg, idx = self._initial
            self.start_game(c, lg, idx)
        else:
            # Launch the picker; caller drives it.
            self.push_screen(TeamPickerScreen(), self._on_picker_done)

    def _on_picker_done(self, res: tuple[str, str, int] | None) -> None:
        if res is None:
            self.exit()
            return
        self.start_game(*res)

    def start_game(self, country_sid: str, league_sid: str,
                   team_idx: int) -> None:
        self.gs = GameState.new(country_sid, league_sid, team_idx,
                                seed=self._seed)
        self.refresh_all()

    # ---- refreshers ----

    def refresh_all(self) -> None:
        if self.gs is None:
            return
        self._refresh_status_bar()
        self._refresh_league_table()
        self._refresh_fixtures()
        self._refresh_squad()
        self._refresh_finance()

    def _refresh_status_bar(self) -> None:
        assert self.gs is not None
        gs = self.gs
        bar = self.query_one("#status_bar", Static)
        t = gs.my_team
        nxt = gs.my_next_fixture()
        if nxt is None:
            nxt_str = "season over"
        else:
            home, away, is_home = nxt
            opp = away.name if is_home else home.name
            venue = "H" if is_home else "A"
            nxt_str = f"vs {opp} ({venue})"
        status_text = (
            f"[b]{t.name}[/b]  •  {gs.my_league.name}  •  "
            f"Season {gs.season}  •  Week {gs.week}/{gs.max_week}  •  "
            f"Cash £{t.cash:,}k  •  Next: {nxt_str}  •  Tactic {t.tactic}"
        )
        bar.update(status_text)
        # Stash raw text for tests; Static doesn't expose renderable
        # consistently across Textual versions.
        self._last_status_text = status_text

    def _refresh_league_table(self) -> None:
        assert self.gs is not None
        dt = self.query_one("#league_table", DataTable)
        dt.clear(columns=True)
        dt.add_columns("#", "Team", "P", "W", "D", "L", "GF", "GA",
                       "GD", "Pts", "Form")
        # Zone markers: top ~3 are "qualification", bottom ~3 are "relegation"
        table = self.gs.my_league.table()
        n = len(table)
        top_zone = max(1, n // 7)                # 2-3 top spots
        rel_zone_start = n - max(1, n // 7)      # last 2-3 spots
        for i, t in enumerate(table):
            rank = i + 1
            if i < top_zone:
                row_style = "[green]"
            elif i >= rel_zone_start:
                row_style = "[red]"
            else:
                row_style = ""
            marker = "[b yellow]►[/b yellow]" if t.is_user else " "
            form_str = "".join(
                f"[green]{c}[/green]" if c == "W"
                else f"[red]{c}[/red]" if c == "L"
                else f"[yellow]{c}[/yellow]"
                for c in t.form
            ) or "-"
            close = "[/]" if row_style else ""
            dt.add_row(
                f"{row_style}{rank}{close}",
                f"{row_style}{marker} {t.name}{close}",
                f"{row_style}{t.played}{close}",
                f"{row_style}{t.won}{close}",
                f"{row_style}{t.drew}{close}",
                f"{row_style}{t.lost}{close}",
                f"{row_style}{t.gf}{close}",
                f"{row_style}{t.ga}{close}",
                f"{row_style}{t.gd}{close}",
                f"{row_style}{t.points}{close}",
                form_str,
                key=t.name,
            )

    def _refresh_fixtures(self) -> None:
        assert self.gs is not None
        gs = self.gs
        dt = self.query_one("#fixtures_table", DataTable)
        dt.clear(columns=True)
        dt.add_columns("Wk", "Home", "", "Away", "Result")
        lg = gs.my_league
        for wk, fx in enumerate(lg.fixtures, start=1):
            # Results stored sparsely — slot matches to their week.
            res_by_pair: dict[tuple[int, int], MatchResult] = {}
            if wk - 1 < len(lg.results):
                for r in lg.results[wk - 1]:
                    hi = lg.teams.index(r.home)
                    ai = lg.teams.index(r.away)
                    res_by_pair[(hi, ai)] = r
            for h, a in fx:
                if gs.my_team_idx not in (h, a):
                    continue
                home = lg.teams[h].name
                away = lg.teams[a].name
                r = res_by_pair.get((h, a))
                if r:
                    score = f"{r.home_goals}-{r.away_goals}"
                    if r.outcome == "H":
                        score = f"[green]{score}[/green]" \
                                if h == gs.my_team_idx else f"[red]{score}[/red]"
                    elif r.outcome == "A":
                        score = f"[green]{score}[/green]" \
                                if a == gs.my_team_idx else f"[red]{score}[/red]"
                    else:
                        score = f"[yellow]{score}[/yellow]"
                else:
                    score = "-"
                marker = "[b]◀[/b]" if wk == gs.week else " "
                dt.add_row(f"{marker} {wk}", home, "vs", away, score)

    def _refresh_squad(self) -> None:
        assert self.gs is not None
        dt = self.query_one("#squad_table", DataTable)
        dt.clear(columns=True)
        dt.add_columns("Pos", "Name", "Age", "Sk", "Tal", "Fit",
                       "G", "A", "Y", "R", "£Value", "£Wage", "Status")
        # Sort by position then skill desc.
        roster = sorted(self.gs.my_team.players,
                        key=lambda p: (p.position, -p.skill))
        for p in roster:
            status = "OK" if p.available else f"INJ {p.injury_weeks}w"
            dt.add_row(
                POS_NAMES[p.position], p.name, str(p.age), str(p.skill),
                str(p.talent), str(p.fitness), str(p.goals), str(p.assists),
                str(p.yellow), str(p.red), f"{p.value}", f"{p.wage}",
                status,
            )

    def _refresh_finance(self) -> None:
        assert self.gs is not None
        t = self.gs.my_team
        dt = self.query_one("#finance_table", DataTable)
        dt.clear(columns=True)
        dt.add_columns("Line", "Amount (£k)")
        wage_bill = sum(p.wage for p in t.players)
        squad_value = sum(p.value for p in t.players)
        ticket = max(200, int(t.avg_talent * 0.15))
        dt.add_row("Cash on hand",       f"{t.cash:,}")
        dt.add_row("Weekly wage bill",   f"{wage_bill:,}")
        dt.add_row("Weekly ticket est.", f"{ticket:,}")
        dt.add_row("Weekly net est.",    f"{ticket - wage_bill:,}")
        dt.add_row("Squad value",        f"{squad_value:,}")

    # ---- actions ----

    def action_tab(self, tab_id: str) -> None:
        try:
            self.query_one(TabbedContent).active = tab_id
        except Exception:
            pass

    def action_play_week(self) -> None:
        """Advance one match-week and refresh all panels."""
        if self.gs is None or self._playing_match:
            return
        gs = self.gs
        if gs.season_over():
            lines = gs.end_season()
            log = self.query_one("#match_log", RichLog)
            log.write("[b]— End of season —[/b]")
            for line in lines:
                log.write(f"  {line}")
            self.refresh_all()
            return
        # Record results keyed to the week (before advancing).
        wk = gs.week - 1
        results = gs.play_current_week()
        # Slot into LeagueState.results parallel to fixtures.
        for lg in gs.leagues.values():
            while len(lg.results) <= wk:
                lg.results.append([])
        for r in results:
            lg = next(l for l in gs.leagues.values()
                      if r.home in l.teams and r.away in l.teams)
            lg.results[wk].append(r)
        # Narrate user's match into the match log (auto-switch to the
        # Match tab so the RichLog actually retains lines — Textual
        # doesn't back-fill writes to an unmounted tab).
        user_res = next((r for r in results
                         if r.home.is_user or r.away.is_user), None)
        if user_res is not None:
            try:
                self.query_one(TabbedContent).active = "tab_match"
            except Exception:
                pass
            self._narrate_user_match_to_log(user_res)
        self.refresh_all()

    def _narrate_user_match_to_log(self, r: MatchResult) -> None:
        log = self.query_one("#match_log", RichLog)
        header = (f"\n[b]=== Week {self.gs.week - 1 if self.gs else '?'}: "
                  f"{r.home.name} {r.home_goals}-{r.away_goals} "
                  f"{r.away.name} ===[/b]")
        log.write(header)
        for ev in r.events:
            prefix = f"[dim]{ev.minute:>2}'[/dim] "
            if ev.kind == "goal":
                log.write(f"{prefix}[b green]{ev.text}[/b green]")
            elif ev.kind in ("half_time", "full_time"):
                log.write(f"{prefix}[b yellow]{ev.text}[/b yellow]")
            elif ev.kind == "kickoff":
                log.write(f"{prefix}[b]{ev.text}[/b]")
            elif ev.kind == "card_yellow":
                log.write(f"{prefix}[yellow]{ev.text}[/yellow]")
            elif ev.kind == "injury":
                log.write(f"{prefix}[red]{ev.text}[/red]")
            elif ev.kind == "save":
                log.write(f"{prefix}[cyan]{ev.text}[/cyan]")
            else:
                log.write(f"{prefix}{ev.text}")
        # Scoreboard static
        sb = self.query_one("#match_scoreboard", Static)
        sb.update(f"{r.home.name}  {r.home_goals} — {r.away_goals}  "
                  f"{r.away.name}")

    async def action_view_match(self) -> None:
        """Play user's next match live in the Match tab with a ticker."""
        if self.gs is None or self._playing_match:
            return
        gs = self.gs
        if gs.season_over():
            return
        nxt = gs.my_next_fixture()
        if nxt is None:
            return
        home, away, _ = nxt
        # Switch to match tab.
        self.query_one(TabbedContent).active = "tab_match"
        log = self.query_one("#match_log", RichLog)
        log.clear()
        sb = self.query_one("#match_scoreboard", Static)
        sb.update(f"{home.name}  0 — 0  {away.name}")
        # Pre-simulate the whole match deterministically, then stream events.
        result = simulate_match(home, away, gs.rng, gs.commentary_templates)
        self._playing_match = True
        try:
            for ev in result.events:
                if ev.kind == "goal":
                    if ev.team_idx == 0:
                        pass  # scoreboard already tracks via result
                    sb.update(
                        f"{home.name}  "
                        f"{sum(1 for e in result.events[:result.events.index(ev)+1] if e.kind == 'goal' and e.team_idx == 0)}"
                        f" — "
                        f"{sum(1 for e in result.events[:result.events.index(ev)+1] if e.kind == 'goal' and e.team_idx == 1)}"
                        f"  {away.name}"
                    )
                prefix = f"[dim]{ev.minute:>2}'[/dim] "
                if ev.kind == "goal":
                    log.write(f"{prefix}[b green]{ev.text}[/b green]")
                elif ev.kind in ("half_time", "full_time"):
                    log.write(f"{prefix}[b yellow]{ev.text}[/b yellow]")
                elif ev.kind == "kickoff":
                    log.write(f"{prefix}[b]{ev.text}[/b]")
                elif ev.kind == "card_yellow":
                    log.write(f"{prefix}[yellow]{ev.text}[/yellow]")
                elif ev.kind == "injury":
                    log.write(f"{prefix}[red]{ev.text}[/red]")
                elif ev.kind == "save":
                    log.write(f"{prefix}[cyan]{ev.text}[/cyan]")
                else:
                    log.write(f"{prefix}{ev.text}")
                await asyncio.sleep(0.05)  # snappy ticker
            # Bookkeeping: mirror play_current_week's state mutation for this
            # result and advance week (simulate_match already updated teams).
            wk = gs.week - 1
            lg = gs.my_league
            while len(lg.results) <= wk:
                lg.results.append([])
            lg.results[wk].append(result)
            # The other fixtures this week still need simulating to keep the
            # league honest — run the rest of the week silently.
            for other_lg, h, a in gs.current_week_fixtures():
                if (other_lg is lg and
                        other_lg.teams[h] is home and
                        other_lg.teams[a] is away):
                    continue
                other_home = other_lg.teams[h]
                other_away = other_lg.teams[a]
                other_res = simulate_match(other_home, other_away, gs.rng,
                                           gs.commentary_templates)
                while len(other_lg.results) <= wk:
                    other_lg.results.append([])
                other_lg.results[wk].append(other_res)
            gs.week += 1
            # Weekly upkeep (copy of play_current_week's tail)
            for l in gs.leagues.values():
                for t in l.teams:
                    for p in t.players:
                        p.fitness = min(100, p.fitness + gs.rng.randint(6, 14))
                        if p.injury_weeks > 0:
                            p.injury_weeks -= 1
                    if t.is_user:
                        wage_bill = sum(p.wage for p in t.players)
                        ticket = max(200, int(t.avg_talent * 0.15))
                        t.cash += ticket - wage_bill
            self.refresh_all()
        finally:
            self._playing_match = False

    def action_choose_tactic(self) -> None:
        if self.gs is None:
            return
        self.push_screen(TacticScreen(), self._on_tactic)

    def _on_tactic(self, tactic: str | None) -> None:
        if tactic and self.gs is not None and tactic in TACTICS:
            self.gs.my_team.tactic = tactic
            self.refresh_all()

    # ---- screens ----

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_transfer_market(self) -> None:
        if self.gs is None:
            return
        self.push_screen(TransferScreen(self.gs),
                         lambda _res: self.refresh_all())

    def action_sell_player(self) -> None:
        if self.gs is None:
            return
        self.push_screen(SellScreen(self.gs),
                         lambda _res: self.refresh_all())

    def action_training(self) -> None:
        if self.gs is None:
            return
        self.push_screen(TrainingScreen(self.gs),
                         lambda _res: self.refresh_all())

    def action_save_game(self) -> None:
        if self.gs is None:
            return
        self.push_screen(SaveScreen(self.gs))

    def action_load_game(self) -> None:
        self.push_screen(LoadScreen(), self._on_load)

    def _on_load(self, path) -> None:
        if path is None:
            return
        try:
            self.gs = load_game(path)
        except Exception as e:  # noqa: BLE001 — user-visible
            self.notify(f"load failed: {e}", severity="error")
            return
        self.refresh_all()


def run() -> None:
    app = BygfootTUI()
    app.run()
