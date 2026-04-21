# -*- coding: utf-8 -*-
"""Trading Intelligence V1.0 — Orchestrator.

3-hour cycle scheduler that runs the full intelligence pipeline:
    Collect Market Data -> Fetch News -> Score Sentiment -> Technical Analysis
    -> Generate Predictions -> Suggest Trades -> Request Approval -> Execute
    -> Format Report -> Send via Telegram

Usage:
    from trading_intelligence import TradingIntelligenceOrchestrator

    orchestrator = TradingIntelligenceOrchestrator(telegram_send_func=my_send)
    await orchestrator.start()
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

import httpx

from .config import (
    CYCLE_SECONDS,
    DB_PATH,
    DB_SCHEMA,
    DATA_DIR,
    MAX_TRADE_AMOUNT_USD,
)
from .data_collector import fetch_top_10_coins, fetch_fear_greed, fetch_defi_tvl
from .news_aggregator import fetch_news
from .sentiment_analyzer import score_news, aggregate_sentiment
from .technical_analyzer import analyze_technical
from .prediction_engine import generate_predictions
from .trade_suggester import generate_suggestions
from .trade_executor import execute_trade, save_pending_suggestion, update_suggestion_status
from .report_formatter import format_telegram_report

logger = logging.getLogger("TradingIntelligence")

# Type alias for the Telegram send function
TelegramSendFunc = Callable[[str], Awaitable[None]]


class TradingIntelligenceOrchestrator:
    """3-hour cycle crypto research and prediction system.

    Args:
        telegram_send_func: Async callable that sends an HTML message to Telegram.
            Signature: async def send(html_text: str) -> None
        auto_trade: If True, will request approval and execute approved trades.
            If False, only generates suggestions in reports (default: False).
    """

    def __init__(
        self,
        telegram_send_func: TelegramSendFunc | None = None,
        auto_trade: bool = False,
    ) -> None:
        self._send_telegram = telegram_send_func
        self._auto_trade = auto_trade
        self._running = False
        self._task: asyncio.Task | None = None
        self._cycle_count = 0
        self._last_cycle_at: str = ""
        self._client: httpx.AsyncClient | None = None

        # Ensure data directory and DB exist
        os.makedirs(DATA_DIR, exist_ok=True)
        logger.info(
            "TradingIntelligenceOrchestrator initialized (auto_trade=%s, db=%s)",
            auto_trade, DB_PATH,
        )

    # -----------------------------------------------------------------------
    # DB initialization
    # -----------------------------------------------------------------------
    async def _init_db(self) -> None:
        """Create database tables if they don't exist."""
        try:
            import aiosqlite

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA busy_timeout=5000")
                await db.executescript(DB_SCHEMA)
                await db.commit()
            logger.info("Database initialized at %s", DB_PATH)
        except ImportError:
            logger.error(
                "aiosqlite not installed — predictions and news cache will NOT be persisted. "
                "Install with: pip install aiosqlite"
            )
        except Exception as exc:
            logger.error("DB initialization failed: %s", exc)

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------
    async def start(self) -> None:
        """Start the 3-hour cycle loop as a background task."""
        if self._running:
            logger.warning("Orchestrator already running")
            return

        await self._init_db()
        self._running = True
        self._client = httpx.AsyncClient(timeout=60)
        self._task = asyncio.create_task(self._loop(), name="TradingIntelligence_Loop")
        logger.info("Trading Intelligence cycle started (every %ds)", CYCLE_SECONDS)

    async def stop(self) -> None:
        """Stop the cycle loop gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Trading Intelligence stopped after %d cycles", self._cycle_count)

    @property
    def status(self) -> dict:
        """Return current orchestrator status."""
        return {
            "running": self._running,
            "cycle_count": self._cycle_count,
            "last_cycle_at": self._last_cycle_at,
            "auto_trade": self._auto_trade,
            "cycle_interval_hours": CYCLE_SECONDS / 3600,
            "max_trade_usd": MAX_TRADE_AMOUNT_USD,
        }

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------
    async def _loop(self) -> None:
        """Run cycles forever until stopped."""
        # Run first cycle immediately
        try:
            await self._run_cycle()
        except Exception as exc:
            logger.error("First cycle failed: %s", exc, exc_info=True)

        while self._running:
            try:
                await self._wait_until_next_cycle()
                if not self._running:
                    break
                await self._run_cycle()
            except asyncio.CancelledError:
                logger.info("Cycle loop cancelled")
                break
            except Exception as exc:
                logger.error("Cycle failed: %s", exc, exc_info=True)
                # Wait 5 minutes before retrying on error
                await asyncio.sleep(300)

    # -----------------------------------------------------------------------
    # Single cycle execution
    # -----------------------------------------------------------------------
    async def _run_cycle(self) -> None:
        """Execute one full intelligence cycle."""
        cycle_start = time.monotonic()
        self._cycle_count += 1
        self._last_cycle_at = datetime.now(timezone.utc).isoformat()

        logger.info("=== CYCLE #%d START ===", self._cycle_count)

        client = self._client or httpx.AsyncClient(timeout=60)

        # Step 1: Collect market data
        logger.info("[1/7] Collecting market data...")
        market_data, fear_greed, defi_tvl = await asyncio.gather(
            fetch_top_10_coins(client),
            fetch_fear_greed(client),
            fetch_defi_tvl(client),
            return_exceptions=True,
        )

        # Handle exceptions from gather
        if isinstance(market_data, Exception):
            logger.error("Market data fetch failed: %s", market_data)
            market_data = []
        if isinstance(fear_greed, Exception):
            logger.error("Fear & Greed fetch failed: %s", fear_greed)
            fear_greed = {"value": 50, "classification": "Neutral"}
        if isinstance(defi_tvl, Exception):
            logger.error("DeFi TVL fetch failed: %s", defi_tvl)
            defi_tvl = {"total_tvl_usd": 0}

        if not market_data:
            logger.error("No market data — aborting cycle")
            await self._notify("No market data available. Cycle #%d aborted." % self._cycle_count)
            return

        # Step 2: Fetch news
        logger.info("[2/7] Fetching news...")
        try:
            news_items = await fetch_news(client)
        except Exception as exc:
            logger.error("News fetch failed: %s", exc)
            news_items = []

        # Step 3: Score news sentiment
        logger.info("[3/7] Scoring news sentiment (%d items)...", len(news_items))
        try:
            scored_news = await score_news(news_items, client)
        except Exception as exc:
            logger.error("News scoring failed: %s", exc)
            scored_news = news_items  # Use unscored

        # Aggregate sentiment
        try:
            agg_sentiment = await aggregate_sentiment(scored_news, fear_greed, client)
        except Exception as exc:
            logger.error("Aggregate sentiment failed: %s", exc)
            agg_sentiment = None

        # Step 4: Technical analysis
        logger.info("[4/7] Running technical analysis...")
        try:
            technical = await analyze_technical(market_data, client)
        except Exception as exc:
            logger.error("Technical analysis failed: %s", exc)
            technical = []

        # Step 5: Generate predictions
        logger.info("[5/7] Generating predictions...")
        try:
            predictions = await generate_predictions(
                market_data, scored_news, technical, fear_greed, agg_sentiment, client
            )
        except Exception as exc:
            logger.error("Prediction generation failed: %s", exc)
            predictions = []

        # Step 6: Generate trade suggestions
        logger.info("[6/7] Generating trade suggestions...")
        try:
            suggestions = await generate_suggestions(predictions, technical)
        except Exception as exc:
            logger.error("Trade suggestion generation failed: %s", exc)
            suggestions = []

        # Step 7: Format and send report
        cycle_duration = time.monotonic() - cycle_start
        logger.info("[7/7] Formatting report...")

        # Fetch exchange balances for report (multi-exchange)
        exchange_balances: dict[str, float] | None = None
        try:
            from .exchanges.exchange_router import get_router
            router = get_router()
            await router.initialize()
            exchange_balances = await router.get_all_balances()
        except Exception as exc:
            logger.debug("Could not fetch exchange balances: %s", exc)

        report = format_telegram_report(
            market_data=market_data,
            fear_greed=fear_greed,
            defi_tvl=defi_tvl,
            scored_news=scored_news,
            predictions=predictions,
            suggestions=suggestions,
            aggregate_sentiment=agg_sentiment,
            cycle_duration_seconds=cycle_duration,
            exchange_balances=exchange_balances,
        )

        await self._notify(report)

        # Handle trade suggestions (if auto_trade enabled)
        if self._auto_trade and suggestions:
            for suggestion in suggestions:
                await self._handle_trade_suggestion(suggestion)

        logger.info(
            "=== CYCLE #%d COMPLETE (%.1fs) — %d predictions, %d suggestions ===",
            self._cycle_count, cycle_duration, len(predictions), len(suggestions),
        )

    # -----------------------------------------------------------------------
    # Trade suggestion handler (with approval gate)
    # -----------------------------------------------------------------------
    async def _handle_trade_suggestion(self, suggestion: dict) -> None:
        """Request approval and execute a trade suggestion."""
        coin = suggestion.get("coin", "unknown")
        action = suggestion.get("action", "?")
        amount = suggestion.get("amount_usd", 0)
        confidence = suggestion.get("confidence", 0)

        # Save as pending first
        suggestion_id = await save_pending_suggestion(suggestion)

        # Request approval via shared approval gate
        try:
            from shared.approval_gate import ApprovalGate

            gate = ApprovalGate.instance()
            result = await gate.require_approval(
                action_description=(
                    f"Trading Intelligence: {action} {coin.upper()} "
                    f"${amount:.2f} (confidence {confidence}%)\n"
                    f"Entry: ${suggestion.get('entry_price', 0):.6f} | "
                    f"SL: ${suggestion.get('stop_loss', 0):.6f} | "
                    f"TP: ${suggestion.get('take_profit', 0):.6f}\n"
                    f"Reasoning: {suggestion.get('reasoning', '')[:100]}"
                ),
                agent_id="trading_intelligence",
                severity="HIGH",
            )

            status = result.get("status", "TIMEOUT_BLOCKED")
            logger.info("Trade approval for %s %s: %s", action, coin, status)

            if status == "APPROVED":
                if suggestion_id:
                    await update_suggestion_status(suggestion_id, "APPROVED")

                # Execute the trade
                exec_result = await execute_trade(suggestion)

                if exec_result.get("success"):
                    if suggestion_id:
                        await update_suggestion_status(
                            suggestion_id, "EXECUTED",
                            execution_price=exec_result.get("execution_price"),
                            order_id=exec_result.get("order_id"),
                        )
                    await self._notify(
                        f"\u2705 Trade executed: {action} {coin.upper()} "
                        f"${amount:.2f} @ ${exec_result.get('execution_price', 0):.6f}"
                    )
                else:
                    await self._notify(
                        f"\u274c Trade execution failed for {coin.upper()}: "
                        f"{exec_result.get('error', 'Unknown error')}"
                    )
            else:
                if suggestion_id:
                    await update_suggestion_status(suggestion_id, "REJECTED")
                logger.info("Trade %s %s was %s", action, coin, status)

        except ImportError:
            logger.error("ApprovalGate not available — skipping trade execution")
        except Exception as exc:
            logger.error("Trade approval/execution failed for %s: %s", coin, exc)

    # -----------------------------------------------------------------------
    # Wait until next cycle
    # -----------------------------------------------------------------------
    async def _wait_until_next_cycle(self) -> None:
        """Sleep until the next 3-hour mark.

        Aligns to 00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00 UTC.
        """
        now = datetime.now(timezone.utc)
        current_hour = now.hour
        current_cycle = (current_hour // 3) * 3
        next_cycle_hour = current_cycle + 3

        if next_cycle_hour >= 24:
            # Next day
            next_cycle_hour = 0
            next_cycle = now.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            # Add a day
            from datetime import timedelta
            next_cycle = next_cycle + timedelta(days=1)
        else:
            next_cycle = now.replace(
                hour=next_cycle_hour, minute=0, second=0, microsecond=0
            )

        wait_seconds = (next_cycle - now).total_seconds()

        if wait_seconds <= 0:
            wait_seconds = CYCLE_SECONDS  # Fallback

        logger.info(
            "Next cycle at %s UTC (in %.0f minutes)",
            next_cycle.strftime("%H:%M"), wait_seconds / 60,
        )

        # Sleep in chunks to allow cancellation
        chunk = 60  # Check every minute
        elapsed = 0
        while elapsed < wait_seconds and self._running:
            sleep_time = min(chunk, wait_seconds - elapsed)
            await asyncio.sleep(sleep_time)
            elapsed += sleep_time

    # -----------------------------------------------------------------------
    # Notification helper
    # -----------------------------------------------------------------------
    async def _notify(self, message: str) -> None:
        """Send a notification via Telegram (if send function provided).

        V1.2 REMAIN_SILENT: the 3h cycle report carries the footer
        "Trading Intelligence V1.0 | RazAgent Enterprise" and is NOT
        actionable in the moment — it's a research digest. Route these
        straight to audit_logs.db and silence Telegram. Trade execution
        notifications (no footer) still pass through.
        """
        if "Trading Intelligence V1.0" in message:
            try:
                from shared.trading_notify import record_audit_action
                record_audit_action(
                    "trading_cycle_report_suppressed",
                    {"len": len(message), "preview": message[:240]},
                )
            except Exception as exc:
                logger.debug("audit write failed for suppressed cycle report: %s", exc)
            logger.info(
                "trading cycle report suppressed to audit-only (%d chars)", len(message),
            )
            return
        if self._send_telegram:
            try:
                await self._send_telegram(message)
            except Exception as exc:
                logger.error("Telegram notification failed: %s", exc)
        else:
            logger.info("No Telegram send function — report logged only")

    # -----------------------------------------------------------------------
    # Manual trigger (for testing or on-demand)
    # -----------------------------------------------------------------------
    async def run_once(self) -> str:
        """Run a single cycle manually and return the report text.

        Useful for testing or triggering from a Telegram command.
        """
        await self._init_db()

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60)

        # Temporarily suppress Telegram send to capture report
        original_send = self._send_telegram
        captured_report = ""

        async def capture_send(text: str) -> None:
            nonlocal captured_report
            captured_report = text
            if original_send:
                await original_send(text)

        self._send_telegram = capture_send
        try:
            await self._run_cycle()
        finally:
            self._send_telegram = original_send

        return captured_report
