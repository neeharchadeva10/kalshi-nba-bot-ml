"""NBA player movement (team changes) from hoopR per-game rosters, 2008..current.  READ ONLY (cached hoopR files;
downloads via scripts/nba_rosters.py helpers: raw.githubusercontent.com / api.github.com GET only).

A "move" = a player's team differs between two consecutive appearances on a game roster (active or inactive).
THE DATA CANNOT SAY trade vs signing vs waiver vs 10-day.  `label` is "likely trade" only when two DIFFERENT players
swap between the same two teams with first_date_new within 3 days of each other; everything else is "moved".
last_date_old / first_date_new are BOUNDS: the actual transaction happened between them (first_date_new is the
player's first game-sheet appearance for the new team, which can lag the real move, esp. for injured players).
Teams are franchise codes (Kalshi style; NJ->BKN, SEA->OKC, NO/NOH->NOP via ESPN franchise id).
Season YYYY = season ENDING in YYYY (2027 = 2026-27).  type = "in-season" if both appearances are in the same
season else "offseason".

Outputs: data/derived/player_moves.csv, data/derived/player_moves_pending_<cur>.csv, docs/PLAYER_MOVES.md
Usage:  .venv/Scripts/python.exe scripts/nba_player_moves.py [--offline]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nba_rosters as R  # noqa: E402

SWAP_DAYS = 3
MPG_WINDOW = 20
MOVE_COLS = ["athlete_id", "player", "from_team", "to_team", "last_date_old", "first_date_new", "season_old",
             "season_new", "type"]


def season_label(y: int) -> str:
    return f"{y - 1}-{str(y)[2:]}"


# ----------------------------------------------------------------------------- pure detection
def detect_moves(app: pd.DataFrame) -> pd.DataFrame:
    """app: one row per roster appearance with columns athlete_id, player, team, season, date ('YYYY-MM-DD'),
    order (tie-break within a date: game time / game id).  Returns the moves (columns MOVE_COLS).
    Uses only the sequence of appearances; exact duplicate rows are ignored."""
    if app.empty:
        return pd.DataFrame(columns=MOVE_COLS)
    a = app[["athlete_id", "player", "team", "season", "date", "order"]].drop_duplicates()
    a = a.sort_values(["athlete_id", "date", "order"], kind="mergesort").reset_index(drop=True)
    g = a.groupby("athlete_id", sort=False)
    prev_team = g["team"].shift(1)
    brk = prev_team.notna() & (prev_team != a["team"])
    out = pd.DataFrame({
        "athlete_id": a["athlete_id"], "player": a["player"],
        "from_team": prev_team, "to_team": a["team"],
        "last_date_old": g["date"].shift(1), "first_date_new": a["date"],
        "season_old": g["season"].shift(1), "season_new": a["season"],
    })[brk].reset_index(drop=True)
    out["season_old"] = out["season_old"].astype(int)
    out["season_new"] = out["season_new"].astype(int)
    out["type"] = np.where(out["season_old"] == out["season_new"], "in-season", "offseason")
    return out[MOVE_COLS]


def label_swaps(moves: pd.DataFrame, days: int = SWAP_DAYS) -> pd.DataFrame:
    """Add `label` ('likely trade' | 'moved') and `swap_partner` (name of the player going the other way)."""
    m = moves.copy().reset_index(drop=True)
    m["label"] = "moved"
    m["swap_partner"] = ""
    if m.empty:
        return m
    d = pd.to_datetime(m["first_date_new"])
    idx: dict = {}
    for i, (f, t) in enumerate(zip(m["from_team"], m["to_team"])):
        idx.setdefault((f, t), []).append(i)
    for i, (f, t, aid) in enumerate(zip(m["from_team"], m["to_team"], m["athlete_id"])):
        for j in idx.get((t, f), []):
            if m.at[j, "athlete_id"] != aid and abs((d[i] - d[j]).days) <= days:
                m.at[i, "label"] = "likely trade"
                m.at[i, "swap_partner"] = str(m.at[j, "player"])
                break
    return m


def prior_mpg(dates: np.ndarray, mins: np.ndarray, before: str, n: int = MPG_WINDOW) -> tuple[float | None, int]:
    """Mean minutes over the last n games the player actually played with date STRICTLY BEFORE `before`
    (dates sorted ascending, 'YYYY-MM-DD' strings).  Returns (mpg, n_games_used)."""
    k = int(np.searchsorted(dates, before, side="left"))
    w = mins[max(0, k - n):k]
    if len(w) == 0:
        return None, 0
    return round(float(np.mean(w)), 1), int(len(w))


def add_prior_mpg(moves: pd.DataFrame, box: pd.DataFrame) -> pd.DataFrame:
    """box: columns athlete_id, game_date, minutes, did_not_play (any teams / seasons)."""
    m = moves.copy()
    b = box.copy()
    b = b[(~b["did_not_play"].fillna(False)) & (b["minutes"].fillna(0) > 0)]
    b = b.sort_values(["athlete_id", "game_date"])
    per = {a: (g["game_date"].to_numpy(), g["minutes"].to_numpy(dtype=float)) for a, g in b.groupby("athlete_id")}
    res = []
    for aid, before in zip(m["athlete_id"], m["first_date_new"]):
        d, mi = per.get(aid, (np.array([]), np.array([])))
        res.append(prior_mpg(d, mi, before))
    m["prior_mpg20"] = [r[0] for r in res]
    m["prior_games_used"] = [r[1] for r in res]
    return m


# ----------------------------------------------------------------------------- data loading
def load_appearances(paths: dict, seasons) -> tuple[pd.DataFrame, dict]:
    """Game-roster appearances for NBA franchises.  Falls back to player_box (games actually played) for a season
    whose game_rosters file is missing.  Returns (appearances, source_by_season)."""
    gd = R.load_game_dates(seasons)
    gd = gd[gd["season_type"].isin([2, 3, 5])]
    key = gd.set_index("game_id")
    parts, src = [], {}
    for y in seasons:
        g = R.read_pq(paths.get(f"game_rosters/game_rosters_{y}.parquet"),
                      columns=["game_id", "team_id", "team_abbreviation", "athlete_id", "athlete_display_name"])
        if g is not None and len(g):
            src[y] = "game_rosters"
            g = g.rename(columns={"athlete_display_name": "player"})
        else:
            b = R.read_pq(paths.get(f"player_box/player_box_{y}.parquet"),
                          columns=["game_id", "team_id", "team_abbreviation", "athlete_id", "athlete_display_name",
                                   "did_not_play", "minutes"])
            if b is None:
                src[y] = "MISSING"
                continue
            src[y] = "player_box (fallback)"
            b = b[(~b["did_not_play"].fillna(False)) & (b["minutes"].fillna(0) > 0)]
            g = b.rename(columns={"athlete_display_name": "player"})[["game_id", "team_id", "team_abbreviation", "athlete_id", "player"]]
        g = g.copy()
        g["game_id"] = g["game_id"].astype(str)
        g["team"] = [R.kalshi_code(a, t) for a, t in zip(g["team_abbreviation"], g["team_id"])]
        g = g[g["team"].notna() & g["athlete_id"].notna() & g["game_id"].isin(key.index)]
        g["athlete_id"] = g["athlete_id"].astype(int)
        g["season"] = y
        g["date"] = g["game_id"].map(key["game_date"])
        g["order"] = g["game_id"].map(key["game_date_time"].astype(str)) + "_" + g["game_id"]
        parts.append(g[["athlete_id", "player", "team", "season", "date", "order", "game_id"]])
    return pd.concat(parts, ignore_index=True), src


def load_box(paths: dict, seasons) -> pd.DataFrame:
    parts = []
    for y in seasons:
        b = R.read_pq(paths.get(f"player_box/player_box_{y}.parquet"),
                      columns=["athlete_id", "game_date", "minutes", "did_not_play"])
        if b is not None:
            b["game_date"] = pd.to_datetime(b["game_date"]).dt.strftime("%Y-%m-%d")
            parts.append(b)
    return pd.concat(parts, ignore_index=True)


def pending_moves(paths: dict, app: pd.DataFrame, box: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Players on rosters_<cur> who have NOT yet appeared on a <cur> game sheet and whose last known team (from
    game rosters, any earlier season) differs from the roster team.  Snapshot-based: dated only by the source's
    as-of time, not by a game date."""
    cur = R.CURRENT_SEASON
    ros = R.read_pq(paths.get(f"rosters/rosters_{cur}.parquet"))
    if ros is None:
        return pd.DataFrame(), 0
    ros = ros.copy()
    ros["athlete_id"] = ros["athlete_id"].astype(int)
    ros["to_team"] = [R.kalshi_code(a, t) for a, t in zip(ros["team_abbreviation"], ros["team_id"])]
    appeared = set(app.loc[app["season"] == cur, "athlete_id"])
    last = app.sort_values(["date", "order"]).groupby("athlete_id").tail(1).set_index("athlete_id")
    rows, new_players = [], 0
    for r in ros.itertuples():
        if r.athlete_id in appeared:
            continue
        if r.athlete_id not in last.index:
            new_players += 1
            continue
        l = last.loc[r.athlete_id]
        if l["team"] != r.to_team:
            rows.append({"athlete_id": r.athlete_id, "player": r.full_name, "from_team": l["team"], "to_team": r.to_team,
                         "last_date_old": l["date"], "season_old": int(l["season"]), "season_new": cur,
                         "type": "offseason (roster snapshot; no game yet)"})
    p = pd.DataFrame(rows)
    if len(p):
        p = add_prior_mpg(p.assign(first_date_new="9999-12-31"), box).drop(columns=["first_date_new"])
    return p, new_players


# ----------------------------------------------------------------------------- docs
def write_doc(m: pd.DataFrame, pend: pd.DataFrame, new_players: int, src: dict, path: Path, asof: str) -> None:
    cur = R.CURRENT_SEASON
    L = [f"# NBA player moves, {season_label(R.FIRST_SEASON)} to {season_label(cur)}", "",
         f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `scripts/nba_player_moves.py`. Source: hoopR game_rosters "
         f"(per-game rosters, active + inactive), dates from team_box, minutes from player_box. Data file: `data/derived/player_moves.csv`.",
         f"As of: {asof}", "",
         "## What this is and is not", "",
         "- A move = a player's team (franchise) differs between two consecutive game-roster appearances. Franchise codes are Kalshi style "
         "(Nets in New Jersey and Brooklyn are both BKN, SEA = OKC, NO/NOH/NOK = NOP).",
         "- **The data cannot say trade vs free-agent signing vs waiver vs 10-day contract vs buyout.** Every move is labelled `moved` except "
         "`likely trade`: two different players swapping between the same two teams with first appearances on the new teams within 3 days. "
         "One-sided trades (player for picks), 3-team trades and trades where one side is not seen are labelled `moved`.",
         "- Dates are bounds. `last_date_old` is the last game sheet on the old team, `first_date_new` the first on the new team; the real "
         "transaction is between them. A player who is hurt after a trade can show up on the new team's sheet days or weeks late, and players "
         "never listed on a game sheet (long-term injured, two-way, G League) do not show at all.",
         "- For `offseason` moves `first_date_new` is the new team's first game of the next season, so many offseason moves share the same date. "
         "The 3-day swap rule therefore over-labels offseason moves as `likely trade` (opening-night coincidences); trust the label for in-season moves only.",
         "- `prior_mpg20` = mean minutes over the last 20 games the player actually played (minutes > 0) strictly before `first_date_new`, any team, "
         "regular season + playoffs. It flags key players; it is not usage on the new team.",
         "- Seasons: hoopR season YYYY = the season ending in YYYY (2026 = 2025-26, 2027 = 2026-27). Regular season, play-in and playoffs are included; "
         "All-Star and non-NBA teams are dropped.", "",
         "## Source per season", "", ", ".join(f"{y}: {s}" for y, s in sorted(src.items())), "",
         "## Move counts per season (season of the NEW team)", ""]
    tab = []
    for y in sorted(m["season_new"].unique()):
        s = m[m["season_new"] == y]
        ins = s[s["type"] == "in-season"]
        tab.append({"season": f"{y} ({season_label(y)})", "offseason": int((s["type"] == "offseason").sum()), "in-season": len(ins),
                    "in-season 'likely trade' rows": int((ins["label"] == "likely trade").sum())})
    L.append(R.md_table(tab, ["season", "offseason", "in-season", "in-season 'likely trade' rows"]))
    L.append("(The first season, %s, has no earlier appearances, so no moves are counted into it; its offseason column is 0 by construction. "
             "'likely trade' rows count players, so a clean 1-for-1 swap is 2 rows.)" % season_label(R.FIRST_SEASON))
    L.append("")

    def top(df, n):
        d = df.sort_values("prior_mpg20", ascending=False, na_position="last").head(n)
        return R.md_table(d.assign(mpg=d["prior_mpg20"]).to_dict("records"),
                          ["player", "from_team", "to_team", "last_date_old", "first_date_new", "mpg", "label", "swap_partner"])

    ins_all = m[m["type"] == "in-season"]
    for y in (cur - 1, cur):
        s = ins_all[ins_all["season_new"] == y]
        L += [f"## Top 15 in-season moves by prior mpg: {y} ({season_label(y)})" + (" so far" if y == cur else ""), "",
              f"{len(s)} in-season moves in this season.", "", top(s, 15)]
    L += ["## Top 15 in-season moves by prior mpg: all seasons", "",
          top(ins_all.assign(player=ins_all["player"] + " (" + ins_all["season_new"].astype(str) + ")"), 15)]
    L += [f"## Top 5 in-season moves per earlier season", ""]
    for y in sorted(ins_all["season_new"].unique()):
        if y >= cur - 1:
            continue
        L += [f"### {y} ({season_label(y)})", "", top(ins_all[ins_all["season_new"] == y], 5)]
    cur_off = m[(m["season_new"] == cur) & (m["type"] == "offseason")]
    L += [f"## {season_label(cur)} offseason moves seen on game sheets so far (top 25 by prior mpg)", "",
          f"{len(cur_off)} players have appeared for a different team than last season "
          "(the season has just started, so most players have not been on a game sheet yet). ", "", top(cur_off, 25)]
    if len(pend):
        L += [f"## {season_label(cur)}: on a new team per roster snapshot, no game played yet (top 25 by prior mpg)", "",
              f"{len(pend)} players are on rosters_{cur} for a team different from their last game-roster team and have not yet been on a {cur} "
              f"game sheet (e.g. CLE and DEN had not played). {new_players} further rostered players have no earlier game-roster history "
              "(rookies / first NBA data). Snapshot-based, undated: see `data/derived/player_moves_pending_%d.csv`." % cur, "",
              R.md_table(pend.sort_values("prior_mpg20", ascending=False, na_position="last").head(25).assign(mpg=lambda d: d["prior_mpg20"]).to_dict("records"),
                         ["player", "from_team", "to_team", "last_date_old", "mpg"])]
    path.write_text("\n".join(L), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv)
    R.DERIVED.mkdir(parents=True, exist_ok=True)
    R.DOCS.mkdir(parents=True, exist_ok=True)
    paths = R.ensure_data(offline=a.offline)
    seasons = list(range(R.FIRST_SEASON, R.CURRENT_SEASON + 1))
    app, src = load_appearances(paths, seasons)
    box = load_box(paths, seasons)
    mv = detect_moves(app)
    mv = label_swaps(mv)
    mv = add_prior_mpg(mv, box)
    mv = mv.sort_values(["first_date_new", "to_team", "player"]).reset_index(drop=True)
    cols = MOVE_COLS + ["prior_mpg20", "prior_games_used", "label", "swap_partner"]
    mv[cols].to_csv(R.DERIVED / "player_moves.csv", index=False)
    pend, newp = pending_moves(paths, app, box)
    pend.to_csv(R.DERIVED / f"player_moves_pending_{R.CURRENT_SEASON}.csv", index=False)
    b_info = R.asof_info("game_rosters", f"game_rosters_{R.CURRENT_SEASON}.parquet", box, "game_date")
    write_doc(mv, pend, newp, src, R.DOCS / "PLAYER_MOVES.md", R.fmt_asof(b_info))
    print("appearances", len(app), "moves", len(mv), "pending", len(pend), "new", newp)
    print(mv.groupby(["season_new", "type"]).size().unstack(fill_value=0).to_string())
    print("sources", {y: s for y, s in src.items() if s != "game_rosters"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
