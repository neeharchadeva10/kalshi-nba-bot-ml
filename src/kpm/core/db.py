"""SQLite access shared by the historical pipeline and later live components."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterable, Iterator

from kpm.core import config


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else config.path("db_path")
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=300)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    sql = resources.files("kpm.core").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(sql)
    _migrate_markets_platforms(conn, sql)
    conn.commit()


def _migrate_markets_platforms(conn: sqlite3.Connection, schema_sql: str) -> None:
    """Rebuild `markets` if its CHECK constraint predates the 'polymarket_us' platform.

    SQLite cannot alter a CHECK constraint in place. legacy_alter_table keeps other tables'
    foreign keys pointing at the name `markets`, so they bind to the rebuilt table.
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='markets'").fetchone()
    if row is None or "polymarket_us" in row[0]:
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE markets RENAME TO markets_old")
        conn.execute("DROP INDEX IF EXISTS ix_markets_game")
        conn.commit()
        conn.executescript(schema_sql)  # creates the new markets table and its index
        conn.execute("INSERT INTO markets SELECT * FROM markets_old")
        conn.execute("DROP TABLE markets_old")
        conn.commit()
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise RuntimeError(f"foreign key check failed after markets migration: {bad[:5]}")


@contextmanager
def session(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        init_schema(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert(conn: sqlite3.Connection, table: str, row: dict, conflict: Iterable[str]) -> None:
    cols = list(row)
    conflict = list(conflict)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in conflict)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT ({', '.join(conflict)}) DO "
        + (f"UPDATE SET {updates}" if updates else "NOTHING")
    )
    conn.execute(sql, [row[c] for c in cols])


def insert_many(conn: sqlite3.Connection, table: str, rows: list[dict], replace: bool = True) -> int:
    if not rows:
        return 0
    cols = list(rows[0])
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    conn.executemany(
        f"{verb} INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        [[r[c] for c in cols] for r in rows],
    )
    return len(rows)
