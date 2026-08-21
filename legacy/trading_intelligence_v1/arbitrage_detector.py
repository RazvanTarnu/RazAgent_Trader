# -*- coding: utf-8 -*-
"""Multi-Exchange Trading V1.0 — Arbitrage Detector.

Monitors price differences between Binance and KuCoin every 5 minutes.
Alerts via Telegram when difference exceeds 0.5% (after fees).
NO auto-execution — alerts only.
"""

import asyncio
import logging
from datetime import datetime
from typing import Callable, Coroutine, Optional

import aiosqlite

from .config import DB_PATH
from .exchanges.exchange_router import get_router

logger = logging.getLogger("TradingIntelligence")

# Top symbols to monitor for arbitrage
_ARBI_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT",
]

_CHECK_INTERVAL = 300  # 5 minutes
_ALERT_THRESHOLD_PCT = 0.5  # 0.5% price difference
_FEE_ESTIMATE_PCT = 0.2     # ~0.1% per side


class ArbitrageDetector:
    """Detects and alerts on cross-exchange arbitrage opportunities."""

    def __init__(
        self,
        telegram_send_func: Optional[Callable[[str], Coroutine]] = None,
    ):
        self._send = telegram_send_func
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._router = get_router()

    async def start(self) -> None:
        """Initialize DB table and start the detection loop."""
        await self._init_db()
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="arbitrage-detector")
        logger.info("ArbitrageDetector started (interval=%ds, threshold=%.1f%%)", _CHECK_INTERVAL, _ALERT_THRESHOLD_PCT)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ArbitrageDetector stopped")

    async def _init_db(self) -> None:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS arbitrage_log (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    detected_at         TEXT NOT NULL DEFAULT (datetime('now')),
                    symbol              TEXT NOT NULL,
                    binance_price       REAL,
                    kucoin_price        REAL,
                    difference_percent  REAL,
                    potential_profit_usd REAL,
                    action_taken        TEXT,
                    notes               TEXT
                );
                CREATE TABLE IF NOT EXISTS exchange_stats (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    date            TEXT NOT NULL,
                    exchange        TEXT NOT NULL,
                    trades_count    INTEGER DEFAULT 0,
                    success_count   INTEGER DEFAULT 0,
                    total_volume_usd REAL DEFAULT 0,
                    avg_slippage_percent REAL,
                    avg_latency_ms  REAL
                );
                CREATE INDEX IF NOT EXISTS idx_arb_symbol ON arbitrage_log(symbol, detected_at);

                CREATE TABLE IF NOT EXISTS trade_executions (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    prediction_id       INTEGER,
                    exchange            TEXT NOT NULL,
                    symbol              TEXT NOT NULL,
                    side                TEXT NOT NULL,
                    amount_usd          REAL NOT NULL,
                    quantity            REAL,
                    entry_price         REAL,
                    fees                REAL,
                    order_id            TEXT,
                    status              TEXT NOT NULL DEFAULT 'pending',
                    error_message       TEXT,
                    created_at          TEXT DEFAULT (datetime('now')),
                    executed_at         TEXT
                );

                CREATE TABLE IF NOT EXISTS price_snapshots (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange    TEXT NOT NULL,
                    symbol      TEXT NOT NULL,
                    price       REAL NOT NULL,
                    volume_24h  REAL,
                    timestamp   TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS exchange_status (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange        TEXT UNIQUE NOT NULL,
                    is_connected    INTEGER DEFAULT 0,
                    can_trade       INTEGER DEFAULT 0,
                    last_check      TEXT,
                    last_error      TEXT,
                    usdt_balance    REAL DEFAULT 0,
                    updated_at      TEXT DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_exec_exchange ON trade_executions(exchange);
                CREATE INDEX IF NOT EXISTS idx_exec_status ON trade_executions(status);
                CREATE INDEX IF NOT EXISTS idx_prices_sym ON price_snapshots(symbol, exchange);
                CREATE INDEX IF NOT EXISTS idx_exstatus_exchange ON exchange_status(exchange);
            """)
            await db.commit()

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._check_all()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Arbitrage check error: %s", exc)
            await asyncio.sleep(_CHECK_INTERVAL)

    async def _check_all(self) -> None:
        """Check all monitored symbols for arbitrage."""
        await self._router.initialize()
        if len(self._router.executors) < 2:
            return  # Need at least 2 exchanges

        for symbol in _ARBI_SYMBOLS:
            try:
                await self._check_symbol(symbol)
            except Exception as exc:
                logger.debug("Arbitrage check failed for %s: %s", symbol, exc)

    async def _check_symbol(self, symbol: str) -> None:
        prices = await self._router.compare_prices(symbol)
        if len(prices) < 2:
            return

        binance_pi = prices.get("binance")
        kucoin_pi = prices.get("kucoin")
        if not binance_pi or not kucoin_pi:
            return

        b_mid = (binance_pi.bid + binance_pi.ask) / 2
        k_mid = (kucoin_pi.bid + kucoin_pi.ask) / 2

        if b_mid <= 0 or k_mid <= 0:
            return

        diff = abs(b_mid - k_mid)
        diff_pct = (diff / min(b_mid, k_mid)) * 100

        if diff_pct < _ALERT_THRESHOLD_PCT:
            return

        # Calculate potential profit on $50 trade
        if b_mid < k_mid:
            direction = "Buy Binance \u2192 Sell KuCoin"
            profit_per_unit = k_mid - b_mid
        else:
            direction = "Buy KuCoin \u2192 Sell Binance"
            profit_per_unit = b_mid - k_mid

        qty = 50.0 / min(b_mid, k_mid)
        gross_profit = qty * profit_per_unit
        fees = 50.0 * _FEE_ESTIMATE_PCT / 100
        net_profit = gross_profit - fees

        # Log to DB
        await self._log_opportunity(
            symbol, b_mid, k_mid, diff_pct, net_profit, direction,
        )

        # Alert if profitable after fees
        if net_profit > 0.50 and self._send:
            await self._send(
                f"\U0001f4b0 <b>ARBITRAGE OPPORTUNITY</b>\n\n"
                f"<b>{symbol}</b>\n"
                f"Binance: ${b_mid:,.2f}\n"
                f"KuCoin: ${k_mid:,.2f}\n"
                f"Difference: {diff_pct:.2f}%\n\n"
                f"<b>Strategy:</b> {direction}\n"
                f"<b>Est. Profit ($50 trade):</b> ${net_profit:.2f}\n\n"
                f"\u26a0\ufe0f Manual execution only"
            )

    async def _log_opportunity(
        self,
        symbol: str,
        binance_price: float,
        kucoin_price: float,
        diff_pct: float,
        net_profit: float,
        direction: str,
    ) -> None:
        action = "alert_sent" if net_profit > 0.50 else "logged"
        try:
            async with aiosqlite.connect(str(DB_PATH)) as db:
                await db.execute(
                    """INSERT INTO arbitrage_log
                       (symbol, binance_price, kucoin_price, difference_percent,
                        potential_profit_usd, action_taken, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (symbol, binance_price, kucoin_price, diff_pct, net_profit, action, direction),
                )
                await db.commit()
        except Exception as exc:
            logger.debug("Failed to log arbitrage: %s", exc)

        logger.info(
            "Arbitrage %s: %.2f%% diff (B=%.2f K=%.2f) profit=$%.2f [%s]",
            symbol, diff_pct, binance_price, kucoin_price, net_profit, action,
        )
