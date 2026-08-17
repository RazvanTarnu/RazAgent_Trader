# -*- coding: utf-8 -*-
"""Trade persistence repository."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from shared.platform.interfaces import TradeRecord, TradeRepository


class SQLiteTradeRepository(TradeRepository):
    """SQLite-backed trade repository."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS platform_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    fee REAL NOT NULL DEFAULT 0,
                    paper_mode INTEGER NOT NULL DEFAULT 1,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.commit()
            conn.close()

    def save_trade(self, trade: TradeRecord) -> int:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                """
                INSERT INTO platform_trades
                (timestamp, exchange, symbol, side, quantity, price, fee, paper_mode, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.timestamp.isoformat(),
                    trade.exchange,
                    trade.symbol,
                    trade.side,
                    trade.quantity,
                    trade.price,
                    trade.fee,
                    1 if trade.paper_mode else 0,
                    json.dumps(trade.metadata),
                ),
            )
            conn.commit()
            trade_id = int(cur.lastrowid)
            conn.close()
            return trade_id

    def get_trade(self, trade_id: int) -> Optional[TradeRecord]:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM platform_trades WHERE id = ?", (trade_id,)
            ).fetchone()
            conn.close()
            if not row:
                return None
            return self._row_to_trade(row)

    def list_trades(
        self,
        *,
        limit: int = 50,
        exchange: Optional[str] = None,
    ) -> list[TradeRecord]:
        with self._lock:
            conn = self._connect()
            if exchange:
                rows = conn.execute(
                    "SELECT * FROM platform_trades WHERE exchange = ? ORDER BY id DESC LIMIT ?",
                    (exchange, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM platform_trades ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            conn.close()
            return [self._row_to_trade(r) for r in rows]

    @staticmethod
    def _row_to_trade(row: sqlite3.Row) -> TradeRecord:
        return TradeRecord(
            id=int(row["id"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            exchange=row["exchange"],
            symbol=row["symbol"],
            side=row["side"],
            quantity=float(row["quantity"]),
            price=float(row["price"]),
            fee=float(row["fee"]),
            paper_mode=bool(row["paper_mode"]),
            metadata=json.loads(row["metadata"] or "{}"),
        )
