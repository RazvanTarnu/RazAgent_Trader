# -*- coding: utf-8 -*-
"""RazAgent_Trader platform foundation.

Provides configuration, interfaces, lifecycle, and provider abstractions
used by the quant engine and security/QA layers.
"""

from shared.platform.interfaces import (
    ExchangeProvider,
    EventLogger,
    LLMProvider,
    MarketDataProvider,
    MetricsProvider,
    TradeRepository,
)
from shared.platform.config import PlatformConfig, load_platform_config

__all__ = [
    "ExchangeProvider",
    "EventLogger",
    "LLMProvider",
    "MarketDataProvider",
    "MetricsProvider",
    "PlatformConfig",
    "TradeRepository",
    "load_platform_config",
]
