"""Research log #66: reproduction / integrity gates (pre-registration "Double checks" 2).  Writes ../main_out/gates.json.

Silent about outcomes: prints PASS/FAIL and counts only.  The Kalshi bracket gate runs only when the downloaded Kalshi parquet files exist
(otherwise it records "pending").
Run:  .venv/Scripts/python.exe reports/output/nfl_totals/main/gates.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nfl_lib as N  # noqa: E402
import part_c as C  # noqa: E402


def apts_reference(g: pd.DataFrame, game_id: str, h: float, k: float) -> float:
    """Slow, loop-based re-implementation of Amendment 0.2 for ONE game (a second formula check for the vectorised code).  g = load_games()."""
    row = g[g.game_id == game_id].iloc[0]
    kick, season = row.kick_s, row.season
    played = g[g.scored & (g.kick_s < kick)]

    def team_games(team, s=None):
        d = played[((played.home == team) | (played.away == team))]
        if s is not None:
            d = d[d.season == s]
        d = d.sort_values("kick_s")
        pf = [r.home_score if r.home == team else r.away_score for r in d.itertuples()]
        pa = [r.away_score if r.home == team else r.home_score for r in d.itertuples()]
        return pf, pa

    def ew(vals):
        n = len(vals)
        w = [0.5 ** ((n - 1 - i) / h) for i in range(n)]
        return sum(a * b for a, b in zip(w, vals)) / sum(w)

    prev = played[played.season == season - 1]
    lprev = (prev.home_score.sum() + prev.away_score.sum()) / (2 * len(prev))
    window = g[g.scored & (g.kick_s < kick) & (g.kick_s >= kick - 365 * 86400)]
    L = (window.home_score.sum() + window.away_score.sum()) / (2 * len(window))
    out = {}
    for team, role in ((row.home, "h"), (row.away, "a")):
        pf_c, pa_c = team_games(team, season)
        pf_p, pa_p = team_games(team, season - 1)
        n = len(pf_c)
        res = []
        for cur, prv in ((pf_c, pf_p), (pa_c, pa_p)):
            prior = lprev if not prv else ew(prv) + (lprev - ew(prv)) / 3
            res.append(prior if n == 0 else n / (n + k) * ew(cur) + k / (n + k) * prior)
        out[role] = res
    return out["h"][0] + out["a"][1] + out["a"][0] + out["h"][1] - 2 * L


def gate_totals_sum(g):
    both = g[g.total.notna() & g.home_score.notna() & g.away_score.notna()]
    bad = int((both.total != both.home_score + both.away_score).sum())
    return {"gate": "nflverse total = home_score + away_score", "pass": bad == 0, "n_checked": int(len(both)), "n_mismatch": bad}


def gate_apts_truncation(g, gt, n_games=6, seed=20261027):
    """No prediction uses game g or later: recompute A-PTS for random D games from history truncated at the game (and from the slow
    reference implementation) and compare with the stored game table."""
    rng = np.random.default_rng(seed)
    d = gt[gt.window == "D"]
    pick = d.iloc[rng.choice(len(d), n_games, replace=False)]
    worst_trunc, worst_ref = 0.0, 0.0
    cols = [c for c in gt.columns if c.startswith("apts_")]
    for gid in pick.game_id:
        row = g[g.game_id == gid].iloc[0]
        gt_trunc = g[g.kick_s < row.kick_s]                          # games at/after g are deleted from the history
        tg = N.team_game_table(gt_trunc)
        hist = tg[tg.scored][["team", "season", "kick_s", "pts", "pts_allowed"]]
        sc = gt_trunc[gt_trunc.scored]
        t = N.apts_table(g[g.game_id == gid], hist, sc)
        for c in cols:
            worst_trunc = max(worst_trunc, abs(float(t[c].iloc[0]) - float(gt.set_index("game_id").loc[gid, c])))
        worst_ref = max(worst_ref, abs(apts_reference(g, gid, 8, 4) - float(gt.set_index("game_id").loc[gid, "apts_h8_k4"])),
                        abs(apts_reference(g, gid, 4, 2) - float(gt.set_index("game_id").loc[gid, "apts_h4_k2"])),
                        abs(apts_reference(g, gid, 16, 8) - float(gt.set_index("game_id").loc[gid, "apts_h16_k8"])))
    return {"gate": "A-PTS: truncated-history recompute and slow reference agree with the stored predictions", "pass": bool(worst_trunc < 1e-9 and worst_ref < 1e-9),
            "n_games": n_games, "max_abs_diff_truncated": worst_trunc, "max_abs_diff_reference": worst_ref}


def synthetic_ladder():
    strikes = np.array([38.5, 40.5, 42.5, 44.5, 46.5, 48.5, 50.5])
    bid = np.array([0.90, 0.80, 0.66, 0.47, 0.30, 0.15, 0.06])
    ask = np.array([0.94, 0.84, 0.70, 0.52, 0.34, 0.19, 0.10])
    return strikes, bid, ask


def gate_picker():
    """The T-60 main-strike picker reproduces #42's choice (nba_totals_grid/grid_bets.main_strike_table) on a synthetic ladder, including
    a spread > 10c strike closest to 0.50 and a tie (both are skipped / broken to the lower strike the same way)."""
    strikes, bid, ask = synthetic_ladder()
    cases = [(strikes, bid, ask)]
    b2, a2 = bid.copy(), ask.copy()
    b2[3], a2[3] = 0.40, 0.58                                          # nearest-to-0.5 strike now has a 18c spread -> skipped
    cases.append((strikes, b2, a2))
    b3, a3 = bid.copy(), ask.copy()
    b3[3], a3[3] = 0.44, 0.46                                          # mid 0.45
    b3[2], a2_ = 0.54, 0.56                                            # mid 0.55 -> tie at |0.05|, lower strike (42.5) wins
    a3[2] = a2_
    cases.append((strikes, b3, a3))
    mine = [C.pick_main_strike(*c) for c in cases]
    ref = []
    try:
        sys.path.insert(0, str(N.OUT / "nba_totals_grid"))
        sys.path.insert(0, str(N.OUT / "nba_totals_grid" / ".."))
        import grid_bets as gb  # noqa: WPS433
        for i, (s, b, a) in enumerate(cases):
            s_df = pd.DataFrame({"event": "E", "ticker": [f"T{j}" for j in range(len(s))], "strike": s, "bid": b, "ask": a})
            games = pd.DataFrame({"event": ["E"]})
            bt = gb.main_strike_table(games, s_df)
            ref.append(float(bt.K.iloc[0]))
        source = "grid_bets.main_strike_table (#42)"
    except Exception as e:  # noqa: BLE001
        ref = []
        source = f"#42 import failed ({type(e).__name__}: {e}); fell back to the documented rule"
        for s, b, a in cases:
            q = [(round(abs((bb + aa) / 2 - 0.5), 9), ss) for ss, bb, aa in zip(s, b, a) if aa - bb <= 0.10 + 1e-9]
            ref.append(min(q)[1])
    got = [float(cases[i][0][m]) for i, m in enumerate(mine)]
    return {"gate": "T-60 main-strike picker reproduces #42's choice on synthetic ladders", "pass": bool(got == ref), "mine": got, "ref": ref, "ref_source": source}


def gate_fee():
    return {"gate": "fee: 51c ask -> P 0.52 -> fee $0.0175; 50c ask -> P 0.51 -> 0.0175", "pass": bool(N.kalshi_fee(0.52) == 0.0175 and N.kalshi_fee(0.51) == 0.0175
                                                                                          and abs(N.kalshi_net(0.51, 1) - (1 - 0.52 - 0.0175)) < 1e-12)}


def gate_kalshi_brackets(g):
    """Settlement brackets agree with nflverse finals: for each matched settled game the highest YES strike < total and the lowest NO
    strike >= total.  Runs only when the downloaded markets.parquet exists."""
    f = N.KALSHI_DIR / "markets.parquet"
    if not f.exists():
        return {"gate": "Kalshi settlement brackets agree with nflverse finals", "pass": None, "status": "pending (kalshi/markets.parquet not downloaded yet)"}
    mk = pd.read_parquet(f)
    mk = mk[[c for c in ("ticker", "event_ticker", "series", "floor_strike", "result", "game_id", "match_status") if c in mk.columns]]
    return bracket_check(mk, g)


def bracket_check(mk: pd.DataFrame, g: pd.DataFrame) -> dict:
    mk = mk[C.is_total_series(mk) & (mk.match_status == "matched") & mk.game_id.notna() & mk.result.isin(["yes", "no"])]    # KXNFLTOTAL only (B-1)
    tot = g.drop_duplicates("game_id").set_index("game_id").pipe(lambda d: d.home_score + d.away_score)
    bad, n = [], 0
    for gid, d in mk.groupby("game_id"):
        t = tot.get(gid, np.nan)
        if not np.isfinite(t):
            continue
        n += 1
        yes, no = d[d.result == "yes"].floor_strike, d[d.result == "no"].floor_strike
        if (len(yes) and yes.max() >= t) or (len(no) and no.min() < t):
            bad.append(gid)
    return {"gate": "Kalshi settlement brackets agree with nflverse finals", "pass": len(bad) == 0, "n_games": n, "n_bad": len(bad), "bad": bad[:20]}


KALSHI_OCCURRENCE_OFFSET_H = 3.0


def gate_kalshi_kickoff(g):
    """nflverse kickoff (gameday + gametime ET) vs Kalshi's OWN time field.  markets.occurrence_datetime of KXNFLTOTAL markets equals
    kickoff + 3.0 h in the smoke data (55/55 events carrying it); the gate checks every event that carries occurrence_datetime has
    offset exactly 3.0 h (events without it are counted, not checked)."""
    f = N.KALSHI_DIR / "markets.parquet"
    name = "Kalshi occurrence_datetime - 3h = nflverse kickoff"
    if not f.exists():
        return {"gate": name, "pass": None, "status": "pending (kalshi/markets.parquet not downloaded yet)"}
    mk = pd.read_parquet(f)
    mk = mk[C.is_total_series(mk) & mk.game_id.notna()]
    if "occurrence_datetime" not in mk.columns:
        return {"gate": name, "pass": None, "status": "occurrence_datetime column missing"}
    ev = mk.drop_duplicates("event_ticker")
    occ = (pd.to_datetime(ev.occurrence_datetime, utc=True, errors="coerce") - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
    k = g.drop_duplicates("game_id").set_index("game_id").kick_s
    off = (occ.values - ev.game_id.map(k).values.astype(float)) / 3600.0
    has = np.isfinite(off)
    bad = ev.event_ticker.values[has & (np.abs(off - KALSHI_OCCURRENCE_OFFSET_H) > 1e-9)]
    return {"gate": name, "pass": bool(has.sum() > 0 and len(bad) == 0), "n_events_checked": int(has.sum()), "n_events_without_field": int((~has).sum()),
            "n_bad": int(len(bad)), "bad": list(bad[:20])}


def gate_close_vs_median(g):
    """nflverse close vs the Kalshi implied median M at T-60 and T-24h on HK games (2025-2026 KXNFLTOTAL): median, p95, max |diff|, the
    games with |diff| > 3, and the number of games whose M comes from a crossing involving a strike with spread > 10c.  No outcomes."""
    name = "nflverse close vs Kalshi implied median (T-60, T-24h)"
    if not (N.KALSHI_DIR / "markets.parquet").exists():
        return {"gate": name, "pass": None, "status": "pending (kalshi parquet files not downloaded yet)"}
    kal = C.load_kalshi()
    snaps = C.build_snapshots(kal, g)
    close = g.drop_duplicates("game_id").set_index("game_id").total_line
    out = {"gate": name, "pass": True}                                    # reported diagnostic: Opus decides from the numbers
    for tau, s in snaps.items():
        d = s.drop_duplicates("game_id").set_index("game_id")
        d = d[d.M.notna()]
        diff = (d.M - d.index.map(close)).abs()
        out[tau] = {"n_games": int(len(d)), "median_abs_diff": float(diff.median()) if len(d) else None,
                    "p95_abs_diff": float(diff.quantile(0.95)) if len(d) else None, "max_abs_diff": float(diff.max()) if len(d) else None,
                    "games_over_3": {k: float(v) for k, v in diff[diff > 3].items()}, "n_games_M_from_wide_crossing": int(d.M_wide.sum()),
                    "share_over_3": float((diff > 3).mean()) if len(d) else None}
    return out


def main():
    g = N.load_games()
    gt = pd.read_parquet(N.MAIN_OUT / "game_table.parquet")
    res = [gate_totals_sum(g), gate_apts_truncation(g, gt), gate_picker(), gate_fee(), gate_kalshi_brackets(g), gate_kalshi_kickoff(g), gate_close_vs_median(g)]
    (N.MAIN_OUT / "gates.json").write_text(json.dumps(res, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    for r in res:
        print(r["gate"], "->", {True: "PASS", False: "FAIL", None: r.get("status", "pending")}[r["pass"]], {k: v for k, v in r.items() if k.startswith(("n_", "max_"))})


if __name__ == "__main__":
    main()
