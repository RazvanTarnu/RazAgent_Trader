# -*- coding: utf-8 -*-
"""Drawdown Guard V42.00 — Emergency Trading Halt.

Monitors daily equity and halts ALL trading if loss exceeds -10%.
This is the ultimate safety net before live trading activation.

Architecture:
  - Tracks daily_start_equity in data/drawdown_state.json (reset at midnight)
  - check_drawdown() called BEFORE every trade execution
  - If current_equity < daily_start_equity * 0.90 → STRICT_HALT
  - HALT can only be lifted via manual admin reset (/drawdown_reset)

Usage:
    from shared.drawdown_guard import check_drawdown, reset_drawdown, get_drawdown_status

    result = check_drawdown(current_equity=45.0)
    if result["halted"]:
        # REFUSE trade — emergency stop active
        ...
"""

import json
import logging
import sqlite3

from shared.db_base import get_connection
import time
from datetime import datetime, date
from pathlib import Path

from shared.config import DATA_DIR, AUDIT_DB

logger = logging.getLogger("godclaw.drawdown_guard")

# ═══════════════════════════════════════════════════════
# CONFIGURATION — IMMUTABLE SAFETY LIMITS
# ═══════════════════════════════════════════════════════
MAX_DAILY_DRAWDOWN_PCT = 0.10  # -10% triggers STRICT_HALT
STATE_FILE = DATA_DIR / "drawdown_state.json"


def _load_state() -> dict:
    """Load drawdown state from JSON file."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "date": str(date.today()),
        "daily_start_equity": 0.0,
        "halted": False,
        "halt_reason": "",
        "halt_time": "",
        "lowest_equity": 0.0,
    }


def _save_state(state: dict) -> None:
    """Persist drawdown state to JSON file."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _audit_log(action: str, details: str, status: str = "INFO") -> None:
    """Log drawdown events to audit trail."""
    try:
        conn = get_connection("audit_logs.db")
        conn.execute(
            "INSERT OR IGNORE INTO audit_actions "
            "(timestamp, timestamp_unix, action_type, agent_id, target, details, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.utcnow().isoformat(), time.time(),
                action, "drawdown_guard", "trading_engine",
                details[:1000], status,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════

def check_drawdown(current_equity: float) -> dict:
    """Check if trading should be halted due to drawdown.

    Must be called BEFORE every trade execution.

    Args:
        current_equity: Current total portfolio value in USD.

    Returns:
        dict with keys:
            halted (bool): True if trading is stopped
            reason (str): Human-readable reason
            drawdown_pct (float): Current drawdown percentage
            daily_start (float): Equity at day start
    """
    state = _load_state()
    today = str(date.today())

    # New day → reset state (unless halted — halt persists until manual reset)
    if state["date"] != today and not state["halted"]:
        state = {
            "date": today,
            "daily_start_equity": current_equity,
            "halted": False,
            "halt_reason": "",
            "halt_time": "",
            "lowest_equity": current_equity,
        }
        _save_state(state)
        _audit_log("DRAWDOWN_DAY_RESET", f"New day. Start equity: ${current_equity:.2f}")
        logger.info("Drawdown guard: new day, start equity $%.2f", current_equity)

    # First call of the day → set baseline
    if state["daily_start_equity"] <= 0:
        state["daily_start_equity"] = current_equity
        state["lowest_equity"] = current_equity
        _save_state(state)

    # Already halted → refuse
    if state["halted"]:
        return {
            "halted": True,
            "reason": state["halt_reason"],
            "drawdown_pct": 0.0,
            "daily_start": state["daily_start_equity"],
        }

    # Calculate drawdown
    start_equity = state["daily_start_equity"]
    if start_equity <= 0:
        return {"halted": False, "reason": "No baseline", "drawdown_pct": 0.0, "daily_start": 0.0}

    drawdown_pct = (start_equity - current_equity) / start_equity
    state["lowest_equity"] = min(state.get("lowest_equity", current_equity), current_equity)

    # TRIGGER HALT
    if drawdown_pct >= MAX_DAILY_DRAWDOWN_PCT:
        state["halted"] = True
        state["halt_reason"] = (
            f"STRICT_HALT: Daily drawdown {drawdown_pct:.1%} >= {MAX_DAILY_DRAWDOWN_PCT:.0%} limit. "
            f"Equity dropped from ${start_equity:.2f} to ${current_equity:.2f}. "
            f"All trading STOPPED. Use /drawdown_reset to resume."
        )
        state["halt_time"] = datetime.now().isoformat()
        _save_state(state)

        _audit_log(
            "DRAWDOWN_HALT_TRIGGERED",
            f"Equity ${start_equity:.2f} -> ${current_equity:.2f} "
            f"(drawdown {drawdown_pct:.1%}). ALL TRADING HALTED.",
            status="CRITICAL",
        )
        logger.critical(
            "DRAWDOWN HALT: equity $%.2f -> $%.2f (%.1f%% loss). Trading STOPPED.",
            start_equity, current_equity, drawdown_pct * 100,
        )

        return {
            "halted": True,
            "reason": state["halt_reason"],
            "drawdown_pct": drawdown_pct,
            "daily_start": start_equity,
        }

    # Safe — no halt
    _save_state(state)
    return {
        "halted": False,
        "reason": f"Drawdown {drawdown_pct:.1%} (limit {MAX_DAILY_DRAWDOWN_PCT:.0%})",
        "drawdown_pct": drawdown_pct,
        "daily_start": start_equity,
    }


def reset_drawdown(admin_reason: str = "Manual admin reset") -> dict:
    """Manually reset the drawdown halt. CEO/admin only.

    Returns:
        dict with reset confirmation.
    """
    state = _load_state()
    was_halted = state.get("halted", False)

    state["halted"] = False
    state["halt_reason"] = ""
    state["halt_time"] = ""
    state["date"] = str(date.today())
    state["daily_start_equity"] = 0.0  # Will be set on next check_drawdown call
    _save_state(state)

    _audit_log(
        "DRAWDOWN_MANUAL_RESET",
        f"Admin reset. Was halted: {was_halted}. Reason: {admin_reason}",
        status="WARNING",
    )
    logger.warning("Drawdown guard RESET by admin: %s", admin_reason)

    return {
        "reset": True,
        "was_halted": was_halted,
        "message": "Drawdown guard reset. Trading can resume. Next trade will set new baseline.",
    }


def get_drawdown_status() -> dict:
    """Get current drawdown guard status for display."""
    state = _load_state()
    return {
        "date": state.get("date", ""),
        "daily_start_equity": state.get("daily_start_equity", 0.0),
        "halted": state.get("halted", False),
        "halt_reason": state.get("halt_reason", ""),
        "halt_time": state.get("halt_time", ""),
        "lowest_equity": state.get("lowest_equity", 0.0),
        "max_drawdown_pct": MAX_DAILY_DRAWDOWN_PCT,
    }
