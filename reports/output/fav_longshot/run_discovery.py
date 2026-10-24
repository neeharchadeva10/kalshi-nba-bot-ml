"""Discovery-only run of research log #47.  Never touches unseen games (load_games filters by split before reading results)."""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fav_longshot as fl  # noqa: E402


def main():
    excl = Counter()
    recs = []
    for s in fl.SPORTS:
        recs += fl.build_quotes(s, "discovery", excl)
    assert all(r["split"] == "discovery" for r in recs)
    bets, stats = fl.version_table(recs, excl)
    assert len(stats) == 30
    best = fl.select_best(stats)
    luck = fl.luck_test(recs, bets, stats)
    games = Counter((r["sport"], r["season"]) for r in recs if r["D"] == "T-1h")
    per_d = Counter((r["sport"], r["D"]) for r in recs)
    res = {"split": "discovery", "n_versions": len(stats),
           "selected_version": fl.vname(best) if best else None,
           "selected_stats": stats[best] if best else None,
           "luck_test": luck,
           "records_per_sport_D": {f"{k[0]}|{k[1]}": v for k, v in sorted(per_d.items())},
           "games_with_T-1h_quotes": {f"{k[0]} {k[1]}": v for k, v in games.items()},
           "skips_and_counts": dict(sorted(excl.items())),
           "min_n_for_selection": 60}
    (fl.OUT / "results_discovery.json").write_text(json.dumps(res, indent=1, default=float))
    rows = []
    for v, s in stats.items():
        rows.append({"version": fl.vname(v), "sport": v[0], "D": v[1], "side": v[2], "band_c": f"{v[3]}-{v[4]}",
                     **{k: (round(x, 5) if isinstance(x, float) else x) for k, x in s.items()},
                     "eligible_n_ge_60": s["n"] >= 60, "selected": v == best})
    fl.write_csv(fl.OUT / "discovery_versions.csv", rows)
    fl.write_csv(fl.OUT / "calibration_discovery.csv", fl.calibration_rows(recs))
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
