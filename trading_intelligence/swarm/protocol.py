# -*- coding: utf-8 -*-
"""Crypto swarm protocol — structured agent outputs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from shared.platform.interfaces import LLMRecommendation

from trading_intelligence.signals.models import AgentOutput

PROMPT_VERSION = "swarm-v1"
AGENT_WEIGHTS = {
    "fundamentals": 0.30,
    "technical": 0.40,
    "risk_analysis": 0.30,
}


def recommendation_to_agent_output(rec: LLMRecommendation, agent: str) -> AgentOutput:
    return AgentOutput(
        agent=agent,
        thesis=rec.thesis,
        signals=list(rec.signals),
        evidence=list(rec.evidence),
        confidence=rec.confidence,
        invalidation_conditions=list(rec.invalidation_conditions),
        timeframe=rec.timeframe,
        risks=list(rec.risks),
        model=rec.model,
        provider=rec.provider,
        timestamp=rec.timestamp,
    )


def build_agent_context(
    agent: str,
    symbol: str,
    *,
    features: dict[str, Any] | None = None,
    regime: dict[str, Any] | None = None,
    market_context: dict[str, Any] | None = None,
    peer_outputs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "agent_role": agent,
        "symbol": symbol,
        "prompt_version": PROMPT_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if features:
        ctx["features"] = features
    if regime:
        ctx["regime"] = regime
    if market_context:
        ctx["market_context"] = market_context
    if peer_outputs:
        ctx["peer_outputs"] = peer_outputs
    ctx["instructions"] = _agent_instructions(agent)
    return ctx


def _agent_instructions(agent: str) -> str:
    base = (
        "Return structured research only. You MUST NOT output order instructions, "
        "execution commands, or position sizes that bypass risk/approval gates."
    )
    roles = {
        "fundamentals": (
            "You are the FUNDAMENTAL analyst. Evaluate macro context, adoption, "
            "tokenomics, news sentiment, and on-chain indicators where available."
        ),
        "technical": (
            "You are the TECHNICAL analyst. Evaluate price action, indicators, "
            "support/resistance, momentum, and volume from provided features."
        ),
        "risk_analysis": (
            "You are the RISK ANALYSIS advisor (not the execution risk engine). "
            "Evaluate downside scenarios, conflicting signals, liquidity, and "
            "invalidation levels. You advise only — you do not approve trades."
        ),
    }
    return f"{roles.get(agent, '')} {base}"
