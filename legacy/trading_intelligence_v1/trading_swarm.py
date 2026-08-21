# -*- coding: utf-8 -*-
"""Trading Swarm — Multi-agent debate architecture for crypto analysis.

Decomposes trading decisions into 3 specialized analyst roles that
engage in a structured debate before reaching consensus:

    1. FUNDAMENTALS ANALYST: News, RSS, macro sentiment
    2. TECHNICAL ANALYST: OHLCV price action, indicators, TVL
    3. RISK MANAGER: Drawdown limits, position sizing, correlation risk

The swarm uses a debate protocol:
    Round 1: Each analyst presents their independent analysis
    Round 2: Cross-examination (analysts challenge each other)
    Round 3: Consensus vote (weighted by confidence)

Only proposals with consensus score >= 75% go to the Telegram
Trading Approval Gate (30-min timeout, REJECT on timeout).

Usage:
    from legacy.trading_intelligence_v1.trading_swarm import run_trading_swarm

    result = await run_trading_swarm("BTC")
    # result: {consensus, score, recommendation, analyst_reports, ...}
"""

import json
import logging
import os
import sys
import time
from typing import Any

logger = logging.getLogger("godclaw.trading_swarm")

OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen3:30b-a3b"
CONSENSUS_THRESHOLD = 75  # Minimum % to proceed to approval gate


# ── Analyst Prompts ──────────────────────────────────────────────────────

_FUNDAMENTALS_PROMPT = """You are a FUNDAMENTALS ANALYST for crypto markets.
Analyze the following news and macro data for {coin}.
Focus on: regulatory news, adoption metrics, developer activity, macro trends.
Rate your conviction: BULLISH (70-100), NEUTRAL (30-70), or BEARISH (0-30).
Respond as JSON: {{"conviction": 0-100, "bias": "BULLISH|NEUTRAL|BEARISH", "analysis": "2-3 sentences", "key_factor": "one main driver"}}"""

_TECHNICAL_PROMPT = """You are a TECHNICAL ANALYST for crypto markets.
Analyze the following price data and indicators for {coin}.
Focus on: trend direction, support/resistance, volume, RSI, moving averages.
Rate your conviction: BULLISH (70-100), NEUTRAL (30-70), or BEARISH (0-30).
Respond as JSON: {{"conviction": 0-100, "bias": "BULLISH|NEUTRAL|BEARISH", "analysis": "2-3 sentences", "key_level": "critical price level"}}"""

_RISK_PROMPT = """You are a RISK MANAGER for a crypto trading desk.
Given these analyst reports, assess the risk of taking a position in {coin}.
Consider: portfolio correlation, drawdown limits ($20/day max), position sizing ($7 max per trade), volatility.
Rate risk: LOW (allow full position), MEDIUM (reduce to 50%), HIGH (block trade).
Respond as JSON: {{"risk_level": "LOW|MEDIUM|HIGH", "max_position_usd": 0-7, "concerns": ["list"], "approval_recommended": true|false}}"""

_CONSENSUS_PROMPT = """You are the SWARM COORDINATOR. Three analysts have debated {coin}:

FUNDAMENTALS: {fundamentals}
TECHNICAL: {technical}
RISK: {risk}

Synthesize their views into a FINAL RECOMMENDATION.
Weight: Fundamentals 30%, Technical 40%, Risk 30%.
Respond as JSON: {{"consensus_score": 0-100, "recommendation": "BUY|SELL|HOLD", "amount_usd": 0-7, "reasoning": "1-2 sentences", "stop_loss_pct": 2-10, "take_profit_pct": 3-20}}"""


async def run_trading_swarm(coin: str, context: dict | None = None) -> dict:
    """Execute a full trading swarm analysis cycle.

    Args:
        coin: Cryptocurrency symbol (e.g., "BTC", "ETH").
        context: Optional pre-fetched data (news, ohlcv, portfolio).

    Returns:
        dict with consensus result, analyst reports, and approval status.
    """
    import asyncio
    start = time.time()

    result = {
        "coin": coin,
        "consensus_score": 0,
        "recommendation": "HOLD",
        "amount_usd": 0,
        "approval_sent": False,
        "analysts": {},
        "status": "analyzing",
    }

    # Gather market data
    market_data = context or await _gather_market_data(coin)

    # Round 1: Independent analysis (parallel)
    logger.info("[SWARM-TRADE] Round 1: Independent analysis for %s", coin)
    fundamentals_task = _run_analyst("fundamentals", coin, market_data)
    technical_task = _run_analyst("technical", coin, market_data)

    analyst_results = await asyncio.gather(
        fundamentals_task, technical_task,
        return_exceptions=True,
    )

    fund_report = analyst_results[0] if isinstance(analyst_results[0], dict) else {"error": str(analyst_results[0])}
    tech_report = analyst_results[1] if isinstance(analyst_results[1], dict) else {"error": str(analyst_results[1])}

    # Round 2: Risk assessment (needs analyst reports)
    logger.info("[SWARM-TRADE] Round 2: Risk assessment")
    risk_report = await _run_risk_manager(coin, fund_report, tech_report)

    result["analysts"] = {
        "fundamentals": fund_report,
        "technical": tech_report,
        "risk": risk_report,
    }

    # Round 3: Consensus synthesis
    logger.info("[SWARM-TRADE] Round 3: Consensus synthesis")
    consensus = await _synthesize_consensus(coin, fund_report, tech_report, risk_report)
    result.update(consensus)

    elapsed = time.time() - start
    result["analysis_time_sec"] = round(elapsed, 1)
    result["status"] = "consensus_reached"

    # Gate: Only proceed if consensus >= threshold
    if result["consensus_score"] >= CONSENSUS_THRESHOLD and result["recommendation"] != "HOLD":
        logger.info("[SWARM-TRADE] Consensus %d%% >= %d%% — sending to approval gate",
                    result["consensus_score"], CONSENSUS_THRESHOLD)
        result["approval_sent"] = await _send_to_approval_gate(result)
    else:
        logger.info("[SWARM-TRADE] Consensus %d%% < %d%% — no action",
                    result["consensus_score"], CONSENSUS_THRESHOLD)
        result["status"] = "below_threshold"

    return result


async def _gather_market_data(coin: str) -> dict:
    """Fetch news + price data for analyst inputs."""
    data: dict[str, Any] = {"coin": coin, "news": [], "ohlcv": {}}

    # Fetch trending news
    try:
        from Data_Worker.skills.trend_scraper import get_viral_topics
        trends = get_viral_topics({"max_topics": 5, "categories": ["crypto"]})
        data["news"] = [t.get("title", "") for t in trends.get("topics", [])]
    except Exception:
        data["news"] = [f"General crypto market analysis for {coin}"]

    # Fetch price data from CoinGecko
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{coin.lower()}/market_chart",
                params={"vs_currency": "usd", "days": "7"},
            )
            if resp.status_code == 200:
                prices = resp.json().get("prices", [])
                if prices:
                    data["ohlcv"] = {
                        "current_price": prices[-1][1],
                        "7d_high": max(p[1] for p in prices),
                        "7d_low": min(p[1] for p in prices),
                        "price_change_7d_pct": round(
                            (prices[-1][1] - prices[0][1]) / prices[0][1] * 100, 2
                        ),
                    }
    except Exception:
        pass

    return data


async def _run_analyst(role: str, coin: str, data: dict) -> dict:
    """Run one analyst agent via Ollama."""
    import httpx

    if role == "fundamentals":
        prompt_template = _FUNDAMENTALS_PROMPT
        context = f"News: {json.dumps(data.get('news', [])[:5])}"
    else:
        prompt_template = _TECHNICAL_PROMPT
        context = f"Price data: {json.dumps(data.get('ohlcv', {}))}"

    prompt = prompt_template.format(coin=coin) + f"\n\nData:\n{context}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt,
                      "stream": False, "think": False,
                      "options": {"temperature": 0.3, "num_predict": 300}},
            )
            if resp.status_code == 200:
                text = resp.json().get("response", "")
                import re
                match = re.search(r"\{.*\}", text, re.DOTALL)
                if match:
                    return json.loads(match.group())
    except Exception as e:
        logger.warning("[SWARM-TRADE] %s analyst failed: %s", role, e)

    return {"conviction": 50, "bias": "NEUTRAL", "analysis": "Analysis unavailable", "error": True}


async def _run_risk_manager(coin: str, fund: dict, tech: dict) -> dict:
    """Run risk manager with analyst reports."""
    import httpx

    prompt = _RISK_PROMPT.format(coin=coin)
    context = f"\nFundamentals: {json.dumps(fund)}\nTechnical: {json.dumps(tech)}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt + context,
                      "stream": False, "think": False,
                      "options": {"temperature": 0.2, "num_predict": 300}},
            )
            if resp.status_code == 200:
                text = resp.json().get("response", "")
                import re
                match = re.search(r"\{.*\}", text, re.DOTALL)
                if match:
                    return json.loads(match.group())
    except Exception as e:
        logger.warning("[SWARM-TRADE] Risk manager failed: %s", e)

    return {"risk_level": "HIGH", "max_position_usd": 0, "concerns": ["Analysis failed"],
            "approval_recommended": False}


async def _synthesize_consensus(coin: str, fund: dict, tech: dict, risk: dict) -> dict:
    """Synthesize analyst reports into consensus recommendation."""
    import httpx

    prompt = _CONSENSUS_PROMPT.format(
        coin=coin,
        fundamentals=json.dumps(fund),
        technical=json.dumps(tech),
        risk=json.dumps(risk),
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt,
                      "stream": False, "think": False,
                      "options": {"temperature": 0.1, "num_predict": 300}},
            )
            if resp.status_code == 200:
                text = resp.json().get("response", "")
                import re
                match = re.search(r"\{.*\}", text, re.DOTALL)
                if match:
                    result = json.loads(match.group())
                    return {
                        "consensus_score": min(100, max(0, result.get("consensus_score", 50))),
                        "recommendation": result.get("recommendation", "HOLD"),
                        "amount_usd": min(7, max(0, result.get("amount_usd", 0))),
                        "reasoning": result.get("reasoning", ""),
                        "stop_loss_pct": result.get("stop_loss_pct", 5),
                        "take_profit_pct": result.get("take_profit_pct", 10),
                    }
    except Exception as e:
        logger.warning("[SWARM-TRADE] Consensus failed: %s", e)

    return {"consensus_score": 0, "recommendation": "HOLD", "amount_usd": 0,
            "reasoning": "Consensus synthesis failed"}


async def _send_to_approval_gate(result: dict) -> bool:
    """Send swarm consensus to Telegram Trading Approval Gate."""
    try:
        from shared.trading_approval_gate import request_trading_approval

        description = (
            f"SWARM CONSENSUS: {result['recommendation']} {result['coin']}\n"
            f"Score: {result['consensus_score']}%\n"
            f"Amount: ${result['amount_usd']}\n"
            f"Reasoning: {result.get('reasoning', '')[:200]}\n"
            f"Stop Loss: {result.get('stop_loss_pct', 5)}% | "
            f"Take Profit: {result.get('take_profit_pct', 10)}%"
        )

        approved = await request_trading_approval(
            action_type="swarm_trade",
            description=description,
            timeout_minutes=30,
        )
        return approved
    except Exception as e:
        logger.warning("[SWARM-TRADE] Approval gate failed: %s", e)
        return False


def register_tools() -> dict:
    """CEO skill tool for trading swarm."""
    async def trading_swarm_analyze(**kwargs) -> dict:
        """Run multi-agent trading swarm analysis for a cryptocurrency."""
        coin = kwargs.get("coin", "BTC")
        return await run_trading_swarm(coin)

    return {
        "trading_swarm_analyze": {
            "fn": trading_swarm_analyze,
            "description": "Run multi-agent trading swarm: 3 analysts debate + consensus vote before approval gate.",
            "parameters": {"coin": "str (e.g., BTC, ETH, SOL)"},
        },
    }
