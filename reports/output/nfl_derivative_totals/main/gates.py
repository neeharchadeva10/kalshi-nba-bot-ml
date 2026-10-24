"""Research log #68: integrity gates (pre-registration "Double checks").  Writes ../main_out/gates.json.  Silent about bet outcomes.

Part 1 (runs now): spread sign convention on 10 NAMED games -- prints each game, home/away, nflverse closing spread_line (pre-game;
  positive = home favoured), and S (implied median home margin at T-60 and T-24h) so a human can verify "home favourite -> S > 0".
  Also counts-only: games with a computable S per season / tau (prices only).
Part 2 (needs the Kalshi download): per-series settlement bracket gate (Kalshi results vs nflverse finals / pbp halves; data integrity
  only), team-identification counts for KXNFLTEAMTOTAL, and usable-ladder counts per series / season / tau.  'pending' if absent.

Run:  .venv/Scripts/python.exe reports/output/nfl_derivative_totals/main/gates.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ldr_lib as L  # noqa: E402

# Chosen by a fixed rule from games.csv spread_line (a pre-game line, not an outcome) among games in the spread sqlite before 2026-01-01:
# the first five (sorted by game_id) with spread_line >= 7 (home big favourite) and the first five with spread_line <= -4 (away favourite)
# AND a computable S at T-60 (2025_01_CIN_CLE was skipped: only one strike had a spread <= 10c, so no median).
NAMED_SIGN_GAMES = (
    "2025_01_DAL_PHI", "2025_01_TEN_DEN", "2025_02_CAR_ARI", "2025_02_CLE_BAL", "2025_03_MIA_BUF",      # home favourites
    "2025_01_ARI_NO", "2025_02_BUF_NYJ", "2025_02_LA_TEN", "2025_03_ATL_CAR", "2025_03_GB_CLE",        # away favourites
)


def sign_check(games: pd.DataFrame, game_ids=NAMED_SIGN_GAMES, snaps=None) -> list:
    """For each named game: home, away, spread_line, S at each tau, and whether sign(S) == sign(spread_line)."""
    snaps = snaps or L.load_spread_snapshots(games, game_ids)
    g = games.drop_duplicates("game_id").set_index("game_id")
    rows = []
    for gid in game_ids:
        r = {"game_id": gid, "home": g.home[gid], "away": g.away[gid], "spread_line": float(g.spread_line[gid])}
        for tau in L.TAUS:
            S = L.spread_S(snaps[tau][snaps[tau].game_id == gid], games)
            r[f"S_{tau}"] = float(S.get(gid, np.nan))
        s = r["S_T60"] if np.isfinite(r["S_T60"]) else r["S_T24h"]
        r["sign_agrees"] = bool(np.isfinite(s) and np.sign(s) == np.sign(r["spread_line"]))
        rows.append(r)
    return rows


def spread_counts(games: pd.DataFrame, snaps: dict) -> list:
    """Counts only: spread games (in the sqlite, nflverse-matched) with a computable S, per season / tau."""
    g = games.drop_duplicates("game_id").set_index("game_id")
    out = []
    for tau, s in snaps.items():
        S = L.spread_S(s, games)
        ids = sorted(set(s.game_id))
        per = pd.Series(ids).map(g.season)
        n_all = g.loc[[i for i in ids if i in g.index]].groupby("season").size() if ids else pd.Series(dtype=int)
        for season, n in n_all.items():
            sub = [i for i in ids if i in g.index and g.season[i] == season]
            out.append({"tau": tau, "season": int(season), "games_with_two_sided_spread_quotes": int(n),
                        "games_with_S": int(sum(np.isfinite(S.get(i, np.nan)) for i in sub))})
    return out


def kalshi_gates(games: pd.DataFrame, r1: float) -> dict:
    kd = L.KALSHI_DIR
    if not (kd / "markets.parquet").exists() or not (kd / "candles_1m.parquet").exists():
        return {"status": "pending: Kalshi download not present"}
    tabs, kal = L.price_tables(games, r1)
    mk = kal["markets"]
    ids = set(mk.game_id)
    act = L.settle_actuals(games, ids)                 # finals + pbp halves; used ONLY for the data-integrity bracket check
    res = {"status": "run", "series_prefix": {}, "bracket": {}, "team_id": {}, "usable_counts": []}
    for key, ser in L.SERIES.items():
        m = mk[mk.skey == key]
        res["series_prefix"][ser] = {"n_markets": int(len(m)), "n_event_prefix_ok": int(m.event_ticker.astype(str).str.startswith(ser + "-").sum())}
    tt = mk[mk.skey == "tt"]
    tagged = L.tag_ladders(tt, "tt", games)
    res["team_id"] = {"n_tt_markets": int(len(tt)), "n_team_resolved": int(len(tagged)), "n_unresolved": int(len(tt) - len(tagged)),
                      "n_games_with_both_sides": int((tagged.groupby("game_id").ladder.nunique() == 2).sum())}
    parts = []
    for key in L.SERIES:
        m = L.tag_ladders(mk[mk.skey == key], key, games)
        parts.append(m)
    allm = pd.concat(parts, ignore_index=True)
    b = L.bracket_gate(allm, act)
    for (key, lad), d in b.groupby(["skey", "ladder"]):
        res["bracket"][f"{key}/{lad}"] = {"n_ladders": int(len(d)), "n_actual_missing": int(d.ok.isna().sum()),
                                           "n_ok": int((d.ok == True).sum()), "n_bad": int((d.ok == False).sum())}  # noqa: E712
        bad = d[d.ok == False].game_id.tolist()                                                                    # noqa: E712
        if bad:
            res["bracket"][f"{key}/{lad}"]["bad_games_first10"] = bad[:10]
    uc = L.usable_counts(tabs["dec"], tabs["inputs"], games)
    res["usable_counts"] = uc.to_dict(orient="records")
    return res


def main():
    games = L.N.load_games()
    out = {}
    rows = sign_check(games)
    out["sign_check"] = rows
    print("Spread sign convention: S = implied median HOME margin; home favourite -> S > 0")
    print(f"{'game_id':18s} {'home':5s} {'away':5s} {'spread_line':>11s} {'S_T60':>8s} {'S_T24h':>8s} agrees")
    for r in rows:
        print(f"{r['game_id']:18s} {r['home']:5s} {r['away']:5s} {r['spread_line']:11.1f} {r['S_T60']:8.2f} {r['S_T24h']:8.2f} {r['sign_agrees']}")
    out["sign_check_all_agree"] = bool(all(r["sign_agrees"] for r in rows))
    print("sign check all agree:", out["sign_check_all_agree"])
    ids = pd.read_sql if False else None
    import sqlite3
    con = sqlite3.connect(f"file:{L.SPREAD_DB.as_posix()}?mode=ro", uri=True)
    sp_ids = pd.read_sql("select distinct game_id from sp_markets where kind='spread'", con).game_id.tolist()
    con.close()
    snaps = L.load_spread_snapshots(games, sp_ids)
    out["spread_counts"] = spread_counts(games, snaps)
    for r in out["spread_counts"]:
        print("spread counts:", r)
    r1 = L.load_r1()
    out["r1"] = r1
    out["kalshi"] = kalshi_gates(games, r1)
    print("kalshi gates:", out["kalshi"].get("status"))
    L.MAIN_OUT.mkdir(exist_ok=True)
    (L.MAIN_OUT / "gates.json").write_text(json.dumps(out, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    if out["kalshi"].get("status") == "run":
        print(json.dumps({k: out["kalshi"][k] for k in ("series_prefix", "team_id", "bracket")}, indent=1))
        for r in out["kalshi"]["usable_counts"]:
            print("usable:", r)


if __name__ == "__main__":
    main()
