# -*- coding: utf-8 -*-
"""Crypto News Broadcaster — V11.10

Sends daily crypto news digest via TradeCrypto Bot (@TradeCrypto13_bot).
Fetches top impact news from the news_cache table in trading_intelligence.db.

Uses TRADE_CRYPTO_BOT_TOKEN and TRADE_CRYPTO_CHAT_ID from keyring.

DB: data/trading_intelligence.db
Table: news_cache (columns: title, source, impact, sentiment, fetched_at, coins)

Usage:
    from crypto_bot.skills.news_broadcaster import broadcast_daily_news
    result = await broadcast_daily_news()
"""
import html as _html
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("godclaw.news_broadcaster")

PROJECT_ROOT = Path("D:/RazAgent_Enterprise")
NEWS_DB = PROJECT_ROOT / "data" / "trading_intelligence.db"

# Impact priority order for sorting
IMPACT_ORDER = {"high": 3, "medium": 2, "low": 1}


def _get_top_news(hours: int = 24, limit: int = 5) -> list[dict]:
    """Fetch top-impact news from the last N hours."""
    try:
        conn = sqlite3.connect(str(NEWS_DB), timeout=5)
        conn.row_factory = sqlite3.Row

        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

        rows = conn.execute(
            "SELECT title, source, impact, sentiment, coins, fetched_at "
            "FROM news_cache "
            "WHERE fetched_at > ? "
            "ORDER BY fetched_at DESC "
            "LIMIT ?",
            (cutoff, limit * 3),  # Fetch extra for dedup + sorting
        ).fetchall()
        conn.close()

        news = []
        seen_titles = set()
        for r in rows:
            title = r["title"] or ""
            # Deduplicate by title similarity
            title_key = title.lower().strip()[:50]
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)

            news.append({
                "title": title,
                "source": r["source"] or "Unknown",
                "impact": r["impact"] or "low",
                "sentiment": r["sentiment"] or "neutral",
                "coins": r["coins"] or "",
                "fetched_at": r["fetched_at"] or "",
            })

        # Sort by impact (high first), then by recency
        news.sort(key=lambda x: (-IMPACT_ORDER.get(x["impact"].lower(), 0), x["fetched_at"]), reverse=False)
        # After sort, high impact items are first because of negative sign
        news.sort(key=lambda x: IMPACT_ORDER.get(x["impact"].lower(), 0), reverse=True)

        return news[:limit]

    except Exception as e:
        logger.error("News cache query failed: %s", e)
        return []


def _get_all_time_news(limit: int = 5) -> list[dict]:
    """Fallback: get most recent news regardless of time window."""
    try:
        conn = sqlite3.connect(str(NEWS_DB), timeout=5)
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            "SELECT title, source, impact, sentiment, coins, fetched_at "
            "FROM news_cache "
            "ORDER BY fetched_at DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()

        return [
            {
                "title": r["title"] or "",
                "source": r["source"] or "Unknown",
                "impact": r["impact"] or "low",
                "sentiment": r["sentiment"] or "neutral",
                "coins": r["coins"] or "",
                "fetched_at": r["fetched_at"] or "",
            }
            for r in rows
        ]
    except Exception as e:
        logger.error("News fallback query failed: %s", e)
        return []


async def _send_crypto_telegram(text: str) -> bool:
    """Send message via TradeCrypto Bot token."""
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


def _impact_emoji(impact: str) -> str:
    """Map impact level to emoji."""
    return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(impact.lower(), "⚪")


def _sentiment_emoji(sentiment: str) -> str:
    """Map sentiment to emoji."""
    s = sentiment.lower()
    if "bull" in s or "positive" in s:
        return "📈"
    if "bear" in s or "negative" in s:
        return "📉"
    return "➡️"


_BRIEF_AUDIT_ACTION = "daily_crypto_brief_sent"
_BRIEF_COOLDOWN_SEC = 12 * 3600  # 12 hours — survives restarts, throttles re-emission


async def broadcast_daily_news(params: dict | None = None) -> dict:
    """Fetch top crypto news and broadcast via TradeCrypto Bot.

    Idempotency guard (V11.11): before sending, consults audit_logs.db for a
    previous `daily_crypto_brief_sent` row within the last 12h. If found, exits
    silently. Prevents the restart-loop-spams-news-5-times-in-an-hour bug.

    Returns:
        dict with success, output, news_count, notification_sent, skipped_reason.
    """
    params = params or {}
    hours = int(params.get("hours", 24))
    limit = int(params.get("limit", 5))
    force = bool(params.get("force", False))  # manual override — e.g. /news in Telegram

    # ── Idempotency gate ──
    if not force:
        try:
            from shared.trading_notify import was_audit_action_sent_within
            if was_audit_action_sent_within(_BRIEF_AUDIT_ACTION, _BRIEF_COOLDOWN_SEC):
                logger.info(
                    "Daily crypto brief: skipped (already sent within last %dh)",
                    _BRIEF_COOLDOWN_SEC // 3600,
                )
                return {
                    "success": True,
                    "output": f"skipped — brief already sent in last {_BRIEF_COOLDOWN_SEC // 3600}h",
                    "news_count": 0,
                    "notification_sent": False,
                    "skipped_reason": "12h_cooldown",
                }
        except Exception as exc:
            # Audit-DB read failure should NOT block an operator-forced send,
            # but normal scheduled runs should lean towards NOT sending if
            # the idempotency check itself is broken — otherwise we risk the
            # original spam bug reappearing silently.
            logger.warning(
                "Daily brief idempotency check failed (%s) — proceeding cautiously",
                exc,
            )

    # Fetch news (24h window, fallback to all-time if empty)
    news = _get_top_news(hours=hours, limit=limit)
    if not news:
        news = _get_all_time_news(limit=limit)

    if not news:
        return {
            "success": True,
            "output": "No crypto news available in cache",
            "news_count": 0,
            "notification_sent": False,
        }

    # Build Telegram message
    ts = datetime.now().strftime("%d %b %H:%M")
    lines = [
        f"📰 <b>[DAILY CRYPTO BRIEF]</b>",
        f"📅 {ts} | Top {len(news)} stiri",
        "─" * 28,
    ]

    for i, n in enumerate(news, 1):
        impact = _impact_emoji(n["impact"])
        sentiment = _sentiment_emoji(n["sentiment"])
        title = _html.escape(n["title"][:100])
        source = _html.escape(n["source"][:20])
        coins = n["coins"][:30] if n["coins"] else ""

        lines.append(f"{i}. {impact} {title}")
        lines.append(f"   {sentiment} {source}" + (f" | {coins}" if coins else ""))

    lines.append("─" * 28)
    msg = "\n".join(lines)

    # Send notification
    sent = await _send_crypto_telegram(msg)

    # Stamp the audit trail only on a successful delivery so a silent network
    # failure doesn't cause the next scheduled run to skip too. The 12h
    # cooldown restarts from the moment the chat actually received the brief.
    if sent:
        try:
            from shared.trading_notify import record_audit_action
            record_audit_action(
                _BRIEF_AUDIT_ACTION,
                {"news_count": len(news), "ts_iso": datetime.now().isoformat()},
            )
        except Exception as exc:
            logger.warning("Failed to record daily brief audit stamp: %s", exc)

    return {
        "success": True,
        "output": msg,
        "news_count": len(news),
        "notification_sent": sent,
    }


async def broadcast_gem_alerts(params: dict | None = None) -> dict:
    """V11.80: Run gem radar sweep and broadcast high-scoring alerts via TradeCrypto Bot.

    Only sends Telegram alerts for gems scoring > 80 (high potential).
    """
    try:
        from crypto_bot.skills.gem_radar import full_gem_sweep

        sweep = await full_gem_sweep()
        if not sweep.get("success"):
            return {"success": False, "output": "Gem sweep failed", "alerts_sent": 0}

        alerts = sweep.get("alerts", [])
        sent_count = 0

        for gem in alerts:
            ev = gem.get("evaluation", {})
            name = _html.escape(gem.get("name", "?")[:50])
            stage = _html.escape(gem.get("stage", "?"))
            score = ev.get("gem_score", 0)
            verdict = _html.escape(str(ev.get("verdict", "?"))[:120])
            risk = _html.escape(str(ev.get("risk", "?")))

            msg = (
                f"💎 <b>[GEM RADAR ALERT]</b>\n"
                f"{'─' * 28}\n"
                f"📊 Proiect: <b>{name}</b>\n"
                f"🏷️ Status: {stage}\n"
                f"⭐ Scor Potential: <b>{score}/100</b>\n"
                f"📋 Analiza: {verdict}\n"
                f"⚠️ Risc: <b>{risk}</b>\n"
                f"{'─' * 28}\n"
                f"🔒 Nu intra cu sume mari! Max $7/trade."
            )
            if await _send_crypto_telegram(msg):
                sent_count += 1

        # Also send the full summary if there were scored gems
        scored = sweep.get("scored_gems", [])
        if scored and not alerts:
            # No high-scoring gems, but send a summary anyway
            summary = sweep.get("output", "No gems scored")
            # Don't send if no interesting gems
            pass

        return {
            "success": True,
            "output": f"Gem Radar: {len(scored)} scanned, {len(alerts)} alerts sent",
            "scored_count": len(scored),
            "alerts_sent": sent_count,
            "alerts": alerts,
        }
    except Exception as e:
        return {"success": False, "output": f"Gem broadcast error: {e}", "alerts_sent": 0}
