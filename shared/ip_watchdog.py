# -*- coding: utf-8 -*-
"""IP Watchdog — detects dynamic-IP rotation + alerts on change.

Pre-boot utility for trading bots: compares current public IP against the last
known value stored in `agent.db::memory_facts` (topic='last_known_public_ip').
When they differ, fires a CRITICAL Telegram alert via shared.trading_notify,
so operator can update the KuCoin / Binance IP allowlist.

Design rules:
  - No new DB tables — reuses the existing `memory_facts` KV pattern.
  - No secrets in logs/alerts (only IP, which is public by definition).
  - Boot-safe: hard total budget ≤4s, never blocks startup longer.
  - Network-failure-tolerant: fetch failure is non-fatal (logs, returns).
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Final

import httpx

logger = logging.getLogger("godclaw.ip_watchdog")

_AGENT_DB: Final[Path] = Path("D:/RazAgent_Enterprise/data/databases/agent.db")
_FACT_TOPIC: Final[str] = "last_known_public_ip"
_FACT_SOURCE: Final[str] = "ip_watchdog"
_FETCH_TIMEOUT_SEC: Final[float] = 2.5
_ENDPOINTS: Final[tuple[str, ...]] = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)


async def _fetch_public_ip() -> str | None:
    """Query public-IP endpoints with a hard total budget of ~3 s.

    Returns the first plausible IPv4 reply or None. Per-endpoint failures are
    swallowed; this is advisory, not load-bearing.
    """
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SEC) as client:
        for url in _ENDPOINTS:
            try:
                resp = await client.get(url)
                ip = resp.text.strip()
                if ip and ip.count(".") == 3:
                    return ip
            except Exception as exc:
                logger.debug("ip_watchdog %s failed: %s", url, exc)
                continue
    return None


def _read_last_ip() -> str | None:
    try:
        conn = sqlite3.connect(str(_AGENT_DB), timeout=3.0)
        row = conn.execute(
            "SELECT content FROM memory_facts WHERE topic = ? "
            "ORDER BY updated_at DESC LIMIT 1",
            (_FACT_TOPIC,),
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except sqlite3.OperationalError as exc:
        logger.debug("ip_watchdog read_last_ip skipped: %s", exc)
        return None


def _store_current_ip(ip: str) -> None:
    try:
        conn = sqlite3.connect(str(_AGENT_DB), timeout=3.0)
        now = time.time()
        conn.execute(
            "INSERT INTO memory_facts "
            "(content, source, topic, confidence, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ip, _FACT_SOURCE, _FACT_TOPIC, 1.0, now, now),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("ip_watchdog store failed: %s", exc)


async def check_and_alert() -> dict:
    """Run a full IP check cycle. Returns a summary dict.

    Keys:
        current_ip: str | None  — what ipify returned (None on full failure)
        last_ip:    str | None  — prior IP from memory_facts (None on first boot)
        changed:    bool        — True if last_ip != current_ip (both non-None)
        alerted:    bool        — True if Telegram CRITICAL went out
        reason:     str         — first_seen | stable | changed | fetch_failed
    """
    current_ip = await _fetch_public_ip()
    if current_ip is None:
        logger.info("ip_watchdog: no public IP endpoint reachable — skipping check")
        return {
            "current_ip": None, "last_ip": _read_last_ip(),
            "changed": False, "alerted": False, "reason": "fetch_failed",
        }

    last_ip = _read_last_ip()
    changed = last_ip is not None and last_ip != current_ip
    first_seen = last_ip is None

    alerted = False
    if changed:
        try:
            from shared.trading_notify import send_trading_alert, CRITICAL
            msg = (
                "\u26a0\ufe0f <b>IP DINAMIC DETECTAT</b>\n\n"
                f"IP vechi: <code>{last_ip}</code>\n"
                f"IP nou: <code>{current_ip}</code>\n\n"
                "Actualizeaza Whitelist-ul in portalul KuCoin (si Binance) "
                "pentru a debloca Trading-ul live."
            )
            alerted = await send_trading_alert(
                msg, source="trading", category="exchange_health", severity=CRITICAL,
            )
        except Exception as exc:
            logger.warning("ip_watchdog alert dispatch failed: %s", exc)

    if first_seen or changed:
        _store_current_ip(current_ip)

    reason = "first_seen" if first_seen else ("changed" if changed else "stable")
    return {
        "current_ip": current_ip, "last_ip": last_ip,
        "changed": changed, "alerted": alerted, "reason": reason,
    }


async def is_ip_stable() -> bool:
    """Boot helper: True if IP is known-stable (or first boot). False on rotation.

    Hard timeout 4 s so a slow network never stalls trading-bot startup.
    On timeout or any exception → True (fail-open: don't block boot on advisory).
    """
    try:
        result = await asyncio.wait_for(check_and_alert(), timeout=4.0)
        return not result["changed"]
    except Exception as exc:
        logger.info("ip_watchdog is_ip_stable fail-open: %s", exc)
        return True


__all__ = ["check_and_alert", "is_ip_stable"]
