"""Research log #54 pipeline: streams one event at a time from the read-only candles.sqlite (#44) and builds per-game records.
Both run_discovery.py and run_unseen.py use these functions, so the unseen run is exactly the discovery code."""
from __future__ import annotations

import csv
import sqlite3

import numpy as np

import lm as L

DB = L.INJ / "candles.sqlite"
EVENTS = L.INJ / "events_list.csv"


def load_events(first, last):
    """Date filter FIRST (ET game date), before any candle/result/outcome is read."""
    rows = list(csv.DictReader(open(EVENTS)))
    return [r for r in rows if L.in_split(r["game_date_et"], first, last)]


def open_db():
    return sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)


def load_event_book(conn, ev):
    ms = conn.execute("SELECT ticker, strike, result FROM mkts WHERE event=?", (ev["event"],)).fetchall()
    strikes = {t: s for t, s, _ in ms}
    results = {t: r for t, _, r in ms}
    candles = {}
    for t in strikes:
        rows = conn.execute("SELECT end_ts,bid,ask FROM candles WHERE ticker=? ORDER BY end_ts", (t,)).fetchall()
        if rows:
            e, b, a = zip(*rows)
            candles[t] = (list(e), [np.nan if x is None else x for x in b], [np.nan if x is None else x for x in a])
    return L.Book(strikes, candles, int(ev["T"])), results


def load_final_totals(events):
    """{hoopr_gid: total points} for the given events only (discovery calibration). Read AFTER the split filter."""
    import pandas as pd
    gids = {int(e["hoopr_gid"]) for e in events if e.get("hoopr_gid")}
    df = pd.read_parquet(L.ROOT / "data/raw/nba_open/team_box_2026.parquet", columns=["game_id", "team_score"])
    df = df[df.game_id.isin(gids)]
    g = df.groupby("game_id").team_score.agg(["sum", "count"])
    return {int(i): float(r["sum"]) for i, r in g.iterrows() if r["count"] == 2}


def build_records(first, last, label="", with_extras=True):
    """One record per game in the split. with_extras adds M(T) and the final total (discovery reported-regardless blocks)."""
    events = load_events(first, last)
    finals = load_final_totals(events) if with_extras else {}
    conn = open_db()
    recs = []
    for k, ev in enumerate(events):
        book, results = load_event_book(conn, ev)
        r = {"event": ev["event"], "date": ev["game_date_et"], "T": int(ev["T"])}
        D = book.T - L.D_BEFORE_T_S
        r["M_D"] = book.M(D)
        for w in L.WS:
            mv, _, m0 = L.move_at(book, w)
            r[f"M_Dm{w}h"] = m0
            r[f"move_{w}h"] = mv
        ms = book.main_strike(D)
        r["entry"] = None
        r["skip"] = ""
        if ms is None:
            r["skip"] = "no_main_strike"
        else:
            res = results.get(ms["ticker"])
            if res not in ("yes", "no"):
                r["skip"] = "no_yes_no_result"
            else:
                nets = L.bet_nets(ms["bid"], ms["ask"], res)
                r["entry"] = {**ms, "result": res, **nets}
        if with_extras:
            r["M_T"] = book.M(book.T)
            r["final_total"] = finals.get(int(ev["hoopr_gid"])) if ev.get("hoopr_gid") else None
        recs.append(r)
        if k % 200 == 0:
            print(label, k, len(events), flush=True)
    conn.close()
    return recs


def version_bets(recs, version):
    """List of dicts for one version: one bet per game at the shared main-strike entry."""
    w, X, d = version
    out = []
    for r in recs:
        mv = r[f"move_{w}h"]
        if not L.triggered(mv, X) or r["entry"] is None:
            continue
        side = L.side_for(mv, d)
        e = r["entry"]
        net = e[side]
        if not np.isfinite(net):
            continue
        out.append({"event": r["event"], "date": r["date"], "move": mv, "side": side, "strike": e["strike"], "price": e["price_" + side],
                    "net": net, "net_yes": e["YES"], "net_no": e["NO"]})
    return out


def write_records_csv(recs, path):
    cols = ["event", "date", "T", "M_D", "M_Dm2h", "M_Dm4h", "move_2h", "move_4h", "M_T", "final_total", "skip",
            "ticker", "strike", "bid", "ask", "end", "result", "price_YES", "price_NO", "net_YES", "net_NO"]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in recs:
            e = r["entry"] or {}
            row = [r.get("event"), r.get("date"), r.get("T"), r.get("M_D"), r.get("M_Dm2h"), r.get("M_Dm4h"), r.get("move_2h"), r.get("move_4h"),
                   r.get("M_T"), r.get("final_total"), r.get("skip"), e.get("ticker"), e.get("strike"), e.get("bid"), e.get("ask"), e.get("end"),
                   e.get("result"), e.get("price_YES"), e.get("price_NO"), e.get("YES"), e.get("NO")]
            w.writerow(["" if x is None else x for x in row])
