"""Roster changes 2026-27 per team: who came IN / went OUT since last season, and how much LAST SEASON's production
that is (NOT a prediction).  Read-only: uses local files only (no network).

Inputs : data/derived/player_moves.csv (season_new 2027) + player_moves_pending_2027.csv, rosters_current.csv (ages),
         data/raw/nba_open/player_box_2026.parquet (2025-26 regular season).
Outputs: docs/ROSTER_CHANGES_2026_27.md and data/derived/roster_changes_2027.csv

Numbers are per-game sums: "net mpg" = minutes per game the incoming players averaged last season minus what the
outgoing players averaged (a team has 240 player-minutes per game); "net ppg" likewise for points.  A player's
last-season line is his whole 2025-26 regular season (all teams).  Tiers by last-season mpg: star 28+, starter 20-28,
bench 12-20, deep <12, none = no 2025-26 minutes (rookie / overseas).  Bench-upgrade line = net change among players
at 12-28 mpg.  Veteran = age 32+.

Usage: .venv/Scripts/python.exe scripts/roster_changes.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trade_watch as T  # noqa: E402  (tier_of, is_veteran, norm_name, load_player_stats)

ROOT = Path(__file__).resolve().parents[1]
OUT_MD = ROOT / "docs" / "ROSTER_CHANGES_2026_27.md"
OUT_CSV = ROOT / "data" / "derived" / "roster_changes_2027.csv"
TIERS = ("star", "starter", "bench", "deep")        # "none" counts with deep (zero production anyway)
FEW_GAMES = 10


def tier_bucket(tier: str) -> str:
    return "deep" if tier == "none" else tier


def net_summary(ins: list[dict], outs: list[dict]) -> dict:
    """ins/outs: dicts with mpg, ppg, tier, age.  Returns net (in minus out) mpg/ppg overall and by tier,
    the 12-28 mpg 'bench upgrade' net, and the veterans (age 32+) added."""
    def tot(rows, key, pred=lambda r: True):
        return sum((r.get(key) or 0.0) for r in rows if pred(r))

    def net(key, pred=lambda r: True):
        return round(tot(ins, key, pred) - tot(outs, key, pred), 1)

    mid = lambda r: r.get("mpg") is not None and 12 <= r["mpg"] < 28
    out = {"n_in": len(ins), "n_out": len(outs), "net_mpg": net("mpg"), "net_ppg": net("ppg"),
           "bench_up_mpg": net("mpg", mid), "bench_up_ppg": net("ppg", mid),
           "vets_added": [r["player"] for r in ins if T.is_veteran(r.get("age"))]}
    for t in TIERS:
        p = lambda r, t=t: tier_bucket(r["tier"]) == t
        out["net_mpg_" + t], out["net_ppg_" + t] = net("mpg", p), net("ppg", p)
    return out


def line_for(ins, outs, s, team) -> str:
    def best(rows):
        r = max(rows, key=lambda r: r.get("mpg") or 0, default=None)
        return None if r is None or not r.get("mpg") else "%s (%.0f mpg, %.1f ppg)" % (r["player"], r["mpg"], r["ppg"])
    sgn = lambda x: ("+%.1f" % x) if x > 0 else ("%.1f" % x if x < 0 else "0.0")
    if not ins and not outs:
        return "%s: no recorded changes." % team
    verdict = "adds more than it loses" if s["net_ppg"] > 1 else "loses more than it adds" if s["net_ppg"] < -1 else "roughly breaks even"
    bits = ["%s %s last-season production: %d in, %d out, net %s mpg / %s ppg." % (team, verdict, s["n_in"], s["n_out"], sgn(s["net_mpg"]), sgn(s["net_ppg"]))]
    bi, bo = best(ins), best(outs)
    if bi:
        bits.append("Biggest add: %s." % bi)
    if bo:
        bits.append("Biggest loss: %s." % bo)
    if abs(s["bench_up_mpg"]) >= 3:
        bits.append("Rotation (12-28 mpg) %s %s mpg." % ("gains" if s["bench_up_mpg"] > 0 else "loses", abs(s["bench_up_mpg"])))
    if s["vets_added"]:
        bits.append("Veterans (32+) added: %s." % ", ".join(s["vets_added"]))
    return " ".join(bits)


def collect_moves(moves_csv, pending_csv) -> pd.DataFrame:
    a = pd.read_csv(moves_csv)
    a = a[a["season_new"] == T.CURRENT_SEASON_NEW].copy()
    a["source"] = "player_moves.csv"
    try:
        b = pd.read_csv(pending_csv)
        b = b[b["season_new"] == T.CURRENT_SEASON_NEW].copy()
        b["source"] = "player_moves_pending_2027.csv"
        b["label"] = "pending (roster snapshot)"
        a = pd.concat([a, b[~b["athlete_id"].isin(a["athlete_id"])]], ignore_index=True)
    except OSError:
        pass
    return a.drop_duplicates(["athlete_id", "from_team", "to_team"])


def build_rows(moves: pd.DataFrame, ages: dict, stats: dict) -> list[dict]:
    rows = []
    for _, m in moves.iterrows():
        aid = int(m["athlete_id"])
        st = stats.get(aid) or {}
        age = ages.get(aid)
        mpg = st.get("mpg")
        base = {"player": m["player"], "athlete_id": aid, "from_team": m["from_team"], "to_team": m["to_team"],
                "move_type": m.get("label") if isinstance(m.get("label"), str) else m.get("type"), "source": m["source"],
                "gp": st.get("gp", 0), "mpg": mpg, "ppg": st.get("ppg"), "rpg": st.get("rpg"), "apg": st.get("apg"),
                "pm_pg": st.get("pm_pg"), "age": age, "tier": T.tier_of(mpg), "veteran": T.is_veteran(age),
                "few_games": st.get("gp", 0) < FEW_GAMES}
        rows.append(dict(base, team=m["to_team"], direction="IN"))
        rows.append(dict(base, team=m["from_team"], direction="OUT"))
    return rows


def team_tables(rows: list[dict], teams: list[str]):
    out = []
    for tm in teams:
        ins = [r for r in rows if r["team"] == tm and r["direction"] == "IN"]
        outs = [r for r in rows if r["team"] == tm and r["direction"] == "OUT"]
        s = net_summary(ins, outs)
        s.update(team=tm, text=line_for(ins, outs, s, tm), ins=ins, outs=outs)
        out.append(s)
    return sorted(out, key=lambda s: (-s["net_ppg"], -s["net_mpg"], s["team"]))


def fmt(x, nd=1):
    return "n/a" if x is None or x != x else ("%." + str(nd) + "f") % x


def player_line(r) -> str:
    chips = ["%s" % r["tier"]] + (["VET"] if r["veteran"] else []) + (["%d gp only" % r["gp"]] if r["few_games"] and r["gp"] else []) + (["no 2025-26 minutes"] if not r["gp"] else [])
    pm = "" if r["pm_pg"] is None else ", +/- %+.1f" % r["pm_pg"]
    return "- %s (%s -> %s, age %s): %s gp, %s mpg, %s ppg, %s rpg, %s apg%s [%s]" % (
        r["player"], r["from_team"], r["to_team"], fmt(r["age"], 0), r["gp"], fmt(r["mpg"]), fmt(r["ppg"]), fmt(r["rpg"]), fmt(r["apg"]), pm, ", ".join(chips))


def write_md(tabs, path: Path, today: str):
    sgn = lambda x: "%+.1f" % x
    L = ["# Roster changes 2026-27 (bench upgrades, adds and losses)", "",
         "Generated %s by `scripts/roster_changes.py` from local files only." % today, "",
         "**Read this first: every number below is LAST SEASON's (2025-26 regular season) production of the players who "
         "moved. It is not a prediction of this season** (age, role, health, new team fit and minutes all change). "
         "\"Net\" = incoming players' last-season per-game numbers minus outgoing players'. Tiers by last-season minutes per "
         "game: star 28+, starter 20-28, bench 12-20, deep <12. Bench-upgrade = net change among 12-28 mpg players. "
         "Veteran = age 32+. Moves come from the roster-snapshot comparison (player_moves.csv + pending file); a few may be "
         "waivers/free-agent signings rather than trades, and players with no 2025-26 NBA team (2026 draft picks, overseas or first-time signings) or who left the NBA are NOT in the move files, "
         "so a team's IN side can be undercounted (e.g. a team showing 0 in).", "",
         "## League table (ranked by net points per game added)", "",
         "| # | Team | In | Out | Net mpg | Net ppg | Star ppg | Starter ppg | Bench ppg | Bench-upgrade mpg (12-28) | Veterans 32+ added |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, s in enumerate(tabs, 1):
        L.append("| %d | %s | %d | %d | %s | %s | %s | %s | %s | %s | %s |" % (
            i, s["team"], s["n_in"], s["n_out"], sgn(s["net_mpg"]), sgn(s["net_ppg"]), sgn(s["net_ppg_star"]), sgn(s["net_ppg_starter"]),
            sgn(s["net_ppg_bench"]), sgn(s["bench_up_mpg"]), ", ".join(s["vets_added"]) or "-"))
    L += ["", "## Teams (alphabetical)", ""]
    for s in sorted(tabs, key=lambda s: s["team"]):
        L += ["### %s" % s["team"], "", s["text"], ""]
        L.append("Net by tier (mpg / ppg): " + "; ".join("%s %s / %s" % (t, sgn(s["net_mpg_" + t]), sgn(s["net_ppg_" + t])) for t in TIERS) +
                 "; rotation 12-28 mpg %s / %s." % (sgn(s["bench_up_mpg"]), sgn(s["bench_up_ppg"])))
        L.append("")
        for title, rows in (("In", s["ins"]), ("Out", s["outs"])):
            L.append("**%s**" % title)
            L += [player_line(r) for r in sorted(rows, key=lambda r: -(r["mpg"] or 0))] or ["- none"]
            L.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    from datetime import date
    roster = pd.read_csv(T.ROSTER_CSV)
    ages = {int(a): int(g) for a, g in zip(roster["athlete_id"], roster["age"]) if a == a and g == g}
    teams = sorted(roster["team_kalshi"].unique())
    moves = collect_moves(T.MOVES_CSV, T.PENDING_CSV)
    stats = T.load_player_stats()
    rows = build_rows(moves, ages, stats)
    tabs = team_tables(rows, teams)
    cols = ["team", "direction", "player", "athlete_id", "from_team", "to_team", "move_type", "source", "gp", "mpg", "ppg", "rpg", "apg",
            "pm_pg", "age", "tier", "veteran", "few_games"]
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows)[cols].sort_values(["team", "direction", "mpg"], ascending=[True, True, False]).to_csv(OUT_CSV, index=False)
    write_md(tabs, OUT_MD, date.today().isoformat())
    print("moves=%d rows=%d teams=%d" % (len(moves), len(rows), len(tabs)))
    for s in tabs[:5] + tabs[-5:]:
        print("%-4s net ppg %+5.1f  net mpg %+6.1f  in %d out %d  bench-up mpg %+5.1f" % (s["team"], s["net_ppg"], s["net_mpg"], s["n_in"], s["n_out"], s["bench_up_mpg"]))


if __name__ == "__main__":
    main()
