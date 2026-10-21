"""Research log #42 winner applied to a live night (for scripts/early_overs_picks.py, rule 'grid_winner').
READ-ONLY, no network.  Reuses grid_lib's exact rating code (CurTable / features / build_block); nothing is re-derived.

Frozen winner (winner_frozen.json): A = w0.0, B = term-own pace, C = decay5, k = 20, no shift, rest categories
0/1/2/3+ with the frozen e_cat, home/away split on, no shrink.  Prior = 2025-26 regular season, current = 2026-27
regular-season rows with ET date before the game's ET date D.
NOT YET TESTED OR WIRED INTO scripts/early_overs_picks.py (blocked by the permission system)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import grid_lib as gl  # noqa: E402

ROOT = HERE.parents[2]
FROZEN = HERE / "winner_frozen.json"
BOX = ROOT / "data" / "raw" / "nba_open"


def load_frozen(path=FROZEN):
    return json.loads(Path(path).read_text())


def rest_cat_from_schedule(sched: dict, team: str, day) -> int:
    """0/1/2/3+ from the team's schedule dates {team: sorted dates}; first game of the season = 3."""
    prev = [d for d in sched.get(team, []) if d < day]
    if not prev:
        return 3
    return int(min((day - prev[-1]).days - 1, 3))


def build_season(box27=None, box_dir=BOX):
    """Prior = 2025-26 regular season, current = 2026-27 regular season (maybe empty)."""
    prev = gl.team_games_g(pd.read_parquet(Path(box_dir) / "team_box_2026.parquet"), (2,))
    cur = prev.iloc[0:0].copy()
    if box27 is not None and Path(box27).exists():
        raw = pd.read_parquet(box27)
        raw = raw[raw.season_type == 2] if len(raw) else raw
        if len(raw):
            cur = gl.team_games_g(raw, (2,))
    return gl.Season(prev, cur, np.zeros(5))       # four-factor coefficients are unused by decay5


def project_winner_for_date(day, games, box27=None, sched=None, season=None, frozen=None, box_dir=BOX):
    """games = [(game_key, away, home, tip_et)] on ET date `day`.  Returns {game_key: projected total}.
    sched: {team: sorted dates} for rest days (else rest comes from the season's own box scores)."""
    fz = frozen or load_frozen()
    a, b, c, d, e, f, g, h = tuple(fz["spec"])
    season = season or build_season(box27, box_dir)
    pair = fz["constants_pair"]
    consts = gl.Consts(np.zeros((7, 10)), np.zeros((7, 10, 3)), np.zeros((7, 10)))
    consts.e_cat[a, c] = pair["e_cat"]
    consts.c_b2b[a, c], consts.b[a, c] = pair["c_b2b"], pair["b"]
    if not games:
        return {}
    dates = np.array([np.datetime64(day, "D")] * len(games), dtype="datetime64[D]")
    away = np.array([x[1] for x in games])
    home = np.array([x[2] for x in games])
    for t in list(away) + list(home):
        assert t in season.prev, f"no 2025-26 prior for {t}"
    tab = gl.CurTable.build(season, dates, away, home, gl.C_OPTS[c])
    if sched is None:
        rest = gl.rest_info(season, dates, away, home)
    else:
        rest = gl.Rest([rest_cat_from_schedule(sched, t, day) for t in away],
                       [rest_cat_from_schedule(sched, t, day) for t in home])
    blk = gl.build_block(tab, rest, consts, c, gl.D_OPTS[d], bool(e), bool(g), [a], [b])[0, 0, f, h]
    return {x[0]: float(v) for x, v in zip(games, blk)}
