"""Research log #66, Part A (formula families): the 56 versions, bets, P&L, selection helper and holdout evaluation.

Pure functions on a game frame.  `frame` = main_out/game_table.parquet rows (predictions + close, NO outcomes) plus, when evaluating,
a `total` column added by nfl_lib.attach_outcomes for the allowed seasons only.
Prediction columns: apts_h{h}_k{k}, aepa_h{h}, adrive, ares_pred (predicted total - close, positive = over).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import nfl_lib as N

TOL = 1e-9


def parse_version(v: str) -> dict:
    """'A-PTS|h8|k4|G3' / 'A-EPA|h8|G4.5' / 'A-DRIVE|G1.5' / 'A-RES|g0.5' -> dict(fam, col, thr, h, k)."""
    p = v.split("|")
    fam = p[0]
    if fam == "A-PTS":
        h, k, G = int(p[1][1:]), int(p[2][1:]), float(p[3][1:])
        return {"version": v, "fam": fam, "h": h, "k": k, "thr": G, "col": f"apts_h{h}_k{k}"}
    if fam == "A-EPA":
        h, G = int(p[1][1:]), float(p[2][1:])
        return {"version": v, "fam": fam, "h": h, "k": None, "thr": G, "col": f"aepa_h{h}"}
    if fam == "A-DRIVE":
        return {"version": v, "fam": fam, "h": None, "k": None, "thr": float(p[1][1:]), "col": "adrive"}
    if fam == "A-RES":
        return {"version": v, "fam": fam, "h": None, "k": None, "thr": float(p[1][1:]), "col": "ares_pred"}
    raise ValueError(f"unknown version {v!r}")


def load_version_ids(path=N.VERSIONS_A_CSV) -> list:
    return list(pd.read_csv(path).version)


def all_versions() -> list:
    """The 56 version ids generated from the grid (must equal versions_A.csv)."""
    out = []
    for h in N.HS:
        for k in N.KS:
            out += [f"A-PTS|h{h}|k{k}|G{g:g}" for g in N.G_GRID]
    for h in N.HS:
        out += [f"A-EPA|h{h}|G{g:g}" for g in N.G_GRID]
    out += [f"A-DRIVE|G{g:g}" for g in N.G_GRID]
    out += [f"A-RES|g{g:g}" for g in N.GRES_GRID]
    return out


def decide(frame: pd.DataFrame, ver: dict, ref=None):
    """-> (bet mask, over mask).  A-PTS/EPA/DRIVE: OVER if pred - ref >= G, UNDER if ref - pred >= G (ref = the close, or the Kalshi
    implied median for the Kalshi evaluation).  A-RES: OVER if predicted residual >= g, UNDER if <= -g (ref unused).  Games whose
    prediction (or ref) is not finite are never bet."""
    pred = frame[ver["col"]].values.astype(float)
    if ver["fam"] == "A-RES":
        d = pred
    else:
        ref = frame.close.values.astype(float) if ref is None else np.asarray(ref, float)
        d = pred - ref
    ok = np.isfinite(d)
    dd = np.nan_to_num(d)
    over = ok & (dd >= ver["thr"] - TOL)
    under = ok & (-dd >= ver["thr"] - TOL)
    return over | under, over


def sb_bets(frame: pd.DataFrame, vid: str) -> pd.DataFrame:
    """Sportsbook (-110 flat) bets of a version: game_id, season, over, pnl.  frame needs `total` (only for allowed seasons)."""
    ver = parse_version(vid)
    bet, over = decide(frame, ver)
    k = bet & np.isfinite(frame.total.values.astype(float))
    return pd.DataFrame({"game_id": frame.game_id.values[k], "season": frame.season.values[k], "over": over[k],
                         "pnl": N.sb_pnl(over[k], frame.total.values[k], frame.close.values[k])})


def version_row(frame: pd.DataFrame, vid: str) -> dict:
    """One row of the D table: version, n, wins, losses, pushes, mean_pnl, win_rate_decided, total."""
    b = sb_bets(frame, vid)
    s = N.summarize_sb(b.pnl.values)
    return {"version": vid, "n": s["n"], "wins": s["wins"], "losses": s["losses"], "pushes": s["pushes"], "mean_pnl": s["mean"],
            "win_rate_decided": s["win_rate_decided"], "total_pnl": s["total"]}


def kalshi_bets(frame: pd.DataFrame, hk: pd.DataFrame, vid: str) -> pd.DataFrame:
    """Kalshi bets (HK): the gap is taken against the Kalshi T-60 implied median M (A-RES: sign of the predicted residual); the model's
    side is bought at the main strike (OVER = YES at the ask, UNDER = NO at 1 - yes bid), taker, ask + 1c + fee.  hk: one row per HK
    game with game_id, M, net_over, net_under (valid rows only)."""
    ver = parse_version(vid)
    m = hk.merge(frame.drop(columns=[c for c in ("M",) if c in frame.columns]), on="game_id", how="inner", suffixes=("", "_f"))
    bet, over = decide(m, ver, ref=m.M.values)
    net = np.where(over, m.net_over.values, m.net_under.values).astype(float)
    k = bet & np.isfinite(net)
    return pd.DataFrame({"game_id": m.game_id.values[k], "over": over[k], "net": net[k]})


def evaluate_version(vid: str, frames: dict, hk: pd.DataFrame, pooled=("H1", "H2")) -> dict:
    """Everything the verdict needs for ONE chosen version.  frames: {'H1': frame, 'H2': frame, 'H3': frame} (each with `total`);
    hk: the HK game table (with predictions merged by game_id from `frames['HK_pred']`, the all-games prediction frame)."""
    per, parts, so, su = {}, [], [], []
    for w in ("H1", "H2", "H3"):
        f = frames[w]
        b = sb_bets(f, vid)
        per[w] = N.summarize_sb(b.pnl.values)
        if w in pooled:
            parts.append(b)
            g = f.set_index("game_id").loc[b.game_id.values]
            so.append(N.sb_pnl(np.ones(len(g), bool), g.total.values, g.close.values))
            su.append(N.sb_pnl(np.zeros(len(g), bool), g.total.values, g.close.values))
    pnl = np.concatenate([b.pnl.values for b in parts]) if parts else np.array([])
    pooled_s = N.summarize_sb(pnl)
    m, lo, hi = N.boot_ci(pnl)
    pooled_s.update(boot_mean=m, boot_lo=lo, boot_hi=hi)
    luck = N.luck_random_sides(np.concatenate(so) if so else [], np.concatenate(su) if su else [], pooled_s["mean"])
    allb = pd.concat(parts, ignore_index=True)
    gp = pd.concat([frames[w] for w in pooled], ignore_index=True)
    controls = {"H1H2": N.side_controls(gp, allb, pooled_s["mean"])}
    b3 = sb_bets(frames["H3"], vid)
    controls["H3"] = N.side_controls(frames["H3"], b3, N.summarize_sb(b3.pnl.values)["mean"])
    kb = kalshi_bets(frames["HK_pred"], hk, vid)
    k = {"n": int(len(kb)), "mean_net": (float(kb.net.mean()) if len(kb) else float("nan"))}
    return {"version": vid, "per_window_sb": per, "pooled_sb": pooled_s, "luck": luck, "kalshi": k, "controls": controls}


def _lsp(res: dict) -> float:
    """Share of side-preserving luck copies >= actual on H1+H2 (NaN-safe: missing -> 0)."""
    try:
        v = res["controls"]["H1H2"]["luck_side_preserving"]["frac_copies_ge_actual"]
    except KeyError:
        return 0.0
    return float(v) if np.isfinite(v) else 0.0


def verdict(res: dict) -> dict:
    """PASS: pooled H1+H2 mean > 0 with bootstrap LB > 0; HK mean net > 0 with >= 30 bets; luck < 5%.
    PROMISING (not PASS): H1, H2, H3 (if >= 10 bets) and HK (if >= 10 bets) point estimates all > 0 and luck < 10%.  Else FAIL."""
    pooled, k, lf = res["pooled_sb"], res["kalshi"], res["luck"]["frac_copies_ge_actual"]
    gt0 = lambda x: bool(np.isfinite(x) and x > 0)          # noqa: E731
    c1 = gt0(pooled["mean"]) and gt0(pooled["boot_lo"])
    c2 = k["n"] >= 30 and gt0(k["mean_net"])
    c3 = bool(np.isfinite(lf) and lf < 0.05)
    passed = c1 and c2 and c3
    pw = res["per_window_sb"]
    each = (gt0(pw["H1"]["mean"]) and gt0(pw["H2"]["mean"]) and (pw["H3"]["n"] < 10 or gt0(pw["H3"]["mean"]))
            and (k["n"] < 10 or gt0(k["mean_net"])))
    prom = (not passed) and each and bool(np.isfinite(lf) and lf < 0.10)
    label = N.ERA_BIAS_LABEL if _lsp(res) >= 0.10 else None
    out = {"criteria": {"1_h1h2_pooled_mean_gt0_and_boot_lb_gt0": c1, "2_hk_mean_gt0_n_ge_30": c2, "3_luck_lt_5pct": c3},
            "verdict": "PASS" if passed else ("PROMISING" if prom else "FAIL")}
    if label:
        out["label"] = label
    return out
