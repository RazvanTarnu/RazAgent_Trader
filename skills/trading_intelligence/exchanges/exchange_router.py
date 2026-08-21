# -*- coding: utf-8 -*-
"""Multi-Exchange Trading V1.0 — Exchange Router.

Smart routing between Binance and KuCoin based on:
  - Balance availability (+50 points)
  - Spread quality (+30 points)
  - Liquidity/volume (+20 points)
  - Automatic failover if primary fails

Usage:
    router = get_router()
    await router.initialize()
    result = await router.execute_trade("BTCUSDT", "BUY", 25.0)
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from .base_executor import BaseExchangeExecutor, OrderResult, PriceInfo

from shared.keyring_loader import get_credential

logger = logging.getLogger("TradingIntelligence")


@dataclass
class ExchangeScore:
    """Scoring for exchange selection."""
    exchange: str
    score: int
    has_balance: bool
    balance_usd: float
    spread_percent: float
    volume_24h: float
    is_available: bool
    reason: str


class ExchangeRouter:
    """Routes trades to the optimal exchange."""

    def __init__(self):
        self.executors: dict[str, BaseExchangeExecutor] = {}
        self._initialized = False

    async def initialize(self) -> None:
        """Load exchange credentials via shared.keyring_loader and create executors."""
        if self._initialized:
            return

        # Binance — get_credential handles all naming variants automatically
        bk = get_credential("BINANCE_API_KEY") or ""
        bs = get_credential("BINANCE_API_SECRET") or ""
        if bk and bs:
            from .binance_executor import BinanceExecutor
            self.executors["binance"] = BinanceExecutor(bk, bs)
            logger.info("Exchange router: Binance loaded")

        # KuCoin
        kk = get_credential("KUCOIN_API_KEY") or ""
        ks = get_credential("KUCOIN_API_SECRET") or ""
        kp = get_credential("KUCOIN_API_PASSPHRASE") or ""
        if kk and ks and kp:
            from .kucoin_executor import KuCoinExecutor
            self.executors["kucoin"] = KuCoinExecutor(kk, ks, kp)
            logger.info("Exchange router: KuCoin loaded")

        self._initialized = True
        logger.info("Exchange router initialized with %d exchanges", len(self.executors))

    # ---- routing ----------------------------------------------------------

    async def get_best_exchange(
        self, symbol: str, side: str, amount_usd: float,
    ) -> tuple[Optional[str], list[ExchangeScore]]:
        """Score all exchanges and return the best one.

        Returns:
            (best_exchange_name or None, list of all scores)
        """
        from shared.execution import raise_execution_forbidden
        raise_execution_forbidden(
            "legacy exchange execution is quarantined; paper-only build",
            target="ExchangeRouter.get_best_exchange",
        )

        await self.initialize()  # pragma: no cover - quarantined legacy implementation

        scores: list[ExchangeScore] = []
        tasks = [
            self._score_exchange(ex, symbol, amount_usd)
            for ex in self.executors.values()
        ]
        scores = await asyncio.gather(*tasks, return_exceptions=False)
        scores = sorted(scores, key=lambda s: s.score, reverse=True)

        for s in scores:
            if s.is_available and s.has_balance:
                return s.exchange, scores

        return None, scores

    async def _score_exchange(
        self, executor: BaseExchangeExecutor, symbol: str, amount_usd: float,
    ) -> ExchangeScore:
        """Score a single exchange on balance, spread, and liquidity."""
        score = 0
        reasons: list[str] = []
        is_available = False
        has_balance = False
        balance_usd = 0.0
        spread_pct = 100.0
        volume = 0.0

        try:
            is_available = await executor.test_connection()
            if not is_available:
                return ExchangeScore(
                    executor.name, 0, False, 0, 100, 0, False, "Connection failed",
                )

            # Balance check (+50) — use total USD across all assets
            if hasattr(executor, "get_total_balance_usd"):
                balance_usd = await executor.get_total_balance_usd()
            else:
                bal = await executor.get_balance("USDT")
                balance_usd = bal.free
            if balance_usd >= amount_usd:
                has_balance = True
                score += 50
                reasons.append(f"Balance ${balance_usd:.2f}")
            else:
                reasons.append(f"Low balance ${balance_usd:.2f}")

            # Spread check (+30)
            try:
                pi = await executor.get_price(symbol)
                spread_pct = pi.spread_percent
                volume = pi.volume_24h

                if spread_pct < 0.05:
                    score += 30
                elif spread_pct < 0.1:
                    score += 20
                elif spread_pct < 0.2:
                    score += 10
                reasons.append(f"Spread {spread_pct:.3f}%")

                # Volume/liquidity check (+20)
                if volume > 100_000_000:
                    score += 20
                elif volume > 10_000_000:
                    score += 10
                reasons.append(f"Vol ${volume / 1e6:.1f}M")

            except Exception as exc:
                reasons.append(f"Price check failed: {exc}")

        except Exception as exc:
            logger.debug("Error scoring %s: %s", executor.name, exc)
            return ExchangeScore(
                executor.name, 0, False, 0, 100, 0, False, f"Error: {exc}",
            )

        return ExchangeScore(
            executor.name, score, has_balance, balance_usd,
            spread_pct, volume, is_available, " | ".join(reasons),
        )

    # ---- execution --------------------------------------------------------

    async def execute_trade(
        self,
        symbol: str,
        side: str,
        amount_usd: float,
        preferred_exchange: Optional[str] = None,
    ) -> OrderResult:
        """Execute trade on the best available exchange with failover.

        Args:
            symbol: Trading pair (e.g. BTCUSDT).
            side: BUY or SELL.
            amount_usd: Amount in USD (max $50).
            preferred_exchange: Force a specific exchange (optional).
        """
        await self.initialize()

        # Preferred exchange shortcut
        if preferred_exchange and preferred_exchange in self.executors:
            return await self.executors[preferred_exchange].place_market_order(
                symbol, side, amount_usd,
            )

        best, scores = await self.get_best_exchange(symbol, side, amount_usd)
        if not best:
            return OrderResult(
                success=False,
                exchange="none",
                symbol=symbol,
                side=side,
                error="No exchange available with sufficient balance",
            )

        logger.info("Router selected %s for %s %s $%.2f", best, side, symbol, amount_usd)
        result = await self.executors[best].place_market_order(symbol, side, amount_usd)

        # Failover
        if not result.success:
            for s in scores:
                if s.exchange != best and s.is_available and s.has_balance:
                    logger.info("Failover to %s after %s failed", s.exchange, best)
                    result = await self.executors[s.exchange].place_market_order(
                        symbol, side, amount_usd,
                    )
                    if result.success:
                        break

        return result

    # ---- info -------------------------------------------------------------

    async def get_all_balances(self) -> dict[str, float]:
        """Get total USD value from all connected exchanges.

        Sums ALL non-zero assets (USDT, USDC, BTC, etc.) converted to USD,
        not just the USDT balance.
        """
        await self.initialize()
        balances: dict[str, float] = {}
        for name, ex in self.executors.items():
            try:
                if hasattr(ex, "get_total_balance_usd"):
                    balances[name] = await ex.get_total_balance_usd()
                else:
                    # Fallback for executors without the new method
                    bal = await ex.get_balance("USDT")
                    balances[name] = bal.free
            except Exception as exc:
                logger.debug("Balance check failed for %s: %s", name, exc)
                balances[name] = 0.0
        return balances

    async def compare_prices(self, symbol: str) -> dict[str, PriceInfo]:
        """Get prices from all exchanges for comparison / arbitrage detection."""
        await self.initialize()
        prices: dict[str, PriceInfo] = {}
        for name, ex in self.executors.items():
            try:
                prices[name] = await ex.get_price(symbol)
            except Exception as exc:
                logger.debug("Price check failed for %s/%s: %s", name, symbol, exc)
        return prices

    @property
    def connected_exchanges(self) -> list[str]:
        return list(self.executors.keys())


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_router: Optional[ExchangeRouter] = None


def get_router() -> ExchangeRouter:
    """Get or create the exchange router singleton."""
    global _router
    if _router is None:
        _router = ExchangeRouter()
    return _router
