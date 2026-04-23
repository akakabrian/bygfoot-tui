"""Load Bygfoot XML data (leagues, teams, player names, commentary).

This module is the bridge between the vendored Bygfoot repo's data files
and our Python reimplementation of the sim. We only read XML — no C code
is executed. See DECISIONS.md for why.

The loader is tolerant of missing files (not every country has a name
pool; fall back to `general`). It also caches parsed files so repeated
calls during season init don't reparse.
"""

from __future__ import annotations

import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENGINE = REPO / "engine"
DEFS = ENGINE / "support_files" / "definitions"
NAMES = ENGINE / "support_files" / "names"
COMMENTARY = ENGINE / "support_files" / "lg_commentary"


@dataclass
class LeagueDef:
    sid: str
    name: str
    short_name: str
    average_talent: int
    names_file: str
    team_names: list[str] = field(default_factory=list)
    first_week: int = 1
    week_gap: int = 1
    # sid of the league below (for relegation) and above (for promotion).
    # Populated by discover() after all leagues in a country are loaded.
    rel_target: str | None = None
    prom_target: str | None = None
    rel_rank_start: int = 0  # 1-based; relegation if rank >= this
    prom_rank_end: int = 0   # 1-based; promotion if rank <= this


@dataclass
class CountryDef:
    sid: str
    name: str
    rating: int
    leagues: list[LeagueDef] = field(default_factory=list)


def _text(elem: ET.Element | None, default: str = "") -> str:
    if elem is None or elem.text is None:
        return default
    return elem.text.strip()


def _int(elem: ET.Element | None, default: int = 0) -> int:
    t = _text(elem, "")
    try:
        return int(t)
    except ValueError:
        return default


def _load_league(path: Path) -> LeagueDef | None:
    """Parse a `league_<sid>.xml` into a LeagueDef. Returns None on
    unparsable files (a few historical/cup-only league stubs throw
    encoding errors; we skip them silently)."""
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return None
    root = tree.getroot()
    if root.tag != "league":
        return None
    lg = LeagueDef(
        sid=_text(root.find("sid")),
        name=_text(root.find("name"), path.stem),
        short_name=_text(root.find("short_name"), path.stem[:8]),
        average_talent=_int(root.find("average_talent"), 5000),
        names_file=_text(root.find("names_file"), "general"),
        first_week=_int(root.find("first_week"), 1),
        week_gap=_int(root.find("week_gap"), 1),
    )
    teams = root.find("teams")
    if teams is not None:
        for te in teams.findall("team"):
            nm = _text(te.find("team_name"))
            if nm:
                lg.team_names.append(nm)
    # Promotion/relegation: we only care about the first element per
    # direction. Files sometimes omit promotion entries (top-flight
    # leagues only relegate).
    pr = root.find("prom_rel")
    if pr is not None:
        for elem in pr.findall("prom_rel_element"):
            kind = _text(elem.find("prom_rel_type"), "")
            dest = _text(elem.find("dest_sid"), "")
            r0 = _int(elem.find("rank_start"), 0)
            r1 = _int(elem.find("rank_end"), 0)
            if kind == "relegation" and not lg.rel_target:
                lg.rel_target = dest
                lg.rel_rank_start = min(r0, r1) or r0
            elif kind == "promotion" and not lg.prom_target:
                lg.prom_target = dest
                lg.prom_rank_end = max(r0, r1) or r1
    return lg if lg.team_names else None


def _load_country(country_xml: Path) -> CountryDef | None:
    try:
        tree = ET.parse(country_xml)
    except ET.ParseError:
        return None
    root = tree.getroot()
    if root.tag != "country":
        return None
    sid = _text(root.find("sid"))
    name = _text(root.find("name"), sid)
    rating = _int(root.find("rating"), 5)
    country = CountryDef(sid=sid, name=name, rating=rating)
    country_dir = country_xml.parent
    league_sids: list[str] = []
    lg_root = root.find("leagues")
    if lg_root is not None:
        for le in lg_root.findall("league"):
            if le.text:
                league_sids.append(le.text.strip())
    for sid in league_sids:
        lp = country_dir / f"league_{sid}.xml"
        if not lp.exists():
            continue
        lg = _load_league(lp)
        if lg:
            country.leagues.append(lg)
    return country if country.leagues else None


def _discover_countries() -> dict[str, CountryDef]:
    """Find every country_*.xml anywhere under definitions/ and build a
    map sid → CountryDef. We take the FIRST match per sid, preferring
    the flat `definitions/<country>/` layout over nested `europe/<country>/`
    (latter is a historical duplicate)."""
    found: dict[str, CountryDef] = {}
    # Flat layout wins — iterate it first.
    for p in sorted(DEFS.glob("*/country_*.xml")):
        c = _load_country(p)
        if c and c.sid not in found:
            found[c.sid] = c
    # Deeper nested country dirs (europe/<country>/country_*.xml etc.)
    for p in sorted(DEFS.glob("*/*/country_*.xml")):
        c = _load_country(p)
        if c and c.sid not in found:
            found[c.sid] = c
    return found


# Cached module-level singleton: loading ~40 countries is ~150 ms, so
# parse once on first access.
_COUNTRIES: dict[str, CountryDef] | None = None


def countries() -> dict[str, CountryDef]:
    global _COUNTRIES
    if _COUNTRIES is None:
        _COUNTRIES = _discover_countries()
    return _COUNTRIES


def country(sid: str) -> CountryDef | None:
    return countries().get(sid)


def country_list() -> list[CountryDef]:
    """Sorted by rating desc (strongest football nations first), then
    alphabetical by name."""
    cs = list(countries().values())
    cs.sort(key=lambda c: (-c.rating, c.name))
    return cs


# ---------- player name pools ----------

@lru_cache(maxsize=None)
def _name_pool(names_file: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (first_names, last_names) for a given names_file sid.

    Bygfoot's name XMLs only ship last names for most countries; first
    names are drawn from `player_names_general.xml` which does have a
    `<first_name>` block. We merge.
    """
    firsts: list[str] = []
    lasts: list[str] = []

    def ingest(path: Path) -> None:
        if not path.exists():
            return
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            return
        for fn in root.findall("first_name"):
            if fn.text:
                firsts.append(fn.text.strip())
        for ln in root.findall("last_name"):
            if ln.text:
                lasts.append(ln.text.strip())

    ingest(NAMES / f"player_names_{names_file}.xml")
    # Always merge general for fallback coverage.
    if names_file != "general":
        ingest(NAMES / "player_names_general.xml")
    # Hard fallback firsts so pre-bootstrap tests don't blow up if the
    # XML isn't present yet.
    if not firsts:
        firsts = [
            "Alex", "Chris", "David", "Marco", "Ivan", "Luka", "Yuki",
            "Aaron", "Noah", "Elias", "Leo", "Kai", "Omar", "Diego",
            "James", "Ben", "Tom", "Nathan", "Oscar", "Jack", "Ryan",
        ]
    if not lasts:
        lasts = [
            "Smith", "Jones", "Williams", "Brown", "Martin", "Rossi",
            "Mueller", "Silva", "Tanaka", "Kim", "Garcia", "Novak",
        ]
    return tuple(firsts), tuple(lasts)


def random_player_name(names_file: str, rng: random.Random) -> str:
    firsts, lasts = _name_pool(names_file)
    return f"{rng.choice(firsts)} {rng.choice(lasts)}"
