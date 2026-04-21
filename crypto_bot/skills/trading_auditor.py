# -*- coding: utf-8 -*-
"""Trading Auditor — V11.10

Evaluates paper trading performance from the trade_journal table
and sends audit results via TradeCrypto Bot (@TradeCrypto13_bot).

Uses TRADE_CRYPTO_BOT_TOKEN and TRADE_CRYPTO_CHAT_ID from keyring.

DB: Shared_Memory/claude_memory.db
Table: trade_journal
Key columns: ts_open, ts_close, pnl_usd, pnl_pct, outcome, paper_mode

Usage:
    from crypto_bot.skills.trading_auditor import audit_paper_trading
    result = await audit_paper_trading()
"""
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("godclaw.trading_auditor")

PROJECT_ROOT = Path("D:/RazAgent_Enterprise")
TRADE_DB = PROJECT_ROOT / "Shared_Memory" / "claude_memory.db"


def _get_trade_stats() -> dict:
    """Query trade_journal for paper trading statistics.

    Returns dict with total_trades, closed_trades, wins, losses,
    win_rate, total_pnl, avg_pnl, best_trade, worst_trade.
    """
    stats = {
        "total_trades": 0, "closed_trades": 0,
        "wins": 0, "losses": 0, "win_rate": 0.0,
        "total_pnl": 0.0, "avg_pnl": 0.0,
        "best_trade": 0.0, "worst_trade": 0.0,
        "top_pair": "", "avg_duration_min": 0,
    }

    try:
        conn = sqlite3.connect(str(TRADE_DB), timeout=5)
        conn.row_factory = sqlite3.Row

        # Total trades
        stats["total_trades"] = conn.execute(
            "SELECT count(*) FROM trade_journal"
        ).fetchone()[0]

        # Closed trades (ts_close IS NOT NULL)
        closed = conn.execute(
            "SELECT count(*) FROM trade_journal WHERE ts_close IS NOT NULL"
        ).fetchone()[0]
        stats["closed_trades"] = closed

        if closed == 0:
            conn.close()
            return stats

        # Win/Loss counts
        stats["wins"] = conn.execute(
            "SELECT count(*) FROM trade_journal WHERE ts_close IS NOT NULL AND pnl_usd > 0"
        ).fetchone()[0]
        stats["losses"] = conn.execute(
            "SELECT count(*) FROM trade_journal WHERE ts_close IS NOT NULL AND pnl_usd <= 0"
        ).fetchone()[0]

        # Win rate
        stats["win_rate"] = round((stats["wins"] / closed) * 100, 1) if closed > 0 else 0.0

        # P&L aggregates
        row = conn.execute(
            "SELECT SUM(pnl_usd), AVG(pnl_usd), MAX(pnl_usd), MIN(pnl_usd), AVG(duration_minutes) "
            "FROM trade_journal WHERE ts_close IS NOT NULL"
        ).fetchone()
        if row:
            stats["total_pnl"] = round(row[0] or 0, 2)
            stats["avg_pnl"] = round(row[1] or 0, 2)
            stats["best_trade"] = round(row[2] or 0, 2)
            stats["worst_trade"] = round(row[3] or 0, 2)
            stats["avg_duration_min"] = int(row[4] or 0)

        # Top traded pair
        top = conn.execute(
            "SELECT pair, count(*) as cnt FROM trade_journal "
            "WHERE ts_close IS NOT NULL GROUP BY pair ORDER BY cnt DESC LIMIT 1"
        ).fetchone()
        if top:
            stats["top_pair"] = top[0]

        conn.close()
    except Exception as e:
        logger.error("Trade journal query failed: %s", e)

    return stats


async def _send_crypto_telegram(text: str) -> bool:
    """Send message via TradeCrypto Bot token to trading chat."""
    try:
        from shared.keyring_loader import get_credential

        token = get_credential("TRADE_CRYPTO_BOT_TOKEN")
        chat_id = get_credential("TRADE_CRYPTO_CHAT_ID")
        if not token or not chat_id:
            logger.warning("Missing TRADE_CRYPTO_BOT_TOKEN or TRADE_CRYPTO_CHAT_ID")
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
        logger.error("Crypto Telegram send failed: %s", e)
        return False


async def audit_paper_trading(params: dict | None = None) -> dict:
    """Audit paper trading performance and notify via TradeCrypto Bot.

    Returns:
        dict with success, output, stats, notification_sent.
    """
    stats = _get_trade_stats()

    if stats["closed_trades"] == 0:
        return {
            "success": True,
            "output": "No closed paper trades to audit yet",
            "stats": stats,
            "notification_sent": False,
        }

    pnl = stats["total_pnl"]
    wr = stats["win_rate"]
    profitable = pnl > 0 and wr > 55

    # Build Telegram message
    if profitable:
        msg = (
            f"📈 <b>[AUDIT PAPER TRADING]</b>\n"
            f"{'─' * 28}\n"
            f"Simularea a demonstrat profitabilitate!\n\n"
            f"💰 P&amp;L Total: <b>${pnl:+.2f}</b>\n"
            f"🎯 Win Rate: <b>{wr}%</b> ({stats['wins']}W / {stats['losses']}L)\n"
            f"📊 Trades: {stats['closed_trades']} closed\n"
            f"🏆 Best: ${stats['best_trade']:+.2f} | Worst: ${stats['worst_trade']:+.2f}\n"
            f"⏱ Avg Duration: {stats['avg_duration_min']}min\n"
            f"🔄 Top Pair: {stats['top_pair']}\n"
            f"{'─' * 28}\n"
            f"Propun activarea LIVE trading cu limita de $2/trade.\n"
            f"Foloseste comanda /trading_activate [PIN] pentru a confirma."
        )
    else:
        msg = (
            f"📊 <b>[AUDIT PAPER TRADING]</b>\n"
            f"{'─' * 28}\n"
            f"💰 P&amp;L Total: ${pnl:+.2f}\n"
            f"🎯 Win Rate: {wr}% ({stats['wins']}W / {stats['losses']}L)\n"
            f"📊 Trades: {stats['closed_trades']} closed\n"
            f"{'─' * 28}\n"
            f"{'⚠️ Performanta sub pragul de activare (>55% WR, P&L pozitiv).' if not profitable else ''}\n"
            f"Continua in modul PAPER."
        )

    # Send notification
    sent = await _send_crypto_telegram(msg)

    return {
        "success": True,
        "output": msg.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""),
        "stats": stats,
        "profitable": profitable,
        "notification_sent": sent,
    }
