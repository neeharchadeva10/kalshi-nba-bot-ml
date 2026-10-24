"""Research log #55: UNSEEN run (ET 2026-01-23..2026-04-12), ONCE. REFUSES unless AUDIT_OK exists (created only by the
Opus audit, never by the builder) and refuses if results_unseen.json exists."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def refusal(here=HERE):
    """(exit_code, message) when the run must be refused, else None. Checked BEFORE any data is touched."""
    if not (here / "AUDIT_OK").exists():
        return 2, "REFUSING: AUDIT_OK does not exist. The unseen half waits for the Opus audit."
    if (here / "results_unseen.json").exists():
        return 3, "REFUSING: results_unseen.json already exists; the unseen half is run once."
    return None


def main():
    r = refusal()
    if r:
        print(r[1])
        sys.exit(r[0])
    sys.path.insert(0, str(HERE))
    import pandas as pd
    import tails1015_lib as L
    recs, games, skips = L.T52.build_records(L.UNSEEN_FIRST, L.UNSEEN_LAST)
    L.assert_window(recs, L.UNSEEN_FIRST, L.UNSEEN_LAST)
    res, rows, bets = L.analyse(recs, skips)
    pd.DataFrame(rows).to_csv(HERE / "records_unseen.csv", index=False)
    p = res["pooled"]
    share = L.coin_flip_share(bets, 500, L.SEED)
    bar = {"1_mean_pos_and_boot_lb_pos": bool(p["n"] and p["mean"] > 0 and p["bootstrap_lb95"] is not None and p["bootstrap_lb95"] > 0),
           "2_n_ge_100": p["n"] >= L.MIN_N,
           "3_coin_flip_share_le_5pct": bool(share is not None and share <= 0.05)}
    out = {"split": "unseen", "dates": [L.UNSEEN_FIRST, L.UNSEEN_LAST], "n": p["n"], "mean": p["mean"],
           "bootstrap_lb95": p["bootstrap_lb95"], "coin_flip_share_ge_real": share, "pass_bar": bar,
           "PASS": all(bar.values()), **res}
    (HERE / "results_unseen.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
