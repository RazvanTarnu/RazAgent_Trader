# -*- coding: utf-8 -*-
"""Transaction-cost model. A backtest without costs is fiction."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """Mandatory execution costs for BacktestEngine.

    Fees are fractions of notional (0.001 = 10 bps). ``spread_bps`` is the
    full quoted spread in basis points. Slippage is
    ``k * volatility * (size / (volume * price))``, capped at 5%.
    """

    maker_fee: float
    taker_fee: float
    spread_bps: float
    slippage_k: float

    def __post_init__(self) -> None:
        for name in ("maker_fee", "taker_fee", "spread_bps", "slippage_k"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{name} must be a non-negative number")

    def half_spread_fraction(self) -> float:
        return self.spread_bps / 20_000.0

    def slippage_fraction(
        self,
        *,
        size: float,
        volatility: float,
        volume: float,
        price: float,
    ) -> float:
        if volume <= 0 or price <= 0:
            raise ValueError("volume and price must be positive to compute slippage")
        if size < 0:
            raise ValueError("size must be non-negative")
        participation = size / (volume * price)
        return min(0.05, self.slippage_k * max(float(volatility), 0.0) * max(participation, 0.0))

    def fill_price(
        self,
        *,
        side: str,
        reference: float,
        size: float,
        volatility: float,
        volume: float,
    ) -> float:
        extra = reference * (
            self.half_spread_fraction()
            + self.slippage_fraction(
                size=size,
                volatility=volatility,
                volume=volume,
                price=reference,
            )
        )
        if side == "BUY":
            return reference + extra
        if side == "SELL":
            return max(0.0, reference - extra)
        raise ValueError(f"unsupported side {side!r}")

    def taker_fee_on(self, notional: float) -> float:
        return abs(notional) * self.taker_fee
