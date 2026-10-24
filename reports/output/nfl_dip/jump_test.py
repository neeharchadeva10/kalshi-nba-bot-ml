import sqlite3, json, math, time, os, datetime as dt
import numpy as np, pandas as pd

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
C = 10
N = 240  # grid minutes 0..240 after kickoff
DS = [0.10, 0.15, 0.20]
EXITS = ["E1", "E2"]
con = sqlite3.connect("file:" + os.path.join(ROOT, "data", "nfl_spread.sqlite").replace("\\", "/") + "?mode=ro", uri=True)
mk = pd.read_sql("select mid,ticker,game_id,season,week,kickoff_ts,team,result,settlement_value from sp_markets where kind='win'", con)
info = {"win_markets_total": len(mk)}
sv = mk.settlement_value.copy()
info["settlement_value_null"] = int(sv.isna().sum())
fb = sv.isna() & mk.result.isin(["yes", "no"])
sv[fb] = (mk.result[fb] == "yes").astype(float)
mk["sv"] = sv
info["settlement_from_result_fallback"] = int(fb.sum())
info["dropped_no_settlement"] = int(mk.sv.isna().sum())
mk = mk[mk.sv.notna()]
q = pd.read_sql("select mid,ts,bid_e4,ask_e4 from sp_quotes order by mid,ts", con)
qg = {m: g for m, g in q.groupby("mid")}
info["candles_off_minute_boundary"] = int((q.ts % 60 != 0).sum())
M = {}
nodata = 0
for r in mk.itertuples():
    g = qg.get(r.mid)
    if g is None:
        nodata += 1
        continue
    ts = g.ts.values
    b = g.bid_e4.values.astype(float)
    a = g.ask_e4.values.astype(float)
    grid = r.kickoff_ts + 60 * np.arange(N + 1)
    idx = np.searchsorted(ts, grid, side="right") - 1  # last candle with ts <= grid minute; whole row carried
    ok = idx >= 0
    B = np.full(N + 1, np.nan)
    A = np.full(N + 1, np.nan)
    B[ok] = b[idx[ok]]
    A[ok] = a[idx[ok]]
    M[r.mid] = dict(t=r.ticker, game=r.game_id, season=int(r.season), week=int(r.week), ko=int(r.kickoff_ts),
                    sv=float(r.sv), B=B, A=A, MID=(B + A) / 2)
info["markets_dropped_no_quotes"] = nodata
info["markets_used"] = len(M)
gm = {}
for m, d in M.items():
    gm.setdefault(d["game"], []).append(m)
info["games_per_season"] = {s: len({d["game"] for d in M.values() if d["season"] == s}) for s in (2025, 2026)}
info["markets_per_season"] = {s: sum(1 for d in M.values() if d["season"] == s) for s in (2025, 2026)}
info["weeks_per_season"] = {s: len({d["week"] for d in M.values() if d["season"] == s}) for s in (2025, 2026)}
weeks_by = info["weeks_per_season"]


def fee(P):
    return math.ceil(0.07 * C * P * (1 - P) / 0.0001 - 1e-9) * 0.0001


def entry_ok(d, e):
    a, b = d["A"][e], d["B"][e]
    if np.isnan(a) or np.isnan(b):
        return False
    return not (a < 500 or a > 9500 or a - b > 500)


def trade(d, e, ex):
    a = d["A"][e] / 1e4
    svv = d["sv"]
    f1 = fee(a)
    if ex == "E1":
        proceeds, f2, xm, xp, fell = C * svv, 0.0, None, svv, False
    else:
        x = e + 10
        if x <= N and not np.isnan(d["B"][x]):
            p = d["B"][x] / 1e4
            proceeds, f2, xm, xp, fell = C * p, fee(p), x, p, False
        else:
            proceeds, f2, xm, xp, fell = C * svv, 0.0, None, svv, True
    return proceeds - C * a - f1 - f2, f1 + f2, xm, xp, fell


def iso(d, k):
    return dt.datetime.fromtimestamp(d["ko"] + 60 * k, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


trades = []
for D in DS:
    thr = D * 1e4
    for m, d in M.items():
        MID = d["MID"]
        last = {"E1": -10**9, "E2": -10**9}
        for t in range(5, N):  # entry t+1 must be <= N
            w = MID[t - 5:t]
            if np.all(np.isnan(w)) or np.isnan(MID[t]):
                continue
            ref = np.nanmin(w)
            if MID[t] - ref < thr - 1e-9:
                continue
            e = t + 1
            if not entry_ok(d, e):
                continue
            for ex in EXITS:
                if e < last[ex] + 15:
                    continue
                last[ex] = e
                net, fees, xm, xp, fell = trade(d, e, ex)
                trades.append(dict(D=D, exit=ex, mid=m, ticker=d["t"], game=d["game"], season=d["season"], week=d["week"],
                                   sig_min=t, sig_iso=iso(d, t), ref_mid=ref / 1e4, mid_t=MID[t] / 1e4,
                                   entry_min=e, entry_iso=iso(d, e), entry_bid=d["B"][e] / 1e4, entry_ask=d["A"][e] / 1e4,
                                   exit_min=xm, exit_iso=(iso(d, xm) if xm else "settlement"), exit_px=xp,
                                   fell_back=fell, sv=d["sv"], fees=fees, net=net, fav=bool(MID[0] > 5000)))
tr = pd.DataFrame(trades)
tr.to_csv(os.path.join(HERE, "jump_trades.csv"), index=False)

NET = {m: {ex: np.array([trade(d, e, ex)[0] if entry_ok(d, e) else np.nan for e in range(N + 1)]) for ex in EXITS} for m, d in M.items()}
VALID = {m: np.array([entry_ok(d, e) for e in range(N + 1)]) for m, d in M.items()}

brng = np.random.default_rng(20261019)
crng = np.random.default_rng(20261019)
rows, cf_rows = [], []
for D in DS:
    for ex in EXITS:
        vname = "U%d_%s" % (round(D * 100), ex)
        for per, s in (("discovery", 2025), ("unseen", 2026)):
            x = tr[(tr.D == D) & (tr.exit == ex) & (tr.season == s)]
            n = len(x)
            r = dict(variant=vname, period=per, trades=n, games=int(x.game.nunique()))
            if n:
                gs = x.groupby("game").net.agg(["sum", "count"])
                S, K = gs["sum"].values, gs["count"].values
                ix = brng.integers(0, len(gs), size=(10000, len(gs)))
                bm = S[ix].sum(1) / K[ix].sum(1)
                lo, hi = np.percentile(bm, [2.5, 97.5])
                r.update(win_rate=float((x.net > 0).mean()), mean_net=float(x.net.mean()),
                         mean_cents_per_contract=float(x.net.mean() / C * 100), total_net=float(x.net.sum()),
                         per_week=float(x.net.sum() / weeks_by[s]), ci_lo=float(lo), ci_hi=float(hi),
                         e2_fallbacks=int(x.fell_back.sum()) if ex == "E2" else None)
                kper = x.groupby("game").size().to_dict()
                means = np.empty(500)
                for c in range(500):
                    tot, cnt = 0.0, 0
                    for g, k in kper.items():
                        ms = gm[g]
                        for _ in range(k):
                            for _try in range(50):
                                m = ms[int(crng.integers(len(ms)))]
                                e = int(crng.integers(6, N))  # minutes 6..239
                                if VALID[m][e]:
                                    tot += NET[m][ex][e]
                                    cnt += 1
                                    break
                    means[c] = tot / cnt if cnt else np.nan
                    cf_rows.append(dict(variant=vname, period=per, copy=c, mean_net=means[c]))
                sm = x.net.mean()
                r.update(cf_median=float(np.nanmedian(means)), cf_p5=float(np.nanpercentile(means, 5)),
                         cf_p95=float(np.nanpercentile(means, 95)), cf_share_ge=float(np.mean(means >= sm)))
            rows.append(r)
R = pd.DataFrame(rows)
verdict = {}
for v in R.variant.unique():
    a = R[(R.variant == v) & (R.period == "discovery")].iloc[0]
    u = R[(R.variant == v) & (R.period == "unseen")].iloc[0]
    ok = bool(a.trades > 0 and u.trades >= 50 and a.mean_net > 0 and u.mean_net > 0 and u.ci_lo > 0 and u.cf_share_ge <= 0.05)
    verdict[v] = "PASS" if ok else "FAIL"
pd.DataFrame(cf_rows).to_csv(os.path.join(HERE, "jump_coinflip.csv"), index=False)

cols = ["ticker", "D", "exit", "sig_iso", "ref_mid", "mid_t", "entry_iso", "entry_bid", "entry_ask", "exit_iso", "exit_px", "fees", "net"]
sample = tr.sample(5, random_state=7)
san = dict(sample_trades=json.loads(sample[cols].to_json(orient="records")),
           avg_entry_price={"U%d_%s" % (round(D * 100), ex): float(tr[(tr.D == D) & (tr.exit == ex)].entry_ask.mean())
                            for D in DS for ex in EXITS},
           favorite_share={"U%d_%s" % (round(D * 100), ex): float(tr[(tr.D == D) & (tr.exit == ex)].fav.mean())
                           for D in DS for ex in EXITS})
rt = time.time() - T0
json.dump(dict(info=info, sanity=san, results=json.loads(R.to_json(orient="records")), verdict=verdict, runtime_s=rt),
          open(os.path.join(HERE, "jump_results.json"), "w"), indent=1)
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 30)
print(json.dumps(info, indent=1))
print(R.round(4).to_string())
print(verdict)
print(json.dumps(san, indent=1))
print("runtime", rt)
