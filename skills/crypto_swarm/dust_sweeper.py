"""Crypto Swarm — dust analysis and portfolio overview (paper-only).

Dust conversion is a financial action and is unconditionally forbidden.
Read-only helpers go through the platform exchange factory.
"""
from __future__ import annotations

import logging

from shared.execution import raise_execution_forbidden

from .exchange_connector import get_exchange

logger = logging.getLogger("godclaw.crypto.dust")

_KEEP_ASSETS = {"BNB", "USDT", "BTC", "USDC", "BUSD", "FDUSD"}
_DUST_THRESHOLD_USD = 10.0
_STABLE = {"USDT", "BUSD", "FDUSD", "USDC"}


async def _get_usdt_price(exchange, asset: str) -> float:
    """Approximate USD value for 1 unit of asset via read-only tickers."""
    if asset in _STABLE:
        return 1.0
    try:
        ticker = await exchange.fetch_ticker(f"{asset}/USDT")
        price = float(ticker.get("last") or 0)
        if price > 0:
            return price
    except Exception:
        logger.debug("USDT ticker unavailable for %s", asset)
    try:
        quoted = await exchange.fetch_ticker(f"{asset}/BTC")
        btc_usdt = await exchange.fetch_ticker("BTC/USDT")
        price = float(quoted.get("last") or 0) * float(btc_usdt.get("last") or 0)
        if price > 0:
            return price
    except Exception:
        logger.debug("BTC-quoted ticker unavailable for %s", asset)
    try:
        quoted = await exchange.fetch_ticker(f"{asset}/BNB")
        bnb_usdt = await exchange.fetch_ticker("BNB/USDT")
        price = float(quoted.get("last") or 0) * float(bnb_usdt.get("last") or 0)
        if price > 0:
            return price
    except Exception:
        logger.debug("BNB-quoted ticker unavailable for %s", asset)
    return 0.0


def _holdings_from_balance(balance: dict) -> list[tuple[str, float]]:
    totals = balance.get("total") or {}
    holdings: list[tuple[str, float]] = []
    for asset, amount in totals.items():
        try:
            total = float(amount or 0)
        except (TypeError, ValueError):
            continue
        if total > 0:
            holdings.append((str(asset), total))
    return holdings


async def crypto_dust_check(**kwargs) -> dict:
    """Check for small balances using the read-only exchange facade."""
    exchange = get_exchange("binance")
    if not exchange:
        return {"output": "❌ Exchange binance not connected.", "error": "not connected"}

    balance = await exchange.fetch_balance()
    dust_assets = []
    total_dust_usd = 0.0

    for asset, total in _holdings_from_balance(balance):
        if asset in _KEEP_ASSETS:
            continue
        price_usd = await _get_usdt_price(exchange, asset)
        value_usd = total * price_usd
        if value_usd < _DUST_THRESHOLD_USD:
            dust_assets.append({
                "asset": asset,
                "amount": total,
                "value_usd": round(value_usd, 4),
            })
            total_dust_usd += value_usd

    if not dust_assets:
        return {
            "output": "🧹 No dust found — all balances are above $10 or in keep-list.",
            "dust": [],
        }

    dust_assets.sort(key=lambda item: -item["value_usd"])
    lines = ["🧹 <b>Dust Analysis</b>\n"]
    for item in dust_assets:
        lines.append(
            f"  • {item['asset']}: {item['amount']:.8g} (~${item['value_usd']:.2f})"
        )
    lines.append(
        f"\n<b>Total dust</b>: ~${total_dust_usd:.2f} across {len(dust_assets)} assets"
    )
    lines.append("\nDust conversion is unavailable in this paper-only build.")
    return {
        "output": "\n".join(lines),
        "dust": dust_assets,
        "total_usd": round(total_dust_usd, 4),
    }


async def crypto_dust_sweep(**kwargs) -> dict:
    """Reject every dust-conversion attempt in the paper-only build."""
    raise_execution_forbidden(
        "dust conversion is a financial action; paper-only build",
        target="crypto_dust_sweep",
    )


async def crypto_portfolio(**kwargs) -> dict:
    """Portfolio overview with USD values via the read-only exchange facade."""
    exchange = get_exchange("binance")
    if not exchange:
        return {"output": "❌ Exchange binance not connected.", "error": "not connected"}

    balance = await exchange.fetch_balance()
    holdings = []
    total_usd = 0.0

    for asset, total in _holdings_from_balance(balance):
        price_usd = await _get_usdt_price(exchange, asset)
        value_usd = total * price_usd
        total_usd += value_usd
        holdings.append({
            "asset": asset,
            "amount": total,
            "price_usd": round(price_usd, 6),
            "value_usd": round(value_usd, 2),
        })

    holdings.sort(key=lambda item: -item["value_usd"])
    lines = ["💼 <b>Portfolio Overview</b>\n"]
    for item in holdings:
        lines.append(
            f"  • <b>{item['asset']}</b>: {item['amount']:.8g}"
            f"  — ${item['value_usd']:.2f} (@${item['price_usd']:.6g})"
        )
    lines.append(f"\n<b>Total portfolio</b>: ${total_usd:,.2f}")
    return {
        "output": "\n".join(lines),
        "holdings": holdings,
        "total_usd": round(total_usd, 2),
    }


def register_tools() -> dict:
    return {
        "crypto_dust_check": crypto_dust_check,
        "crypto_portfolio": crypto_portfolio,
    }
