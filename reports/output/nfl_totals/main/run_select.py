"""Research log #66: Part A SELECTION on the discovery window D (2006-2014) only.  DO NOT RUN before the agreement check (Opus runs it).
Loads outcomes for D seasons only (nfl_lib.attach_outcomes refuses any other season).  Writes ../d_versions.csv (every version's D row),
../top20_A.csv, ../results_select.json (the chosen version and the STOP flag).  Refuses to run twice or once AUDIT_OK exists."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nfl_lib as N  # noqa: E402
import part_a as A  # noqa: E402


def _default(o):
    return o.item() if hasattr(o, "item") else str(o)


def select(frame_d: pd.DataFrame, ids: list) -> tuple:
    """-> (rows DataFrame in enumeration order, results dict).  frame_d: D rows with `total`."""
    rows = pd.DataFrame([A.version_row(frame_d, v) for v in ids])
    best = N.choose_best(rows, N.MIN_BETS_A)
    el = rows[rows.n >= N.MIN_BETS_A].sort_values(["mean_pnl", "n"], ascending=[False, False], kind="stable")
    res = {"min_bets": N.MIN_BETS_A, "n_eligible": int(len(el)), "chosen": None if best is None else best.to_dict(),
           "stop": True if best is None else bool(not best.mean_pnl > 0)}
    return rows, el, res


def main():
    for f in ("results_select.json", "AUDIT_OK", "results_test.json", "test_started.json"):
        if (N.STUDY / f).exists():
            raise SystemExit(f"REFUSED: {f} exists; selection runs once, before the audit.")
    g = N.load_games()
    gt = pd.read_parquet(N.MAIN_OUT / "game_table.parquet")
    gt = gt[gt.window == "D"]                                   # holdout rows never get outcomes
    assert set(gt.season.unique()) <= set(N.D_SEASONS)
    fd = N.attach_outcomes(gt, g, N.D_SEASONS)
    ids = A.load_version_ids()
    assert ids == A.all_versions() and len(ids) == 56
    rows, el, res = select(fd, ids)
    rows.to_csv(N.STUDY / "d_versions.csv", index=False)
    el.head(20).to_csv(N.STUDY / "top20_A.csv", index=False)
    (N.STUDY / "results_select.json").write_text(json.dumps({"parts": {"A": res}}, indent=1, default=_default))
    print(json.dumps({"chosen": res["chosen"] and res["chosen"]["version"], "stop": res["stop"], "n_eligible": res["n_eligible"]}, default=_default))


if __name__ == "__main__":
    main()
