"""Research log #65 (refined totals strategy) -- MAIN library.  Pure functions first, loaders/builders after.

Implements reports/output/nba_refined_totals/PREREGISTRATION.md literally.  Read-only on every input; no network,
no kpm.execution, no keys.  Readings of ambiguous points are listed in main/README.md ("Readings").

Conventions
  * season labels "2022-23"; player/team box file year = end year (file 2021 = season 2020-21).
  * team codes in this study are Kalshi codes (GSW NYK SAS UTA WAS NOP ...), as in #62/#64.
  * ET dates are pandas Timestamps (midnight) in frames, or integer day numbers (e2_lib.day_int) inside the scans.
  * a version id is a string exactly as in ../versions_all.csv, e.g. "A|E3|rolling|G4|Q+NEW", "B|M28|T20|game".
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent                                   # reports/output/nba_refined_totals
OUT = STUDY.parent                                    # reports/output
ROOT = STUDY.parents[2]                               # repo root
MAIN_OUT = STUDY / "main_out"
VERSIONS_CSV = STUDY / "versions_all.csv"
E2_MAIN = OUT / "nba_score_model_e2" / "main"
FA_DIR = OUT / "nba_formula_accuracy"
GRID_DIR = OUT / "nba_totals_grid"
E3_STUDY = OUT / "nba_score_model_e3"
E1_CACHE = OUT / "nba_assumptions_audit" / "partB" / "_cache" / "e1_2021_26.parquet"
CLOSING = ROOT / "data" / "raw" / "nba_odds_samples" / "closing_lines_odds_api.parquet"
BOX_DIR = ROOT / "data" / "raw" / "nba_open"


def _import_e2():
    if str(E2_MAIN) not in sys.path:
        sys.path.insert(0, str(E2_MAIN))
    import e2_lib  # noqa: WPS433
    while str(E2_MAIN) in sys.path:                    # E2's main/ holds run_test.py / run_select.py: never leave it on the path
        sys.path.remove(str(E2_MAIN))
    return e2_lib


E2 = _import_e2()           # frozen #62 library (report set, name matching, Reports, day_int, ridge helpers); never modified

# ------------------------------------------------------------------ constants (pre-registered)
BOOT_N, BOOT_SEED = 10000, 20261023
LUCK_N, LUCK_SEED = 500, 20261024
WIN_PAY = 10.0 / 11.0                     # -110 flat: win +0.909090.., loss -1, push 0
BREAK_EVEN = 110.0 / 210.0
LEAN_MIN_GAMES = 30
Q_MIN, NEW_MAX, EARLY_MIN_GAMES = 1, 0.30, 10        # Q: n_q_rot >= 1; NEW: share >= 0.30; EARLY: < 10 games
B_WINDOW, B_MIN_TEAM_GAMES, B_FRESH_GAMES = 10, 5, 3
MODELS = ("F42", "E1", "E3")
LEANS = ("none", "rolling")
GAPS = (2, 4, 6, 8)
SKIP_SUBSETS = ((), ("Q",), ("NEW",), ("EARLY",), ("Q", "NEW"), ("Q", "EARLY"), ("NEW", "EARLY"), ("Q", "NEW", "EARLY"))
B_MS = (20, 28)
B_TAUS = (10, 15, 20, 30)
B_SCOPES = ("game", "team")
FLAVORS = ("report", "box")

SEASONS_ALL = ("2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26")
D_A = ("2022-23", "2023-24")
D_B = ("2021-22", "2022-23", "2023-24")
H1 = ("2020-21",)
H2_SB = ("2024-25", "2025-26")
H2_K = ("2025-26",)
MIN_BETS_A, MIN_BETS_B = 150, 80
D_A_F42 = ("2023-24",)          # amendment 1: F42's discovery window is 2023-24 only (E1/E3 keep D_A)


def d_seasons(vid: str) -> tuple:
    """Discovery seasons of a version (amendment 1): A|F42 -> 2023-24; other A -> D_A; B -> D_B."""
    v = parse_version(vid)
    if v["part"] == "B":
        return D_B
    return D_A_F42 if v["model"] == "F42" else D_A


def discovery_row(ev: pd.DataFrame, vid: str) -> dict:
    """One version's discovery row (sportsbook, report flavor for B): bets, wins, losses, pushes, mean P&L."""
    b = version_sb_bets(restrict(ev, d_seasons(vid)), vid, "report")
    s = summarize_sb(b.pnl.values)
    return {"version": vid, "n": s["n"], "wins": s["wins"], "losses": s["losses"], "pushes": s["pushes"], "mean_pnl": s["mean"]}
MODEL_COL = {"F42": "f42", "E1": "e1", "E3": "e3"}


# ------------------------------------------------------------------ versions
def skip_label(skips) -> str:
    return "none" if not skips else "+".join(skips)


def all_versions() -> list:
    out = []
    for m in MODELS:
        for ln in LEANS:
            for g in GAPS:
                for sk in SKIP_SUBSETS:
                    out.append(f"A|{m}|{ln}|G{g}|{skip_label(sk)}")
    for M in B_MS:
        for t in B_TAUS:
            for sc in B_SCOPES:
                out.append(f"B|M{M}|T{t}|{sc}")
    return out


def parse_version(v: str) -> dict:
    p = v.split("|")
    if p[0] == "A":
        assert len(p) == 5, v
        _, model, lean, g, sk = p
        assert model in MODELS and lean in LEANS and g[0] == "G", v
        skips = () if sk == "none" else tuple(sk.split("+"))
        assert all(s in ("Q", "NEW", "EARLY") for s in skips), v
        return {"id": v, "part": "A", "model": model, "lean": lean, "G": int(g[1:]), "skips": skips}
    if p[0] == "B":
        assert len(p) == 4, v
        _, M, T, sc = p
        assert M[0] == "M" and T[0] == "T" and sc in B_SCOPES, v
        return {"id": v, "part": "B", "M": int(M[1:]), "tau": int(T[1:]), "scope": sc}
    raise ValueError(v)


def load_version_ids(path=VERSIONS_CSV) -> list:
    return pd.read_csv(path).version.tolist()


LIVE_RULE = "A|F42|none|G4|none"            # #40 v5


# ------------------------------------------------------------------ closing lines
def load_closing(path=CLOSING, regular_ids=None) -> pd.Series:
    """Sportsbook consensus close: drop snapshot >= commence_time, keep 180 < over_under < 275, one row per game_id, and (if given)
    only regular-season hoopR game ids.  Returns Series game_id -> line."""
    p = pd.read_parquet(path)
    p = p[np.isfinite(p.over_under)]
    lead = (pd.to_datetime(p.commence_time, utc=True) - pd.to_datetime(p.snapshot, utc=True)).dt.total_seconds()
    p = p[lead > 0]
    p = p[(p.over_under > 180) & (p.over_under < 275)]
    p = p.drop_duplicates("game_id")
    if regular_ids is not None:
        p = p[p.game_id.isin(set(int(x) for x in regular_ids))]
    return pd.Series(p.over_under.values.astype(float), index=p.game_id.astype("int64").values, name="close_line")


# ------------------------------------------------------------------ lean fix
def lean_table(games: pd.DataFrame, total_col: str, model: str, min_games: int = LEAN_MIN_GAMES) -> pd.DataFrame:
    """b for every (season, ET date) of `games` (columns season, et_date, actual_total, <total_col>).
    b = mean(actual - model total) over the league's regular-season games of the SAME season with ET date STRICTLY BEFORE et_date and the
    model defined; used only when there are >= min_games such games (else 0).  Returns season, model, et_date, b, n_games_before."""
    rows = []
    for s, g in games.groupby("season", sort=True):
        d_all = np.sort(g.et_date.unique())
        ok = g[np.isfinite(g[total_col].values) & np.isfinite(g.actual_total.values)]
        res = (ok.actual_total - ok[total_col]).groupby(ok.et_date).agg(["sum", "count"]).sort_index()
        days = res.index.values
        csum = np.concatenate([[0.0], np.cumsum(res["sum"].values)])
        ccnt = np.concatenate([[0], np.cumsum(res["count"].values)])
        for d in d_all:
            i = int(np.searchsorted(days, d, side="left"))          # days strictly before d
            n, sm = int(ccnt[i]), float(csum[i])
            rows.append((s, model, pd.Timestamp(d), (sm / n if n >= min_games else 0.0), n))
    return pd.DataFrame(rows, columns=["season", "model", "et_date", "b", "n_games_before"])


def lean_brute(games: pd.DataFrame, total_col: str, season: str, date, min_games: int = LEAN_MIN_GAMES):
    """Reference implementation of one b (slow, obviously correct): returns (b, n)."""
    g = games[(games.season == season) & (games.et_date < pd.Timestamp(date))]
    g = g[np.isfinite(g[total_col].values) & np.isfinite(g.actual_total.values)]
    n = len(g)
    return (float((g.actual_total - g[total_col]).mean()) if n >= min_games else 0.0), n


# ------------------------------------------------------------------ skip flags
def team_game_counts_before(tg: pd.DataFrame) -> pd.DataFrame:
    """tg: one row per (game_id, team) with season, date, team.  Adds n_before = this team's regular-season games of the same season with
    ET date strictly before this game's date."""
    tg = tg.sort_values(["team", "season", "date", "game_id"], kind="stable").copy()
    out = np.zeros(len(tg), dtype=int)
    pos = 0
    for (_, _), g in tg.groupby(["team", "season"], sort=False):
        d = g.date.values
        out[pos:pos + len(g)] = np.searchsorted(d, d, side="left")
        pos += len(g)
    tg["n_before"] = out
    return tg


def compute_flags(e3_rows: pd.DataFrame, tg: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """game_id, q_skip, new_skip, early_skip, flags_defined.
    e3_rows: e3_features rows (game_id, team_k, n_q_rot_c60, new_share_own_c60), two rows per game; the game flag looks at EITHER row.
    tg: (game_id, team, season, date) team-games; games: game_id list (frame with game_id, home, away)."""
    r = e3_rows.copy()
    q = r.groupby("game_id").n_q_rot_c60.max() >= Q_MIN
    nw = r.groupby("game_id").new_share_own_c60.max() >= NEW_MAX
    cnt = team_game_counts_before(tg)
    early = (cnt.groupby("game_id").n_before.min() < EARLY_MIN_GAMES)
    f = pd.DataFrame({"game_id": games.game_id.values.astype("int64")})
    f["flags_defined"] = f.game_id.isin(q.index)
    f["q_skip"] = f.game_id.map(q).fillna(False).astype(bool)
    f["new_skip"] = f.game_id.map(nw).fillna(False).astype(bool)
    f["early_skip"] = f.game_id.map(early).fillna(False).astype(bool)
    return f


def skip_mask(ev: pd.DataFrame, skips) -> np.ndarray:
    m = np.zeros(len(ev), dtype=bool)
    for s in skips:
        m |= ev[{"Q": "q_skip", "NEW": "new_skip", "EARLY": "early_skip"}[s]].values.astype(bool)
    return m


# ------------------------------------------------------------------ E3 (cross-fitted for 2022-23; frozen otherwise)
def e3_residual_frozen(frozen_ridge: dict, rows: pd.DataFrame) -> np.ndarray:
    X = rows[frozen_ridge["features"]].values.astype(float)
    return E2.ridge_predict(X, np.array(frozen_ridge["mu"]), np.array(frozen_ridge["sd"]), frozen_ridge["b0"], np.array(frozen_ridge["coef"]))


def e3_crossfit_residual(features: list, train: pd.DataFrame, target: np.ndarray, apply_rows: pd.DataFrame, lam: float = 1000.0) -> np.ndarray:
    """Standardisation + ridge (lambda fixed) fitted on `train` rows ONLY; target = actual team pts - E1 team pred."""
    X = train[features].values.astype(float)
    mu, sd = E2.standardise_fit(X)
    b0, b = E2.ridge_fit((X - mu) / sd, np.asarray(target, float), lam)
    return E2.ridge_predict(apply_rows[features].values.astype(float), mu, sd, b0, b)


# ------------------------------------------------------------------ P&L
def sb_pnl(side_over: np.ndarray, actual: np.ndarray, line: np.ndarray) -> np.ndarray:
    """-110 flat; side_over True = OVER.  win +10/11, loss -1, push (actual == line) 0."""
    actual, line = np.asarray(actual, float), np.asarray(line, float)
    over = np.where(actual > line, WIN_PAY, np.where(actual < line, -1.0, 0.0))
    under = np.where(actual < line, WIN_PAY, np.where(actual > line, -1.0, 0.0))
    return np.where(side_over, over, under)


def kalshi_net(win: float, ask: float, fee: float, slip: float = 0.01) -> float:
    """1/0 payout minus (ask + 1c) minus fee (the #42 machinery: grid_bets.net_over)."""
    return float(win) - ask - slip - fee


def kalshi_fee1(price: float) -> float:
    """0.07 * P * (1-P) rounded UP to $0.000001 -- computed by #37's FeeModel via grid_bets.fee1 (imported lazily)."""
    _grid_path()
    import grid_bets as gb
    return float(gb.fee1(price))


def _grid_path():
    for p in (str(FA_DIR), str(GRID_DIR)):
        if p not in sys.path:
            sys.path.append(p)


def summarize_sb(pnl: np.ndarray) -> dict:
    pnl = np.asarray(pnl, float)
    n = int(len(pnl))
    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    push = n - wins - losses
    return {"n": n, "mean": (float(pnl.mean()) if n else float("nan")), "wins": wins, "losses": losses, "pushes": int(push),
            "win_rate_decided": (wins / (wins + losses) if wins + losses else float("nan")), "total": float(pnl.sum())}


# ------------------------------------------------------------------ Part A bets
def model_adjusted(ev: pd.DataFrame, model: str, lean: str) -> np.ndarray:
    tot = ev[MODEL_COL[model]].values.astype(float)
    if lean == "rolling":
        return tot + ev["b_" + MODEL_COL[model]].values.astype(float)
    return tot


def part_a_decision(ev: pd.DataFrame, ver: dict, ref: np.ndarray):
    """-> (bet mask, over mask).  ref = the line (sportsbook close) or the Kalshi T-1h implied median.  Bet OVER if adj - ref >= G,
    UNDER if ref - adj >= G; skips remove games; games whose adj or ref is not finite are never bet."""
    adj = model_adjusted(ev, ver["model"], ver["lean"])
    ref = np.asarray(ref, float)
    d = adj - ref
    okd = np.isfinite(d) & ~skip_mask(ev, ver["skips"])
    dd = np.nan_to_num(d)
    over = okd & (dd >= ver["G"])
    under = okd & (-dd >= ver["G"])
    return over | under, over


def part_a_sb(ev: pd.DataFrame, ver: dict) -> pd.DataFrame:
    """Sportsbook bets of a Part A version over all games of `ev`: game_id, season, over (bool), pnl."""
    bet, over = part_a_decision(ev, ver, ev.close_line.values)
    k = bet & np.isfinite(ev.actual_total.values)
    return pd.DataFrame({"game_id": ev.game_id.values[k], "season": ev.season.values[k], "over": over[k],
                         "pnl": sb_pnl(over[k], ev.actual_total.values[k], ev.close_line.values[k])})


def part_a_kalshi(ev: pd.DataFrame, ver: dict) -> pd.DataFrame:
    """Kalshi bets (2025-26 rows with a valid settled main strike): gap vs the T-1h implied median, buy the model's side at the main strike."""
    bet, over = part_a_decision(ev, ver, ev.k_median.values)
    k = bet & ev.k_valid.fillna(False).values.astype(bool)
    net = np.where(over, ev.k_net_over.values, ev.k_net_under.values).astype(float)
    win = np.where(over, ev.k_win_over.values, ev.k_win_under.values).astype(float)
    return pd.DataFrame({"game_id": ev.game_id.values[k], "season": ev.season.values[k], "over": over[k], "net": net[k], "win": win[k]})


# ------------------------------------------------------------------ Part B bets
def part_b_stat(ev: pd.DataFrame, ver: dict, flavor: str) -> np.ndarray:
    sh = ev[f"S_{flavor}_M{ver['M']}_home"].values.astype(float)
    sa = ev[f"S_{flavor}_M{ver['M']}_away"].values.astype(float)
    return sh + sa if ver["scope"] == "game" else np.maximum(sh, sa)


def part_b_mask(ev: pd.DataFrame, ver: dict, flavor: str) -> np.ndarray:
    ok = ev[f"ok_{flavor}"].values.astype(bool)
    return ok & (np.nan_to_num(part_b_stat(ev, ver, flavor), nan=-1.0) >= ver["tau"])


def part_b_sb(ev: pd.DataFrame, ver: dict, flavor: str) -> pd.DataFrame:
    k = part_b_mask(ev, ver, flavor) & np.isfinite(ev.close_line.values) & np.isfinite(ev.actual_total.values)
    ov = np.ones(int(k.sum()), dtype=bool)
    return pd.DataFrame({"game_id": ev.game_id.values[k], "season": ev.season.values[k], "over": ov,
                         "pnl": sb_pnl(ov, ev.actual_total.values[k], ev.close_line.values[k])})


def part_b_kalshi(ev: pd.DataFrame, ver: dict, flavor: str = "report") -> pd.DataFrame:
    k = part_b_mask(ev, ver, flavor) & ev.k_valid.fillna(False).values.astype(bool)
    return pd.DataFrame({"game_id": ev.game_id.values[k], "season": ev.season.values[k], "over": np.ones(int(k.sum()), dtype=bool),
                         "net": ev.k_net_over.values[k].astype(float), "win": ev.k_win_over.values[k].astype(float)})


def version_sb_bets(ev: pd.DataFrame, vid: str, flavor: str = "report") -> pd.DataFrame:
    ver = parse_version(vid)
    return part_a_sb(ev, ver) if ver["part"] == "A" else part_b_sb(ev, ver, flavor)


def version_kalshi_bets(ev: pd.DataFrame, vid: str) -> pd.DataFrame:
    ver = parse_version(vid)
    return part_a_kalshi(ev, ver) if ver["part"] == "A" else part_b_kalshi(ev, ver, "report")


# ------------------------------------------------------------------ bootstrap and luck tests
def boot_ci(x, n_boot=BOOT_N, seed=BOOT_SEED, chunk=500):
    """Game bootstrap of the mean (each bet = one game): (mean, 2.5th, 97.5th percentile).  Deterministic given the seed."""
    x = np.asarray(x, float)
    if len(x) == 0:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for lo in range(0, n_boot, chunk):
        k = min(chunk, n_boot - lo)
        means[lo:lo + k] = x[rng.integers(0, len(x), size=(k, len(x)))].mean(axis=1)
    return float(x.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def luck_random_sides(pnl_over: np.ndarray, pnl_under: np.ndarray, actual_mean: float, n=LUCK_N, seed=LUCK_SEED) -> dict:
    """Part A luck test: the SAME bet games, a fair coin per game for the side, same costs; n copies.
    Share of copies whose mean P&L per bet is >= the actual."""
    pnl_over, pnl_under = np.asarray(pnl_over, float), np.asarray(pnl_under, float)
    if len(pnl_over) == 0:
        return {"copies": n, "frac_copies_ge_actual": float("nan"), "copies_mean": float("nan"), "actual_mean": actual_mean, "n_bets": 0}
    rng = np.random.default_rng(seed)
    side = rng.integers(0, 2, (n, len(pnl_over))).astype(bool)
    means = np.where(side, pnl_over, pnl_under).mean(axis=1)
    return {"copies": n, "frac_copies_ge_actual": float((means >= actual_mean - 1e-12).mean()), "copies_mean": float(means.mean()),
            "actual_mean": float(actual_mean), "n_bets": int(len(pnl_over))}


def luck_random_games(elig_over_pnl_by_season: dict, n_by_season: dict, actual_mean: float, n=LUCK_N, seed=LUCK_SEED) -> dict:
    """Part B luck test: per season the same number of OVER bets as the real version has, on RANDOM ELIGIBLE games of that season
    (without replacement); the pooled mean P&L per bet of each copy vs the actual.  elig_over_pnl_by_season: season -> array of the
    OVER P&L of every eligible game of that season."""
    rng = np.random.default_rng(seed)
    tot = np.zeros(n)
    nb = 0
    for s in sorted(n_by_season):
        k = int(n_by_season[s])
        if k == 0:
            continue
        pool = np.asarray(elig_over_pnl_by_season[s], float)
        assert k <= len(pool), f"{s}: {k} real bets but only {len(pool)} eligible games"
        keys = rng.random((n, len(pool)))
        idx = np.argpartition(keys, k - 1, axis=1)[:, :k] if k < len(pool) else np.tile(np.arange(len(pool)), (n, 1))
        tot += pool[idx].sum(axis=1)
        nb += k
    if nb == 0:
        return {"copies": n, "frac_copies_ge_actual": float("nan"), "copies_mean": float("nan"), "actual_mean": actual_mean, "n_bets": 0}
    means = tot / nb
    return {"copies": n, "frac_copies_ge_actual": float((means >= actual_mean - 1e-12).mean()), "copies_mean": float(means.mean()),
            "actual_mean": float(actual_mean), "n_bets": int(nb)}


# ------------------------------------------------------------------ evaluation frame (one row per game)
def make_eval_frame(preds: pd.DataFrame, flags: pd.DataFrame, bfeat: pd.DataFrame) -> pd.DataFrame:
    """preds + flags + the Part B stats pivoted to home/away columns S_<flavor>_M<M>_home/_away and ok_<flavor> (both teams defined and
    the flavor's information present for both teams)."""
    ev = preds.merge(flags, on="game_id", how="left", validate="one_to_one")
    for c in ("q_skip", "new_skip", "early_skip", "flags_defined"):
        ev[c] = ev[c].fillna(False).astype(bool)
    bf = bfeat.copy()
    ev = ev.copy()
    for side, key in (("home", "home"), ("away", "away")):
        sub = bf.merge(ev[["game_id", key]].rename(columns={key: "team"}), on=["game_id", "team"], how="inner")
        cols = ["game_id", "defined", "report_found", "box_found"] + [f"S_{f}_M{M}" for f in FLAVORS for M in B_MS]
        sub = sub[cols].rename(columns={c: f"{c}_{side}" for c in cols if c != "game_id"})
        ev = ev.merge(sub, on="game_id", how="left", validate="one_to_one")
    for f in FLAVORS:
        for M in B_MS:
            for side in ("home", "away"):
                ev[f"S_{f}_M{M}_{side}"] = ev[f"S_{f}_M{M}_{side}"].astype(float)
    dd = ev.defined_home.fillna(False).astype(bool) & ev.defined_away.fillna(False).astype(bool)
    ev["ok_report"] = dd & ev.report_found_home.fillna(False).astype(bool) & ev.report_found_away.fillna(False).astype(bool)
    ev["ok_box"] = dd & ev.box_found_home.fillna(False).astype(bool) & ev.box_found_away.fillna(False).astype(bool)
    return ev


def restrict(ev: pd.DataFrame, seasons) -> pd.DataFrame:
    return ev[ev.season.isin(set(seasons))].reset_index(drop=True)


# ------------------------------------------------------------------ Part B: absentee features (pure pieces)
def window_candidates(stats: dict, team: str, window_gids: list, current_aids, fresh_n: int = B_FRESH_GAMES) -> dict:
    """Players of `team` who are current-team players (aid in current_aids) and appear in the stats of at least one window game.
    stats[(team, game_id)] = {aid: (minutes, pts)} for players with minutes > 0.  Window = X's last <= 10 games of this season before g
    (list of game ids, oldest first).  Returns aid -> (mpg_per_team_game, fresh_ppg, fresh) with DNP/absent counted as 0 and
    fresh = minutes > 0 in at least one of the last `fresh_n` games of the window."""
    n = len(window_gids)
    seen = set()
    for g in window_gids:
        seen.update(stats.get((team, g), {}))
    out = {}
    last = window_gids[-fresh_n:]
    for a in seen:
        if a not in current_aids:
            continue
        mins = pts = 0.0
        for g in window_gids:
            r = stats.get((team, g), {}).get(a)
            if r is not None:
                mins += r[0]
                pts += r[1]
        fresh = any(a in stats.get((team, g), {}) for g in last)
        out[a] = (mins / n, pts / n, bool(fresh))
    return out


def absentees(cands: dict, M: float, absent_set) -> list:
    """Players with mpg_per_team_game >= M, fresh, and in absent_set -> sorted list of (aid, fresh_ppg, mpg)."""
    res = [(int(a), float(v[1]), float(v[0])) for a, v in cands.items() if v[0] >= M and v[2] and a in absent_set]
    return sorted(res)


def abs_json(lst) -> str:
    return json.dumps([{"athlete_id": a, "fresh_ppg": round(p, 6), "mpg_per_team_game": round(m, 6)} for a, p, m in lst])


def report_out_ids(listed: dict, cn: dict, statuses=E2.OUT_STATUSES):
    """Most severe status per matched athlete -> (set of athletes listed OUT/DOUBTFUL, dict aid->status, n_unmatched).
    listed: {raw name: status} of one team; cn: normalised display name -> [aid] of the CURRENT-team players (exact matching)."""
    st, unmatched = {}, 0
    for raw, s in listed.items():
        if s not in E2.STATUS_RANK:
            continue
        a = E2.match_name(raw, cn, None)
        if a is None:
            unmatched += 1
            continue
        if E2.STATUS_RANK[s] > E2.STATUS_RANK.get(st.get(a), 0):
            st[a] = s
    return {a for a, s in st.items() if s in statuses}, st, unmatched


def build_part_b(games: pd.DataFrame, box_df: pd.DataFrame, reports=None, Ms=B_MS, tip_check: bool = True) -> pd.DataFrame:
    """One row per (game_id, team).  Uses ONLY box rows dated strictly before the game's ET date (state is updated after each day's
    queries), reports created <= tip - 3600 s (#62 rules), and for the box flavor the game's own box rows ONLY to decide who was absent.

    games : game_id, season, date (ET Timestamp), tip (UTC s), away, home (Kalshi codes)
    box_df: game_id, yr, d, aid, name, team, played, minutes, pts   (regular season only, NBA teams; E2.load_box(...).df)
    reports: e2_lib.Reports or None.
    Output columns: game_id, season, et_date, team, opp, is_home, defined, n_team_games_before, report_found, report_name, box_found,
      S_report_M*, S_box_M*, abs_report_M*, abs_box_M* (JSON), boxabs_n_M*, boxabs_listed_t60_M*, boxabs_listed_tip_M*."""
    games = games.copy()
    games["d"] = [E2.day_int(x) for x in games.date]
    gtip = dict(zip(games.game_id.astype("int64").values, games.tip.astype(float).values))
    bdf = box_df.copy()
    bdf["tipx"] = bdf.game_id.map(gtip).astype(float).fillna(bdf.d.astype(float) * 86400.0)
    bdf = bdf.sort_values(["tipx", "game_id", "aid"], kind="stable")
    bgroups = [(float(t), g) for (t, _), g in bdf.groupby(["tipx", "game_id"], sort=True)]     # box games in tip order
    names = {}
    for a, n in zip(bdf.aid.values, bdf.name.values):
        names[int(a)] = n
    tgdf = bdf.drop_duplicates(["team", "game_id"])[["team", "game_id", "d", "yr"]].sort_values(["d", "game_id"], kind="stable")
    tg = {t: (g.d.values.astype(np.int64), g.game_id.values.astype(np.int64), g.yr.values.astype(np.int64)) for t, g in tgdf.groupby("team", sort=False)}
    pl = bdf[bdf.played]
    stats = {}
    for t, gid, a, m, p in zip(pl.team.values, pl.game_id.values, pl.aid.values, pl.minutes.values, pl.pts.values):
        stats.setdefault((t, int(gid)), {})[int(a)] = (float(m), float(p))
    last_team, last_yr, roster = {}, {}, {}
    recs = []
    queries = games.sort_values(["tip", "game_id"], kind="stable")
    bi = 0
    for _, r in queries.iterrows():
        tip = float(r.tip)
        while bi < len(bgroups) and bgroups[bi][0] < tip:        # state = box rows of games with tip STRICTLY before this game's tip
            b = bgroups[bi][1]
            for aid, team, yr in zip(b.aid.values, b.team.values, b.yr.values):
                aid = int(aid)
                old = last_team.get(aid)
                if old != team:
                    if old is not None:
                        roster[old].discard(aid)
                    roster.setdefault(team, set()).add(aid)
                    last_team[aid] = team
                last_yr[aid] = int(yr)
            bi += 1
        gid, d = r.game_id, int(r.d)
        away, home = r.away, r.home
        year = E2.year_of(r.season)
        matchup, mdy = f"{away}@{home}", pd.Timestamp(r.date).strftime("%m/%d/%Y")
        cov60 = reports.last_covering(tip - 3600, mdy, matchup) if reports is not None else None
        covtip = (reports.last_covering(tip, mdy, matchup) if (reports is not None and tip_check) else None)
        for team, opp, is_home in ((away, home, False), (home, away, True)):
            td_gid = tg.get(team)
            if td_gid is None:
                ncur, window = 0, []
            else:
                td, tgid, ty = td_gid
                i = int(np.searchsorted(td, d, side="left"))        # team games STRICTLY before d
                lo = int(np.searchsorted(ty[:i], year, side="left"))
                cur = [int(x) for x in tgid[lo:i]]
                ncur, window = len(cur), cur[-B_WINDOW:]
            defined = ncur >= B_MIN_TEAM_GAMES
            cur_aids = {a for a in roster.get(team, ()) if last_yr.get(a, 0) == year}   # most recent row before d is for team, this season
            cands = window_candidates(stats, team, window, cur_aids) if defined else {}
            played_g = set(stats.get((team, int(gid)), {}))
            box_found = (team, int(gid)) in stats
            cn = {}
            for a in cur_aids:
                cn.setdefault(E2.norm_display(names.get(a, "")), []).append(a)

            def listed_for(cov):
                if cov is None:
                    return {}
                listed, _ = E2.team_status_map(cov[2], cov[3], team)
                return listed

            out60, _, _ = report_out_ids(listed_for(cov60), cn)
            outtip, _, _ = report_out_ids(listed_for(covtip), cn)
            row = {"game_id": int(gid), "season": r.season, "et_date": pd.Timestamp(r.date), "team": team, "opp": opp,
                   "is_home": is_home, "defined": bool(defined), "n_team_games_before": int(ncur),
                   "report_found": cov60 is not None, "report_name": (cov60[1] if cov60 else ""), "box_found": bool(box_found)}
            for M in Ms:
                ar = absentees(cands, M, out60) if defined else []
                absent_box = {a for a in cands if a not in played_g} if box_found else set()
                ab = absentees(cands, M, absent_box) if defined else []
                row[f"S_report_M{M}"] = float(sum(p for _, p, _ in ar))
                row[f"S_box_M{M}"] = float(sum(p for _, p, _ in ab))
                row[f"abs_report_M{M}"] = abs_json(ar)
                row[f"abs_box_M{M}"] = abs_json(ab)
                row[f"boxabs_n_M{M}"] = len(ab)
                row[f"boxabs_listed_t60_M{M}"] = sum(1 for a, _, _ in ab if a in out60)
                row[f"boxabs_listed_tip_M{M}"] = sum(1 for a, _, _ in ab if a in outtip)
            recs.append(row)
    F = pd.DataFrame(recs)
    # a season with no report at all (2020-21) has an UNDEFINED report flavor: NaN, not 0 (a season WITH reports but a game without one stays 0)
    no_rep = F.groupby("season").report_found.transform("any") == False          # noqa: E712
    for M in Ms:
        F.loc[no_rep, f"S_report_M{M}"] = np.nan
        F.loc[no_rep, f"abs_report_M{M}"] = ""
    return F.sort_values(["season", "et_date", "game_id", "team"], kind="stable").reset_index(drop=True)


# ------------------------------------------------------------------ hashing / audit
def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


OWN_AUDIT = ("rt_lib.py", "build_tables.py", "gates.py", "run_select.py", "run_test.py", "make_audit_hashes.py",
             "../PREREGISTRATION.md", "../versions_all.csv",
             "../main_out/preds.parquet", "../main_out/flags.parquet", "../main_out/b_features.parquet", "../main_out/lean.parquet",
             "../results_select.json")
UPSTREAM_AUDIT = (
    "../../nba_score_model_e2/main/e2_lib.py", "../../nba_formula_accuracy/fa_lib.py",
    "../../nba_totals_grid/grid_lib.py", "../../nba_totals_grid/grid_bets.py", "../../nba_totals_grid/grid_run.py",
    "../../nba_totals_grid/winner_frozen.json",
    "../../nba_score_model_e3/frozen_e3.json", "../../nba_score_model_e3/e3_features.parquet",
    "../../nba_assumptions_audit/partB/_cache/e1_2021_26.parquet",
    "../../nba_model_vs_lines/mvl_lib.py", "../../nba_model_vs_lines/records.csv",
    "../../nba_model/injury_parsed_v3.json", "../../nba_teammate_out/injury_parsed_tm.jsonl",
    "../../../../data/raw/nba_injury/_probe_cache_v3.json",
    *[f"../../../../data/raw/nba_open/player_box_{y}.parquet" for y in range(2021, 2027)],
    *[f"../../../../data/raw/nba_open/team_box_{y}.parquet" for y in range(2021, 2027)],
    "../../../../data/raw/nba_odds_samples/closing_lines_odds_api.parquet")
AUDIT_FILES = OWN_AUDIT + UPSTREAM_AUDIT


def audit_hashes(base=HERE, files=AUDIT_FILES) -> dict:
    return {"sha256": {f: sha256_file(Path(base) / f) for f in files}}


def check_audit_ok(study=STUDY, base=HERE, files=AUDIT_FILES):
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


# ------------------------------------------------------------------ table loading
def load_tables(seasons=None, main_out=MAIN_OUT) -> pd.DataFrame:
    """preds + flags + Part B stats as one row per game.  `seasons`: restrict BEFORE anything else (run_select passes the D seasons so
    holdout rows never enter memory)."""
    preds = pd.read_parquet(Path(main_out) / "preds.parquet")
    flags = pd.read_parquet(Path(main_out) / "flags.parquet")
    bf = pd.read_parquet(Path(main_out) / "b_features.parquet")
    if seasons is not None:
        preds = preds[preds.season.isin(set(seasons))]
        bf = bf[bf.season.isin(set(seasons))]
        flags = flags[flags.game_id.isin(set(preds.game_id))]
    return make_eval_frame(preds.reset_index(drop=True), flags, bf)


# ------------------------------------------------------------------ selection (D only)
def choose_best(rows: pd.DataFrame, min_bets: int):
    """rows: version, n, mean_pnl (in enumeration order).  Highest mean P&L per bet among versions with n >= min_bets; ties (equal to
    1e-12) -> more bets; then earlier enumeration order.  Returns the chosen row (Series) or None."""
    el = rows[(rows.n >= min_bets) & rows.mean_pnl.notna()].copy()
    if el.empty:
        return None
    el["_o"] = np.arange(len(el))
    el["_m"] = el.mean_pnl.round(12)
    return el.sort_values(["_m", "n", "_o"], ascending=[False, False, True], kind="stable").iloc[0].drop(["_o", "_m"])


# ------------------------------------------------------------------ holdout evaluation (windows are parameters so D can rehearse)
HOLDOUT_WINDOWS = {"A": {"sb": [("H2-sb", H2_SB, "report")], "k": H2_K},
                   "B": {"sb": [("H1", H1, "box"), ("H2-sb", H2_SB, "report")], "k": H2_K}}
REHEARSAL_WINDOWS = {"A": {"sb": [("H2-sb*", ("2023-24",), "report")], "k": ()},
                     "B": {"sb": [("H1*", ("2021-22",), "box"), ("H2-sb*", ("2022-23", "2023-24"), "report")], "k": ()}}


def evaluate_version(ev: pd.DataFrame, vid: str, windows: dict) -> dict:
    """Everything the verdict needs for ONE chosen version.  `ev` must already contain only the seasons of the windows."""
    ver = parse_version(vid)
    parts, per_window, sides_over, sides_under = [], {}, [], []
    elig_pnl, n_by_season = {}, {}
    for name, seasons, flavor in windows["sb"]:
        e = restrict(ev, seasons)
        b = version_sb_bets(e, vid, flavor)
        per_window[name] = summarize_sb(b.pnl.values)
        parts.append(b)
        if ver["part"] == "A":
            g = e.set_index("game_id").loc[b.game_id.values]
            sides_over.append(sb_pnl(np.ones(len(g), bool), g.actual_total.values, g.close_line.values))
            sides_under.append(sb_pnl(np.zeros(len(g), bool), g.actual_total.values, g.close_line.values))
        else:
            for s in seasons:
                es = e[e.season == s]
                k = es[f"ok_{flavor}"].values.astype(bool) & np.isfinite(es.close_line.values) & np.isfinite(es.actual_total.values)
                elig_pnl[s] = sb_pnl(np.ones(int(k.sum()), bool), es.actual_total.values[k], es.close_line.values[k])
                n_by_season[s] = int((b.season == s).sum())
    pooled_pnl = np.concatenate([b.pnl.values for b in parts]) if parts else np.array([])
    pooled = summarize_sb(pooled_pnl)
    m, lo, hi = boot_ci(pooled_pnl)
    pooled.update(boot_mean=m, boot_lo=lo, boot_hi=hi)
    if ver["part"] == "A":
        luck = luck_random_sides(np.concatenate(sides_over) if sides_over else [], np.concatenate(sides_under) if sides_under else [], pooled["mean"])
    else:
        luck = luck_random_games(elig_pnl, n_by_season, pooled["mean"]) if pooled["n"] else luck_random_sides([], [], float("nan"))
    kseasons = windows["k"]
    kb = version_kalshi_bets(restrict(ev, kseasons), vid) if len(kseasons) else pd.DataFrame({"net": [], "win": []})
    k = {"n": int(len(kb)), "mean_net": (float(kb.net.mean()) if len(kb) else float("nan")),
         "win_rate": (float(kb.win.mean()) if len(kb) else float("nan"))}
    return {"version": vid, "per_window_sb": per_window, "pooled_sb": pooled, "luck": luck, "kalshi": k}


def verdict(res: dict) -> dict:
    """PASS: pooled sb mean > 0 with bootstrap lower bound > 0; Kalshi mean net > 0 with >= 30 bets; luck < 5%.
    PROMISING: not PASS, every holdout point estimate > 0 (each sb window separately; Kalshi when it has >= 10 bets) and luck < 10%.
    FAIL otherwise."""
    pooled, k, lf = res["pooled_sb"], res["kalshi"], res["luck"]["frac_copies_ge_actual"]
    gt0 = lambda x: bool(np.isfinite(x) and x > 0)          # noqa: E731
    c1 = gt0(pooled["mean"]) and gt0(pooled["boot_lo"])
    c2 = k["n"] >= 30 and gt0(k["mean_net"])
    c3 = bool(np.isfinite(lf) and lf < 0.05)
    passed = c1 and c2 and c3
    each = all(gt0(w["mean"]) for w in res["per_window_sb"].values()) and (k["n"] < 10 or gt0(k["mean_net"]))
    prom = (not passed) and each and bool(np.isfinite(lf) and lf < 0.10)
    return {"criteria": {"1_sb_pooled_mean_gt0_and_boot_lb_gt0": c1, "2_kalshi_mean_gt0_n_ge_30": c2, "3_luck_lt_5pct": c3},
            "verdict": "PASS" if passed else ("PROMISING" if prom else "FAIL")}
