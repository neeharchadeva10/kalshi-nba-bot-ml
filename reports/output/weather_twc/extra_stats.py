"""Adds Wilson intervals and a Fisher exact test (NWS-era control vs TWC era) to results.json."""
import json
from math import comb, sqrt
from pathlib import Path
P = Path(__file__).parent / "results.json"
r = json.load(open(P))

def fisher(a, b, c, d):
    n = a + b + c + d; r1 = a + b; c1 = a + c
    p = lambda x: comb(r1, x) * comb(n - r1, c1 - x) / comb(n, c1)
    p0 = p(a)
    return sum(p(x) for x in range(max(0, c1 - (n - r1)), min(r1, c1) + 1) if p(x) <= p0 * (1 + 1e-9))

def wilson(k, n, z=1.96):
    ph = k / n; den = 1 + z * z / n; c = (ph + z * z / (2 * n)) / den
    h = z * sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return [c - h, c + h]

n, t = r["nws_era"], r["twc_era"]
hou = [e for e in r["nws_mismatch_events_first60"] if e["cli"] == "CLIHOU"]
r["stats"] = {
    "nws_era_wilson95": wilson(n["mismatches"], n["n"]),
    "twc_era_wilson95": wilson(t["mismatches"], t["n"]),
    "fisher_p_nws_vs_twc": fisher(n["mismatches"], n["n"] - n["mismatches"], t["mismatches"], t["n"] - t["mismatches"]),
    "nws_era_excluding_HOU_jul2026_cluster": {"n": n["n"] - len(hou), "mismatches": n["mismatches"] - len(hou),
                                              "wilson95": wilson(n["mismatches"] - len(hou), n["n"] - len(hou))},
    "note": "GHCN source flag '1' = NWS CF6 monthly form, not the CLI itself; CF6-vs-CLI differences are the likely "
            "source of the NWS-era control mismatches",
}
json.dump(r, open(P, "w"), indent=1)
print(r["stats"])
