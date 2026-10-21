"""Stage A of research log #30: model probabilities + decision-time quotes for every bracket market.
Run from repo root:  python build_rows.py   (needs data/nbm_station_max.csv, data/nbm_files.csv, data/weather.sqlite)
Writes reports/output/weather_nbm/rows_{morning,evening}.csv.gz and build_summary.json.  Read-only on the DB."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nbm_model as nm
import nbm_runsel as rs

LAST_DAY = "2026-10-12"
DB = "data/weather.sqlite"


def w1_reasons(ms):
    """Event validity (same checks as weather_calib.w1_event_reasons): scalar, unparsed strikes, overlap, one YES."""
    r = set()
    if any(m["result"] == "scalar" for m in ms):
        r.add("scalar_result")
    if any(m["stype"] is None or m["result"] not in ("yes", "no", "scalar") for m in ms):
        r.add("strike_or_result_missing")
    else:
        try:
            iv = sorted(nm.market_interval(m["stype"], m["floor"], m["cap"]) for m in ms)
            if any(iv[i][1] >= iv[i + 1][0] for i in range(len(iv) - 1)):
                r.add("overlapping_ranges")
        except Exception:
            r.add("strike_or_result_missing")
    ny = sum(1 for m in ms if m["result"] == "yes")
    if ny == 0:
        r.add("no_yes")
    elif ny > 1:
        r.add("multiple_yes")
    return r


def load_events(conn):
    q = ("SELECT m.mid,m.ticker,m.series,m.event_ticker,m.tz,m.day,m.strike_type,m.floor_strike,m.cap_strike,"
         "m.open_ts,m.close_ts,m.settled_ts,m.result,json_extract(m.raw_json,'$.expiration_value'),s.station,"
         "EXISTS(SELECT 1 FROM wx_done d WHERE d.ticker=m.ticker AND d.step='quotes') "
         "FROM wx_markets m JOIN wx_series s ON s.ticker=m.series "
         "WHERE m.kind='high' AND s.station IS NOT NULL AND m.day<=? ORDER BY m.day, m.ticker")
    ev = defaultdict(list)
    for r in conn.execute(q, (LAST_DAY,)):
        ev[r[3]].append(dict(mid=r[0], ticker=r[1], series=r[2], event=r[3], tz=r[4], day=r[5], stype=r[6],
                             floor=r[7], cap=r[8], open_ts=r[9], close_ts=r[10], settled_ts=r[11], result=r[12],
                             expval=r[13], station=r[14], done=bool(r[15])))
    return ev


def load_nbm():
    f = pd.read_csv("data/nbm_files.csv", dtype=str)
    files = {}
    for r in f.itertuples(index=False):
        if r.status == "ok":
            files[(r.run_date, r.cycle, r.target_date)] = dict(lm=datetime.fromisoformat(r.last_modified_utc),
                                                              version=r.nbm_version)
    d = pd.read_csv("data/nbm_station_max.csv", dtype=dict(run_date=str, cycle=str, station=str, nbm_version=str))
    fc = {}
    for r in d.itertuples(index=False):
        fc[(r.run_date, r.cycle, r.target_date, r.station)] = (int(r.txn), None if pd.isna(r.xnd) else int(r.xnd),
                                                             str(r.nbm_version), int(r.fhr), r.last_modified_utc)
    return files, fc


def main():
    t0 = time.time()
    conn = sqlite3.connect(f"file:{Path(DB).resolve().as_posix()}?mode=ro", uri=True, timeout=120)
    raw_events = load_events(conn)
    files, fc = load_nbm()
    summ = dict(events_total=len(raw_events), markets_total=sum(len(v) for v in raw_events.values()))
    excl = Counter()
    events = {}
    for et, ms in raw_events.items():
        rs_ = w1_reasons(ms)
        if rs_:
            for r in rs_:
                excl[r] += 1
            continue
        if not all(m["done"] for m in ms):
            excl["candles_incomplete"] += 1
            continue
        yvals = {m["expval"] for m in ms if m["expval"] not in (None, "")}
        y = None
        if len(yvals) == 1:
            try:
                y = int(round(float(next(iter(yvals)))))
            except ValueError:
                y = None
        if y is not None:        # consistency of expiration_value with the settled brackets
            for m in ms:
                a, b = nm.market_interval(m["stype"], m["floor"], m["cap"])
                if (a <= y <= b) != (m["result"] == "yes"):
                    y = None
                    excl["obs_inconsistent_with_results(kept for trading, not for calibration)"] += 1
                    break
        else:
            excl["obs_missing(kept for trading, not for calibration)"] += 1
        events[et] = dict(event=et, station=ms[0]["station"], icao="K" + ms[0]["station"][3:], tz=ms[0]["tz"],
                          day=ms[0]["day"], markets=ms, settled_ts=max(m["settled_ts"] for m in ms), y=y)
    summ["excluded_events"] = dict(excl)
    summ["events_valid"] = len(events)
    by_station = defaultdict(list)
    for e in events.values():
        by_station[e["station"]].append(e)
    dup = 0
    for s in by_station:
        by_station[s].sort(key=lambda e: e["day"])
        days = [e["day"] for e in by_station[s]]
        dup += len(days) - len(set(days))
    summ["duplicate_station_days"] = dup

    out_summary = {}
    for typ in ("morning", "evening"):
        skip = Counter()
        sel_count = Counter()
        for e in events.values():
            D = e["day"]
            dd = D.replace("-", "")
            if typ == "morning":
                lm = files.get((dd, "06", D), {}).get("lm")
                cyc = rs.choose_morning(D, e["tz"], lm)
                key = (dd, "06", D) if cyc else None
                dec = rs.morning_decision_utc(D, e["tz"])
            else:
                prev = (date.fromisoformat(D) - timedelta(days=1)).strftime("%Y%m%d")
                lm00 = files.get((dd, "00", D), {}).get("lm")
                lm18 = files.get((prev, "18", D), {}).get("lm")
                cyc = rs.choose_evening(D, lm00, lm18)
                key = (dd, "00", D) if cyc == "00" else (prev, "18", D) if cyc == "18" else None
                dec = rs.evening_decision_utc(D)
            e["dec_" + typ] = dec
            e["run_" + typ] = None
            if key is None:
                skip["no_run_published_by_decision_time"] += 1
                continue
            f = fc.get(key + (e["icao"],))
            if f is None:
                skip["station_missing_in_run"] += 1
                continue
            if f[1] is None:
                skip["xnd_missing"] += 1
                continue
            sel_count[key[1]] += 1
            assert datetime.fromisoformat(f[4]) <= dec, "look-ahead: run published after the decision time"
            e["run_" + typ] = dict(run_date=key[0], cycle=key[1], txn=f[0], xnd=f[1], version=f[2], fhr=f[3], lm=f[4])
        hist_days, hist_entries = {}, {}
        for s, lst in by_station.items():
            hist_days[s] = [e["day"] for e in lst]
            hist_entries[s] = [dict(day=e["day"], settled_ts=e["settled_ts"], y=e["y"],
                                    txn=(e["run_" + typ]["txn"] if e["run_" + typ] else None),
                                    xnd=(e["run_" + typ]["xnd"] if e["run_" + typ] else None)) for e in lst]
        pool_cache = {}
        rows = []
        cal_modes = Counter()
        order = sorted((e for e in events.values() if e["run_" + typ]), key=lambda e: e["day"])
        for e in order:
            dec = e["dec_" + typ]
            dts = int(dec.timestamp())
            s = e["station"]
            hist = nm.select_history(hist_days[s], hist_entries[s], e["day"], dts)
            ck = (e["day"], dts)
            if ck not in pool_cache:
                for k in [k for k in pool_cache if k[0] < e["day"]]:
                    del pool_cache[k]
                pool = []
                for s2 in by_station:
                    pool += nm.select_history(hist_days[s2], hist_entries[s2], e["day"], dts)
                pf = None
                if len(pool) >= nm.MIN_STATION_DAYS:
                    pf = nm.fit_bk([x["txn"] for x in pool], [x["xnd"] for x in pool], [x["y"] for x in pool])
                pool_cache[ck] = (pool, pf)
            pool, pf = pool_cache[ck]
            cal = nm.calibrate(hist, pool, pooled=pf)
            if cal is None:
                skip["no_calibration_pool_lt_15"] += 1
                continue
            cal_modes[cal["mode"]] += 1
            run = e["run_" + typ]
            mu = run["txn"] + cal["b"]
            sig = float(nm.sigma_of(cal["k"], run["xnd"]))
            for m in e["markets"]:
                a, b = nm.market_interval(m["stype"], m["floor"], m["cap"])
                p = float(nm.bracket_prob(a, b, mu, sig))
                q = conn.execute("SELECT ts,bid_c,ask_c FROM wx_quotes WHERE mid=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                                 (m["mid"], dts)).fetchone()
                qts, bid, ask = (None, None, None) if q is None else q
                is_open = m["open_ts"] <= dts < m["close_ts"]
                valid = (bid is not None and ask is not None and bid <= ask and is_open)
                rows.append(dict(typ=typ, event=e["event"], ticker=m["ticker"], station=s, tz=e["tz"], day=e["day"],
                                 stype=m["stype"], a=a, b=b, result=m["result"], y_obs=e["y"], p=p,
                                 bid_e4=bid, ask_e4=ask, qts=qts, qage_s=None if qts is None else dts - qts,
                                 is_open=int(is_open), valid=int(valid), mu=mu, sigma=sig, txn=run["txn"],
                                 xnd=run["xnd"], nbm_version=run["version"], cycle=run["cycle"],
                                 run_date=run["run_date"], run_lm=run["lm"], dec_utc=dec.isoformat(),
                                 b_cal=cal["b"], k_cal=cal["k"], cal_mode=cal["mode"], cal_n=cal["n"],
                                 cal_npool=cal["n_pool"]))
        df = pd.DataFrame(rows)
        df.to_csv(HERE / f"rows_{typ}.csv.gz", index=False)
        out_summary[typ] = dict(station_days_modelled=int(df.groupby(["station", "day"]).ngroups),
                                markets=len(df), markets_valid_quote=int(df["valid"].sum()),
                                markets_no_candle_before_decision=int(df["qts"].isna().sum()),
                                markets_not_open_at_decision=int((df["is_open"] == 0).sum()),
                                markets_missing_bid_or_ask=int(((df["bid_e4"].isna() | df["ask_e4"].isna())
                                                                & df["qts"].notna()).sum()),
                                skips=dict(skip), runs_used=dict(sel_count), cal_modes=dict(cal_modes))
        print(typ, out_summary[typ], f"{time.time()-t0:.0f}s", flush=True)
    summ["by_type"] = out_summary
    (HERE / "build_summary.json").write_text(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()
