"""Refusal gates for run_unseen.py (testable with a temp directory)."""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent


def unseen_refusals(here: Path = HERE, status: dict | None = None):
    """-> list of reasons the unseen run must NOT start (empty list = allowed).
    * AUDIT_OK must exist (created only by the Opus audit);
    * results_unseen.json must not exist (the unseen set is used once);
    * threes_download_complete.flag must exist;
    * #48's PTS/REB/AST selection and the 3PT selection must be fully cached (status from data.download_status())."""
    why = []
    if not (here / "AUDIT_OK").exists():
        why.append("AUDIT_OK does not exist (the audit has not signed off)")
    if (here / "results_unseen.json").exists():
        why.append("results_unseen.json already exists (the unseen period is run once)")
    if not (here / "threes_download_complete.flag").exists():
        why.append("threes_download_complete.flag does not exist (3PT download not complete)")
    if status is not None:
        for k, v in status.items():
            if v["selected"] == 0 or v["missing"] > 0:
                why.append(f"download incomplete for {k}: selected={v['selected']} missing={v['missing']}")
    return why


def discovery_stop(here: Path = HERE):
    """-> reason string if results_discovery.json says STOP (or is absent / has no betting block), else None."""
    import json
    p = here / "results_discovery.json"
    if not p.exists():
        return "results_discovery.json missing: run run_discovery.py first"
    r = json.loads(p.read_text())
    if str(r.get("verdict", "")).startswith("STOP"):
        return "results_discovery.json verdict is STOP: " + r["verdict"]
    if "betting_discovery" not in r:
        return "results_discovery.json has no betting_discovery block"
    return None
