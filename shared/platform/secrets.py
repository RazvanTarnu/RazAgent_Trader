# -*- coding: utf-8 -*-
"""Platform secret loading — no secrets in source, logs, or traces."""

from __future__ import annotations

import logging
import re
from typing import Optional

from shared.keyring_loader import get_credential, load_keys

logger = logging.getLogger("platform.secrets")

# Keys required for full platform operation (non-paper LLM + metrics auth)
PLATFORM_OPTIONAL_KEYS = [
    "OPENROUTER_API_KEY",
    "MOONSHOT_API_KEY",
    "MOONSHOT_ORG_ID",
    "TAILSCALE_METRIC_TOKEN",
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "KUCOIN_API_KEY",
    "KUCOIN_API_SECRET",
    "KUCOIN_API_PASSPHRASE",
    "TRADE_CRYPTO_BOT_TOKEN",
    "TRADE_CRYPTO_CHAT_ID",
]

_SECRET_PATTERN = re.compile(
    r"(sk-(?:or-)?[A-Za-z0-9]{10,}|"
    r"[A-Za-z0-9+/=]{32,}|"
    r"\d{8,12}:[A-Za-z0-9_-]{20,})"
)


def sanitize_message(message: str) -> str:
    """Redact likely secrets from exception/log messages."""
    return _SECRET_PATTERN.sub("***REDACTED***", message)


def load_platform_secrets(*, required: Optional[list[str]] = None) -> dict[str, Optional[str]]:
    """Load platform secrets from keyring into os.environ."""
    return load_keys(keys=PLATFORM_OPTIONAL_KEYS, required=required or [])


def require_secrets(keys: list[str]) -> dict[str, str]:
    """Load secrets and raise if any are missing."""
    loaded = load_platform_secrets(required=keys)
    missing = [k for k in keys if not loaded.get(k)]
    if missing:
        raise RuntimeError(
            f"Required credentials missing from keyring: {missing}. "
            "Set via Windows Credential Manager (service: AgentCeoR or RazAgentTrader)."
        )
    return {k: loaded[k] for k in keys if loaded[k]}


def credential_status() -> dict[str, str]:
    """Audit credential presence without exposing values."""
    return {key: ("SET" if get_credential(key) else "MISSING") for key in PLATFORM_OPTIONAL_KEYS}


def safe_exception_message(exc: BaseException) -> str:
    """Return sanitized exception string safe for logs/metrics."""
    return sanitize_message(str(exc))
