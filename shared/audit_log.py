# -*- coding: utf-8 -*-
"""V25.1 — Enterprise Audit Log.

Centralized, append-only audit trail for ALL agent actions.
Records tool calls, file operations, API requests, browser actions.
Includes circuit breaker for anomaly detection.

Usage:
    from audit_log import AuditLog
    audit = AuditLog.instance()
    audit.log_action("tool_call", target="web_search", agent_id="ceo", session_id="abc123", details={"query": "test"})
"""

import os
import sys
import json
import time
import sqlite3
import logging
import threading
import functools
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

from shared.config import AUDIT_DB as DB_PATH

logger = logging.getLogger("godclaw.audit_log")

# Circuit breaker config
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 100    # max actions per window per agent (V18.0: was 10, too low for agentic loop)
BLOCK_DURATION = 300    # 5 min block after breach


def with_sqlite_retry(max_retries: int = 3, base_delay: float = 0.1):
    """Decorator: retry on sqlite3.OperationalError with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Base delay in seconds (doubled each retry).
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            "[V25.2] SQLite retry %d/%d for %s: %s (backoff %.2fs)",
                            attempt + 1, max_retries, func.__name__, exc, delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            "[V25.2] SQLite exhausted %d retries for %s: %s",
                            max_retries, func.__name__, exc,
                        )
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


class AuditLog:
    """Singleton enterprise audit logger with SQLite backend and circuit breaker."""

    _instance: Optional["AuditLog"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._db_lock = threading.Lock()
        self._rate_tracker: dict[str, list[float]] = {}  # agent_id -> [timestamps]
        self._blocked_agents: dict[str, float] = {}      # agent_id -> block_until
        self._init_db()

    @classmethod
    def instance(cls) -> "AuditLog":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def force_wal_repair(cls) -> dict:
        """V25.1: Force WAL repair — checkpoint + integrity check. Call after zombie kill."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=15)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            conn.close()
            return {"status": "ok", "mode": mode, "integrity": integrity}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _get_conn(self) -> sqlite3.Connection:
        """Get SQLite connection via shared/db_base.py factory with retry logic.

        V1.8.0: Migrated from inline sqlite3.connect + PRAGMA boilerplate
        to centralized get_connection() which enforces WAL + synchronous=NORMAL.
        """
        from shared.db_base import get_connection
        max_retries = 3
        for attempt in range(max_retries):
            try:
                conn = get_connection(str(DB_PATH), timeout=20.0, busy_timeout=10000)
                return conn
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < max_retries - 1:
                    logger.warning(f"[V25.1] DB locked, retry {attempt+1}/{max_retries}...")
                    time.sleep(1 + attempt)
                    continue
                raise
        raise sqlite3.OperationalError("DB connection failed after retries")

    def _init_db(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_conn()

        # Step 1: Create tables (without indexes that reference migrated columns)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                timestamp_unix REAL NOT NULL,
                action_type TEXT NOT NULL,
                target TEXT,
                status TEXT NOT NULL DEFAULT 'ok',
                error_message TEXT,
                session_id TEXT,
                agent_id TEXT NOT NULL DEFAULT 'ceo',
                details TEXT,
                rollback_info TEXT,
                duration_ms REAL
            );

            CREATE TABLE IF NOT EXISTS audit_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                message TEXT,
                resolved INTEGER DEFAULT 0
            );
        """)
        conn.commit()

        # Step 2: Schema migration — add columns missing on older DBs
        _MIGRATIONS = [
            ("audit_actions", "timestamp_unix", "ALTER TABLE audit_actions ADD COLUMN timestamp_unix REAL DEFAULT 0"),
            ("audit_actions", "error_message", "ALTER TABLE audit_actions ADD COLUMN error_message TEXT"),
            ("audit_actions", "session_id", "ALTER TABLE audit_actions ADD COLUMN session_id TEXT"),
            ("audit_actions", "rollback_info", "ALTER TABLE audit_actions ADD COLUMN rollback_info TEXT"),
            ("audit_actions", "duration_ms", "ALTER TABLE audit_actions ADD COLUMN duration_ms REAL"),
        ]
        for table, col, sql in _MIGRATIONS:
            try:
                existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if col not in existing:
                    conn.execute(sql)
                    conn.commit()
                    logger.info("Schema migration: added %s.%s", table, col)
            except Exception as exc:
                logger.debug("Migration skip %s.%s: %s", table, col, exc)

        # Step 3: Create indexes (AFTER migrations ensure columns exist)
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_actions(timestamp_unix)",
            "CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit_actions(agent_id)",
            "CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_actions(action_type)",
            "CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_actions(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_alerts_agent ON audit_alerts(agent_id)",
        ]:
            try:
                conn.execute(idx_sql)
            except Exception:
                pass
        conn.commit()
        # V25.1: Force WAL checkpoint to merge any stale WAL data
        try:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass  # Non-critical if checkpoint fails
        conn.close()
        logger.info("[V25.1] Audit log initialized at %s", DB_PATH)

    def log_action(
        self,
        action_type: str,
        target: str = "",
        status: str = "ok",
        error_message: str = "",
        session_id: str = "",
        agent_id: str = "ceo",
        details: dict | None = None,
        rollback_info: dict | None = None,
        duration_ms: float = 0.0,
    ) -> dict:
        """Log an action to the audit trail. Returns {"logged": True, "blocked": False} or {"logged": False, "blocked": True, "reason": "..."}."""
        now = time.time()
        now_utc = datetime.now(timezone.utc).isoformat()

        # Circuit breaker check
        blocked, reason = self._check_rate_limit(agent_id, now)
        if blocked:
            # Log the blocked attempt itself
            self._write_record(
                now_utc, now, "CIRCUIT_BREAKER_BLOCK", target,
                "blocked", reason, session_id, agent_id,
                json.dumps({"original_action": action_type}), None, 0.0,
            )
            return {"logged": False, "blocked": True, "reason": reason}

        # Write audit record
        details_json = json.dumps(details, default=str) if details else None
        rollback_json = json.dumps(rollback_info, default=str) if rollback_info else None

        self._write_record(
            now_utc, now, action_type, target,
            status, error_message, session_id, agent_id,
            details_json, rollback_json, duration_ms,
        )
        return {"logged": True, "blocked": False}

    @with_sqlite_retry(max_retries=3, base_delay=0.1)
    def _write_record(self, ts_utc, ts_unix, action_type, target, status, error_msg, session_id, agent_id, details, rollback, duration_ms):
        with self._db_lock:
            try:
                conn = self._get_conn()
                conn.execute(
                    "INSERT INTO audit_actions (timestamp, timestamp_unix, action_type, target, status, error_message, session_id, agent_id, details, rollback_info, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ts_utc, ts_unix, action_type, target, status, error_msg or None, session_id, agent_id, details, rollback, duration_ms),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error("[V25.1] Audit write failed: %s", e)

    def _check_rate_limit(self, agent_id: str, now: float) -> tuple[bool, str]:
        # Check if agent is currently blocked
        if agent_id in self._blocked_agents:
            if now < self._blocked_agents[agent_id]:
                remaining = int(self._blocked_agents[agent_id] - now)
                return True, f"Agent {agent_id} blocked for {remaining}s (rate limit breach)"
            else:
                del self._blocked_agents[agent_id]

        # Track rate
        if agent_id not in self._rate_tracker:
            self._rate_tracker[agent_id] = []

        timestamps = self._rate_tracker[agent_id]
        # Remove old timestamps outside window
        cutoff = now - RATE_LIMIT_WINDOW
        self._rate_tracker[agent_id] = [t for t in timestamps if t > cutoff]
        self._rate_tracker[agent_id].append(now)

        if len(self._rate_tracker[agent_id]) > RATE_LIMIT_MAX:
            # BREACH — block agent and create alert
            self._blocked_agents[agent_id] = now + BLOCK_DURATION
            reason = f"Rate limit exceeded: {len(self._rate_tracker[agent_id])} actions in {RATE_LIMIT_WINDOW}s (max {RATE_LIMIT_MAX})"
            self._create_alert(agent_id, "rate_limit_breach", reason)
            logger.warning("[V25.1] CIRCUIT BREAKER: %s", reason)
            return True, reason

        return False, ""

    def _create_alert(self, agent_id: str, alert_type: str, message: str):
        now_utc = datetime.now(timezone.utc).isoformat()
        with self._db_lock:
            try:
                conn = self._get_conn()
                conn.execute(
                    "INSERT INTO audit_alerts (timestamp, agent_id, alert_type, message) VALUES (?, ?, ?, ?)",
                    (now_utc, agent_id, alert_type, message),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error("[V25.1] Alert write failed: %s", e)

    # ── Query methods (for dashboard/API) ──

    def get_recent_actions(self, limit: int = 50, agent_id: str = "", action_type: str = "") -> list[dict]:
        """Get recent audit actions for dashboard."""
        limit = min(limit, 500)
        conn = self._get_conn()
        sql = "SELECT * FROM audit_actions WHERE 1=1"
        params: list = []
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        if action_type:
            sql += " AND action_type = ?"
            params.append(action_type)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_alerts(self, unresolved_only: bool = True, limit: int = 50) -> list[dict]:
        """Get audit alerts."""
        conn = self._get_conn()
        sql = "SELECT * FROM audit_alerts"
        if unresolved_only:
            sql += " WHERE resolved = 0"
        sql += " ORDER BY id DESC LIMIT ?"
        rows = conn.execute(sql, (min(limit, 200),)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_stats(self, hours: int = 24) -> dict:
        """Get audit statistics for the last N hours."""
        cutoff = time.time() - (hours * 3600)
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM audit_actions WHERE timestamp_unix > ?", (cutoff,)).fetchone()[0]
        by_type = conn.execute(
            "SELECT action_type, COUNT(*) as cnt FROM audit_actions WHERE timestamp_unix > ? GROUP BY action_type ORDER BY cnt DESC",
            (cutoff,),
        ).fetchall()
        by_agent = conn.execute(
            "SELECT agent_id, COUNT(*) as cnt FROM audit_actions WHERE timestamp_unix > ? GROUP BY agent_id ORDER BY cnt DESC",
            (cutoff,),
        ).fetchall()
        errors = conn.execute("SELECT COUNT(*) FROM audit_actions WHERE timestamp_unix > ? AND status != 'ok'", (cutoff,)).fetchone()[0]
        alerts = conn.execute("SELECT COUNT(*) FROM audit_alerts WHERE resolved = 0").fetchone()[0]
        conn.close()
        return {
            "period_hours": hours,
            "total_actions": total,
            "errors": errors,
            "unresolved_alerts": alerts,
            "by_type": {r["action_type"]: r["cnt"] for r in by_type},
            "by_agent": {r["agent_id"]: r["cnt"] for r in by_agent},
        }

    def is_agent_blocked(self, agent_id: str) -> bool:
        """Check if an agent is currently blocked by circuit breaker."""
        if agent_id in self._blocked_agents:
            if time.time() < self._blocked_agents[agent_id]:
                return True
            del self._blocked_agents[agent_id]
        return False

    def unblock_agent(self, agent_id: str) -> str:
        """Manually unblock an agent."""
        if agent_id in self._blocked_agents:
            del self._blocked_agents[agent_id]
            return f"Agent {agent_id} unblocked"
        return f"Agent {agent_id} is not blocked"
