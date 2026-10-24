"""Parse the 2025-26 official injury PDFs that #32's injury_parsed_v3.json does not hold (local files only; no download).
Appends one JSON line per file to injury_parsed_tm.jsonl (resumable).  Parse-only (pdfplumber)."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "reports" / "output" / "nba_model"))
import injury_parse_v3 as P  # noqa: E402

OUT = HERE / "injury_parsed_tm.jsonl"
pc = json.loads((ROOT / "data/raw/nba_injury/_probe_cache_v3.json").read_text())
have = json.loads((ROOT / "reports/output/nba_model/injury_parsed_v3.json").read_text())
done = set()
if OUT.exists():
    for ln in OUT.read_text().splitlines():
        try:
            done.add(json.loads(ln)["name"])
        except Exception:
            pass
todo = sorted(n for n in pc if n >= "Injury-Report_2025-11" and (pc[n] or 0) > 0 and (n not in have or "error" in have[n])
              and n not in done and (ROOT / "data/raw/nba_injury" / n).exists())
print("todo", len(todo), flush=True)
with OUT.open("a") as fh:
    for i, n in enumerate(todo):
        try:
            r = P.parse_any(ROOT / "data/raw/nba_injury" / n)
        except Exception as e:  # noqa: BLE001
            r = {"error": f"{type(e).__name__}: {e}", "rows": [], "nys": []}
        r["name"] = n
        fh.write(json.dumps(r) + "\n")
        fh.flush()
        if i % 50 == 0:
            print(i, len(todo), flush=True)
print("DONE", flush=True)
