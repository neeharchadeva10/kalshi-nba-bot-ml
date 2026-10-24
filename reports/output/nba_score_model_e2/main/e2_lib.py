"""Research log #62 (E2 = E1 + who is out tonight): shared library.

Pure functions first (testable), then loaders, the feature builder, sampling, ridge residual layer, hashing and run-once gates.
Module is named e2_lib so it never clashes with E1's sm_lib / models (E1 is loaded through load_e1()).
Read-only on every input.  No network, no kpm.execution, no keys.  See FEATURES_E2.md for the exact feature definitions.
"""
from __future__ import annotations

import bisect
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent                                   # reports/output/nba_score_model_e2
OUT = STUDY.parent                                    # reports/output
ROOT = STUDY.parents[2]                               # repo root
E1_STUDY = OUT / "nba_score_model"
E1_MAIN = E1_STUDY / "main"
BOX_DIR = ROOT / "data" / "raw" / "nba_open"
INJ_DIR = ROOT / "data" / "raw" / "nba_injury"
PROBE_CACHE = INJ_DIR / "_probe_cache_v3.json"
PARSED_V3 = OUT / "nba_model" / "injury_parsed_v3.json"
PARSED_TM = OUT / "nba_teammate_out" / "injury_parsed_tm.jsonl"
TM_DIR = OUT / "nba_teammate_out"
CLOSING = ROOT / "data" / "raw" / "nba_odds_samples" / "closing_lines_odds_api.parquet"

FEATURES_PARQUET = STUDY / "injury_features.parquet"
E1_FROZEN = E1_STUDY / "frozen.json"

# ------------------------------------------------------------------ constants (pre-registered)
SEASONS = ("2021-22", "2022-23", "2023-24", "2024-25", "2025-26")      # 2026-27: no player_box_2027 on disk -> not built
TRAIN = ("2021-22", "2022-23")
VALID = ("2023-24",)
TEST = ("2024-25", "2025-26")
ROT_MPG = 15.0              # rotation-quality: mean minutes over last 10 games played >= 15
KEY_MPG = 28.0              # key player (test split only): >= 28 mpg over last 10 games played
WINDOW = 10                 # last 10 games played
MIN_CUR = 3                 # fewer than 3 games played this season -> last season
BACK_TEAM_GAMES = 10        # team's last 10 games
BACK_MAX_APPEARED = 5       # appeared in fewer than 5 of them -> returning
D_OFFSET_S = 3600           # D = T - 60 min
BACK_S = 72 * 3600          # report look-back
SEED_SAMPLE = 20261024
N_SAMPLE = 200
BOOT_N = 10000
BOOT_SEED = 20261023
WITHIN = 10.0
LAMBDAS = (0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0, 100000.0, 1000000.0)
VARIANTS = {"A": ["out_pts"], "B": ["out_pts", "q_pts"], "C": ["out_pts", "q_pts", "back_pts", "out_min"]}
CANDIDATE_ORDER = ("E1", "A", "B", "C")                                 # ties go to the earlier id (E1 first)
STATUS_RANK = {"Out": 3, "Doubtful": 2, "Questionable": 1}
OUT_STATUSES = ("Out", "Doubtful")
BASE_FEATS = ["out_pts", "q_pts", "back_pts", "out_min"]
FEAT_COLS = [f"{f}_{s}" for s in ("own", "opp") for f in BASE_FEATS]
SUFFIXES = ("jr", "sr", "ii", "iii", "iv", "v")

# hoopR / ESPN abbreviation -> Kalshi code.  Kalshi: GSW NYK SAS UTA WAS NOP; hoopR: GS NY SA UTAH WSH NO.
HOOPR2KALSHI = {"ATL": "ATL", "BKN": "BKN", "BOS": "BOS", "CHA": "CHA", "CHI": "CHI", "CLE": "CLE", "DAL": "DAL", "DEN": "DEN",
                "DET": "DET", "GS": "GSW", "HOU": "HOU", "IND": "IND", "LAC": "LAC", "LAL": "LAL", "MEM": "MEM", "MIA": "MIA",
                "MIL": "MIL", "MIN": "MIN", "NO": "NOP", "NY": "NYK", "OKC": "OKC", "ORL": "ORL", "PHI": "PHI", "PHX": "PHX",
                "POR": "POR", "SA": "SAS", "SAC": "SAC", "TOR": "TOR", "UTAH": "UTA", "WSH": "WAS"}
KALSHI2HOOPR = {v: k for k, v in HOOPR2KALSHI.items()}
TEAMNAME2CODE = {
    "AtlantaHawks": "ATL", "BrooklynNets": "BKN", "BostonCeltics": "BOS", "CharlotteHornets": "CHA", "ChicagoBulls": "CHI",
    "ClevelandCavaliers": "CLE", "DallasMavericks": "DAL", "DenverNuggets": "DEN", "DetroitPistons": "DET",
    "GoldenStateWarriors": "GSW", "HoustonRockets": "HOU", "IndianaPacers": "IND", "LAClippers": "LAC",
    "LosAngelesClippers": "LAC", "LosAngelesLakers": "LAL", "MemphisGrizzlies": "MEM", "MiamiHeat": "MIA",
    "MilwaukeeBucks": "MIL", "MinnesotaTimberwolves": "MIN", "NewOrleansPelicans": "NOP", "NewYorkKnicks": "NYK",
    "OklahomaCityThunder": "OKC", "OrlandoMagic": "ORL", "Philadelphia76ers": "PHI", "PhoenixSuns": "PHX",
    "PortlandTrailBlazers": "POR", "SacramentoKings": "SAC", "SanAntonioSpurs": "SAS", "TorontoRaptors": "TOR",
    "UtahJazz": "UTA", "WashingtonWizards": "WAS"}


def season_label(year: int) -> str:
    return f"{year - 1}-{year % 100:02d}"


def year_of(season: str) -> int:
    return int(season[:4]) + 1


def to_kalshi(code):
    return HOOPR2KALSHI.get(str(code))


# ------------------------------------------------------------------ name normalisation (same rules as #48 tm_lib / #56 player_model)
def _ascii(s):
    import unicodedata
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def norm_name(display: str) -> str:
    """hoopR display name -> lower-case letters only, suffix (Jr, Sr, II, III, IV, V) dropped.  Same as tm_lib.norm_name."""
    import re
    toks = [t for t in re.split(r"\s+", _ascii(display).lower().strip()) if t]
    while len(toks) > 2 and re.sub(r"[^a-z]", "", toks[-1]) in SUFFIXES:
        toks.pop()
    return re.sub(r"[^a-z]", "", "".join(toks))


def norm_injury_name(raw: str) -> str:
    """'PippenJr.,Scotty' -> 'scottypippen'.  Same as tm_lib.norm_injury_name."""
    import re
    if "," in raw:
        last, first = raw.split(",", 1)
    else:
        last, first = raw, ""
    last_c = _ascii(last)
    m = re.match(r"^(.*?)(Jr|Sr|II|III|IV)\.?$", last_c)
    if m and len(m.group(1)) > 2:
        last_c = m.group(1)
    return re.sub(r"[^a-z]", "", (first + last_c).lower().replace("jr.", ""))


def lastfirst(raw: str):
    """'Pippen Jr.,Scotty' -> (normalised full name, normalised last name without suffix, first initial)."""
    last, first = (raw.split(",", 1) + [""])[:2] if "," in raw else (raw, "")
    n = norm_injury_name(raw)
    ln = norm_injury_name(last + ",")
    return n, ln, (first.strip()[:1].lower() if first.strip() else "")


def cand_key(display: str):
    """hoopR display name -> (normalised last name without suffix, first initial)."""
    toks = [t for t in display.split() if t]
    while len(toks) > 2 and norm_name(toks[-1]) in SUFFIXES:
        toks.pop()
    if not toks:
        return "", ""
    return norm_name(toks[-1]), norm_name(toks[0])[:1]


def _clean_tokens(text: str):
    """lowercase, strip accents, apostrophes, periods and hyphens, split on spaces, drop suffix tokens jr/sr/ii/iii/iv."""
    import re
    t = _ascii(text).lower()
    t = re.sub(r"[.'’\-]", "", t)
    toks = [x for x in re.split(r"\s+", t.strip()) if x]
    return [x for x in toks if x not in SUFFIXES[:5]]


def norm_display(display: str) -> str:
    """hoopR display name -> normalised 'first last' with spaces removed (so 'Van Vleet' == 'VanVleet')."""
    return "".join(_clean_tokens(display))


def norm_report_name(raw: str) -> str:
    """Report 'Last,First' (glued suffixes like 'PippenJr.' allowed) -> 'firstlast' with the same normalisation."""
    import re
    last, first = raw.split(",", 1) if "," in raw else (raw, "")
    lt = _clean_tokens(last)
    if lt:                                        # glued suffix on the last token: 'pippenjr' -> 'pippen'
        m = re.match(r"^(.*?)(jr|sr|iii|ii|iv)$", lt[-1])
        if m and len(m.group(1)) > 2:
            lt[-1] = m.group(1)
    return "".join(_clean_tokens(first)) + "".join(lt)


def match_name(raw: str, cand_norm: dict, cand_last=None):
    """Report name -> athlete id: EXACT match of the normalised 'first last' to the normalised display name among the team's
    players; zero or 2+ matches -> None.  Never matches on surname alone."""
    hits = cand_norm.get(norm_report_name(raw), [])
    return hits[0] if len(hits) == 1 else None


# ------------------------------------------------------------------ pure feature maths
def player_window(hd, hm, hp, hy, d: int, year: int):
    """Rotation-quality inputs for one player at ET day-number d (all games STRICTLY before d).
    hd/hm/hp/hy = day number, minutes, points, season-year of his regular-season games PLAYED, sorted by date.
    Returns (mpg, ppg, source) where source is 'cur' (>= 3 games played this season: mean over the last 10 played) or 'prev'
    (< 3 this season: mean over the last 10 games played of last season) or None (no usable history)."""
    i = int(np.searchsorted(hd, d, side="left"))
    if i == 0:
        return None
    yy = hy[:i]
    lo = int(np.searchsorted(yy, year, side="left"))
    n_cur = i - lo
    if n_cur >= MIN_CUR:
        a = max(lo, i - WINDOW)
        return float(hm[a:i].mean()), float(hp[a:i].mean()), "cur"
    lo2 = int(np.searchsorted(yy, year - 1, side="left"))
    hi2 = int(np.searchsorted(yy, year - 1, side="right"))
    if hi2 > lo2:
        a = max(lo2, hi2 - WINDOW)
        return float(hm[a:hi2].mean()), float(hp[a:hi2].mean()), "prev"
    return None


def team_status_map(rows, nys, team_code):
    """One team's listed players in one report game -> ({raw name: most severe status}, team_not_submitted)."""
    nys_flag = any(TEAMNAME2CODE.get(x[2]) == team_code for x in nys)
    listed = {}
    for r in rows:
        if TEAMNAME2CODE.get(r["team"]) != team_code:
            continue
        raw, s = r["player"], r["status"]
        cur = listed.get(raw)
        if cur is None or STATUS_RANK.get(s, 0) > STATUS_RANK.get(cur, 0):
            listed[raw] = s
    return listed, nys_flag


class Reports:
    """Official injury reports.  Publish time = PDF /CreationDate from the probe cache (positive values only); content from
    injury_parsed_v3.json (+ #48's local parse of 2025-11..2026-06, injury_parsed_tm.jsonl, when include_tm).  Reports whose
    content is missing are skipped (counted)."""

    def __init__(self, created: dict | None = None, content: dict | None = None, include_tm: bool = True):
        if created is None:
            created = {k: v for k, v in json.loads(PROBE_CACHE.read_text()).items() if v and v > 0}
        if content is None:
            content = {k: v for k, v in json.loads(PARSED_V3.read_text()).items() if "error" not in v}
            if include_tm and PARSED_TM.exists():
                for ln in PARSED_TM.read_text().splitlines():
                    try:
                        r = json.loads(ln)
                    except Exception:  # noqa: BLE001
                        continue
                    if "error" not in r and r.get("name") not in content:
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
        """Latest report with creation time <= d_ts (inclusive; one second later is ignored) that has rows or not-yet-submitted
        notes for this game, looking back at most 72 h.  -> (created, name, rows, nys, skipped_newer) or None."""
        hi = bisect.bisect_right(self.created, d_ts)
        lo = bisect.bisect_right(self.created, d_ts - back_s)
        skipped = 0
        for i in range(hi - 1, lo - 1, -1):
            g = self._game(self.names[i], mdy, matchup)
            if g is not None:
                return self.created[i], self.names[i], g[0], g[1], skipped
            skipped += 1
        return None


# ------------------------------------------------------------------ box scores
def day_int(x) -> int:
    return int(np.datetime64(pd.Timestamp(x).date(), "D").astype(np.int64))


class Box:
    """hoopR player_box rows (regular season only, NBA teams only) for the file years given."""

    def __init__(self, df: pd.DataFrame):
        """df columns: game_id, yr (season end year), d (ET day number), aid, name, team (Kalshi), played (bool), minutes, pts."""
        df = df.sort_values(["d", "game_id", "aid"], kind="stable").reset_index(drop=True)
        self.df = df
        self.names = {}
        for a, n in zip(df.aid.values, df.name.values):
            self.names[int(a)] = n
        pl = df[df.played]
        self.hist = {}
        for a, g in pl.groupby("aid", sort=False):
            self.hist[int(a)] = (g.d.values.astype(np.int64), g.minutes.values.astype(float), g.pts.values.astype(float),
                                 g.yr.values.astype(np.int64))
        self.played_in = {}                      # (team, game_id) -> set of athlete ids that played
        for t, gid, a in zip(pl.team.values, pl.game_id.values, pl.aid.values):
            self.played_in.setdefault((t, int(gid)), set()).add(int(a))
        tg = df.drop_duplicates(["team", "game_id"])[["team", "game_id", "d", "yr"]].sort_values(["d", "game_id"], kind="stable")
        self.team_games = {t: (g.d.values.astype(np.int64), g.game_id.values.astype(np.int64), g.yr.values.astype(np.int64))
                           for t, g in tg.groupby("team", sort=False)}

    def stats(self, aid: int, d: int, year: int):
        h = self.hist.get(aid)
        return None if h is None else player_window(*h, d, year)

    def last10_team_games(self, team: str, d: int, year: int):
        """The team's last 10 regular-season games of THIS season strictly before d -> list of game ids, or None when the team has
        played fewer than 10 games this season before d (the 'returning' rule is then not applied)."""
        t = self.team_games.get(team)
        if t is None:
            return None
        td, tgid, ty = t
        i = int(np.searchsorted(td, d, side="left"))
        cur = ty[:i] == year
        gids = tgid[:i][cur]
        if len(gids) < BACK_TEAM_GAMES:
            return None
        return [int(g) for g in gids[-BACK_TEAM_GAMES:]]


def load_box(years=range(2021, 2027)) -> Box:
    cols = ["game_id", "season", "season_type", "game_date_time", "athlete_id", "athlete_display_name", "team_abbreviation",
            "minutes", "points", "did_not_play"]
    parts = []
    for y in years:
        pb = pd.read_parquet(BOX_DIR / f"player_box_{y}.parquet", columns=cols)
        pb = pb[(pb.season_type == 2) & pb.athlete_id.notna()]
        pb = pb[pb.team_abbreviation.isin(HOOPR2KALSHI)]
        gdt = pd.to_datetime(pb.game_date_time, utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
        mins = pd.to_numeric(pb.minutes, errors="coerce")
        played = (~pb.did_not_play.astype(bool)) & mins.notna() & (mins > 0)
        parts.append(pd.DataFrame({
            "game_id": pb.game_id.astype("int64").values, "yr": pb.season.astype(int).values,
            "d": [day_int(x) for x in gdt], "aid": pb.athlete_id.astype("int64").values,
            "name": pb.athlete_display_name.values, "team": pb.team_abbreviation.map(HOOPR2KALSHI).values,
            "played": played.values, "minutes": mins.fillna(0.0).values, "pts": pd.to_numeric(pb.points, errors="coerce").fillna(0.0).values}))
    df = pd.concat(parts, ignore_index=True).drop_duplicates(["game_id", "aid"], keep="first")
    return Box(df)


# ------------------------------------------------------------------ E1 (loaded without name clashes)
_E1 = {}


def load_e1():
    """Loads E1's sm_lib, models and run_test under unique names without polluting this folder's module names
    (features / compare / run_select / run_test exist in both).  Returns a namespace dict."""
    if _E1:
        return _E1
    clash = ("run_select", "run_test", "features", "compare", "finals_check")
    saved = {k: sys.modules.pop(k) for k in clash if k in sys.modules}
    sys.path.insert(0, str(E1_MAIN))
    try:
        import sm_lib  # noqa: F401
        import models  # noqa: F401
        spec = importlib.util.spec_from_file_location("e1_run_test", E1_MAIN / "run_test.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["e1_run_test"] = mod
        spec.loader.exec_module(mod)
        if "run_select" in sys.modules:
            sys.modules["e1_run_select"] = sys.modules.pop("run_select")
        _E1.update(S=sys.modules["sm_lib"], M=sys.modules["models"], RT=mod, RS=sys.modules["e1_run_select"])
    finally:
        for k in clash:
            sys.modules.pop(k, None) if k not in saved else None
        sys.modules.update(saved)
        while str(E1_MAIN) in sys.path:
            sys.path.remove(str(E1_MAIN))
        if str(HERE) in sys.path:
            sys.path.remove(str(HERE))
        sys.path.insert(0, str(HERE))
    return _E1


def load_games(seasons=SEASONS) -> pd.DataFrame:
    """Regular-season team-games (E1's cleaning): game_id, season, date (ET), tip (UTC s), team (hoopR), opp, venue, plus Kalshi codes."""
    E = load_e1()
    tg = E["S"].load_all_team_games(list(seasons))
    g = tg[["game_id", "season", "date", "tip", "team", "opp", "venue"]].copy()
    g["team_k"] = g.team.map(HOOPR2KALSHI)
    g["opp_k"] = g.opp.map(HOOPR2KALSHI)
    assert g.team_k.notna().all() and g.opp_k.notna().all()
    g["date"] = pd.to_datetime(g.date)
    return g.sort_values(["season", "date", "game_id", "team"], kind="stable").reset_index(drop=True)


# ------------------------------------------------------------------ the feature builder
def _cands(box: Box, roster):
    cand_norm = {}
    for a in roster:
        cand_norm.setdefault(norm_display(box.names.get(a, "")), []).append(a)
    return cand_norm, None


def team_features(box: Box, team: str, d: int, year: int, roster, last_yr, listed, report_ok, window_gids, played_cur=None):
    """Features for one team in one game.  roster = athlete ids whose most recent box row before d is for `team`;
    last_yr[aid] = season-year of that row; listed = {raw name: status} from the chosen report (empty when no information);
    report_ok = a report covers the game and the team is not 'not yet submitted'; window_gids = team's last 10 games (or None).
    Returns a dict with the 4 features, key-player ids and coverage numbers."""
    roster = [a for a in roster if last_yr.get(a, 0) >= year - 1]      # team = most recent regular-season row (this season, else last)
    cn, cl = _cands(box, roster)
    st = {}
    unmatched = []
    n_listed = 0
    n_known_not_rot = 0
    for raw, s in listed.items():
        if s not in STATUS_RANK:
            continue
        n_listed += 1
        a = match_name(raw, cn, cl)
        if a is None:
            unmatched.append(f"{raw}|{s}")
            continue
        if STATUS_RANK[s] > STATUS_RANK.get(st.get(a), 0):
            st[a] = s
    out_pts = q_pts = out_min = back_pts = 0.0
    n_out_rot = n_q_rot = n_back = 0
    listed_rot = []
    for a, s in sorted(st.items()):
        w = box.stats(a, d, year)
        if w is None or w[0] < ROT_MPG:
            n_known_not_rot += 1
            continue
        listed_rot.append(f"{a}:{s}:{w[0]:.3f}")
        if s in OUT_STATUSES:
            out_pts += w[1]
            out_min += w[0]
            n_out_rot += 1
        else:
            q_pts += w[1]
            n_q_rot += 1
    key_aids = []
    for a in sorted(roster):
        w = box.stats(a, d, year)
        if w is None:
            continue
        if w[0] >= KEY_MPG:
            key_aids.append(a)
        if (report_ok and window_gids is not None and w[0] >= ROT_MPG and st.get(a) not in OUT_STATUSES
                and (played_cur is None or a in played_cur)):
            appeared = sum(1 for g in window_gids if a in box.played_in.get((team, g), ()))
            if appeared < BACK_MAX_APPEARED:
                back_pts += w[1]
                n_back += 1
    return {"out_pts": out_pts, "q_pts": q_pts, "back_pts": back_pts, "out_min": out_min / 240.0,
            "n_listed": n_listed, "n_unmatched": len(unmatched), "unmatched_names": ";".join(unmatched),
            "n_out_rot": n_out_rot, "n_q_rot": n_q_rot, "n_back": n_back, "key_aids": ";".join(str(a) for a in key_aids),
            "listed_rot": ";".join(listed_rot)}


def build_injury_features(games: pd.DataFrame, box: Box, reports: Reports) -> pd.DataFrame:
    """One row per (game_id, team).  games = load_games() output.  Uses only box rows dated strictly before the game's ET date and
    the latest report published at or before D = tip - 3600 s."""
    games = games.copy()
    games["d"] = [day_int(x) for x in games.date]
    by_date = {d: g for d, g in games.groupby("d", sort=True)}
    bdf = box.df
    brows = {d: g for d, g in bdf.groupby("d", sort=True)}
    last_team, last_yr, roster = {}, {}, {}
    played_cur = {}                                      # (team, season-year) -> athletes with minutes > 0 for the team
    recs = []
    all_days = sorted(set(by_date) | set(brows))
    for d in all_days:
        if d in by_date:
            g = by_date[d]
            # group the two team rows of each game
            for gid, gg in g.groupby("game_id", sort=False):
                tip = float(gg.tip.iloc[0])
                dts = tip - D_OFFSET_S
                away = gg[gg.venue == "away"].iloc[0] if (gg.venue == "away").any() else gg.iloc[0]
                home = gg[gg.venue == "home"].iloc[0] if (gg.venue == "home").any() else gg.iloc[-1]
                matchup = f"{away.team_k}@{home.team_k}"
                mdy = pd.Timestamp(gg.date.iloc[0]).strftime("%m/%d/%Y")
                cov = reports.last_covering(dts, mdy, matchup)
                rows, nys = (cov[2], cov[3]) if cov else ([], [])
                res = {}
                for r in gg.itertuples(index=False):
                    team = r.team_k
                    year = year_of(r.season)
                    listed, nys_flag = team_status_map(rows, nys, team) if cov else ({}, False)
                    report_ok = cov is not None and len(listed) > 0       # amendment: a team with no rows in the report -> all features 0
                    f = team_features(box, team, d, year, roster.get(team, ()), last_yr, listed if report_ok else {},
                                      report_ok, box.last10_team_games(team, d, year), played_cur.get((team, year), ()))
                    f.update(report_found=cov is not None, report_name=(cov[1] if cov else ""),
                             report_created=(float(cov[0]) if cov else np.nan), nys=bool(cov is not None and nys_flag),
                             report_skipped_newer=int(cov[4]) if cov else 0, box_found=(team, int(gid)) in box.played_in)
                    res[team] = (r, f)
                for team, (r, f) in res.items():
                    opp = r.opp_k
                    fo = res[opp][1]
                    row = {"game_id": int(gid), "season": r.season, "date": r.date, "team": r.team, "opp": r.opp, "team_k": team,
                           "opp_k": opp, "tip": int(tip), "D": int(dts)}
                    for k in BASE_FEATS:
                        row[f"{k}_own"] = f[k]
                        row[f"{k}_opp"] = fo[k]
                    for k in ("report_found", "report_name", "report_created", "nys", "report_skipped_newer", "box_found"):
                        row[k] = f[k]
                    row["n_listed_own"], row["n_unmatched_own"], row["unmatched_names_own"] = f["n_listed"], f["n_unmatched"], f["unmatched_names"]
                    row["n_out_rot_own"], row["n_q_rot_own"], row["n_back_own"] = f["n_out_rot"], f["n_q_rot"], f["n_back"]
                    row["key_aids_own"] = f["key_aids"]
                    row["listed_rot_own"] = f["listed_rot"]
                    recs.append(row)
        if d in brows:                                   # state update AFTER the day's queries (strictly-before rule)
            b = brows[d]
            for aid, team, yr, pl in zip(b.aid.values, b.team.values, b.yr.values, b.played.values):
                aid = int(aid)
                if pl:
                    played_cur.setdefault((team, int(yr)), set()).add(aid)
                old = last_team.get(aid)
                if old != team:
                    if old is not None:
                        roster[old].discard(aid)
                    roster.setdefault(team, set()).add(aid)
                    last_team[aid] = team
                last_yr[aid] = int(yr)
    F = pd.DataFrame(recs)
    return F.sort_values(["season", "date", "game_id", "team"], kind="stable").reset_index(drop=True)


# ------------------------------------------------------------------ sampling
def sample_ids(F: pd.DataFrame, seed=SEED_SAMPLE, n=N_SAMPLE) -> pd.DataFrame:
    """n (game_id, team) rows drawn uniformly (no replacement) from the rows sorted by (season, date, game_id, team); sorted."""
    P = F.sort_values(["season", "date", "game_id", "team"], kind="stable").reset_index(drop=True)
    idx = np.sort(np.random.default_rng(seed).choice(len(P), n, replace=False))
    cols = [c for c in ("game_id", "team", "team_k", "season", "date") if c in P.columns]
    return P.iloc[idx][cols].reset_index(drop=True)


# ------------------------------------------------------------------ ridge residual layer
def design(F: pd.DataFrame, variant: str) -> np.ndarray:
    cols = [f"{f}_{s}" for f in VARIANTS[variant] for s in ("own", "opp")]
    return F[cols].values.astype(float)


def variant_columns(variant: str):
    return [f"{f}_{s}" for f in VARIANTS[variant] for s in ("own", "opp")]


def standardise_fit(X):
    mu = X.mean(axis=0)
    sd = X.std(axis=0, ddof=0)
    return mu, np.where(sd > 0, sd, 1.0)


def ridge_fit(Xs, y, lam):
    """argmin sum (y - b0 - Xs b)^2 + lam |b|^2 with standardised Xs (column means 0) and an unpenalised intercept b0 = mean(y)."""
    ym = float(y.mean())
    A = Xs.T @ Xs + lam * np.eye(Xs.shape[1])
    return ym, np.linalg.solve(A, Xs.T @ (y - ym))


def ridge_predict(X, mu, sd, b0, b):
    return b0 + ((np.asarray(X, float) - mu) / sd) @ b


def loso_folds(seasons_of_rows):
    s = np.asarray(seasons_of_rows)
    return [(u, s == u) for u in sorted(set(s))]


def loso_predict(X, y, seasons_of_rows, lam):
    pred = np.full(len(y), np.nan)
    for _, te in loso_folds(seasons_of_rows):
        mu, sd = standardise_fit(X[~te])
        b0, b = ridge_fit((X[~te] - mu) / sd, y[~te], lam)
        pred[te] = ridge_predict(X[te], mu, sd, b0, b)
    return pred


def cv_lambda(X, y, seasons_of_rows, lambdas=LAMBDAS):
    """Leave-one-season-out CV on the training seasons: pooled out-of-fold MSE per lambda; lowest wins, ties -> larger lambda."""
    mse = {}
    for lam in lambdas:
        p = loso_predict(X, y, seasons_of_rows, lam)
        mse[lam] = float(np.mean((y - p) ** 2))
    return min(mse, key=lambda L_: (mse[L_], -L_)), mse


def fit_variant(F_train: pd.DataFrame, resid: np.ndarray, variant: str) -> dict:
    X = design(F_train, variant)
    lam, cv = cv_lambda(X, resid, F_train.season.values)
    mu, sd = standardise_fit(X)
    b0, b = ridge_fit((X - mu) / sd, resid, lam)
    return {"variant": variant, "features": variant_columns(variant), "lambda": lam, "cv_mse": {str(k): v for k, v in cv.items()},
            "b0": b0, "coef": b.tolist(), "mu": mu.tolist(), "sd": sd.tolist(), "n_rows": int(len(resid)),
            "train_seasons": sorted(set(F_train.season.values))}


def predict_variant(model: dict, F: pd.DataFrame) -> np.ndarray:
    X = F[model["features"]].values.astype(float)
    return ridge_predict(X, np.array(model["mu"]), np.array(model["sd"]), model["b0"], np.array(model["coef"]))


def choose(table: dict) -> str:
    """Lowest total MAE; ties -> CANDIDATE_ORDER (E1 first).  STOP rule: the caller stops when the result is 'E1'."""
    return min(table, key=lambda k: (table[k]["total_mae"], CANDIDATE_ORDER.index(k)))


# ------------------------------------------------------------------ metrics
def game_metrics(game_id, home, y, pred):
    game_id, home = np.asarray(game_id), np.asarray(home, float)
    y, pred = np.asarray(y, float), np.asarray(pred, float)
    u, inv = np.unique(game_id, return_inverse=True)
    assert (np.bincount(inv) == 2).all(), "every game needs exactly two team rows"
    tp, ta = np.bincount(inv, pred), np.bincount(inv, y)
    e = tp - ta
    return {"n_games": int(len(u)), "total_mae": float(np.abs(e).mean()), "pct_within_10": float((np.abs(e) <= WITHIN).mean()),
            "team_mae": float(np.abs(pred - y).mean()), "bias": float(e.mean()), "game_ids": u, "err": e, "tot_pred": tp, "tot_act": ta}


def strip(m):
    return {k: v for k, v in m.items() if k not in ("game_ids", "err", "tot_pred", "tot_act")}


def paired_boot_ci(d, n_boot=BOOT_N, seed=BOOT_SEED, chunk=500):
    """Game bootstrap of mean(d): (mean, 2.5th, 97.5th percentile)."""
    d = np.asarray(d, float)
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for lo in range(0, n_boot, chunk):
        k = min(chunk, n_boot - lo)
        means[lo:lo + k] = d[rng.integers(0, len(d), size=(k, len(d)))].mean(axis=1)
    return float(d.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


# ------------------------------------------------------------------ hashing / gates
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


# Everything the test stage depends on, relative to main/.  The Opus leak audit hashes exactly these.
AUDIT_FILES = (
    "e2_lib.py", "features.py", "compare.py", "run_select.py", "run_test.py",
    "../PREREGISTRATION.md", "../FEATURES_E2.md", "../frozen_e2.json", "../injury_features.parquet", "../sample_ids.csv",
    # E1: frozen model, its code and its inputs
    "../../nba_score_model/frozen.json", "../../nba_score_model/team_games_features.parquet",
    "../../nba_score_model/main/sm_lib.py", "../../nba_score_model/main/models.py", "../../nba_score_model/main/run_test.py",
    "../../nba_score_model/main/run_select.py",
    "../../nba_line_error/impl_a/lea_lib.py", "../../nba_line_error/impl_a/fa_ext.py",
    "../../nba_formula_accuracy/fa_lib.py", "../../nba_totals_grid/grid_lib.py",
    "../../nba_totals_pace/lib.py", "../../nba_totals_v2/ratings2.py", "../../nba_totals_tourney/tourney.py",
    # injury inputs
    "../../nba_model/injury_parsed_v3.json", "../../nba_teammate_out/injury_parsed_tm.jsonl",
    "../../../../data/raw/nba_injury/_probe_cache_v3.json",
    # box scores
    *[f"../../../../data/raw/nba_open/player_box_{y}.parquet" for y in range(2021, 2027)],
    *[f"../../../../data/raw/nba_open/team_box_{y}.parquet" for y in range(2022, 2027)],
    "../../../../data/raw/nba_odds_samples/closing_lines_odds_api.parquet")


def audit_hashes(base=HERE, files=AUDIT_FILES):
    return {"sha256": {f: sha256_file(Path(base) / f) for f in files}}


def check_audit_ok(study, base=HERE, files=AUDIT_FILES):
    p = Path(study) / "AUDIT_OK"
    if not p.exists():
        raise SystemExit("REFUSED: AUDIT_OK not found (only the Opus leak audit creates it).")
    try:
        got = json.loads(p.read_text())["sha256"]
    except Exception:  # noqa: BLE001
        raise SystemExit("REFUSED: AUDIT_OK is not JSON with a 'sha256' map.")
    for f in files:
        if f not in got:
            raise SystemExit(f"REFUSED: AUDIT_OK does not cover {f}.")
        if sha256_file(Path(base) / f) != got[f]:
            raise SystemExit(f"REFUSED: {f} changed since the audit (hash mismatch).")


def check_frozen_hash(study):
    study = Path(study)
    fz, shaf = study / "frozen_e2.json", study / "frozen_e2.sha256"
    if not fz.exists():
        raise SystemExit("REFUSED: frozen_e2.json not found.")
    if not shaf.exists():
        raise SystemExit("REFUSED: frozen_e2.sha256 not found.")
    want = shaf.read_text().split()[0].strip().lower()
    got = sha256_file(fz)
    if want != got:
        raise SystemExit(f"REFUSED: frozen_e2.json hash {got} != frozen_e2.sha256 {want}.")
    return json.loads(fz.read_text())


def check_agreement_pass(study=STUDY, features_path=None):
    """AGREEMENT.md must say 'STATUS: PASS' and its FEATURES_SHA256 must equal the hash of the current features parquet."""
    p = Path(study) / "AGREEMENT.md"
    if not p.exists():
        raise SystemExit("REFUSED: AGREEMENT.md not found (run compare.py first).")
    txt = p.read_text(encoding="utf-8")
    if "STATUS: PASS" not in txt:
        raise SystemExit("REFUSED: AGREEMENT.md does not say STATUS: PASS.")
    want = next((ln.split(":", 1)[1].strip() for ln in txt.splitlines() if ln.startswith("FEATURES_SHA256:")), "")
    fp = Path(features_path) if features_path else Path(study) / "injury_features.parquet"
    if not fp.exists() or sha256_file(fp) != want:
        raise SystemExit("REFUSED: injury_features.parquet changed since AGREEMENT.md was written (hash mismatch).")
