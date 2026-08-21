# -*- coding: utf-8 -*-
"""Exchange adapter factory."""

from __future__ import annotations

from typing import Dict

from shared.platform.config import PlatformConfig
from shared.platform.interfaces import ExchangeProvider
from shared.keyring_loader import get_credential
from shared.providers.exchange.binance import BinanceAdapter
from shared.providers.exchange.kucoin import KuCoinAdapter


def create_exchange_adapters(config: PlatformConfig) -> dict[str, ExchangeProvider]:
    """Create enabled exchange adapters for read-only/paper operation."""
    if config.safety.paper_mode is not True:
        from shared.execution import ExecutionForbidden
        raise ExecutionForbidden("exchange adapters require paper-only configuration")
    paper = True
    timeout_ms = int(config.exchanges.request_timeout_seconds * 1000)
    retries = config.exchanges.max_retries
    adapters: dict[str, ExchangeProvider] = {}

    if "binance" in config.exchanges.enabled:
        adapters["binance"] = BinanceAdapter(
            api_key=get_credential("BINANCE_API_KEY") or "",
            api_secret=get_credential("BINANCE_API_SECRET") or "",
            paper_mode=paper,
            timeout_ms=timeout_ms,
            max_retries=retries,
        )

    if "kucoin" in config.exchanges.enabled:
        adapters["kucoin"] = KuCoinAdapter(
            api_key=get_credential("KUCOIN_API_KEY") or "",
            api_secret=get_credential("KUCOIN_API_SECRET") or "",
            passphrase=get_credential("KUCOIN_API_PASSPHRASE") or "",
            paper_mode=paper,
            timeout_ms=timeout_ms,
            max_retries=retries,
        )

    return adapters
