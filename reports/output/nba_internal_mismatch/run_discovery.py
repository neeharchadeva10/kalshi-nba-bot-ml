"""#49 DISCOVERY run only (ET 2025-10-21 .. 2026-01-22): fit k, 16 versions, LB selection (n >= 60), luck test (500 reps, seed 20261022).
Writes results_discovery.json, discovery_versions.csv, discovery_games.csv, k_frozen.json (only if k could be fitted)."""
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mismatch as MM  # noqa: E402
import pipeline as P  # noqa: E402


def main():
    games, skips = P.build_games("discovery")
    ratios = [g["M1H"] / g["Mg"] for g in games if g.get("M1H") is not None and g["Mg"]]
    res = {"split": "discovery", "window": [MM.WIN_FIRST, MM.WIN_LAST], "split_rule": "first n//2 games by scheduled start (amended 2026-10-22)", "n_games_with_game_total_median": len(games),
           "game_skips": skips, "versions": 16}
    k = None
    try:
        k = MM.fit_k(ratios)
        (HERE / "k_frozen.json").write_text(f'{{"k": {k!r}, "n_games": {len(ratios)}, "fitted_on": "discovery"}}')
    except ValueError as e:
        res["k"] = None
        res["k_note"] = f"k could not be fitted: {e}"
    if k is not None:
        res["k"], res["k_n_games"] = k, len(ratios)
    P.set_d1h(games, k) if k is not None else None
    stats, vlist, arr, skipc = P.version_table(games)
    res["version_skips"] = skipc
    res["distributions"] = {"D_team": P.dist([g["D_team"] for g in games]), "D_1H": P.dist([g["D_1H"] for g in games])}
    res["spreads"] = P.spread_report(games)
    res["closer"] = P.closer_stats(games, k)
    sel = MM.select_version(stats)
    res["selected_version"] = MM.vname(sel) if sel else None
    res["eligible_versions"] = [MM.vname(v) for v in vlist if stats[v]["n"] >= MM.MIN_N]
    if sel:
        res["selected_stats"] = stats[sel]
        share, best = MM.luck_test(arr["ny"], arr["nn"], arr["incl"], stats[sel]["lb"])
        res["luck_test"] = {"reps": MM.LUCK_REPS, "seed": MM.SEED, "share_reaching_real_best_lb": share,
                            "limit": 0.05, "verdict": "PASS" if share <= 0.05 else "FAIL"}
    else:
        res["luck_test"] = {"verdict": "NOT RUN", "reason": "no version has n >= 60 on discovery"}
        res["conclusion"] = ("No version is eligible on discovery, so no version can be selected and the unseen run cannot proceed "
                             "under the pre-registration as written.")
    groups = {}
    for gname in sorted({g["stype"] for g in games}):
        sub = [g for g in games if g["stype"] == gname]
        st_g, _, _, _ = P.version_table(sub)
        groups[gname] = {"n_games": len(sub), "versions": {MM.vname(v): {"n": s["n"], "mean": s["mean"]} for v, s in st_g.items()}}
    res["by_season_type"] = groups
    res["season_type_counts"] = {k: sum(1 for g in games if g["stype"] == k) for k in groups}
    P.dump(HERE / "results_discovery.json", res)
    with open(HERE / "discovery_versions.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["version", "n", "mean_net", "sd", "lb", "win_rate", "total_net", "eligible_n_ge_60"])
        for v in vlist:
            s = stats[v]
            w.writerow([MM.vname(v), s["n"], s["mean"], s["sd"], s["lb"], s["win_rate"], s["total_net"], s["n"] >= MM.MIN_N])
    with open(HERE / "discovery_games.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["event", "date", "Mg", "Mh", "Ma", "M1H", "D_team", "D_1H"])
        for g in games:
            w.writerow([g["event"], g["date"], g["Mg"], g["Mh"], g["Ma"], g["M1H"], g["D_team"], g["D_1H"]])
    print(open(HERE / "results_discovery.json").read())


if __name__ == "__main__":
    main()
