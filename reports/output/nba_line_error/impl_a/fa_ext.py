"""Research log #57, implementation A: #56's season-generalised grid extended back to 2008-09 (prior 2007-08).

Nothing in #56 / #42 is modified.  Differences from fa_lib.build_world:
  1. team-code map: hoopR's old NJ (Nets) and SEA (SuperSonics) codes are mapped to BKN / OKC (franchise continuity) by adding
     two keys to the in-process #37 `lib.TEAM_MAP` dict (no existing key changes, so 2022-23..2025-26 are unaffected).
  2. constants for season S are fitted on S-1 with S-2 as ITS prior whenever S-2's box score is on disk (file year >= 2008);
     for S = 2008-09 (fit season 2007-08, prior 2006-07 missing) the fit uses #56's no-prior branch (k = 0).
  3. the box-score reader is lea_lib.read_year_fixed: hoopR's 2007-08..2011-12 files have total_turnovers = 2 x turnovers (a
     duplicated team-turnover column), which #38's fix_tov would have taken as the turnover count; there turnovers is used.
     2012-13 onward the reader returns the file untouched, so #56's seasons are unaffected.
  4. S = 2022-23 keeps #56's own treatment (no-prior fit on 2021-22) so that 2022-23 .. 2025-26 reproduce #56 EXACTLY
     (legacy_2022 = True; 2020-21 box scores exist on disk now, but #56 did not have them).  repro_f8.py verifies this.
The projection is #56's frozen primary form: flat 7791 = spec (2,0,1,0,1,0,1,1) = w0.5 / own pace / decay5 / k0 / shift on / no rest /
split on / shrink on; frozen shift 0.0 ('team_raw')."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent.parent
for _p in (OUT / "nba_formula_accuracy", OUT / "nba_totals_grid"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import lea_lib as L  # noqa: E402
import fa_lib as fa  # noqa: E402
import grid_lib as gl  # noqa: E402
from grid_lib import lib, r2, tn  # noqa: E402

lib.TEAM_MAP.setdefault("NJ", "BKN")
lib.TEAM_MAP.setdefault("SEA", "OKC")

FLAT = 7791
SPEC = (2, 0, 1, 0, 1, 0, 1, 1)
SHIFT = 0.0
FIRST_BOX_YEAR = 2008
assert gl.flat_from_spec(SPEC) == FLAT


def fit_constants_ext(prev_year, ff_coef, reader=None, legacy_2022=True):
    """Constants fitted on regular season `prev_year`.  Returns (consts, note)."""
    reader = reader or L.read_year_fixed
    prior_ok = (prev_year - 1 >= FIRST_BOX_YEAR) and not (legacy_2022 and prev_year == 2022)
    if not prior_ok:
        return fa.fit_constants_for(prev_year, reader, ff_coef)          # #56 no-prior branch (k = 0)
    cur = fa.team_games_of(prev_year, r2.CUR_SEASON_TYPES, reader)
    games_fit = tn.fit_games_2425(cur)
    pp = fa.team_games_of(prev_year - 1, (2,), reader)
    season_fit = gl.Season(pp, cur, ff_coef)
    consts = gl.fit_constants(season_fit, games_fit)
    return consts, f"fitted on {fa.label_of(prev_year)} (k = {gl.K_FIT}, prior {fa.label_of(prev_year - 1)})"


def build_world_ext(season, reader=None, legacy_2022=True):
    """As fa.build_world: prior = S-1 regular season, current-season rows only if dated before the game's ET date,
    four-factor coefficients and every constant from S-1 only."""
    reader = reader or L.read_year_fixed          # same TOV rule as lea_lib (hoopR 2007-08..2011-12 total_turnovers is doubled)
    y = fa.year_of(season)
    w = fa.World()
    w.season_label = season
    w.prev_tg = fa.team_games_of(y - 1, (2,), reader)
    w.cur_tg = fa.team_games_of(y, r2.CUR_SEASON_TYPES, reader)
    w.ff_coef, w.ff_r2 = gl.fit_four_factor(w.prev_tg)
    w.season = gl.Season(w.prev_tg, w.cur_tg, w.ff_coef)
    w.consts, w.const_note = fit_constants_ext(y - 1, w.ff_coef, reader, legacy_2022)
    return w


def project_season(season, game_ids=None, reader=None, legacy_2022=True):
    """Projection (flat 7791, shift 0) for the season's regular-season games (or just `game_ids`).
    Returns a DataFrame game_id, proj, plus the world's constants note."""
    w = build_world_ext(season, reader, legacy_2022)
    games = fa.games_frame(w)
    if game_ids is not None:
        games = games[games.game_id.isin(set(int(g) for g in game_ids))].reset_index(drop=True)
    proj = gl.project_one(w.season, games, w.consts, SPEC) + SHIFT
    import pandas as pd
    return pd.DataFrame({"game_id": games.game_id.astype("int64").values, "proj": proj}), w.const_note
