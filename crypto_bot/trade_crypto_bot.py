# -*- coding: utf-8 -*-
"""TradeCrypto Bot — Dedicated Telegram Bot for Crypto Trading

V10.46 — Fully independent from CEO Agent (@HulkClaw_bot).
Bot: @TradeCrypto13_bot | Port: 8012 | Token: TRADE_CRYPTO_BOT_TOKEN

Commands:
  /start      → Welcome + status
  /portfolio  → Pozitii curente Binance + KuCoin
  /trades     → Ultimele 20 trades din trade_journal
  /pnl        → P&L zilnic/saptamanal/total
  /status     → Trading engine status + PAPER/LIVE mode
  /review     → Daily self-improvement report
  /trading_activate [PIN] → Activare live trading
  /help       → Lista comenzi

Notifications automate:
  - Trade propus → [APPROVE] [REJECT] inline keyboard
  - Trade executat → outcome notification
  - Daily P&L report (20:00)
  - Daily review (00:00)
  - Kill switch alert (MAX_DAILY_LOSS_USD)
"""
import os
import sys
import json
import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from shared.setup_paths import activate; activate()

import httpx
from aiohttp import web

from telegram import (
    Update,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from shared.config import PROJECT_ROOT, DATA_DIR
from shared.version import VERSION
from shared.keyring_loader import get_credential

# Suppress httpx URL logging to prevent bot token leaks in log files
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
from shared.log_filter import install_log_masking; install_log_masking()

logger = logging.getLogger("trade_crypto_bot")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(str(PROJECT_ROOT / "logs" / "trade_crypto_bot.log"),
                            encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

# ═══════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════
PORT = 8012
BOT_TOKEN = get_credential("TRADE_CRYPTO_BOT_TOKEN") or ""
CHAT_ID = get_credential("TRADE_CRYPTO_CHAT_ID") or ""
BASE_DIR = PROJECT_ROOT
# DATA_DIR already imported from shared.config

# Bot reference for async notifications
_bot_ref = {"bot": None, "app": None}


def _safe_html(text: str, max_len: int = 4000) -> str:
    """Escape HTML for Telegram safe sending."""
    if not text:
        return ""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text[:max_len]


async def _send_msg(text: str, reply_markup=None, parse_mode="HTML"):
    """Send message to trading chat."""
    bot = _bot_ref.get("bot")
    if not bot or not CHAT_ID:
        logger.warning("Bot not initialized or CHAT_ID missing")
        return None
    try:
        return await bot.send_message(
            chat_id=int(CHAT_ID),
            text=text[:4096],
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"send_msg failed: {e}")
        return None


# ═══════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message."""
    chat_id = str(update.effective_chat.id)
    if chat_id != CHAT_ID:
        await update.message.reply_text("🔒 Acces restricționat.")
        return
    sep = "─" * 28
    await update.message.reply_text(
        f"💹 <b>TradeCrypto Bot</b> — {VERSION}\n"
        f"{sep}\n"
        f"🏦 Binance + KuCoin\n"
        f"🛡 Zero-Withdrawal Guardrail\n"
        f"📈 Smart Exit + Trailing Stop\n"
        f"🤖 Auto P&amp;L Daily (20:00)\n"
        f"{sep}\n"
        f"/help — Toate comenzile\n"
        f"/portfolio — Pozitii live\n"
        f"/status — Engine status\n"
        f"{sep}\n"
        f"🕐 {datetime.now().strftime('%d %b %H:%M')}",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command."""
    chat_id = str(update.effective_chat.id)
    if chat_id != CHAT_ID:
        return
    sep = "─" * 28
    await update.message.reply_text(
        f"💹 <b>TradeCrypto Bot — Comenzi</b>\n"
        f"{sep}\n"
        f"<b>📊 Portofoliu</b>\n"
        f"/portfolio — Poziții Binance + KuCoin\n"
        f"/trades — Ultimele 20 trades\n"
        f"/pnl — P&amp;L zilnic / săptămânal / total\n"
        f"{sep}\n"
        f"<b>⚙️ Engine</b>\n"
        f"/status — Trading engine status\n"
        f"/review — Daily self-improvement\n"
        f"🔐 /trading_activate — Activare LIVE (PIN)\n"
        f"{sep}\n"
        f"<b>🤖 Automat</b>\n"
        f"  💰 P&amp;L daily — 20:00\n"
        f"  📈 Review — 00:00\n"
        f"  🔔 Trade proposals — real-time\n"
        f"{sep}\n"
        f"<b>🛡 Siguranță</b>\n"
        f"/drawdown — Status drawdown guard\n"
        f"/drawdown_reset — Reset emergency halt\n"
        f"  $7/trade | $50 cap | -10% daily halt\n",
        parse_mode="HTML",
    )


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current portfolio positions."""
    chat_id = str(update.effective_chat.id)
    if chat_id != CHAT_ID:
        return
    await update.message.reply_text("💼 Citesc portofoliul...", parse_mode=None)
    try:
        from backend.razagent_server.skills.crypto_swarm import register_tools
        tools = register_tools()
        result = await asyncio.wait_for(tools["crypto_portfolio"](), timeout=30.0)
        text_out = result.get("output", result.get("error", str(result)))
        try:
            await update.message.reply_text(text_out[:4000], parse_mode="HTML")
        except Exception:
            await update.message.reply_text(_safe_html(text_out), parse_mode="HTML")
    except asyncio.TimeoutError:
        await update.message.reply_text("⏱️ Timeout la citirea portofoliului.")
    except Exception as e:
        await update.message.reply_text(f"❌ Eroare: {e}", parse_mode=None)


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show last 20 trades from trade journal."""
    chat_id = str(update.effective_chat.id)
    if chat_id != CHAT_ID:
        return
    try:
        from shared.trade_journal import get_daily_stats, get_lessons
        stats = get_daily_stats(30)
        lessons = get_lessons(5)

        # Also get recent trades directly from DB
        db_path = BASE_DIR / "Shared_Memory" / "claude_memory.db"
        trades_text = ""
        if db_path.exists():
            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trade_journal ORDER BY ts_open DESC LIMIT 20"
            ).fetchall()
            conn.close()
            if rows:
                lines = []
                for r in rows:
                    side_icon = "🟢" if r["side"] == "buy" else "🔴"
                    closed = "✅" if r["ts_close"] else "⏳"
                    pnl = f"${r['pnl_usd']:.2f}" if r["pnl_usd"] else "—"
                    lines.append(
                        f"{closed} {side_icon} {r['pair']} ${r['size_usd']:.2f} "
                        f"→ {pnl}"
                    )
                trades_text = "\n".join(lines)
            else:
                trades_text = "<i>Niciun trade înregistrat.</i>"
        else:
            trades_text = "<i>DB indisponibilă.</i>"

        await update.message.reply_text(
            f"📋 <b>Ultimele Trades</b>\n"
            f"{'─' * 28}\n"
            f"{trades_text}\n"
            f"{'─' * 28}\n"
            f"📊 30d stats: {json.dumps(stats, indent=0) if stats else 'N/A'}",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Eroare: {e}", parse_mode=None)


async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show P&L daily/weekly/total."""
    chat_id = str(update.effective_chat.id)
    if chat_id != CHAT_ID:
        return
    try:
        from shared.trade_journal import get_daily_stats, get_pattern_analysis
        stats = get_daily_stats(30)
        patterns = get_pattern_analysis(30)

        daily_pnl = stats.get("total_pnl", 0) if stats else 0
        win_rate = stats.get("win_rate", 0) if stats else 0
        total_trades = stats.get("total_trades", 0) if stats else 0

        await update.message.reply_text(
            f"💰 <b>P&amp;L Report</b>\n"
            f"{'─' * 28}\n"
            f"📊 Total P&amp;L (30d): <b>${daily_pnl:.2f}</b>\n"
            f"📈 Win Rate: <b>{win_rate:.0f}%</b>\n"
            f"🔢 Total Trades: <b>{total_trades}</b>\n"
            f"{'─' * 28}\n"
            f"🎯 Best pair: {patterns.get('by_pair', {}).get('best', 'N/A') if patterns else 'N/A'}\n"
            f"📉 Worst pair: {patterns.get('by_pair', {}).get('worst', 'N/A') if patterns else 'N/A'}",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Eroare: {e}", parse_mode=None)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show trading engine status."""
    chat_id = str(update.effective_chat.id)
    if chat_id != CHAT_ID:
        return
    try:
        # V11.52: Reload config to get latest state (hot-reload safe)
        import importlib
        import shared.binance_live_config as _cfg
        importlib.reload(_cfg)
        PAPER_MODE = _cfg.PAPER_MODE
        MAX_TRADE_SIZE_USD = _cfg.MAX_TRADE_SIZE_USD
        MAX_DAILY_LOSS_USD = _cfg.MAX_DAILY_LOSS_USD
        STOP_LOSS_PCT = _cfg.STOP_LOSS_PCT
        TAKE_PROFIT_PCT = _cfg.TAKE_PROFIT_PCT
        MAX_OPEN_POSITIONS = _cfg.MAX_OPEN_POSITIONS
        mode = "📄 PAPER" if PAPER_MODE else "⚡ LIVE"
        await update.message.reply_text(
            f"⚙️ <b>Trading Engine Status</b>\n"
            f"{'─' * 28}\n"
            f"Mode: <b>{mode}</b>\n"
            f"Max Trade: ${MAX_TRADE_SIZE_USD:.2f}\n"
            f"Max Daily Loss: ${MAX_DAILY_LOSS_USD:.2f}\n"
            f"SL: {STOP_LOSS_PCT * 100:.0f}% | TP: {TAKE_PROFIT_PCT * 100:.0f}%\n"
            f"Max Positions: {MAX_OPEN_POSITIONS}\n"
            f"{'─' * 28}\n"
            f"Version: {VERSION}\n"
            f"🕐 {datetime.now().strftime('%d %b %H:%M')}",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Eroare: {e}", parse_mode=None)


async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show daily self-improvement report."""
    chat_id = str(update.effective_chat.id)
    if chat_id != CHAT_ID:
        return
    await update.message.reply_text("📈 Generez review...", parse_mode=None)
    try:
        from shared.trading_improvement_loop import run_daily_review
        result = await run_daily_review()
        await update.message.reply_text(
            f"📈 <b>Daily Review</b>\n{'─' * 28}\n{result}",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Eroare review: {e}", parse_mode=None)


async def cmd_trading_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """PIN-protected live trading activation."""
    chat_id = str(update.effective_chat.id)
    if chat_id != CHAT_ID:
        await update.message.reply_text("🔒 Only admin can activate live trading.")
        return
    # Delegate to existing implementation
    from shared.patches.trading_activate import (
        _get_or_create_pin, _verify_pin, _audit_log,
    )
    # V11.52: Reload to get current state
    import importlib
    import shared.binance_live_config as _live_cfg
    importlib.reload(_live_cfg)
    PAPER_MODE = _live_cfg.PAPER_MODE

    text = (update.message.text or "").strip()
    parts = text.split()

    if len(parts) < 2:
        if PAPER_MODE:
            pin_str, is_new = _get_or_create_pin()
            if is_new:
                await update.message.reply_text(
                    f"🔐 Trading in PAPER MODE.\n\n"
                    f"PIN activare: <code>{pin_str}</code>\n"
                    f"Comanda: /trading_activate {pin_str}",
                    parse_mode="HTML",
                )
                _audit_log("PIN generated on TradeCrypto bot")
            else:
                await update.message.reply_text(
                    "🔐 PAPER MODE activ. PIN deja generat.\n"
                    "Folosește: /trading_activate <PIN>",
                )
        else:
            await update.message.reply_text("⚡ LIVE TRADING deja activ.")
        return

    user_pin = parts[1].strip()
    if not _verify_pin(user_pin):
        _audit_log(f"Invalid PIN attempt: {user_pin[:3]}***", "error")
        await update.message.reply_text("❌ PIN incorect.")
        return

    try:
        config_file = PROJECT_ROOT / "shared" / "binance_live_config.py"
        content = config_file.read_text(encoding="utf-8")
        new_content = content.replace(
            "PAPER_MODE            = True",
            "PAPER_MODE            = False",
        )
        config_file.write_text(new_content, encoding="utf-8")

        # V11.52: Hot-reload config module — no restart needed
        import importlib
        import shared.binance_live_config as _cfg_mod
        importlib.reload(_cfg_mod)
        logger.info(f"[LIVE] Config hot-reloaded: PAPER_MODE={_cfg_mod.PAPER_MODE}")

        _audit_log("LIVE TRADING ACTIVATED via TradeCrypto bot (hot-reloaded)")
        await update.message.reply_text(
            "⚡ <b>LIVE TRADING ACTIVAT</b>\n\n"
            "Safeguards active:\n"
            "  💰 Max $7/trade\n"
            "  🛑 Max $20 loss/zi\n"
            "  📉 SL 2% obligatoriu\n"
            "  📊 Max 3 poziții simultane\n\n"
            "✅ Configurație aplicată instant (fără restart).\n"
            "Sistemul tranzacționează acum cu fonduri reale.",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Activare eșuată: {e}")


# ═══════════════════════════════════════════════════════
# DRAWDOWN GUARD COMMANDS (V42.00)
# ═══════════════════════════════════════════════════════

async def cmd_drawdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current drawdown guard status."""
    chat_id = str(update.effective_chat.id)
    if chat_id != CHAT_ID:
        return
    try:
        from shared.drawdown_guard import get_drawdown_status
        s = get_drawdown_status()
        sep = "─" * 28
        icon = "🔴 HALTED" if s["halted"] else "🟢 ACTIVE"
        await update.message.reply_text(
            f"🛡 <b>Drawdown Guard</b> — {icon}\n"
            f"{sep}\n"
            f"📅 Data: {s['date']}\n"
            f"💰 Start equity: ${s['daily_start_equity']:.2f}\n"
            f"📉 Lowest: ${s['lowest_equity']:.2f}\n"
            f"🚧 Max drawdown: {s['max_drawdown_pct']:.0%}\n"
            f"{sep}\n"
            + (f"⚠️ {s['halt_reason']}\n🕐 Halt: {s['halt_time']}\n" if s["halted"] else "✅ Trading permitted.\n"),
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Drawdown status error: {e}")


async def cmd_drawdown_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually reset drawdown halt. CEO admin only."""
    chat_id = str(update.effective_chat.id)
    if chat_id != CHAT_ID:
        return
    try:
        from shared.drawdown_guard import reset_drawdown
        result = reset_drawdown(admin_reason="CEO manual reset via /drawdown_reset")
        sep = "─" * 28
        await update.message.reply_text(
            f"🔓 <b>Drawdown Guard RESET</b>\n"
            f"{sep}\n"
            f"Was halted: {'Yes' if result['was_halted'] else 'No'}\n"
            f"{result['message']}\n"
            f"{sep}\n"
            f"🕐 {datetime.now().strftime('%d %b %H:%M')}",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Reset failed: {e}")


# ═══════════════════════════════════════════════════════
# APPROVAL CALLBACK HANDLER
# ═══════════════════════════════════════════════════════

async def _handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle trade approval/rejection inline buttons."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if data.startswith("trade_approve_"):
        request_id = data.replace("trade_approve_", "")
        try:
            from shared.trading_approval_gate import TradingApprovalGate
            gate = TradingApprovalGate.instance()
            gate.resolve(request_id, "APPROVED")
            await query.edit_message_text(
                f"✅ Trade APROBAT — {request_id}\n"
                f"🕐 {datetime.now().strftime('%d %b %H:%M')}",
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Eroare aprobare: {e}")

    elif data.startswith("trade_reject_"):
        request_id = data.replace("trade_reject_", "")
        try:
            from shared.trading_approval_gate import TradingApprovalGate
            gate = TradingApprovalGate.instance()
            gate.resolve(request_id, "REJECTED")
            await query.edit_message_text(
                f"❌ Trade RESPINS — {request_id}\n"
                f"🕐 {datetime.now().strftime('%d %b %H:%M')}",
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Eroare respingere: {e}")


# ═══════════════════════════════════════════════════════
# SCHEDULED TASKS
# ═══════════════════════════════════════════════════════

async def _daily_pnl_report(context: ContextTypes.DEFAULT_TYPE):
    """Send daily P&L report at 20:00."""
    try:
        from shared.trade_journal import get_daily_stats
        stats = get_daily_stats(1)
        if not stats:
            return

        pnl = stats.get("total_pnl", 0)
        icon = "📈" if pnl >= 0 else "📉"
        await _send_msg(
            f"{icon} <b>Daily P&amp;L Report</b>\n"
            f"{'─' * 28}\n"
            f"💰 P&amp;L: <b>${pnl:.2f}</b>\n"
            f"📊 Win Rate: {stats.get('win_rate', 0):.0f}%\n"
            f"🔢 Trades: {stats.get('total_trades', 0)}\n"
            f"{'─' * 28}\n"
            f"🕐 {datetime.now().strftime('%d %b %H:%M')}",
        )
    except Exception as e:
        logger.error(f"Daily P&L report failed: {e}")


async def _daily_review_task(context: ContextTypes.DEFAULT_TYPE):
    """Send daily self-improvement review at 00:00."""
    try:
        from shared.trading_improvement_loop import run_daily_review
        result = await run_daily_review()
        await _send_msg(
            f"📈 <b>Daily Trading Review</b>\n"
            f"{'─' * 28}\n"
            f"{result}\n"
            f"{'─' * 28}\n"
            f"🕐 {datetime.now().strftime('%d %b %H:%M')}",
        )
    except Exception as e:
        logger.error(f"Daily review task failed: {e}")


# ═══════════════════════════════════════════════════════
# HEALTH ENDPOINT
# ═══════════════════════════════════════════════════════

async def _health_handler(request):
    """Health check endpoint."""
    return web.json_response({
        "status": "ok",
        "service": "TradeCryptoBot",
        "version": VERSION,
        "port": PORT,
        "uptime": time.time() - _start_time,
        "bot": "@TradeCrypto13_bot",
    })

_start_time = time.time()


async def _run_health_server():
    """Run aiohttp health server on port 8012."""
    app_web = web.Application()
    app_web.router.add_get("/health", _health_handler)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Health server running on port {PORT}")


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════

async def _preflight_ip_watchdog() -> None:
    """Pre-boot IP-rotation check. On change → force PAPER_MODE + CRITICAL alert.

    Non-blocking: hard timeout 4 s inside is_ip_stable; fail-open on any error.
    The alert itself is dispatched by ip_watchdog.check_and_alert via the
    existing trading_notify channel (TradeCrypto Bot) — no extra Telegram code here.
    """
    try:
        from shared.ip_watchdog import check_and_alert
        result = await asyncio.wait_for(check_and_alert(), timeout=4.0)
    except Exception as exc:
        logger.info("ip_watchdog preflight skipped (%s) — boot continues", exc)
        return

    if result.get("changed"):
        try:
            import shared.binance_live_config as _cfg
            _cfg.PAPER_MODE = True
        except Exception as exc:
            logger.warning("Could not force PAPER_MODE after IP change: %s", exc)
        logger.warning(
            "ip_watchdog: public IP rotated — PAPER_MODE forced. "
            "Update KuCoin/Binance whitelist, then /trading_activate to re-enable live."
        )
    else:
        logger.info("ip_watchdog preflight: %s", result.get("reason"))


async def main():
    """Start TradeCrypto bot."""
    if not BOT_TOKEN:
        logger.error("TRADE_CRYPTO_BOT_TOKEN not found in keyring!")
        return

    logger.info(f"Starting TradeCrypto Bot — {VERSION} on port {PORT}")

    # Preflight: detect dynamic-IP rotation BEFORE any exchange handshake.
    await _preflight_ip_watchdog()

    # Start health server
    await _run_health_server()

    # Build Telegram application
    app = Application.builder().token(BOT_TOKEN).build()
    _bot_ref["app"] = app

    # Register commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("trades", cmd_trades))
    app.add_handler(CommandHandler("pnl", cmd_pnl))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("review", cmd_review))
    app.add_handler(CommandHandler("trading_activate", cmd_trading_activate))
    app.add_handler(CommandHandler("drawdown", cmd_drawdown))
    app.add_handler(CommandHandler("drawdown_reset", cmd_drawdown_reset))

    # Approval callbacks
    app.add_handler(CallbackQueryHandler(
        _handle_approval_callback,
        pattern=r"^trade_(approve|reject)_",
    ))

    # Set bot commands menu
    await app.bot.set_my_commands([
        BotCommand("start", "Start TradeCrypto Bot"),
        BotCommand("portfolio", "Pozitii Binance + KuCoin"),
        BotCommand("trades", "Ultimele 20 trades"),
        BotCommand("pnl", "P&L zilnic/saptamanal/total"),
        BotCommand("status", "Trading engine status"),
        BotCommand("review", "Daily self-improvement"),
        BotCommand("trading_activate", "Activare LIVE trading"),
        BotCommand("help", "Lista comenzi"),
    ])

    _bot_ref["bot"] = app.bot

    # Schedule daily tasks
    job_queue = app.job_queue
    if job_queue:
        from datetime import time as dt_time
        # Daily P&L at 20:00
        job_queue.run_daily(_daily_pnl_report, time=dt_time(20, 0))
        # Daily review at 00:00
        job_queue.run_daily(_daily_review_task, time=dt_time(0, 0))
        logger.info("Scheduled: P&L report 20:00, review 00:00")

    # V2.2 Hard Mute (2026-04-17): "TradeCrypto Bot Online" boot greeting DISABLED.
    # Watchdog auto-restarts were emitting this on every respawn → chat spam.
    logger.info(
        "TradeCrypto Bot startup v%s port=%s (Telegram boot greeting suppressed)",
        VERSION, PORT,
    )

    logger.info("TradeCrypto Bot started — polling...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down TradeCrypto Bot...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    # Single Instance Enforcement (V1.0) — blocks duplicate polling of Telegram token
    # Root cause: autostart + manual launch race → `409 Conflict` (CLAUDE.md V2.3.2).
    # MUST be first executable instruction; exit(0) is silent, non-error.
    from shared.single_instance import enforce_single_instance
    enforce_single_instance("TradeCrypto")

    asyncio.run(main())
