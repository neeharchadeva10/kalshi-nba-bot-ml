"""24/7 NBA injury watcher (research tool; NO betting, NO Kalshi calls, NO Discord).

Two sources, both PUBLIC and GET-only:
  OFFICIAL  the NBA injury-report PDFs  https://ak-static.cms.nba.com/referee/injury/Injury-Report_YYYY-MM-DD_HH_MMAM|PM.pdf
            (15-minute stamps since Dec 2025, e.g. 05_00PM, 06_45PM; a PDF's /CreationDate is its real publication time and
            equals the stamp for the 15-minute files; earlier seasons used hourly names ...05PM.pdf created at H:30, so an
            on-the-hour stamp whose 15-minute name is 404 also tries the hourly name).  Each run probes at most MAX_GETS names
            whose stamp could already exist (stamp <= now, within the last PROBE_WINDOW_H hours, ET dates yesterday/today/tomorrow);
            404 = not yet.  New PDFs are saved atomically into data/raw/nba_injury/, parsed with the existing v3 parser and appended
            (one line per PDF, deduped by name) to data/derived/injury_reports_2027.jsonl.
            Each new report is diffed against the previous report that covers the same game (date + matchup):
              NEW (listed now, not before) / STATUS_CHANGE (e.g. Questionable->Out) / REMOVED (cleared from the report).
            LATE = the report was published within 90 min before the game's scheduled tip (tip-90 min <= publication <= tip).  The scheduled tip is the
            game time printed in the report itself (official, ET); the Kalshi schedule is NOT called (GET-only to NBA PDFs + RSS).
            A game with no earlier report (or a team whose earlier entry was NOT YET SUBMITTED) has no baseline: only
            Out/Doubtful/Questionable star/starter listings are alerted then (flag first_report).
  NEWS      injury headlines from the RealGM / ESPN / CBS RSS feeds, matched to roster players, type NEWS_INJURY, always marked
            "news, unofficial".  Pure trade / signing / waiver items are dropped (the trade watcher owns them).

Writes  logs/injuries/alerts.jsonl (rotated monthly), summary.json (latest 60 alerts + counts + last official report + feed errors),
        seen.json (news dedupe), state.json (probe memory, diffed reports), run.lock, raw/<feed>.xml.
Feed text and PDF content are untrusted DATA: nothing is executed or followed (same XML guards as trade_watch).

Usage: .venv/Scripts/python.exe scripts/injury_watch.py [--dry-run] [--max-age-hours 24]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[0]
sys.path.insert(0, str(HERE))
import trade_watch as TW  # noqa: E402  (feed fetch/parse guards, lock, rotation, atomic write, names, tiers)

ET = ZoneInfo("America/New_York")
LOGS = ROOT / "logs" / "injuries"
PDF_DIR = ROOT / "data" / "raw" / "nba_injury"
REPORTS_JSONL = ROOT / "data" / "derived" / "injury_reports_2027.jsonl"
ROSTER_CSV = TW.ROSTER_CSV
NOTIFIED_JSON = ROOT / "logs" / "early_overs_notified.json"
PARSER_DIR = ROOT / "reports" / "output" / "nba_model"

BASE_URL = "https://ak-static.cms.nba.com/referee/injury/"
USER_AGENT = TW.USER_AGENT                     # "KalshiPM-research/1.0"
TIMEOUT_S = 20
MAX_PDF_BYTES = 3 * 1024 * 1024
MAX_GETS = 6                    # PDF GETs per run
PROBE_WINDOW_H = 6              # only stamps from the last 6 h
FIRST_STAMP_MIN = 11 * 60       # earliest daily stamp ever seen: 11:00 AM
FINAL_MISS_Q_S = 25 * 60        # a 15-minute name still 404 25 min after its stamp is treated as never published
FINAL_MISS_H_S = 65 * 60        # an hourly name (created at H:30) still 404 65 min after its stamp
LATE_MIN = 90
ALERT_MAX_AGE_H = 36            # reports older than this when first seen are stored but not alerted
SUMMARY_ALERTS = 60
NEWS_MAX_AGE_H = 24
FIRST_REPORT_STATUSES = ("Out", "Doubtful", "Questionable")
FIRST_REPORT_TIERS = ("star", "starter")
PARSE_RETRIES = 3

TEAMNAME2CODE = {  # glued team names as printed in the PDFs (copied from research #62 e2_lib.TEAMNAME2CODE)
    "AtlantaHawks": "ATL", "BrooklynNets": "BKN", "BostonCeltics": "BOS", "CharlotteHornets": "CHA", "ChicagoBulls": "CHI",
    "ClevelandCavaliers": "CLE", "DallasMavericks": "DAL", "DenverNuggets": "DEN", "DetroitPistons": "DET",
    "GoldenStateWarriors": "GSW", "HoustonRockets": "HOU", "IndianaPacers": "IND", "LAClippers": "LAC",
    "LosAngelesClippers": "LAC", "LosAngelesLakers": "LAL", "MemphisGrizzlies": "MEM", "MiamiHeat": "MIA",
    "MilwaukeeBucks": "MIL", "MinnesotaTimberwolves": "MIN", "NewOrleansPelicans": "NOP", "NewYorkKnicks": "NYK",
    "OklahomaCityThunder": "OKC", "OrlandoMagic": "ORL", "Philadelphia76ers": "PHI", "PhoenixSuns": "PHX",
    "PortlandTrailBlazers": "POR", "SacramentoKings": "SAC", "SanAntonioSpurs": "SAS", "TorontoRaptors": "TOR",
    "UtahJazz": "UTA", "WashingtonWizards": "WAS"}
CODE_FIX = {"PHO": "PHX", "GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS", "BRK": "BKN", "CHO": "CHA"}


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ official: file names + cadence
def name15(d: datetime) -> str:
    return "Injury-Report_" + d.strftime("%Y-%m-%d_%I_%M%p") + ".pdf"


def name1h(d: datetime) -> str:
    return "Injury-Report_" + d.strftime("%Y-%m-%d_%I%p") + ".pdf"


def candidate_stamps(now: datetime, window_h: float = PROBE_WINDOW_H) -> list[datetime]:
    """15-minute stamps (ET, 11:00 AM .. 11:45 PM) of yesterday / today / tomorrow that are <= now and within the window,
    newest first.  A stamp later than now cannot have been published, so it is never probed."""
    now_et = now.astimezone(ET)
    lo = now_et - timedelta(hours=window_h)
    out = []
    for dd in (-1, 0, 1):
        d = now_et.date() + timedelta(days=dd)
        for mins in range(FIRST_STAMP_MIN, 24 * 60, 15):
            st = datetime(d.year, d.month, d.day, mins // 60, mins % 60, tzinfo=ET)
            if lo < st <= now_et:
                out.append(st)
    return sorted(out, reverse=True)


_CD = re.compile(rb"/CreationDate\(D:(\d{14})([+-]\d{2})'(\d{2})'\)")


def creation_of(body: bytes):
    """PDF /CreationDate -> UTC epoch seconds, or None (same reader as research #62 / collect_injury_v3)."""
    m = _CD.search(body or b"")
    if not (body.startswith(b"%PDF") and m):
        return None
    dt = datetime.strptime(m.group(1).decode(), "%Y%m%d%H%M%S")
    sign = 1 if m.group(2)[:1] == b"+" else -1
    off = timedelta(hours=int(m.group(2)[1:]), minutes=int(m.group(3))) * sign
    return (dt - off).replace(tzinfo=timezone.utc).timestamp()


def pdf_get(url: str):
    """GET one injury PDF.  Returns bytes, or None for 404/403 (not published).  Raises on anything else."""
    import requests
    r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf"}, timeout=TIMEOUT_S, stream=True)
    try:
        if r.status_code in (403, 404):
            return None
        r.raise_for_status()
        data = r.raw.read(MAX_PDF_BYTES + 1, decode_content=True)
    finally:
        r.close()
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("pdf too large")
    return data


def save_pdf(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    tmp.write_bytes(body)
    os.replace(tmp, path)


def default_parse(path: Path) -> dict:
    sys.path.insert(0, str(PARSER_DIR))
    import injury_parse_v3 as P  # noqa: E402
    return P.parse_any(path)


# ------------------------------------------------------------------ reports file
def read_reports(path: Path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("name"):
                    out.append(r)
    except OSError:
        pass
    return out


def append_report(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ players
def key_letters(s: str) -> str:
    return re.sub(r"[^a-z]", "", TW.norm_name(s))


def injury_key(raw: str) -> str:
    """'PippenJr.,Scotty' -> 'scottypippen' (same rule as research #62 norm_injury_name)."""
    import unicodedata
    if "," in raw:
        last, first = raw.split(",", 1)
    else:
        last, first = raw, ""
    last_c = unicodedata.normalize("NFKD", last).encode("ascii", "ignore").decode()
    m = re.match(r"^(.*?)(Jr|Sr|II|III|IV)\.?$", last_c)
    if m and len(m.group(1)) > 2:
        last_c = m.group(1)
    return re.sub(r"[^a-z]", "", (first + last_c).lower().replace("jr.", ""))


def display_name(raw: str) -> str:
    """'Antetokounmpo,Giannis' -> 'Giannis Antetokounmpo' (best effort when the player is not on the roster file)."""
    if "," not in raw:
        return raw
    last, first = raw.split(",", 1)
    last = re.sub(r"(?<=[a-z])(Jr\.|Sr\.|II|III|IV)$", r" \1", last)
    return ("%s %s" % (first.strip(), last.strip())).strip()


class Players:
    """Roster index with this-season / last-season minutes.  tier: mpg >= 28 star, 20-28 starter, 12-20 bench, < 12 deep;
    this season's mpg is used once the player has >= 3 games, else last season's (box scores)."""

    def __init__(self, rows: list[dict]):
        self.by_key: dict[str, list[dict]] = {}
        for r in rows:
            self.by_key.setdefault(key_letters(r["name"]), []).append(r)

    @classmethod
    def load(cls, roster_csv=ROSTER_CSV, stats=None):
        import pandas as pd
        stats = stats if stats is not None else {}
        df = pd.read_csv(roster_csv)
        rows = []
        for x in df.to_dict("records"):
            aid = None if x.get("athlete_id") != x.get("athlete_id") else int(x["athlete_id"])
            age = x.get("age")
            gp26 = x.get("gp_2027")
            cur = isinstance(gp26, (int, float)) and gp26 == gp26 and gp26 >= 3
            last = stats.get(aid) or {}
            mpg = x.get("mpg_2027") if cur else last.get("mpg")
            ppg = x.get("ppg_2027") if cur else last.get("ppg")
            rows.append({"name": str(x["name"]), "team": str(x["team_kalshi"]), "age": None if age != age else int(age), "athlete_id": aid,
                         "mpg": None if mpg is None or mpg != mpg else round(float(mpg), 1),
                         "ppg": None if ppg is None or ppg != ppg else round(float(ppg), 1),
                         "mpg_src": ("2026-27" if cur else "2025-26") if mpg is not None and mpg == mpg else None})
        return cls(rows)

    def lookup(self, raw_key: str, team: str | None = None):
        c = self.by_key.get(raw_key) or []
        if team:
            m = [p for p in c if p["team"] == team]
            if m:
                return m[0]
        return c[0] if len(c) == 1 else None

    @staticmethod
    def info(p: dict | None, raw: str = "", team: str | None = None) -> dict:
        if p is None:
            return {"player": display_name(raw), "team": team, "age": None, "mpg": None, "ppg": None, "mpg_src": None,
                    "tier": "none", "veteran": False}
        return {"player": p["name"], "team": team or p["team"], "age": p["age"], "mpg": p["mpg"], "ppg": p["ppg"],
                "mpg_src": p["mpg_src"], "tier": TW.tier_of(p["mpg"]), "veteran": TW.is_veteran(p["age"])}


def bot_games(path: Path = NOTIFIED_JSON) -> set:
    """{(game_date_iso, away, home)} from the early-overs bot's notified picks ('2026-10-22|26OCT22DENOKC')."""
    j = TW.read_json(path, {})
    out = set()
    for k, v in j.items():
        if not isinstance(v, dict) or "|" not in k:
            continue
        d, code = k.split("|", 1)
        if len(code) >= 13:
            out.add((d, code[7:10], code[10:13]))
    return out


# ------------------------------------------------------------------ official: diff
def tip_of(date_s: str, time_s: str | None, created: float | None = None):
    """Scheduled tip printed in the report ('10/23/2026' + '07:30(ET)') -> aware ET datetime, else None.
    The PDF prints a 12-hour clock WITHOUT am/pm.  NBA tips are afternoon / evening, so 12:xx is read as noon and 1:00-11:59 as
    PM (a 10:30 pm tip must not become 10:30 am; the report's own time cannot disambiguate a report listing next-day games).
    Known limitation: a rare morning tip (about 10-11 am) is read 12 h late, so it can never be flagged LATE.
    `created` is accepted for API symmetry and not used."""
    try:
        t = (time_s or "").replace("(ET)", "").strip()
        base = datetime.strptime(date_s + " " + t, "%m/%d/%Y %H:%M")
    except (TypeError, ValueError):
        return None
    return base.replace(hour=base.hour % 12 + 12, tzinfo=ET)


def group_games(rep: dict) -> dict:
    """{(date, matchup): {'rows': {team: {key: (status, raw)}}, 'nys': {team}, 'time': 'hh:mm(ET)', 'away', 'home'}}"""
    games: dict = {}
    for r in rep.get("rows") or []:
        try:
            d, m = r["date"], r["matchup"]
            at = m.split("@")
            away, home = CODE_FIX.get(at[0], at[0]), CODE_FIX.get(at[1], at[1])
        except (KeyError, IndexError, AttributeError, TypeError):
            continue
        g = games.setdefault((d, m), {"rows": {}, "nys": set(), "time": r.get("time"), "away": away, "home": home})
        t = TEAMNAME2CODE.get(r.get("team"))
        if t is None or not r.get("player"):
            continue
        g["rows"].setdefault(t, {})[injury_key(r["player"])] = (r.get("status"), r["player"])
    for n in rep.get("nys") or []:
        try:
            d, m, tn = n
            at = m.split("@")
        except (ValueError, TypeError, AttributeError):
            continue
        g = games.setdefault((d, m), {"rows": {}, "nys": set(), "time": None, "away": CODE_FIX.get(at[0], at[0]),
                                      "home": CODE_FIX.get(at[1], at[1])})
        t = TEAMNAME2CODE.get(tn)
        if t:
            g["nys"].add(t)
    return games


def diff_game(prev: dict | None, new: dict) -> list[dict]:
    """Changes between two group_games() entries of the same game.  prev None = no baseline for the game."""
    out = []
    for team, players in new["rows"].items():
        before = (prev["rows"].get(team) if prev else None)
        if not before:                                                   # no baseline for this team
            for k, (st, raw) in players.items():
                if st in FIRST_REPORT_STATUSES:
                    out.append({"type": "NEW", "team": team, "key": k, "raw": raw, "old": None, "new": st, "first_report": True})
            continue
        for k, (st, raw) in players.items():
            if k not in before:
                out.append({"type": "NEW", "team": team, "key": k, "raw": raw, "old": None, "new": st, "first_report": False})
            elif before[k][0] != st:
                out.append({"type": "STATUS_CHANGE", "team": team, "key": k, "raw": raw, "old": before[k][0], "new": st, "first_report": False})
        for k, (st, raw) in before.items():
            if k not in players:
                out.append({"type": "REMOVED", "team": team, "key": k, "raw": raw, "old": st, "new": None, "first_report": False})
    if prev:                                                             # team with an earlier list but none now (and not 'not yet submitted')
        for team, before in prev["rows"].items():
            if team not in new["rows"] and team not in new["nys"] and before:
                for k, (st, raw) in before.items():
                    out.append({"type": "REMOVED", "team": team, "key": k, "raw": raw, "old": st, "new": None, "first_report": False})
    return out


def previous_for_game(reports: list[dict], idx: int, gkey) -> dict | None:
    """Group of the latest report BEFORE reports[idx] (by creation time) that has rows or NYS for the game."""
    for j in range(idx - 1, -1, -1):
        g = group_games(reports[j]).get(gkey)
        if g is not None and (g["rows"] or g["nys"]):
            return g
    return None


def official_alerts(reports: list[dict], idx: int, players: Players, bots: set, now: datetime) -> list[dict]:
    """Alerts produced by reports[idx] (reports sorted by creation time)."""
    rep = reports[idx]
    created = rep.get("creation_utc")
    out = []
    for gkey, g in group_games(rep).items():
        d_s, matchup = gkey
        prev = previous_for_game(reports, idx, gkey)
        tip = tip_of(d_s, g.get("time"), created)
        if tip is None and prev:
            tip = tip_of(d_s, prev.get("time"), created)
        try:
            game_iso = datetime.strptime(d_s, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            game_iso = d_s
        late = bool(tip is not None and created is not None and tip.timestamp() - LATE_MIN * 60 <= created <= tip.timestamp())
        for ch in diff_game(prev, g):
            p = players.lookup(ch["key"], ch["team"])
            info = Players.info(p, ch["raw"], ch["team"])
            if ch.get("first_report") and (info["tier"] not in FIRST_REPORT_TIERS
                                           or (tip is not None and created is not None and created > tip.timestamp())):
                continue                       # baseline listing: notable players only, and not for a game that already started
            opp = g["home"] if ch["team"] == g["away"] else g["away"]
            aid = hashlib.sha1("|".join([rep["name"], d_s, matchup, ch["team"], ch["key"], ch["type"], str(ch["new"])]).encode()).hexdigest()[:12]
            out.append({"id": aid, "ts_utc": iso(now), "source": "OFFICIAL", "official": True, "type": ch["type"],
                        "player": info["player"], "team": ch["team"], "opponent": opp, "matchup": matchup, "game_date": game_iso,
                        "tip_et": tip.isoformat() if tip else None,
                        "min_to_tip": round((tip.timestamp() - created) / 60) if (tip is not None and created is not None) else None,
                        "old_status": ch["old"], "new_status": ch["new"], "first_report": ch["first_report"], "late": late,
                        "bot_game": (game_iso, g["away"], g["home"]) in bots, "tier": info["tier"], "mpg": info["mpg"], "ppg": info["ppg"],
                        "mpg_src": info["mpg_src"], "age": info["age"], "veteran": info["veteran"], "report": rep["name"],
                        "report_created_utc": iso(datetime.fromtimestamp(created, timezone.utc)) if created else None,
                        "headline": "%s %s: %s -> %s" % (info["player"], ch["type"], ch["old"] or "-", ch["new"] or "cleared")})
    return out


# ------------------------------------------------------------------ news
_HARD = re.compile(r"(?<![A-Za-z0-9])(?:ruled out|questionable|doubtful|probable|sprain(?:ed)?|strain(?:ed)?|fracture[sd]?|surgery|mri|"
                   r"day-to-day|sidelined|will miss|miss(?:es|ed)?|illness|load management|scratch(?:ed)?|injur\w*)(?![A-Za-z0-9])", re.I)
_SOFT = re.compile(r"(?<![A-Za-z0-9])(?:return(?:s|ed|ing)?|available|will play|rest(?:ing|s|ed)?)(?![A-Za-z0-9])", re.I)
# 'out' alone is far too common ("blow out", "bounce out"): count it only in injury-style phrases
_OUT = re.compile(r"(?:\b(?:is|are|be|been|being|remains?|stays?|officially|also|listed|ruled|sits?|sat)\s+out\b|"
                  r"\bout\s+(?:for|with|vs\.?|against|tonight|tomorrow|indefinitely|until|at least|several|multiple|the|\d))", re.I)
_STATUS_WORDS = [("Out", re.compile(r"ruled out|\bout\b|will miss|sidelined", re.I)), ("Doubtful", re.compile(r"doubtful", re.I)),
                 ("Questionable", re.compile(r"questionable|day-to-day", re.I)), ("Probable", re.compile(r"probable", re.I)),
                 ("Available", re.compile(r"will play|available|cleared|returns?\b", re.I))]
_PURE_MOVES = ("TRADE", "SIGNING", "WAIVER", "TWO_WAY_10_DAY")


def injury_hits(text: str) -> list[str]:
    hits = [m.group(0).lower() for m in _HARD.finditer(text or "")]
    hits += [m.group(0).lower().strip() for m in _OUT.finditer(text or "")]
    hits += [m.group(0).lower() for m in _SOFT.finditer(text or "")]
    return sorted(set(hits))


def news_status(text: str):
    for st, rx in _STATUS_WORDS:
        if rx.search(text or ""):
            return st
    return None


def is_injury_news(title: str, description: str, has_player: bool) -> list[str]:
    """Keyword hits (empty = not injury news).  Needs a roster player.  Keywords are searched in the title; the description
    only for hard injury words (the soft words return / available / rest are title-only).  A pure trade / signing / waiver item without a hard injury word is dropped."""
    if not has_player:
        return []
    hits = injury_hits(title)
    if not hits:                                    # description: only hard words / injury-style 'out' (not return / available / rest)
        hits = [h for h in injury_hits(description) if _HARD.fullmatch(h) or _OUT.search(h)]
    if not hits:
        return []
    if TW.classify(title, description, True) in _PURE_MOVES and not _HARD.search(title or ""):
        return []
    return hits


def news_alerts(items: list[tuple[str, dict]], players_roster: dict, plist: Players, stats_now: datetime, bot_teams: set,
                seen: dict, max_age_h: float) -> tuple[list[dict], int, int]:
    out, n_new, n_skipped = [], 0, 0
    cutoff = stats_now - timedelta(hours=max_age_h)
    for feed, it in items:
        k = TW.item_key(it)
        if k in seen:
            continue
        seen[k] = iso(stats_now)
        pub = TW.parse_pub(it["pub"])
        if pub is not None and pub < cutoff:
            continue
        n_new += 1
        pl, tm = TW.match_item(it, players_roster)
        title_pl = TW.find_players(it["title"], players_roster)
        hits = is_injury_news(it["title"], it["description"], bool(title_pl) or bool(pl))
        if not hits:
            n_skipped += 1
            continue
        infos = []
        for p in pl:
            infos.append(Players.info(plist.lookup(key_letters(p["name"]), p["team"]), p["name"], p["team"]))
        ages = [i["age"] for i in infos if i["age"] is not None]
        top = max((i["tier"] for i in infos), key=lambda t: TW.TIER_RANK[t], default="none")
        first = infos[0] if infos else {}
        teams = sorted({i["team"] for i in infos if i["team"]}) or tm
        out.append({"id": "n" + TW.alert_id(k), "ts_utc": iso(stats_now), "source": "NEWS", "official": False, "label": "news, unofficial",
                    "feed": feed, "type": "NEWS_INJURY", "player": first.get("player"), "team": teams[0] if teams else None,
                    "teams": teams, "players": [i["player"] for i in infos], "opponent": None, "game_date": None, "tip_et": None,
                    "old_status": None, "new_status": news_status(it["title"]) or news_status(it["description"]),
                    "first_report": False, "late": False, "bot_game": bool(set(teams) & bot_teams), "tier": top,
                    "mpg": first.get("mpg"), "ppg": first.get("ppg"), "age": max(ages) if ages else None,
                    "veteran": any(i["veteran"] for i in infos), "keywords": hits, "headline": it["title"], "link": it["link"], "pub": it["pub"]})
    uniq, sigs = [], set()
    for a in out:                                   # same story from two feeds in one run
        sig = (tuple(sorted(a["players"])), tuple(a["keywords"]))
        if a["players"] and sig in sigs:
            continue
        sigs.add(sig)
        uniq.append(a)
    return uniq, n_new, n_skipped


# ------------------------------------------------------------------ the run
def run(fetch_pdf=pdf_get, fetch_feed=TW.http_get, now: datetime | None = None, logs: Path = LOGS, pdf_dir: Path = PDF_DIR,
        reports_path: Path = REPORTS_JSONL, parse_fn=None, plist: Players | None = None, roster: dict | None = None,
        notified_path: Path = NOTIFIED_JSON, feeds=TW.FEEDS, max_gets: int = MAX_GETS, news_max_age_h: float = NEWS_MAX_AGE_H,
        dry_run: bool = False) -> dict:
    now = now or datetime.now(timezone.utc)
    parse_fn = parse_fn or default_parse
    logs.mkdir(parents=True, exist_ok=True)
    with TW.RunLock(logs / "run.lock") as lk:
        if not lk.held:
            return {"skipped": "another run holds the lock"}
        summ_path, seen_path, state_path, alerts_path = logs / "summary.json", logs / "seen.json", logs / "state.json", logs / "alerts.jsonl"
        summary = TW.read_json(summ_path, {})
        seen = TW.read_json(seen_path, {})
        state = TW.read_json(state_path, {})
        state.setdefault("final_missing", {})
        state.setdefault("diffed", [])
        state.setdefault("parse_fail", {})
        errors = []
        try:
            roster = roster if roster is not None else TW.load_roster()
        except Exception as e:  # noqa: BLE001
            roster = {}
            errors.append("roster: %s" % type(e).__name__)
        try:
            if plist is None:
                try:
                    stats = TW.load_player_stats()
                except Exception as e:  # noqa: BLE001
                    stats = {}
                    errors.append("stats: %s" % type(e).__name__)
                plist = Players.load(stats=stats)
        except Exception as e:  # noqa: BLE001
            plist = Players([])
            errors.append("players: %s" % type(e).__name__)
        bots = bot_games(notified_path)

        # ---------------- official: probe + download + parse
        reports = read_reports(reports_path)
        have = {r["name"] for r in reports}
        probe = {"gets": 0, "not_found": 0, "found": [], "errors": 0, "candidates": 0}
        new_names = []
        stamps = candidate_stamps(now)
        probe["candidates"] = len(stamps)
        fm = state["final_missing"]
        for st in stamps:
            variants = [(name15(st), FINAL_MISS_Q_S)] + ([(name1h(st), FINAL_MISS_H_S)] if st.minute == 0 else [])
            for nm, final_s in variants:
                if nm in have or nm in fm:
                    continue
                path = pdf_dir / nm
                if path.exists() and path.stat().st_size > 1000:        # on disk (earlier run / parse retry): no GET
                    new_names.append(nm)
                    continue
                if dry_run or probe["gets"] >= max_gets:
                    continue
                probe["gets"] += 1
                try:
                    body = fetch_pdf(BASE_URL + nm)
                except Exception as e:  # noqa: BLE001
                    probe["errors"] += 1
                    errors.append("pdf %s: %s" % (nm, type(e).__name__))
                    continue
                if body is None:
                    probe["not_found"] += 1
                    if (now - st).total_seconds() >= final_s:
                        fm[nm] = iso(now)
                    continue
                if not (body.startswith(b"%PDF") and len(body) > 1000 and creation_of(body) is not None):
                    probe["errors"] += 1
                    errors.append("pdf %s: not a PDF with CreationDate" % nm)
                    continue
                save_pdf(path, body)
                probe["found"].append(nm)
                new_names.append(nm)
        new_reports = []
        for nm in sorted(set(new_names)):
            path = pdf_dir / nm
            try:
                body = path.read_bytes()
                created = creation_of(body)
                if created is None:
                    raise ValueError("no CreationDate")
                parsed = parse_fn(path)
                rec = {"name": nm, "header": parsed.get("header"), "creation_utc": created, "created_iso": iso(datetime.fromtimestamp(created, timezone.utc)),
                       "layout": parsed.get("layout"), "rows": parsed.get("rows") or [],
                       "nys": [list(x) for x in (parsed.get("nys") or [])], "problems": [list(x) for x in (parsed.get("problems") or [])][:20],
                       "parsed_utc": iso(now)}
            except Exception as e:  # noqa: BLE001  never crash on a bad PDF
                state["parse_fail"][nm] = int(state["parse_fail"].get(nm, 0)) + 1
                errors.append("parse %s: %s" % (nm, type(e).__name__))
                if state["parse_fail"][nm] < PARSE_RETRIES:
                    continue
                rec = {"name": nm, "error": "%s: %s" % (type(e).__name__, str(e)[:100]), "creation_utc": None, "rows": [], "nys": [], "parsed_utc": iso(now)}
            new_reports.append(rec)
        if not dry_run:
            for rec in sorted(new_reports, key=lambda r: (r.get("creation_utc") or 0, r["name"])):
                append_report(reports_path, rec)
        reports = read_reports(reports_path) if not dry_run else reports + new_reports
        ok_reports = sorted([r for r in reports if r.get("creation_utc") and not r.get("error")], key=lambda r: (r["creation_utc"], r["name"]))

        # ---------------- official: diffs for reports not yet diffed
        diffed = set(state["diffed"])
        official, n_diffed = [], 0
        cut = now.timestamp() - ALERT_MAX_AGE_H * 3600
        seen_ids = set(summary.get("recent_ids") or [])
        for i, rep in enumerate(ok_reports):
            if rep["name"] in diffed:
                continue
            diffed.add(rep["name"])
            backfill = rep["creation_utc"] < float(state.get("max_diffed_created") or 0)
            state["max_diffed_created"] = max(float(state.get("max_diffed_created") or 0), rep["creation_utc"])
            if rep["creation_utc"] < cut or backfill:
                continue          # old report, or one that arrives AFTER a newer one was diffed (catch-up): baseline only, no alerts
            n_diffed += 1
            for a in official_alerts(ok_reports, i, plist, bots, now):
                if a["id"] not in seen_ids:
                    seen_ids.add(a["id"])
                    official.append(a)

        # ---------------- news
        items, feed_status = [], {}
        for name, url in feeds:
            try:
                body = fetch_feed(url)
                if not dry_run:
                    try:
                        (logs / "raw").mkdir(exist_ok=True)
                        (logs / "raw" / (name + ".xml")).write_bytes(body)
                    except OSError:
                        pass
                its = TW.parse_feed(body)
            except Exception as e:  # noqa: BLE001
                feed_status[name] = {"ok": False, "error": ("%s: %s" % (type(e).__name__, str(e)))[:160], "at": iso(now)}
                continue
            feed_status[name] = {"ok": True, "items": len(its), "at": iso(now)}
            items += [(name, it) for it in its]
        bot_teams = {t for (d, a, h) in bots for t in (a, h)}
        news, n_items, n_not = news_alerts(items, roster, plist, now, bot_teams, seen, news_max_age_h)

        # ---------------- outputs
        new_alerts = official + news
        window = ([a for a in summary.get("alerts", []) if isinstance(a, dict)] + new_alerts)[-SUMMARY_ALERTS:]
        counts = dict(summary.get("counts") or {})
        for a in new_alerts:
            counts[a["type"]] = int(counts.get(a["type"], 0)) + 1
            if a.get("late"):
                counts["LATE"] = int(counts.get("LATE", 0)) + 1
            if a.get("bot_game"):
                counts["BOT_GAME"] = int(counts.get("BOT_GAME", 0)) + 1
        last = ok_reports[-1] if ok_reports else None
        out = {"updated_utc": iso(now), "alerts": window, "counts": counts, "feeds": feed_status, "errors": errors[:20],
               "last_official": ({"name": last["name"], "creation_utc": last["created_iso"] if "created_iso" in last else None,
                                 "creation_et": datetime.fromtimestamp(last["creation_utc"], ET).strftime("%Y-%m-%d %H:%M ET")} if last else None),
               "n_reports": len(ok_reports),
               "last_run": {"gets": probe["gets"], "not_found": probe["not_found"], "found": probe["found"], "pdf_errors": probe["errors"],
                            "candidates": probe["candidates"], "reports_diffed": n_diffed, "official_alerts": len(official),
                            "news_items": n_items, "news_not_injury": n_not, "news_alerts": len(news)},
               "recent_ids": sorted(seen_ids)[-1500:]}
        if not dry_run:
            TW.rotate_alerts(alerts_path, now)
            if new_alerts:
                with open(alerts_path, "a", encoding="utf-8") as fh:
                    for a in new_alerts:
                        fh.write(json.dumps(a, ensure_ascii=False) + "\n")
            state["diffed"] = sorted(diffed)[-3000:]
            keep = (now - timedelta(days=4)).strftime("%Y-%m-%d")
            state["final_missing"] = {k: v for k, v in fm.items() if k[len("Injury-Report_"):len("Injury-Report_") + 10] >= keep}
            seen = {k: v for k, v in seen.items() if v >= iso(now - timedelta(days=TW.SEEN_KEEP_DAYS))}
            TW.atomic_write(seen_path, json.dumps(seen))
            TW.atomic_write(state_path, json.dumps(state))
            TW.atomic_write(summ_path, json.dumps(out, ensure_ascii=False, indent=1))
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="fetch feeds, no PDF GETs, write nothing")
    ap.add_argument("--max-age-hours", type=float, default=NEWS_MAX_AGE_H, help="news items older than this are remembered, not alerted")
    a = ap.parse_args()
    out = run(news_max_age_h=a.max_age_hours, dry_run=a.dry_run)
    if "skipped" in out:
        print(out["skipped"])
        return
    print("last_run:", json.dumps(out["last_run"]))
    print("last_official:", out["last_official"], "reports:", out["n_reports"])
    print("counts:", out["counts"])
    for n, s in out["feeds"].items():
        print(" feed %s: %s" % (n, "ok %d items" % s["items"] if s["ok"] else "ERROR " + s["error"]))
    for e in out["errors"]:
        print(" error:", e)
    for al in out["alerts"][-10:]:
        print(" ", al["source"], al["type"], al.get("team"), al.get("player"), al.get("old_status"), "->", al.get("new_status"),
              "LATE" if al.get("late") else "", "BOT" if al.get("bot_game") else "", "|", (al.get("headline") or "")[:80])


if __name__ == "__main__":
    sys.exit(main())
