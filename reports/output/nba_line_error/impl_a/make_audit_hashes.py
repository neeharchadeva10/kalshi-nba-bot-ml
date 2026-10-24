"""Prints (does NOT write) the JSON the Opus leak audit may save as reports/output/nba_line_error/AUDIT_OK:
{"sha256": {relative_path: sha256}} for every code file run_test.py depends on (lea_lib.AUDIT_FILES, paths relative to impl_a/)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lea_lib as L  # noqa: E402

if __name__ == "__main__":
    print(json.dumps(L.audit_hashes(), indent=1))
