# -*- coding: utf-8 -*-
"""Structured signal and recommendation models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class QuantSignal:
    """Deterministic signal — never an executable order."""

    symbol: str
    direction: str  # BUY | SELL | HOLD
    strength: float  # 0.0–1.0
    confidence: float  # 0.0–1.0
    time_horizon: str
    entry_rationale: str
    invalidation: list[str]
    required_market_conditions: list[str]
    source: str
    timeframe: str
    features: dict[str, Any] = field(default_factory=dict)
    regime: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.utcnow())
    strategy_version: str = "1.0.0"
    data_source: str = ""
    data_quality: str = "ok"


@dataclass(frozen=True)
class AgentOutput:
    """Structured output from one crypto-swarm agent."""

    agent: str  # fundamentals | technical | risk_analysis
    thesis: str
    signals: list[dict[str, Any]]
    evidence: list[str]
    confidence: float
    invalidation_conditions: list[str]
    timeframe: str
    risks: list[str]
    model: str = ""
    provider: str = ""
    timestamp: Optional[datetime] = None


@dataclass(frozen=True)
class SignalBundle:
    """Aggregated signals for one symbol from multiple engines."""

    symbol: str
    signals: tuple[QuantSignal, ...]
    agent_outputs: tuple[AgentOutput, ...]
    regime: str
    aggregate_confidence: float
    aggregate_direction: str
    timestamp: datetime
    feature_snapshot: dict[str, Any] = field(default_factory=dict)
    prompt_version: str = "swarm-v1"
    strategy_version: str = "1.0.0"


@dataclass(frozen=True)
class PortfolioRecommendation:
    """Research output for risk engine — NOT an order."""

    symbol: str
    direction: str
    confidence: float
    bundle: SignalBundle
    thesis: str
    invalidation_conditions: list[str]
    risks: list[str]
    timeframe: str
    reproducibility: dict[str, Any]
    timestamp: datetime
