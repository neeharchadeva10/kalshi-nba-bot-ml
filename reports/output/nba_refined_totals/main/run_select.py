"""Research log #65: SELECTION on the discovery windows only (D-A: 2022-23/2023-24, F42 2023-24 only per amendment 1; D-B: 2021-22..2023-24).
Loads ONLY discovery seasons (holdout rows never enter memory).  Writes d_versions.csv, results_select.json, top20 CSVs in the study dir.
Refuses to run twice or after AUDIT_OK / results_test.json exist.  Run once, by Opus."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rt_lib as R  # noqa: E402


def _default(o):
    return o.item() if hasattr(o, "item") else str(o)


def main():
    for f in ("results_select.json", "AUDIT_OK", "results_test.json", "test_started.json"):
        if (R.STUDY / f).exists():
            raise SystemExit(f"REFUSED: {f} exists; selection runs once, before the audit.")
    ev = R.load_tables(seasons=R.D_B)                       # discovery seasons only
    assert set(ev.season) <= set(R.D_B)
    ids = R.load_version_ids()
    assert set(ids) == set(R.all_versions()) and len(ids) == 208
    rows = pd.DataFrame([R.discovery_row(ev, v) for v in ids])
    rows["part"] = [v[0] for v in ids]
    rows.to_csv(R.STUDY / "d_versions.csv", index=False)
    res = {"live_rule": rows[rows.version == R.LIVE_RULE].iloc[0].to_dict(), "parts": {}}
    for part, floor in (("A", R.MIN_BETS_A), ("B", R.MIN_BETS_B)):
        sub = rows[rows.part == part].reset_index(drop=True)
        best = R.choose_best(sub, floor)
        el = sub[sub.n >= floor].sort_values(["mean_pnl", "n"], ascending=[False, False], kind="stable")
        el.head(20).to_csv(R.STUDY / f"top20_{part}.csv", index=False)
        blk = {"min_bets": floor, "n_eligible": int(len(el)), "chosen": None if best is None else best.to_dict(), "stop": True}
        if best is not None:
            blk["stop"] = bool(not best.mean_pnl > 0)              # STOP rule: D mean P&L <= 0
        res["parts"][part] = blk
    (R.STUDY / "results_select.json").write_text(json.dumps(res, indent=1, default=_default))
    print(json.dumps({p: {"chosen": b["chosen"] and b["chosen"]["version"], "stop": b["stop"]} for p, b in res["parts"].items()}))


if __name__ == "__main__":
    main()
