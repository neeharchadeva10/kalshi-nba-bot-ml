"""PRINTS (does NOT write) the JSON the Opus leak audit may save as reports/output/nba_score_model_e3/AUDIT_OK:
{"sha256": {relative_path: sha256}} for every file run_test.py depends on (e3_lib.AUDIT_FILES, relative to main/)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e3_lib as L  # noqa: E402

if __name__ == "__main__":
    print(json.dumps(L.audit_hashes(), indent=1))
