# -*- coding: utf-8 -*-
"""Trading Swarm V1.0 — 3-Agent Consensus Debate

Architecture:
  Round 1 (parallel): Fundamentals Analyst + Technical Analyst + Risk Manager
  Round 2: Risk assessment (Risk Manager evaluates Round 1)
  Round 3: Consensus synthesis (weighted 30/40/30%)
  Only consensus >= 75% confidence triggers Trading Approval Gate (30min timeout)

Agents:
  - Fundamentals Analyst (30%): News sentiment, macro context, on-chain signals
  - Technical Analyst  (40%): OHLCV, RSI, SMA50/200, support/resistance, patterns
  - Risk Manager       (30%): Drawdown limits, position sizing, portfolio exposure

IMPORTANT: All trade proposals MUST go through shared/trading_approval_gate.py
           (30-minute timeout, TIMEOUT = REJECT).
"""
import os
import json
import logging
import asyncio
from datetime import datetime

logger = logging.getLogger("godclaw.trading_swarm")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = "qwen3:30b-a3b"
CONSENSUS_THRESHOLD = 75  # Minimum % to propose a trade
AGENT_WEIGHTS = {"fundamentals": 0.30, "technical": 0.40, "risk": 0.30}


async def _llm_call(system_prompt: str, user_prompt: str) -> str:
    """Call Ollama LLM with a system + user prompt. Returns raw text."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                    "options": {"num_ctx": 4096},
                },
            )
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "")
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return json.dumps({"error": str(e)})


def _parse_agent_response(raw: str) -> dict:
    """Extract JSON from agent response, handling markdown fences."""
    text = raw.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"direction": "NEUTRAL", "confidence": 30, "reasoning": text[:300]}


# ═══════════════════════════════════════════════════════
# AGENT PROMPTS
# ═══════════════════════════════════════════════════════

_FUNDAMENTALS_SYSTEM = """You are the Fundamentals Analyst in a 3-agent trading debate.
Analyze the coin from a MACRO and NEWS perspective.
Consider: recent news sentiment, regulatory developments, on-chain metrics,
market fear/greed index, whale movements, upcoming events.
Respond ONLY with JSON: {"direction": "BUY|SELL|NEUTRAL", "confidence": 0-100, "reasoning": "..."}"""

_TECHNICAL_SYSTEM = """You are the Technical Analyst in a 3-agent trading debate.
Analyze the coin from a TECHNICAL perspective using price data provided.
Consider: RSI (overbought >70, oversold <30), SMA50 vs SMA200 (golden/death cross),
support/resistance levels, 24h/7d momentum, volume trends, chart patterns.
Respond ONLY with JSON: {"direction": "BUY|SELL|NEUTRAL", "confidence": 0-100, "reasoning": "..."}"""

_RISK_SYSTEM = """You are the Risk Manager in a 3-agent trading debate.
Given the Fundamentals and Technical analyses below, evaluate:
1. Is the risk/reward ratio acceptable? (minimum 1:2)
2. Is the position size safe given portfolio exposure?
3. Are there conflicting signals that reduce confidence?
4. Would you approve this trade with a 5% stop-loss?
Respond ONLY with JSON: {"approved": true|false, "confidence": 0-100, "position_pct": 1-10, "reasoning": "..."}"""


async def _run_fundamentals(coin: str, news_context: str) -> dict:
    """Round 1a: Fundamentals Analyst."""
    prompt = f"Coin: {coin}\nRecent news and context:\n{news_context}"
    raw = await _llm_call(_FUNDAMENTALS_SYSTEM, prompt)
    result = _parse_agent_response(raw)
    result["agent"] = "fundamentals"
    return result


async def _run_technical(coin: str, technical_context: str) -> dict:
    """Round 1b: Technical Analyst."""
    prompt = f"Coin: {coin}\nTechnical data:\n{technical_context}"
    raw = await _llm_call(_TECHNICAL_SYSTEM, prompt)
    result = _parse_agent_response(raw)
    result["agent"] = "technical"
    return result


async def _run_risk_manager(coin: str, fund_result: dict, tech_result: dict) -> dict:
    """Round 2: Risk Manager evaluates both analyses."""
    prompt = (
        f"Coin: {coin}\n\n"
        f"Fundamentals Analysis:\n{json.dumps(fund_result, indent=2)}\n\n"
        f"Technical Analysis:\n{json.dumps(tech_result, indent=2)}"
    )
    raw = await _llm_call(_RISK_SYSTEM, prompt)
    result = _parse_agent_response(raw)
    result["agent"] = "risk"
    return result


def _compute_consensus(fund: dict, tech: dict, risk: dict) -> dict:
    """Round 3: Weighted consensus synthesis.

    Weights: Fundamentals 30%, Technical 40%, Risk 30%.
    Direction resolved by majority vote.
    Confidence = weighted average.
    """
    directions = {
        "fundamentals": fund.get("direction", "NEUTRAL").upper(),
        "technical": tech.get("direction", "NEUTRAL").upper(),
    }
    # Risk manager doesn't vote direction — it approves/rejects
    risk_approved = risk.get("approved", False)

    # Direction: majority of fund + tech
    direction_votes = list(directions.values())
    if direction_votes[0] == direction_votes[1]:
        consensus_direction = direction_votes[0]
    else:
        consensus_direction = "NEUTRAL"  # Disagreement = no trade

    # Weighted confidence
    fund_conf = min(100, max(0, int(fund.get("confidence", 30))))
    tech_conf = min(100, max(0, int(tech.get("confidence", 30))))
    risk_conf = min(100, max(0, int(risk.get("confidence", 30))))

    weighted_confidence = int(
        fund_conf * AGENT_WEIGHTS["fundamentals"]
        + tech_conf * AGENT_WEIGHTS["technical"]
        + risk_conf * AGENT_WEIGHTS["risk"]
    )

    # Risk manager veto: if not approved, cap confidence at 40%
    if not risk_approved:
        weighted_confidence = min(weighted_confidence, 40)
        consensus_direction = "NEUTRAL"

    position_pct = risk.get("position_pct", 5)

    return {
        "direction": consensus_direction,
        "confidence": weighted_confidence,
        "position_pct": position_pct,
        "risk_approved": risk_approved,
        "agents": {
            "fundamentals": {"direction": directions["fundamentals"], "confidence": fund_conf},
            "technical": {"direction": directions["technical"], "confidence": tech_conf},
            "risk": {"approved": risk_approved, "confidence": risk_conf},
        },
        "reasoning": (
            f"Fund: {fund.get('reasoning', '')[:100]} | "
            f"Tech: {tech.get('reasoning', '')[:100]} | "
            f"Risk: {risk.get('reasoning', '')[:100]}"
        ),
    }


async def run_swarm_analysis(
    coin: str,
    news_context: str = "",
    technical_context: str = "",
) -> dict:
    """Execute the full 3-agent trading swarm debate.

    Returns consensus dict with direction, confidence, and agent details.
    Only triggers approval gate if confidence >= CONSENSUS_THRESHOLD (75%).
    """
    logger.info(f"[SWARM] Starting 3-agent debate for {coin}")

    # Round 1: Parallel analysis (Fundamentals + Technical)
    fund_task = asyncio.create_task(_run_fundamentals(coin, news_context))
    tech_task = asyncio.create_task(_run_technical(coin, technical_context))
    fund_result, tech_result = await asyncio.gather(fund_task, tech_task)

    logger.info(
        f"[SWARM] Round 1 — Fund: {fund_result.get('direction')} ({fund_result.get('confidence')}%), "
        f"Tech: {tech_result.get('direction')} ({tech_result.get('confidence')}%)"
    )

    # Round 2: Risk Manager evaluates
    risk_result = await _run_risk_manager(coin, fund_result, tech_result)
    logger.info(
        f"[SWARM] Round 2 — Risk: approved={risk_result.get('approved')}, "
        f"confidence={risk_result.get('confidence')}%"
    )

    # Round 3: Consensus synthesis
    consensus = _compute_consensus(fund_result, tech_result, risk_result)
    consensus["coin"] = coin
    consensus["timestamp"] = datetime.now().isoformat()

    logger.info(
        f"[SWARM] Round 3 — Consensus: {consensus['direction']} "
        f"({consensus['confidence']}%) — "
        f"{'ABOVE' if consensus['confidence'] >= CONSENSUS_THRESHOLD else 'BELOW'} threshold"
    )

    return consensus


async def trading_swarm_analyze(**kwargs) -> dict:
    """CEO skill tool: Run 3-agent trading debate for a coin.

    Args:
        coin: Coin symbol (e.g., BTC, ETH, SOL)
        news: Optional news context string
        technical: Optional technical data string
    """
    coin = kwargs.get("coin", "BTC").upper()
    news = kwargs.get("news", f"Analyze {coin} from a fundamental perspective.")
    technical = kwargs.get("technical", f"Analyze {coin} from a technical perspective using available data.")

    consensus = await run_swarm_analysis(coin, news, technical)

    output_lines = [
        f"🐝 Trading Swarm Debate — {coin}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 Fundamentals: {consensus['agents']['fundamentals']['direction']} "
        f"({consensus['agents']['fundamentals']['confidence']}%)",
        f"📈 Technical: {consensus['agents']['technical']['direction']} "
        f"({consensus['agents']['technical']['confidence']}%)",
        f"🛡️ Risk Manager: {'✅ Approved' if consensus['agents']['risk']['approved'] else '❌ Rejected'} "
        f"({consensus['agents']['risk']['confidence']}%)",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 Consensus: {consensus['direction']} ({consensus['confidence']}%)",
    ]

    if consensus["confidence"] >= CONSENSUS_THRESHOLD and consensus["direction"] != "NEUTRAL":
        output_lines.append(f"✅ ABOVE {CONSENSUS_THRESHOLD}% threshold — eligible for Trading Approval Gate")
    else:
        output_lines.append(f"⛔ BELOW {CONSENSUS_THRESHOLD}% threshold — no trade proposed")

    return {
        "success": True,
        "output": "\n".join(output_lines),
        "consensus": consensus,
    }


# ═══════════════════════════════════════════════════════
# SKILL REGISTRATION
# ═══════════════════════════════════════════════════════

SKILL_TOOLS = {
    "trading_swarm_analyze": {
        "fn": trading_swarm_analyze,
        "description": (
            "Run 3-agent trading debate (Fundamentals + Technical + Risk Manager). "
            "Only proposes trade if consensus >= 75%. coin: BTC|ETH|SOL etc."
        ),
        "parameters": {
            "coin": "string",
            "news": "string",
            "technical": "string",
        },
    },
}
