# -*- coding: utf-8 -*-
"""Trading Intelligence V1.0 — Trade Suggester.

Filters predictions by confidence >= 75% and generates actionable trade suggestions
with entry price, stop loss, and take profit levels.

HARD LIMIT: Max $50 per trade.
"""
import logging
from typing import Any

from .config import (
    MAX_TRADE_AMOUNT_USD,
    MIN_CONFIDENCE_FOR_TRADE,
    MAX_TRADES_PER_CYCLE,
)

logger = logging.getLogger("TradingIntelligence")


def _calculate_position_size(
    confidence: int,
    max_amount: float = MAX_TRADE_AMOUNT_USD,
) -> float:
    """Scale position size by confidence (75-100 maps to $25-$50)."""
    # Linear scale: confidence 75 -> $25, confidence 100 -> $50
    ratio = (confidence - MIN_CONFIDENCE_FOR_TRADE) / (100 - MIN_CONFIDENCE_FOR_TRADE)
    amount = 25.0 + (ratio * 25.0)
    return min(round(amount, 2), max_amount)


def _calculate_stop_loss(
    entry_price: float,
    direction: str,
    support: float = 0,
) -> float:
    """Calculate stop loss price.

    BUY: 5% below entry (or support level if closer)
    SELL: 5% above entry
    """
    if direction == "BUY":
        default_stop = entry_price * 0.95
        if support > 0 and support < entry_price:
            # Use support level if it's within 7% of entry
            if (entry_price - support) / entry_price < 0.07:
                return round(support * 0.99, 6)  # Slightly below support
        return round(default_stop, 6)
    else:
        return round(entry_price * 1.05, 6)


def _calculate_take_profit(
    entry_price: float,
    direction: str,
    target_high: float = 0,
    resistance: float = 0,
) -> float:
    """Calculate take profit price.

    BUY: target_high or resistance or 8% above entry
    SELL: 8% below entry
    """
    if direction == "BUY":
        # Use target if reasonable
        if target_high > entry_price * 1.02:
            return round(target_high, 6)
        if resistance > entry_price * 1.02:
            return round(resistance * 0.99, 6)
        return round(entry_price * 1.08, 6)
    else:
        return round(entry_price * 0.92, 6)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def generate_suggestions(
    predictions: list[dict],
    technical: list[dict] | None = None,
) -> list[dict]:
    """Filter predictions and generate trade suggestions.

    Args:
        predictions: From prediction_engine.generate_predictions()
        technical: From technical_analyzer.analyze_technical() (optional, for S/R levels)

    Returns:
        List of trade suggestion dicts, max MAX_TRADES_PER_CYCLE.
    """
    if not predictions:
        return []

    # Build technical lookup
    tech_map = {}
    if technical:
        tech_map = {t["coin_id"]: t for t in technical}

    # Filter by confidence and direction (only 24h predictions for trading)
    candidates = []
    for pred in predictions:
        if pred.get("timeframe") != "24h":
            continue
        if pred.get("confidence", 0) < MIN_CONFIDENCE_FOR_TRADE:
            continue
        if pred.get("direction") == "NEUTRAL":
            continue
        candidates.append(pred)

    # Sort by confidence descending
    candidates.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    # Generate suggestions for top N
    suggestions = []
    for pred in candidates[:MAX_TRADES_PER_CYCLE]:
        coin_id = pred["coin"]
        direction = pred["direction"]
        confidence = pred["confidence"]
        entry_price = pred.get("price_at_prediction", 0)
        tech = tech_map.get(coin_id, {})

        if entry_price <= 0:
            continue

        # Map direction to action
        action = "BUY" if direction == "BULLISH" else "SELL"

        # Calculate position size
        amount_usd = _calculate_position_size(confidence)

        # Ensure HARD LIMIT
        amount_usd = min(amount_usd, MAX_TRADE_AMOUNT_USD)

        # Calculate stop loss and take profit
        stop_loss = _calculate_stop_loss(
            entry_price, action, tech.get("support", 0)
        )
        take_profit = _calculate_take_profit(
            entry_price, action,
            pred.get("target_high", 0),
            tech.get("resistance", 0),
        )

        # Build reasoning
        reasoning_parts = [pred.get("reasoning", "")]
        if tech.get("rsi"):
            reasoning_parts.append(f"RSI={tech['rsi']}")
        if tech.get("ma_signal") and tech["ma_signal"] != "NO_SIGNAL":
            reasoning_parts.append(f"MA={tech['ma_signal']}")
        reasoning = " | ".join(filter(None, reasoning_parts))

        suggestion = {
            "coin": coin_id,
            "symbol": pred.get("symbol", ""),
            "name": pred.get("name", ""),
            "action": action,
            "amount_usd": amount_usd,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "reasoning": reasoning,
            "confidence": confidence,
            "direction": direction,
            "timeframe": "24h",
            "risk_reward_ratio": round(
                abs(take_profit - entry_price) / max(abs(entry_price - stop_loss), 0.01), 2
            ),
        }

        # Risk/reward filter: must be at least 1.5:1
        if suggestion["risk_reward_ratio"] < 1.5:
            logger.debug(
                "Skipping %s: R/R ratio %.2f < 1.5",
                coin_id, suggestion["risk_reward_ratio"],
            )
            continue

        suggestions.append(suggestion)
        logger.info(
            "Trade suggestion: %s %s $%.2f @ $%.6f (conf=%d%%, R/R=%.2f)",
            action, coin_id, amount_usd, entry_price,
            confidence, suggestion["risk_reward_ratio"],
        )

    logger.info(
        "Generated %d trade suggestions from %d candidates (min confidence=%d%%)",
        len(suggestions), len(candidates), MIN_CONFIDENCE_FOR_TRADE,
    )
    return suggestions
