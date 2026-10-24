"""Tests for the #66 weather build. Toy/fixture data only; no network."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lib  # noqa: E402
import fetch_archive as fa  # noqa: E402

spec = importlib.util.spec_from_file_location("nfl_wind_forecast", HERE.parents[3] / "scripts" / "nfl_wind_forecast.py")
fwd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fwd)


# ---------------------------------------------------------------- kickoff -> UTC / hour
def test_kickoff_utc_dst():
    assert lib.kickoff_utc("2024-09-08", "13:00") == pd.Timestamp("2024-09-08 17:00", tz="UTC")   # EDT = UTC-4
    assert lib.kickoff_utc("2024-12-08", "13:00") == pd.Timestamp("2024-12-08 18:00", tz="UTC")   # EST = UTC-5
    # 2024 DST ended Sunday Nov 3: a 20:20 ET kickoff that night is EST
    assert lib.kickoff_utc("2024-11-03", "20:20") == pd.Timestamp("2024-11-04 01:20", tz="UTC")
    assert lib.kickoff_utc("2024-11-03", "13:00") == pd.Timestamp("2024-11-03 18:00", tz="UTC")   # 13:00 after fall-back
    assert lib.kickoff_utc("2024-03-10", "13:00") == pd.Timestamp("2024-03-10 17:00", tz="UTC")   # spring-forward day
    assert pd.isna(lib.kickoff_utc("1999-09-12", float("nan")))


def test_forward_script_agrees_with_lib():
    for d, t in [("2024-09-08", "13:00"), ("2025-01-04", "16:30"), ("2024-11-03", "20:20"), ("2026-10-25", "09:30")]:
        assert fwd.kickoff_utc(d, t) == lib.kickoff_utc(d, t)


def test_kickoff_hour_floor():
    assert lib.kickoff_hour(pd.Timestamp("2024-09-08 20:25", tz="UTC")) == pd.Timestamp("2024-09-08 20:00", tz="UTC")
    assert lib.kickoff_hour(pd.Timestamp("2024-09-08 20:00", tz="UTC")) == pd.Timestamp("2024-09-08 20:00", tz="UTC")
    # 4:30 pm ET = 21:30 UTC (EST) -> hour 21:00
    k = lib.kickoff_utc("2025-01-04", "16:30")
    assert lib.kickoff_hour(k) == pd.Timestamp("2025-01-04 21:00", tz="UTC")


# ---------------------------------------------------------------- stadium coverage
def test_every_game_stadium_in_table():
    games = lib.load_games()
    st = lib.load_stadiums()
    assert lib.check_stadium_coverage(games, st, 1999, 2026) == []
    assert st.roof_category.isin(["open-air", "dome", "retractable"]).all()
    assert st.stadium_id.is_unique and st[["lat", "lon"]].notna().all().all()


def test_coverage_check_detects_missing():
    games = pd.DataFrame({"season": [2020, 2020], "stadium_id": ["XXX00", "BOS00"], "stadium": ["Nowhere", "Gillette Stadium"]})
    assert lib.check_stadium_coverage(games, lib.load_stadiums()) == ["XXX00"]


def test_known_roof_categories():
    st = lib.load_stadiums()
    for sid in ["PHO00", "ATL97", "DAL00", "HOU00", "IND00"]:
        assert st.loc[sid, "roof_category"] == "retractable"
    for sid in ["NOR00", "DET00", "MIN01", "VEG00", "LAX01"]:
        assert st.loc[sid, "roof_category"] == "dome"
    for sid in ["GNB00", "BOS00", "BUF00", "CHI98", "NYC01"]:
        assert st.loc[sid, "roof_category"] == "open-air"


def test_name_override_london_game():
    assert lib.resolve_stadium_id("JAX00", "Tottenham Hotspur Stadium") == "LON02"
    assert lib.resolve_stadium_id("JAX00", "EverBank Stadium") == "JAX00"


def test_open_air_selection_uses_both_rules():
    st = lib.load_stadiums()
    g = pd.DataFrame({
        "game_id": ["a", "b", "c", "d"], "season": [2024] * 4, "game_type": ["REG"] * 4, "week": [1] * 4,
        "gameday": ["2024-09-08"] * 4, "gametime": ["13:00"] * 4, "roof": ["outdoors", "closed", "outdoors", "open"],
        "temp": [70] * 4, "wind": [5] * 4,
        "stadium_id": ["BOS00", "PHO00", "NOR00", "RIO00"], "stadium": ["Gillette Stadium", "State Farm Stadium",
                                                                        "Caesars Superdome", "Maracana Stadium"]})
    oa, _ = fa.open_air_games(g, st)
    assert sorted(oa.game_id) == ["a", "d"]       # retractable and dome stadium excluded even if roof says outdoors


# ---------------------------------------------------------------- issue-time guarantee
def test_variant_guarantee():
    assert lib.variant_guarantee_h("previous_day1") == 24
    assert lib.variant_guarantee_h("previous_day2") == 48
    for bad in ["previous_day0", "best_match", "historical_forecast"]:
        with pytest.raises(ValueError):
            lib.variant_guarantee_h(bad)


def test_issue_ok_boundary():
    k = pd.Timestamp("2024-09-08 17:00", tz="UTC")
    assert lib.issue_ok(k, pd.Timestamp("2024-09-07 17:00", tz="UTC"))            # exactly 24 h: ok
    assert not lib.issue_ok(k, pd.Timestamp("2024-09-07 17:01", tz="UTC"))
    assert lib.issue_ok(k, pd.Timestamp("2024-09-06 06:00", tz="UTC"))


def test_guarantee_ok_kickoff_hour_floor_never_hurts():
    k = pd.Timestamp("2024-09-08 17:25", tz="UTC")
    h = lib.kickoff_hour(k)
    assert lib.guarantee_ok("previous_day1", k, h)         # init <= 16:00 - 24h = 09-07 16:00  <= k - 24h
    assert lib.guarantee_ok("previous_day2", k, h)
    # a (hypothetical) 23 h guarantee would fail; model it through min_h
    assert not lib.guarantee_ok("previous_day1", k, h, min_h=24 + 2)


def test_forecast_ok_false_when_value_missing(tmp_path):
    # forecast_ok in fetch_archive = value present AND guarantee holds -> a NaN forecast can never be ok
    df = pd.DataFrame({"fc_wind_mph": [np.nan, 12.0], "fc_variant": ["previous_day1"] * 2,
                       "kickoff_utc": [pd.Timestamp("2024-09-08 17:00", tz="UTC")] * 2,
                       "kickoff_hour_utc": [pd.Timestamp("2024-09-08 17:00", tz="UTC")] * 2})
    ok = df.fc_wind_mph.notna().to_numpy() & np.array(
        [lib.guarantee_ok(v, k, h) for v, k, h in zip(df.fc_variant, df.kickoff_utc, df.kickoff_hour_utc)])
    assert ok.tolist() == [False, True]


# ---------------------------------------------------------------- parsing
FIXTURE = {
    "latitude": 42.1, "longitude": -71.25, "elevation": 79.0, "timezone": "GMT",
    "hourly": {"time": ["2024-09-08T16:00", "2024-09-08T17:00", "2024-09-08T18:00"],
               "wind_speed_10m_previous_day1": [8.1, 12.4, None], "temperature_2m_previous_day1": [70.0, 71.5, 72.0]},
}


def test_parse_hourly_fixture():
    df = lib.parse_hourly(FIXTURE)
    assert str(df.index.tz) == "UTC"
    assert df.at[pd.Timestamp("2024-09-08 17:00", tz="UTC"), "wind_speed_10m_previous_day1"] == 12.4
    assert np.isnan(df.at[pd.Timestamp("2024-09-08 18:00", tz="UTC"), "wind_speed_10m_previous_day1"])


def test_parse_hourly_rejects_non_utc():
    bad = dict(FIXTURE, timezone="America/New_York")
    with pytest.raises(ValueError):
        lib.parse_hourly(bad)


def test_forward_parse_values():
    resp = {"timezone": "GMT", "hourly": {"time": ["2026-10-25T17:00", "2026-10-25T18:00"],
                                          "wind_speed_10m": [9.0, 11.0], "wind_gusts_10m": [15.0, 20.0],
                                          "temperature_2m": [50.0, 51.0], "precipitation": [0.0, 0.2]}}
    v = fwd.parse_values(resp, pd.Timestamp("2026-10-25 18:05", tz="UTC"))
    assert v["wind_mph"] == 11.0 and v["gust_mph"] == 20.0 and v["precip_mm"] == 0.2
    assert fwd.parse_values(resp, pd.Timestamp("2026-10-26 18:05", tz="UTC")) is None


def test_cache_key_stable_and_params_sensitive():
    k1 = lib.OpenMeteo.key("u", {"a": 1, "b": 2})
    assert k1 == lib.OpenMeteo.key("u", {"b": 2, "a": 1})
    assert k1 != lib.OpenMeteo.key("u", {"a": 1, "b": 3})


def test_request_weight():
    p = {"hourly": "a,b,c,d,e", "start_date": "2024-09-01", "end_date": "2025-02-10"}
    assert lib.OpenMeteo.weight(p) == pytest.approx(5 / 10 * 163 / 14)
    assert lib.OpenMeteo.weight({"hourly": "a", "start_date": "2024-09-01", "end_date": "2024-09-01"}) == 1.0


# ---------------------------------------------------------------- no outcome columns
def test_loader_drops_outcome_columns(tmp_path):
    p = tmp_path / "games.csv"
    pd.DataFrame({"game_id": ["g1"], "season": [2024], "game_type": ["REG"], "week": [1], "gameday": ["2024-09-08"],
                  "gametime": ["13:00"], "away_team": ["A"], "home_team": ["B"], "roof": ["outdoors"], "temp": [70],
                  "wind": [5], "stadium_id": ["BOS00"], "stadium": ["Gillette Stadium"],
                  "home_score": [20], "away_score": [17], "total": [37], "result": [3], "total_line": [44.5],
                  "spread_line": [-3.0], "overtime": [0]}).to_csv(p, index=False)
    df = lib.load_games(p)
    assert not (lib.FORBIDDEN_COLS & set(df.columns))
    assert {"wind", "temp"} <= set(df.columns)


def test_real_loader_and_outputs_have_no_outcome_columns():
    assert not (lib.FORBIDDEN_COLS & set(lib.load_games().columns))
    out = HERE / "wind_forecasts.parquet"
    if out.exists():
        assert not (lib.FORBIDDEN_COLS & set(pd.read_parquet(out).columns))


def test_forward_script_reads_no_outcome_columns():
    assert not (lib.FORBIDDEN_COLS & set(fwd.SAFE_COLS))
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    g = fwd.upcoming_games(now)
    assert not (lib.FORBIDDEN_COLS & set(g.columns))
    assert (g.kickoff_utc > pd.Timestamp(now)).all() and (g.kickoff_utc <= pd.Timestamp(now) + pd.Timedelta(days=8)).all()


def test_games_refresh_skipped_when_fresh(tmp_path):
    f = tmp_path / "games.csv"
    f.write_text("game_id,x\n")
    msg = fwd.refresh_games(datetime.now(timezone.utc), f, url="http://invalid.invalid/")
    assert "fresh" in msg
