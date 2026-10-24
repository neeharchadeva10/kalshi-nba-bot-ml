"""NBA rosters (current) from the free hoopR data repo.  READ ONLY: GET requests to raw.githubusercontent.com
and api.github.com (commit listing for the as-of stamp) only.  No Kalshi, no keys, no other network.

Outputs
  data/derived/rosters_current.csv   one row per rostered player (30 teams)
  docs/ROSTERS_CURRENT.md            per-team rosters + as-of stamps
  docs/ROSTERS_CHECK.md              multi-source disagreement list (rosters vs game_rosters vs player_box)
Raw cache: data/raw/nba_open/hoopr_extra/<folder>/<file>  (atomic temp-then-rename; 2027 files refetched
after 12 h, older seasons downloaded once).

Season convention (hoopR/ESPN): season YYYY = the season ENDING in YYYY, so 2027 = 2026-27.

Usage:  .venv/Scripts/python.exe scripts/nba_rosters.py [--offline]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "nba_open" / "hoopr_extra"
DERIVED = ROOT / "data" / "derived"
DOCS = ROOT / "docs"
META_PATH = RAW / "_meta.json"

BASE = "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/nba/"
API_COMMITS = "https://api.github.com/repos/sportsdataverse/hoopR-nba-data/commits"
UA = {"User-Agent": "kalshi-pm-research/1.0 (read-only GET)"}
FIRST_SEASON = 2008
MAX_AGE_H = 12.0


def season_for(d: date) -> int:
    """Season ending-year for a calendar date (Oct-Dec belong to the next year's season)."""
    return d.year + 1 if d.month >= 9 else d.year


CURRENT_SEASON = season_for(date.today())

# ----------------------------------------------------------------------------- team codes
# ESPN/hoopR team_id is a stable FRANCHISE id (NJ and BKN are both 17, SEA and OKC both 25, ...).
FRANCHISE_BY_ID = {
    1: "ATL", 2: "BOS", 3: "NOP", 4: "CHI", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GSW", 10: "HOU",
    11: "IND", 12: "LAC", 13: "LAL", 14: "MIA", 15: "MIL", 16: "MIN", 17: "BKN", 18: "NYK", 19: "ORL",
    20: "PHI", 21: "PHX", 22: "POR", 23: "SAC", 24: "SAS", 25: "OKC", 26: "UTA", 27: "WAS", 28: "TOR",
    29: "MEM", 30: "CHA",
}
# hoopR/ESPN abbreviation -> Kalshi-style code (incl. historical / alias spellings)
HOOPR_TO_KALSHI = {
    "GS": "GSW", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "NO": "NOP",
    "NJ": "BKN", "NJN": "BKN", "BRK": "BKN", "SEA": "OKC", "NOH": "NOP", "NOK": "NOP", "NOR": "NOP",
    "CHO": "CHA", "CHH": "CHA", "CHB": "CHA", "PHO": "PHX", "VAN": "MEM", "GSW": "GSW",
}
KALSHI_TO_HOOPR = {"GSW": "GS", "NYK": "NY", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH", "NOP": "NO"}


def kalshi_code(abbr: str, team_id: int | None = None) -> str | None:
    """hoopR abbreviation (any era) -> Kalshi-style franchise code.  team_id wins when given (franchise id).
    Returns None for non-NBA teams (All-Star EAST/WEST, WORLD, USA, ...)."""
    if team_id is not None:
        try:
            return FRANCHISE_BY_ID.get(int(team_id))
        except (TypeError, ValueError):
            return None
    a = str(abbr).strip().upper()
    a = HOOPR_TO_KALSHI.get(a, a)
    return a if a in FRANCHISE_BY_ID.values() else None


def hoopr_code(kalshi: str) -> str:
    return KALSHI_TO_HOOPR.get(kalshi, kalshi)


# ----------------------------------------------------------------------------- cache / download
def _load_meta() -> dict:
    try:
        return json.loads(META_PATH.read_text())
    except Exception:
        return {}


def _save_meta(m: dict) -> None:
    atomic_write_bytes(META_PATH, json.dumps(m, indent=1, sort_keys=True).encode())


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write to a temp file in the same folder, then os.replace (a crash never leaves a half file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.part")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def is_fresh(path: Path, season: int | None, now: float | None = None) -> bool:
    """Cached copy usable?  Old seasons: any existing non-empty file.  Current season: younger than 12 h."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    if season is not None and season >= CURRENT_SEASON:
        import time
        age_h = ((now if now is not None else time.time()) - path.stat().st_mtime) / 3600.0
        return age_h < MAX_AGE_H
    return True


def fetch(folder: str, fname: str, rel: str, season: int | None, offline: bool = False, session=None) -> Path | None:
    """Return local path of the cached file (downloading if needed).  None if absent (404 / offline)."""
    path = RAW / folder / fname
    if is_fresh(path, season) or offline:
        return path if path.exists() and path.stat().st_size > 0 else None
    import requests
    s = session or requests
    url = BASE + rel
    try:
        r = s.get(url, headers=UA, timeout=60)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] GET failed {rel}: {type(e).__name__}", flush=True)
        return path if path.exists() else None
    if r.status_code == 404:
        return None
    if r.status_code != 200 or not r.content:
        print(f"  [warn] {r.status_code} for {rel}", flush=True)
        return path if path.exists() else None
    if fname.endswith(".parquet") and r.content[:4] != b"PAR1":
        print(f"  [warn] not a parquet body for {rel}", flush=True)
        return path if path.exists() else None
    atomic_write_bytes(path, r.content)
    meta = _load_meta()
    meta.setdefault(f"{folder}/{fname}", {})["fetched_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _save_meta(meta)
    return path


def github_last_commit(rel: str, session=None) -> str | None:
    """api.github.com listing: ISO timestamp of the last commit that touched nba/<rel> (GET)."""
    import requests
    s = session or requests
    try:
        r = s.get(API_COMMITS, params={"path": "nba/" + rel, "per_page": 1}, headers=UA, timeout=30)
        if r.status_code == 200 and r.json():
            return r.json()[0]["commit"]["committer"]["date"]
    except Exception:  # noqa: BLE001
        pass
    return None


def ensure_data(offline: bool = False, seasons=range(FIRST_SEASON, CURRENT_SEASON + 1)) -> dict:
    """Download everything needed (idempotent).  Returns {'folder/name': Path|None}."""
    out: dict = {}
    jobs = []
    for y in seasons:
        jobs += [("game_rosters", f"game_rosters_{y}.parquet", f"game_rosters/parquet/game_rosters_{y}.parquet", y),
                 ("player_box", f"player_box_{y}.parquet", f"player_box/parquet/player_box_{y}.parquet", y),
                 ("team_box", f"team_box_{y}.parquet", f"team_box/parquet/team_box_{y}.parquet", y),
                 ("crosswalk", f"nba_team_crosswalk_{y}.parquet", f"crosswalk/parquet/nba_team_crosswalk_{y}.parquet", y)]
    for y in sorted({CURRENT_SEASON - 2, CURRENT_SEASON - 1, CURRENT_SEASON}):
        jobs.append(("rosters", f"rosters_{y}.parquet", f"rosters/parquet/rosters_{y}.parquet", y))
    jobs.append(("crosswalk", f"nba_player_crosswalk_{CURRENT_SEASON}.parquet",
                 f"crosswalk/parquet/nba_player_crosswalk_{CURRENT_SEASON}.parquet", CURRENT_SEASON))
    meta = _load_meta()
    for folder, fname, rel, y in jobs:
        was_fresh = is_fresh(RAW / folder / fname, y)
        p = fetch(folder, fname, rel, y, offline=offline)
        out[f"{folder}/{fname}"] = p
        if y == CURRENT_SEASON and not offline and not was_fresh and p is not None and folder != "crosswalk":
            c = github_last_commit(rel)
            if c:
                meta = _load_meta()
                meta.setdefault(f"{folder}/{fname}", {})["github_last_commit"] = c
                _save_meta(meta)
    return out


def asof_info(folder: str, fname: str, df: pd.DataFrame | None = None, date_col: str | None = None) -> dict:
    m = _load_meta().get(f"{folder}/{fname}", {})
    info = {"fetched_at_utc": m.get("fetched_at_utc"), "github_last_commit": m.get("github_last_commit"), "data_through": None}
    if df is not None and date_col and date_col in df.columns and len(df):
        info["data_through"] = str(pd.to_datetime(df[date_col]).max().date())
    return info


def fmt_asof(info: dict) -> str:
    """Human 'as of' string.  Never invents: missing parts are reported as unknown."""
    return (f"GitHub last commit {info.get('github_last_commit') or 'unknown'}; "
            f"downloaded {info.get('fetched_at_utc') or 'unknown (cached before stamping)'}; "
            f"latest game date in data {info.get('data_through') or 'n/a'}")


# ----------------------------------------------------------------------------- readers
def read_pq(path: Path | None, columns=None) -> pd.DataFrame | None:
    if path is None or not Path(path).exists():
        return None
    return pd.read_parquet(path, columns=columns)


def norm_name(s) -> str:
    """'Walker IV,Lonnie' / 'Lonnie Walker IV' / "De'Aaron Fox" -> 'lonnie walker' style key (suffix-free)."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s)
    if "," in s:
        last, first = s.split(",", 1)
        s = f"{first} {last}"
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s.replace("-", " "))
    toks = [t for t in s.split() if t not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(toks)


def load_game_dates(seasons) -> pd.DataFrame:
    """game_id -> (game_date, game_date_time, season_type) from team_box files."""
    parts = []
    for y in seasons:
        p = RAW / "team_box" / f"team_box_{y}.parquet"
        if p.exists():
            t = pd.read_parquet(p, columns=["game_id", "season", "season_type", "game_date", "game_date_time"])
            parts.append(t.drop_duplicates("game_id"))
    if not parts:
        return pd.DataFrame(columns=["game_id", "season", "season_type", "game_date", "game_date_time"])
    d = pd.concat(parts, ignore_index=True)
    d["game_id"] = d["game_id"].astype(str)
    d["game_date"] = pd.to_datetime(d["game_date"]).dt.strftime("%Y-%m-%d")
    return d


# ----------------------------------------------------------------------------- injury reports (local only)
def load_injury_latest(root: Path = ROOT) -> tuple[dict, dict]:
    """Latest official injury-report row per normalised player name, from LOCALLY parsed reports only
    (reports/output/nba_model/injury_parsed_v3.json + reports/output/nba_teammate_out/injury_parsed_tm.jsonl
    + data/derived/injury_reports_2027.jsonl, the live watcher's file for the current season).
    Returns ({name_key: row}, {'n_reports', 'latest_report_date'})."""
    reports = []
    p1 = root / "reports/output/nba_model/injury_parsed_v3.json"
    if p1.exists():
        try:
            d = json.loads(p1.read_text())
            for name, v in d.items():
                if isinstance(v, dict) and v.get("rows"):
                    reports.append((name, v.get("header"), v["rows"]))
        except Exception:  # noqa: BLE001
            pass
    p2 = root / "reports/output/nba_teammate_out/injury_parsed_tm.jsonl"
    if p2.exists():
        for ln in p2.read_text().splitlines():
            try:
                v = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            if v.get("rows"):
                reports.append((v.get("name"), v.get("header"), v["rows"]))
    p3 = root / "data/derived/injury_reports_2027.jsonl"           # written by scripts/injury_watch.py (current season)
    if p3.exists():
        for ln in p3.read_text(encoding="utf-8").splitlines():
            try:
                v = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(v, dict) and v.get("rows"):
                reports.append((v.get("name"), v.get("header"), v["rows"]))
    return latest_injury(reports)


def _parse_header(h) -> datetime | None:
    for f in ("%m/%d/%y %I:%M %p", "%m/%d/%Y %I:%M %p"):
        try:
            return datetime.strptime(str(h).strip(), f)
        except ValueError:
            pass
    return None


def latest_injury(reports) -> tuple[dict, dict]:
    """reports: iterable of (file_name, header, rows).  Pick for each player the row with the latest
    (game date, report publish time)."""
    best: dict = {}
    latest = None
    n = 0
    for fname, header, rows in reports:
        n += 1
        hd = _parse_header(header)
        for r in rows:
            try:
                gd = datetime.strptime(r["date"], "%m/%d/%Y")
            except Exception:  # noqa: BLE001
                continue
            key = norm_name(r.get("player"))
            if not key:
                continue
            rank = (gd, hd or datetime.min, str(fname))
            if key not in best or rank > best[key][0]:
                best[key] = (rank, {"status": r.get("status"), "date": gd.strftime("%Y-%m-%d"),
                                    "matchup": r.get("matchup"), "team": r.get("team"), "report": fname})
            if latest is None or gd > latest:
                latest = gd
    return {k: v[1] for k, v in best.items()}, {"n_reports": n, "latest_report_date": latest.strftime("%Y-%m-%d") if latest else None}


# ----------------------------------------------------------------------------- build
def box_aggregate(box: pd.DataFrame) -> pd.DataFrame:
    """Per (athlete_id, team_id) games / minutes / points where the player actually played."""
    b = box.copy()
    b["played"] = (~b["did_not_play"].fillna(False)) & (b["minutes"].fillna(0) > 0)
    listed = b.groupby(["athlete_id", "team_id"]).size().rename("listed_games")
    p = b[b["played"]]
    a = p.groupby(["athlete_id", "team_id"]).agg(gp=("game_id", "nunique"), minutes_total=("minutes", "sum"),
                                                 points_total=("points", "sum"), last_game_date=("game_date", "max"))
    a = a.join(listed, how="outer").fillna({"gp": 0, "minutes_total": 0.0, "points_total": 0.0, "listed_games": 0})
    a["mpg"] = (a["minutes_total"] / a["gp"].where(a["gp"] > 0)).round(1)
    a["ppg"] = (a["points_total"] / a["gp"].where(a["gp"] > 0)).round(1)
    return a.reset_index()


def build_rosters(paths: dict, now_stamp: str | None = None) -> dict:
    y = CURRENT_SEASON
    ros = read_pq(paths.get(f"rosters/rosters_{y}.parquet"))
    if ros is None:
        raise SystemExit(f"rosters_{y}.parquet unavailable (offline and not cached?)")
    box = read_pq(paths.get(f"player_box/player_box_{y}.parquet"))
    grs = read_pq(paths.get(f"game_rosters/game_rosters_{y}.parquet"))
    tb = read_pq(paths.get(f"team_box/team_box_{y}.parquet"))
    ros = ros.copy()
    ros["athlete_id"] = ros["athlete_id"].astype(int)
    ros["team_kalshi"] = [kalshi_code(a, t) for a, t in zip(ros["team_abbreviation"], ros["team_id"])]
    ros["team_hoopr"] = ros["team_abbreviation"]

    asof_r = asof_info("rosters", f"rosters_{y}.parquet")
    asof_g = asof_info("game_rosters", f"game_rosters_{y}.parquet")
    asof_b = asof_info("player_box", f"player_box_{y}.parquet", box, "game_date")
    asof_g["data_through"] = asof_b["data_through"]

    agg = box_aggregate(box) if box is not None else pd.DataFrame(columns=["athlete_id", "team_id"])
    df = ros.merge(agg, on=["athlete_id", "team_id"], how="left")
    for c in ("gp", "minutes_total", "points_total", "listed_games"):
        df[c] = df[c].fillna(0)
    inj, injmeta = load_injury_latest()
    season_start = f"{y - 1}-09-01"
    keys = df["full_name"].map(norm_name)
    df["injury_status_latest"] = [inj.get(k, {}).get("status") for k in keys]
    df["injury_report_date"] = [inj.get(k, {}).get("date") for k in keys]
    df["injury_report_matchup"] = [inj.get(k, {}).get("matchup") for k in keys]
    df["injury_is_current_season"] = [bool(isinstance(d, str) and d >= season_start) for d in df["injury_report_date"]]
    df["roster_source_asof"] = asof_r.get("github_last_commit") or asof_r.get("fetched_at_utc") or ""
    out = pd.DataFrame({
        "team_kalshi": df["team_kalshi"], "team_hoopr": df["team_hoopr"], "team_name": df["team_display_name"],
        "athlete_id": df["athlete_id"], "name": df["full_name"], "position": df["position_abbreviation"],
        "jersey": df["jersey"], "age": df["age"], "height": df["height"], "weight": df["weight"],
        "experience_years": df["experience_years"], "status": df["status_name"],
        f"gp_{y}": df["gp"].astype(int), f"minutes_{y}": df["minutes_total"].round(0).astype(int),
        f"mpg_{y}": df["mpg"], f"points_{y}": df["points_total"].astype(int), f"ppg_{y}": df["ppg"],
        "last_game_date": df["last_game_date"],
        "injury_status_latest": df["injury_status_latest"], "injury_report_date": df["injury_report_date"],
        "injury_report_matchup": df["injury_report_matchup"], "injury_is_current_season": df["injury_is_current_season"],
        "roster_source_asof": df["roster_source_asof"],
    }).sort_values(["team_kalshi", "name"]).reset_index(drop=True)
    return {"df": out, "raw_roster": ros, "box": box, "grs": grs, "tb": tb,
            "asof": {"rosters": asof_r, "game_rosters": asof_g, "player_box": asof_b},
            "injury_meta": injmeta, "inj_n_matched": int(df["injury_status_latest"].notna().sum()),
            "inj_current_n": int(df["injury_is_current_season"].sum())}


# ----------------------------------------------------------------------------- multi-source check
def check_sources(ros: pd.DataFrame, grs: pd.DataFrame | None, box: pd.DataFrame | None, gdates: pd.DataFrame,
                  ros_prev: pd.DataFrame | None = None) -> dict:
    """Pure comparison.  ros = rosters_<cur> (needs team_id, team_abbreviation, athlete_id, full_name);
    grs = game_rosters_<cur>; box = player_box_<cur>.  Returns dict of lists of disagreement dicts."""
    res = {"added_since_last_game": [], "dropped_since_last_game": [], "never_on_game_sheet": [],
           "box_not_on_roster": [], "roster_team_differs_from_box": [], "box_players_not_in_rosters": [],
           "no_games_yet": [], "prev_roster_team_changed": [], "sheet_team_differs": []}
    ros = ros.copy()
    ros["athlete_id"] = ros["athlete_id"].astype(int)
    ros["code"] = [kalshi_code(a, t) for a, t in zip(ros["team_abbreviation"], ros["team_id"])]
    name_by_id = dict(zip(ros["athlete_id"], ros["full_name"]))
    rteam = dict(zip(ros["athlete_id"], ros["code"]))
    if grs is not None and len(grs):
        g = grs.copy()
        g["game_id"] = g["game_id"].astype(str)
        g["code"] = [kalshi_code(a, t) for a, t in zip(g["team_abbreviation"], g["team_id"])]
        g = g.merge(gdates[["game_id", "game_date"]], on="game_id", how="left")
        g = g[g["code"].notna()]
        name_by_id.update(dict(zip(g["athlete_id"], g["athlete_display_name"])))
        lastsheet = g.sort_values(["game_date", "game_id"]).groupby("athlete_id").tail(1)
        for a, c, d in zip(lastsheet["athlete_id"], lastsheet["code"], lastsheet["game_date"]):
            if a in rteam and rteam[a] != c:
                res["sheet_team_differs"].append({"athlete_id": int(a), "name": name_by_id.get(a), "sheet_team": c,
                                                  "roster_team": rteam[a], "last_sheet_date": d})
        for code in sorted(ros["code"].dropna().unique()):
            R = set(ros.loc[ros["code"] == code, "athlete_id"])
            gt = g[g["code"] == code]
            if gt.empty:
                res["no_games_yet"].append(code)
                continue
            last = gt.sort_values(["game_date", "game_id"]).iloc[-1]
            G = set(gt.loc[gt["game_id"] == last["game_id"], "athlete_id"])
            allG = set(gt["athlete_id"])
            for a in sorted(R - G):
                res["added_since_last_game"].append(
                    {"team": code, "athlete_id": a, "name": name_by_id.get(a), "last_game": last["game_date"],
                     "on_any_2027_game_sheet": a in allG})
            for a in sorted(G - R):
                res["dropped_since_last_game"].append(
                    {"team": code, "athlete_id": a, "name": name_by_id.get(a), "last_game": last["game_date"],
                     "roster_team_now": rteam.get(a)})
            for a in sorted(R - allG):
                res["never_on_game_sheet"].append({"team": code, "athlete_id": a, "name": name_by_id.get(a)})
    if box is not None and len(box):
        b = box.copy()
        b["code"] = [kalshi_code(a, t) for a, t in zip(b["team_abbreviation"], b["team_id"])]
        b = b[b["code"].notna()]
        b["played"] = (~b["did_not_play"].fillna(False)) & (b["minutes"].fillna(0) > 0)
        for (a, code), grp in b[b["played"]].groupby(["athlete_id", "code"]):
            nm = grp["athlete_display_name"].iloc[0]
            if a not in rteam:
                res["box_players_not_in_rosters"].append({"team": code, "athlete_id": int(a), "name": nm, "gp": len(grp)})
            elif rteam[a] != code:
                res["roster_team_differs_from_box"].append(
                    {"athlete_id": int(a), "name": nm, "box_team": code, "roster_team": rteam[a], "gp": len(grp),
                     "last_box_date": str(grp["game_date"].max())})
        # box appearance for the right team but absent from that team's roster is covered by 'differs' / 'not in rosters'
    if ros_prev is not None and len(ros_prev):
        rp = ros_prev.copy()
        rp["athlete_id"] = rp["athlete_id"].astype(int)
        prev = {a: kalshi_code(x, t) for a, x, t in zip(rp["athlete_id"], rp["team_abbreviation"], rp["team_id"])}
        for a, code in rteam.items():
            if a in prev and prev[a] != code:
                res["prev_roster_team_changed"].append({"athlete_id": a, "name": name_by_id.get(a), "from": prev[a], "to": code})
    return res


# ----------------------------------------------------------------------------- markdown
def md_table(rows: list, cols: list) -> str:
    if not rows:
        return "_(none)_\n"
    s = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    for r in rows:
        s += "| " + " | ".join("" if r.get(c) is None or (isinstance(r.get(c), float) and pd.isna(r.get(c))) else str(r.get(c)) for c in cols) + " |\n"
    return s


def write_current_doc(b: dict, path: Path) -> None:
    df = b["df"]
    y = CURRENT_SEASON
    L = [f"# NBA rosters, {y - 1}-{str(y)[2:]} season (hoopR season {y})", "",
         f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `scripts/nba_rosters.py`. Read-only; source is the free "
         "hoopR data repo (ESPN-derived). Data file: `data/derived/rosters_current.csv`.", "",
         "## As-of stamps", "",
         f"- rosters_{y}.parquet: {fmt_asof(b['asof']['rosters'])}",
         f"- game_rosters_{y}.parquet: {fmt_asof(b['asof']['game_rosters'])}",
         f"- player_box_{y}.parquet: {fmt_asof(b['asof']['player_box'])}", "",
         "The rosters file carries no timestamp inside it; the GitHub last-commit time is the best available as-of. "
         "A roster move made after that commit (waiver, signing, trade) is NOT here.", "",
         "## Injury status", ""]
    im = b["injury_meta"]
    L.append(f"Locally parsed official injury reports: {im['n_reports']} files, latest report game date "
             f"**{im['latest_report_date']}**. Players on the {y} rosters with a 2026-27 report: **{b['inj_current_n']}**. ")
    if b["inj_current_n"] == 0:
        L.append("**No 2026-27 injury reports are parsed locally**, so `injury_status_latest` in the CSV is the player's last "
                 "status from the previous season (see `injury_report_date`) and `injury_is_current_season` is False. "
                 "Treat it as stale; it says nothing about today. "
                 "(The live watcher scripts/injury_watch.py appends 2026-27 reports to data/derived/injury_reports_2027.jsonl; "
                 "re-run this script after it has found some.)")
    L += ["", "## Roster counts", ""]
    cnt = df.groupby(["team_kalshi", "team_hoopr"]).size().reset_index(name="players")
    L.append(f"{len(cnt)} teams, {len(df)} players. Counts include two-way / training-camp style slots as listed by ESPN "
             "(the source has no two-way flag). Teams outside 14-18: " +
             (", ".join(f"{r.team_kalshi}={r.players}" for r in cnt.itertuples() if not 14 <= r.players <= 18) or "none") + ".")
    L.append("")
    L.append(md_table(cnt.to_dict("records"), ["team_kalshi", "team_hoopr", "players"]))
    for (code, hc, tn), g in df.groupby(["team_kalshi", "team_hoopr", "team_name"]):
        L += [f"## {code} ({hc}) - {tn} - {len(g)} players", ""]
        rows = g.sort_values(f"minutes_{y}", ascending=False).to_dict("records")
        for r in rows:
            r["inj"] = (f"{r['injury_status_latest']} ({r['injury_report_date']})" if r["injury_status_latest"] else "")
        L.append(md_table(rows, ["name", "position", "jersey", "age", "experience_years", "status", f"gp_{y}", f"mpg_{y}", f"ppg_{y}", "inj"]))
    path.write_text("\n".join(L), encoding="utf-8")


def write_check_doc(res: dict, b: dict, path: Path, ros_prev_asof: dict | None = None) -> None:
    y = CURRENT_SEASON
    L = [f"# Roster multi-source check (season {y})", "",
         f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `scripts/nba_rosters.py`.", "",
         "Sources compared: **A** rosters_%d (current team list), **B** game_rosters_%d (who was on each game sheet, active or inactive), "
         "**C** player_box_%d (who actually played)." % (y, y, y), "",
         f"- A: {fmt_asof(b['asof']['rosters'])}", f"- B: {fmt_asof(b['asof']['game_rosters'])}", f"- C: {fmt_asof(b['asof']['player_box'])}", "",
         "Reading guide: B lists only the ~13-15 players on a game sheet, A lists 16-18 (two-way / reserve players do not appear on "
         "game sheets), so 'on roster but not on the last game sheet' is mostly normal. The informative lines are the ones marked REAL SIGNAL.", "",
         "## Summary", ""]
    for k, lab in [("added_since_last_game", "A-only vs team's last game sheet (on roster, not on last sheet)"),
                   ("dropped_since_last_game", "REAL SIGNAL: on team's last game sheet but NOT on current roster (waived/traded/moved since)"),
                   ("never_on_game_sheet", "on roster, never on any game sheet yet this season"),
                   ("sheet_team_differs", "REAL SIGNAL: last game-sheet team differs from roster team"),
                   ("roster_team_differs_from_box", "REAL SIGNAL: played for team X in box score, roster says team Y"),
                   ("box_players_not_in_rosters", "REAL SIGNAL: played in box score, absent from rosters file entirely"),
                   ("prev_roster_team_changed", f"rosters_{y - 1} -> rosters_{y} team changed (offseason moves per roster snapshots)")]:
        L.append(f"- {lab}: **{len(res[k])}**")
    L.append(f"- Teams with no games yet (no sheet to compare): {', '.join(res['no_games_yet']) or 'none'}")
    L.append("")
    L += ["## REAL SIGNAL: on last game sheet, not on current roster", "",
          md_table(res["dropped_since_last_game"], ["team", "name", "athlete_id", "last_game", "roster_team_now"]),
          "## REAL SIGNAL: last game-sheet team differs from roster team", "",
          md_table(res["sheet_team_differs"], ["name", "athlete_id", "sheet_team", "roster_team", "last_sheet_date"]),
          "## REAL SIGNAL: box-score team differs from roster team", "",
          md_table(res["roster_team_differs_from_box"], ["name", "athlete_id", "box_team", "roster_team", "gp", "last_box_date"]),
          "## REAL SIGNAL: played but absent from rosters file", "",
          md_table(res["box_players_not_in_rosters"], ["team", "name", "athlete_id", "gp"]),
          "## On roster, not on the team's last game sheet", "",
          md_table(res["added_since_last_game"], ["team", "name", "athlete_id", "last_game", "on_any_2027_game_sheet"]),
          "## On roster, never on a game sheet this season", "",
          md_table(res["never_on_game_sheet"], ["team", "name", "athlete_id"]),
          f"## Offseason team changes between rosters_{y - 1} and rosters_{y}", "",
          "(snapshot-vs-snapshot; the rosters_%d file's own date is unknown, see as-of note)" % (y - 1), "",
          md_table(res["prev_roster_team_changed"], ["name", "athlete_id", "from", "to"])]
    path.write_text("\n".join(L), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="use cached files only")
    a = ap.parse_args(argv)
    DERIVED.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    paths = ensure_data(offline=a.offline)
    missing = [k for k, v in paths.items() if v is None]
    if missing:
        print("not available (404/offline):", ", ".join(missing[:10]), "..." if len(missing) > 10 else "")
    b = build_rosters(paths)
    b["df"].to_csv(DERIVED / "rosters_current.csv", index=False)
    write_current_doc(b, DOCS / "ROSTERS_CURRENT.md")
    gd = load_game_dates([CURRENT_SEASON])
    prev = read_pq(paths.get(f"rosters/rosters_{CURRENT_SEASON - 1}.parquet"))
    res = check_sources(b["raw_roster"], b["grs"], b["box"], gd, prev)
    write_check_doc(res, b, DOCS / "ROSTERS_CHECK.md")
    cnt = b["df"].groupby("team_kalshi").size()
    print("teams", len(cnt), "players", len(b["df"]), "odd counts:", {k: int(v) for k, v in cnt.items() if not 14 <= v <= 18})
    print("asof", b["asof"])
    print("injury", b["injury_meta"], "matched", b["inj_n_matched"], "current", b["inj_current_n"])
    print({k: len(v) for k, v in res.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
