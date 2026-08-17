# -*- coding: utf-8 -*-
"""Platform event/audit logger."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from shared.platform.interfaces import AuditEvent, EventLogger


class SQLiteEventLogger(EventLogger):
    """Append-only SQLite event logger for platform audit trail."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS platform_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    category TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    target TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL
                )
                """
            )
            conn.commit()
            conn.close()

    def log_event(self, event: AuditEvent) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO platform_events
                (timestamp, category, action, actor, target, details, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp.isoformat(),
                    event.category,
                    event.action,
                    event.actor,
                    event.target,
                    json.dumps(event.details),
                    event.status,
                ),
            )
            conn.commit()
            conn.close()

    def list_events(
        self,
        *,
        category: Optional[str] = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        with self._lock:
            conn = self._connect()
            if category:
                rows = conn.execute(
                    "SELECT * FROM platform_events WHERE category = ? ORDER BY id DESC LIMIT ?",
                    (category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM platform_events ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            conn.close()
            return [self._row_to_event(r) for r in rows]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            timestamp=datetime.fromisoformat(row["timestamp"]),
            category=row["category"],
            action=row["action"],
            actor=row["actor"],
            target=row["target"],
            details=json.loads(row["details"] or "{}"),
            status=row["status"],
        )
