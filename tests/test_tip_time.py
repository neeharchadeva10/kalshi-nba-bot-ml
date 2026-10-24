"""Tip time from the hoopR schedule (kpm.collectors.nba_tip_time) and its live call sites."""
import importlib.util
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from kpm.collectors import nba_tip_time as tt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import early_overs_picks as eop  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 10, 23, 18, 0, tzinfo=UTC)
KEY = "26OCT23HOUSAS"
TIP = datetime(2026, 10, 24, 0, 30, tzinfo=UTC)          # 8:30 pm ET on Oct 23


def occ(tip, minutes=0):
    """Kalshi occurrence_datetime for a tip: tip + 3 h (+ minutes of disagreement)."""
    return (tip + timedelta(hours=3, minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def row(away, home, tip, **kw):
    return {"game_date_time": tip, "away_abbreviation": away, "home_abbreviation": home, "time_valid": True,
            "status_type_name": "STATUS_SCHEDULED", "status_type_detail": "", **kw}


def sched(*rows, fetched_at=NOW - timedelta(minutes=30)):
    return tt.schedule_from_rows(rows or [row("HOU", "SA", TIP)], fetched_at)


# ---------------------------------------------------------------- get_tip_time
def test_exact_match_uses_schedule():
    r = tt.get_tip_time(KEY, occ(TIP), live=True, schedule=sched(), now=NOW)
    assert r.tip == TIP and r.source == "hoopr"
    assert tt.get_tip_time(KEY, None, live=True, schedule=sched(), now=NOW).tip == TIP   # no Kalshi time to check


def test_schedule_wins_within_15_minutes_and_exactly_15_is_accepted():
    r = tt.get_tip_time(KEY, occ(TIP, 10), live=True, schedule=sched(), now=NOW)
    assert r.tip == TIP and r.source == "hoopr"
    assert tt.get_tip_time(KEY, occ(TIP, -15), live=True, schedule=sched(), now=NOW).tip == TIP


@pytest.mark.parametrize("live", [True, False])
@pytest.mark.parametrize("minutes", [16, -20, -510])
def test_disagreement_over_15_minutes_skips(caplog, live, minutes):
    with caplog.at_level(logging.WARNING, logger="kpm.nba_tip_time"):
        r = tt.get_tip_time(KEY, occ(TIP, minutes), live=live, schedule=sched(), now=NOW)
    assert r.tip is None and r.source == "skip" and "disagreement" in r.note
    assert any("disagreement" in m and KEY in m for m in caplog.messages)


def test_fallback_when_not_in_schedule_logs_warning(caplog):
    other = sched(row("BOS", "NY", TIP))
    with caplog.at_level(logging.WARNING, logger="kpm.nba_tip_time"):
        r = tt.get_tip_time(KEY, occ(TIP, 20), live=False, schedule=other, now=NOW)
    assert r.tip == TIP + timedelta(minutes=20) and r.source == "fallback"
    assert any("FALLBACK" in m and KEY in m for m in caplog.messages)
    assert tt.get_tip_time(KEY, None, live=False, schedule=other, now=NOW).tip is None   # nothing to fall back to


def test_live_missing_game_skips(caplog):
    with caplog.at_level(logging.WARNING, logger="kpm.nba_tip_time"):
        r = tt.get_tip_time(KEY, occ(TIP), live=True, schedule=sched(row("BOS", "NY", TIP)), now=NOW)
    assert r.tip is None and r.note == "game not in hoopR schedule"
    assert any("skipping" in m for m in caplog.messages)
    assert tt.get_tip_time(KEY, occ(TIP), live=True, schedule=None, now=NOW).tip is None   # no file at all


@pytest.mark.parametrize("kw", [
    {"time_valid": False},                                                  # ESPN: time not set
    {"status_type_detail": "10/23 - TBD"},
    {"status_type_name": "STATUS_POSTPONED"},
    {"game_date_time": datetime(2026, 10, 23, 4, 0, tzinfo=UTC)},            # 00:00 ET placeholder
])
def test_placeholder_time_skips_live_and_falls_back_otherwise(kw):
    s = sched(row("HOU", "SA", TIP, **{k: v for k, v in kw.items()}))
    r = tt.get_tip_time(KEY, occ(TIP), live=True, schedule=s, now=NOW)
    assert r.tip is None and "TBD/placeholder" in r.note
    assert tt.get_tip_time(KEY, occ(TIP), live=False, schedule=s, now=NOW).source == "fallback"


def test_stale_schedule_skips_live_only():
    s = sched(fetched_at=NOW - timedelta(hours=37))
    r = tt.get_tip_time(KEY, occ(TIP), live=True, schedule=s, now=NOW)
    assert r.tip is None and "37h old" in r.note
    assert tt.get_tip_time(KEY, occ(TIP), live=True, schedule=sched(fetched_at=NOW - timedelta(hours=35)),
                           now=NOW).tip == TIP
    assert tt.get_tip_time(KEY, occ(TIP), live=False, schedule=s, now=NOW).source == "hoopr"


def test_team_codes_et_date_and_duplicates():
    # hoopR GS/NY/SA/UTAH/WSH/NO -> Kalshi codes; a 02:00Z tip belongs to the previous ET date
    s = sched(row("GS", "NY", datetime(2026, 10, 25, 2, 0, tzinfo=UTC)), row("UTAH", "NO", TIP), row("TBD", "TBD", TIP))
    assert tt.get_tip_time("26OCT24GSWNYK", None, live=True, schedule=s, now=NOW).source == "hoopr"
    assert tt.get_tip_time("26OCT23UTANOP", None, live=True, schedule=s, now=NOW).source == "hoopr"
    assert tt.get_tip_time("26OCT25GSWNYK", None, live=True, schedule=s, now=NOW).tip is None
    dup = sched(row("HOU", "SA", TIP), row("HOU", "SA", TIP + timedelta(hours=1)))
    assert tt.get_tip_time(KEY, None, live=True, schedule=dup, now=NOW).tip is None     # ambiguous -> no time
    assert tt.get_tip_time("bad", occ(TIP), live=False, schedule=sched(), now=NOW).tip is None


def write_parquet(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df["game_date_time"] = pd.to_datetime(df["game_date_time"], utc=True).dt.tz_convert("America/New_York")
    df.to_parquet(path)


def test_load_schedule_from_file_and_stale_file(tmp_path):
    path = tt.CACHE_DIR / "nba_schedule_2027.parquet"
    write_parquet(path, [row("HOU", "SA", TIP), row("TBD", "TBD", datetime(2026, 12, 11, 5, 0, tzinfo=UTC),
                                                    time_valid=False, status_type_detail="12/11 - TBD")])
    fresh = NOW.timestamp() - 3600
    os.utime(path, (fresh, fresh))
    r = tt.get_tip_time(KEY, occ(TIP), live=True, now=NOW)       # loads data/.../nba_schedule_2027.parquet itself
    assert r.tip == TIP and r.source == "hoopr"
    old = NOW.timestamp() - 40 * 3600                               # download failed for 40 h: file kept, but stale
    os.utime(path, (old, old))
    assert tt.get_tip_time(KEY, occ(TIP), live=True, now=NOW).tip is None
    assert tt.get_tip_time(KEY, occ(TIP), live=False, now=NOW).source == "hoopr"


def test_failed_download_keeps_cached_copy(tmp_path, monkeypatch):
    path = tt.CACHE_DIR / "nba_schedule_2027.parquet"
    write_parquet(path, [row("HOU", "SA", TIP)])
    old = NOW.timestamp() - 2 * 3600
    os.utime(path, (old, old))
    monkeypatch.setattr(tt, "DOWNLOAD", True)

    class Down:
        def get(self, *a, **k):
            raise OSError("net down")
    assert tt.fetch_schedule(2027, session=Down(), now=NOW) == path and path.stat().st_mtime == old


# ---------------------------------------------------------------- live call sites
def fake_kalshi(monkeypatch, keys_occ: dict):
    """compute_rows inputs: one KXNBAGAME + one KXNBATOTAL market per game; no network, no ratings."""
    def markets(series):
        return [{"event_ticker": f"{series}-{k}", "occurrence_datetime": o, "ticker": f"{series}-{k}-230",
                 "floor_strike": 230.5} for k, o in keys_occ.items()]
    monkeypatch.setattr(eop, "all_open_markets", markets)
    monkeypatch.setattr(eop, "fetch_box27", lambda: None)
    monkeypatch.setattr(eop, "load_engine", lambda *a, **k: (None, 0))
    monkeypatch.setattr(eop, "recent_game_keys", lambda now: set())


def use_schedule(monkeypatch, s):
    monkeypatch.setattr(tt, "load_schedule", lambda season, cache_dir=None, now=None: s)


@pytest.mark.parametrize("case,s,note", [
    ("missing", sched(row("BOS", "NY", TIP)), "SKIP: game not in hoopR schedule"),
    ("placeholder", sched(row("HOU", "SA", TIP, time_valid=False)), "SKIP: hoopR schedule has no real time"),
    ("stale", sched(fetched_at=NOW - timedelta(hours=40)), "SKIP: hoopR schedule file is 40h old"),
    ("disagree", sched(row("HOU", "SA", TIP + timedelta(minutes=20))), "SKIP: tip disagreement"),
])
def test_compute_rows_skips_game_without_confirmed_tip(monkeypatch, case, s, note):
    fake_kalshi(monkeypatch, {KEY: occ(TIP)})
    use_schedule(monkeypatch, s)
    seen = []
    rows, _n27, games, _eng = eop.compute_rows(date(2026, 10, 23), NOW, lambda n, g: seen.extend(g))
    assert games == [] and seen == []                         # never reaches the bot's game list (no window, no bet)
    assert len(rows) == 1 and rows[0]["game"] == KEY and rows[0]["bet"] is False
    assert rows[0]["status"].startswith(note) and rows[0]["tip_et"] == "2026-10-23 --:--"


def test_compute_rows_keeps_good_game_and_lists_skip(monkeypatch):
    other = "26OCT23BOSNYK"
    fake_kalshi(monkeypatch, {KEY: occ(TIP), other: occ(TIP)})
    use_schedule(monkeypatch, sched(row("HOU", "SA", TIP)))

    class Stop(Exception):
        pass
    got = []

    def hdr(n, g):
        got.extend(g)
        raise Stop
    with pytest.raises(Stop):
        eop.compute_rows(date(2026, 10, 23), NOW, hdr)
    assert [(t, k) for t, k, _ in got] == [(TIP, KEY)]


def test_compute_rows_other_night_skip_not_listed(monkeypatch):
    fake_kalshi(monkeypatch, {"26OCT24HOUSAS": occ(TIP + timedelta(days=1))})
    use_schedule(monkeypatch, sched(row("BOS", "NY", TIP)))
    rows, _n27, games, _eng = eop.compute_rows(date(2026, 10, 23), NOW)
    assert rows == [] and games == []


def load_dk():
    spec = importlib.util.spec_from_file_location("dk_snapshot_t", ROOT / "reports/output/nba_dk_gap/dk_snapshot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dk_snapshot_uses_live_rule(monkeypatch):
    dk = load_dk()
    fake_kalshi(monkeypatch, {KEY: occ(TIP), "26OCT23BOSNYK": occ(TIP)})
    real_now = datetime.now(UTC)                                # kalshi_games checks staleness against the clock
    use_schedule(monkeypatch, sched(row("HOU", "SA", TIP), fetched_at=real_now))
    out = dk.kalshi_games(eop, date(2026, 10, 23))
    assert [(t, k) for t, k, _ in out] == [(TIP, KEY)]          # BOS@NYK not in the schedule: left out, no -3h guess
    use_schedule(monkeypatch, sched(fetched_at=real_now - timedelta(hours=40)))
    assert dk.kalshi_games(eop, date(2026, 10, 23)) == []       # stale schedule: nothing for live betting


# ---------------------------------------------------------------- fallback call sites
def test_bet_journal_falls_back_with_warning(caplog, tmp_path):
    import bet_journal as bj

    class Client:
        def market(self, t):
            return {"market": {"result": "no", "occurrence_datetime": occ(TIP)}}
    cache = bj.Cache(tmp_path / "cache.json")
    with caplog.at_level(logging.WARNING, logger="kpm.nba_tip_time"):
        info = bj.game_facts(Client(), cache, KEY, None)            # no schedule file in tests -> occurrence - 3h
    assert info["tip_utc"] == TIP.isoformat()
    assert any("FALLBACK" in m for m in caplog.messages)


def test_compute_rows_reports_skips_to_on_skips_with_reason(monkeypatch):
    fake_kalshi(monkeypatch, {KEY: occ(TIP)})
    use_schedule(monkeypatch, sched(fetched_at=NOW - timedelta(hours=40)))
    got = []
    eop.compute_rows(date(2026, 10, 23), NOW, on_skips=got.extend)
    assert [(r["game"], r["tip_skip"]) for r in got] == [(KEY, "stale")]
