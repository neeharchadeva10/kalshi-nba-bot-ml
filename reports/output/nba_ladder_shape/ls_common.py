"""Research log #61 shared constants and helpers (read-only study, no network).
implied_total is loaded from #44's common.py and fee_taker from #46's common.py (by file path, under private module names, so
neither study's `common` / `pipeline` module name can shadow the other; sys.path is restored after loading)."""
from __future__ import annotations

import hashlib
import importlib.util
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
INJ = ROOT / "reports" / "output" / "nba_injury_speed"
ING = ROOT / "reports" / "output" / "nba_ingame_overreaction"
HOURLY = ROOT / "reports" / "output" / "nba_totals_pace"
PBP = ROOT / "data" / "raw" / "nba_open" / "play_by_play_2026.parquet"


def _load(name: str, path: Path):
    saved = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved
    return mod


_inj = _load("ls_dep_injury_common", INJ / "common.py")
_ing = _load("ls_dep_ingame_common", ING / "common.py")
implied_total = _inj.implied_total      # #44: strike-interpolated YES-mid 0.50 crossing (None when no crossing)
fee_taker = _ing.fee_taker              # 0.07*C*P*(1-P) rounded UP to $0.0001

SEED = 20261023
DISC_FIRST, DISC_LAST = "2025-10-21", "2026-01-22"      # ET game dates, inclusive
UNSEEN_FIRST, UNSEEN_LAST = "2026-01-23", "2026-04-12"
D_BEFORE_T_S = 3600          # decision time D = T - 60 min
MAX_AGE_S = 1800             # quote may be at most 30 min old at D
LADDER_SPREAD = 0.15         # ladder strike filter: ask - bid <= 0.15
BET_SPREAD = 0.10            # chosen bet strike: ask - bid <= 0.10
SLIP = 0.01
SKIP_AT = 0.99               # skip an entry (price incl. slippage) >= 0.99
FIT_LO, FIT_HI = 0.02, 0.98  # mids strictly inside are used for the normal fit
FIT_MIN_STRIKES = 4
MIN_N = 60
QUOTE_TOL = 0.03             # T-1h 1-minute vs hourly mid disagreement that excludes a game
BANDS = {"6-10": (6.0, 10.0, 8.0), "10-16": (10.0, 16.0, 13.0)}   # name -> (lo, hi, centre)
TAILS = ("high", "low")
DIRECTIONS = ("buy", "sell")
BUCKETS = [(-15, -12), (-12, -9), (-9, -6), (-6, -3), (-3, 0), (0, 3), (3, 6), (6, 9), (9, 12), (12, 15)]
LUCK_REPS = 500
COIN_REPS = 500
BOOT_DRAWS = 10_000


def grid():
    return [(b, t, d) for b in BANDS for t in TAILS for d in DIRECTIONS]


def lb_of(vals) -> float:
    """mean - 1.96*sd/sqrt(n) (sample sd); nan when n < 2."""
    import statistics
    n = len(vals)
    if n < 2:
        return float("nan")
    return statistics.fmean(vals) - 1.96 * statistics.stdev(vals) / math.sqrt(n)


# ------------------------------------------------------------------ audit hashes
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def required_paths(root: Path = ROOT) -> list[Path]:
    """Every file the unseen run depends on: code (this study + the two reused #44/#46 helpers), the two sqlite inputs,
    and the other inputs it reads (events list, hoopR play-by-play for finals, the discovery result it is gated on)."""
    h = root / "reports" / "output" / "nba_ladder_shape"
    return [h / "ls_common.py", h / "ls_pipeline.py", h / "run_discovery.py", h / "run_unseen.py", h / "checks.py",
            h / "test_ladder_shape.py", h / "results_discovery.json",
            root / "reports" / "output" / "nba_injury_speed" / "common.py",
            root / "reports" / "output" / "nba_ingame_overreaction" / "common.py",
            root / "reports" / "output" / "nba_injury_speed" / "candles.sqlite",
            root / "reports" / "output" / "nba_totals_pace" / "totals.sqlite",
            root / "reports" / "output" / "nba_injury_speed" / "events_list.csv",
            root / "data" / "raw" / "nba_open" / "play_by_play_2026.parquet"]


def rel(p: Path, root: Path = ROOT) -> str:
    return p.resolve().relative_to(root.resolve()).as_posix()
