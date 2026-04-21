"""V16.0 Crypto Swarm — Trade Executioner Agent"""
import os, logging, time, json, sqlite3
from pathlib import Path

logger = logging.getLogger("godclaw.crypto.executor")

DB_PATH = Path("D:/RazAgent_Enterprise/Shared_Memory/claude_memory.db")

def _init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS trade_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exchange TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        amount REAL NOT NULL,
        price REAL,
        cost_usd REAL,
        order_id TEXT,
        status TEXT DEFAULT 'pending',
        stop_loss REAL,
        take_profit REAL,
        created_at TEXT DEFAULT (datetime('now')),
        executed_at TEXT
    )""")
    conn.commit()
    conn.close()

_init_db()

async def prepare_trade(exchange_name: str, symbol: str, side: str, amount: float, **kwargs) -> dict:
    """Prepare a trade order (does NOT execute — requires manual approval)."""
    from .exchange_connector import get_exchange
    from .risk_manager import validate_trade

    # Validate side parameter
    side = side.lower().strip()
    if side not in ("buy", "sell"):
        return {"error": f"Invalid side '{side}'. Must be 'buy' or 'sell'."}

    ex = get_exchange(exchange_name)
    if not ex:
        return {"error": f"Exchange {exchange_name} not connected."}

    try:
        ticker = await ex.fetch_ticker(symbol)
        price = ticker["last"]
        cost_usd = amount * price if side == "buy" else amount * price

        # Get balance for risk check
        balance = await ex.fetch_balance()
        usdt_balance = balance.get("total", {}).get("USDT", 0) or balance.get("total", {}).get("USD", 0) or 0

        # Risk validation
        proposal = {
            "action": f"{side} {symbol}",
            "amount_usd": cost_usd,
            "asset": symbol.split("/")[0],
            "stop_loss_pct": kwargs.get("stop_loss", 5.0),
        }
        risk_result = await validate_trade(proposal, usdt_balance)

        if not risk_result["approved"]:
            return {"error": risk_result["reason"], "risk_rule": risk_result["rule"]}

        # Save pending trade (context manager for safe cleanup)
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute(
                "INSERT INTO trade_history (exchange, symbol, side, amount, price, cost_usd, stop_loss, take_profit, status) VALUES (?,?,?,?,?,?,?,?,?)",
                (exchange_name, symbol, side, amount, price, cost_usd, risk_result["stop_loss"], risk_result["take_profit"], "pending"),
            )
            trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()

        return {
            "output": (
                f"📋 <b>Trade Proposal #{trade_id}</b>\n\n"
                f"Exchange: {exchange_name.upper()}\n"
                f"Action: {side.upper()} {symbol}\n"
                f"Amount: {amount}\n"
                f"Price: ${price:.4f}\n"
                f"Cost: ${cost_usd:.2f}\n"
                f"Stop-Loss: {risk_result['stop_loss']}%\n"
                f"Take-Profit: {risk_result['take_profit']}%\n\n"
                f"⚠️ <b>Awaiting manual approval</b>"
            ),
            "trade_id": trade_id,
            "approved_by_risk": True,
        }
    except Exception as e:
        logger.error(f"[Executor] Prepare failed: {e}", exc_info=True)
        return {"error": f"Prepare failed: {type(e).__name__}: {str(e)[:200]}"}

async def execute_trade(trade_id: int, **kwargs) -> dict:
    """Execute an approved trade by ID. Requires confirmed='true' for safety."""
    from .exchange_connector import get_exchange

    # Human-in-the-loop confirmation gate
    confirmed = str(kwargs.get("confirmed", "")).lower().strip()
    if confirmed != "true":
        return {
            "output": (
                f"⚠️ <b>Confirmation Required</b>\n\n"
                f"To execute trade #{trade_id}, reply with:\n"
                f"<code>/crypto execute {trade_id} confirmed=true</code>"
            ),
            "requires_confirmation": True,
        }

    with sqlite3.connect(str(DB_PATH)) as conn:
        # BEGIN EXCLUSIVE to prevent race conditions (double-execution)
        conn.execute("BEGIN EXCLUSIVE")
        row = conn.execute("SELECT * FROM trade_history WHERE id=? AND status='pending'", (trade_id,)).fetchone()
        if not row:
            conn.rollback()
            return {"error": f"Trade #{trade_id} not found or already executed."}

        exchange_name, symbol, side, amount, prepared_price = row[1], row[2], row[3], row[4], row[5]
        ex = get_exchange(exchange_name)
        if not ex:
            conn.rollback()
            return {"error": f"Exchange {exchange_name} not connected."}

        try:
            # Price re-validation: fetch current price and compare with prepared price
            ticker = await ex.fetch_ticker(symbol)
            current_price = ticker["last"]
            if prepared_price and prepared_price > 0:
                deviation = abs(current_price - prepared_price) / prepared_price
                if deviation > 0.05:
                    conn.rollback()
                    return {
                        "error": (
                            f"Price deviation too high ({deviation:.1%}). "
                            f"Prepared: ${prepared_price:.4f}, Current: ${current_price:.4f}. "
                            f"Re-run prepare_trade for a fresh quote."
                        ),
                    }

            order = await ex.create_order(symbol, "market", side, amount)
            order_id = order.get("id", "unknown")
            fill_price = order.get("average") or order.get("price", 0)

            conn.execute(
                "UPDATE trade_history SET status='executed', order_id=?, price=?, executed_at=datetime('now') WHERE id=?",
                (str(order_id), fill_price, trade_id),
            )
            conn.commit()

            return {
                "output": (
                    f"✅ <b>Trade #{trade_id} EXECUTED</b>\n\n"
                    f"Order ID: {order_id}\n"
                    f"{side.upper()} {amount} {symbol}\n"
                    f"Fill Price: ${fill_price:.4f}"
                ),
                "order_id": order_id,
            }
        except Exception as e:
            conn.execute("UPDATE trade_history SET status='failed' WHERE id=?", (trade_id,))
            conn.commit()
            logger.error(f"[Executor] Execute failed: {e}", exc_info=True)
            return {"error": f"Execution failed: {type(e).__name__}: {str(e)[:200]}"}

async def trade_history(limit: int = 10, **kwargs) -> dict:
    """Get recent trade history."""
    limit = max(1, min(int(limit), 100))  # Bound: 1-100

    with sqlite3.connect(str(DB_PATH)) as conn:
        rows = conn.execute(
            "SELECT id, exchange, symbol, side, amount, price, cost_usd, status, created_at FROM trade_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    if not rows:
        return {"output": "No trade history yet."}

    lines = ["📜 <b>Trade History</b>\n"]
    for r in rows:
        emoji = {"executed": "✅", "pending": "⏳", "failed": "❌"}.get(r[7], "❓")
        lines.append(f"{emoji} #{r[0]} {r[3].upper()} {r[4]} {r[2]} @ ${r[5] or 0:.4f} [{r[7]}]")

    return {"output": "\n".join(lines)}

def register_tools() -> dict:
    return {
        "crypto_prepare_trade": prepare_trade,
        "crypto_execute_trade": execute_trade,
        "crypto_trade_history": trade_history,
    }
