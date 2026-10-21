"""Research log #32 features (reports/output/nba_model/PREREGISTRATION_V3.md). Strict no-look-ahead.

build_features_v3(box, reports, name2code) -> (rows, stats). One row per box game in (tip, gid) order, 5 seasons (2021-22..2025-26).
Features for game g use only: games with a strictly earlier tip (Elo; ties at the same tip all see the state before that tip),
the same team's games of the SAME season for rolling net margin / rest (reset every season), the Elo carried over from earlier
seasons (rating = 0.75 * end-of-season + 0.25 * 1505), box scores of the same season with a strictly earlier local date (player
values), and the injury report published (PDF CreationDate) <= tip - 3600 s.
"""
from __future__ import annotations

import bisect
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import features as F  # noqa: E402  (elo_update, constants)
import features_v2 as F2  # noqa: E402  (player_value, team_values, report_lookup)
import injury_parse as IP  # noqa: E402

CARRY, MEAN_RATING = 0.75, 1505.0
FIRST_START = 1500.0              # 2021-22 start rating (v1 value); every later season starts from the carry-over
MIN_SEASON_GAMES = 5              # a player's first 5 games of a season: shrinkage only (value 0)

INTERPRETATION_V3 = [
    "Game universe = every hoopR box-score game of the 30 NBA teams in 2021-22..2025-26 (regular season, play-in, playoffs; no "
    "preseason, All-Star dropped), sorted by (tip, game id). Elo and rolling stats run over this complete list (not over the Kalshi "
    "list). Tip = ESPN game_date_time (scheduled start). Local date = ESPN game_date (US Eastern).",
    "Elo: K=20, home advantage 100, 538 multiplier, exact box-score margin for every game (v1/v2 formulas, features.elo_update). "
    "2021-22 starts every team at 1500 (v1 value). At the first game of each later season every rating is replaced by "
    "0.75 * (rating after the team's last game of the previous season, playoffs included) + 0.25 * 1505.",
    "Rolling 10-game net margin and rest (calendar days, cap 4, first game of a season = 4) are computed within a season only and "
    "reset at each season start (only Elo carries over, as the pre-registration says).",
    "Player value = v2 recipe (last 15 games of the player's team in the same season with a strictly earlier local date; pooled "
    "plus-minus per minute x average minutes x n/(n+10)). 'A player's first 5 games of a season get shrinkage only' is read as: a "
    "listed player who has played fewer than 5 games for any team earlier in the same season (box-score minutes > 0, strictly "
    "earlier date) gets value 0 (the prior); no previous-season data is used. Sensitivity column inj_diff_v2 keeps the v2 value "
    "(n/(n+10) shrinkage only) and is reported for the primary version.",
    "Injury report per game: the file with the latest PDF CreationDate <= tip - 3600 s (as v2). Matchup AWAY@HOME on the game's local "
    "date must appear in that report for the game to be 'covered'; otherwise inj_diff = 0 (neutral). Team-name map: ESPN display "
    "names without spaces (+ 'LAClippers', 'LosAngelesClippers').",
    "The 2021-22 / 2022-23 PDFs use an older layout (two-word headers, names with spaces); injury_parse_v3.parse_old reads it and "
    "returns the same text form ('TrentJr.,Gary'), so the v2 name normalisation is unchanged.",
]


def season_carry(r: float) -> float:
    """538-style new-season rating from an end-of-season rating."""
    return CARRY * r + (1.0 - CARRY) * MEAN_RATING


def build_features_v3(box, reports, name2code):
    gl = sorted(box["games"].values(), key=lambda g: (g["tip"], g["gid"]))
    box_by_team_season = defaultdict(list)       # (team, season) -> [(date_iso, gid)] sorted
    for g in gl:
        for t in (g["home"], g["away"]):
            box_by_team_season[(t, g["season"])].append((g["date"], g["gid"]))
    for k in box_by_team_season:
        box_by_team_season[k].sort()
    player_dates = defaultdict(list)             # (norm_name, season) -> sorted iso dates he played (any team)
    for g in gl:
        for t in (g["home"], g["away"]):
            for nm in g["players"].get(t, {}):
                player_dates[(nm, g["season"])].append(g["date"])
    for k in player_dates:
        player_dates[k].sort()
    elo = defaultdict(lambda: FIRST_START)
    team_hist = defaultdict(list)                # team -> [(date, margin_for_team)] of the CURRENT season
    stats = defaultdict(int)
    unmatched_names = defaultdict(int)
    rows = []
    cur_season = None
    i = 0
    while i < len(gl):
        j = i
        while j < len(gl) and gl[j]["tip"] == gl[i]["tip"]:
            j += 1
        grp = gl[i:j]
        if grp[0]["season"] != cur_season:
            if cur_season is not None:
                for t in list(elo):
                    elo[t] = season_carry(elo[t])
                stats["season_rollovers"] += 1
            team_hist.clear()
            cur_season = grp[0]["season"]
        pending = []
        for g in grp:
            h_c, a_c = g["home"], g["away"]
            gdate = date.fromisoformat(g["date"])
            row = {"gid": g["gid"], "tip": g["tip"], "date": g["date"], "season": g["season"], "stype": g["stype"], "home": h_c,
                   "away": a_c, "home_win": 1.0 if g["hs"] > g["as_"] else 0.0, "margin": g["hs"] - g["as_"]}
            for side, tm in (("h", h_c), ("a", a_c)):
                row["elo_" + side] = elo[tm]
                mg = [m for _, m in team_hist[tm][-F.RECENT:]]
                row["net_" + side] = sum(mg) / len(mg) if mg else 0.0
                hist = team_hist[tm]
                rest = max(min((gdate - hist[-1][0]).days, F.REST_CAP), 0) if hist else F.REST_CAP
                row["rest_" + side] = float(rest)
                row["b2b_" + side] = 1.0 if rest == 1 else 0.0
            row["elo_diff"] = row["elo_h"] - row["elo_a"]
            row["net_diff"] = row["net_h"] - row["net_a"]
            row["rest_diff"] = row["rest_h"] - row["rest_a"]
            row["b2b_diff"] = row["b2b_h"] - row["b2b_a"]
            row.update({"report_found": 0.0, "covered": 0.0, "miss_h": 0.0, "miss_a": 0.0, "inj_diff": 0.0, "inj_diff_v2": 0.0,
                        "key_missing": 0.0, "nys_h": 0.0, "nys_a": 0.0, "n_listed": 0.0})
            rep = reports.get(g["gid"])
            if rep is not None:
                row["report_found"] = 1.0
                covered, prow, nys = F2.report_lookup(rep, gdate, a_c, h_c)
                row["covered"] = float(covered)
                if covered:
                    vals = {}
                    for tm in (h_c, a_c):
                        lst = box_by_team_season[(tm, g["season"])]
                        k = bisect.bisect_left(lst, (g["date"], ""))      # strictly earlier local date
                        vals[tm] = F2.team_values([(box["games"][b], tm) for _, b in lst[:k]])
                    miss, miss2 = {h_c: 0.0, a_c: 0.0}, {h_c: 0.0, a_c: 0.0}
                    seen = set()
                    for r in prow:
                        tm = name2code.get(r["team"])
                        if tm not in (a_c, h_c):
                            stats["row_team_not_in_matchup"] += 1
                            continue
                        nm = IP.norm_injury_name(r["player"])
                        if (tm, nm) in seen:
                            stats["duplicate_listing"] += 1
                            continue
                        seen.add((tm, nm))
                        pmiss = IP.P_MISS[r["status"]]
                        row["n_listed"] += 1
                        stats["listed_" + r["status"]] += 1
                        v, n, avgm = vals[tm].get(nm, (0.0, 0, 0.0))
                        if pmiss > 0:
                            stats["listed_nonavailable"] += 1
                            if nm not in vals[tm]:
                                if any(s == g["season"] for s, _ in box["all_names"].get(nm, ())):
                                    stats["no_window_games"] += 1
                                else:
                                    stats["unmatched_never_in_box_this_season"] += 1
                                    unmatched_names[nm] += 1
                        v2 = v
                        n_prior = bisect.bisect_left(player_dates.get((nm, g["season"]), []), g["date"])
                        if n_prior < MIN_SEASON_GAMES:
                            if pmiss > 0 and v != 0.0:
                                stats["value_zeroed_first5"] += 1
                            v = 0.0
                        miss[tm] += v * pmiss
                        miss2[tm] += v2 * pmiss
                        if r["status"] in ("Out", "Doubtful") and n >= F2.REGULAR_MIN_N and avgm >= F2.REGULAR_MIN_AVG:
                            row["key_missing"] = 1.0
                    for x in nys:
                        tm = name2code.get(x[2])
                        if tm in (h_c, a_c):
                            row["nys_h" if tm == h_c else "nys_a"] = 1.0
                    row["miss_h"], row["miss_a"] = miss[h_c], miss[a_c]
                    row["inj_diff"] = miss[h_c] - miss[a_c]
                    row["inj_diff_v2"] = miss2[h_c] - miss2[a_c]
            rows.append(row)
            pending.append((g, gdate))
        for g, gdate in pending:
            m = g["hs"] - g["as_"]
            h_c, a_c = g["home"], g["away"]
            elo[h_c], elo[a_c] = F.elo_update(elo[h_c], elo[a_c], m > 0, abs(m))
            team_hist[h_c].append((gdate, m))
            team_hist[a_c].append((gdate, -m))
        i = j
    stats["unmatched_name_examples"] = dict(sorted(unmatched_names.items(), key=lambda kv: -kv[1])[:25])
    return rows, dict(stats)
