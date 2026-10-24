"""Research log #44: parse every report in reports_manifest.json with the existing #32 parser (injury_parse_v3.parse_any;
parse only, nothing from the files is executed).  Output: reports_parsed.jsonl, one line per file, resumable, streamed.
Run: .venv/Scripts/python.exe reports/output/nba_injury_speed/parse_reports.py
"""
import json
from concurrent.futures import ProcessPoolExecutor

import common as C
from common import HERE, ROOT

import injury_parse_v3 as P    # reports/output/nba_model

DIR = ROOT / "data" / "raw" / "nba_injury"
OUTF = HERE / "reports_parsed.jsonl"


def one(name):
    try:
        r = P.parse_any(DIR / name)
        return {"name": name, "layout": r.get("layout"), "header": r.get("header"),
                "rows": [(x["date"], x["matchup"], x["team"], x["player"], x["status"]) for x in r["rows"]],
                "nys": [list(x) for x in r["nys"]], "problems": [list(map(str, x)) for x in r["problems"]]}
    except Exception as e:   # noqa: BLE001
        return {"name": name, "error": f"{type(e).__name__}: {e}", "rows": [], "nys": [], "problems": []}


def main():
    man = json.loads((HERE / "reports_manifest.json").read_text())
    have = set()
    if OUTF.exists():
        for ln in OUTF.read_text().splitlines():
            try:
                have.add(json.loads(ln)["name"])
            except Exception:   # noqa: BLE001
                pass
    todo = sorted(n for n in man if n not in have)
    print("to parse", len(todo), "already", len(have), flush=True)
    with open(OUTF, "a") as fh, ProcessPoolExecutor(max_workers=3) as ex:
        for k, r in enumerate(ex.map(one, todo, chunksize=4)):
            fh.write(json.dumps(r) + "\n")
            if k % 200 == 0:
                fh.flush()
                print(k, len(todo), flush=True)
    print("DONE")


if __name__ == "__main__":
    main()
