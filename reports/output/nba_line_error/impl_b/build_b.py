"""Build impl_b outputs: features_b.parquet, excluded_b.csv, counts_b.csv, agreement_b.csv, points_mismatch_b.csv,
poss_check_b.csv, team_games_b.parquet.  No analysis of y."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import lib_b as L

OUT = L.OUT


def main():
    tg = L.load_hoopr()
    g_all = L.build_games(tg)
    g_reg = L.regular(g_all)
    seasons = sorted(g_reg.season.unique())
    print("regular-season games per season:", g_reg.groupby("season").size().to_dict())

    o = L.match_oddsdata(L.load_oddsdata(), g_all)
    close_raw = pd.read_parquet(L.CLOSE_PQ)
    clean, excl, info = L.clean_lines(g_reg, g_all, o, close_raw)
    # y and the game columns
    gcols = g_reg[["game_id", "season", "date", "away", "home", "away_id", "home_id", "actual"]]
    base = clean.merge(gcols, on="game_id", how="left")
    base["y"] = base["actual"] - base["line"]
    feats = L.compute_features(g_reg, clean)
    base = base.merge(feats, on="game_id", how="left")

    # f8
    sg = {s: g_reg[g_reg.season == s].reset_index(drop=True) for s in seasons}
    stg = {s: L.team_game_table(sg[s]) for s in seasons}
    f8 = {}
    bs = {}
    for i, S in enumerate(seasons):
        if i == 0:
            continue
        S1 = seasons[i - 1]
        S2 = seasons[i - 2] if i >= 2 else None
        for mode in ("own", "termown"):
            shr, proj, b = L.f8_for_season(sg, stg, S, S1, S2, mode)
            for gid, v, p in zip(sg[S].game_id, shr, proj):
                f8[(mode, gid)] = (v, p)
            bs[(S, mode)] = b
    base["f8_proj"] = [f8.get(("own", g), (np.nan, np.nan))[0] for g in base.game_id]
    base["f8"] = base["f8_proj"] - base["line"]
    base["f8_unshrunk_proj"] = [f8.get(("own", g), (np.nan, np.nan))[1] for g in base.game_id]
    base["f8_termown"] = [f8.get(("termown", g), (np.nan, np.nan))[0] for g in base.game_id] - base["line"]
    base["f8_b"] = [bs.get((s, "own"), np.nan) for s in base.season]

    base["date"] = base["date"].dt.strftime("%Y-%m-%d")
    cols = ["game_id", "date", "season", "away", "home", "line", "line_source", "spread", "actual", "y",
            "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10",
            "f5_alt", "f9_alt", "f8_termown", "f8_proj", "f8_unshrunk_proj", "f8_b", "n_games_before"]
    base = base[base.season >= "2008-09"].sort_values(["date", "game_id"])[cols].reset_index(drop=True)
    base.to_parquet(OUT / "features_b.parquet", index=False)

    ex = pd.DataFrame(excl)
    ex.to_csv(OUT / "excluded_b.csv", index=False)
    info["points_mismatch"].to_csv(OUT / "points_mismatch_b.csv", index=False)

    # agreement closing vs OddsData
    ag = info["agreement"]
    if len(ag):
        ag["d_ou"] = (ag["ou"] - ag["close"])
        rows = []
        for s, grp in ag.groupby("season"):
            rec = dict(season=s, n=len(grp), exact_ou=float((grp.d_ou == 0).mean()), within_1=float((grp.d_ou.abs() <= 1).mean()),
                       mean_abs=float(grp.d_ou.abs().mean()), max_abs=float(grp.d_ou.abs().max()), mean_signed=float(grp.d_ou.mean()), median_signed=float(grp.d_ou.median()), n_abs_gt10=int((grp.d_ou.abs() > 10).sum()), spread_exact=float(((grp.sp_odds - grp.sp_close).abs() < 1e-9).mean()), spread_mean_abs=float((grp.sp_odds - grp.sp_close).abs().mean()))
            for c_ in ("x", "xn"):
                m = grp[c_].notna()
                rec[f"{c_}_n"] = int(m.sum())
                rec[f"{c_}_mean_abs_vs_close"] = float((grp[c_][m] - grp.close[m]).abs().mean()) if m.any() else np.nan
            rows.append(rec)
        pd.DataFrame(rows).to_csv(OUT / "agreement_b.csv", index=False)

    # per-season counts
    cnt = []
    for s in sorted(set(seasons) | set(o["season_tab"])):
        o_s = o["game_id"].map(g_all.set_index("game_id")["season"]).fillna(o["season_tab"])
        rec = dict(season=s, hoopr_regular_games=int((g_reg.season == s).sum()))
        rec["oddsdata_rows_in"] = int((o_s == s).sum())
        rec["closing_rows_in"] = int(close_raw["game_id"].map(g_all.set_index("game_id")["season"]).eq(s).sum())
        if len(ex):
            for reason, n in ex[ex.season == s].reason.value_counts().items():
                rec[f"excl_{reason}"] = int(n)
        rec["clean_out"] = int((base.season == s).sum())
        cnt.append(rec)
    cnt = pd.DataFrame(cnt).fillna(0)
    cnt.to_csv(OUT / "counts_b.csv", index=False)

    # possession / pace check per team-season, regular season
    r = tg[tg.season_type == 2].copy()
    gp = g_reg.set_index("game_id")["pace"]
    r["pace_game"] = r["game_id"].map(gp)
    rows = []
    for (s, t), grp in r.groupby(["season", "team_id"]):
        n = len(grp)
        ppg = grp.pts.mean()
        ortg_own = 100 * grp.pts.sum() / grp.poss_own.sum()
        pace_own = grp.poss_own.mean()
        ortg_game = 100 * grp.pts.sum() / grp.pace_game.sum()
        pace_game = grp.pace_game.mean()
        rows.append(dict(season=s, team_id=t, abbr=grp.abbr.iloc[-1], games=n, ppg=ppg, ortg_own=ortg_own, pace_own=pace_own,
                         ppg_from_own=ortg_own * pace_own / 100, ortg_game=ortg_game, pace_game=pace_game,
                         ppg_from_game=ortg_game * pace_game / 100))
    pc = pd.DataFrame(rows)
    pc["abs_diff_own"] = (pc.ppg - pc.ppg_from_own).abs()
    pc["abs_diff_game"] = (pc.ppg - pc.ppg_from_game).abs()
    pc.to_csv(OUT / "poss_check_b.csv", index=False)
    tg.to_parquet(OUT / "team_games_b.parquet", index=False)

    print(cnt.to_string())
    print("clean rows:", len(base), "excluded rows:", len(ex))
    print("poss check max abs diff (own, game):", pc.abs_diff_own.max(), pc.abs_diff_game.max())
    print("b by season:", {k[0]: round(v, 4) for k, v in bs.items() if k[1] == "own"})
    print("NaN counts:\n", base[["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10"]].isna().sum().to_string())


if __name__ == "__main__":
    main()
