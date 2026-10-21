"""Research log #36: markets + box + reports -> player-game-stat rows (features, actual count) and market rows (quote, result).

No prices or Kalshi results are used here to build features; `result` / quotes are only attached to the market rows.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

import numpy as np

import data as D
import features as F


def mdy(game_date_iso: str) -> str:
    d = date.fromisoformat(game_date_iso)
    return d.strftime("%m/%d/%Y")


def build(markets, box: D.Box, store: D.ReportStore, game_keys=None, progress=False):
    """markets: data.load_markets() output.  game_keys: optional set of (game_date, game_code) to restrict to.
    -> (rows, mrows, counts, games)
      rows  : one dict per (game, player, stat) that is matched, not Out and has history: vec, info, y (hoopR count or None)
      mrows : one dict per market with 'row' (index into rows or None) and 'excl' (None or reason)
      games : {game_key: {tip, covered, file, created, ...}} injury coverage per game"""
    by_game = defaultdict(list)
    for m in markets:
        gk = (m["game_date"], m["game_code"])
        if game_keys is not None and gk not in game_keys:
            continue
        by_game[gk].append(m)
    counts = Counter()
    rows, mrows, games = [], [], {}
    rowidx = {}
    unmatched_names = Counter()
    for gi, (gk, ms) in enumerate(sorted(by_game.items(), key=lambda kv: (min(x["tip"] or 0 for x in kv[1]), kv[0]))):
        gdate, gcode = gk
        away, home = gcode[:3], gcode[3:]
        tip = ms[0]["tip"]
        dec = tip - 3600
        rep = store.report_for_game(dec, mdy(gdate), away, home)
        games[gk] = {"tip": tip, "tip_src": ms[0]["tip_src"], "has_report": rep is not None,
                     "covered": bool(rep and rep["covered"]), "file": rep["file"] if rep else None,
                     "created": rep["created"] if rep else None,
                     "lag_min": (dec + 3600 - rep["created"]) / 60 if rep else None}
        for m in ms:
            rec = dict(m)
            rec["gk"] = gk
            rec["row"] = None
            rec["excl"] = None
            counts["markets"] += 1
            counts["markets_" + m["stat"]] += 1
            if m["result"] not in ("yes", "no"):
                rec["excl"] = "fair_price_settlement"
            elif m["tip"] is None:
                rec["excl"] = "no_tip"
            else:
                tm = m["team"]
                opp = away if tm == home else home
                key = (gk, m["player_key"], m["stat"])
                if key in rowidx:
                    rec["row"] = rowidx[key]
                else:
                    aid = box.match_player(m["player_name"], tm)
                    if aid is None:
                        rec["excl"] = "unmatched_player"
                        unmatched_names[(m["player_name"], tm)] += 1
                    else:
                        vec, info = F.build_vector(box, aid, tm, opp, tm == home, m["stat"], gdate, dec, rep, m["player_name"])
                        if info["status"] == "Out":
                            rec["excl"] = "listed_out"
                        elif vec is None:
                            rec["excl"] = "no_history"
                        else:
                            prow = box.player_games_on[aid].get((gdate, tm))
                            y = prow[F.SI[m["stat"]]] if prow else None
                            rows.append({"gk": gk, "tip": tip, "aid": aid, "team": tm, "opp": opp, "home": tm == home,
                                         "stat": m["stat"], "player_key": m["player_key"], "name": m["player_name"],
                                         "vec": vec, "info": info, "y": y, "minutes": prow[4] if prow else None})
                            rowidx[key] = len(rows) - 1
                            rec["row"] = rowidx[key]
            if rec["excl"] is None and rec["bid"] is None:
                rec["quote_excl"] = "no_quote" if rec["downloaded"] else "not_downloaded"
            else:
                rec["quote_excl"] = None
            if rec["excl"]:
                counts["excl_" + rec["excl"]] += 1
            elif rec["quote_excl"]:
                counts["excl_" + rec["quote_excl"]] += 1
            mrows.append(rec)
        if progress and gi % 100 == 0:
            print("game", gi, len(by_game), flush=True)
    counts["unmatched_player_keys"] = len(unmatched_names)
    return rows, mrows, dict(counts), games, unmatched_names


def design(rows, stat):
    """-> (X, y, idx) for rows of one stat that have an actual count."""
    idx = [i for i, r in enumerate(rows) if r["stat"] == stat and r["y"] is not None]
    X = np.array([rows[i]["vec"] for i in idx]) if idx else np.zeros((0, len(F.FEATURE_NAMES)))
    y = np.array([rows[i]["y"] for i in idx], dtype=float)
    return X, y, idx
