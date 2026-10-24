import pandas as pd, numpy as np
from pathlib import Path
R = Path(r"%USERPROFILE%\Documents\Kalshi-PM")
B = R / "reports/output/nfl_totals"
g = pd.read_csv(R / "data/raw/nflverse/games/games.csv")[["game_id", "roof", "stadium_id"]]
st = pd.read_csv(B / "weather/stadiums.csv")[["stadium_id", "roof_category"]]
w = pd.read_parquet(B / "weather/wind_forecasts.parquet")[["game_id", "fc_wind_mph", "fc_variant", "fc_issue_guarantee_h", "forecast_ok"]]
s = pd.read_csv(B / "sample_weather_games.csv")
d = s.merge(g, on="game_id", how="left").merge(st, on="stadium_id", how="left").merge(w, on="game_id", how="left")
d["open_air"] = (d.roof_category == "open-air") & d.roof.isin(["outdoors", "open"])
# own forecast_ok: value present, previous_day1 variant, guarantee >= 24h
d["forecast_ok_own"] = d.fc_wind_mph.notna() & (d.fc_variant == "previous_day1") & (d.fc_issue_guarantee_h >= 24)
d["forecast_ok"] = d.forecast_ok_own
d["b2_flag"] = d.open_air & d.forecast_ok & (d.fc_wind_mph >= 10) & (d.fc_wind_mph < 20)
d["b3_flag"] = d.open_air & d.forecast_ok & (d.fc_wind_mph >= 20)
d[["game_id", "open_air", "fc_wind_mph", "forecast_ok", "b2_flag", "b3_flag"]].to_csv(B / "check/out/check_b2_join.csv", index=False)
print(len(d), "in-parquet", int(d.fc_variant.notna().sum()),
      "open_air", int(d.open_air.sum()), "ok", int(d.forecast_ok.sum()), "b2", int(d.b2_flag.sum()), "b3", int(d.b3_flag.sum()))
w2 = s.merge(w, on="game_id", how="left")
print("own vs parquet forecast_ok mismatches:", int((d.forecast_ok_own.values != w2.forecast_ok.fillna(False).values).sum()))
