# -*- coding: utf-8 -*-
"""CoinGecko read-only market data provider (no API key required)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

from shared.platform.interfaces import DataQuality, MarketDataPoint, MarketDataProvider

logger = logging.getLogger("quant.data.coingecko")

_BASE = "https://api.coingecko.com/api/v3"
_STALE = timedelta(minutes=10)


class CoinGeckoProvider(MarketDataProvider):
    """Public CoinGecko endpoints — market context only, no execution."""

    def __init__(self, timeout: float = 15.0):
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "coingecko"

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{_BASE}{path}", params=params or {})
            resp.raise_for_status()
            return resp.json()

    async def fetch_ticker(self, symbol: str) -> MarketDataPoint:
        coin_id = _symbol_to_coingecko_id(symbol)
        try:
            data = await self._get(f"/simple/price", {"ids": coin_id, "vs_currencies": "usd", "include_24hr_vol": "true"})
            price_data = data.get(coin_id, {})
            if not price_data:
                return _unavailable(symbol, "tick")
            now = datetime.now(timezone.utc)
            return MarketDataPoint(
                timestamp=now,
                source=self.name,
                symbol=symbol,
                timeframe="tick",
                quality=DataQuality.OK,
                payload={
                    "last": float(price_data.get("usd", 0)),
                    "volume_24h": float(price_data.get("usd_24h_vol", 0)),
                },
            )
        except Exception as exc:
            logger.warning("CoinGecko ticker failed for %s: %s", symbol, type(exc).__name__)
            return _unavailable(symbol, "tick")

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 100,
    ) -> MarketDataPoint:
        coin_id = _symbol_to_coingecko_id(symbol)
        days = _timeframe_to_days(timeframe, limit)
        try:
            # CoinGecko OHLC returns [timestamp, open, high, low, close]
            rows = await self._get(f"/coins/{coin_id}/ohlc", {"vs_currency": "usd", "days": days})
            if not isinstance(rows, list) or not rows:
                return _unavailable(symbol, timeframe)

            bars = []
            for row in rows[-limit:]:
                if not isinstance(row, list) or len(row) < 5:
                    continue
                ts = datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc)
                bars.append(
                    {
                        "timestamp": ts.isoformat(),
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": 0.0,
                    }
                )

            quality = DataQuality.OK
            if bars:
                last_ts = datetime.fromisoformat(bars[-1]["timestamp"].replace("Z", "+00:00"))
                if datetime.now(timezone.utc) - last_ts > _STALE:
                    quality = DataQuality.STALE

            return MarketDataPoint(
                timestamp=datetime.now(timezone.utc),
                source=self.name,
                symbol=symbol,
                timeframe=timeframe,
                quality=quality if bars else DataQuality.UNAVAILABLE,
                payload={"bars": bars},
            )
        except Exception as exc:
            logger.warning("CoinGecko OHLCV failed for %s: %s", symbol, type(exc).__name__)
            return _unavailable(symbol, timeframe)


def _unavailable(symbol: str, timeframe: str) -> MarketDataPoint:
    return MarketDataPoint(
        timestamp=datetime.now(timezone.utc),
        source="coingecko",
        symbol=symbol,
        timeframe=timeframe,
        quality=DataQuality.UNAVAILABLE,
        payload={"bars": []},
    )


def _symbol_to_coingecko_id(symbol: str) -> str:
    base = symbol.split("/")[0].upper()
    mapping = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "SOL": "solana",
        "BNB": "binancecoin",
        "XRP": "ripple",
        "ADA": "cardano",
        "DOGE": "dogecoin",
        "AVAX": "avalanche-2",
        "DOT": "polkadot",
        "LINK": "chainlink",
    }
    return mapping.get(base, base.lower())


def _timeframe_to_days(timeframe: str, limit: int) -> int:
    tf = timeframe.lower()
    if tf.endswith("d"):
        return min(365, max(7, limit))
    if tf.endswith("h"):
        return min(90, max(7, limit // 24 + 1))
    return min(90, max(7, limit // 6 + 1))
