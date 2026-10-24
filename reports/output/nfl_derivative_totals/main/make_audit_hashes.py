"""PRINTS (does NOT write) the JSON the Opus leak audit may save as reports/output/nfl_derivative_totals/AUDIT_OK:
{"sha256": {relative_path: sha256}} for every file run_test.py depends on (ldr_lib.AUDIT_FILES, relative to main/).
Fails loudly if any audited file is missing (Kalshi download, agree_snapshots.csv, results_select.json ...)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ldr_lib as L  # noqa: E402

if __name__ == "__main__":
    print(json.dumps(L.audit_hashes(), indent=1))
