"""Research log #66: the HOLDOUT stage (Part A on H1/H2/H3/HK, Part B on D/H3/HK + second looks, Part C on HK-sel -> HK-test).
DO NOT RUN without the Opus audit.

REFUSES (before any outcome is loaded) unless reports/output/nfl_totals/AUDIT_OK holds matching SHA-256 hashes for every file in
nfl_lib.AUDIT_FILES AND reports/output/nfl_totals/test_started.json exists, and results_test.json does not exist (runs once).
Part A is skipped if the D-selected version has D mean P&L <= 0 (STOP) ; Part C is skipped if the HK-sel winner's mean net <= 0."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nfl_lib as N  # noqa: E402
import part_a as A  # noqa: E402
import part_b as B  # noqa: E402
import part_c as C  # noqa: E402


SECONDARY_VERSION = "A-PTS|h8|k8|G4.5"          # Amendment 5: best D version among families with no fitted residual-vs-close model


def _default(o):
    return o.item() if hasattr(o, "item") else str(o)


def check_gates(study=N.STUDY, base=HERE, files=N.AUDIT_FILES) -> dict:
    N.check_audit_ok(study, base, files)
    sel = Path(study) / "results_select.json"
    if not sel.exists():
        raise SystemExit("REFUSED: results_select.json not found (run run_select.py first).")
    return json.loads(sel.read_text())


def run_stage(g: pd.DataFrame, gt: pd.DataFrame, wf: pd.DataFrame, st: pd.DataFrame, kal: dict, sel: dict, fair: dict) -> dict:
    """The whole holdout computation on already-loaded inputs (so unit tests can run it on synthetic data).
    g: games (game_id, season, kick_s, et_date, home_score, away_score); gt: the game table (predictions, no outcomes)."""
    allseasons = sorted(gt.season.unique())
    gto = N.attach_outcomes(gt, g, allseasons)
    totals = (g.drop_duplicates("game_id").set_index("game_id").home_score + g.drop_duplicates("game_id").set_index("game_id").away_score)
    if kal is not None:
        snaps = C.build_snapshots(kal, g)
        hk_main = C.main_strike_table(snaps["T60"])
        hk = C.with_outcomes(hk_main, totals)
    else:                                                        # Kalshi inputs not downloaded yet (rehearsal only)
        snaps = None
        hk_main = hk = pd.DataFrame({"game_id": pd.Series([], dtype=str), **{c: pd.Series([], dtype=float) for c in
                                     ("M", "K", "bid", "ask", "total", "y", "net_over", "net_under")}})
    kc = dict(kal.get("counts", {})) if kal is not None else {}
    if kal is not None:
        kc.update(C.restrict_hk(kal["markets"], g)[1])
    out = {"kalshi_counts": kc, "n_hk_games_t60_usable": int(len(hk_main)), "n_hk_games_settled": int(len(hk))}

    # ---- Part A (chosen) and the Amendment 5 secondary candidate: same computations, same verdict rules
    def part_a(vid, blk):
        if blk is not None and (blk["chosen"] is None or blk["stop"]):
            return {"status": "STOPPED (no eligible version or chosen D mean P&L <= 0); no holdout run", "chosen": blk["chosen"]}
        frames = {w: gto[(gto.window == w) & gto.total.notna()] for w in ("H1", "H2", "H3")}
        frames["HK_pred"] = gto
        res = A.evaluate_version(vid, frames, hk)
        return {"version": vid, "result": res, "verdict": A.verdict(res)}

    blk = sel["parts"]["A"]
    out["A"] = part_a(blk["chosen"]["version"], blk) if blk["chosen"] is not None else part_a(None, blk)
    if blk["chosen"] is not None and not blk["stop"]:
        out["A"]["chosen"] = blk["chosen"]
    out["A_secondary"] = part_a(SECONDARY_VERSION, None)

    # ---- Part B
    f = B.b_frame(gto, wf, st)
    out["B"] = {}
    for r in B.RULES:
        res = B.evaluate_rule(r, f, hk)
        out["B"][r] = {"result": res, "verdict": B.verdict(res)}

    # ---- Part C: selection on HK-sel, one test on HK-test
    if kal is None:
        out["C"] = {"status": "inputs missing (Kalshi parquet files not downloaded yet)"}
        return out
    tabs = [C.prepare_c_table(snaps[t], fair, totals) for t in ("T24h", "T60")]
    tab = pd.concat(tabs, ignore_index=True)
    split = C.hk_split(tab.game_id.values, g)
    tab["hk"] = split.values
    sel_tab, test_tab = tab[tab.hk == "HK-sel"], tab[tab.hk == "HK-test"]
    rows = pd.DataFrame([C.version_row_c(sel_tab, v) for v in C.all_versions_c()])
    best = N.choose_best(rows, N.MIN_BETS_C)
    c_out = {"rows_hk_sel": rows.to_dict(orient="records"), "chosen": None if best is None else best.to_dict()}
    if best is None or not best.mean_pnl > 0:
        c_out["status"] = "STOPPED (no version with >= 100 bets on HK-sel, or chosen mean net <= 0); HK-test not run"
    else:
        res = C.evaluate_c(test_tab, best.version)
        c_out.update(result=res, verdict=C.verdict_c(res))
    out["C"] = c_out
    return out


FORBIDDEN_KEY_PARTS = ("mean", "win", "loss", "push", "pnl", "net", "boot", "lo", "hi", "frac", "rate", "total", "verdict", "label",
                       "hindsight", "chosen", "edge", "criteria", "version", "sum")
COUNT_KEYS = ("n", "n_bets", "n_games", "n_over", "n_under", "copies", "n_hk_games_t60_usable", "n_hk_games_settled",
              "n_games_total_series_matched", "n_games_dropped_unsettled", "n_games_before_season_filter", "n_games_hk_seasons_2025_2026")


def counts_only(x):
    """Keep only count keys of a nested result; everything else (means, win rates, CIs, verdicts, version names) is dropped."""
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            if k in COUNT_KEYS and isinstance(v, (int, float, np.integer)):
                out[k] = int(v)
            elif k in ("status",) and isinstance(v, str):
                out[k] = v.split(";")[0][:60]
            elif isinstance(v, (dict, list)):
                c = counts_only(v)
                if c not in ({}, []):
                    out["chosen_counts" if k == "chosen" else k] = c
        return out
    if isinstance(x, list):
        return [c for c in (counts_only(v) for v in x) if c not in ({}, [])]
    return {}


def assert_no_pnl_keys(d):
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in COUNT_KEYS or k in ("status", "A", "B", "C", "rehearsal_note") or k.startswith(("B", "H", "HK", "D", "W", "T", "2")) or k in (
                        "per_window_sb", "pooled_sb", "clean_pooled_sb", "luck", "kalshi", "result", "obs_ge_fc", "obs_lt_fc", "rows_hk_sel",
                        "hindsight_all_forecast_era", "hindsight_h3", "second_look_2021_25", "chosen_counts", "A_secondary", "controls", "side_mix", "always_over", "always_under", "luck_side_preserving", "kalshi_counts"):
                    walk(v)
                else:
                    raise AssertionError(f"unexpected key in rehearsal output: {k}")
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(d)
    flat = json.dumps(d)
    for bad in ("mean", "pnl", "wins", "losses", "pushes", "win_rate", "boot_lo", "boot_hi", "frac_copies", "mean_net", "verdict"):
        assert bad not in flat, bad


def rehearsal(n_draws: int = 50):
    """Runs every holdout code path (small bootstrap / luck n) and writes ONLY counts to main_out/rehearsal_counts.json.  Bypasses the
    AUDIT_OK / test_started guard (this mode only).  A stand-in Part A version is used (run_select has not been run)."""
    from functools import partial
    N.boot_ci = partial(N.boot_ci, n_boot=n_draws)
    N.boot_ci_by_game = partial(N.boot_ci_by_game, n_boot=n_draws)
    N.luck_random_sides = partial(N.luck_random_sides, n=n_draws)
    N.luck_random_games = partial(N.luck_random_games, n=n_draws)
    N.luck_random_strikes = partial(N.luck_random_strikes, n=n_draws)
    N.luck_side_preserving = partial(N.luck_side_preserving, n=n_draws)
    g = N.load_games()
    gt = pd.read_parquet(N.MAIN_OUT / "game_table.parquet")
    notes = []
    try:
        wf, st = B.load_weather()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"Rehearsal: weather inputs missing or unreadable ({type(e).__name__}); nothing run.")
    try:
        kal = C.load_kalshi()
    except FileNotFoundError:
        kal = None
        notes.append("Kalshi parquet files not downloaded yet: HK (Part A/B Kalshi legs) and Part C skipped (inputs missing)")
        print("Rehearsal: Kalshi inputs missing; Kalshi legs skipped.")
    fair = C.load_fair(N.MAIN_OUT / "fair_model.npz")
    sp = N.STUDY / "results_select.json"
    sel = json.loads(sp.read_text()) if sp.exists() else {"parts": {"A": {"chosen": {"version": "A-PTS|h8|k4|G3"}, "stop": False}}}
    out = counts_only(run_stage(g, gt, wf, st, kal, sel, fair))
    out["rehearsal_note"] = "; ".join(notes + [f"Part A version from results_select.json (stand-in if absent), {n_draws} bootstrap/luck draws, counts only"])
    assert_no_pnl_keys(out)
    (N.MAIN_OUT / "rehearsal_counts.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


def main():
    if "--rehearsal-counts" in sys.argv:
        if (N.STUDY / "AUDIT_OK").exists():
            raise SystemExit("REFUSED: AUDIT_OK exists; the rehearsal path is closed once the audit has passed.")
        return rehearsal()
    sel = check_gates()                                          # refuses BEFORE any outcome is loaded
    g = N.load_games()
    gt = pd.read_parquet(N.MAIN_OUT / "game_table.parquet")
    wf, st = B.load_weather()
    kal = C.load_kalshi()
    fair = C.load_fair(N.MAIN_OUT / "fair_model.npz")
    out = run_stage(g, gt, wf, st, kal, sel, fair)
    parts = {k: out.pop(k) for k in ("A", "A_secondary", "B", "C")}
    out["parts"] = parts
    (N.STUDY / "results_test.json").write_text(json.dumps(out, indent=1, default=_default))
    out.update(parts)
    print(json.dumps({"A": out["A"].get("verdict", out["A"].get("status")), "A_secondary": out["A_secondary"].get("verdict"),
                      "B": {r: v["verdict"]["verdict"] for r, v in out["B"].items()},
                      "C": out["C"].get("verdict", out["C"].get("status"))}, indent=1, default=_default))


if __name__ == "__main__":
    main()
