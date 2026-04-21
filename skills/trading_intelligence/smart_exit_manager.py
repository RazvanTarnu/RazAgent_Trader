# -*- coding: utf-8 -*-
"""Smart Exit Manager — V11.30

Dynamic trailing stop-loss manager for open trades.
Monitors open positions, adjusts stop-loss levels, and auto-closes
trades when the trailing stop is hit.

Rules:
  1. Break-Even:  pnl_pct > 3.0%  → move SL to entry_price (eliminate loss risk)
  2. Trailing:    pnl_pct > 6.0%  → trail SL at 3% below current price (BUY)
                                     or 3% above current price (SELL)
                                     ONLY if new SL is better than existing
  3. Stop-Loss:   price hits SL   → close trade, record final P&L, notify via Telegram

Uses Binance /api/v3/ticker/price for lightweight price checks (no API key needed).

DB: Shared_Memory/claude_memory.db (trade_journal table)
Telegram: TRADE_CRYPTO_BOT_TOKEN / TRADE_CRYPTO_CHAT_ID

Usage:
    from trading_intelligence.smart_exit_manager import evaluate_open_trades
    result = await evaluate_open_trades()
"""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("TradingIntelligence.smart_exit")

JOURNAL_DB = Path("D:/RazAgent_Enterprise/Shared_Memory/claude_memory.db")
PROJECT_ROOT = Path("D:/RazAgent_Enterprise")

# Trailing stop configuration
BREAKEVEN_THRESHOLD_PCT = 3.0    # Move SL to entry when profit > 3%
TRAILING_THRESHOLD_PCT = 6.0     # Start trailing when profit > 6%
TRAILING_DISTANCE_PCT = 3.0      # Trail 3% behind current price


async def _get_live_price(symbol: str) -> float | None:
    """Fetch current price from Binance public ticker (no API key needed)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": symbol},
            )
            if resp.status_code == 200:
                return float(resp.json().get("price", 0))
    except Exception as e:
        logger.debug("Price fetch failed for %s: %s", symbol, e)
    return None


async def _send_crypto_telegram(text: str) -> bool:
    """Send message via TradeCrypto Bot."""
    try:
        from shared.keyring_loader import get_credential

        token = get_credential("TRADE_CRYPTO_BOT_TOKEN")
        chat_id = get_credential("TRADE_CRYPTO_CHAT_ID")
        if not token or not chat_id:
            return False

        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text[:4096],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            return resp.status_code == 200
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        return False


def _get_open_trades() -> list[dict]:
    """Fetch all open trades (ts_close IS NULL)."""
    try:
        conn = sqlite3.connect(str(JOURNAL_DB), timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, pair, side, entry_price, size_usd, stop_loss, current_sl, "
            "ts_open, paper_mode, strategy "
            "FROM trade_journal WHERE ts_close IS NULL"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("Failed to fetch open trades: %s", e)
        return []


def _update_sl(trade_id: int, new_sl: float):
    """Update the current_sl for an open trade."""
    try:
        conn = sqlite3.connect(str(JOURNAL_DB), timeout=5)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "UPDATE trade_journal SET current_sl = ? WHERE id = ?",
            (new_sl, trade_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Failed to update SL for trade %d: %s", trade_id, e)


def _close_trade(trade_id: int, exit_price: float, pnl_usd: float, pnl_pct: float, reason: str):
    """Close a trade by setting ts_close, exit_price, pnl, outcome."""
    try:
        outcome = "win" if pnl_usd > 0 else "loss" if pnl_usd < 0 else "breakeven"
        conn = sqlite3.connect(str(JOURNAL_DB), timeout=5)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "UPDATE trade_journal SET ts_close = ?, exit_price = ?, pnl_usd = ?, "
            "pnl_pct = ?, exit_reason = ?, outcome = ? WHERE id = ?",
            (
                datetime.utcnow().isoformat(),
                exit_price, round(pnl_usd, 4), round(pnl_pct, 2),
                reason, outcome, trade_id,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Failed to close trade %d: %s", trade_id, e)


async def evaluate_open_trades() -> dict:
    """Evaluate all open trades and apply trailing stop logic.

    Returns:
        dict with success, output, trades_evaluated, sl_updates, trades_closed.
    """
    import asyncio

    trades = _get_open_trades()
    if not trades:
        return {
            "success": True,
            "output": "No open trades to monitor",
            "trades_evaluated": 0,
            "sl_updates": 0,
            "trades_closed": 0,
        }

    sl_updates = 0
    trades_closed = 0
    actions = []

    for trade in trades:
        trade_id = trade["id"]
        pair = trade["pair"]
        side = trade["side"]  # "buy" or "sell"
        entry = trade["entry_price"]
        size = trade["size_usd"]
        current_sl = trade["current_sl"] or trade["stop_loss"] or 0

        # Fetch live price
        price = await _get_live_price(pair)
        if price is None or price <= 0:
            logger.debug("Skipping trade %d (%s): price unavailable", trade_id, pair)
            continue

        # Calculate current P&L
        if side == "buy":
            pnl_pct = ((price - entry) / entry) * 100
            pnl_usd = (pnl_pct / 100) * size
        else:  # sell/short
            pnl_pct = ((entry - price) / entry) * 100
            pnl_usd = (pnl_pct / 100) * size

        # --- Rule 1: Stop-Loss Hit → Close trade ---
        if current_sl > 0:
            sl_hit = False
            if side == "buy" and price <= current_sl:
                sl_hit = True
            elif side == "sell" and price >= current_sl:
                sl_hit = True

            if sl_hit:
                _close_trade(trade_id, price, pnl_usd, pnl_pct, "trailing_stop")
                trades_closed += 1
                actions.append(f"CLOSED {pair}: SL hit at ${price:.2f} (P&L: ${pnl_usd:+.2f})")

                # Telegram notification
                mode = "PAPER" if trade.get("paper_mode") else "LIVE"
                emoji = "🟢" if pnl_usd > 0 else "🔴"
                await _send_crypto_telegram(
                    f"{emoji} <b>[TRAILING STOP ACTIVAT]</b>\n"
                    f"{'─' * 28}\n"
                    f"Tranzactie inchisa automat!\n"
                    f"📊 Pereche: <b>{pair}</b>\n"
                    f"💰 Profit: <b>${pnl_usd:+.2f}</b> ({pnl_pct:+.1f}%)\n"
                    f"📈 Entry: ${entry:.2f} → Exit: ${price:.2f}\n"
                    f"🛡️ SL: ${current_sl:.2f}\n"
                    f"📋 Mode: {mode}\n"
                    f"{'─' * 28}"
                )
                logger.info(
                    "Trade %d (%s) CLOSED by trailing stop: P&L=$%.2f (%.1f%%)",
                    trade_id, pair, pnl_usd, pnl_pct,
                )
                continue

        # --- Rule 2: Break-Even (pnl > 3%) ---
        if pnl_pct > BREAKEVEN_THRESHOLD_PCT:
            if side == "buy" and (current_sl < entry or current_sl == 0):
                _update_sl(trade_id, entry)
                sl_updates += 1
                actions.append(f"BREAK-EVEN {pair}: SL → ${entry:.2f} (was ${current_sl:.2f})")
                current_sl = entry  # Update for trailing check below
            elif side == "sell" and (current_sl > entry or current_sl == 0):
                _update_sl(trade_id, entry)
                sl_updates += 1
                actions.append(f"BREAK-EVEN {pair}: SL → ${entry:.2f}")
                current_sl = entry

        # --- Rule 3: Trailing (pnl > 6%) ---
        if pnl_pct > TRAILING_THRESHOLD_PCT:
            if side == "buy":
                new_sl = round(price * (1 - TRAILING_DISTANCE_PCT / 100), 2)
                if new_sl > current_sl:
                    _update_sl(trade_id, new_sl)
                    sl_updates += 1
                    actions.append(f"TRAIL {pair}: SL ${current_sl:.2f} → ${new_sl:.2f} (price=${price:.2f})")
            else:  # sell
                new_sl = round(price * (1 + TRAILING_DISTANCE_PCT / 100), 2)
                if current_sl == 0 or new_sl < current_sl:
                    _update_sl(trade_id, new_sl)
                    sl_updates += 1
                    actions.append(f"TRAIL {pair}: SL ${current_sl:.2f} → ${new_sl:.2f} (price=${price:.2f})")

        # Brief rate limit between price checks
        await asyncio.sleep(0.1)

    # Build output
    output_lines = [
        f"🛡️ Smart Exit: {len(trades)} trades evaluated",
        f"  SL updates: {sl_updates}",
        f"  Trades closed: {trades_closed}",
    ]
    if actions:
        output_lines.append("  Actions:")
        for a in actions:
            output_lines.append(f"    • {a}")

    return {
        "success": True,
        "output": "\n".join(output_lines),
        "trades_evaluated": len(trades),
        "sl_updates": sl_updates,
        "trades_closed": trades_closed,
        "actions": actions,
    }
