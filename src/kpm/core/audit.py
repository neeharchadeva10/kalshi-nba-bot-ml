"""Audit trail: every decision goes to the audit_log table and a JSONL file."""
from __future__ import annotations

import json
import sqlite3

from kpm.core import config
from kpm.core.timeutil import now_ts, to_iso


def log(conn: sqlite3.Connection, component: str, action: str, entity_type: str | None = None,
        entity_id: str | int | None = None, details: dict | None = None) -> None:
    ts = now_ts()
    payload = json.dumps(details or {}, default=str, sort_keys=True)
    conn.execute(
        "INSERT INTO audit_log (ts, component, entity_type, entity_id, action, details) VALUES (?,?,?,?,?,?)",
        (ts, component, entity_type, None if entity_id is None else str(entity_id), action, payload),
    )
    logfile = config.path("logs_dir") / f"{component.split('.')[0]}.jsonl"
    with open(logfile, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": to_iso(ts), "component": component, "action": action,
                             "entity_type": entity_type, "entity_id": entity_id,
                             "details": details or {}}, default=str) + "\n")
