"""nflverse schedule data via nflreadpy (successor to nfl_data_py), cached to parquet."""
from __future__ import annotations

import pandas as pd

from kpm.core import config


def load_schedule(season: int, refresh: bool = False) -> pd.DataFrame:
    cache = config.path("raw_cache_dir") / "nflverse"
    cache.mkdir(parents=True, exist_ok=True)
    fp = cache / f"schedules_{season}.parquet"
    if fp.exists() and not refresh:
        return pd.read_parquet(fp)
    import nflreadpy as nfl  # imported lazily: it downloads on first use

    df = nfl.load_schedules(seasons=[season]).to_pandas()
    df.to_parquet(fp, index=False)
    return df
