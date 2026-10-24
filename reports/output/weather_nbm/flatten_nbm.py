"""Flatten the server's nbm_raw.jsonl into data/nbm_station_max.csv and data/nbm_files.csv (run from repo root)."""
import csv, json, sys
src = sys.argv[1] if len(sys.argv) > 1 else "data/nbm_raw.jsonl"
seen = {}
for l in open(src):
    r = json.loads(l)
    seen[(r["day_dir"], r["cycle"], r["target"])] = r          # last record wins (retries)
with open("data/nbm_station_max.csv", "w", newline="") as f, open("data/nbm_files.csv", "w", newline="") as g:
    w = csv.writer(f); v = csv.writer(g)
    w.writerow("station run_date cycle issued_utc target_date fhr valid_end_utc txn xnd nbm_version last_modified_utc".split())
    v.writerow("run_date cycle target_date status last_modified_utc bytes nbm_version n_stations".split())
    for k in sorted(seen):
        r = seen[k]
        st = "missing" if r.get("missing") else "failed" if r.get("failed") else "ok"
        v.writerow([r["day_dir"], r["cycle"], r["target"], st, r.get("last_modified", ""), r.get("bytes", ""),
                    r.get("version", ""), len(r.get("rows", {}))])
        for s, x in sorted(r.get("rows", {}).items()):
            w.writerow([s, r["day_dir"], r["cycle"], r["issued"], r["target"], x["fhr"], x["valid_end"], x["txn"],
                        "" if x["xnd"] is None else x["xnd"], r["version"], r["last_modified"]])
print(len(seen), "files")
