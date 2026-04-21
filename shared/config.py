# -*- coding: utf-8 -*-
"""System-wide SSOT constants for RazAgent Enterprise.

Central registry for ports, LLM defaults, GPU thresholds, and paths.
Domain-specific configs (trading limits, video pipeline timeouts) stay in
their own modules but should import shared values from here.

Usage:
    from shared.config import OLLAMA_MODEL, PORTS, VRAM_THRESHOLD_MB
"""

import json
import os
import time
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # D:\RazAgent_Enterprise
DATA_DIR = PROJECT_ROOT / "data"
DB_DIR = DATA_DIR / "databases"
COMFYUI_ROOT = Path(os.environ.get("COMFYUI_ROOT", r"D:\ComfyUI"))

# ── LLM ───────────────────────────────────────────────────────────────
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = "qwen3:30b-a3b"
OLLAMA_FALLBACK_MODEL = os.environ.get("OLLAMA_FALLBACK_MODEL", "gemma4:26b")
CEO_ROUTING_MODE = "cloud_only"
WORKER_ROUTING_MODE = "cloud_only"
OLLAMA_KEEP_ALIVE = "60s"  # V2.13: reduced from 5m to free GPU faster when idle
OLLAMA_NUM_CTX = 8192
MAX_CONTEXT_MESSAGES = 10

# ── Service Ports ─────────────────────────────────────────────────────
PORTS = {
    "backend": 8770,
    "agent_video": 8009,
    "video_worker": 8001,
    "data_worker": 8002,
    "security_worker": 8003,
    "devops_worker": 8004,
    "desktop_chat": 8771,
    "monitor": 8781,
    "nexus_dashboard": 8800,
    "comfyui": 8188,
    "trade_crypto": 8012,
    "vibes_audio": 8999,
    "etsy_store": 8998,
    "visual_design": 8997,
    "publishing_hub": 8996,
    "newsletter_marketing": 8995,
    "audio_studio": 8010,
    "social_distribution": 8011,
    "trading_arena": 8013,
    "ollama": 11434,
}

# ── Voice Agent ──────────────────────────────────────────────────────
VOICE_SILENCE_THRESHOLD = 20000   # RMS amplitude below which audio is silence

# ── GPU / VRAM ────────────────────────────────────────────────────────
VRAM_THRESHOLD_MB = 20_000       # Minimum for local LLM (qwen3:30b)
VRAM_MIN_OLLAMA_GB = 20.0        # alias in GB — import this in llm_router.py
GPU_TEMP_CLOUD_THRESHOLD = 80    # Celsius — above this, route to cloud
GPU_TEMP_PAUSE_THRESHOLD = 85    # Celsius — pause GPU work
GPU_TEMP_RESUME_THRESHOLD = 70   # Celsius — resume GPU work

# Hard pre-flight gate: if free VRAM < this value OR gpu_lock.is_rendering() → force cloud LLM routing.
# Motivare: Wan 2.2 fp8 = 15GB + margin 3GB = 18GB; Ollama qwen3:30b = 15GB → coliziune inevitabilă pe 32GB.
# Override: env VRAM_CLOUD_FALLBACK_MB
VRAM_CLOUD_FALLBACK_THRESHOLD_MB = int(os.environ.get("VRAM_CLOUD_FALLBACK_MB", 18 * 1024))

# ── Video Pipeline (shared subset) ───────────────────────────────────
COMFYUI_BASE_URL = f"http://127.0.0.1:{PORTS['comfyui']}"
MONITOR_BASE_URL = f"http://127.0.0.1:{PORTS['monitor']}"
DEFAULT_RESOLUTION = "1080x1920"
DEFAULT_FPS = 30
DEFAULT_LANGUAGE = os.environ.get("RAZAGENT_LANGUAGE", "ro")

# ── Freelance / Billing ──────────────────────────────────────────────
MAX_PROPOSALS_PER_DAY = 5
BILLING_DB = os.environ.get(
    "BILLING_DB_OVERRIDE",
    str((DATA_DIR / "billing.db").resolve()),
)

# ── Approval Gate ─────────────────────────────────────────────────────
APPROVAL_TIMEOUT_MINUTES = 200
APPROVAL_REMINDER_INTERVAL = 300  # seconds (5 min)

# ── Fal.ai Cloud GPU ─────────────────────────────────────────────────
FAL_SESSION_BUDGET_USD = 5.0
FAL_HARD_LIMIT_USD = 8.0

# ── Trading Safeguards ────────────────────────────────────────────────
MAX_TRADE_SIZE_USD    = 7.0    # max per trade (V11.70)
TRADING_MAX_PER_TRADE = MAX_TRADE_SIZE_USD  # alias — import this in consumers
MAX_DAILY_LOSS_USD    = 20.0   # daily kill-switch threshold
POSITION_CAP_USD      = 50.0   # hard cap total exposure
TRADING_HARD_CAP      = POSITION_CAP_USD    # alias
MAX_PORTFOLIO_RISK    = 0.04   # 4% per position
STOP_LOSS_PCT         = 0.02   # SL mandatory 2%
TAKE_PROFIT_PCT       = 0.04   # TP default 4%

# ── Trading Approval Gate ─────────────────────────────────────────────
TRADING_APPROVAL_TIMEOUT  = 1800  # 30 minutes — timeout = REJECT
TRADING_RETRY_INTERVAL    = 300   # 5 minutes between reminders

# ── API / LLM Budget ─────────────────────────────────────────────────
API_BUDGET_CAP_USD     = 10.0   # hard session cap for all external APIs
API_BUDGET_WARNING_USD = 5.0    # warning threshold
LLM_SESSION_BUDGET_USD = API_BUDGET_CAP_USD   # alias — import this in llm_router.py
LLM_SESSION_WARN_USD   = API_BUDGET_WARNING_USD  # alias

# ── Runtime paths (derived) ───────────────────────────────────────────
VENV_PYTHON  = str(PROJECT_ROOT / "backend" / ".venv" / "Scripts" / "python.exe")
VENV_PYTHONW = str(PROJECT_ROOT / "backend" / ".venv" / "Scripts" / "pythonw.exe")
AUDIT_DB     = DATA_DIR / "audit_logs.db"
AGENT_DB     = DATA_DIR / "databases" / "agent.db"
CHROMA_PATH  = str(DATA_DIR / "chroma")

# ── Tailscale VPN (Etherphase A2A) ───────────────────────────────────
GODCLAW_TAILSCALE_IP = os.environ.get(
    "GODCLAW_TAILSCALE_IP", "100.x.x.x"  # TODO: Răzvan sets his PC Tailscale IP here
)
KIMICLAW_TAILSCALE_IP = os.environ.get(
    "KIMICLAW_TAILSCALE_IP", "100.x.x.x"  # TODO: Laptop Tailscale IP
)
ETHERPHASE_PORT = PORTS["backend"]  # 8770 — same as CEO Backend


# ── Strategy Hot-Reload (V1.6.4) ────────────────────────────────────
# God-Mode Strategy Tuner — JSON config reloaded on every cycle execution.
# File: shared/strategy_config.json
# Updated via: POST /api/strategy/update (NEXUS Dashboard)

STRATEGY_CONFIG_PATH = PROJECT_ROOT / "shared" / "strategy_config.json"

_strategy_cache: dict = {}
_strategy_mtime: float = 0.0

STRATEGY_DEFAULTS = {
    "creativity": 0.7,
    "profit_weight": 0.8,
    "max_gpu_load": 80,
    "post_frequency_hours": 12,
    "growth_threshold_pct": 50,
    "max_replies_per_cycle": 15,
    "safety_unlisted_uploads": 10,
    "trend_scout_boost_pct": 15,
}


def load_strategy() -> dict:
    """Load strategy config with file-mtime caching.

    Reloads from disk ONLY when the file has been modified since last read.
    Returns a dict of strategy values (defaults merged with file values).
    Hot-reload safe — called on every watchdog cycle execution.
    """
    global _strategy_cache, _strategy_mtime

    try:
        if STRATEGY_CONFIG_PATH.exists():
            mtime = STRATEGY_CONFIG_PATH.stat().st_mtime
            if mtime != _strategy_mtime:
                with open(STRATEGY_CONFIG_PATH, "r", encoding="utf-8", errors="replace") as f:
                    data = json.load(f)
                _strategy_cache = {**STRATEGY_DEFAULTS, **data}
                _strategy_mtime = mtime
            return _strategy_cache.copy()
    except Exception:
        pass

    return STRATEGY_DEFAULTS.copy()


def save_strategy(values: dict) -> bool:
    """Save strategy config to disk (called by /api/strategy/update).

    Merges provided values with existing config (partial updates OK).
    Returns True on success.
    """
    global _strategy_cache, _strategy_mtime
    try:
        current = load_strategy()
        # Only update known keys (ignore unknown)
        for k in STRATEGY_DEFAULTS:
            if k in values:
                current[k] = values[k]

        current["_version"] = "1.6.4"
        current["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        with open(STRATEGY_CONFIG_PATH, "w", encoding="utf-8", errors="replace") as f:
            json.dump(current, f, indent=2)

        _strategy_cache = current
        _strategy_mtime = STRATEGY_CONFIG_PATH.stat().st_mtime
        return True
    except Exception:
        return False


def get_strategy_value(key: str, default=None):
    """Get a single strategy value (convenience helper)."""
    cfg = load_strategy()
    return cfg.get(key, STRATEGY_DEFAULTS.get(key, default))


# ── OpenClaw Laptop Deployment (Kimi 2.5 Strategist) ────────────────
# Role-based config: PC = Executor (GPU), Laptop = Strategist (Cloud LLM).
# Activated via: set RAZAGENT_ROLE=strategist (or ACTIVATE_OPENCLAW_LAPTOP.bat)

RAZAGENT_ROLE = os.environ.get("RAZAGENT_ROLE", "executor")  # executor | strategist

LAPTOP_SOP = {
    # 12h cycle: laptop pushes trend directives → PC pulls and renders
    "WATCHDOG_STRATEGY_SYNC": {
        "enabled": RAZAGENT_ROLE == "strategist",
        "interval_hours": 12,
        "description": "Analyze long-context trend data via Kimi 2.5, "
                       "generate niche directives, push to shared repo",
        "tasks": [
            "scan_micro_niches",       # CoinGecko + Google Trends deep analysis
            "generate_trend_report",   # 10-page niche opportunity report
            "push_directives_to_pc",   # git commit + push strategy_directives.json
        ],
    },
    # 24h cycle: audit billing + affiliate for revenue optimization
    "MONETIZATION_AUDIT": {
        "enabled": RAZAGENT_ROLE == "strategist",
        "interval_hours": 24,
        "description": "Scan billing.db + affiliate_links.db for optimization "
                       "opportunities (pricing, product swaps, dead links)",
        "tasks": [
            "audit_affiliate_ctr",     # Flag links with 0 clicks after 7 days
            "suggest_product_swaps",   # Higher-commission alternatives via LLM
            "audit_billing_churn",     # Dunning effectiveness + recovery rate
            "generate_pricing_memo",   # Kimi 2.5 analyzes competitor pricing
        ],
    },
}

# PC vs Laptop capability matrix
DEVICE_CAPABILITIES = {
    "executor": {
        "gpu_rendering": True,
        "local_llm": True,
        "trading_bot": True,
        "video_pipeline": True,
        "cloud_llm": True,       # fallback
    },
    "strategist": {
        "gpu_rendering": False,
        "local_llm": False,
        "trading_bot": False,    # read-only audit
        "video_pipeline": False,
        "cloud_llm": True,       # primary (Kimi 2.5)
        "code_review": True,
        "niche_analysis": True,
        "marketing_copy": True,
    },
}


def is_strategist() -> bool:
    """Check if this instance runs as Strategist (laptop)."""
    return RAZAGENT_ROLE == "strategist"


def get_device_caps() -> dict:
    """Get capability flags for current device role."""
    return DEVICE_CAPABILITIES.get(RAZAGENT_ROLE, DEVICE_CAPABILITIES["executor"])
