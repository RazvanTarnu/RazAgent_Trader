# -*- coding: utf-8 -*-
"""Unified Database Factory — sync + async connections with WAL enforcement.

Extends db_base.py with async (aiosqlite) support and a connection registry
for all 13+ project databases. Replaces ~45 raw aiosqlite.connect() calls.

Usage (sync):
    from shared.db_utils import get_db_connection
    conn = get_db_connection("billing")          # -> data/billing.db
    conn = get_db_connection("agent")            # -> data/databases/agent.db

Usage (async):
    from shared.db_utils import get_async_connection
    async with get_async_connection("trading") as db:
        rows = await db.execute_fetchall("SELECT ...")

V6.3 — Tech Debt P1: Centralized DB connections.

# REFACTOR (audit V-007, 2026-04-20): 25 modules still define local `_get_conn()`
# helpers + ~257 direct `sqlite3.connect(` calls bypass this SSOT. Migration
# target: replace every local `_get_conn()` with `get_db_connection(<db_key>)`.
# Known offenders (file:line, from audit grep):
#   shared/audit_log.py:104 (method), shared/mission_control.py:67,
#   shared/telemetry.py:56, shared/trade_journal.py:22, shared/webhooks.py:72,
#   shared/referral_engine.py:63, shared/monetization_tracker.py:60,
#   shared/plugin_marketplace.py:61, shared/push_notifications.py:51,
#   shared/replay_engine.py:74, shared/voice_api.py:106, shared/blog_generator.py:108,
#   shared/freelance_worker.py:203,
#   shared/watchdog_cycles/{analytics_feedback,publishing,trend_scout}_cycle.py,
#   Social_Distribution_Worker/pipeline/{analytics_scraper,comment_manager,youtube_upload}.py,
#   backend/razagent_server/api/routers/{admin_stats,admin_ui}.py,
#   backend/razagent_server/skills/{a2a_protocol,affiliate_bridge,video_analytics}.py.
# Execute as a dedicated sprint (one module at a time, test after each) — do NOT
# mass-migrate; the `_get_conn` signatures vary slightly (timeout, WAL pragma).
"""

import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"

# ── Database Registry: logical name → (relative_path, data_dir_override) ──
# All project databases in one place for discoverability.
DB_REGISTRY: dict[str, str] = {
    # Core
    "agent":                str(_DATA_DIR / "databases" / "agent.db"),
    "billing":              str(_DATA_DIR / "billing.db"),
    "audit":                str(_DATA_DIR / "audit_logs.db"),
    "telemetry":            str(_DATA_DIR / "telemetry.db"),
    # Trading
    "trading":              str(_DATA_DIR / "trading_intelligence.db"),
    "trade_journal":        str(_PROJECT_ROOT / "Shared_Memory" / "claude_memory.db"),
    # Video & Content
    "youtube":              str(_DATA_DIR / "youtube_publisher.db"),
    "tiktok":               str(_DATA_DIR / "tiktok_publisher.db"),
    "financial":            str(_DATA_DIR / "financial_agents.db"),
    "affiliate":            str(_DATA_DIR / "affiliate_links.db"),
    "ab_tests":             str(_DATA_DIR / "ab_tests.db"),
    "omnichannel":          str(_DATA_DIR / "omnichannel.db"),
    # Other
    "mission_control":      str(_DATA_DIR / "mission_control.db"),
    "memory":               str(_PROJECT_ROOT / "Shared_Memory" / "claude_memory.db"),
    "webhooks":             str(_DATA_DIR / "webhooks.db"),
    "decision_replay":      str(_DATA_DIR / "decision_replay.db"),
    "a2a":                  str(_DATA_DIR / "a2a_protocol.db"),
}


def _resolve_db_path(name_or_path: str) -> str:
    """Resolve a logical DB name or absolute path to a file path."""
    # If it's a registered name, use the registry
    if name_or_path in DB_REGISTRY:
        return DB_REGISTRY[name_or_path]
    # If it's an absolute path, use as-is
    if os.path.isabs(name_or_path):
        return name_or_path
    # If it ends with .db, treat as relative to data/
    if name_or_path.endswith(".db"):
        return str(_DATA_DIR / name_or_path)
    # Last resort: try adding .db
    return str(_DATA_DIR / f"{name_or_path}.db")


# ── Sync Connection Factory ──

def get_db_connection(
    name_or_path: str,
    *,
    timeout: float = 20.0,
    busy_timeout: int = 5000,
    row_factory=sqlite3.Row,
) -> sqlite3.Connection:
    """Open a sync SQLite connection with WAL + NORMAL synchronous.

    Args:
        name_or_path: Logical name from DB_REGISTRY (e.g. "billing", "agent")
                      or an absolute/relative path to a .db file.
        timeout: sqlite3.connect timeout in seconds.
        busy_timeout: PRAGMA busy_timeout in milliseconds.
        row_factory: Row factory (default sqlite3.Row).

    Returns:
        sqlite3.Connection with WAL mode enforced.
    """
    db_path = _resolve_db_path(name_or_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout}")
    if row_factory is not None:
        conn.row_factory = row_factory
    return conn


# ── Async Connection Factory ──

@asynccontextmanager
async def get_async_connection(
    name_or_path: str,
    *,
    timeout: float = 20.0,
    busy_timeout: int = 5000,
) -> AsyncGenerator:
    """Open an async SQLite connection (aiosqlite) with WAL enforcement.

    Usage:
        async with get_async_connection("trading") as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT ...")

    Args:
        name_or_path: Logical name from DB_REGISTRY or path to .db file.
        timeout: aiosqlite timeout in seconds.
        busy_timeout: PRAGMA busy_timeout in milliseconds.

    Yields:
        aiosqlite.Connection with WAL mode enforced.
    """
    import aiosqlite

    db_path = _resolve_db_path(name_or_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    db = await aiosqlite.connect(db_path, timeout=timeout)
    try:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute(f"PRAGMA busy_timeout={busy_timeout}")
        db.row_factory = aiosqlite.Row
        yield db
    finally:
        await db.close()
