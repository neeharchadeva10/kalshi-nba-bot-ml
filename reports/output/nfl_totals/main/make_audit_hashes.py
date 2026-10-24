"""PRINTS (does NOT write) the JSON the Opus leak audit may save as reports/output/nfl_totals/AUDIT_OK:
{"sha256": {relative_path: sha256}} for every file run_test.py depends on (nfl_lib.AUDIT_FILES, relative to main/).
Fails loudly if any audited file is missing (e.g. the Kalshi / weather downloads are not there yet)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nfl_lib as N  # noqa: E402

if __name__ == "__main__":
    print(json.dumps(N.audit_hashes(), indent=1))
