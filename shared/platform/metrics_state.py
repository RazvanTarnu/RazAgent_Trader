# -*- coding: utf-8 -*-
"""Mutable platform metrics state — internal only.

The metrics HTTP server exposes READ-ONLY snapshots via MetricsProvider.
External supervisors must never mutate this state.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional

from shared.platform.interfaces import MetricsProvider, PlatformMetrics, ProcessState


class MetricsState(MetricsProvider):
    """Thread-safe in-memory metrics registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._health = "starting"
        self._readiness = "not_ready"
        self._process_state = ProcessState.STARTING
        self._paper_mode = True
        self._provider_status: dict[str, str] = {}
        self._exchange_status: dict[str, str] = {}
        self._last_market_data: Optional[datetime] = None
        self._last_model_call: Optional[datetime] = None

    def set_health(self, value: str) -> None:
        with self._lock:
            self._health = value

    def set_readiness(self, value: str) -> None:
        with self._lock:
            self._readiness = value

    def set_process_state(self, state: ProcessState) -> None:
        with self._lock:
            self._process_state = state

    def set_paper_mode(self, paper: bool) -> None:
        with self._lock:
            self._paper_mode = paper

    def set_provider_status(self, provider: str, status: str) -> None:
        with self._lock:
            self._provider_status[provider] = status

    def set_exchange_status(self, exchange: str, status: str) -> None:
        with self._lock:
            self._exchange_status[exchange] = status

    def set_last_market_data(self, ts: datetime) -> None:
        with self._lock:
            self._last_market_data = ts

    def set_last_model_call(self, ts: datetime) -> None:
        with self._lock:
            self._last_model_call = ts

    def snapshot(self) -> PlatformMetrics:
        with self._lock:
            return PlatformMetrics(
                health=self._health,
                readiness=self._readiness,
                process_state=self._process_state,
                paper_mode=self._paper_mode,
                provider_status=dict(self._provider_status),
                exchange_connectivity=dict(self._exchange_status),
                last_market_data_ts=self._last_market_data,
                last_successful_model_call=self._last_model_call,
            )
