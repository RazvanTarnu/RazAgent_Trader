# -*- coding: utf-8 -*-
"""Shared exchange adapter utilities."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

import httpx

from shared.platform.interfaces import (
    Balance,
    OHLCVBar,
    OrderBook,
    OrderBookLevel,
    OrderRequest,
    OrderResult,
    Ticker,
)
from shared.platform.secrets import safe_exception_message

logger = logging.getLogger("platform.exchange")

T = TypeVar("T")

# Immutable guard — blocks withdrawal/transfer endpoints
_FORBIDDEN = frozenset({
    "withdraw", "transfer", "capital/withdraw", "inner-transfer",
    "sub-account/transfer", "universal-transfer",
})


class ExchangeSecurityError(Exception):
    """Raised when a forbidden endpoint is attempted."""


def validate_url_safety(exchange: str, url: str) -> None:
    url_lower = url.lower()
    for forbidden in _FORBIDDEN:
        if forbidden in url_lower:
            raise ExchangeSecurityError(
                f"Forbidden endpoint blocked for {exchange}: {forbidden}"
            )


def parse_balances(exchange: str, raw: list[dict[str, Any]]) -> list[Balance]:
    balances: list[Balance] = []
    for item in raw:
        free = float(item.get("free", 0) or 0)
        locked = float(item.get("used", item.get("locked", 0)) or 0)
        total = float(item.get("total", free + locked) or 0)
        if total > 0 or free > 0 or locked > 0:
            balances.append(
                Balance(
                    asset=str(item.get("currency", item.get("asset", ""))),
                    free=free,
                    locked=locked,
                    total=total,
                )
            )
    return balances


def parse_ticker(exchange: str, symbol: str, raw: dict[str, Any]) -> Ticker:
    return Ticker(
        symbol=symbol,
        bid=float(raw.get("bid", 0) or 0),
        ask=float(raw.get("ask", 0) or 0),
        last=float(raw.get("last", raw.get("close", 0)) or 0),
        volume_24h=float(raw.get("baseVolume", raw.get("volume", 0)) or 0),
        timestamp=datetime.fromtimestamp(
            (raw.get("timestamp") or 0) / 1000 if raw.get("timestamp") else datetime.now(timezone.utc).timestamp(),
            tz=timezone.utc,
        ),
    )


def parse_order_book(symbol: str, raw: dict[str, Any]) -> OrderBook:
    bids = tuple(
        OrderBookLevel(price=float(p), quantity=float(q))
        for p, q in raw.get("bids", [])[:50]
    )
    asks = tuple(
        OrderBookLevel(price=float(p), quantity=float(q))
        for p, q in raw.get("asks", [])[:50]
    )
    ts = raw.get("timestamp")
    timestamp = (
        datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        if ts
        else datetime.now(timezone.utc)
    )
    return OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=timestamp)


def parse_ohlcv(raw: list[list[float]]) -> list[OHLCVBar]:
    bars: list[OHLCVBar] = []
    for row in raw:
        if len(row) < 6:
            continue
        ts, o, h, l, c, v = row[:6]
        bars.append(
            OHLCVBar(
                timestamp=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(v),
            )
        )
    return bars


def parse_order_result(exchange: str, raw: dict[str, Any]) -> OrderResult:
    return OrderResult(
        success=True,
        exchange=exchange,
        order_id=str(raw.get("id", raw.get("orderId", ""))),
        symbol=str(raw.get("symbol", "")),
        side=str(raw.get("side", "")),
        quantity=float(raw.get("amount", raw.get("origQty", 0)) or 0),
        price=float(raw.get("price", raw.get("average", 0)) or 0),
    )


async def with_retry(
    fn: Callable[[], Any],
    *,
    max_retries: int = 3,
    base_delay: float = 2.0,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            result = fn()
            if asyncio.iscoroutine(result):
                return await result
            return result
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code in {401, 403, 404, 422}:
                raise RuntimeError(safe_exception_message(exc)) from exc
            if attempt < max_retries:
                await asyncio.sleep(base_delay * (2 ** attempt))
                continue
            raise RuntimeError(safe_exception_message(exc)) from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < max_retries:
                await asyncio.sleep(base_delay * (2 ** attempt))
                continue
            raise TimeoutError(safe_exception_message(exc)) from exc
    raise RuntimeError(safe_exception_message(last_exc or RuntimeError("retry exhausted")))
