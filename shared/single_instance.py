# -*- coding: utf-8 -*-
"""Single Instance Enforcement — kernel-level mutex guard (V1.0).

Prevents duplicate bot processes from polling the same Telegram token,
which produces `409 Conflict` errors and corrupts update delivery.

Uses Windows kernel named mutex (`win32event.CreateMutex`). When a second
process tries to create a mutex with the same name, `GetLastError()` returns
`ERROR_ALREADY_EXISTS` — we exit(0) immediately (not an error; duplicate
start is expected behaviour when autostart + manual launch race).

CRITICAL DESIGN NOTES:
  - The mutex HANDLE must outlive the process lifetime. We hold it in a
    module-level global (_MUTEX_HANDLE). If it were stored in a local var,
    Python's GC would close it, releasing the mutex and defeating the guard.
  - Session-local mutex only (no `Global\\` prefix) — avoids cross-user
    ERROR_ACCESS_DENIED false positives and needs zero elevation.
  - Non-Windows → no-op with warning. Bots on Linux/macOS rely on systemd
    unit `ExecStartPre=` or PID-file guards instead.

Usage:
    # At the very top of an entry-point __main__ block:
    from shared.single_instance import enforce_single_instance
    enforce_single_instance("TradeCrypto")
    # ... rest of bot startup ...
"""
import logging
import sys

logger = logging.getLogger("godclaw.single_instance")

_MUTEX_HANDLE = None  # DO NOT remove — keeps kernel mutex alive for process lifetime


def enforce_single_instance(bot_name: str) -> None:
    """Acquire kernel named mutex or exit(0) if another instance owns it.

    Args:
        bot_name: Short identifier for the bot (e.g. "TradeCrypto", "CEO").
                  Becomes `RazAgent_<bot_name>_Mutex` in the kernel namespace.

    Side effects:
        - On duplicate detection: writes to stderr and calls sys.exit(0).
        - On success: stores mutex handle in module-level global.
        - On non-Windows: logs warning and returns (no-op).
    """
    global _MUTEX_HANDLE

    if sys.platform != "win32":
        logger.warning(
            "[SINGLE-INSTANCE] Non-Windows platform (%s) — guard is no-op. "
            "Use systemd/PID-file on this OS.", sys.platform,
        )
        return

    try:
        import win32event
        import win32api
        import winerror
    except ImportError:
        logger.error("[SINGLE-INSTANCE] pywin32 not installed — guard DISABLED")
        return

    # Sanitize bot_name: mutex names cannot contain backslash
    safe_name = "".join(c for c in bot_name if c.isalnum() or c in ("_", "-"))
    if not safe_name:
        logger.error("[SINGLE-INSTANCE] Invalid bot_name %r — guard DISABLED", bot_name)
        return

    mutex_name = f"RazAgent_{safe_name}_Mutex"

    # CreateMutex(None, False, name) — no security attrs, not owned yet, shared name
    handle = win32event.CreateMutex(None, False, mutex_name)
    last_error = win32api.GetLastError()

    if last_error == winerror.ERROR_ALREADY_EXISTS:
        # Duplicate detected — kernel says another process already owns this mutex.
        # sys.exit(0) because duplicate-start is expected (autostart + manual launch race),
        # not a crash condition. Anything emitting logs would pollute the surviving instance.
        sys.stderr.write(
            f"[SINGLE-INSTANCE] Another instance of '{bot_name}' is already running "
            f"(mutex: {mutex_name}). Exiting silently to avoid Telegram 409 Conflict.\n"
        )
        sys.stderr.flush()
        sys.exit(0)

    # Success — pin handle to module global so GC cannot release the mutex.
    _MUTEX_HANDLE = handle
    logger.info("[SINGLE-INSTANCE] Acquired mutex '%s' (this PID owns it)", mutex_name)
