# -*- coding: utf-8 -*-
"""Swarm agent runners — each uses LLMProvider.recommend() only."""

from __future__ import annotations

from typing import Any

from shared.platform.interfaces import LLMProvider

from trading_intelligence.signals.models import AgentOutput
from trading_intelligence.swarm.protocol import build_agent_context, recommendation_to_agent_output


async def run_fundamentals_agent(
    llm: LLMProvider,
    symbol: str,
    *,
    features: dict[str, Any] | None = None,
    regime: dict[str, Any] | None = None,
    market_context: dict[str, Any] | None = None,
) -> AgentOutput:
    ctx = build_agent_context(
        "fundamentals",
        symbol,
        features=features,
        regime=regime,
        market_context=market_context,
    )
    rec = await llm.recommend(ctx)
    return recommendation_to_agent_output(rec, "fundamentals")


async def run_technical_agent(
    llm: LLMProvider,
    symbol: str,
    *,
    features: dict[str, Any] | None = None,
    regime: dict[str, Any] | None = None,
) -> AgentOutput:
    ctx = build_agent_context("technical", symbol, features=features, regime=regime)
    rec = await llm.recommend(ctx)
    return recommendation_to_agent_output(rec, "technical")


async def run_risk_analysis_agent(
    llm: LLMProvider,
    symbol: str,
    *,
    features: dict[str, Any] | None = None,
    regime: dict[str, Any] | None = None,
    peer_outputs: list[dict[str, Any]] | None = None,
) -> AgentOutput:
    ctx = build_agent_context(
        "risk_analysis",
        symbol,
        features=features,
        regime=regime,
        peer_outputs=peer_outputs,
    )
    rec = await llm.recommend(ctx)
    return recommendation_to_agent_output(rec, "risk_analysis")
