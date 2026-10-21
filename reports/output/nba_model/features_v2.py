"""Research log #31 features (reports/output/nba_model/PREREGISTRATION_V2.md). Strict no-look-ahead.

build_features_v2(games, box, reports, name2code) -> (rows, stats). One row per game in (tip, gid) order.
  box      : load_box() output (open hoopR/ESPN box scores, data/raw/nba_open/)
  reports  : {gid: {"file", "created_utc", "rows": [...], "nys": [...], "header"}} from the official PDFs
Features for game g use only: games with a strictly earlier tip for the SAME teams (Elo, net margin, rest; a team's
previous game always ended before g starts), box scores of dates strictly before g's local date (player values), and
the injury report published (PDF CreationDate) <= tip - 3600 s.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import features as F  # noqa: E402
import injury_parse as IP  # noqa: E402

ROOT = F.ROOT
WINDOW, SHRINK_K = 15, 10.0
REGULAR_MIN_AVG, REGULAR_MIN_N = 20.0, 5
ESPN2KALSHI = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}
KALSHI_CODES = {"ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC", "LAL", "MEM",
                "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS"}

INTERPRETATION_V2 = [
    "Box scores: hoopR-nba-data player_box_2026 / team_box_2026 (ESPN-derived, CC BY 4.0). Only the 30 NBA teams are used (All-Star "
    "exhibition teams dropped). ESPN abbreviations mapped GS/NO/NY/SA/UTAH/WSH to GSW/NOP/NYK/SAS/UTA/WAS.",
    "Game matching Kalshi <-> box: (ticker date, home code, away code), +-1 day tolerance; unmatched games are counted. Exact margin = "
    "box final home score - away score. Winner disagreement with the Kalshi result is counted (such a margin is not used).",
    "Elo / rolling net margin / rest use the Kalshi game list exactly as v1 (same games, K=20, home 100, 538 multiplier, 1500 start); "
    "only the margin source changes (exact box score). A game with no matched box score updates Elo as a 1-point win and is left "
    "out of the rolling net margin (v1 rule).",
    "Injury report publication time = the PDF /CreationDate (stricter than the file-name stamp: hourly files stamped H were created at H:30). "
    "The game's report = the file with the latest CreationDate <= scheduled tip - 3600 s (tip = Kalshi occurrence - 3 h, drift.load_nba).",
    "A game is 'covered' when its matchup (AWAY@HOME, same date) appears in that report. A team shown only as NOTYETSUBMITTED, or a "
    "game absent from the report, contributes 0 (neutral); a game with no qualifying report at all has inj_diff = 0. "
    "A covered team with no listed players is a real 0.",
    "Player value window = the player's team's last 15 box-score games with a strictly earlier local date (fewer early in the season; "
    "2025-26 only, no carry-over, as Elo). The player's games = the games in that window he played (minutes > 0), n = their count. "
    "Average plus-minus per minute = sum(plus_minus)/sum(minutes) over those games (pooled, minute-weighted); average minutes = "
    "sum(minutes)/n over the same games. value = pm_per_min x avg_minutes x n/(n+10)  (= mean plus-minus per game x n/(n+10)). "
    "A listed player with no games for that team in the window has n = 0, value 0.",
    "P(miss): Out 1.0, Doubtful 0.75, Questionable 0.5, Probable 0.1, Available 0.0. Missing value of a team = sum over its listed "
    "players of value x P(miss). Feature inj_diff = home missing value minus away missing value (as v1's miss_diff).",
    "Name matching: injury 'Last,First' (suffix glued to last name, e.g. PippenJr.,Scotty) and box 'First Last Jr.' are both normalised to "
    "accent-free lower-case letters of 'first+last' with the generational suffix removed, matched within the listed team's window. "
    "'Unmatched' = listed (non-Available) players with no box-score row at all this season on any team; 'no window games' = matched "
    "name but zero games in the team's last-15 window (value 0).",
    "Regular (breakdown only): a player with n >= 5 and average minutes >= 20 in the window. 'Regular Out/Doubtful' = such a player "
    "listed Out or Doubtful for either team (stored as key_missing).",
    "Fit, standardisation (discovery mean/sd), ridge, bets, bootstrap, coin flip, pass-bar rules: model.py unchanged. Elo-exact-only "
    "model = elo_diff alone (with exact margins). Full v2 columns = elo_diff, net_diff, rest_diff, b2b_diff, inj_diff.",
]


def load_box(box_dir: Path | None = None):
    """-> {'games': {game_id: {date, home, away, hs, as_, players{team: {norm_name: (min, pm, pts)}}}}, 'by_key', 'all_names'}."""
    import pandas as pd
    d = box_dir or ROOT / "data" / "raw" / "nba_open"
    pb = pd.read_parquet(d / "player_box_2026.parquet")
    tb = pd.read_parquet(d / "team_box_2026.parquet")
    code = lambda a: ESPN2KALSHI.get(a, a)  # noqa: E731
    games = {}
    for r in tb.itertuples():
        c = code(r.team_abbreviation)
        if c not in KALSHI_CODES:
            continue
        g = games.setdefault(str(r.game_id), {"date": str(r.game_date), "home": None, "away": None, "hs": None, "as_": None,
                                              "players": defaultdict(dict), "season_type": int(r.season_type)})
        if r.team_home_away == "home":
            g["home"], g["hs"] = c, int(r.team_score)
        else:
            g["away"], g["as_"] = c, int(r.team_score)
    all_names = defaultdict(set)
    pb = pb[pb["did_not_play"] == False]  # noqa: E712
    for r in pb.itertuples():
        gid = str(r.game_id)
        if gid not in games:
            continue
        c = code(r.team_abbreviation)
        mn = r.minutes
        if mn is None or mn != mn or mn <= 0:
            continue
        try:
            pm = int(str(r.plus_minus).replace("+", ""))
        except ValueError:
            continue
        pts = float(r.points) if r.points == r.points else 0.0
        nm = IP.norm_box_name(r.athlete_display_name)
        games[gid]["players"][c][nm] = (float(mn), pm, pts)
        all_names[nm].add(c)
    games = {k: v for k, v in games.items() if v["home"] and v["away"] and v["hs"] is not None and v["as_"] is not None}
    by_key = {(g["date"], g["home"], g["away"]): gid for gid, g in games.items()}
    return {"games": games, "by_key": by_key, "all_names": all_names}


def match_box(box, gdate, home, away):
    """(box game id, day offset) for a Kalshi game or (None, None); tries date, date-1, date+1."""
    for dd in (0, -1, 1):
        k = ((gdate + timedelta(days=dd)).isoformat(), home, away)
        if k in box["by_key"]:
            return box["by_key"][k], dd
    return None, None


def player_value(stats):
    """stats: list of (minutes, plus_minus) of the games played in the window -> (value, n, avg_min)."""
    n = len(stats)
    if n == 0:
        return 0.0, 0, 0.0
    tm = sum(m for m, _ in stats)
    tp = sum(p for _, p in stats)
    pm_per_min = tp / tm
    avg_min = tm / n
    return pm_per_min * avg_min * n / (n + SHRINK_K), n, avg_min


def team_values(team_games, window=WINDOW):
    """team_games: [(box game, team)] time ordered, strictly before g -> {norm_name: (value, n, avg_min)}."""
    acc = defaultdict(list)
    for bg, team in team_games[-window:]:
        for nm, tup in bg["players"].get(team, {}).items():
            acc[nm].append((tup[0], tup[1]))   # (minutes, plus_minus[, points])
    return {nm: player_value(s) for nm, s in acc.items()}


def report_lookup(rep, gdate, away, home):
    """-> (covered, rows of this matchup/date, nys entries)."""
    ds = gdate.strftime("%m/%d/%Y")
    mu = f"{away}@{home}"
    rows = [r for r in rep["rows"] if r["date"] == ds and r["matchup"] == mu]
    nys = [x for x in rep["nys"] if x[0] == ds and x[1] == mu]
    return bool(rows or nys), rows, nys


def build_features_v2(games, box, reports, name2code):
    games = sorted(games, key=lambda g: (g.tip, g.gid))
    elo = defaultdict(lambda: F.ELO_START)
    team_hist = defaultdict(list)        # team -> [(date, margin_for_team or None)]
    box_by_team = defaultdict(list)      # team -> [(date, box game id)] sorted
    for gid, g in box["games"].items():
        for t in (g["home"], g["away"]):
            box_by_team[t].append((g["date"], gid))
    for t in box_by_team:
        box_by_team[t].sort()
    stats = defaultdict(int)
    unmatched_names = defaultdict(int)
    rows = []
    i = 0
    while i < len(games):
        j = i
        while j < len(games) and games[j].tip == games[i].tip:
            j += 1
        pending = []
        for g in games[i:j]:
            suf = g.gid.split("-", 1)[1]
            h_c, a_c = g.mk[g.home_idx].code, g.mk[1 - g.home_idx].code
            gdate = F.parse_suffix_date(suf)
            row = {"gid": g.gid, "tip": g.tip, "home": h_c, "away": a_c, "home_win": g.mk[g.home_idx].result}
            for side, tm in (("h", h_c), ("a", a_c)):
                row["elo_" + side] = elo[tm]
                mg = [m for _, m in team_hist[tm][-F.RECENT:] if m is not None]
                row["net_" + side] = sum(mg) / len(mg) if mg else 0.0
                hist = team_hist[tm]
                rest = max(min((gdate - hist[-1][0]).days, F.REST_CAP), 0) if hist else F.REST_CAP
                row["rest_" + side] = float(rest)
                row["b2b_" + side] = 1.0 if rest == 1 else 0.0
            row["elo_diff"] = row["elo_h"] - row["elo_a"]
            row["net_diff"] = row["net_h"] - row["net_a"]
            row["rest_diff"] = row["rest_h"] - row["rest_a"]
            row["b2b_diff"] = row["b2b_h"] - row["b2b_a"]
            # ---------------- injury feature
            row.update({"report_found": 0.0, "covered": 0.0, "miss_h": 0.0, "miss_a": 0.0, "inj_diff": 0.0,
                        "key_missing": 0.0, "both_observed": 0.0, "nys_h": 0.0, "nys_a": 0.0, "n_listed": 0.0})
            rep = reports.get(g.gid)
            if rep is not None:
                row["report_found"] = 1.0
                covered, prow, nys = report_lookup(rep, gdate, a_c, h_c)
                row["covered"] = row["both_observed"] = float(covered)
                if covered:
                    vals = {}
                    for tm in (h_c, a_c):
                        prev = [(box["games"][b], tm) for d_, b in box_by_team[tm] if d_ < gdate.isoformat()]
                        vals[tm] = team_values(prev)
                    miss = {h_c: 0.0, a_c: 0.0}
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
                                if nm in box["all_names"]:
                                    stats["no_window_games"] += 1
                                else:
                                    stats["unmatched_never_in_box"] += 1
                                    unmatched_names[nm] += 1
                        miss[tm] += v * pmiss
                        if r["status"] in ("Out", "Doubtful") and n >= REGULAR_MIN_N and avgm >= REGULAR_MIN_AVG:
                            row["key_missing"] = 1.0
                    for x in nys:
                        tm = name2code.get(x[2])
                        if tm in (h_c, a_c):
                            row["nys_h" if tm == h_c else "nys_a"] = 1.0
                    row["miss_h"], row["miss_a"] = miss[h_c], miss[a_c]
                    row["inj_diff"] = miss[h_c] - miss[a_c]
            rows.append(row)
            pending.append((g, gdate, h_c, a_c))
        for g, gdate, h_c, a_c in pending:
            home_won = g.mk[g.home_idx].result == 1.0
            bid, dd = match_box(box, gdate, h_c, a_c)
            margin_home = None
            if bid is None:
                stats["box_game_unmatched"] += 1
            else:
                bg = box["games"][bid]
                m = bg["hs"] - bg["as_"]
                if m == 0 or (m > 0) != home_won:
                    stats["box_winner_disagrees_kalshi"] += 1
                else:
                    margin_home = m
                    stats["box_matched"] += 1
                    if dd:
                        stats["box_matched_off_by_one_day"] += 1
            elo[h_c], elo[a_c] = F.elo_update(elo[h_c], elo[a_c], home_won, abs(margin_home) if margin_home else 1.0)
            team_hist[h_c].append((gdate, margin_home))
            team_hist[a_c].append((gdate, None if margin_home is None else -margin_home))
        i = j
    stats["unmatched_name_examples"] = dict(sorted(unmatched_names.items(), key=lambda kv: -kv[1])[:25])
    return rows, dict(stats)
