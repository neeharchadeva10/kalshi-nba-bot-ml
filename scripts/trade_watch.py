"""24/7 NBA transactions watcher (research tool; NO betting, NO Kalshi calls, NO Discord).

Polls three PUBLIC RSS feeds (one GET each per run, polite User-Agent, 15 s timeout), classifies new items
(TRADE / SIGNING / WAIVER / TWO_WAY_10_DAY / POSSIBLE_MOVE, else IGNORE), attaches player + team info from
data/derived/rosters_current.csv and last season's box scores, marks alerts "confirmed by roster data" when
data/derived/player_moves*.csv shows the same team change, and writes:
  logs/trades/alerts.jsonl   one JSON line per alert (+ "confirmation" lines); rotated monthly
  logs/trades/summary.json   last 50 alerts + counts + per-feed status (read by the dashboard)
  logs/trades/seen.json      dedupe memory (link + title hash), atomic writes
  logs/trades/raw/<feed>.xml latest raw response per feed (overwritten, so no unbounded growth)

Feed text is untrusted DATA: only title/link/pubDate/description text is read; nothing in it is executed, fetched
or followed. DOCTYPE/ENTITY declarations are rejected before parsing (XML bomb guard) and bodies are capped at 5 MB.

Usage: .venv/Scripts/python.exe scripts/trade_watch.py [--max-age-days 3] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs" / "trades"
ROSTER_CSV = ROOT / "data" / "derived" / "rosters_current.csv"
MOVES_CSV = ROOT / "data" / "derived" / "player_moves.csv"
PENDING_CSV = ROOT / "data" / "derived" / "player_moves_pending_2027.csv"
BOX_PARQUET = ROOT / "data" / "raw" / "nba_open" / "player_box_2026.parquet"
BOX_SEASON = 2026          # ESPN/hoopR season label for 2025-26
CURRENT_SEASON_NEW = 2027  # season_new label for 2026-27 moves

USER_AGENT = "KalshiPM-research/1.0"
TIMEOUT_S = 15
MAX_BYTES = 5 * 1024 * 1024
FEEDS = [
    ("realgm", "https://basketball.realgm.com/rss/wiretap/0/0.xml"),
    ("espn", "https://www.espn.com/espn/rss/nba/news"),
    ("cbs", "https://www.cbssports.com/rss/headlines/nba/"),
]
SUMMARY_ALERTS = 50
SEEN_KEEP_DAYS = 60
LOCK_STALE_S = 600

# ------------------------------------------------------------------ teams
TEAMS = {  # Kalshi code -> aliases (case-sensitive, whole words). Bare "Los Angeles" is ambiguous, so not listed.
    "ATL": ["Atlanta Hawks", "Atlanta", "Hawks"], "BKN": ["Brooklyn Nets", "Brooklyn", "Nets"],
    "BOS": ["Boston Celtics", "Boston", "Celtics"], "CHA": ["Charlotte Hornets", "Charlotte", "Hornets"],
    "CHI": ["Chicago Bulls", "Chicago", "Bulls"], "CLE": ["Cleveland Cavaliers", "Cleveland", "Cavaliers", "Cavs"],
    "DAL": ["Dallas Mavericks", "Dallas", "Mavericks", "Mavs"], "DEN": ["Denver Nuggets", "Denver", "Nuggets"],
    "DET": ["Detroit Pistons", "Detroit", "Pistons"], "GSW": ["Golden State Warriors", "Golden State", "Warriors"],
    "HOU": ["Houston Rockets", "Houston", "Rockets"], "IND": ["Indiana Pacers", "Indiana", "Pacers"],
    "LAC": ["Los Angeles Clippers", "LA Clippers", "Clippers"], "LAL": ["Los Angeles Lakers", "LA Lakers", "Lakers"],
    "MEM": ["Memphis Grizzlies", "Memphis", "Grizzlies"], "MIA": ["Miami Heat", "Miami", "Heat"],
    "MIL": ["Milwaukee Bucks", "Milwaukee", "Bucks"], "MIN": ["Minnesota Timberwolves", "Minnesota", "Timberwolves", "Wolves"],
    "NOP": ["New Orleans Pelicans", "New Orleans", "Pelicans"], "NYK": ["New York Knicks", "New York", "Knicks"],
    "OKC": ["Oklahoma City Thunder", "Oklahoma City", "Thunder"], "ORL": ["Orlando Magic", "Orlando", "Magic"],
    "PHI": ["Philadelphia 76ers", "Philadelphia", "76ers", "Sixers"], "PHX": ["Phoenix Suns", "Phoenix", "Suns"],
    "POR": ["Portland Trail Blazers", "Portland", "Trail Blazers", "Blazers"], "SAC": ["Sacramento Kings", "Sacramento", "Kings"],
    "SAS": ["San Antonio Spurs", "San Antonio", "Spurs"], "TOR": ["Toronto Raptors", "Toronto", "Raptors"],
    "UTA": ["Utah Jazz", "Utah", "Jazz"], "WAS": ["Washington Wizards", "Washington", "Wizards"],
}
_TEAM_RE = [(code, re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % "|".join(re.escape(a) for a in sorted(al, key=len, reverse=True))))
            for code, al in TEAMS.items()]


def find_teams(text: str) -> list[str]:
    """Kalshi codes of every team named (city / nickname / full name) in the text, in the order of the TEAMS table."""
    return [code for code, rx in _TEAM_RE if rx.search(text or "")]


# ------------------------------------------------------------------ classification
# Simple, documented keyword rules (case-insensitive, whole words). Categories found in the TITLE decide the type:
#   exactly one category -> that type;  TWO_WAY + SIGNING -> TWO_WAY_10_DAY;  TWO_WAY + WAIVER -> WAIVER;
#   any other mix -> POSSIBLE_MOVE.  Opinion / preview words (need, should, predictions, builder) also downgrade.
# A rumour word (could/might/eyeing/rumor/...) downgrades any type to POSSIBLE_MOVE ("not done yet").
# No category in the title but one in the description, or a weak word (claimed/cut/joins/contract/...) with a
# player or team match -> POSSIBLE_MOVE (never dropped when unsure). Anything else -> IGNORE.
RULES = {
    "TWO_WAY_10_DAY": r"two-?\s?way|10-?\s?day|ten-?\s?day|exhibit\s?10",
    "WAIVER": r"waive[sd]?|waiving|waivers?|releas(?:e[sd]?|ing)|buy-?\s?outs?|buys? out|bought out|buyout",
    "TRADE": r"traded?|trades|trading|acquir(?:e[sd]?|ing)|deal sends?|swap(?:s|ped)?|in exchange|sent to",
    "SIGNING": r"sign(?:s|ed|ing)?|agree[sd]? to|agreeing to|re-?sign(?:s|ed|ing)?|extension|extend(?:s|ed)?|inks?",
}
_RX = {k: re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % v, re.I) for k, v in RULES.items()}
RUMOUR = re.compile(r"(?<![A-Za-z0-9])(?:rumou?rs?|could|might|may|would|eyes?|eyeing|explor\w*|interest(?:ed)?|targets?|targeting|"
                    r"pursu\w*|considering|needs?|should|must|predictions?|overreactions?|builder|expected|expects?|likely|talks?|discuss\w*|open to|wants? to|request\w*|linked|monitoring)(?![A-Za-z0-9])", re.I)
WEAK = re.compile(r"(?<![A-Za-z0-9])(?:claimed?|cut|cuts|joins?|join|contract|free agent|roster|deal|reunit\w*|returns? to|"
                  r"signing|transaction)(?![A-Za-z0-9])", re.I)
TYPES = ("TRADE", "SIGNING", "WAIVER", "TWO_WAY_10_DAY", "POSSIBLE_MOVE")


def _cats(text: str) -> set[str]:
    return {k for k, rx in _RX.items() if rx.search(text or "")}


def classify(title: str, description: str = "", has_match: bool = True) -> str:
    """Return one of TYPES or "IGNORE". has_match = a roster player or team was found in the item."""
    t_cats = _cats(title)
    if "TWO_WAY_10_DAY" in t_cats and t_cats <= {"TWO_WAY_10_DAY", "SIGNING"}:
        typ = "TWO_WAY_10_DAY"
    elif t_cats == {"TWO_WAY_10_DAY", "WAIVER"}:
        typ = "WAIVER"                      # "waived from his two-way deal" is a waiver
    elif len(t_cats) == 1:
        typ = next(iter(t_cats))
    elif len(t_cats) > 1:
        typ = "POSSIBLE_MOVE"
    else:
        d_cats = _cats(description)
        if d_cats and has_match:
            return "POSSIBLE_MOVE"
        if has_match and WEAK.search(title or ""):
            return "POSSIBLE_MOVE"
        return "IGNORE"
    if RUMOUR.search(title or ""):
        return "POSSIBLE_MOVE"
    return typ


# ------------------------------------------------------------------ names, tiers, stats
_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm_name(s: str) -> str:
    """Lower-case, accents/punctuation removed, Jr/Sr/II/III dropped: 'Jaren Jackson Jr.' -> 'jaren jackson'."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower().replace("'", "").replace(".", ""))
    parts = [p for p in s.split() if p]
    while parts and parts[-1] in _SUFFIX:
        parts.pop()
    return " ".join(parts)


def tier_of(mpg) -> str:
    """star (28+ mpg), starter (20-28, 'starter/key role'), bench (12-20), deep (<12), none (no last-season minutes)."""
    try:
        m = float(mpg)
    except (TypeError, ValueError):
        return "none"
    if m != m:
        return "none"
    if m >= 28:
        return "star"
    if m >= 20:
        return "starter"
    if m >= 12:
        return "bench"
    return "deep"


def is_veteran(age) -> bool:
    try:
        return float(age) >= 32
    except (TypeError, ValueError):
        return False


TIER_RANK = {"star": 4, "starter": 3, "bench": 2, "deep": 1, "none": 0}


def load_player_stats(parquet=BOX_PARQUET, season=BOX_SEASON):
    """athlete_id(int) -> {gp, mpg, ppg, rpg, apg, pm_pg} for the regular season (season_type 2), games with minutes > 0."""
    import pandas as pd
    cols = ["season", "season_type", "athlete_id", "minutes", "points", "rebounds", "assists", "plus_minus", "did_not_play"]
    d = pd.read_parquet(parquet, columns=cols)
    d = d[(d["season"] == season) & (d["season_type"] == 2) & d["athlete_id"].notna()]
    d = d[(~d["did_not_play"].fillna(False).astype(bool)) & (pd.to_numeric(d["minutes"], errors="coerce") > 0)].copy()
    d["pm"] = pd.to_numeric(d["plus_minus"].astype(str).str.replace("+", "", regex=False), errors="coerce")
    g = d.groupby(d["athlete_id"].astype("int64"))
    out = {}
    for aid, x in g:
        pm = x["pm"].mean()
        out[int(aid)] = {"gp": int(len(x)), "mpg": round(float(x["minutes"].mean()), 1), "ppg": round(float(x["points"].mean()), 1),
                         "rpg": round(float(x["rebounds"].mean()), 1), "apg": round(float(x["assists"].mean()), 1),
                         "pm_pg": None if pm != pm else round(float(pm), 1)}
    return out


def load_roster(path=ROSTER_CSV):
    """normalised full name -> {name, team, age, athlete_id}.  Duplicate normalised names are dropped (ambiguous)."""
    import pandas as pd
    r = pd.read_csv(path)
    out, dup = {}, set()
    for _, x in r.iterrows():
        k = norm_name(x["name"])
        if not k:
            continue
        if k in out:
            dup.add(k)
        age = x.get("age")
        out[k] = {"name": str(x["name"]), "team": str(x["team_kalshi"]), "age": None if age != age else int(age),
                  "athlete_id": None if x.get("athlete_id") != x.get("athlete_id") else int(x["athlete_id"])}
    for k in dup:
        out.pop(k, None)
    return out


def find_players(text: str, roster: dict) -> list[dict]:
    """Roster players whose normalised full name appears (whole words) in the normalised text."""
    hay = " " + norm_name_keep_suffix(text) + " "
    return [v for k, v in roster.items() if (" " + k + " ") in hay]


def norm_name_keep_suffix(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower().replace("'", "").replace(".", ""))
    return " ".join(s.split())


def enrich_player(p: dict, stats: dict) -> dict:
    st = stats.get(p.get("athlete_id")) or {}
    mpg = st.get("mpg")
    return {"name": p["name"], "team": p["team"], "age": p.get("age"), "mpg": mpg, "ppg": st.get("ppg"), "gp": st.get("gp"),
            "tier": tier_of(mpg), "veteran": is_veteran(p.get("age"))}


def top_tier(players: list[dict]) -> str:
    return max((p["tier"] for p in players), key=lambda t: TIER_RANK[t], default="none")


# ------------------------------------------------------------------ moves (second source)
def load_moves(moves_csv=MOVES_CSV, pending_csv=PENDING_CSV):
    """normalised player name -> set of destination team codes, for 2026-27 moves (both files)."""
    import pandas as pd
    out: dict[str, set] = {}
    frames = []
    for p in (moves_csv, pending_csv):
        try:
            d = pd.read_csv(p)
        except (OSError, ValueError):
            continue
        frames.append(d[d["season_new"] == CURRENT_SEASON_NEW] if "season_new" in d else d)
    for d in frames:
        for _, x in d.iterrows():
            out.setdefault(norm_name(x["player"]), set()).add(str(x["to_team"]))
    return out


def confirmed_by_moves(alert: dict, moves: dict) -> bool:
    """True when a matched player has a 2026-27 team change in the roster data whose destination is a team the item names
    (or the item names no team). Only trades / signings / possible moves can be confirmed (a waiver is not a team change; vague POSSIBLE_MOVE items are never auto-confirmed)."""
    if alert["type"] not in ("TRADE", "SIGNING", "TWO_WAY_10_DAY"):
        return False
    teams = set(alert.get("teams") or [])
    for p in alert.get("players") or []:
        dest = moves.get(norm_name(p["name"]))
        if dest and (not teams or dest & teams):
            return True
    return False


# ------------------------------------------------------------------ feed parsing (untrusted)
_TAGS = re.compile(r"<[^>]*>")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_text(s, limit: int) -> str:
    s = html.unescape(_TAGS.sub(" ", s or ""))
    s = _CTRL.sub("", s)
    return " ".join(s.split())[:limit]


def parse_feed(body: bytes) -> list[dict]:
    """RSS 2.0 <item>s -> [{title, link, pub, description}] (text only). Raises ValueError on unsafe / unparsable input."""
    if len(body) > MAX_BYTES:
        raise ValueError("feed too large")
    if b"<!doctype" in body[:200000].lower() or b"<!entity" in body.lower():
        raise ValueError("DOCTYPE/ENTITY rejected")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ValueError("bad XML: %s" % e) from None
    items = []
    for it in root.iter("item"):
        link = clean_text(it.findtext("link"), 500)
        if not link.lower().startswith(("http://", "https://")):
            link = ""
        items.append({"title": clean_text(it.findtext("title"), 300), "link": link, "pub": clean_text(it.findtext("pubDate"), 60),
                      "description": clean_text(it.findtext("description"), 600)})
    return [i for i in items if i["title"]]


def parse_pub(s: str):
    try:
        d = parsedate_to_datetime(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


def item_key(it: dict) -> str:
    return it["link"] + "|" + hashlib.sha1(it["title"].encode("utf-8")).hexdigest()[:16]


def alert_id(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


# ------------------------------------------------------------------ files
def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default):
    try:
        j = json.loads(path.read_text(encoding="utf-8"))
        return j if isinstance(j, type(default)) else default
    except (OSError, ValueError):
        return default


class RunLock:
    """Single-run lock file (O_EXCL). A lock older than LOCK_STALE_S is treated as left over by a crash and taken over."""

    def __init__(self, path: Path):
        self.path, self.held = path, False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > LOCK_STALE_S:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                return self
        return self

    def __exit__(self, *a):
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass


def rotate_alerts(path: Path, now: datetime) -> None:
    """Monthly rotation: an alerts.jsonl last written in an earlier month becomes alerts-YYYY-MM.jsonl."""
    try:
        m = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return
    if (m.year, m.month) != (now.year, now.month):
        dest = path.with_name("alerts-%04d-%02d.jsonl" % (m.year, m.month))
        n = 1
        while dest.exists():
            dest = path.with_name("alerts-%04d-%02d-%d.jsonl" % (m.year, m.month, n))
            n += 1
        os.replace(path, dest)


def http_get(url: str) -> bytes:
    import requests
    r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"},
                     timeout=TIMEOUT_S, stream=True, allow_redirects=True)
    r.raise_for_status()
    data = r.raw.read(MAX_BYTES + 1, decode_content=True)
    r.close()
    if len(data) > MAX_BYTES:
        raise ValueError("feed too large")
    return data


# ------------------------------------------------------------------ the run
def match_item(it: dict, roster: dict):
    """Players and teams named in the TITLE; the description is used only when the title names none (it is often
    a recap that mentions unrelated stars)."""
    pl, tm = find_players(it["title"], roster), find_teams(it["title"])
    if not pl:
        pl = find_players(it["description"], roster)
    if not tm:
        tm = find_teams(it["description"])
    return pl, tm


def build_alert(it: dict, source: str, typ: str, roster: dict, stats: dict, now: datetime) -> dict:
    pl, teams = match_item(it, roster)
    players = [enrich_player(p, stats) for p in pl]
    ages = [p["age"] for p in players if p["age"] is not None]
    return {"id": alert_id(item_key(it)), "ts_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": source, "type": typ,
            "headline": it["title"], "link": it["link"], "pub": it["pub"], "players": players, "teams": teams,
            "tier": top_tier(players), "age": max(ages) if ages else None, "veteran": any(p["veteran"] for p in players),
            "status": "reported"}


def run(fetch=http_get, now=None, logs: Path = LOGS, roster=None, stats=None, moves=None, max_age_days=3, dry_run=False,
        feeds=FEEDS) -> dict:
    now = now or datetime.now(timezone.utc)
    logs.mkdir(parents=True, exist_ok=True)
    with RunLock(logs / "run.lock") as lk:
        if not lk.held:
            return {"skipped": "another run holds the lock"}
        summ_path, seen_path, alerts_path = logs / "summary.json", logs / "seen.json", logs / "alerts.jsonl"
        summary = read_json(summ_path, {})
        seen = read_json(seen_path, {})
        errors, feed_status = [], {}
        try:
            roster = roster if roster is not None else load_roster()
        except Exception as e:  # noqa: BLE001
            roster = {}
            errors.append("roster: %s" % type(e).__name__)
        try:
            stats = stats if stats is not None else load_player_stats()
        except Exception as e:  # noqa: BLE001
            stats = {}
            errors.append("stats: %s" % type(e).__name__)
        try:
            moves = moves if moves is not None else load_moves()
        except Exception as e:  # noqa: BLE001
            moves = {}
            errors.append("moves: %s" % type(e).__name__)

        new_alerts, n_new_items, n_ignored = [], 0, 0
        cutoff = now - timedelta(days=max_age_days)
        for name, url in feeds:
            try:
                body = fetch(url)
                if not dry_run:
                    try:
                        (logs / "raw").mkdir(exist_ok=True)
                        (logs / "raw" / (name + ".xml")).write_bytes(body)
                    except OSError:
                        pass
                items = parse_feed(body)
            except Exception as e:  # noqa: BLE001  never crash on a bad feed
                feed_status[name] = {"ok": False, "error": ("%s: %s" % (type(e).__name__, str(e)))[:160], "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
                continue
            feed_status[name] = {"ok": True, "items": len(items), "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
            for it in items:
                k = item_key(it)
                if k in seen:
                    continue
                seen[k] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                pub = parse_pub(it["pub"])
                if pub is not None and pub < cutoff:
                    continue                      # old story on the first run: remember it, don't alert
                n_new_items += 1
                pl, tm = match_item(it, roster)
                typ = classify(it["title"], it["description"], bool(pl or tm))
                if typ == "IGNORE":
                    n_ignored += 1
                    continue
                new_alerts.append(build_alert(it, name, typ, roster, stats, now))

        # de-duplicate the same story arriving from two feeds in one run (same type + same players + same teams)
        uniq, sigs = [], set()
        for a in new_alerts:
            sig = (a["type"], tuple(sorted(p["name"] for p in a["players"])), tuple(a["teams"]))
            if a["players"] and sig in sigs:
                continue
            sigs.add(sig)
            uniq.append(a)
        new_alerts = uniq
        for a in new_alerts:
            if confirmed_by_moves(a, moves):
                a["status"] = "confirmed by roster data"

        # re-check older still-"reported" alerts in the dashboard window against the (daily refreshed) moves files
        window = [a for a in summary.get("alerts", []) if isinstance(a, dict)]
        confirmations = []
        for a in window:
            if a.get("status") == "reported" and confirmed_by_moves(a, moves):
                a["status"] = "confirmed by roster data"
                confirmations.append({"kind": "confirmation", "id": a.get("id"), "ts_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      "status": a["status"]})
        window = (window + new_alerts)[-SUMMARY_ALERTS:]

        counts = dict(summary.get("counts") or {})
        for a in new_alerts:
            counts[a["type"]] = int(counts.get(a["type"], 0)) + 1
        summary_out = {"updated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "alerts": window, "counts": counts,
                       "feeds": feed_status, "errors": errors,
                       "last_run": {"new_items": n_new_items, "ignored": n_ignored, "new_alerts": len(new_alerts)}}
        cutoff_seen = (now - timedelta(days=SEEN_KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        seen = {k: v for k, v in seen.items() if v >= cutoff_seen}
        if not dry_run:
            rotate_alerts(alerts_path, now)
            if new_alerts or confirmations:
                with open(alerts_path, "a", encoding="utf-8") as fh:
                    for rec in new_alerts + confirmations:
                        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            atomic_write(seen_path, json.dumps(seen))
            atomic_write(summ_path, json.dumps(summary_out, ensure_ascii=False, indent=1))
        return summary_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=float, default=3, help="items published earlier than this are remembered but not alerted")
    ap.add_argument("--dry-run", action="store_true", help="fetch + classify, write nothing")
    a = ap.parse_args()
    out = run(max_age_days=a.max_age_days, dry_run=a.dry_run)
    if "skipped" in out:
        print(out["skipped"])
        return
    print("new_items=%(new_items)d ignored=%(ignored)d new_alerts=%(new_alerts)d" % out["last_run"], "counts=%s" % out["counts"])
    for n, s in out["feeds"].items():
        print(" feed %s: %s" % (n, "ok %d items" % s["items"] if s["ok"] else "ERROR " + s["error"]))
    for e in out["errors"]:
        print(" error:", e)


if __name__ == "__main__":
    sys.exit(main())
