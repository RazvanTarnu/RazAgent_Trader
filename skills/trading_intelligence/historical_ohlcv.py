# -*- coding: utf-8 -*-
"""Historical OHLCV Fetcher — V11.20

Fetches 2 years of daily candlestick data from Binance Public API
and stores it in data/trading_intelligence.db (daily_ohlcv table).

No API key required — uses the public /api/v3/klines endpoint.
Binance returns max 1000 candles per request, so for 730 days
a single request with limit=730 is sufficient.

Usage:
    from trading_intelligence.historical_ohlcv import fetch_2yr_daily_ohlcv, sync_ohlcv_watchlist
    data = await fetch_2yr_daily_ohlcv("BTCUSDT")
    await sync_ohlcv_watchlist()
"""
import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger("TradingIntelligence.ohlcv")

DB_PATH = Path("D:/RazAgent_Enterprise/data/trading_intelligence.db")
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# Default watchlist for daily sync
DEFAULT_WATCHLIST = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "BNBUSDT",
]


def _ensure_table():
    """Create the daily_ohlcv table if it doesn't exist."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_ohlcv (
            symbol TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            quote_volume REAL DEFAULT 0,
            trades INTEGER DEFAULT 0,
            fetched_at TEXT DEFAULT (datetime('now')),
            UNIQUE(symbol, timestamp)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_ts ON daily_ohlcv(symbol, timestamp)"
    )
    conn.commit()
    conn.close()


def _store_klines(symbol: str, klines: list[list]) -> int:
    """Store klines data in SQLite. Returns number of rows inserted/updated."""
    if not klines:
        return 0

    _ensure_table()
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    rows = []
    for k in klines:
        # Binance kline format: [open_time, open, high, low, close, volume,
        #   close_time, quote_volume, trades, taker_buy_base, taker_buy_quote, ignore]
        rows.append((
            symbol,
            int(k[0]),           # timestamp (ms)
            float(k[1]),         # open
            float(k[2]),         # high
            float(k[3]),         # low
            float(k[4]),         # close
            float(k[5]),         # volume
            float(k[7]),         # quote_volume
            int(k[8]),           # trades
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO daily_ohlcv "
        "(symbol, timestamp, open, high, low, close, volume, quote_volume, trades) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return len(rows)


async def fetch_2yr_daily_ohlcv(symbol: str) -> dict:
    """Fetch ~2 years (730 days) of daily OHLCV from Binance public API.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")

    Returns:
        dict with success, rows_stored, symbol, date_range.
    """
    import httpx

    symbol = symbol.upper()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                BINANCE_KLINES_URL,
                params={
                    "symbol": symbol,
                    "interval": "1d",
                    "limit": 730,
                },
            )
            if resp.status_code != 200:
                return {
                    "success": False,
                    "error": f"Binance API returned HTTP {resp.status_code}: {resp.text[:200]}",
                }

            klines = resp.json()
            if not isinstance(klines, list) or len(klines) == 0:
                return {"success": False, "error": "No kline data returned"}

            rows_stored = _store_klines(symbol, klines)

            # Date range
            first_ts = int(klines[0][0]) / 1000
            last_ts = int(klines[-1][0]) / 1000
            from datetime import datetime
            first_date = datetime.utcfromtimestamp(first_ts).strftime("%Y-%m-%d")
            last_date = datetime.utcfromtimestamp(last_ts).strftime("%Y-%m-%d")

            logger.info(
                "OHLCV fetched: %s — %d candles (%s to %s)",
                symbol, rows_stored, first_date, last_date,
            )

            return {
                "success": True,
                "symbol": symbol,
                "rows_stored": rows_stored,
                "date_range": f"{first_date} → {last_date}",
                "days": len(klines),
            }

    except httpx.TimeoutException:
        return {"success": False, "error": f"Binance API timeout for {symbol}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def sync_ohlcv_watchlist(symbols: list[str] | None = None) -> dict:
    """Sync daily OHLCV for all watchlist symbols.

    Args:
        symbols: List of trading pairs. Defaults to DEFAULT_WATCHLIST.

    Returns:
        dict with success, synced (count), errors, details.
    """
    import asyncio

    symbols = symbols or DEFAULT_WATCHLIST
    results = []
    errors = []

    for sym in symbols:
        result = await fetch_2yr_daily_ohlcv(sym)
        if result.get("success"):
            results.append(f"{sym}: {result['rows_stored']} candles")
        else:
            errors.append(f"{sym}: {result.get('error', 'unknown')}")
        # Rate limit: 100ms between requests (Binance allows 1200 req/min)
        await asyncio.sleep(0.2)

    return {
        "success": len(errors) == 0,
        "synced": len(results),
        "errors": len(errors),
        "details": results + errors,
        "output": (
            f"OHLCV Sync: {len(results)}/{len(symbols)} symbols synced"
            + (f", {len(errors)} errors" if errors else "")
        ),
    }
