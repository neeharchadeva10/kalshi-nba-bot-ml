"""Research log #32 analysis (reports/output/nba_model/PREREGISTRATION_V3.md). Implements the pre-registration as written.

Run: .venv/Scripts/python.exe reports/output/nba_model/model_v3.py   -> results_v3.json + README_V3.md
Read-only: hoopR box scores (data/raw/nba_open), OddsData.sqlite (PRIVATE RESEARCH USE ONLY, data/raw/nba_odds_samples),
parsed injury reports, data/nba_drift.sqlite (Kalshi 2025-26). Never imports kpm.execution.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import data_v3 as D  # noqa: E402
import features as F  # noqa: E402
import features_v2 as F2  # noqa: E402
import features_v3 as F3  # noqa: E402
import model as M  # noqa: E402

drift = F.drift
SEED, THETAS, SIZE = M.SEED, M.THETAS, M.SIZE
FULL_COLS = ["elo_diff", "net_diff", "rest_diff", "b2b_diff", "inj_diff"]
ELO_COLS = ["elo_diff"]
INJ_DIR = D.ROOT / "data" / "raw" / "nba_injury"
PARSED = HERE / "injury_parsed_v3.json"
TRAIN_A, TEST_A, TRAIN_B, TEST_B = (2022, 2023, 2024), 2025, (2022, 2023, 2024, 2025), 2026
INTERP_V3 = [
    "Fit (both models): logistic regression for P(home win), one row per game, intercept = home flag, numpy IRLS with ridge 1e-6 on the "
    "slopes (model.fit_logit), features standardized with TRAINING mean/sd only. Fitted on ALL box-score games of the training seasons "
    "(odds and Kalshi quotes are not needed to fit). Model A = 2021-22..2023-24; Model B = 2021-22..2024-25.",
    "Moneylines: odds_2021-22_new, odds_2022-23_new, 2023-24, 2024-25 tables. ML values like '+145' with non-breaking spaces are parsed "
    "to integers. Join to box games on (local date, home team); exact date first, then +-1 day; away team must agree. Odds rows that "
    "match no game, and box games with no odds, are listed in the results (unmatched).",
    "Implied probability of American odds p: 100/(p+100) if p > 0 else -p/(-p+100) (raw, vig included). Edge = model P(team) - implied "
    "P(team). Bet the side with the larger edge (home on a tie) if edge >= theta (1e-12 tolerance); at most one bet per game; $1 stake; "
    "profit = p/100 (p>0) or 100/(-p) (p<0) if the team wins, else -1.",
    "Bootstrap: 10,000 resamples of the 2024-25 bets (games), seed 20261021, percentile 2.5/97.5 (drift.bootstrap_ci). Coin flip: 500 "
    "copies, same bet games, random side (uniform) per game at that side's own American odds, seed 20261021 (fresh generator per "
    "version); share = fraction of copies with mean return >= the strategy's.",
    "$/week at $10 per bet = mean return per $1 x 10 x bets / weeks, weeks = ISO weeks (UTC tip time) with at least one 2024-25 "
    "odds-matched game (weeks with games). Kalshi confirmation: mean net per contract x 10 x bets / ISO weeks of the Kalshi 2025-26 game list.",
    "Rule 6 Brier is computed on the same 2024-25 odds-matched games for the model and for the de-vigged moneyline "
    "(home implied / (home implied + away implied)). 'Within 0.002 while rules 1-5 pass' = model - moneyline <= 0.002 AND rules 1-5 pass.",
    "Kalshi confirmation (Model B): games = drift.load_nba() (#24: clean results, T >= 2025-10-21) mapped to box games on (ticker date, "
    "home, away) with +-1 day tolerance; features come from the box-score row (full 5-season Elo with carry-over); quotes, edge "
    "= model p - YES ask - fee(ask, 10 contracts), hold to settlement, exactly as #31 (model.quotes_for / make_bets); theta equals the "
    "primary version's theta. Kalshi game result is used for settlement (box winner disagreements are counted).",
    "Return by favourite vs underdog: favourite = the bet side's American odds < 0 (underdog > 0; even money counts as underdog). "
    "Season types: 2 regular season, 3 playoffs, 5 play-in (shown separately).",
]


# ------------------------------------------------------------------ helpers
def build_name2code():
    m = {k.replace(" ", ""): v for k, v in D.NAME2CODE.items()}
    m["LosAngelesClippers"] = "LAC"
    return m


def load_reports(box):
    man = json.loads((INJ_DIR / "_manifest_v3.json").read_text())["games"]
    parsed = json.loads(PARSED.read_text())
    reports = {}
    for gid in box["games"]:
        e = man.get(gid)
        if e and e["file"] and e["file"] in parsed and "error" not in parsed[e["file"]]:
            p = parsed[e["file"]]
            reports[gid] = {"file": e["file"], "created_utc": e["created_utc"], "rows": p["rows"],
                            "nys": [tuple(x) for x in p["nys"]], "header": p["header"]}
    return reports, man, parsed


def ml_bets(rows, p_home, odds, theta):
    """One bet per game at American odds. rows aligned with p_home; odds {gid: (ml_h, ml_a)}."""
    out = []
    for r, ph in zip(rows, p_home):
        o = odds.get(r["gid"])
        if o is None:
            continue
        mh, ma = o
        eh, ea = ph - D.implied_prob(mh), (1 - ph) - D.implied_prob(ma)
        side = 0 if eh >= ea else 1
        e = eh if side == 0 else ea
        if e >= theta - 1e-12:
            hw = r["home_win"] == 1.0
            won = hw if side == 0 else not hw
            od = mh if side == 0 else ma
            out.append({"gid": r["gid"], "side": side, "edge": e, "odds": od, "fav": od < 0, "stype": r["stype"],
                        "pnl": D.profit_per_dollar(od, won),
                        "pnl_both": (D.profit_per_dollar(mh, hw), D.profit_per_dollar(ma, not hw)),
                        "covered": r["covered"]})
    return out


def weeks_of(tips):
    return len({datetime.fromtimestamp(t, timezone.utc).isocalendar()[:2] for t in tips})


def summarize(bets, weeks, key="pnl"):
    u = np.array([b[key] for b in bets], dtype=float)
    v = {"n": len(u), "weeks": weeks, "mean": float(u.mean()) if len(u) else None, "total": float(u.sum()) if len(u) else 0.0}
    if len(u):
        lo, hi = drift.bootstrap_ci(u, 10000, seed=SEED)
        v.update({"ci_lo": lo, "ci_hi": hi, "usd_per_week": float(u.mean() * SIZE * len(u) / weeks)})
        both = np.array([b["pnl_both"] for b in bets], dtype=float)
        rng = np.random.default_rng(SEED)
        pr = rng.integers(0, 2, size=(500, len(both)))
        cf = both[np.arange(len(both))[None, :], pr].mean(axis=1)
        v["coinflip_share_ge"] = float((cf >= u.mean() - 1e-15).mean())
        v["coinflip_mean"] = float(cf.mean())
    else:
        v.update({"ci_lo": float("nan"), "ci_hi": float("nan"), "usd_per_week": None, "coinflip_share_ge": None})
    return v


def mean_of(bets):
    return float(np.mean([b["pnl"] for b in bets])) if bets else None


def verdict(vA, vK, mod_brier, ml_brier):
    r = []
    ok1 = (vA["mean"] or -1) > 0 and (vK["mean"] or -1) > 0
    r.append(("1 mean return per $1 > 0 on 2024-25 AND mean net > 0 on the 2025-26 Kalshi confirmation", ok1,
              f"2024-25 {M.fmt(vA['mean'])}, Kalshi 2025-26 {M.fmt(vK['mean'])}"))
    r.append(("2 2024-25 95% bootstrap lower bound > 0", bool(vA["ci_lo"] > 0), f"CI [{M.fmt(vA['ci_lo'])}, {M.fmt(vA['ci_hi'])}]"))
    r.append(("3 at least 200 bets in 2024-25", vA["n"] >= 200, f"n {vA['n']}"))
    cf = vA.get("coinflip_share_ge")
    cf = 1.0 if cf is None else cf
    r.append(("4 coin flip: at most 5% of 500 random-side copies do as well", cf <= 0.05, f"share {cf:.3f}"))
    r.append(("5 at least $2/week at $10 per bet on 2024-25", (vA["usd_per_week"] or -1) >= 2, f"${(vA['usd_per_week'] or 0):.2f}/wk"))
    first5 = all(x[1] for x in r)
    diff = mod_brier - ml_brier
    ok6 = diff <= 0 or (diff <= 0.002 and first5)
    r.append(("6 2024-25 model Brier <= de-vigged moneyline Brier (or within 0.002 while rules 1-5 pass)", bool(ok6),
              f"model {mod_brier:.4f} vs moneyline {ml_brier:.4f}, diff {diff:+.4f}"))
    return r


def table_ml(res_versions):
    L = ["| version | in-sample bets (21-22..23-24) | in-sample mean | 2024-25 bets | mean return per $1 | 95% CI | coin-flip share | $/wk at $10 |",
         "|---|---|---|---|---|---|---|---|"]
    for nm, v in res_versions.items():
        t = v["test"]
        cf = f"{t['coinflip_share_ge']:.3f}" if t.get("coinflip_share_ge") is not None else "n/a"
        ci = f"[{M.fmt(t['ci_lo'])}, {M.fmt(t['ci_hi'])}]" if t["n"] else "n/a"
        L.append(f"| {nm} | {v['train']['n']} | {M.fmt(v['train']['mean'])} | {t['n']} | {M.fmt(t['mean'])} | {ci} | {cf} | {M.fmt(t['usd_per_week'], 2)} |")
    return "\n".join(L)


def table_kalshi(kv):
    L = ["| version | 2025-26 Kalshi bets | mean net per contract | 95% CI | coin-flip share | $/wk at 10 contracts |", "|---|---|---|---|---|---|"]
    for nm, t in kv.items():
        cf = f"{t['coinflip_share_ge']:.3f}" if t.get("coinflip_share_ge") is not None else "n/a"
        ci = f"[{M.fmt(t['ci_lo'])}, {M.fmt(t['ci_hi'])}]" if t["n"] else "n/a"
        L.append(f"| {nm} | {t['n']} | {M.fmt(t['mean'])} | {ci} | {cf} | {M.fmt(t['usd_per_week'], 2)} |")
    return "\n".join(L)


def qual_block(p, y):
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    return {"n": int(len(y)), "brier": M.brier(p, y), "logloss": M.logloss(p, y)}


# ------------------------------------------------------------------ main
def main():
    box = D.load_box_v3()
    odds_all = D.load_odds()
    name2code = build_name2code()
    reports, man, parsed = load_reports(box)
    rows, fstats = F3.build_features_v3(box, reports, name2code)
    rowmap = {r["gid"]: r for r in rows}
    seas = np.array([r["season"] for r in rows])
    y = np.array([r["home_win"] for r in rows])
    res = {"n_box_games": len(rows), "feature_stats": fstats, "versions": {}, "models": {}, "coverage": {}, "interpretations": INTERP_V3 + F3.INTERPRETATION_V3}
    # ---- odds join + validation
    odds, join_rep, join_sum, validation = {}, {}, {}, {}
    for yr in D.ODDS_TABLE:
        mt, rep, summ = D.join_odds(box, odds_all[yr], yr)
        join_sum[D.SEASON_LABEL[yr]] = summ
        join_rep[D.SEASON_LABEL[yr]] = {k: v[:60] for k, v in rep.items()}
        bad_w, bad_pts, n_chk = 0, 0, 0
        for gid, o in mt.items():
            odds[gid] = (o["ml_h"], o["ml_a"])
        # winner / total check against the odds file's own Win_Margin / Points columns
        import sqlite3
        con = sqlite3.connect(f"file:{D.ODDS_DB.as_posix()}?mode=ro", uri=True)
        tab = D.ODDS_TABLE[yr]
        wm = {(str(d)[:10], D.NAME2CODE.get(h)): (m, pts) for d, h, m, pts in con.execute(f'select Date, Home, Win_Margin, Points from "{tab}"')}
        con.close()
        for gid, o in mt.items():
            g = box["games"][gid]
            m_, pts_ = wm.get((o["date"], o["home"]), (None, None))
            if m_ is None:
                continue
            n_chk += 1
            if (m_ > 0) != (g["hs"] > g["as_"]):
                bad_w += 1
            if pts_ is not None and pts_ != g["hs"] + g["as_"]:
                bad_pts += 1
        validation[D.SEASON_LABEL[yr]] = {"checked": n_chk, "winner_disagrees_with_odds_file_Win_Margin": bad_w, "total_points_disagree": bad_pts}
    res["odds_join"] = {"summary": join_sum, "unmatched_detail": join_rep, "validation_vs_odds_file_results": validation}
    # ---- fit models
    preds = {"A": {}, "B": {}}
    for tag, train in (("A", TRAIN_A), ("B", TRAIN_B)):
        m_tr = np.isin(seas, train)
        for name, cols in (("full", FULL_COLS), ("elo_only", ELO_COLS)):
            X = np.array([[r[c] for c in cols] for r in rows], dtype=float)
            mu, sd = X[m_tr].mean(axis=0), X[m_tr].std(axis=0)
            sd[sd == 0] = 1.0
            Z = (X - mu) / sd
            w = M.fit_logit(Z[m_tr], y[m_tr])
            preds[tag][name] = M.predict(w, Z)
            res["models"][f"{tag} {name}"] = {"train_seasons": [D.SEASON_LABEL[s] for s in train], "train_games": int(m_tr.sum()),
                                              "cols": cols, "intercept_std": float(w[0]),
                                              "weights_standardized": dict(zip(cols, map(float, w[1:]))),
                                              "weights_per_raw_unit": dict(zip(cols, map(float, w[1:] / sd))),
                                              "feature_mean_train": dict(zip(cols, map(float, mu))),
                                              "feature_sd_train": dict(zip(cols, map(float, sd)))}
    # ---- coverage per season
    pr = json.loads((HERE / "injury_parse_report_v3.json").read_text()) if (HERE / "injury_parse_report_v3.json").exists() else {}
    for yr in D.SEASONS:
        lab = D.SEASON_LABEL[yr]
        rs = [r for r in rows if r["season"] == yr]
        res["coverage"][lab] = {
            "box_games": len(rs), "odds_matched": sum(1 for r in rs if r["gid"] in odds),
            "report_file_found": int(sum(r["report_found"] for r in rs)), "game_listed_in_report": int(sum(r["covered"] for r in rs)),
            "one_team_not_yet_submitted": int(sum(1 for r in rs if r["covered"] and (r["nys_h"] + r["nys_a"]) == 1)),
            "both_not_yet_submitted": int(sum(1 for r in rs if r["covered"] and (r["nys_h"] + r["nys_a"]) == 2)),
            "nonzero_inj_diff": int(sum(1 for r in rs if r["inj_diff"] != 0.0)),
            "home_win_rate": float(np.mean([r["home_win"] for r in rs])),
            "report_lag_min_mean": float(np.mean([(man[r["gid"]]["tip"] - man[r["gid"]]["created_utc"]) / 60 for r in rs if man.get(r["gid"], {}).get("file")] or [float("nan")])),
            "parse": pr.get(str(yr), {})}
    # ---- primary test: Model A on 2024-25 vs moneylines
    rows_te = [r for r in rows if r["season"] == TEST_A and r["gid"] in odds]
    idx_te = [i for i, r in enumerate(rows) if r["season"] == TEST_A and r["gid"] in odds]
    wk_te = weeks_of([r["tip"] for r in rows_te])
    train_idx = [i for i, r in enumerate(rows) if r["season"] in TRAIN_A and r["gid"] in odds]
    rows_tr = [rows[i] for i in train_idx]
    allbets = {}
    for name in ("full", "elo_only"):
        for th in THETAS:
            bt = ml_bets(rows_te, preds["A"][name][idx_te], odds, th)
            btr = ml_bets(rows_tr, preds["A"][name][train_idx], odds, th)
            allbets[(name, th)] = bt
            st = summarize(bt, wk_te)
            tr = {"n": len(btr), "mean": mean_of(btr)}
            tr["by_season"] = {D.SEASON_LABEL[s]: {"n": sum(1 for b in btr if rowmap[b["gid"]]["season"] == s),
                                                   "mean": mean_of([b for b in btr if rowmap[b["gid"]]["season"] == s])} for s in TRAIN_A}
            res["versions"][f"{name} theta={th * 100:.0f}%"] = {"train": tr, "test": st}
    # ---- Brier / log loss by season
    p_ml = {gid: D.devig_home(*o) for gid, o in odds.items()}
    qual = {"by_season": {}}
    for yr in D.SEASONS:
        lab = D.SEASON_LABEL[yr]
        ia = [i for i, r in enumerate(rows) if r["season"] == yr]
        d = {"all_games": {}, "odds_matched_games": {}}
        for tag in ("A", "B"):
            for name in ("full", "elo_only"):
                d["all_games"][f"model {tag} {name}"] = qual_block(preds[tag][name][ia], y[ia])
        io = [i for i in ia if rows[i]["gid"] in p_ml]
        if io:
            pm = np.array([p_ml[rows[i]["gid"]] for i in io])
            d["odds_matched_games"]["moneyline devig"] = qual_block(pm, y[io])
            for tag in ("A", "B"):
                for name in ("full", "elo_only"):
                    d["odds_matched_games"][f"model {tag} {name}"] = qual_block(preds[tag][name][io], y[io])
        d["in_sample"] = {"A": yr in TRAIN_A, "B": yr in TRAIN_B}
        qual["by_season"][lab] = d
    res["quality"] = qual
    pA_full = qual["by_season"][D.SEASON_LABEL[TEST_A]]["odds_matched_games"]["model A full"]["brier"]
    pML = qual["by_season"][D.SEASON_LABEL[TEST_A]]["odds_matched_games"]["moneyline devig"]["brier"]
    # calibration 2024-25
    ite = np.array(idx_te)
    res["calibration_2024_25"] = {"model_A_full": M.calib(preds["A"]["full"][ite], y[ite]),
                                  "moneyline_devig": M.calib(np.array([p_ml[rows[i]["gid"]] for i in idx_te]), y[ite])}
    # ---- breakdowns of 2024-25 bets
    b2 = allbets[("full", 0.02)]
    by_edge = []
    for lo, hi in [(0.02, 0.04), (0.04, 0.06), (0.06, 0.10), (0.10, 9.0)]:
        s_ = [b for b in b2 if lo - 1e-12 <= b["edge"] < hi - 1e-12]
        by_edge.append({"edge_range": f"{lo:.2f}-{hi:.2f}", "n": len(s_), "mean": mean_of(s_)})
    res["bets_by_edge_full_theta2"] = by_edge
    b4 = allbets[("full", 0.04)]
    res["bets_by_side_type_full_theta4"] = {lab: {"n": len(s_), "mean": mean_of(s_)} for lab, s_ in
                                            (("favourite", [b for b in b4 if b["fav"]]), ("underdog", [b for b in b4 if not b["fav"]]),
                                             ("home side", [b for b in b4 if b["side"] == 0]), ("away side", [b for b in b4 if b["side"] == 1]),
                                             ("regular season", [b for b in b4 if b["stype"] == 2]), ("play-in", [b for b in b4 if b["stype"] == 5]),
                                             ("playoffs", [b for b in b4 if b["stype"] == 3]),
                                             ("game listed in injury report", [b for b in b4 if b["covered"] == 1.0]))}
    # sensitivity: v2-style injury value (no first-5-games zeroing)
    alt = [dict(r, inj_diff=r["inj_diff_v2"]) for r in rows]
    Xa = np.array([[r[c] for c in FULL_COLS] for r in alt], dtype=float)
    m_tr = np.isin(seas, TRAIN_A)
    mu, sd = Xa[m_tr].mean(axis=0), Xa[m_tr].std(axis=0)
    sd[sd == 0] = 1.0
    wa = M.fit_logit(((Xa - mu) / sd)[m_tr], y[m_tr])
    pa = M.predict(wa, (Xa - mu) / sd)
    res["sensitivity_v2_style_injury_value_full_theta4"] = summarize(ml_bets(rows_te, pa[idx_te], odds, 0.04), wk_te)
    # ---- Kalshi 2025-26 confirmation with Model B
    games, excl, flags = drift.load_nba()
    games = sorted(games, key=lambda g: (g.tip, g.gid))
    krows, kmap = [], Counter()
    for g in games:
        gdate = F.parse_suffix_date(g.gid.split("-", 1)[1])
        h_c, a_c = g.mk[g.home_idx].code, g.mk[1 - g.home_idx].code
        bid, dd = F2.match_box(box, gdate, h_c, a_c)
        if bid is None:
            kmap["kalshi_game_without_box_game"] += 1
            continue
        r = dict(rowmap[bid])
        hw = g.mk[g.home_idx].result
        if hw != r["home_win"]:
            kmap["kalshi_result_disagrees_with_box"] += 1
        r.update({"gid": g.gid, "home_win": hw, "both_observed": r["covered"], "box_gid": bid})
        krows.append(r)
        if dd:
            kmap["matched_off_by_one_day"] += 1
        kmap["matched"] += 1
    quotes, qex = M.quotes_for(games)
    gid2i = {r["gid"]: i for i, r in enumerate(krows)}
    kweeks = weeks_of([g.tip for g in games])
    box_idx = [i for i, r in enumerate(rows) if r["gid"] in {kr["box_gid"] for kr in krows}]
    box_order = {rows[i]["gid"]: i for i in box_idx}
    kidx = [box_order[r["box_gid"]] for r in krows]
    kal = {}
    kbets = {}
    for name in ("full", "elo_only"):
        for th in THETAS:
            bk = M.make_bets(krows, preds["B"][name][kidx], quotes, th)
            kbets[(name, th)] = bk
            kal[f"{name} theta={th * 100:.0f}%"] = summarize(bk, kweeks)
    res["kalshi_confirmation"] = {"games_mapped": dict(kmap), "kalshi_games": len(games), "loader_exclusions": dict(excl),
                                  "quote_exclusions": qex, "games_with_valid_quotes": len(quotes), "weeks": kweeks, "versions": kal}
    # 2025-26 Brier / log loss: model B vs Kalshi mid-normalized
    qk = [i for i, r in enumerate(krows) if r["gid"] in quotes]
    mk = np.array([quotes[krows[i]["gid"]][2] / (quotes[krows[i]["gid"]][2] + quotes[krows[i]["gid"]][3]) for i in qk])
    yk = np.array([krows[i]["home_win"] for i in qk])
    kq = {"kalshi T-1h mid (normalized)": qual_block(mk, yk)}
    for tag in ("B", "A"):
        for name in ("full", "elo_only"):
            kq[f"model {tag} {name}"] = qual_block(preds[tag][name][[kidx[i] for i in qk]], yk)
    res["quality"]["kalshi_2025_26_valid_quote_games"] = kq
    res["calibration_2025_26"] = {"model_B_full": M.calib(preds["B"]["full"][[kidx[i] for i in qk]], yk), "kalshi": M.calib(mk, yk)}
    kb4 = kbets[("full", 0.04)]
    res["kalshi_bets_by_edge_full_theta2"] = [{"edge_range": f"{lo:.2f}-{hi:.2f}", "n": len(s_), "mean": mean_of(s_)} for lo, hi in
                                              [(0.02, 0.04), (0.04, 0.06), (0.06, 0.10), (0.10, 9.0)]
                                              for s_ in [[b for b in kbets[("full", 0.02)] if lo - 1e-12 <= b["edge"] < hi - 1e-12]]]
    # ---- odds-timing checks: file's 2025-26 table vs Kalshi T-1h; spread vs moneyline
    import sqlite3
    con = sqlite3.connect(f"file:{D.ODDS_DB.as_posix()}?mode=ro", uri=True)
    o26 = [dict(date=str(d)[:10], home=D.NAME2CODE.get(h), away=D.NAME2CODE.get(a), ml_h=D.to_int_odds(mh), ml_a=D.to_int_odds(ma), spread=sp)
           for d, h, a, sp, mh, ma in con.execute('select Date, Home, Away, Spread, ML_Home, ML_Away from "odds_2025-26"')]
    con.close()
    m26, rep26, sum26 = D.join_odds(box, [o for o in o26 if o["home"]], 2026)
    k_by_box = {r["box_gid"]: r for r in krows}
    pairs = []
    for gid, o in m26.items():
        kr = k_by_box.get(gid)
        if kr and kr["gid"] in quotes:
            q = quotes[kr["gid"]]
            pairs.append((D.devig_home(o["ml_h"], o["ml_a"]), q[2] / (q[2] + q[3]), kr["home_win"], o["spread"]))
    tim = {"odds_2025_26_table_rows": len(o26), "joined_to_box": sum26, "overlap_with_kalshi_valid_quotes": len(pairs)}
    if pairs:
        a = np.array(pairs, dtype=float)
        tim.update({"mean_abs_diff_ml_vs_kalshi_prob": float(np.mean(np.abs(a[:, 0] - a[:, 1]))), "corr_ml_vs_kalshi": float(np.corrcoef(a[:, 0], a[:, 1])[0, 1]),
                    "brier_ml_devig": M.brier(a[:, 0], a[:, 2]), "brier_kalshi": M.brier(a[:, 1], a[:, 2]),
                    "logloss_ml_devig": M.logloss(a[:, 0], a[:, 2]), "logloss_kalshi": M.logloss(a[:, 1], a[:, 2]),
                    "first_game_date": min(m26[g]["date"] for g in m26), "last_game_date": max(m26[g]["date"] for g in m26)})
    pp, ss = [], []
    for yr in D.ODDS_TABLE:
        for o in odds_all[yr]:
            if o["ml_h"] and o["ml_a"] and o["spread"] is not None:
                try:
                    sv = float(o["spread"])
                except (TypeError, ValueError):
                    continue
                ss.append(sv)
                pp.append(D.devig_home(o["ml_h"], o["ml_a"]))
    ss, pp = np.array(ss), np.array(pp)
    lo_ = np.log(pp / (1 - pp))
    slope = float(np.polyfit(ss, lo_, 1)[0])
    tim["spread_vs_devig_logit"] = {"n": int(len(ss)), "corr": float(np.corrcoef(ss, lo_)[0, 1]), "logit_per_point_slope": slope,
                                    "note": "spread is the home-favourite margin in the file; a closing line typically gives about 0.14-0.15 logit per point"}
    res["odds_timing_checks"] = tim
    # ---- verdict
    pv = res["versions"]["full theta=4%"]["test"]
    rules = verdict(pv, kal["full theta=4%"], pA_full, pML)
    res["primary"] = {"pass": all(x[1] for x in rules), "rules": [(a, bool(b), c) for a, b, c in rules]}
    (HERE / "results_v3.json").write_text(json.dumps(res, indent=1, default=str))
    write_readme(res)
    print(json.dumps({"primary": res["primary"], "coverage": res["coverage"], "join": join_sum, "validation": validation}, indent=1, default=str))
    print(table_ml(res["versions"]))
    print(table_kalshi(kal))


def qrow(lab, d):
    return f"| {lab} | {d['n']} | {d['brier']:.4f} | {d['logloss']:.4f} |"


def write_readme(res):
    p, q, cov = res["primary"], res["quality"], res["coverage"]
    L = ["# Research log #32 results: NBA game-winner model v3 (5 seasons, sportsbook moneylines + Kalshi)\n",
         "**Data licence note (private research only):** the moneylines come from kyleskom/NBA-Machine-Learning-Sports-Betting "
         "`Data/OddsData.sqlite` (scraped from Sportsbook Review by that repo's author, no licence). The file is kept under "
         "data/raw/nba_odds_samples/ and must not be republished or redistributed. Only aggregate results are written here. "
         "The book and the time of the listed line are unknown. Box scores: hoopR-nba-data (CC BY 4.0). Injury reports: the NBA's own PDFs.\n",
         f"**Primary verdict (full model, theta = 4%, 2024-25 against moneylines): {'PASS' if p['pass'] else 'FAIL'}**\n"]
    L += [f"- {'PASS' if b else 'FAIL'}  rule {a}: {c}" for a, b, c in p["rules"]]
    L.append("\nIn plain English: every bet is $1 at the sportsbook's listed price (its margin included); the table also shows what that is per week at $10 a bet. "
             "A bet is placed only when the model's chance for a team beats the price's implied chance by at least theta. "
             "Model A was fitted on 2021-22 to 2023-24 and never saw 2024-25. Model B (2021-22 to 2024-25) is checked on Kalshi 2025-26 "
             "(10 contracts, ask plus fee, held to settlement).\n")
    L.append("## All 6 versions: Model A on 2024-25 against moneylines\n")
    L.append(table_ml(res["versions"]))
    L.append("\nIn-sample columns are the training seasons (they do not count). Return is per $1 staked.\n")
    L.append("## Same 6 versions: Model B on Kalshi 2025-26 (confirmation)\n")
    ck = res["kalshi_confirmation"]
    L.append(table_kalshi(ck["versions"]))
    L.append(f"\nKalshi games {ck['kalshi_games']}, mapped to box scores {ck['games_mapped']}, with a valid T-1h quote on both teams {ck['games_with_valid_quotes']}; "
             f"weeks {ck['weeks']}; quote exclusions {ck['quote_exclusions']}.\n")
    L.append("## Data coverage per season\n")
    L.append("| season | box games | odds matched | report file found | game listed in report | one team not yet submitted | home win rate | mean report lead (min) | PDFs used | PDF parse errors | files with problems |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for lab, c in cov.items():
        ps = c.get("parse", {})
        L.append(f"| {lab} | {c['box_games']} | {c['odds_matched']} | {c['report_file_found']} | {c['game_listed_in_report']} | {c['one_team_not_yet_submitted']} | "
                 f"{c['home_win_rate']:.3f} | {c['report_lag_min_mean']:.0f} | {ps.get('files', 'n/a')} | {ps.get('errors', 'n/a')} | {ps.get('files_with_problems', 'n/a')} |")
    oj = res["odds_join"]
    L.append(f"\nOdds join summary: {oj['summary']}\n")
    L.append(f"Join validation against the odds file's own Win_Margin / Points columns: {oj['validation_vs_odds_file_results']}\n")
    L.append("Unmatched rows are listed in results_v3.json under odds_join.unmatched_detail.\n")
    L.append("## Brier score and log loss by season (lower is better)\n")
    L.append("Model A and B rows for a season are in-sample when that season was in the fit (flag in results_v3.json).\n")
    for lab, d in q["by_season"].items():
        L.append(f"**{lab}** (A in-sample: {d['in_sample']['A']}, B in-sample: {d['in_sample']['B']})")
        L.append("| set | n | Brier | log loss |\n|---|---|---|---|")
        for k_, v_ in d["odds_matched_games"].items():
            L.append(qrow(k_ + " (odds-matched games)", v_))
        for k_, v_ in d["all_games"].items():
            L.append(qrow(k_ + " (all games)", v_))
        L.append("")
    L.append("**2025-26 on games with a valid Kalshi T-1h quote**")
    L.append("| set | n | Brier | log loss |\n|---|---|---|---|")
    for k_, v_ in q["kalshi_2025_26_valid_quote_games"].items():
        L.append(qrow(k_, v_))
    L.append("\n## Feature weights (logistic regression, training seasons)\n")
    for nm, m in res["models"].items():
        L.append(f"**{nm}** (trained {', '.join(m['train_seasons'])}, {m['train_games']} games): intercept (home flag, standardized scale) {m['intercept_std']:+.3f}")
        L.append("| feature | weight per 1 sd | weight per raw unit | training sd |\n|---|---|---|---|")
        for c in m["cols"]:
            L.append(f"| {c} | {m['weights_standardized'][c]:+.3f} | {m['weights_per_raw_unit'][c]:+.4f} | {m['feature_sd_train'][c]:.2f} |")
        L.append("")
    L.append("## Calibration, 2024-25 (10 buckets)\n")
    L.append("| bucket | model n | model mean p | model win rate | moneyline n | moneyline mean p | moneyline win rate |\n|---|---|---|---|---|---|---|")
    mk = {b["bucket"]: b for b in res["calibration_2024_25"]["moneyline_devig"]}
    for b in res["calibration_2024_25"]["model_A_full"]:
        m = mk.get(b["bucket"])
        L.append(f"| {b['bucket']} | {b['n']} | {b['mean_p']:.3f} | {b['win_rate']:.3f} | " + (f"{m['n']} | {m['mean_p']:.3f} | {m['win_rate']:.3f} |" if m else "0 | | |"))
    L.append("\n## Calibration, 2025-26 (10 buckets, games with a Kalshi quote)\n")
    L.append("| bucket | model B n | model mean p | model win rate | Kalshi n | Kalshi mean p | Kalshi win rate |\n|---|---|---|---|---|---|---|")
    mk = {b["bucket"]: b for b in res["calibration_2025_26"]["kalshi"]}
    for b in res["calibration_2025_26"]["model_B_full"]:
        m = mk.get(b["bucket"])
        L.append(f"| {b['bucket']} | {b['n']} | {b['mean_p']:.3f} | {b['win_rate']:.3f} | " + (f"{m['n']} | {m['mean_p']:.3f} | {m['win_rate']:.3f} |" if m else "0 | | |"))
    L.append("\n## 2024-25 return by edge size (full, theta 2% bets)\n")
    L.append("| edge | bets | mean return per $1 |\n|---|---|---|")
    for b in res["bets_by_edge_full_theta2"]:
        L.append(f"| {b['edge_range']} | {b['n']} | {M.fmt(b['mean'])} |")
    L.append("\n## 2024-25 return by bet type (full, theta 4% bets)\n")
    L.append("| group | bets | mean return per $1 |\n|---|---|---|")
    for lab, b in res["bets_by_side_type_full_theta4"].items():
        L.append(f"| {lab} | {b['n']} | {M.fmt(b['mean'])} |")
    L.append("\n## 2025-26 Kalshi return by edge size (full, theta 2% bets)\n")
    L.append("| edge | bets | mean net per contract |\n|---|---|---|")
    for b in res["kalshi_bets_by_edge_full_theta2"]:
        L.append(f"| {b['edge_range']} | {b['n']} | {M.fmt(b['mean'])} |")
    s = res["sensitivity_v2_style_injury_value_full_theta4"]
    L.append(f"\nSensitivity, v2-style injury value (no zeroing for a player's first 5 games), full theta 4%, 2024-25: {s['n']} bets, mean {M.fmt(s['mean'])}, CI [{M.fmt(s['ci_lo'])}, {M.fmt(s['ci_hi'])}] (lead only).\n")
    L.append(f"## Odds-timing checks\n\n{json.dumps(res['odds_timing_checks'], indent=1, default=str)}\n")
    L.append(f"Feature stats: {res['feature_stats']}\n")
    L.append("## Data provenance and download notes (added after the run; no analysis choice depends on them)\n")
    L += [
        "- Box scores: GitHub sportsdataverse/hoopR-nba-data, nba/team_box/parquet/team_box_{2022..2024}.parquet and nba/player_box/parquet/player_box_{2022..2024}.parquet "
        "(plain HTTPS GET from raw.githubusercontent.com, 1 s apart) added to the 2025 and 2026 files already in data/raw/nba_open/. CC BY 4.0, ESPN-derived. "
        "Games used: 1323 / 1320 / 1319 / 1321 / 1322 (the 4 All-Star games of 2025-26 and the exhibition teams are dropped).",
        "- Moneylines: kyleskom/NBA-Machine-Learning-Sports-Betting Data/OddsData.sqlite (4.6 MB), the copy already at data/raw/nba_odds_samples/OddsData.sqlite, opened read-only. "
        "No licence; scraped from Sportsbook Review by that author; PRIVATE RESEARCH ONLY: do not republish or redistribute it (keep it under data/raw/, which is not for publication). "
        "The book and the capture time of each line are not documented. The file's 2025-26 table (554 rows, to 2026-01-07) is used only for the timing check.",
        "- Injury reports: official PDFs of the NBA's own CDN (ak-static.cms.nba.com/referee/injury/), fetched by the file-name pattern only (HTTP GET, no site scraping, no login). "
        "Hourly names Injury-Report_YYYY-MM-DD_HHPM.pdf exist for every season (2021-22 to 2025-26); 15-minute names (_HH_MMPM) appear from 2025-12-22. "
        "5715 probes in total (2171 downloads, the rest read from files already on disk, 0.25 s between requests, at most 3 workers at a time); 3533 distinct files "
        "(225 MB) were chosen as game reports; the other existing files probed are also kept in data/raw/nba_injury/. A missing file answers HTTP 403.",
        "- File format changed: the 2021-22 / 2022-23 PDFs (iTextSharp, /CreationDate at the END of the file, two-word headers 'Game' 'Date', names with spaces, multi-word reasons) "
        "need the old-layout parser (injury_parse_v3.parse_old); from 2023-24 (37 files already in 2022-23) the layout is the one parsed in #31 "
        "(single-word headers, glued names, /CreationDate in the first 4 KB). The layout is detected per file from the header. Hourly files stamped H are created at H:30 in every season.",
        "- Parse check: 3533 files parsed, 0 exceptions, 0 flagged problem items (every status word paired with a player), 0 files with zero rows. "
        "Per-season counts are in the coverage table and results_v3.json (coverage.<season>.parse). The practical check is coverage: every game but 6 is found in its report.",
        "- Postponed games (the January 2025 Los Angeles fire and weather postponements; verified for all 5) appear twice, at the original and the rescheduled date, with different prices in the odds file; 5 odds rows on the original dates match no box game and are dropped (listed above).",
    ]
    L.append("## Interpretation choices\n")
    L += [f"- {s_}" for s_ in res["interpretations"]]
    (HERE / "README_V3.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
