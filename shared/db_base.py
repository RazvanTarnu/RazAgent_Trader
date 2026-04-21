# -*- coding: utf-8 -*-
"""Unified SQLite connection factory with WAL mode enforcement.

Replaces ~50 duplicate ``sqlite3.connect()`` + PRAGMA patterns across the
codebase with a single, consistent factory function.

Usage:
    from shared.db_base import get_connection

    conn = get_connection("billing.db")           # relative to data/
    conn = get_connection("agent.db", data_dir="data/databases")
    conn = get_connection("/abs/path/to/db.db")   # absolute path
"""

import os
import sqlite3
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def get_connection(
    db_name: str,
    *,
    data_dir: str | Path | None = None,
    timeout: float = 20.0,
    busy_timeout: int = 10000,
    row_factory=sqlite3.Row,
) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode, synchronous=NORMAL, and busy timeout.

    All RazAgent SQLite databases MUST use this factory to guarantee:
      - WAL journal mode (concurrent readers)
      - synchronous=NORMAL (safe + fast, recommended for WAL)
      - busy_timeout (avoid SQLITE_BUSY on multi-process access)
      - row_factory=sqlite3.Row (dict-like access)
      - Parent directories auto-created

    Args:
        db_name: Database filename (relative) or absolute path.
        data_dir: Base directory for relative db_name. Defaults to PROJECT_ROOT/data.
        timeout: sqlite3.connect timeout (seconds). Default 20s.
        busy_timeout: PRAGMA busy_timeout value (milliseconds). Default 5000ms.
        row_factory: Row factory (default sqlite3.Row, pass None to disable).

    Returns:
        sqlite3.Connection with WAL mode and synchronous=NORMAL.
    """
    if os.path.isabs(db_name):
        db_path = Path(db_name)
    else:
        base = Path(data_dir) if data_dir else _DATA_DIR
        db_path = base / db_name

    os.makedirs(db_path.parent, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout}")
    if row_factory is not None:
        conn.row_factory = row_factory
    return conn
