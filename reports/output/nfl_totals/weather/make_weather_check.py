"""Weather-vs-weather checks -> WEATHER_CHECK.md. Reads only wind_forecasts.parquet (weather columns) and game_universe.csv.
No scores, totals or lines are touched."""
import numpy as np
import pandas as pd
import lib

H = lib.HERE


def corr_table(df, a, b):
    d = df[[a, b]].dropna()
    if len(d) < 5:
        return dict(n=len(d))
    diff = d[a] - d[b]
    return dict(n=len(d), corr=round(d[a].corr(d[b]), 3), mean_diff=round(diff.mean(), 2),
                mae=round(diff.abs().mean(), 2), rmse=round(float(np.sqrt((diff ** 2).mean())), 2))


def md(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        lines.append("| " + " | ".join("" if (isinstance(v, float) and np.isnan(v)) else str(v) for v in r) + " |")
    return "\n".join(lines)


def main():
    df = pd.read_parquet(H / "wind_forecasts.parquet")
    uni = pd.read_csv(H / "game_universe.csv")
    out = ["# WEATHER_CHECK (weather vs weather only; no betting outcomes touched)", ""]
    out += [f"Generated from wind_forecasts.parquet ({len(df)} open-air games, seasons {df.season.min()}-{df.season.max()}).", ""]

    # coverage by season
    cov = df.groupby("season").agg(
        games=("game_id", "size"), forecast_ok=("forecast_ok", "sum"),
        fc_wind=("fc_wind_mph", "count"), fc_gust=("fc_gust_mph", "count"), fc_temp=("fc_temp_f", "count"),
        fc_precip=("fc_precip_mm", "count"), fc_wind_d2=("fc_wind_d2_mph", "count"), hf_wind=("hf_wind_mph", "count"),
        era5_wind=("era5_wind_mph", "count"), obs_wind=("obs_wind_mph", "count"), obs_temp=("obs_temp_f", "count")).reset_index()
    out += ["## Coverage by season (non-null counts of open-air games)", "", md(cov), ""]

    # universe accounting
    u = uni[uni.season.between(2018, 2026)]
    out += ["## Game universe 2018-2026", "",
            f"- all games: {len(u)}; in the open-air set: {int(u.in_open_air_set.sum())}",
            "- excluded: " + ", ".join(f"{k}={v}" for k, v in u[~u.in_open_air_set].excluded_reason.replace("", "no kickoff time").value_counts().items()),
            ""]
    fut = df[df.kickoff_utc > pd.Timestamp.now(tz="UTC")]
    out += [f"- open-air games with kickoff still in the future (no weather yet): {len(fut)}", ""]

    # comparisons
    rows = []
    for name, a, b in [("fc (prev day1) vs ERA5", "fc_wind_mph", "era5_wind_mph"),
                       ("fc (prev day1) vs games.csv obs", "fc_wind_mph", "obs_wind_mph"),
                       ("fc day2 vs ERA5", "fc_wind_d2_mph", "era5_wind_mph"),
                       ("HF short-lead vs ERA5", "hf_wind_mph", "era5_wind_mph"),
                       ("HF short-lead vs games.csv obs", "hf_wind_mph", "obs_wind_mph"),
                       ("ERA5 vs games.csv obs", "era5_wind_mph", "obs_wind_mph"),
                       ("fc day1 vs HF short-lead", "fc_wind_mph", "hf_wind_mph")]:
        rows.append(dict(pair=name + " (a - b)", **corr_table(df, a, b)))
    out += ["## Wind: correlation and mean difference (mph; a - b)", "", md(pd.DataFrame(rows)), ""]
    rows = []
    for yrs, lab in [((2024, 2026), "2024-2026"), ((2018, 2023), "2018-2023")]:
        s = df[df.season.between(*yrs)]
        for name, a, b in [("ERA5 vs obs", "era5_wind_mph", "obs_wind_mph"), ("HF vs obs", "hf_wind_mph", "obs_wind_mph"),
                           ("fc day1 vs obs", "fc_wind_mph", "obs_wind_mph")]:
            rows.append(dict(seasons=lab, pair=name, **corr_table(s, a, b)))
    out += ["### Same comparisons by period", "", md(pd.DataFrame(rows)), ""]
    rows = []
    for name, a, b in [("fc day1 temp vs ERA5 temp", "fc_temp_f", "era5_temp_f"), ("fc day1 temp vs games.csv temp", "fc_temp_f", "obs_temp_f"),
                       ("ERA5 temp vs games.csv temp", "era5_temp_f", "obs_temp_f"), ("HF temp vs games.csv temp", "hf_temp_f", "obs_temp_f")]:
        rows.append(dict(pair=name + " (a - b, F)", **corr_table(df, a, b)))
    out += ["## Temperature: correlation and mean difference", "", md(pd.DataFrame(rows)), ""]

    # unit sanity
    o = df.obs_wind_mph.dropna()
    ratio = (df.era5_wind_mph / df.obs_wind_mph).replace([np.inf, -np.inf], np.nan).dropna()
    out += ["## Unit sanity (games.csv wind)", "",
            f"- games.csv wind: n={len(o)}, mean {o.mean():.2f}, median {o.median():.1f}, p95 {o.quantile(.95):.1f}, max {o.max():.0f}",
            f"- ERA5 10 m wind (mph, requested wind_speed_unit=mph): mean {df.era5_wind_mph.mean():.2f}; "
            f"mean ratio ERA5/obs (obs>0) = {ratio.mean():.2f}, median {ratio.median():.2f}",
            "- If games.csv were km/h or knots the ERA5/obs ratio would be about 0.62 or 1.15 relative to a true mph match; "
            "the mean-difference table above is the evidence (ERA5 10 m is a grid-cell mean and usually reads a little "
            "different from a stadium anemometer).",
            "- games.csv wind is a whole number; ERA5/forecast values have 0.1 resolution.", ""]

    # per-stadium bias (gross coordinate / unit error detector)
    g = df.dropna(subset=["era5_wind_mph", "obs_wind_mph"]).groupby("stadium_id").apply(
        lambda d: pd.Series(dict(n=len(d), wind_corr=round(d.era5_wind_mph.corr(d.obs_wind_mph), 2),
                                 wind_bias_era5_minus_obs=round((d.era5_wind_mph - d.obs_wind_mph).mean(), 2),
                                 temp_bias_era5_minus_obs=round((d.era5_temp_f - d.obs_temp_f).mean(), 1))),
        include_groups=False).reset_index()
    out += ["## Per-stadium ERA5 vs games.csv (outliers would flag a wrong coordinate)", "", md(g), ""]

    # missing
    miss = {c: int(df[c].isna().sum()) for c in ["fc_wind_mph", "fc_gust_mph", "fc_temp_f", "fc_precip_mm", "hf_wind_mph",
                                                 "era5_wind_mph", "obs_wind_mph", "obs_temp_f"]}
    out += ["## Missing values (open-air games, all seasons)", "", "```", str(miss), "```", ""]
    m24 = df[df.season >= 2024]
    out += ["### Missing within 2024-2026, by column", "", "```",
            str({c: int(m24[c].isna().sum()) for c in miss}), "```", "",
            "### 2024-2026 games without a day1 forecast (kickoff past)", ""]
    nf = m24[(~m24.forecast_ok) & (m24.kickoff_utc < pd.Timestamp.now(tz="UTC"))][["game_id", "stadium_id", "kickoff_utc"]]
    out += [f"{len(nf)} games", "", md(nf.head(40)) if len(nf) else "none", ""]
    (H / "WEATHER_CHECK.md").write_text("\n".join(out), encoding="utf8")
    print("\n".join(out[:60]))


if __name__ == "__main__":
    main()
