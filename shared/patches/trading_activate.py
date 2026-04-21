"""Trading Activation Command — V10.44

PIN-protected command to switch from PAPER_MODE to LIVE trading.
First call generates PIN and sends to admin. Subsequent calls verify PIN.

Usage: /trading_activate <PIN>
"""
import hashlib
import logging
import os
import random
import sqlite3
from pathlib import Path

logger = logging.getLogger("godclaw.trading_activate")

AUDIT_DB = Path("D:/RazAgent_Enterprise/data/audit_logs.db")
PIN_FILE = Path("D:/RazAgent_Enterprise/data/.trading_pin")
CONFIG_FILE = Path("D:/RazAgent_Enterprise/shared/binance_live_config.py")


def _audit_log(details: str, status: str = "ok"):
    try:
        conn = sqlite3.connect(str(AUDIT_DB), timeout=5)
        conn.execute("PRAGMA busy_timeout=3000")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now')),
                action_type TEXT, agent_id TEXT, target TEXT,
                details TEXT, status TEXT DEFAULT 'ok'
            )
        """)
        conn.execute(
            "INSERT INTO audit_actions (action_type, agent_id, target, details, status) VALUES (?,?,?,?,?)",
            ("trading_activate", "admin", "binance_config", details[:1000], status),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _get_or_create_pin() -> tuple[str, bool]:
    """Get existing PIN or create new one. Returns (pin, is_new)."""
    if PIN_FILE.is_file():
        stored_hash = PIN_FILE.read_text().strip()
        return stored_hash, False

    # Generate new 6-digit PIN
    pin = f"{random.randint(100000, 999999)}"
    pin_hash = hashlib.sha256(pin.encode()).hexdigest()
    PIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    PIN_FILE.write_text(pin_hash)
    return pin, True


def _verify_pin(user_pin: str) -> bool:
    """Verify PIN against stored hash."""
    if not PIN_FILE.is_file():
        return False
    stored_hash = PIN_FILE.read_text().strip()
    user_hash = hashlib.sha256(user_pin.encode()).hexdigest()
    return user_hash == stored_hash


async def cmd_trading_activate(update, context):
    """Handle /trading_activate <PIN> command."""
    # V11.52: Reload config for fresh state
    import importlib
    import shared.binance_live_config as _live_cfg
    importlib.reload(_live_cfg)
    PAPER_MODE = _live_cfg.PAPER_MODE

    admin_id = os.environ.get("ADMIN_CHAT_ID", "")
    chat_id = str(update.effective_chat.id)

    # Only admin can activate
    if chat_id != admin_id:
        await update.message.reply_text("🔒 Only admin can activate live trading.")
        return

    text = update.message.text or ""
    parts = text.strip().split()

    # No PIN provided — show status or generate PIN
    if len(parts) < 2:
        if PAPER_MODE:
            pin_str, is_new = _get_or_create_pin()
            if is_new:
                await update.message.reply_text(
                    f"🔐 Trading System in PAPER MODE.\n\n"
                    f"Testeaza 7+ zile inainte de activare live.\n"
                    f"PIN activare: <code>{pin_str}</code>\n"
                    f"(salveaza-l in siguranta)\n\n"
                    f"Comanda activare: /trading_activate {pin_str}",
                    parse_mode="HTML",
                )
                _audit_log("PIN generated and sent to admin")
            else:
                await update.message.reply_text(
                    "🔐 PAPER MODE activ. PIN-ul a fost deja generat.\n"
                    "Foloseste: /trading_activate <PIN>",
                )
        else:
            await update.message.reply_text("⚡ LIVE TRADING deja activ.")
        return

    # PIN provided — verify and activate
    user_pin = parts[1].strip()
    if not _verify_pin(user_pin):
        _audit_log(f"Invalid PIN attempt: {user_pin[:3]}***", "error")
        await update.message.reply_text("❌ PIN incorect. Incearca din nou.")
        return

    # Activate live trading by modifying config file
    try:
        content = CONFIG_FILE.read_text(encoding="utf-8")
        new_content = content.replace(
            "PAPER_MODE            = True",
            "PAPER_MODE            = False",
        )
        CONFIG_FILE.write_text(new_content, encoding="utf-8")

        # V11.52: Hot-reload the config module in-process
        importlib.reload(_live_cfg)
        logger.info(f"[LIVE] Config hot-reloaded: PAPER_MODE={_live_cfg.PAPER_MODE}")

        _audit_log("LIVE TRADING ACTIVATED via PIN (hot-reloaded, no restart)")

        await update.message.reply_text(
            "⚡ <b>LIVE TRADING ACTIVAT</b>\n\n"
            "Safeguards active:\n"
            "  💰 Max $7/trade\n"
            "  🛑 Max $20 pierdere/zi (kill switch)\n"
            "  📉 SL 2% obligatoriu\n"
            "  📊 Max 3 poziții simultane\n"
            "  🔐 Approval gate Telegram\n\n"
            "✅ Configurație aplicată instant (fără restart).",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Failed to activate live trading: {e}")
        await update.message.reply_text(f"❌ Activare esuata: {e}")
