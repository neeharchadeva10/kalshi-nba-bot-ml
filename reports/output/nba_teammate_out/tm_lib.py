"""Research log #48 (reports/output/nba_teammate_out/PREREGISTRATION.md): library.

Pure functions (testable) first, then data loaders.  Read-only on all inputs.  No kpm.execution, no keys.
"""
from __future__ import annotations

import bisect
import csv
import gzip
import json
import math
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PROPS_DB = ROOT / "reports" / "output" / "nba_props" / "nba_props.sqlite"
DRIFT_DB = ROOT / "data" / "nba_drift.sqlite"
BOX_DIR = ROOT / "data" / "raw" / "nba_open"
INJ_DIR = ROOT / "data" / "raw" / "nba_injury"
PROBE_CACHE = INJ_DIR / "_probe_cache_v3.json"
PARSED_V3 = ROOT / "reports" / "output" / "nba_model" / "injury_parsed_v3.json"
PARSED_TM = HERE / "injury_parsed_tm.jsonl"
CANDLE_DIR = ROOT / "data" / "raw" / "kalshi_props_hourly"
SELECTION_CSV = HERE / "selection.csv"

DISC_LO, DISC_HI = "2025-11-18", "2026-01-22"
UNSEEN_LO = "2026-01-23"
SEED = 20261022
SLIP = 0.01
MAX_SPREAD = 0.10
PX_LO, PX_HI = 0.15, 0.85
STATS = ["PTS", "REB", "AST"]
STAT_IDX = {"PTS": 4, "REB": 5, "AST": 6}
STAR_MINS = [28, 32]
BENS = ["top1", "rotation"]
TIMINGS = ["any", "late"]
VERSIONS = [(s, m, b, t) for s in STATS for m in STAR_MINS for b in BENS for t in TIMINGS]   # 3x2x2x2 = 24
MIN_N = 60
LATE_WINDOW_S = 3 * 3600

ESPN2CODE = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}
CODES = {"ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL",
         "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS"}
TEAMNAME2CODE = {
    "AtlantaHawks": "ATL", "BrooklynNets": "BKN", "BostonCeltics": "BOS", "CharlotteHornets": "CHA", "ChicagoBulls": "CHI",
    "ClevelandCavaliers": "CLE", "DallasMavericks": "DAL", "DenverNuggets": "DEN", "DetroitPistons": "DET",
    "GoldenStateWarriors": "GSW", "HoustonRockets": "HOU", "IndianaPacers": "IND", "LAClippers": "LAC",
    "LosAngelesClippers": "LAC", "LosAngelesLakers": "LAL", "MemphisGrizzlies": "MEM", "MiamiHeat": "MIA",
    "MilwaukeeBucks": "MIL", "MinnesotaTimberwolves": "MIN", "NewOrleansPelicans": "NOP", "NewYorkKnicks": "NYK",
    "OklahomaCityThunder": "OKC", "OrlandoMagic": "ORL", "Philadelphia76ers": "PHI", "PhoenixSuns": "PHX",
    "PortlandTrailBlazers": "POR", "SacramentoKings": "SAC", "SanAntonioSpurs": "SAS", "TorontoRaptors": "TOR",
    "UtahJazz": "UTA", "WashingtonWizards": "WAS"}
SUFFIXES = ("jr", "sr", "ii", "iii", "iv", "v")


# =================================================================== pure helpers
def code_of(abbr: str) -> str:
    return ESPN2CODE.get(abbr, abbr)


def norm_name(display: str) -> str:
    s = unicodedata.normalize("NFKD", display or "").encode("ascii", "ignore").decode().lower()
    toks = [t for t in re.split(r"\s+", s.strip()) if t]
    while len(toks) > 2 and re.sub(r"[^a-z]", "", toks[-1]) in SUFFIXES:
        toks.pop()
    return re.sub(r"[^a-z]", "", "".join(toks))


def norm_injury_name(raw: str) -> str:
    """'PippenJr.,Scotty' -> 'scottypippen'."""
    if "," in raw:
        last, first = raw.split(",", 1)
    else:
        last, first = raw, ""
    last_c = unicodedata.normalize("NFKD", last).encode("ascii", "ignore").decode()
    m = re.match(r"^(.*?)(Jr|Sr|II|III|IV)\.?$", last_c)
    if m and len(m.group(1)) > 2:
        last_c = m.group(1)
    return re.sub(r"[^a-z]", "", (first + last_c).lower().replace("jr.", ""))


def taker_fee(p: float) -> float:
    """Kalshi taker fee for ONE contract at price p (dollars): 0.07*p*(1-p), rounded UP to $0.0001."""
    return math.ceil(round(0.07 * p * (1.0 - p) * 10000.0, 6)) / 10000.0


def bet_net(settle: float, ask: float):
    """Buy 1 YES contract at the ask + 1c slippage, hold to settlement.
    Net = settlement value - ask - 1c - fee, with the fee charged at the fill price (ask + 1c).  -> (net, fee)."""
    fill = ask + SLIP
    fee = taker_fee(fill)
    return settle - ask - SLIP - fee, fee


def settlement_value(result, settlement_value_dollars):
    """yes -> 1, no -> 0, scalar (active player who never took the court) -> the market's settlement value.
    -> (value, is_nonbinary).  Raises ValueError for an unusable record (caller counts it as skipped)."""
    if result == "yes":
        return 1.0, False
    if result == "no":
        return 0.0, False
    if result == "scalar":
        if settlement_value_dollars in (None, ""):
            raise ValueError("scalar result without settlement_value_dollars")
        v = float(settlement_value_dollars)
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"settlement value out of range: {v}")
        return v, True
    raise ValueError(f"unknown result {result!r}")


def valid_quote(bid, ask) -> bool:
    return (bid is not None and ask is not None and bid == bid and ask == ask and bid > 0 and ask < 1 and bid <= ask)


def pre_game_mean(games, date_iso: str, n: int = 10, min_games: int = 5):
    """games: list of (date_iso, value) sorted by date, PLAYED games.  Mean of the last n games with date STRICTLY before
    date_iso.  -> (mean, k) or (None, k) if fewer than min_games."""
    ds = [g[0] for g in games]
    i = bisect.bisect_left(ds, date_iso)             # games[:i] have date < date_iso
    last = games[max(0, i - n):i]
    if len(last) < min_games:
        return None, len(last)
    return sum(v for _, v in last) / len(last), len(last)


def select_strikes(strikes, mean: float, k: int = 2):
    """strikes: iterable of (floor_strike, ticker).  The k nearest the mean (|floor_strike - mean|); ties -> lower strike."""
    return sorted(strikes, key=lambda s: (abs(s[0] - mean), s[0], s[1]))[:k]


def decision_candle(candles, d_ts: float):
    """candles: [(end_ts, bid, ask, ...)] -> the last candle with end_ts <= d_ts (the decision time T-1h), else None."""
    best = None
    for c in candles:
        if c[0] <= d_ts and (best is None or c[0] > best[0]):
            best = c
    return best


def classify_quote(c):
    """-> (ok, reason, bid, ask, mid, spread)"""
    if c is None:
        return False, "no_candle", None, None, None, None
    bid, ask = c[1], c[2]
    if not valid_quote(bid, ask):
        return False, "invalid_quote", bid, ask, None, None
    mid, sp = (bid + ask) / 2.0, ask - bid
    if not (PX_LO - 1e-9 <= mid <= PX_HI + 1e-9):
        return False, "price_out_of_range", bid, ask, mid, sp
    if sp > MAX_SPREAD + 1e-9:
        return False, "spread_too_wide", bid, ask, mid, sp
    return True, "ok", bid, ask, mid, sp


def pick_strike_quote(cands):
    """cands: list of dicts {strike, ticker, candle}.  The strike whose mid is nearest 0.50 among those passing the filters
    (15-85c mid, spread <= 10c, valid quote).  Ties -> lower strike.  -> (chosen dict | None, reason)."""
    ok, reasons = [], []
    for c in cands:
        good, why, bid, ask, mid, sp = classify_quote(c.get("candle"))
        reasons.append(why)
        if good:
            ok.append((round(abs(mid - 0.5), 9), c["strike"], c["ticker"], c, bid, ask, mid))
    if ok:
        ok.sort(key=lambda x: x[:3])
        _, _, _, c, bid, ask, mid = ok[0]
        return dict(c, bid=bid, ask=ask, mid=mid), "ok"
    for r in ("spread_too_wide", "price_out_of_range", "invalid_quote", "no_candle"):
        if r in reasons:
            return None, r
    return None, "no_market"


def star_flags(team_window_min, played_last3, star_min):
    """Star test: avg minutes over the team's last 10 games (games he missed count 0 min) >= star_min AND he played in at
    least 1 of the team's previous 3 games (so a long-term absence, already priced, is excluded)."""
    return team_window_min >= star_min and played_last3 >= 1


def first_out_late(first_out_created, d_ts, window_s=LATE_WINDOW_S):
    """'Late news': the first report listing him Out was published within 3 h before the decision time d_ts = T-1h
    (strictly after d_ts-3h, at or before d_ts)."""
    return first_out_created is not None and d_ts - window_s < first_out_created <= d_ts


def pick_beneficiaries(active, kind):
    """active: list of dicts {aid, avg_min, usage_pm} of ACTIVE teammates.  Only avg_min >= 20 qualify.
    top1: highest usage per minute (ties: more minutes, then lower aid).  rotation: all of them."""
    elig = [a for a in active if a["avg_min"] >= 20.0]
    if kind == "rotation":
        return sorted(elig, key=lambda a: a["aid"])
    if not elig:
        return []
    return [sorted(elig, key=lambda a: (-a["usage_pm"], -a["avg_min"], a["aid"]))[0]]


def mean_lb(nets):
    n = len(nets)
    if n == 0:
        return dict(n=0, mean=None, sd=None, lb=None)
    a = np.asarray(nets, float)
    m = float(a.mean())
    if n < 2:
        return dict(n=n, mean=m, sd=None, lb=None)
    sd = float(a.std(ddof=1))
    return dict(n=n, mean=m, sd=sd, lb=m - 1.96 * sd / math.sqrt(n))


def select_version(stats_by_version, min_n=MIN_N):
    """Highest LB among versions with n >= min_n.  -> version or None."""
    best, bl = None, -math.inf
    for v in VERSIONS:
        s = stats_by_version.get(v)
        if s is None or s["n"] < min_n or s["lb"] is None or s["lb"] != s["lb"]:
            continue
        if s["lb"] > bl:
            best, bl = v, s["lb"]
    return best


def luck_candidates(real_bets, pool, tol=0.02):
    """real_bets: {key: dict(night, stat, ask, player)}.  pool: list of dict(night, stat, ask, player, net).
    For each real bet: pool bets on the same night and stat, ANOTHER player, with ask within tol (2c).
    -> (cand {key: [pool idx]}, n_fallback, n_empty).  If none lies within tol, the nearest-priced ones are used (counted as
    fallback); if the night/stat has no other player at all the bet keeps its own net (counted as empty)."""
    idx = defaultdict(list)
    for i, p in enumerate(pool):
        idx[(p["night"], p["stat"])].append(i)
    cand, n_fb, n_empty = {}, 0, 0
    for k in sorted(real_bets):
        b = real_bets[k]
        others = [i for i in idx.get((b["night"], b["stat"]), []) if pool[i]["player"] != b["player"]]
        if not others:
            cand[k] = []
            n_empty += 1
            continue
        near = [i for i in others if abs(pool[i]["ask"] - b["ask"]) <= tol + 1e-9]
        if not near:
            dmin = min(abs(pool[i]["ask"] - b["ask"]) for i in others)
            near = [i for i in others if abs(pool[i]["ask"] - b["ask"]) <= dmin + 1e-9]
            n_fb += 1
        cand[k] = near
    return cand, n_fb, n_empty


def luck_test(version_keys, real_nets, cand, pool_nets, reps=500, seed=SEED, min_n=MIN_N):
    """version_keys: {version: [bet key,...]}; real_nets: {key: net}; cand: {key: [pool idx]}; pool_nets: sequence.
    Each rep replaces EVERY distinct real bet (shared across versions) by one random candidate, then re-runs the 24-version
    selection (best LB among n >= min_n).  -> dict(real_best_lb, share, rep_best_lbs).  Deterministic given seed."""
    rng = np.random.default_rng(seed)
    pool_nets = np.asarray(pool_nets, float)
    keys = sorted({k for ks in version_keys.values() for k in ks})
    kix = {k: i for i, k in enumerate(keys)}
    ncand = np.array([len(cand.get(k, [])) for k in keys])
    base = np.array([real_nets[k] for k in keys], float)
    real_stats = {v: mean_lb([real_nets[k] for k in ks]) for v, ks in version_keys.items()}
    sel = select_version(real_stats, min_n)
    real_best = real_stats[sel]["lb"] if sel else None
    elig = [v for v in VERSIONS if v in version_keys and len(version_keys[v]) >= min_n]
    vix = {v: np.array([kix[k] for k in version_keys[v]]) for v in elig}
    best_lbs = []
    for _ in range(reps):
        nets = base.copy()
        r = rng.random(len(keys))
        for j, k in enumerate(keys):
            if ncand[j]:
                nets[j] = pool_nets[cand[k][int(r[j] * ncand[j])]]
        bl = -math.inf
        for v in elig:
            x = nets[vix[v]]
            lb = x.mean() - 1.96 * x.std(ddof=1) / math.sqrt(len(x))
            bl = max(bl, lb)
        best_lbs.append(float(bl) if elig else None)
    if real_best is None or not elig:
        return dict(real_best_lb=real_best, share=None, rep_best_lbs=best_lbs)
    share = sum(1 for b in best_lbs if b >= real_best) / reps
    return dict(real_best_lb=real_best, share=share, rep_best_lbs=best_lbs)


def bootstrap_lb(nets_by_game, draws=10000, seed=SEED):
    """Bootstrap by game (resample games with replacement; statistic = mean net per bet).  -> (mean, 2.5th percentile)."""
    games = sorted(nets_by_game)
    sums = np.array([sum(nets_by_game[g]) for g in games], float)
    cnt = np.array([len(nets_by_game[g]) for g in games], float)
    rng = np.random.default_rng(seed)
    ix = rng.integers(0, len(games), size=(draws, len(games)))
    means = sums[ix].sum(1) / cnt[ix].sum(1)
    return float(sums.sum() / cnt.sum()), float(np.percentile(means, 2.5))


# =================================================================== game / tip helpers
def parse_event(ev):
    m = re.match(r"^(KXNBA[A-Z0-9]+)-(\d{2})([A-Z]{3})(\d{2})([A-Z0-9]+)$", ev or "")
    mon = {m_: i + 1 for i, m_ in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
    if not m or m.group(3) not in mon:
        return None
    from datetime import date
    return m.group(1), date(2000 + int(m.group(2)), mon[m.group(3)], int(m.group(4))), m.group(5)


def game_tips():
    out = {}
    if not DRIFT_DB.exists():
        return out
    c = sqlite3.connect(f"file:{DRIFT_DB.as_posix()}?mode=ro", uri=True)
    for ev, occ in c.execute("SELECT event_ticker, max(occurrence_ts) FROM markets GROUP BY event_ticker"):
        pe = parse_event(ev)
        if pe:
            out[(pe[1].isoformat(), pe[2])] = occ - 3 * 3600
    c.close()
    return out


def tip_of(game_date, game_code, occ_ts, exp_ts, tips):
    """KXNBAGAME occurrence - 3 h first (repo rule); else the prop's own occurrence - 3 h, else expected expiration - 3 h."""
    k = (game_date, game_code)
    if k in tips:
        return tips[k], "kxnbagame"
    if occ_ts is not None:
        return occ_ts - 3 * 3600, "prop_occurrence"
    if exp_ts is not None:
        return exp_ts - 3 * 3600, "prop_expected_exp"
    return None, None


def group_of(game_date: str, reg_end: str) -> int:
    """Download queue order: 0 = discovery, 1 = unseen regular season, 2 = after the regular season (play-in / playoffs)."""
    if game_date <= DISC_HI:
        return 0
    if game_date <= reg_end:
        return 1
    return 2


# =================================================================== candle cache (shared)
def candle_path(ticker: str, game_date: str) -> Path:
    return CANDLE_DIR / game_date / f"{ticker}.json.gz"


def parse_candles(resp):
    out = []
    for c in (resp or {}).get("candlesticks", []) or []:
        def g(side):
            v = (c.get(side) or {}).get("close")
            return float(v) if v not in (None, "") else None
        vol = c.get("volume")
        out.append((int(c["end_period_ts"]), g("yes_bid"), g("yes_ask"), float(vol) if vol not in (None, "") else 0.0))
    return out


def read_candles(ticker: str, game_date: str):
    """-> None if not downloaded; else list of (end_ts, bid, ask, volume) (possibly empty) from the cached raw response."""
    p = candle_path(ticker, game_date)
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        rec = json.load(fh)
    return parse_candles(rec.get("response"))


# =================================================================== box scores (lean)
class Box:
    """hoopR player_box 2025 (=2024-25) and 2026 (=2025-26).  Games are keyed by ET date.
    played[aid]        sorted [(date, gid, team, minutes, pts, reb, ast, fga, fta, tov, stype)]  (minutes > 0 only)
    team_games[team]   sorted [(date, gid)] of all the team's games (any row)
    team_min[(team, gid)] {aid: minutes};  roster[(team, 2026)] {norm_name: {aid}}."""

    def __init__(self, seasons=(2025, 2026)):
        import pandas as pd
        cols = ["game_id", "season_type", "game_date_time", "athlete_id", "athlete_display_name", "minutes",
                "field_goals_attempted", "free_throws_attempted", "turnovers", "rebounds", "assists", "points",
                "did_not_play", "team_abbreviation"]
        self.played = defaultdict(list)
        tg = defaultdict(set)
        self.team_min = defaultdict(dict)
        self.roster = defaultdict(lambda: defaultdict(set))
        self.names = {}
        self.reg_end = ""
        for yr in seasons:
            pb = pd.read_parquet(BOX_DIR / f"player_box_{yr}.parquet", columns=cols)
            pb["date"] = pd.to_datetime(pb["game_date_time"], utc=True).dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
            pb = pb.drop(columns=["game_date_time"])
            for r in pb.itertuples(index=False):
                team = code_of(r.team_abbreviation)
                if team not in CODES or r.athlete_id != r.athlete_id:
                    continue
                aid = int(r.athlete_id)
                gid = str(r.game_id)
                self.names[aid] = r.athlete_display_name
                if yr == 2026:
                    self.roster[(team, yr)][norm_name(r.athlete_display_name)].add(aid)
                tg[team].add((r.date, gid))
                mn = r.minutes
                if r.did_not_play or mn is None or mn != mn or mn <= 0:
                    continue

                def f(v):
                    return float(v) if v == v and v is not None else 0.0
                row = (r.date, gid, team, float(mn), f(r.points), f(r.rebounds), f(r.assists), f(r.field_goals_attempted),
                       f(r.free_throws_attempted), f(r.turnovers), int(r.season_type))
                self.played[aid].append(row)
                self.team_min[(team, gid)][aid] = float(mn)
                if yr == 2026 and int(r.season_type) == 2 and r.date > self.reg_end:
                    self.reg_end = r.date
            del pb
        for a in self.played:
            self.played[a].sort(key=lambda x: (x[0], x[1]))
        self.team_games = {t: sorted(s) for t, s in tg.items()}
        self.tg_dates = {t: [x[0] for x in g] for t, g in self.team_games.items()}
        self.actual = {}
        for a, rows in self.played.items():
            for x in rows:
                self.actual[(a, x[0])] = {"PTS": x[4], "REB": x[5], "AST": x[6]}

    def match_player(self, name: str, team: str):
        """Unique normalised-name match in the team's 2025-26 roster; fallback: unique last-name + first-initial.
        -> (aid | None, method)."""
        ro = self.roster.get((team, 2026), {})
        ids = ro.get(norm_name(name), set())
        if len(ids) == 1:
            return next(iter(ids)), "exact"
        if len(ids) > 1:
            return None, "ambiguous"
        s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower().strip()
        toks = [t for t in re.split(r"\s+", s) if t]
        if len(toks) >= 2:
            lastt = toks[-1]
            if re.sub(r"[^a-z]", "", lastt) in SUFFIXES and len(toks) > 2:
                lastt = toks[-2]
            last = re.sub(r"[^a-z]", "", lastt)
            fi = toks[0][:1]
            hits = {a for nm, ss in ro.items() for a in ss if nm.endswith(last) and self.names[a][:1].lower() == fi}
            if len(hits) == 1:
                return next(iter(hits)), "lastname_initial"
        return None, "none"

    def stat_games(self, aid, stat):
        i = STAT_IDX[stat]
        return [(x[0], x[i]) for x in self.played.get(aid, [])]

    def team_window(self, team, date_iso, n=10):
        """The team's last n games strictly before date_iso -> [(date, gid)]."""
        ds = self.tg_dates.get(team)
        if not ds:
            return []
        i = bisect.bisect_left(ds, date_iso)
        return self.team_games[team][max(0, i - n):i]

    def window_stats(self, aid, team, date_iso):
        """Over the team's last 10 games before date_iso:
        avg_min (games he missed count 0), played_last3, usage_pm (FGA+.44FTA+TOV per minute over games he played),
        avg_min_played, n_games, n_played.  None if the team has no earlier game."""
        w = self.team_window(team, date_iso, 10)
        if not w:
            return None
        mins = [self.team_min.get((team, gid), {}).get(aid, 0.0) for _, gid in w]
        pl = {x[1]: x for x in self.played.get(aid, [])}
        tot_m = sum(mins)
        use = sum(pl[gid][7] + 0.44 * pl[gid][8] + pl[gid][9] for (_, gid), m in zip(w, mins) if m > 0 and gid in pl)
        nplayed = sum(1 for m in mins if m > 0)
        return dict(avg_min=tot_m / len(w), played_last3=sum(1 for m in mins[-3:] if m > 0),
                    usage_pm=(use / tot_m) if tot_m > 0 else 0.0, avg_min_played=(tot_m / nplayed) if nplayed else 0.0,
                    n_games=len(w), n_played=nplayed)


# =================================================================== injury reports
class Reports:
    """Official reports with /CreationDate (probe cache); content from #32's parsed json + our own local parse."""

    def __init__(self):
        pc = json.loads(PROBE_CACHE.read_text())
        items = sorted((v, k) for k, v in pc.items() if v and v > 0 and k >= "Injury-Report_2025-10")
        self.created = [v for v, _ in items]
        self.names = [k for _, k in items]
        v3 = json.loads(PARSED_V3.read_text())
        self.content = {k: v for k, v in v3.items() if k >= "Injury-Report_2025-10" and "error" not in v}
        del v3
        if PARSED_TM.exists():
            for ln in PARSED_TM.read_text().splitlines():
                try:
                    r = json.loads(ln)
                except Exception:   # noqa: BLE001
                    continue
                if "error" not in r:
                    self.content[r["name"]] = r
        self.missing = sum(1 for n in self.names if n not in self.content)
        self._idx = {}

    def _game_rows(self, name, mdy, matchup):
        key = (name, mdy, matchup)
        if key not in self._idx:
            c = self.content.get(name)
            if c is None:
                self._idx[key] = None
            else:
                rows = [r for r in c["rows"] if r["date"] == mdy and r["matchup"] == matchup]
                nys = [x for x in c["nys"] if x[0] == mdy and x[1] == matchup]
                self._idx[key] = (rows, nys) if (rows or nys) else None
        return self._idx[key]

    def reports_before(self, d_ts, back_s=72 * 3600):
        """[(created, name)] with created in (d_ts - back_s, d_ts], oldest first.  Only reports published AT OR BEFORE d_ts."""
        hi = bisect.bisect_right(self.created, d_ts)
        lo = bisect.bisect_right(self.created, d_ts - back_s)
        return [(self.created[i], self.names[i]) for i in range(lo, hi)]

    def last_covering(self, d_ts, mdy, matchup):
        """The LAST report published at or before d_ts that has rows for this game.
        -> (created, name, rows, skipped_newer) or None.  skipped_newer = newer reports (<= d_ts) without this game."""
        skipped = 0
        for created, name in reversed(self.reports_before(d_ts)):
            g = self._game_rows(name, mdy, matchup)
            if g is not None:
                return created, name, g[0], skipped
            skipped += 1
        return None

    def first_out(self, d_ts, mdy, matchup, team, player_norm):
        """Earliest report (<= d_ts, within 72 h) listing this player Out for this game -> created ts or None."""
        for created, name in self.reports_before(d_ts):
            g = self._game_rows(name, mdy, matchup)
            if g is None:
                continue
            for r in g[0]:
                if r["status"] == "Out" and TEAMNAME2CODE.get(r["team"]) == team and norm_injury_name(r["player"]) == player_norm:
                    return created
        return None


def mdy_of(date_iso: str) -> str:
    y, m, d = date_iso.split("-")
    return f"{m}/{d}/{y}"


# =================================================================== selection (shared download scope)
def load_market_defs():
    """Streams (ticker, stat, game_date, game_code, team, player_key, player_name, floor_strike, result, occ_ts, exp_ts,
    settlement_value_dollars) for PTS/REB/AST."""
    conn = sqlite3.connect(f"file:{PROPS_DB.as_posix()}?mode=ro", uri=True)
    q = ("SELECT ticker, stat, game_date, game_code, team, player_key, player_name, floor_strike, result, occ_ts, exp_ts, "
         "json_extract(raw_json, '$.settlement_value_dollars') FROM mk WHERE stat IN ('PTS','REB','AST') "
         "ORDER BY game_date, ticker")
    for r in conn.execute(q):
        yield r
    conn.close()


def build_selection(box: Box):
    """-> (rows, report).  rows: the 2 selected strikes per player-stat-game.  Everything skipped is counted."""
    tips = game_tips()
    by_psg = defaultdict(list)
    pname_of = {}
    tip_by_game = {}
    for (t, stat, gd, gc, team, pk, pname, fs, res, occ, exp, sv) in load_market_defs():
        by_psg[(gd, gc, team, pk, stat)].append((fs, t))
        pname_of[(team, pk)] = pname
        tp = tip_of(gd, gc, occ, exp, tips)[0]
        if tp is not None:
            tip_by_game[(gd, gc)] = tp
    pmap, methods = {}, defaultdict(int)
    for (team, pk), pname in pname_of.items():
        pmap[(team, pk)] = box.match_player(pname, team)
        methods[pmap[(team, pk)][1]] += 1
    rep = dict(n_players=len(pmap), map_methods=dict(methods), players_matched=sum(1 for a, _ in pmap.values() if a is not None))
    rows, skip = [], defaultdict(int)
    n_psg = 0
    for (gd, gc, team, pk, stat), strikes in sorted(by_psg.items()):
        n_psg += 1
        aid, _ = pmap[(team, pk)]
        if aid is None:
            skip["unmapped_player"] += 1
            continue
        if (gd, gc) not in tip_by_game:
            skip["no_tip_time"] += 1
            continue
        mean, k = pre_game_mean(box.stat_games(aid, stat), gd)
        if mean is None:
            skip["fewer_than_5_prior_games"] += 1
            continue
        for rank, (fs, t) in enumerate(select_strikes(strikes, mean, 2), 1):
            rows.append(dict(ticker=t, game_date=gd, game_code=gc, team=team, player_key=pk, aid=aid, stat=stat,
                             floor_strike=fs, mean10=round(mean, 4), n_prior=k, rank=rank, tip=tip_by_game[(gd, gc)]))
    rep.update(player_stat_games=n_psg, skips=dict(skip), markets_selected=len(rows))
    return rows, rep


def write_selection(rows, reg_end):
    for r in rows:
        r["group"] = group_of(r["game_date"], reg_end)
    rows.sort(key=lambda r: (r["group"], r["game_date"], r["ticker"]))
    cols = ["group", "game_date", "ticker", "game_code", "team", "player_key", "aid", "stat", "floor_strike", "mean10", "n_prior",
            "rank", "tip"]
    with SELECTION_CSV.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r[c] for c in cols})
    return rows


def read_selection():
    with SELECTION_CSV.open() as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["group"] = int(r["group"])
        r["aid"] = int(r["aid"])
        r["floor_strike"] = float(r["floor_strike"])
        r["tip"] = int(r["tip"])
        r["rank"] = int(r["rank"])
        r["mean10"] = float(r["mean10"])
    return rows
