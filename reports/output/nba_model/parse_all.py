"""Research log #31: parse every downloaded injury PDF once -> reports/output/nba_model/injury_parsed.json (+ stats).
Run: .venv/Scripts/python.exe reports/output/nba_model/parse_all.py   (parse only; nothing from the files is executed)
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import injury_parse as IP  # noqa: E402

ROOT = HERE.parents[2]
DIR = ROOT / "data" / "raw" / "nba_injury"


def one(name):
    try:
        r = IP.parse_report(DIR / name)
        return name, r
    except Exception as e:  # noqa: BLE001
        return name, {"error": f"{type(e).__name__}: {e}", "header": None, "rows": [], "nys": [], "problems": []}


def main():
    man = json.loads((DIR / "_manifest.json").read_text())
    files = sorted({v["file"] for v in man["games"].values() if v["file"]})
    out = {}
    with ProcessPoolExecutor(max_workers=6) as ex:
        for k, (name, r) in enumerate(ex.map(one, files, chunksize=4)):
            out[name] = r
            if k % 100 == 0:
                print(k, len(files), flush=True)
    (HERE / "injury_parsed.json").write_text(json.dumps(out))
    bad = {n: r["error"] for n, r in out.items() if "error" in r}
    probs = sum(len(r["problems"]) for r in out.values())
    print("files", len(out), "errors", len(bad), "problem items", probs, "rows", sum(len(r["rows"]) for r in out.values()))
    print(list(bad.items())[:5])


if __name__ == "__main__":
    main()
