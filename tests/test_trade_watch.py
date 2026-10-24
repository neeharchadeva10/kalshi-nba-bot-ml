import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import trade_watch as T

NOW = datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc)

ROSTER = {
    "walter clayton": {"name": "Walter Clayton Jr.", "team": "DAL", "age": 23, "athlete_id": 1},
    "lebron james": {"name": "LeBron James", "team": "LAL", "age": 41, "athlete_id": 2},
    "bench guy": {"name": "Bench Guy", "team": "BOS", "age": 27, "athlete_id": 3},
}
STATS = {1: {"gp": 40, "mpg": 21.0, "ppg": 9.0, "rpg": 3.0, "apg": 2.0, "pm_pg": 1.0},
         2: {"gp": 60, "mpg": 33.5, "ppg": 24.0, "rpg": 7.0, "apg": 8.0, "pm_pg": 2.0},
         3: {"gp": 30, "mpg": 8.0, "ppg": 2.0, "rpg": 1.0, "apg": 1.0, "pm_pg": None}}


def rss(*items):
    body = "".join("<item><title>%s</title><link>%s</link><pubDate>%s</pubDate><description>%s</description></item>" % i for i in items)
    return ("<?xml version='1.0'?><rss><channel>%s</channel></rss>" % body).encode()


def pub(days_ago=0):
    return (NOW - timedelta(days=days_ago)).strftime("%a, %d %b %Y %H:%M:%S +0000")


# ---------------------------------------------------------------- pure rules
@pytest.mark.parametrize("title,expected", [
    ("Celtics Trade Bench Guy To Lakers For Pick", "TRADE"),
    ("Lakers acquire LeBron James in exchange for picks", "TRADE"),
    ("Walter Clayton Jr. Signs Two-Way Contract With Mavericks", "TWO_WAY_10_DAY"),
    ("Hawks sign guard to 10-day contract", "TWO_WAY_10_DAY"),
    ("Lakers Sign LeBron James To Extension", "SIGNING"),
    ("Bench Guy agrees to deal with Celtics", "SIGNING"),
    ("Blake Hinson Waived By Jazz From Two-Way", "WAIVER"),
    ("Knicks Release Bench Guy", "WAIVER"),
    ("Bench Guy reaches buyout with Celtics", "WAIVER"),
    ("Warriors Exploring Trade Market For Porzingis", "POSSIBLE_MOVE"),      # rumour word downgrades
    ("Lakers could sign LeBron James", "POSSIBLE_MOVE"),
    ("NBA overreactions: Warriors need to trade Curry", "POSSIBLE_MOVE"),
    ("Celtics sign player and waive another", "POSSIBLE_MOVE"),              # mixed categories
    ("Lakers claimed the veteran", "POSSIBLE_MOVE"),                         # weak word + a match
    ("Stephen Curry scores 40 in blowout", "IGNORE"),
])
def test_classify(title, expected):
    assert T.classify(title, "", True) == expected


def test_classify_unmatched_weak_is_ignored_but_category_kept():
    assert T.classify("Weather report for Tuesday", "", False) == "IGNORE"
    assert T.classify("Quiet day", "The team will sign a veteran", True) == "POSSIBLE_MOVE"
    assert T.classify("Quiet day", "The team will sign a veteran", False) == "IGNORE"


def test_find_teams_and_players():
    assert T.find_teams("Lakers acquire a guard from Boston") == ["BOS", "LAL"]
    assert T.find_teams("Los Angeles is far") == []                     # ambiguous city not mapped
    assert T.find_teams("Trail Blazers waive someone") == ["POR"]
    assert T.find_teams("Orlando Magic") == ["ORL"]
    assert T.find_teams("magic happens") == []                          # case-sensitive nickname
    assert [p["name"] for p in T.find_players("Walter Clayton Jr. signs", ROSTER)] == ["Walter Clayton Jr."]
    assert [p["name"] for p in T.find_players("Walter Clayton signs", ROSTER)] == ["Walter Clayton Jr."]
    assert T.find_players("LeBron Jamesy", ROSTER) == []                # whole words only
    assert T.norm_name("Jaren Jackson Jr.") == "jaren jackson" and T.norm_name("Nikola Jokić") == "nikola jokic"


def test_tier_and_veteran_boundaries():
    assert [T.tier_of(x) for x in (28, 27.99, 20, 19.99, 12, 11.99, 0, None, float("nan"))] == \
        ["star", "starter", "starter", "bench", "bench", "deep", "deep", "none", "none"]
    assert T.is_veteran(32) and not T.is_veteran(31) and not T.is_veteran(None)


# ---------------------------------------------------------------- feed parsing / safety
def test_parse_feed_text_only_and_sanitised():
    body = rss(("Hawks &lt;b&gt;sign&lt;/b&gt; X &amp;amp; Y", "https://e.com/1", pub(), "&lt;script&gt;alert(1)&lt;/script&gt;desc"),
               ("bad link", "javascript:alert(1)", pub(), ""))
    items = T.parse_feed(body)
    assert items[0]["title"] == "Hawks sign X & Y" and "<" not in items[0]["description"] and items[0]["link"] == "https://e.com/1"
    assert items[1]["link"] == ""


def test_parse_feed_rejects_entities_and_garbage():
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
    with pytest.raises(ValueError):
        T.parse_feed(bomb)
    with pytest.raises(ValueError):
        T.parse_feed(b"<rss><oops")
    with pytest.raises(ValueError):
        T.parse_feed(b"x" * (T.MAX_BYTES + 1))


# ---------------------------------------------------------------- full run (fake network, tmp logs)
def fake_fetch(feeds):
    def f(url):
        v = feeds[url]
        if isinstance(v, Exception):
            raise v
        return v
    return f


FEED_LIST = [("a", "http://a"), ("b", "http://b")]


def do_run(tmp_path, feeds, **kw):
    kw.setdefault("moves", {})
    return T.run(fetch=fake_fetch(feeds), now=NOW, logs=tmp_path, roster=ROSTER, stats=STATS, feeds=FEED_LIST, **kw)


def test_run_alert_fields_dedupe_and_bad_feed(tmp_path):
    good = rss(("Walter Clayton Jr. Signs Two-Way Contract With Mavericks", "https://x/1", pub(), ""),
               ("Lakers sign LeBron James to extension", "https://x/2", pub(), ""),
               ("Stephen Curry scores 40", "https://x/3", pub(), ""),
               ("Old trade story: Celtics trade Bench Guy", "https://x/4", pub(10), ""))
    out = do_run(tmp_path, {"http://a": good, "http://b": OSError("boom")})
    assert out["feeds"]["a"]["ok"] and not out["feeds"]["b"]["ok"] and "boom" in out["feeds"]["b"]["error"]
    types = {a["type"] for a in out["alerts"]}
    assert types == {"TWO_WAY_10_DAY", "SIGNING"} and out["counts"] == {"TWO_WAY_10_DAY": 1, "SIGNING": 1}
    lb = next(a for a in out["alerts"] if a["type"] == "SIGNING")
    assert lb["tier"] == "star" and lb["veteran"] is True and lb["age"] == 41 and lb["teams"] == ["LAL"] and lb["status"] == "reported"
    lines = [json.loads(x) for x in (tmp_path / "alerts.jsonl").read_text().splitlines()]
    assert len(lines) == 2 and {"ts_utc", "source", "type", "headline", "link", "players", "teams", "tier", "age", "status"} <= set(lines[0])
    # second run: nothing new, nothing appended, counts unchanged (idempotent)
    out2 = do_run(tmp_path, {"http://a": good, "http://b": OSError("boom")})
    assert out2["last_run"]["new_alerts"] == 0 and out2["counts"] == out["counts"]
    assert len((tmp_path / "alerts.jsonl").read_text().splitlines()) == 2
    assert json.loads((tmp_path / "seen.json").read_text())
    assert (tmp_path / "raw" / "a.xml").exists()


def test_old_items_remembered_not_alerted(tmp_path):
    f = rss(("Celtics trade Bench Guy", "https://x/old", pub(10), ""))
    out = do_run(tmp_path, {"http://a": f, "http://b": f})
    assert out["alerts"] == [] and len(json.loads((tmp_path / "seen.json").read_text())) == 1


def test_confirmation_by_moves_now_and_later(tmp_path):
    f = rss(("Walter Clayton Jr. Signs Two-Way Contract With Mavericks", "https://x/1", pub(), ""))
    out = do_run(tmp_path, {"http://a": f, "http://b": f})
    assert out["alerts"][0]["status"] == "reported"
    out2 = do_run(tmp_path, {"http://a": f, "http://b": f}, moves={"walter clayton": {"DAL"}})   # roster task refreshed later
    assert out2["alerts"][0]["status"] == "confirmed by roster data"
    kinds = [json.loads(x).get("kind") for x in (tmp_path / "alerts.jsonl").read_text().splitlines()]
    assert kinds == [None, "confirmation"]
    # wrong destination is NOT confirmation; waivers never are
    a = {"type": "TRADE", "teams": ["BOS"], "players": [{"name": "Walter Clayton Jr."}]}
    assert not T.confirmed_by_moves(a, {"walter clayton": {"DAL"}})
    assert T.confirmed_by_moves(dict(a, teams=["DAL"]), {"walter clayton": {"DAL"}})
    assert not T.confirmed_by_moves(dict(a, type="WAIVER", teams=["DAL"]), {"walter clayton": {"DAL"}})


def test_summary_keeps_last_50_and_lock_blocks(tmp_path):
    items = [("Celtics trade pick %d" % i, "https://x/%d" % i, pub(), "") for i in range(60)]
    out = do_run(tmp_path, {"http://a": rss(*items), "http://b": rss()})
    assert len(out["alerts"]) == 50 and out["counts"]["TRADE"] == 60
    (tmp_path / "run.lock").write_text("123")
    assert do_run(tmp_path, {"http://a": rss(), "http://b": rss()}) == {"skipped": "another run holds the lock"}
    old = time.time() - 3600
    os.utime(tmp_path / "run.lock", (old, old))
    assert "skipped" not in do_run(tmp_path, {"http://a": rss(), "http://b": rss()})        # stale lock taken over
    assert not (tmp_path / "run.lock").exists()


def test_monthly_rotation(tmp_path):
    p = tmp_path / "alerts.jsonl"
    p.write_text("{}\n")
    old = datetime(2026, 9, 15, tzinfo=timezone.utc).timestamp()
    os.utime(p, (old, old))
    T.rotate_alerts(p, NOW)
    assert not p.exists() and (tmp_path / "alerts-2026-09.jsonl").exists()
    p.write_text("{}\n")
    T.rotate_alerts(p, NOW)
    assert p.exists()                                                  # same month: untouched


def test_dry_run_writes_nothing(tmp_path):
    f = rss(("Celtics trade Bench Guy", "https://x/1", pub(), ""))
    do_run(tmp_path, {"http://a": f, "http://b": f}, dry_run=True)
    assert sorted(x.name for x in tmp_path.iterdir()) == []


def test_enrich_player_tier():
    e = T.enrich_player(ROSTER["bench guy"], STATS)
    assert e["tier"] == "deep" and e["veteran"] is False
    assert T.enrich_player(ROSTER["lebron james"], STATS)["veteran"] is True
    assert T.enrich_player({"name": "Nobody", "team": "X", "age": None, "athlete_id": 99}, STATS)["tier"] == "none"


def test_task_scripts_register_nothing_when_written():
    d = Path(T.__file__).parent
    task = (d / "trade_watch_task.ps1").read_text()
    assert "/SC MINUTE /MO 10" in task and "/ED 06/30/2027" in task and "KalshiPM trade watcher" in task
    run = (d / "trade_watch_run.ps1").read_text()
    assert "trade_watch.py" in run and "discord" not in (run + task).lower().replace("nothing is sent to discord", "")


def test_no_forbidden_imports():
    src = Path(T.__file__).read_text()
    assert "kpm.execution" not in src and "requests.post" not in src and "requests.put" not in src and "discord" not in src.lower().replace("no discord", "")
