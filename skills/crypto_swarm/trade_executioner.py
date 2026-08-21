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

async def prepare_trade(exchange_name: str, symbol: str, side: str, amount: float, **kwargs) -> dict:
    """Prepare a trade order (does NOT execute — requires manual approval)."""
    _init_db()
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
    """Reject every execution attempt in the paper-only build."""
    from shared.execution import ExecutionForbidden

    raise ExecutionForbidden("live execution not implemented; paper-only build")

async def trade_history(limit: int = 10, **kwargs) -> dict:
    """Get recent trade history."""
    _init_db()
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
