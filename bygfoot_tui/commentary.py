"""Load and resolve Bygfoot's match commentary templates.

Templates look like:
    "A [long|short|slow] floating pass from _P1_ finds _P0_"

with variables:
    _P0_ / _P1_ — attacker / passer
    _T_POSS__ / _T_NPOSS__ — possessing / non-possessing team
    _MI_ — match minute
    _GD_ — goal differential

and conditional attrs (`pri`, `cond`) we skip for now — those would
need a small expression parser. We just take unconditional general
commentary as pre-match flavour for the engine, then rely on the
engine's own event text for goals / cards / full-time lines.
"""

from __future__ import annotations

import random
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from . import data

_BRACKET_RE = re.compile(r"\[([^\[\]]+)\]")


def _expand_brackets(text: str, rng: random.Random) -> str:
    """Replace every `[a|b|c]` with one of its alternatives."""
    while True:
        m = _BRACKET_RE.search(text)
        if not m:
            return text
        choice = rng.choice(m.group(1).split("|"))
        text = text[:m.start()] + choice + text[m.end():]


@lru_cache(maxsize=1)
def _raw_templates() -> list[str]:
    path = data.COMMENTARY / "lg_commentary_en.xml"
    if not path.exists():
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    out: list[str] = []
    for evt in root.findall("event"):
        # For now, only "general" event commentary (no conditional /
        # situational). Skip any <commentary> carrying a `cond` attr
        # because we don't evaluate expressions.
        name_elem = evt.find("name")
        name = name_elem.text.strip() if (name_elem is not None
                                          and name_elem.text) else ""
        if name != "general":
            continue
        for c in evt.findall("commentary"):
            if c.get("cond") or c.get("pri"):
                continue
            if c.text and c.text.strip():
                out.append(c.text.strip())
    return out


def load_commentary_templates() -> list[str]:
    """Return a prepared (bracket-expanded) list of 200+ flavour lines.
    We expand brackets with a throwaway RNG so the engine just picks
    randomly from a ready pool — no regex in the hot path."""
    rng = random.Random(0)   # deterministic expansion pass
    raw = _raw_templates()
    out: list[str] = []
    for tmpl in raw:
        # Each template gets 3 variant expansions if it has brackets.
        if "[" in tmpl:
            seen = set()
            for _ in range(3):
                expanded = _expand_brackets(tmpl, rng)
                if expanded not in seen:
                    out.append(expanded)
                    seen.add(expanded)
        else:
            out.append(tmpl)
    if not out:
        # Fallback — commentary XML missing or unparsable.
        out = [
            "The ball is moved upfield.",
            "A neat passing move.",
            "The defence hold their shape.",
            "Play breaks down in midfield.",
            "A probing through-ball.",
        ]
    return out


def render_flavour(template: str, attacker_name: str, passer_name: str,
                   poss_team: str, non_poss_team: str) -> str:
    """Substitute the common variables in a Bygfoot commentary template
    with real names. Unused variables are left alone."""
    return (template
            .replace("_P0_", attacker_name)
            .replace("_P1_", passer_name)
            .replace("_T_POSS__", poss_team)
            .replace("_T_NPOSS__", non_poss_team))
