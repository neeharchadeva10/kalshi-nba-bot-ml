"""Research log #65: build the four tables in reports/output/nba_refined_totals/main_out/ (preds, flags, b_features, lean).

Cached data only (no network).  Tables contain outcomes (actual totals, Kalshi results) because the later stages need them, but this
script prints only counts/coverage, never an outcome-based number.
Run:  .venv/Scripts/python.exe reports/output/nba_refined_totals/main/build_tables.py"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rt_lib as R  # noqa: E402

R._grid_path()
import fa_lib as fa  # noqa: E402
import grid_lib as gl  # noqa: E402


def label_of(year: int) -> str:
    return f"{year - 1}-{year % 100:02d}"


def load_games_frames():
    """Regular-season games 2020-21..2025-26 (hoopR team box via #42's team_games_g, which drops non-NBA teams).  Returns
    (games, team_games): games = game_id, season, et_date, tip, away, home, actual_total; team_games = game_id, team, season, date."""
    gs, tgs = [], []
    for y in range(2021, 2027):
        tg = gl.team_games_g(fa.read_box(y), (2,))
        tg["season"] = label_of(y)
        tg["game_id"] = tg.game_id.astype("int64")
        a = tg[tg.venue == "away"].drop_duplicates("game_id")
        h = tg[tg.venue == "home"].drop_duplicates("game_id").set_index("game_id")
        g = pd.DataFrame({"game_id": a.game_id.values, "season": a.season.values, "et_date": pd.to_datetime(a.date.values),
                          "tip": a.tip.values.astype("int64"), "away": a.team.values, "home": a.opp.values,
                          "actual_total": (a.pts + a.opp_pts).values.astype(float)})
        assert (h.reindex(g.game_id.values).team.values == g.home.values).all()
        gs.append(g)
        tgs.append(tg[["game_id", "team", "season", "date"]])
    games = pd.concat(gs, ignore_index=True).sort_values(["season", "et_date", "tip", "game_id"], kind="stable").reset_index(drop=True)
    return games, pd.concat(tgs, ignore_index=True)


def model_tables(games):
    """F42, E1, E3 total per game (NaN where not defined)."""
    out = pd.DataFrame({"game_id": games.game_id.values})
    # ---- E1 per-team predictions (cached #59 predictions)
    e1 = pd.read_parquet(R.E1_CACHE)
    e1["game_id"] = e1.game_id.astype("int64")
    tot_e1 = e1.groupby("game_id").pred.sum()
    out["e1"] = out.game_id.map(tot_e1).astype(float)
    # ---- E3: frozen #64 residual for 2023-24 onward; 2022-23 cross-fitted on 2021-22 rows only
    frozen = json.loads((R.E3_STUDY / "frozen_e3.json").read_text())
    ridge = frozen["ridge"]
    feat = pd.read_parquet(R.E3_STUDY / "e3_features.parquet")
    cols = ridge["features"]
    D = e1.merge(feat[["game_id", "team"] + cols], on=["game_id", "team"], how="left", validate="one_to_one")
    resid = np.full(len(D), np.nan)
    for s in ("2023-24", "2024-25", "2025-26"):
        m = (D.season == s).values
        resid[m] = R.e3_residual_frozen(ridge, D[m])
    tr = (D.season == "2021-22").values
    ap = (D.season == "2022-23").values
    assert D.loc[tr | ap, cols].notna().all().all()
    resid[ap] = R.e3_crossfit_residual(cols, D[tr], D.pts.values[tr] - D.pred.values[tr], D[ap], lam=float(ridge["lambda"]))
    D["e3_team"] = D.pred.values + resid
    out["e3"] = out.game_id.map(D.groupby("game_id").e3_team.sum()).astype(float)     # NaN if any team row NaN (sum of NaN -> 0!)
    bad = D.groupby("game_id").e3_team.apply(lambda x: x.isna().any())
    out.loc[out.game_id.map(bad).fillna(True).values.astype(bool), "e3"] = np.nan
    # ---- F42: the frozen #42 spec, constants refitted per season on S-1 (fa_lib.build_world), no shift
    spec = tuple(json.loads((R.GRID_DIR / "winner_frozen.json").read_text())["spec"])
    f42 = {}
    for s in ("2022-23", "2023-24", "2024-25", "2025-26"):
        w = fa.build_world(s)
        g = fa.games_frame(w)
        p = gl.project_one(w.season, g, w.consts, spec)
        f42.update(dict(zip(g.game_id.astype("int64").values, np.asarray(p, float))))
    out["f42"] = out.game_id.map(f42).astype(float)
    return out, D


def kalshi_table():
    """Kalshi 2025-26 regular season at T-1h (#42 machinery, unchanged), keyed by hoopR game_id."""
    import grid_bets as gb
    import grid_run as gr
    games, strikes, _ = gr.v2.load_games()
    kg, ks = gr.v2.attach_outcomes(games, strikes, ["discovery", "unseen"])
    kg = kg[(kg.season == "2025-26") & (kg.season_type == 2)].reset_index(drop=True)
    ks = ks[ks.event.isin(kg.event)]
    bt = gb.main_strike_table(kg, ks)
    return pd.DataFrame({"game_id": kg.hoopr_id.astype("int64").values, "k_event": kg.event.values, "k_median": kg["median"].values.astype(float),
                         "k_K": bt.K.values.astype(float), "k_valid": bt.valid.values.astype(bool),
                         "k_has_strike": bt.has_strike.values.astype(bool),
                         "k_paid_over": bt.paid_over.values.astype(float), "k_paid_under": bt.paid_under.values.astype(float),
                         "k_fee_over": bt.fee_over.values.astype(float), "k_fee_under": bt.fee_under.values.astype(float),
                         "k_win_over": bt.win_over.values.astype(float), "k_win_under": bt.win_under.values.astype(float),
                         "k_net_over": bt.net_over.values.astype(float), "k_net_under": bt.net_under.values.astype(float),
                         "k_actual": kg.actual.values.astype(float)})


def main():
    t0 = time.time()
    R.MAIN_OUT.mkdir(parents=True, exist_ok=True)
    games, tgs = load_games_frames()
    print("games:", games.groupby("season").size().to_dict(), flush=True)
    mt, _ = model_tables(games)
    close = R.load_closing(regular_ids=games.game_id.values)
    preds = games.merge(mt, on="game_id", how="left", validate="one_to_one")
    preds["close_line"] = preds.game_id.map(close).astype(float)
    kt = kalshi_table()
    preds = preds.merge(kt, on="game_id", how="left", validate="one_to_one")
    for c in ("k_valid", "k_has_strike"):
        preds[c] = preds[c].fillna(False).astype(bool)
    # ---- lean tables
    leans = []
    for model, col in R.MODEL_COL.items():
        sub = preds[["season", "et_date", "actual_total", col]]
        sub = sub[sub.groupby("season")[col].transform(lambda x: x.notna().any())]
        leans.append(R.lean_table(sub, col, model))
    lean = pd.concat(leans, ignore_index=True)
    for model, col in R.MODEL_COL.items():
        l = lean[lean.model == model][["season", "et_date", "b", "n_games_before"]].rename(columns={"b": "b_" + col, "n_games_before": "n_lean_" + col})
        preds = preds.merge(l, on=["season", "et_date"], how="left")
        preds["b_" + col] = preds["b_" + col].fillna(0.0)
    preds = preds.sort_values(["season", "et_date", "tip", "game_id"], kind="stable").reset_index(drop=True)
    # ---- flags
    feat = pd.read_parquet(R.E3_STUDY / "e3_features.parquet", columns=["game_id", "team_k", "n_q_rot_c60", "new_share_own_c60"])
    flags = R.compute_flags(feat, tgs, preds[["game_id", "home", "away"]])
    # ---- Part B absentees
    box = R.E2.load_box(range(2021, 2027))
    reports = R.E2.Reports()
    bfeat = R.build_part_b(preds[["game_id", "season", "et_date", "tip", "away", "home"]].rename(columns={"et_date": "date"}),
                           box.df, reports)
    # ---- write
    preds.to_parquet(R.MAIN_OUT / "preds.parquet", index=False)
    flags.to_parquet(R.MAIN_OUT / "flags.parquet", index=False)
    bfeat.to_parquet(R.MAIN_OUT / "b_features.parquet", index=False)
    lean.to_parquet(R.MAIN_OUT / "lean.parquet", index=False)
    # ---- counts and coverage (no outcomes)
    rows = {}
    for s, g in preds.groupby("season"):
        b = bfeat[bfeat.season == s]
        rows[s] = {"games": len(g), "f42": int(g.f42.notna().sum()), "e1": int(g.e1.notna().sum()), "e3": int(g.e3.notna().sum()),
                   "close": int(g.close_line.notna().sum()), "kalshi_valid": int(g.k_valid.sum()),
                   "report_found_games": int(b.groupby("game_id").report_found.all().sum()),
                   "box_found_games": int(b.groupby("game_id").box_found.all().sum()),
                   "flags_defined": int(flags[flags.game_id.isin(g.game_id)].flags_defined.sum())}
    cov = pd.DataFrame(rows).T
    print(cov.to_string())
    print("table rows:", {"preds": len(preds), "flags": len(flags), "b_features": len(bfeat), "lean": len(lean)})
    print("runtime_s", round(time.time() - t0, 1))
    cov.to_csv(R.MAIN_OUT / "coverage.csv")
    # discovery rows for the agreement sample versions (discovery data only)
    ev = R.load_tables(seasons=R.D_B)
    vs = pd.read_csv(R.STUDY / "sample_versions.csv").version.tolist() + pd.read_csv(R.STUDY / "sample_versions_B.csv").version.tolist()
    pd.DataFrame([R.discovery_row(ev, v) for v in vs]).to_csv(R.MAIN_OUT / "versions_sample_main.csv", index=False)


if __name__ == "__main__":
    main()
