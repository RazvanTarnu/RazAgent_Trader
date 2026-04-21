# -*- coding: utf-8 -*-
"""B2B Webhook Dispatch — Tenant callback registration and async delivery.

Allows Business/Enterprise tenants to register callback URLs for real-time
notifications when events occur (video render complete, trade executed, etc.).

All dispatches are fire-and-forget with retry (3 attempts, exponential backoff).
Failed deliveries are logged to webhooks.db for debugging.

Storage: SQLite (data/webhooks.db) with WAL mode.
"""

import hashlib
import hmac
import json
import logging
import os
import sqlite3

from shared.db_base import get_connection
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("godclaw.webhooks")

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
WEBHOOKS_DB = os.environ.get(
    "WEBHOOKS_DB_OVERRIDE",
    os.path.join(os.path.abspath(_DATA_DIR), "webhooks.db"),
)

# Webhook signing secret (tenants verify payload authenticity)
# V1.8.1 Security: env -> keyring -> auto-generate + persist to keyring
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SIGNING_SECRET", "")
if not WEBHOOK_SECRET:
    try:
        from shared.keyring_loader import get_credential
        WEBHOOK_SECRET = get_credential("WEBHOOK_SIGNING_SECRET") or ""
    except Exception:
        pass
if not WEBHOOK_SECRET:
    import secrets as _sec
    WEBHOOK_SECRET = _sec.token_hex(32)
    # Auto-persist to keyring so it survives restarts
    try:
        import keyring as _kr
        _kr.set_password("AgentCeoR", "WEBHOOK_SIGNING_SECRET", WEBHOOK_SECRET)
        logger.warning("WEBHOOK_SIGNING_SECRET auto-generated and saved to keyring.")
    except Exception:
        logger.error("WEBHOOK_SIGNING_SECRET not configured and keyring write failed. "
                     "Signatures will break on restart.")

# Supported event types
EVENT_TYPES = {
    "video.render.completed",
    "video.render.failed",
    "trade.executed",
    "trade.stopped",
    "pipeline.batch.completed",
    "subscription.activated",
    "subscription.cancelled",
}

# Retry config
MAX_RETRIES = 3
RETRY_DELAYS = [2, 10, 60]  # seconds (exponential backoff)


def _get_conn() -> sqlite3.Connection:
    return get_connection("webhooks.db")


def _ensure_tables():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS webhook_registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            callback_url TEXT NOT NULL,
            event_types TEXT NOT NULL DEFAULT '*',
            secret TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tenant_id, callback_url)
        );

        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registration_id INTEGER,
            tenant_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            attempts INTEGER DEFAULT 0,
            last_status_code INTEGER,
            last_error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            delivered_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_reg_tenant ON webhook_registrations(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_del_status ON webhook_deliveries(status);
    """)
    conn.commit()
    conn.close()


try:
    _ensure_tables()
except Exception as e:
    logger.debug("Webhooks DB init failed (non-fatal): %s", e)


# ── Registration API ────────────────────────────────────────────────────

def register_webhook(tenant_id: str, callback_url: str,
                      event_types: list[str] | None = None,
                      secret: str = "") -> dict:
    """Register a webhook callback URL for a tenant.

    Args:
        tenant_id: Tenant identifier (from JWT).
        callback_url: HTTPS URL to receive POST callbacks.
        event_types: List of event types to subscribe to (default: all).
        secret: Optional per-webhook signing secret (overrides global).

    Returns:
        dict with registration details.
    """
    if not callback_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        return {"error": "callback_url must use HTTPS (or localhost for testing)"}

    events_str = ",".join(event_types) if event_types else "*"

    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO webhook_registrations (tenant_id, callback_url, event_types, secret)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(tenant_id, callback_url) DO UPDATE SET
                 event_types = excluded.event_types,
                 secret = excluded.secret,
                 is_active = 1,
                 updated_at = CURRENT_TIMESTAMP""",
            (tenant_id, callback_url, events_str, secret),
        )
        conn.commit()
        reg_id = conn.execute(
            "SELECT id FROM webhook_registrations WHERE tenant_id = ? AND callback_url = ?",
            (tenant_id, callback_url),
        ).fetchone()["id"]
        logger.info("Webhook registered: tenant=%s url=%s events=%s", tenant_id, callback_url[:50], events_str)
        return {
            "id": reg_id,
            "tenant_id": tenant_id,
            "callback_url": callback_url,
            "event_types": events_str,
            "status": "active",
        }
    finally:
        conn.close()


def list_webhooks(tenant_id: str) -> list[dict]:
    """List all active webhooks for a tenant."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, callback_url, event_types, is_active, created_at "
            "FROM webhook_registrations WHERE tenant_id = ? AND is_active = 1",
            (tenant_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_webhook(tenant_id: str, webhook_id: int) -> bool:
    """Deactivate a webhook registration."""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE webhook_registrations SET is_active = 0, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND tenant_id = ?",
            (webhook_id, tenant_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ── Dispatch Engine ─────────────────────────────────────────────────────

def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload verification."""
    return hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()


def _get_matching_registrations(event_type: str, tenant_id: str | None = None) -> list[dict]:
    """Find all active webhook registrations matching an event type."""
    conn = _get_conn()
    try:
        if tenant_id:
            rows = conn.execute(
                "SELECT * FROM webhook_registrations WHERE is_active = 1 AND tenant_id = ?",
                (tenant_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM webhook_registrations WHERE is_active = 1"
            ).fetchall()

        matches = []
        for row in rows:
            events = row["event_types"]
            if events == "*" or event_type in events.split(","):
                matches.append(dict(row))
        return matches
    finally:
        conn.close()


def dispatch_event(event_type: str, payload: dict,
                    tenant_id: str | None = None):
    """Fire webhook callbacks for an event (non-blocking, background thread).

    Args:
        event_type: One of EVENT_TYPES (e.g., "video.render.completed").
        payload: Event data dict (serialized to JSON in the POST body).
        tenant_id: If set, only notify this tenant's webhooks.
    """
    registrations = _get_matching_registrations(event_type, tenant_id)
    if not registrations:
        return

    # Enrich payload with event metadata
    envelope = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }
    payload_json = json.dumps(envelope, default=str)

    for reg in registrations:
        # Fire in background thread (never blocks caller)
        t = threading.Thread(
            target=_deliver_with_retry,
            args=(reg, payload_json),
            daemon=True,
            name=f"webhook-{reg['id']}",
        )
        t.start()

    logger.info("Webhook dispatch: event=%s, targets=%d", event_type, len(registrations))


def _deliver_with_retry(registration: dict, payload_json: str):
    """Attempt webhook delivery with exponential backoff retries."""
    import urllib.request
    import urllib.error

    url = registration["callback_url"]
    secret = registration.get("secret") or WEBHOOK_SECRET
    reg_id = registration["id"]
    tenant_id = registration["tenant_id"]

    payload_bytes = payload_json.encode("utf-8")
    signature = _sign_payload(payload_bytes, secret)

    conn = _get_conn()
    delivery_id = None
    try:
        conn.execute(
            "INSERT INTO webhook_deliveries (registration_id, tenant_id, event_type, payload, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (reg_id, tenant_id, json.loads(payload_json).get("event", ""), payload_json[:2000]),
        )
        conn.commit()
        delivery_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except Exception:
        pass
    finally:
        conn.close()

    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(
                url,
                data=payload_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-RazAgent-Signature": f"sha256={signature}",
                    "X-RazAgent-Event": json.loads(payload_json).get("event", ""),
                    "User-Agent": "RazAgent-Webhook/1.0",
                },
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=10)
            status = resp.status

            # Success (2xx)
            if 200 <= status < 300:
                logger.info("Webhook delivered: reg=%d url=%s status=%d", reg_id, url[:50], status)
                _update_delivery(delivery_id, "delivered", attempt + 1, status)
                return

            logger.warning("Webhook non-2xx: reg=%d status=%d", reg_id, status)
            _update_delivery(delivery_id, "retrying", attempt + 1, status)

        except urllib.error.HTTPError as e:
            logger.warning("Webhook HTTP error: reg=%d status=%d", reg_id, e.code)
            _update_delivery(delivery_id, "retrying", attempt + 1, e.code, str(e)[:200])
        except Exception as e:
            logger.warning("Webhook delivery failed: reg=%d error=%s", reg_id, e)
            _update_delivery(delivery_id, "retrying", attempt + 1, error=str(e)[:200])

        # Backoff before retry
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAYS[attempt])

    # All retries exhausted
    _update_delivery(delivery_id, "failed", MAX_RETRIES)
    logger.error("Webhook failed after %d retries: reg=%d url=%s", MAX_RETRIES, reg_id, url[:50])


def _update_delivery(delivery_id: int | None, status: str, attempts: int,
                      status_code: int = 0, error: str = ""):
    """Update delivery record in DB."""
    if not delivery_id:
        return
    try:
        conn = _get_conn()
        now = datetime.now(timezone.utc).isoformat() if status == "delivered" else None
        conn.execute(
            "UPDATE webhook_deliveries SET status = ?, attempts = ?, "
            "last_status_code = ?, last_error = ?, delivered_at = ? WHERE id = ?",
            (status, attempts, status_code, error, now, delivery_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_delivery_history(tenant_id: str, limit: int = 50) -> list[dict]:
    """Get recent webhook delivery history for a tenant."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, event_type, status, attempts, last_status_code, last_error, "
            "created_at, delivered_at FROM webhook_deliveries "
            "WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
            (tenant_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
