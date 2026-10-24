"""PRINTS (does NOT write) the JSON the Opus leak audit may save as reports/output/nba_score_model/AUDIT_OK:
{"sha256": {relative_path: sha256}} for every code file run_test.py depends on (sm_lib.AUDIT_FILES, relative to main/), including frozen.json."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sm_lib as S  # noqa: E402

if __name__ == "__main__":
    print(json.dumps(S.audit_hashes(), indent=1))
