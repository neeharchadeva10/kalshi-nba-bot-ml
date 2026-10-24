"""Research log #36 features (PREREGISTRATION.md "Model").  Strictly before the decision time D = tip - 3600 s:
  * box games count as "earlier" only if tip + 4 h <= D (data.Box.prior_games / team_prior / league_avg_allowed);
  * the injury report is the latest published (PDF CreationDate) at or before D.
Feature vector per stat, in order FEATURE_NAMES.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np

import data as D

SI = {"PTS": 5, "REB": 6, "AST": 7, "3PT": 8}          # index of the stat inside a Box.hist row
FEATURE_NAMES = ["ln_rate", "ln_min", "tm_out", "q_flag", "p_flag", "ln_opp", "home", "rest", "b2b"]
RATE_WINDOW, MIN_WINDOW, ROT_WINDOW, ROT_MIN, OPP_WINDOW = 20, 10, 10, 10.0, 15
SHRINK_K = 10.0
RATE_EPS = 0.01                  # ln(rate + eps): players with a zero rate stay finite (see README)
REST_CAP = 4


def rate_features(prior, stat: str, season: int):
    """prior: the player's games before the decision (oldest first).  -> (shrunk per-minute rate, projected minutes, n20) or None.
    rate20 = sum(stat)/sum(min) over the last 20 played (any season); season rate = same over this season's prior games;
    shrunk = w*rate20 + (1-w)*season_rate, w = n/(n+10), n = games in the 20-game window (no season games -> rate20)."""
    if not prior:
        return None
    i = SI[stat]
    w20 = prior[-RATE_WINDOW:]
    m20 = sum(g[4] for g in w20)
    if m20 <= 0:
        return None
    r20 = sum(g[i] for g in w20) / m20
    ssn = [g for g in prior if g[2] == season]
    ms = sum(g[4] for g in ssn)
    n = len(w20)
    if ms > 0:
        rs = sum(g[i] for g in ssn) / ms
        w = n / (n + SHRINK_K)
        rate = w * r20 + (1 - w) * rs
    else:
        rate = r20
    w10 = prior[-MIN_WINDOW:]
    proj_min = sum(g[4] for g in w10) / len(w10)
    return rate, proj_min, n


def rotation_avg(prior, stat: str):
    """teammate: (is rotation, avg stat per game) over his last 10 played; rotation = mean minutes >= 10."""
    if not prior:
        return False, 0.0
    w = prior[-ROT_WINDOW:]
    mpg = sum(g[4] for g in w) / len(w)
    return mpg >= ROT_MIN, sum(g[SI[stat]] for g in w) / len(w)


def opp_rel(box: D.Box, opp: str, stat: str, season: int, decision_ts: float) -> float:
    """opponent's allowed stat per game over its last 15 games THIS season / league average per team-game this season to date."""
    lg, n = box.league_avg_allowed(season, decision_ts)
    if lg is None or lg[stat] <= 0:
        return 1.0
    og = [g for g in box.team_prior(opp, decision_ts) if g[2] == season][-OPP_WINDOW:]
    if not og:
        return 1.0
    return (sum(g[5][stat] for g in og) / len(og)) / lg[stat]


def rest_features(box: D.Box, team: str, game_date_iso: str, decision_ts: float):
    prev = box.team_prior(team, decision_ts)
    if not prev:
        return float(REST_CAP), 0.0
    diff = (date.fromisoformat(game_date_iso) - date.fromisoformat(prev[-1][1])).days
    diff = max(diff, 0)
    return float(min(diff, REST_CAP)), 1.0 if diff == 1 else 0.0


def report_status(report, team: str, player_name: str):
    """own status of the player in this report ('Out', 'Doubtful', 'Questionable', 'Probable', 'Available' or None)."""
    if not report or not report["covered"]:
        return None
    key = D.norm_name(player_name)
    for r in report["rows"]:
        if D.TEAMNAME2CODE.get(r["team"]) == team and D.norm_injury_name(r["player"]) == key:
            return r["status"]
    return None


def teammates_out(box: D.Box, report, team: str, aid: int, stat: str, decision_ts: float, season: int):
    """Sum of last-10 per-game averages of `stat` over rotation teammates (mean min >= 10 over last 10) listed Out or Doubtful."""
    if not report or not report["covered"]:
        return 0.0, 0, 0
    tot, n_listed, n_unmatched = 0.0, 0, 0
    seen = set()
    for r in report["rows"]:
        if D.TEAMNAME2CODE.get(r["team"]) != team or r["status"] not in ("Out", "Doubtful"):
            continue
        key = D.norm_injury_name(r["player"])
        if key in seen:
            continue
        seen.add(key)
        ids = box.roster.get((team, season), {}).get(key, set())
        if len(ids) != 1:
            n_unmatched += 1
            continue
        tid = next(iter(ids))
        if tid == aid:
            continue
        n_listed += 1
        rot, avg = rotation_avg(box.prior_games(tid, decision_ts), stat)
        if rot:
            tot += avg
    return tot, n_listed, n_unmatched


def build_vector(box: D.Box, aid: int, team: str, opp: str, home: bool, stat: str, game_date_iso: str, decision_ts: float,
                 report, player_name: str, season: int = D.CUR_SEASON):
    """-> (vector or None, info dict).  None = no history."""
    prior = box.prior_games(aid, decision_ts)
    rf = rate_features(prior, stat, season)
    info = {"status": report_status(report, team, player_name)}
    if rf is None:
        return None, info
    rate, pmin, n20 = rf
    tm, n_list, n_unm = teammates_out(box, report, team, aid, stat, decision_ts, season)
    rest, b2b = rest_features(box, team, game_date_iso, decision_ts)
    orel = opp_rel(box, opp, stat, season, decision_ts)
    st = info["status"]
    vec = [math.log(rate + RATE_EPS), math.log(max(pmin, 1.0)), tm, 1.0 if st == "Questionable" else 0.0,
           1.0 if st == "Probable" else 0.0, math.log(max(orel, 0.1)), 1.0 if home else 0.0, rest, b2b]
    info.update({"rate": rate, "proj_min": pmin, "n20": n20, "tm_out": tm, "tm_out_listed": n_list, "tm_out_unmatched": n_unm,
                 "opp_rel": orel, "rest": rest, "b2b": b2b, "expected": rate * pmin})
    return np.array(vec, dtype=float), info
