"""Step C: compare the bracket Kalshi paid with the official NWS daily max/min. NO prices are read anywhere.
Run from repo root: .venv/Scripts/python.exe reports/output/weather_twc/analyze.py"""
import glob, gzip, json, sqlite3, sys
from collections import Counter
from pathlib import Path
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from twc_core import c10_to_f, event_payout_interval, mismatch, parse_cli
from fetch_noaa import STATIONS

CACHE = HERE / "cache"

# ---------------------------------------------------------------- definitions
defs = pd.DataFrame(json.load(gzip.open(CACHE / "market_defs.json.gz", "rt")))
db = sqlite3.connect("file:data/weather.sqlite?mode=ro", uri=True)
ser = pd.read_sql("select ticker series, kind, city, station from wx_series", db)
defs = defs.drop(columns=["station"]).merge(ser, on="series", how="left")
# TWC-era rules name the CLI code only in rules_secondary ("Data for CLINYC ...")
sec = defs.rules_secondary.fillna("").str.extract(r"Data for (CLI[A-Z]{2,4})")[0]
defs["station"] = defs.station.fillna(sec)
defs["station_rules"] = sec

# ---------------------------------------------------------------- 1. switch dates per series
sw = []
for s, g in defs.groupby("series"):
    t, n = g[g.source == "TWC"], g[g.source == "NWS"]
    sw.append(dict(series=s, last_nws_day=n.day.max() if len(n) else None,
                   first_twc_day=t.day.min() if len(t) else None))
sw = pd.DataFrame(sw)
sw = sw[sw.first_twc_day.notna()].sort_values(["first_twc_day", "series"])
bad_order = [r.series for r in sw.itertuples() if isinstance(r.last_nws_day, str) and r.last_nws_day > r.first_twc_day]

# ---------------------------------------------------------------- 2. event payout intervals
defs = defs[defs.day.notna()]
ev_rows = []
for et, g in defs.groupby("event_ticker"):
    r0 = g.iloc[0]
    rec = dict(event=et, series=r0.series, day=r0.day, kind=r0.kind, city=r0.city, cli=r0.station,
               n_mk=len(g), source=r0.source if g.source.nunique() == 1 else "MIXED")
    if r0.station not in STATIONS:
        rec["status"] = "no_US_CLI_station"
    elif rec["source"] not in ("NWS", "TWC"):
        rec["status"] = "source_" + rec["source"]
    elif g.station_rules.notna().any() and (g.station_rules.dropna() != r0.station).any():
        rec["status"] = "station_conflict"
    elif g.strike_type.isna().any():
        rec["status"] = "missing_strike"
    else:
        ms = [dict(strike_type=m.strike_type, floor=m.floor_strike, cap=m.cap_strike, result=m.result)
              for m in g.itertuples()]
        lo, hi, st = event_payout_interval(ms)
        rec.update(lo=lo, hi=hi, status=st)
    ev_rows.append(rec)
ev = pd.DataFrame(ev_rows)

# ---------------------------------------------------------------- 3. NOAA values
ghcn = {}   # (cli, date, 'TMAX'/'TMIN') -> (F, sflag)
cli_by_gid = {v: k for k, v in STATIONS.items()}
stale = {}
for gid, cli in cli_by_gid.items():
    df = pd.read_csv(CACHE / "ghcnd" / f"{gid}.csv.gz", header=None, dtype=str,
                     names=["id", "date", "el", "val", "m", "q", "s", "obs"])
    df = df[df.el.isin(["TMAX", "TMIN"]) & (df.date >= "20210601")]
    last = df.date.max()
    n_q = 0
    for r in df.itertuples():
        if isinstance(r.q, str) and r.q.strip():
            n_q += 1
            continue
        d = f"{r.date[:4]}-{r.date[4:6]}-{r.date[6:]}"
        ghcn[(cli, d, r.el)] = (c10_to_f(int(r.val)), r.s)
    stale[cli] = (last, n_q)

cli_nws = {}   # (cli, date) -> (max, min, issuance) for the latest FINAL report
n_cli_products = 0
for f in glob.glob(str(CACHE / "nws_cli" / "*-*.json")):
    if f.endswith("_list.json"):
        continue
    d = json.load(open(f))
    loc = Path(f).name.split("_")[0]
    p = parse_cli(d.get("productText", ""))
    n_cli_products += 1
    if not p or not p["final"]:
        continue
    k = ("CLI" + loc, p["date"])
    if k not in cli_nws or d["issuanceTime"] > cli_nws[k][2]:
        cli_nws[k] = (p["max"], p["min"], d["issuanceTime"])

xc = Counter(); xc_diff = []
for (cli, dt), (mx, mn, iss) in cli_nws.items():
    for el, v in (("TMAX", mx), ("TMIN", mn)):
        g = ghcn.get((cli, dt, el))
        if g is None or v is None:
            continue
        xc["agree" if g[0] == v else "differ"] += 1
        if g[0] != v:
            xc_diff.append((cli, dt, el, g[0], v))


def nws_value(row):
    el = "TMAX" if row.kind == "high" else "TMIN"
    g = ghcn.get((row.cli, row.day, el))
    if g is not None:
        return g[0], "ghcn", g[1]
    c = cli_nws.get((row.cli, row.day))
    if c is not None:
        v = c[0] if row.kind == "high" else c[1]
        if v is not None:
            return v, "cli", "CLI"
    return None, None, None


ok = ev[ev.status == "ok"].copy()
vals = ok.apply(nws_value, axis=1, result_type="expand")
ok["nws"], ok["nws_src"], ok["sflag"] = vals[0], vals[1], vals[2]
ok_missing = ok[ok.nws.isna()]
ok = ok[ok.nws.notna()].copy()
ok["nws"] = ok.nws.astype(int)
ok["mm"] = [mismatch(r.nws, None if pd.isna(r.lo) else int(r.lo), None if pd.isna(r.hi) else int(r.hi))
            for r in ok.itertuples()]
ok["mis"] = ok.mm != 0
ok["bracket_w"] = [(0 if pd.isna(r.lo) or pd.isna(r.hi) else int(r.hi - r.lo + 1)) for r in ok.itertuples()]
ok["pos"] = ["tail" if r.bracket_w == 0 else ("low_edge" if r.nws == r.lo else ("high_edge" if r.nws == r.hi else "interior"))
             for r in ok.itertuples()]
ok.to_csv(HERE / "events.csv", index=False)

# ---------------------------------------------------------------- 4. exclusions
ev["excl_reason"] = ev.status.where(ev.status != "ok")
ev.loc[ev.event.isin(ok_missing.event), "excl_reason"] = "no_NOAA_value"
excl_tab = ev[ev.excl_reason.notna()].groupby(["source", "excl_reason"]).size().rename("events").reset_index()
# US-station events only (non-US series have no NWS value by construction)
excl_us = ev[ev.excl_reason.notna() & (ev.excl_reason != "no_US_CLI_station")]
excl_us_tab = excl_us.groupby(["source", "excl_reason"]).size().rename("events").reset_index()


def rate(df):
    return dict(n=int(len(df)), mismatches=int(df.mis.sum()), rate=float(df.mis.mean()) if len(df) else None)


res = {"switch": sw.to_dict("records"), "series_with_nws_after_twc": bad_order}
res["n_events_total"] = int(len(ev))
res["n_events_analysed"] = int(len(ok))
res["exclusions_all"] = excl_tab.to_dict("records")
res["exclusions_us_station_series"] = excl_us_tab.to_dict("records")
res["nws_era"] = rate(ok[ok.source == "NWS"])
res["twc_era"] = rate(ok[ok.source == "TWC"])
res["nws_era_by_year"] = {y: rate(g) for y, g in ok[ok.source == "NWS"].groupby(ok.day.str[:4])}
res["by_source_kind"] = {f"{s}/{k}": rate(g) for (s, k), g in ok.groupby(["source", "kind"])}
byc = pd.DataFrame([dict(source=s, cli=c, **rate(g)) for (s, c), g in ok.groupby(["source", "cli"])])
res["by_station"] = byc.to_dict("records")
res["nws_value_source_counts"] = ok.groupby(["source", "nws_src"]).size().rename("n").reset_index().to_dict("records")
res["ghcn_vs_cli_crosscheck"] = dict(xc) | {"differences": xc_diff[:50]}
res["n_cli_products_parsed"] = n_cli_products
res["ghcn_last_date_and_n_qflagged"] = stale
tw = ok[(ok.source == "TWC") & ok.mis]
nw = ok[(ok.source == "NWS") & ok.mis]
cols = ["event", "cli", "kind", "day", "nws", "lo", "hi", "mm", "nws_src", "sflag"]
res["twc_mismatch_size_direction"] = {"by_mm": {str(k): int(v) for k, v in tw.mm.value_counts().sort_index().items()},
                                      "twc_higher": int((tw.mm > 0).sum()), "twc_lower": int((tw.mm < 0).sum())}
res["twc_mismatch_events"] = tw[cols].to_dict("records")
res["nws_mismatch_size_direction"] = {str(k): int(v) for k, v in nw.mm.value_counts().sort_index().items()}
res["nws_mismatch_events_first60"] = nw[cols].head(60).to_dict("records")
T = ok[ok.source == "TWC"]
res["twc_by_nws_value_F_with_mismatch"] = {int(v): rate(g) for v, g in T.groupby("nws") if g.mis.any()}
res["twc_by_date_with_mismatch"] = {d: rate(g) for d, g in T.groupby("day") if g.mis.any()}
res["twc_by_month"] = {m: rate(g) for m, g in T.groupby(T.day.str[:7])}
res["twc_by_bracket_width_0_is_tail"] = {str(k): rate(g) for k, g in T.groupby("bracket_w")}
res["twc_nws_position_in_bracket_matched_events"] = {k: int(v) for k, v in T[~T.mis].pos.value_counts().items()}
res["nws_nws_position_in_bracket_matched_events"] = {k: int(v) for k, v in ok[(ok.source == "NWS") & ~ok.mis].pos.value_counts().items()}
res["ladder_status_counts"] = ev.groupby(["source", "status"]).size().rename("n").reset_index().to_dict("records")
res["latest_event_day_analysed"] = ok.day.max()
json.dump(res, open(HERE / "results.json", "w"), indent=1, default=str)

pd.set_option("display.width", 220, "display.max_rows", 500, "display.max_columns", 50)
print(sw.to_string(index=False)); print("order violations", bad_order)
print("events total", len(ev), "analysed", len(ok))
print(excl_tab.to_string(index=False))
print("NWS era", res["nws_era"], "\nTWC era", res["twc_era"])
print("NWS by year", res["nws_era_by_year"])
print("xcheck", dict(xc), xc_diff[:10]); print("nws_src", res["nws_value_source_counts"])
print("NWS mismatches size", res["nws_mismatch_size_direction"])
print("TWC size/dir", res["twc_mismatch_size_direction"])
print(byc.to_string(index=False))
print(tw[cols].to_string(index=False))
print("TWC by NWS value", res["twc_by_nws_value_F_with_mismatch"]); print("by month", res["twc_by_month"])
print("by width", res["twc_by_bracket_width_0_is_tail"]); print("pos matched", res["twc_nws_position_in_bracket_matched_events"])
print(nw[cols].head(40).to_string(index=False))
print("ghcn last", stale)
