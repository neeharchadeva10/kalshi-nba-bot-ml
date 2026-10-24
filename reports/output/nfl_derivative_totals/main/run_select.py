"""Research log #68: SELECTION on the selection window (games with ET gameday before 2026-01-01).  DO NOT RUN before the independent check.
Outcomes are attached to selection-window rows only (ldr_lib.attach_actual refuses anything else).  Writes ../results_select.json.
REFUSES if ../results_select.json (or AUDIT_OK / test_started.json / results_test.json) already exists: selection runs once."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ldr_lib as L  # noqa: E402


def _default(o):
    return o.item() if hasattr(o, "item") else str(o)


def refuse_if_done(study=L.STUDY):
    for f in ("results_select.json", "AUDIT_OK", "results_test.json", "test_started.json"):
        if (Path(study) / f).exists():
            raise SystemExit(f"REFUSED: {f} exists; selection runs once, before the audit.")


def select(dec_sel: pd.DataFrame) -> dict:
    """dec_sel: selection-window decision rows WITH `actual`.  Returns the results dict (12 version rows, chosen, stop)."""
    rows = pd.DataFrame([L.version_row(dec_sel, v) for v in L.all_versions()])
    best = L.choose(rows)
    return {"min_bets": L.MIN_BETS, "rows": rows.to_dict(orient="records"), "chosen": None if best is None else best.to_dict(),
            "stop": True if best is None else bool(not best.mean_pnl > 0)}


def main():
    refuse_if_done()
    games = L.N.load_games()
    r1 = L.load_r1()
    tabs, _ = L.price_tables(games, r1)
    dec = L.window_rows(tabs["dec"], "select")
    act = L.settle_actuals(games, dec.game_id.unique())
    dec = L.attach_actual(dec, act, "select")
    res = select(dec)
    (L.STUDY / "results_select.json").write_text(json.dumps(res, indent=1, default=_default))
    print(json.dumps({"chosen": res["chosen"], "stop": res["stop"], "rows": res["rows"]}, indent=1, default=_default))


if __name__ == "__main__":
    main()
