# -*- coding: utf-8 -*-
"""Backtest performance metrics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class BacktestMetrics:
    cagr: float
    sharpe: float
    sortino: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    turnover: float
    exposure: float
    tail_loss: float
    total_trades: int
    total_return: float


def compute_metrics(
    equity_curve: Sequence[float],
    trade_pnls: Sequence[float],
    *,
    periods_per_year: float = 252.0,
    risk_free_rate: float = 0.0,
) -> BacktestMetrics:
    if len(equity_curve) < 2:
        return BacktestMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev == 0:
            continue
        returns.append((equity_curve[i] - prev) / prev)

    total_return = (equity_curve[-1] / equity_curve[0]) - 1 if equity_curve[0] else 0
    n = len(returns)
    years = n / periods_per_year if periods_per_year else 1
    cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 and total_return > -1 else total_return

    mean_r = sum(returns) / n if n else 0
    std_r = _std(returns)
    sharpe = ((mean_r - risk_free_rate / periods_per_year) / std_r * math.sqrt(periods_per_year)) if std_r else 0

    downside = [r for r in returns if r < 0]
    down_std = _std(downside) if downside else 0
    sortino = ((mean_r - risk_free_rate / periods_per_year) / down_std * math.sqrt(periods_per_year)) if down_std else 0

    max_dd = _max_drawdown(equity_curve)
    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p < 0]
    win_rate = len(wins) / len(trade_pnls) if trade_pnls else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else 0)

    turnover = len(trade_pnls) / n if n else 0
    exposure = sum(1 for r in returns if r != 0) / n if n else 0
    tail_loss = min(returns) if returns else 0

    return BacktestMetrics(
        cagr=round(cagr, 6),
        sharpe=round(sharpe, 4),
        sortino=round(sortino, 4),
        max_drawdown=round(max_dd, 6),
        win_rate=round(win_rate, 4),
        profit_factor=round(profit_factor, 4) if profit_factor != float("inf") else 999.0,
        turnover=round(turnover, 4),
        exposure=round(exposure, 4),
        tail_loss=round(tail_loss, 6),
        total_trades=len(trade_pnls),
        total_return=round(total_return, 6),
    )


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def _max_drawdown(equity: Sequence[float]) -> float:
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak else 0
        max_dd = max(max_dd, dd)
    return max_dd
