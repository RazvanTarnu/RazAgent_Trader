# -*- coding: utf-8 -*-
"""Trading Intelligence V1.0 — News Aggregator.

Sources:
  - CryptoCompare news API (free, no key)
  - CryptoPanic API (optional, CRYPTOPANIC_API_KEY from keyring)
  - RSS feeds: CoinDesk, Decrypt, The Block

Deduplicates by URL using SQLite news_cache table.
"""
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import (
    CRYPTOCOMPARE_NEWS_URL,
    CRYPTOPANIC_NEWS_URL,
    RSS_FEEDS,
    HTTP_TIMEOUT_SECONDS,
    DB_PATH,
    NEWS_CACHE_TTL_HOURS,
)

logger = logging.getLogger("TradingIntelligence")

# Top coins to detect in news titles/bodies
_COIN_KEYWORDS: dict[str, str] = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "xrp": "xrp", "ripple": "xrp",
    "cardano": "cardano", "ada": "cardano",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "avalanche": "avalanche", "avax": "avalanche",
    "polkadot": "polkadot", "dot": "polkadot",
    "chainlink": "chainlink", "link": "chainlink",
    "bnb": "binancecoin", "binance": "binancecoin",
    "toncoin": "toncoin", "ton": "toncoin",
    "sui": "sui",
    "near": "near",
}


def _detect_coins(text: str) -> list[str]:
    """Detect mentioned coins in text."""
    text_lower = text.lower()
    found = set()
    for keyword, coin_id in _COIN_KEYWORDS.items():
        # Word boundary match to avoid false positives
        if re.search(rf"\b{re.escape(keyword)}\b", text_lower):
            found.add(coin_id)
    return sorted(found)


# ---------------------------------------------------------------------------
# CryptoCompare news
# ---------------------------------------------------------------------------
async def _fetch_cryptocompare(client: httpx.AsyncClient) -> list[dict]:
    """Fetch latest news from CryptoCompare (free, no key)."""
    try:
        resp = await client.get(CRYPTOCOMPARE_NEWS_URL, timeout=HTTP_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()

        items = []
        for article in data.get("Data", [])[:20]:
            title = article.get("title", "").strip()
            url = article.get("url", "").strip()
            if not title or not url:
                continue
            items.append({
                "title": title,
                "url": url,
                "source": article.get("source", "CryptoCompare"),
                "published_at": datetime.fromtimestamp(
                    article.get("published_on", 0), tz=timezone.utc
                ).isoformat(),
                "coins_mentioned": _detect_coins(title + " " + article.get("body", "")),
            })
        logger.info("CryptoCompare: fetched %d news items", len(items))
        return items

    except Exception as exc:
        logger.error("CryptoCompare news failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# CryptoPanic news (optional key)
# ---------------------------------------------------------------------------
async def _fetch_cryptopanic(client: httpx.AsyncClient) -> list[dict]:
    """Fetch news from CryptoPanic (requires CRYPTOPANIC_API_KEY)."""
    api_key = os.environ.get("CRYPTOPANIC_API_KEY")
    if not api_key:
        # Try keyring
        try:
            import keyring
            api_key = keyring.get_password("AgentCeoR", "CRYPTOPANIC_API_KEY")
        except Exception:
            pass
    if not api_key:
        logger.debug("CryptoPanic: no API key, skipping")
        return []

    try:
        params = {
            "auth_token": api_key,
            "public": "true",
            "kind": "news",
        }
        resp = await client.get(CRYPTOPANIC_NEWS_URL, params=params, timeout=HTTP_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()

        items = []
        for post in data.get("results", [])[:20]:
            title = post.get("title", "").strip()
            url = post.get("url", "").strip()
            if not title or not url:
                continue
            # Extract coin mentions from CryptoPanic currencies field
            cp_coins = [c.get("code", "").lower() for c in post.get("currencies", [])]
            detected = _detect_coins(title)
            all_coins = sorted(set(detected + [_COIN_KEYWORDS.get(c, c) for c in cp_coins if c in _COIN_KEYWORDS]))

            items.append({
                "title": title,
                "url": url,
                "source": post.get("source", {}).get("title", "CryptoPanic"),
                "published_at": post.get("published_at", ""),
                "coins_mentioned": all_coins,
            })
        logger.info("CryptoPanic: fetched %d news items", len(items))
        return items

    except Exception as exc:
        logger.error("CryptoPanic news failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# RSS feeds
# ---------------------------------------------------------------------------
async def _fetch_rss_feed(client: httpx.AsyncClient, name: str, url: str) -> list[dict]:
    """Fetch and parse a single RSS feed."""
    try:
        resp = await client.get(url, timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True)
        resp.raise_for_status()
        xml_text = resp.text

        root = ET.fromstring(xml_text)

        items = []
        # Handle both RSS 2.0 and Atom
        # RSS 2.0: channel/item
        for item in root.findall(".//item")[:15]:
            title_el = item.find("title")
            link_el = item.find("link")
            pubdate_el = item.find("pubDate")

            title = (title_el.text or "").strip() if title_el is not None else ""
            link = (link_el.text or "").strip() if link_el is not None else ""
            pubdate = (pubdate_el.text or "").strip() if pubdate_el is not None else ""

            if not title or not link:
                continue

            items.append({
                "title": title,
                "url": link,
                "source": name,
                "published_at": pubdate,
                "coins_mentioned": _detect_coins(title),
            })

        # Atom: entry
        if not items:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall(".//atom:entry", ns)[:15]:
                title_el = entry.find("atom:title", ns)
                link_el = entry.find("atom:link", ns)
                updated_el = entry.find("atom:updated", ns)

                title = (title_el.text or "").strip() if title_el is not None else ""
                link = link_el.get("href", "").strip() if link_el is not None else ""
                updated = (updated_el.text or "").strip() if updated_el is not None else ""

                if not title or not link:
                    continue

                items.append({
                    "title": title,
                    "url": link,
                    "source": name,
                    "published_at": updated,
                    "coins_mentioned": _detect_coins(title),
                })

        logger.debug("RSS %s: fetched %d items", name, len(items))
        return items

    except Exception as exc:
        logger.warning("RSS %s fetch failed: %s", name, exc)
        return []


# ---------------------------------------------------------------------------
# Deduplication via SQLite news_cache
# ---------------------------------------------------------------------------
async def _dedup_news(items: list[dict]) -> list[dict]:
    """Remove items already in news_cache (by URL). Insert new ones."""
    if not items:
        return []

    try:
        import aiosqlite

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA journal_mode=WAL")

            # Get existing URLs
            urls = [item["url"] for item in items]
            placeholders = ",".join("?" for _ in urls)
            cursor = await db.execute(
                f"SELECT url FROM news_cache WHERE url IN ({placeholders})", urls
            )
            existing = {row[0] for row in await cursor.fetchall()}

            # Filter new items
            new_items = [item for item in items if item["url"] not in existing]

            # Insert new items
            for item in new_items:
                await db.execute(
                    """INSERT OR IGNORE INTO news_cache (url, title, source, fetched_at, coins)
                       VALUES (?, ?, ?, datetime('now'), ?)""",
                    (
                        item["url"],
                        item["title"],
                        item["source"],
                        json.dumps(item["coins_mentioned"]),
                    ),
                )
            await db.commit()

            logger.info("News dedup: %d total, %d new, %d existing", len(items), len(new_items), len(existing))
            return new_items

    except ImportError:
        logger.warning("aiosqlite not available, skipping dedup")
        return items
    except Exception as exc:
        logger.error("News dedup failed: %s", exc)
        return items


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def fetch_news(client: httpx.AsyncClient | None = None) -> list[dict]:
    """Fetch, merge, and deduplicate news from all sources.

    Returns list of dicts:
        {title, url, source, published_at, coins_mentioned}
    """
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)
        close_client = True

    try:
        # Fetch from all sources concurrently
        tasks = [
            _fetch_cryptocompare(client),
            _fetch_cryptopanic(client),
        ]
        for name, url in RSS_FEEDS.items():
            tasks.append(_fetch_rss_feed(client, name, url))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Merge all items
        all_items: list[dict] = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning("News source failed: %s", result)
                continue
            all_items.extend(result)

        # Dedup
        new_items = await _dedup_news(all_items)
        logger.info("News aggregator: %d new items from %d total across all sources", len(new_items), len(all_items))
        return new_items

    finally:
        if close_client:
            await client.aclose()


# Need asyncio import at module level for gather
import asyncio
