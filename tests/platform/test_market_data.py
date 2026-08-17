# -*- coding: utf-8 -*-
"""Market data provider tests."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from shared.market_data.provider import ExchangeMarketDataProvider
from shared.platform.interfaces import (
    DataQuality,
    OHLCVBar,
    Ticker,
)


class MockExchange:
    name = "mock"

    async def get_ticker(self, symbol):
        return Ticker(
            symbol=symbol,
            bid=100.0,
            ask=101.0,
            last=100.5,
            volume_24h=1000,
            timestamp=datetime.now(timezone.utc),
        )

    async def get_ohlcv(self, symbol, timeframe, *, limit=100):
        return [
            OHLCVBar(
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=1),
                open=1, high=2, low=0.5, close=1.5, volume=100,
            )
        ]


@pytest.mark.asyncio
async def test_fetch_ticker_quality_ok():
    provider = ExchangeMarketDataProvider(MockExchange())
    point = await provider.fetch_ticker("BTC/USDT")
    assert point.quality == DataQuality.OK
    assert point.source == "mock"
    assert point.payload["last"] == 100.5


@pytest.mark.asyncio
async def test_fetch_ohlcv_empty_unavailable():
    class EmptyExchange(MockExchange):
        async def get_ohlcv(self, symbol, timeframe, *, limit=100):
            return []

    provider = ExchangeMarketDataProvider(EmptyExchange())
    point = await provider.fetch_ohlcv("BTC/USDT", "1h")
    assert point.quality == DataQuality.UNAVAILABLE
