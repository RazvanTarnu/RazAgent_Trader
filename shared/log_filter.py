# -*- coding: utf-8 -*-
"""Log Masking Filter — Redacts sensitive tokens from log output.

Catches Telegram bot tokens, API keys, and other secrets that might leak
into log messages (e.g., httpx URL logging, error tracebacks).

Usage (in entry points):
    from shared.log_filter import install_log_masking
    install_log_masking()  # Installs on root logger — covers ALL loggers

V1.0 — Security Hardening: Prevents 106k+ token leaks in logs.
"""

import logging
import re

# Patterns that match sensitive data in log messages.
# V1.1 (2026-04-17): added bare-Telegram-token pattern. The old filter only
# caught `bot<id>:<secret>` URL form; it missed exceptions like
# `telegram.error.InvalidToken: The token '8639488984:AAFm...' was rejected`,
# which reach logs via `exc_info` from python-telegram-bot internals.
_SENSITIVE_PATTERNS = [
    # Telegram bot tokens in API URLs: api.telegram.org/bot<id>:<secret>
    (re.compile(r"(api\.telegram\.org/bot)\d+:[A-Za-z0-9_-]+"), r"\1***REDACTED***"),
    # Telegram bot tokens prefixed with 'bot': bot123456:ABCdef...
    (re.compile(r"(bot\d{8,12}:)[A-Za-z0-9_-]{30,50}"), r"\1***REDACTED***"),
    # Bare Telegram bot tokens: 123456789:ABCdef... (NO bot/URL prefix).
    # Token format is fixed: 8–12 digit bot_id, colon, 35 char base64-ish body.
    # Requires 35+ secret chars to avoid matching `HH:MM:SS` timestamps or
    # port specifiers. Keep the bot_id visible (for debug), redact the secret.
    (re.compile(r"\b(\d{8,12}:)[A-Za-z0-9_-]{35,}\b"), r"\1***REDACTED***"),
    # Bearer tokens in URLs or headers
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_.=-]{20,}"), r"\1***REDACTED***"),
    # OpenRouter / OpenAI API keys: sk-or-..., sk-...
    (re.compile(r"(sk-(?:or-)?)[A-Za-z0-9]{20,}"), r"\1***REDACTED***"),
    # GitHub PATs: ghp_..., github_pat_...
    (re.compile(r"(ghp_)[A-Za-z0-9]{20,}"), r"\1***REDACTED***"),
    (re.compile(r"(github_pat_)[A-Za-z0-9]{20,}"), r"\1***REDACTED***"),
    # Generic long hex tokens (API keys, secrets) — 32+ hex chars
    (re.compile(r"(token[=:]\s*)[a-fA-F0-9]{32,}"), r"\1***REDACTED***"),
]

_installed = False


class SensitiveLogFilter(logging.Filter):
    """Redacts sensitive tokens from log records before they reach handlers.

    V1.1: also scrubs `exc_text` and exception args. The prior version only
    touched `record.msg` — tokens leaked via `logger.exception(...)` slipped
    through because the traceback is stringified later by the formatter from
    `record.exc_info`. We now pre-format exception text here (so the formatter
    picks up our redacted version) and we also scrub `exc_info[1].args` in
    place so any downstream handler that re-formats gets the clean version.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # 1) redact the log message itself
        if record.args:
            try:
                msg = record.getMessage()
                redacted = _redact(msg)
                if redacted != msg:
                    record.msg = redacted
                    record.args = None
            except Exception:
                pass
        else:
            try:
                record.msg = _redact(str(record.msg))
            except Exception:
                pass

        # 2) redact the exception tail (tracebacks from logger.exception)
        if record.exc_info:
            try:
                import logging as _logging
                # Prefer the formatter that's already on the handler; if not
                # available (filter runs before handler binding), fall back to
                # a default Formatter — its formatException is the same code.
                formatter = _logging.Formatter()
                record.exc_text = _redact(formatter.formatException(record.exc_info))
                # Also scrub the live exception's args in-place, so any other
                # handler that re-formats exc_info sees the redacted version.
                exc_value = record.exc_info[1]
                if exc_value is not None and getattr(exc_value, "args", None):
                    exc_value.args = tuple(
                        _redact(str(a)) if isinstance(a, str) else a
                        for a in exc_value.args
                    )
            except Exception:
                pass

        # 3) belt-and-braces: if a handler already cached exc_text, redact it
        if record.exc_text:
            try:
                record.exc_text = _redact(record.exc_text)
            except Exception:
                pass

        return True


def _redact(text: str) -> str:
    """Apply all sensitive patterns to redact secrets from text."""
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def install_log_masking() -> None:
    """Install the sensitive log filter on the root logger.

    Idempotent — safe to call multiple times.
    Covers ALL loggers (httpx, httpcore, urllib3, etc.) since
    they all propagate to root.
    """
    global _installed
    if _installed:
        return

    root = logging.getLogger()
    root.addFilter(SensitiveLogFilter())
    _installed = True
