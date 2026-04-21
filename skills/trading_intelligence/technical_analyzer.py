# -*- coding: utf-8 -*-
"""Trading Intelligence V1.0 — Technical Analyzer.

Calculates per-coin technical indicators:
  - RSI (14-period)
  - Simple Moving Average crossover (7/25)
  - Support/Resistance levels (7d high/low)

Uses CoinGecko price chart data via data_collector.fetch_price_chart().
"""
import asyncio
import logging
from typing import Any

import httpx

from .config import (
    RSI_PERIOD,
    MA_SHORT,
    MA_LONG,
    CHART_DAYS,
    HTTP_TIMEOUT_SECONDS,
)
from .data_collector import fetch_price_chart

logger = logging.getLogger("TradingIntelligence")


# ---------------------------------------------------------------------------
# RSI calculation
# ---------------------------------------------------------------------------
def _calculate_rsi(prices: list[float], period: int = RSI_PERIOD) -> float | None:
    """Calculate RSI from a list of closing prices.

    Returns RSI value 0-100 or None if not enough data.
    """
    if len(prices) < period + 1:
        return None

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

    # Use last `period` deltas for initial averages, then EMA-style
    gains = []
    losses = []
    for d in deltas[-period:]:
        if d > 0:
            gains.append(d)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(d))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)


# ---------------------------------------------------------------------------
# Moving average calculation
# ---------------------------------------------------------------------------
def _simple_ma(prices: list[float], period: int) -> float | None:
    """Calculate simple moving average over the last `period` prices."""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def _ma_crossover_signal(prices: list[float]) -> str:
    """Determine MA crossover signal.

    Returns: BULLISH_CROSS, BEARISH_CROSS, or NO_SIGNAL.
    """
    if len(prices) < MA_LONG + 2:
        return "NO_SIGNAL"

    # Current MA values
    ma_short_now = _simple_ma(prices, MA_SHORT)
    ma_long_now = _simple_ma(prices, MA_LONG)

    # Previous MA values (shift by 1)
    ma_short_prev = _simple_ma(prices[:-1], MA_SHORT)
    ma_long_prev = _simple_ma(prices[:-1], MA_LONG)

    if None in (ma_short_now, ma_long_now, ma_short_prev, ma_long_prev):
        return "NO_SIGNAL"

    # Bullish cross: short crosses above long
    if ma_short_prev <= ma_long_prev and ma_short_now > ma_long_now:
        return "BULLISH_CROSS"

    # Bearish cross: short crosses below long
    if ma_short_prev >= ma_long_prev and ma_short_now < ma_long_now:
        return "BEARISH_CROSS"

    return "NO_SIGNAL"


# ---------------------------------------------------------------------------
# Support / Resistance from 7d data
# ---------------------------------------------------------------------------
def _support_resistance(prices: list[float], window_days: int = 7) -> dict:
    """Calculate simple support/resistance from recent price data.

    Uses the last `window_days` worth of data points.
    CoinGecko typically returns ~24 points/day for 30d charts (hourly).
    """
    # Approximate: 24 data points per day
    points_per_day = max(1, len(prices) // CHART_DAYS)
    window_points = points_per_day * window_days
    recent = prices[-window_points:] if len(prices) > window_points else prices

    if not recent:
        return {"support": 0, "resistance": 0}

    return {
        "support": round(min(recent), 2),
        "resistance": round(max(recent), 2),
    }


# ---------------------------------------------------------------------------
# Per-coin technical analysis
# ---------------------------------------------------------------------------
async def _analyze_coin(
    coin: dict,
    client: httpx.AsyncClient,
) -> dict:
    """Run technical analysis for a single coin."""
    coin_id = coin["id"]
    current_price = coin.get("current_price", 0)

    # Fetch 30-day price chart
    chart_data = await fetch_price_chart(coin_id, days=CHART_DAYS, client=client)

    if not chart_data:
        return {
            "coin_id": coin_id,
            "symbol": coin.get("symbol", ""),
            "current_price": current_price,
            "rsi": None,
            "ma_signal": "NO_DATA",
            "support": 0,
            "resistance": 0,
            "ma_short": None,
            "ma_long": None,
            "trend": "UNKNOWN",
            "error": "No chart data available",
        }

    # Extract just the prices
    prices = [p[1] for p in chart_data]

    # Calculate indicators
    rsi = _calculate_rsi(prices)
    ma_signal = _ma_crossover_signal(prices)
    ma_short = _simple_ma(prices, MA_SHORT)
    ma_long = _simple_ma(prices, MA_LONG)
    sr = _support_resistance(prices)

    # Determine overall trend
    trend = "NEUTRAL"
    if rsi is not None:
        if rsi > 70:
            trend = "OVERBOUGHT"
        elif rsi < 30:
            trend = "OVERSOLD"
        elif ma_signal == "BULLISH_CROSS" or (ma_short and ma_long and ma_short > ma_long):
            trend = "BULLISH"
        elif ma_signal == "BEARISH_CROSS" or (ma_short and ma_long and ma_short < ma_long):
            trend = "BEARISH"

    return {
        "coin_id": coin_id,
        "symbol": coin.get("symbol", ""),
        "current_price": current_price,
        "rsi": rsi,
        "ma_signal": ma_signal,
        "ma_short": round(ma_short, 2) if ma_short else None,
        "ma_long": round(ma_long, 2) if ma_long else None,
        "support": sr["support"],
        "resistance": sr["resistance"],
        "trend": trend,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def analyze_technical(
    market_data: list[dict],
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Run technical analysis for all coins in market_data.

    Args:
        market_data: List of coin dicts from fetch_top_10_coins().

    Returns:
        List of technical analysis results per coin.
    """
    if not market_data:
        return []

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)
        close_client = True

    try:
        # Analyze coins sequentially to respect CoinGecko rate limits
        results = []
        for coin in market_data:
            try:
                result = await _analyze_coin(coin, client)
                results.append(result)
            except Exception as exc:
                logger.error("Technical analysis failed for %s: %s", coin.get("id"), exc)
                results.append({
                    "coin_id": coin["id"],
                    "symbol": coin.get("symbol", ""),
                    "current_price": coin.get("current_price", 0),
                    "rsi": None,
                    "ma_signal": "ERROR",
                    "support": 0,
                    "resistance": 0,
                    "trend": "UNKNOWN",
                    "error": str(exc),
                })

        # Log summary
        trends = {}
        for r in results:
            t = r.get("trend", "UNKNOWN")
            trends[t] = trends.get(t, 0) + 1
        logger.info("Technical analysis complete: %d coins — %s", len(results), trends)

        return results

    finally:
        if close_client:
            await client.aclose()
