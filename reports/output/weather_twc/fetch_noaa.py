"""Step B: official NOAA data. GHCN-Daily by_station files (NCEI) for each CLI site, and NWS CLI products
(api.weather.gov, ~7 days of history). All raw responses cached under cache/."""
import json, re, sys, time
from pathlib import Path
import requests

CACHE = Path(__file__).parent / "cache"
UA = {"User-Agent": "kpm-research (read-only; rmarrig@gmail.com)"}
# CLI code -> (GHCN-Daily id, NWS products location id)
STATIONS = {
    "CLIATL": "USW00013874", "CLIAUS": "USW00013904", "CLIBOS": "USW00014739", "CLIMDW": "USW00014819",
    "CLIDFW": "USW00003927", "CLIDCA": "USW00013743", "CLIDEN": "USW00003017", "CLIEWR": "USW00014734",
    "CLIHOU": "USW00012918", "CLILAX": "USW00023174", "CLILAS": "USW00023169", "CLIMIA": "USW00012839",
    "CLIMSP": "USW00014922", "CLIMSY": "USW00012916", "CLINYC": "USW00094728", "CLIOKC": "USW00013967",
    "CLIPHL": "USW00013739", "CLIPHX": "USW00023183", "CLISAN": "USW00023188", "CLISAT": "USW00012921",
    "CLISDF": "USW00093821", "CLISEA": "USW00024233", "CLISFO": "USW00023234", "CLITTN": "USW00014792",
}

def get(url, path, **kw):
    if path.exists() and path.stat().st_size > 0:
        return path
    for i in range(6):
        r = requests.get(url, headers=UA, timeout=120, **kw)
        if r.status_code == 200:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
            return path
        time.sleep(2 + 3 * i)
    raise RuntimeError(f"{url} -> {r.status_code}")

def main():
    for cli, gid in STATIONS.items():
        get(f"https://www.ncei.noaa.gov/pub/data/ghcn/daily/by_station/{gid}.csv.gz", CACHE / "ghcnd" / f"{gid}.csv.gz")
        loc = cli[3:]
        lst = CACHE / "nws_cli" / f"{loc}_list.json"
        if lst.exists():
            lst.unlink()                      # product lists change daily; refetch (previous copy overwritten)
        get(f"https://api.weather.gov/products/types/CLI/locations/{loc}", lst)
        for p in json.loads(lst.read_text())["@graph"]:
            get(f"https://api.weather.gov/products/{p['id']}", CACHE / "nws_cli" / f"{loc}_{p['id']}.json")
            time.sleep(0.15)
        print(cli, gid, "ok")

if __name__ == "__main__":
    main()
