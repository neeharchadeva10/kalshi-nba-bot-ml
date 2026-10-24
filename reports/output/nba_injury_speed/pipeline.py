"""Research log #44 pipeline: loads events, parsed injury reports, minutes and candles (streamed per game) and runs one split.
run_discovery.py and run_unseen.py both call analyze_split() so the unseen run uses exactly the discovery code."""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import common as C
from common import HERE, ROOT
import injury_speed as S

import injury_parse as IP     # reports/output/nba_model (name normalisation only)


def load_events(first, last):
    rows = list(csv.DictReader(open(HERE / "events_list.csv")))
    return [r for r in rows if first <= r["game_date_et"] <= last]


def load_minutes():
    import lib
    df = pd.read_parquet(ROOT / "data/raw/nba_open/player_box_2026.parquet",
                         columns=["season_type", "game_date", "athlete_display_name", "team_abbreviation",
                                  "team_display_name", "minutes", "did_not_play"])
    df = df[df.season_type == 2]
    team_glued = {}
    for disp, ab in df[["team_display_name", "team_abbreviation"]].drop_duplicates().itertuples(index=False):
        k = lib.to_kalshi(ab)
        if k:
            team_glued[disp.replace(" ", "")] = k
    played = df[(df.minutes.fillna(0) > 0) & (~df.did_not_play.fillna(False).astype(bool))]
    rows = ((lib.to_kalshi(r.team_abbreviation), IP.norm_box_name(r.athlete_display_name), str(r.game_date)[:10], r.minutes)
            for r in played.itertuples(index=False) if lib.to_kalshi(r.team_abbreviation))
    return S.Minutes(rows), team_glued


def load_reports():
    """{(date_iso, away, home): [Report]} from reports_parsed.jsonl (streamed line by line)."""
    by_game = defaultdict(dict)
    man = json.loads((HERE / "reports_manifest.json").read_text())
    meta = {"files": 0, "errors": [], "problems_files": 0}
    with open(HERE / "reports_parsed.jsonl") as fh:
        for ln in fh:
            r = json.loads(ln)
            meta["files"] += 1
            if r.get("error"):
                meta["errors"].append((r["name"], r["error"]))
                continue
            if r.get("problems"):
                meta["problems_files"] += 1
            P = man[r["name"]]
            era15 = bool(S.ERA15_RE.search(r["name"]))
            games = defaultdict(lambda: {"status": {}, "nys": set()})
            for date, matchup, team, player, status in r["rows"]:
                d = datetime.strptime(date, "%m/%d/%Y").strftime("%Y-%m-%d")
                a, h = matchup.split("@")
                games[(d, a, h)]["status"][(team, player)] = status
            for date, matchup, team in r["nys"]:
                if not date or not matchup:
                    continue
                d = datetime.strptime(date, "%m/%d/%Y").strftime("%Y-%m-%d")
                a, h = matchup.split("@")
                games[(d, a, h)]["nys"].add(team)
            for k, v in games.items():
                by_game[k][r["name"]] = S.Report(r["name"], P, v["status"], frozenset(v["nys"]), era15)
    return {k: list(v.values()) for k, v in by_game.items()}, meta


def make_resolver(minutes, team_glued, date_iso, away, home, threshold, stats):
    def resolve(team, player_raw):
        pn = IP.norm_injury_name(player_raw)
        tc = team_glued.get(team)
        cands = [tc] if tc else [away, home]
        best = None
        for c in cands:
            mat, avg = minutes.material(c, pn, date_iso, threshold)
            if avg is not None:
                best = (mat, avg)
                break
        if best is None:
            stats["unmatched_names"][(team, player_raw)] += 1
            return False, None
        return best
    return resolve


def load_event_book(conn, ev):
    pre = ev["event"]
    ms = conn.execute("SELECT ticker, strike, result FROM mkts WHERE event=?", (pre,)).fetchall()
    strikes = {t: s for t, s, _ in ms}
    results = {t: r for t, _, r in ms}
    candles = {}
    for t in strikes:
        rows = conn.execute("SELECT end_ts,bid,ask FROM candles WHERE ticker=? ORDER BY end_ts", (t,)).fetchall()
        if rows:
            e, b, a = zip(*rows)
            candles[t] = (list(e), [np.nan if x is None else x for x in b], [np.nan if x is None else x for x in a])
    return S.Book(strikes, candles, int(ev["T"])), results


VARIANTS = {   # information only
    "entry_delay_5min": dict(entry_delay_s=300),
    "exit_P+10min": dict(exit_after_s=600),
    "exit_P+60min": dict(exit_after_s=3600),
    "exit_at_tip": dict(exit_at_tip=True),
    "entry_carry_forward_at_P+120s": dict(entry_mode="carry"),
}


def summarize(trs):
    """trs: list of dicts with net, net_yes, net_no, game."""
    if not trs:
        return {"n": 0}
    nets = [t["net"] for t in trs]
    b = S.boot_lb(nets, [t["game"] for t in trs])
    cf = S.coin_flip([t["net_yes"] for t in trs], [t["net_no"] for t in trs], float(np.mean(nets)))
    return {"n": len(trs), "mean_net_cents": 100 * b["mean"], "lb_2.5_cents": 100 * b["lb_2.5"], "lb_5_cents": 100 * b["lb_5"],
            "ub_97.5_cents": 100 * b["ub_97.5"], "n_games": b["n_games"], "coin_flip_share_as_good": cf["share_as_good"],
            "win_rate": float(np.mean([n > 0 for n in nets]))}


def analyze_split(first, last, label):
    events = load_events(first, last)
    by_game, rmeta = load_reports()
    minutes, team_glued = load_minutes()
    conn = sqlite3.connect(f"file:{(HERE / 'candles.sqlite').as_posix()}?mode=ro", uri=True)
    done_notes = Counter(r[0] for r in conn.execute("SELECT note FROM done"))
    stats = {"unmatched_names": Counter(), "events": len(events), "games_no_report": 0, "games_one_report_only": 0,
             "games_no_candles": 0}
    cnt = {}
    rows, prim, variant_trs, det30_trs = [], [], defaultdict(list), []
    skip = Counter()
    for k, ev in enumerate(events):
        key = (ev["game_date_et"], ev["away"], ev["home"])
        reps = by_game.get(key, [])
        if not reps:
            stats["games_no_report"] += 1
            continue
        if len(reps) == 1:
            stats["games_one_report_only"] += 1
        book, results = load_event_book(conn, ev)
        if len(book.ends) == 0:
            stats["games_no_candles"] += 1
        T = int(ev["T"])
        for thr in (S.MATERIAL_MIN, 30.0):
            res = make_resolver(minutes, team_glued, ev["game_date_et"], ev["away"], ev["home"], thr, stats)
            evs = S.detect_events(reps, T, res, thr, counters=cnt if thr == S.MATERIAL_MIN else {})
            for e in evs:
                if thr == 30.0:
                    if e["status"] == "ok" and len(book.ends):
                        tr = S.trade_event(book, e["P"], e["direction"])
                        if tr["skip_reason"] is None:
                            det30_trs.append({**tr, "game": ev["event"]})
                    continue
                P = e["P"]
                row = {"event": ev["event"], "game_date_et": ev["game_date_et"], "away": ev["away"], "home": ev["home"],
                       "report": e["name"], "prev_report": e["prev_name"], "era": "15min" if e["era15"] else "hourly",
                       "P_utc": datetime.fromtimestamp(P, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "T_utc": T,
                       "min_to_tip": round((T - P) / 60, 1), "event_status": e["status"], "direction": e["direction"] or "",
                       "down_players": "; ".join(f"{p}({m:.1f})" for p, m in e["down"]),
                       "up_players": "; ".join(f"{p}({m:.1f})" for p, m in e["up"])}
                if e["status"] != "ok":
                    row["skip_reason"] = e["status"]
                    skip[e["status"]] += 1
                    rows.append(row)
                    continue
                if not len(book.ends):
                    row["skip_reason"] = "no_candles"
                    skip["no_candles"] += 1
                    rows.append(row)
                    continue
                m = S.m_series(book, P)
                for off in S.OFFSETS_MIN:
                    row[f"M_{'m' if off < 0 else 'p'}{abs(off)}"] = "" if m[off] is None else round(m[off], 3)
                tot, pre, late = S.shares(m)
                tot_c, pre_c, late_c = S.shares(S.m_series(book, P, cap_at_tip=True))
                row.update(total_move_cap="" if tot_c is None else round(tot_c, 3), pre_share_cap="" if pre_c is None else round(pre_c, 4),
                           late_share_cap="" if late_c is None else round(late_c, 4))
                row.update(total_move="" if tot is None else round(tot, 3), pre_share="" if pre is None else round(pre, 4),
                           late_share="" if late is None else round(late, 4))
                tr = S.trade_event(book, P, e["direction"])
                if tr["skip_reason"]:
                    row["skip_reason"] = tr["skip_reason"]
                    skip[tr["skip_reason"]] += 1
                    rows.append(row)
                    continue
                res_t = results.get(tr["ticker"])
                tr2 = S.trade_event(book, P, e["direction"], result=res_t)
                row.update(ticker=tr["ticker"], strike=tr["strike"], side=tr["side"],
                           entry_end_utc=datetime.fromtimestamp(tr["entry_end"], timezone.utc).strftime("%H:%M:%S"),
                           entry_delay_s=int(tr["entry_end"] - P), entry_price=round(tr["entry_price"], 4),
                           fee_in=tr["fee_in"], exit_end_utc=datetime.fromtimestamp(tr["exit_end"], timezone.utc).strftime("%H:%M:%S"),
                           exit_after_s=int(tr["exit_end"] - P), exit_price=round(tr["exit_price"], 4), fee_out=tr["fee_out"],
                           net=round(tr["net"], 6), net_opposite_side=round(tr["net_opposite"], 6),
                           net_hold_settle=round(tr2.get("net_settle", float("nan")), 6) if "net_settle" in tr2 else "",
                           skip_reason="")
                rows.append(row)
                rec = {**tr, "game": ev["event"], "direction": e["direction"], "era": row["era"], "net_settle": tr2.get("net_settle"),
                       "total_move": tot, "pre": pre, "late": late, "m": m}
                prim.append(rec)
                for vn, kw in VARIANTS.items():
                    v = S.trade_event(book, P, e["direction"], **kw)
                    if v["skip_reason"] is None:
                        variant_trs[vn].append({**v, "game": ev["event"]})
        if k % 100 == 0:
            print(label, k, len(events), len(prim), flush=True)
    # ------------------------------------------------ results
    ev_all = [r for r in rows if r["event_status"] in ("ok", "tie")]
    n_ok = sum(1 for r in rows if r["event_status"] == "ok")
    res = {"label": label, "first": first, "last": last, "report_files_parsed": rmeta["files"],
           "report_parse_errors": rmeta["errors"][:10], "n_report_parse_errors": len(rmeta["errors"]),
           "report_files_with_problems": rmeta["problems_files"], "stats": {k: v for k, v in stats.items() if k != "unmatched_names"},
           "unmatched_player_listings_total": int(sum(stats["unmatched_names"].values())),
           "unmatched_players_top": [[list(k), v] for k, v in stats["unmatched_names"].most_common(25)],
           "detection_counters": cnt, "events_in_window_ok": n_ok,
           "events_tie_skipped": skip["tie"], "events_outside_window_not_counted": skip["outside_window"],
           "events_by_direction": dict(Counter(r["direction"] for r in rows if r["event_status"] == "ok")),
           "trade_skips_by_reason": {k: v for k, v in skip.items() if k not in ("tie", "outside_window")}, "n_traded": len(prim)}
    # descriptive
    def desc(sel):
        pre = [p["pre"] for p in sel if p["pre"] is not None]
        late = [p["late"] for p in sel if p["late"] is not None]
        return {"n_events": len(sel), "n_with_share": len(pre), "median_pre_move_share": S.median_or_none(pre),
                "median_late_share": S.median_or_none(late)}
    # descriptive set = ALL ok events (including those whose trade was skipped): rebuild from rows
    drows = [r for r in rows if r["event_status"] == "ok" and r.get("pre_share", "") != ""]
    def drow(sel):
        pre = [float(r["pre_share"]) for r in sel if r.get("pre_share", "") != ""]
        late = [float(r["late_share"]) for r in sel if r.get("late_share", "") != ""]
        tm = [r for r in sel if r.get("total_move", "") != ""]
        return {"n_events": len(sel), "n_total_move_defined": len(tm), "n_pre_share": len(pre), "n_late_share": len(late),
                "median_pre_move_share": S.median_or_none(pre), "median_late_share": S.median_or_none(late)}
    okrows = [r for r in rows if r["event_status"] == "ok"]
    res["descriptive"] = {"overall": drow(okrows), "era_15min": drow([r for r in okrows if r["era"] == "15min"]),
                          "era_hourly": drow([r for r in okrows if r["era"] == "hourly"])}
    exp_dir = []
    for r in okrows:
        if r.get("total_move", "") != "" and abs(float(r["total_move"])) >= 1:
            ok_dir = (float(r["total_move"]) < 0) == (r["direction"] == "DOWN")
            if ok_dir:
                exp_dir.append(r)
    res["descriptive_information_only_moves_in_expected_direction"] = {"INFORMATION ONLY": True, **drow(exp_dir)}
    def drow_cap(sel):
        pre = [float(r["pre_share_cap"]) for r in sel if r.get("pre_share_cap", "") != ""]
        late = [float(r["late_share_cap"]) for r in sel if r.get("late_share_cap", "") != ""]
        return {"INFORMATION ONLY": True, "n_pre_share": len(pre), "n_late_share": len(late),
                "median_pre_move_share": S.median_or_none(pre), "median_late_share": S.median_or_none(late)}
    res["descriptive_information_only_offsets_after_tip_capped_at_tip"] = {
        "overall": drow_cap(okrows), "era_15min": drow_cap([r for r in okrows if r["era"] == "15min"]),
        "era_hourly": drow_cap([r for r in okrows if r["era"] == "hourly"])}
    kill_overall = res["descriptive"]["overall"]["median_pre_move_share"]
    kill_15 = res["descriptive"]["era_15min"]["median_pre_move_share"]
    res["kill_rule"] = {"median_pre_move_share_overall": kill_overall, "median_pre_move_share_15min": kill_15,
                        "fires_overall": bool(kill_overall is not None and kill_overall >= 0.5),
                        "fires_15min_era_only": bool(kill_15 is not None and kill_15 >= 0.5),
                        "primary_uses": "overall"}
    # trading
    trs = [{"net": p["net"], "net_yes": p["net_yes"], "net_no": p["net_no"], "game": p["game"]} for p in prim]
    main = summarize(trs)
    ent_delay = [p["entry_end"] - 0 for p in prim]
    res["trading"] = main
    res["trading_diagnostics"] = {
        "entry_delay_s_quantiles(25/50/75/90)": None,
        "mean_fee_total_cents": 100 * float(np.mean([p["fee_in"] + p["fee_out"] for p in prim])) if prim else None,
        "mean_gross_cents": 100 * float(np.mean([p["net"] + p["fee_in"] + p["fee_out"] for p in prim])) if prim else None,
        "by_side": {s: summarize([{"net": p["net"], "net_yes": p["net_yes"], "net_no": p["net_no"], "game": p["game"]}
                                  for p in prim if p["side"] == s]) for s in ("YES", "NO")}}
    ed = [r["entry_delay_s"] for r in rows if r.get("entry_delay_s", "") != ""]
    if ed:
        res["trading_diagnostics"]["entry_delay_s_quantiles(25/50/75/90)"] = [float(np.quantile(ed, q)) for q in (.25, .5, .75, .9)]
    bar = {}
    bar["1_mean_net_gt_0"] = bool(main["n"] > 0 and main["mean_net_cents"] > 0)
    bar["2_bootstrap_lb_gt_0"] = bool(main["n"] > 0 and main["lb_2.5_cents"] > 0)
    bar["3_at_least_50_events"] = bool(main["n"] >= 50)
    bar["4_coin_flip_le_5pct"] = bool(main["n"] > 0 and main["coin_flip_share_as_good"] <= 0.05)
    bar["5_kill_rule_does_not_fire"] = bool(not res["kill_rule"]["fires_overall"] and kill_overall is not None)
    res["pass_bar"] = bar
    res["pass_bar_all"] = all(bar.values())
    # information only
    info = {"NOTE": "INFORMATION ONLY - never selectable; a pass found only here is not a pass"}
    for vn, vt in variant_trs.items():
        info[vn] = {"INFORMATION ONLY": True, **summarize(vt)}
    info["hold_to_settlement"] = {"INFORMATION ONLY": True, **summarize(
        [{"net": p["net_settle"], "net_yes": p["net_settle"], "net_no": p["net_settle"], "game": p["game"]}
         for p in prim if p.get("net_settle") is not None]), "note": "coin-flip share not meaningful here (same net on both sides)"}
    info["material_threshold_30min"] = {"INFORMATION ONLY": True, **summarize(det30_trs)}
    for d in ("DOWN", "UP"):
        info[f"{d}_only"] = {"INFORMATION ONLY": True, **summarize(
            [{"net": p["net"], "net_yes": p["net_yes"], "net_no": p["net_no"], "game": p["game"]} for p in prim if p["direction"] == d])}
    for era in ("hourly", "15min"):
        info[f"era_{era}"] = {"INFORMATION ONLY": True, **summarize(
            [{"net": p["net"], "net_yes": p["net_yes"], "net_no": p["net_no"], "game": p["game"]} for p in prim if p["era"] == era])}
    res["information_only"] = info
    res["candle_download_notes"] = dict(done_notes)
    return rows, res


def write_rows(rows, path):
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
