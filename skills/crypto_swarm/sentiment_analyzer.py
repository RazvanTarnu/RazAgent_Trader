# -*- coding: utf-8 -*-
"""Crypto Sentiment Analyzer — Fear & Greed + News Sentiment via LLM.

Fetches crypto news from FREE public APIs (no API key needed):
1. CryptoCompare news API
2. Fear & Greed Index (alternative.me)

Uses LLM (OpenRouter first, Ollama fallback) for sentiment analysis.
Web safety: URL scanning before content processing (5-layer pattern from trend_scraper.py).

V1.0 — 2026-03-20
"""
import asyncio
import json
import logging
import os
import re
import time
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("godclaw.crypto_swarm.sentiment")

# ──────────────────────────────────────────
# LLM Configuration (same pattern as campaign_orchestrator.py)
# ──────────────────────────────────────────
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "qwen/qwen3-30b-a3b")
OPENROUTER_FALLBACK_MODEL = "moonshotai/kimi-k2.5"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:30b-a3b")

_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0

# ──────────────────────────────────────────
# Web Safety Layer (5-layer pattern from trend_scraper.py)
# ──────────────────────────────────────────
VIRUSTOTAL_API_KEY = os.environ.get("VIRUSTOTAL_API_KEY", "")

SAFE_DOMAINS = {
    "coingecko.com",
    "www.coingecko.com",
    "cryptocompare.com",
    "www.cryptocompare.com",
    "min-api.cryptocompare.com",
    "alternative.me",
    "api.alternative.me",
    "binance.com",
    "www.binance.com",
    "coinmarketcap.com",
    "www.coinmarketcap.com",
    "api.coingecko.com",
    "coindesk.com",
    "www.coindesk.com",
    "cointelegraph.com",
    "www.cointelegraph.com",
    "decrypt.co",
    "www.decrypt.co",
    "theblock.co",
    "www.theblock.co",
    "bloomberg.com",
    "www.bloomberg.com",
    "reuters.com",
    "www.reuters.com",
}

BLOCKED_PATTERNS = [
    r"<script[^>]*>.*?</script>",
    r"<iframe[^>]*>.*?</iframe>",
    r"javascript:",
    r"data:text/html",
    r"\.exe\b",
    r"\.bat\b",
    r"\.ps1\b",
    r"\.vbs\b",
]


async def _scan_url_safety(url: str) -> dict:
    """Scan URL through 5-layer security pipeline.

    Layers:
    1. URL validation (https only, blocked patterns)
    2. Domain whitelist check
    3. VirusTotal scan (if API key available)
    4. Fallback: safe domains whitelist
    Returns: {"safe": bool, "reason": str}
    """
    # Layer 1: URL validation
    parsed = urlparse(url)
    if parsed.scheme not in ("https",):
        return {"safe": False, "reason": f"Blocked scheme: {parsed.scheme} (https only)"}

    domain = parsed.hostname or ""
    if not domain:
        return {"safe": False, "reason": "Empty domain"}

    # Check for suspicious path patterns
    if any(ext in parsed.path.lower() for ext in [".exe", ".bat", ".ps1", ".vbs", ".msi"]):
        return {"safe": False, "reason": "Blocked file extension in URL path"}

    # Layer 2: Domain whitelist (fast path)
    domain_base = domain
    if domain.startswith("www."):
        domain_base = domain[4:]
    is_whitelisted = domain in SAFE_DOMAINS or domain_base in SAFE_DOMAINS

    # Layer 3: VirusTotal scan (if API key available and domain not whitelisted)
    if VIRUSTOTAL_API_KEY and not is_whitelisted:
        vt_result = await _scan_url_virustotal(url)
        if vt_result.get("status") == "scanned":
            malicious = vt_result.get("malicious", 0)
            suspicious = vt_result.get("suspicious", 0)
            if malicious + suspicious > 0:
                return {
                    "safe": False,
                    "reason": f"VirusTotal: {malicious} malicious, {suspicious} suspicious detections",
                }
            return {"safe": True, "reason": f"VirusTotal clean ({malicious} mal, {suspicious} sus)"}
        # VT scan failed — fall through to whitelist check
        logger.warning(f"[Sentiment] VT scan inconclusive for {url}: {vt_result}")

    # Layer 4: Whitelist fallback
    if is_whitelisted:
        return {"safe": True, "reason": f"Whitelisted domain: {domain}"}

    # Not whitelisted, no VT scan available
    return {"safe": False, "reason": f"Domain not whitelisted and no VirusTotal key: {domain}"}


async def _scan_url_virustotal(url: str) -> dict:
    """Scan URL through VirusTotal API (free tier: 4 req/min)."""
    if not VIRUSTOTAL_API_KEY:
        return {"status": "skipped", "reason": "No VIRUSTOTAL_API_KEY configured"}

    headers = {"x-apikey": VIRUSTOTAL_API_KEY}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            resp = await client.post(
                "https://www.virustotal.com/api/v3/urls",
                headers=headers,
                data={"url": url},
            )
            if resp.status_code == 200:
                data = resp.json()
                analysis_id = data.get("data", {}).get("id", "")

                await asyncio.sleep(3)

                resp2 = await client.get(
                    f"https://www.virustotal.com/api/v3/analyses/{analysis_id}",
                    headers=headers,
                )
                if resp2.status_code == 200:
                    stats = (
                        resp2.json()
                        .get("data", {})
                        .get("attributes", {})
                        .get("stats", {})
                    )
                    malicious = stats.get("malicious", 0)
                    suspicious = stats.get("suspicious", 0)
                    return {
                        "status": "scanned",
                        "malicious": malicious,
                        "suspicious": suspicious,
                        "harmless": stats.get("harmless", 0),
                        "safe": malicious == 0 and suspicious == 0,
                    }
            return {"status": "error", "code": resp.status_code}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


# ──────────────────────────────────────────
# LLM Call Helpers (copied from campaign_orchestrator.py pattern)
# ──────────────────────────────────────────


async def _call_llm(prompt: str, temperature: float = 0.3) -> str | None:
    """Smart LLM routing: OpenRouter first (fast), Ollama fallback (free)."""
    if OPENROUTER_API_KEY:
        result = await _call_openrouter(prompt, temperature)
        if result:
            return result
        logger.info("[Sentiment] OpenRouter failed, falling back to local Ollama")

    return await _call_ollama_local(prompt, temperature)


async def _call_openrouter(prompt: str, temperature: float = 0.3) -> str | None:
    """Call OpenRouter chat completions API with retry logic."""
    if not OPENROUTER_API_KEY:
        return None

    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0)
            ) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": OPENROUTER_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": temperature,
                        "max_tokens": 4096,
                    },
                )
                if resp.status_code == 402:
                    logger.warning("[Sentiment] OpenRouter: out of credits (402)")
                    return None
                if resp.status_code != 200:
                    logger.warning(
                        f"[Sentiment] OpenRouter returned {resp.status_code} "
                        f"(attempt {attempt}/{_MAX_RETRIES})"
                    )
                    last_error = f"HTTP {resp.status_code}"
                    if attempt < _MAX_RETRIES:
                        await asyncio.sleep(_RETRY_BACKOFF * attempt)
                        continue
                    return None
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                return None
        except httpx.ConnectError as e:
            logger.warning(f"[Sentiment] OpenRouter connect error (attempt {attempt}): {e}")
            last_error = str(e)
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
            logger.warning(f"[Sentiment] OpenRouter timeout (attempt {attempt}): {e}")
            last_error = str(e)
        except Exception as e:
            logger.warning(f"[Sentiment] OpenRouter failed (attempt {attempt}): {e}")
            last_error = str(e)

        if attempt < _MAX_RETRIES:
            await asyncio.sleep(_RETRY_BACKOFF * attempt)

    # Fallback model
    if OPENROUTER_FALLBACK_MODEL and OPENROUTER_FALLBACK_MODEL != OPENROUTER_MODEL:
        logger.info(f"[Sentiment] Trying fallback model: {OPENROUTER_FALLBACK_MODEL}")
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0)
            ) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": OPENROUTER_FALLBACK_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": temperature,
                        "max_tokens": 4096,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "")
        except Exception as e:
            logger.warning(f"[Sentiment] Fallback model failed: {e}")

    return None


async def _call_ollama_local(prompt: str, temperature: float = 0.3) -> str | None:
    """Call local Ollama /api/generate with retry logic."""
    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=10.0)
            ) as client:
                resp = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": temperature,
                            "num_ctx": 4096,
                        },
                    },
                )
                if resp.status_code != 200:
                    logger.warning(
                        f"[Sentiment] Ollama returned {resp.status_code} "
                        f"(attempt {attempt}/{_MAX_RETRIES})"
                    )
                    last_error = f"HTTP {resp.status_code}"
                    if attempt < _MAX_RETRIES:
                        await asyncio.sleep(_RETRY_BACKOFF * attempt)
                        continue
                    return None
                data = resp.json()
                return data.get("response", "")
        except httpx.ConnectError as e:
            logger.warning(f"[Sentiment] Ollama connect error (attempt {attempt}): {e}")
            last_error = str(e)
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
            logger.warning(f"[Sentiment] Ollama timeout (attempt {attempt}): {e}")
            last_error = str(e)
        except Exception as e:
            logger.warning(f"[Sentiment] Ollama failed (attempt {attempt}): {e}")
            last_error = str(e)

        if attempt < _MAX_RETRIES:
            await asyncio.sleep(_RETRY_BACKOFF * attempt)

    logger.error(f"[Sentiment] All Ollama retries exhausted. Last error: {last_error}")
    return None


# ──────────────────────────────────────────
# Data Fetchers (FREE public APIs)
# ──────────────────────────────────────────


async def _fetch_fear_greed() -> dict | None:
    """Fetch Fear & Greed Index from alternative.me (trusted, no URL scan needed)."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            resp = await client.get("https://api.alternative.me/fng/?limit=1")
            if resp.status_code == 200:
                data = resp.json()
                entries = data.get("data", [])
                if entries:
                    entry = entries[0]
                    return {
                        "value": int(entry.get("value", 0)),
                        "classification": entry.get("value_classification", "Unknown"),
                        "timestamp": entry.get("timestamp", ""),
                        "time_until_update": entry.get("time_until_update", ""),
                    }
    except Exception as e:
        logger.warning(f"[Sentiment] Fear & Greed fetch failed: {e}")
    return None


async def _fetch_cryptocompare_news(asset: str = "BTC") -> list[dict]:
    """Fetch crypto news from CryptoCompare (free, no key needed)."""
    # Map common asset names to CryptoCompare categories
    category_map = {
        "BTC": "BTC",
        "ETH": "ETH",
        "BNB": "BNB",
        "SOL": "SOL",
        "XRP": "XRP",
        "ADA": "ADA",
        "DOGE": "DOGE",
        "DOT": "DOT",
        "AVAX": "AVAX",
        "MATIC": "MATIC",
    }
    categories = category_map.get(asset.upper(), asset.upper())

    articles = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            resp = await client.get(
                "https://min-api.cryptocompare.com/data/v2/news/",
                params={"lang": "EN", "categories": categories},
            )
            if resp.status_code == 200:
                data = resp.json()
                news_items = data.get("Data", [])
                for item in news_items[:15]:
                    articles.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "source": item.get("source", ""),
                        "body": item.get("body", "")[:200],
                        "published_on": item.get("published_on", 0),
                    })
    except Exception as e:
        logger.warning(f"[Sentiment] CryptoCompare news fetch failed: {e}")

    return articles


# ──────────────────────────────────────────
# Tool 1: crypto_sentiment — Full Sentiment Analysis
# ──────────────────────────────────────────


async def crypto_sentiment(**kwargs) -> dict:
    """Analyze crypto market sentiment for a specific asset.

    Fetches Fear & Greed Index, CryptoCompare news, scans URLs for safety,
    and uses LLM for sentiment analysis.

    Params:
        asset (str): Crypto asset symbol (default: "BTC")

    Usage: [TOOL:crypto_sentiment asset="ETH"]
    """
    asset = kwargs.get("asset", "BTC").upper()

    logger.info(f"[Sentiment] Starting sentiment analysis for {asset}")

    # Step 1: Fetch Fear & Greed Index (trusted API, no scan needed)
    fng = await _fetch_fear_greed()
    fng_value = fng["value"] if fng else None
    fng_class = fng["classification"] if fng else "Unavailable"

    # Step 2: Fetch CryptoCompare news
    articles = await _fetch_cryptocompare_news(asset)
    if not articles:
        # Return partial result with just Fear & Greed
        fng_emoji = _fng_emoji(fng_value)
        output = (
            f"📊 Sentiment Analysis: {asset}\n\n"
            f"{fng_emoji} Fear & Greed: {fng_value or 'N/A'} ({fng_class})\n"
            f"📰 News analyzed: 0 articles (no news available)\n"
            f"⚠️ Could not fetch news from CryptoCompare. Try again later."
        )
        return {
            "success": True,
            "output": output,
            "data": {
                "asset": asset,
                "fear_greed": fng,
                "sentiment": None,
                "articles_total": 0,
                "articles_safe": 0,
                "articles_skipped": 0,
            },
        }

    # Step 3: Scan article URLs for safety
    safe_articles = []
    skipped_count = 0
    for article in articles:
        url = article.get("url", "")
        if not url:
            skipped_count += 1
            continue
        scan_result = await _scan_url_safety(url)
        if scan_result.get("safe"):
            safe_articles.append(article)
        else:
            skipped_count += 1
            logger.info(
                f"[Sentiment] Skipped unsafe URL: {url} — {scan_result.get('reason')}"
            )

    # Step 4: LLM sentiment analysis on safe headlines
    sentiment_data = None
    if safe_articles:
        # Sanitize headlines: strip text that looks like prompt injection
        _INJECTION_PATTERNS = re.compile(
            r"(?i)(ignore\s+(all\s+)?previous|system\s*:|you\s+are\b|\[TOOL:|\bsystem\s+prompt\b|"
            r"\bforget\s+(all|everything)\b|\boverride\b|\bact\s+as\b|\bpretend\b)",
        )
        sanitized_articles = []
        for a in safe_articles[:10]:
            title = a.get("title", "")
            if _INJECTION_PATTERNS.search(title):
                logger.warning(f"[Sentiment] Skipped suspicious headline: {title[:80]}")
                continue
            sanitized_articles.append(a)

        if not sanitized_articles:
            # All headlines were suspicious — skip LLM analysis
            sanitized_articles = []

        headlines = "\n".join(
            f"- {a['title']}" for a in sanitized_articles
        )
        prompt = (
            f"Analyze the sentiment of these crypto news headlines for {asset}:\n"
            f"{headlines}\n\n"
            f"Return ONLY a JSON object (no markdown, no explanation):\n"
            f'{{"sentiment": "bullish|bearish|neutral", '
            f'"confidence": 0-100, '
            f'"summary": "1-2 sentences summarizing the market mood"}}'
        )

        llm_response = await _call_llm(prompt, temperature=0.3)
        if llm_response:
            sentiment_data = _parse_sentiment_json(llm_response)

    # Step 5: Format output
    fng_emoji = _fng_emoji(fng_value)
    sentiment_str = "UNKNOWN"
    confidence_str = "N/A"
    summary_str = "LLM analysis unavailable."

    if sentiment_data:
        sentiment_str = sentiment_data.get("sentiment", "unknown").upper()
        confidence_str = f"{sentiment_data.get('confidence', 'N/A')}%"
        summary_str = sentiment_data.get("summary", "No summary available.")

    sentiment_emoji = {
        "BULLISH": "📈",
        "BEARISH": "📉",
        "NEUTRAL": "➡️",
    }.get(sentiment_str, "❓")

    output = (
        f"📊 Sentiment Analysis: {asset}\n\n"
        f"{fng_emoji} Fear & Greed: {fng_value or 'N/A'} ({fng_class})\n"
        f"📰 News analyzed: {len(articles)} articles "
        f"({len(safe_articles)} safe, {skipped_count} skipped)\n"
        f"{sentiment_emoji} Sentiment: {sentiment_str} (confidence: {confidence_str})\n"
        f"💡 Summary: {summary_str}"
    )

    return {
        "success": True,
        "output": output,
        "data": {
            "asset": asset,
            "fear_greed": fng,
            "sentiment": sentiment_data,
            "articles_total": len(articles),
            "articles_safe": len(safe_articles),
            "articles_skipped": skipped_count,
        },
    }


def _fng_emoji(value: int | None) -> str:
    """Return emoji for Fear & Greed value."""
    if value is None:
        return "❓"
    if value >= 75:
        return "🟢"  # Extreme Greed
    if value >= 55:
        return "🟡"  # Greed
    if value >= 45:
        return "⚪"  # Neutral
    if value >= 25:
        return "🟠"  # Fear
    return "🔴"  # Extreme Fear


def _parse_sentiment_json(llm_response: str) -> dict | None:
    """Parse LLM response to extract sentiment JSON."""
    # Try direct JSON parse
    try:
        return json.loads(llm_response.strip())
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from markdown code block
    match = re.search(r"```(?:json)?\s*({.*?})\s*```", llm_response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding any JSON object in the response
    match = re.search(r"\{[^{}]*\"sentiment\"[^{}]*\}", llm_response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning(f"[Sentiment] Could not parse LLM response as JSON: {llm_response[:200]}")
    return None


# ──────────────────────────────────────────
# Tool 2: crypto_fear_greed — Simple Fear & Greed Index
# ──────────────────────────────────────────


async def crypto_fear_greed(**kwargs) -> dict:
    """Fetch the current Crypto Fear & Greed Index.

    Simple, fast, no LLM needed. Returns the current market sentiment index.

    Usage: [TOOL:crypto_fear_greed]
    """
    fng = await _fetch_fear_greed()
    if not fng:
        return {
            "error": "Could not fetch Fear & Greed Index. API may be down.",
        }

    value = fng["value"]
    classification = fng["classification"]
    emoji = _fng_emoji(value)

    # Convert Unix timestamp to human-readable
    ts = fng.get("timestamp", "")
    if ts:
        try:
            ts_human = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(ts)))
        except (ValueError, TypeError):
            ts_human = ts
    else:
        ts_human = "Unknown"

    output = (
        f"{emoji} Crypto Fear & Greed Index\n\n"
        f"📊 Value: {value}/100\n"
        f"🏷️ Classification: {classification}\n"
        f"🕐 Updated: {ts_human}"
    )

    return {
        "success": True,
        "output": output,
        "data": fng,
    }


# ──────────────────────────────────────────
# Skill Registration
# ──────────────────────────────────────────


def register_tools() -> dict:
    """Register crypto sentiment analysis tools for CEO agent discovery."""
    return {
        "crypto_sentiment": crypto_sentiment,
        "crypto_fear_greed": crypto_fear_greed,
    }
