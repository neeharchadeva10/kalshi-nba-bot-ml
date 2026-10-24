"""Download the Open-Meteo archive for every OPEN-AIR NFL game 2018-2026 and write wind_forecasts.parquet.

Open-air = stadiums.csv roof_category == 'open-air' AND games.csv roof in {outdoors, open}  (Amendment 0 item 6).
One request per (stadium, season) per API, restricted to the date span of that stadium's games that season.
Resumable: every raw response is cached under cache/<api>/. Rerunning costs no requests.

APIs (all free, no key, hourly, UTC, mph, Fahrenheit, mm):
  prev  https://previous-runs-api.open-meteo.com/v1/forecast       *_previous_day1 / _day2 (the >=24 h-issued forecast)
  hf    https://historical-forecast-api.open-meteo.com/v1/forecast short-lead forecast (reference only)
  era5  https://archive-api.open-meteo.com/v1/archive  models=era5   reanalysis ("observed" proxy)
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import lib

FIRST_SEASON, LAST_SEASON = 2018, 2026
URLS = {
    "prev": "https://previous-runs-api.open-meteo.com/v1/forecast",
    "hf": "https://historical-forecast-api.open-meteo.com/v1/forecast",
    "era5": "https://archive-api.open-meteo.com/v1/archive",
}
VARS = {
    "prev": ["wind_speed_10m_previous_day1", "wind_speed_10m_previous_day2", "wind_gusts_10m_previous_day1",
             "temperature_2m_previous_day1", "precipitation_previous_day1"],
    "hf": ["wind_speed_10m", "temperature_2m"],
    "era5": ["wind_speed_10m", "temperature_2m"],
}
ERA5_DELAY_DAYS = 7          # ERA5 is published with a lag of about 5 days


def open_air_games(games: pd.DataFrame, stadiums: pd.DataFrame) -> pd.DataFrame:
    g = games[(games.season >= FIRST_SEASON) & (games.season <= LAST_SEASON)].copy()
    g["sid"] = [lib.resolve_stadium_id(a, b) for a, b in zip(g.stadium_id, g.stadium)]
    g["kickoff_utc"] = [lib.kickoff_utc(d, t) for d, t in zip(g.gameday, g.gametime)]
    g["kickoff_hour_utc"] = g.kickoff_utc.map(lib.kickoff_hour)
    cat = g.sid.map(stadiums.roof_category)
    keep = (cat == "open-air") & g.roof.isin(lib.OPEN_ROOFS)
    g["excluded_reason"] = np.select([cat != "open-air", ~g.roof.isin(lib.OPEN_ROOFS)],
                                     ["stadium not open-air", "games.csv roof not outdoors/open"], "")
    return g[keep & g.kickoff_utc.notna()].copy(), g


def request_params(api: str, lat: float, lon: float, start: str, end: str) -> dict:
    p = dict(latitude=lat, longitude=lon, start_date=start, end_date=end, hourly=",".join(VARS[api]),
             wind_speed_unit="mph", temperature_unit="fahrenheit", precipitation_unit="mm", timezone="UTC")
    if api == "era5":
        p["models"] = "era5"
    return p


def span(api: str, d: pd.DataFrame, now: datetime) -> tuple[str, str] | None:
    lo = d.kickoff_utc.min().floor("D").date()
    hi = d.kickoff_utc.max().floor("D").date()
    cap = (now - timedelta(days=ERA5_DELAY_DAYS if api == "era5" else 1)).date()
    hi = min(hi, cap)
    return None if lo > hi else (str(lo), str(hi))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="debug: only the first N (stadium, season) groups")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    stadiums = lib.load_stadiums()
    games = lib.load_games()
    miss = lib.check_stadium_coverage(games, stadiums)
    assert not miss, f"stadium ids missing from stadiums.csv: {miss}"
    oa, allg = open_air_games(games, stadiums)
    print(f"{len(oa)} open-air games 2018-2026 of {len(allg)}; groups:", oa.groupby(['sid', 'season']).ngroups)

    om = lib.OpenMeteo()
    series: dict[str, dict[tuple, pd.DataFrame]] = {a: {} for a in URLS}
    grid = {}
    groups = list(oa.groupby(["sid", "season"]))
    if args.limit:
        groups = groups[:args.limit]
    for i, ((sid, season), d) in enumerate(groups, 1):
        lat, lon = stadiums.loc[sid, ["lat", "lon"]]
        for api in URLS:
            sp = span(api, d, now)
            if sp is None:
                continue
            resp = om.get(api, URLS[api], request_params(api, lat, lon, *sp))
            series[api][(sid, season)] = lib.parse_hourly(resp)
            grid[sid] = (resp["latitude"], resp["longitude"], resp.get("elevation"))
        if i % 10 == 0 or i == len(groups):
            print(f"[{i}/{len(groups)}] requests={om.n_requests} cached={om.n_cached}", flush=True)

    def look(api, col, sid, season, t):
        df = series[api].get((sid, season))
        if df is None or col not in df.columns or t not in df.index:
            return np.nan
        return df.at[t, col]

    out = []
    for r in oa.itertuples():
        k = (r.sid, r.season)
        t = r.kickoff_hour_utc
        lat, lon = stadiums.loc[r.sid, ["lat", "lon"]]
        pd1 = look("prev", "wind_speed_10m_previous_day1", *k, t)
        out.append(dict(
            game_id=r.game_id, season=r.season, game_type=r.game_type, week=r.week, roof=r.roof,
            kickoff_utc=r.kickoff_utc, kickoff_hour_utc=t, stadium_id=r.sid, lat=lat, lon=lon,
            fc_wind_mph=pd1,
            fc_variant="previous_day1", fc_issue_guarantee_h=lib.variant_guarantee_h("previous_day1"),
            fc_gust_mph=look("prev", "wind_gusts_10m_previous_day1", *k, t),
            fc_temp_f=look("prev", "temperature_2m_previous_day1", *k, t),
            fc_precip_mm=look("prev", "precipitation_previous_day1", *k, t),
            fc_wind_d2_mph=look("prev", "wind_speed_10m_previous_day2", *k, t),   # strict 48 h alternative
            hf_wind_mph=look("hf", "wind_speed_10m", *k, t), hf_temp_f=look("hf", "temperature_2m", *k, t),
            era5_wind_mph=look("era5", "wind_speed_10m", *k, t), era5_temp_f=look("era5", "temperature_2m", *k, t),
            obs_wind_mph=r.wind, obs_temp_f=r.temp,
        ))
    df = pd.DataFrame(out)
    # forecast_ok: the >=24 h-issued variant exists for the kickoff hour. Defined from the variant's guarantee and
    # the availability of the value, never from the value itself.
    df["forecast_ok"] = df.fc_wind_mph.notna().to_numpy() & np.array([
        lib.guarantee_ok(v, k, h) for v, k, h in zip(df.fc_variant, df.kickoff_utc, df.kickoff_hour_utc)])
    df.loc[~df.forecast_ok, ["fc_issue_guarantee_h"]] = np.nan
    df.to_parquet(lib.HERE / "wind_forecasts.parquet", index=False)
    pd.DataFrame([dict(stadium_id=k, grid_lat=v[0], grid_lon=v[1], grid_elevation_m=v[2]) for k, v in sorted(grid.items())]
                 ).to_csv(lib.HERE / "stadium_grid.csv", index=False)
    allg[["game_id", "season", "sid", "roof", "kickoff_utc", "excluded_reason"]].assign(
        in_open_air_set=allg.game_id.isin(oa.game_id)).to_csv(lib.HERE / "game_universe.csv", index=False)
    print(f"wrote wind_forecasts.parquet rows={len(df)} forecast_ok={int(df.forecast_ok.sum())} "
          f"requests={om.n_requests} cached={om.n_cached}")


if __name__ == "__main__":
    main()
