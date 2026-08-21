# -*- coding: utf-8 -*-
"""Quant engine tests — unit and mock contract tests only."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

import pytest

from shared.platform.interfaces import (
    DataQuality,
    LLMProvider,
    LLMRecommendation,
    MarketDataPoint,
    MarketDataProvider,
)
from trading_intelligence.backtest.costs import CostModel
from trading_intelligence.backtest.engine import BacktestEngine
from trading_intelligence.backtest.metrics import compute_metrics
from trading_intelligence.backtest.walk_forward import WalkForwardEvaluator
from trading_intelligence.features.pipeline import FeaturePipeline
from trading_intelligence.features.technical import compute_technical_features, rsi, returns
from trading_intelligence.regime.classifier import RegimeClassifier
from trading_intelligence.signals.aggregator import SignalAggregator
from trading_intelligence.signals.models import AgentOutput
from trading_intelligence.swarm.coordinator import SwarmCoordinator


def _cost_model() -> CostModel:
    return CostModel(maker_fee=0.001, taker_fee=0.001, spread_bps=2.0, slippage_k=0.1)


def _engine(**kwargs) -> BacktestEngine:
    params = {
        "cost_model": _cost_model(),
        "stop_loss_pct": 0.1,
        "time_stop_bars": 40,
        "min_bars": 30,
        "min_trades": 1,
    }
    params.update(kwargs)
    return BacktestEngine(**params)


def _walk_forward(**kwargs) -> WalkForwardEvaluator:
    params = {
        "cost_model": _cost_model(),
        "stop_loss_pct": 0.1,
        "time_stop_bars": 40,
    }
    params.update(kwargs)
    return WalkForwardEvaluator(**params)


def _make_bars(n: int, *, start: float = 100.0, drift: float = 0.001) -> list[dict[str, Any]]:
    bars = []
    price = start
    for i in range(n):
        price *= 1 + drift + (0.002 if i % 7 == 0 else -0.001)
        bars.append(
            {
                "timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(),
                "open": price * 0.99,
                "high": price * 1.01,
                "low": price * 0.98,
                "close": price,
                "volume": 1000 + i * 10,
            }
        )
    return bars


class MockMarketData(MarketDataProvider):
    def __init__(self, bars: list[dict[str, Any]] | None = None, quality: DataQuality = DataQuality.OK):
        self._bars = bars or _make_bars(100)
        self._quality = quality

    @property
    def name(self) -> str:
        return "mock"

    async def fetch_ticker(self, symbol: str) -> MarketDataPoint:
        return MarketDataPoint(
            timestamp=datetime.now(timezone.utc),
            source="mock",
            symbol=symbol,
            timeframe="tick",
            quality=self._quality,
            payload={"bid": 100.0, "ask": 100.1, "last": 100.05, "volume_24h": 1e6},
        )

    async def fetch_ohlcv(self, symbol: str, timeframe: str, *, limit: int = 100) -> MarketDataPoint:
        return MarketDataPoint(
            timestamp=datetime.now(timezone.utc),
            source="mock",
            symbol=symbol,
            timeframe=timeframe,
            quality=self._quality,
            payload={"bars": self._bars[-limit:]},
        )


class MockLLM(LLMProvider):
    def __init__(self, response: dict[str, Any] | None = None):
        self._response = response or {
            "thesis": "Mock thesis",
            "signals": [
                {
                    "direction": "BUY",
                    "strength": 0.6,
                    "confidence": 0.7,
                    "time_horizon": "1d",
                    "entry_rationale": "Mock entry",
                    "invalidation": ["break support"],
                    "required_market_conditions": ["uptrend"],
                }
            ],
            "evidence": ["mock evidence"],
            "confidence": 0.7,
            "invalidation_conditions": ["regime shift"],
            "timeframe": "1d",
            "risks": ["volatility"],
        }

    @property
    def name(self) -> str:
        return "mock_llm"

    async def complete(self, messages: Sequence[dict[str, str]], *, temperature: float = 0.2, max_tokens: int = 4096) -> str:
        import json

        return json.dumps(self._response)

    async def recommend(self, context: dict[str, Any], *, temperature: float = 0.2) -> LLMRecommendation:
        return LLMRecommendation(
            thesis=str(self._response["thesis"]),
            signals=list(self._response["signals"]),
            evidence=list(self._response["evidence"]),
            confidence=float(self._response["confidence"]),
            invalidation_conditions=list(self._response["invalidation_conditions"]),
            timeframe=str(self._response["timeframe"]),
            risks=list(self._response["risks"]),
            model="mock",
            provider=self.name,
            timestamp=datetime.now(timezone.utc),
        )

    async def health_check(self) -> bool:
        return True


def test_rsi_bounds():
    closes = [100 + i * 0.5 for i in range(30)]
    val = rsi(closes)
    assert val is not None
    assert 0 <= val <= 100


def test_returns_calculation():
    closes = [100.0, 101.0, 102.0]
    assert returns(closes, period=2) == pytest.approx(0.02)


def test_feature_pipeline_rejects_malformed():
    pipeline = FeaturePipeline()
    point = MarketDataPoint(
        timestamp=datetime.now(timezone.utc),
        source="mock",
        symbol="BTC/USDT",
        timeframe="1d",
        quality=DataQuality.MALFORMED,
        payload={"bars": []},
    )
    with pytest.raises(ValueError, match="malformed"):
        pipeline.from_ohlcv_point(point)


def test_features_no_lookahead():
    bars = _make_bars(50)
    full = compute_technical_features("BTC/USDT", "1d", bars)
    partial = compute_technical_features("BTC/USDT", "1d", bars[:30])
    assert full.features["last_close"] != partial.features["last_close"] or len(bars) == 30


def test_regime_classifier_uptrend():
    bars = _make_bars(250, drift=0.003)
    fv = compute_technical_features("BTC/USDT", "1d", bars)
    regime = RegimeClassifier().classify(fv)
    assert regime.label in ("bull_trend", "neutral", "range_bound", "high_vol_chop")


def test_aggregator_weighted_consensus():
    agg = SignalAggregator()
    agents = (
        AgentOutput("fundamentals", "t", [{"direction": "BUY"}], [], 0.8, [], "1d", []),
        AgentOutput("technical", "t", [{"direction": "BUY"}], [], 0.7, [], "1d", []),
        AgentOutput("risk_analysis", "t", [{"direction": "HOLD"}], [], 0.6, [], "1d", []),
    )
    bundle = agg.aggregate_swarm("BTC/USDT", agents, regime_label="bull_trend", feature_snapshot={})
    assert bundle.aggregate_direction in ("BUY", "HOLD")
    assert 0 <= bundle.aggregate_confidence <= 1


@pytest.mark.asyncio
async def test_swarm_coordinator_uses_llm_not_exchange():
    llm = MockLLM()
    coord = SwarmCoordinator(llm)
    bundle = await coord.analyze("BTC/USDT", features={"rsi_14": 45}, regime={"label": "neutral"})
    assert len(bundle.agent_outputs) == 3
    assert bundle.agent_outputs[0].agent == "fundamentals"
    assert llm.name == "mock_llm"


def test_llm_cannot_be_execution_authority():
    from trading_intelligence.signals.models import PortfolioRecommendation, QuantSignal

    assert not hasattr(QuantSignal, "place_order")
    assert not hasattr(PortfolioRecommendation, "place_order")


def test_backtest_no_future_leakage():
    bars = _make_bars(80, drift=0.002)
    engine = _engine(min_bars=30, min_trades=1)
    result = engine.run("BTC/USDT", bars)
    assert len(result.equity_curve) > 1
    for entry in result.signals_log:
        assert entry["bar_index"] >= 30


def test_backtest_metrics_reported():
    equity = [10000, 10100, 10200, 10150, 10300]
    pnls = [100, 100, -50, 150]
    m = compute_metrics(equity, pnls)
    assert m.total_trades == 4
    assert m.max_drawdown >= 0


def test_walk_forward_minimum_folds():
    bars = _make_bars(200)
    wf = _walk_forward(train_size=60, test_size=20, step_size=20, min_folds=2)
    result = wf.evaluate("BTC/USDT", bars)
    assert len(result.folds) >= 2


def test_walk_forward_detects_insufficient_data():
    wf = _walk_forward(train_size=120, test_size=30)
    result = wf.evaluate("BTC/USDT", _make_bars(50))
    assert any("Insufficient" in n for n in result.notes)


@pytest.mark.asyncio
async def test_research_cycle_rejects_bad_data():
    from trading_intelligence.pipeline.cycle import ResearchCycle

    market = MockMarketData(quality=DataQuality.UNAVAILABLE)
    cycle = ResearchCycle(market, MockLLM())
    with pytest.raises(ValueError, match="Rejecting"):
        await cycle.run("BTC/USDT")


@pytest.mark.asyncio
async def test_research_cycle_produces_recommendation_not_order():
    from trading_intelligence.pipeline.cycle import ResearchCycle

    market = MockMarketData(_make_bars(100))
    cycle = ResearchCycle(market, MockLLM())
    rec = await cycle.run("BTC/USDT", run_swarm=True)
    assert rec.symbol == "BTC/USDT"
    assert rec.direction in ("BUY", "SELL", "HOLD")
    assert "provider" in rec.reproducibility
    assert "feature_version" in rec.reproducibility


@pytest.mark.asyncio
async def test_research_cycle_traceability():
    from trading_intelligence.pipeline.cycle import ResearchCycle

    market = MockMarketData(_make_bars(100))
    cycle = ResearchCycle(market, MockLLM())
    rec = await cycle.run("BTC/USDT", run_swarm=False)
    assert rec.reproducibility["strategy_version"]
    assert rec.reproducibility["data_timestamp"]
