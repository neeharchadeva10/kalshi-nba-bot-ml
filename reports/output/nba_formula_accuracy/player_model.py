"""Research log #56, section B: the player-based points formula (8 versions).  Pure functions + two small data classes.

Interface:
    VERSIONS                        8 dicts {"q", "opp", "pace"}, fixed order (q outer, opp, pace inner)
    project(season, games, version) -> ndarray of projected totals (one per games row)
    project_all(season, games)      -> {version index: ndarray}; also fills LAST_STATS[season] (coverage diagnostics)

Read-only on every input (hoopR parquet, parsed injury json).  No network, no kpm.execution, no keys.
Decision time D = T - 3600.  Everything uses games with an ET date STRICTLY before the target game's ET date
(player history, rotation window, team pace and ratings) and injury reports created at or before D.
See PLAYER_MODEL_NOTES.md for every reading choice.
"""
from __future__ import annotations

import bisect
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BOX_DIR = ROOT / "data" / "raw" / "nba_open"
INJ_DIR = ROOT / "data" / "raw" / "nba_injury"
PROBE_CACHE = INJ_DIR / "_probe_cache_v3.json"
PARSED_V3 = ROOT / "reports" / "output" / "nba_model" / "injury_parsed_v3.json"
PARSED_TM = ROOT / "reports" / "output" / "nba_teammate_out" / "injury_parsed_tm.jsonl"

for _p in (ROOT / "reports" / "output" / "nba_teammate_out", ROOT / "reports" / "output" / "nba_totals_grid"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))              # append: this folder's own modules win names
import tm_lib  # noqa: E402  (#48 name normalisers, team-name map)
import grid_lib as gl  # noqa: E402  (#42 possession formula via team_games_g; imports #37 lib, #38 ratings2)

lib = gl.lib

# ------------------------------------------------------------------ constants / interface
VERSIONS = [{"q": q, "opp": o, "pace": p} for q, o, p in itertools.product((0.5, 1.0), (False, True), (False, True))]
ROT_MPG = 10.0          # rotation: >= 10 mpg over the team's last 10 games
ROT_WINDOW = 10
MIN_WINDOW = 10         # minutes = mean of the last 10 games played
RATE_GAMES = 20
SHRINK_K = 10.0
MIN_GAMES_RATE = 5
TEAM_MINUTES = 240.0
RATING_GAMES = 15
LEAGUE_MIN_ROWS = 60    # season-to-date league average needs >= 60 team-game rows, else last season's whole average
TEAM_RATE_GAMES = 20
HIST_TYPES = [2, 3]     # regular season + playoffs count as history (preseason / All-Star excluded)
BACK_S = 72 * 3600
STATUS_RANK = {"Out": 3, "Doubtful": 2, "Questionable": 1}
LAST_STATS: dict = {}


# ================================================================== pure helpers
def day_int(x) -> int:
    return int(np.datetime64(pd.Timestamp(x).date(), "D").astype(np.int64))


def shrink_rate(last_rate: float, cur_rate: float | None, n_cur: int, prior_rate: float | None = None) -> float:
    """Last-20 rate shrunk toward the current-season rate with weight w = n/(n+10) on the season rate.
    n_cur = 0: the target is last season's rate (else the last-20 rate), but its weight is 0 anyway."""
    w = n_cur / (n_cur + SHRINK_K)
    if n_cur > 0 and cur_rate is not None:
        target = cur_rate
    elif prior_rate is not None:
        target = prior_rate
    else:
        target = last_rate
    return (1.0 - w) * last_rate + w * target


def scale_minutes(eff) -> np.ndarray:
    """Scale (weighted) expected minutes so the team sums to 240.  Empty / zero -> empty."""
    a = np.asarray(eff, float)
    s = a.sum()
    if a.size == 0 or not s > 0:
        return np.zeros_like(a)
    return a * (TEAM_MINUTES / s)


def team_raw_points(mean_min, weights, ppm) -> float:
    """sum ppm_i * minutes_i with minutes = scale_minutes(weight_i * mean_min_i)."""
    m = scale_minutes(np.asarray(mean_min, float) * np.asarray(weights, float))
    if m.size == 0:
        return float("nan")
    return float(np.dot(np.asarray(ppm, float), m))


def status_weight(status: str | None, q: float) -> float:
    """Out / Doubtful -> 0 (removed); Questionable -> q; anything else (Probable, Available, unlisted) -> 1."""
    if status in ("Out", "Doubtful"):
        return 0.0
    if status == "Questionable":
        return float(q)
    return 1.0


def _lastfirst(raw: str):
    """'Pippen Jr.,Scotty' -> ('pippen', 's')."""
    last, first = (raw.split(",", 1) + [""])[:2] if "," in raw else (raw, "")
    n = tm_lib.norm_injury_name(raw)
    ln = tm_lib.norm_injury_name(last + ",")  # last name only (suffix stripped)
    return n, ln, (first.strip()[:1].lower() if first.strip() else "")


# ================================================================== injury reports
class Reports:
    """Official reports: creation time from the PDF /CreationDate probe cache, content from #32's parse + #48's local parse."""

    def __init__(self, created: dict | None = None, content: dict | None = None):
        if created is None:
            pc = json.loads(PROBE_CACHE.read_text())
            created = {k: v for k, v in pc.items() if v and v > 0}
        if content is None:
            content = {k: v for k, v in json.loads(PARSED_V3.read_text()).items() if "error" not in v}
            if PARSED_TM.exists():
                for ln in PARSED_TM.read_text().splitlines():
                    try:
                        r = json.loads(ln)
                    except Exception:  # noqa: BLE001
                        continue
                    if "error" not in r:
                        content[r["name"]] = r
        items = sorted((v, k) for k, v in created.items())
        self.created = [v for v, _ in items]
        self.names = [k for _, k in items]
        self.content = content
        self._idx = {}

    def _game(self, name, mdy, matchup):
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

    def last_covering(self, d_ts, mdy, matchup, back_s=BACK_S):
        """Latest report with creation <= d_ts (inclusive) that has rows / not-yet-submitted notes for this game, looking back
        at most 72 h.  -> (created, name, rows, nys, skipped_newer) or None."""
        hi = bisect.bisect_right(self.created, d_ts)
        lo = bisect.bisect_right(self.created, d_ts - back_s)
        skipped = 0
        for i in range(hi - 1, lo - 1, -1):
            g = self._game(self.names[i], mdy, matchup)
            if g is not None:
                return self.created[i], self.names[i], g[0], g[1], skipped
            skipped += 1
        return None


def team_status_map(rows, nys, team_code):
    """-> (statuses {normalised name: status}, listed [(raw name, status)], team_not_submitted).
    A player listed several times keeps the most severe status (Out > Doubtful > Questionable > other)."""
    nys_flag = any(tm_lib.TEAMNAME2CODE.get(x[2]) == team_code for x in nys)
    st, listed = {}, {}
    for r in rows:
        if tm_lib.TEAMNAME2CODE.get(r["team"]) != team_code:
            continue
        raw = r["player"]
        s = r["status"]
        cur = listed.get(raw)
        if cur is None or STATUS_RANK.get(s, 0) > STATUS_RANK.get(cur, 0):
            listed[raw] = s
    return listed, nys_flag


# ================================================================== per-season tables
class Tables:
    """History tables built from hoopR-style frames.  `pb` = player_box rows, `tb` = team_box rows (both for the season file and
    the previous one).  Everything is read-only after construction; look-ups take the target ET date and use rows strictly before it."""

    def __init__(self, pb: pd.DataFrame, tb: pd.DataFrame, year: int):
        self.year = year
        tg = gl.team_games_g(tb, HIST_TYPES)
        yr_of = tb.drop_duplicates("game_id").set_index("game_id")["season"]
        tg["yr"] = tg.game_id.map(yr_of).astype(int)
        tg["d"] = tg.date.map(lambda x: day_int(x))
        tg = tg.sort_values(["d", "game_id"], kind="stable").reset_index(drop=True)

        pbx = pb[pb.season_type.isin(HIST_TYPES)].copy()
        pbx["team"] = pbx.team_abbreviation.map(lib.to_kalshi)
        pbx = pbx[pbx.team.notna() & pbx.athlete_id.notna()]
        gd = pd.to_datetime(pbx.game_date_time, utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
        pbx["d"] = gd.map(lambda x: day_int(x))
        self.names = {}
        for a, n in zip(pbx.athlete_id.astype(int), pbx.athlete_display_name):
            self.names[a] = n
        self.all_norm = {tm_lib.norm_name(n) for n in self.names.values()}      # diagnostics only (never an input)
        played = pbx[(~pbx.did_not_play.astype(bool)) & pbx.minutes.notna() & (pbx.minutes > 0)].copy()
        played["pts"] = played.points.fillna(0.0).astype(float)
        played["mn"] = played.minutes.astype(float)
        played["aid"] = played.athlete_id.astype(int)
        played["gid"] = played.game_id.astype(np.int64)
        played["yr"] = played.season.astype(int)
        played = played.sort_values(["aid", "d", "gid"], kind="stable")

        # --- player history (played games only)
        self.hist = {}
        for a, g in played.groupby("aid", sort=False):
            self.hist[int(a)] = (g.d.values.astype(np.int64), g.mn.values, g.pts.values, g.yr.values.astype(np.int64))
        # --- (team, gid) -> {aid: minutes}  (played only; absent = 0 minutes = DNP / not on the roster)
        self.team_min = {}
        for t, gid, a, m in zip(played.team, played.gid, played.aid, played.mn):
            self.team_min.setdefault((t, int(gid)), {})[int(a)] = float(m)
        ps = played.groupby(["team", "gid"]).agg(pp_pts=("pts", "sum"), pp_min=("mn", "sum")).reset_index()
        tg["gid"] = tg.game_id.astype(np.int64)
        tg = tg.merge(ps, left_on=["team", "gid"], right_on=["team", "gid"], how="left")
        tg = tg.sort_values(["d", "gid"], kind="stable").reset_index(drop=True)
        self.tg = tg
        self.team = {}
        for t, g in tg.groupby("team", sort=False):
            self.team[t] = dict(d=g.d.values.astype(np.int64), gid=g.gid.values, yr=g.yr.values.astype(np.int64),
                                pts=g.pts.values, opp_pts=g.opp_pts.values, poss=g.poss.values, opp_poss=g.opp_poss.values,
                                pace=g.pace.values, pp_pts=g.pp_pts.values, pp_min=g.pp_min.values)
        self._ld = tg.d.values.astype(np.int64)
        self._league_cache = {}

    # ---- team-game look-ups (strictly before day d)
    def _upto(self, team, d):
        t = self.team.get(team)
        if t is None:
            return None, 0
        return t, int(np.searchsorted(t["d"], d, side="left"))

    def rotation(self, team, d):
        """-> {aid: window mpg} for players with >= 10 mpg over the team's last <=10 games before d (absent games = 0 minutes)."""
        t, i = self._upto(team, d)
        if t is None or i == 0:
            return {}
        has = ~np.isnan(t["pp_min"][:i])                  # games with no player_box rows at all (data not on disk) are not team games here
        w = t["gid"][:i][has][-ROT_WINDOW:]
        if len(w) == 0:
            return {}
        tot = {}
        for gid in w:
            for a, m in self.team_min.get((team, int(gid)), {}).items():
                tot[a] = tot.get(a, 0.0) + m
        n = len(w)
        return {a: s / n for a, s in tot.items() if s / n >= ROT_MPG}

    def player_inputs(self, aid, d, year):
        """-> dict(mean_min, last20_rate, cur_rate, n_cur, prior_rate, n_total) from games played strictly before d."""
        h = self.hist.get(aid)
        if h is None:
            return None
        hd, hm, hp, hy = h
        i = int(np.searchsorted(hd, d, side="left"))
        if i == 0:
            return None
        m, p, y = hm[:i], hp[:i], hy[:i]
        lm = m[-MIN_WINDOW:].mean()
        l20m, l20p = m[-RATE_GAMES:], p[-RATE_GAMES:]
        cur = y == year
        n_cur = int(cur.sum())
        pri = y == year - 1
        return dict(mean_min=float(lm), last20_rate=float(l20p.sum() / l20m.sum()), n_total=i,
                    cur_rate=float(p[cur].sum() / m[cur].sum()) if n_cur and m[cur].sum() > 0 else None, n_cur=n_cur,
                    prior_rate=float(p[pri].sum() / m[pri].sum()) if pri.any() and m[pri].sum() > 0 else None)

    def team_rate(self, team, d):
        """Team per-minute average: sum player points / sum player minutes over the team's last 20 games before d."""
        t, i = self._upto(team, d)
        if t is None or i == 0:
            return None
        a = slice(max(0, i - TEAM_RATE_GAMES), i)
        pts, mn = np.nansum(t["pp_pts"][a]), np.nansum(t["pp_min"][a])
        return float(pts / mn) if mn > 0 else None

    def recent(self, team, d, n=RATING_GAMES):
        """-> (drtg, pace) over the team's last <= n games before d.  drtg = points allowed per 100 possessions
        = 100 * sum(opp_pts) / sum(opp_poss)  (the #37/#38 DRtg definition); pace = mean of the per-game pace."""
        t, i = self._upto(team, d)
        if t is None or i == 0:
            return None, None
        a = slice(max(0, i - n), i)
        return 100.0 * t["opp_pts"][a].sum() / t["opp_poss"][a].sum(), float(t["pace"][a].mean())

    def league_avg(self, d, year):
        """League points per 100 possessions, season to date (games of `year` strictly before d); if the season has < 60
        team-game rows so far, last season's whole-year value."""
        key = (d, year)
        if key not in self._league_cache:
            tg = self.tg
            before = self._ld < d
            cur = tg[before & (tg.yr.values == year)]
            if len(cur) < LEAGUE_MIN_ROWS:
                cur = tg[before & (tg.yr.values == year - 1)]
            if len(cur) == 0:
                cur = tg[before]
            self._league_cache[key] = 100.0 * cur.pts.sum() / cur.poss.sum() if len(cur) else None
        return self._league_cache[key]


def load_tables(season: str) -> Tables:
    """hoopR season '2023-24' -> file year 2024; loads the season file and the previous one (history for last-20 etc.)."""
    year = int(season[:4]) + 1
    pcols = ["game_id", "season", "season_type", "game_date_time", "athlete_id", "athlete_display_name", "minutes",
             "points", "did_not_play", "team_abbreviation"]
    pbs, tbs = [], []
    for y in (year - 1, year):
        f = BOX_DIR / f"player_box_{y}.parquet"
        if f.exists():
            pbs.append(pd.read_parquet(f, columns=pcols))
        tbs.append(pd.read_parquet(BOX_DIR / f"team_box_{y}.parquet"))
    pb = pd.concat(pbs, ignore_index=True)
    tb = pd.concat(tbs, ignore_index=True)
    return Tables(pb, tb, year)


# ================================================================== per-game projection
_TABLES: dict = {}
_REPORTS: dict = {}
_LAST_CALL: dict = {}


def _get_reports():
    if "r" not in _REPORTS:
        _REPORTS["r"] = Reports()
    return _REPORTS["r"]


def _cand_key(display: str):
    """hoopR display name -> (normalised last name without suffix, first initial)."""
    toks = [t for t in display.split() if t]
    while len(toks) > 2 and tm_lib.norm_name(toks[-1]) in tm_lib.SUFFIXES:
        toks.pop()
    if not toks:
        return "", ""
    return tm_lib.norm_name(toks[-1]), tm_lib.norm_name(toks[0])[:1]


def _match_rotation(raw, status_norm_map, cand_norm, cand_last):
    """Match an injury-report name to a rotation candidate: exact normalised name, else unique last name + first initial."""
    n, ln, fi = _lastfirst(raw)
    if n in cand_norm and len(cand_norm[n]) == 1:
        return cand_norm[n][0]
    if n in cand_norm:
        return None
    hits = [a for (l_, f_), aids in cand_last.items() if ln and l_ == ln and f_ == fi for a in aids]
    return hits[0] if len(hits) == 1 else None


def _team_game(tabs: Tables, team, d, year, listed, nys_flag, have_report, stats):
    """-> (raw points for q = 0.5, raw points for q = 1.0) or (nan, nan)."""
    rot = tabs.rotation(team, d)
    if not rot:
        return float("nan"), float("nan")
    cands = sorted(rot)
    cand_norm, cand_last = {}, {}
    for a in cands:
        nm = tabs.names.get(a, "")
        cand_norm.setdefault(tm_lib.norm_name(nm), []).append(a)
        cand_last.setdefault(_cand_key(nm), []).append(a)
    status = {}
    if have_report and not nys_flag:
        for raw, s in listed.items():
            if s not in STATUS_RANK:
                continue
            stats["listed"] += 1
            a = _match_rotation(raw, None, cand_norm, cand_last)
            if a is not None:
                stats["matched_rotation"] += 1
                if STATUS_RANK[s] > STATUS_RANK.get(status.get(a), 0):
                    status[a] = s
            elif tm_lib.norm_injury_name(raw) in tabs.all_norm:
                stats["known_not_rotation"] += 1
            else:
                stats["unmatched"] += 1
                stats["unmatched_names"].append((team, raw, s))
    tr = tabs.team_rate(team, d)
    out = []
    for q in (0.5, 1.0):
        mm, ww, pp = [], [], []
        for a in cands:
            w = status_weight(status.get(a), q)
            if w == 0.0:
                continue
            inp = tabs.player_inputs(a, d, year)
            if inp is None:
                continue
            if inp["n_total"] < MIN_GAMES_RATE:
                if tr is None:
                    continue
                ppm = tr
            else:
                ppm = shrink_rate(inp["last20_rate"], inp["cur_rate"], inp["n_cur"], inp["prior_rate"])
            mm.append(inp["mean_min"]); ww.append(w); pp.append(ppm)
        out.append(team_raw_points(mm, ww, pp) if mm else float("nan"))
    return out[0], out[1]


def _new_stats():
    return dict(games=0, with_report=0, report_skipped_newer=0, listed=0, matched_rotation=0, known_not_rotation=0,
                unmatched=0, unmatched_names=[], team_not_submitted=0, nan_projections=0)


_KALSHI_CODES = set(lib.TEAM_MAP.values())


def _kcode(x):
    """Accepts Kalshi codes (GSW, NYK, ...: used as is) or hoopR codes (GS, NY, ...: mapped).  None if neither."""
    x = str(x)
    return x if x in _KALSHI_CODES else lib.to_kalshi(x)


def project_all(season: str, games: pd.DataFrame, tables: Tables | None = None, reports: Reports | None = None) -> dict:
    """All 8 versions in one pass.  -> {i: ndarray (len(games))} for i = index into VERSIONS.  NaN where a team has no rotation.
    games: game_id, date (ET date), T (UTC epoch s of the scheduled start; D = T - 3600), away, home (Kalshi or hoopR abbreviations)."""
    tabs = tables if tables is not None else _TABLES.setdefault(season, load_tables(season))
    reps = reports if reports is not None else _get_reports()
    year = int(season[:4]) + 1
    n = len(games)
    stats = _new_stats()
    raw = np.full((n, 2, 2), np.nan)        # [game, side(0 away, 1 home), q(0 .5, 1 1.0)]
    of = np.full((n, 2), np.nan)            # opponent factor
    pf = np.full((n, 2), np.nan)            # pace factor
    dates = [day_int(x) for x in games["date"]]
    Ts = games["T"].astype(float).values
    for k in range(n):
        away = _kcode(games["away"].iloc[k])
        home = _kcode(games["home"].iloc[k])
        d = dates[k]
        if away is None or home is None:
            continue
        stats["games"] += 1
        dts = Ts[k] - 3600.0
        mdy = pd.Timestamp(games["date"].iloc[k]).strftime("%m/%d/%Y")
        cov = reps.last_covering(dts, mdy, f"{away}@{home}") if reps is not None else None
        rows, nys = (cov[2], cov[3]) if cov else ([], [])
        if cov:
            stats["with_report"] += 1
            stats["report_skipped_newer"] += 1 if cov[4] else 0
        for side, (team, opp) in enumerate(((away, home), (home, away))):
            listed, nys_flag = team_status_map(rows, nys, team)
            stats["team_not_submitted"] += 1 if (cov and nys_flag) else 0
            r5, r1 = _team_game(tabs, team, d, year, listed, nys_flag, cov is not None, stats)
            raw[k, side, 0], raw[k, side, 1] = r5, r1
            lg = tabs.league_avg(d, year)
            drtg_opp, _ = tabs.recent(opp, d)
            if drtg_opp is not None and lg:
                of[k, side] = drtg_opp / lg
        pa = tabs.recent(away, d)[1]
        ph = tabs.recent(home, d)[1]
        if pa is not None and ph is not None:
            exp = (pa + ph) / 2.0
            pf[k, 0], pf[k, 1] = exp / pa, exp / ph
    out = {}
    for i, v in enumerate(VERSIONS):
        qi = 0 if v["q"] == 0.5 else 1
        pts = raw[:, :, qi].copy()
        if v["opp"]:
            pts = pts * of
        if v["pace"]:
            pts = pts * pf
        out[i] = pts.sum(axis=1)
    stats["nan_projections"] = int(np.isnan(out[0]).sum())
    LAST_STATS[season] = stats
    return out


def project(season: str, games: pd.DataFrame, version: dict) -> np.ndarray:
    """Projected total per row of `games` for one version (a dict from VERSIONS).  Caches the last project_all call."""
    key = (season, tuple(games["game_id"]), tuple(games["T"]), tuple(str(x) for x in games["date"]))
    if _LAST_CALL.get("key") != key:
        _LAST_CALL["key"] = key
        _LAST_CALL["val"] = project_all(season, games)
    return _LAST_CALL["val"][VERSIONS.index(version)]
