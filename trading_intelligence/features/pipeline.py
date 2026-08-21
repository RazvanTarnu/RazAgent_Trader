# -*- coding: utf-8 -*-
"""Feature pipeline — transforms market data into feature vectors."""

from __future__ import annotations

from typing import Any

from shared.platform.interfaces import DataQuality, MarketDataPoint

from trading_intelligence.features.technical import FeatureVector, compute_technical_features


class FeaturePipeline:
    """Build feature vectors from platform MarketDataPoint objects."""

    VERSION = "1.0.0"

    def from_ohlcv_point(self, point: MarketDataPoint) -> FeatureVector:
        if point.quality in (DataQuality.MALFORMED, DataQuality.UNAVAILABLE):
            raise ValueError(f"Cannot compute features from {point.quality.value} data")

        bars = point.payload.get("bars", [])
        if not bars:
            raise ValueError("OHLCV payload missing bars")

        fv = compute_technical_features(
            point.symbol,
            point.timeframe,
            bars,
            timestamp=point.timestamp.isoformat(),
        )
        return FeatureVector(
            symbol=fv.symbol,
            timeframe=fv.timeframe,
            timestamp=fv.timestamp,
            features={**fv.features, "data_quality": point.quality.value, "data_source": point.source},
            version=self.VERSION,
        )

    def to_context(self, fv: FeatureVector) -> dict[str, Any]:
        return {
            "symbol": fv.symbol,
            "timeframe": fv.timeframe,
            "timestamp": fv.timestamp,
            "features": fv.features,
            "feature_version": fv.version,
        }
