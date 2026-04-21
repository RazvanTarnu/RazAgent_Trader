"""Trading Daily Self-Improvement Loop — V10.44

Runs at DAILY_REVIEW_HOUR (00:00) to analyze recent trades,
identify patterns, and propose strategy adjustments.

Uses Ollama LOCAL only (financial data stays private).
"""
import asyncio
import logging
import os
from datetime import datetime

import httpx

from shared.config import OLLAMA_MODEL, OLLAMA_URL

logger = logging.getLogger("godclaw.trading_improvement")


async def run_daily_review() -> dict:
    """Analyze recent trades and propose strategy adjustments.

    Called by autonomous_watchdog at DAILY_REVIEW_HOUR.
    Returns dict with analysis results.
    """
    from shared.trade_journal import get_daily_stats, get_lessons, get_pattern_analysis
    from shared.binance_live_config import PAPER_MODE

    # Gather data
    stats_7d = get_daily_stats(days=7)
    stats_30d = get_daily_stats(days=30)
    lessons = get_lessons(n=10)
    patterns = get_pattern_analysis(days=30)

    if stats_30d["trades"] < 3:
        return {"status": "skipped", "reason": "Not enough trades for analysis (need >= 3)"}

    # Build LLM prompt
    mode = "PAPER" if PAPER_MODE else "LIVE"
    prompt = (
        f"Analizeaza aceste trade-uri din ultimele 30 zile ({mode} MODE).\n\n"
        f"STATISTICI 7 ZILE:\n"
        f"  Trades: {stats_7d['trades']}, Win Rate: {stats_7d['win_rate']}%\n"
        f"  P&L: ${stats_7d['pnl_usd']}, Best: ${stats_7d['best']}, Worst: ${stats_7d['worst']}\n\n"
        f"STATISTICI 30 ZILE:\n"
        f"  Trades: {stats_30d['trades']}, Win Rate: {stats_30d['win_rate']}%\n"
        f"  P&L total: ${stats_30d['pnl_usd']}\n\n"
        f"PATTERN-URI per pair:\n"
    )
    for pair, data in patterns.get("by_pair", {}).items():
        wr = round(data["wins"] / data["trades"] * 100, 1) if data["trades"] > 0 else 0
        prompt += f"  {pair}: {data['trades']} trades, {wr}% win, ${data['pnl']:.2f} P&L\n"

    if lessons:
        prompt += f"\nLECTII ANTERIOARE:\n"
        for i, lesson in enumerate(lessons[:5], 1):
            prompt += f"  {i}. {lesson[:200]}\n"

    prompt += (
        "\nRaspunde cu EXACT:\n"
        "1. Top 3 pattern-uri identificate (1 linie fiecare)\n"
        "2. Top 3 greseli repetate (1 linie fiecare)\n"
        "3. 3 ajustari concrete pentru saptamana urmatoare "
        "(cu valori RSI, timeframe, size specifice)\n"
    )

    # Call Ollama LOCAL (financial data stays private)
    analysis = "LLM analysis unavailable"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_ctx": 4096, "temperature": 0.3},
                },
            )
            if resp.status_code == 200:
                analysis = resp.json().get("response", "No analysis")[:3000]
    except Exception as e:
        logger.warning(f"Daily review LLM call failed: {e}")

    # Save daily review to journal DB
    try:
        from shared.trade_journal import _get_conn
        conn = _get_conn()
        conn.execute(
            """INSERT INTO daily_reviews
            (period_days, trade_count, win_rate, total_pnl,
             best_trade_pnl, worst_trade_pnl, insights, adjustments)
            VALUES (?,?,?,?,?,?,?,?)""",
            (30, stats_30d["trades"], stats_30d["win_rate"],
             stats_30d["pnl_usd"], stats_30d["best"], stats_30d["worst"],
             analysis[:2000], ""),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to save daily review: {e}")

    # Send Telegram notification
    await _notify_telegram(stats_7d, analysis)

    return {
        "status": "completed",
        "stats_7d": stats_7d,
        "stats_30d": stats_30d,
        "analysis_length": len(analysis),
        "mode": mode,
    }


async def _notify_telegram(stats: dict, analysis: str):
    """Send daily trading review to Telegram admin."""
    try:
        admin_id = os.environ.get("ADMIN_CHAT_ID")
        token = os.environ.get("TELEGRAM_TOKEN")
        if not admin_id or not token:
            return

        from shared.binance_live_config import PAPER_MODE
        mode = "PAPER" if PAPER_MODE else "LIVE"

        # Extract first 3 insights from analysis
        lines = [l.strip() for l in analysis.split("\n") if l.strip()]
        insights = "\n".join(lines[:6]) if lines else "No insights"

        msg = (
            f"📊 Trading Review {datetime.now().strftime('%Y-%m-%d')}\n"
            f"💰 P&L 7d: ${stats['pnl_usd']} | Win Rate: {stats['win_rate']}%\n"
            f"📈 Best: ${stats['best']} | 📉 Worst: ${stats['worst']}\n"
            f"🔄 Mode: {mode}\n\n"
            f"🧠 Insights:\n{insights[:500]}"
        )

        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": int(admin_id), "text": msg},
            )
    except Exception as e:
        logger.warning(f"Telegram review notification failed: {e}")
