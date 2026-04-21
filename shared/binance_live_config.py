"""Binance Live Trading Safeguards — V10.44

HARDCODED SAFEGUARDS — DO NOT MODIFY WITHOUT MANUAL APPROVAL.
Any change requires CEO Backend restart + Telegram confirmation.

PAPER_MODE = True until Razvan activates via /trading_activate.
"""

# ═══════════════════════════════════════════════════════
# SAFEGUARDS — NU MODIFICA FARA APROBARE MANUALA
# ═══════════════════════════════════════════════════════
PAPER_MODE            = True      # 2026-04-17: forced True for hard restart (CEO-approved). Was False. Re-arm via /trading_activate.
from shared.config import (
    MAX_TRADE_SIZE_USD,
    MAX_DAILY_LOSS_USD,
    MAX_PORTFOLIO_RISK,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
)
MAX_OPEN_POSITIONS    = 3        # max 3 pozitii simultan
REQUIRE_APPROVAL      = True     # Telegram gate OBLIGATORIU
ALLOWED_PAIRS         = [        # doar perechi majore, lichide (V42.00: +SOL)
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
]
COMPOUNDING_MODE      = True     # profiturile raman in cont
DAILY_REVIEW_HOUR     = 0        # ora 00:00 pentru self-improvement
TRADE_COOLDOWN_HOURS  = 4        # cooldown intre trades (evita overtrading)
