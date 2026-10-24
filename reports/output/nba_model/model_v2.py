"""Research log #31 analysis (reports/output/nba_model/PREREGISTRATION_V2.md). Betting/fit/bootstrap/coin-flip/pass bar
are model.py (#29) unchanged; only the features differ (features_v2.py).

Run: .venv/Scripts/python.exe reports/output/nba_model/model_v2.py   -> results_v2.json + README_V2.md
Read-only: data/nba_drift.sqlite, data/nba_model.sqlite (v1 comparison), data/raw/nba_open, parsed injury reports.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import features as F  # noqa: E402
import features_v2 as F2  # noqa: E402
import model as M  # noqa: E402

drift = F.drift
SEED, THETAS = M.SEED, M.THETAS
FULL_COLS = ["elo_diff", "net_diff", "rest_diff", "b2b_diff", "inj_diff"]
ELO_COLS = ["elo_diff"]
PARSED = HERE / "injury_parsed.json"
MANIFEST = F2.ROOT / "data" / "raw" / "nba_injury" / "_manifest.json"


def build_name2code():
    import pandas as pd
    tb = pd.read_parquet(F2.ROOT / "data" / "raw" / "nba_open" / "team_box_2026.parquet")
    m = {}
    for r in tb[["team_display_name", "team_abbreviation"]].drop_duplicates().itertuples():
        c = F2.ESPN2KALSHI.get(r.team_abbreviation, r.team_abbreviation)
        if c in F2.KALSHI_CODES:
            m[r.team_display_name.replace(" ", "")] = c
    m["LAClippers"] = "LAC"
    m["LosAngelesClippers"] = "LAC"
    return m


def load_reports(games):
    man = json.loads(MANIFEST.read_text())["games"]
    parsed = json.loads(PARSED.read_text())
    reports = {}
    for g in games:
        e = man.get(g.gid)
        if e and e["file"] and e["file"] in parsed and "error" not in parsed[e["file"]]:
            p = parsed[e["file"]]
            reports[g.gid] = {"file": e["file"], "created_utc": e["created_utc"], "rows": p["rows"],
                              "nys": [tuple(x) for x in p["nys"]], "header": p["header"]}
    return reports, man, parsed


def v1_probs(games, is_d):
    """v1 full-model probabilities (features.py + model.py recipe) for all games, same discovery set."""
    pts, spread, expv = F.load_tables()
    rows, _ = F.build_features(games, pts, spread, expv)
    X = np.array([[r[c] for c in M.FULL_COLS] for r in rows], dtype=float)
    y = np.array([r["home_win"] for r in rows])
    mu, sd = X[is_d].mean(axis=0), X[is_d].std(axis=0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    w = M.fit_logit(Z[is_d], y[is_d])
    return M.predict(w, Z)


def main():
    games, excl, flags = drift.load_nba()
    games = sorted(games, key=lambda g: (g.tip, g.gid))
    n = len(games)
    k = drift.split_index(n)
    disc_ids = {g.gid for g in games[:k]}
    box = F2.load_box()
    name2code = build_name2code()
    reports, man, parsed = load_reports(games)
    rows, fstats = F2.build_features_v2(games, box, reports, name2code)
    assert [r["gid"] for r in rows] == [g.gid for g in games]
    quotes, qex = M.quotes_for(games)
    iso = lambda t: M.datetime.fromtimestamp(t, M.timezone.utc).isoformat()  # noqa: E731
    unseen_games = games[k:]
    unseen_weeks = len({M.datetime.fromtimestamp(g.tip, M.timezone.utc).isocalendar()[:2] for g in unseen_games})
    is_d = np.array([r["gid"] in disc_ids for r in rows])
    y = np.array([r["home_win"] for r in rows])
    res = {"n_games": n, "discovery_games": k, "unseen_games": n - k, "split_tip_utc": iso(games[k].tip),
           "first_tip_utc": iso(games[0].tip), "last_tip_utc": iso(games[-1].tip), "unseen_weeks": unseen_weeks,
           "loader_exclusions": dict(excl), "feature_stats": fstats, "quote_exclusions": qex,
           "games_with_valid_quotes": len(quotes), "versions": {}, "models": {}}
    cov = {"games": n, "report_file_found": int(sum(r["report_found"] for r in rows)),
           "game_listed_in_report": int(sum(r["covered"] for r in rows)),
           "one_team_not_yet_submitted": int(sum(1 for r in rows if r["covered"] and (r["nys_h"] + r["nys_a"]) == 1)),
           "both_teams_not_yet_submitted": int(sum(1 for r in rows if r["covered"] and (r["nys_h"] + r["nys_a"]) == 2)),
           "regular_out_or_doubtful_games": int(sum(r["key_missing"] for r in rows)),
           "games_with_nonzero_inj_diff": int(sum(1 for r in rows if r["inj_diff"] != 0.0))}
    cov["gamesNoReport_list"] = [r["gid"] for r in rows if not r["covered"]][:40]
    for part, mask in (("discovery", is_d), ("unseen", ~is_d)):
        cov[part + "_covered"] = int(sum(r["covered"] for r, m_ in zip(rows, mask) if m_))
        cov[part + "_games"] = int(mask.sum())
    res["report_coverage"] = cov
    files = {v["file"] for v in man.values() if v["file"]}
    res["injury_files"] = {"distinct_files_used": len(files),
                           "mb": sum((F2.ROOT / "data" / "raw" / "nba_injury" / f).stat().st_size for f in files) / 1e6,
                           "files_with_parse_problems": sum(1 for f in files if parsed.get(f, {}).get("problems")),
                           "parse_problem_items": sum(len(parsed[f]["problems"]) for f in files if f in parsed),
                           "files_with_error": sum(1 for f in files if "error" in parsed.get(f, {})),
                           "report_lag_min_mean": float(np.mean([(v["tip"] - v["created_utc"]) / 60 for v in man.values() if v["file"]])),
                           "report_lag_min_min": float(np.min([(v["tip"] - v["created_utc"]) / 60 for v in man.values() if v["file"]])),
                           "report_lag_min_max": float(np.max([(v["tip"] - v["created_utc"]) / 60 for v in man.values() if v["file"]]))}
    mkt_p = {gid: q[2] / (q[2] + q[3]) for gid, q in quotes.items()}
    preds = {}
    for name, cols in (("full", FULL_COLS), ("elo_only", ELO_COLS)):
        X = np.array([[r[c] for c in cols] for r in rows], dtype=float)
        mu, sd = X[is_d].mean(axis=0), X[is_d].std(axis=0)
        sd[sd == 0] = 1.0
        Z = (X - mu) / sd
        w = M.fit_logit(Z[is_d], y[is_d])
        preds[name] = M.predict(w, Z)
        res["models"][name] = {"cols": cols, "intercept_std": float(w[0]),
                               "weights_standardized": dict(zip(cols, map(float, w[1:]))),
                               "weights_per_raw_unit": dict(zip(cols, map(float, w[1:] / sd))),
                               "feature_mean_disc": dict(zip(cols, map(float, mu))),
                               "feature_sd_disc": dict(zip(cols, map(float, sd)))}
    p_v1 = v1_probs(games, is_d)
    qual = {}
    for part, mask in (("discovery", is_d), ("unseen", ~is_d)):
        idx = [i for i, r in enumerate(rows) if mask[i] and r["gid"] in quotes]
        yy = y[idx]
        pm = np.array([mkt_p[rows[i]["gid"]] for i in idx])
        d = {"n_games": len(idx), "market": {"brier": M.brier(pm, yy), "logloss": M.logloss(pm, yy)},
             "v1_full": {"brier": M.brier(p_v1[idx], yy), "logloss": M.logloss(p_v1[idx], yy)}}
        for name in preds:
            pp = preds[name][idx]
            d[name] = {"brier": M.brier(pp, yy), "logloss": M.logloss(pp, yy)}
            avg = (pp + pm) / 2
            d[name + "+market avg"] = {"brier": M.brier(avg, yy), "logloss": M.logloss(avg, yy)}
        d["calibration_full"] = M.calib(preds["full"][idx], yy)
        d["calibration_market"] = M.calib(pm, yy)
        ia = [i for i in range(len(rows)) if mask[i]]
        d["all_games_model_brier"] = {nm: M.brier(preds[nm][ia], y[ia]) for nm in preds}
        d["home_win_rate"] = float(y[ia].mean())
        qual[part] = d
    res["quality"] = qual
    rows_d = [r for r in rows if r["gid"] in disc_ids]
    rows_u = [r for r in rows if r["gid"] not in disc_ids]
    allbets = {}
    for name in ("full", "elo_only"):
        pd_, pu_ = preds[name][is_d], preds[name][~is_d]
        for th in THETAS:
            bd = M.make_bets(rows_d, pd_, quotes, th)
            bu = M.make_bets(rows_u, pu_, quotes, th)
            allbets[(name, th)] = (bd, bu)
            res["versions"][f"{name} theta={th * 100:.0f}c"] = M.summarize(bd, bu, unseen_weeks)
    pv = res["versions"]["full theta=4c"]
    rules = M.verdict(pv, qual["unseen"]["full"]["brier"], qual["unseen"]["market"]["brier"])
    res["primary"] = {"pass": all(x[1] for x in rules), "rules": [(a, bool(b), c) for a, b, c in rules]}
    bd2, bu2 = allbets[("full", 0.02)]
    by_edge = []
    for lo, hi in [(0.02, 0.04), (0.04, 0.06), (0.06, 0.10), (0.10, 9.0)]:
        d_ = [b["pnl"] for b in bd2 if lo - 1e-12 <= b["edge"] < hi - 1e-12]
        u_ = [b["pnl"] for b in bu2 if lo - 1e-12 <= b["edge"] < hi - 1e-12]
        by_edge.append({"edge_range": f"{lo:.2f}-{hi:.2f}", "n_disc": len(d_), "mean_disc": float(np.mean(d_)) if d_ else None,
                        "n_unseen": len(u_), "mean_unseen": float(np.mean(u_)) if u_ else None})
    res["bets_by_edge_full_theta2c"] = by_edge
    bd4, bu4 = allbets[("full", 0.04)]
    br = {}
    for lab, fn in (("regular Out/Doubtful (either team)", lambda b: b["key_missing"] == 1.0),
                    ("no regular Out/Doubtful", lambda b: b["key_missing"] == 0.0),
                    ("game listed in a qualifying report", lambda b: b["both_obs"] == 1.0)):
        d_ = [b["pnl"] for b in bd4 if fn(b)]
        u_ = [b["pnl"] for b in bu4 if fn(b)]
        br[lab] = {"n_disc": len(d_), "mean_disc": float(np.mean(d_)) if d_ else None, "n_unseen": len(u_),
                   "mean_unseen": float(np.mean(u_)) if u_ else None}
    res["bets_by_regular_out_full_theta4c"] = br
    res["sensitivity_primary_covered_only"] = M.summarize([b for b in bd4 if b["both_obs"] == 1.0],
                                                          [b for b in bu4 if b["both_obs"] == 1.0], unseen_weeks)
    km = {}
    for part, mask in (("discovery", is_d), ("unseen", ~is_d)):
        for lab, val in (("regular_out", 1.0), ("no_regular_out", 0.0)):
            idx = [i for i, r in enumerate(rows) if mask[i] and r["gid"] in quotes and r["key_missing"] == val]
            if idx:
                pm = np.array([mkt_p[rows[i]["gid"]] for i in idx])
                km[f"{part} {lab}"] = {"n": len(idx), "model_brier": M.brier(preds["full"][idx], y[idx]),
                                       "v1_brier": M.brier(p_v1[idx], y[idx]), "market_brier": M.brier(pm, y[idx])}
    res["quality_by_regular_out"] = km
    res["feature_summary"] = {c: {"mean": float(np.mean([r[c] for r in rows])), "sd": float(np.std([r[c] for r in rows]))}
                              for c in FULL_COLS + ["key_missing", "covered"]}
    (HERE / "results_v2.json").write_text(json.dumps(res, indent=1, default=str))
    write_readme(res)
    print(json.dumps({"primary": res["primary"], "coverage": {k_: v_ for k_, v_ in cov.items() if k_ != "gamesNoReport_list"},
                      "injury_files": res["injury_files"], "feature_stats": fstats,
                      "quality": {p: {k_: v_ for k_, v_ in qual[p].items() if k_ in ("n_games", "market", "v1_full", "full", "elo_only")} for p in qual}},
                     indent=1, default=str))
    print(M.table(res))


def write_readme(res):
    p, q, cov = res["primary"], res["quality"], res["report_coverage"]
    L = ["# Research log #31 results: NBA game-winner model v2 (official injury reports + open box scores)\n",
         f"**Primary verdict (full v2, theta = 4c): {'PASS' if p['pass'] else 'FAIL'}**\n"]
    L += [f"- {'PASS' if b else 'FAIL'}  rule {a}: {c}" for a, b, c in p["rules"]]
    L.append(f"\nGames {res['n_games']} (discovery {res['discovery_games']}, unseen {res['unseen_games']}), "
             f"{res['first_tip_utc']} to {res['last_tip_utc']}, split at {res['split_tip_utc']} (same split as #24 / #29). "
             f"Games with a usable T-1h quote on both teams: {res['games_with_valid_quotes']}. Unseen weeks with games: {res['unseen_weeks']}.\n")
    L.append("In plain English: dollars are for 10 contracts per bet. A bet is made only when the model's chance for a team, "
             "minus the ask and the fee, is at least theta. The pass bar needs every rule to hold on games the model never saw.\n")
    L.append("## All 6 versions\n")
    L.append(M.table(res))
    L.append("\nMean net is dollars per contract after the exact fee, held to settlement. $/wk = 10 contracts.\n")
    L.append("## v1 vs v2 vs the market (Brier and log loss; same games = valid T-1h quotes)\n")
    L.append("| set | n | market Brier | v1 full Brier | v2 full Brier | v2 Elo-exact-only Brier | v2 full+market avg | market log loss | v1 log loss | v2 full log loss |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for part in ("discovery", "unseen"):
        d = q[part]
        L.append(f"| {part} | {d['n_games']} | {d['market']['brier']:.4f} | {d['v1_full']['brier']:.4f} | {d['full']['brier']:.4f} | "
                 f"{d['elo_only']['brier']:.4f} | {d['full+market avg']['brier']:.4f} | {d['market']['logloss']:.4f} | "
                 f"{d['v1_full']['logloss']:.4f} | {d['full']['logloss']:.4f} |")
    L.append("\nDiscovery numbers for the models are in-sample; the market has no fitting.\n")
    L.append("## Feature weights (logistic regression on discovery)\n")
    for nm, m in res["models"].items():
        L.append(f"**{nm}**: intercept (home flag, standardized scale) {m['intercept_std']:+.3f}")
        L.append("| feature | weight per 1 sd | weight per raw unit | discovery sd |\n|---|---|---|---|")
        for c in m["cols"]:
            L.append(f"| {c} | {m['weights_standardized'][c]:+.3f} | {m['weights_per_raw_unit'][c]:+.4f} | {m['feature_sd_disc'][c]:.2f} |")
        L.append("")
    L.append("## Injury-report coverage\n")
    L.append(f"- Games: {cov['games']}; with a qualifying report file (published >= 60 min before tip): {cov['report_file_found']}; "
             f"game listed in that report: {cov['game_listed_in_report']} (discovery {cov['discovery_covered']}/{cov['discovery_games']}, "
             f"unseen {cov['unseen_covered']}/{cov['unseen_games']}).")
    L.append(f"- One team NOTYETSUBMITTED: {cov['one_team_not_yet_submitted']}; both: {cov['both_teams_not_yet_submitted']}. "
             f"Games with a regular Out/Doubtful: {cov['regular_out_or_doubtful_games']}. Games with non-zero inj_diff: {cov['games_with_nonzero_inj_diff']}.")
    L.append(f"- Files: {res['injury_files']}\n")
    L.append("## Calibration (unseen; 10 buckets)\n")
    L.append("| bucket | full n | full mean p | full win rate | market n | market mean p | market win rate |\n|---|---|---|---|---|---|---|")
    mk = {b["bucket"]: b for b in q["unseen"]["calibration_market"]}
    for b in q["unseen"]["calibration_full"]:
        m = mk.get(b["bucket"])
        L.append(f"| {b['bucket']} | {b['n']} | {b['mean_p']:.3f} | {b['win_rate']:.3f} | "
                 + (f"{m['n']} | {m['mean_p']:.3f} | {m['win_rate']:.3f} |" if m else "0 | | |"))
    L.append("\n## Bets by edge size (full v2, theta 2c bets)\n")
    L.append("| edge | disc n | disc mean | unseen n | unseen mean |\n|---|---|---|---|---|")
    for b in res["bets_by_edge_full_theta2c"]:
        L.append(f"| {b['edge_range']} | {b['n_disc']} | {M.fmt(b['mean_disc'])} | {b['n_unseen']} | {M.fmt(b['mean_unseen'])} |")
    L.append("\n## Bets where a regular is Out / Doubtful (full v2, theta 4c)\n")
    L.append("| group | disc n | disc mean | unseen n | unseen mean |\n|---|---|---|---|---|")
    for lab, b in res["bets_by_regular_out_full_theta4c"].items():
        L.append(f"| {lab} | {b['n_disc']} | {M.fmt(b['mean_disc'])} | {b['n_unseen']} | {M.fmt(b['mean_unseen'])} |")
    L.append("\nModel vs v1 vs market Brier by regular-Out games:\n")
    for kk, b in res["quality_by_regular_out"].items():
        L.append(f"- {kk}: n {b['n']}, v2 {b['model_brier']:.4f}, v1 {b['v1_brier']:.4f}, market {b['market_brier']:.4f}")
    s = res["sensitivity_primary_covered_only"]
    L.append(f"\nSensitivity, primary bets only in games listed in a qualifying report: disc n {s['n_disc']}, mean {M.fmt(s['disc_mean'])}; "
             f"unseen n {s['n_unseen']}, mean {M.fmt(s['unseen_mean'])}.\n")
    L.append(f"Feature stats / matching: {res['feature_stats']}. Quote exclusions: {res['quote_exclusions']}. Loader exclusions: {res['loader_exclusions']}.\n")
    L.append("## Interpretation choices\n")
    L += [f"- {s_}" for s_ in F2.INTERPRETATION_V2 + M.INTERP]
    (HERE / "README_V2.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
