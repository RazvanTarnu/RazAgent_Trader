# -*- coding: utf-8 -*-
"""Persistence and event logger tests."""

from datetime import datetime, timezone
from pathlib import Path

from shared.events.event_logger import SQLiteEventLogger
from shared.persistence.trade_repository import SQLiteTradeRepository
from shared.platform.interfaces import AuditEvent, TradeRecord


def test_trade_repository_roundtrip(tmp_path: Path):
    repo = SQLiteTradeRepository(tmp_path / "trades.db")
    trade_id = repo.save_trade(
        TradeRecord(
            id=None,
            timestamp=datetime.now(timezone.utc),
            exchange="binance",
            symbol="BTC/USDT",
            side="buy",
            quantity=0.01,
            price=50000.0,
            fee=0.1,
            paper_mode=True,
        )
    )
    fetched = repo.get_trade(trade_id)
    assert fetched is not None
    assert fetched.symbol == "BTC/USDT"
    assert fetched.paper_mode is True


def test_event_logger_append_only(tmp_path: Path):
    logger = SQLiteEventLogger(tmp_path / "events.db")
    logger.log_event(
        AuditEvent(
            timestamp=datetime.now(timezone.utc),
            category="platform",
            action="startup",
            actor="test",
            target="lifecycle",
            details={"paper_mode": True},
            status="ok",
        )
    )
    events = logger.list_events(category="platform", limit=10)
    assert len(events) == 1
    assert events[0].action == "startup"
