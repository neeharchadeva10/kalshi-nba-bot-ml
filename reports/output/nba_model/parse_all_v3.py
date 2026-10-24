"""Research log #32: parse every chosen injury PDF once -> reports/output/nba_model/injury_parsed_v3.json (+ per-season stats).
Run: .venv/Scripts/python.exe reports/output/nba_model/parse_all_v3.py   (parse only; nothing from the files is executed)
"""
import json
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import injury_parse_v3 as P  # noqa: E402

ROOT = HERE.parents[2]
DIR = ROOT / "data" / "raw" / "nba_injury"


def one(name):
    try:
        return name, P.parse_any(DIR / name)
    except Exception as e:  # noqa: BLE001
        return name, {"error": f"{type(e).__name__}: {e}", "header": None, "rows": [], "nys": [], "problems": [], "layout": "?"}


def main():
    man = json.loads((DIR / "_manifest_v3.json").read_text())["games"]
    files = sorted({v["file"] for v in man.values() if v["file"]})
    out = {}
    with ProcessPoolExecutor(max_workers=6) as ex:
        for k, (name, r) in enumerate(ex.map(one, files, chunksize=4)):
            out[name] = r
            if k % 200 == 0:
                print(k, len(files), flush=True)
    (HERE / "injury_parsed_v3.json").write_text(json.dumps(out))
    # per-season parse report (season of a file = season of the games that chose it)
    by_season = defaultdict(set)
    for v in man.values():
        if v["file"]:
            by_season[v["season"]].add(v["file"])
    rep = {}
    for s, fs in sorted(by_season.items()):
        rep[s] = {"files": len(fs), "errors": sum(1 for f in fs if "error" in out[f]),
                  "files_with_problems": sum(1 for f in fs if out[f]["problems"]),
                  "problem_items": sum(len(out[f]["problems"]) for f in fs),
                  "zero_row_files": sum(1 for f in fs if not out[f]["rows"] and not out[f]["nys"]),
                  "layouts": {l: sum(1 for f in fs if out[f]["layout"] == l) for l in ("old", "new", "?")},
                  "rows": sum(len(out[f]["rows"]) for f in fs)}
    (HERE / "injury_parse_report_v3.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))
    bad = {n: r["error"] for n, r in out.items() if "error" in r}
    print("errors", len(bad), list(bad.items())[:5])


if __name__ == "__main__":
    main()
