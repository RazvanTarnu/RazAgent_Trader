# -*- coding: utf-8 -*-
"""Trading Intelligence V1.0 — Trade Executor.

Executes SPOT market trades via existing crypto_swarm/exchange_connector.py.
Requires human approval via shared/approval_gate.py before each trade.

Safety:
  - SPOT MARKET ONLY (no futures, no margin)
  - Max $50 per trade (HARD LIMIT)
  - API key validation before execution
  - All trades logged to SQLite

V1.1: Post-trade audit alert via TradeCrypto Bot (Telegram).
"""
import logging
from datetime import datetime, timezone
from typing import Any

from .config import MAX_TRADE_AMOUNT_USD, DB_PATH

logger = logging.getLogger("TradingIntelligence")


# ---------------------------------------------------------------------------
# Trade audit alert — fires after every executed trade (paper or live)
# ---------------------------------------------------------------------------
async def _send_trade_audit(suggestion: dict, exec_result: dict) -> None:
    """Send a detailed audit alert to @TradeCrypto13_bot after trade execution.

    Includes: pair, action, strategy, price, quantity, reasoning, VRAM/GPU health.
    Uses HTML parse_mode for clean Telegram rendering.

    V1.2 (2026-04-17): delegates to `shared.trading_notify.send_trading_alert`
    so channel isolation + severity gate are enforced centrally. Trade
    executions are CRITICAL — they are financial actions that must always
    reach the chat regardless of noise-suppression config.
    """
    try:
        # Gather trade details
        coin = suggestion.get("coin", "unknown").upper()
        action = suggestion.get("action", "?")
        symbol = exec_result.get("symbol", f"{coin}USDT")
        exchange = exec_result.get("exchange", "unknown")
        entry_price = exec_result.get("execution_price", suggestion.get("entry_price", 0))
        quantity = exec_result.get("quantity", 0)
        amount_usd = suggestion.get("amount_usd", 0)
        order_id = exec_result.get("order_id", "N/A")
        confidence = suggestion.get("confidence", 0)
        reasoning = suggestion.get("reasoning", "No reasoning provided")
        stop_loss = suggestion.get("stop_loss", 0)
        take_profit = suggestion.get("take_profit", 0)

        # Determine strategy from confidence/reasoning heuristics
        reasoning_lower = reasoning.lower()
        if any(kw in reasoning_lower for kw in ("momentum", "trend", "ma cross", "breakout", "1h")):
            strategy = "\u03b1 Alpha (Momentum)"
        elif any(kw in reasoning_lower for kw in ("scalp", "1m", "quick", "spread", "micro")):
            strategy = "\u03b2 Beta (Scalper)"
        else:
            strategy = "\u03b1 Alpha (Trend)"

        # Determine mode (paper vs live)
        try:
            from crypto_bot.config import PAPER_MODE
            mode = "\U0001f4c4 PAPER" if PAPER_MODE else "\u26a1 LIVE"
        except Exception:
            mode = "\U0001f4c4 PAPER"

        # GPU health snapshot
        vram_line = ""
        try:
            from shared.vram_utils import get_vram
            _, total_mb, free_mb, temp_c = get_vram()
            vram_line = f"\U0001f4ca VRAM: {free_mb}MB free / {total_mb}MB | {temp_c}\u00b0C"
        except Exception:
            vram_line = "\U0001f4ca VRAM: unavailable"

        # Action emoji
        action_emoji = "\U0001f7e2" if action == "BUY" else "\U0001f534"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        msg = (
            f"{action_emoji} <b>TRADE AUDIT REPORT</b> [{mode}]\n"
            f"{'=' * 32}\n"
            f"\n"
            f"\U0001f4b1 <b>{action} {symbol}</b> on {exchange.capitalize()}\n"
            f"\U0001f3af Strategy: {strategy}\n"
            f"\n"
            f"\U0001f4b0 <b>Execution</b>\n"
            f"   Entry Price: <code>${entry_price:.6f}</code>\n"
            f"   Quantity: <code>{quantity:.8f}</code>\n"
            f"   Size: <code>${amount_usd:.2f}</code>\n"
            f"   Order ID: <code>{order_id}</code>\n"
            f"\n"
            f"\U0001f6e1 <b>Risk Management</b>\n"
            f"   Stop Loss: <code>${stop_loss:.6f}</code>\n"
            f"   Take Profit: <code>${take_profit:.6f}</code>\n"
            f"   Confidence: {confidence}%\n"
            f"\n"
            f"\U0001f9e0 <b>Reasoning</b>\n"
            f"   <i>{reasoning[:300]}</i>\n"
            f"\n"
            f"{vram_line}\n"
            f"\u23f0 {ts}"
        )

        from shared.trading_notify import send_trading_alert, CRITICAL
        ok = await send_trading_alert(
            msg,
            source="trade_audit",
            category="crypto",
            severity=CRITICAL,   # trade executions are financial actions — always deliver
        )
        if ok:
            logger.info("Trade audit sent for %s %s", action, symbol)
        else:
            logger.warning("Trade audit send returned False for %s %s", action, symbol)

    except Exception as exc:
        logger.error("Trade audit notification failed: %s", exc)

# Symbol mapping: CoinGecko ID -> exchange trading pair (Binance-style)
_SYMBOL_MAP: dict[str, str] = {
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "binancecoin": "BNBUSDT",
    "solana": "SOLUSDT",
    "xrp": "XRPUSDT",
    "cardano": "ADAUSDT",
    "dogecoin": "DOGEUSDT",
    "avalanche-2": "AVAXUSDT",
    "polkadot": "DOTUSDT",
    "chainlink": "LINKUSDT",
    "toncoin": "TONUSDT",
    "sui": "SUIUSDT",
    "near": "NEARUSDT",
    "tron": "TRXUSDT",
}


def _get_trading_symbol(coin_id: str) -> str | None:
    """Map CoinGecko coin ID to exchange trading pair."""
    return _SYMBOL_MAP.get(coin_id)


# ---------------------------------------------------------------------------
# Execute a single trade
# ---------------------------------------------------------------------------
async def execute_trade(suggestion: dict) -> dict:
    """Execute a SPOT market trade based on a suggestion.

    Uses crypto_swarm/exchange_connector.py for the actual exchange call.
    HARD LIMIT: $50 per trade enforced here regardless of suggestion.

    Args:
        suggestion: Trade suggestion dict from trade_suggester.

    Returns:
        Execution result dict with keys: success, order_id, execution_price, error.
    """
    coin = suggestion.get("coin", "")
    action = suggestion.get("action", "")
    amount_usd = suggestion.get("amount_usd", 0)
    entry_price = suggestion.get("entry_price", 0)

    # ---- Safety checks ----

    # 1. Validate amount HARD LIMIT
    if amount_usd > MAX_TRADE_AMOUNT_USD:
        logger.error(
            "BLOCKED: Trade amount $%.2f exceeds HARD LIMIT $%.2f",
            amount_usd, MAX_TRADE_AMOUNT_USD,
        )
        return {
            "success": False,
            "error": f"Amount ${amount_usd} exceeds hard limit ${MAX_TRADE_AMOUNT_USD}",
        }

    if amount_usd <= 0:
        return {"success": False, "error": "Invalid trade amount"}

    # 2. Get trading symbol
    symbol = _get_trading_symbol(coin)
    if not symbol:
        return {"success": False, "error": f"No trading pair for {coin}"}

    # 3. Validate action
    if action not in ("BUY", "SELL"):
        return {"success": False, "error": f"Invalid action: {action}"}

    # ---- Execute via Exchange Router (multi-exchange) ----
    try:
        from .exchanges.exchange_router import get_router

        router = get_router()
        result = await router.execute_trade(symbol, action, amount_usd)

        if result.success:
            logger.info(
                "Trade executed on %s: %s %s qty=%.8f @ $%.6f (order=%s)",
                result.exchange, action, symbol, result.quantity,
                result.price, result.order_id,
            )

            # Save to DB
            await _save_execution(suggestion, result.order_id or "", result.price)

            exec_result = {
                "success": True,
                "order_id": result.order_id or "",
                "execution_price": float(result.price),
                "quantity": result.quantity,
                "symbol": symbol,
                "action": action,
                "amount_usd": amount_usd,
                "exchange": result.exchange,
            }

            # V19.30: Fire B2B webhook for trade.executed
            try:
                from shared.webhooks import dispatch_event
                dispatch_event("trade.executed", exec_result)
            except Exception:
                pass  # Webhook failure never blocks trade flow

            # V1.1: Post-trade audit alert to Telegram
            try:
                await _send_trade_audit(suggestion, exec_result)
            except Exception:
                pass  # Audit failure never blocks trade flow

            return exec_result
        else:
            return {"success": False, "error": result.error or "Unknown error"}

    except ImportError:
        logger.error("Exchange router not available")
        return {"success": False, "error": "Exchange router module not found"}
    except Exception as exc:
        logger.error("Trade execution failed: %s", exc)
        return {"success": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


# ---------------------------------------------------------------------------
# Persist execution result
# ---------------------------------------------------------------------------
async def _save_execution(suggestion: dict, order_id: str, execution_price: float) -> None:
    """Save trade suggestion with execution details to DB."""
    try:
        import aiosqlite

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """INSERT INTO trade_suggestions
                   (coin, action, amount_usd, entry_price, stop_loss, take_profit,
                    reasoning, confidence, status, executed_at, execution_price, order_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'EXECUTED', datetime('now'), ?, ?)""",
                (
                    suggestion.get("coin", ""),
                    suggestion.get("action", ""),
                    suggestion.get("amount_usd", 0),
                    suggestion.get("entry_price", 0),
                    suggestion.get("stop_loss", 0),
                    suggestion.get("take_profit", 0),
                    suggestion.get("reasoning", ""),
                    suggestion.get("confidence", 0),
                    execution_price,
                    order_id,
                ),
            )
            await db.commit()
    except Exception as exc:
        logger.error("Failed to save execution to DB: %s", exc)


# ---------------------------------------------------------------------------
# Save pending suggestion (before approval)
# ---------------------------------------------------------------------------
async def save_pending_suggestion(suggestion: dict) -> int | None:
    """Save a pending trade suggestion to DB. Returns the row ID."""
    try:
        import aiosqlite

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            cursor = await db.execute(
                """INSERT INTO trade_suggestions
                   (coin, action, amount_usd, entry_price, stop_loss, take_profit,
                    reasoning, confidence, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')""",
                (
                    suggestion.get("coin", ""),
                    suggestion.get("action", ""),
                    suggestion.get("amount_usd", 0),
                    suggestion.get("entry_price", 0),
                    suggestion.get("stop_loss", 0),
                    suggestion.get("take_profit", 0),
                    suggestion.get("reasoning", ""),
                    suggestion.get("confidence", 0),
                ),
            )
            await db.commit()
            return cursor.lastrowid
    except Exception as exc:
        logger.error("Failed to save pending suggestion: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Update suggestion status
# ---------------------------------------------------------------------------
async def update_suggestion_status(
    suggestion_id: int,
    status: str,
    **kwargs: Any,
) -> None:
    """Update a trade suggestion status in DB."""
    try:
        import aiosqlite

        valid_statuses = ("PENDING", "APPROVED", "REJECTED", "EXECUTED", "EXPIRED")
        if status not in valid_statuses:
            logger.error("Invalid status: %s", status)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA journal_mode=WAL")

            set_parts = ["status = ?"]
            params: list[Any] = [status]

            if status == "APPROVED":
                set_parts.append("approved_at = datetime('now')")
            if "execution_price" in kwargs:
                set_parts.append("execution_price = ?")
                params.append(kwargs["execution_price"])
            if "order_id" in kwargs:
                set_parts.append("order_id = ?")
                params.append(kwargs["order_id"])
            if status == "EXECUTED":
                set_parts.append("executed_at = datetime('now')")

            params.append(suggestion_id)
            await db.execute(
                f"UPDATE trade_suggestions SET {', '.join(set_parts)} WHERE id = ?",
                params,
            )
            await db.commit()
    except Exception as exc:
        logger.error("Failed to update suggestion %d: %s", suggestion_id, exc)
