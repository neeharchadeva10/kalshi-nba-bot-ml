"""Pre-registration report over lockwatch logs (see PREREGISTRATION.md). Offline, read-only.

  python lock_report.py --logs FILE [FILE...] [--reaction-ms 8]

De-duplication: locks sharing any leg that open within 50 ms of each other are one event, valued at its most profitable
lock (max_profit, fees included). Reachable: that lock lived >= R + 2 ms. Tolerates old logs (no ns/ts_ms/is_prop).
Writes lock_report_<first log stem>.json next to the first log.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lockcore as lc  # noqa: E402


def read_logs(paths):
    locks, samples, sntp, nights, files = [], [], [], set(), []
    read_logs.proc = []
    read_logs.backlog = []
    for p in paths:
        n0 = len(locks)
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                ev = r.get("event")
                if ev == "lock_close":
                    n = lc.normalize_close(r)
                    if n:
                        locks.append(n)
                elif ev == "delay" and r.get("recv_ns") and r.get("ts_ms") is not None:
                    samples.append((r["recv_ns"], r["ts_ms"]))
                    if r.get("proc_us") is not None:
                        read_logs.proc.append(r["proc_us"])
                    if r.get("backlog") is not None:
                        read_logs.backlog.append(r["backlog"])
                elif ev == "sntp" and r.get("offset_ms") is not None:
                    sntp.append((r.get("recv_ns", 0), r["offset_ms"]))
                elif ev in ("start", "heartbeat") and r.get("date"):
                    nights.add(r["date"])
        files.append({"file": str(p), "lock_closes": len(locks) - n0})
    nights |= {x["night"] for x in locks}
    return locks, samples, sntp, nights, files


def scope_summary(locks, nights, R):
    ev = lc.dedupe(locks)
    reach = lc.reachable(ev, R)
    return {
        "raw_locks": len(locks), "events": len(ev), "reachable_events": len(reach),
        "raw_value": round(sum(x["profit"] for x in locks), 4),
        "event_value": round(sum(e["profit"] for e in ev), 4),
        "reachable_value": round(sum(e["profit"] for e in reach), 4),
        "reachable_value_by_night": {k: round(v, 4) for k, v in sorted(lc.value_by_night(reach, nights).items())},
        "mean_per_night": round(lc.mean_value_per_night(reach, nights), 4),
        "mean_per_night_excl_biggest": round(lc.mean_value_per_night(reach, nights, drop_biggest=True), 4),
        "biggest_event": (None if not reach else {k: (max(reach, key=lambda e: e["profit"]))[k]
                                                  for k in ("game", "kind", "profit", "life_ms", "mtype")}),
    }, ev


def breakdown(events, key, R):
    out = {}
    for e in events:
        k = key(e)
        d = out.setdefault(k, {"events": 0, "event_value": 0.0, "reachable_events": 0, "reachable_value": 0.0})
        d["events"] += 1
        d["event_value"] += e["profit"]
        if e["life_ms"] >= R + 2.0:
            d["reachable_events"] += 1
            d["reachable_value"] += e["profit"]
    return {k: {a: round(b, 4) if isinstance(b, float) else b for a, b in v.items()} for k, v in sorted(out.items())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--reaction-ms", type=float, default=8.0)
    a = ap.parse_args()
    R = a.reaction_ms
    locks, samples, sntp, nights, files = read_logs(a.logs)
    all_s, all_ev = scope_summary(locks, nights, R)
    np_locks = [x for x in locks if not x["is_prop"]]
    np_s, np_ev = scope_summary(np_locks, nights, R)
    res = {"reaction_ms": R, "reachable_threshold_ms": R + 2.0, "nights": sorted(nights), "files": files,
           "with_props": all_s, "without_props": np_s,
           "by_market_type_with_props": breakdown(all_ev, lambda e: e["mtype"], R),
           "by_lifetime_bucket_with_props": breakdown(all_ev, lambda e: lc.lifetime_bucket(e["life_ms"]), R),
           "by_lifetime_bucket_without_props": breakdown(np_ev, lambda e: lc.lifetime_bucket(e["life_ms"]), R),
           "feed_delay_ms": lc.delay_stats(samples, sntp),
           "sntp_offsets_ms": [round(o, 2) for _, o in sntp]}
    out = Path(a.logs[0]).with_name("lock_report_" + Path(a.logs[0]).stem + ".json")
    out.write_text(json.dumps(res, indent=1), encoding="utf-8")

    print(f"R = {R:g} ms  (reachable = lifetime >= {R + 2:g} ms)   nights: {', '.join(sorted(nights)) or '-'}")
    hdr = f"{'scope':<14}{'raw':>6}{'events':>8}{'reach':>7}{'event $':>10}{'reach $':>10}{'mean/night':>12}{'excl biggest':>14}"
    print(hdr)
    for nm, s in (("with props", all_s), ("without props", np_s)):
        print(f"{nm:<14}{s['raw_locks']:>6}{s['events']:>8}{s['reachable_events']:>7}{s['event_value']:>10.2f}"
              f"{s['reachable_value']:>10.2f}{s['mean_per_night']:>12.2f}{s['mean_per_night_excl_biggest']:>14.2f}")
    print("\nBy market type (de-duplicated events, with props):")
    print(f"  {'type':<18}{'events':>7}{'event $':>10}{'reach':>7}{'reach $':>10}")
    for k, v in res["by_market_type_with_props"].items():
        print(f"  {k:<18}{v['events']:>7}{v['event_value']:>10.2f}{v['reachable_events']:>7}{v['reachable_value']:>10.2f}")
    print("\nBy lifetime bucket (with props):")
    print(f"  {'bucket':<14}{'events':>7}{'event $':>10}{'reach':>7}{'reach $':>10}")
    for k, v in res["by_lifetime_bucket_with_props"].items():
        print(f"  {k:<14}{v['events']:>7}{v['event_value']:>10.2f}{v['reachable_events']:>7}{v['reachable_value']:>10.2f}")
    d = res["feed_delay_ms"]
    if d.get("n"):
        print(f"\nFeed delay (n={d['n']}, SNTP-corrected={d['offset_corrected']}): median {d['median_ms']} ms, "
              f"p90 {d['p90_ms']} ms, p99 {d['p99_ms']} ms")
    else:
        print("\nFeed delay: no samples in these logs")
    pu = getattr(read_logs, "proc", [])
    if pu:
        res["proc_us"] = {"n": len(pu), "median": round(lc.pct(pu, 50), 1), "p90": round(lc.pct(pu, 90), 1),
                          "p99": round(lc.pct(pu, 99), 1), "max": max(pu)}
        bl = getattr(read_logs, "backlog", [])
        print(f"Processing time (n={len(pu)}): median {res['proc_us']['median']} us, p90 {res['proc_us']['p90']} us, "
              f"p99 {res['proc_us']['p99']} us, max {max(pu)} us"
              + (f"; backlog max {max(bl)}, p99 {lc.pct(bl, 99)}" if bl else ""))
    if all_s["biggest_event"]:
        print(f"Biggest reachable event: {all_s['biggest_event']}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
