# -*- coding: utf-8 -*-
"""Price Action Analyzer — V11.20

Compresses 730 rows of daily OHLCV into a compact macro-summary
that fits within qwen3:30b's 8192 token context window.

All calculations are pure Python math — no LLM calls, no external libs.

Output: ~200 tokens of structured JSON containing:
  - macro_trend (Bullish/Bearish based on SMA50 vs SMA200)
  - 2yr_high, 2yr_low
  - sma50, sma200
  - current_price
  - distance_from_ath_pct, distance_from_atl_pct
  - last_14_days (array of close prices for recent momentum)

Usage:
    from legacy.trading_intelligence_v1.price_action_analyzer import get_macro_summary
    summary = get_macro_summary("BTCUSDT")
"""
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("TradingIntelligence.price_action")

DB_PATH = Path("D:/RazAgent_Enterprise/data/trading_intelligence.db")


def _fetch_closes(symbol: str) -> list[tuple[int, float]]:
    """Fetch all (timestamp, close) pairs for a symbol, ordered by time."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        rows = conn.execute(
            "SELECT timestamp, close FROM daily_ohlcv "
            "WHERE symbol = ? ORDER BY timestamp ASC",
            (symbol.upper(),),
        ).fetchall()
        conn.close()
        return rows
    except Exception as e:
        logger.error("Failed to fetch closes for %s: %s", symbol, e)
        return []


def _fetch_highs_lows(symbol: str) -> tuple[float, float]:
    """Fetch 2-year high and low from stored OHLCV data."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        row = conn.execute(
            "SELECT MAX(high), MIN(low) FROM daily_ohlcv WHERE symbol = ?",
            (symbol.upper(),),
        ).fetchone()
        conn.close()
        if row and row[0] is not None:
            return float(row[0]), float(row[1])
    except Exception as e:
        logger.error("Failed to fetch highs/lows for %s: %s", symbol, e)
    return 0.0, 0.0


def _sma(closes: list[float], period: int) -> float | None:
    """Calculate Simple Moving Average for the last N values."""
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 2)


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """Calculate RSI (Relative Strength Index) from close prices."""
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(-period, 0)]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]

    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def get_macro_summary(symbol: str) -> dict | None:
    """Generate a compact macro-summary for LLM context injection.

    Returns a dict with ~200 tokens of structured data, or None if
    insufficient data is available.

    Keys:
        symbol, macro_trend, sma50, sma200, current_price,
        two_yr_high, two_yr_low, distance_from_high_pct,
        distance_from_low_pct, rsi_14, last_14_days,
        golden_cross (bool), death_cross (bool),
        days_of_data, volatility_30d_pct
    """
    rows = _fetch_closes(symbol)
    if len(rows) < 50:
        logger.warning("Insufficient OHLCV data for %s: %d rows (need 50+)", symbol, len(rows))
        return None

    closes = [r[1] for r in rows]
    current_price = closes[-1]

    # Moving averages
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)

    # Macro trend determination
    if sma50 is not None and sma200 is not None:
        if sma50 > sma200:
            macro_trend = "BULLISH"
        elif sma50 < sma200:
            macro_trend = "BEARISH"
        else:
            macro_trend = "NEUTRAL"
    else:
        macro_trend = "INSUFFICIENT_DATA"

    # Golden/Death cross detection (SMA50 crossing SMA200)
    golden_cross = False
    death_cross = False
    if len(closes) >= 201 and sma50 is not None and sma200 is not None:
        prev_sma50 = _sma(closes[:-1], 50)
        prev_sma200 = _sma(closes[:-1], 200)
        if prev_sma50 and prev_sma200:
            if prev_sma50 <= prev_sma200 and sma50 > sma200:
                golden_cross = True
            elif prev_sma50 >= prev_sma200 and sma50 < sma200:
                death_cross = True

    # 2-year high/low
    two_yr_high, two_yr_low = _fetch_highs_lows(symbol)

    # Distance from extremes
    dist_high = round(((current_price - two_yr_high) / two_yr_high) * 100, 1) if two_yr_high > 0 else 0
    dist_low = round(((current_price - two_yr_low) / two_yr_low) * 100, 1) if two_yr_low > 0 else 0

    # RSI
    rsi_14 = _rsi(closes)

    # 30-day volatility (standard deviation of daily returns)
    volatility = 0.0
    if len(closes) >= 31:
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(-30, 0)]
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        volatility = round((variance ** 0.5) * 100, 2)  # as percentage

    # Last 14 days (compact array for recent momentum)
    last_14 = [round(c, 2) for c in closes[-14:]]

    return {
        "symbol": symbol.upper(),
        "macro_trend": macro_trend,
        "sma50": sma50,
        "sma200": sma200,
        "current_price": round(current_price, 2),
        "two_yr_high": round(two_yr_high, 2),
        "two_yr_low": round(two_yr_low, 2),
        "distance_from_high_pct": dist_high,
        "distance_from_low_pct": dist_low,
        "rsi_14": rsi_14,
        "golden_cross": golden_cross,
        "death_cross": death_cross,
        "last_14_days": last_14,
        "days_of_data": len(closes),
        "volatility_30d_pct": volatility,
    }


def format_macro_for_prompt(summary: dict) -> str:
    """Format macro summary as a compact text block for LLM prompt injection.

    Keeps token count under ~150 tokens.
    """
    if not summary:
        return "MACRO DATA: Not available (insufficient historical data)"

    cross_signal = ""
    if summary.get("golden_cross"):
        cross_signal = " | ⚡ GOLDEN CROSS (bullish reversal)"
    elif summary.get("death_cross"):
        cross_signal = " | 💀 DEATH CROSS (bearish reversal)"

    return (
        f"MACRO PRICE ACTION ({summary['days_of_data']} days):\n"
        f"- Macro Trend: {summary['macro_trend']}{cross_signal}\n"
        f"- SMA50: ${summary['sma50']:,.2f} | SMA200: ${summary['sma200']:,.2f}\n"
        f"- 2yr High: ${summary['two_yr_high']:,.2f} ({summary['distance_from_high_pct']:+.1f}% from current)\n"
        f"- 2yr Low: ${summary['two_yr_low']:,.2f} ({summary['distance_from_low_pct']:+.1f}% from current)\n"
        f"- RSI(14): {summary.get('rsi_14', 'N/A')}\n"
        f"- 30d Volatility: {summary['volatility_30d_pct']}%\n"
        f"- Last 14d closes: {summary['last_14_days']}"
    )
