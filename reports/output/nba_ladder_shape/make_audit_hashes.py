"""PRINTS the sha256 of every file run_unseen.py depends on, as the JSON an AUDIT_OK file must contain. Writes nothing.
The Opus audit decides whether to create AUDIT_OK from this output; the builder never does."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ls_common as C

if __name__ == "__main__":
    out = {}
    for p in C.required_paths():
        out[C.rel(p)] = C.sha256_file(p) if p.exists() else "MISSING"
    print(json.dumps({"sha256": out}, indent=1))
