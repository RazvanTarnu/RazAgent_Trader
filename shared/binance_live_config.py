"""Legacy trading limit aliases (mode lives only in PlatformConfig).

HARDCODED SAFEGUARDS — DO NOT MODIFY WITHOUT MANUAL APPROVAL.
Any change requires CEO Backend restart + Telegram confirmation.

"""

# ═══════════════════════════════════════════════════════
# SAFEGUARDS — NU MODIFICA FARA APROBARE MANUALA
# ═══════════════════════════════════════════════════════
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
