# -*- coding: utf-8 -*-
"""Crypto swarm coordinator — 3 independent agents, structured aggregation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from shared.platform.interfaces import LLMProvider

from trading_intelligence.signals.aggregator import SignalAggregator
from trading_intelligence.signals.models import AgentOutput, SignalBundle
from trading_intelligence.swarm.agents import (
    run_fundamentals_agent,
    run_risk_analysis_agent,
    run_technical_agent,
)

logger = logging.getLogger("quant.swarm")


class SwarmCoordinator:
    """Run Fundamentals + Technical in parallel, then Risk Analysis advisor."""

    def __init__(self, llm: LLMProvider, aggregator: SignalAggregator | None = None):
        self._llm = llm
        self._aggregator = aggregator or SignalAggregator()

    async def analyze(
        self,
        symbol: str,
        *,
        features: dict[str, Any] | None = None,
        regime: dict[str, Any] | None = None,
        market_context: dict[str, Any] | None = None,
    ) -> SignalBundle:
        fund_task = run_fundamentals_agent(
            self._llm,
            symbol,
            features=features,
            regime=regime,
            market_context=market_context,
        )
        tech_task = run_technical_agent(
            self._llm,
            symbol,
            features=features,
            regime=regime,
        )
        fund, tech = await asyncio.gather(fund_task, tech_task)

        peer = [_agent_to_dict(fund), _agent_to_dict(tech)]
        risk = await run_risk_analysis_agent(
            self._llm,
            symbol,
            features=features,
            regime=regime,
            peer_outputs=peer,
        )

        return self._aggregator.aggregate_swarm(
            symbol=symbol,
            agents=(fund, tech, risk),
            regime_label=regime.get("label", "unknown") if regime else "unknown",
            feature_snapshot=features or {},
        )


def _agent_to_dict(agent: AgentOutput) -> dict[str, Any]:
    return {
        "agent": agent.agent,
        "thesis": agent.thesis,
        "confidence": agent.confidence,
        "signals": agent.signals,
        "risks": agent.risks,
        "invalidation_conditions": agent.invalidation_conditions,
    }
