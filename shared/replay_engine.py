# -*- coding: utf-8 -*-
"""V28.0 — Decision Replay Engine.

Logs every autonomous decision to a SQLite database and supports full replay
for post-mortem debugging. Each decision captures input sources, confidence
scores, and enough context to re-evaluate the decision offline.

Usage:
    from shared.replay_engine import log_decision, update_approval, replay_decision

    did = log_decision("ceo", {"vision": True}, {"vision": 0.93}, "approve_publish")
    update_approval(did, "APPROVED", wait_time=4.2)
    replay = replay_decision(did)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Version import (graceful fallback)
# ---------------------------------------------------------------------------
try:
    from shared.version import APP_VERSION
except ImportError:
    APP_VERSION = "V28.0"

# ---------------------------------------------------------------------------
# Ghost-log pattern: silently no-ops if AuditLog is unavailable
# ---------------------------------------------------------------------------
_audit: Optional[object] = None


def _ghost_log(action: str, **details: object) -> None:
    """Log to enterprise audit trail. Silently no-ops on any failure."""
    global _audit
    try:
        if _audit is None:
            from shared.audit_log import AuditLog
            _audit = AuditLog.instance()
        _audit.log_action(  # type: ignore[union-attr]
            action,
            agent_id="replay_engine",
            session_id="system",
            details=details,
        )
    except Exception:
        pass  # ghost — never surface errors from audit logging


from shared.config import DATA_DIR
from shared.db_connection_pool import get_pooled_connection

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("godclaw.replay_engine")

# ---------------------------------------------------------------------------
# Database setup — uses thread-local pooled connection (Tech Debt P1)
# ---------------------------------------------------------------------------
DB_PATH = DATA_DIR / "decision_replay.db"

_schema_initialized = threading.Event()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local pooled connection (WAL + busy_timeout enforced)."""
    conn = get_pooled_connection("decision_replay")
    if not _schema_initialized.is_set():
        _ensure_schema(conn)
        _schema_initialized.set()
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the decision_log table and indexes if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS decision_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        TEXT    NOT NULL,
            decision_id      TEXT    NOT NULL UNIQUE,
            agent_id         TEXT    NOT NULL,
            input_sources    TEXT    NOT NULL,  -- JSON
            confidence_scores TEXT   NOT NULL,  -- JSON
            decision         TEXT    NOT NULL,
            approval_status  TEXT    NOT NULL DEFAULT 'PENDING',
            approval_wait_time REAL  NOT NULL DEFAULT 0.0,
            retries_sent     INTEGER NOT NULL DEFAULT 0,
            details          TEXT,              -- JSON
            replay_data      TEXT               -- JSON — enough info to re-evaluate
        );

        CREATE INDEX IF NOT EXISTS idx_decision_log_timestamp
            ON decision_log(timestamp);
        CREATE INDEX IF NOT EXISTS idx_decision_log_decision_id
            ON decision_log(decision_id);
        CREATE INDEX IF NOT EXISTS idx_decision_log_approval_status
            ON decision_log(approval_status);
    """)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_decision(
    agent_id: str,
    input_sources: dict,
    confidence_scores: dict,
    decision: str,
    details: Optional[dict] = None,
) -> str:
    """Record a new decision and return its unique decision_id.

    Parameters
    ----------
    agent_id:
        Identifier of the agent that made the decision (e.g. ``"ceo"``,
        ``"agent_video"``).
    input_sources:
        Dict describing which input signals were available and their state.
    confidence_scores:
        Dict mapping signal names to their confidence values.
    decision:
        Short label for the decision taken (e.g. ``"approve_publish"``,
        ``"block_action"``).
    details:
        Optional free-form dict with extra context.

    Returns
    -------
    str
        The 12-character hex decision_id for future reference.
    """
    conn = _get_conn()
    decision_id = uuid.uuid4().hex[:12]
    timestamp = datetime.now(timezone.utc).isoformat()

    # Build replay_data: everything needed to re-run the evaluation
    replay_data = {
        "input_sources": input_sources,
        "confidence_scores": confidence_scores,
        "decision": decision,
        "agent_id": agent_id,
        "version": APP_VERSION,
        "original_timestamp": timestamp,
    }
    if details:
        replay_data["details"] = details

    conn.execute(
        """
        INSERT INTO decision_log
            (timestamp, decision_id, agent_id, input_sources,
             confidence_scores, decision, details, replay_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            timestamp,
            decision_id,
            agent_id,
            json.dumps(input_sources, default=str),
            json.dumps(confidence_scores, default=str),
            decision,
            json.dumps(details, default=str) if details else None,
            json.dumps(replay_data, default=str),
        ),
    )
    conn.commit()

    logger.info(
        "Decision logged: id=%s agent=%s decision=%s",
        decision_id,
        agent_id,
        decision,
    )
    _ghost_log(
        "decision_logged",
        decision_id=decision_id,
        agent_id=agent_id,
        decision=decision,
        version=APP_VERSION,
    )

    return decision_id


def update_approval(
    decision_id: str,
    status: str,
    wait_time: float = 0.0,
    retries: int = 0,
) -> None:
    """Update the approval status of an existing decision.

    Parameters
    ----------
    decision_id:
        The 12-character hex ID returned by :func:`log_decision`.
    status:
        New approval status: ``"APPROVED"``, ``"REJECTED"``, or ``"TIMEOUT"``.
    wait_time:
        How many seconds the system waited for approval.
    retries:
        Number of retry notifications sent before this status was set.
    """
    valid_statuses = {"PENDING", "APPROVED", "REJECTED", "TIMEOUT"}
    if status not in valid_statuses:
        raise ValueError(f"Invalid status '{status}'. Must be one of {valid_statuses}")

    conn = _get_conn()
    cursor = conn.execute(
        """
        UPDATE decision_log
        SET approval_status = ?,
            approval_wait_time = ?,
            retries_sent = ?
        WHERE decision_id = ?
        """,
        (status, wait_time, retries, decision_id),
    )
    conn.commit()

    if cursor.rowcount == 0:
        logger.warning("update_approval: decision_id=%s not found", decision_id)
    else:
        logger.info(
            "Approval updated: id=%s status=%s wait=%.2fs retries=%d",
            decision_id,
            status,
            wait_time,
            retries,
        )
        _ghost_log(
            "approval_updated",
            decision_id=decision_id,
            status=status,
            wait_time=wait_time,
            retries=retries,
        )


def get_decision(decision_id: str) -> Optional[dict]:
    """Fetch a single decision by its ID.

    Returns
    -------
    dict or None
        The full decision record with JSON fields parsed, or ``None`` if
        the decision_id does not exist.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM decision_log WHERE decision_id = ?",
        (decision_id,),
    ).fetchone()

    if row is None:
        return None

    return _row_to_dict(row)


def replay_decision(decision_id: str) -> dict:
    """Retrieve replay data for a decision so the caller can re-evaluate.

    Returns a dict containing ``input_sources`` and ``confidence_scores``
    exactly as they were at decision time, allowing the caller to feed
    them back into :func:`shared.failsafe_engine.evaluate_inputs` or
    any other evaluation function.

    Parameters
    ----------
    decision_id:
        The 12-character hex ID to replay.

    Returns
    -------
    dict
        Keys: ``decision_id``, ``input_sources``, ``confidence_scores``,
        ``decision``, ``agent_id``, ``original_timestamp``, ``replay_data``.

    Raises
    ------
    KeyError
        If the decision_id is not found.
    """
    record = get_decision(decision_id)
    if record is None:
        raise KeyError(f"Decision '{decision_id}' not found in replay database")

    replay_data = record.get("replay_data") or {}

    result = {
        "decision_id": decision_id,
        "input_sources": record.get("input_sources", {}),
        "confidence_scores": record.get("confidence_scores", {}),
        "decision": record.get("decision", ""),
        "agent_id": record.get("agent_id", ""),
        "original_timestamp": record.get("timestamp", ""),
        "replay_data": replay_data,
    }

    logger.info("Replaying decision: id=%s", decision_id)
    _ghost_log(
        "decision_replayed",
        decision_id=decision_id,
        version=APP_VERSION,
    )

    return result


def get_recent_decisions(
    limit: int = 20,
    status_filter: str = "",
) -> list[dict]:
    """Return recent decisions, newest first.

    Parameters
    ----------
    limit:
        Maximum number of records to return (default 20).
    status_filter:
        If non-empty, only return decisions with this ``approval_status``
        (e.g. ``"PENDING"``, ``"REJECTED"``).

    Returns
    -------
    list[dict]
        List of decision records with JSON fields parsed.
    """
    conn = _get_conn()

    if status_filter:
        rows = conn.execute(
            """
            SELECT * FROM decision_log
            WHERE approval_status = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (status_filter, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM decision_log
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    results = [_row_to_dict(row) for row in rows]

    logger.debug(
        "get_recent_decisions: returned %d records (filter=%s)",
        len(results),
        status_filter or "none",
    )

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict, parsing JSON text fields."""
    record = dict(row)

    # Parse JSON text columns
    for key in ("input_sources", "confidence_scores", "details", "replay_data"):
        raw = record.get(key)
        if isinstance(raw, str):
            try:
                record[key] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass  # keep as string if malformed

    return record
