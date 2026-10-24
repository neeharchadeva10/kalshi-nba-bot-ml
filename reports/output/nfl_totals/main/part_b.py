"""Research log #66, Part B (researched factor rules B1-B5, all UNDER).  Code paths only: nothing here has been run on real outcomes.

Weather inputs (built by another agent): weather/wind_forecasts.parquet [game_id, fc_wind_mph, forecast_ok, obs_wind_mph] and
weather/stadiums.csv [stadium_id, roof_category] ('open-air' / 'retractable' / 'dome' ...).  B2/B3 use ONLY rows with forecast_ok True
(the forecast was issued >= 24 h before kickoff).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import nfl_lib as N

RULES = ("B1", "B2", "B3", "B4", "B5")
NEEDS_HK = ("B2", "B4")                                   # the pre-registered PASS needs HK > 0 with >= 30 bets for B2 and B4
CLEAN_SB = {"B1": ("H3",), "B4": ("D", "H3"), "B5": ("D", "H3"), "B2": ("H3",), "B3": ("H3",)}
SECOND_LOOK_SEASONS = tuple(range(2021, 2026))             # B2/B3 forecast-archive era, reported but not used for the verdict
WIND_LO, WIND_HI = 10.0, 20.0                              # "10-19 mph": 10 <= w < 20
FC_HIGH = 20.0
PRIMETIME_HHMM = "19:00"


def load_weather(weather_dir=N.WEATHER_DIR) -> tuple:
    wf = pd.read_parquet(Path(weather_dir) / "wind_forecasts.parquet")[["game_id", "fc_wind_mph", "forecast_ok", "obs_wind_mph"]]
    st = pd.read_csv(Path(weather_dir) / "stadiums.csv")
    return wf, st


def b_frame(gt: pd.DataFrame, wf: pd.DataFrame, st: pd.DataFrame) -> pd.DataFrame:
    """Game-table rows + forecast columns + roof_category (joined on stadium_id; games with no match get NaN)."""
    f = gt.merge(wf, on="game_id", how="left")
    cat = st.drop_duplicates("stadium_id")[["stadium_id", "roof_category"]]
    f = f.merge(cat, on="stadium_id", how="left")
    f["forecast_ok"] = f.forecast_ok.fillna(False).astype(bool)
    return f


def rule_mask(rule: str, f: pd.DataFrame) -> np.ndarray:
    """Boolean mask of the games on which rule `rule` bets UNDER.  f needs roof, roof_category, wind, fc_wind_mph, forecast_ok,
    game_type, weekday, gametime, week."""
    openair = (f.roof.isin(["outdoors", "open"]) & (f.roof_category == "open-air")).values
    fc = f.fc_wind_mph.values.astype(float) if "fc_wind_mph" in f else np.full(len(f), np.nan)
    ok = f.forecast_ok.values.astype(bool) if "forecast_ok" in f else np.zeros(len(f), bool)
    reg = (f.game_type == "REG").values
    if rule == "B1":
        w = f.wind.values.astype(float)
        return openair & np.isfinite(w) & (w >= WIND_LO) & (w < WIND_HI)
    if rule == "B2":
        return openair & ok & np.isfinite(fc) & (fc >= WIND_LO) & (fc < WIND_HI)
    if rule == "B3":
        return openair & ok & np.isfinite(fc) & (fc >= FC_HIGH)
    if rule == "B4":
        return reg & f.weekday.isin(["Sunday", "Monday"]).values & (f.gametime.fillna("").astype(str).values >= PRIMETIME_HHMM)
    if rule == "B5":
        return reg & (f.weekday == "Thursday").values & (f.week.values >= 2)
    raise ValueError(rule)


def rule_bets(rule: str, f: pd.DataFrame) -> pd.DataFrame:
    """UNDER bets at -110 on the games of f (needs total, close) where the rule fires: game_id, season, window, pnl."""
    m = rule_mask(rule, f) & np.isfinite(f.total.values.astype(float)) & np.isfinite(f.close.values.astype(float))
    g = f[m]
    return pd.DataFrame({"game_id": g.game_id.values, "season": g.season.values, "window": g.window.values,
                         "pnl": N.sb_pnl(np.zeros(len(g), bool), g.total.values, g.close.values)})


def hindsight_split(f: pd.DataFrame, seasons=None) -> dict:
    """B2 falsification: B2 bets (UNDER at -110) split by observed - forecast wind >= 0 vs < 0 (obs_wind_mph from the weather file).
    'hindsight' = the win rate (decided bets) exceeds 52.38% where observed >= forecast but not where observed < forecast."""
    m = rule_mask("B2", f) & np.isfinite(f.total.values.astype(float)) & np.isfinite(f.close.values.astype(float))
    if seasons is not None:
        m &= f.season.isin(seasons).values
    g = f[m & np.isfinite(f.obs_wind_mph.values.astype(float))]
    pnl = N.sb_pnl(np.zeros(len(g), bool), g.total.values, g.close.values)
    diff = (g.obs_wind_mph - g.fc_wind_mph).values
    out = {}
    for name, k in (("obs_ge_fc", diff >= 0), ("obs_lt_fc", diff < 0)):
        s = N.summarize_sb(pnl[k])
        out[name] = {"n": s["n"], "wins": s["wins"], "losses": s["losses"], "pushes": s["pushes"], "win_rate_decided": s["win_rate_decided"],
                     "mean": s["mean"]}
    a, b = out["obs_ge_fc"]["win_rate_decided"], out["obs_lt_fc"]["win_rate_decided"]
    gt = lambda x: bool(np.isfinite(x) and x > N.BREAK_EVEN)          # noqa: E731
    out["edge_was_hindsight"] = bool(gt(a) and not gt(b))
    return out


def evaluate_rule(rule: str, f: pd.DataFrame, hk: pd.DataFrame) -> dict:
    """f: b_frame rows for every season to be evaluated, with `total`; hk: HK main-strike table (game_id, net_under) -- HK games'
    rows must be present in f (their features), totals not needed."""
    bets = rule_bets(rule, f)
    per = {w: N.summarize_sb(bets[bets.window == w].pnl.values) for w in ("W", "D", "H1", "H2", "H3")}
    second = None
    if rule in ("B2", "B3"):
        second = N.summarize_sb(bets[bets.season.isin(SECOND_LOOK_SEASONS)].pnl.values)
    clean = bets[bets.window.isin(CLEAN_SB[rule])]
    pnl = clean.pnl.values
    pooled = N.summarize_sb(pnl)
    m, lo, hi = N.boot_ci(pnl)
    pooled.update(boot_mean=m, boot_lo=lo, boot_hi=hi)
    pool, nby = {}, {}
    for s in sorted(clean.season.unique()):
        fs = f[(f.season == s) & np.isfinite(f.total.values.astype(float)) & np.isfinite(f.close.values.astype(float))]
        pool[s] = N.sb_pnl(np.zeros(len(fs), bool), fs.total.values, fs.close.values)
        nby[s] = int((clean.season == s).sum())
    luck = N.luck_random_games(pool, nby, pooled["mean"]) if pooled["n"] else N.luck_random_sides([], [], float("nan"))
    mk = pd.Series(rule_mask(rule, f), index=f.game_id.values)
    mk = mk[~mk.index.duplicated()]
    hb = hk[hk.game_id.map(mk).fillna(False).astype(bool).values]
    kn = hb.net_under.values.astype(float)
    kn = kn[np.isfinite(kn)]
    kal = {"n": int(len(kn)), "mean_net": float(kn.mean()) if len(kn) else float("nan")}
    cg = f[f.window.isin(CLEAN_SB[rule])]
    cb = clean.assign(over=False)
    controls = N.side_controls(cg, cb, pooled["mean"])
    res = {"rule": rule, "controls": controls, "per_window_sb": per, "second_look_2021_25": second, "clean_pooled_sb": pooled, "luck": luck, "kalshi": kal}
    if rule == "B2":
        res["hindsight_all_forecast_era"] = hindsight_split(f)
        res["hindsight_h3"] = hindsight_split(f, seasons=N.H3_SEASONS)
    return res


def verdict(res: dict) -> dict:
    """PASS: clean sportsbook set mean > 0 with bootstrap LB > 0, HK mean net > 0 with >= 30 bets (B2/B4 only), luck < 5%.
    PROMISING (not PASS): every clean point estimate > 0 (D always; H3 and HK when they have >= 10 bets) and luck < 10%.  Else FAIL.
    B2 with edge_was_hindsight is labelled so whatever its mean."""
    rule = res["rule"]
    pooled, k, lf = res["clean_pooled_sb"], res["kalshi"], res["luck"]["frac_copies_ge_actual"]
    gt0 = lambda x: bool(np.isfinite(x) and x > 0)          # noqa: E731
    c1 = gt0(pooled["mean"]) and gt0(pooled["boot_lo"])
    c2 = (not rule in NEEDS_HK) or (k["n"] >= 30 and gt0(k["mean_net"]))
    c3 = bool(np.isfinite(lf) and lf < 0.05)
    passed = c1 and c2 and c3
    pw = res["per_window_sb"]
    est = []
    for w in CLEAN_SB[rule]:
        n = pw[w]["n"]
        if w == "D" or n >= 10:
            est.append(gt0(pw[w]["mean"]))
    if k["n"] >= 10:
        est.append(gt0(k["mean_net"]))
    each = bool(est) and all(est) and gt0(pooled["mean"])
    prom = (not passed) and each and bool(np.isfinite(lf) and lf < 0.10)
    v = "PASS" if passed else ("PROMISING" if prom else "FAIL")
    out = {"criteria": {"1_clean_mean_gt0_and_boot_lb_gt0": c1, "2_hk_mean_gt0_n_ge_30_if_required": bool(c2), "3_luck_lt_5pct": c3}, "verdict": v}
    try:
        lsp = res["controls"]["luck_side_preserving"]["frac_copies_ge_actual"]
    except KeyError:
        lsp = float("nan")
    if np.isfinite(lsp) and lsp >= 0.10:
        out["label_era_bias"] = N.ERA_BIAS_LABEL
    if rule == "B2" and res.get("hindsight_all_forecast_era", {}).get("edge_was_hindsight"):
        out["label"] = "edge was hindsight"
    return out
