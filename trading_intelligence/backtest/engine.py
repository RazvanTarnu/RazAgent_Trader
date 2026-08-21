# -*- coding: utf-8 -*-
"""Bar-by-bar backtest engine — no look-ahead bias.

Signals are computed on ``bars[:i+1]``. Entries fill at ``open(i+1)`` plus
spread and slippage. Same-bar close fills are forbidden: that price already
embeds the information that generated the signal.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from importlib import metadata
from typing import Any, Callable, Sequence

from trading_intelligence.backtest.costs import CostModel
from trading_intelligence.backtest.metrics import BacktestMetrics, compute_metrics
from trading_intelligence.features.technical import compute_technical_features
from trading_intelligence.regime.classifier import RegimeClassifier
from trading_intelligence.signals.aggregator import SignalAggregator

_TRACKED_PACKAGES = ("numpy", "pandas", "pyarrow", "duckdb", "hypothesis")


@dataclass
class BacktestResult:
    metrics: BacktestMetrics
    equity_curve: list[float]
    trade_pnls: list[float]
    signals_log: list[dict[str, Any]] = field(default_factory=list)
    rejected_trades: int = 0
    run_id: str = ""
    config_hash: str = ""
    dataset_hash: str = ""
    seed: int | None = None
    package_versions: dict[str, str] = field(default_factory=dict)


def _sha256(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in _TRACKED_PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "missing"
    return versions


class BacktestEngine:
    """Simulate strategy bar-by-bar using only past data at each step."""

    def __init__(
        self,
        cost_model: CostModel,
        *,
        stop_loss_pct: float,
        time_stop_bars: int,
        initial_capital: float = 10_000.0,
        position_size_pct: float = 0.1,
        min_bars: int = 30,
        min_trades: int = 5,
    ):
        if not isinstance(cost_model, CostModel):
            raise TypeError(
                "BacktestEngine requires a CostModel; backtests without costs are forbidden"
            )
        if stop_loss_pct <= 0:
            raise ValueError("stop_loss_pct must be positive")
        if time_stop_bars < 1:
            raise ValueError("time_stop_bars must be >= 1")
        if initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if not 0 < position_size_pct <= 1:
            raise ValueError("position_size_pct must be in (0, 1]")
        self.cost_model = cost_model
        self.stop_loss_pct = float(stop_loss_pct)
        self.time_stop_bars = int(time_stop_bars)
        self.initial_capital = float(initial_capital)
        self.position_size_pct = float(position_size_pct)
        self.min_bars = int(min_bars)
        self.min_trades = int(min_trades)
        self._aggregator = SignalAggregator()
        self._regime = RegimeClassifier()

    def _config_payload(self) -> dict[str, Any]:
        return {
            "cost_model": asdict(self.cost_model),
            "stop_loss_pct": self.stop_loss_pct,
            "time_stop_bars": self.time_stop_bars,
            "initial_capital": self.initial_capital,
            "position_size_pct": self.position_size_pct,
            "min_bars": self.min_bars,
            "min_trades": self.min_trades,
        }

    def _result(
        self,
        *,
        metrics: BacktestMetrics,
        equity_curve: list[float],
        trade_pnls: list[float],
        signals_log: list[dict[str, Any]],
        rejected_trades: int,
        bars: Sequence[dict[str, Any]],
        seed: int | None,
    ) -> BacktestResult:
        return BacktestResult(
            metrics=metrics,
            equity_curve=equity_curve,
            trade_pnls=trade_pnls,
            signals_log=signals_log,
            rejected_trades=rejected_trades,
            run_id=str(uuid.uuid4()),
            config_hash=_sha256(self._config_payload()),
            dataset_hash=_sha256(list(bars)),
            seed=seed,
            package_versions=_package_versions(),
        )

    def _volatility(self, features: dict[str, Any]) -> float:
        value = features.get("volatility_20")
        if isinstance(value, (int, float)):
            return float(value)
        return 0.0

    def _close_position(
        self,
        *,
        position: dict[str, Any],
        reference: float,
        volume: float,
        volatility: float,
        capital: float,
        reason: str,
    ) -> tuple[float, float]:
        notional = position["qty"] * reference
        fill = self.cost_model.fill_price(
            side="SELL",
            reference=reference,
            size=notional,
            volatility=volatility,
            volume=volume,
        )
        fee = self.cost_model.taker_fee_on(position["qty"] * fill)
        pnl = (fill - position["entry_price"]) * position["qty"] - fee
        position["exit_reason"] = reason
        return capital + pnl, pnl

    def run(
        self,
        symbol: str,
        bars: Sequence[dict[str, Any]],
        *,
        timeframe: str = "1d",
        signal_fn: Callable | None = None,
        seed: int | None = None,
    ) -> BacktestResult:
        empty = self._result(
            metrics=compute_metrics([self.initial_capital], []),
            equity_curve=[self.initial_capital],
            trade_pnls=[],
            signals_log=[],
            rejected_trades=0,
            bars=bars,
            seed=seed,
        )
        # Need one bar to compute a signal and a subsequent bar to fill it.
        if len(bars) < self.min_bars + 2:
            return empty

        capital = self.initial_capital
        equity = [capital]
        trade_pnls: list[float] = []
        signals_log: list[dict[str, Any]] = []
        position: dict[str, Any] | None = None
        pending_entry = False
        pending_exit = False
        last_volatility = 0.0
        rejected = 0

        for i in range(self.min_bars, len(bars)):
            bar = bars[i]
            open_px = float(bar["open"])
            low_px = float(bar["low"])
            close_px = float(bar["close"])
            volume = float(bar.get("volume") or 0.0)

            if pending_exit and position is not None:
                try:
                    capital, pnl = self._close_position(
                        position=position,
                        reference=open_px,
                        volume=volume,
                        volatility=last_volatility,
                        capital=capital,
                        reason="signal",
                    )
                    trade_pnls.append(pnl)
                    position = None
                except ValueError:
                    rejected += 1
                pending_exit = False

            if pending_entry and position is None:
                size = capital * self.position_size_pct
                try:
                    fill = self.cost_model.fill_price(
                        side="BUY",
                        reference=open_px,
                        size=size,
                        volatility=last_volatility,
                        volume=volume,
                    )
                    fee = self.cost_model.taker_fee_on(size)
                    if size <= 0 or fill <= 0 or fee >= capital:
                        rejected += 1
                    else:
                        qty = size / fill
                        capital -= fee
                        position = {
                            "entry_price": fill,
                            "qty": qty,
                            "entry_index": i,
                            "stop": fill * (1.0 - self.stop_loss_pct),
                        }
                except ValueError:
                    rejected += 1
                pending_entry = False

            if (
                position is not None
                and i > position["entry_index"]
                and (i - position["entry_index"]) >= self.time_stop_bars
            ):
                try:
                    capital, pnl = self._close_position(
                        position=position,
                        reference=open_px,
                        volume=volume,
                        volatility=last_volatility,
                        capital=capital,
                        reason="time_stop",
                    )
                    trade_pnls.append(pnl)
                    position = None
                except ValueError:
                    rejected += 1

            if position is not None and low_px <= position["stop"]:
                ref = min(open_px, position["stop"])
                try:
                    capital, pnl = self._close_position(
                        position=position,
                        reference=ref,
                        volume=volume,
                        volatility=last_volatility,
                        capital=capital,
                        reason="stop_loss",
                    )
                    trade_pnls.append(pnl)
                    position = None
                except ValueError:
                    rejected += 1

            window = list(bars[: i + 1])
            fv = compute_technical_features(
                symbol, timeframe, window, timestamp=str(window[-1].get("timestamp", i))
            )
            last_volatility = self._volatility(fv.features)
            regime = self._regime.classify(fv)
            signal = self._aggregator.from_features(fv, regime)
            if signal_fn:
                signal = signal_fn(fv, regime, signal)

            ts = window[-1].get("timestamp", i)
            signals_log.append(
                {
                    "bar_index": i,
                    "timestamp": ts,
                    "direction": signal.direction,
                    "confidence": signal.confidence,
                    "regime": regime.label,
                    "fill_reference": "open_next",
                }
            )

            is_last = i == len(bars) - 1
            if position is None and signal.direction == "BUY" and signal.confidence >= 0.55:
                if is_last:
                    rejected += 1
                else:
                    pending_entry = True
            elif position is not None:
                exit_signal = signal.direction == "SELL" or (
                    signal.direction == "HOLD" and regime.label in ("bear_trend", "high_vol_chop")
                )
                if exit_signal:
                    if is_last:
                        try:
                            capital, pnl = self._close_position(
                                position=position,
                                reference=close_px,
                                volume=volume,
                                volatility=last_volatility,
                                capital=capital,
                                reason="signal_last_bar",
                            )
                            trade_pnls.append(pnl)
                            position = None
                        except ValueError:
                            rejected += 1
                    else:
                        pending_exit = True

            mark = capital
            if position:
                mark += (close_px - position["entry_price"]) * position["qty"]
            equity.append(mark)

        if position is not None:
            last = bars[-1]
            try:
                capital, pnl = self._close_position(
                    position=position,
                    reference=float(last["close"]),
                    volume=float(last.get("volume") or 0.0),
                    volatility=last_volatility,
                    capital=capital,
                    reason="eod_flatten",
                )
                trade_pnls.append(pnl)
            except ValueError:
                rejected += 1
            mark = capital
            if equity:
                equity[-1] = mark

        if len(trade_pnls) < self.min_trades:
            rejected += self.min_trades - len(trade_pnls)

        return self._result(
            metrics=compute_metrics(equity, trade_pnls),
            equity_curve=equity,
            trade_pnls=trade_pnls,
            signals_log=signals_log,
            rejected_trades=rejected,
            bars=bars,
            seed=seed,
        )
