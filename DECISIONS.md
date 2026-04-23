# bygfoot-tui — design decisions

## 2026-04-22 — Binding strategy

**Decision: Option (b) — reimplement the sim in Python on top of Bygfoot's
XML data files and commentary corpus.**

### Context

The upstream engine (https://github.com/kashifsoofi/bygfoot, mirror of the
original SourceForge repo) is ~190 C source files, heavy GTK2 / GLib
coupling:

- UI callbacks wired into `.glade` files (interface.c, callbacks.c,
  game_gui.c, window.c, treeview.c, misc_callbacks.c)
- Core types use GLib primitives (`gint`, `gfloat`, `gchar*`, `GArray`,
  `GPtrArray`) throughout
- GTK signals are load-bearing for many sim steps (end-of-match UI
  waits, confirm dialogs embedded in the engine loop)

### Options considered

**(a) Build Bygfoot as a library, strip GTK.** Would require excising
GTK from ~60 files and replacing `GArray`/`GPtrArray` with a compat
shim or keeping GLib. Brittle, weeks of work, and the resulting `.so`
would still depend on GLib.

**(b) Reimplement in Python using the data files.** The XML corpus is
cleanly separable:
- `definitions/<country>/country_<c>.xml` — league/cup membership
- `definitions/<country>/league_<c>N.xml` — teams per league +
  promotion/relegation rules
- `definitions/<country>/cup_*.xml` — cup formats
- `names/player_names_<country>.xml` — 3000+ real surname pools
- `lg_commentary/lg_commentary_en.xml` — text play-by-play templates
  with variable substitution (`_P0_`, `_T_POSS__`) and conditions
  (`_MI_>80 and _GD_==0`) — this IS the match engine's narrative layer
- `hints/` — rotating tips
- `strategy/` — preset tactics

The match engine itself (game.c, live_game.c) is ~3000 lines of C with
well-defined inputs (team skill, tactics, home advantage) and outputs
(shots, goals, commentary events). Straightforward to reimplement in
Python — and easier to extend/debug than binding to C.

**(c) Subprocess + parse game state files.** Bygfoot needs a running
GTK main loop; can't run headless reliably. Rejected.

### Why (b) is right for a TUI

Bygfoot is **table-and-menu driven**: league standings, fixtures lists,
squad tables, finance tables, transfer lists. The original UI is 80%
GtkTreeView. Textual's `DataTable` is a near-one-to-one mapping — the
TUI port feels **more native** than the GTK original because a terminal
is built for columnar data.

We keep fidelity by:
- Using the real XML team rosters (20 Premier League + 20 Bundesliga +
  20 Serie A + 20 La Liga + 20 Ligue 1 + 20 Eredivisie at launch)
- Real surname pools for player generation (per-country)
- Real commentary templates (bracket-alternation + variable sub)
- Reproducing Bygfoot's match simulation formulas (skill vs skill
  shots, finishing %, GK saves, home advantage)

### Scope

Launch feature set targets the original Bygfoot's menu-driven flow:
1. Team selection (pick a country → league → team)
2. League table (rank, P/W/D/L, GF/GA/GD, Pts)
3. Fixtures list (this week + all season)
4. Match week with text play-by-play ticker (3-speed + pause)
5. Squad management (XI selection, tactics 4-4-2 / 4-3-3 / 3-5-2 etc.,
   substitutions, training)
6. Transfer market (buy/sell with age/skill/fee logic)
7. Finances (weekly wages, ticket revenue, balance)
8. Season progression (promotion/relegation, new season)
