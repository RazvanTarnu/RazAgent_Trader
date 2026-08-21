# -*- coding: utf-8 -*-
"""Exchange adapter normalization and contract tests."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from shared.platform.interfaces import OrderRequest
from shared.providers.exchange.base import (
    ExchangeSecurityError,
    parse_balances,
    parse_ohlcv,
    parse_order_book,
    parse_ticker,
    validate_url_safety,
    with_retry,
)
from shared.providers.exchange.binance import BinanceAdapter
from shared.providers.exchange.kucoin import KuCoinAdapter


def test_validate_url_safety_blocks_withdraw():
    with pytest.raises(ExchangeSecurityError):
        validate_url_safety("binance", "https://api.binance.com/sapi/v1/capital/withdraw/apply")


def test_parse_balances_normalization():
    raw = [{"asset": "USDT", "free": "100.5", "locked": "0", "total": "100.5"}]
    # parse_balances expects currency key from ccxt format
    items = [{"currency": "USDT", "free": 100.5, "used": 0, "total": 100.5}]
    balances = parse_balances("binance", items)
    assert len(balances) == 1
    assert balances[0].asset == "USDT"
    assert balances[0].free == 100.5


def test_parse_ticker_normalization():
    raw = {"bid": 100.0, "ask": 101.0, "last": 100.5, "baseVolume": 1000, "timestamp": 1700000000000}
    ticker = parse_ticker("binance", "BTC/USDT", raw)
    assert ticker.symbol == "BTC/USDT"
    assert ticker.last == 100.5


def test_parse_ohlcv_skips_malformed():
    bars = parse_ohlcv([[1700000000000, 1, 2, 0.5, 1.5, 100], [1, 2]])
    assert len(bars) == 1


def test_parse_order_book():
    raw = {
        "bids": [[100.0, 1.0], [99.0, 2.0]],
        "asks": [[101.0, 1.5]],
        "timestamp": 1700000000000,
    }
    book = parse_order_book("BTC/USDT", raw)
    assert len(book.bids) == 2
    assert len(book.asks) == 1


@pytest.mark.asyncio
async def test_binance_paper_mode_place_order(monkeypatch):
    monkeypatch.setattr("shared.execution.kill_switch.is_armed", lambda *a, **k: False)
    adapter = BinanceAdapter(api_key="", api_secret="", paper_mode=True, max_retries=0)
    result = await adapter.place_order(
        OrderRequest(symbol="BTC/USDT", side="buy", order_type="market", quantity=0.001)
    )
    assert result.success is True
    assert result.order_id.startswith("paper-")
    await adapter.close()


@pytest.mark.asyncio
async def test_binance_missing_credentials_live_fails(monkeypatch):
    monkeypatch.setattr("shared.execution.kill_switch.is_armed", lambda *a, **k: False)
    adapter = BinanceAdapter(api_key="", api_secret="", paper_mode=False, max_retries=0)
    result = await adapter.place_order(
        OrderRequest(symbol="BTC/USDT", side="buy", order_type="market", quantity=0.001)
    )
    assert result.success is False
    assert "credential" in (result.error or "").lower()
    await adapter.close()


@pytest.mark.asyncio
async def test_kucoin_paper_mode_default_without_creds():
    adapter = KuCoinAdapter(api_key="", api_secret="", passphrase="", paper_mode=True)
    balances = await adapter.get_balances()
    assert balances[0].asset == "USDT"
    assert balances[0].free == 0.0
    await adapter.close()


@pytest.mark.asyncio
async def test_with_retry_fatal_auth_no_retry():
    call_count = 0

    async def failing():
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.status_code = 401
        raise httpx.HTTPStatusError("auth", request=MagicMock(), response=resp)

    with pytest.raises(RuntimeError):
        await with_retry(failing, max_retries=3)
    assert call_count == 1


@pytest.mark.asyncio
async def test_with_retry_transient_retries():
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise httpx.TimeoutException("timeout")
        return "ok"

    result = await with_retry(flaky, max_retries=2, base_delay=0.01)
    assert result == "ok"
    assert call_count == 2
