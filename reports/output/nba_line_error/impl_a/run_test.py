"""Research log #57, implementation A: the TEST stage (2022-23 .. 2025-26).  DO NOT RUN without the Opus leak audit.

REFUSES (before any outcome is loaded) unless
  * frozen.json's SHA-256 equals frozen.sha256 and selection did not STOP,
  * results_validate.json exists and says continue = true,
  * AUDIT_OK is JSON {"sha256": {path: hash}} covering every file in lea_lib.AUDIT_FILES with matching hashes,
  * test_started.json and results_test.json do not exist.
Then it writes test_started.json FIRST, and only afterwards loads any test-season outcome.  The test runs once.

Pass bar (pre-registration): (1) side-rate Wilson 95% LB > 110/210 pooled over 2022-26 vs the sportsbook close, n >= 300;
(2) Kalshi 2025-26 (T-1h main strike, taker ask + 1c + fee, 1 contract, held): mean net > 0 and game-bootstrap LB > 0
(10,000 draws, seed 20261022), n >= 150; (3) coin flip: random sides on the same games, 500 copies, seed 20261022: at most 5% reach
the real Kalshi mean."""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lea_lib as L  # noqa: E402
import run_select as rs  # noqa: E402


def check_gates(study: Path, base: Path = HERE, files=L.AUDIT_FILES) -> dict:
    """All refusals, before anything is loaded.  Returns the frozen dict."""
    study = Path(study)
    frozen = L.check_frozen_hash(study)
    sel = study / "results_select.json"
    if not sel.exists() or L.read_json(sel).get("stop"):
        raise SystemExit("REFUSED: results_select.json missing or says STOP.")
    rv = study / "results_validate.json"
    if not rv.exists():
        raise SystemExit("REFUSED: results_validate.json not found (validation has not run).")
    if L.read_json(rv).get("continue") is not True:
        raise SystemExit("REFUSED: results_validate.json does not say continue.")
    L.check_audit_ok(study, base, files)
    if (study / "test_started.json").exists():
        raise SystemExit("REFUSED: test_started.json exists; the test stage already started (runs once).")
    if (study / "results_test.json").exists():
        raise SystemExit("REFUSED: results_test.json exists; the test stage runs once.")
    return frozen


def start_test(study: Path):
    """Written BEFORE any outcome is loaded: a crash cannot silently allow a second run."""
    (Path(study) / "test_started.json").write_text(json.dumps({"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}))


def side_test(frozen: dict, D: pd.DataFrame) -> dict:
    """Pooled and per-season side rates vs the close (pure)."""
    side = L.apply_rule(frozen, D)
    y = D.y.values.astype(float)
    pooled = L.side_stats(side, y)
    per = {s: L.side_stats(side[D.season.values == s], y[D.season.values == s]) for s in sorted(set(D.season))}
    ok = bool(pooled["n"] >= L.MIN_N_TEST and np.isfinite(pooled["lb"]) and pooled["lb"] > L.BREAK_EVEN)
    return {"pooled": pooled, "per_season": per, "pass": ok, "side": side}


def pseudo_projection(K, game_side):
    """Projection that makes grid_bets.bets_for (T = 0) bet exactly the frozen side: K + side (side 0 -> proj == K -> no bet)."""
    return np.asarray(K, float) + np.asarray(game_side, float)


def kalshi_from_bt(bt, side_by_hoopr, hoopr_ids, boot_n=L.N_BOOT, seed=L.SEED, coin_n=500):
    """bt: grid_bets.main_strike_table(...) over games g (same order as hoopr_ids); side_by_hoopr: {hoopr game_id: +1/-1/0}.
    Returns the Kalshi block (n, mean net, bootstrap CI, coin flip)."""
    import grid_bets as gb
    side = np.array([side_by_hoopr.get(int(g), 0.0) for g in hoopr_ids], float)
    proj = pseudo_projection(bt.K.values, side)
    bets = gb.bets_for(proj, bt, 0)
    n = int(len(bets))
    out = {"n": n, "games_with_strike": int(bt.has_strike.sum()), "valid_settled": int(bt.valid.sum())}
    if n < 2:
        out.update(mean_net=float("nan"), boot_ci95=[float("nan")] * 2, coin_flip=None, pass_=False)
        return out
    mean, lo, hi = L.boot_mean(bets.net.values, boot_n, seed)
    coin = gb.coin_flip(bets, bt, n=coin_n, seed=seed)
    out.update(mean_net=float(mean), boot_ci95=[lo, hi], win_rate=float(bets.win.mean()), avg_paid=float(bets.paid.mean()),
               over_under={k: {"n": int(len(x)), "mean_net": float(x.net.mean())} for k, x in bets.groupby("side")}, coin_flip=coin)
    out["pass_"] = bool(n >= L.MIN_N_KALSHI and mean > 0 and lo > 0 and coin["frac_copies_ge_actual"] <= 0.05)
    return out


def verdict(side_block: dict, kalshi_block: dict) -> dict:
    c1 = bool(side_block["pass"])
    c2 = bool(kalshi_block.get("n", 0) >= L.MIN_N_KALSHI and kalshi_block.get("mean_net", -1) > 0 and kalshi_block.get("boot_ci95", [-1])[0] > 0)
    c3 = bool(kalshi_block.get("coin_flip") and kalshi_block["coin_flip"]["frac_copies_ge_actual"] <= 0.05)
    return {"1_side_rate_LB_gt_52.38_n_ge_300": c1, "2_kalshi_mean_pos_boot_LB_gt0_n_ge_150": c2, "3_coin_flip_le_5pct": c3,
            "EDGE": bool(c1 and c2 and c3)}


def kalshi_stage(frozen: dict, D2526: pd.DataFrame) -> dict:
    """#42 / #56 machinery: regular-season 2025-26 Kalshi games with a main strike; the frozen side comes from the game's features
    (hoopR game_id).  Games without a clean sportsbook line have no features and are not bet."""
    sys.path.insert(0, str(L.ROOT / "reports" / "output" / "nba_totals_grid"))
    sys.path.insert(0, str(L.ROOT / "reports" / "output" / "nba_formula_accuracy"))
    import grid_bets as gb
    import grid_run as gr
    games, strikes, info = gr.v2.load_games()
    g, s = gr.v2.attach_outcomes(games, strikes, ["discovery", "unseen"])
    g = g[(g.season == "2025-26") & (g.season_type == 2)].reset_index(drop=True)
    s = s[s.event.isin(g.event)]
    side = L.apply_rule(frozen, D2526)
    side_by = {int(i): float(x) for i, x in zip(D2526.game_id.values, side)}
    bt = gb.main_strike_table(g, s)
    res = kalshi_from_bt(bt, side_by, g.hoopr_id.astype("int64").values)
    res["kalshi_games_regular_season"] = int(len(g))
    res["kalshi_games_with_features"] = int(g.hoopr_id.astype("int64").isin(set(side_by)).sum())
    return res


def main():
    study = L.STUDY
    frozen = check_gates(study)
    start_test(study)                                  # marker first
    D = rs.load_stage(L.TEST)                          # outcomes (y) are loaded only now
    D = D[D.line_source == L.CLOSING_SRC].reset_index(drop=True)          # "vs the close": consensus-close games only
    side_block = side_test(frozen, D)
    d2526 = D[D.season == "2025-26"]
    kal = kalshi_stage(frozen, d2526)
    res = {"version": frozen["version"], "side_vs_close": {k: v for k, v in side_block.items() if k != "side"}, "kalshi_2025_26": kal,
           "verdict": verdict(side_block, kal), "rows": int(len(D))}
    (study / "results_test.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res["verdict"], indent=1))


if __name__ == "__main__":
    main()
