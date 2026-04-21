# -*- coding: utf-8 -*-
"""Exchange Rules Fetcher — V11.60

Downloads and caches the latest trading rules (min order sizes, tick sizes,
lot sizes) from exchange public endpoints. Prevents order rejections due
to MIN_NOTIONAL or LOT_SIZE violations.

Usage:
    from trading_intelligence.exchange_rules import get_symbol_rules, fetch_exchange_limits
    rules = await get_symbol_rules("BTCUSDT")
    # rules = {"min_notional": 5.0, "min_qty": 0.00001, "step_size": 0.00001, ...}
"""
import logging
import time
from typing import Optional

logger = logging.getLogger("TradingIntelligence.exchange_rules")

# In-memory cache with TTL
_rules_cache: dict[str, dict] = {}
_cache_ts: float = 0
_CACHE_TTL = 3600 * 6  # 6 hours


async def fetch_exchange_limits(symbols: list[str] | None = None) -> dict:
    """Fetch trading rules from Binance exchangeInfo (public, no API key).

    Args:
        symbols: List of trading pairs (e.g., ["BTCUSDT"]). None = all.

    Returns:
        dict mapping symbol → rules dict.
    """
    global _rules_cache, _cache_ts
    import httpx

    # Check cache
    if _rules_cache and (time.time() - _cache_ts) < _CACHE_TTL:
        if symbols:
            return {s: _rules_cache.get(s, {}) for s in symbols}
        return dict(_rules_cache)

    try:
        url = "https://api.binance.com/api/v3/exchangeInfo"
        params = {}
        if symbols and len(symbols) == 1:
            params["symbol"] = symbols[0].upper()
        elif symbols and len(symbols) <= 10:
            import json
            url_symbols = "%5B" + ",".join(f'%22{s.upper()}%22' for s in symbols) + "%5D"
            url = f"{url}?symbols={url_symbols}"
            params = None  # URL already has params

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                params=params,
            )
            if resp.status_code != 200:
                logger.warning("exchangeInfo returned HTTP %d", resp.status_code)
                return _rules_cache

            data = resp.json()

        for sym_info in data.get("symbols", []):
            symbol = sym_info["symbol"]
            rules = {
                "symbol": symbol,
                "status": sym_info.get("status", "UNKNOWN"),
                "base_asset": sym_info.get("baseAsset", ""),
                "quote_asset": sym_info.get("quoteAsset", ""),
                "min_notional": 0.0,
                "min_qty": 0.0,
                "max_qty": 0.0,
                "step_size": 0.0,
                "tick_size": 0.0,
                "min_price": 0.0,
                "max_price": 0.0,
            }

            for f in sym_info.get("filters", []):
                ft = f.get("filterType", "")
                if ft == "NOTIONAL":
                    rules["min_notional"] = float(f.get("minNotional", 0))
                elif ft == "MIN_NOTIONAL":
                    rules["min_notional"] = float(f.get("minNotional", 0))
                elif ft == "LOT_SIZE":
                    rules["min_qty"] = float(f.get("minQty", 0))
                    rules["max_qty"] = float(f.get("maxQty", 0))
                    rules["step_size"] = float(f.get("stepSize", 0))
                elif ft == "PRICE_FILTER":
                    rules["tick_size"] = float(f.get("tickSize", 0))
                    rules["min_price"] = float(f.get("minPrice", 0))
                    rules["max_price"] = float(f.get("maxPrice", 0))

            _rules_cache[symbol] = rules

        _cache_ts = time.time()
        logger.info("Exchange rules fetched: %d symbols cached", len(_rules_cache))

        if symbols:
            return {s: _rules_cache.get(s.upper(), {}) for s in symbols}
        return dict(_rules_cache)

    except Exception as e:
        logger.error("Failed to fetch exchange rules: %s", e)
        return _rules_cache


async def get_symbol_rules(symbol: str) -> Optional[dict]:
    """Get cached rules for a single symbol. Fetches if not cached."""
    symbol = symbol.upper()
    if symbol in _rules_cache and (time.time() - _cache_ts) < _CACHE_TTL:
        return _rules_cache[symbol]

    result = await fetch_exchange_limits([symbol])
    return result.get(symbol)


def validate_order_size(symbol: str, amount_usd: float, price: float) -> dict:
    """Validate an order against cached exchange rules.

    Returns dict with valid (bool), adjusted_qty, reason.
    """
    rules = _rules_cache.get(symbol.upper())
    if not rules:
        return {"valid": True, "reason": "No rules cached — allowing"}

    result = {"valid": True, "reason": "OK"}

    # Check MIN_NOTIONAL
    if rules["min_notional"] > 0 and amount_usd < rules["min_notional"]:
        result["valid"] = False
        result["reason"] = (
            f"Below MIN_NOTIONAL: ${amount_usd:.2f} < ${rules['min_notional']:.2f}"
        )
        return result

    # Calculate quantity
    qty = amount_usd / price if price > 0 else 0
    if rules["min_qty"] > 0 and qty < rules["min_qty"]:
        result["valid"] = False
        result["reason"] = f"Below MIN_QTY: {qty} < {rules['min_qty']}"
        return result

    # Step size rounding
    if rules["step_size"] > 0:
        step = rules["step_size"]
        adjusted = round(qty // step * step, 8)
        result["adjusted_qty"] = adjusted
        if adjusted <= 0:
            result["valid"] = False
            result["reason"] = f"Quantity rounds to 0 after step_size adjustment"

    return result
