"""PRINTS the JSON that AUDIT_OK must contain ({"sha256": {relative_path: hash}}).  Does NOT create AUDIT_OK (Opus does)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fa_lib as fa  # noqa: E402

if __name__ == "__main__":
    print(json.dumps(fa.audit_hashes(), indent=1))
