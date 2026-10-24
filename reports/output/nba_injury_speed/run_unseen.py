"""Research log #44: UNSEEN run (ET game dates 2026-01-23 .. end of the regular season), ONCE, rule frozen after discovery.
REFUSES to run unless reports/output/nba_injury_speed/AUDIT_OK exists (created by the Opus audit, never by the builder)."""
import json
import sys

import common as C


def main():
    if not (C.HERE / "AUDIT_OK").exists():
        print("REFUSING: reports/output/nba_injury_speed/AUDIT_OK does not exist. The unseen half waits for the Opus audit.")
        sys.exit(2)
    out = C.HERE / "results_unseen.json"
    if out.exists():
        print("REFUSING: results_unseen.json already exists; the unseen half is run once.")
        sys.exit(3)
    import pipeline as PL
    rows, res = PL.analyze_split(C.UNSEEN_FIRST, "2026-04-13", "unseen")
    PL.write_rows(rows, C.HERE / "events_unseen.csv")
    out.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("pass_bar", "pass_bar_all", "trading", "kill_rule")}, indent=1, default=str))


if __name__ == "__main__":
    main()
