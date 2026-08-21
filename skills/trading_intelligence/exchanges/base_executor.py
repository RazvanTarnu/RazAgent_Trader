# -*- coding: utf-8 -*-
"""Multi-Exchange Trading V11.60 — Base Executor.

Abstract base class for all exchange implementations.
Defines standardized result types and interface contract.

V11.60: IRONCLAD Zero-Withdrawal Guardrail
  - Blocks ALL withdrawal/transfer API endpoints at code level
  - CriticalSecurityException raised + audit logged on any attempt
  - Cannot be bypassed by LLM, config changes, or env vars
"""

import logging
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("TradingIntelligence")

# V1.8.0: Import from shared/config.py SSOT (was hardcoded 50.0)
try:
    from shared.config import TRADING_HARD_CAP as MAX_TRADE_USD
except ImportError:
    MAX_TRADE_USD: float = 50.0  # HARD LIMIT — applies to ALL exchanges

# ═══════════════════════════════════════════════════════
# V11.60: ZERO-WITHDRAWAL GUARDRAIL — IMMUTABLE
# ═══════════════════════════════════════════════════════
# These keywords in ANY API URL trigger an immediate block.
# This list is HARDCODED and cannot be modified at runtime.
_FORBIDDEN_ENDPOINTS = frozenset({
    "withdraw", "transfer", "outbound", "capital/withdraw",
    "margin/transfer", "sub-account/transfer", "futures/transfer",
    "mining/hash-transfer", "asset/transfer", "universal-transfer",
    "inner-transfer", "accounts/inner-transfer",
    "accounts/sub-transfer", "sapi/v1/capital",
    "asset/dust", "asset/convert", "convert/", "margin/", "futures/",
    "lending/", "staking/", "sub-account/", "simple-earn/", "loan/",
})


class CriticalSecurityException(Exception):
    """Raised when a forbidden exchange operation is attempted.

    This exception MUST NEVER be caught silently. It indicates
    an unauthorized attempt to extract funds from the exchange.
    """
    pass


def _audit_security_violation(exchange: str, endpoint: str, details: str):
    """Log a critical security violation to audit_logs.db."""
    try:
        db = Path("D:/RazAgent_Enterprise/data/audit_logs.db")
        conn = sqlite3.connect(str(db), timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO audit_actions (timestamp, timestamp_unix, action_type, "
            "agent_id, target, details, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.utcnow().isoformat(), time.time(),
                "CRITICAL_SECURITY_VIOLATION", "zero_withdrawal_guard",
                f"{exchange}:{endpoint}", details[:1000], "BLOCKED",
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Audit failure must not mask the security exception


def validate_endpoint_safety(exchange: str, url: str) -> None:
    """Check if an API URL contains forbidden withdrawal/transfer endpoints.

    Raises CriticalSecurityException if a forbidden endpoint is detected.
    This function MUST be called before ANY signed API request.

    THIS FUNCTION IS IMMUTABLE — DO NOT MODIFY.
    """
    url_lower = url.lower()
    for forbidden in _FORBIDDEN_ENDPOINTS:
        if forbidden in url_lower:
            msg = (
                f"UNAUTHORIZED ACTION: WITHDRAWALS ARE STRICTLY FORBIDDEN. "
                f"Exchange={exchange}, blocked_endpoint='{forbidden}' in URL={url[:200]}"
            )
            logger.critical(msg)
            _audit_security_violation(exchange, forbidden, msg)
            raise CriticalSecurityException(msg)


@dataclass
class OrderResult:
    """Standardized order result across exchanges."""
    success: bool
    exchange: str
    order_id: Optional[str] = None
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    price: float = 0.0
    fee: float = 0.0
    fee_currency: str = "USDT"
    error: Optional[str] = None
    raw_response: Optional[dict] = field(default=None, repr=False)


@dataclass
class BalanceInfo:
    """Standardized balance info."""
    exchange: str
    asset: str
    free: float
    locked: float
    total: float


@dataclass
class PriceInfo:
    """Standardized price/spread info."""
    exchange: str
    symbol: str
    bid: float
    ask: float
    spread: float
    spread_percent: float
    last_price: float
    volume_24h: float


class BaseExchangeExecutor(ABC):
    """Abstract base class — all exchange implementations inherit from this."""

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret

    @property
    @abstractmethod
    def name(self) -> str:
        """Exchange name identifier (e.g. 'binance', 'kucoin')."""
        ...

    @abstractmethod
    async def get_balance(self, asset: str = "USDT") -> BalanceInfo:
        ...

    @abstractmethod
    async def get_price(self, symbol: str) -> PriceInfo:
        ...

    @abstractmethod
    async def place_market_order(
        self, symbol: str, side: str, amount_usd: float,
    ) -> OrderResult:
        ...

    @abstractmethod
    async def test_connection(self) -> bool:
        ...

    def _validate_trade(self, amount_usd: float) -> None:
        """Validate trade parameters. Raises ValueError on violation.

        V42.00: Added drawdown guard check — halts all trading if daily loss >= 10%.
        """
        if amount_usd > MAX_TRADE_USD:
            raise ValueError(
                f"Amount ${amount_usd:.2f} exceeds HARD LIMIT ${MAX_TRADE_USD:.2f}"
            )
        if amount_usd <= 0:
            raise ValueError("Trade amount must be positive")

        # V42.00: Drawdown guard — check daily equity before EVERY trade
        try:
            from shared.drawdown_guard import check_drawdown
            # Get current balance to check drawdown
            balance_usd = float(os.environ.get("_LAST_KNOWN_EQUITY", "0"))
            if balance_usd > 0:
                dd = check_drawdown(balance_usd)
                if dd["halted"]:
                    raise ValueError(
                        f"DRAWDOWN HALT ACTIVE: {dd['reason']}. "
                        f"Use /drawdown_reset to resume trading."
                    )
        except ImportError:
            raise ValueError(
                "DRAWDOWN GUARD MISSING: shared.drawdown_guard module not found. "
                "Trades blocked until module is restored (fail-closed)."
            )
        except ValueError:
            raise  # Re-raise drawdown halt
        except Exception as e:
            logger.warning("Drawdown check failed (non-blocking): %s", e)
