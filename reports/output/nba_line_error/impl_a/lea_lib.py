"""Research log #57, implementation A: shared library (paths, hoopR loading, team maps, statistics, gates).

Everything here is written for impl A only; it shares no code with impl B.  Pure functions where possible
(unit-tested in test_line_error_a.py)."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent                      # reports/output/nba_line_error
ROOT = STUDY.parents[2]                  # repo root
BOX = ROOT / "data" / "raw" / "nba_open"
ODDS_DB = ROOT / "data" / "raw" / "nba_odds_samples" / "OddsData.sqlite"
CLOSING = ROOT / "data" / "raw" / "nba_odds_samples" / "closing_lines_odds_api.parquet"

SEED = 20261022
N_BOOT = 10000
BREAK_EVEN = 110 / 210                   # 0.5238095...
LINE_LO, LINE_HI = 170.0, 275.0
MEAN_TOL = 30.0
MIN_PRIOR_FOR_MEAN = 5
DISPUTE_TOL = 1.0
MIN_N_SELECT = 300
MIN_N_VALID = 150
MIN_N_TEST = 300
MIN_N_KALSHI = 150
FEATS = [f"f{i}" for i in range(1, 11)]

DISCOVERY = tuple(f"{y}-{(y + 1) % 100:02d}" for y in range(2008, 2019))        # 2008-09 .. 2018-19
VALIDATION = ("2020-21", "2021-22")      # amendment 2026-10-22: 2019-20 dropped (line timing unknown); closing-source games only
CLOSING_SRC = "closing_lines"
TEST = ("2022-23", "2023-24", "2024-25", "2025-26")
ALL_SEASONS = DISCOVERY + ("2019-20",) + VALIDATION + TEST     # 2019-20 is built but belongs to no stage
CLOSING_FROM = "2020-21"                 # closing_lines is the line source from this season on

# hoopR team codes of the 30 NBA franchises (incl. the old NJ / SEA codes); everything else (All-Star, USA, LEB, ...) is not NBA.
NBA_HOOPR = {"ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GS", "HOU", "IND", "LAC", "LAL", "MEM", "MIA",
             "MIL", "MIN", "NJ", "NO", "NY", "OKC", "ORL", "PHI", "PHX", "POR", "SA", "SAC", "SEA", "TOR", "UTAH", "WSH"}
# franchise key = Kalshi code (#37 lib.TEAM_MAP) with the two relocated franchises mapped onto their current code.
FRANCHISE = {"ATL": "ATL", "BKN": "BKN", "BOS": "BOS", "CHA": "CHA", "CHI": "CHI", "CLE": "CLE", "DAL": "DAL", "DEN": "DEN",
             "DET": "DET", "GS": "GSW", "HOU": "HOU", "IND": "IND", "LAC": "LAC", "LAL": "LAL", "MEM": "MEM", "MIA": "MIA",
             "MIL": "MIL", "MIN": "MIN", "NJ": "BKN", "NO": "NOP", "NY": "NYK", "OKC": "OKC", "ORL": "ORL", "PHI": "PHI",
             "PHX": "PHX", "POR": "POR", "SA": "SAS", "SAC": "SAC", "SEA": "OKC", "TOR": "TOR", "UTAH": "UTA", "WSH": "WAS"}
NAME_TO_FRANCHISE = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "New Jersey Nets": "BKN",
    "Charlotte Bobcats": "CHA", "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET", "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU", "Indiana Pacers": "IND", "LA Clippers": "LAC", "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP", "New Orleans Hornets": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Seattle SuperSonics": "OKC", "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS"}
ALTITUDE_HOOPR = {"DEN", "UTAH"}


# ------------------------------------------------------------------ seasons
def label_of(year: int) -> str:
    """hoopR file year (end year) -> '2008-09' style label of the season that ends in `year`."""
    return f"{year - 1}-{year % 100:02d}"


def year_of(season: str) -> int:
    a = int(season[:4])
    assert season == f"{a}-{(a + 1) % 100:02d}", season
    return a + 1


def season_of_date_for_unmatched(d: pd.Timestamp) -> str:
    """Only used to bucket line rows that matched no hoopR game: Oct 15 or later starts the new season; Jan-Oct 14 belongs to
    the one that began the previous autumn (the 2019-20 bubble finished on Oct 11 2020)."""
    y = d.year
    start = y if (d.month > 10 or (d.month == 10 and d.day >= 15) or d.month == 11 or d.month == 12) else y - 1
    return f"{start}-{(start + 1) % 100:02d}"


def old_odds_date(date_str: str) -> pd.Timestamp:
    """OddsData 'X' tables store 'YYYY-YY-MMDD' (e.g. '2008-09-1028' = 2008-10-28).  Month >= 8 -> start year, else end year
    (exception: 2019-20 months 8-9 and Oct 1-15 are 2020, the bubble)."""
    s = str(date_str)
    if len(s) == 10 and s[4] == "-" and s[7] == "-":      # already a plain date
        return pd.Timestamp(s)
    start = int(s[:4])
    mm, dd = int(s[-4:-2]), int(s[-2:])
    if s[:7] == "2019-20" and (mm in (8, 9) or (mm == 10 and dd <= 15)):    # the 2019-20 bubble ran Jul 30 - Oct 11 2020
        return pd.Timestamp(2020, mm, dd)
    return pd.Timestamp(start if mm >= 8 else start + 1, mm, dd)


# ------------------------------------------------------------------ hoopR
def fix_tov(total_turnovers, turnovers):
    """total_turnovers if finite and >= turnovers, else turnovers (hoopR has NaN / zero total_turnovers in places;
    the whole 2017-18 file has NaN, so it falls back to `turnovers`)."""
    tot = np.asarray(total_turnovers, float)
    tur = np.asarray(turnovers, float)
    ok = np.isfinite(tot) & (tot >= tur)
    return np.where(ok, tot, tur)


def tov_diagnostics(tb: pd.DataFrame) -> dict:
    """Regular-season rows of a hoopR file: ratio of mean(total_turnovers) to mean(turnovers) and the rule that applies.
    hoopR's 2007-08 .. 2011-12 files carry total_turnovers = 2 x turnovers (the team-turnover column duplicates the player
    column, ratio ~2.0); from 2012-13 the ratio is ~1.04 (a real team-turnover share); 2017-18 has no total_turnovers at all."""
    r = tb[tb.season_type == 2]
    tot, tur = r.total_turnovers.astype(float), r.turnovers.astype(float)
    ratio = float(tot.mean() / tur.mean()) if tot.notna().any() else float("nan")
    defect = bool(np.isfinite(ratio) and ratio > 1.5)
    return {"ratio_total_over_turnovers": ratio, "doubled_total_turnovers": defect,
            "rule": ("turnovers (total_turnovers is doubled)" if defect else
                     ("turnovers (no total_turnovers)" if not np.isfinite(ratio) else "total_turnovers if finite and >= turnovers, else turnovers")),
            "rows_total_lt_turnovers": int((tot < tur).sum()), "rows_total_nan": int(tot.isna().sum())}


def tov_for_box(tb: pd.DataFrame) -> np.ndarray:
    """TOV per row of a hoopR file under the rule in tov_diagnostics."""
    d = tov_diagnostics(tb)
    if d["doubled_total_turnovers"]:
        return tb.turnovers.values.astype(float)
    return fix_tov(tb.total_turnovers.values, tb.turnovers.values)


def read_year_fixed(year: int) -> pd.DataFrame:
    """read_year, with total_turnovers replaced by turnovers where hoopR doubled it.  Used as the `reader` of #56's grid so that
    f8 sees the same possessions as f5 (rows are otherwise untouched)."""
    tb = read_year(year)
    if tov_diagnostics(tb)["doubled_total_turnovers"]:
        tb = tb.copy()
        tb["total_turnovers"] = tb["turnovers"]
    return tb


def possessions(fga, oreb, tov, fta, factor=0.96):
    """Pre-registration: 0.96 x (FGA - OREB + TOV + 0.44 x FTA)."""
    return factor * (np.asarray(fga, float) - np.asarray(oreb, float) + np.asarray(tov, float) + 0.44 * np.asarray(fta, float))


def read_year(year: int) -> pd.DataFrame:
    return pd.read_parquet(BOX / f"team_box_{year}.parquet")


def team_games_from_box(tb: pd.DataFrame, season: str) -> pd.DataFrame:
    """Long table, one row per team-game, regular season (season_type 2), both teams NBA franchises.
    Columns: game_id, season, date (ET, datetime64[ns]), tip (UTC epoch s), team, opp (hoopR codes), venue, pts, opp_pts,
    fga, fta, oreb, tov, tpa, poss, opp_poss, pace."""
    tb = tb[(tb.season_type == 2) & tb.team_abbreviation.isin(NBA_HOOPR) & tb.opponent_team_abbreviation.isin(NBA_HOOPR)].copy()
    gdt = pd.to_datetime(tb.game_date_time)
    if gdt.dt.tz is None:
        gdt = gdt.dt.tz_localize("UTC")
    et = gdt.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    tip = ((gdt.dt.tz_convert("UTC") - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)).astype("int64")
    tov = tov_for_box(tb)
    out = pd.DataFrame({
        "game_id": tb.game_id.astype("int64").values, "season": season, "date": et.values, "tip": tip.values,
        "team": tb.team_abbreviation.values, "opp": tb.opponent_team_abbreviation.values, "venue": tb.team_home_away.values,
        "pts": tb.team_score.values.astype(float), "opp_pts": tb.opponent_team_score.values.astype(float),
        "fga": tb.field_goals_attempted.values.astype(float), "fta": tb.free_throws_attempted.values.astype(float),
        "oreb": tb.offensive_rebounds.values.astype(float), "tov": tov.astype(float),
        "tpa": tb.three_point_field_goals_attempted.values.astype(float)})
    out["poss"] = possessions(out.fga, out.oreb, out.tov, out.fta)
    # only games that have exactly one home and one away row
    cnt = out.groupby("game_id").venue.agg(lambda v: (sorted(v) == ["away", "home"]))
    good = cnt[cnt].index
    out = out[out.game_id.isin(good)].copy()
    opp = out[["game_id", "team", "poss"]].rename(columns={"team": "opp", "poss": "opp_poss"})
    out = out.merge(opp, on=["game_id", "opp"], how="inner")
    out["pace"] = (out.poss + out.opp_poss) / 2.0
    return out.sort_values(["date", "tip", "game_id", "venue"], kind="stable").reset_index(drop=True)


def load_team_games(seasons=None) -> pd.DataFrame:
    """All regular-season team-games for the seasons (default 2007-08 .. 2025-26)."""
    seasons = list(seasons) if seasons else [label_of(y) for y in range(2008, 2027)]
    parts = [team_games_from_box(read_year(year_of(s)), s) for s in seasons]
    return pd.concat(parts, ignore_index=True)


def games_from_team_games(tg: pd.DataFrame) -> pd.DataFrame:
    """One row per game: game_id, season, date, tip, away, home (hoopR codes), away_pts, home_pts, actual."""
    h = tg[tg.venue == "home"]
    a = tg[tg.venue == "away"].set_index("game_id")
    g = pd.DataFrame({"game_id": h.game_id.values, "season": h.season.values, "date": h.date.values, "tip": h.tip.values,
                      "home": h.team.values, "away": a.loc[h.game_id.values, "team"].values,
                      "home_pts": h.pts.values, "away_pts": a.loc[h.game_id.values, "pts"].values})
    g["actual"] = g.home_pts + g.away_pts
    return g.sort_values(["date", "tip", "game_id"], kind="stable").reset_index(drop=True)


# ------------------------------------------------------------------ statistics
def wilson(wins: int, n: int, z: float = 1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = wins / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def side_stats(side, y) -> dict:
    """side: +1 over, -1 under, 0 no bet; y = actual - line.  Pushes (y == 0) are excluded from n."""
    side, y = np.asarray(side, float), np.asarray(y, float)
    bet = side != 0
    push = bet & (y == 0)
    m = bet & ~push
    win = ((side > 0) & (y > 0)) | ((side < 0) & (y < 0))
    n, w = int(m.sum()), int((win & m).sum())
    lo, hi = wilson(w, n)
    return {"n": n, "wins": w, "rate": (w / n if n else float("nan")), "lb": lo, "ub": hi, "pushes": int(push.sum())}


def boot_mean(x, n_boot=N_BOOT, seed=SEED, chunk=500):
    x = np.asarray(x, float)
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for lo in range(0, n_boot, chunk):
        k = min(chunk, n_boot - lo)
        idx = rng.integers(0, len(x), size=(k, len(x)))
        means[lo:lo + k] = x[idx].mean(axis=1)
    return float(x.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


# ------------------------------------------------------------------ ridge (standardised features, unpenalised intercept)
def standardise_fit(X):
    mu = X.mean(axis=0)
    sd = X.std(axis=0, ddof=0)
    sd = np.where(sd > 0, sd, 1.0)
    return mu, sd


def ridge_fit(Xs, y, lam):
    """Xs already standardised.  Minimises sum (y - b0 - Xs b)^2 + lam * |b|^2.  Returns (b0, b)."""
    ym = float(y.mean())
    p = Xs.shape[1]
    A = Xs.T @ Xs + lam * np.eye(p)
    b = np.linalg.solve(A, Xs.T @ (y - ym))
    return ym, b            # Xs has mean 0 over the fit rows, so b0 = mean(y)


def ridge_predict(X, mu, sd, b0, b):
    return b0 + ((np.asarray(X, float) - mu) / sd) @ b


LAMBDAS = (0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0, 100000.0)


def season_folds(seasons_of_rows, k=5):
    """Fold id per row; whole seasons go to one fold (sorted season i -> fold i % k)."""
    uniq = sorted(set(seasons_of_rows))
    fm = {s: i % k for i, s in enumerate(uniq)}
    return np.array([fm[s] for s in seasons_of_rows])


def cv_lambda(X, y, seasons_of_rows, lambdas=LAMBDAS, k=5):
    """5-fold CV with folds by season; standardisation re-fitted on each training part.  Returns (best_lambda, {lam: mse}).
    Ties go to the larger lambda."""
    fold = season_folds(seasons_of_rows, k)
    res = {}
    for lam in lambdas:
        se, cnt = 0.0, 0
        for f in range(k):
            te = fold == f
            tr = ~te
            if te.sum() == 0 or tr.sum() == 0:
                continue
            mu, sd = standardise_fit(X[tr])
            b0, b = ridge_fit((X[tr] - mu) / sd, y[tr], lam)
            e = y[te] - ridge_predict(X[te], mu, sd, b0, b)
            se += float((e ** 2).sum())
            cnt += int(te.sum())
        res[lam] = se / cnt
    best = min(res, key=lambda L: (res[L], -L))
    return best, res


# ------------------------------------------------------------------ decile rules
def decile_cuts(x):
    """q10 and q90 (numpy linear interpolation) plus all 9 decile cut points of the non-NaN values of x."""
    v = np.asarray(x, float)
    v = v[np.isfinite(v)]
    qs = np.quantile(v, np.arange(1, 10) / 10.0)
    return qs


def is_discrete(x, k=20):
    """Amendment 2026-10-22: a feature with fewer than 20 distinct finite values (f4, f7) is discrete."""
    v = np.asarray(x, float)
    return len(np.unique(v[np.isfinite(v)])) < k


def tail_masks_rule(x, r):
    """Tails of a frozen/selection U rule r: discrete -> games at the min / max value; continuous -> x <= q10 / x >= q90."""
    x = np.asarray(x, float)
    if r.get("discrete"):
        with np.errstate(invalid="ignore"):
            return (x == r["xmin"]) & np.isfinite(x), (x == r["xmax"]) & np.isfinite(x)
    return tail_masks(x, r["q10"], r["q90"])


def loso_predict(X, y, seasons_of_rows, lam):
    """Leave-one-season-out predictions at fixed lambda (standardisation refitted on each training part)."""
    pred = np.full(len(y), np.nan)
    seasons_of_rows = np.asarray(seasons_of_rows)
    for s in sorted(set(seasons_of_rows)):
        te = seasons_of_rows == s
        mu, sd = standardise_fit(X[~te])
        b0, b = ridge_fit((X[~te] - mu) / sd, y[~te], lam)
        pred[te] = ridge_predict(X[te], mu, sd, b0, b)
    return pred


def tail_masks(x, q10, q90):
    """bottom tail: x <= q10 ; top tail: x >= q90 (NaN -> False)."""
    x = np.asarray(x, float)
    with np.errstate(invalid="ignore"):
        return (x <= q10) & np.isfinite(x), (x >= q90) & np.isfinite(x)


def decile_table(x, y):
    """Mean y per decile bin (bins by the 9 cut points; ties go to the upper bin via searchsorted right) - reporting only."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    cuts = decile_cuts(x[ok])
    b = np.searchsorted(cuts, x[ok], side="right")
    rows = []
    for k in range(10):
        m = b == k
        rows.append({"decile": k + 1, "n": int(m.sum()), "mean_y": (float(y[ok][m].mean()) if m.any() else None),
                     "x_lo": (float(x[ok][m].min()) if m.any() else None), "x_hi": (float(x[ok][m].max()) if m.any() else None)})
    return rows


# ------------------------------------------------------------------ hashing / gates
def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def read_json(p):
    return json.loads(Path(p).read_text())


def check_frozen_hash(d: Path):
    """frozen.json must exist and its SHA-256 must equal the one in frozen.sha256.  Returns the frozen dict."""
    d = Path(d)
    fz, shaf = d / "frozen.json", d / "frozen.sha256"
    if not fz.exists():
        raise SystemExit("REFUSED: frozen.json not found (selection did not freeze a version).")
    if not shaf.exists():
        raise SystemExit("REFUSED: frozen.sha256 not found.")
    want = shaf.read_text().split()[0].strip().lower()
    got = sha256_file(fz)
    if want != got:
        raise SystemExit(f"REFUSED: frozen.json hash {got} != frozen.sha256 {want}.")
    return json.loads(fz.read_text())


# Code files whose bytes the test stage depends on (relative to impl_a/).  The leak audit hashes exactly these.
AUDIT_FILES = ("lea_lib.py", "clean.py", "features.py", "fa_ext.py", "run_select.py", "run_validate.py", "run_test.py",
               "compare.py", "../PREREGISTRATION.md",
               "../../nba_formula_accuracy/fa_lib.py", "../../nba_totals_grid/grid_lib.py", "../../nba_totals_grid/grid_bets.py",
               "../../nba_totals_grid/grid_run.py", "../../nba_totals_pace/lib.py", "../../nba_totals_pace/prep.py",
               "../../nba_totals_pace/run.py", "../../nba_totals_v2/ratings2.py", "../../nba_totals_v2/run.py",
               "../../nba_totals_tourney/tourney.py")


def audit_hashes(base=HERE, files=AUDIT_FILES) -> dict:
    return {"sha256": {f: sha256_file(Path(base) / f) for f in files}}


def check_audit_ok(d: Path, base=HERE, files=AUDIT_FILES):
    """AUDIT_OK must be JSON {"sha256": {path: hash}} covering every file in `files`; any mismatch refuses."""
    p = Path(d) / "AUDIT_OK"
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


# ------------------------------------------------------------------ versions and frozen rules
M_VERSIONS = [("M_tau1", 1.0), ("M_tau2", 2.0), ("M_tau3", 3.0)]


def u_version_names():
    return [f"U_{f}_{t}" for f in FEATS for t in ("bottom", "top")]


def all_version_names():
    return [n for n, _ in M_VERSIONS] + u_version_names()


def apply_rule(frozen: dict, F: pd.DataFrame) -> np.ndarray:
    """Side (+1 over, -1 under, 0 none) for each row of F (needs f1..f10) under a frozen version."""
    r = frozen["rule"]
    if r["kind"] == "M":
        X = F[FEATS].values.astype(float)
        ok = np.isfinite(X).all(axis=1)
        pred = np.full(len(F), np.nan)
        if ok.any():
            pred[ok] = ridge_predict(X[ok], np.array(r["mu"]), np.array(r["sd"]), r["b0"], np.array(r["coef"]))
        side = np.zeros(len(F))
        side[ok & (pred >= r["tau"])] = 1.0
        side[ok & (pred <= -r["tau"])] = -1.0
        return side
    x = F[r["feature"]].values.astype(float)
    bot, top = tail_masks_rule(x, r)
    m = bot if r["tail"] == "bottom" else top
    return np.where(m, float(r["sign"]), 0.0)


def check_agreement_pass(study: Path = STUDY):
    """Selection gate: AGREEMENT.md must say 'STATUS: PASS' and its A_SHA256 must equal the SHA-256 of the current features_a.parquet
    (so a later rebuild of the features cannot ride on an old agreement)."""
    p = Path(study) / "AGREEMENT.md"
    if not p.exists():
        raise SystemExit("REFUSED: AGREEMENT.md not found (run compare.py against impl B first).")
    txt = p.read_text(encoding="utf-8")
    if "STATUS: PASS" not in txt:
        raise SystemExit("REFUSED: AGREEMENT.md does not say STATUS: PASS.")
    want = next((ln.split(":", 1)[1].strip() for ln in txt.splitlines() if ln.startswith("A_SHA256:")), "")
    fa_ = Path(study) / "features_a.parquet"
    if not fa_.exists() or sha256_file(fa_) != want:
        raise SystemExit("REFUSED: features_a.parquet changed since AGREEMENT.md was written (hash mismatch).")
