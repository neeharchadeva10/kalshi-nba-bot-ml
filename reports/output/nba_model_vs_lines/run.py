"""Research log #60 runner (ONE SHOT).  Implements PREREGISTRATION.md exactly.

REFUSES if results.json or started.json exists.  Writes started.json BEFORE any outcome-based number is computed.
No network; no Kalshi order/portfolio endpoints; reads cached data only.
Run:  .venv/Scripts/python.exe reports/output/nba_model_vs_lines/run.py"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
SM_MAIN = OUT / "nba_score_model" / "main"
FA_DIR = OUT / "nba_formula_accuracy"
GRID_DIR = OUT / "nba_totals_grid"
sys.path.insert(0, str(HERE))
import mvl_lib as L  # noqa: E402

REPRO_TOL = 1e-9
VAL_SEASONS = ("2020-21", "2021-22")          # validation seasons used for w_fit (prereg: 2020-21 + 2021-22)


def refuse_if_started():
    for f in ("results.json", "started.json"):
        if (HERE / f).exists():
            raise SystemExit(f"REFUSED: {f} exists; this study runs once.")


def e1_game_totals(frozen, seasons, S, M, rs, rt):
    """E1 predicted total per game (two team rows summed) with the actual total and season.  DataFrame."""
    D = rs.load_stage(list(seasons))
    pred = np.full(len(D), np.nan)
    for s in seasons:
        m = (D.season == s).values
        pred[m] = rt.season_predictions(frozen, D[m], s, ["E1"])["E1"]
    assert np.isfinite(pred).all()
    met = M.game_metrics(D.game_id.values, D.home.values, D[S.TARGET].values, pred)
    season = pd.Series(D.season.values, index=D.game_id.values)
    season = season[~season.index.duplicated()].reindex(met["game_ids"]).values
    return pd.DataFrame({"game_id": met["game_ids"].astype(np.int64), "season": season, "e1": met["tot_pred"], "actual": met["tot_act"]})


def main():
    refuse_if_started()
    sys.path.insert(0, str(SM_MAIN))                 # first, so its run_select / models win the module names
    import sm_lib as S                               # noqa: E402
    import models as M                               # noqa: E402
    import run_select as rs                          # noqa: E402
    import run_test as rt                            # noqa: E402
    frozen = S.check_frozen_hash(S.STUDY)            # frozen.json SHA-256 must match frozen.sha256
    (HERE / "started.json").write_text(json.dumps({"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}))
    ref = json.loads((S.STUDY / "results_test.json").read_text())["blocks"]["E1"]["pooled_test"]["total_mae"]

    # ---- E1 predictions: test seasons (must reproduce #59) and validation seasons (part C's w_fit only)
    test = e1_game_totals(frozen, S.TEST, S, M, rs, rt)
    mae_test = float(np.abs(test.e1 - test.actual).mean())
    repro = {"recomputed_pooled_test_total_mae": mae_test, "results_test_json": ref, "abs_diff": abs(mae_test - ref),
             "ok": L.repro_ok(mae_test, ref, REPRO_TOL), "n_games": int(len(test))}
    print("reproduction:", repro, flush=True)
    if not repro["ok"]:
        (HERE / "repro_failed.json").write_text(json.dumps(repro, indent=1))
        raise SystemExit("STOP: E1 predictions do not reproduce #59's pooled test MAE; see repro_failed.json")
    val = e1_game_totals(frozen, VAL_SEASONS, S, M, rs, rt)

    # ---- sportsbook close
    sys.path.insert(0, str(FA_DIR))
    import fa_lib as fa                              # noqa: E402
    close = fa.load_closing()
    test["close"] = test.game_id.map(close).astype(float)
    val["close"] = val.game_id.map(close).astype(float)
    tc = test[np.isfinite(test.close)].reset_index(drop=True)
    vc = val[np.isfinite(val.close)].reset_index(drop=True)

    res = {"repro": repro, "n_test_games": int(len(test)), "n_test_with_close": int(len(tc)), "n_val_games": int(len(val)),
           "n_val_with_close": int(len(vc)), "pushes_note": "pushes (actual == line) excluded from side rates"}

    # ---- A
    A = {}
    for g in L.GAPS:
        blk = {"pooled": L.side_table(tc.e1, tc.close, tc.actual, g), "by_season": {}}
        for s in sorted(tc.season.unique()):
            m = (tc.season == s).values
            blk["by_season"][s] = L.side_table(tc.e1[m], tc.close[m], tc.actual[m], g)
        A[f"g{g}"] = blk
    res["A"] = A

    # ---- C
    w_fit, val_maes = L.fit_w(vc.e1.values, vc.close.values, vc.actual.values)
    C = {"w_fit": w_fit, "validation_mae_by_w": {str(k): v for k, v in val_maes.items()}, "versions": {}}
    for name, w in [("w0.1", 0.1), ("w0.2", 0.2), ("w0.3", 0.3), ("w_fit", w_fit)]:
        blk = {"pooled": L.blend_vs_close(tc.e1.values, tc.close.values, tc.actual.values, w), "by_season": {}}
        for s in sorted(tc.season.unique()):
            m = (tc.season == s).values
            blk["by_season"][s] = L.blend_vs_close(tc.e1.values[m], tc.close.values[m], tc.actual.values[m], w)
        C["versions"][name] = blk
    res["C"] = C

    # ---- Kalshi 2025-26 (as #56 kalshi_stage)
    sys.path.insert(0, str(GRID_DIR))
    import grid_bets as gb                           # noqa: E402
    import grid_run as gr                            # noqa: E402
    games, strikes, info = gr.v2.load_games()
    kg, ks = gr.v2.attach_outcomes(games, strikes, ["discovery", "unseen"])
    kg = kg[(kg.season == "2025-26") & (kg.season_type == 2)].reset_index(drop=True)
    ks = ks[ks.event.isin(kg.event)]
    bt = gb.main_strike_table(kg, ks)
    hid = kg.hoopr_id.astype(int).values
    e1_map = test.set_index("game_id").e1
    e1_k = pd.Series(hid).map(e1_map).values.astype(float)
    med = kg["median"].values.astype(float)
    close_k = pd.Series(hid).map(close).values.astype(float)
    actual_k = kg.actual.values.astype(float)
    both = np.isfinite(med) & np.isfinite(close_k)
    K = {"n_games": int(len(kg)), "with_main_strike": int(bt.has_strike.sum()), "valid_settled": int(bt.valid.sum()),
         "with_e1": int(np.isfinite(e1_k).sum()), "with_median": int(np.isfinite(med).sum()),
         "median_vs_close": {"n": int(both.sum()),
                             "median_abs_diff": float(np.median(np.abs(med[both] - close_k[both]))) if both.any() else None,
                             "mean_abs_diff": float(np.mean(np.abs(med[both] - close_k[both]))) if both.any() else None,
                             "mean_signed_median_minus_close": float(np.mean(med[both] - close_k[both])) if both.any() else None,
                             "share_within_1": float(np.mean(np.abs(med[both] - close_k[both]) <= 1.0)) if both.any() else None}}
    ok_act = np.isfinite(actual_k) & np.isfinite(e1_k)
    K["actual_total_agrees_with_hoopR_E1_actual"] = int(np.sum(np.abs(actual_k[ok_act] - pd.Series(hid).map(test.set_index("game_id").actual).values[ok_act]) < 1e-9))
    K["n_actual_compared"] = int(ok_act.sum())
    res["kalshi_2025_26"] = K
    B = {}
    bet_cols = {}
    for g in L.GAPS:
        bets = L.kalshi_bets(e1_k, med, bt, g)
        B[f"g{g}"] = L.b_summary(bets, bt, g)
        flag = np.zeros(len(kg), bool)
        flag[bets["idx"]] = True
        net = np.full(len(kg), np.nan)
        net[bets["idx"]] = bets["net"]
        bet_cols[f"bet_g{g}"] = flag
        bet_cols[f"net_g{g}"] = net
    res["B"] = B

    # ---- overall
    res["overall"] = L.overall(A["g4"]["pooled"]["pass"], B["g4"]["pass"], C["versions"]["w0.2"]["pooled"]["pass"],
                               C["versions"]["w_fit"]["pooled"]["pass"])
    res["versions_total"] = 10

    # ---- records.csv (per test game, with Kalshi fields where present)
    rec = test.copy()
    rec["gap_vs_close"] = rec.e1 - rec.close
    rec["side_vs_close"] = np.where(rec.gap_vs_close > 0, "over", np.where(rec.gap_vs_close < 0, "under", ""))
    kdf = pd.DataFrame({"game_id": hid, "kalshi_median": med, "kalshi_K": bt.K.values, "kalshi_valid": bt.valid.values, **bet_cols})
    rec = rec.merge(kdf, on="game_id", how="left")
    rec.to_csv(HERE / "records.csv", index=False)

    (HERE / "results.json").write_text(json.dumps(res, indent=1, default=str))
    (HERE / "RESULTS.md").write_text(render(res))
    print(render(res))


def f(x, p=4):
    return "nan" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{p}f}"


def render(r):
    o = ["# Research #60 results (auto-generated by run.py)", "",
         f"Reproduction of #59 pooled test MAE: {r['repro']['recomputed_pooled_test_total_mae']:.12f} vs {r['repro']['results_test_json']:.12f} "
         f"(ok={r['repro']['ok']}).  Test games {r['n_test_games']}, with close {r['n_test_with_close']}; validation games "
         f"{r['n_val_games']}, with close {r['n_val_with_close']}.", "", "## A. Side rate vs the close (pass: LB > 52.38%, n >= 150)",
         "| g | scope | n | wins | rate | Wilson LB | pushes | pass |", "|---|---|---|---|---|---|---|---|"]
    for k, b in r["A"].items():
        for scope, t in [("pooled", b["pooled"])] + list(b["by_season"].items()):
            o.append(f"| {t['g']} | {scope} | {t['n']} | {t['wins']} | {f(t['rate'])} | {f(t['lb'])} | {t['pushes']} | {t['pass'] if scope == 'pooled' else ''} |")
    o += ["", "## B. Kalshi 2025-26 bets (pass: mean>0, boot LB>0, n>=150, coin flip <= 5%)",
          "| g | n | win% | avg paid | mean net | boot CI95 | coin-flip share | pass |", "|---|---|---|---|---|---|---|---|"]
    for k, b in r["B"].items():
        if b["n"] < 2:
            o.append(f"| {b['g']} | {b['n']} | | | | | | False |")
            continue
        o.append(f"| {b['g']} | {b['n']} | {f(b['win_rate'])} | {f(b['avg_paid'])} | {f(b['mean_net'])} | "
                 f"[{f(b['boot_ci95'][0])}, {f(b['boot_ci95'][1])}] | {f(b['coin_flip']['frac_copies_ge_actual'])} | {b['pass']} |")
    km = r["kalshi_2025_26"]
    o += ["", f"Kalshi games {km['n_games']}, with main strike {km['with_main_strike']}, valid settled {km['valid_settled']}, with E1 {km['with_e1']}.",
          f"Kalshi T-1h median vs close: {km['median_vs_close']}", "",
          f"## C. Blend MAE vs close (pass: paired CI of blend-close entirely below 0).  w_fit = {r['C']['w_fit']}",
          "| version | w | scope | n | blend MAE | close MAE | diff | CI95 | pass |", "|---|---|---|---|---|---|---|---|---|"]
    for name, b in r["C"]["versions"].items():
        for scope, t in [("pooled", b["pooled"])] + list(b["by_season"].items()):
            o.append(f"| {name} | {t['w']} | {scope} | {t['n']} | {f(t['blend_mae'])} | {f(t['close_mae'])} | {f(t['diff'])} | "
                     f"[{f(t['ci95'][0])}, {f(t['ci95'][1])}] | {t['pass'] if scope == 'pooled' else ''} |")
    o += ["", "## Overall", json.dumps(r["overall"])]
    return "\n".join(o) + "\n"


if __name__ == "__main__":
    main()
