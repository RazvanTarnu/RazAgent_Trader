# -*- coding: utf-8 -*-
"""Walk-forward evaluation — rolling train/validation/test windows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from trading_intelligence.backtest.costs import CostModel
from trading_intelligence.backtest.engine import BacktestEngine, BacktestResult
from trading_intelligence.backtest.metrics import BacktestMetrics, compute_metrics


@dataclass(frozen=True)
class WalkForwardFold:
    fold_index: int
    train_bars: int
    test_bars: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    test_metrics: BacktestMetrics
    in_sample_metrics: BacktestMetrics | None = None


@dataclass
class WalkForwardResult:
    folds: list[WalkForwardFold] = field(default_factory=list)
    aggregate_test_metrics: BacktestMetrics | None = None
    robust: bool = False
    notes: list[str] = field(default_factory=list)


class WalkForwardEvaluator:
    """Rolling walk-forward with explicit train/test separation."""

    def __init__(
        self,
        cost_model: CostModel,
        *,
        stop_loss_pct: float,
        time_stop_bars: int,
        train_size: int = 120,
        test_size: int = 30,
        step_size: int = 30,
        min_folds: int = 2,
        min_trades_per_fold: int = 3,
    ):
        self.train_size = train_size
        self.test_size = test_size
        self.step_size = step_size
        self.min_folds = min_folds
        self.min_trades_per_fold = min_trades_per_fold
        self._engine = BacktestEngine(
            cost_model,
            stop_loss_pct=stop_loss_pct,
            time_stop_bars=time_stop_bars,
            min_trades=min_trades_per_fold,
        )

    def evaluate(self, symbol: str, bars: Sequence[dict[str, Any]], *, timeframe: str = "1d") -> WalkForwardResult:
        notes: list[str] = []
        folds: list[WalkForwardFold] = []
        all_test_pnls: list[float] = []
        all_test_equity: list[float] = [10_000.0]

        if len(bars) < self.train_size + self.test_size:
            notes.append("Insufficient bars for walk-forward")
            return WalkForwardResult(folds=[], notes=notes, robust=False)

        fold_idx = 0
        start = 0
        while start + self.train_size + self.test_size <= len(bars):
            train_end = start + self.train_size
            test_end = train_end + self.test_size
            train_bars = list(bars[start:train_end])
            test_bars = list(bars[train_end:test_end])

            # In-sample sanity (not used for parameter optimization here)
            is_result = self._engine.run(symbol, train_bars, timeframe=timeframe)
            oos_result = self._engine.run(symbol, test_bars, timeframe=timeframe)

            folds.append(
                WalkForwardFold(
                    fold_index=fold_idx,
                    train_bars=len(train_bars),
                    test_bars=len(test_bars),
                    train_start=start,
                    train_end=train_end,
                    test_start=train_end,
                    test_end=test_end,
                    test_metrics=oos_result.metrics,
                    in_sample_metrics=is_result.metrics,
                )
            )
            all_test_pnls.extend(oos_result.trade_pnls)
            if oos_result.equity_curve:
                all_test_equity.extend(oos_result.equity_curve[1:])

            fold_idx += 1
            start += self.step_size

        if len(folds) < self.min_folds:
            notes.append(f"Only {len(folds)} folds — below minimum {self.min_folds}")

        aggregate = compute_metrics(all_test_equity, all_test_pnls) if all_test_pnls else None

        robust = bool(
            folds
            and len(folds) >= self.min_folds
            and aggregate
            and aggregate.total_trades >= self.min_trades_per_fold * len(folds)
        )

        # Overfitting check: OOS Sharpe should not collapse vs IS average
        if folds and aggregate:
            is_sharpes = [f.in_sample_metrics.sharpe for f in folds if f.in_sample_metrics]
            if is_sharpes:
                avg_is = sum(is_sharpes) / len(is_sharpes)
                if avg_is > 1.0 and aggregate.sharpe < avg_is * 0.3:
                    notes.append("Possible overfitting: OOS Sharpe << IS Sharpe")
                    robust = False

        return WalkForwardResult(folds=folds, aggregate_test_metrics=aggregate, robust=robust, notes=notes)
