"""Modal screens for Bygfoot TUI — Help, Transfers, Training, Save, Load."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

from . import engine
from .engine import TACTICS, TRAINING_REGIMES


SAVE_DIR = Path.home() / ".local" / "share" / "bygfoot-tui" / "saves"
SAVE_DIR.mkdir(parents=True, exist_ok=True)


# ---- Help ----

class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        with Container(id="picker_panel"):
            yield Static("Bygfoot TUI — Help", id="picker_title")
            yield Static(
                "[b]Match week[/b]\n"
                "  w        advance one week (simulates all fixtures)\n"
                "  m        play your next match live (streaming ticker)\n"
                "  t        change tactic (4-4-2, 4-3-3, 3-5-2, ...)\n"
                "\n"
                "[b]Tabs[/b]\n"
                "  1 2 3 4 5  League Table / Fixtures / Squad / Match / Finance\n"
                "\n"
                "[b]Squad management[/b]\n"
                "  b        transfer market (buy)\n"
                "  x        sell a player from your squad\n"
                "  r        training regime (light / normal / hard)\n"
                "\n"
                "[b]File[/b]\n"
                "  s        save game\n"
                "  l        load game\n"
                "  ?        this help\n"
                "  q        quit\n"
                "\n"
                "[b]Season[/b]\n"
                "  After week 38, press [b]w[/b] again to roll the\n"
                "  season — promotions/relegations are applied and a\n"
                "  fresh fixture list is generated. Your team pointer\n"
                "  follows you up or down.\n",
            )
            yield Static("  esc back", classes="dim")


# ---- Transfers (buy) ----

class TransferScreen(ModalScreen[tuple[str, int] | None]):
    """Browse players on the transfer market. Dismiss returns
    (player_name, index) if user bought, else None."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("enter", "confirm", "Buy"),
    ]

    def __init__(self, gs: engine.GameState) -> None:
        super().__init__()
        self.gs = gs
        self._listings = engine.transfer_listing(gs, size=30)

    def compose(self) -> ComposeResult:
        with Container(id="picker_panel"):
            yield Static("Transfer Market — pick a player, enter to buy",
                         id="picker_title")
            yield DataTable(id="transfer_table", zebra_stripes=True,
                            cursor_type="row")
            yield Static("", id="transfer_status", classes="dim")
            yield Static("  ↑↓ navigate   enter buy   esc back",
                         classes="dim")

    def on_mount(self) -> None:
        dt = self.query_one("#transfer_table", DataTable)
        dt.add_columns("From", "Pos", "Name", "Age", "Sk", "Tal",
                       "£Fee", "£Wage")
        for seller, p in self._listings:
            fee = int(p.value * 1.2)
            dt.add_row(
                seller.name,
                engine.POS_NAMES[p.position],
                p.name,
                str(p.age),
                str(p.skill),
                str(p.talent),
                f"{fee:,}",
                f"{p.wage}",
            )
        dt.focus()
        self._update_status()

    def _update_status(self) -> None:
        s = self.query_one("#transfer_status", Static)
        s.update(
            f"Your cash: £{self.gs.my_team.cash:,}k  —  "
            f"squad size: {len(self.gs.my_team.players)}/25"
        )

    def action_confirm(self) -> None:
        dt = self.query_one("#transfer_table", DataTable)
        if dt.cursor_row < 0 or dt.cursor_row >= len(self._listings):
            return
        seller, player = self._listings[dt.cursor_row]
        ok, reason = engine.buy_player(self.gs, seller, player)
        s = self.query_one("#transfer_status", Static)
        tag = "[green]" if ok else "[red]"
        s.update(f"{tag}{reason}[/]  •  cash £{self.gs.my_team.cash:,}k")
        if ok:
            # Remove the row so the user sees the list shrink.
            dt.remove_row(dt.coordinate_to_cell_key(
                __import__("textual.coordinate", fromlist=["Coordinate"])
                .Coordinate(dt.cursor_row, 0)
            ).row_key)
            self._listings.pop(dt.cursor_row)


# ---- Sell ----

class SellScreen(ModalScreen[bool]):
    """Pick a user-squad player and try to sell them."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("enter", "confirm", "Sell"),
    ]

    def __init__(self, gs: engine.GameState) -> None:
        super().__init__()
        self.gs = gs
        self._roster = sorted(
            gs.my_team.players, key=lambda p: (p.position, -p.skill)
        )
        self._sold_something = False

    def compose(self) -> ComposeResult:
        with Container(id="picker_panel"):
            yield Static("Sell player — enter to list", id="picker_title")
            yield DataTable(id="sell_table", zebra_stripes=True,
                            cursor_type="row")
            yield Static("", id="sell_status", classes="dim")
            yield Static("  ↑↓ navigate   enter list for sale   esc back",
                         classes="dim")

    def on_mount(self) -> None:
        dt = self.query_one("#sell_table", DataTable)
        dt.add_columns("Pos", "Name", "Age", "Sk", "Tal", "£Value")
        for p in self._roster:
            dt.add_row(engine.POS_NAMES[p.position], p.name, str(p.age),
                       str(p.skill), str(p.talent), f"{p.value:,}")
        dt.focus()

    def action_confirm(self) -> None:
        dt = self.query_one("#sell_table", DataTable)
        if dt.cursor_row < 0 or dt.cursor_row >= len(self._roster):
            return
        player = self._roster[dt.cursor_row]
        ok, reason = engine.sell_player(self.gs, player)
        s = self.query_one("#sell_status", Static)
        tag = "[green]" if ok else "[red]"
        s.update(f"{tag}{reason}[/]  •  cash £{self.gs.my_team.cash:,}k")
        if ok:
            self._sold_something = True


# ---- Training ----

class TrainingScreen(ModalScreen[str | None]):
    """Pick a training regime; also shows last weekly log."""

    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def __init__(self, gs: engine.GameState) -> None:
        super().__init__()
        self.gs = gs

    def compose(self) -> ComposeResult:
        with Container(id="picker_panel"):
            yield Static("Training Regime", id="picker_title")
            yield Static(
                f"Current: [b]{self.gs.training_regime}[/b]\n"
                "\n"
                "  [b]light[/b]  — slower development, fewer injuries\n"
                "  [b]normal[/b] — balanced (default)\n"
                "  [b]hard[/b]   — faster development, higher injury risk\n",
            )
            yield ListView(
                *[ListItem(Label(k)) for k in TRAINING_REGIMES.keys()],
                id="training_list",
            )
            yield Static("Recent training log:", classes="bright")
            log = RichLog(id="training_log", max_lines=50, markup=True,
                          wrap=True, highlight=False)
            yield log
            yield Static("  ↑↓ + enter to set   esc back", classes="dim")

    def on_mount(self) -> None:
        log = self.query_one("#training_log", RichLog)
        if not self.gs.training_log:
            log.write("[dim](no training events yet)[/dim]")
        else:
            for line in self.gs.training_log[-10:]:
                log.write(line)

    def on_list_view_selected(self, event) -> None:  # type: ignore[override]
        label = event.item.query_one(Label).renderable
        new = str(label)
        if new in TRAINING_REGIMES:
            self.gs.training_regime = new
            self.dismiss(new)


# ---- Save ----

class SaveScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def __init__(self, gs: engine.GameState) -> None:
        super().__init__()
        self.gs = gs

    def compose(self) -> ComposeResult:
        with Container(id="picker_panel"):
            yield Static("Save game", id="picker_title")
            yield Static("Enter save slot name (letters/digits only):")
            yield Input(value=f"{self.gs.country_sid}-"
                        f"{self.gs.my_team.name.lower().replace(' ', '')}"
                        f"-s{self.gs.season}w{self.gs.week}",
                        id="save_name")
            yield Static("", id="save_status", classes="dim")
            yield Static("  enter save   esc back", classes="dim")

    def on_input_submitted(self, event) -> None:  # type: ignore[override]
        name = event.value.strip()
        if not name:
            return
        # Sanitize.
        safe = "".join(c for c in name if c.isalnum() or c in "-_")
        path = SAVE_DIR / f"{safe}.json"
        try:
            save_game(self.gs, path)
            size = path.stat().st_size
            self.query_one("#save_status", Static).update(
                f"[green]saved {path.name}[/green] ({size} bytes)"
            )
            self.dismiss(str(path))
        except Exception as e:  # pragma: no cover
            self.query_one("#save_status", Static).update(
                f"[red]save failed: {e}[/red]"
            )


class LoadScreen(ModalScreen[Path | None]):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("enter", "confirm", "Load"),
    ]

    def compose(self) -> ComposeResult:
        self._paths = sorted(SAVE_DIR.glob("*.json"))
        with Container(id="picker_panel"):
            yield Static("Load game", id="picker_title")
            if not self._paths:
                yield Static("  [dim]no saves in "
                             f"{SAVE_DIR}[/dim]")
            else:
                yield ListView(
                    *[ListItem(Label(f"{p.name}  "
                                     f"({p.stat().st_size} B)"))
                      for p in self._paths],
                    id="load_list",
                )
            yield Static("  ↑↓ + enter   esc back", classes="dim")

    def action_confirm(self) -> None:
        if not self._paths:
            return
        lv = self.query_one("#load_list", ListView)
        idx = lv.index
        if idx is None:
            return
        self.dismiss(self._paths[idx])


# ---- serialisation ----

def _team_to_dict(t: engine.Team) -> dict[str, Any]:
    return {
        "name": t.name,
        "league_sid": t.league_sid,
        "avg_talent": t.avg_talent,
        "is_user": t.is_user,
        "tactic": t.tactic,
        "cash": t.cash,
        "played": t.played, "won": t.won, "drew": t.drew, "lost": t.lost,
        "gf": t.gf, "ga": t.ga,
        "players": [asdict(p) for p in t.players],
    }


def _team_from_dict(d: dict[str, Any]) -> engine.Team:
    t = engine.Team(
        name=d["name"], league_sid=d["league_sid"],
        avg_talent=d["avg_talent"],
    )
    t.is_user = d["is_user"]
    t.tactic = d["tactic"]
    t.cash = d["cash"]
    t.played = d["played"]; t.won = d["won"]; t.drew = d["drew"]
    t.lost = d["lost"]; t.gf = d["gf"]; t.ga = d["ga"]
    t.players = [engine.Player(**pd) for pd in d["players"]]
    return t


def save_game(gs: engine.GameState, path: Path) -> None:
    """Serialise GameState to JSON. Drops RNG state (re-seeded on load)
    and fixtures (regenerated from team count)."""
    payload: dict[str, Any] = {
        "version": 1,
        "country_sid": gs.country_sid,
        "my_league_sid": gs.my_league_sid,
        "my_team_idx": gs.my_team_idx,
        "week": gs.week,
        "season": gs.season,
        "training_regime": gs.training_regime,
        "training_log": list(gs.training_log),
        "leagues": {
            sid: {
                "sid": lg.sid,
                "name": lg.name,
                "teams": [_team_to_dict(t) for t in lg.teams],
            }
            for sid, lg in gs.leagues.items()
        },
    }
    path.write_text(json.dumps(payload))


def load_game(path: Path) -> engine.GameState:
    import random
    payload = json.loads(path.read_text())
    leagues: dict[str, engine.LeagueState] = {}
    for sid, ld in payload["leagues"].items():
        teams = [_team_from_dict(td) for td in ld["teams"]]
        # Fixtures are rebuilt — match weeks already consumed are not
        # re-played; we only regenerate the round-robin for the
        # remainder of the season. Simplest approach: rebuild a full
        # schedule and let `week` index into it. Saved games mid-season
        # won't replay the exact same opponents, but the table state
        # is preserved.
        fixtures = engine._round_robin(len(teams))
        leagues[sid] = engine.LeagueState(
            sid=ld["sid"], name=ld["name"], teams=teams, fixtures=fixtures
        )
    from .commentary import load_commentary_templates
    gs = engine.GameState(
        rng=random.Random(42),
        country_sid=payload["country_sid"],
        leagues=leagues,
        my_league_sid=payload["my_league_sid"],
        my_team_idx=payload["my_team_idx"],
        week=payload["week"],
        season=payload["season"],
        commentary_templates=load_commentary_templates(),
        training_regime=payload.get("training_regime", "normal"),
        training_log=list(payload.get("training_log", [])),
    )
    return gs
