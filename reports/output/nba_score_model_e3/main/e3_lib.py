"""Research log #64 (E3 = E1 + everything missing players do, all feature combinations): shared library.

Re-uses #62's machinery (report union + CreationDate, exact name matching among current-team players, player windows, current-team rule)
by importing e2_lib read-only; everything new (12 families, ages, moves, subset ridge engine, gates) lives here.
Read-only on every input.  No network here, no kpm.execution, no keys.  Definitions: FEATURES_E3.md.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent                                   # reports/output/nba_score_model_e3
OUT = STUDY.parent                                    # reports/output
ROOT = STUDY.parents[2]
E2_MAIN = OUT / "nba_score_model_e2" / "main"
if str(E2_MAIN) not in sys.path:
    sys.path.insert(0, str(E2_MAIN))
import e2_lib as E2  # noqa: E402  (frozen #62 library; never modified)

sys.path[:] = [p for p in sys.path if p != str(E2_MAIN)]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

E1_STUDY = E2.E1_STUDY
BOX_DIR = E2.BOX_DIR
MOVES_CSV = ROOT / "data" / "derived" / "player_moves.csv"
CORE_DIR = ROOT / "data" / "raw" / "nba_open" / "hoopr_extra" / "player_core"
CORE_URL = "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/nba/player_core/parquet/player_core_{y}.parquet"
FEATURES_PARQUET = STUDY / "e3_features.parquet"

# ------------------------------------------------------------------ constants (pre-registered)
SEASONS = E2.SEASONS
TRAIN, VALID, TEST = E2.TRAIN, E2.VALID, E2.TEST
ROT_MPG = 10.0                    # rotation-quality for E3: window mpg >= 10
STAR_MPG, STARTER_MPG = 28.0, 20.0
VET_AGE, YOUNG_AGE = 32, 23       # missing veteran: age >= 32; missing young: age <= 23
AGE_MIN, AGE_MAX = 18, 45         # DOB sanity: ages outside are treated as unknown
NEW_WINDOW = 10                   # team's last 10 games
CUTOFFS = {"c60": 3600, "c20": 1200}   # seconds before tip
SEED_SAMPLE = 20261025
N_SAMPLE = 200
BOOT_N, BOOT_SEED = E2.BOOT_N, E2.BOOT_SEED
LAMBDAS = (1.0, 10.0, 100.0, 1000.0, 10000.0)
OUT_STATUSES, STATUS_RANK = E2.OUT_STATUSES, E2.STATUS_RANK
WITHIN = 10.0

STAT_NAMES = ["minutes", "pts", "reb", "ast", "tpm", "dfn", "usg", "pm"]     # window columns, in this order
FAMILIES = [("PTS", ["pts"]), ("MIN", ["min"]), ("REB", ["reb"]), ("AST", ["ast"]), ("3PM", ["tpm"]), ("DEF", ["dfn"]),
            ("USG", ["usg"]), ("PM", ["pm"]), ("TIER", ["tier_star", "tier_starter", "tier_bench"]),
            ("AGE", ["age_vet", "age_young"]), ("Q", ["q_pts", "q_min"]), ("NEW", ["new_share", "gone_share"])]
FAMILY_NAMES = [f for f, _ in FAMILIES]
FAMILY_BASES = dict(FAMILIES)
BASES = [b for _, bs in FAMILIES for b in bs]


def family_columns(fam: str, cutoff: str):
    return [f"{b}_{s}_{cutoff}" for b in FAMILY_BASES[fam] for s in ("own", "opp")]


def all_feature_columns():
    return [c for cut in CUTOFFS for f in FAMILY_NAMES for c in family_columns(f, cut)]


def enumerate_versions():
    """8,190 versions: cutoff c60 then c20; within a cutoff, family subsets by ascending bitmask (bit i = family i of FAMILIES).
    Returns list of (version_id, cutoff, tuple(family names))."""
    out = []
    for cut in CUTOFFS:
        for mask in range(1, 1 << len(FAMILY_NAMES)):
            fams = tuple(FAMILY_NAMES[i] for i in range(len(FAMILY_NAMES)) if mask >> i & 1)
            out.append((f"{cut}:{'+'.join(fams)}", cut, fams))
    return out


def version_columns(cutoff, fams):
    return [c for f in fams for c in family_columns(f, cutoff)]


# ------------------------------------------------------------------ age
def age_on(dob, on) -> int:
    """Completed years on date `on` (dob, on: datetime.date / Timestamp)."""
    dob, on = pd.Timestamp(dob), pd.Timestamp(on)
    return int(on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day)))


class AgeBook:
    """athlete id -> date of birth (hoopR player_core).  age(aid, on) -> completed years, or None when the DOB is missing or the
    age falls outside 18-45 (sanity: treated as unknown)."""

    def __init__(self, dob: dict):
        self.dob = {int(k): pd.Timestamp(v) for k, v in dob.items() if v is not None and not pd.isna(v)}
        self.n_out_of_range = 0

    def age(self, aid, on):
        d = self.dob.get(int(aid))
        if d is None:
            return None
        a = age_on(d, on)
        if a < AGE_MIN or a > AGE_MAX:
            self.n_out_of_range += 1
            return None
        return a


def load_agebook(years=range(2021, 2027)) -> tuple[AgeBook, dict]:
    """Date of birth from the cached hoopR player_core files (later files override earlier ones; conflicts counted)."""
    dob, conflicts = {}, 0
    for y in years:
        p = CORE_DIR / f"player_core_{y}.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p, columns=["athlete_id", "date_of_birth"])
        df = df[df.date_of_birth.notna() & df.athlete_id.notna()]
        for a, s in zip(df.athlete_id.astype("int64").values, df.date_of_birth.values):
            d = str(s)[:10]
            if a in dob and dob[a] != d:
                conflicts += 1
            dob[int(a)] = d
    return AgeBook(dob), {"n_dob": len(dob), "conflicts": conflicts}


# ------------------------------------------------------------------ moves
class Moves:
    """Player moves (data/derived/player_moves.csv).  Only moves with first_date_new STRICTLY BEFORE the game's ET date are visible."""

    def __init__(self, df: pd.DataFrame):
        df = df.copy()
        df["fd"] = [E2.day_int(x) for x in df.first_date_new]
        df = df.sort_values(["fd", "athlete_id"], kind="stable")
        self.to_team = {}
        self.from_team = {}
        self.by_player = {}
        for a, ft, tt, fd, sn in zip(df.athlete_id.values, df.from_team.values, df.to_team.values, df.fd.values, df.season_new.values):
            a, fd, sn = int(a), int(fd), int(sn)
            self.to_team.setdefault(tt, []).append((fd, a, sn))
            self.from_team.setdefault(ft, []).append((fd, a, sn))
            self.by_player.setdefault(a, []).append((fd, ft, tt))

    def new_to(self, team, d, year):
        """Athletes who moved TO team (season_new == year) with first game for it dated strictly before d."""
        return {a for fd, a, sn in self.to_team.get(team, ()) if fd < d and sn == year}

    def gone_from(self, team, d):
        """Athletes with a move FROM team dated strictly before d whose latest visible move did not bring him back to team."""
        res = set()
        for fd, a, sn in self.from_team.get(team, ()):
            if fd >= d:
                continue
            vis = [m for m in self.by_player[a] if m[0] < d]
            if vis and vis[-1][2] != team:
                res.add(a)
        return res


def load_moves(path=MOVES_CSV) -> Moves:
    return Moves(pd.read_csv(path))


# ------------------------------------------------------------------ box scores (all stat columns)
def window_slice(hd, hy, d: int, year: int):
    """Same window logic as e2_lib.player_window, returning (a, b, source): rows hd[a:b] are the window (all strictly before d)."""
    i = int(np.searchsorted(hd, d, side="left"))
    if i == 0:
        return None
    yy = hy[:i]
    lo = int(np.searchsorted(yy, year, side="left"))
    if i - lo >= E2.MIN_CUR:
        return max(lo, i - E2.WINDOW), i, "cur"
    lo2 = int(np.searchsorted(yy, year - 1, side="left"))
    hi2 = int(np.searchsorted(yy, year - 1, side="right"))
    if hi2 > lo2:
        return max(lo2, hi2 - E2.WINDOW), hi2, "prev"
    return None


class Box3(E2.Box):
    """E2's Box plus the 8 stat columns of STAT_NAMES per played game."""

    def __init__(self, df: pd.DataFrame):
        super().__init__(df)
        pl = self.df[self.df.played]
        self.hist3 = {}
        for a, g in pl.groupby("aid", sort=False):
            self.hist3[int(a)] = (g.d.values.astype(np.int64), g[STAT_NAMES].values.astype(float), g.yr.values.astype(np.int64))
        self.min_by = {}                                 # (team, game_id) -> {aid: minutes}
        self.season_min = {}                             # (team, season-year) -> {aid: minutes}
        for t, gid, a, yr, m in zip(pl.team.values, pl.game_id.values, pl.aid.values, pl.yr.values, pl.minutes.values):
            self.min_by.setdefault((t, int(gid)), {})[int(a)] = float(m)
            sm = self.season_min.setdefault((t, int(yr)), {})
            sm[int(a)] = sm.get(int(a), 0.0) + float(m)
        self._wcache = {}

    def window3(self, aid: int, d: int, year: int):
        """-> (vector of the 8 STAT_NAMES means over the player's window, source) or None."""
        key = (aid, d, year)
        if key not in self._wcache:
            h = self.hist3.get(aid)
            r = None if h is None else window_slice(h[0], h[2], d, year)
            self._wcache[key] = None if r is None else (h[1][r[0]:r[1]].mean(axis=0), r[2])
        return self._wcache[key]


def load_box3(years=range(2021, 2027)) -> Box3:
    cols = ["game_id", "season", "season_type", "game_date_time", "athlete_id", "athlete_display_name", "team_abbreviation", "minutes",
            "points", "did_not_play", "rebounds", "assists", "steals", "blocks", "turnovers", "field_goals_attempted",
            "free_throws_attempted", "three_point_field_goals_made", "plus_minus"]
    parts = []
    for y in years:
        pb = pd.read_parquet(BOX_DIR / f"player_box_{y}.parquet", columns=cols)
        pb = pb[(pb.season_type == 2) & pb.athlete_id.notna()]
        pb = pb[pb.team_abbreviation.isin(E2.HOOPR2KALSHI)]
        gdt = pd.to_datetime(pb.game_date_time, utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
        mins = pd.to_numeric(pb.minutes, errors="coerce")
        played = (~pb.did_not_play.astype(bool)) & mins.notna() & (mins > 0)

        def num(c):
            return pd.to_numeric(pb[c], errors="coerce").fillna(0.0).values

        pm = pd.to_numeric(pb.plus_minus.astype(str).str.replace("+", "", regex=False), errors="coerce").fillna(0.0).values
        parts.append(pd.DataFrame({
            "game_id": pb.game_id.astype("int64").values, "yr": pb.season.astype(int).values,
            "d": [E2.day_int(x) for x in gdt], "aid": pb.athlete_id.astype("int64").values,
            "name": pb.athlete_display_name.values, "team": pb.team_abbreviation.map(E2.HOOPR2KALSHI).values,
            "played": played.values, "minutes": mins.fillna(0.0).values, "pts": num("points"), "reb": num("rebounds"),
            "ast": num("assists"), "tpm": num("three_point_field_goals_made"), "dfn": num("steals") + num("blocks"),
            "usg": num("field_goals_attempted") + 0.44 * num("free_throws_attempted") + num("turnovers"), "pm": pm}))
    df = pd.concat(parts, ignore_index=True).drop_duplicates(["game_id", "aid"], keep="first")
    return Box3(df)


# ------------------------------------------------------------------ the families for one team in one game (one cutoff)
ZERO_BASES = {b: 0.0 for b in BASES if b not in ("new_share", "gone_share")}


def tier_of(mpg: float) -> str:
    return "tier_star" if mpg >= STAR_MPG else ("tier_starter" if mpg >= STARTER_MPG else "tier_bench")


def report_family_features(box: Box3, team: str, d: int, year: int, roster, last_yr, listed, ages: AgeBook, game_date):
    """The 10 report-dependent families (PTS..Q) for one team.  roster/last_yr/listed as in e2_lib.team_features.
    Out/Doubtful and Questionable players must have window mpg >= ROT_MPG (10)."""
    roster = [a for a in roster if last_yr.get(a, 0) >= year - 1]
    cn, _ = E2._cands(box, roster)
    st, unmatched, n_listed = {}, [], 0
    for raw, s in listed.items():
        if s not in STATUS_RANK:
            continue
        n_listed += 1
        a = E2.match_name(raw, cn, None)
        if a is None:
            unmatched.append(f"{raw}|{s}")
            continue
        if STATUS_RANK[s] > STATUS_RANK.get(st.get(a), 0):
            st[a] = s
    f = dict(ZERO_BASES)
    f.update(tier_star=0.0, tier_starter=0.0, tier_bench=0.0, age_vet=0.0, age_young=0.0, q_pts=0.0, q_min=0.0)
    n_out = n_q = n_age_unknown = n_age_known = 0
    for a, s in sorted(st.items()):
        w = box.window3(a, d, year)
        if w is None or w[0][0] < ROT_MPG:
            continue
        v = w[0]
        mpg, ppg = float(v[0]), float(v[1])
        if s in OUT_STATUSES:
            n_out += 1
            f["pts"] += ppg
            f["min"] += mpg / 240.0
            f["reb"] += float(v[2])
            f["ast"] += float(v[3])
            f["tpm"] += float(v[4])
            f["dfn"] += float(v[5])
            f["usg"] += float(v[6])
            f["pm"] += float(v[7])
            f[tier_of(mpg)] += ppg
            age = ages.age(a, game_date)
            if age is None:
                n_age_unknown += 1
            else:
                n_age_known += 1
                if age >= VET_AGE:
                    f["age_vet"] += mpg
                if age <= YOUNG_AGE:
                    f["age_young"] += mpg
        else:
            n_q += 1
            f["q_pts"] += ppg
            f["q_min"] += mpg / 240.0
    f.update(n_listed=n_listed, n_unmatched=len(unmatched), unmatched_names=";".join(unmatched), n_out_rot=n_out, n_q_rot=n_q,
             n_age_known=n_age_known, n_age_unknown=n_age_unknown)
    return f


def new_family_features(box: Box3, moves: Moves, team: str, d: int, year: int):
    """NEW family (cutoff-independent): new_share = share of the team's minutes in its last <= 10 games of THIS season (strictly
    before d) played by athletes who moved to the team this season with a first game dated strictly before d.  gone_share = share of
    the team's LAST-season minutes (regular season, season-year-1) held by athletes whose moves, dated strictly before d, took them
    away from the team and not back.  0 when there are no such games / no last-season minutes."""
    t = box.team_games.get(team)
    new_share = 0.0
    if t is not None:
        td, tgid, ty = t
        i = int(np.searchsorted(td, d, side="left"))
        gids = [int(g) for g in tgid[:i][ty[:i] == year]][-NEW_WINDOW:]
        newset = moves.new_to(team, d, year)
        tot = num = 0.0
        for g in gids:
            mm = box.min_by.get((team, g), {})
            tot += sum(mm.values())
            num += sum(m for a, m in mm.items() if a in newset)
        new_share = num / tot if tot > 0 else 0.0
    sm = box.season_min.get((team, year - 1), {})
    tot = sum(sm.values())
    gone = moves.gone_from(team, d)
    gone_share = sum(m for a, m in sm.items() if a in gone) / tot if tot > 0 else 0.0
    return {"new_share": float(new_share), "gone_share": float(gone_share)}


# ------------------------------------------------------------------ the builder
def build_features(games: pd.DataFrame, box: Box3, reports: E2.Reports, ages: AgeBook, moves: Moves) -> pd.DataFrame:
    """One row per (game_id, team); games = e2_lib.load_games() output.  Uses only box rows dated strictly before the game's ET date,
    moves with first game strictly before that date, and, per cutoff, the latest report published at or before tip - 60 min / 20 min."""
    games = games.copy()
    games["d"] = [E2.day_int(x) for x in games.date]
    by_date = {d: g for d, g in games.groupby("d", sort=True)}
    brows = {d: g for d, g in box.df.groupby("d", sort=True)}
    last_team, last_yr, roster = {}, {}, {}
    recs = []
    for d in sorted(set(by_date) | set(brows)):
        if d in by_date:
            for gid, gg in by_date[d].groupby("game_id", sort=False):
                tip = float(gg.tip.iloc[0])
                away = gg[gg.venue == "away"].iloc[0] if (gg.venue == "away").any() else gg.iloc[0]
                home = gg[gg.venue == "home"].iloc[0] if (gg.venue == "home").any() else gg.iloc[-1]
                matchup = f"{away.team_k}@{home.team_k}"
                gdate = pd.Timestamp(gg.date.iloc[0])
                mdy = gdate.strftime("%m/%d/%Y")
                res = {r.team_k: {"r": r, "new": new_family_features(box, moves, r.team_k, d, E2.year_of(r.season))}
                       for r in gg.itertuples(index=False)}
                for cut, off in CUTOFFS.items():
                    cov = reports.last_covering(tip - off, mdy, matchup)
                    rows, nys = (cov[2], cov[3]) if cov else ([], [])
                    for team, x in res.items():
                        listed, nys_flag = E2.team_status_map(rows, nys, team) if cov else ({}, False)
                        report_ok = cov is not None and len(listed) > 0
                        f = report_family_features(box, team, d, E2.year_of(x["r"].season), roster.get(team, ()), last_yr,
                                                   listed if report_ok else {}, ages, gdate)
                        f.update(x["new"])
                        f.update(report_found=cov is not None, report_name=(cov[1] if cov else ""),
                                 report_created=(float(cov[0]) if cov else np.nan), nys=bool(cov is not None and nys_flag),
                                 report_skipped_newer=int(cov[4]) if cov else 0)
                        x[cut] = f
                for team, x in res.items():
                    r = x["r"]
                    opp = r.opp_k
                    row = {"game_id": int(gid), "season": r.season, "date": r.date, "team": r.team, "opp": r.opp, "team_k": team,
                           "opp_k": opp, "tip": int(tip), "D60": int(tip - CUTOFFS["c60"]), "D20": int(tip - CUTOFFS["c20"]),
                           "box_found": (team, int(gid)) in box.played_in}
                    for cut in CUTOFFS:
                        fo, fp = x[cut], res[opp][cut]
                        for b in BASES:
                            row[f"{b}_own_{cut}"] = fo[b]
                            row[f"{b}_opp_{cut}"] = fp[b]
                        for k in ("report_found", "report_name", "report_created", "nys", "report_skipped_newer", "n_listed", "n_unmatched",
                                  "unmatched_names", "n_out_rot", "n_q_rot", "n_age_known", "n_age_unknown"):
                            row[f"{k}_{cut}"] = fo[k]
                    recs.append(row)
        if d in brows:                                   # state update AFTER the day's queries (strictly-before rule)
            b = brows[d]
            for aid, team, yr in zip(b.aid.values, b.team.values, b.yr.values):
                aid = int(aid)
                old = last_team.get(aid)
                if old != team:
                    if old is not None:
                        roster[old].discard(aid)
                    roster.setdefault(team, set()).add(aid)
                    last_team[aid] = team
                last_yr[aid] = int(yr)
    F = pd.DataFrame(recs)
    return F.sort_values(["season", "date", "game_id", "team"], kind="stable").reset_index(drop=True)


def sample_ids(F: pd.DataFrame, seed=SEED_SAMPLE, n=N_SAMPLE) -> pd.DataFrame:
    P = F.sort_values(["season", "date", "game_id", "team"], kind="stable").reset_index(drop=True)
    idx = np.sort(np.random.default_rng(seed).choice(len(P), n, replace=False))
    cols = [c for c in ("game_id", "team", "team_k", "season", "date") if c in P.columns]
    return P.iloc[idx][cols].reset_index(drop=True)


# ------------------------------------------------------------------ ridge (closed form) and the subset engine
def standardise_fit(X):
    return E2.standardise_fit(X)


def ridge_fit(Xs, y, lam):
    return E2.ridge_fit(Xs, y, lam)


def ridge_predict(X, mu, sd, b0, b):
    return E2.ridge_predict(X, mu, sd, b0, b)


def loso_predict(X, y, seasons, lam):
    return E2.loso_predict(X, y, seasons, lam)


def cv_lambda(X, y, seasons, lambdas=LAMBDAS):
    """LOSO over the training seasons; pooled out-of-fold MSE; lowest wins, ties -> larger lambda (as #62)."""
    return E2.cv_lambda(X, y, seasons, lambdas)


class SubsetEngine:
    """Fits the ridge for any column subset of one fixed design matrix using precomputed standardised Gram blocks.
    Mathematically identical to cv_lambda / ridge_fit on X[:, idx] (checked in the tests); the standardisation of each fold uses that
    fold's training rows only, so the subset only selects columns of the same standardised matrix."""

    def __init__(self, X, y, seasons, X_val, lambdas=LAMBDAS):
        self.lambdas = tuple(lambdas)
        self.y = np.asarray(y, float)
        s = np.asarray(seasons)
        self.folds = []
        for u in sorted(set(s)):
            te = s == u
            mu, sd = standardise_fit(X[~te])
            Xtr = (X[~te] - mu) / sd
            ym = float(self.y[~te].mean())
            self.folds.append({"te": te, "mu": mu, "sd": sd, "G": Xtr.T @ Xtr, "c": Xtr.T @ (self.y[~te] - ym), "ym": ym,
                               "Xte": (X[te] - mu) / sd, "yte": self.y[te]})
        self.mu, self.sd = standardise_fit(X)
        Xs = (X - self.mu) / self.sd
        self.ym = float(self.y.mean())
        self.G, self.c = Xs.T @ Xs, Xs.T @ (self.y - self.ym)
        self.Xval = (X_val - self.mu) / self.sd

    def _betas(self, G, c, lams):
        w, Q = np.linalg.eigh(G)
        qc = Q.T @ c
        return [Q @ (qc / (np.maximum(w, 0.0) + lam)) for lam in lams]

    def fit(self, idx):
        """-> (best lambda, cv mse per lambda, b0, beta in standardised units, validation residual prediction)."""
        idx = np.asarray(idx)
        mse = np.zeros(len(self.lambdas))
        n = 0
        for fd in self.folds:
            bs = self._betas(fd["G"][np.ix_(idx, idx)], fd["c"][idx], self.lambdas)
            Xte = fd["Xte"][:, idx]
            for j, b in enumerate(bs):
                mse[j] += float(((fd["yte"] - (fd["ym"] + Xte @ b)) ** 2).sum())
            n += len(fd["yte"])
        mse /= n
        best = min(range(len(self.lambdas)), key=lambda j: (mse[j], -self.lambdas[j]))
        lam = self.lambdas[best]
        b = self._betas(self.G[np.ix_(idx, idx)], self.c[idx], [lam])[0]
        return lam, mse, self.ym, b, self.ym + self.Xval[:, idx] @ b


def score_totals(inv, n_games, y, pred, within=WITHIN):
    """Game-level scores from team-level predictions.  inv: game index (0..n_games-1) of each row (exactly two rows per game)."""
    e_team = pred - y
    e_game = np.bincount(inv, e_team, minlength=n_games)
    return {"total_mae": float(np.abs(e_game).mean()), "pct_within_10": float((np.abs(e_game) <= within).mean()),
            "team_mae": float(np.abs(e_team).mean()), "bias": float(e_game.mean())}


CANDIDATE_E1 = "E1"


def pick(scores: dict, order: list):
    """Lowest total MAE; ties -> earlier in `order` (E1 first).  STOP rule: the caller stops when the answer is E1."""
    return min(order, key=lambda k: (scores[k]["total_mae"], order.index(k)))


# ------------------------------------------------------------------ metrics, bootstrap (same as #62)
game_metrics = E2.game_metrics
strip = E2.strip
paired_boot_ci = E2.paired_boot_ci
sha256_file = E2.sha256_file
sha256_bytes = E2.sha256_bytes
predict_variant = E2.predict_variant


# ------------------------------------------------------------------ hashing / gates
OWN_AUDIT = ("e3_lib.py", "features.py", "compare.py", "run_select.py", "run_test.py", "../PREREGISTRATION.md", "../FEATURES_E3.md",
             "../frozen_e3.json", "../e3_features.parquet", "../sample_ids.csv", "../AGREEMENT.md")
E2_SHARED = tuple(f for f in E2.AUDIT_FILES[10:])      # E1 frozen+code, injury inputs, box scores, closing lines (same relative depth)
EXTRA_AUDIT = ("../../nba_score_model_e2/main/e2_lib.py", "../../../../data/derived/player_moves.csv",
               *[f"../../../../data/raw/nba_open/hoopr_extra/player_core/player_core_{y}.parquet" for y in range(2021, 2027)])
AUDIT_FILES = OWN_AUDIT + EXTRA_AUDIT + E2_SHARED


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
    fz, shaf = study / "frozen_e3.json", study / "frozen_e3.sha256"
    if not fz.exists():
        raise SystemExit("REFUSED: frozen_e3.json not found.")
    if not shaf.exists():
        raise SystemExit("REFUSED: frozen_e3.sha256 not found.")
    want = shaf.read_text().split()[0].strip().lower()
    got = sha256_file(fz)
    if want != got:
        raise SystemExit(f"REFUSED: frozen_e3.json hash {got} != frozen_e3.sha256 {want}.")
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
    fp = Path(features_path) if features_path else Path(study) / "e3_features.parquet"
    if not fp.exists() or sha256_file(fp) != want:
        raise SystemExit("REFUSED: e3_features.parquet changed since AGREEMENT.md was written (hash mismatch).")


def load_e1():
    """E1 namespace (S, M, RT, RS) via #62's loader; E2's main dir is removed from sys.path afterwards so module names stay ours."""
    X = E2.load_e1()
    sys.path[:] = [p for p in sys.path if p != str(E2_MAIN)]
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    return X
