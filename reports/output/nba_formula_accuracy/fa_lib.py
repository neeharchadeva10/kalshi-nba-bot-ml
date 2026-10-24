"""Research log #56 library: season-generalised team grid (#42), games/outcome tables, closing lines, threshold
selection, bootstrap, gates.  Pure functions where possible; unit-tested in test_formula_accuracy.py.

Season labels: "2023-24" has hoopR file year 2024 (end year).  Prior of season S = regular season S-1; the constants
(rest/back-to-back effects, shrink slope, four-factor coefficients) are fitted on S-1 only (walk-forward inside S-1 with
S-2 as its prior, exactly as #42 did for 2025-26 with 2024-25 / 2023-24).  For S = 2022-23 there is no 2020-21 on disk:
the fit on 2021-22 uses NO prior-season blend (k = 0, games where either team has no earlier game are dropped)."""
from __future__ import annotations

import hashlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
GRID = HERE.parent / "nba_totals_grid"
if str(GRID) not in sys.path:
    sys.path.insert(0, str(GRID))
import grid_lib as gl  # noqa: E402
from grid_lib import lib, r2, tn  # noqa: E402

BOX = ROOT / "data" / "raw" / "nba_open"
CLOSING = ROOT / "data" / "raw" / "nba_odds_samples" / "closing_lines_odds_api.parquet"
SEED = 20261022
N_BOOT = 10000
BREAK_EVEN = 110 / 210         # -110 exactly
THRESHOLDS = (2, 4, 6, 8)
MIN_N = 150
SELECTION = ("2022-23", "2023-24")
CONFIRM = ("2024-25", "2025-26")
LIVE = "2026-27"

READ_LOG: list = []            # every team/player box year read through read_box (tests check it)


def year_of(season: str) -> int:
    """'2023-24' -> 2024 (hoopR file year)."""
    a = int(season[:4])
    assert season == f"{a}-{(a + 1) % 100:02d}", season
    return a + 1


def label_of(year: int) -> str:
    return f"{year - 1}-{year % 100:02d}"


def prev_season(season: str) -> str:
    return label_of(year_of(season) - 1)


# ------------------------------------------------------------------ box scores
def read_box(year: int, kind: str = "team") -> pd.DataFrame:
    READ_LOG.append((kind, int(year)))
    return pd.read_parquet(BOX / f"{kind}_box_{year}.parquet")


@contextmanager
def fit_k(k):
    """Temporarily set grid_lib.K_FIT (the k at which #42 fits its constants).  k = 20 is #42's own setting."""
    old = gl.K_FIT
    gl.K_FIT = k
    try:
        yield
    finally:
        gl.K_FIT = old


class World:
    """Everything a season needs: season (ratings), consts (fitted on S-1 only), team-games, notes."""
    pass


def team_games_of(year, types, reader=None):
    return gl.team_games_g((reader or read_box)(year), types)


def drop_no_history(season_obj, games):
    """Games whose away AND home team both have at least one earlier game (date < decision date) in the season."""
    dates = gl.decision_dates(games)
    ok = np.zeros(len(games), bool)
    for j, (D, a, h) in enumerate(zip(dates, games.away.values, games.home.values)):
        ok[j] = (season_obj.cur_rating("season", a, D) is not None) and (season_obj.cur_rating("season", h, D) is not None)
    return games[ok].reset_index(drop=True)


def fit_constants_for(prev_year, reader=None, ff_coef=None):
    """Constants fitted on regular season `prev_year`.  Needs prev_year-1 as its prior; if that file does not exist
    (prev_year = 2022, i.e. S = 2022-23) the fit uses no prior blend.  Returns (consts, note)."""
    cur = team_games_of(prev_year, r2.CUR_SEASON_TYPES, reader)
    reg = cur[cur.season_type == 2]
    ff = ff_coef if ff_coef is not None else gl.fit_four_factor(reg)
    games_fit = tn.fit_games_2425(cur)
    if prev_year - 1 < 2022:                          # no 2020-21 box scores on disk
        season_fit = gl.Season(reg, cur, ff)        # prior = the fit season itself, used ONLY for n = 0 / league level
        games_fit = drop_no_history(season_fit, games_fit)
        with fit_k(0):
            consts = gl.fit_constants(season_fit, games_fit)
        return consts, (f"no prior-season blend: constants fitted on {label_of(prev_year)} with k = 0 "
                        f"({len(games_fit)} games with history on both teams; league-level shift uses the in-sample "
                        f"season level)")
    pp = team_games_of(prev_year - 1, (2,), reader)
    season_fit = gl.Season(pp, cur, ff)
    consts = gl.fit_constants(season_fit, games_fit)
    return consts, f"fitted on {label_of(prev_year)} (k = {gl.K_FIT}, prior {label_of(prev_year - 1)})"


def build_world(season: str, reader=None, with_consts=True):
    """prior = S-1 regular season; current = S team-games (rows used only if dated before the game's ET date);
    four-factor coefficients and every constant from S-1 only."""
    y = year_of(season)
    w = World()
    w.season_label = season
    w.prev_tg = team_games_of(y - 1, (2,), reader)
    w.cur_tg = team_games_of(y, r2.CUR_SEASON_TYPES, reader)
    w.ff_coef, w.ff_r2 = gl.fit_four_factor(w.prev_tg)
    w.season = gl.Season(w.prev_tg, w.cur_tg, w.ff_coef)
    w.consts, w.const_note = (fit_constants_for(y - 1, reader, w.ff_coef) if with_consts else (None, "not fitted"))
    return w


# ------------------------------------------------------------------ games / outcomes
def games_frame(world, regular_only=True):
    """One row per regular-season game of the world's season, NO outcome columns.  T = hoopR scheduled start (UTC epoch).
    Columns: game_id, date (ET date), tip, T, away, home, season."""
    tg = world.cur_tg
    if regular_only:
        tg = tg[tg.season_type == 2]
    a = tg[tg.venue == "away"]
    g = pd.DataFrame({"game_id": a.game_id.values, "date": a.date.values, "tip": a.tip.values, "T": a.tip.values,
                      "away": a.team.values, "home": a.opp.values, "season": world.season_label})
    g = g.drop_duplicates("game_id").sort_values(["tip", "game_id"], kind="stable").reset_index(drop=True)
    return g


def actuals_frame(world, regular_only=True):
    """game_id -> actual total (hoopR final, OT included).  Kept separate from games_frame on purpose."""
    tg = world.cur_tg
    if regular_only:
        tg = tg[tg.season_type == 2]
    a = tg[tg.venue == "away"].drop_duplicates("game_id")
    return pd.Series((a.pts + a.opp_pts).values, index=a.game_id.values, name="actual")


def load_closing(path=None):
    """Sportsbook consensus closing totals: game_id (hoopR/ESPN id), line.  Dropped: NaN, outside 180-270, and rows whose
    snapshot is at or after the odds API's commence_time (93 of 7,942 rows; possible in-play lines).  Builder note."""
    p = pd.read_parquet(path or CLOSING)
    p = p.drop_duplicates("game_id")
    if "snapshot" in p and "commence_time" in p:           # conservative: a snapshot at/after commence may be an in-play line
        lead = (pd.to_datetime(p.commence_time, utc=True) - pd.to_datetime(p.snapshot, utc=True)).dt.total_seconds()
        p = p[lead > 0]
    p = p[np.isfinite(p.over_under) & (p.over_under >= 180) & (p.over_under <= 270)]
    return pd.Series(p.over_under.values.astype(float), index=p.game_id.values, name="line")


# ------------------------------------------------------------------ metrics
def mae_rmse_bias(proj, actual):
    e = np.asarray(proj, float) - np.asarray(actual, float)
    return {"n": int(len(e)), "mae": float(np.abs(e).mean()), "rmse": float(np.sqrt((e ** 2).mean())),
            "bias": float(e.mean())}           # bias = mean(proj - actual); lean shift = -bias


def lean_shift(proj, actual):
    """mean(actual - projection)."""
    return float((np.asarray(actual, float) - np.asarray(proj, float)).mean())


def wilson(wins, n, z=1.96):
    """Wilson score interval (lo, hi) for a proportion."""
    if n == 0:
        return float("nan"), float("nan")
    p = wins / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return float(c - h), float(c + h)


def side_table(proj, line, actual, T):
    """Formula-side vs the line at disagreement >= T.  Side = over if proj > line, under if proj < line (no bet at
    proj == line).  Pushes (actual == line) are excluded.  Returns dict n, wins, rate, lo, hi, pushes."""
    proj, line, actual = (np.asarray(x, float) for x in (proj, line, actual))
    d = proj - line
    m = (np.abs(d) >= T) & (d != 0)
    push = m & (actual == line)
    m = m & ~push
    win = np.where(d > 0, actual > line, actual < line)
    n, w = int(m.sum()), int((win & m).sum())
    lo, hi = wilson(w, n)
    return {"T": T, "n": n, "wins": w, "rate": (w / n if n else float("nan")), "lb": lo, "ub": hi, "pushes": int(push.sum())}


def select_threshold(proj, line, actual, thresholds=THRESHOLDS, min_n=MIN_N):
    """Per T the side table; best = highest lower bound among n >= min_n (ties -> smaller T).  Returns (table, best|None)."""
    tab = [side_table(proj, line, actual, T) for T in thresholds]
    ok = [t for t in tab if t["n"] >= min_n and np.isfinite(t["lb"])]
    best = max(ok, key=lambda t: (t["lb"], -t["T"])) if ok else None
    return tab, best


def choose_primary(forms):
    """forms: {name: {'best': side-table|None, ...}}.  Primary = the form with the highest best LB (ties -> sorted name)."""
    cand = {k: v["best"] for k, v in forms.items() if v.get("best")}
    if not cand:
        return None
    return max(sorted(cand), key=lambda k: cand[k]["lb"])


def paired_boot_mae_diff(proj, line, actual, n_boot=N_BOOT, seed=SEED, chunk=500):
    """Paired game bootstrap of mean(|proj - actual| - |line - actual|); returns (point, lo, hi) (2.5/97.5 pct)."""
    d = np.abs(np.asarray(proj, float) - actual) - np.abs(np.asarray(line, float) - actual)
    return boot_mean(d, n_boot, seed, chunk)


def boot_mean(x, n_boot=N_BOOT, seed=SEED, chunk=500):
    x = np.asarray(x, float)
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for lo in range(0, n_boot, chunk):
        k = min(chunk, n_boot - lo)
        idx = rng.integers(0, len(x), size=(k, len(x)))
        means[lo:lo + k] = x[idx].mean(axis=1)
    return float(x.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


# ------------------------------------------------------------------ baselines
def baseline_37(world, games):
    """#37: prior-season ratings only (no current-season blend, no league shift): lib.project_total on the S-1 ratings.
    (Uses r2.prior_from_tg, i.e. with #38's turnover fix; #37's own build_ratings differs only where total_turnovers is NaN.)"""
    out = np.empty(len(games))
    for j, (a, h) in enumerate(zip(games.away.values, games.home.values)):
        out[j] = lib.project_total(world.season.prev[a], world.season.prev[h])[2]
    return out


DEFAULT_FLAT = gl.flat_from_spec(gl.spec_from_names(gl.DEFAULTS))     # #38 at k = 20 (the user's formula, blended)


# ------------------------------------------------------------------ selection sums (one season at a time)
def season_sums(P, actual, has_line, chunk=2000):
    """P (S, N) float64 projections; actual (N,), has_line (N,) bool.  Returns per-formula sums:
    n, sum|e|, sum e^2, sum e (e = proj - actual), and sum|e| over the games with a line."""
    S = P.shape[0]
    out = {k: np.zeros(S) for k in ("sabs", "ssq", "se", "sabs_line")}
    for lo in range(0, S, chunk):
        e = P[lo:lo + chunk] - actual
        out["sabs"][lo:lo + chunk] = np.abs(e).sum(axis=1)
        out["ssq"][lo:lo + chunk] = (e ** 2).sum(axis=1)
        out["se"][lo:lo + chunk] = e.sum(axis=1)
        out["sabs_line"][lo:lo + chunk] = np.abs(e[:, has_line]).sum(axis=1)
    out["n"] = int(len(actual))
    out["n_line"] = int(has_line.sum())
    return out


def pool_sums(parts):
    n = sum(p["n"] for p in parts)
    nl = sum(p["n_line"] for p in parts)
    agg = {k: sum(p[k] for p in parts) for k in ("sabs", "ssq", "se", "sabs_line")}
    return {"n": n, "n_line": nl, "mae": agg["sabs"] / n, "rmse": np.sqrt(agg["ssq"] / n), "bias": agg["se"] / n,
            "mae_line_games": agg["sabs_line"] / max(nl, 1)}


def rank_formulas(pooled):
    """Indices sorted by pooled MAE ascending; ties -> smaller flat index (stable)."""
    return np.argsort(pooled["mae"], kind="stable")


# ------------------------------------------------------------------ gates / hashing
def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


AUDIT_FILES = ("fa_lib.py", "run_confirm.py", "player_model.py", "repro_check.py",
               "../nba_totals_grid/grid_lib.py", "../nba_totals_grid/grid_bets.py", "../nba_totals_grid/grid_run.py",
               "../nba_totals_pace/lib.py", "../nba_totals_pace/prep.py", "../nba_totals_pace/run.py",
               "../nba_totals_v2/ratings2.py", "../nba_totals_v2/run.py", "../nba_totals_tourney/tourney.py",
               "../nba_teammate_out/tm_lib.py")


def audit_hashes(base=HERE, files=AUDIT_FILES):
    return {"sha256": {f: sha256_file(Path(base) / f) for f in files}}


def check_audit_ok(d: Path, base=HERE, files=AUDIT_FILES):
    """AUDIT_OK must hold JSON {"sha256": {relative_path: hash}} covering every file in `files`; any mismatch refuses."""
    try:
        got = json.loads((Path(d) / "AUDIT_OK").read_text())["sha256"]
    except Exception:  # noqa: BLE001
        raise SystemExit("REFUSED: AUDIT_OK is not JSON with a 'sha256' map.")
    for f in files:
        if f not in got:
            raise SystemExit(f"REFUSED: AUDIT_OK does not cover {f}.")
        if sha256_file(Path(base) / f) != got[f]:
            raise SystemExit(f"REFUSED: {f} changed since the audit (hash mismatch).")


def check_confirm_gates(d: Path, base=HERE, files=AUDIT_FILES):
    """run_confirm.py refuses unless frozen.json and AUDIT_OK exist, results_confirm.json does not, and the hash of
    frozen.json equals the one stored in frozen.sha256.  Returns the frozen dict."""
    d = Path(d)
    fz, ok, res, shaf = d / "frozen.json", d / "AUDIT_OK", d / "results_confirm.json", d / "frozen.sha256"
    if not fz.exists():
        raise SystemExit("REFUSED: frozen.json not found (run run_select.py first).")
    if not ok.exists():
        raise SystemExit("REFUSED: AUDIT_OK not found (only the Opus leak audit creates it).")
    if res.exists():
        raise SystemExit("REFUSED: results_confirm.json exists; the confirmation stage runs once.")
    if (d / "confirm_started.json").exists():
        raise SystemExit("REFUSED: confirm_started.json exists; the confirmation stage already started (runs once).")
    if not shaf.exists():
        raise SystemExit("REFUSED: frozen.sha256 not found.")
    want = shaf.read_text().split()[0].strip().lower()
    got = sha256_file(fz)
    if want != got:
        raise SystemExit(f"REFUSED: frozen.json hash {got} != frozen.sha256 {want}.")
    check_audit_ok(d, base, files)
    return json.loads(fz.read_text())


def start_confirm(d: Path):
    """Written BEFORE any outcome is loaded; a crash therefore cannot silently allow a second run."""
    import datetime
    (Path(d) / "confirm_started.json").write_text(json.dumps({"started_utc": datetime.datetime.utcnow().isoformat()}))


def check_select_gates(d: Path, preview: bool):
    d = Path(d)
    if not preview and ((d / "frozen.json").exists() or (d / "frozen.sha256").exists()):
        raise SystemExit("REFUSED: frozen.json already exists; selection is run once.")
    if (d / "AUDIT_OK").exists() or (d / "results_confirm.json").exists():
        raise SystemExit("REFUSED: confirmation artefacts exist; selection must not re-run.")


# ------------------------------------------------------------------ player family (optional, parallel builder)
def load_player_model():
    p = HERE / "player_model.py"
    if not p.exists():
        return None
    import importlib
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    return importlib.import_module("player_model")
