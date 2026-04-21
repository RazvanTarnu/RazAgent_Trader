# -*- coding: utf-8 -*-
"""Trading-channel notification gateway — V1.1 (2026-04-17).

Single choke-point for all trading/crypto alerts. Enforces two rules:

  (a) Channel isolation — trading alerts go EXCLUSIVELY to TradeCrypto Bot
      (:8012, @TradeCrypto13_bot). Never to CEO Bot or anywhere else.
  (b) Severity gate — only CRITICAL / HIGH reach Telegram. INFO / DEBUG are
      written to audit_logs.db only, never bothering the chat. This kills
      the KuCoin-reconnect / routine-diagnostic spam that was filling the
      trade channel at every transient network hiccup.

Callers MUST use this helper instead of calling Telegram directly. A RuntimeError
is raised at call time if someone attempts to route a trading alert via the CEO
token — this is the defense-in-depth layer against cross-channel leaks.

Persists every dispatch (sent, suppressed-by-severity, blocked) to
audit_logs.db.audit_actions (existing table — no new DB).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Final

import httpx

from shared.keyring_loader import get_credential

logger = logging.getLogger("godclaw.trading_notify")

# ── Severity ladder ────────────────────────────────────────────────────
# Only CRITICAL and HIGH are allowed to hit the Telegram API. Everything
# else lands in audit_logs.db and the local logger, nothing else.
CRITICAL: Final[str] = "CRITICAL"   # P0: live-trade error, zero-withdrawal breach, fund at risk
HIGH:     Final[str] = "HIGH"       # P1: trade executed, exit triggered, balance change
INFO:     Final[str] = "INFO"       # P2: routine reconciliation, heartbeat
DEBUG:    Final[str] = "DEBUG"      # P3: reconnect attempts, rate-limit backoff, per-tick diagnostics

_VALID_SEVERITIES: Final[tuple[str, ...]] = (CRITICAL, HIGH, INFO, DEBUG)
_TELEGRAM_SEVERITIES: Final[frozenset[str]] = frozenset({CRITICAL, HIGH})

_AUDIT_DB: Final[Path] = Path("D:/RazAgent_Enterprise/data/audit_logs.db")
_AGENT_ID: Final[str] = "trading_notify"
_TELEGRAM_API: Final[str] = "https://api.telegram.org"

# Tokens that are NEVER allowed to carry trading traffic. If a caller somehow
# injects one here we refuse — trading stays on its own channel, period.
_FORBIDDEN_TOKEN_KEYS: Final[tuple[str, ...]] = (
    "TELEGRAM_TOKEN",
    "AGENT_VIDEO_TOKEN",
)

_VALID_SOURCES: Final[set[str]] = {"trading", "trade_exit", "trade_audit", "trade_exec"}
_VALID_CATEGORIES: Final[set[str]] = {
    "crypto", "exchange_health", "position",
    "revenue", "trade_approval",  # V1.2 REMAIN_SILENT: business-critical allowlist
}

# V1.2 REMAIN_SILENT — categories that are allowed to reach Telegram at HIGH severity.
# Everything else at HIGH is forced to audit-only. CRITICAL always passes regardless.
_TELEGRAM_BUSINESS_CATEGORIES: Final[frozenset[str]] = frozenset({
    "revenue", "trade_approval", "position",
})

# V1.2 REMAIN_SILENT — content-based denylist. Messages carrying any of these
# substrings are FORCED to audit-only regardless of severity or category.
# Root cause: Trading Intelligence 3h cycle report ignored the severity gate
# because it was routed through an externally-injected `_send_telegram` rather
# than through `send_trading_alert`. The source-side fix lives in
# trading_intelligence/orchestrator.py::_notify; this list is belt-and-braces
# for any future caller that re-discovers this module.
_TELEGRAM_CONTENT_DENYLIST: Final[tuple[str, ...]] = (
    "Trading Intelligence V1.0",
    "RAPORT AUTONOM",      # V2.3: nerveclaw 3h autonomous status report
    "GODCLAW WATCHDOG",    # V2.4: fleet watchdog alerts — surfaced in dashboard instead
    "OAuth Sentinel",      # V2.5 night-watchman: sentinel warnings -> dashboard, never Telegram
    "Mission reminder",    # V2.5: reminder banner text -> reminders.json + NEXUS /api/missions
)


def _audit(action: str, category: str, source: str, details: dict | None = None) -> None:
    """Append a row to audit_logs.db.audit_actions. Best-effort; never raises.

    Uses the pre-existing `audit_actions` schema (column: timestamp_unix:REAL).
    We intentionally do NOT CREATE TABLE here — the DB is provisioned at install
    time and the existing rich schema (status, session_id, duration_ms, etc.)
    must not be shadowed by a simpler one via IF NOT EXISTS no-op.
    """
    try:
        conn = sqlite3.connect(str(_AUDIT_DB), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO audit_actions "
            "(agent_id, action_type, target, details, status, timestamp_unix) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _AGENT_ID,
                action,
                f"{source}/{category}",
                json.dumps(details or {}),
                "ok",
                time.time(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # pragma: no cover - audit is advisory
        logger.debug("trading_notify audit write failed: %s", exc)


async def send_trading_alert(
    text: str,
    *,
    source: str = "trading",
    category: str = "crypto",
    severity: str = HIGH,
    parse_mode: str = "HTML",
    disable_web_page_preview: bool = True,
) -> bool:
    """Send `text` to TradeCrypto Bot only. Returns True on 200 response.

    Severity gate:
        CRITICAL / HIGH → hits Telegram AND audit_logs.db
        INFO / DEBUG    → audit_logs.db only (silent on Telegram)

    Default `severity=HIGH` preserves the V1.0 contract — existing callers
    that don't pass severity still reach the chat.

    Raises:
        RuntimeError: if `source`/`category`/`severity` is invalid, or if the
                      TRADE_CRYPTO token is the same value as a CEO-channel
                      token (collision). Either case indicates a routing bug.
    """
    if source not in _VALID_SOURCES:
        raise RuntimeError(
            f"trading_notify: refused source={source!r} (expected one of {sorted(_VALID_SOURCES)})"
        )
    if category not in _VALID_CATEGORIES:
        raise RuntimeError(
            f"trading_notify: refused category={category!r} (expected one of {sorted(_VALID_CATEGORIES)})"
        )
    if severity not in _VALID_SEVERITIES:
        raise RuntimeError(
            f"trading_notify: refused severity={severity!r} "
            f"(expected one of {list(_VALID_SEVERITIES)})"
        )

    # V1.2 REMAIN_SILENT content denylist: force audit-only for noisy reports
    # regardless of severity/category. Checked before severity gate so CRITICAL
    # cannot smuggle a banned template.
    for banned in _TELEGRAM_CONTENT_DENYLIST:
        if banned in text:
            _audit(
                "send_suppressed_content_denylist", category, source,
                {"banned": banned, "severity": severity,
                 "text_len": len(text), "preview": text[:160]},
            )
            logger.info("trading_notify DENYLIST [%s] blocked %s", banned, source)
            return False

    # V1.2 REMAIN_SILENT gate. Telegram allowed iff:
    #   (a) severity == CRITICAL (always passes — P0, fund at risk), OR
    #   (b) severity == HIGH AND category is business-critical (revenue, position, trade_approval).
    # Everything else (HIGH + exchange_health/crypto, INFO, DEBUG) → audit only.
    severity_passes = severity == CRITICAL or (
        severity == HIGH and category in _TELEGRAM_BUSINESS_CATEGORIES
    )
    if not severity_passes:
        _audit(
            "send_suppressed_remain_silent", category, source,
            {"severity": severity, "category": category, "text_len": len(text),
             "preview": text[:160]},
        )
        # Emit to local logger so operators can grep the stream; audit row is authoritative.
        log_fn = logger.info if severity in (HIGH, INFO) else logger.debug
        log_fn("trading_notify REMAIN_SILENT [%s/%s] %s: %s",
               severity, category, source, text[:240])
        return False

    token = get_credential("TRADE_CRYPTO_BOT_TOKEN") or ""
    chat_id = get_credential("TRADE_CRYPTO_CHAT_ID") or ""
    if not token or not chat_id:
        # Do NOT fall back to TELEGRAM_TOKEN — that would cross the channel.
        _audit(
            "send_blocked_no_creds", category, source,
            {"severity": severity, "reason": "TRADE_CRYPTO_* missing"},
        )
        logger.error(
            "trading_notify: missing TRADE_CRYPTO_BOT_TOKEN/CHAT_ID — alert DROPPED "
            "(no CEO-channel fallback by policy)"
        )
        return False

    # Defence-in-depth: if something upstream accidentally put a forbidden key's
    # value into TRADE_CRYPTO_BOT_TOKEN (via env-var collision for example), bail.
    for forbidden_key in _FORBIDDEN_TOKEN_KEYS:
        forbidden_val = get_credential(forbidden_key) or ""
        if forbidden_val and forbidden_val == token:
            _audit(
                "send_blocked_token_collision", category, source,
                {"severity": severity, "forbidden": forbidden_key},
            )
            raise RuntimeError(
                f"trading_notify: TRADE_CRYPTO_BOT_TOKEN matches {forbidden_key} — "
                "channel collision detected, refusing to send"
            )

    payload = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_TELEGRAM_API}/bot{token}/sendMessage", json=payload,
            )
        ok = resp.status_code == 200
        _audit(
            "send_ok" if ok else "send_failed",
            category, source,
            {"severity": severity, "status": resp.status_code, "text_len": len(text)},
        )
        if not ok:
            logger.warning("trading_notify non-200: %s %s", resp.status_code, resp.text[:200])
        return ok
    except Exception as exc:
        _audit(
            "send_exception", category, source,
            {"severity": severity, "err": str(exc)[:200]},
        )
        logger.warning("trading_notify send failed: %s", exc)
        return False


# ── Idempotency helpers (reuses audit_logs.db — no new DB) ──

def was_audit_action_sent_within(action_type: str, within_sec: float) -> bool:
    """Return True if `audit_actions.action_type=<x>` exists with a timestamp
    newer than (now - within_sec).

    Queries the real `timestamp_unix` column (REAL, provisioned elsewhere).
    Used by the daily crypto brief to avoid re-sending after a restart.
    Lives here (rather than in news_broadcaster) so other periodic senders can
    reuse the same pattern without duplicating the SQLite boilerplate.
    """
    try:
        conn = sqlite3.connect(str(_AUDIT_DB), timeout=5.0)
        row = conn.execute(
            "SELECT 1 FROM audit_actions "
            "WHERE action_type = ? AND timestamp_unix > ? LIMIT 1",
            (action_type, time.time() - within_sec),
        ).fetchone()
        conn.close()
        return row is not None
    except sqlite3.OperationalError:
        # Table may not exist on a fresh install — treat as "not sent".
        return False
    except Exception as exc:  # pragma: no cover
        logger.debug("was_audit_action_sent_within failed: %s", exc)
        return False


def record_audit_action(action_type: str, details: dict | None = None) -> None:
    """Write a generic audit row (caller names the action_type).

    Thin wrapper over `_audit` that lets non-send callers (e.g. news broadcaster)
    register "I did this at T" markers without pretending it was a message send.
    """
    _audit(action_type, "n/a", "n/a", details)


# ── Pure predicate — usable by CEO Bot filter without side effects ──

_TRADING_COMMAND_PREFIXES: Final[tuple[str, ...]] = (
    "/trade", "/buy", "/sell", "/position", "/positions",
    "/pnl", "/orders", "/open_order", "/cancel_order",
    "/portfolio", "/balance", "/crypto", "/arbitrage",
    "/trading_activate", "/trading_deactivate",
)

_TRADING_KEYWORDS: Final[tuple[str, ...]] = (
    "place trade", "execute trade", "buy btc", "sell btc",
    "open position", "close position", "kucoin", "binance order",
)


def is_trading_command(text: str | None) -> bool:
    """True if `text` looks like a trading command/query that belongs on :8012.

    Used by CEO Bot filter to DROP (silently ignore) trading traffic reaching
    the wrong channel. Pure function — no I/O, safe to call on every update.
    """
    if not text:
        return False
    stripped = text.strip().lower()
    if any(stripped.startswith(p) for p in _TRADING_COMMAND_PREFIXES):
        return True
    return any(kw in stripped for kw in _TRADING_KEYWORDS)


__all__ = [
    "send_trading_alert",
    "is_trading_command",
    "was_audit_action_sent_within",
    "record_audit_action",
    "CRITICAL", "HIGH", "INFO", "DEBUG",
]
