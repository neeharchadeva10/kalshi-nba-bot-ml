"""Research log #56 confirmation stage.  DO NOT RUN without the Opus leak audit.

REFUSES unless frozen.json and AUDIT_OK exist, REFUSES if results_confirm.json exists, and verifies the SHA-256 of
frozen.json against frozen.sha256 before anything is loaded.  AUDIT_OK is created only by the Opus audit.

Seasons: 2024-25 and 2025-26 regular season (decide), 2026-27 to date (descriptive, no sportsbook lines on disk).
Every season refits its own constants on S-1 only (fa_lib.build_world).  Edge criteria are the pre-registered ones:
 1. formula-side rate vs the sportsbook close at the frozen T, pooled 2024-25 + 2025-26: Wilson 95% LB > 52.38%;
 2. Kalshi 2025-26 (frozen rule, T-1h main strike, taker ask + 1c + fee, 1 contract): mean net > 0 and game-bootstrap LB > 0;
 3. at least 150 bets in each of 1 and 2."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fa_lib as fa  # noqa: E402
import grid_lib as gl  # noqa: E402


def season_inputs(S, reader=None, closing=None):
    world = fa.build_world(S, reader)
    games = fa.games_frame(world)
    actual = fa.actuals_frame(world).reindex(games.game_id.values).values.astype(float)
    if closing is None and S != fa.LIVE:
        closing = fa.load_closing()
    line = (closing.reindex(games.game_id.values).values.astype(float) if closing is not None
            else np.full(len(games), np.nan))
    return {"season": S, "world": world, "games": games, "actual": actual, "line": line}


def form_projection(fz, name, S, inp, pm, cache):
    """Raw (before shift) projection of the form's family, then + frozen shift.  cache avoids recomputing a family."""
    f = fz["forms"][name]
    fam = f["family"]
    if fam not in cache:
        if fam == "team":
            cache[fam] = gl.project_one(inp["world"].season, inp["games"], inp["world"].consts, tuple(fz["team"]["spec"]))
        else:
            assert pm is not None, "frozen selection uses the player model but it is not importable"
            v = fz["player"]["version"]
            assert pm.VERSIONS[fz["player"]["version_index"]] == v, "player VERSIONS changed since freezing"
            cache[fam] = np.asarray(pm.project(S, inp["games"][["game_id", "date", "T", "away", "home", "season"]].copy(), dict(v)), float)
    return cache[fam] + f["shift"]


def accuracy_block(proj, actual, line, extra=None):
    """proj/actual/line aligned arrays (any may hold NaN lines).  MAE etc. on all finite games, then formula vs line on the
    games that have both, with the paired bootstrap of (formula MAE - line MAE)."""
    fin = np.isfinite(proj)
    out = {"games": int(fin.sum()), "all_games": fa.mae_rmse_bias(proj[fin], actual[fin]) if fin.any() else None}
    m = fin & np.isfinite(line)
    if m.sum() >= 2:
        d, lo, hi = fa.paired_boot_mae_diff(proj[m], line[m], actual[m])
        out["vs_sportsbook_close"] = {"n": int(m.sum()), "formula": fa.mae_rmse_bias(proj[m], actual[m]),
                                      "close": fa.mae_rmse_bias(line[m], actual[m]),
                                      "mae_diff_formula_minus_close": d, "ci95": [lo, hi]}
    return out


def kalshi_median_block(proj, actual, med):
    m = np.isfinite(proj) & np.isfinite(med) & np.isfinite(actual)
    if m.sum() < 2:
        return None
    d, lo, hi = fa.paired_boot_mae_diff(proj[m], med[m], actual[m])
    return {"n": int(m.sum()), "formula": fa.mae_rmse_bias(proj[m], actual[m]), "kalshi_T-1h_median": fa.mae_rmse_bias(med[m], actual[m]),
            "mae_diff_formula_minus_kalshi": d, "ci95": [lo, hi]}


def kalshi_bets(proj_by_form, T_by_form, bt):
    """Frozen rule on the Kalshi T-1h main strike (grid_bets machinery): per form n, mean net, game-bootstrap CI.
    bt = grid_bets.main_strike_table(...) over the same games as the projections (same order)."""
    import grid_bets as gb
    out = {}
    for name, proj in proj_by_form.items():
        T = T_by_form[name]
        if T is None:
            out[name] = {"n": 0, "note": "no eligible threshold was frozen for this form"}
            continue
        b = gb.bets_for(proj, bt, T)
        if len(b) < 2:
            out[name] = {"n": int(len(b)), "T": T}
            continue
        mean, lo, hi = fa.boot_mean(b.net.values)
        out[name] = {"T": T, "n": int(len(b)), "mean_net": float(b.net.mean()), "boot_ci95": [lo, hi], "lb": lo,
                     "win_rate": float(b.win.mean()), "avg_paid": float(b.paid.mean()),
                     "over_under": {k: {"n": int(len(x)), "mean_net": float(x.net.mean())} for k, x in b.groupby("side")}}
    return out


def edge_verdict(primary, side_pooled, kalshi):
    """The pre-registered criteria for the primary form."""
    sp, kb = side_pooled[primary], kalshi.get(primary, {})
    c1 = bool(sp["n"] > 0 and np.isfinite(sp["lb"]) and sp["lb"] > fa.BREAK_EVEN)
    c2 = bool(kb.get("n", 0) > 0 and kb.get("mean_net", -1) > 0 and kb.get("lb", -1) > 0)
    c3 = bool(sp["n"] >= fa.MIN_N and kb.get("n", 0) >= fa.MIN_N)
    return {"primary": primary, "1_side_rate_LB_gt_52.38pct": c1, "2_kalshi_mean_pos_and_LB_gt0": c2,
            "3_at_least_150_bets_in_each": c3, "EDGE": bool(c1 and c2 and c3)}


def core_confirm(fz, pm, seasons=fa.CONFIRM, live=fa.LIVE, kalshi=None, reader=None, closing=None, decide_seasons=None):
    """kalshi: None or dict(proj_inputs=callable(world)->..., ) is handled by run_confirm.main (needs the Kalshi tables)."""
    t0 = time.time()
    forms = list(fz["forms"])
    Tmap = {k: fz["forms"][k]["T"] for k in forms}
    per, pooled_parts = {}, {k: [] for k in forms}
    for S in list(seasons) + ([live] if live else []):
        inp = season_inputs(S, reader, closing)
        cache = {}
        blk = {"n_games": int(len(inp["games"])), "n_with_line": int(np.isfinite(inp["line"]).sum()),
               "constants": inp["world"].const_note, "forms": {}}
        for k in forms:
            proj = form_projection(fz, k, S, inp, pm, cache)
            b = accuracy_block(proj, inp["actual"], inp["line"])
            T = Tmap[k]
            if T is not None and np.isfinite(inp["line"]).any():
                fin = np.isfinite(proj) & np.isfinite(inp["line"])
                b["side_vs_close"] = fa.side_table(proj[fin], inp["line"][fin], inp["actual"][fin], T)
            blk["forms"][k] = b
            if S in seasons:
                pooled_parts[k].append((proj, inp["actual"], inp["line"]))
        per[S] = blk
        inp_keep = inp if S == "2025-26" else None
        if inp_keep is not None:
            per[S]["_inp"] = inp_keep
    pooled = {}
    side_pooled = {}
    for k in forms:
        proj = np.concatenate([p[0] for p in pooled_parts[k]])
        act = np.concatenate([p[1] for p in pooled_parts[k]])
        line = np.concatenate([p[2] for p in pooled_parts[k]])
        pooled[k] = accuracy_block(proj, act, line)
        T = Tmap[k]
        fin = np.isfinite(proj) & np.isfinite(line)
        side_pooled[k] = (fa.side_table(proj[fin], line[fin], act[fin], T) if T is not None
                          else {"T": None, "n": 0, "lb": float("nan")})
        pooled[k]["side_vs_close"] = side_pooled[k]
    return {"seasons": per, "pooled_confirm_seasons": pooled, "side_pooled": side_pooled, "runtime_s": time.time() - t0}


def kalshi_stage(fz, pm, res_core):
    """Kalshi 2025-26 regular-season games (#42 machinery): MAE vs the T-1h median and the frozen rule's bets."""
    import grid_bets as gb
    import grid_run as gr
    games, strikes, info = gr.v2.load_games()
    g, s = gr.v2.attach_outcomes(games, strikes, ["discovery", "unseen"])
    g = g[(g.season == "2025-26") & (g.season_type == 2)].reset_index(drop=True)
    s = s[s.event.isin(g.event)]
    inp = res_core["seasons"]["2025-26"]["_inp"]
    gdf = pd.DataFrame({"game_id": g.hoopr_id.astype(int).values, "date": pd.to_datetime(g.hoopr_date).values, "T": g["T"].values,
                        "tip": g.tip.values, "away": g.away.values, "home": g.home.values, "season": "2025-26"})
    forms = list(fz["forms"])
    cache, proj = {}, {}
    fake = {"world": inp["world"], "games": gdf}
    for k in forms:
        proj[k] = form_projection(fz, k, "2025-26", fake, pm, cache)
    bt = gb.main_strike_table(g, s)
    bets = kalshi_bets(proj, {k: fz["forms"][k]["T"] for k in forms}, bt)
    actual = g.actual.values.astype(float)
    med = g["median"].values.astype(float)
    mae = {k: kalshi_median_block(proj[k], actual, med) for k in forms}
    return {"games": int(len(g)), "with_main_strike": int(bt.has_strike.sum()), "valid_settled": int(bt.valid.sum()),
            "bets": bets, "mae_vs_kalshi_T-1h_median": mae,
            "note": "regular-season games only (season_type 2) of the #42 Kalshi table, discovery + unseen halves"}


def main():
    fz = fa.check_confirm_gates(HERE)                  # refuses before anything is loaded
    fa.start_confirm(HERE)                             # marker first: a crash cannot allow a silent second run
    pm = fa.load_player_model()
    if fz.get("player"):
        want = fz.get("player_model_sha256")
        if pm is None or (want and fa.sha256_file(HERE / "player_model.py") != want):
            raise SystemExit("REFUSED: player_model.py missing or changed since freezing.")
    res = core_confirm(fz, pm)
    res["kalshi_2025_26"] = kalshi_stage(fz, pm, res)
    res["edge"] = edge_verdict(fz["primary"], res["side_pooled"], res["kalshi_2025_26"]["bets"])
    for blk in res["seasons"].values():
        blk.pop("_inp", None)
    (HERE / "results_confirm.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res["edge"], indent=1))
    print(json.dumps({k: v.get("all_games") for k, v in res["pooled_confirm_seasons"].items()}, indent=1, default=str))


if __name__ == "__main__":
    main()
