# -*- coding: utf-8 -*-
"""Trading Intelligence V1.0 — Telegram Report Formatter.

Generates HTML-formatted reports for Telegram (parse_mode="HTML"):
  - Market Overview (Top 10 prices + 24h change)
  - Fear & Greed index
  - Top 5 News (scored by impact)
  - Predictions with confidence
  - Trade suggestions
  - Risk alerts
"""
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("TradingIntelligence")

# ---------------------------------------------------------------------------
# Emoji helpers
# ---------------------------------------------------------------------------
_DIRECTION_EMOJI = {
    "BULLISH": "\U0001f7e2",    # green circle
    "BEARISH": "\U0001f534",    # red circle
    "NEUTRAL": "\u26aa",        # white circle
}

_IMPACT_EMOJI = {
    "HIGH": "\U0001f525",       # fire
    "MEDIUM": "\u26a1",         # lightning
    "LOW": "\U0001f4a4",        # zzz
}

_SENTIMENT_EMOJI = {
    "POSITIVE": "\U0001f44d",
    "NEGATIVE": "\U0001f44e",
    "NEUTRAL": "\u2796",
}

_RISK_EMOJI = {
    "HIGH": "\U0001f6a8",
    "MEDIUM": "\u26a0\ufe0f",
    "LOW": "\u2705",
}


def _format_price(price: float) -> str:
    """Format price with appropriate decimals."""
    if price >= 1000:
        return f"${price:,.0f}"
    elif price >= 1:
        return f"${price:,.2f}"
    elif price >= 0.01:
        return f"${price:.4f}"
    else:
        return f"${price:.6f}"


def _format_change(change: float | None) -> str:
    """Format percentage change with emoji."""
    if change is None:
        return "N/A"
    emoji = "\U0001f4c8" if change >= 0 else "\U0001f4c9"  # chart up/down
    return f"{emoji} {change:+.1f}%"


def _fear_greed_emoji(value: int) -> str:
    """Emoji for Fear & Greed index value."""
    if value <= 25:
        return "\U0001f630"  # anxious face
    elif value <= 45:
        return "\U0001f628"  # fearful face
    elif value <= 55:
        return "\U0001f610"  # neutral face
    elif value <= 75:
        return "\U0001f60f"  # smirk
    else:
        return "\U0001f911"  # money face


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------
def _section_header(title: str, emoji: str = "") -> str:
    return f"\n{emoji} <b>{title}</b>\n{'=' * 28}"


def _section_market_overview(market_data: list[dict]) -> str:
    """Top 10 coins with prices and 24h change."""
    lines = [_section_header("MARKET OVERVIEW", "\U0001f4ca")]

    for coin in market_data[:10]:
        symbol = coin.get("symbol", "?")
        price = _format_price(coin.get("current_price", 0))
        change = _format_change(coin.get("price_change_24h"))
        rank = coin.get("market_cap_rank", "?")

        lines.append(f"#{rank} <b>{symbol}</b>: {price} {change}")

    return "\n".join(lines)


def _section_fear_greed(fear_greed: dict) -> str:
    """Fear & Greed index display."""
    value = fear_greed.get("value", 50)
    classification = fear_greed.get("classification", "Neutral")
    emoji = _fear_greed_emoji(value)

    lines = [_section_header("FEAR & GREED INDEX", "\U0001f3af")]
    lines.append(f"{emoji} <b>{value}/100</b> — {classification}")

    # Visual bar
    filled = value // 5
    bar = "\u2588" * filled + "\u2591" * (20 - filled)
    lines.append(f"<code>[{bar}]</code>")

    return "\n".join(lines)


def _section_defi_tvl(defi_tvl: dict) -> str:
    """DeFi TVL section."""
    tvl = defi_tvl.get("total_tvl_usd", 0)
    if tvl <= 0:
        return ""
    tvl_b = tvl / 1e9
    lines = [_section_header("DEFI TVL", "\U0001f3e6")]
    lines.append(f"Total Value Locked: <b>${tvl_b:,.2f}B</b>")
    return "\n".join(lines)


def _section_news(scored_news: list[dict]) -> str:
    """Top 5 news items sorted by impact."""
    if not scored_news:
        return ""

    # Sort by impact priority
    priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    sorted_news = sorted(scored_news, key=lambda x: priority.get(x.get("impact", "LOW"), 2))

    lines = [_section_header("TOP NEWS", "\U0001f4f0")]

    for item in sorted_news[:5]:
        impact = item.get("impact", "LOW")
        sentiment = item.get("sentiment", "NEUTRAL")
        title = item.get("title", "")[:65]
        source = item.get("source", "")
        emoji_i = _IMPACT_EMOJI.get(impact, "")
        emoji_s = _SENTIMENT_EMOJI.get(sentiment, "")

        lines.append(f"{emoji_i}{emoji_s} <i>{title}</i>")
        lines.append(f"   \u2514 {source} | {impact} impact")

    return "\n".join(lines)


def _section_predictions(predictions: list[dict]) -> str:
    """Predictions with confidence bars."""
    if not predictions:
        return ""

    lines = [_section_header("PREDICTIONS", "\U0001f52e")]

    # Group by coin, show 24h prediction
    seen_coins: set[str] = set()
    for pred in predictions:
        coin = pred.get("coin", "")
        if coin in seen_coins:
            continue
        if pred.get("timeframe") != "24h":
            continue
        seen_coins.add(coin)

        symbol = pred.get("symbol", "?")
        direction = pred.get("direction", "NEUTRAL")
        confidence = pred.get("confidence", 0)
        target_low = _format_price(pred.get("target_low", 0))
        target_high = _format_price(pred.get("target_high", 0))
        emoji = _DIRECTION_EMOJI.get(direction, "\u26aa")

        # Confidence bar
        filled = confidence // 10
        bar = "\u2588" * filled + "\u2591" * (10 - filled)

        lines.append(
            f"{emoji} <b>{symbol}</b> {direction} ({confidence}%)"
        )
        lines.append(f"   <code>[{bar}]</code> {target_low}-{target_high}")

        # Show 72h prediction if exists
        pred_72h = next(
            (p for p in predictions if p.get("coin") == coin and p.get("timeframe") == "72h"),
            None,
        )
        if pred_72h:
            d72 = pred_72h.get("direction", "?")
            c72 = pred_72h.get("confidence", 0)
            e72 = _DIRECTION_EMOJI.get(d72, "\u26aa")
            lines.append(f"   72h: {e72} {d72} ({c72}%)")

    return "\n".join(lines)


def _section_suggestions(suggestions: list[dict]) -> str:
    """Trade suggestions with details."""
    if not suggestions:
        lines = [_section_header("TRADE SUGGESTIONS", "\U0001f4b0")]
        lines.append("\u274c No high-confidence trades this cycle.")
        return "\n".join(lines)

    lines = [_section_header("TRADE SUGGESTIONS", "\U0001f4b0")]

    for i, sug in enumerate(suggestions, 1):
        action = sug.get("action", "?")
        symbol = sug.get("symbol", "?")
        amount = sug.get("amount_usd", 0)
        entry = _format_price(sug.get("entry_price", 0))
        sl = _format_price(sug.get("stop_loss", 0))
        tp = _format_price(sug.get("take_profit", 0))
        confidence = sug.get("confidence", 0)
        rr = sug.get("risk_reward_ratio", 0)

        action_emoji = "\U0001f7e2" if action == "BUY" else "\U0001f534"

        lines.append(f"\n{action_emoji} <b>#{i} {action} {symbol}</b>")
        lines.append(f"   Amount: <b>${amount:.2f}</b> @ {entry}")
        lines.append(f"   SL: {sl} | TP: {tp}")
        lines.append(f"   Confidence: {confidence}% | R/R: {rr:.1f}:1")
        lines.append(f"   \u2514 {sug.get('reasoning', '')[:80]}")

    return "\n".join(lines)


def _section_aggregate_sentiment(aggregate: dict | None) -> str:
    """Overall market sentiment summary."""
    if not aggregate:
        return ""

    lines = [_section_header("MARKET SENTIMENT", "\U0001f9e0")]

    sentiment = aggregate.get("overall_sentiment", "NEUTRAL")
    confidence = aggregate.get("confidence", 50)
    risk = aggregate.get("risk_level", "MEDIUM")
    drivers = aggregate.get("key_drivers", [])

    s_emoji = _DIRECTION_EMOJI.get(sentiment, "\u26aa")
    r_emoji = _RISK_EMOJI.get(risk, "\u26a0\ufe0f")

    lines.append(f"{s_emoji} Overall: <b>{sentiment}</b> (confidence {confidence}%)")
    lines.append(f"{r_emoji} Risk Level: <b>{risk}</b>")

    if drivers:
        lines.append("Key Drivers:")
        for d in drivers[:3]:
            lines.append(f"  \u2022 {d}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def format_telegram_report(
    market_data: list[dict],
    fear_greed: dict,
    defi_tvl: dict | None = None,
    scored_news: list[dict] | None = None,
    predictions: list[dict] | None = None,
    suggestions: list[dict] | None = None,
    aggregate_sentiment: dict | None = None,
    cycle_duration_seconds: float = 0,
    exchange_balances: dict[str, float] | None = None,
) -> str:
    """Format a complete Telegram HTML report.

    Returns HTML string suitable for Telegram parse_mode="HTML".
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    sections = [
        f"\U0001f916 <b>TRADING INTELLIGENCE REPORT</b>",
        f"<i>{now}</i>",
    ]

    # Exchange balances (multi-exchange)
    if exchange_balances:
        bal_lines = ["\n\U0001f4b0 <b>EXCHANGE BALANCES</b>"]
        total = 0.0
        for exchange, balance in exchange_balances.items():
            if exchange != "total":
                bal_lines.append(f"  \u2022 {exchange.title()}: <code>${balance:.2f}</code> USDT")
                total += balance
        bal_lines.append(f"  <b>Total: ${total:.2f} USDT</b>")
        sections.append("\n".join(bal_lines))

    # Market overview
    if market_data:
        sections.append(_section_market_overview(market_data))

    # Fear & Greed
    if fear_greed:
        sections.append(_section_fear_greed(fear_greed))

    # DeFi TVL
    if defi_tvl:
        tvl_section = _section_defi_tvl(defi_tvl)
        if tvl_section:
            sections.append(tvl_section)

    # Aggregate sentiment
    if aggregate_sentiment:
        sections.append(_section_aggregate_sentiment(aggregate_sentiment))

    # News
    if scored_news:
        sections.append(_section_news(scored_news))

    # Predictions
    if predictions:
        sections.append(_section_predictions(predictions))

    # Trade suggestions
    sections.append(_section_suggestions(suggestions or []))

    # Footer
    footer_parts = ["\n\u2500" * 14]
    if cycle_duration_seconds > 0:
        footer_parts.append(f"\u23f1 Cycle duration: {cycle_duration_seconds:.1f}s")
    footer_parts.append("\U0001f501 Next cycle in 3h")
    footer_parts.append("<i>Trading Intelligence V1.0 | RazAgent Enterprise</i>")
    sections.append("\n".join(footer_parts))

    report = "\n".join(sections)

    # Telegram message limit is 4096 chars
    if len(report) > 4096:
        # Truncate news section first
        report_short = report[:4000] + "\n\n<i>... report truncated (4096 char limit)</i>"
        return report_short

    return report
