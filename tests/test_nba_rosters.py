import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import nba_rosters as R  # noqa: E402
import nba_player_moves as M  # noqa: E402


def app(rows):
    return pd.DataFrame(rows, columns=["athlete_id", "player", "team", "season", "date", "order"])


def test_in_season_vs_offseason_and_return():
    a = app([
        (1, "A", "BOS", 2025, "2025-03-01", "1"), (1, "A", "BOS", 2025, "2025-03-05", "2"),
        (1, "A", "NYK", 2025, "2025-03-20", "3"),            # in-season move
        (1, "A", "NYK", 2025, "2025-04-10", "4"),
        (1, "A", "BOS", 2026, "2025-10-25", "5"),            # offseason return to old team
        (2, "B", "LAL", 2025, "2025-04-01", "6"),
        (2, "B", "LAL", 2026, "2025-10-30", "7"),            # no move
    ])
    m = M.detect_moves(a)
    assert list(m["from_team"]) == ["BOS", "NYK"] and list(m["to_team"]) == ["NYK", "BOS"]
    assert list(m["type"]) == ["in-season", "offseason"]
    assert m.iloc[0]["last_date_old"] == "2025-03-05" and m.iloc[0]["first_date_new"] == "2025-03-20"
    assert m.iloc[1]["last_date_old"] == "2025-04-10" and m.iloc[1]["season_new"] == 2026


def test_same_day_duplicates_no_move():
    a = app([(1, "A", "BOS", 2025, "2025-03-01", "1"), (1, "A", "BOS", 2025, "2025-03-01", "1"),
             (1, "A", "BOS", 2025, "2025-03-01", "2"), (1, "A", "BOS", 2025, "2025-03-02", "3")])
    assert M.detect_moves(a).empty


def test_same_day_two_teams_orders_by_order_key():
    a = app([(1, "A", "NYK", 2025, "2025-03-01", "b"), (1, "A", "BOS", 2025, "2025-03-01", "a"),
             (1, "A", "NYK", 2025, "2025-03-04", "c")])
    m = M.detect_moves(a)
    assert list(zip(m["from_team"], m["to_team"])) == [("BOS", "NYK")]


def test_swap_label_three_day_rule():
    mv = pd.DataFrame({"athlete_id": [1, 2, 3, 4, 5], "player": list("ABCDE"),
                       "from_team": ["BOS", "NYK", "LAL", "MIA", "BOS"], "to_team": ["NYK", "BOS", "MIA", "LAL", "NYK"],
                       "first_date_new": ["2025-02-01", "2025-02-04", "2025-02-01", "2025-02-05", "2025-02-02"]})
    out = M.label_swaps(mv)
    # A<->B are 3 days apart (swap); C<->D are 4 days apart (not a swap); E (BOS->NYK) pairs with B, 2 days
    assert out.loc[0, "label"] == "likely trade" and out.loc[0, "swap_partner"] == "B"
    assert out.loc[2, "label"] == "moved" and out.loc[3, "label"] == "moved"
    assert out.loc[4, "label"] == "likely trade"


def test_swap_needs_different_players():
    mv = pd.DataFrame({"athlete_id": [1, 1], "player": ["A", "A"], "from_team": ["BOS", "NYK"], "to_team": ["NYK", "BOS"],
                       "first_date_new": ["2025-02-01", "2025-02-02"]})
    assert list(M.label_swaps(mv)["label"]) == ["moved", "moved"]


def test_prior_mpg_has_no_lookahead():
    d = pd.Series(["2025-01-01", "2025-01-03", "2025-01-05", "2025-01-07"]).to_numpy()
    mins = pd.Series([10.0, 20.0, 30.0, 99.0]).to_numpy()
    assert M.prior_mpg(d, mins, "2025-01-07") == (20.0, 3)     # the game ON the move date is excluded
    assert M.prior_mpg(d, mins, "2025-01-01") == (None, 0)
    assert M.prior_mpg(d, mins, "2025-01-07", n=2) == (25.0, 2)


def test_add_prior_mpg_skips_dnp():
    mv = pd.DataFrame({"athlete_id": [1], "first_date_new": ["2025-02-01"]})
    box = pd.DataFrame({"athlete_id": [1, 1, 1, 1], "game_date": ["2025-01-01", "2025-01-02", "2025-01-03", "2025-02-05"],
                        "minutes": [30.0, 0.0, 20.0, 40.0], "did_not_play": [False, True, False, False]})
    out = M.add_prior_mpg(mv, box)
    assert out.loc[0, "prior_mpg20"] == 25.0 and out.loc[0, "prior_games_used"] == 2


def test_team_code_mapping_current():
    for h, k in [("GS", "GSW"), ("NY", "NYK"), ("SA", "SAS"), ("UTAH", "UTA"), ("WSH", "WAS"), ("NO", "NOP"), ("BOS", "BOS")]:
        assert R.kalshi_code(h) == k
        assert R.hoopr_code(k) == h


def test_team_code_mapping_historical_and_nonnba():
    assert R.kalshi_code("NJ") == "BKN" and R.kalshi_code("NJ", 17) == "BKN"
    assert R.kalshi_code("SEA") == "OKC" and R.kalshi_code("SEA", 25) == "OKC"
    assert R.kalshi_code("NOH") == "NOP" and R.kalshi_code("NO", 3) == "NOP"
    assert R.kalshi_code("CHO") == "CHA" and R.kalshi_code("CHA", 30) == "CHA"
    assert R.kalshi_code("EAST") is None and R.kalshi_code("WORLD", 111386) is None and R.kalshi_code("X", 31) is None
    assert len(set(R.FRANCHISE_BY_ID.values())) == 30


def test_nj_to_bkn_is_not_a_move():
    a = app([(1, "A", R.kalshi_code("NJ", 17), 2012, "2012-04-01", "1"), (1, "A", R.kalshi_code("BKN", 17), 2013, "2012-11-01", "2")])
    assert M.detect_moves(a).empty


def test_atomic_write_leaves_no_part_file(tmp_path):
    p = tmp_path / "x" / "f.parquet"
    R.atomic_write_bytes(p, b"PAR1abc")
    assert p.read_bytes() == b"PAR1abc" and [f.name for f in p.parent.iterdir()] == ["f.parquet"]
    R.atomic_write_bytes(p, b"PAR1zzz")
    assert p.read_bytes() == b"PAR1zzz"


class FakeResp:
    def __init__(self, code, content=b""):
        self.status_code, self.content = code, content


class FakeSession:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def get(self, url, **kw):
        self.calls.append(url)
        return self.resp


def test_fetch_caching_and_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "RAW", tmp_path)
    monkeypatch.setattr(R, "META_PATH", tmp_path / "_meta.json")
    s = FakeSession(FakeResp(200, b"PAR1data"))
    p = R.fetch("rosters", "rosters_2027.parquet", "rosters/parquet/rosters_2027.parquet", R.CURRENT_SEASON, session=s)
    assert p.read_bytes() == b"PAR1data" and len(s.calls) == 1 and s.calls[0].startswith("https://raw.githubusercontent.com/")
    R.fetch("rosters", "rosters_2027.parquet", "x", R.CURRENT_SEASON, session=s)       # fresh -> no second GET
    assert len(s.calls) == 1
    old = time.time() - 13 * 3600
    os.utime(p, (old, old))
    R.fetch("rosters", "rosters_2027.parquet", "x", R.CURRENT_SEASON, session=s)       # >12h -> refetch
    assert len(s.calls) == 2
    # old season: downloaded once, never refetched
    R.fetch("game_rosters", "g_2010.parquet", "g", 2010, session=s)
    os.utime(tmp_path / "game_rosters" / "g_2010.parquet", (old - 9e6, old - 9e6))
    n = len(s.calls)
    R.fetch("game_rosters", "g_2010.parquet", "g", 2010, session=s)
    assert len(s.calls) == n
    # 404 -> None, no file; bad body -> not stored
    assert R.fetch("x", "m_2009.parquet", "m", 2009, session=FakeSession(FakeResp(404))) is None
    assert R.fetch("x", "bad_2009.parquet", "b", 2009, session=FakeSession(FakeResp(200, b"<html>"))) is None
    assert not (tmp_path / "x").exists() or not list((tmp_path / "x").iterdir())
    # failed refresh keeps the stale copy
    os.utime(p, (old, old))
    assert R.fetch("rosters", "rosters_2027.parquet", "x", R.CURRENT_SEASON, session=FakeSession(FakeResp(500))) == p
    assert p.read_bytes() == b"PAR1data"


def test_offline_does_not_download(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "RAW", tmp_path)
    assert R.fetch("a", "b_2027.parquet", "b", R.CURRENT_SEASON, offline=True) is None


def test_asof_reporting(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "META_PATH", tmp_path / "_meta.json")
    R.META_PATH.write_text('{"rosters/rosters_2027.parquet": {"fetched_at_utc": "2026-10-23T01:00:00Z", "github_last_commit": "2026-10-22T11:34:13Z"}}')
    df = pd.DataFrame({"game_date": ["2026-10-20", "2026-10-21"]})
    info = R.asof_info("rosters", "rosters_2027.parquet", df, "game_date")
    s = R.fmt_asof(info)
    assert "2026-10-22T11:34:13Z" in s and "2026-10-23T01:00:00Z" in s and "2026-10-21" in s
    s2 = R.fmt_asof(R.asof_info("rosters", "missing.parquet"))
    assert "unknown" in s2 and "n/a" in s2


def test_name_norm_and_latest_injury():
    assert R.norm_name("Walker IV,Lonnie") == R.norm_name("Lonnie Walker IV") == "lonnie walker"
    assert R.norm_name("Fox,De'Aaron") == "deaaron fox"
    reps = [("r1", "10/19/21 06:30 PM", [{"date": "10/19/2021", "player": "Irving,Kyrie", "status": "Out", "matchup": "X"}]),
            ("r2", "10/20/21 06:30 PM", [{"date": "10/20/2021", "player": "Irving,Kyrie", "status": "Questionable", "matchup": "Y"}]),
            ("r3", "10/19/21 08:30 PM", [{"date": "10/19/2021", "player": "Irving,Kyrie", "status": "Probable", "matchup": "Z"}])]
    inj, meta = R.latest_injury(reps)
    assert inj["kyrie irving"]["status"] == "Questionable" and meta["latest_report_date"] == "2021-10-20" and meta["n_reports"] == 3


def test_check_sources_toy():
    ros = pd.DataFrame({"team_id": [2, 2, 18], "team_abbreviation": ["BOS", "BOS", "NY"], "athlete_id": [1, 2, 3],
                        "full_name": ["P1", "P2", "P3"]})
    grs = pd.DataFrame({"game_id": ["g1"] * 3, "team_id": [2, 2, 2], "team_abbreviation": ["BOS"] * 3, "athlete_id": [1, 3, 9],
                        "athlete_display_name": ["P1", "P3", "P9"]})
    gd = pd.DataFrame({"game_id": ["g1"], "game_date": ["2026-10-20"]})
    box = pd.DataFrame({"athlete_id": [1, 3], "team_id": [2, 2], "team_abbreviation": ["BOS", "BOS"], "athlete_display_name": ["P1", "P3"],
                        "did_not_play": [False, False], "minutes": [30.0, 12.0], "game_date": ["2026-10-20"] * 2})
    res = R.check_sources(ros, grs, box, gd)
    assert [r["athlete_id"] for r in res["dropped_since_last_game"]] == [3, 9]  # 3 moved to NYK, 9 gone
    assert [r["athlete_id"] for r in res["added_since_last_game"]] == [2]
    assert res["roster_team_differs_from_box"][0]["athlete_id"] == 3 and res["roster_team_differs_from_box"][0]["roster_team"] == "NYK"
    assert res["sheet_team_differs"][0]["athlete_id"] == 3
    assert res["no_games_yet"] == ["NYK"]


def test_load_injury_latest_reads_live_watcher_file(tmp_path):
    import json
    (tmp_path / "data/derived").mkdir(parents=True)
    old = [{"date": "01/25/2026", "player": "Irving,Kyrie", "status": "Available", "matchup": "A@B"}]
    new = [{"date": "10/23/2026", "player": "Irving,Kyrie", "status": "Out", "matchup": "DAL@LAL"}]
    (tmp_path / "data/derived/injury_reports_2027.jsonl").write_text(
        json.dumps({"name": "Injury-Report_2026-10-23_05_00PM.pdf", "header": "10/23/26 05:00 PM", "rows": new}) + "\n"
        + "not json\n" + json.dumps({"name": "bad", "rows": []}) + "\n", encoding="utf-8")
    inj, meta = R.load_injury_latest(root=tmp_path)
    assert inj["kyrie irving"]["status"] == "Out" and inj["kyrie irving"]["date"] == "2026-10-23"
    assert meta["latest_report_date"] == "2026-10-23" and meta["n_reports"] == 1
