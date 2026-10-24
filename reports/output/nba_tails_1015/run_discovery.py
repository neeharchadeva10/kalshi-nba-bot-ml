"""Research log #55: DISCOVERY run (ET 2025-10-21..2026-01-22). IN-SAMPLE, REFERENCE ONLY (the rule was written after
seeing #52's discovery calibration table). Never touches unseen dates."""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tails1015_lib as L


def main():
    recs, games, skips = L.T52.build_records(L.DISC_FIRST, L.DISC_LAST)
    L.assert_window(recs, L.DISC_FIRST, L.DISC_LAST)
    res, rows, bets = L.analyse(recs, skips)
    res = {"label": "in-sample, reference only", "split": "discovery", "dates": [L.DISC_FIRST, L.DISC_LAST], **res}
    pd.DataFrame(rows).to_csv(L.HERE / "records_discovery.csv", index=False)
    (L.HERE / "results_discovery.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("label", "pooled", "by_checkpoint_tail", "bet_skip_reasons", "scalar_results_skipped",
                                          "game_level_skips", "game_checkpoint_records")}, indent=1, default=str))


if __name__ == "__main__":
    main()
