# -*- coding: utf-8 -*-
"""Market data provider — read-only layer over exchange adapters."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from shared.platform.interfaces import (
    DataQuality,
    ExchangeProvider,
    MarketDataPoint,
    MarketDataProvider,
)


class ExchangeMarketDataProvider(MarketDataProvider):
    """Wraps an ExchangeProvider for read-only market data access."""

    STALE_THRESHOLD = timedelta(minutes=5)

    def __init__(self, exchange: ExchangeProvider, source_name: str | None = None):
        self._exchange = exchange
        self._source = source_name or exchange.name

    @property
    def name(self) -> str:
        return f"marketdata:{self._source}"

    async def fetch_ticker(self, symbol: str) -> MarketDataPoint:
        ticker = await self._exchange.get_ticker(symbol)
        quality = DataQuality.OK
        age = datetime.now(timezone.utc) - ticker.timestamp
        if age > self.STALE_THRESHOLD:
            quality = DataQuality.STALE
        return MarketDataPoint(
            timestamp=ticker.timestamp,
            source=self._source,
            symbol=symbol,
            timeframe="tick",
            quality=quality,
            payload={
                "bid": ticker.bid,
                "ask": ticker.ask,
                "last": ticker.last,
                "volume_24h": ticker.volume_24h,
            },
        )

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 100,
    ) -> MarketDataPoint:
        bars = await self._exchange.get_ohlcv(symbol, timeframe, limit=limit)
        if not bars:
            return MarketDataPoint(
                timestamp=datetime.now(timezone.utc),
                source=self._source,
                symbol=symbol,
                timeframe=timeframe,
                quality=DataQuality.UNAVAILABLE,
                payload={"bars": []},
            )
        quality = DataQuality.OK
        last_ts = bars[-1].timestamp
        if datetime.now(timezone.utc) - last_ts > self.STALE_THRESHOLD:
            quality = DataQuality.STALE
        return MarketDataPoint(
            timestamp=last_ts,
            source=self._source,
            symbol=symbol,
            timeframe=timeframe,
            quality=quality,
            payload={
                "bars": [
                    {
                        "timestamp": b.timestamp.isoformat(),
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "volume": b.volume,
                    }
                    for b in bars
                ]
            },
        )
