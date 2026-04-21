# -*- coding: utf-8 -*-
"""Dry-Run LIVE Simulation mode.

Purpose: exercise the LIVE decision path (swarm consensus → approval gate
decision point) WITHOUT actually calling the gate or placing orders, so
we can observe interference (IP Watchdog mid-loop, gate latency, spurious
rejections) before flipping PAPER_MODE=False.

Activation: ``set DRY_RUN_LIVE=1`` in the environment of any trading
service (trade_crypto_bot.py, prediction_engine cycles, trading_arena).
PAPER_MODE stays True — this is strictly additive observation.

Usage:
    from shared.dry_run_live import is_active, log_simulated_gate_call
    if is_active():
        log_simulated_gate_call(pair, side, size_usd, reason)
        continue  # skip the real gate call
    await gate.require_approval(...)
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("godclaw.dry_run_live")

_PREFIX = "[DRY-RUN-LIVE]"
# Rate-limit Telegram echoes to avoid flooding even in simulation.
_LAST_TG_ECHO_AT = 0.0
_TG_ECHO_MIN_GAP_SEC = 300  # max 1 Telegram message per 5 min


def is_active() -> bool:
    """True when DRY_RUN_LIVE env var is set to a truthy value."""
    v = os.environ.get("DRY_RUN_LIVE", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _log(msg: str) -> None:
    """Log with consistent prefix at INFO so it shows up in normal backend logs."""
    logger.info("%s %s", _PREFIX, msg)


async def _echo_to_telegram(text: str) -> None:
    """Best-effort Telegram echo, rate-limited, via TradeCrypto bot channel."""
    global _LAST_TG_ECHO_AT
    now = time.monotonic()
    if now - _LAST_TG_ECHO_AT < _TG_ECHO_MIN_GAP_SEC:
        return
    _LAST_TG_ECHO_AT = now
    try:
        from shared.trading_notify import send_trading_alert, MEDIUM
        await send_trading_alert(
            f"{_PREFIX} {text}",
            source="trading",
            category="dry_run_live",
            severity=MEDIUM,
        )
    except Exception as exc:
        logger.debug("dry_run_live telegram echo skipped: %s", exc)


def log_simulated_gate_call(pair: str, side: str, size_usd: float, reason: str = "") -> None:
    """Log what would have been sent to TradingApprovalGate.

    No Telegram call, no gate call. Safe side-effect-free observation.
    """
    msg = (
        f"Semnal {reason or 'consensus-pass'} detectat. "
        f"Aș fi cerut aprobare pentru ${size_usd:.2f} {pair} ({side})."
    )
    _log(msg)


async def log_simulated_gate_call_with_echo(
    pair: str, side: str, size_usd: float, reason: str = "",
) -> None:
    """Same as log_simulated_gate_call but also echoes to Telegram (rate-limited)."""
    log_simulated_gate_call(pair, side, size_usd, reason)
    await _echo_to_telegram(
        f"Semnal {reason or 'consensus-pass'}: ar cere aprobare pentru "
        f"${size_usd:.2f} {pair} ({side}). PAPER_MODE rămâne True."
    )


def log_observation(label: str, detail: str) -> None:
    """Generic observation hook — use for IP watchdog ticks, gate import success, etc."""
    _log(f"{label}: {detail}")
