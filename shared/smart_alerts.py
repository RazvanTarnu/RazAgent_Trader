# -*- coding: utf-8 -*-
"""Smart Alerts — V38.50

Executive-level Telegram filtering. Only HIGH and CRITICAL priority
messages reach the CEO. Everything else goes to terminal + ChromaDB only.

Priority levels:
  LOW:      Terminal log only (trends scanned, pipeline steps, routine checks)
  NORMAL:   Terminal + ChromaDB memory (video generated, trade executed)
  HIGH:     Terminal + ChromaDB + CEO Telegram (ghost trigger, video published)
  CRITICAL: Terminal + ChromaDB + CEO Telegram + TradeCrypto Bot (CUDA alert, trade stop-loss)

Usage:
    from shared.smart_alerts import alert

    alert("Video published: Bitcoin News", priority="HIGH",
          category="ghost_mode", metadata={"topic": "Bitcoin"})
"""
import html as _html
import logging
from datetime import datetime

logger = logging.getLogger("godclaw.smart_alerts")

# Only these priorities reach Telegram
TELEGRAM_PRIORITIES = {"HIGH", "CRITICAL"}


async def _send_to_ceo_telegram(text: str) -> bool:
    """Send to CEO Bot (@HulkClaw_bot)."""
    try:
        from shared.keyring_loader import get_credential
        import httpx

        token = get_credential("TELEGRAM_TOKEN")
        chat_id = get_credential("ADMIN_CHAT_ID")
        if not token or not chat_id:
            return False

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML", "disable_web_page_preview": True},
            )
            return r.status_code == 200
    except Exception as e:
        logger.debug("CEO Telegram failed: %s", e)
        return False


async def _send_to_crypto_telegram(text: str) -> bool:
    """Send to TradeCrypto Bot (@TradeCrypto13_bot)."""
    try:
        from shared.keyring_loader import get_credential
        import httpx

        token = get_credential("TRADE_CRYPTO_BOT_TOKEN")
        chat_id = get_credential("TRADE_CRYPTO_CHAT_ID")
        if not token or not chat_id:
            return False

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML", "disable_web_page_preview": True},
            )
            return r.status_code == 200
    except Exception as e:
        logger.debug("Crypto Telegram failed: %s", e)
        return False


async def alert(
    message: str,
    priority: str = "NORMAL",
    category: str = "system",
    metadata: dict | None = None,
    crypto_bot: bool = False,
) -> dict:
    """Unified smart alert with priority-based routing.

    Args:
        message: Alert text (can include HTML for Telegram).
        priority: LOW | NORMAL | HIGH | CRITICAL
        category: ChromaDB memory category.
        metadata: Additional metadata for memory storage.
        crypto_bot: If True, also sends to TradeCrypto bot (for trading alerts).

    Returns:
        dict with logged (bool), memorized (bool), telegrammed (bool).
    """
    priority = priority.upper()
    result = {"logged": True, "memorized": False, "telegrammed": False, "priority": priority}

    # Always log to terminal
    prefix = {"LOW": "ℹ️", "NORMAL": "📋", "HIGH": "⚡", "CRITICAL": "🚨"}.get(priority, "📋")
    clean_msg = _html.unescape(message.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""))
    logger.info("[ALERT:%s] %s", priority, clean_msg[:120])

    # NORMAL+ → Save to ChromaDB memory
    if priority != "LOW":
        try:
            from shared.memory_manager import remember
            importance = {"NORMAL": 5, "HIGH": 7, "CRITICAL": 9}.get(priority, 5)
            remember(clean_msg[:200], category=category, metadata=metadata, importance=importance)
            result["memorized"] = True
        except Exception:
            pass

    # HIGH/CRITICAL → Send to CEO Telegram
    if priority in TELEGRAM_PRIORITIES:
        ts = datetime.now().strftime("%d %b %H:%M")
        formatted = (
            f"{prefix} <b>[{priority}]</b>\n"
            f"{'─' * 28}\n"
            f"{message}\n"
            f"{'─' * 28}\n"
            f"🕐 {ts}"
        )
        sent = await _send_to_ceo_telegram(formatted)
        result["telegrammed"] = sent

        # CRITICAL trading alerts → also TradeCrypto bot
        if priority == "CRITICAL" and crypto_bot:
            await _send_to_crypto_telegram(formatted)

    return result


def alert_sync(message: str, priority: str = "NORMAL", **kwargs):
    """Synchronous wrapper for alert() — for use in non-async code."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(alert(message, priority, **kwargs))
        else:
            loop.run_until_complete(alert(message, priority, **kwargs))
    except Exception:
        # Last resort — just log it
        logger.info("[ALERT:%s] %s", priority, message[:120])
