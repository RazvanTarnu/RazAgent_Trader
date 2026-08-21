"""Execution safety primitives for the paper-only platform."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import NoReturn

from shared.setup_paths import PROJECT_ROOT

logger = logging.getLogger("shared.execution")

AUDIT_DB_PATH = PROJECT_ROOT / "data" / "platform_events.db"


class ExecutionForbidden(RuntimeError):
    """Raised whenever legacy code attempts to execute a financial action."""


def emit_execution_forbidden_audit(
    message: str,
    *,
    target: str = "",
    actor: str = "system",
) -> None:
    """Best-effort audit for a blocked financial action. Never raises."""
    try:
        from shared.events.event_logger import SQLiteEventLogger
        from shared.platform.interfaces import AuditEvent

        SQLiteEventLogger(AUDIT_DB_PATH).log_event(
            AuditEvent(
                timestamp=datetime.now(timezone.utc),
                category="execution",
                action="ExecutionForbidden",
                actor=actor,
                target=target or "unknown",
                details={"message": message},
                status="BLOCKED",
            )
        )
    except Exception:
        logger.warning(
            "failed to persist ExecutionForbidden audit event",
            exc_info=True,
        )


def raise_execution_forbidden(
    message: str,
    *,
    target: str = "",
    actor: str = "system",
) -> NoReturn:
    """Emit an AuditEvent, then raise ExecutionForbidden.

    Audit failures are swallowed so they cannot mask the original refusal.
    """
    emit_execution_forbidden_audit(message, target=target, actor=actor)
    raise ExecutionForbidden(message)
