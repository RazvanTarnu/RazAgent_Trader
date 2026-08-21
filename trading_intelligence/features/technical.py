# -*- coding: utf-8 -*-
"""Technical feature engineering — pure functions, no look-ahead."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional, Sequence


@dataclass(frozen=True)
class FeatureVector:
    symbol: str
    timeframe: str
    timestamp: str
    features: dict[str, float | str | None]
    version: str = "1.0.0"


def simple_ma(values: Sequence[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def returns(closes: Sequence[float], period: int = 1) -> Optional[float]:
    if len(closes) <= period:
        return None
    prev = closes[-period - 1]
    if prev == 0:
        return None
    return (closes[-1] - prev) / prev


def volatility(closes: Sequence[float], period: int = 20) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    rets = []
    for i in range(len(closes) - period, len(closes)):
        if closes[i - 1] == 0:
            continue
        rets.append((closes[i] - closes[i - 1]) / closes[i - 1])
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = []
    losses = []
    for d in deltas[-period:]:
        if d > 0:
            gains.append(d)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(d))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def momentum(closes: Sequence[float], period: int = 10) -> Optional[float]:
    if len(closes) <= period:
        return None
    base = closes[-period - 1]
    if base == 0:
        return None
    return (closes[-1] - base) / base


def volume_anomaly(volumes: Sequence[float], period: int = 20) -> Optional[float]:
    if len(volumes) < period:
        return None
    window = volumes[-period:]
    mean = sum(window) / period
    if mean == 0:
        return None
    return volumes[-1] / mean


def spread_pct(bid: float, ask: float) -> Optional[float]:
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    if mid == 0:
        return None
    return (ask - bid) / mid


def order_book_imbalance(bid_qty: float, ask_qty: float) -> Optional[float]:
    total = bid_qty + ask_qty
    if total == 0:
        return None
    return (bid_qty - ask_qty) / total


def ma_crossover_signal(closes: Sequence[float], short: int = 7, long: int = 25) -> str:
    if len(closes) < long + 2:
        return "NO_SIGNAL"
    s_now = simple_ma(closes, short)
    l_now = simple_ma(closes, long)
    s_prev = simple_ma(closes[:-1], short)
    l_prev = simple_ma(closes[:-1], long)
    if None in (s_now, l_now, s_prev, l_prev):
        return "NO_SIGNAL"
    if s_prev <= l_prev and s_now > l_now:
        return "BULLISH_CROSS"
    if s_prev >= l_prev and s_now < l_now:
        return "BEARISH_CROSS"
    return "NO_SIGNAL"


def compute_technical_features(
    symbol: str,
    timeframe: str,
    bars: list[dict[str, Any]],
    *,
    timestamp: str | None = None,
) -> FeatureVector:
    """Compute features from OHLCV bars ending at the last bar (no future data)."""
    if not bars:
        return FeatureVector(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp or "",
            features={},
        )

    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    volumes = [float(b.get("volume", 0)) for b in bars]
    ts = timestamp or str(bars[-1].get("timestamp", ""))

    ma50 = simple_ma(closes, 50)
    ma200 = simple_ma(closes, 200)
    vol = volatility(closes)
    atr_val = atr(highs, lows, closes)
    rsi_val = rsi(closes)
    mom = momentum(closes)
    vol_anom = volume_anomaly(volumes)
    crossover = ma_crossover_signal(closes)

    trend_regime = "unknown"
    if ma50 is not None and ma200 is not None:
        if ma50 > ma200 * 1.01:
            trend_regime = "uptrend"
        elif ma50 < ma200 * 0.99:
            trend_regime = "downtrend"
        else:
            trend_regime = "range"

    vol_regime = "unknown"
    if vol is not None:
        if vol > 0.03:
            vol_regime = "high_volatility"
        elif vol < 0.01:
            vol_regime = "low_volatility"
        else:
            vol_regime = "normal_volatility"

    return FeatureVector(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=ts,
        features={
            "return_1": returns(closes),
            "volatility_20": vol,
            "atr_14": atr_val,
            "rsi_14": rsi_val,
            "ma_7": simple_ma(closes, 7),
            "ma_25": simple_ma(closes, 25),
            "ma_50": ma50,
            "ma_200": ma200,
            "momentum_10": mom,
            "volume_anomaly": vol_anom,
            "ma_crossover": crossover,
            "trend_regime": trend_regime,
            "volatility_regime": vol_regime,
            "last_close": closes[-1],
        },
    )
