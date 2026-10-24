"""Research log #68: the HOLDOUT (ET gameday on or after 2026-01-01).  DO NOT RUN without the Opus audit.
REFUSES (before any outcome is loaded) unless ../AUDIT_OK holds matching SHA-256 hashes for every file in ldr_lib.AUDIT_FILES AND
../test_started.json exists, ../results_select.json exists and ../results_test.json does not (runs once).
If the selection said STOP (no version with >= 60 bets, or the chosen mean net <= 0) no holdout is evaluated."""
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


def check_gates(study=L.STUDY, base=HERE, files=L.AUDIT_FILES) -> dict:
    L.check_audit_ok(study, base, files)
    sel = Path(study) / "results_select.json"
    if not sel.exists():
        raise SystemExit("REFUSED: results_select.json not found (run run_select.py first).")
    return json.loads(sel.read_text())


def run_stage(dec_test: pd.DataFrame, strikes_test: pd.DataFrame, act: pd.DataFrame, sel: dict) -> dict:
    """The holdout computation on already-loaded test-window inputs (unit tests run it on synthetic data)."""
    if sel.get("chosen") is None or sel.get("stop"):
        return {"status": "STOPPED at selection (no eligible version or chosen mean net <= 0); holdout not run", "chosen": sel.get("chosen")}
    vid = sel["chosen"]["version"]
    dec = L.attach_actual(dec_test, act, "test")
    sn = L.strike_nets(strikes_test, act)
    return {"status": "run", "chosen": sel["chosen"], "result": L.evaluate(dec, sn, vid)}


def main():
    sel = check_gates()                                          # refuses BEFORE any outcome is loaded
    games = L.N.load_games()
    r1 = L.load_r1()
    tabs, _ = L.price_tables(games, r1)
    dec = L.window_rows(tabs["dec"], "test")
    strikes = tabs["strikes"]
    g = games.drop_duplicates("game_id").set_index("game_id")
    strikes = strikes[[L.window_of_day(g.et_date.get(x)) == "test" for x in strikes.game_id.values]]
    act = L.settle_actuals(games, set(dec.game_id) | set(strikes.game_id))
    out = run_stage(dec, strikes, act, sel)
    (L.STUDY / "results_test.json").write_text(json.dumps(out, indent=1, default=_default))
    print(json.dumps(out, indent=1, default=_default))


if __name__ == "__main__":
    main()
