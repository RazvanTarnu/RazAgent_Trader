# -*- coding: utf-8 -*-
"""Push Notification Service — FCM/Expo for Background Approval Alerts.

Sends native push notifications to the CEO's mobile device when approval
decisions are required (CEO gate, trading gate, freelance proposals).

Supports two backends:
  1. Expo Push Notifications (default — works with React Native Expo apps)
  2. Firebase Cloud Messaging (FCM) for standalone React Native/Flutter

The CEO registers their device push token via POST /api/mobile/push/register.
All approval gates call send_approval_push() to fire the notification.

Keys (stored in keyring via shared/keyring_loader.py):
  - EXPO_PUSH_TOKEN: Device Expo push token (ExponentPushToken[xxx])
  - FCM_SERVER_KEY: Firebase Cloud Messaging server key (optional)

Usage:
    from shared.push_notifications import send_approval_push

    await send_approval_push(
        title="Trade Approval Required",
        body="BUY BTC $7.00 — Score 85%",
        data={"request_id": "abc123", "type": "trading"},
    )
"""

import json
import logging
import os
import sqlite3

from shared.db_base import get_connection
from datetime import datetime, timezone
from pathlib import Path

from shared.config import PROJECT_ROOT as BASE_DIR, DATA_DIR

logger = logging.getLogger("godclaw.push_notifications")
BILLING_DB = os.environ.get(
    "BILLING_DB_OVERRIDE",
    str((DATA_DIR / "billing.db").resolve()),
)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
FCM_PUSH_URL = "https://fcm.googleapis.com/fcm/send"


# ── Database (device tokens) ──────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    return get_connection("billing.db")


def _ensure_tables():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS push_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'ceo',
            platform TEXT NOT NULL DEFAULT 'expo',
            token TEXT UNIQUE NOT NULL,
            device_name TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_used TEXT
        );
        CREATE TABLE IF NOT EXISTS push_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            body TEXT,
            push_type TEXT DEFAULT 'approval',
            token TEXT,
            status TEXT DEFAULT 'sent',
            response TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_pt_user ON push_tokens(user_id, active);
    """)
    conn.commit()
    conn.close()


try:
    _ensure_tables()
except Exception:
    pass


# ── Token Registration ────────────────────────────────────────────────

def register_push_token(token: str, platform: str = "expo",
                         user_id: str = "ceo", device_name: str = "") -> dict:
    """Register a device push token for notifications."""
    _ensure_tables()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO push_tokens (user_id, platform, token, device_name) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(token) DO UPDATE SET active=1, last_used=CURRENT_TIMESTAMP",
            (user_id, platform, token, device_name),
        )
        conn.commit()
        logger.info("[PUSH] Token registered: %s (%s)", token[:20] + "...", platform)
        return {"status": "registered", "platform": platform}
    finally:
        conn.close()


def get_active_tokens(user_id: str = "ceo") -> list[dict]:
    """Get all active push tokens for a user."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT token, platform FROM push_tokens WHERE user_id = ? AND active = 1",
            (user_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Push Sending ──────────────────────────────────────────────────────

async def send_approval_push(title: str, body: str,
                              data: dict | None = None,
                              user_id: str = "ceo") -> dict:
    """Send push notification to all registered devices.

    Args:
        title: Notification title (shown in notification bar).
        body: Notification body text.
        data: Extra payload (request_id, type, etc.).
        user_id: Target user.

    Returns:
        dict with {sent, failed, tokens_tried}.
    """
    tokens = get_active_tokens(user_id)
    if not tokens:
        # Fallback: try keyring token
        token = _get_keyring_token()
        if token:
            tokens = [{"token": token, "platform": "expo"}]

    if not tokens:
        logger.debug("[PUSH] No push tokens registered — notification skipped")
        return {"sent": 0, "failed": 0, "tokens_tried": 0}

    sent = 0
    failed = 0

    for t in tokens:
        platform = t.get("platform", "expo")
        token = t["token"]

        try:
            if platform == "expo":
                ok = await _send_expo(token, title, body, data)
            elif platform == "fcm":
                ok = await _send_fcm(token, title, body, data)
            else:
                ok = await _send_expo(token, title, body, data)

            if ok:
                sent += 1
                _log_push(title, body, "approval", token, "sent")
            else:
                failed += 1
                _log_push(title, body, "approval", token, "failed")
        except Exception as e:
            failed += 1
            logger.warning("[PUSH] Send failed for %s: %s", platform, e)

    return {"sent": sent, "failed": failed, "tokens_tried": len(tokens)}


async def _send_expo(token: str, title: str, body: str,
                      data: dict | None = None) -> bool:
    """Send via Expo Push Notifications API."""
    import httpx

    payload = {
        "to": token,
        "title": title,
        "body": body[:256],
        "sound": "default",
        "priority": "high",
        "channelId": "approvals",
    }
    if data:
        payload["data"] = data

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            EXPO_PUSH_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            result = resp.json()
            status = result.get("data", {}).get("status", "")
            return status == "ok"
    return False


async def _send_fcm(token: str, title: str, body: str,
                     data: dict | None = None) -> bool:
    """Send via Firebase Cloud Messaging."""
    import httpx

    server_key = _get_fcm_key()
    if not server_key:
        return False

    payload = {
        "to": token,
        "notification": {
            "title": title,
            "body": body[:256],
            "sound": "default",
        },
        "priority": "high",
    }
    if data:
        payload["data"] = {k: str(v) for k, v in data.items()}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            FCM_PUSH_URL,
            json=payload,
            headers={
                "Authorization": f"key={server_key}",
                "Content-Type": "application/json",
            },
        )
        return resp.status_code == 200


def _log_push(title: str, body: str, push_type: str, token: str, status: str):
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO push_log (title, body, push_type, token, status) VALUES (?, ?, ?, ?, ?)",
            (title[:200], body[:500], push_type, token[:50], status),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _get_keyring_token() -> str:
    try:
        from shared.keyring_loader import get_credential
        return get_credential("EXPO_PUSH_TOKEN") or ""
    except Exception:
        return os.environ.get("EXPO_PUSH_TOKEN", "")


def _get_fcm_key() -> str:
    try:
        from shared.keyring_loader import get_credential
        return get_credential("FCM_SERVER_KEY") or ""
    except Exception:
        return os.environ.get("FCM_SERVER_KEY", "")
