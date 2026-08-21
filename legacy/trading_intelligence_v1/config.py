# -*- coding: utf-8 -*-
"""Trading Intelligence V1.0 — Configuration & Constants.

All tunables live here. DB initialization is deferred to first use.
"""
import os
import logging

logger = logging.getLogger("TradingIntelligence")

# ---------------------------------------------------------------------------
# Cycle timing
# ---------------------------------------------------------------------------
CYCLE_HOURS: int = 3
CYCLE_SECONDS: int = CYCLE_HOURS * 3600  # 10 800s

# ---------------------------------------------------------------------------
# Trade safety limits
# ---------------------------------------------------------------------------
MAX_TRADE_AMOUNT_USD: float = 50.0       # HARD LIMIT per trade
MIN_CONFIDENCE_FOR_TRADE: int = 75       # 0-100 scale
MAX_TRADES_PER_CYCLE: int = 3            # never more than 3 trades per 3h

# ---------------------------------------------------------------------------
# Stablecoins (excluded from predictions)
# ---------------------------------------------------------------------------
STABLECOINS: set[str] = {
    "tether", "usd-coin", "dai", "binance-usd", "trueusd",
    "paxos-standard", "frax", "usdd", "first-digital-usd",
}

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATA_DIR: str = os.environ.get("RAZAGENT_DATA", r"D:\RazAgent_Enterprise\data")
DB_PATH: str = os.path.join(DATA_DIR, "trading_intelligence.db")

# ---------------------------------------------------------------------------
# API URLs (all free-tier, no key required unless noted)
# ---------------------------------------------------------------------------
COINGECKO_BASE: str = "https://api.coingecko.com/api/v3"
COINGECKO_MARKETS: str = f"{COINGECKO_BASE}/coins/markets"
COINGECKO_CHART: str = f"{COINGECKO_BASE}/coins/{{coin_id}}/market_chart"

FEAR_GREED_URL: str = "https://api.alternative.me/fng/"
DEFI_TVL_URL: str = "https://api.llama.fi/v2/chains"

CRYPTOCOMPARE_NEWS_URL: str = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN"
CRYPTOPANIC_NEWS_URL: str = "https://cryptopanic.com/api/v1/posts/"

RSS_FEEDS: dict[str, str] = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Decrypt": "https://decrypt.co/feed",
    "TheBlock": "https://www.theblock.co/rss.xml",
}

# ---------------------------------------------------------------------------
# Ollama (local LLM)
# ---------------------------------------------------------------------------
OLLAMA_BASE: str = "http://127.0.0.1:11434"
OLLAMA_GENERATE: str = f"{OLLAMA_BASE}/api/generate"
OLLAMA_MODEL: str = "qwen3:30b-a3b"

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
COINGECKO_RATE_LIMIT_SECONDS: float = 65.0   # V10.48: ~1 req/min (safe for free tier, avoids 429)
HTTP_TIMEOUT_SECONDS: float = 30.0
NEWS_CACHE_TTL_HOURS: int = 6                # dedup window

# ---------------------------------------------------------------------------
# Technical analysis
# ---------------------------------------------------------------------------
RSI_PERIOD: int = 14
MA_SHORT: int = 7
MA_LONG: int = 25
CHART_DAYS: int = 30

# ---------------------------------------------------------------------------
# DB Schema (created on first orchestrator init)
# ---------------------------------------------------------------------------
DB_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    coin            TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('BULLISH','BEARISH','NEUTRAL')),
    confidence      INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    timeframe       TEXT NOT NULL,
    target_low      REAL,
    target_high     REAL,
    reasoning       TEXT,
    price_at_prediction REAL,
    outcome         TEXT,
    price_at_evaluation REAL,
    evaluated_at    TEXT
);

CREATE TABLE IF NOT EXISTS news_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    source      TEXT,
    impact      TEXT CHECK (impact IN ('HIGH','MEDIUM','LOW')),
    sentiment   TEXT,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    coins       TEXT
);

CREATE TABLE IF NOT EXISTS trade_suggestions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    coin            TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('BUY','SELL')),
    amount_usd      REAL NOT NULL CHECK (amount_usd <= 50.0),
    entry_price     REAL,
    stop_loss       REAL,
    take_profit     REAL,
    reasoning       TEXT,
    confidence      INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','APPROVED','REJECTED','EXECUTED','EXPIRED')),
    approved_at     TEXT,
    executed_at     TEXT,
    execution_price REAL,
    order_id        TEXT,
    exchange        TEXT DEFAULT 'binance'
);

CREATE INDEX IF NOT EXISTS idx_predictions_coin   ON predictions(coin, created_at);
CREATE INDEX IF NOT EXISTS idx_news_cache_url     ON news_cache(url);
CREATE INDEX IF NOT EXISTS idx_trade_status       ON trade_suggestions(status, created_at);
"""
