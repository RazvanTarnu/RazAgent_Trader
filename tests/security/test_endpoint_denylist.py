# -*- coding: utf-8 -*-
"""Parameterized coverage for the extended financial-endpoint deny-lists."""

from __future__ import annotations

import pytest

from shared.providers.exchange.base import ExchangeSecurityError, validate_url_safety
from skills.trading_intelligence.exchanges.base_executor import (
    CriticalSecurityException,
    validate_endpoint_safety,
)

REQUIRED_FRAGMENTS = (
    "asset/dust",
    "asset/convert",
    "convert/",
    "margin/",
    "futures/",
    "lending/",
    "staking/",
    "sub-account/",
    "simple-earn/",
    "loan/",
)


@pytest.mark.parametrize("fragment", REQUIRED_FRAGMENTS)
def test_platform_denylist_blocks_each_fragment(fragment):
    with pytest.raises(ExchangeSecurityError):
        validate_url_safety("binance", f"https://api.binance.com/sapi/v1/{fragment}x")


@pytest.mark.parametrize("fragment", REQUIRED_FRAGMENTS)
def test_legacy_denylist_blocks_each_fragment(fragment):
    with pytest.raises(CriticalSecurityException):
        validate_endpoint_safety("kucoin", f"https://api.kucoin.com/{fragment}x")
