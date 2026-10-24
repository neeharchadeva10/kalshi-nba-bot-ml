"""Research log #65: the HOLDOUT stage (H1 2020-21 [B], H2-sb 2024-25 + 2025-26, H2-K Kalshi 2025-26).  DO NOT RUN without the Opus audit.
REFUSES (before any outcome is loaded) unless AUDIT_OK holds matching SHA-256 hashes for every file in rt_lib.AUDIT_FILES (code + inputs),
results_select.json exists, and test_started.json / results_test.json do not exist.  test_started.json is written FIRST, then outcomes load.
A part whose chosen version has D mean P&L <= 0 STOPS (no holdout run).  Runs once."""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rt_lib as R  # noqa: E402


def _default(o):
    return o.item() if hasattr(o, "item") else str(o)


def check_gates(study=R.STUDY, base=HERE, files=R.AUDIT_FILES):
    study = Path(study)
    R.check_audit_ok(study, base, files)
    if not (study / "results_select.json").exists():
        raise SystemExit("REFUSED: results_select.json not found (run run_select.py first).")
    for f in ("test_started.json", "results_test.json"):
        if (study / f).exists():
            raise SystemExit(f"REFUSED: {f} exists; the holdout stage runs once.")
    return json.loads((study / "results_select.json").read_text())


def main():
    sel = check_gates()
    (R.STUDY / "test_started.json").write_text(json.dumps({"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}))
    ev = R.load_tables()                                    # holdout outcomes are loaded only now
    out = {"live_rule_row": sel["live_rule"], "parts": {}}
    live = R.evaluate_version(ev, R.LIVE_RULE, R.HOLDOUT_WINDOWS["A"])
    out["live_rule_holdout"] = {"result": live, "verdict_if_it_were_chosen": R.verdict(live)}
    for part in ("A", "B"):
        blk = sel["parts"][part]
        if blk["stop"] or blk["chosen"] is None:
            out["parts"][part] = {"status": "STOPPED (no eligible version or chosen D mean P&L <= 0); no holdout run", "chosen": blk["chosen"]}
            continue
        res = R.evaluate_version(ev, blk["chosen"]["version"], R.HOLDOUT_WINDOWS[part])
        out["parts"][part] = {"chosen": blk["chosen"], "result": res, "verdict": R.verdict(res)}
    (R.STUDY / "results_test.json").write_text(json.dumps(out, indent=1, default=_default))
    print(json.dumps({p: b.get("verdict", b.get("status")) for p, b in out["parts"].items()}, indent=1, default=_default))


if __name__ == "__main__":
    main()
