# -*- coding: utf-8 -*-
"""Multi-Exchange Trading V1.0 — Binance Executor.

Binance Spot REST API v3 with HMAC-SHA256 signing.
"""

import hashlib
import hmac
import logging
import time

import httpx

from .base_executor import (
    BaseExchangeExecutor,
    BalanceInfo,
    OrderResult,
    PriceInfo,
    validate_endpoint_safety,
)

logger = logging.getLogger("TradingIntelligence")

_BASE = "https://api.binance.com"


class BinanceExecutor(BaseExchangeExecutor):
    """Binance Spot trading executor."""

    @property
    def name(self) -> str:
        return "binance"

    # ---- signing ----------------------------------------------------------

    def _sign(self, params: dict) -> str:
        qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return hmac.new(
            self.api_secret.encode(), qs.encode(), hashlib.sha256,
        ).hexdigest()

    def _headers(self) -> dict:
        return {"X-MBX-APIKEY": self.api_key}

    # ---- interface --------------------------------------------------------

    async def test_connection(self) -> bool:
        try:
            bal = await self.get_balance("USDT")
            return bal is not None
        except Exception as exc:
            logger.debug("Binance connection test failed: %s", exc)
            return False

    async def _fetch_account(self) -> dict:
        """Fetch full account info from Binance (signed request)."""
        params = {"timestamp": int(time.time() * 1000)}
        params["signature"] = self._sign(params)

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_BASE}/api/v3/account",
                headers=self._headers(),
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_balance(self, asset: str = "USDT") -> BalanceInfo:
        data = await self._fetch_account()
        for b in data.get("balances", []):
            if b["asset"] == asset:
                free = float(b["free"])
                locked = float(b["locked"])
                return BalanceInfo("binance", asset, free, locked, free + locked)

        return BalanceInfo("binance", asset, 0.0, 0.0, 0.0)

    async def get_total_balance_usd(self) -> float:
        """Sum USD value of ALL non-zero assets (not just USDT).

        Converts each holding via its USDT ticker price.  Stablecoins
        (USDT, USDC, BUSD, FDUSD) are valued at $1.
        """
        _STABLES = {"USDT", "USDC", "BUSD", "FDUSD", "DAI", "TUSD"}
        data = await self._fetch_account()
        total_usd = 0.0

        async with httpx.AsyncClient(timeout=10) as client:
            for b in data.get("balances", []):
                amount = float(b["free"]) + float(b["locked"])
                if amount <= 0:
                    continue

                asset = b["asset"]
                if asset in _STABLES:
                    total_usd += amount
                    continue

                # Try USDT pair for price conversion
                try:
                    resp = await client.get(
                        f"{_BASE}/api/v3/ticker/price",
                        params={"symbol": f"{asset}USDT"},
                        timeout=5,
                    )
                    if resp.status_code == 200:
                        price = float(resp.json().get("price", 0))
                        total_usd += amount * price
                except Exception:
                    pass  # skip assets with no USDT pair

        return total_usd

    async def get_price(self, symbol: str) -> PriceInfo:
        async with httpx.AsyncClient(timeout=10) as client:
            book_r, ticker_r = await asyncio.gather(
                client.get(f"{_BASE}/api/v3/ticker/bookTicker", params={"symbol": symbol}),
                client.get(f"{_BASE}/api/v3/ticker/24hr", params={"symbol": symbol}),
            )
            book_r.raise_for_status()
            ticker_r.raise_for_status()

            book = book_r.json()
            ticker = ticker_r.json()

            bid = float(book["bidPrice"])
            ask = float(book["askPrice"])
            spread = ask - bid

            return PriceInfo(
                exchange="binance",
                symbol=symbol,
                bid=bid,
                ask=ask,
                spread=spread,
                spread_percent=(spread / bid * 100) if bid > 0 else 0,
                last_price=float(ticker["lastPrice"]),
                volume_24h=float(ticker["quoteVolume"]),
            )

    async def place_market_order(
        self, symbol: str, side: str, amount_usd: float,
    ) -> OrderResult:
        self._validate_trade(amount_usd)

        try:
            price_info = await self.get_price(symbol)
            price = price_info.last_price
            if price <= 0:
                return OrderResult(False, "binance", symbol=symbol, side=side, error="Zero price")

            quantity = _round_qty(symbol, amount_usd / price)

            params = {
                "symbol": symbol,
                "side": side.upper(),
                "type": "MARKET",
                "quantity": quantity,
                "timestamp": int(time.time() * 1000),
            }
            params["signature"] = self._sign(params)

            async with httpx.AsyncClient(timeout=10) as client:
                # V11.60: Zero-Withdrawal Guardrail
                order_url = f"{_BASE}/api/v3/order"
                validate_endpoint_safety("binance", order_url)

                resp = await client.post(
                    order_url,
                    headers=self._headers(),
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

            # Extract fill price
            fill_price = price
            fee = 0.0
            fills = data.get("fills", [])
            if fills:
                total_qty = sum(float(f["qty"]) for f in fills)
                total_cost = sum(float(f["qty"]) * float(f["price"]) for f in fills)
                fill_price = total_cost / total_qty if total_qty > 0 else price
                fee = sum(
                    float(f.get("commission", 0))
                    for f in fills
                    if f.get("commissionAsset") == "USDT"
                )

            logger.info("Binance order executed: %s", data.get("orderId"))
            return OrderResult(
                success=True,
                exchange="binance",
                order_id=str(data.get("orderId")),
                symbol=symbol,
                side=side,
                quantity=float(data.get("executedQty", quantity)),
                price=fill_price,
                fee=fee,
                raw_response=data,
            )

        except Exception as exc:
            logger.error("Binance order failed: %s", exc)
            return OrderResult(False, "binance", symbol=symbol, side=side, error=str(exc))


# ---------------------------------------------------------------------------
import asyncio  # noqa: E402 (needed for gather in get_price)


def _round_qty(symbol: str, qty: float) -> float:
    """Round quantity to valid step size (simplified)."""
    if "BTC" in symbol:
        return round(qty, 6)
    if "ETH" in symbol:
        return round(qty, 5)
    return round(qty, 2)
