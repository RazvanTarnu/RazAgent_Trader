# -*- coding: utf-8 -*-
"""Trading Intelligence V1.0 — News Sentiment Analyzer.

Uses Ollama local LLM (qwen3:30b-a3b) to score each news item
as HIGH/MEDIUM/LOW impact and POSITIVE/NEGATIVE/NEUTRAL sentiment.

NOT the same as crypto_swarm/sentiment_analyzer.py (that one does Fear & Greed only).
"""
import json
import logging
from typing import Any

import httpx

from .config import (
    OLLAMA_GENERATE,
    OLLAMA_MODEL,
    HTTP_TIMEOUT_SECONDS,
)

logger = logging.getLogger("TradingIntelligence")

# ---------------------------------------------------------------------------
# Prompt template for news scoring
# ---------------------------------------------------------------------------
_SCORE_PROMPT = """You are a crypto market analyst. Score the following news headline for:
1. IMPACT on crypto markets: HIGH, MEDIUM, or LOW
2. SENTIMENT: POSITIVE, NEGATIVE, or NEUTRAL
3. Brief reasoning (1 sentence max)

News headline: "{title}"
Source: {source}
Coins mentioned: {coins}

Respond ONLY in this exact JSON format (no markdown, no extra text):
{{"impact": "HIGH|MEDIUM|LOW", "sentiment": "POSITIVE|NEGATIVE|NEUTRAL", "reasoning": "..."}}"""

_AGGREGATE_PROMPT = """You are a crypto market analyst. Given the following market signals, provide an overall market sentiment assessment.

Fear & Greed Index: {fear_greed_value} ({fear_greed_class})

News Summary (scored by impact):
{news_summary}

Respond ONLY in this exact JSON format:
{{"overall_sentiment": "BULLISH|BEARISH|NEUTRAL", "confidence": 0-100, "key_drivers": ["driver1", "driver2"], "risk_level": "HIGH|MEDIUM|LOW"}}"""


# ---------------------------------------------------------------------------
# Score individual news items via Ollama
# ---------------------------------------------------------------------------
async def score_news(
    news_items: list[dict],
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Score each news item with impact and sentiment using Ollama.

    Modifies items in-place and returns them with added keys:
        impact (HIGH/MEDIUM/LOW), sentiment (POSITIVE/NEGATIVE/NEUTRAL), reasoning
    """
    if not news_items:
        return []

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=120)  # LLM can be slow
        close_client = True

    scored = []
    try:
        for item in news_items:
            prompt = _SCORE_PROMPT.format(
                title=item.get("title", ""),
                source=item.get("source", "Unknown"),
                coins=", ".join(item.get("coins_mentioned", [])) or "general market",
            )

            try:
                resp = await client.post(
                    OLLAMA_GENERATE,
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.2, "num_predict": 200},
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                raw = resp.json().get("response", "").strip()

                # Parse JSON from response (handle potential markdown wrapping)
                json_str = raw
                if "```" in json_str:
                    # Extract JSON between code fences
                    parts = json_str.split("```")
                    for part in parts:
                        part = part.strip()
                        if part.startswith("json"):
                            part = part[4:].strip()
                        if part.startswith("{"):
                            json_str = part
                            break

                result = json.loads(json_str)
                item["impact"] = result.get("impact", "LOW").upper()
                item["sentiment"] = result.get("sentiment", "NEUTRAL").upper()
                item["reasoning"] = result.get("reasoning", "")

                # Validate enums
                if item["impact"] not in ("HIGH", "MEDIUM", "LOW"):
                    item["impact"] = "LOW"
                if item["sentiment"] not in ("POSITIVE", "NEGATIVE", "NEUTRAL"):
                    item["sentiment"] = "NEUTRAL"

            except json.JSONDecodeError:
                logger.warning("Failed to parse Ollama JSON for: %s", item.get("title", "")[:60])
                item["impact"] = "LOW"
                item["sentiment"] = "NEUTRAL"
                item["reasoning"] = "LLM parse error"
            except httpx.HTTPError as exc:
                logger.warning("Ollama HTTP error scoring news: %s", exc)
                item["impact"] = "LOW"
                item["sentiment"] = "NEUTRAL"
                item["reasoning"] = "LLM unavailable"

            scored.append(item)

        logger.info(
            "Scored %d news items: %d HIGH, %d MEDIUM, %d LOW",
            len(scored),
            sum(1 for i in scored if i.get("impact") == "HIGH"),
            sum(1 for i in scored if i.get("impact") == "MEDIUM"),
            sum(1 for i in scored if i.get("impact") == "LOW"),
        )
        return scored

    finally:
        if close_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# Aggregate sentiment from scored news + Fear & Greed
# ---------------------------------------------------------------------------
async def aggregate_sentiment(
    scored_news: list[dict],
    fear_greed: dict,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Combine all signals into an overall market sentiment assessment.

    Returns dict with keys:
        overall_sentiment (BULLISH/BEARISH/NEUTRAL),
        confidence (0-100),
        key_drivers (list[str]),
        risk_level (HIGH/MEDIUM/LOW)
    """
    # Build news summary for the prompt
    if scored_news:
        # Sort by impact: HIGH first
        priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        sorted_news = sorted(scored_news, key=lambda x: priority.get(x.get("impact", "LOW"), 2))
        summary_lines = []
        for item in sorted_news[:10]:  # Top 10 only
            summary_lines.append(
                f"- [{item.get('impact', 'LOW')}] [{item.get('sentiment', 'NEUTRAL')}] "
                f"{item.get('title', '')[:80]}"
            )
        news_summary = "\n".join(summary_lines)
    else:
        news_summary = "No recent news available."

    prompt = _AGGREGATE_PROMPT.format(
        fear_greed_value=fear_greed.get("value", 50),
        fear_greed_class=fear_greed.get("classification", "Neutral"),
        news_summary=news_summary,
    )

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=120)
        close_client = True

    default_result = {
        "overall_sentiment": "NEUTRAL",
        "confidence": 50,
        "key_drivers": [],
        "risk_level": "MEDIUM",
    }

    try:
        resp = await client.post(
            OLLAMA_GENERATE,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 300},
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()

        # Parse JSON
        json_str = raw
        if "```" in json_str:
            parts = json_str.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    json_str = part
                    break

        result = json.loads(json_str)

        # Validate
        if result.get("overall_sentiment") not in ("BULLISH", "BEARISH", "NEUTRAL"):
            result["overall_sentiment"] = "NEUTRAL"
        result["confidence"] = max(0, min(100, int(result.get("confidence", 50))))
        if result.get("risk_level") not in ("HIGH", "MEDIUM", "LOW"):
            result["risk_level"] = "MEDIUM"
        if not isinstance(result.get("key_drivers"), list):
            result["key_drivers"] = []

        logger.info(
            "Aggregate sentiment: %s (confidence=%d%%, risk=%s)",
            result["overall_sentiment"], result["confidence"], result["risk_level"],
        )
        return result

    except Exception as exc:
        logger.error("Aggregate sentiment failed: %s", exc)
        return default_result
    finally:
        if close_client:
            await client.aclose()
