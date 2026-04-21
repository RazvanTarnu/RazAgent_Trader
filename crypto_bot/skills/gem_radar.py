# -*- coding: utf-8 -*-
"""Gem Radar — V11.80

Discovers early-stage, high-potential crypto projects ("gems") from
public data sources. Evaluates fundamentals via LLM (qwen3:30b).

Sources (no API key needed):
  - CoinGecko: recently added coins + trending
  - CryptoPanic RSS: ICO/presale/airdrop news
  - Binance new listings announcements

Usage:
    from crypto_bot.skills.gem_radar import scan_for_gems, evaluate_gem
    gems = await scan_for_gems()
    scored = await evaluate_gem(gems[0])
"""
import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("godclaw.gem_radar")

PROJECT_ROOT = Path("D:/RazAgent_Enterprise")
OLLAMA_URL = "http://127.0.0.1:11434"
from shared.config import OLLAMA_MODEL

# Keywords that signal early-stage projects
GEM_KEYWORDS = frozenset({
    "ico", "presale", "airdrop", "launch", "listing", "launchpad",
    "ido", "ieo", "token sale", "fair launch", "new coin", "just listed",
    "mainnet launch", "testnet", "seed round", "private sale",
})

# Hot sectors for 2025-2026
HOT_SECTORS = ["AI", "DePIN", "RWA", "Layer 2", "Gaming", "SocialFi", "DeFAI", "Meme"]

_EVAL_PROMPT = """\
You are a Crypto Venture Capital Analyst specializing in early-stage projects.
Evaluate this crypto project for explosive growth potential.

PROJECT DATA:
Name: {name}
Stage: {stage}
Description: {description}
Sector: {sector}
Source: {source}

EVALUATION CRITERIA:
- Current hype level (social mentions, news volume)
- Utility and real-world use case
- Sector trend alignment (AI, DePIN, RWA are hot in 2026)
- Team credibility indicators
- Rug-pull / scam red flags

Be EXTREMELY skeptical. Most new projects fail. Only score > 80 for genuinely promising ones.

Output ONLY valid JSON:
{{"gem_score": <1-100>, "verdict": "<1-2 sentences>", "risk": "Low|Medium|High|Extreme", "sector_match": "<matched hot sector or 'None'>"}}"""


async def _fetch_coingecko_new() -> list[dict]:
    """Fetch recently added coins from CoinGecko (free, no API key)."""
    import httpx
    gems = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Trending coins (high momentum)
            resp = await client.get(
                "https://api.coingecko.com/api/v3/search/trending",
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("coins", [])[:5]:
                    coin = item.get("item", {})
                    gems.append({
                        "name": coin.get("name", "Unknown"),
                        "symbol": coin.get("symbol", "?"),
                        "stage": "Trending",
                        "description": f"Market cap rank #{coin.get('market_cap_rank', '?')}, "
                                       f"score {coin.get('score', 0)}",
                        "sector": _guess_sector(coin.get("name", "")),
                        "source": "CoinGecko Trending",
                        "score_hint": coin.get("score", 0),
                    })
    except Exception as e:
        logger.debug("CoinGecko trending fetch failed: %s", e)

    # Also try recently added (new coins)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/coins/list",
                params={"include_platform": "false"},
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                all_coins = resp.json()
                # Last 10 entries are typically the newest
                for coin in all_coins[-10:]:
                    name = coin.get("name", "")
                    gems.append({
                        "name": name,
                        "symbol": coin.get("symbol", "?"),
                        "stage": "New Listing",
                        "description": f"Recently added to CoinGecko",
                        "sector": _guess_sector(name),
                        "source": "CoinGecko New",
                        "score_hint": 3,
                    })
    except Exception as e:
        logger.debug("CoinGecko new coins fetch failed: %s", e)

    return gems


async def _fetch_cryptopanic_gems() -> list[dict]:
    """Fetch ICO/presale/airdrop news from CryptoPanic RSS (no API key)."""
    import httpx
    gems = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://cryptopanic.com/news/rss/",
                headers={"User-Agent": "Mozilla/5.0 (compatible; RazAgent/1.0)"},
                follow_redirects=True,
            )
            if resp.status_code == 200:
                xml_text = resp.text.replace('xmlns=', 'xmlns_ignore=')
                try:
                    root = ET.fromstring(xml_text)
                    for item in root.findall(".//item")[:30]:
                        title = (item.find("title").text or "").strip() if item.find("title") is not None else ""
                        title_lower = title.lower()
                        # Filter for gem-related keywords
                        if any(kw in title_lower for kw in GEM_KEYWORDS):
                            gems.append({
                                "name": title[:80],
                                "symbol": "?",
                                "stage": _classify_stage(title_lower),
                                "description": title,
                                "sector": _guess_sector(title),
                                "source": "CryptoPanic RSS",
                                "score_hint": 5,
                            })
                except ET.ParseError:
                    pass
    except Exception as e:
        logger.debug("CryptoPanic fetch failed: %s", e)

    return gems[:5]


def _classify_stage(text: str) -> str:
    """Classify project stage from text keywords."""
    if "presale" in text or "private sale" in text or "seed" in text:
        return "Presale"
    if "airdrop" in text:
        return "Airdrop"
    if "ico" in text or "ido" in text or "ieo" in text:
        return "ICO/IDO"
    if "launch" in text or "listing" in text or "listed" in text:
        return "New Listing"
    return "Early Stage"


def _guess_sector(name: str) -> str:
    """Guess sector from project name/description."""
    name_lower = name.lower()
    sector_map = {
        "ai": "AI", "gpt": "AI", "neural": "AI", "agent": "AI",
        "depin": "DePIN", "iot": "DePIN", "sensor": "DePIN",
        "rwa": "RWA", "real world": "RWA", "tokeniz": "RWA",
        "layer": "Layer 2", "l2": "Layer 2", "rollup": "Layer 2",
        "game": "Gaming", "play": "Gaming", "metaverse": "Gaming",
        "social": "SocialFi", "meme": "Meme", "pepe": "Meme", "dog": "Meme",
        "defi": "DeFi", "swap": "DeFi", "lend": "DeFi",
    }
    for keyword, sector in sector_map.items():
        if keyword in name_lower:
            return sector
    return "Unknown"


async def scan_for_gems(params: dict | None = None) -> dict:
    """Scan multiple sources for early-stage gem projects.

    Returns:
        dict with success, output, gems (list of dicts), count.
    """
    params = params or {}
    max_gems = int(params.get("max_gems", 10))

    all_gems = []

    # Fetch from all sources
    cg_gems = await _fetch_coingecko_new()
    all_gems.extend(cg_gems)

    cp_gems = await _fetch_cryptopanic_gems()
    all_gems.extend(cp_gems)

    # Deduplicate by name
    seen = set()
    unique = []
    for g in all_gems:
        key = g["name"].lower().strip()[:30]
        if key not in seen:
            seen.add(key)
            unique.append(g)

    # Sort by score_hint descending
    unique.sort(key=lambda x: x.get("score_hint", 0), reverse=True)
    top_gems = unique[:max_gems]

    # Build output
    lines = [f"💎 Gem Radar: {len(top_gems)} projects discovered"]
    for i, g in enumerate(top_gems, 1):
        lines.append(f"  {i}. [{g['stage']}] {g['name']} ({g['symbol']}) — {g['sector']}")
    lines.append(f"\nSources: CoinGecko Trending + New, CryptoPanic RSS")

    return {
        "success": True,
        "output": "\n".join(lines),
        "gems": top_gems,
        "count": len(top_gems),
    }


async def evaluate_gem(params: dict) -> dict:
    """Evaluate a single gem project via LLM fundamental analysis.

    Args (via params dict):
        gem: dict with name, stage, description, sector, source.

    Returns:
        dict with success, output, evaluation (gem_score, verdict, risk).
    """
    import httpx

    gem = params.get("gem", {})
    if not gem or not gem.get("name"):
        return {"success": False, "output": "No gem data provided"}

    prompt = _EVAL_PROMPT.format(
        name=gem.get("name", "Unknown"),
        stage=gem.get("stage", "Unknown"),
        description=gem.get("description", "No description"),
        sector=gem.get("sector", "Unknown"),
        source=gem.get("source", "Unknown"),
    )

    evaluation = None

    # Ollama-first
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are a crypto VC analyst. Output only JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.4, "num_ctx": 4096},
                },
            )
            if resp.status_code == 200:
                content = resp.json().get("message", {}).get("content", "")
                evaluation = json.loads(content)
    except (json.JSONDecodeError, Exception) as e:
        logger.debug("Ollama gem eval failed: %s", e)

    if not evaluation:
        # Fallback: heuristic scoring
        score = 40
        if gem.get("sector") in HOT_SECTORS:
            score += 20
        if gem.get("stage") in ("Trending", "New Listing"):
            score += 10
        evaluation = {
            "gem_score": score,
            "verdict": f"Heuristic score (LLM unavailable). Sector: {gem.get('sector', '?')}",
            "risk": "High",
            "sector_match": gem.get("sector", "None"),
        }

    # Validate
    evaluation.setdefault("gem_score", 50)
    evaluation["gem_score"] = max(1, min(100, int(evaluation["gem_score"])))

    return {
        "success": True,
        "output": (
            f"💎 {gem['name']} — Score: {evaluation['gem_score']}/100\n"
            f"  Verdict: {evaluation.get('verdict', '?')}\n"
            f"  Risk: {evaluation.get('risk', '?')}\n"
            f"  Sector: {evaluation.get('sector_match', '?')}"
        ),
        "evaluation": evaluation,
        "gem_name": gem["name"],
    }


async def full_gem_sweep(params: dict | None = None) -> dict:
    """Full pipeline: scan → evaluate top gems → return scored results.

    Returns dict with success, output, scored_gems, alerts (gems > 80 score).
    """
    import asyncio

    # Step 1: Scan
    scan_result = await scan_for_gems({"max_gems": 5})
    if not scan_result.get("gems"):
        return {"success": True, "output": "No gems found", "scored_gems": [], "alerts": []}

    # Step 2: Evaluate each
    scored = []
    for gem in scan_result["gems"][:5]:
        eval_result = await evaluate_gem({"gem": gem})
        if eval_result.get("success"):
            gem["evaluation"] = eval_result.get("evaluation", {})
            scored.append(gem)
        await asyncio.sleep(0.5)  # Rate limit LLM calls

    # Sort by gem_score
    scored.sort(key=lambda x: x.get("evaluation", {}).get("gem_score", 0), reverse=True)

    # Identify high-potential alerts (score > 80)
    alerts = [g for g in scored if g.get("evaluation", {}).get("gem_score", 0) > 80]

    # Build output
    lines = [f"💎 <b>GEM RADAR SWEEP</b> — {len(scored)} evaluated"]
    lines.append("─" * 28)
    for g in scored:
        ev = g.get("evaluation", {})
        score = ev.get("gem_score", 0)
        icon = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
        lines.append(f"{icon} {g['name']} ({g['symbol']}): {score}/100")
        lines.append(f"   {ev.get('verdict', '?')[:80]}")
        lines.append(f"   Risk: {ev.get('risk', '?')} | Sector: {ev.get('sector_match', '?')}")
    lines.append("─" * 28)
    if alerts:
        lines.append(f"🚨 {len(alerts)} HIGH-POTENTIAL ALERTS (>80 score)")
    else:
        lines.append("No high-potential gems this sweep")

    return {
        "success": True,
        "output": "\n".join(lines),
        "scored_gems": scored,
        "alerts": alerts,
    }
