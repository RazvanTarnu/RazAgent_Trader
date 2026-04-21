# -*- coding: utf-8 -*-
"""ApprovalGateBase — Shared base class for CEO and Trading approval gates.

Extracts the common state management and resolve() logic from:
  - shared/approval_gate.py (CEO gate, 200min timeout, TIMEOUT=BLOCK)
  - shared/trading_approval_gate.py (Trading gate, 30min timeout, TIMEOUT=REJECT)

Usage:
    class MyGate(ApprovalGateBase["MyGate"]):
        ...
"""

import asyncio
import logging
from typing import Generic, Optional, TypeVar

logger = logging.getLogger("godclaw.approval_base")

T = TypeVar("T", bound="ApprovalGateBase")


class ApprovalGateBase(Generic[T]):
    """Base class providing shared state management for approval gates.

    Subclasses must:
      1. Define their own ``_instance`` class variable (typed to subclass)
      2. Implement ``_send_message()``
      3. Implement ``_reminder_loop()``
      4. Call ``super().__init__()``
    """

    # Subclasses define their own _instance
    _instance: Optional["ApprovalGateBase"] = None

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Event] = {}
        self._results: dict[str, str] = {}
        self._retry_tasks: dict[str, asyncio.Task] = {}
        self._background_tasks: set[asyncio.Task] = set()  # prevent GC of fire-and-forget tasks

    # ── Singleton factory (each subclass overrides with own _instance) ──
    @classmethod
    def instance(cls: type[T]) -> T:
        """Return (or create) the process-wide singleton for this class."""
        if cls._instance is None:
            cls._instance = cls()
            logger.info("%s singleton created", cls.__name__)
        return cls._instance

    # ── Public resolution API ────────────────────────────────────────────
    def resolve(self, request_id: str, decision: str) -> None:
        """Unblock a pending approval request.

        Called by the Telegram CallbackQueryHandler when admin taps a button.

        Args:
            request_id: The 12-char hex identifier.
            decision:   ``"APPROVED"`` or ``"REJECTED"``.
        """
        if request_id not in self._pending:
            logger.warning(
                "%s.resolve() called for unknown request_id=%s",
                type(self).__name__, request_id,
            )
            return

        self._results[request_id] = decision
        self._pending[request_id].set()

        # Cancel reminder task immediately
        self._cancel_reminder(request_id)

        logger.info(
            "%s resolved externally — id=%s decision=%s",
            type(self).__name__, request_id, decision,
        )

    # ── Protected helpers for subclasses ────────────────────────────────
    def _cancel_reminder(self, request_id: str) -> None:
        """Cancel the reminder task for a request if still running."""
        task = self._retry_tasks.pop(request_id, None)
        if task and not task.done():
            task.cancel()

    def _cleanup_request(self, request_id: str) -> None:
        """Remove all state for a completed request."""
        self._pending.pop(request_id, None)
        self._results.pop(request_id, None)
        self._cancel_reminder(request_id)

    def _fire_and_forget(self, coro) -> asyncio.Task:
        """Schedule a coroutine as a tracked fire-and-forget task.

        Uses asyncio.create_task() (not ensure_future) and keeps a strong
        reference in _background_tasks to prevent GC before completion.

        Returns:
            The created asyncio.Task for optional cancellation.
        """
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def _start_reminder(self, request_id: str, coro) -> asyncio.Task:
        """Start a named reminder loop task, stored in _retry_tasks.

        Args:
            request_id: Key to store the task under.
            coro:       The reminder coroutine to run.

        Returns:
            The created asyncio.Task.
        """
        task = asyncio.create_task(coro)
        self._retry_tasks[request_id] = task
        return task
