import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import injury_watch as W  # noqa: E402
import trade_watch as T  # noqa: E402

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 23, 22, 40, tzinfo=timezone.utc)      # 18:40 ET

ROSTER = {
    "lebron james": {"name": "LeBron James", "team": "LAL", "age": 41, "athlete_id": 2},
    "bench guy": {"name": "Bench Guy", "team": "LAL", "age": 27, "athlete_id": 3},
    "luka doncic": {"name": "Luka Doncic", "team": "DAL", "age": 27, "athlete_id": 1},
}
PLIST = W.Players([
    {"name": "LeBron James", "team": "LAL", "age": 41, "athlete_id": 2, "mpg": 33.5, "ppg": 24.0, "mpg_src": "2025-26"},
    {"name": "Bench Guy", "team": "LAL", "age": 27, "athlete_id": 3, "mpg": 8.0, "ppg": 2.0, "mpg_src": "2025-26"},
    {"name": "Luka Doncic", "team": "DAL", "age": 27, "athlete_id": 1, "mpg": 36.0, "ppg": 30.0, "mpg_src": "2026-27"},
])


def pdf_bytes(created_utc: datetime) -> bytes:
    o = created_utc.astimezone(ET)
    off = o.utcoffset()
    sign = "+" if off >= timedelta(0) else "-"
    h = abs(off) // timedelta(hours=1)
    return (b"%PDF-1.4\n" + b"x" * 1200 + ("\n/CreationDate(D:%s%s%02d'00')\n" % (o.strftime("%Y%m%d%H%M%S"), sign, h)).encode())


def row(team, player, status, time="07:30(ET)", date="10/23/2026", matchup="DAL@LAL"):
    return {"date": date, "time": time, "matchup": matchup, "team": team, "player": player, "status": status}


def rep(name, created, rows, nys=()):
    return {"name": name, "creation_utc": created.timestamp(), "rows": rows, "nys": [list(x) for x in nys]}


# ---------------------------------------------------------------- cadence / names
def test_names_and_stamps_only_before_now():
    st = datetime(2026, 10, 23, 17, 0, tzinfo=ET)
    assert W.name15(st) == "Injury-Report_2026-10-23_05_00PM.pdf" and W.name1h(st) == "Injury-Report_2026-10-23_05PM.pdf"
    stamps = W.candidate_stamps(NOW)
    assert stamps and all(s <= NOW.astimezone(ET) for s in stamps)
    assert max(stamps) == datetime(2026, 10, 23, 18, 30, tzinfo=ET) and stamps == sorted(stamps, reverse=True)
    assert min(stamps) > NOW.astimezone(ET) - timedelta(hours=6)
    # just after midnight ET: yesterday's late-evening stamps are included, nothing from the future
    s2 = W.candidate_stamps(datetime(2026, 10, 23, 4, 10, tzinfo=timezone.utc))
    assert max(s2) == datetime(2026, 10, 22, 23, 45, tzinfo=ET) and all(s.hour >= 18 for s in s2)


def test_creation_of_reads_offset_and_rejects_non_pdf():
    c = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
    assert W.creation_of(pdf_bytes(c)) == c.timestamp()
    assert W.creation_of(b"<html>404</html>") is None and W.creation_of(b"%PDF-1.4 no date") is None


# ---------------------------------------------------------------- name rules
def test_injury_keys_and_display():
    assert W.injury_key("PippenJr.,Scotty") == "scottypippen" and W.injury_key("Antetokounmpo,Giannis") == "giannisantetokounmpo"
    assert W.key_letters("Jaren Jackson Jr.") == W.injury_key("JacksonJr.,Jaren") == "jarenjackson"
    assert W.display_name("PorterJr.,Kevin") == "Kevin Porter Jr."
    assert PLIST.lookup("lebronjames", "LAL")["name"] == "LeBron James" and PLIST.lookup("nobody") is None


# ---------------------------------------------------------------- diff + attachments
def test_diff_new_change_removed_and_late():
    t1 = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)       # 17:00 ET
    t2 = datetime(2026, 10, 23, 23, 30, tzinfo=timezone.utc)      # 19:30 ET = tip: LATE
    r1 = rep("r1", t1, [row("LosAngelesLakers", "James,LeBron", "Questionable"), row("LosAngelesLakers", "Guy,Bench", "Out"),
                         row("DallasMavericks", "Doncic,Luka", "Probable")])
    r2 = rep("r2", t2, [row("LosAngelesLakers", "James,LeBron", "Out"), row("DallasMavericks", "Doncic,Luka", "Probable"),
                         row("DallasMavericks", "Newman,Nick", "Out")])
    al = W.official_alerts([r1, r2], 1, PLIST, {("2026-10-23", "DAL", "LAL")}, NOW)
    by = {(a["type"], a["player"]): a for a in al}
    chg = by[("STATUS_CHANGE", "LeBron James")]
    assert (chg["old_status"], chg["new_status"], chg["team"], chg["opponent"]) == ("Questionable", "Out", "LAL", "DAL")
    assert chg["tier"] == "star" and chg["veteran"] is True and chg["late"] is True and chg["bot_game"] is True
    assert chg["tip_et"].startswith("2026-10-23T19:30") and chg["min_to_tip"] == 0 and chg["official"] is True
    assert by[("REMOVED", "Bench Guy")]["old_status"] == "Out" and by[("REMOVED", "Bench Guy")]["new_status"] is None
    assert by[("NEW", "Nick Newman")]["tier"] == "none" and by[("NEW", "Nick Newman")]["old_status"] is None
    assert ("STATUS_CHANGE", "Luka Doncic") not in by and len(al) == 3
    # not late 3 h before tip, and not a bot game
    r2b = rep("r2", t1 + timedelta(minutes=30), r2["rows"])
    al2 = W.official_alerts([r1, r2b], 1, PLIST, set(), NOW)
    assert not any(a["late"] or a["bot_game"] for a in al2)


def test_late_boundary_is_90_minutes():
    tip = datetime(2026, 10, 23, 19, 30, tzinfo=ET)
    r1 = rep("r1", tip - timedelta(hours=4), [row("LosAngelesLakers", "James,LeBron", "Questionable")])
    on = rep("r2", tip - timedelta(minutes=90), [row("LosAngelesLakers", "James,LeBron", "Out")])
    before = rep("r2", tip - timedelta(minutes=91), [row("LosAngelesLakers", "James,LeBron", "Out")])
    assert W.official_alerts([r1, on], 1, PLIST, set(), NOW)[0]["late"] is True
    assert W.official_alerts([r1, before], 1, PLIST, set(), NOW)[0]["late"] is False


def test_first_report_only_notable_players_and_nys_not_removed():
    t1 = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
    r1 = rep("r1", t1, [row("LosAngelesLakers", "James,LeBron", "Out"), row("LosAngelesLakers", "Guy,Bench", "Out"),
                         row("LosAngelesLakers", "Rando,Joe", "Available")])
    al = W.official_alerts([r1], 0, PLIST, set(), NOW)
    assert [a["player"] for a in al] == ["LeBron James"] and al[0]["first_report"] is True
    # DAL listed before, now 'not yet submitted' -> no REMOVED flood; a team with no entry at all and not NYS -> removed
    rA = rep("a", t1, [row("DallasMavericks", "Doncic,Luka", "Out"), row("LosAngelesLakers", "James,LeBron", "Out")])
    rB = rep("b", t1 + timedelta(minutes=15), [row("LosAngelesLakers", "James,LeBron", "Out")], nys=[("10/23/2026", "DAL@LAL", "DallasMavericks")])
    assert W.official_alerts([rA, rB], 1, PLIST, set(), NOW) == []
    rC = rep("c", t1 + timedelta(minutes=15), [row("LosAngelesLakers", "James,LeBron", "Out")])
    assert [a["type"] for a in W.official_alerts([rA, rC], 1, PLIST, set(), NOW)] == ["REMOVED"]


def test_diff_uses_previous_report_that_covers_the_same_game():
    t = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
    other = row("BostonCeltics", "Tatum,Jayson", "Out", matchup="BOS@NYK")
    r1 = rep("r1", t, [row("LosAngelesLakers", "James,LeBron", "Questionable")])
    r2 = rep("r2", t + timedelta(minutes=15), [other])                       # covers a different game only
    r3 = rep("r3", t + timedelta(minutes=30), [row("LosAngelesLakers", "James,LeBron", "Out")])
    al = W.official_alerts([r1, r2, r3], 2, PLIST, set(), NOW)
    assert [(a["type"], a["old_status"]) for a in al] == [("STATUS_CHANGE", "Questionable")]


def test_no_lookahead_alerts_use_only_earlier_reports():
    t = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
    r1 = rep("r1", t, [row("LosAngelesLakers", "James,LeBron", "Questionable")])
    r2 = rep("r2", t + timedelta(minutes=15), [row("LosAngelesLakers", "James,LeBron", "Out")])
    r3 = rep("r3", t + timedelta(minutes=30), [row("LosAngelesLakers", "James,LeBron", "Available")])
    a2 = W.official_alerts([r1, r2, r3], 1, PLIST, set(), NOW)
    assert [(a["old_status"], a["new_status"]) for a in a2] == [("Questionable", "Out")]       # r3 is invisible to r2


# ---------------------------------------------------------------- news
@pytest.mark.parametrize("title,expect", [
    ("LeBron James ruled out for Friday's game with ankle sprain", True),
    ("Lakers' LeBron James questionable vs. Mavericks", True),
    ("LeBron James will play tonight", True),
    ("LeBron James is out tonight", True),
    ("Lakers blow out Mavericks behind LeBron James", False),
    ("Lakers trade Bench Guy to Celtics", False),
    ("Lakers sign LeBron James to extension", False),
    ("Lakers trade Bench Guy to Celtics; he was recovering from a sprain", True),     # hard injury word kept
    ("Unknown Person ruled out", False),
])
def test_news_filter(title, expect):
    pl = bool(T.find_players(title, ROSTER))
    assert bool(W.is_injury_news(title, "", pl)) is expect


def test_news_alert_fields_dedupe_and_old_items():
    items = [("espn", {"title": "LeBron James ruled out vs. Mavericks (ankle)", "link": "https://x/1", "pub": "Fri, 23 Oct 2026 22:00:00 +0000", "description": ""}),
             ("cbs", {"title": "LeBron James ruled out vs. Mavericks (ankle)", "link": "https://y/1", "pub": "Fri, 23 Oct 2026 22:05:00 +0000", "description": ""}),
             ("cbs", {"title": "Luka Doncic questionable", "link": "https://y/2", "pub": "Mon, 12 Oct 2026 22:05:00 +0000", "description": ""})]
    seen = {}
    al, n_new, n_skip = W.news_alerts(items, ROSTER, PLIST, NOW, {"LAL"}, seen, 24)
    assert len(al) == 1 and n_new == 2 and len(seen) == 3             # old item remembered, not alerted; duplicate story collapsed
    a = al[0]
    assert a["type"] == "NEWS_INJURY" and a["official"] is False and a["label"] == "news, unofficial" and a["source"] == "NEWS"
    assert a["new_status"] == "Out" and a["tier"] == "star" and a["veteran"] is True and a["bot_game"] is True and a["team"] == "LAL"
    assert W.news_alerts(items, ROSTER, PLIST, NOW, set(), seen, 24)[0] == []     # seen cache: idempotent


# ---------------------------------------------------------------- full run (fake network, fake parser)
def fake_world(tmp_path, available):
    """available: {name: created_utc datetime}.  Returns kwargs for W.run and the list of requested URLs."""
    calls = []

    def fetch_pdf(url):
        calls.append(url)
        name = url.rsplit("/", 1)[1]
        return pdf_bytes(available[name]) if name in available else None

    def parse_fn(path):
        return {"header": "10/23/26 06:30 PM", "layout": "new", "nys": [],
                "rows": [row("LosAngelesLakers", "James,LeBron", "Out" if "06_30" in path.name else "Questionable")]}
    rss = ("<?xml version='1.0'?><rss><channel><item><title>LeBron James questionable for Friday</title><link>https://e/1</link>"
           "<pubDate>Fri, 23 Oct 2026 22:30:00 +0000</pubDate><description>d</description></item></channel></rss>").encode()
    kw = dict(fetch_pdf=fetch_pdf, fetch_feed=lambda u: rss, logs=tmp_path / "logs", pdf_dir=tmp_path / "pdf",
              reports_path=tmp_path / "derived" / "injury_reports_2027.jsonl", parse_fn=parse_fn, plist=PLIST, roster=ROSTER,
              notified_path=tmp_path / "none.json", feeds=[("espn", "https://e/rss")])
    return kw, calls


def test_run_end_to_end_idempotent_and_get_cap(tmp_path):
    avail = {"Injury-Report_2026-10-23_06_15PM.pdf": datetime(2026, 10, 23, 22, 15, tzinfo=timezone.utc),
             "Injury-Report_2026-10-23_06_30PM.pdf": datetime(2026, 10, 23, 22, 30, tzinfo=timezone.utc)}
    kw, calls = fake_world(tmp_path, avail)
    out = W.run(now=NOW, **kw)
    assert out["last_run"]["gets"] <= W.MAX_GETS and all(u.startswith("https://ak-static.cms.nba.com/referee/injury/Injury-Report_") for u in calls)
    assert len(calls) <= W.MAX_GETS
    lines = (tmp_path / "derived" / "injury_reports_2027.jsonl").read_text().splitlines()
    names = [json.loads(x)["name"] for x in lines]
    assert names == sorted(avail) and len(set(names)) == 2                       # deduped by PDF name, creation order
    assert (tmp_path / "pdf" / "Injury-Report_2026-10-23_06_30PM.pdf").exists()
    types = [(a["source"], a["type"]) for a in out["alerts"]]
    assert ("OFFICIAL", "STATUS_CHANGE") in types and ("NEWS", "NEWS_INJURY") in types
    assert out["last_official"]["name"] == "Injury-Report_2026-10-23_06_30PM.pdf" and out["last_official"]["creation_et"] == "2026-10-23 18:30 ET"
    assert not list((tmp_path / "pdf").glob("*.tmp"))
    n_alerts = len((tmp_path / "logs" / "alerts.jsonl").read_text().splitlines())
    # second run later: nothing new -> no duplicate rows/alerts; 404s older than 25 min become final (not re-probed)
    calls.clear()
    out2 = W.run(now=NOW + timedelta(minutes=10), **kw)
    assert len((tmp_path / "derived" / "injury_reports_2027.jsonl").read_text().splitlines()) == 2
    assert len((tmp_path / "logs" / "alerts.jsonl").read_text().splitlines()) == n_alerts
    assert out2["last_run"]["official_alerts"] == 0 and out2["last_run"]["news_alerts"] == 0
    assert not any("06_30PM" in u or "06_15PM" in u for u in calls)
    st = json.loads((tmp_path / "logs" / "state.json").read_text())
    assert any("05_00PM" in k for k in st["final_missing"])


def test_run_survives_bad_inputs(tmp_path):
    kw, calls = fake_world(tmp_path, {"Injury-Report_2026-10-23_06_30PM.pdf": datetime(2026, 10, 23, 22, 30, tzinfo=timezone.utc)})

    def boom(path):
        raise RuntimeError("bad pdf")
    kw["parse_fn"] = boom
    kw["fetch_feed"] = lambda u: b"<!DOCTYPE x [<!ENTITY a 'b'>]><rss/>"
    out = W.run(now=NOW, **kw)                                                    # no exception
    assert out["feeds"]["espn"]["ok"] is False and any("parse" in e for e in out["errors"])
    assert not (tmp_path / "derived" / "injury_reports_2027.jsonl").exists()
    for _ in range(W.PARSE_RETRIES):                                              # retried from disk, then an error line is stored
        W.run(now=NOW, **kw)
    rec = json.loads((tmp_path / "derived" / "injury_reports_2027.jsonl").read_text().splitlines()[0])
    assert "error" in rec
    kw["fetch_pdf"] = lambda u: (_ for _ in ()).throw(OSError("net down"))        # network failure: counted, not raised
    assert W.run(now=NOW + timedelta(hours=1), **kw)["last_run"]["pdf_errors"] > 0


def test_non_pdf_body_not_saved(tmp_path):
    kw, calls = fake_world(tmp_path, {})
    kw["fetch_pdf"] = lambda u: b"<html>captcha</html>" * 100
    out = W.run(now=NOW, **kw)
    assert out["last_run"]["pdf_errors"] > 0 and not list((tmp_path / "pdf").glob("*.pdf"))


def test_lock_held_skips(tmp_path):
    kw, _ = fake_world(tmp_path, {})
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "run.lock").write_text("1")
    assert "skipped" in W.run(now=NOW, **kw)


def test_hourly_fallback_name_probed_for_on_the_hour_stamp(tmp_path):
    avail = {"Injury-Report_2026-10-23_06PM.pdf": datetime(2026, 10, 23, 22, 30, tzinfo=timezone.utc)}
    kw, calls = fake_world(tmp_path, avail)
    W.run(now=NOW, **kw)
    assert any(u.endswith("_06PM.pdf") for u in calls)


def test_bot_games_parse(tmp_path):
    p = tmp_path / "n.json"
    p.write_text(json.dumps({"2026-10-22|26OCT22DENOKC": {"side": "UNDER"}, "2026-10-21|SCHEDULE": "2026-10-21T21:00:03+00:00"}))
    assert W.bot_games(p) == {("2026-10-22", "DEN", "OKC")}
    assert W.bot_games(tmp_path / "missing.json") == set()


def test_reuses_trade_watch_helpers():
    assert W.TW is T and W.USER_AGENT == "KalshiPM-research/1.0"


def test_tip_of_twelve_hour_clock_without_ampm():
    assert W.tip_of("10/23/2026", "07:30(ET)").hour == 19 and W.tip_of("10/23/2026", "10:30(ET)").hour == 22
    assert W.tip_of("10/23/2026", "12:30(ET)").hour == 12                 # noon, not 00:30
    assert W.tip_of("10/23/2026", "bad") is None and W.tip_of("x", None) is None


def test_late_not_for_reports_after_tip_and_first_report_skips_started_games():
    tip = datetime(2026, 10, 23, 19, 30, tzinfo=ET)
    r1 = rep("r1", tip - timedelta(hours=4), [row("LosAngelesLakers", "James,LeBron", "Questionable")])
    after = rep("r2", tip + timedelta(minutes=10), [row("LosAngelesLakers", "James,LeBron", "Out")])
    assert W.official_alerts([r1, after], 1, PLIST, set(), NOW)[0]["late"] is False
    assert W.official_alerts([after], 0, PLIST, set(), NOW) == []          # first-report listing of a game already under way


def test_news_description_soft_words_do_not_count():
    assert W.is_injury_news("Tyronn Lue dedicates opening win to owner", "Kawhi Leonard made a return after a long summer", True) == []
    assert W.is_injury_news("Opening night recap", "LeBron James left with a sprain", True)


def test_backfilled_older_report_is_baseline_only(tmp_path):
    new = "Injury-Report_2026-10-23_06_30PM.pdf"
    old = "Injury-Report_2026-10-23_06_15PM.pdf"
    kw, calls = fake_world(tmp_path, {new: datetime(2026, 10, 23, 22, 30, tzinfo=timezone.utc)})
    t0 = datetime(2026, 10, 23, 22, 34, tzinfo=timezone.utc)       # 18:34 ET: the 18:15 name is only 19 min old, still retried
    W.run(now=t0, **kw)
    n = len((tmp_path / "logs" / "alerts.jsonl").read_text().splitlines())
    kw2, _ = fake_world(tmp_path, {new: datetime(2026, 10, 23, 22, 30, tzinfo=timezone.utc), old: datetime(2026, 10, 23, 22, 15, tzinfo=timezone.utc)})
    kw2["fetch_feed"] = kw["fetch_feed"]
    out = W.run(now=t0 + timedelta(minutes=2), **kw2)           # the 06:15 report shows up late
    assert old in out["last_run"]["found"] and out["last_run"]["official_alerts"] == 0
    assert len((tmp_path / "logs" / "alerts.jsonl").read_text().splitlines()) == n
