"""
Trading Strategy Learner V1.0
=============================
Learns trading patterns from simulated_trades, proposes rules, backtests strategies.
Uses Ollama (qwen3:30b-a3b) locally for pattern analysis — zero cloud cost.

DB: D:\\RazAgent_Enterprise\\data\\financial_agents.db (SQLite WAL)
Simulation window: now .. 2026-05-26

Skills exported:
    strategy_analyze, strategy_backtest, strategy_propose, strategy_report, strategy_rules
"""

from pathlib import Path
import sqlite3
import json
import logging
import time
import random
import math
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

DB_PATH = Path(r"D:\RazAgent_Enterprise\data\financial_agents.db")
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3:30b-a3b"
SIMULATION_END = datetime(2026, 5, 26)

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _ensure_db() -> sqlite3.Connection:
    """Create tables if missing, return WAL-mode connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS simulated_trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL DEFAULT (datetime('now')),
            pair        TEXT NOT NULL,
            exchange    TEXT NOT NULL DEFAULT 'binance',
            direction   TEXT NOT NULL CHECK(direction IN ('long','short')),
            entry_price REAL NOT NULL,
            exit_price  REAL NOT NULL,
            quantity    REAL NOT NULL DEFAULT 1.0,
            pnl         REAL NOT NULL,
            duration_s  INTEGER NOT NULL DEFAULT 0,
            tags        TEXT DEFAULT '[]',
            strategy_id INTEGER,
            FOREIGN KEY (strategy_id) REFERENCES learned_strategies(id)
        );

        CREATE TABLE IF NOT EXISTS learned_strategies (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            name        TEXT NOT NULL,
            description TEXT,
            pair        TEXT,
            direction   TEXT,
            win_rate    REAL,
            avg_profit  REAL,
            max_drawdown REAL,
            sharpe      REAL,
            trade_count INTEGER DEFAULT 0,
            status      TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed','approved','rejected','retired')),
            metadata    TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS strategy_rules (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id   INTEGER NOT NULL,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            condition     TEXT NOT NULL,
            action        TEXT NOT NULL,
            confidence    REAL NOT NULL DEFAULT 0.5,
            status        TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed','approved','rejected')),
            FOREIGN KEY (strategy_id) REFERENCES learned_strategies(id)
        );
    """)

    # Migration: ensure 'ts' column exists (older DBs may lack it)
    # NOTE: SQLite cannot use functions as DEFAULT in ALTER TABLE,
    # so we ADD with no default, then UPDATE existing rows.
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(simulated_trades)").fetchall()}
        if "ts" not in cols:
            conn.execute("ALTER TABLE simulated_trades ADD COLUMN ts TEXT")
            conn.execute("UPDATE simulated_trades SET ts = datetime('now') WHERE ts IS NULL")
            conn.commit()
            logger.info("Migrated simulated_trades: added 'ts' column")
    except Exception as e:
        logger.warning(f"ts column migration: {e}")

    # Create indexes after migration ensures columns exist
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_trades_pair ON simulated_trades(pair);
        CREATE INDEX IF NOT EXISTS idx_trades_ts ON simulated_trades(ts);
        CREATE INDEX IF NOT EXISTS idx_strategies_status ON learned_strategies(status);
        CREATE INDEX IF NOT EXISTS idx_rules_strategy ON strategy_rules(strategy_id);
    """)
    conn.commit()
    return conn


def _trade_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM simulated_trades").fetchone()[0]


def _sync_trade_journal(conn: sqlite3.Connection) -> int:
    """Sync closed trades from trade_journal (ts_open/ts_close) into simulated_trades.

    trade_journal lives in Shared_Memory/claude_memory.db and tracks real
    paper/live trades via trading_auditor.py. This function imports completed
    trades so the strategy learner can analyze actual outcomes.

    Returns number of newly imported trades.
    """
    import os
    journal_db = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..", "Shared_Memory", "claude_memory.db"
    )
    if not os.path.isfile(journal_db):
        return 0

    try:
        jconn = sqlite3.connect(journal_db, timeout=5)
        jconn.execute("PRAGMA journal_mode=WAL")
        jconn.row_factory = sqlite3.Row

        # Check if trade_journal table exists
        tables = {r[0] for r in jconn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "trade_journal" not in tables:
            jconn.close()
            return 0

        # Get closed trades not yet synced (use ts_open as dedup key)
        existing = {r[0] for r in conn.execute(
            "SELECT ts FROM simulated_trades WHERE ts IS NOT NULL"
        ).fetchall()}

        rows = jconn.execute("""
            SELECT pair, ts_open, ts_close, pnl_usd, duration_minutes
            FROM trade_journal
            WHERE ts_close IS NOT NULL
        """).fetchall()
        jconn.close()

        imported = 0
        for r in rows:
            ts_key = r["ts_open"]
            if ts_key in existing:
                continue
            pnl = r["pnl_usd"] or 0.0
            direction = "long" if pnl >= 0 else "short"
            duration_s = int((r["duration_minutes"] or 0) * 60)
            conn.execute(
                "INSERT INTO simulated_trades (ts, pair, exchange, direction, "
                "entry_price, exit_price, quantity, pnl, duration_s) "
                "VALUES (?, ?, 'binance', ?, 0, 0, 1.0, ?, ?)",
                (ts_key, r["pair"] or "UNKNOWN", direction, pnl, duration_s),
            )
            imported += 1

        if imported:
            conn.commit()
            logger.info(f"Synced {imported} trades from trade_journal into strategy learner")
        return imported
    except Exception as e:
        logger.warning(f"trade_journal sync failed: {e}")
        return 0


# ---------------------------------------------------------------------------
# Ollama helper
# ---------------------------------------------------------------------------

async def _ask_ollama(prompt: str, max_tokens: int = 2048) -> str:
    """Send prompt to local Ollama and return generated text."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": 0.4,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
    except Exception as e:
        logger.error("Ollama call failed: %s", e)
        return f"[Ollama error: {e}]"


# ---------------------------------------------------------------------------
# Core analytics
# ---------------------------------------------------------------------------

def _group_stats(conn: sqlite3.Connection) -> list[dict]:
    """Group trades by pair/exchange/direction, compute stats per group."""
    rows = conn.execute("""
        SELECT pair, exchange, direction,
               COUNT(*)                          AS cnt,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
               AVG(pnl)                          AS avg_pnl,
               MIN(pnl)                          AS worst,
               MAX(pnl)                          AS best,
               SUM(pnl)                          AS total_pnl
        FROM simulated_trades
        GROUP BY pair, exchange, direction
        HAVING cnt >= 3
        ORDER BY avg_pnl DESC
    """).fetchall()

    groups = []
    for r in rows:
        cnt = r["cnt"]
        win_rate = r["wins"] / cnt if cnt else 0
        avg_pnl = r["avg_pnl"] or 0

        # Sharpe approximation: mean(pnl) / std(pnl)
        pnls = [
            row[0]
            for row in conn.execute(
                "SELECT pnl FROM simulated_trades WHERE pair=? AND exchange=? AND direction=?",
                (r["pair"], r["exchange"], r["direction"]),
            ).fetchall()
        ]
        mean_pnl = sum(pnls) / len(pnls) if pnls else 0
        var = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls) if pnls else 0
        std = math.sqrt(var) if var > 0 else 1e-9
        sharpe = mean_pnl / std

        # Max drawdown (peak-to-trough on cumulative PnL)
        cum = 0
        peak = 0
        max_dd = 0
        for p in pnls:
            cum += p
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd

        groups.append({
            "pair": r["pair"],
            "exchange": r["exchange"],
            "direction": r["direction"],
            "trade_count": cnt,
            "win_rate": round(win_rate, 4),
            "avg_profit": round(avg_pnl, 4),
            "max_drawdown": round(max_dd, 4),
            "sharpe": round(sharpe, 4),
            "total_pnl": round(r["total_pnl"], 4),
        })
    return groups


# ---------------------------------------------------------------------------
# Auto-learning triggers
# ---------------------------------------------------------------------------

async def _maybe_auto_learn(conn: sqlite3.Connection) -> str | None:
    """Fire automatic learning milestones."""
    count = _trade_count(conn)
    msgs = []
    if count >= 100:
        existing = conn.execute(
            "SELECT COUNT(*) FROM learned_strategies"
        ).fetchone()[0]
        if existing == 0:
            res = await strategy_analyze({})
            if res["success"]:
                msgs.append(f"Auto-analyze triggered at {count} trades")
    if count >= 500:
        proposed = conn.execute(
            "SELECT COUNT(*) FROM strategy_rules WHERE status='proposed'"
        ).fetchone()[0]
        if proposed == 0:
            res = await strategy_propose({})
            if res["success"]:
                msgs.append(f"Auto-propose triggered at {count} trades")
    if count >= 1000:
        strats = conn.execute(
            "SELECT id FROM learned_strategies WHERE status='proposed' LIMIT 1"
        ).fetchone()
        if strats:
            res = await strategy_backtest({"strategy_id": strats["id"]})
            if res["success"]:
                msgs.append(f"Auto-backtest triggered at {count} trades")
    return " | ".join(msgs) if msgs else None


# ---------------------------------------------------------------------------
# Skill functions
# ---------------------------------------------------------------------------

async def strategy_analyze(params: dict) -> dict:
    """Group trades by pair/exchange/time/direction, compute metrics, use Ollama to find patterns."""
    try:
        conn = _ensure_db()
        # V1.0: Sync closed trades from trade_journal (ts_open/ts_close) before analysis
        synced = _sync_trade_journal(conn)
        if synced:
            logger.info(f"strategy_analyze: synced {synced} trades from trade_journal")
        count = _trade_count(conn)
        if count < 3:
            return {"success": False, "output": f"Need at least 3 trades to analyze (have {count})"}

        groups = _group_stats(conn)
        if not groups:
            return {"success": False, "output": "No trade groups with >= 3 trades found"}

        # Ask Ollama to find patterns
        summary = json.dumps(groups[:20], indent=2)
        prompt = (
            "You are a quantitative trading analyst. Analyze these trade group statistics "
            "and identify the strongest patterns, correlations, and actionable insights.\n\n"
            f"Trade Groups:\n{summary}\n\n"
            "Return a concise analysis with:\n"
            "1. Top performing groups and why\n"
            "2. Common failure patterns\n"
            "3. Suggested strategy rules (condition -> action)\n"
            "Keep it under 500 words."
        )
        analysis = await _ask_ollama(prompt)

        # Save top groups as learned strategies
        saved = 0
        for g in groups[:5]:
            conn.execute(
                """INSERT INTO learned_strategies
                   (name, description, pair, direction, win_rate, avg_profit, max_drawdown, sharpe, trade_count, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"{g['pair']}_{g['direction']}_{g['exchange']}",
                    f"Auto-learned from {g['trade_count']} trades",
                    g["pair"],
                    g["direction"],
                    g["win_rate"],
                    g["avg_profit"],
                    g["max_drawdown"],
                    g["sharpe"],
                    g["trade_count"],
                    json.dumps(g),
                ),
            )
            saved += 1
        conn.commit()
        conn.close()

        return {
            "success": True,
            "output": {
                "total_trades": count,
                "groups_found": len(groups),
                "strategies_saved": saved,
                "top_groups": groups[:5],
                "ollama_analysis": analysis,
            },
        }
    except Exception as e:
        logger.exception("strategy_analyze failed")
        return {"success": False, "output": str(e)}


async def strategy_backtest(params: dict) -> dict:
    """Backtest a strategy against simulated_trades and compare vs random baseline."""
    try:
        strategy_id = params.get("strategy_id")
        if not strategy_id:
            return {"success": False, "output": "Missing required parameter: strategy_id"}

        conn = _ensure_db()
        strat = conn.execute(
            "SELECT * FROM learned_strategies WHERE id=?", (strategy_id,)
        ).fetchone()
        if not strat:
            conn.close()
            return {"success": False, "output": f"Strategy {strategy_id} not found"}

        # Get matching trades
        query = "SELECT pnl FROM simulated_trades WHERE 1=1"
        bind = []
        if strat["pair"]:
            query += " AND pair=?"
            bind.append(strat["pair"])
        if strat["direction"]:
            query += " AND direction=?"
            bind.append(strat["direction"])
        query += " ORDER BY ts"

        trades = [r[0] for r in conn.execute(query, bind).fetchall()]
        if len(trades) < 5:
            conn.close()
            return {"success": False, "output": f"Not enough matching trades ({len(trades)}) for backtest"}

        # Strategy PnL curve
        cum_pnl = []
        running = 0
        for p in trades:
            running += p
            cum_pnl.append(running)

        strat_total = cum_pnl[-1] if cum_pnl else 0
        strat_max_dd = 0
        peak = 0
        for v in cum_pnl:
            if v > peak:
                peak = v
            dd = peak - v
            if dd > strat_max_dd:
                strat_max_dd = dd

        # Random baseline (shuffle PnL 100x, average)
        all_pnls = [r[0] for r in conn.execute("SELECT pnl FROM simulated_trades").fetchall()]
        random_totals = []
        for _ in range(100):
            sample = random.choices(all_pnls, k=len(trades))
            random_totals.append(sum(sample))
        baseline_avg = sum(random_totals) / len(random_totals) if random_totals else 0

        # Sharpe of strategy trades
        mean_pnl = sum(trades) / len(trades)
        var = sum((p - mean_pnl) ** 2 for p in trades) / len(trades)
        std = math.sqrt(var) if var > 0 else 1e-9
        sharpe = mean_pnl / std

        # Update strategy record
        conn.execute(
            """UPDATE learned_strategies
               SET sharpe=?, max_drawdown=?, avg_profit=?, trade_count=?, metadata=?
               WHERE id=?""",
            (
                round(sharpe, 4),
                round(strat_max_dd, 4),
                round(mean_pnl, 4),
                len(trades),
                json.dumps({
                    "backtest_at": datetime.utcnow().isoformat(),
                    "total_pnl": round(strat_total, 4),
                    "baseline_avg": round(baseline_avg, 4),
                }),
                strategy_id,
            ),
        )
        conn.commit()
        conn.close()

        edge = strat_total - baseline_avg
        return {
            "success": True,
            "output": {
                "strategy_id": strategy_id,
                "strategy_name": strat["name"],
                "trades_tested": len(trades),
                "total_pnl": round(strat_total, 4),
                "max_drawdown": round(strat_max_dd, 4),
                "sharpe": round(sharpe, 4),
                "baseline_avg_pnl": round(baseline_avg, 4),
                "edge_vs_random": round(edge, 4),
                "verdict": "OUTPERFORMS" if edge > 0 else "UNDERPERFORMS",
            },
        }
    except Exception as e:
        logger.exception("strategy_backtest failed")
        return {"success": False, "output": str(e)}


async def strategy_propose(params: dict) -> dict:
    """Use Ollama to propose trading rules based on learned patterns."""
    try:
        conn = _ensure_db()
        groups = _group_stats(conn)
        if not groups:
            conn.close()
            return {"success": False, "output": "No trade data to derive rules from"}

        # Get existing strategies
        strats = conn.execute(
            "SELECT id, name, pair, direction, win_rate, sharpe FROM learned_strategies ORDER BY sharpe DESC LIMIT 10"
        ).fetchall()

        strats_json = json.dumps([dict(s) for s in strats], indent=2) if strats else "None yet"
        groups_json = json.dumps(groups[:10], indent=2)

        prompt = (
            "You are a quantitative trading strategist. Based on the trade statistics and strategies below, "
            "propose 3-5 concrete trading rules.\n\n"
            f"Trade Groups:\n{groups_json}\n\n"
            f"Existing Strategies:\n{strats_json}\n\n"
            "For each rule, return EXACTLY this JSON format (no extra text):\n"
            '[\n'
            '  {"condition": "...", "action": "...", "confidence": 0.0-1.0, "strategy_name": "..."},\n'
            '  ...\n'
            ']\n\n'
            "condition = when to enter (e.g. 'BTC/USDT drops 3% in 1h on Binance')\n"
            "action = what to do (e.g. 'Open long, TP +2%, SL -1%')\n"
            "confidence = how confident (0.0-1.0) based on the data\n"
            "strategy_name = which existing strategy this ties to, or a new name\n"
        )
        raw = await _ask_ollama(prompt, max_tokens=2048)

        # Parse JSON from Ollama response
        rules = []
        try:
            # Try to extract JSON array from response
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start >= 0 and end > start:
                rules = json.loads(raw[start:end])
        except json.JSONDecodeError:
            logger.warning("Could not parse Ollama rules JSON, saving raw response")
            rules = [{"condition": "see raw analysis", "action": raw[:500], "confidence": 0.3, "strategy_name": "manual_review"}]

        # Find or create strategy for each rule, insert into strategy_rules
        saved = 0
        for rule in rules:
            sname = rule.get("strategy_name", "auto_proposed")
            strat_row = conn.execute(
                "SELECT id FROM learned_strategies WHERE name=? LIMIT 1", (sname,)
            ).fetchone()
            if strat_row:
                sid = strat_row["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO learned_strategies (name, description, status) VALUES (?, ?, 'proposed')",
                    (sname, f"Auto-proposed by Ollama at {datetime.utcnow().isoformat()}"),
                )
                sid = cur.lastrowid

            conn.execute(
                "INSERT INTO strategy_rules (strategy_id, condition, action, confidence) VALUES (?, ?, ?, ?)",
                (sid, rule.get("condition", ""), rule.get("action", ""), rule.get("confidence", 0.5)),
            )
            saved += 1

        conn.commit()
        conn.close()

        return {
            "success": True,
            "output": {
                "rules_proposed": saved,
                "rules": rules,
            },
        }
    except Exception as e:
        logger.exception("strategy_propose failed")
        return {"success": False, "output": str(e)}


async def strategy_report(params: dict) -> dict:
    """Weekly report: top 3 strategies by Sharpe, days remaining in simulation."""
    try:
        conn = _ensure_db()
        now = datetime.utcnow()
        days_remaining = max(0, (SIMULATION_END - now).days)
        total_trades = _trade_count(conn)

        top_strats = conn.execute(
            """SELECT id, name, pair, direction, win_rate, avg_profit, max_drawdown, sharpe, trade_count, status
               FROM learned_strategies
               WHERE sharpe IS NOT NULL
               ORDER BY sharpe DESC
               LIMIT 3"""
        ).fetchall()

        top_list = [dict(s) for s in top_strats]

        # Overall stats
        overall = conn.execute(
            """SELECT
                   COUNT(*) AS total,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                   AVG(pnl) AS avg_pnl,
                   SUM(pnl) AS total_pnl
               FROM simulated_trades"""
        ).fetchone()

        rule_count = conn.execute(
            "SELECT COUNT(*) FROM strategy_rules"
        ).fetchone()[0]

        conn.close()

        report = {
            "report_date": now.strftime("%Y-%m-%d"),
            "simulation_end": SIMULATION_END.strftime("%Y-%m-%d"),
            "days_remaining": days_remaining,
            "total_trades": total_trades,
            "overall_win_rate": round(overall["wins"] / overall["total"], 4) if overall["total"] else 0,
            "overall_avg_pnl": round(overall["avg_pnl"], 4) if overall["avg_pnl"] else 0,
            "overall_total_pnl": round(overall["total_pnl"], 4) if overall["total_pnl"] else 0,
            "active_rules": rule_count,
            "top_3_strategies": top_list,
        }

        return {"success": True, "output": report}
    except Exception as e:
        logger.exception("strategy_report failed")
        return {"success": False, "output": str(e)}


async def strategy_rules(params: dict) -> dict:
    """Return all proposed + approved rules as JSON."""
    try:
        conn = _ensure_db()
        rows = conn.execute(
            """SELECT r.id, r.strategy_id, s.name AS strategy_name,
                      r.condition, r.action, r.confidence, r.status, r.created_at
               FROM strategy_rules r
               JOIN learned_strategies s ON s.id = r.strategy_id
               WHERE r.status IN ('proposed', 'approved')
               ORDER BY r.confidence DESC"""
        ).fetchall()
        conn.close()

        rules_list = [dict(r) for r in rows]
        return {
            "success": True,
            "output": {
                "count": len(rules_list),
                "rules": rules_list,
            },
        }
    except Exception as e:
        logger.exception("strategy_rules failed")
        return {"success": False, "output": str(e)}


# ---------------------------------------------------------------------------
# Skill registration
# ---------------------------------------------------------------------------

SKILL_TOOLS = {
    "strategy_analyze": {
        "fn": strategy_analyze,
        "description": "Analyze simulated trades: groups by pair/exchange/direction, computes win_rate/avg_profit/drawdown/Sharpe, uses Ollama to find patterns, saves to learned_strategies",
        "parameters": {},
    },
    "strategy_backtest": {
        "fn": strategy_backtest,
        "description": "Backtest a learned strategy against simulated_trades and compare vs random baseline",
        "parameters": {
            "strategy_id": {"type": "integer", "required": True, "description": "ID of the strategy to backtest"},
        },
    },
    "strategy_propose": {
        "fn": strategy_propose,
        "description": "Use Ollama to propose concrete trading rules (condition/action/confidence) based on learned patterns",
        "parameters": {},
    },
    "strategy_report": {
        "fn": strategy_report,
        "description": "Weekly report: top 3 strategies by Sharpe ratio, days remaining in simulation (ends 2026-05-26)",
        "parameters": {},
    },
    "strategy_rules": {
        "fn": strategy_rules,
        "description": "Return all proposed and approved trading rules as JSON",
        "parameters": {},
    },
}
