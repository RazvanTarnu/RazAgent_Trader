# -*- coding: utf-8 -*-
"""Binance exchange adapter — normalized platform interface."""

from __future__ import annotations

import logging
from typing import Optional

import ccxt.async_support as ccxt

from shared.platform.interfaces import (
    Balance,
    ExchangeProvider,
    OHLCVBar,
    OrderBook,
    OrderRequest,
    OrderResult,
    Ticker,
)
from shared.platform.secrets import safe_exception_message
from shared.providers.exchange.base import (
    ExchangeSecurityError,
    parse_balances,
    parse_ohlcv,
    parse_order_book,
    parse_order_result,
    parse_ticker,
    reject_if_kill_switch_armed,
    validate_url_safety,
    with_retry,
)

logger = logging.getLogger("platform.exchange.binance")


class BinanceAdapter(ExchangeProvider):
    """Binance spot adapter via ccxt — exchange specifics stay inside."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        paper_mode: bool = True,
        timeout_ms: int = 15000,
        max_retries: int = 3,
    ):
        self._paper_mode = paper_mode
        self._max_retries = max_retries
        self._client = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": timeout_ms,
            "options": {"defaultType": "spot"},
        })
        self._has_credentials = bool(api_key and api_secret)

    @property
    def name(self) -> str:
        return "binance"

    async def _ensure_safe(self, method: str) -> None:
        validate_url_safety(self.name, method)

    async def close(self) -> None:
        await self._client.close()

    async def get_balances(self) -> list[Balance]:
        if self._paper_mode or not self._has_credentials:
            return [Balance(asset="USDT", free=0.0, locked=0.0, total=0.0)]
        await self._ensure_safe("fetchBalance")
        raw = await with_retry(
            lambda: self._client.fetch_balance(),
            max_retries=self._max_retries,
        )
        return parse_balances(self.name, raw.get("info", {}).get("balances", []) or [
            {"currency": k, "free": v.get("free", 0), "used": v.get("used", 0), "total": v.get("total", 0)}
            for k, v in raw.items()
            if isinstance(v, dict) and k not in {"info", "free", "used", "total", "datetime", "timestamp"}
        ])

    async def get_ticker(self, symbol: str) -> Ticker:
        await self._ensure_safe("fetchTicker")
        raw = await with_retry(
            lambda: self._client.fetch_ticker(symbol),
            max_retries=self._max_retries,
        )
        return parse_ticker(self.name, symbol, raw)

    async def get_order_book(self, symbol: str, *, depth: int = 20) -> OrderBook:
        await self._ensure_safe("fetchOrderBook")
        raw = await with_retry(
            lambda: self._client.fetch_order_book(symbol, depth),
            max_retries=self._max_retries,
        )
        return parse_order_book(symbol, raw)

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 100,
    ) -> list[OHLCVBar]:
        await self._ensure_safe("fetchOHLCV")
        raw = await with_retry(
            lambda: self._client.fetch_ohlcv(symbol, timeframe, limit=limit),
            max_retries=self._max_retries,
        )
        return parse_ohlcv(raw)

    async def place_order(self, request: OrderRequest) -> OrderResult:
        blocked = reject_if_kill_switch_armed(self.name)
        if blocked is not None:
            return blocked
        if self._paper_mode:
            return OrderResult(
                success=True,
                exchange=self.name,
                order_id=f"paper-{request.symbol}-{request.side}",
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                price=request.price or 0.0,
            )
        if not self._has_credentials:
            return OrderResult(
                success=False,
                exchange=self.name,
                error="Missing Binance credentials",
            )
        await self._ensure_safe("createOrder")
        try:
            if request.order_type == "market":
                raw = await with_retry(
                    lambda: self._client.create_order(
                        request.symbol, "market", request.side, request.quantity
                    ),
                    max_retries=self._max_retries,
                )
            else:
                if request.price is None:
                    return OrderResult(success=False, exchange=self.name, error="Limit order requires price")
                raw = await with_retry(
                    lambda: self._client.create_order(
                        request.symbol, "limit", request.side, request.quantity, request.price
                    ),
                    max_retries=self._max_retries,
                )
            return parse_order_result(self.name, raw)
        except ExchangeSecurityError:
            raise
        except Exception as exc:
            return OrderResult(success=False, exchange=self.name, error=safe_exception_message(exc))

    async def cancel_order(self, symbol: str, order_id: str) -> OrderResult:
        if self._paper_mode:
            return OrderResult(success=True, exchange=self.name, order_id=order_id, symbol=symbol)
        await self._ensure_safe("cancelOrder")
        try:
            raw = await with_retry(
                lambda: self._client.cancel_order(order_id, symbol),
                max_retries=self._max_retries,
            )
            return parse_order_result(self.name, raw)
        except Exception as exc:
            return OrderResult(success=False, exchange=self.name, error=safe_exception_message(exc))

    async def get_order(self, symbol: str, order_id: str) -> OrderResult:
        if self._paper_mode:
            return OrderResult(success=True, exchange=self.name, order_id=order_id, symbol=symbol)
        await self._ensure_safe("fetchOrder")
        raw = await with_retry(
            lambda: self._client.fetch_order(order_id, symbol),
            max_retries=self._max_retries,
        )
        return parse_order_result(self.name, raw)

    async def get_open_orders(self, symbol: Optional[str] = None) -> list[OrderResult]:
        if self._paper_mode or not self._has_credentials:
            return []
        await self._ensure_safe("fetchOpenOrders")
        raw = await with_retry(
            lambda: self._client.fetch_open_orders(symbol),
            max_retries=self._max_retries,
        )
        return [parse_order_result(self.name, item) for item in raw]

    async def test_connection(self) -> bool:
        try:
            await self.get_ticker("BTC/USDT")
            if self._has_credentials and not self._paper_mode:
                await self.get_balances()
            return True
        except Exception as exc:
            logger.debug("Binance connection test failed: %s", safe_exception_message(exc))
            return False
