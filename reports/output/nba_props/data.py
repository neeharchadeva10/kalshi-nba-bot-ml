"""Research log #36 data layer: Kalshi props + quotes (nba_props.sqlite), hoopR box scores, official injury reports.

Everything here is read-only on its inputs.  No outcome (result) is touched except where a function says so.
"""
from __future__ import annotations

import bisect
import json
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DB = HERE / "nba_props.sqlite"
BOX_DIR = ROOT / "data" / "raw" / "nba_open"
INJ_DIR = ROOT / "data" / "raw" / "nba_injury"
PARSED_V3 = ROOT / "reports" / "output" / "nba_model" / "injury_parsed_v3.json"
PROBE_CACHE = INJ_DIR / "_probe_cache_v3.json"
PARSED_OWN = HERE / "injury_parsed_props.json"       # extra PDFs parsed here (local files only)
SEASONS = [2022, 2023, 2024, 2025, 2026]
CUR_SEASON = 2026
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
STATS = ["PTS", "REB", "AST", "3PT"]
BOXCOL = {"PTS": "points", "REB": "rebounds", "AST": "assists", "3PT": "three_point_field_goals_made"}
SUFFIXES = ("jr", "sr", "ii", "iii", "iv", "v")
GAME_END_BUFFER_S = 4 * 3600      # a box-score game counts as "earlier" only if tip + 4 h <= decision time


# ------------------------------------------------------------------ names
def norm_name(display: str) -> str:
    """'Jaren Jackson Jr.' -> 'jarenjackson'; 'Nikola Jokic' -> 'nikolajokic' (accent-free letters, generational suffix dropped)."""
    s = unicodedata.normalize("NFKD", display or "").encode("ascii", "ignore").decode().lower()
    toks = [t for t in re.split(r"\s+", s.strip()) if t]
    while len(toks) > 2 and re.sub(r"[^a-z]", "", toks[-1]) in SUFFIXES:
        toks.pop()
    return re.sub(r"[^a-z]", "", "".join(toks))


def norm_injury_name(raw: str) -> str:
    """'PippenJr.,Scotty' -> 'scottypippen' ; 'Prosper,Olivier-Maxence' -> 'oliviermaxenceprosper'."""
    if "," in raw:
        last, first = raw.split(",", 1)
    else:
        last, first = raw, ""
    last_c = unicodedata.normalize("NFKD", last).encode("ascii", "ignore").decode()
    m = re.match(r"^(.*?)(Jr|Sr|II|III|IV)\.?$", last_c)
    if m and len(m.group(1)) > 2:
        last_c = m.group(1)
    return re.sub(r"[^a-z]", "", (first + last_c).lower().replace("jr.", ""))


def code_of(abbr: str) -> str:
    return ESPN2CODE.get(abbr, abbr)


# ------------------------------------------------------------------ box scores
class Box:
    """hoopR player/team box scores 2022..2026.

    hist[athlete_id]  : list of played games sorted by tip: (tip_ts, date_iso, season, team, minutes, pts, reb, ast, fg3m, gid)
    hist_keys[aid]    : [tip_ts + 4h] parallel list (bisect key = the time the game is certainly over)
    roster[(team, season)] : {norm_name: {athlete ids}}   (all rows, DNP included -> identity only)
    team_games[team]  : sorted [(tip_ts, date_iso, season, gid, opp, {stat: allowed_by_this_team})] (all NBA games of the team)
    all_team_games    : sorted list of (tip_ts, season, {stat: stat total of one team}) -- one entry per team-game (league average)
    game_row[(aid, gid)] : the played-row dict (for outcome lookup of a player-game)
    """

    def __init__(self, box_dir: Path | None = None, seasons=None):
        import pandas as pd
        d = box_dir or BOX_DIR
        seasons = seasons or SEASONS
        self.hist = defaultdict(list)
        self.roster = defaultdict(lambda: defaultdict(set))
        self.names = {}
        self.team_games = defaultdict(list)
        self.all_team_games = []
        self.by_game_team = {}                               # (gid, team) -> {'date','tip','stats':{...}, 'opp'}
        self.player_games_on = defaultdict(dict)             # (aid) -> {(date_iso, team): row} played rows, for outcome lookup
        tot = {}
        for yr in seasons:
            pb = pd.read_parquet(d / f"player_box_{yr}.parquet")
            for r in pb.itertuples(index=False):
                team = code_of(r.team_abbreviation)
                if team not in CODES:
                    continue
                aid = int(r.athlete_id) if r.athlete_id == r.athlete_id else None
                if aid is None:
                    continue
                nm = norm_name(r.athlete_display_name)
                self.names[aid] = r.athlete_display_name
                self.roster[(team, yr)][nm].add(aid)
                gid = str(r.game_id)
                tip = pd.Timestamp(r.game_date_time).tz_convert("UTC").timestamp()
                date_iso = str(r.game_date)
                opp = code_of(r.opponent_team_abbreviation)
                key = (gid, team)
                t = tot.setdefault(key, {"tip": tip, "date": date_iso, "season": yr, "opp": opp, "gid": gid,
                                         "stats": {s: 0.0 for s in STATS}})
                for s in STATS:
                    v = getattr(r, BOXCOL[s])
                    t["stats"][s] += float(v) if v == v and v is not None else 0.0
                mn = r.minutes
                if r.did_not_play or mn is None or mn != mn or mn <= 0:
                    continue
                vals = []
                for s in STATS:
                    v = getattr(r, BOXCOL[s])
                    vals.append(float(v) if v == v and v is not None else 0.0)
                row = (tip, date_iso, yr, team, float(mn), vals[0], vals[1], vals[2], vals[3], gid)
                self.hist[aid].append(row)
                self.player_games_on[aid][(date_iso, team)] = row
        for aid in self.hist:
            self.hist[aid].sort(key=lambda x: (x[0], x[9]))
        self.hist_keys = {aid: [x[0] + GAME_END_BUFFER_S for x in h] for aid, h in self.hist.items()}
        # team-games: keep only games where both sides are NBA teams
        for (gid, team), t in tot.items():
            opp_t = tot.get((gid, t["opp"]))
            if opp_t is None:
                continue
            self.by_game_team[(gid, team)] = t
            self.team_games[team].append((t["tip"], t["date"], t["season"], gid, t["opp"],
                                          dict(opp_t["stats"]), dict(t["stats"])))     # [5] = allowed by team, [6] = scored by team
            self.all_team_games.append((t["tip"], t["season"], dict(t["stats"])))
        for tm in self.team_games:
            self.team_games[tm].sort(key=lambda x: (x[0], x[3]))
        self.all_team_games.sort(key=lambda x: x[0])
        self.team_keys = {tm: [x[0] + GAME_END_BUFFER_S for x in g] for tm, g in self.team_games.items()}
        # cumulative league sums per season for "league average to date"
        self.league_keys = [x[0] + GAME_END_BUFFER_S for x in self.all_team_games]
        self.league_seasons = [x[1] for x in self.all_team_games]
        # per-season prefix sums: store (cum over all rows) and count by season through a dict of cumulative arrays
        self._cum = {}
        for season in set(self.league_seasons):
            ks, cs = [], {s: [] for s in STATS}
            rr = {s: 0.0 for s in STATS}
            for tipk, (tip, sea, st) in zip(self.league_keys, self.all_team_games):
                if sea != season:
                    continue
                for s in STATS:
                    rr[s] += st[s]
                ks.append(tipk)
                for s in STATS:
                    cs[s].append(rr[s])
            self._cum[season] = (ks, cs)

    # ---- identity
    def match_player(self, name: str, team: str, season: int = CUR_SEASON):
        """Kalshi (name, team) -> athlete id or None.  Unique normalised-name match among the team's season roster."""
        ids = self.roster.get((team, season), {}).get(norm_name(name), set())
        return next(iter(ids)) if len(ids) == 1 else None

    # ---- history before a decision time
    def prior_games(self, aid: int, decision_ts: float):
        h = self.hist.get(aid, [])
        n = bisect.bisect_right(self.hist_keys.get(aid, []), decision_ts)
        return h[:n]

    def league_avg_allowed(self, season: int, decision_ts: float):
        """Mean of each stat per team-game over this season's team-games finished before the decision -> ({stat: avg}, n)."""
        ks, cs = self._cum.get(season, ([], {s: [] for s in STATS}))
        n = bisect.bisect_right(ks, decision_ts)
        if n == 0:
            return None, 0
        return {s: cs[s][n - 1] / n for s in STATS}, n

    def team_prior(self, team: str, decision_ts: float):
        g = self.team_games.get(team, [])
        n = bisect.bisect_right(self.team_keys.get(team, []), decision_ts)
        return g[:n]


# ------------------------------------------------------------------ injury reports
class ReportStore:
    """All published injury PDFs with a known /CreationDate (probe cache of #32, 2025-26 part).  For a decision time D the
    report is the file with the latest CreationDate <= D.  Parsed content is read from #32's json; files not in it are parsed
    from the local PDF (no download) and cached in injury_parsed_props.json."""

    def __init__(self, probe_cache: Path | None = None, parsed_v3: Path | None = None, parse_missing=True):
        pc = json.loads((probe_cache or PROBE_CACHE).read_text())
        items = sorted((v, k) for k, v in pc.items() if v and v > 0)
        self.created = [v for v, _ in items]
        self.names = [k for _, k in items]
        self.parsed = json.loads((parsed_v3 or PARSED_V3).read_text()) if (parsed_v3 or PARSED_V3).exists() else {}
        self.own = json.loads(PARSED_OWN.read_text()) if PARSED_OWN.exists() else {}
        self.parse_missing = parse_missing
        self.dirty = False
        self.stats = defaultdict(int)

    def select(self, decision_ts: float):
        """-> (file name, created_ts) with the latest creation <= decision_ts, or (None, None)."""
        i = bisect.bisect_right(self.created, decision_ts)
        if i == 0:
            return None, None
        return self.names[i - 1], self.created[i - 1]

    def content(self, name: str):
        if name in self.parsed and "error" not in self.parsed[name]:
            return self.parsed[name]
        if name in self.own:
            return self.own[name]
        if not self.parse_missing:
            return None
        sys.path.insert(0, str(ROOT / "reports" / "output" / "nba_model"))
        import injury_parse_v3 as P  # noqa: E402  (parse-only; pdfplumber)
        try:
            r = P.parse_any(INJ_DIR / name)
        except Exception as e:  # noqa: BLE001
            r = {"error": f"{type(e).__name__}: {e}", "header": None, "rows": [], "nys": [], "problems": [], "layout": "?"}
        self.own[name] = r
        self.dirty = True
        self.stats["parsed_locally"] += 1
        return r

    def save(self):
        if self.dirty:
            PARSED_OWN.write_text(json.dumps(self.own))
            self.dirty = False

    def report_for_game(self, decision_ts: float, date_mdy: str, away: str, home: str):
        """-> dict(file, created, covered(bool), rows [of this matchup/date], nys [(date, matchup, team)]) or None if no report."""
        name, cre = self.select(decision_ts)
        if name is None:
            return None
        c = self.content(name)
        if c is None or "error" in c:
            return {"file": name, "created": cre, "covered": False, "rows": [], "nys": [], "error": True}
        mu = f"{away}@{home}"
        rows = [r for r in c["rows"] if r["date"] == date_mdy and r["matchup"] == mu]
        nys = [x for x in c["nys"] if x[0] == date_mdy and x[1] == mu]
        return {"file": name, "created": cre, "covered": bool(rows or nys), "rows": rows, "nys": nys, "error": False}


# ------------------------------------------------------------------ Kalshi markets + quotes
def valid_quote(bid, ask) -> bool:
    """repo rule (nba_drift.valid_quote): both sides present, bid > 0, ask < 1, not crossed."""
    return (bid is not None and ask is not None and bid == bid and ask == ask and bid > 0 and ask < 1 and bid <= ask)


def decision_candle(candles, tip_ts: int):
    """candles: [(end_ts, bid, ask, volume)] -> the candle with the largest end_ts <= tip - 3600 (else None).  A candle ending at
    time e reports the book at e, so it is known at decision time T-60min iff e <= T-60min."""
    cut = tip_ts - 3600
    best = None
    for c in candles:
        if c[0] <= cut and (best is None or c[0] > best[0]):
            best = c
    return best


SHARED_SELECTION = ROOT / "reports" / "output" / "nba_teammate_out" / "selection.csv"       # #48: PTS/REB/AST, 2 strikes per player-game
SHARED_CANDLES = ROOT / "data" / "raw" / "kalshi_props_hourly"                              # #48's raw cache
THREES_SELECTION = HERE / "threes_selection.csv"                                              # #36 job 1: 3PT, same rule
THREES_CANDLES = ROOT / "data" / "raw" / "kalshi_props_hourly_3pt"


def candle_file(stat: str, ticker: str, game_date: str) -> Path:
    base = THREES_CANDLES if stat == "3PT" else SHARED_CANDLES
    return base / game_date / f"{ticker}.json.gz"


def parse_candles(resp):
    """Raw hourly-candle response -> [(end_ts, yes_bid close, yes_ask close, volume)] (None where the side has no close)."""
    out = []
    for c in (resp or {}).get("candlesticks", []) or []:
        def g(side):
            v = (c.get(side) or {}).get("close")
            return float(v) if v not in (None, "") else None
        vol = c.get("volume")
        out.append((int(c["end_period_ts"]), g("yes_bid"), g("yes_ask"), float(vol) if vol not in (None, "") else 0.0))
    return out


def read_cached_candles(path: Path):
    """None if not downloaded; [] for a cached 404 / empty response; else the parsed candles."""
    import gzip
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        rec = json.load(fh)
    return parse_candles(rec.get("response"))


def read_selected_tickers():
    """{ticker} of the downloaded-price scope: #48's PTS/REB/AST selection plus the 3PT selection (2 strikes per player-stat-game)."""
    import csv
    sel = set()
    for p in (SHARED_SELECTION, THREES_SELECTION):
        if p.exists():
            with p.open() as fh:
                sel.update(r["ticker"] for r in csv.DictReader(fh))
    return sel


def download_status():
    """-> dict(selected, cached, missing) over both selections (file existence only; no content read)."""
    import csv
    out = {}
    for name, p, base in (("pts_reb_ast", SHARED_SELECTION, SHARED_CANDLES), ("3pt", THREES_SELECTION, THREES_CANDLES)):
        n = c = 0
        if p.exists():
            with p.open() as fh:
                for r in csv.DictReader(fh):
                    n += 1
                    c += (base / r["game_date"] / f"{r['ticker']}.json.gz").exists()
        out[name] = {"selected": n, "cached": c, "missing": n - c}
    return out


def load_markets(db: Path | None = None):
    """-> list of dicts (one per market, all four series) with the T-1h quote attached (bid/ask None if no valid quote).
    Prices come ONLY from the shared raw caches of the 2-strike selections; a market outside the selection (or selected but not
    cached) has downloaded=False and is counted as "not downloaded" (it is never given a price)."""
    sys.path.insert(0, str(HERE))
    import collect as C
    conn = sqlite3.connect(f"file:{(db or DB).as_posix()}?mode=ro", uri=True)
    tips = C.game_tips()
    selected = read_selected_tickers()
    out = []
    for (t, s, stat, gd, gc, team, pk, pname, fs, res, status, occ, exp) in conn.execute(
            "SELECT ticker, series, stat, game_date, game_code, team, player_key, player_name, floor_strike, result, status, "
            "occ_ts, exp_ts FROM mk ORDER BY game_date, ticker"):
        tip, src = C.tip_of(gd, gc, occ, exp, tips)
        candles = read_cached_candles(candle_file(stat, t, gd)) if t in selected else None
        c = decision_candle(candles, tip) if (candles and tip is not None) else None
        bid, ask = (c[1], c[2]) if c else (None, None)
        out.append({"ticker": t, "stat": stat, "game_date": gd, "game_code": gc, "team": team, "player_key": pk,
                    "player_name": pname, "strike": int(round(fs + 0.5)) if fs is not None else None, "result": res,
                    "tip": tip, "tip_src": src, "downloaded": candles is not None, "n_candles": None if candles is None else len(candles),
                    "has_candle": c is not None, "cand_end": c[0] if c else None,
                    "age_min": (tip - 3600 - c[0]) / 60 if (c and tip is not None) else None, "volume_c": c[3] if c else None,
                    "bid": bid if valid_quote(bid, ask) else None, "ask": ask if valid_quote(bid, ask) else None,
                    "raw_bid": bid, "raw_ask": ask})
    conn.close()
    return out


def split_games(games):
    """games: iterable of (tip_ts, game_key) -> (discovery set, unseen set): sorted by (tip, key), first ceil(N/2) = discovery."""
    g = sorted(set(games))
    n = (len(g) + 1) // 2
    return {k for _, k in g[:n]}, {k for _, k in g[n:]}
