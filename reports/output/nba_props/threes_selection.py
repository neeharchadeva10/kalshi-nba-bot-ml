"""Research log #36, JOB 1: KXNBA3PT selection, built exactly like #48's PTS/REB/AST selection
(nba_teammate_out/tm_lib.build_selection): for every player-game, the 2 strikes nearest the player's pre-game last-10-played-games
mean of threes made (hoopR three_point_field_goals_made, games with ET date STRICTLY before the game date, >= 5 prior games),
same player matching (Box.match_player), same game date / tip definitions (tm_lib.game_tips / tip_of).
Reads the local nba_props.sqlite and the hoopR parquet only.  No network.  No outcomes used (results/settlements are not read).
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nba_teammate_out"))
import tm_lib as L  # noqa: E402

SELECTION_3PT_CSV = HERE / "threes_selection.csv"
SELECTION_3PT_REPORT = HERE / "threes_selection_report.json"
CANDLE_DIR_3PT = L.ROOT / "data" / "raw" / "kalshi_props_hourly_3pt"      # sibling of #48's kalshi_props_hourly


def candle_path_3pt(ticker: str, game_date: str) -> Path:
    return CANDLE_DIR_3PT / game_date / f"{ticker}.json.gz"


def threes_games(box, aid):
    """[(date, threes_made)] of PLAYED games, sorted by date (box.played rows are (date, gid, ...) ; 3PM looked up by gid)."""
    return [(x[0], box.threes.get((aid, x[1]), 0.0)) for x in box.played.get(aid, [])]


def load_box():
    """tm_lib.Box plus a (athlete, game_id) -> threes made map from the same hoopR files."""
    import pandas as pd
    box = L.Box()
    box.threes = {}
    for yr in (2025, 2026):
        pb = pd.read_parquet(L.BOX_DIR / f"player_box_{yr}.parquet",
                             columns=["game_id", "athlete_id", "three_point_field_goals_made"])
        for gid, aid, v in zip(pb["game_id"], pb["athlete_id"], pb["three_point_field_goals_made"]):
            if aid == aid:
                box.threes[(int(aid), str(gid))] = float(v) if v == v and v is not None else 0.0
    return box


def load_3pt_market_defs():
    conn = sqlite3.connect(f"file:{L.PROPS_DB.as_posix()}?mode=ro", uri=True)
    q = ("SELECT ticker, stat, game_date, game_code, team, player_key, player_name, floor_strike, occ_ts, exp_ts "
         "FROM mk WHERE stat = '3PT' ORDER BY game_date, ticker")
    rows = list(conn.execute(q))
    conn.close()
    return rows


def select_rows(defs, box, tips, aid_of, pre_mean=L.pre_game_mean):
    """Pure-ish core (testable with toy inputs).  defs: market-def tuples; aid_of: {(team, pk): aid|None}; box needs
    stat games via threes_games.  -> (rows, skips, n_psg)."""
    by_psg = defaultdict(list)
    tip_by_game = {}
    for (t, stat, gd, gc, team, pk, pname, fs, occ, exp) in defs:
        by_psg[(gd, gc, team, pk)].append((fs, t))
        tp = L.tip_of(gd, gc, occ, exp, tips)[0]
        if tp is not None:
            tip_by_game[(gd, gc)] = tp
    rows, skip = [], defaultdict(int)
    for (gd, gc, team, pk), strikes in sorted(by_psg.items()):
        aid = aid_of.get((team, pk))
        if aid is None:
            skip["unmapped_player"] += 1
            continue
        if (gd, gc) not in tip_by_game:
            skip["no_tip_time"] += 1
            continue
        mean, k = pre_mean(threes_games(box, aid), gd)
        if mean is None:
            skip["fewer_than_5_prior_games"] += 1
            continue
        for rank, (fs, t) in enumerate(L.select_strikes(strikes, mean, 2), 1):
            rows.append(dict(ticker=t, game_date=gd, game_code=gc, team=team, player_key=pk, aid=aid, stat="3PT",
                             floor_strike=fs, mean10=round(mean, 4), n_prior=k, rank=rank, tip=tip_by_game[(gd, gc)]))
    return rows, dict(skip), len(by_psg)


def main():
    box = load_box()
    tips = L.game_tips()
    defs = load_3pt_market_defs()
    pname_of = {(d[4], d[5]): d[6] for d in defs}
    methods = defaultdict(int)
    aid_of = {}
    for (team, pk), pname in pname_of.items():
        a, m = box.match_player(pname, team)
        aid_of[(team, pk)] = a
        methods[m] += 1
    rows, skips, n_psg = select_rows(defs, box, tips, aid_of)
    for r in rows:
        r["group"] = L.group_of(r["game_date"], box.reg_end)
    rows.sort(key=lambda r: (r["group"], r["game_date"], r["ticker"]))
    cols = ["group", "game_date", "ticker", "game_code", "team", "player_key", "aid", "stat", "floor_strike", "mean10", "n_prior",
            "rank", "tip"]
    with SELECTION_3PT_CSV.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r[c] for c in cols})
    rep = dict(n_markets_defined=len(defs), n_players=len(aid_of), map_methods=dict(methods),
               players_matched=sum(1 for a in aid_of.values() if a is not None), player_games=n_psg, skips=skips,
               markets_selected=len(rows), reg_end=box.reg_end,
               by_group={g: sum(1 for r in rows if r["group"] == g) for g in (0, 1, 2)})
    SELECTION_3PT_REPORT.write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
