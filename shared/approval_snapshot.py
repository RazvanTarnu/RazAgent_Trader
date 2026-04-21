# -*- coding: utf-8 -*-
"""V31.1 — Approval Gate Context Enrichment.

Builds a rich decision snapshot for Telegram approval messages.
Gathers 4 data sources in parallel (3s timeout), gracefully degrades per source.

Usage:
    from shared.approval_snapshot import build_snapshot

    text = await build_snapshot(
        action_description="Deploy model to production",
        request_id="abc123def456",
        severity="HIGH",
        metadata={"platform": "youtube"},
    )
    # Returns Telegram HTML-formatted string with all context
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("godclaw.approval_snapshot")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DASHBOARD_APPROVAL_URL = "http://localhost:8888/dashboard/v2/approval/pending"
_GLOBAL_TIMEOUT = 3.0  # seconds — hard cap for all 4 sources combined


# ---------------------------------------------------------------------------
# Source 1: Fal.ai Cost
# ---------------------------------------------------------------------------
async def _fetch_fal_cost() -> str:
    """Read current fal.ai session cost + daily spend."""
    try:
        from Video_Studio_Worker.pipeline.fal_router import (
            get_cost_tracker,
            _read_daily_cloud_spend,
            FAL_JOB_HARD_LIMIT_USD,
        )
        tracker = get_cost_tracker()
        status = tracker.get_status()
        daily = _read_daily_cloud_spend()

        spent = status.get("spent_usd", 0)
        remaining = status.get("remaining_usd", 0)
        cloud_renders = status.get("renders_cloud", 0)

        emoji = "\u2705" if daily < FAL_JOB_HARD_LIMIT_USD * 0.7 else "\u26a0\ufe0f"
        return (
            f"{emoji} Sesiune: ${spent:.2f} cheltuit | "
            f"${remaining:.2f} r\u0103mas\n"
            f"    Azi total: ${daily:.2f} / ${FAL_JOB_HARD_LIMIT_USD:.2f} limit\u0103 "
            f"({cloud_renders} renders cloud)"
        )
    except Exception as exc:
        logger.debug("Fal cost snapshot failed: %s", exc)
        return "\u23f3 N/A"


# ---------------------------------------------------------------------------
# Source 2: VRAM + GPU Temp
# ---------------------------------------------------------------------------
async def _fetch_vram_gpu() -> str:
    """Read VRAM and GPU temperature via shared.vram_utils."""
    try:
        from shared.vram_utils import get_vram

        used_mb, total_mb, free_mb, temp_c = get_vram()
        if total_mb == 0:
            return "\u23f3 N/A (GPU not detected)"

        free_gb = free_mb / 1024
        total_gb = total_mb / 1024
        used_gb = used_mb / 1024

        # Status emoji
        temp_ok = temp_c < 80
        vram_ok = free_mb > 8000  # 8 GB threshold
        emoji = "\u2705" if (temp_ok and vram_ok) else "\u26a0\ufe0f"

        return (
            f"{emoji} VRAM: {free_gb:.1f} GB libere / {total_gb:.0f} GB "
            f"(folosit: {used_gb:.1f} GB)\n"
            f"    Temp: {temp_c}\u00b0C "
            f"{'[\u2705 OK]' if temp_ok else '[\u26a0\ufe0f HOT]'}"
        )
    except Exception as exc:
        logger.debug("VRAM snapshot failed: %s", exc)
        return "\u23f3 N/A"


# ---------------------------------------------------------------------------
# Source 3: RAG Memory Context
# ---------------------------------------------------------------------------
async def _fetch_rag_context(query: str) -> str:
    """Semantic search in ChromaDB for relevant memory fragments."""
    try:
        from shared.rag_sync import semantic_search

        hits = semantic_search(query=query[:60], limit=2, collection_name="godclaw_memory")
        if not hits:
            return "\u23f3 Niciun context relevant g\u0103sit"

        lines = []
        for i, hit in enumerate(hits[:2]):
            content = hit.get("content", "")[:80].replace("\n", " ").strip()
            similarity = hit.get("similarity", 0)
            source = hit.get("metadata", {}).get("source_table", "unknown")
            lines.append(f"\u2022 [{similarity:.0%}] {content}\n      (sursa: {source})")

        return "\n".join(lines)
    except Exception as exc:
        logger.debug("RAG snapshot failed: %s", exc)
        return "\u23f3 N/A (ChromaDB indisponibil)"


# ---------------------------------------------------------------------------
# Source 4: Failsafe Status
# ---------------------------------------------------------------------------
async def _fetch_failsafe_status() -> str:
    """Read the last cached failsafe result without running a new check."""
    try:
        from shared import failsafe_engine

        # Check for a cached last result (if the module tracks it)
        last_result = getattr(failsafe_engine, "_last_result", None)
        if last_result is not None:
            status = last_result.status
            conf = last_result.confidence
            emoji = "\u2705" if status == "PASS" else "\u26a0\ufe0f"
            signals = ", ".join(
                k for k, v in last_result.input_sources.items()
                if v.get("available")
            ) or "none"
            return f"{emoji} {status} | Confidence: {conf:.2%} | Signals: {signals}"

        # No cached result — report as not yet evaluated
        return "\u2139\ufe0f Nicio evaluare recent\u0103 (primul check \u00eenc\u0103 nu a rulat)"
    except Exception as exc:
        logger.debug("Failsafe snapshot failed: %s", exc)
        return "\u23f3 N/A"


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------
async def build_snapshot(
    action_description: str,
    request_id: str = "",
    severity: str = "HIGH",
    metadata: Optional[dict] = None,
    timeout: float = _GLOBAL_TIMEOUT,
) -> str:
    """Build a rich context snapshot for Telegram approval messages.

    Runs all 4 data sources in parallel with a global timeout.
    Each source that fails or times out is replaced with "N/A".

    Args:
        action_description: What action needs approval.
        request_id: The 12-char hex request identifier.
        severity: Severity level (HIGH, CRITICAL, etc.).
        metadata: Optional extra context from the caller.
        timeout: Max seconds to wait for all sources (default 3.0).

    Returns:
        HTML-formatted string ready for Telegram ``parse_mode="HTML"``.
    """
    meta = metadata or {}

    # Launch all 4 sources in parallel with timeout
    tasks = [
        asyncio.wait_for(_fetch_fal_cost(), timeout=timeout),
        asyncio.wait_for(_fetch_vram_gpu(), timeout=timeout),
        asyncio.wait_for(_fetch_rag_context(action_description), timeout=timeout),
        asyncio.wait_for(_fetch_failsafe_status(), timeout=timeout),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Extract results (replace exceptions with N/A)
    fal_line = results[0] if isinstance(results[0], str) else "\u23f3 N/A"
    vram_line = results[1] if isinstance(results[1], str) else "\u23f3 N/A"
    rag_lines = results[2] if isinstance(results[2], str) else "\u23f3 N/A"
    failsafe_line = results[3] if isinstance(results[3], str) else "\u23f3 N/A"

    # Optional metadata section
    meta_section = ""
    if meta:
        meta_items = "\n".join(f"    \u2022 <b>{k}:</b> {str(v)[:80]}" for k, v in meta.items())
        meta_section = f"\n\U0001f4ce <b>Metadata:</b>\n{meta_items}\n"

    # Build the final message (HTML format for Telegram)
    snapshot = (
        f"\u26a1 <b>APROBARE NECESAR\u0102</b> [{severity}]\n\n"
        f"\U0001f4cb <b>Ac\u021biune:</b> {action_description}\n"
        f"\U0001f194 <b>Request ID:</b> <code>{request_id}</code>\n"
        f"{meta_section}\n"
        f"\U0001f4b0 <b>Fal.ai Budget:</b>\n    {fal_line}\n\n"
        f"\U0001f5a5\ufe0f <b>Sistem:</b>\n    {vram_line}\n\n"
        f"\U0001f9e0 <b>Context RAG:</b>\n    {rag_lines}\n\n"
        f"\U0001f6e1\ufe0f <b>Failsafe:</b> {failsafe_line}\n\n"
        f"\u23f1\ufe0f Timeout: 200 min | Reminder: 5 min\n"
        f"\u26a0\ufe0f <i>No response = AUTO-BLOCK</i>"
    )

    return snapshot
