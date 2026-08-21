# -*- coding: utf-8 -*-
"""Trading Intelligence Module V1.0 — 3-Hour Cycle Crypto Research & Prediction System.

Modules:
    config              — Constants, DB path, API URLs
    data_collector      — CoinGecko, Fear & Greed, DeFi TVL fetchers
    news_aggregator     — CryptoCompare, CryptoPanic, RSS news feeds
    sentiment_analyzer  — Ollama-based news impact scoring
    technical_analyzer  — RSI, MA crossover, support/resistance
    prediction_engine   — LLM-powered directional predictions
    trade_suggester     — Confidence-filtered trade suggestions
    trade_executor      — SPOT market execution via exchange_connector
    report_formatter    — Telegram HTML report builder
    orchestrator        — 3-hour cycle scheduler
"""

from .orchestrator import TradingIntelligenceOrchestrator

__all__ = ["TradingIntelligenceOrchestrator"]
