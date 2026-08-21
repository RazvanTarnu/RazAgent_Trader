# -*- coding: utf-8 -*-
"""Research cycle — market data → features → regime → swarm → recommendation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from shared.platform.interfaces import DataQuality, LLMProvider, MarketDataProvider

from trading_intelligence.features.pipeline import FeaturePipeline
from trading_intelligence.regime.classifier import RegimeClassifier
from trading_intelligence.signals.aggregator import SignalAggregator
from trading_intelligence.signals.models import PortfolioRecommendation
from trading_intelligence.swarm.coordinator import SwarmCoordinator

logger = logging.getLogger("quant.pipeline")


class ResearchCycle:
    """One research cycle for a symbol — produces PortfolioRecommendation only."""

    VERSION = "1.0.0"

    def __init__(
        self,
        market: MarketDataProvider,
        llm: LLMProvider,
        *,
        timeframe: str = "1d",
        ohlcv_limit: int = 200,
    ):
        self._market = market
        self._llm = llm
        self._timeframe = timeframe
        self._limit = ohlcv_limit
        self._features = FeaturePipeline()
        self._regime = RegimeClassifier()
        self._aggregator = SignalAggregator()
        self._swarm = SwarmCoordinator(llm)

    async def run(
        self,
        symbol: str,
        *,
        market_context: dict[str, Any] | None = None,
        run_swarm: bool = True,
    ) -> PortfolioRecommendation:
        ohlcv = await self._market.fetch_ohlcv(symbol, self._timeframe, limit=self._limit)
        if ohlcv.quality in (DataQuality.MALFORMED, DataQuality.UNAVAILABLE):
            raise ValueError(f"Rejecting stale/malformed data for {symbol}: {ohlcv.quality.value}")

        fv = self._features.from_ohlcv_point(ohlcv)
        spread_pct = None
        try:
            ticker = await self._market.fetch_ticker(symbol)
            if ticker.quality == DataQuality.OK:
                bid = ticker.payload.get("bid", 0)
                ask = ticker.payload.get("ask", 0)
                if bid and ask:
                    from trading_intelligence.features.technical import spread_pct as calc_spread

                    spread_pct = calc_spread(float(bid), float(ask))
        except Exception:
            pass

        regime = self._regime.classify(fv, spread_pct=spread_pct)
        tech_signal = self._aggregator.from_features(fv, regime)
        feature_ctx = self._features.to_context(fv)
        regime_ctx = self._regime.to_context(regime)

        if run_swarm:
            bundle = await self._swarm.analyze(
                symbol,
                features=feature_ctx,
                regime=regime_ctx,
                market_context=market_context,
            )
            bundle = self._aggregator.merge(tech_signal, bundle)
        else:
            bundle = self._aggregator.aggregate_swarm(
                symbol=symbol,
                agents=(),
                regime_label=regime.label,
                feature_snapshot=feature_ctx,
                technical_signal=tech_signal,
            )

        thesis = _synthesize_thesis(bundle)
        return PortfolioRecommendation(
            symbol=symbol,
            direction=bundle.aggregate_direction,
            confidence=bundle.aggregate_confidence,
            bundle=bundle,
            thesis=thesis,
            invalidation_conditions=_collect_invalidations(bundle),
            risks=_collect_risks(bundle),
            timeframe=self._timeframe,
            reproducibility={
                "data_source": ohlcv.source,
                "data_quality": ohlcv.quality.value,
                "data_timestamp": ohlcv.timestamp.isoformat(),
                "feature_version": fv.version,
                "strategy_version": self.VERSION,
                "prompt_version": "swarm-v1",
                "model": getattr(self._llm, "_model", "unknown"),
                "provider": self._llm.name,
            },
            timestamp=datetime.now(timezone.utc),
        )


def _synthesize_thesis(bundle) -> str:
    parts = [a.thesis for a in bundle.agent_outputs if a.thesis]
    if parts:
        return " | ".join(parts[:3])
    if bundle.signals:
        return bundle.signals[0].entry_rationale
    return "No actionable thesis"


def _collect_invalidations(bundle) -> list[str]:
    out: list[str] = []
    for s in bundle.signals:
        out.extend(s.invalidation)
    for a in bundle.agent_outputs:
        out.extend(a.invalidation_conditions)
    return list(dict.fromkeys(out))


def _collect_risks(bundle) -> list[str]:
    out: list[str] = []
    for a in bundle.agent_outputs:
        out.extend(a.risks)
    return list(dict.fromkeys(out))
