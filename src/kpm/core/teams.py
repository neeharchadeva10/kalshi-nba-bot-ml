"""Normalize team identifiers from each venue to nflverse abbreviations."""
from __future__ import annotations

import re

# nflverse code -> (city/region as Kalshi writes it, mascot as Polymarket writes it)
TEAMS: dict[str, tuple[str, str]] = {
    "ARI": ("Arizona", "Cardinals"), "ATL": ("Atlanta", "Falcons"), "BAL": ("Baltimore", "Ravens"),
    "BUF": ("Buffalo", "Bills"), "CAR": ("Carolina", "Panthers"), "CHI": ("Chicago", "Bears"),
    "CIN": ("Cincinnati", "Bengals"), "CLE": ("Cleveland", "Browns"), "DAL": ("Dallas", "Cowboys"),
    "DEN": ("Denver", "Broncos"), "DET": ("Detroit", "Lions"), "GB": ("Green Bay", "Packers"),
    "HOU": ("Houston", "Texans"), "IND": ("Indianapolis", "Colts"), "JAX": ("Jacksonville", "Jaguars"),
    "KC": ("Kansas City", "Chiefs"), "LA": ("Los Angeles R", "Rams"), "LAC": ("Los Angeles C", "Chargers"),
    "LV": ("Las Vegas", "Raiders"), "MIA": ("Miami", "Dolphins"), "MIN": ("Minnesota", "Vikings"),
    "NE": ("New England", "Patriots"), "NO": ("New Orleans", "Saints"), "NYG": ("New York G", "Giants"),
    "NYJ": ("New York J", "Jets"), "PHI": ("Philadelphia", "Eagles"), "PIT": ("Pittsburgh", "Steelers"),
    "SEA": ("Seattle", "Seahawks"), "SF": ("San Francisco", "49ers"), "TB": ("Tampa Bay", "Buccaneers"),
    "TEN": ("Tennessee", "Titans"), "WAS": ("Washington", "Commanders"),
}

ALIASES = {
    "JAC": "JAX", "LAR": "LA", "STL": "LA", "SD": "LAC", "OAK": "LV", "LVR": "LV", "WSH": "WAS",
    "GNB": "GB", "KAN": "KC", "NWE": "NE", "NOR": "NO", "SFO": "SF", "TAM": "TB",
}

_BY_NAME: dict[str, str] = {}
for code, (city, mascot) in TEAMS.items():
    _BY_NAME[city.lower()] = code
    _BY_NAME[mascot.lower()] = code
    _BY_NAME[f"{city} {mascot}".lower()] = code
for extra, code in {"los angeles rams": "LA", "los angeles chargers": "LAC", "new york giants": "NYG",
                    "new york jets": "NYJ", "washington commanders": "WAS", "ny giants": "NYG",
                    "ny jets": "NYJ", "la rams": "LA", "la chargers": "LAC"}.items():
    _BY_NAME[extra] = code


def norm_code(code: str | None) -> str | None:
    if not code:
        return None
    c = code.strip().upper()
    c = ALIASES.get(c, c)
    return c if c in TEAMS else None


def from_name(name: str | None) -> str | None:
    """Map a team name/city/mascot (any venue's display text) to an nflverse code."""
    if not name:
        return None
    n = re.sub(r"\s+", " ", name.strip().lower())
    if n in _BY_NAME:
        return _BY_NAME[n]
    return norm_code(n)


def kalshi_ticker_team(ticker: str) -> str | None:
    """'KXNFLGAME-25SEP04DALPHI-DAL' -> 'DAL'."""
    return norm_code(ticker.rsplit("-", 1)[-1])


_POLY_SLUG = re.compile(r"^(?:aec-)?nfl-([a-z]+)-([a-z]+)-(\d{4}-\d{2}-\d{2})$")


def poly_slug_teams(slug: str) -> tuple[str | None, str | None, str | None]:
    """'nfl-dal-phi-2025-09-04' (Polymarket) or 'aec-nfl-dal-phi-2025-09-04' (Polymarket US)
    -> ('DAL', 'PHI', '2025-09-04'), i.e. (away, home, ET date)."""
    m = _POLY_SLUG.match(slug or "")
    if not m:
        return None, None, None
    return norm_code(m.group(1)), norm_code(m.group(2)), m.group(3)
