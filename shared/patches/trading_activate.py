"""Retired LIVE activation command for the paper-only build."""

import logging

logger = logging.getLogger("godclaw.trading_activate")
LIVE_UNAVAILABLE_MESSAGE = "LIVE nu este implementat în această versiune; sistemul rămâne paper-only."


def _audit_log(details: str, status: str = "blocked") -> None:
    """Retain the compatibility hook without persisting secrets or mode changes."""
    logger.warning("Trading activation blocked (%s): %s", status, details)


async def cmd_trading_activate(update, context):
    """Reject activation unconditionally and leave configuration untouched."""
    _audit_log("LIVE activation requested")
    await update.message.reply_text(LIVE_UNAVAILABLE_MESSAGE)
