# -*- coding: utf-8 -*-
"""Bar-by-bar backtest engine — no look-ahead bias."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Sequence

from trading_intelligence.backtest.metrics import BacktestMetrics, compute_metrics
from trading_intelligence.features.technical import compute_technical_features
from trading_intelligence.regime.classifier import RegimeClassifier
from trading_intelligence.signals.aggregator import SignalAggregator


@dataclass
class BacktestResult:
    metrics: BacktestMetrics
    equity_curve: list[float]
    trade_pnls: list[float]
    signals_log: list[dict[str, Any]] = field(default_factory=list)
    rejected_trades: int = 0


class BacktestEngine:
    """Simulate strategy bar-by-bar using only past data at each step."""

    def __init__(
        self,
        *,
        initial_capital: float = 10_000.0,
        position_size_pct: float = 0.1,
        min_bars: int = 30,
        min_trades: int = 5,
    ):
        self.initial_capital = initial_capital
        self.position_size_pct = position_size_pct
        self.min_bars = min_bars
        self.min_trades = min_trades
        self._aggregator = SignalAggregator()
        self._regime = RegimeClassifier()

    def run(
        self,
        symbol: str,
        bars: Sequence[dict[str, Any]],
        *,
        timeframe: str = "1d",
        signal_fn: Callable | None = None,
    ) -> BacktestResult:
        if len(bars) < self.min_bars + 1:
            return BacktestResult(
                metrics=compute_metrics([self.initial_capital], []),
                equity_curve=[self.initial_capital],
                trade_pnls=[],
                rejected_trades=0,
            )

        capital = self.initial_capital
        equity = [capital]
        trade_pnls: list[float] = []
        signals_log: list[dict[str, Any]] = []
        position: dict[str, Any] | None = None
        rejected = 0

        for i in range(self.min_bars, len(bars)):
            # CRITICAL: only use bars[:i+1] — no future leakage
            window = list(bars[: i + 1])
            fv = compute_technical_features(symbol, timeframe, window, timestamp=str(window[-1].get("timestamp", i)))
            regime = self._regime.classify(fv)
            signal = self._aggregator.from_features(fv, regime)

            if signal_fn:
                signal = signal_fn(fv, regime, signal)

            price = float(window[-1]["close"])
            ts = window[-1].get("timestamp", i)

            signals_log.append(
                {
                    "bar_index": i,
                    "timestamp": ts,
                    "direction": signal.direction,
                    "confidence": signal.confidence,
                    "regime": regime.label,
                }
            )

            if position is None and signal.direction == "BUY" and signal.confidence >= 0.55:
                size = capital * self.position_size_pct
                if size <= 0:
                    rejected += 1
                    continue
                qty = size / price if price else 0
                position = {"entry_price": price, "qty": qty, "entry_index": i}
            elif position is not None:
                exit_signal = signal.direction == "SELL" or (
                    signal.direction == "HOLD" and regime.label in ("bear_trend", "high_vol_chop")
                )
                if exit_signal:
                    pnl = (price - position["entry_price"]) * position["qty"]
                    capital += pnl
                    trade_pnls.append(pnl)
                    position = None

            mark = capital
            if position:
                mark += (price - position["entry_price"]) * position["qty"]
            equity.append(mark)

        if len(trade_pnls) < self.min_trades:
            rejected += self.min_trades - len(trade_pnls)

        metrics = compute_metrics(equity, trade_pnls)
        return BacktestResult(
            metrics=metrics,
            equity_curve=equity,
            trade_pnls=trade_pnls,
            signals_log=signals_log,
            rejected_trades=rejected,
        )
