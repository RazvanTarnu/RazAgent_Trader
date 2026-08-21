# -*- coding: utf-8 -*-
"""Trading Intelligence V1.0 — Market Data Collector.

Async fetchers for:
  - CoinGecko top 10 coins by market cap
  - Alternative.me Fear & Greed Index
  - DefiLlama total TVL

All use httpx.AsyncClient with rate limiting and caching.
"""
import asyncio
import logging
import time
from typing import Any

import httpx

from .config import (
    COINGECKO_MARKETS,
    COINGECKO_CHART,
    COINGECKO_RATE_LIMIT_SECONDS,
    FEAR_GREED_URL,
    DEFI_TVL_URL,
    HTTP_TIMEOUT_SECONDS,
    STABLECOINS,
    CHART_DAYS,
)

logger = logging.getLogger("TradingIntelligence")

# ---------------------------------------------------------------------------
# Rate limiter for CoinGecko free tier
# ---------------------------------------------------------------------------
_last_cg_request: float = 0.0
_cg_lock = asyncio.Lock()


async def _rate_limit_coingecko() -> None:
    """Enforce minimum interval between CoinGecko requests."""
    global _last_cg_request
    async with _cg_lock:
        elapsed = time.monotonic() - _last_cg_request
        if elapsed < COINGECKO_RATE_LIMIT_SECONDS:
            await asyncio.sleep(COINGECKO_RATE_LIMIT_SECONDS - elapsed)
        _last_cg_request = time.monotonic()


# ---------------------------------------------------------------------------
# Simple in-memory cache
# ---------------------------------------------------------------------------
_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 300  # 5 minutes


def _get_cached(key: str) -> Any | None:
    if key in _cache:
        ts, data = _cache[key]
        if time.monotonic() - ts < CACHE_TTL:
            return data
        del _cache[key]
    return None


def _set_cached(key: str, data: Any) -> None:
    _cache[key] = (time.monotonic(), data)


# ---------------------------------------------------------------------------
# CoinGecko: Top 10 coins
# ---------------------------------------------------------------------------
async def fetch_top_10_coins(client: httpx.AsyncClient | None = None) -> list[dict]:
    """Fetch top 10 non-stablecoin coins by market cap from CoinGecko.

    Returns list of dicts with keys:
        id, symbol, name, current_price, market_cap, market_cap_rank,
        price_change_percentage_24h, total_volume, high_24h, low_24h,
        ath, ath_change_percentage, circulating_supply
    """
    cached = _get_cached("top_10_coins")
    if cached is not None:
        return cached

    await _rate_limit_coingecko()

    # V10.48: Reduced to 3 coins matching ALLOWED_PAIRS to minimize API calls
    # and avoid CoinGecko 429 rate limiting on free tier.
    params = {
        "vs_currency": "usd",
        "ids": "bitcoin,ethereum,binancecoin",
        "order": "market_cap_desc",
        "per_page": 5,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "24h,7d",
    }

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)
        close_client = True

    try:
        resp = await client.get(COINGECKO_MARKETS, params=params)
        resp.raise_for_status()
        data = resp.json()

        # Filter stablecoins, take top 10
        coins = []
        for coin in data:
            if coin.get("id", "").lower() in STABLECOINS:
                continue
            coins.append({
                "id": coin["id"],
                "symbol": coin["symbol"].upper(),
                "name": coin["name"],
                "current_price": coin.get("current_price", 0),
                "market_cap": coin.get("market_cap", 0),
                "market_cap_rank": coin.get("market_cap_rank", 0),
                "price_change_24h": coin.get("price_change_percentage_24h", 0),
                "price_change_7d": coin.get("price_change_percentage_7d_in_currency", 0),
                "total_volume": coin.get("total_volume", 0),
                "high_24h": coin.get("high_24h", 0),
                "low_24h": coin.get("low_24h", 0),
                "ath": coin.get("ath", 0),
                "ath_change_percentage": coin.get("ath_change_percentage", 0),
                "circulating_supply": coin.get("circulating_supply", 0),
            })
            if len(coins) >= 3:
                break

        _set_cached("top_10_coins", coins)
        logger.info("Fetched %d top coins from CoinGecko", len(coins))
        return coins

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            wait = int(exc.response.headers.get("Retry-After", 60))
            logger.warning("CoinGecko 429 — waiting %ds (Retry-After)", wait)
            await asyncio.sleep(wait)
        else:
            logger.error("CoinGecko HTTP error %d: %s", exc.response.status_code, exc.response.text[:200])
        return []
    except Exception as exc:
        logger.error("CoinGecko fetch failed: %s", exc)
        return []
    finally:
        if close_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# CoinGecko: Price chart for a coin (used by technical_analyzer)
# ---------------------------------------------------------------------------
async def fetch_price_chart(
    coin_id: str,
    days: int = CHART_DAYS,
    client: httpx.AsyncClient | None = None,
) -> list[tuple[float, float]]:
    """Fetch price history for a coin. Returns list of (timestamp_ms, price_usd)."""
    cache_key = f"chart_{coin_id}_{days}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    await _rate_limit_coingecko()

    url = COINGECKO_CHART.format(coin_id=coin_id)
    params = {"vs_currency": "usd", "days": str(days)}

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)
        close_client = True

    try:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

        prices = [(p[0], p[1]) for p in data.get("prices", [])]
        _set_cached(cache_key, prices)
        logger.debug("Fetched %d price points for %s (%dd)", len(prices), coin_id, days)
        return prices

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            wait = int(exc.response.headers.get("Retry-After", 60))
            logger.warning("CoinGecko chart 429 for %s — waiting %ds", coin_id, wait)
            await asyncio.sleep(wait)
        else:
            logger.error("CoinGecko chart error for %s: HTTP %d", coin_id, exc.response.status_code)
        return []
    except Exception as exc:
        logger.error("CoinGecko chart fetch failed for %s: %s", coin_id, exc)
        return []
    finally:
        if close_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# Fear & Greed Index
# ---------------------------------------------------------------------------
async def fetch_fear_greed(client: httpx.AsyncClient | None = None) -> dict:
    """Fetch Crypto Fear & Greed Index from alternative.me.

    Returns dict with keys: value (0-100), classification, timestamp.
    """
    cached = _get_cached("fear_greed")
    if cached is not None:
        return cached

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)
        close_client = True

    try:
        resp = await client.get(FEAR_GREED_URL)
        resp.raise_for_status()
        data = resp.json()

        fg_data = data.get("data", [{}])[0] if data.get("data") else {}
        result = {
            "value": int(fg_data.get("value", 50)),
            "classification": fg_data.get("value_classification", "Neutral"),
            "timestamp": fg_data.get("timestamp", ""),
        }

        _set_cached("fear_greed", result)
        logger.info("Fear & Greed Index: %d (%s)", result["value"], result["classification"])
        return result

    except Exception as exc:
        logger.error("Fear & Greed fetch failed: %s", exc)
        return {"value": 50, "classification": "Neutral", "timestamp": ""}
    finally:
        if close_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# DeFi TVL
# ---------------------------------------------------------------------------
async def fetch_defi_tvl(client: httpx.AsyncClient | None = None) -> dict:
    """Fetch total DeFi TVL from DefiLlama.

    Returns dict with key: total_tvl_usd (float).
    """
    cached = _get_cached("defi_tvl")
    if cached is not None:
        return cached

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)
        close_client = True

    try:
        resp = await client.get(DEFI_TVL_URL)
        resp.raise_for_status()

        # DefiLlama /v2/chains returns a JSON array of chains with tvl field
        data = resp.json()
        if isinstance(data, list):
            tvl = sum(chain.get("tvl", 0) for chain in data if isinstance(chain, dict))
        elif isinstance(data, (int, float)):
            tvl = float(data)
        else:
            tvl = 0.0
        result = {"total_tvl_usd": tvl}

        _set_cached("defi_tvl", result)
        logger.info("DeFi TVL: $%.2fB", tvl / 1e9)
        return result

    except Exception as exc:
        logger.warning("DeFi TVL fetch failed: %s", exc)
        return {"total_tvl_usd": 0.0}
    finally:
        if close_client:
            await client.aclose()
