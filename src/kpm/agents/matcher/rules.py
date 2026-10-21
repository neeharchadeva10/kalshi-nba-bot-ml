"""Deterministic matching: pure functions so they are easy to test and reuse live."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from kpm.core.teams import kalshi_ticker_team, norm_code
from kpm.core.timeutil import ET

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
_KALSHI_EVENT = re.compile(r"^[A-Z0-9]+-(\d{2})([A-Z]{3})(\d{2})([A-Z]+)$")

WEIGHTS = {
    "teams_match": 0.30,
    "outcome_team_match": 0.30,
    "date_match": 0.20,
    "kickoff_match": 0.10,
    "kalshi_expiry_plausible": 0.05,
    "market_type_ok": 0.05,
}


@dataclass(frozen=True)
class GameRef:
    game_id: str
    home: str
    away: str
    kickoff_ts: int

    @property
    def teams(self) -> frozenset[str]:
        return frozenset((self.home, self.away))

    @property
    def et_date(self) -> str:
        return datetime.fromtimestamp(self.kickoff_ts, tz=timezone.utc).astimezone(ET).strftime("%Y-%m-%d")


@dataclass
class Contract:
    """Venue-neutral view of one YES contract, enough for matching."""
    platform: str
    market_id: int
    contract_key: str
    event_key: str
    team: str | None              # YES pays if this team wins
    event_teams: frozenset[str]   # both teams in the event
    event_date: str | None        # ET date implied by the venue's identifiers
    kickoff_hint_ts: int | None = None
    expiry_hint_ts: int | None = None
    market_type_ok: bool = True
    extra: dict = field(default_factory=dict)


def parse_kalshi_event_date(event_ticker: str) -> str | None:
    """'KXNFLGAME-25SEP04DALPHI' -> '2025-09-04'."""
    m = _KALSHI_EVENT.match(event_ticker or "")
    if not m or m.group(2) not in _MONTHS:
        return None
    return f"20{m.group(1)}-{_MONTHS[m.group(2)]:02d}-{m.group(3)}"


def kalshi_event_teams(event_ticker: str, market_tickers: list[str]) -> frozenset[str]:
    """Teams come from the per-team market suffixes (unambiguous), not the event code."""
    return frozenset(t for t in (kalshi_ticker_team(x) for x in market_tickers) if t)


def find_game(games: list[GameRef], teams: frozenset[str], et_date: str | None,
              kickoff_hint_ts: int | None = None, tolerance_s: int = 2 * 86400) -> GameRef | None:
    """Same two teams, and same ET date (or kickoff within tolerance if date missing)."""
    cands = [g for g in games if g.teams == teams]
    if et_date:
        exact = [g for g in cands if g.et_date == et_date]
        if exact:
            return exact[0]
        # Venue date can be off by one around midnight-ET kickoffs or reschedules.
        d = datetime.strptime(et_date, "%Y-%m-%d").replace(tzinfo=ET).timestamp()
        cands = [g for g in cands if abs(g.kickoff_ts - d) <= tolerance_s]
    elif kickoff_hint_ts is not None:
        cands = [g for g in cands if abs(g.kickoff_ts - kickoff_hint_ts) <= tolerance_s]
    else:
        return None
    return min(cands, key=lambda g: abs(g.kickoff_ts - (kickoff_hint_ts or g.kickoff_ts))) if cands else None


def score_pair(k: Contract, p: Contract, game: GameRef | None, kickoff_tolerance_s: int = 7200) -> tuple[float, dict]:
    """Confidence that Kalshi contract k and Polymarket contract p pay on the same outcome."""
    f: dict = {}
    f["teams_match"] = bool(game and k.event_teams == p.event_teams == game.teams)
    f["outcome_team_match"] = bool(k.team and k.team == p.team and (game is None or k.team in game.teams))
    f["date_match"] = bool(game and k.event_date == game.et_date and p.event_date == game.et_date)
    if game and p.kickoff_hint_ts is not None:
        f["kickoff_delta_s"] = abs(p.kickoff_hint_ts - game.kickoff_ts)
        f["kickoff_match"] = f["kickoff_delta_s"] <= kickoff_tolerance_s
    else:
        f["kickoff_match"] = False
    if game and k.expiry_hint_ts is not None:
        off = k.expiry_hint_ts - game.kickoff_ts
        f["kalshi_expiry_offset_s"] = off
        f["kalshi_expiry_plausible"] = 0 <= off <= 8 * 3600
    else:
        f["kalshi_expiry_plausible"] = False
    f["market_type_ok"] = bool(k.market_type_ok and p.market_type_ok)
    score = sum(w for name, w in WEIGHTS.items() if f.get(name))
    return round(score, 4), f


def norm_team_set(*codes: str | None) -> frozenset[str]:
    return frozenset(c for c in (norm_code(x) for x in codes) if c)
