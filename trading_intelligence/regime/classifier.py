# -*- coding: utf-8 -*-
"""Market regime classification from feature vectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trading_intelligence.features.technical import FeatureVector


@dataclass(frozen=True)
class RegimeContext:
    label: str
    trend: str
    volatility: str
    liquidity: str
    confidence: float
    rationale: list[str]


class RegimeClassifier:
    """Rule-based regime detector — explicit, reproducible, no ML black box."""

    def classify(self, features: FeatureVector, *, spread_pct: float | None = None) -> RegimeContext:
        f = features.features
        trend = str(f.get("trend_regime", "unknown"))
        vol = str(f.get("volatility_regime", "unknown"))
        rsi_val = f.get("rsi_14")
        vol_anom = f.get("volume_anomaly")

        liquidity = "normal"
        if spread_pct is not None and spread_pct > 0.005:
            liquidity = "thin"
        elif spread_pct is not None and spread_pct < 0.001:
            liquidity = "deep"

        rationale: list[str] = []
        if trend != "unknown":
            rationale.append(f"Trend regime: {trend}")
        if vol != "unknown":
            rationale.append(f"Volatility regime: {vol}")

        label = "neutral"
        confidence = 0.5

        if trend == "uptrend" and vol in ("normal_volatility", "low_volatility"):
            label = "bull_trend"
            confidence = 0.65
        elif trend == "downtrend" and vol in ("normal_volatility", "high_volatility"):
            label = "bear_trend"
            confidence = 0.65
        elif vol == "high_volatility":
            label = "high_vol_chop"
            confidence = 0.6
        elif trend == "range":
            label = "range_bound"
            confidence = 0.55

        if isinstance(rsi_val, (int, float)):
            if rsi_val > 70:
                rationale.append("RSI overbought")
                if label == "bull_trend":
                    confidence *= 0.9
            elif rsi_val < 30:
                rationale.append("RSI oversold")
                if label == "bear_trend":
                    confidence *= 0.9

        if isinstance(vol_anom, (int, float)) and vol_anom > 2.0:
            rationale.append("Volume spike detected")
            confidence = min(1.0, confidence + 0.05)

        return RegimeContext(
            label=label,
            trend=trend,
            volatility=vol,
            liquidity=liquidity,
            confidence=round(confidence, 3),
            rationale=rationale,
        )

    def to_context(self, regime: RegimeContext) -> dict[str, Any]:
        return {
            "label": regime.label,
            "trend": regime.trend,
            "volatility": regime.volatility,
            "liquidity": regime.liquidity,
            "confidence": regime.confidence,
            "rationale": regime.rationale,
        }
