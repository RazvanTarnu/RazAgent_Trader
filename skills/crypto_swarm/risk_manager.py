"""V16.0 Crypto Swarm — Risk Manager Agent"""
import logging

logger = logging.getLogger("godclaw.crypto.risk")

# Strict risk limits
MAX_TRADE_PCT = 10.0      # Max 10% of balance per trade
MIN_TRADE_USD = 1.0       # Don't trade less than $1
MAX_TRADE_USD = 50.0      # Hard cap for micro-trading
STOP_LOSS_PCT = 5.0       # Mandatory 5% stop-loss
TAKE_PROFIT_PCT = 10.0    # Default take-profit at 10%

# BLACKLISTED operations — NEVER allow
BLOCKED_OPERATIONS = {"withdraw", "transfer", "margin", "futures", "lending", "staking"}

async def validate_trade(trade_proposal: dict, balance_usd: float, **kwargs) -> dict:
    """Validate a trade proposal against risk rules. Returns approved/rejected with reason."""
    action = trade_proposal.get("action", "").lower()
    amount_usd = trade_proposal.get("amount_usd", 0)
    asset = trade_proposal.get("asset", "UNKNOWN")

    # Rule 0: Block forbidden operations
    for blocked in BLOCKED_OPERATIONS:
        if blocked in action:
            return {
                "approved": False,
                "reason": f"🚫 BLOCKED: '{blocked}' operations are FORBIDDEN. Only spot trading allowed.",
                "rule": "SECURITY_GATE",
            }

    # Rule 1: Minimum trade size
    if amount_usd < MIN_TRADE_USD:
        return {
            "approved": False,
            "reason": f"Trade too small: ${amount_usd:.2f} < ${MIN_TRADE_USD} minimum.",
            "rule": "MIN_SIZE",
        }

    # Rule 2: Maximum trade size (hard cap)
    if amount_usd > MAX_TRADE_USD:
        return {
            "approved": False,
            "reason": f"Trade too large: ${amount_usd:.2f} > ${MAX_TRADE_USD} hard cap.",
            "rule": "MAX_SIZE",
        }

    # Rule 3: Position size limit (max 10% of balance)
    if balance_usd > 0:
        pct = (amount_usd / balance_usd) * 100
        if pct > MAX_TRADE_PCT:
            return {
                "approved": False,
                "reason": f"Position too large: {pct:.1f}% > {MAX_TRADE_PCT}% max of balance.",
                "rule": "MAX_PCT",
            }

    # Rule 4: Mandatory stop-loss
    stop_loss = trade_proposal.get("stop_loss_pct")
    if not stop_loss:
        trade_proposal["stop_loss_pct"] = STOP_LOSS_PCT
        trade_proposal["take_profit_pct"] = TAKE_PROFIT_PCT

    return {
        "approved": True,
        "reason": f"✅ Trade approved: {action} ${amount_usd:.2f} {asset}",
        "stop_loss": trade_proposal.get("stop_loss_pct", STOP_LOSS_PCT),
        "take_profit": trade_proposal.get("take_profit_pct", TAKE_PROFIT_PCT),
        "rule": "APPROVED",
    }

async def get_risk_limits(**kwargs) -> dict:
    """Return current risk configuration."""
    return {
        "output": (
            "🛡️ <b>Risk Manager Configuration</b>\n\n"
            f"Max per trade: {MAX_TRADE_PCT}% of balance\n"
            f"Min trade: ${MIN_TRADE_USD}\n"
            f"Max trade: ${MAX_TRADE_USD} (hard cap)\n"
            f"Stop-Loss: {STOP_LOSS_PCT}% (mandatory)\n"
            f"Take-Profit: {TAKE_PROFIT_PCT}% (default)\n"
            f"Blocked: {', '.join(sorted(BLOCKED_OPERATIONS))}\n"
            f"Mode: SPOT ONLY (no futures/margin)"
        ),
    }

def register_tools() -> dict:
    return {
        "crypto_risk_check": validate_trade,
        "crypto_risk_limits": get_risk_limits,
    }
