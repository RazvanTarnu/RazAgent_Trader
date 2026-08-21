# -*- coding: utf-8 -*-
"""Trading Intelligence V1.0 — Balance Aggregator.

Aggregates USDT balances across all connected exchanges and updates
the exchange_status table for dashboard/reporting use.
"""

import logging
from datetime import datetime, timezone

import aiosqlite

from .config import DB_PATH
from .exchanges.exchange_router import get_router

logger = logging.getLogger("TradingIntelligence")


async def get_aggregated_balances() -> dict[str, float]:
    """Fetch USDT balances from all connected exchanges.

    Returns:
        Dict mapping exchange name to USDT free balance,
        plus a "total" key with the sum.
    """
    router = get_router()
    await router.initialize()
    return await router.get_all_balances()


async def update_exchange_status() -> dict[str, dict]:
    """Update exchange_status table with current connection state and balances.

    Returns:
        Dict of exchange → {connected, can_trade, balance, error}.
    """
    router = get_router()
    await router.initialize()

    statuses: dict[str, dict] = {}
    now = datetime.now(timezone.utc).isoformat()

    for name, executor in router.executors.items():
        status = {
            "is_connected": 0,
            "can_trade": 0,
            "usdt_balance": 0.0,
            "last_error": None,
        }
        try:
            connected = await executor.test_connection()
            status["is_connected"] = 1 if connected else 0

            if connected:
                # Use total USD across all assets, not just USDT
                if hasattr(executor, "get_total_balance_usd"):
                    status["usdt_balance"] = await executor.get_total_balance_usd()
                else:
                    balance = await executor.get_balance("USDT")
                    status["usdt_balance"] = balance.free if balance else 0.0
                status["can_trade"] = 1
        except Exception as exc:
            status["last_error"] = str(exc)[:200]
            logger.debug("Exchange status check failed for %s: %s", name, exc)

        statuses[name] = status

        # Persist to DB
        try:
            async with aiosqlite.connect(str(DB_PATH)) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("""
                    INSERT INTO exchange_status (exchange, is_connected, can_trade, last_check, last_error, usdt_balance, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(exchange) DO UPDATE SET
                        is_connected = excluded.is_connected,
                        can_trade = excluded.can_trade,
                        last_check = excluded.last_check,
                        last_error = excluded.last_error,
                        usdt_balance = excluded.usdt_balance,
                        updated_at = excluded.updated_at
                """, (
                    name,
                    status["is_connected"],
                    status["can_trade"],
                    now,
                    status["last_error"],
                    status["usdt_balance"],
                    now,
                ))
                await db.commit()
        except Exception as exc:
            logger.debug("Failed to persist exchange_status for %s: %s", name, exc)

    return statuses


async def save_price_snapshot(exchange: str, symbol: str, price: float, volume_24h: float = 0) -> None:
    """Save a price snapshot for historical analysis."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("""
                INSERT INTO price_snapshots (exchange, symbol, price, volume_24h)
                VALUES (?, ?, ?, ?)
            """, (exchange, symbol, price, volume_24h))
            await db.commit()
    except Exception as exc:
        logger.debug("Failed to save price snapshot: %s", exc)


async def get_recent_price_snapshots(symbol: str, hours: int = 24) -> list[dict]:
    """Get recent price snapshots for a symbol across exchanges."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT exchange, symbol, price, volume_24h, timestamp
                FROM price_snapshots
                WHERE symbol = ? AND datetime(timestamp) > datetime('now', ?)
                ORDER BY timestamp DESC
                LIMIT 200
            """, (symbol, f'-{hours} hours'))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as exc:
        logger.debug("Failed to fetch price snapshots: %s", exc)
        return []
