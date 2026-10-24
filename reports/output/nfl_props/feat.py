"""nflverse feature building + Kalshi event mapping. No prices or outcomes of Kalshi markets are used here."""
from __future__ import annotations
import re, json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
NV = ROOT / "data" / "raw" / "nflverse"
IDX = Path(__file__).parent / "markets_index.jsonl"
STAT_COLS = ["player_id", "player_display_name", "position", "season", "week", "season_type", "team",
             "carries", "rushing_yards", "targets", "receptions", "receiving_yards"]
RB_POS = {"RB", "FB"}
REC_POS = {"WR", "TE"}
STAT_FOR = {"KXNFLRSHYDS": "rush", "KXNFLRECYDS": "recyds", "KXNFLREC": "rec"}
STAT_COL = {"rush": "rushing_yards", "recyds": "receiving_yards", "rec": "receptions"}
GROUP = {"rush": "RB", "recyds": "REC", "rec": "REC"}
ALIAS = {"LAR": "LA", "JAC": "JAX"}
MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


def norm_name(s: str) -> str:
    s = re.sub(r"[.'`]", "", str(s).lower())
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return re.sub(r"[^a-z]+", "", s)


def load_stats(years=(2024, 2025, 2026)) -> pd.DataFrame:
    fr = []
    for y in years:
        d = pd.read_csv(NV / f"stats_player_week_{y}.csv", usecols=STAT_COLS, low_memory=False)
        fr.append(d)
    d = pd.concat(fr, ignore_index=True)
    d[["carries", "targets", "receptions", "rushing_yards", "receiving_yards"]] = \
        d[["carries", "targets", "receptions", "rushing_yards", "receiving_yards"]].fillna(0)
    d["nm"] = d.player_display_name.map(norm_name)
    d["gkey"] = d.season * 100 + d.week
    return d


def load_injuries(years=(2024, 2025, 2026)) -> pd.DataFrame:
    fr = [pd.read_csv(NV / f"injuries_{y}.csv", usecols=["season", "week", "team", "gsis_id", "report_status",
                                                         "position"]) for y in years]
    d = pd.concat(fr, ignore_index=True)
    d["gkey"] = d.season * 100 + d.week
    return d


def load_schedule(years=(2024, 2025, 2026)) -> pd.DataFrame:
    d = pd.concat([pd.read_parquet(NV / f"schedules_{y}.parquet",
                                   columns=["game_id", "season", "game_type", "week", "gameday", "gametime",
                                            "away_team", "home_team"]) for y in years], ignore_index=True)
    d["gkey"] = d.season * 100 + d.week
    return d


def parse_event(ev: str, teams: set[str]):
    """KXNFLRSHYDS-26OCT19WASSF -> (date 'YYYY-10-19', awayTok, homeTok) or None."""
    m = re.match(r"^[A-Z]+-(\d\d)([A-Z]{3})(\d\d)([A-Z]+)$", ev)
    if not m:
        return None
    yy, mon, dd, tt = m.groups()
    date = f"20{yy}-{MONTHS[mon]:02d}-{dd}"
    for k in (2, 3):
        a, h = tt[:k], tt[k:]
        for aa in {a, ALIAS.get(a, a)}:
            for hh in {h, ALIAS.get(h, h)}:
                if aa in teams and hh in teams:
                    return date, aa, hh
    return None


def map_event_to_game(ev: str, sched: pd.DataFrame):
    teams = set(sched.home_team) | set(sched.away_team)
    p = parse_event(ev, teams)
    if p is None:
        return None
    date, a, h = p
    d0 = pd.Timestamp(date)
    cand = sched[(sched.away_team == a) & (sched.home_team == h)]
    cand = cand[(pd.to_datetime(cand.gameday) - d0).abs() <= pd.Timedelta(days=1)]
    if len(cand) != 1:
        return None
    return cand.iloc[0]


def team_games(stats: pd.DataFrame) -> dict:
    """team -> sorted list of (season, gkey) the team has played (from stats rows), per season."""
    out = {}
    for (team, season), g in stats.groupby(["team", "season"]):
        out[(team, season)] = sorted(g.gkey.unique())
    return out


def group_frame(stats: pd.DataFrame, group: str) -> pd.DataFrame:
    """Rows of the position group with the usage column ('carries' for RB, 'targets' for REC)."""
    pos = RB_POS if group == "RB" else REC_POS
    g = stats[stats.position.isin(pos)].copy()
    g["use"] = g.carries if group == "RB" else g.targets
    return g


def shares(g: pd.DataFrame, team: str, season: int, games: list[int]) -> pd.Series:
    """Share of the group's usage per player over the listed games (gkeys). Strict: caller passes prior games only."""
    x = g[(g.team == team) & (g.season == season) & (g.gkey.isin(games))]
    tot = x.use.sum()
    if tot <= 0:
        return pd.Series(dtype=float)
    return (x.groupby("player_id").use.sum() / tot)
