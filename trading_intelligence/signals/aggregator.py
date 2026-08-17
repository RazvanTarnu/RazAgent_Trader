# -*- coding: utf-8 -*-
"""Signal aggregation — merges deterministic + swarm outputs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from trading_intelligence.features.technical import FeatureVector
from trading_intelligence.regime.classifier import RegimeContext
from trading_intelligence.signals.models import AgentOutput, QuantSignal, SignalBundle
from trading_intelligence.swarm.protocol import AGENT_WEIGHTS


class SignalAggregator:
    """Combine technical features, regime, and swarm agent outputs."""

    STRATEGY_VERSION = "1.0.0"
    MIN_CONFIDENCE = 0.55

    def from_features(
        self,
        fv: FeatureVector,
        regime: RegimeContext,
    ) -> QuantSignal:
        direction = "HOLD"
        strength = 0.0
        confidence = regime.confidence
        rationale_parts: list[str] = []

        rsi_val = fv.features.get("rsi_14")
        crossover = fv.features.get("ma_crossover")
        mom = fv.features.get("momentum_10")

        if crossover == "BULLISH_CROSS":
            direction = "BUY"
            strength = 0.6
            rationale_parts.append("Bullish MA crossover")
        elif crossover == "BEARISH_CROSS":
            direction = "SELL"
            strength = 0.6
            rationale_parts.append("Bearish MA crossover")

        if isinstance(rsi_val, (int, float)):
            if rsi_val < 30 and direction != "SELL":
                direction = "BUY"
                strength = max(strength, 0.5)
                rationale_parts.append("RSI oversold")
            elif rsi_val > 70 and direction != "BUY":
                direction = "SELL"
                strength = max(strength, 0.5)
                rationale_parts.append("RSI overbought")

        if isinstance(mom, (int, float)):
            if mom > 0.05 and direction == "HOLD":
                direction = "BUY"
                strength = 0.4
                rationale_parts.append("Positive momentum")
            elif mom < -0.05 and direction == "HOLD":
                direction = "SELL"
                strength = 0.4
                rationale_parts.append("Negative momentum")

        if regime.label in ("bear_trend", "high_vol_chop") and direction == "BUY":
            confidence *= 0.85
            rationale_parts.append("Regime headwind for longs")

        return QuantSignal(
            symbol=fv.symbol,
            direction=direction,
            strength=strength,
            confidence=min(1.0, confidence),
            time_horizon=fv.timeframe,
            entry_rationale="; ".join(rationale_parts) or "No strong technical edge",
            invalidation=[f"Regime shift from {regime.label}"],
            required_market_conditions=[f"trend={regime.trend}", f"volatility={regime.volatility}"],
            source="technical_engine",
            timeframe=fv.timeframe,
            features=dict(fv.features),
            regime=regime.label,
            timestamp=datetime.now(timezone.utc),
            strategy_version=self.STRATEGY_VERSION,
            data_source=str(fv.features.get("data_source", "")),
            data_quality=str(fv.features.get("data_quality", "ok")),
        )

    def aggregate_swarm(
        self,
        symbol: str,
        agents: Sequence[AgentOutput],
        *,
        regime_label: str,
        feature_snapshot: dict[str, Any],
        technical_signal: QuantSignal | None = None,
    ) -> SignalBundle:
        signals: list[QuantSignal] = []
        if technical_signal:
            signals.append(technical_signal)

        for agent in agents:
            for raw in agent.signals:
                signals.append(_signal_from_agent(agent, raw, symbol, regime_label))

        direction, confidence = _weighted_consensus(agents)

        return SignalBundle(
            symbol=symbol,
            signals=tuple(signals),
            agent_outputs=tuple(agents),
            regime=regime_label,
            aggregate_confidence=confidence,
            aggregate_direction=direction,
            timestamp=datetime.now(timezone.utc),
            feature_snapshot=feature_snapshot,
            strategy_version=self.STRATEGY_VERSION,
        )

    def merge(
        self,
        technical: QuantSignal,
        bundle: SignalBundle,
    ) -> SignalBundle:
        all_signals = (technical, *bundle.signals)
        return SignalBundle(
            symbol=bundle.symbol,
            signals=all_signals,
            agent_outputs=bundle.agent_outputs,
            regime=bundle.regime,
            aggregate_confidence=_blend_confidence(technical.confidence, bundle.aggregate_confidence),
            aggregate_direction=_resolve_direction(technical.direction, bundle.aggregate_direction),
            timestamp=datetime.now(timezone.utc),
            feature_snapshot=bundle.feature_snapshot,
            strategy_version=self.STRATEGY_VERSION,
        )


def _signal_from_agent(
    agent: AgentOutput,
    raw: dict[str, Any],
    symbol: str,
    regime: str,
) -> QuantSignal:
    direction = str(raw.get("direction", "HOLD")).upper()
    if direction not in ("BUY", "SELL", "HOLD"):
        direction = "HOLD"
    return QuantSignal(
        symbol=symbol,
        direction=direction,
        strength=float(raw.get("strength", 0.0)),
        confidence=float(raw.get("confidence", agent.confidence)),
        time_horizon=str(raw.get("time_horizon", agent.timeframe or "unknown")),
        entry_rationale=str(raw.get("entry_rationale", agent.thesis)),
        invalidation=[str(x) for x in raw.get("invalidation", agent.invalidation_conditions)],
        required_market_conditions=[str(x) for x in raw.get("required_market_conditions", [])],
        source=agent.agent,
        timeframe=agent.timeframe or "unknown",
        regime=regime,
        timestamp=agent.timestamp or datetime.now(timezone.utc),
    )


def _weighted_consensus(agents: Sequence[AgentOutput]) -> tuple[str, float]:
    votes: dict[str, float] = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
    total_weight = 0.0
    conf_sum = 0.0

    for agent in agents:
        weight = AGENT_WEIGHTS.get(agent.agent, 0.25)
        total_weight += weight
        conf_sum += agent.confidence * weight
        direction = _dominant_direction(agent)
        votes[direction] += weight * max(0.0, min(1.0, agent.confidence))

    if total_weight == 0:
        return "HOLD", 0.0

    best = max(votes, key=lambda k: votes[k])
    if votes[best] < 0.2:
        best = "HOLD"
    return best, round(conf_sum / total_weight, 4)


def _dominant_direction(agent: AgentOutput) -> str:
    if not agent.signals:
        return "HOLD"
    counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    for s in agent.signals:
        d = str(s.get("direction", "HOLD")).upper()
        if d in counts:
            counts[d] += 1
    return max(counts, key=lambda k: counts[k])


def _blend_confidence(a: float, b: float) -> float:
    return round((a + b) / 2, 4)


def _resolve_direction(tech: str, swarm: str) -> str:
    if tech == swarm:
        return tech
    if tech == "HOLD":
        return swarm
    if swarm == "HOLD":
        return tech
    return "HOLD"
