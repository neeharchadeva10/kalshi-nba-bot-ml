"""Research log #57, implementation A: VALIDATION (2019-20 .. 2021-22 regular season) of the frozen version only.

REFUSES unless frozen.json's SHA-256 equals frozen.sha256 and results_select.json says the selection did not STOP.  Runs once.
Rule: side rate (pushes excluded) > 110/210 with n >= 150, else STOP (the test is never run)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lea_lib as L  # noqa: E402
import run_select as rs  # noqa: E402

FLAGS = {"2020-21": "no / limited fans, 72-game season",
         "2021-22": "normal"}


def validate_core(frozen: dict, D) -> dict:
    """Pure: apply the frozen rule to validation rows D (f1..f10, y, season) and decide continue / STOP."""
    side = L.apply_rule(frozen, D)
    y = D.y.values.astype(float)
    pooled = L.side_stats(side, y)
    per = {s: {**L.side_stats(side[D.season.values == s], y[D.season.values == s]), "flag": FLAGS.get(s, "")} for s in sorted(set(D.season))}
    ok = bool(pooled["n"] >= L.MIN_N_VALID and np.isfinite(pooled["rate"]) and pooled["rate"] > L.BREAK_EVEN)
    reason = None if ok else (f"n = {pooled['n']} < {L.MIN_N_VALID}" if pooled["n"] < L.MIN_N_VALID
                              else f"side rate {pooled['rate']:.4f} <= {L.BREAK_EVEN:.4f}")
    return {"version": frozen["version"], "pooled": pooled, "per_season": per, "continue": ok, "stop_reason": reason,
            "pass_bar": {"side_rate_gt": L.BREAK_EVEN, "min_n": L.MIN_N_VALID}}


def main():
    study = L.STUDY
    frozen = L.check_frozen_hash(study)
    sel = L.read_json(study / "results_select.json")
    if sel.get("stop"):
        raise SystemExit("REFUSED: selection STOPPED; there is nothing to validate.")
    if (study / "results_validate.json").exists():
        raise SystemExit("REFUSED: results_validate.json exists; validation runs once.")
    D = rs.load_stage(L.VALIDATION)
    D = D[D.line_source == L.CLOSING_SRC].reset_index(drop=True)          # odds-API consensus close only (amendment)
    res = validate_core(frozen, D)
    res["rows"] = int(len(D))
    (study / "results_validate.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("version", "pooled", "continue", "stop_reason")}, indent=1, default=str))


if __name__ == "__main__":
    main()
