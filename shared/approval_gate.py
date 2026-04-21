# -*- coding: utf-8 -*-
"""V28.0 — Strict Telegram Approval Gate with 200-minute timeout.

Async approval gate that sends Telegram inline keyboard and waits for human
response. Completely non-blocking (asyncio-based, NO time.sleep loops).

HARD RULE: TIMEOUT = BLOCK. No CEO fallback, no override.

Usage:
    from shared.approval_gate import ApprovalGate

    gate = ApprovalGate.instance()
    result = await gate.require_approval(
        action_description="Deploy new model to production",
        agent_id="ceo",
        decision_id="dec_abc123",
        severity="HIGH",
    )
    # result["status"] in ("APPROVED", "REJECTED", "TIMEOUT_BLOCKED")
"""

import os
import sys
import uuid
import time
import asyncio
import logging
from typing import Optional

from shared.approval_base import ApprovalGateBase


try:
    from shared.version import APP_VERSION
except ImportError:
    APP_VERSION = "V28.0"

logger = logging.getLogger("godclaw.approval_gate")  # TODO-TECHDEBT: rename logger prefix to "godclaw"

# ---------------------------------------------------------------------------
# Config from environment (with sane defaults)
# REFACTOR: approval_gate.py and trading_approval_gate.py share ~80% logic
# — extract shared base class ApprovalGateBase with _build_message(), _handle_timeout()
# ---------------------------------------------------------------------------
APPROVAL_TIMEOUT: int = int(os.environ.get("APPROVAL_TIMEOUT", "12000"))   # 200 min
RETRY_INTERVAL: int = int(os.environ.get("RETRY_INTERVAL", "300"))         # 5 min


# ---------------------------------------------------------------------------
# Ghost-log helper (audit trail)
# ---------------------------------------------------------------------------
def _ghost_log(
    action: str,
    target: str = "",
    agent_id: str = "SYSTEM",
    details: Optional[dict] = None,
) -> None:
    """Non-blocking best-effort audit log entry."""
    try:
        from shared.audit_log import AuditLog
        audit = AuditLog.instance()
        audit.log_action(
            action_type=action,
            target=target,
            agent_id=agent_id,
            details=details or {},
        )
    except Exception:
        logger.debug("_ghost_log failed (non-critical), action=%s", action)


# ---------------------------------------------------------------------------
# Telegram helpers (standalone, no PTB application needed)
# ---------------------------------------------------------------------------
def _get_telegram_token() -> str:
    """Resolve TELEGRAM_TOKEN from env or keyring."""
    from shared.keyring_loader import get_credential
    token = os.environ.get("TELEGRAM_TOKEN") or get_credential("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN not found in env or keyring")
    return token


def _get_admin_chat_id() -> str:
    """Resolve ADMIN_CHAT_ID from env or keyring."""
    from shared.keyring_loader import get_credential
    chat_id = os.environ.get("ADMIN_CHAT_ID") or get_credential("ADMIN_CHAT_ID")
    if not chat_id:
        raise RuntimeError("ADMIN_CHAT_ID not found in env or keyring")
    return chat_id


# ---------------------------------------------------------------------------
# ApprovalGate — singleton async gate
# ---------------------------------------------------------------------------
class ApprovalGate(ApprovalGateBase["ApprovalGate"]):
    """Singleton async approval gate with 200-min timeout and 5-min reminders.

    Lifecycle:
      1. ``require_approval()`` sends an inline-keyboard message to Telegram.
      2. A background ``_reminder_loop`` pings every 5 min until answered.
      3. The Telegram ``CallbackQueryHandler`` calls ``resolve()`` which unblocks
         the waiting coroutine.
      4. If nobody answers within 200 min the request is **BLOCKED** (not approved).
    """

    _instance: Optional["ApprovalGate"] = None
    _pending: dict[str, asyncio.Event]       # request_id -> event
    _results: dict[str, str]                 # request_id -> "APPROVED" | "REJECTED"
    _retry_tasks: dict[str, asyncio.Task]    # request_id -> reminder task

    def __init__(self) -> None:
        super().__init__()

    # ---- singleton --------------------------------------------------------
    @classmethod
    def instance(cls) -> "ApprovalGate":
        """Return (or create) the process-wide singleton."""
        if cls._instance is None:
            cls._instance = cls()
            logger.info("ApprovalGate singleton created (%s)", APP_VERSION)
        return cls._instance

    # ---- public API -------------------------------------------------------
    async def require_approval(
        self,
        action_description: str,
        metadata: dict | None = None,
        agent_id: str = "SYSTEM",
        decision_id: str = "",
        severity: str = "HIGH",
        old_code: str = "",
        new_code: str = "",
    ) -> dict:
        """Request human approval via Telegram and block until answered or timed out.

        Returns dict with keys: status, request_id, waited_seconds.
        Status is one of: APPROVED, REJECTED, TIMEOUT_BLOCKED.

        V10.60: If old_code and new_code are provided, a visual Git-style diff
        is appended to the Telegram approval message via code_change_approval().
        """
        request_id = uuid.uuid4().hex[:12]
        event = asyncio.Event()
        self._pending[request_id] = event

        logger.info(
            "[%s] Approval requested — id=%s severity=%s agent=%s desc=%s",
            APP_VERSION, request_id, severity, agent_id, action_description[:120],
        )
        _ghost_log(
            "approval_requested",
            target=request_id,
            agent_id=agent_id,
            details={
                "description": action_description,
                "severity": severity,
                "decision_id": decision_id,
                "timeout_seconds": APPROVAL_TIMEOUT,
            },
        )

        # -- Send initial Telegram message with inline keyboard --------------
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "\u2705 APPROVE",
                            callback_data=f"v28_approve_{request_id}",
                        ),
                        InlineKeyboardButton(
                            "\u274c REJECT",
                            callback_data=f"v28_reject_{request_id}",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "\U0001f50d Detalii",
                            callback_data=f"v28_details_{request_id}",
                        ),
                    ],
                ]
            )

            # Build rich context snapshot (3s timeout, graceful per-source fallback)
            try:
                from shared.approval_snapshot import build_snapshot

                text = await build_snapshot(
                    action_description=action_description,
                    request_id=request_id,
                    severity=severity,
                    metadata=metadata,
                    timeout=3.0,
                )
            except Exception as snap_exc:
                logger.warning("Snapshot build failed, using fallback text: %s", snap_exc)
                text = (
                    f"\U0001f6a8 <b>APPROVAL REQUIRED</b> [{severity}]\n\n"
                    f"<b>Action:</b> {action_description}\n"
                    f"<b>Agent:</b> {agent_id}\n"
                    f"<b>Request ID:</b> <code>{request_id}</code>\n"
                    f"<b>Timeout:</b> {APPROVAL_TIMEOUT // 60} minutes\n\n"
                    f"\u26a0\ufe0f <i>No response = AUTO-BLOCK</i>"
                )

            # V10.60: Append visual code diff if provided
            if old_code and new_code:
                try:
                    from shared.telegram_templates import code_change_approval

                    diff_text = code_change_approval(
                        file_path=action_description,
                        agent_id=agent_id,
                        change_description=action_description,
                        old_code=old_code,
                        new_code=new_code,
                        risk_level=severity.lower(),
                    )
                    # Replace text with the richer diff version
                    text = diff_text
                except Exception as diff_exc:
                    logger.warning("Diff generation failed, using standard text: %s", diff_exc)

            await self._send_telegram_message(text, reply_markup=keyboard)
        except Exception as exc:
            logger.error("Failed to send approval request to Telegram: %s", exc)
            _ghost_log(
                "approval_send_failed",
                target=request_id,
                agent_id=agent_id,
                details={"error": str(exc)},
            )

        # -- Send push notification for background mobile alerts ---------------
        try:
            from shared.push_notifications import send_approval_push
            self._fire_and_forget(send_approval_push(
                title=f"Approval Required [{severity}]",
                body=action_description[:200],
                data={"request_id": request_id, "type": "ceo_approval",
                      "severity": severity, "agent_id": agent_id},
            ))
        except Exception as push_exc:
            logger.debug("Push notification skipped: %s", push_exc)

        # -- Start reminder loop ----------------------------------------------
        self._start_reminder(request_id, self._reminder_loop(request_id, action_description))

        # -- Wait for resolution or timeout -----------------------------------
        start_time = time.monotonic()
        retries_count = 0
        result_status = "TIMEOUT_BLOCKED"

        try:
            await asyncio.wait_for(event.wait(), timeout=APPROVAL_TIMEOUT)
            result_status = self._results.get(request_id, "TIMEOUT_BLOCKED")
        except asyncio.TimeoutError:
            result_status = "TIMEOUT_BLOCKED"
            logger.warning(
                "[%s] Approval TIMEOUT_BLOCKED — id=%s after %ds",
                APP_VERSION, request_id, APPROVAL_TIMEOUT,
            )
            _ghost_log(
                "approval_timeout_blocked",
                target=request_id,
                agent_id=agent_id,
                details={"waited_seconds": APPROVAL_TIMEOUT},
            )
        finally:
            waited_seconds = round(time.monotonic() - start_time, 2)

            # Count how many reminders were sent
            retries_count = min(
                int(waited_seconds // RETRY_INTERVAL),
                APPROVAL_TIMEOUT // RETRY_INTERVAL,
            )

            # Cleanup
            self._cleanup_request(request_id)

            logger.info(
                "[%s] Approval resolved — id=%s status=%s waited=%.1fs retries=%d",
                APP_VERSION, request_id, result_status, waited_seconds, retries_count,
            )
            _ghost_log(
                "approval_resolved",
                target=request_id,
                agent_id=agent_id,
                details={
                    "status": result_status,
                    "waited_seconds": waited_seconds,
                    "retries": retries_count,
                },
            )

            # Replay engine integration (best-effort)
            if decision_id:
                try:
                    from shared.replay_engine import update_approval
                    update_approval(decision_id, result_status, waited_seconds, retries_count)
                except Exception:
                    pass

        return {
            "status": result_status,
            "request_id": request_id,
            "waited_seconds": waited_seconds,
        }

    # ---- resolve (called from Telegram callback handler) -------------------
    def resolve(self, request_id: str, decision: str) -> None:
        """Unblock a pending approval. Overrides base to add audit log."""
        super().resolve(request_id, decision)
        _ghost_log(
            "approval_button_pressed",
            target=request_id,
            details={"decision": decision},
        )

    # ---- background reminder loop ------------------------------------------
    async def _reminder_loop(self, request_id: str, action_description: str) -> None:
        """Send periodic reminders until the request is resolved or times out.

        Runs as a background ``asyncio.Task``.  Uses ``asyncio.sleep`` (never
        ``time.sleep``).
        """
        max_retries = APPROVAL_TIMEOUT // RETRY_INTERVAL  # 40 for 200min/5min
        reminder_num = 0

        try:
            while reminder_num < max_retries:
                await asyncio.sleep(RETRY_INTERVAL)

                # Check if already resolved
                if request_id not in self._pending:
                    return

                reminder_num += 1
                remaining_seconds = APPROVAL_TIMEOUT - (reminder_num * RETRY_INTERVAL)
                remaining_minutes = max(remaining_seconds // 60, 0)

                text = (
                    f"\u23f3 <b>REMINDER #{reminder_num}/{max_retries}</b> \u2014 "
                    f"Awaiting approval for:\n<i>{action_description}</i>\n\n"
                    f"\u26a0\ufe0f Auto-BLOCK in <b>{remaining_minutes}</b> minutes"
                )

                try:
                    await self._send_telegram_reminder(text)
                except Exception as exc:
                    logger.debug("Reminder send failed (non-critical): %s", exc)

        except asyncio.CancelledError:
            # Task cancelled because request was resolved — normal flow
            return

    # ---- Telegram message senders ------------------------------------------
    async def _send_telegram_message(self, text: str, reply_markup=None) -> None:
        """Send a message to ADMIN_CHAT_ID with optional inline keyboard."""
        from telegram import Bot

        token = _get_telegram_token()
        admin_id = _get_admin_chat_id()

        bot = Bot(token=token)
        await bot.send_message(
            chat_id=admin_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

    async def _send_telegram_reminder(self, text: str) -> None:
        """Send a plain reminder message to ADMIN_CHAT_ID."""
        from telegram import Bot

        token = _get_telegram_token()
        admin_id = _get_admin_chat_id()

        bot = Bot(token=token)
        await bot.send_message(
            chat_id=admin_id,
            text=text,
            parse_mode="HTML",
        )
