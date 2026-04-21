"""Trade Journal — V11.30

Comprehensive trade logging for auto-improvement.
Separate from trade_history in claude_memory.db — this focuses on
post-trade analysis, lessons learned, and pattern detection.

DB: Shared_Memory/claude_memory.db (coexists with existing tables).
"""
import json
import logging
import sqlite3

from shared.db_base import get_connection
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("godclaw.trade_journal")

JOURNAL_DB = Path("D:/RazAgent_Enterprise/Shared_Memory/claude_memory.db")


def _get_conn() -> sqlite3.Connection:
    conn = get_connection("claude_memory.db", data_dir="D:/RazAgent_Enterprise/Shared_Memory")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trade_journal (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_open           TEXT NOT NULL DEFAULT (datetime('now')),
            ts_close          TEXT,
            pair              TEXT NOT NULL,
            side              TEXT NOT NULL CHECK(side IN ('buy','sell')),
            entry_price       REAL NOT NULL,
            exit_price        REAL,
            size_usd          REAL NOT NULL,
            pnl_usd           REAL DEFAULT 0,
            pnl_pct           REAL DEFAULT 0,
            strategy          TEXT DEFAULT 'manual',
            entry_reason      TEXT,
            exit_reason       TEXT,
            rsi_at_entry      REAL,
            volume_at_entry   REAL,
            market_condition  TEXT CHECK(market_condition IN ('bull','bear','sideways',NULL)),
            stop_loss         REAL,
            take_profit       REAL,
            duration_minutes  INTEGER DEFAULT 0,
            outcome           TEXT CHECK(outcome IN ('win','loss','breakeven',NULL)),
            paper_mode        INTEGER DEFAULT 1,
            mistakes          TEXT,
            lessons_learned   TEXT,
            daily_review_id   INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_journal_ts ON trade_journal(ts_open);
        CREATE INDEX IF NOT EXISTS idx_journal_pair ON trade_journal(pair);
        CREATE INDEX IF NOT EXISTS idx_journal_outcome ON trade_journal(outcome);

        CREATE TABLE IF NOT EXISTS daily_reviews (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT NOT NULL DEFAULT (datetime('now')),
            period_days     INTEGER DEFAULT 7,
            trade_count     INTEGER,
            win_rate        REAL,
            total_pnl       REAL,
            best_trade_pnl  REAL,
            worst_trade_pnl REAL,
            insights        TEXT,
            adjustments     TEXT,
            balance_usd     REAL
        );
    """)
    # V11.30: Add current_sl column for trailing stop tracking
    try:
        conn.execute("ALTER TABLE trade_journal ADD COLUMN current_sl REAL")
        conn.commit()
        logger.info("trade_journal: added current_sl column")
    except sqlite3.OperationalError:
        pass  # Column already exists — safe to ignore

    conn.commit()
    return conn


def log_trade_open(
    pair: str,
    side: str,
    entry_price: float,
    size_usd: float,
    strategy: str = "manual",
    entry_reason: str = "",
    rsi_at_entry: float | None = None,
    volume_at_entry: float | None = None,
    market_condition: str | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    paper_mode: bool = True,
) -> int | None:
    """Log trade open. Returns trade_id."""
    try:
        conn = _get_conn()
        cursor = conn.execute(
            """INSERT INTO trade_journal
            (pair, side, entry_price, size_usd, strategy, entry_reason,
             rsi_at_entry, volume_at_entry, market_condition, stop_loss,
             take_profit, paper_mode)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pair, side, entry_price, size_usd, strategy, entry_reason,
             rsi_at_entry, volume_at_entry, market_condition, stop_loss,
             take_profit, 1 if paper_mode else 0),
        )
        conn.commit()
        trade_id = cursor.lastrowid
        conn.close()
        return trade_id
    except Exception as e:
        logger.error(f"Failed to log trade open: {e}")
        return None


def log_trade_close(
    trade_id: int,
    exit_price: float,
    exit_reason: str = "",
    mistakes: str = "",
    lessons_learned: str = "",
) -> bool:
    """Log trade close with P&L calculation."""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT entry_price, size_usd, side, ts_open FROM trade_journal WHERE id = ?",
            (trade_id,),
        ).fetchone()
        if not row:
            conn.close()
            return False

        entry_price = row["entry_price"]
        size_usd = row["size_usd"]
        side = row["side"]
        ts_open = row["ts_open"]

        # Calculate P&L
        if side == "buy":
            pnl_pct = (exit_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - exit_price) / entry_price
        pnl_usd = size_usd * pnl_pct

        # Determine outcome
        if abs(pnl_pct) < 0.001:
            outcome = "breakeven"
        elif pnl_usd > 0:
            outcome = "win"
        else:
            outcome = "loss"

        # Duration
        try:
            open_dt = datetime.fromisoformat(ts_open)
            duration_min = int((datetime.now() - open_dt).total_seconds() / 60)
        except Exception:
            duration_min = 0

        conn.execute(
            """UPDATE trade_journal SET
                ts_close = datetime('now'),
                exit_price = ?, pnl_usd = ?, pnl_pct = ?,
                exit_reason = ?, duration_minutes = ?,
                outcome = ?, mistakes = ?, lessons_learned = ?
            WHERE id = ?""",
            (exit_price, round(pnl_usd, 4), round(pnl_pct, 4),
             exit_reason, duration_min, outcome, mistakes, lessons_learned,
             trade_id),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to log trade close: {e}")
        return False


def get_daily_stats(days: int = 1) -> dict:
    """Get P&L, win rate, avg trade for the last N days."""
    try:
        conn = _get_conn()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT * FROM trade_journal WHERE ts_open >= ? AND outcome IS NOT NULL ORDER BY ts_open DESC",
            (cutoff,),
        ).fetchall()
        conn.close()

        if not rows:
            return {"trades": 0, "pnl_usd": 0, "win_rate": 0, "avg_trade": 0, "best": 0, "worst": 0}

        pnls = [r["pnl_usd"] for r in rows]
        wins = sum(1 for r in rows if r["outcome"] == "win")

        return {
            "trades": len(rows),
            "pnl_usd": round(sum(pnls), 2),
            "win_rate": round(wins / len(rows) * 100, 1),
            "avg_trade": round(sum(pnls) / len(rows), 4),
            "best": round(max(pnls), 4),
            "worst": round(min(pnls), 4),
        }
    except Exception as e:
        logger.error(f"Failed to get daily stats: {e}")
        return {"trades": 0, "pnl_usd": 0, "win_rate": 0, "avg_trade": 0, "best": 0, "worst": 0}


def get_lessons(n: int = 10) -> list[str]:
    """Get the last N lessons learned for LLM context injection."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT lessons_learned FROM trade_journal WHERE lessons_learned IS NOT NULL AND lessons_learned != '' ORDER BY ts_open DESC LIMIT ?",
            (n,),
        ).fetchall()
        conn.close()
        return [r["lessons_learned"] for r in rows]
    except Exception:
        return []


def get_pattern_analysis(days: int = 30) -> dict:
    """Get trade data for self-improvement analysis."""
    try:
        conn = _get_conn()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT * FROM trade_journal WHERE ts_open >= ? AND outcome IS NOT NULL ORDER BY ts_open",
            (cutoff,),
        ).fetchall()
        conn.close()

        if not rows:
            return {"total": 0, "by_pair": {}, "by_strategy": {}, "by_market": {}}

        by_pair = {}
        by_strategy = {}
        by_market = {}
        for r in rows:
            pair = r["pair"]
            strategy = r["strategy"] or "unknown"
            market = r["market_condition"] or "unknown"

            for key, group in [(pair, by_pair), (strategy, by_strategy), (market, by_market)]:
                if key not in group:
                    group[key] = {"trades": 0, "wins": 0, "pnl": 0}
                group[key]["trades"] += 1
                if r["outcome"] == "win":
                    group[key]["wins"] += 1
                group[key]["pnl"] += r["pnl_usd"] or 0

        return {
            "total": len(rows),
            "days": days,
            "by_pair": by_pair,
            "by_strategy": by_strategy,
            "by_market": by_market,
        }
    except Exception as e:
        logger.error(f"Pattern analysis failed: {e}")
        return {"total": 0, "by_pair": {}, "by_strategy": {}, "by_market": {}}
