# -*- coding: utf-8 -*-
"""Blocked ExecutionForbidden actions must leave an audit trail, best-effort."""

from __future__ import annotations

import pytest

from shared.events.event_logger import SQLiteEventLogger
from shared.execution import ExecutionForbidden, raise_execution_forbidden


def test_raise_execution_forbidden_emits_audit_event(tmp_path, monkeypatch):
    db_path = tmp_path / "audit.db"
    monkeypatch.setattr("shared.execution.AUDIT_DB_PATH", db_path)

    with pytest.raises(ExecutionForbidden, match="paper-only"):
        raise_execution_forbidden(
            "live execution not implemented; paper-only build",
            target="crypto_execute_trade",
            actor="test",
        )

    events = SQLiteEventLogger(db_path).list_events(category="execution")
    assert len(events) == 1
    event = events[0]
    assert event.action == "ExecutionForbidden"
    assert event.status == "BLOCKED"
    assert event.target == "crypto_execute_trade"
    assert "paper-only" in event.details["message"]


def test_audit_failure_does_not_mask_execution_forbidden(monkeypatch):
    def _boom(self, event):
        raise RuntimeError("audit backend unavailable")

    monkeypatch.setattr(
        "shared.events.event_logger.SQLiteEventLogger.log_event",
        _boom,
    )
    with pytest.raises(ExecutionForbidden, match="paper-only"):
        raise_execution_forbidden(
            "live execution not implemented; paper-only build",
            target="crypto_execute_trade",
        )
