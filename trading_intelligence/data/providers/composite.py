# -*- coding: utf-8 -*-
"""Composite market data — prefers exchange, falls back to CoinGecko."""

from __future__ import annotations

import logging

from shared.platform.interfaces import DataQuality, MarketDataPoint, MarketDataProvider

logger = logging.getLogger("quant.data.composite")


class CompositeMarketDataProvider(MarketDataProvider):
    """Try primary (exchange) then secondary (CoinGecko) providers."""

    def __init__(
        self,
        primary: MarketDataProvider,
        secondary: MarketDataProvider | None = None,
    ):
        self._primary = primary
        self._secondary = secondary

    @property
    def name(self) -> str:
        return f"composite:{self._primary.name}"

    async def fetch_ticker(self, symbol: str) -> MarketDataPoint:
        point = await self._primary.fetch_ticker(symbol)
        if point.quality == DataQuality.OK:
            return point
        if self._secondary:
            fallback = await self._secondary.fetch_ticker(symbol)
            if fallback.quality == DataQuality.OK:
                return fallback
        return point

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 100,
    ) -> MarketDataPoint:
        point = await self._primary.fetch_ohlcv(symbol, timeframe, limit=limit)
        if point.quality == DataQuality.OK and point.payload.get("bars"):
            return point
        if self._secondary:
            fallback = await self._secondary.fetch_ohlcv(symbol, timeframe, limit=limit)
            if fallback.quality in (DataQuality.OK, DataQuality.STALE) and fallback.payload.get("bars"):
                return fallback
        return point
