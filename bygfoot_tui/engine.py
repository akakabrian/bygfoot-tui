"""Python reimplementation of Bygfoot's match/season sim.

Replicates Bygfoot's design (not its code, which is heavy GTK-coupled C):

- Teams have 11 starters + up to 5 subs; skills 1-9999 (we use 0-99).
- Player position (GK/DEF/MID/FWD), skill (current), talent (peak),
  age, fitness, value, wage.
- League is round-robin home-and-away.
- Match sim: 90 minutes split into 90 minute-ticks; each tick has a
  probabilistic chance of producing an event (pass / shot / goal /
  foul / save) biased by team strength, tactic, home advantage.
- Season: weekly fixtures → match week → table update → transfer
  window → next week.

This lives independent of the TUI and can be driven from a REPL:

    sim = GameState.new("england", "england1", team_index=0)
    sim.play_current_week()
    print(sim.league_table(sim.my_league))
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterator

from . import data


# ----- constants -----

POS_GK, POS_DEF, POS_MID, POS_FWD = 0, 1, 2, 3
POS_NAMES = ["GK", "DEF", "MID", "FWD"]

# Default tactics: (DEF, MID, FWD). GK is implicit (1).
TACTICS: dict[str, tuple[int, int, int]] = {
    "4-4-2": (4, 4, 2),
    "4-3-3": (4, 3, 3),
    "4-5-1": (4, 5, 1),
    "3-5-2": (3, 5, 2),
    "3-4-3": (3, 4, 3),
    "5-3-2": (5, 3, 2),
    "5-4-1": (5, 4, 1),
}

SEASON_WEEKS = 38        # 20-team round-robin = 38 match-weeks
MATCH_MINUTES = 90


# ----- data classes -----

@dataclass
class Player:
    name: str
    position: int            # POS_*
    age: int
    skill: int               # 0-99 current
    talent: int              # 0-99 ceiling
    fitness: int = 100       # 0-100
    form: int = 5            # -3 .. +3 (re-centred at 0 each match-week)
    goals: int = 0
    assists: int = 0
    yellow: int = 0
    red: int = 0
    games: int = 0
    value: int = 0           # currency (k)
    wage: int = 0            # per week (k)
    injury_weeks: int = 0    # >0 → unavailable

    @property
    def available(self) -> bool:
        return self.injury_weeks == 0

    @property
    def effective_skill(self) -> int:
        """Skill adjusted for fitness + form. Used by match sim."""
        base = self.skill * self.fitness / 100
        return max(1, int(base + self.form))


@dataclass
class Team:
    name: str
    league_sid: str
    avg_talent: int           # league's base talent; used to generate players
    is_user: bool = False
    players: list[Player] = field(default_factory=list)
    tactic: str = "4-4-2"
    cash: int = 2000          # in k
    # Running league stats (reset at season start)
    played: int = 0
    won: int = 0
    drew: int = 0
    lost: int = 0
    gf: int = 0
    ga: int = 0

    @property
    def points(self) -> int:
        return self.won * 3 + self.drew

    @property
    def gd(self) -> int:
        return self.gf - self.ga

    # XI selection — greedy by effective skill within the tactic shape.
    def starting_xi(self) -> list[Player]:
        d, m, f = TACTICS[self.tactic]
        buckets: dict[int, list[Player]] = {POS_GK: [], POS_DEF: [],
                                            POS_MID: [], POS_FWD: []}
        for p in self.players:
            if p.available:
                buckets[p.position].append(p)
        for bucket in buckets.values():
            bucket.sort(key=lambda p: -p.effective_skill)
        xi: list[Player] = []
        xi += buckets[POS_GK][:1]
        xi += buckets[POS_DEF][:d]
        xi += buckets[POS_MID][:m]
        xi += buckets[POS_FWD][:f]
        # If a bucket is short of bodies (injuries), fill with any
        # available player — an outfielder can deputise in an
        # emergency. Real Bygfoot auto-selects but notifies the user.
        if len(xi) < 11:
            remaining = [p for p in self.players
                         if p.available and p not in xi]
            remaining.sort(key=lambda p: -p.effective_skill)
            xi += remaining[:11 - len(xi)]
        return xi

    @property
    def team_strength(self) -> float:
        """Average effective skill of XI, weighted slightly toward
        attack when using an attacking tactic."""
        xi = self.starting_xi()
        if not xi:
            return 1.0
        return sum(p.effective_skill for p in xi) / len(xi)


# ----- player factory -----

def _generate_player(rng: random.Random, avg_talent: int, names_file: str,
                     position: int | None = None,
                     age: int | None = None) -> Player:
    """Create a player whose skill/talent cluster around the league's
    `avg_talent`. Bygfoot's average_talent scale is 0-9999; we rescale
    to 20-90 so a top-flight league (8700) gives players clustered in
    the low 80s — fitness + form then wobble that into the real
    effective_skill range on match day."""
    # 8700 → ~75, 5000 → ~45, 10000 → ~85. Wider per-player Gaussian
    # (stddev 14) so within a squad there are clear stars and role
    # players — which makes the XI strength differ more between teams.
    base = max(25, min(85, int(avg_talent * 0.0085 + 5)))
    talent = max(25, min(95, int(rng.gauss(base, 14))))
    age = age or rng.randint(17, 35)
    # Young players skilled below their talent; older near it.
    skill_gap = max(0, 28 - age) + rng.randint(-3, 3)
    skill = max(25, min(talent, talent - max(0, skill_gap) // 2))
    position = (position if position is not None
                else rng.choices(
                    [POS_GK, POS_DEF, POS_MID, POS_FWD],
                    weights=[1, 4, 4, 3], k=1)[0])
    value = int(skill * skill * (36 - age) / 100)
    wage = max(1, value // 20)
    return Player(
        name=data.random_player_name(names_file, rng),
        position=position,
        age=age,
        skill=skill,
        talent=talent,
        value=value,
        wage=wage,
    )


def _generate_squad(rng: random.Random, avg_talent: int,
                    names_file: str) -> list[Player]:
    """Build a balanced 20-man squad: 3 GK, 7 DEF, 7 MID, 5 FWD (=22).
    Trim to 22; league-leading teams should have depth."""
    players: list[Player] = []
    counts = [(POS_GK, 3), (POS_DEF, 7), (POS_MID, 7), (POS_FWD, 5)]
    for pos, n in counts:
        for _ in range(n):
            players.append(_generate_player(rng, avg_talent, names_file,
                                            position=pos))
    return players


# ----- fixtures -----

def _round_robin(n: int) -> list[list[tuple[int, int]]]:
    """Berger tables: generate a round-robin schedule for `n` teams.
    Returns a list of rounds, each a list of (home_idx, away_idx) pairs.
    Doubled so we also play the return leg (home/away swapped)."""
    if n % 2:
        n += 1  # bye slot
    rounds_1: list[list[tuple[int, int]]] = []
    teams = list(range(n))
    for r in range(n - 1):
        round_fixtures: list[tuple[int, int]] = []
        for i in range(n // 2):
            a, b = teams[i], teams[n - 1 - i]
            # Alternate who's home per pair to balance home games
            if r % 2 == i % 2:
                a, b = b, a
            round_fixtures.append((a, b))
        rounds_1.append(round_fixtures)
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]  # rotate
    rounds_2 = [[(b, a) for (a, b) in rnd] for rnd in rounds_1]
    return rounds_1 + rounds_2


# ----- match engine -----

@dataclass
class MatchEvent:
    minute: int
    team_idx: int            # 0 = home, 1 = away
    kind: str                # "goal", "shot", "save", "pass", "foul",
                             #  "injury", "card_yellow", "card_red",
                             #  "half_time", "full_time", "kickoff"
    text: str
    scorer: str | None = None
    assister: str | None = None


@dataclass
class MatchResult:
    home: Team
    away: Team
    home_goals: int
    away_goals: int
    events: list[MatchEvent] = field(default_factory=list)
    home_shots: int = 0
    away_shots: int = 0

    @property
    def outcome(self) -> str:
        if self.home_goals > self.away_goals:
            return "H"
        if self.home_goals < self.away_goals:
            return "A"
        return "D"


def _pick_goalscorer(team: Team, rng: random.Random) -> Player | None:
    """Choose a scorer weighted toward attackers. Forwards 6x, mids 3x,
    defenders 1x, GK 0."""
    xi = team.starting_xi()
    weights = []
    pool = []
    for p in xi:
        if p.position == POS_FWD:
            w = 6
        elif p.position == POS_MID:
            w = 3
        elif p.position == POS_DEF:
            w = 1
        else:
            w = 0
        if w > 0:
            pool.append(p)
            weights.append(w * p.effective_skill)
    if not pool:
        return None
    return rng.choices(pool, weights=weights, k=1)[0]


def simulate_match(home: Team, away: Team, rng: random.Random,
                   commentary: list[str] | None = None) -> MatchResult:
    """Minute-by-minute sim. Each minute has a base 8% chance of "notable
    event"; if one fires, resolve to shot / foul / card / etc. Home
    gets a 5% strength bonus. Skill differential skews shot outcomes."""
    hs = home.team_strength * 1.05
    as_ = away.team_strength
    total = hs + as_
    hs_share = hs / total
    result = MatchResult(home=home, away=away, home_goals=0, away_goals=0)
    result.events.append(MatchEvent(
        0, 0, "kickoff",
        f"Kick-off! {home.name} {home.tactic} vs {away.name} {away.tactic}."
    ))
    for minute in range(1, MATCH_MINUTES + 1):
        if minute == 46:
            result.events.append(MatchEvent(
                45, 0, "half_time",
                f"HALF TIME — {home.name} {result.home_goals} - "
                f"{result.away_goals} {away.name}."
            ))
        # Event chance per minute (tuned so ~25-30 events per match
        # including flavour passes — with real-football goal rate).
        if rng.random() > 0.32:
            continue
        # Which team has possession for this event?
        attacking_home = rng.random() < hs_share
        attacker = home if attacking_home else away
        defender = away if attacking_home else home
        team_idx = 0 if attacking_home else 1
        # Resolve event type. Distribution aims for ~12 shots/team
        # and ~2.7 goals/match. Shots 40% of events, flavour 45%,
        # fouls 12%, injuries 3%.
        r = rng.random()
        if r < 0.45:
            # Passing move / build-up — flavour text only
            if commentary:
                text = rng.choice(commentary)
            else:
                text = f"{attacker.name} build down the flank."
            result.events.append(MatchEvent(minute, team_idx, "pass", text))
        elif r < 0.85:
            # Shot attempt
            if attacking_home:
                result.home_shots += 1
            else:
                result.away_shots += 1
            # Goal chance = attacker strength / (attacker + defender GK skill
            # effective proxy via defender.team_strength). Base 28%.
            gk = next((p for p in defender.starting_xi()
                       if p.position == POS_GK), None)
            gk_skill = gk.effective_skill if gk else 40
            att = attacker.team_strength
            # Real football: ~2.7 goals per match from ~12 shots
            # on-target per side → ~22% per shot. Tuned to that
            # baseline; skill differential skews a dominant side up
            # to ~32%, and the weaker side down to ~14%.
            p_goal = 0.40 * (att / (att + gk_skill * 1.05))
            if rng.random() < p_goal:
                scorer = _pick_goalscorer(attacker, rng)
                name = scorer.name if scorer else "Unknown"
                if attacking_home:
                    result.home_goals += 1
                else:
                    result.away_goals += 1
                if scorer:
                    scorer.goals += 1
                result.events.append(MatchEvent(
                    minute, team_idx, "goal",
                    f"GOAL! {attacker.name} — {name} scores! "
                    f"({result.home_goals}-{result.away_goals})",
                    scorer=name,
                ))
            else:
                # Saved / off target
                if rng.random() < 0.5 and gk:
                    result.events.append(MatchEvent(
                        minute, team_idx, "save",
                        f"Great save by {gk.name} ({defender.name})!"
                    ))
                else:
                    result.events.append(MatchEvent(
                        minute, team_idx, "shot",
                        f"{attacker.name} shoot wide."
                    ))
        elif r < 0.93:
            # Foul / card
            offenders = [p for p in defender.starting_xi()
                         if p.position != POS_GK]
            if offenders:
                off = rng.choice(offenders)
                off.yellow += 1
                result.events.append(MatchEvent(
                    minute, 1 - team_idx, "card_yellow",
                    f"Yellow card for {off.name} ({defender.name})."
                ))
        else:
            # Injury — mild
            victims = attacker.starting_xi()
            if victims:
                v = rng.choice(victims)
                weeks = rng.randint(1, 4)
                v.injury_weeks = weeks
                result.events.append(MatchEvent(
                    minute, team_idx, "injury",
                    f"{v.name} ({attacker.name}) down — injury, "
                    f"out ~{weeks} wk."
                ))
    result.events.append(MatchEvent(
        90, 0, "full_time",
        f"FULL TIME — {home.name} {result.home_goals} - "
        f"{result.away_goals} {away.name}."
    ))
    # Post-match bookkeeping
    for p in home.starting_xi() + away.starting_xi():
        p.games += 1
        p.fitness = max(40, p.fitness - rng.randint(4, 10))
    home.played += 1
    away.played += 1
    home.gf += result.home_goals
    home.ga += result.away_goals
    away.gf += result.away_goals
    away.ga += result.home_goals
    if result.outcome == "H":
        home.won += 1
        away.lost += 1
    elif result.outcome == "A":
        away.won += 1
        home.lost += 1
    else:
        home.drew += 1
        away.drew += 1
    return result


# ----- season / game state -----

@dataclass
class LeagueState:
    sid: str
    name: str
    teams: list[Team]
    fixtures: list[list[tuple[int, int]]]   # [week_idx][match] = (h, a)
    results: list[list[MatchResult]] = field(default_factory=list)

    def table(self) -> list[Team]:
        """Sort by points desc, GD desc, GF desc, name alpha."""
        return sorted(self.teams, key=lambda t: (-t.points, -t.gd,
                                                 -t.gf, t.name))


@dataclass
class GameState:
    rng: random.Random
    country_sid: str
    leagues: dict[str, LeagueState]
    my_league_sid: str
    my_team_idx: int
    week: int = 1             # 1-based; advances after each match-week
    season: int = 1
    commentary_templates: list[str] = field(default_factory=list)

    @classmethod
    def new(cls, country_sid: str, league_sid: str, team_idx: int,
            seed: int = 0) -> "GameState":
        rng = random.Random(seed or 42)
        c = data.country(country_sid)
        if c is None:
            raise ValueError(f"unknown country: {country_sid}")
        leagues: dict[str, LeagueState] = {}
        for lg in c.leagues:
            teams: list[Team] = []
            for tn in lg.team_names:
                t = Team(name=tn, league_sid=lg.sid, avg_talent=lg.average_talent)
                t.players = _generate_squad(rng, lg.average_talent, lg.names_file)
                teams.append(t)
            fixtures = _round_robin(len(teams))
            leagues[lg.sid] = LeagueState(sid=lg.sid, name=lg.name,
                                          teams=teams, fixtures=fixtures)
        if league_sid not in leagues:
            raise ValueError(f"unknown league: {league_sid}")
        if not (0 <= team_idx < len(leagues[league_sid].teams)):
            raise ValueError(f"team_idx out of range: {team_idx}")
        leagues[league_sid].teams[team_idx].is_user = True
        from .commentary import load_commentary_templates
        commentary = load_commentary_templates()
        return cls(rng=rng, country_sid=country_sid, leagues=leagues,
                   my_league_sid=league_sid, my_team_idx=team_idx,
                   commentary_templates=commentary)

    @property
    def my_league(self) -> LeagueState:
        return self.leagues[self.my_league_sid]

    @property
    def my_team(self) -> Team:
        return self.my_league.teams[self.my_team_idx]

    @property
    def max_week(self) -> int:
        return len(self.my_league.fixtures)

    def current_week_fixtures(self) -> list[tuple[LeagueState, int, int]]:
        """Flattened list of (league, home_idx, away_idx) for the current
        week across ALL leagues in the country."""
        out = []
        w = self.week - 1
        for lg in self.leagues.values():
            if w >= len(lg.fixtures):
                continue
            for h, a in lg.fixtures[w]:
                out.append((lg, h, a))
        return out

    def my_next_fixture(self) -> tuple[Team, Team, bool] | None:
        """(home, away, is_home) for user's next match, or None if
        season's over."""
        if self.week > self.max_week:
            return None
        fx = self.my_league.fixtures[self.week - 1]
        for h, a in fx:
            if h == self.my_team_idx:
                return (self.my_league.teams[h],
                        self.my_league.teams[a], True)
            if a == self.my_team_idx:
                return (self.my_league.teams[h],
                        self.my_league.teams[a], False)
        return None

    def play_current_week(self) -> list[MatchResult]:
        """Simulate every fixture across every league in this country
        for the current week. Returns results in order."""
        out: list[MatchResult] = []
        for lg, h, a in self.current_week_fixtures():
            home = lg.teams[h]
            away = lg.teams[a]
            res = simulate_match(home, away, self.rng,
                                 self.commentary_templates)
            out.append(res)
        self.week += 1
        # Post-week fitness recovery and injury tick
        for lg in self.leagues.values():
            for t in lg.teams:
                for p in t.players:
                    p.fitness = min(100, p.fitness + self.rng.randint(6, 14))
                    if p.injury_weeks > 0:
                        p.injury_weeks -= 1
                # Pay weekly wages + ticket income for user team.
                # Ticket income scales with league average talent so a
                # top-flight team isn't immediately bankrupt.
                if t.is_user:
                    wage_bill = sum(p.wage for p in t.players)
                    # Rough: income > wages in a healthy top-flight
                    # team, slight deficit in lower leagues to
                    # incentivise promotion pushes.
                    ticket = max(200, int(t.avg_talent * 0.15))
                    t.cash += ticket - wage_bill
        return out

    def season_over(self) -> bool:
        return self.week > self.max_week

    def end_season(self) -> list[str]:
        """Promote/relegate, reset stats, rebuild fixtures. Returns
        human-readable season-end headlines.

        Pairing is symmetric: for each (higher, lower) league pair we
        swap exactly k = min(rel_slots, prom_slots) teams both ways so
        league sizes stay constant. Without this clamp you'd end up
        with 19-team Premiership + 25-team Championship (Bygfoot XMLs
        often have mismatched prom/rel counts).
        """
        country_def = data.country(self.country_sid)
        lines: list[str] = []
        if country_def is None:
            return lines
        # Map sid → league def for quick lookup.
        defs = {ld.sid: ld for ld in country_def.leagues}
        # Determine symmetric exchange count for each (higher → lower)
        # pair. A "pair" is a higher league whose rel_target names a
        # lower league that promotes back up to this sid.
        for higher_sid, higher_def in defs.items():
            if not higher_def.rel_target:
                continue
            lower_sid = higher_def.rel_target
            lower_def = defs.get(lower_sid)
            if lower_def is None:
                continue
            # Exchange count = min of the two intended slot sizes so we
            # preserve league sizes. Most XMLs match (both =3), but the
            # Premiership→Championship pair is 3 down / 2 up which would
            # unbalance sizes after the first season.
            rel_want = max(0, len(self.leagues[higher_sid].teams)
                           - higher_def.rel_rank_start + 1) \
                if higher_def.rel_rank_start else 0
            prom_want = lower_def.prom_rank_end or 0
            k = min(rel_want, prom_want)
            if k <= 0:
                continue
            higher = self.leagues[higher_sid]
            lower = self.leagues[lower_sid]
            going_down = higher.table()[-k:]          # bottom k of higher
            going_up = lower.table()[:k]              # top k of lower
            # Swap.
            for t in going_down:
                if t in higher.teams:
                    higher.teams.remove(t)
                t.league_sid = lower_sid
                lower.teams.append(t)
                lines.append(f"{t.name} relegated from {higher.name}.")
            for t in going_up:
                if t in lower.teams:
                    lower.teams.remove(t)
                t.league_sid = higher_sid
                higher.teams.append(t)
                lines.append(f"{t.name} promoted from {lower.name}.")
        # If user's team changed leagues, update pointers.
        for sid, lg in self.leagues.items():
            for idx, t in enumerate(lg.teams):
                if t.is_user:
                    self.my_league_sid = sid
                    self.my_team_idx = idx
                    break
        # Reset stats + new fixtures
        for lg in self.leagues.values():
            for t in lg.teams:
                t.played = t.won = t.drew = t.lost = 0
                t.gf = t.ga = 0
                for p in t.players:
                    p.goals = p.assists = 0
                    p.yellow = p.red = p.games = 0
                    p.age += 1  # everyone ages one year
            lg.fixtures = _round_robin(len(lg.teams))
            lg.results = []
        self.week = 1
        self.season += 1
        return lines


# ----- convenience for REPL / tests -----

def quickstart(country_sid: str = "england", league_sid: str = "england1",
               team_idx: int = 0, seed: int = 42) -> GameState:
    return GameState.new(country_sid, league_sid, team_idx, seed=seed)


def iter_results(gs: GameState, weeks: int) -> Iterator[list[MatchResult]]:
    for _ in range(weeks):
        if gs.season_over():
            gs.end_season()
        yield gs.play_current_week()
