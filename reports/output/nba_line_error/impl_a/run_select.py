"""Research log #57, implementation A: SELECTION on the discovery seasons only (2008-09 .. 2018-19).

REFUSES unless AGREEMENT.md says STATUS: PASS (for the current features_a.parquet).  Runs once.
Order: (1) read the discovery rows WITHOUT y, (2) only then read y for exactly those rows, (3) fit/evaluate 23 versions,
(4) pick the highest Wilson 95% LB among versions with n >= 300; if that LB does not exceed 110/210 -> STOP (no frozen.json).
Writes results_select.json, and (if not STOP) frozen.json + frozen.sha256 (SHA-256 of frozen.json, printed)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lea_lib as L  # noqa: E402

FEATURES = L.STUDY / "features_a.parquet"
READ_LOG: list = []        # (columns, seasons) of every parquet read - tests check that y is only read for requested seasons


def read_features(seasons, columns, path=None):
    """Parquet column projection with a season filter: rows of other seasons are never materialised."""
    cols = list(columns)
    READ_LOG.append((tuple(cols), tuple(seasons)))
    t = pq.read_table(path or FEATURES, columns=cols, filters=[("season", "in", list(seasons))])
    return t.to_pandas()


def load_stage(seasons, path=None):
    """Features first (no y / actual), then y for the same seasons, merged on game_id."""
    X = read_features(seasons, ["game_id", "season", "date", "line_source"] + L.FEATS, path)
    yv = read_features(seasons, ["game_id", "season", "y"], path)[["game_id", "y"]]
    D = X.merge(yv, on="game_id", how="left", validate="one_to_one")
    assert D.y.notna().all()
    return D.sort_values(["date", "game_id"], kind="stable").reset_index(drop=True)


def evaluate_versions(D: pd.DataFrame) -> dict:
    """D: discovery rows with f1..f10, y, season.  Returns the full selection record (pure; unit-tested)."""
    y = D.y.values.astype(float)
    out = {"versions": {}, "deciles": {}, "cuts": {}, "notes": []}
    # ---- M
    X = D[L.FEATS].values.astype(float)
    ok = np.isfinite(X).all(axis=1)
    out["m_complete_cases"] = int(ok.sum())
    lam, cv = L.cv_lambda(X[ok], y[ok], D.season.values[ok])
    mu, sd = L.standardise_fit(X[ok])
    b0, b = L.ridge_fit((X[ok] - mu) / sd, y[ok], lam)
    pred = np.full(len(D), np.nan)                 # amendment: discovery statistics use leave-one-season-out (out-of-fold) predictions
    pred[ok] = L.loso_predict(X[ok], y[ok], D.season.values[ok], lam)
    pred_in = np.full(len(D), np.nan)
    pred_in[ok] = L.ridge_predict(X[ok], mu, sd, b0, b)
    out["ridge"] = {"lambda": lam, "cv_mse": {str(k): v for k, v in cv.items()}, "b0": b0, "coef": b.tolist(), "mu": mu.tolist(),
                    "sd": sd.tolist(), "pred_sd_oof": float(np.nanstd(pred)), "pred_sd_in_sample": float(np.nanstd(pred_in)),
                    "oof_corr_with_y": float(np.corrcoef(pred[ok], y[ok])[0, 1])}
    for name, tau in L.M_VERSIONS:
        side = np.zeros(len(D))
        side[ok & (pred >= tau)] = 1.0
        side[ok & (pred <= -tau)] = -1.0
        st = L.side_stats(side, y)
        out["versions"][name] = {**st, "kind": "M", "tau": tau, "rule": {"kind": "M", "tau": tau, "lambda": lam, "b0": b0, "coef": b.tolist(),
                                                                         "mu": mu.tolist(), "sd": sd.tolist()}}
    # ---- U
    for f in L.FEATS:
        x = D[f].values.astype(float)
        fin = np.isfinite(x)
        cuts = L.decile_cuts(x[fin])
        q10, q90 = float(cuts[0]), float(cuts[-1])
        out["cuts"][f] = {"q10": q10, "q90": q90, "n": int(fin.sum()), "all_cuts": [float(c) for c in cuts]}
        out["deciles"][f] = L.decile_table(x, y)
        disc = L.is_discrete(x)
        urule = {"discrete": bool(disc), "q10": q10, "q90": q90, "xmin": float(np.nanmin(x)), "xmax": float(np.nanmax(x))}
        out["cuts"][f]["discrete"] = bool(disc)
        bot, top = L.tail_masks_rule(x, urule)
        for tail, m in (("bottom", bot), ("top", top)):
            my = float(y[m].mean()) if m.any() else float("nan")
            sign = 0.0 if (not np.isfinite(my) or my == 0) else float(np.sign(my))
            side = np.where(m, sign, 0.0)
            st = L.side_stats(side, y)
            out["versions"][f"U_{f}_{tail}"] = {**st, "kind": "U", "feature": f, "tail": tail, "tail_n_rows": int(m.sum()),
                                                "tail_share": float(m.sum() / max(fin.sum(), 1)), "tail_mean_y": my,
                                                "rule": {"kind": "U", "feature": f, "tail": tail, "sign": sign, **urule}}
    # ---- choose
    order = L.all_version_names()
    assert set(order) == set(out["versions"]), "version list mismatch"
    elig = [n for n in order if out["versions"][n]["n"] >= L.MIN_N_SELECT and np.isfinite(out["versions"][n]["lb"])]
    out["eligible"] = elig
    if not elig:
        out.update(stop=True, stop_reason="no version has n >= 300", selected=None)
        return out
    best = max(elig, key=lambda n: (out["versions"][n]["lb"], -order.index(n)))
    out["best_lb_version"] = best
    out["best_lb"] = out["versions"][best]["lb"]
    if not out["best_lb"] > L.BREAK_EVEN:
        out.update(stop=True, stop_reason=f"no version's discovery LB exceeds {L.BREAK_EVEN:.4f}: no line error found", selected=None)
        return out
    out.update(stop=False, stop_reason=None, selected=best)
    sel = out["versions"][best]
    if sel["kind"] == "M":
        side = np.zeros(len(D))
        side[ok & (pred >= sel["tau"])] = 1.0
        side[ok & (pred <= -sel["tau"])] = -1.0
    else:
        r = sel["rule"]
        bot, top = L.tail_masks_rule(D[r["feature"]].values.astype(float), r)
        side = np.where(bot if r["tail"] == "bottom" else top, r["sign"], 0.0)
    out["per_season_selected"] = {s: L.side_stats(side[D.season.values == s], y[D.season.values == s]) for s in sorted(set(D.season))}
    return out


def main():
    study = L.STUDY
    L.check_agreement_pass(study)
    for f in ("frozen.json", "frozen.sha256", "results_select.json", "results_validate.json", "AUDIT_OK", "test_started.json"):
        if (study / f).exists():
            raise SystemExit(f"REFUSED: {f} exists; selection runs once.")
    D = load_stage(L.DISCOVERY)
    assert set(D.season) <= set(L.DISCOVERY)
    res = evaluate_versions(D)
    res["discovery_rows"] = int(len(D))
    res["seasons"] = list(L.DISCOVERY)
    (study / "results_select.json").write_text(json.dumps(res, indent=1, default=str))
    tab = pd.DataFrame([{k: v[k] for k in ("n", "wins", "rate", "lb", "pushes")} | {"version": n} for n, v in res["versions"].items()])
    print(tab.to_string(index=False))
    if res["stop"]:
        print("STOP:", res["stop_reason"])
        return
    sel = res["versions"][res["selected"]]
    frozen = {"version": res["selected"], "rule": sel["rule"], "discovery": {k: sel[k] for k in ("n", "wins", "rate", "lb", "ub", "pushes")},
              "break_even": L.BREAK_EVEN, "split": {"discovery": list(L.DISCOVERY), "validation": list(L.VALIDATION), "test": list(L.TEST)}}
    blob = json.dumps(frozen, indent=1, sort_keys=True).encode()
    (study / "frozen.json").write_bytes(blob)
    h = L.sha256_bytes(blob)
    (study / "frozen.sha256").write_text(h + "  frozen.json\n")
    print("SELECTED:", res["selected"], "| frozen.json SHA-256:", h)


if __name__ == "__main__":
    main()
