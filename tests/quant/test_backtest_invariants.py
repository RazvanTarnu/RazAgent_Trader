# -*- coding: utf-8 -*-
"""P2-2…P2-6: costs, next-bar entry, stops, leakage, run manifest."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest

from trading_intelligence.backtest.costs import CostModel
from trading_intelligence.backtest.engine import BacktestEngine
from trading_intelligence.signals.models import QuantSignal


def _cost(**kwargs) -> CostModel:
    params = {"maker_fee": 0.001, "taker_fee": 0.001, "spread_bps": 2.0, "slippage_k": 0.1}
    params.update(kwargs)
    return CostModel(**params)


def _engine(**kwargs) -> BacktestEngine:
    params = {
        "cost_model": _cost(),
        "stop_loss_pct": 0.05,
        "time_stop_bars": 10,
        "min_bars": 5,
        "min_trades": 1,
        "position_size_pct": 0.5,
    }
    params.update(kwargs)
    return BacktestEngine(**params)


def _bar(i: int, open_: float, high: float, low: float, close: float, volume: float = 1_000_000.0) -> dict[str, Any]:
    return {
        "timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat() + f"+{i}",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _force_buy(fv, regime, signal: QuantSignal) -> QuantSignal:
    return replace(signal, direction="BUY", confidence=1.0)


def _buy_then_hold(fv, regime, signal: QuantSignal) -> QuantSignal:
    close = fv.features.get("last_close")
    if isinstance(close, (int, float)) and close >= 109:
        return replace(signal, direction="BUY", confidence=1.0)
    return replace(signal, direction="HOLD", confidence=1.0)


def _buy_first_spike_only():
    spent = {"done": False}

    def _fn(fv, regime, signal: QuantSignal) -> QuantSignal:
        close = fv.features.get("last_close")
        if (
            not spent["done"]
            and isinstance(close, (int, float))
            and abs(float(close) - 110.0) < 1e-9
        ):
            spent["done"] = True
            return replace(signal, direction="BUY", confidence=1.0)
        return replace(signal, direction="HOLD", confidence=1.0)

    return _fn


def test_cost_model_is_required():
    with pytest.raises(TypeError, match="cost_model"):
        BacktestEngine()  # type: ignore[call-arg]


def test_cost_model_none_is_rejected():
    with pytest.raises(TypeError, match="CostModel"):
        BacktestEngine(None, stop_loss_pct=0.05, time_stop_bars=5)  # type: ignore[arg-type]


def test_stop_parameters_are_required():
    with pytest.raises(TypeError):
        BacktestEngine(_cost())  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="stop_loss_pct"):
        BacktestEngine(_cost(), stop_loss_pct=0, time_stop_bars=5)
    with pytest.raises(ValueError, match="time_stop_bars"):
        BacktestEngine(_cost(), stop_loss_pct=0.05, time_stop_bars=0)


def test_entry_uses_next_bar_open_not_signal_close():
    """BUY on close=110 must fill at the following open (120), not 110."""
    bars = [_bar(i, 100, 101, 99, 100) for i in range(8)]
    bars.append(_bar(8, 100, 110, 99, 110))
    bars.append(_bar(9, 120, 121, 119, 120))
    bars.append(_bar(10, 120, 121, 119, 120))

    fills: list[float] = []

    class Probe(CostModel):
        def fill_price(self, *, side, reference, size, volatility, volume):  # type: ignore[override]
            fills.append(reference)
            return super().fill_price(
                side=side, reference=reference, size=size, volatility=volatility, volume=volume
            )

    engine = _engine(cost_model=Probe(maker_fee=0.0, taker_fee=0.0, spread_bps=0.0, slippage_k=0.0))
    engine.run("FAKE/USDT", bars, signal_fn=_buy_then_hold, seed=7)
    assert fills, "expected at least one fill"
    assert 110 not in fills
    assert fills[0] == pytest.approx(120)


def test_stop_loss_exits_before_reverse_signal():
    bars = [_bar(i, 100, 101, 99, 100) for i in range(8)]
    bars.append(_bar(8, 100, 110, 99, 110))
    bars.append(_bar(9, 110, 111, 109, 110))
    bars.append(_bar(10, 110, 110, 50, 55))
    bars.extend(_bar(i, 55, 56, 54, 55) for i in range(11, 16))

    engine = _engine(stop_loss_pct=0.10, time_stop_bars=50)
    result = engine.run("FAKE/USDT", bars, signal_fn=_buy_then_hold, seed=1)
    assert result.trade_pnls, "stop-loss must close the position"
    assert result.trade_pnls[0] < 0


def test_time_stop_exits_without_reverse_signal():
    bars = [_bar(i, 100, 101, 99, 100) for i in range(8)]
    bars.append(_bar(8, 100, 110, 99, 110))
    bars.extend(_bar(i, 110, 111, 109, 110) for i in range(9, 20))

    engine = _engine(stop_loss_pct=0.50, time_stop_bars=3)
    result = engine.run("FAKE/USDT", bars, signal_fn=_buy_first_spike_only(), seed=2)
    assert result.trade_pnls, "time-stop must close the position"
    assert len(result.trade_pnls) == 1


def test_lookahead_bait_series_is_not_profitable():
    """Future is visible as a gap-up after the signal close.

    Same-bar close fills would buy 110 and mark 120 (profit). The engine must
    fill open(i+1)=120 and therefore cannot harvest that gap.
    """
    bars: list[dict[str, Any]] = []
    for i in range(80):
        if i % 3 == 0:
            bars.append(_bar(i, 100, 110, 99, 110))
        else:
            bars.append(_bar(i, 120, 120, 120, 120))

    engine = _engine(
        cost_model=_cost(maker_fee=0.001, taker_fee=0.001, spread_bps=2.0, slippage_k=0.1),
        stop_loss_pct=0.5,
        time_stop_bars=1000,
        min_bars=5,
        min_trades=1,
        position_size_pct=0.5,
    )
    result = engine.run("FAKE/USDT", bars, signal_fn=_buy_first_spike_only(), seed=3)
    assert result.metrics.total_return <= 0


def test_backtest_result_carries_run_manifest():
    bars = [_bar(i, 100 + i * 0.1, 101 + i * 0.1, 99 + i * 0.1, 100 + i * 0.1) for i in range(40)]
    engine = _engine()
    result = engine.run("BTC/USDT", bars, signal_fn=_force_buy, seed=42)
    assert result.run_id
    assert len(result.config_hash) == 64
    assert len(result.dataset_hash) == 64
    assert result.seed == 42
    for name in ("numpy", "pandas", "pyarrow", "duckdb", "hypothesis"):
        assert name in result.package_versions
        assert result.package_versions[name]
    other = engine.run("BTC/USDT", bars, signal_fn=_force_buy, seed=42)
    assert other.run_id != result.run_id
    assert other.config_hash == result.config_hash
    assert other.dataset_hash == result.dataset_hash
