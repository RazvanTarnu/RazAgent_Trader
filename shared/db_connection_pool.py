# -*- coding: utf-8 -*-
"""Centralized SQLite Connection Pool — Thread-Safe Singleton.

Thin wrapper over shared.db_utils that adds:
- threading.local() connection caching (one conn per thread per DB)
- Default busy_timeout=10000ms (10s, user requirement)
- Re-exports everything from db_utils for backward compat

Usage:
    from shared.db_connection_pool import get_pooled_connection
    conn = get_pooled_connection("billing")   # cached per-thread
    conn = get_pooled_connection("agent")

    # Or use the standard (non-cached) API:
    from shared.db_connection_pool import get_db_connection
    conn = get_db_connection("billing")       # new conn each call

V1.0 — Tech Debt Cleanup: Centralized DB access.
"""

import sqlite3
import threading
import logging
from typing import Optional

from shared.db_utils import (
    get_db_connection as _upstream_get_db_connection,
    get_async_connection,
    DB_REGISTRY,
    _resolve_db_path,
)

__all__ = [
    "get_pooled_connection",
    "get_db_connection",
    "get_async_connection",
    "close_all",
    "DB_REGISTRY",
]

logger = logging.getLogger("godclaw.db_connection_pool")

# ── Thread-Local Connection Cache ──
_local = threading.local()

# Default pragmas enforced on every connection
_DEFAULT_BUSY_TIMEOUT = 10_000  # 10 seconds


def get_pooled_connection(
    name_or_path: str,
    *,
    busy_timeout: int = _DEFAULT_BUSY_TIMEOUT,
) -> sqlite3.Connection:
    """Get a thread-local cached SQLite connection.

    Returns the same connection for the same DB name within a thread.
    Enforces WAL + synchronous=NORMAL + busy_timeout.

    Args:
        name_or_path: Logical name from DB_REGISTRY or path to .db file.
        busy_timeout: PRAGMA busy_timeout in ms (default 10000).

    Returns:
        sqlite3.Connection (cached per thread).
    """
    cache: dict = getattr(_local, "connections", None)
    if cache is None:
        cache = {}
        _local.connections = cache

    db_path = _resolve_db_path(name_or_path)

    # Return cached connection if still valid
    conn = cache.get(db_path)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except (sqlite3.ProgrammingError, sqlite3.OperationalError):
            # Connection was closed or broken — recreate
            cache.pop(db_path, None)

    # Create fresh connection via upstream factory
    conn = _upstream_get_db_connection(
        name_or_path,
        busy_timeout=busy_timeout,
    )
    cache[db_path] = conn
    logger.debug("Cached connection for %s (thread %s)", name_or_path, threading.current_thread().name)
    return conn


def get_db_connection(
    name_or_path: str,
    *,
    timeout: float = 20.0,
    busy_timeout: int = _DEFAULT_BUSY_TIMEOUT,
    row_factory=sqlite3.Row,
) -> sqlite3.Connection:
    """Non-cached connection — delegates to db_utils with 10s busy_timeout default.

    Drop-in replacement for shared.db_utils.get_db_connection with higher
    default busy_timeout (10s vs 5s).
    """
    return _upstream_get_db_connection(
        name_or_path,
        timeout=timeout,
        busy_timeout=busy_timeout,
        row_factory=row_factory,
    )


def close_all() -> int:
    """Close all cached connections in the current thread.

    Returns:
        Number of connections closed.
    """
    cache: Optional[dict] = getattr(_local, "connections", None)
    if not cache:
        return 0
    count = 0
    for path, conn in list(cache.items()):
        try:
            conn.close()
            count += 1
        except Exception:
            pass
    cache.clear()
    logger.debug("Closed %d cached connections (thread %s)", count, threading.current_thread().name)
    return count
