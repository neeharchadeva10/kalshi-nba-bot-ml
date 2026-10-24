"""PRINTS (does NOT write) the JSON the Opus leak audit may save as reports/output/nba_refined_totals/AUDIT_OK:
{"sha256": {relative_path: sha256}} for every file run_test.py depends on (rt_lib.AUDIT_FILES, relative to main/)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rt_lib as R  # noqa: E402

if __name__ == "__main__":
    print(json.dumps(R.audit_hashes(), indent=1))
