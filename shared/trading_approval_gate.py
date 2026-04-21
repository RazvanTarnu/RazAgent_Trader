# -*- coding: utf-8 -*-
"""Trading Approval Gate — V10.47

Dedicated approval gate for crypto trading, separate from CEO approval gate.
Uses @TradeCrypto13_bot token and TRADE_CRYPTO_CHAT_ID.

Key differences from shared/approval_gate.py:
  - Token: TRADE_CRYPTO_BOT_TOKEN (not TELEGRAM_TOKEN)
  - Chat ID: TRADE_CRYPTO_CHAT_ID (not ADMIN_CHAT_ID)
  - Timeout: 30 minutes (not 200 — trading is time-sensitive)
  - Timeout → REJECT (fail-closed)
  - Trading-specific message format
  - Callback pattern: trade_approve_/trade_reject_ (not v28_)
"""
import os
import uuid
import time
import asyncio
import logging
from typing import Optional

from shared.approval_base import ApprovalGateBase
from shared.config import DATA_DIR



try:
    from shared.version import APP_VERSION
except ImportError:
    APP_VERSION = "V10.47"

logger = logging.getLogger("godclaw.trading_approval_gate")  # TODO-TECHDEBT: rename logger prefix to "godclaw"

TRADING_APPROVAL_TIMEOUT: int = 1800   # 30 minutes
TRADING_RETRY_INTERVAL: int = 300      # 5 minutes


def _get_trading_token() -> str:
    """Resolve TRADE_CRYPTO_BOT_TOKEN from env or keyring."""
    from shared.keyring_loader import get_credential
    token = os.environ.get("TRADE_CRYPTO_BOT_TOKEN") or get_credential("TRADE_CRYPTO_BOT_TOKEN")
    if not token:
        raise RuntimeError("TRADE_CRYPTO_BOT_TOKEN not found")
    return token


def _get_trading_chat_id() -> str:
    """Resolve TRADE_CRYPTO_CHAT_ID from env or keyring."""
    from shared.keyring_loader import get_credential
    chat_id = os.environ.get("TRADE_CRYPTO_CHAT_ID") or get_credential("TRADE_CRYPTO_CHAT_ID")
    if not chat_id:
        raise RuntimeError("TRADE_CRYPTO_CHAT_ID not found")
    return chat_id


def _audit_log(action: str, details: str, status: str = "ok"):
    """Best-effort audit log entry."""
    try:
        import sqlite3
        from pathlib import Path
        db = DATA_DIR / "audit_logs.db"
        conn = sqlite3.connect(str(db), timeout=5)
        conn.execute("PRAGMA busy_timeout=3000")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now')),
                action_type TEXT, agent_id TEXT, target TEXT,
                details TEXT, status TEXT DEFAULT 'ok'
            )
        """)
        conn.execute(
            "INSERT INTO audit_actions (action_type, agent_id, target, details, status) "
            "VALUES (?,?,?,?,?)",
            (action, "TRADING_GATE", "trade_approval", details[:1000], status),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


class TradingApprovalGate(ApprovalGateBase["TradingApprovalGate"]):
    """Singleton trading approval gate with 30-min timeout.

    TIMEOUT = REJECT (fail-closed for trading).
    """

    _instance: Optional["TradingApprovalGate"] = None
    _pending: dict[str, asyncio.Event]
    _results: dict[str, str]
    _retry_tasks: dict[str, asyncio.Task]

    def __init__(self):
        super().__init__()

    @classmethod
    def instance(cls) -> "TradingApprovalGate":
        if cls._instance is None:
            cls._instance = cls()
            logger.info("TradingApprovalGate singleton created")
        return cls._instance

    async def require_approval(
        self,
        pair: str,
        side: str,
        size_usd: float,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        sl_pct: float = 2.0,
        tp_pct: float = 4.0,
        rsi: float = 50.0,
        market_condition: str = "neutral",
        paper_mode: bool = True,
        metadata: dict | None = None,
    ) -> dict:
        """Request human approval for a trade via @TradeCrypto13_bot.

        Returns dict with: status (APPROVED/REJECTED/TIMEOUT_REJECTED),
        request_id, waited_seconds.
        """
        request_id = uuid.uuid4().hex[:12]
        event = asyncio.Event()
        self._pending[request_id] = event

        mode_icon = "📄" if paper_mode else "⚡"
        mode_text = "PAPER 📄" if paper_mode else "LIVE ⚡"
        side_icon = "🟢 BUY" if side.lower() == "buy" else "🔴 SELL"

        text = (
            f"{mode_icon} <b>TRADE PROPUS</b>\n"
            f"{'─' * 28}\n"
            f"{side_icon} <b>{pair}</b>\n"
            f"💵 Size:  ${size_usd:.2f}\n"
            f"📍 Entry: ${entry_price:,.4f}\n"
            f"🛑 SL:    ${sl_price:,.4f} (-{sl_pct:.0f}%)\n"
            f"🎯 TP:    ${tp_price:,.4f} (+{tp_pct:.0f}%)\n"
            f"📊 RSI:   {rsi:.1f}\n"
            f"🌡 Market: {market_condition}\n"
            f"{'─' * 28}\n"
            f"Mode: <b>{mode_text}</b>\n"
            f"⏱ Timeout: {TRADING_APPROVAL_TIMEOUT // 60} min → auto-REJECT\n"
            f"ID: <code>{request_id}</code>"
        )

        _audit_log("trading_approval_requested", f"pair={pair} side={side} size=${size_usd}")

        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ APPROVE", callback_data=f"trade_approve_{request_id}"),
                    InlineKeyboardButton("❌ REJECT", callback_data=f"trade_reject_{request_id}"),
                ],
            ])
            await self._send_message(text, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Failed to send trading approval to Telegram: {e}")

        # Push notification for background mobile alerts
        try:
            from shared.push_notifications import send_approval_push
            self._fire_and_forget(send_approval_push(
                title=f"Trade: {side.upper()} {pair}",
                body=f"${size_usd:.2f} | Entry: ${entry_price:,.4f} | {mode_text}",
                data={"request_id": request_id, "type": "trading",
                      "pair": pair, "side": side, "size_usd": str(size_usd)},
            ))
        except Exception:
            pass

        # Start reminder loop
        self._start_reminder(request_id, self._reminder_loop(request_id, pair, side))

        # Wait for resolution or timeout
        start_time = time.monotonic()
        result_status = "TIMEOUT_REJECTED"

        try:
            await asyncio.wait_for(event.wait(), timeout=TRADING_APPROVAL_TIMEOUT)
            result_status = self._results.get(request_id, "TIMEOUT_REJECTED")
        except asyncio.TimeoutError:
            result_status = "TIMEOUT_REJECTED"
            logger.warning(f"Trading approval TIMEOUT — id={request_id}")
            _audit_log("trading_approval_timeout", f"pair={pair} side={side}", "timeout")
            await self._send_message(
                f"⏱ <b>TIMEOUT</b> — Trade {pair} {side} auto-RESPINS\n"
                f"ID: <code>{request_id}</code>",
            )
        finally:
            waited = round(time.monotonic() - start_time, 2)
            self._cleanup_request(request_id)

            _audit_log(
                "trading_approval_resolved",
                f"pair={pair} status={result_status} waited={waited}s",
            )

        return {"status": result_status, "request_id": request_id, "waited_seconds": waited}

    def resolve(self, request_id: str, decision: str):
        """Unblock a pending approval. Overrides base to add audit log."""
        super().resolve(request_id, decision)
        _audit_log("trading_approval_resolved_manual", f"id={request_id} decision={decision}")

    async def _reminder_loop(self, request_id: str, pair: str, side: str):
        """Send reminders every 5 min until resolved or timeout."""
        elapsed = 0
        while elapsed < TRADING_APPROVAL_TIMEOUT:
            await asyncio.sleep(TRADING_RETRY_INTERVAL)
            elapsed += TRADING_RETRY_INTERVAL
            if request_id not in self._pending:
                return
            remaining = (TRADING_APPROVAL_TIMEOUT - elapsed) // 60
            await self._send_message(
                f"⏳ <b>REMINDER</b> — Trade {pair} {side} așteaptă aprobare\n"
                f"⏱ {remaining} min rămase — auto-REJECT la timeout\n"
                f"ID: <code>{request_id}</code>",
            )

    async def _send_message(self, text: str, reply_markup=None):
        """Send via Telegram Bot API (standalone, no PTB app needed)."""
        import httpx
        token = _get_trading_token()
        chat_id = _get_trading_chat_id()
        payload = {
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup.to_json()
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
            )
            if r.status_code != 200:
                logger.warning(f"Telegram send failed: {r.status_code} {r.text[:200]}")
