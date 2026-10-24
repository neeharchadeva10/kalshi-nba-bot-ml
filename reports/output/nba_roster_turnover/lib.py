"""Research log #45: early-season lines vs roster continuity.  Implements PREREGISTRATION.md exactly.
Pure functions are unit-tested in test_roster_turnover.py.  No network, no kpm.execution.
OddsData.sqlite is unlicensed private research data: never copied out of the repo, never dumped."""
from __future__ import annotations

import importlib.util
import itertools
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
from kpm.core.fees import FeeModel  # noqa: E402

BOX = ROOT / "data" / "raw" / "nba_open"
ODDS_DB = ROOT / "data" / "raw" / "nba_odds_samples" / "OddsData.sqlite"
TOTALS_PACE = ROOT / "reports" / "output" / "nba_totals_pace"
SEED = 20261022
N_LUCK = 500
WIN_NET = 10.0 / 11.0          # -110 pricing: +0.9091 per $1 risked on a win
LOSS_NET = -1.0
MIN_N = 40
FEE = FeeModel(0.07, 0.25, "micro_ceil")   # Kalshi: round the order fee UP to $0.000001

DISCOVERY = (2023, 2024)       # season label = end year (2022-23, 2023-24)
UNSEEN = (2025, 2026)          # 2024-25, 2025-26
ALL_SEASONS = DISCOVERY + UNSEEN
ODDS_TABLE = {2023: "odds_2022-23_new", 2024: "2023-24", 2025: "2024-25", 2026: "odds_2025-26"}

# odds-file team names -> hoopR abbreviation (team_id is stable across seasons; checked at run time)
NAME_TO_ABBR = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GS", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NO",
    "New York Knicks": "NY", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SA",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTAH", "Washington Wizards": "WSH"}
REAL_TEAM_IDS = set(range(1, 31))   # hoopR/ESPN ids of the 30 franchises (All-Star teams have ids > 100000)


# ----------------------------------------------------------------------------------------------- continuity
def continuity_for_team(prev_minutes: pd.DataFrame, first3_players) -> dict:
    """prev_minutes: rows (athlete_id, minutes) = the team's player-game rows of season s-1 (a traded player's rows
    for OTHER teams are simply not in this frame, so minutes count for the team they were played for).
    first3_players: athlete_ids who played (minutes > 0) for the team in its first 3 games of season s.
    Continuity = minutes share of season s-1 played by players who are in first3_players."""
    m = prev_minutes.assign(minutes=prev_minutes.minutes.fillna(0.0))
    total = float(m.minutes.sum())
    keep = set(first3_players)
    kept = float(m[m.athlete_id.isin(keep)].minutes.sum())
    return {"prev_minutes": total, "retained_minutes": kept, "continuity": kept / total if total > 0 else float("nan")}


def first_n_games(team_games: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    """team_games: rows (game_id, tip) of ONE team, one season, regular season. The n earliest by tip (ties by id)."""
    return team_games.sort_values(["tip", "game_id"]).head(n)


def assert_no_lookahead(bet_tip, first3_tips) -> None:
    """A bet may use a team's continuity only if the bet's game starts strictly after that team's first 3 games."""
    if not bet_tip > max(first3_tips):
        raise AssertionError(f"look-ahead: bet at {bet_tip} is not after first-3 games ending {max(first3_tips)}")


def rank_flags(cont: dict, share: float) -> dict:
    """cont: {team: continuity} for the 30 teams. Low = the k lowest by continuity, k = round(share * n)
    (20% of 30 = 6, 33% of 30 = 9.9 -> 10).  Ties broken by team key (deterministic)."""
    k = int(round(share * len(cont)))
    order = sorted(cont, key=lambda t: (cont[t], str(t)))
    low = set(order[:k])
    return {t: (t in low) for t in cont}


def tercile_of(cont: dict) -> dict:
    """0 = lowest third of teams by continuity (10 of 30), 1 = middle, 2 = highest."""
    order = sorted(cont, key=lambda t: (cont[t], str(t)))
    n = len(order)
    return {t: min(2, i * 3 // n) for i, t in enumerate(order)}


# ----------------------------------------------------------------------------------------------- data loading
def to_et_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True).dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")


def load_player_rows(year: int) -> pd.DataFrame:
    p = pd.read_parquet(BOX / f"player_box_{year}.parquet",
                        columns=["game_id", "season", "season_type", "athlete_id", "team_id", "minutes",
                                 "did_not_play"])
    p = p[(p.season_type == 2) & p.team_id.isin(REAL_TEAM_IDS)].copy()
    p["minutes"] = p.minutes.fillna(0.0)
    p.loc[p.did_not_play.fillna(False).astype(bool), "minutes"] = 0.0
    return p


def load_team_games(year: int) -> pd.DataFrame:
    """One row per (game, team), regular season, 30 real teams. Columns incl. ET date, tip (UTC epoch), scores."""
    t = pd.read_parquet(BOX / f"team_box_{year}.parquet",
                        columns=["game_id", "season", "season_type", "game_date_time", "team_id",
                                 "team_abbreviation", "team_home_away", "team_score", "opponent_team_id",
                                 "opponent_team_score"])
    t = t[(t.season_type == 2) & t.team_id.isin(REAL_TEAM_IDS) & t.opponent_team_id.isin(REAL_TEAM_IDS)].copy()
    ts = pd.to_datetime(t.game_date_time, utc=True)
    t["tip"] = (ts - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().astype("int64")
    t["date"] = to_et_date(t.game_date_time)
    return t.drop(columns=["game_date_time"]).reset_index(drop=True)


def build_continuity(year: int, prev_rows: pd.DataFrame, rows: pd.DataFrame, tg: pd.DataFrame):
    """Continuity per team for season `year` (end-year label). Returns (DataFrame, first3 dict {team_id: set(game_id)},
    first3_tip {team_id: max tip of the first 3 games})."""
    out, first3, f3tip = [], {}, {}
    prev_by_team = {t: g for t, g in prev_rows.groupby("team_id")}
    for team_id, g in tg.groupby("team_id"):
        f3 = first_n_games(g[["game_id", "tip"]], 3)
        first3[team_id] = set(f3.game_id)
        f3tip[team_id] = int(f3.tip.max())
        who = rows[(rows.team_id == team_id) & rows.game_id.isin(first3[team_id]) & (rows.minutes > 0)].athlete_id
        c = continuity_for_team(prev_by_team[team_id][["athlete_id", "minutes"]], set(who))
        out.append({"season_end": year, "season": f"{year - 1}-{str(year)[2:]}", "team_id": int(team_id),
                    "abbr": g.team_abbreviation.iloc[0], "n_players_first3": int(len(set(who))),
                    "first3_last_date": g.sort_values("tip").date.iloc[min(2, len(g) - 1)], **c})
    df = pd.DataFrame(out).sort_values("continuity").reset_index(drop=True)
    df["rank_low_to_high"] = np.arange(1, len(df) + 1)
    return df, first3, f3tip


def load_odds(year: int) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{ODDS_DB.as_posix()}?mode=ro", uri=True)
    try:
        d = pd.read_sql(f'SELECT Date,Home,Away,OU,Spread,ML_Home,ML_Away,Points,Win_Margin FROM "{ODDS_TABLE[year]}"',
                        con)
    finally:
        con.close()
    d["Date"] = d.Date.astype(str)
    d["home"] = d.Home.map(NAME_TO_ABBR)
    d["away"] = d.Away.map(NAME_TO_ABBR)
    if d.home.isna().any() or d.away.isna().any():
        raise ValueError("unmapped odds team name(s)")
    return d


def match_odds_to_hoopr(odds: pd.DataFrame, tg: pd.DataFrame):
    """Match each odds row to a regular-season hoopR game by (ET date, home, away); fall back to +/-1 day.
    Returns (matched DataFrame with game_id, n_exact, n_shifted, unmatched odds rows)."""
    home = tg[tg.team_home_away == "home"][["game_id", "date", "tip", "team_abbreviation", "opponent_team_id",
                                            "team_id"]].rename(columns={"team_abbreviation": "home", "team_id": "home_id"})
    away_ab = tg[tg.team_home_away == "away"][["game_id", "team_abbreviation", "team_id"]].rename(
        columns={"team_abbreviation": "away", "team_id": "away_id"})
    g = home.merge(away_ab, on="game_id")
    idx = {(r.date, r.home, r.away): r for r in g.itertuples()}
    rows, exact, shifted, un = [], 0, 0, []
    used = set()
    for r in odds.itertuples():
        d0 = pd.Timestamp(r.Date)
        hit = None
        for k in (0, -1, 1):
            key = ((d0 + pd.Timedelta(days=k)).strftime("%Y-%m-%d"), r.home, r.away)
            if key in idx and idx[key].game_id not in used:
                hit = idx[key]
                if k == 0:
                    exact += 1
                else:
                    shifted += 1
                break
        if hit is None:
            un.append(r)
            continue
        used.add(hit.game_id)
        rows.append({"game_id": hit.game_id, "date": hit.date, "tip": hit.tip, "home": r.home, "away": r.away,
                     "home_id": hit.home_id, "away_id": hit.away_id, "OU": r.OU, "Spread": r.Spread,
                     "ML_Home": r.ML_Home, "ML_Away": r.ML_Away, "Points": r.Points, "Win_Margin": r.Win_Margin})
    return pd.DataFrame(rows), exact, shifted, pd.DataFrame(un)


def build_games(year: int, cont: pd.DataFrame, first3tip: dict, matched: pd.DataFrame,
                season_start: str) -> pd.DataFrame:
    """Per-game table with continuity flags. No outcome logic here (outcomes are columns Points/Win_Margin)."""
    c = cont.set_index("team_id").continuity.to_dict()
    low20 = rank_flags(c, 0.20)
    low33 = rank_flags(c, 0.33)
    terc = tercile_of(c)
    g = matched.copy()
    g["season_end"] = year
    g["cont_home"] = g.home_id.map(c)
    g["cont_away"] = g.away_id.map(c)
    for nm, fl in (("low20", low20), ("low33", low33)):
        g[f"{nm}_home"] = g.home_id.map(fl)
        g[f"{nm}_away"] = g.away_id.map(fl)
    g["terc_home"] = g.home_id.map(terc)
    g["terc_away"] = g.away_id.map(terc)
    g["day_idx"] = (pd.to_datetime(g.date) - pd.Timestamp(season_start)).dt.days
    # bet allowed only if the game is AFTER the first 3 games of BOTH teams (their rosters define continuity)
    g["f3_home"] = g.home_id.map(first3tip)
    g["f3_away"] = g.away_id.map(first3tip)
    g["excluded_first3"] = ~((g.tip > g.f3_home) & (g.tip > g.f3_away))
    for r in g[~g.excluded_first3].itertuples():
        assert_no_lookahead(r.tip, [r.f3_home, r.f3_away])
    return g.sort_values(["tip", "game_id"]).reset_index(drop=True)


# ----------------------------------------------------------------------------------------------- versions
def version_grid():
    """market x direction x threshold x window x involvement = 2*2*2*2*2 = 32."""
    dirs = {"spread": ("fade", "back"), "total": ("under", "over")}
    out = []
    for market in ("spread", "total"):
        for d in dirs[market]:
            for thr in (20, 33):
                for win in (14, 30):
                    for inv in ("low_in_game", "opp_not_low"):
                        out.append({"market": market, "dir": d, "thr": thr, "win": win, "inv": inv})
    return out


def vname(v):
    return f"{v['market']}-{v['dir']}-low{v['thr']}-d{v['win']}-{v['inv']}"


def sign_outcomes(g: pd.DataFrame):
    """Reference results: +1 home covers / goes over, -1 away covers / under, 0 push."""
    spread_ref = np.sign(g.Win_Margin.values - g.Spread.values)
    total_ref = np.sign(g.Points.values - g.OU.values)
    return spread_ref, total_ref


def version_sides(g: pd.DataFrame, v: dict):
    """Returns (index array of games in the bet set, side array +1/-1 in the reference orientation)."""
    lh = g[f"low{v['thr']}_home"].values.astype(bool)
    la = g[f"low{v['thr']}_away"].values.astype(bool)
    nl = lh.astype(int) + la.astype(int)
    base = (~g.excluded_first3.values) & (g.day_idx.values < v["win"])
    if v["market"] == "spread":
        # exactly one low team (both-low games have no single "low team": excluded, counted in the report)
        inc = base & (nl == 1)
        low_home = lh
        back_side = np.where(low_home, 1, -1)           # back the low team: home cover=+1 else away cover=-1
        side = back_side if v["dir"] == "back" else -back_side
    else:
        if v["inv"] == "opp_not_low":
            inc = base & (nl == 1)
        else:
            inc = base & (nl >= 1)
        side = np.full(len(g), 1 if v["dir"] == "over" else -1)
    idx = np.flatnonzero(inc)
    return idx, side[idx]


def nets(ref: np.ndarray, side: np.ndarray):
    """-110 settlement: win +10/11, loss -1, push (ref == 0) refunded and excluded. Returns net array (pushes dropped)."""
    r = ref * side
    r = r[ref != 0]
    return np.where(r > 0, WIN_NET, LOSS_NET)


def stats(x: np.ndarray) -> dict:
    n = len(x)
    if n == 0:
        return {"n": 0, "win_pct": float("nan"), "mean": float("nan"), "sd": float("nan"), "lb": float("-inf")}
    mean = float(np.mean(x))
    sd = float(np.std(x, ddof=1)) if n > 1 else float("nan")
    lb = mean - 1.96 * sd / math.sqrt(n) if n > 1 else float("-inf")
    return {"n": n, "win_pct": float((x > 0).mean()), "mean": mean, "sd": sd, "lb": lb}


def evaluate_versions(g: pd.DataFrame, grid=None):
    grid = grid or version_grid()
    s_ref, t_ref = sign_outcomes(g)
    rows = []
    for v in grid:
        idx, side = version_sides(g, v)
        ref = (s_ref if v["market"] == "spread" else t_ref)[idx]
        rows.append({"version": vname(v), **v, "n_games_in_set": int(len(idx)), **stats(nets(ref, side))})
    return pd.DataFrame(rows)


def select_best(df: pd.DataFrame, min_n: int = MIN_N):
    """Highest LB among versions with n >= min_n; ties -> first in grid order. None if none qualifies."""
    ok = df[df.n >= min_n]
    if ok.empty:
        return None
    return ok.loc[ok.lb.idxmax()]   # idxmax returns the first max


def luck_test(g: pd.DataFrame, reps: int = N_LUCK, seed: int = SEED, grid=None, min_n: int = MIN_N):
    """Shared random sides: each rep gives every game ONE random side (+1 home/over, -1 away/under) used by all 32
    versions; the same selection is re-run. Returns (best LB per rep array (nan if none qualifies), real best LB)."""
    grid = grid or version_grid()
    real = select_best(evaluate_versions(g, grid), min_n)
    real_lb = float(real.lb) if real is not None else float("-inf")
    rng = np.random.default_rng(seed)
    signs = rng.integers(0, 2, size=(reps, len(g))) * 2 - 1
    s_ref, t_ref = sign_outcomes(g)
    best = np.full(reps, -np.inf)
    for v in grid:
        idx, _ = version_sides(g, v)
        ref = (s_ref if v["market"] == "spread" else t_ref)[idx]
        nz = ref != 0
        ref = ref[nz]
        sg = signs[:, idx][:, nz]                       # reps x n
        win = (sg * ref[None, :]) > 0
        n = win.shape[1]
        if n < min_n:
            continue
        w = win.sum(axis=1)
        mean = (w * WIN_NET - (n - w)) / n
        # sd of a two-point variable with ddof=1
        p = w / n
        var = (n / (n - 1)) * (p * (WIN_NET - mean) ** 2 + (1 - p) * (LOSS_NET - mean) ** 2)
        lb = mean - 1.96 * np.sqrt(var) / math.sqrt(n)
        best = np.maximum(best, lb)
    return best, real_lb


def luck_share(best: np.ndarray, real_lb: float) -> float:
    return float(np.mean(best >= real_lb))


# ----------------------------------------------------------------------------------------------- unseen helpers
def boot_lb(x, n=10000, seed=SEED, alpha=0.05):
    x = np.asarray(x, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), size=(n, len(x)))
    means = x[idx].mean(axis=1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def coin_flip(g: pd.DataFrame, v: dict, real_mean: float, reps: int = N_LUCK, seed: int = SEED) -> float:
    """Random sides on the same games of version v; share of copies whose mean net >= the real mean."""
    idx, _ = version_sides(g, v)
    s_ref, t_ref = sign_outcomes(g)
    ref = (s_ref if v["market"] == "spread" else t_ref)[idx]
    ref = ref[ref != 0]
    rng = np.random.default_rng(seed)
    sg = rng.integers(0, 2, size=(reps, len(ref))) * 2 - 1
    w = ((sg * ref[None, :]) > 0).sum(axis=1)
    means = (w * WIN_NET - (len(ref) - w)) / len(ref)
    return float(np.mean(means >= real_mean - 1e-12))


def kalshi_fee(price: float, count: int = 1) -> float:
    return FEE.per_contract(price, count)


def kalshi_net(side: str, bid: float, ask: float, yes_result: bool, count: int = 1) -> float:
    """Taker net $ per contract: over = buy YES at ask; under = buy NO at 1 - bid. Fee 0.07*P*(1-P) rounded up
    per order. Hold to settlement."""
    price = ask if side == "over" else 1.0 - bid
    win = yes_result if side == "over" else (not yes_result)
    return (1.0 if win else 0.0) - price - kalshi_fee(price, count)


def main_strike(strikes: pd.DataFrame):
    """The quoted strike whose mid is closest to 0.5 (ties -> lowest strike). strikes: rows strike,bid,ask,mid."""
    q = strikes.dropna(subset=["bid", "ask"]).copy()
    if q.empty:
        return None
    q["d"] = (q.mid - 0.5).abs()
    return q.sort_values(["d", "strike"]).iloc[0]


def load_totals_prep():
    """Import the #37 study's prep.py (it imports its own sibling 'lib')."""
    spec_l = importlib.util.spec_from_file_location("lib", TOTALS_PACE / "lib.py")
    mod_l = importlib.util.module_from_spec(spec_l)
    old = sys.modules.get("lib")
    sys.modules["lib"] = mod_l
    spec_l.loader.exec_module(mod_l)
    spec_p = importlib.util.spec_from_file_location("totals_prep", TOTALS_PACE / "prep.py")
    mod_p = importlib.util.module_from_spec(spec_p)
    spec_p.loader.exec_module(mod_p)
    if old is not None:
        sys.modules["lib"] = old
    return mod_p
