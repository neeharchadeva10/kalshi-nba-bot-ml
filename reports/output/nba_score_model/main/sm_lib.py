"""Research log #59 (NBA score model): shared library - paths, seasons/splits, team-game loading (reuses #57 impl_a's cleaning and
turnover guard), the feature builder (strictly before the game's ET date), sampling, hashing and run-once gates.

Module name is sm_lib (not lib / features ...) because the reused #56 / #37 code puts folders on sys.path that already contain
a module called `lib`."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent                                   # reports/output/nba_score_model
OUT = STUDY.parent                                    # reports/output
ROOT = STUDY.parents[2]                               # repo root
for _p in (OUT / "nba_line_error" / "impl_a", OUT / "nba_formula_accuracy", OUT / "nba_totals_grid"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import lea_lib as L  # noqa: E402  (#57 impl A: cleaning, turnover guard, possessions, franchise map)

if str(HERE) in sys.path:                             # our own modules must win name clashes (features, compare, run_select ...)
    sys.path.remove(str(HERE))
sys.path.insert(0, str(HERE))

BOX = L.BOX
SEED_SAMPLE = 20261023
N_SAMPLE = 300
MIN_LG_GAMES = 50
REST_CAP = 4
TRAIN = tuple(f"{y}-{(y + 1) % 100:02d}" for y in range(2008, 2019))        # 2008-09 .. 2018-19
VALID = ("2019-20", "2020-21", "2021-22")
TEST = ("2022-23", "2023-24", "2024-25", "2025-26")
FORWARD = "2026-27"
ALL_SEASONS = TRAIN + VALID + TEST + (FORWARD,)
PREV_OF_FIRST = "2007-08"
SAMPLE_SEASONS = TRAIN + VALID + TEST                                        # 2008-09 .. 2025-26

IDS = ["game_id", "season", "date", "team", "opp"]
FEATS = ["ortg_x_season", "ortg_x_l10", "ortg_x_l5", "ortg_x_prev",
         "drtg_y_season", "drtg_y_l10", "drtg_y_l5", "drtg_y_prev",
         "pace_x_season", "pace_x_l10", "pace_x_prev", "pace_y_season", "pace_y_l10", "pace_y_prev",
         "lg_ortg", "lg_pace", "home", "rest_x", "rest_y", "b2b_x", "b2b_y", "gp_x", "gp_y", "dos"]
TARGET = "pts"
FEATURE_COLUMNS = IDS + FEATS + [TARGET]


def label_of(year):
    return L.label_of(year)


def year_of(season):
    return L.year_of(season)


def prev_label(season):
    return label_of(year_of(season) - 1)


# ------------------------------------------------------------------ team games
def load_all_team_games(seasons=None):
    """Regular-season (season_type 2) NBA-vs-NBA team-games, 2007-08 .. 2026-27, #57 cleaning + turnover guard."""
    seasons = list(seasons) if seasons else [PREV_OF_FIRST] + list(ALL_SEASONS)
    parts = [L.team_games_from_box(L.read_year(year_of(s)), s) for s in seasons]
    tg = pd.concat(parts, ignore_index=True)
    tg["fr"] = tg.team.map(L.FRANCHISE)
    tg["fr_opp"] = tg.opp.map(L.FRANCHISE)
    assert tg.fr.notna().all() and tg.fr_opp.notna().all()
    return tg


# ------------------------------------------------------------------ feature builder
class TeamHist:
    """One franchise's games of one season in date order, with cumulative sums.  Every query takes a date D and uses only rows
    with date < D (np.searchsorted side='left')."""

    def __init__(self, dates, pts, opp_pts, pace):
        d = np.asarray(dates).astype("datetime64[D]")
        o = np.argsort(d, kind="stable")
        self.dates = d[o]
        pts, opp_pts, pace = (np.asarray(x, float)[o] for x in (pts, opp_pts, pace))
        z = np.zeros(1)
        self.c_pts, self.c_opp, self.c_pace = (np.concatenate([z, np.cumsum(x)]) for x in (pts, opp_pts, pace))

    def n_before(self, D):
        return int(np.searchsorted(self.dates, np.datetime64(D, "D"), side="left"))

    def window(self, D, n=None):
        """(count, sum pts, sum opp_pts, sum pace) over the last n games before D (n None = all of them)."""
        i = self.n_before(D)
        lo = 0 if n is None else max(0, i - n)
        return (i - lo, self.c_pts[i] - self.c_pts[lo], self.c_opp[i] - self.c_opp[lo], self.c_pace[i] - self.c_pace[lo])

    def rest(self, D):
        i = self.n_before(D)
        if i == 0:
            return REST_CAP
        gap = int((np.datetime64(D, "D") - self.dates[i - 1]).astype("int64"))
        return int(min(max(gap - 1, 0), REST_CAP))


def full_season_values(tg_season):
    """Full-season values per franchise: (ortg, drtg, pace), plus league (ortg, pace).  ortg = 100 sum pts / sum game poss."""
    out = {}
    for fr, g in tg_season.groupby("fr"):
        sp = g.pace.sum()
        out[fr] = (100.0 * g.pts.sum() / sp, 100.0 * g.opp_pts.sum() / sp, g.pace.mean())
    league = (100.0 * tg_season.pts.sum() / tg_season.pace.sum(), tg_season.pace.mean())
    return out, league


class LeagueHist:
    """League season-to-date (over team-game rows before D): ortg = 100 sum pts / sum game poss, pace = mean game poss."""

    def __init__(self, dates, pts, pace):
        d = np.asarray(dates).astype("datetime64[D]")
        o = np.argsort(d, kind="stable")
        self.dates = d[o]
        z = np.zeros(1)
        self.c_pts = np.concatenate([z, np.cumsum(np.asarray(pts, float)[o])])
        self.c_pace = np.concatenate([z, np.cumsum(np.asarray(pace, float)[o])])

    def query(self, D):
        i = int(np.searchsorted(self.dates, np.datetime64(D, "D"), side="left"))
        if i == 0:
            return 0, float("nan"), float("nan")
        return i // 2, 100.0 * self.c_pts[i] / self.c_pace[i], self.c_pace[i] / i       # two rows per game -> games = rows / 2


def season_features(tg_s: pd.DataFrame, tg_prev: pd.DataFrame) -> pd.DataFrame:
    """Features for every team-game row of one season (tg_s) using tg_prev as S-1.  The target column is carried through but no
    feature reads it for the same or a later date."""
    tg_s = tg_s.sort_values(["date", "tip", "game_id", "team"], kind="stable").reset_index(drop=True)
    prev_vals, prev_league = full_season_values(tg_prev)
    hist = {fr: TeamHist(g.date.values, g.pts.values, g.opp_pts.values, g.pace.values) for fr, g in tg_s.groupby("fr")}
    lg = LeagueHist(tg_s.date.values, tg_s.pts.values, tg_s.pace.values)
    first = np.datetime64(tg_s.date.min(), "D")
    nan3 = (float("nan"),) * 3
    rows = []
    for r in tg_s.itertuples(index=False):
        D = np.datetime64(r.date, "D")
        hx, hy = hist[r.fr], hist[r.fr_opp]
        px, py = prev_vals.get(r.fr, nan3), prev_vals.get(r.fr_opp, nan3)
        gx, gy = hx.n_before(D), hy.n_before(D)

        def off(h, n, prev):         # 100 sum pts / sum poss; the S-1 value if the window has no game
            c, sp, so, sq = h.window(D, n)
            return 100.0 * sp / sq if c else prev

        def dfn(h, n, prev):
            c, sp, so, sq = h.window(D, n)
            return 100.0 * so / sq if c else prev

        def pc(h, n, prev):
            c, sp, so, sq = h.window(D, n)
            return sq / c if c else prev

        ngames, l_o, l_p = lg.query(D)
        if ngames < MIN_LG_GAMES:
            l_o, l_p = prev_league
        rx, ry = hx.rest(D), hy.rest(D)
        rows.append((int(r.game_id), r.season, r.date, r.team, r.opp,
                     off(hx, None, px[0]), off(hx, 10, px[0]), off(hx, 5, px[0]), px[0],
                     dfn(hy, None, py[1]), dfn(hy, 10, py[1]), dfn(hy, 5, py[1]), py[1],
                     pc(hx, None, px[2]), pc(hx, 10, px[2]), px[2], pc(hy, None, py[2]), pc(hy, 10, py[2]), py[2],
                     l_o, l_p, 1.0 if r.venue == "home" else 0.0, float(rx), float(ry), float(rx == 0), float(ry == 0),
                     float(gx), float(gy), float((D - first).astype("int64")), float(r.pts)))
    return pd.DataFrame(rows, columns=FEATURE_COLUMNS)


def build_features(tg: pd.DataFrame, seasons=None) -> pd.DataFrame:
    seasons = list(seasons) if seasons else list(ALL_SEASONS)
    parts = [season_features(tg[tg.season == s], tg[tg.season == prev_label(s)]) for s in seasons]
    F = pd.concat(parts, ignore_index=True)
    F["date"] = pd.to_datetime(F.date)
    return F.sort_values(["season", "date", "game_id", "team"], kind="stable").reset_index(drop=True)


def sample_ids(F: pd.DataFrame, seed=SEED_SAMPLE, n=N_SAMPLE, seasons=SAMPLE_SEASONS) -> pd.DataFrame:
    """300 team-games drawn uniformly from the regular-season team-games of 2008-09..2025-26: rows sorted by (season, date,
    game_id, team), rng.choice(len, n, replace=False), sorted."""
    P = F[F.season.isin(set(seasons))].sort_values(["season", "date", "game_id", "team"], kind="stable").reset_index(drop=True)
    idx = np.sort(np.random.default_rng(seed).choice(len(P), n, replace=False))
    return P.iloc[idx][["game_id", "team", "season", "date"]].reset_index(drop=True)


# ------------------------------------------------------------------ hashing / gates
def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


# Code files the test stage depends on (paths relative to main/).  The Opus leak audit hashes exactly these (incl. frozen.json).
AUDIT_FILES = ("sm_lib.py", "features.py", "compare.py", "models.py", "run_select.py", "run_test.py",
               "../PREREGISTRATION.md", "../FEATURES.md",
               "../../nba_line_error/impl_a/lea_lib.py", "../../nba_line_error/impl_a/fa_ext.py",
               "../../nba_formula_accuracy/fa_lib.py", "../../nba_totals_grid/grid_lib.py",
               "../../nba_totals_pace/lib.py", "../../nba_totals_v2/ratings2.py", "../../nba_totals_tourney/tourney.py",
               "../frozen.json", "../team_games_features.parquet",
               *[f"../../../../data/raw/nba_open/team_box_{y}.parquet" for y in range(2022, 2027)],       # team_box_2027 is live: not hashed
               "../../../../data/raw/nba_open/play_by_play_2025.parquet", "../../../../data/raw/nba_open/play_by_play_2026.parquet",
               "../../../../data/raw/nba_open/player_box_2023.parquet", "../../../../data/raw/nba_open/player_box_2024.parquet",
               "../../../../data/raw/nba_odds_samples/closing_lines_odds_api.parquet")


FORWARD_SNAPSHOT = "forward_snapshot_team_box_2027.parquet"
FORWARD_YEAR = 2027


def make_forward_snapshot(study=STUDY):
    """Copy of the live team_box_2027 taken BEFORE the start marker; the forward block reads only this copy."""
    import shutil
    dst = Path(study) / FORWARD_SNAPSHOT
    shutil.copy2(BOX / f"team_box_{FORWARD_YEAR}.parquet", dst)
    return dst


def reader_with_snapshot(snapshot):
    """Box-score reader for the grid: file year 2027 comes from the snapshot, every other year from the (hashed) data files."""
    def rd(year):
        if int(year) != FORWARD_YEAR:
            return L.read_year_fixed(year)
        tb = pd.read_parquet(snapshot)
        if L.tov_diagnostics(tb)["doubled_total_turnovers"]:
            tb = tb.copy()
            tb["total_turnovers"] = tb["turnovers"]
        return tb
    return rd


def snapshot_team_games(snapshot):
    """2026-27 team-games from the snapshot, same cleaning as load_all_team_games."""
    tg = L.team_games_from_box(pd.read_parquet(snapshot), FORWARD)
    tg["fr"], tg["fr_opp"] = tg.team.map(L.FRANCHISE), tg.opp.map(L.FRANCHISE)
    return tg


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
    fz, shaf = study / "frozen.json", study / "frozen.sha256"
    if not fz.exists():
        raise SystemExit("REFUSED: frozen.json not found.")
    if not shaf.exists():
        raise SystemExit("REFUSED: frozen.sha256 not found.")
    want = shaf.read_text().split()[0].strip().lower()
    got = sha256_file(fz)
    if want != got:
        raise SystemExit(f"REFUSED: frozen.json hash {got} != frozen.sha256 {want}.")
    return json.loads(fz.read_text())


def check_agreement_pass(study=STUDY, features_path=None):
    """Selection gate: AGREEMENT.md must say 'STATUS: PASS' and its FEATURES_SHA256 must equal the hash of the current parquet."""
    p = Path(study) / "AGREEMENT.md"
    if not p.exists():
        raise SystemExit("REFUSED: AGREEMENT.md not found (run compare.py first).")
    txt = p.read_text(encoding="utf-8")
    if "STATUS: PASS" not in txt:
        raise SystemExit("REFUSED: AGREEMENT.md does not say STATUS: PASS.")
    want = next((ln.split(":", 1)[1].strip() for ln in txt.splitlines() if ln.startswith("FEATURES_SHA256:")), "")
    fp = Path(features_path) if features_path else Path(study) / "team_games_features.parquet"
    if not fp.exists() or sha256_file(fp) != want:
        raise SystemExit("REFUSED: team_games_features.parquet changed since AGREEMENT.md was written (hash mismatch).")
