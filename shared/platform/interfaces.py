# -*- coding: utf-8 -*-
"""Platform architectural interfaces.

Higher-level trading code must depend on these abstractions — not on
OpenRouter, Binance, KuCoin, keyring, or HTTP implementation details.

The LLM provider returns structured recommendations only; it must NEVER
receive direct authority to execute exchange orders.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Sequence


class DataQuality(str, Enum):
    OK = "ok"
    STALE = "stale"
    MALFORMED = "malformed"
    UNAVAILABLE = "unavailable"


class ProcessState(str, Enum):
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass(frozen=True)
class MarketDataPoint:
    timestamp: datetime
    source: str
    symbol: str
    timeframe: str
    quality: DataQuality
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Balance:
    asset: str
    free: float
    locked: float
    total: float


@dataclass(frozen=True)
class Ticker:
    symbol: str
    bid: float
    ask: float
    last: float
    volume_24h: float
    timestamp: datetime


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    quantity: float


@dataclass(frozen=True)
class OrderBook:
    symbol: str
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    timestamp: datetime


@dataclass(frozen=True)
class OHLCVBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str  # buy | sell
    order_type: str  # market | limit
    quantity: float
    price: Optional[float] = None
    client_order_id: Optional[str] = None


@dataclass(frozen=True)
class OrderResult:
    success: bool
    exchange: str
    order_id: Optional[str] = None
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    price: float = 0.0
    error: Optional[str] = None


@dataclass(frozen=True)
class LLMRecommendation:
    """Structured LLM output — never an executable order."""

    thesis: str
    signals: list[dict[str, Any]]
    evidence: list[str]
    confidence: float
    invalidation_conditions: list[str]
    timeframe: str
    risks: list[str]
    model: str
    provider: str
    timestamp: datetime


@dataclass(frozen=True)
class TradeRecord:
    id: Optional[int]
    timestamp: datetime
    exchange: str
    symbol: str
    side: str
    quantity: float
    price: float
    fee: float
    paper_mode: bool
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditEvent:
    timestamp: datetime
    category: str
    action: str
    actor: str
    target: str
    details: dict[str, Any]
    status: str


@dataclass(frozen=True)
class PlatformMetrics:
    health: str
    readiness: str
    process_state: ProcessState
    paper_mode: bool
    provider_status: dict[str, str]
    exchange_connectivity: dict[str, str]
    last_market_data_ts: Optional[datetime]
    last_successful_model_call: Optional[datetime]


class LLMProvider(ABC):
    """Analyze and recommend — never execute trades."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        ...

    @abstractmethod
    async def recommend(
        self,
        context: dict[str, Any],
        *,
        temperature: float = 0.2,
    ) -> LLMRecommendation:
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        ...


class ExchangeProvider(ABC):
    """Normalized exchange adapter — all exchange quirks stay inside."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def get_balances(self) -> list[Balance]:
        ...

    @abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker:
        ...

    @abstractmethod
    async def get_order_book(self, symbol: str, *, depth: int = 20) -> OrderBook:
        ...

    @abstractmethod
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 100,
    ) -> list[OHLCVBar]:
        ...

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderResult:
        ...

    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> OrderResult:
        ...

    @abstractmethod
    async def get_order(self, symbol: str, order_id: str) -> OrderResult:
        ...

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> list[OrderResult]:
        ...

    @abstractmethod
    async def test_connection(self) -> bool:
        ...


class MarketDataProvider(ABC):
    """Read-only market data — no order execution."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> MarketDataPoint:
        ...

    @abstractmethod
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 100,
    ) -> MarketDataPoint:
        ...


class TradeRepository(ABC):
    """Persistence boundary for trade records."""

    @abstractmethod
    def save_trade(self, trade: TradeRecord) -> int:
        ...

    @abstractmethod
    def get_trade(self, trade_id: int) -> Optional[TradeRecord]:
        ...

    @abstractmethod
    def list_trades(
        self,
        *,
        limit: int = 50,
        exchange: Optional[str] = None,
    ) -> list[TradeRecord]:
        ...


class EventLogger(ABC):
    """Append-only audit/event infrastructure."""

    @abstractmethod
    def log_event(self, event: AuditEvent) -> None:
        ...

    @abstractmethod
    def list_events(
        self,
        *,
        category: Optional[str] = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        ...


class MetricsProvider(ABC):
    """Read-only metrics snapshot for supervisor polling."""

    @abstractmethod
    def snapshot(self) -> PlatformMetrics:
        ...
