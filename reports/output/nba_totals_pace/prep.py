"""Research log #37: build the game table WITHOUT any outcomes (no actual totals, no market results).
games: one row per KXNBATOTAL event with schedule T, season, projection, strikes (T-1h quotes), market median, split.
Outcomes are attached later by run.py, and only for the games a stage is allowed to see."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

import lib

HERE = lib.Path(__file__).resolve().parent
ROOT = lib.ROOT
TOT = HERE / "totals.sqlite"
DRIFT = ROOT / "data" / "nba_drift.sqlite"
BOX = ROOT / "data" / "raw" / "nba_open"
SEASON_SPLIT_TS = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())   # T before -> 2025-26, after -> 2026-27
ET = ZoneInfo("America/New_York")


def load_box(year):
    return pd.read_parquet(BOX / f"team_box_{year}.parquet")


def ratings_tables():
    """{'2024-25': ratings from team_box_2025, '2025-26': from team_box_2026}  (regular season only)."""
    return {"2024-25": lib.build_ratings(load_box(2025)), "2025-26": lib.build_ratings(load_box(2026))}


def hoopr_games(year):
    """One row per game: game_id, date, season_type, away/home (Kalshi codes) and total points."""
    tb = load_box(year)
    tb = tb.assign(game_date=tb.game_date.astype(str))
    away = tb[tb.team_home_away == "away"][["game_id", "game_date", "season_type", "team_abbreviation",
                                            "team_score", "game_date_time"]]
    home = tb[tb.team_home_away == "home"][["game_id", "team_abbreviation", "team_score"]]
    g = away.merge(home, on="game_id", suffixes=("_a", "_h"))
    g["away"] = g.team_abbreviation_a.map(lib.to_kalshi)
    g["home"] = g.team_abbreviation_h.map(lib.to_kalshi)
    g["total"] = g.team_score_a + g.team_score_h
    g["tip"] = g.game_date_time.map(lambda x: int(x.timestamp()))   # hoopR scheduled tip (UTC epoch)
    g["raw_away"], g["raw_home"] = g.team_abbreviation_a, g.team_abbreviation_h
    return g[["game_id", "game_date", "season_type", "away", "home", "raw_away", "raw_home", "total", "tip"]]


def match_hoopr(h, date, away, home):
    """Match by team pair and date (ET game date; +/-1 day tolerance)."""
    d0 = datetime.strptime(date, "%Y-%m-%d")
    cands = {(d0 + timedelta(days=k)).strftime("%Y-%m-%d"): k for k in (0, -1, 1)}
    m = h[(h.away == away) & (h.home == home) & h.game_date.isin(cands)]
    if m.empty:
        return None
    m = m.assign(_k=m.game_date.map(lambda x: abs(cands[x]))).sort_values("_k")
    return m.iloc[0]


def schedule_T(base_T, hoopr_tip):
    """Amendment 2.3: for 2025-26 games T = min(KXNBAGAME-or-own T, hoopR tip). Returns (T, source_tag)."""
    if hoopr_tip is not None and hoopr_tip < base_T:
        return int(hoopr_tip), "hoopr_tip"
    return int(base_T), None


def assert_candle_before_tip(candle_end, tip):
    """Every quote used must come from a candle ending at or before tip - 1h."""
    if candle_end is not None and tip is not None and candle_end > tip - 3600:
        raise AssertionError(f"candle ending {candle_end} is after tip-60m ({tip - 3600})")


def build_games():
    """Returns (games DataFrame, strikes DataFrame, info dict). No outcomes included."""
    conn = sqlite3.connect(TOT)
    mk = pd.read_sql("SELECT ticker,event_ticker,tier,status,strike_type,floor_strike,open_ts,own_occ_ts,volume "
                     "FROM markets", conn)
    cd = pd.read_sql("SELECT ticker,end_ts,bid,ask,volume FROM candles", conn)
    d = sqlite3.connect(f"file:{DRIFT.as_posix()}?mode=ro", uri=True)
    start = {et.split("-", 1)[1]: occ - 3 * 3600 for et, occ in d.execute(
        "SELECT event_ticker, MIN(occurrence_ts) FROM markets GROUP BY event_ticker")}
    rat = ratings_tables()
    hoopr = {"2025-26": hoopr_games(2026)}
    info = {"markets": len(mk), "events": int(mk.event_ticker.nunique()), "unparsed_events": [], "no_T": 0,
            "T_from_drift": 0, "T_from_own": 0, "unmatched_hoopr": [], "unrated": [], "no_ratings_team": []}
    # last candle per market ending at or before T-60m (selection done again here, never later)
    cdg = {t: g for t, g in cd.groupby("ticker")}
    rows, srows = [], []
    for et, g in mk.groupby("event_ticker"):
        pe = lib.parse_event(et)
        if pe is None:
            info["unparsed_events"].append(et)
            continue
        date, away, home = pe
        key = et.split("-", 1)[1]
        if key in start:
            T, tsrc = start[key], "drift"
            info["T_from_drift"] += 1
        else:
            own = g.own_occ_ts.dropna()
            if own.empty:
                info["no_T"] += 1
                continue
            T, tsrc = int(own.min()) - 3 * 3600, "own"
            info["T_from_own"] += 1
        season = "2025-26" if T < SEASON_SPLIT_TS else "2026-27"
        rt = rat["2024-25"] if season == "2025-26" else rat["2025-26"]
        if away not in rt or home not in rt:
            info["unrated"].append(et)
            continue
        hid, stype, hdate, tip = None, None, None, None
        if season == "2025-26":
            hm = match_hoopr(hoopr["2025-26"], date, away, home)
            if hm is None:
                info["unmatched_hoopr"].append(et)
                continue
            hid, stype, hdate = int(hm.game_id), int(hm.season_type), hm.game_date
            tip = int(hm.tip)
            T, tag = schedule_T(T, tip)
            if tag:
                tsrc = f"{tsrc}->{tag}"
                info["T_moved_to_hoopr_tip"] = info.get("T_moved_to_hoopr_tip", []) + [et]
        a, b, proj = lib.project_total(rt[away], rt[home])
        pts = []
        for r in g.itertuples():
            cs = cdg.get(r.ticker)
            c = None
            if cs is not None:
                c = lib.pick_candle([{"end_period_ts": int(x.end_ts), "bid": x.bid, "ask": x.ask, "volume": x.volume}
                                     for x in cs.itertuples()], T)
            q = lib.valid_quote(c["bid"], c["ask"]) if c else None
            if q:
                assert_candle_before_tip(c["end_period_ts"], tip if tip is not None else T)
            srows.append({"event": et, "ticker": r.ticker, "strike": r.floor_strike, "strike_type": r.strike_type,
                          "candle_end": c["end_period_ts"] if c else None, "has_candle": c is not None,
                          "bid": q[0] if q else None, "ask": q[1] if q else None,
                          "mid": (q[0] + q[1]) / 2 if q else None, "cvol": c["volume"] if c else None})
            if q:
                pts.append((r.floor_strike, (q[0] + q[1]) / 2))
        rows.append({"event": et, "date": date, "away": away, "home": home, "T": T, "T_src": tsrc, "season": season,
                     "hoopr_id": hid, "tip": tip, "hoopr_date": hdate, "season_type": stype, "pts_away": a, "pts_home": b,
                     "proj": proj, "n_markets": len(g), "n_quoted": len(pts), "median": lib.market_median(pts)})
    games = pd.DataFrame(rows).sort_values("T").reset_index(drop=True)
    strikes = pd.DataFrame(srows)
    # split: 2025-26 universe sorted by scheduled start; first half discovery
    u = games[games.season == "2025-26"].index
    half = len(u) // 2
    games["split"] = "forward"
    games.loc[u[:half], "split"] = "discovery"
    games.loc[u[half:], "split"] = "unseen"
    games["month"] = games["T"].map(lambda t: datetime.fromtimestamp(t, ET).strftime("%Y-%m"))
    games["stage"] = games.season_type.map({2: "regular", 3: "playoffs", 5: "play-in"})
    info["games"] = len(games)
    info["games_by_split"] = games.split.value_counts().to_dict()
    return games, strikes, info
