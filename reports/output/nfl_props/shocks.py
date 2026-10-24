"""Shock A / Shock B detection from nflverse data. Uses only team games strictly before the game (look-ahead safe)
plus the final injury report of the game's own week."""
from __future__ import annotations
import pandas as pd
from feat import shares

MIN_PRIOR = 4          # a team needs >= 4 prior games in the same season (last-4 window must be complete)
A_THRESHOLD = 0.50
J_VALUES = (10, 20)


def prior_games(team_games: dict, team: str, season: int, gkey: int) -> list[int]:
    return [k for k in team_games.get((team, season), []) if k < gkey]


def detect(g: pd.DataFrame, inj_out: dict, team_games: dict, team: str, season: int, gkey: int,
           min_prior: int = MIN_PRIOR) -> dict:
    """Returns {'A': player_id|None, 'B10': set(player_ids), 'B20': set(player_ids)} for one team/group/game.
    g: group frame (columns player_id, team, season, gkey, use). inj_out: {(team,gkey): set(gsis ids Out/Doubtful)}."""
    prior = prior_games(team_games, team, season, gkey)
    res = {"A": None, "B10": set(), "B20": set()}
    if len(prior) < min_prior:
        return res
    s4 = shares(g, team, season, prior[-4:])
    out = inj_out.get((team, gkey), {"out": set(), "doubtful": set()})
    if len(s4):
        big = [p for p, v in s4.items() if v >= A_THRESHOLD and p in out["out"]]
        if big:
            rest = s4.drop([p for p in s4.index if p in out["out"] or p in out["doubtful"]], errors="ignore")
            if len(rest):
                res["A"] = rest.sort_values(ascending=False, kind="mergesort").index[0]
    s2 = shares(g, team, season, prior[-2:])
    ss = shares(g, team, season, prior)
    for J in J_VALUES:
        for p, v in s2.items():
            if p in out["out"] or p in out["doubtful"]:
                continue
            if v >= ss.get(p, 0.0) + J / 100.0 - 1e-12:
                res[f"B{J}"].add(p)
    return res


def build_inj_out(inj: pd.DataFrame) -> dict:
    d = {}
    for (team, gkey), x in inj.groupby(["team", "gkey"]):
        d[(team, gkey)] = {"out": set(x[x.report_status == "Out"].gsis_id),
                           "doubtful": set(x[x.report_status == "Doubtful"].gsis_id)}
    return d
