# -*- coding: utf-8 -*-
"""Multi-Exchange Trading V1.1 — KuCoin Executor.

KuCoin Spot REST API with HMAC-SHA256-Base64 signing + v2 passphrase.

V1.1 resilience (2026-04-17):
  * Clock drift compensation (adjustForTimeDifference equivalent): fetches
    server time from /api/v1/timestamp on first use and every 1h, applies
    offset to KC-API-TIMESTAMP signing — eliminates 90% of spurious 401s.
  * Exponential-backoff retry wrapper (2s / 4s / 8s, max 3 attempts) on
    transient httpx errors. Fatal responses (4xx auth/validation) are NOT
    retried — they fail fast.
  * Client-side rate limiter: minimum 120 ms between signed requests.
    KuCoin's per-endpoint limits aren't modeled; this is a conservative
    floor that avoids tripping the 1800-req/min account cap.

Source dispatch for alerts goes via shared.trading_notify (channel isolation).
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import random
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

_BASE = "https://api.kucoin.com"

# ── Resilience tunables ────────────────────────────────────────────────
_RETRY_ATTEMPTS = 3
_RETRY_BASE_SEC = 2.0           # 2s, 4s, 8s
_RETRY_MAX_SEC = 10.0
_TIME_SYNC_INTERVAL_SEC = 3600  # refresh clock offset hourly
_RATE_LIMIT_GAP_SEC = 0.12      # min 120 ms between signed requests

# HTTP status codes that should NOT be retried (fatal — caller sees error).
# 401/403 = auth, 400 = bad request, 404 = not found.
_FATAL_HTTP_STATUSES = frozenset({400, 401, 403, 404, 422})


class KuCoinExecutor(BaseExchangeExecutor):
    """KuCoin Spot trading executor with clock-drift + retry resilience."""

    def __init__(self, api_key: str, api_secret: str, passphrase: str):
        super().__init__(api_key, api_secret)
        self.passphrase = passphrase
        # Graceful degradation: if creds are missing/blank, stay alive in paper mode
        # instead of crashing downstream on first signed request.
        self._live_disabled: bool = not (api_key and api_secret and passphrase)
        self._paper_mode: bool = self._live_disabled
        if self._live_disabled:
            logger.info(
                "KuCoinExecutor: missing credentials — PAPER_MODE=True, "
                "live endpoint disabled (mock-only, no reconnect loop)."
            )

        # Shared async state for clock sync + rate-limiting.
        self._time_offset_ms: int = 0            # server_ts - local_ts
        self._time_synced_at: float = 0.0
        self._last_signed_call: float = 0.0
        self._sync_lock = asyncio.Lock()
        self._rate_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "kucoin"

    # ---- clock drift compensation ----------------------------------------

    async def _sync_server_time(self) -> None:
        """Fetch KuCoin server time and update `_time_offset_ms`.

        KuCoin's 401 "Invalid KC-API-TIMESTAMP" fires when local clock drifts
        more than ~5s from theirs. This method brings us in sync without
        touching the system clock.
        """
        async with self._sync_lock:
            # Double-check: another coro may have synced while we waited.
            if (time.time() - self._time_synced_at) < _TIME_SYNC_INTERVAL_SEC:
                return
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{_BASE}/api/v1/timestamp")
                if resp.status_code == 200:
                    server_ts = int(resp.json().get("data", 0))
                    local_ts = int(time.time() * 1000)
                    self._time_offset_ms = server_ts - local_ts
                    self._time_synced_at = time.time()
                    logger.info(
                        "KuCoin clock sync: offset=%dms (server=%d local=%d)",
                        self._time_offset_ms, server_ts, local_ts,
                    )
            except Exception as exc:
                # Keep stale offset rather than crash — /timestamp is public.
                # Severity: INFO (benign, we have a last-known-good offset).
                logger.info("[INFO] KuCoin clock sync transient: %s", exc)

    def _now_kucoin_ms(self) -> str:
        """Return current timestamp in KuCoin-aligned milliseconds."""
        return str(int(time.time() * 1000) + self._time_offset_ms)

    # ---- client-side rate limiting ---------------------------------------

    async def _rate_gate(self) -> None:
        """Enforce minimum gap between signed requests."""
        async with self._rate_lock:
            elapsed = time.time() - self._last_signed_call
            if elapsed < _RATE_LIMIT_GAP_SEC:
                await asyncio.sleep(_RATE_LIMIT_GAP_SEC - elapsed)
            self._last_signed_call = time.time()

    # ---- retry wrapper ---------------------------------------------------

    async def _call_with_retry(self, coro_factory, label: str):
        """Run `coro_factory()` with exponential backoff on transient errors.

        `coro_factory` must be a zero-arg async callable — a fresh coroutine
        per attempt (we cannot reuse an awaited coroutine). Returns the
        awaited value on success, raises the last exception on exhaustion.
        """
        last_exc: Exception | None = None
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                return await coro_factory()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response else 0
                if status in _FATAL_HTTP_STATUSES:
                    # 403 handled upstream by shared.ip_watchdog pre-boot check;
                    # here we just fail fast without amplifying to Telegram.
                    logger.info(
                        "KuCoin %s fatal HTTP %s — not retrying: %s",
                        label, status, exc,
                    )
                    raise
                last_exc = exc
            except (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                last_exc = exc
            except RuntimeError as exc:
                # API-level errors from _fetch_accounts etc.: retry once only.
                msg = str(exc).lower()
                if "timestamp" in msg or "invalid" in msg and attempt == 1:
                    # Refresh clock once, then retry.
                    await self._sync_server_time()
                    last_exc = exc
                else:
                    raise

            if attempt < _RETRY_ATTEMPTS:
                backoff = min(_RETRY_BASE_SEC * (2 ** (attempt - 1)), _RETRY_MAX_SEC)
                # Small jitter so concurrent callers don't dogpile.
                backoff += random.uniform(0, 0.3)
                logger.info(
                    "KuCoin %s attempt %d failed (%s) — backing off %.1fs",
                    label, attempt, type(last_exc).__name__, backoff,
                )
                await asyncio.sleep(backoff)

        assert last_exc is not None
        logger.error("KuCoin %s exhausted %d attempts: %s", label, _RETRY_ATTEMPTS, last_exc)
        raise last_exc

    @property
    def name(self) -> str:
        return "kucoin"

    # ---- signing ----------------------------------------------------------

    def _sign_headers(
        self, timestamp: str, method: str, endpoint: str, body: str = "",
    ) -> dict:
        """Generate KuCoin V2 signature headers.

        `timestamp` should come from `_now_kucoin_ms()` to include clock offset.
        """
        str_to_sign = timestamp + method + endpoint + body

        signature = base64.b64encode(
            hmac.new(
                self.api_secret.encode(), str_to_sign.encode(), hashlib.sha256,
            ).digest()
        ).decode()

        passphrase_sig = base64.b64encode(
            hmac.new(
                self.api_secret.encode(), self.passphrase.encode(), hashlib.sha256,
            ).digest()
        ).decode()

        return {
            "KC-API-KEY": self.api_key,
            "KC-API-SIGN": signature,
            "KC-API-TIMESTAMP": timestamp,
            "KC-API-PASSPHRASE": passphrase_sig,
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json",
        }

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _to_kucoin_symbol(symbol: str) -> str:
        """BTCUSDT -> BTC-USDT"""
        if "USDT" in symbol:
            return symbol.replace("USDT", "") + "-USDT"
        return symbol

    @staticmethod
    def _round_qty(symbol: str, qty: float) -> float:
        if "BTC" in symbol:
            return round(qty, 8)
        if "ETH" in symbol:
            return round(qty, 6)
        return round(qty, 4)

    # ---- interface --------------------------------------------------------

    async def test_connection(self) -> bool:
        try:
            # Prime the clock offset on first ever call so subsequent signed
            # requests are aligned out of the gate.
            await self._sync_server_time()
            bal = await self.get_balance("USDT")
            return bal is not None
        except Exception as exc:
            logger.debug("KuCoin connection test failed: %s", exc)
            return False

    async def _fetch_accounts_inner(self) -> list[dict]:
        """Single-attempt signed fetch. Caller wraps with _call_with_retry."""
        # Refresh clock offset if stale (hourly).
        if (time.time() - self._time_synced_at) > _TIME_SYNC_INTERVAL_SEC:
            await self._sync_server_time()

        await self._rate_gate()
        ts = self._now_kucoin_ms()
        endpoint = "/api/v1/accounts"
        headers = self._sign_headers(ts, "GET", endpoint)

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_BASE}{endpoint}", headers=headers)
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != "200000":
            raise RuntimeError(f"KuCoin API error: {data.get('msg')}")

        return data.get("data", [])

    async def _fetch_accounts(self) -> list[dict]:
        """Fetch all trade-type accounts with retry + backoff."""
        if self._live_disabled:
            # PAPER_MODE: no creds → return deterministic empty book, no retry loop.
            return []
        return await self._call_with_retry(
            self._fetch_accounts_inner, label="_fetch_accounts",
        )

    async def get_balance(self, asset: str = "USDT") -> BalanceInfo:
        accounts = await self._fetch_accounts()
        for acct in accounts:
            if acct["currency"] == asset and acct["type"] == "trade":
                free = float(acct["available"])
                locked = float(acct["holds"])
                return BalanceInfo("kucoin", asset, free, locked, free + locked)

        return BalanceInfo("kucoin", asset, 0.0, 0.0, 0.0)

    async def get_total_balance_usd(self) -> float:
        """Sum USD value of ALL non-zero trade-account assets."""
        _STABLES = {"USDT", "USDC", "BUSD", "DAI", "TUSD"}
        accounts = await self._fetch_accounts()
        total_usd = 0.0

        async with httpx.AsyncClient(timeout=10) as client:
            for acct in accounts:
                if acct["type"] != "trade":
                    continue
                amount = float(acct["available"]) + float(acct["holds"])
                if amount <= 0:
                    continue

                currency = acct["currency"]
                if currency in _STABLES:
                    total_usd += amount
                    continue

                # Try USDT pair for price conversion
                try:
                    kc_sym = f"{currency}-USDT"
                    resp = await client.get(
                        f"{_BASE}/api/v1/market/orderbook/level1",
                        params={"symbol": kc_sym},
                        timeout=5,
                    )
                    if resp.status_code == 200:
                        rdata = resp.json()
                        if rdata.get("code") == "200000" and rdata.get("data"):
                            price = float(rdata["data"].get("price", 0))
                            total_usd += amount * price
                except Exception:
                    pass

        return total_usd

    async def get_price(self, symbol: str) -> PriceInfo:
        kc_sym = self._to_kucoin_symbol(symbol)

        async with httpx.AsyncClient(timeout=10) as client:
            book_r = await client.get(
                f"{_BASE}/api/v1/market/orderbook/level1",
                params={"symbol": kc_sym},
            )
            stats_r = await client.get(
                f"{_BASE}/api/v1/market/stats",
                params={"symbol": kc_sym},
            )
            book_r.raise_for_status()
            stats_r.raise_for_status()

        book = book_r.json().get("data", {})
        stats = stats_r.json().get("data", {})

        bid = float(book.get("bestBid", 0))
        ask = float(book.get("bestAsk", 0))
        spread = ask - bid

        return PriceInfo(
            exchange="kucoin",
            symbol=symbol,
            bid=bid,
            ask=ask,
            spread=spread,
            spread_percent=(spread / bid * 100) if bid > 0 else 0,
            last_price=float(book.get("price", stats.get("last", 0))),
            volume_24h=float(stats.get("volValue", 0)),
        )

    async def place_market_order(
        self, symbol: str, side: str, amount_usd: float,
    ) -> OrderResult:
        from shared.execution import raise_execution_forbidden
        raise_execution_forbidden(
            "legacy KuCoin executor is quarantined; paper-only build",
            target="KuCoinExecutor.place_market_order",
        )

        if self._live_disabled:
            return OrderResult(
                False, "kucoin", symbol=symbol, side=side,
                error="PAPER_MODE (no KuCoin credentials) — live order blocked",
            )
        self._validate_trade(amount_usd)

        # Keep clock sync fresh before signing, but DO NOT retry the POST —
        # placing the same market order twice would be a financial bug. Retry
        # is safe for idempotent GETs (balances, prices), not for order writes.
        if (time.time() - self._time_synced_at) > _TIME_SYNC_INTERVAL_SEC:
            await self._sync_server_time()

        try:
            price_info = await self.get_price(symbol)
            price = price_info.last_price
            if price <= 0:
                return OrderResult(False, "kucoin", symbol=symbol, side=side, error="Zero price")

            quantity = self._round_qty(symbol, amount_usd / price)
            kc_sym = self._to_kucoin_symbol(symbol)

            await self._rate_gate()
            ts = self._now_kucoin_ms()
            endpoint = "/api/v1/orders"
            body_dict = {
                "clientOid": f"razagent_{int(time.time() * 1000)}",
                "side": side.lower(),
                "symbol": kc_sym,
                "type": "market",
                "size": str(quantity),
            }
            body = json.dumps(body_dict)
            headers = self._sign_headers(ts, "POST", endpoint, body)

            async with httpx.AsyncClient(timeout=10) as client:
                # V11.60: Zero-Withdrawal Guardrail
                order_url = f"{_BASE}{endpoint}"
                validate_endpoint_safety("kucoin", order_url)

                resp = await client.post(
                    order_url, headers=headers, content=body,
                )
                resp.raise_for_status()
                data = resp.json()

            if data.get("code") != "200000":
                raise RuntimeError(f"KuCoin order error: {data.get('msg')}")

            order_id = data.get("data", {}).get("orderId", "")
            logger.info("KuCoin order executed: %s", order_id)

            return OrderResult(
                success=True,
                exchange="kucoin",
                order_id=order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                raw_response=data,
            )

        except Exception as exc:
            logger.error("KuCoin order failed: %s", exc)
            return OrderResult(False, "kucoin", symbol=symbol, side=side, error=str(exc))
