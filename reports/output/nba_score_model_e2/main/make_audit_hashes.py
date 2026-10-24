"""PRINTS (does NOT write) the JSON the Opus leak audit may save as reports/output/nba_score_model_e2/AUDIT_OK:
{"sha256": {relative_path: sha256}} for every file run_test.py depends on (e2_lib.AUDIT_FILES, relative to main/), including
frozen_e2.json, injury_features.parquet, E1's frozen.json and code, the injury inputs and the box-score files."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2_lib as E  # noqa: E402

if __name__ == "__main__":
    print(json.dumps(E.audit_hashes(), indent=1))
