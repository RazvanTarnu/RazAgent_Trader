# -*- coding: utf-8 -*-
"""Telegram Message Templates — V10.60

Consistent formatting for all Telegram messages across the ecosystem.
Emoji meaningful (not decorative) — codifies message type.
Separators: ─ (28 chars). Max 3 indentation levels.
Timestamps: DD Mon HH:MM format.

V10.60: Added generate_code_diff_html() for visual Git-style diffs
        and code_change_approval() for code modification reviews.

Usage:
    from shared.telegram_templates import watchdog_report, system_error, fleet_status
    from shared.telegram_templates import generate_code_diff_html, code_change_approval
"""
import difflib
import html as _html
from datetime import datetime


def _ts() -> str:
    """Current timestamp in DD Mon HH:MM format."""
    return datetime.now().strftime("%d %b %H:%M")


def _sep(n: int = 28) -> str:
    return "─" * n


def watchdog_report(cycle_name: str, status: str, data: str, next_run: str) -> str:
    """Watchdog cycle report — executive 3-line format."""
    icon = "✅" if status == "ok" else "⚠️"
    # Extract first meaningful line from data
    summary = data.strip().split("\n")[0][:120] if data.strip() else "OK"
    return (
        f"{icon} <b>{_html.escape(cycle_name)}</b>\n"
        f"{_html.escape(summary)}\n"
        f"⏭ Următor: {_html.escape(next_run)}"
    )


def system_error(component: str, error: str, severity: str = "medium") -> str:
    """System error — executive 3-line format. Full error logged to file."""
    icons = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}
    # Only first line of error, max 150 chars
    short_err = error.strip().split("\n")[0][:150]
    return (
        f"{icons.get(severity, '🔴')} <b>EROARE — {_html.escape(component)}</b>\n"
        f"{_html.escape(short_err)}\n"
        f"🕐 {_ts()}"
    )


def fleet_status(services: list[dict]) -> str:
    """Fleet health — executive 3-line format."""
    online = sum(1 for s in services if s["online"])
    total = len(services)
    down = [s["name"] for s in services if not s["online"]]
    icon = "📡" if online == total else "⚠️"
    status_line = f"{online}/{total} servicii active"
    detail = f"Down: {', '.join(down[:3])}" if down else "Totul funcțional"
    return (
        f"{icon} <b>Stare Flotă</b>\n"
        f"{status_line}\n"
        f"{detail}"
    )


def trade_proposal(
    pair: str, side: str, size: float, entry: float,
    sl: float, tp: float, rsi: float, mode: str,
) -> str:
    """Trade proposal — executive 3-line format."""
    mode_icon = "📄" if mode == "paper" else "⚡"
    side_icon = "🟢 BUY" if side.lower() == "buy" else "🔴 SELL"
    return (
        f"{mode_icon} <b>TRADE {side_icon} {_html.escape(pair)}</b>\n"
        f"${size:.2f} @ ${entry:,.4f} | SL ${sl:,.4f} → TP ${tp:,.4f} (RSI {rsi:.1f})\n"
        f"Mode: {'PAPER 📄' if mode == 'paper' else 'LIVE ⚡'}"
    )


def revenue_report(
    target: str, active_opps: int, proposed: int,
    completed: int, next_action: str,
) -> str:
    """Revenue report — executive 3-line format."""
    return (
        f"📊 <b>Raport Venituri</b>\n"
        f"🎯 {_html.escape(target)} | {active_opps} oportunități, {proposed} propuse, {completed} finalizate\n"
        f"▶ {_html.escape(next_action)}"
    )


def approval_request(action: str, details: str, risk_level: str = "medium") -> str:
    """Approval request — executive 3-line format."""
    icons = {"low": "🟢", "medium": "🟡", "high": "🔴"}
    short_details = details.strip().split("\n")[0][:150]
    return (
        f"⏳ <b>APROBARE NECESARĂ</b> {icons.get(risk_level, '🟡')}\n"
        f"📋 {_html.escape(action)}\n"
        f"{_html.escape(short_details)}"
    )


def daily_briefing(
    services_online: int, services_total: int,
    vram_free: str, gpu_temp: str,
    revenue_24h: float, trading_mode: str, trading_pnl: float,
    videos_produced: int, errors_resolved: int, git_commits: int,
    top_tasks: list[str],
    auto_video_topic: str = "", auto_video_score: int = 0,
) -> str:
    """Daily briefing — compact executive format."""
    lines = [
        f"🌅 <b>Raport Zilnic — {_ts()}</b>",
        f"🖥 {services_online}/{services_total} servicii | VRAM {vram_free} | GPU {gpu_temp}",
        f"📊 Revenue +${revenue_24h:.2f} | {trading_mode} P&amp;L ${trading_pnl:.2f} | 🎬 {videos_produced} videos",
    ]
    if auto_video_topic:
        safe_topic = _html.escape(auto_video_topic[:60])
        lines.append(f"🤖 Video autonom: {safe_topic} (scor {auto_video_score}/100)")
    if top_tasks:
        lines.append(f"📋 Taskuri: {', '.join(t[:40] for t in top_tasks[:3])}")
    return "\n".join(lines)


def log_scan_report(files_scanned: int, findings: list[dict]) -> str:
    """Log error scan — executive 3-line format. Details in log files."""
    if not findings:
        return ""
    total_errors = sum(f.get("count", 0) for f in findings)
    top_files = ", ".join(f"{f['file']}({f['count']})" for f in findings[:3])
    return (
        f"🔍 <b>Log Scan — {len(findings)} fișiere cu erori</b>\n"
        f"{total_errors} erori în {files_scanned} fișiere scanate\n"
        f"Top: {_html.escape(top_files)}"
    )


def resource_alert(gpu_temp: int, vram_pct: float) -> str:
    """Resource alert — executive 2-line format."""
    parts = []
    if gpu_temp >= 80:
        parts.append(f"GPU {gpu_temp}°C {'🚨' if gpu_temp >= 85 else '⚠️'}")
    if vram_pct >= 90:
        parts.append(f"VRAM {vram_pct:.0f}%")
    if not parts:
        return ""
    return (
        f"⚠️ <b>Alertă Resurse</b>\n"
        f"{' | '.join(parts)} — {_ts()}"
    )


# ═══════════════════════════════════════════════════════════════
# V10.60: Visual Code Diff for Telegram
# ═══════════════════════════════════════════════════════════════

def generate_code_diff_html(
    old_code: str,
    new_code: str,
    filename: str = "",
    context_lines: int = 3,
    max_lines: int = 40,
) -> str:
    """Generate a Git-style unified diff formatted for Telegram HTML.

    CRITICAL: All code content is html.escape()-d before wrapping in tags
    to prevent breaking Telegram's HTML parser.

    Args:
        old_code: Original file content.
        new_code: Modified file content.
        filename: Optional filename for the diff header.
        context_lines: Number of context lines around changes.
        max_lines: Maximum diff lines to show (truncate with note).

    Returns:
        HTML-formatted diff string safe for Telegram parse_mode="HTML".
    """
    old_lines = old_code.splitlines(keepends=True)
    new_lines = new_code.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{filename}" if filename else "a/original",
        tofile=f"b/{filename}" if filename else "b/modified",
        n=context_lines,
    ))

    if not diff:
        return "✅ <i>Fișierele sunt identice — nicio modificare.</i>"

    # Count additions/deletions
    additions = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

    # Build HTML output
    parts = []
    if filename:
        parts.append(f"📝 <b>{_html.escape(filename)}</b>")
    parts.append(f"<code>+{additions} −{deletions}</code>")
    parts.append(_sep())

    line_count = 0
    truncated = False

    for line in diff:
        if line_count >= max_lines:
            truncated = True
            break

        line = line.rstrip("\n\r")
        escaped = _html.escape(line)

        if line.startswith("@@"):
            # Hunk header
            parts.append(f"<code>{escaped}</code>")
        elif line.startswith("+++") or line.startswith("---"):
            # File header — skip (already shown above)
            continue
        elif line.startswith("+"):
            parts.append(f"<code>🟢 {escaped}</code>")
        elif line.startswith("-"):
            parts.append(f"<code>🔴 {escaped}</code>")
        else:
            # Context line
            parts.append(f"<code>   {escaped}</code>")

        line_count += 1

    if truncated:
        remaining = len(diff) - max_lines
        parts.append(f"\n<i>... +{remaining} linii omise (diff prea mare)</i>")

    parts.append(_sep())
    return "\n".join(parts)


def code_change_approval(
    file_path: str,
    agent_id: str,
    change_description: str,
    old_code: str = "",
    new_code: str = "",
    risk_level: str = "medium",
) -> str:
    """Code change approval — compact 3-line header + optional diff."""
    import os
    icons = {"low": "🟢", "medium": "🟡", "high": "🔴"}
    risk_icon = icons.get(risk_level, "🟡")
    fname = os.path.basename(file_path)

    lines = [
        f"🔧 <b>CODE CHANGE</b> {risk_icon} — {_html.escape(fname)}",
        f"🤖 {_html.escape(agent_id)}: {_html.escape(change_description[:120])}",
    ]

    if old_code and new_code:
        diff_html = generate_code_diff_html(old_code, new_code, filename=fname)
        lines.append(diff_html)
    elif new_code:
        preview = new_code[:300]
        lines.append(f"<pre>{_html.escape(preview)}</pre>")

    lines.append(f"⏳ Așteaptă aprobare — {_ts()}")
    return "\n".join(lines)
