# -*- coding: utf-8 -*-
"""Trading Intelligence V1.0 — Prediction Engine.

Uses Ollama LLM to generate directional predictions per coin by combining:
  - Market data (price, volume, 24h change)
  - Scored news sentiment
  - Technical indicators (RSI, MA, S/R)
  - Fear & Greed index

Saves all predictions to SQLite for historical tracking and accuracy evaluation.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import (
    OLLAMA_GENERATE,
    OLLAMA_MODEL,
    DB_PATH,
)

logger = logging.getLogger("TradingIntelligence")

# ---------------------------------------------------------------------------
# Prediction prompt
# ---------------------------------------------------------------------------
_PREDICTION_PROMPT = """You are the autonomous Chief Trading Officer (CTO) of RazAgent Enterprise.
You have FULL EXECUTION RIGHTS on Binance and KuCoin Spot markets.
Your goal: maximum compounding profit within the $7/trade hard limit.
UNDER NO CIRCUMSTANCES can you withdraw or transfer funds out of the exchange.
Your sole domain is Spot Trading to accumulate capital.

AVAILABLE CAPITAL:
{wallet_context}

Based on the data below, generate a price prediction for {coin_name} ({symbol}).

MARKET DATA:
- Current Price: ${current_price:,.2f}
- 24h Change: {change_24h:+.2f}%
- 24h Volume: ${volume:,.0f}
- Market Cap Rank: #{rank}

TECHNICAL INDICATORS:
- RSI (14): {rsi}
- MA Signal: {ma_signal}
- MA Short ({ma_short_period}d): {ma_short}
- MA Long ({ma_long_period}d): {ma_long}
- 7d Support: ${support:,.2f}
- 7d Resistance: ${resistance:,.2f}
- Trend: {trend}

NEWS SENTIMENT:
{news_context}

FEAR & GREED INDEX: {fear_greed_value} ({fear_greed_class})

AGGREGATE MARKET SENTIMENT: {aggregate_sentiment}

{macro_price_action}

Generate predictions for TWO timeframes: 24h and 72h.
For each, provide direction, confidence (0-100), and a target price range.

Respond ONLY in this exact JSON format (no markdown):
{{
  "predictions": [
    {{
      "timeframe": "24h",
      "direction": "BULLISH|BEARISH|NEUTRAL",
      "confidence": 0-100,
      "target_low": <float>,
      "target_high": <float>,
      "reasoning": "<1-2 sentences>"
    }},
    {{
      "timeframe": "72h",
      "direction": "BULLISH|BEARISH|NEUTRAL",
      "confidence": 0-100,
      "target_low": <float>,
      "target_high": <float>,
      "reasoning": "<1-2 sentences>"
    }}
  ]
}}"""


def _build_news_context(scored_news: list[dict], coin_id: str) -> str:
    """Build news context string relevant to a specific coin."""
    relevant = []
    general_high = []

    for item in scored_news:
        coins = item.get("coins_mentioned", [])
        impact = item.get("impact", "LOW")
        sentiment = item.get("sentiment", "NEUTRAL")

        if coin_id in coins:
            relevant.append(f"- [{impact}] [{sentiment}] {item.get('title', '')[:80]}")
        elif impact == "HIGH":
            general_high.append(f"- [{sentiment}] {item.get('title', '')[:80]}")

    lines = []
    if relevant:
        lines.append(f"Directly relevant ({len(relevant)}):")
        lines.extend(relevant[:5])
    if general_high:
        lines.append(f"High-impact general ({len(general_high)}):")
        lines.extend(general_high[:3])
    if not lines:
        lines.append("No significant news for this coin.")

    return "\n".join(lines)


def _parse_llm_json(raw: str) -> dict:
    """Parse JSON from LLM response, handling think tags, code fences, and dirty output.

    V1.8.4: Added <think>...</think> tag stripping for qwen3 thinking mode.
    """
    import re as _re
    text = raw.strip()

    # V1.8.4: Strip <think>...</think> tags (qwen3 thinking mode)
    text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()

    if not text:
        logger.warning("LLM returned empty content after think-tag stripping")
        return {}

    # Try direct parse first
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try extracting from code fences
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                try:
                    return json.loads(part)
                except Exception:
                    pass

    # Fallback: extract first JSON object via regex
    match = _re.search(r'\{.*\}', text, _re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass

    logger.warning("JSON parse failed, raw: %s", text[:150])
    return {}


# ---------------------------------------------------------------------------
# Generate predictions for all coins
# ---------------------------------------------------------------------------
async def generate_predictions(
    market_data: list[dict],
    scored_news: list[dict],
    technical: list[dict],
    fear_greed: dict,
    aggregate_sent: dict | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Generate LLM-powered predictions for all coins.

    Args:
        market_data: From data_collector.fetch_top_10_coins()
        scored_news: From sentiment_analyzer.score_news()
        technical: From technical_analyzer.analyze_technical()
        fear_greed: From data_collector.fetch_fear_greed()
        aggregate_sent: From sentiment_analyzer.aggregate_sentiment()

    Returns:
        Flat list of prediction dicts (2 per coin: 24h + 72h).
    """
    if not market_data:
        return []

    # Build coin->technical lookup
    tech_map = {t["coin_id"]: t for t in technical}

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=120)
        close_client = True

    all_predictions: list[dict] = []

    try:
        for coin in market_data:
            coin_id = coin["id"]
            tech = tech_map.get(coin_id, {})

            # V11.70: Inject live wallet balances
            wallet_text = "Not available (exchange API offline)"
            try:
                from .exchanges.exchange_router import ExchangeRouter
                router = ExchangeRouter()
                balances = await router.get_all_balances()
                parts = []
                for exch, bal in balances.items():
                    parts.append(f"{exch.capitalize()}: ${bal:,.2f} USDT")
                if parts:
                    wallet_text = " | ".join(parts) + f"\nMax trade size: $7. Allocate on exchange with sufficient balance."
                else:
                    wallet_text = "No exchange balances available"
            except Exception as _wal_err:
                logger.debug("Wallet data unavailable: %s", _wal_err)

            # V11.20: Inject macro price action context (SMA50/200, 2yr high/low)
            macro_text = ""
            try:
                from .price_action_analyzer import get_macro_summary, format_macro_for_prompt
                # Map CoinGecko coin_id to Binance symbol
                _symbol = coin.get("symbol", "").upper() + "USDT"
                macro = get_macro_summary(_symbol)
                if macro:
                    macro_text = format_macro_for_prompt(macro)
            except Exception as _macro_err:
                logger.debug("Macro data unavailable for %s: %s", coin_id, _macro_err)

            prompt = _PREDICTION_PROMPT.format(
                wallet_context=wallet_text,
                coin_name=coin.get("name", coin_id),
                symbol=coin.get("symbol", ""),
                current_price=coin.get("current_price", 0),
                change_24h=coin.get("price_change_24h", 0) or 0,
                volume=coin.get("total_volume", 0) or 0,
                rank=coin.get("market_cap_rank", 0),
                rsi=tech.get("rsi", "N/A"),
                ma_signal=tech.get("ma_signal", "N/A"),
                ma_short_period=7,
                ma_long_period=25,
                ma_short=f"${tech['ma_short']:,.2f}" if tech.get("ma_short") else "N/A",
                ma_long=f"${tech['ma_long']:,.2f}" if tech.get("ma_long") else "N/A",
                support=tech.get("support", 0),
                resistance=tech.get("resistance", 0),
                trend=tech.get("trend", "UNKNOWN"),
                news_context=_build_news_context(scored_news, coin_id),
                fear_greed_value=fear_greed.get("value", 50),
                fear_greed_class=fear_greed.get("classification", "Neutral"),
                aggregate_sentiment=(
                    f"{aggregate_sent.get('overall_sentiment', 'NEUTRAL')} "
                    f"(confidence={aggregate_sent.get('confidence', 50)}%, "
                    f"risk={aggregate_sent.get('risk_level', 'MEDIUM')})"
                    if aggregate_sent else "N/A"
                ),
                macro_price_action=macro_text or "MACRO DATA: Not yet synced (run sync_historical_ohlcv first)",
            )

            # V1.8.4: VRAM-aware LLM routing with cloud fallback
            raw = ""
            llm_source = "unknown"

            # Check VRAM to decide local vs cloud
            _use_cloud = False
            try:
                from shared.vram_utils import get_vram
                _u, _t, _free, _temp = get_vram()
                if _free < 17000:
                    _use_cloud = True
                    logger.info("[PREDICT] VRAM %dMB free < 17GB — using cloud for %s", _free, coin_id)
            except Exception:
                _use_cloud = True

            if not _use_cloud:
                # Path A: Ollama local with strict 25s timeout + think:false
                try:
                    resp = await client.post(
                        OLLAMA_GENERATE,
                        json={
                            "model": OLLAMA_MODEL,
                            "prompt": prompt,
                            "stream": False,
                            "think": False,
                            "options": {"temperature": 0.3, "num_predict": 500},
                        },
                        timeout=25.0,
                    )
                    if resp.status_code == 200:
                        raw = resp.json().get("response", "")
                        llm_source = f"ollama/{OLLAMA_MODEL}"
                    else:
                        _use_cloud = True
                        logger.warning("[PREDICT] Ollama %d for %s — cloud fallback", resp.status_code, coin_id)
                except Exception as _oll_err:
                    _use_cloud = True
                    logger.warning("[PREDICT] Ollama failed for %s (%s) — cloud fallback", coin_id, _oll_err)

            if _use_cloud or not raw.strip():
                # Path B: Cloud (Kimi K2.6 via OpenRouter) — guaranteed response
                try:
                    import os as _os
                    from shared.keyring_loader import get_credential
                    api_key = get_credential("OPENROUTER_API_KEY")
                    if api_key:
                        cloud_url = _os.environ.get("OLLAMA_CLOUD_URL", "https://openrouter.ai/api/v1")
                        # V1.8.4: Gemini Flash as prediction cloud — Kimi K2.6 returns None content
                        cloud_model = _os.environ.get("PREDICTION_CLOUD_MODEL", "google/gemini-2.0-flash-001")
                        cloud_resp = await client.post(
                            f"{cloud_url}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json",
                                "HTTP-Referer": "https://razagent.ai",
                                "X-Title": "GodClaw Trading Predictions",
                            },
                            json={
                                "model": cloud_model,
                                "messages": [
                                    {"role": "system", "content": "You are a crypto trading analyst. Respond ONLY with valid JSON, no markdown."},
                                    {"role": "user", "content": prompt},
                                ],
                                "temperature": 0.3,
                                "max_tokens": 600,
                            },
                            timeout=30.0,
                        )
                        if cloud_resp.status_code == 200:
                            choices = cloud_resp.json().get("choices", [])
                            if choices:
                                raw = choices[0].get("message", {}).get("content", "")
                                llm_source = f"cloud/{cloud_model}"
                                logger.info("[PREDICT] Cloud prediction for %s via %s", coin_id, cloud_model)
                        else:
                            logger.error("[PREDICT] Cloud returned %d for %s", cloud_resp.status_code, coin_id)
                except Exception as _cloud_err:
                    logger.error("[PREDICT] Cloud failed for %s: %s", coin_id, _cloud_err)

            # Parse prediction JSON
            try:
                parsed = _parse_llm_json(raw)
                preds = parsed.get("predictions", [])

                if not preds:
                    raise ValueError("No predictions in LLM response")

                for pred in preds:
                    direction = pred.get("direction", "NEUTRAL").upper()
                    if direction not in ("BULLISH", "BEARISH", "NEUTRAL"):
                        direction = "NEUTRAL"

                    confidence = max(0, min(100, int(pred.get("confidence", 50))))
                    timeframe = pred.get("timeframe", "24h")
                    if timeframe not in ("24h", "72h"):
                        timeframe = "24h"

                    all_predictions.append({
                        "coin": coin_id,
                        "symbol": coin.get("symbol", ""),
                        "name": coin.get("name", ""),
                        "direction": direction,
                        "confidence": confidence,
                        "timeframe": timeframe,
                        "target_low": float(pred.get("target_low", 0)),
                        "target_high": float(pred.get("target_high", 0)),
                        "reasoning": pred.get("reasoning", ""),
                        "price_at_prediction": coin.get("current_price", 0),
                        "_llm_source": llm_source,
                    })

            except Exception:
                logger.warning("Prediction parse failed for %s (source=%s)", coin_id, llm_source)
                for tf in ("24h", "72h"):
                    all_predictions.append({
                        "coin": coin_id,
                        "symbol": coin.get("symbol", ""),
                        "name": coin.get("name", ""),
                        "direction": "NEUTRAL",
                        "confidence": 30,
                        "timeframe": tf,
                        "target_low": coin.get("current_price", 0) * 0.95,
                        "target_high": coin.get("current_price", 0) * 1.05,
                        "reasoning": f"LLM prediction failed ({llm_source}), defaulting to neutral",
                        "price_at_prediction": coin.get("current_price", 0),
                        "_llm_source": "fallback",
                    })

        # Save all predictions to SQLite
        await _save_predictions(all_predictions)

        # V1.0 SWARM CONSENSUS: High-confidence predictions trigger 3-agent debate
        # Only BULLISH/BEARISH predictions with confidence >= 60% are debated.
        # Swarm must reach >= 75% consensus to propose a trade via Approval Gate.
        try:
            from ..crypto_swarm.trading_swarm import run_swarm_analysis, CONSENSUS_THRESHOLD
            from shared.trading_approval_gate import TradingApprovalGate

            for pred in all_predictions:
                if pred["direction"] in ("BULLISH", "BEARISH") and pred["confidence"] >= 60:
                    coin_symbol = pred.get("symbol", pred["coin"]).upper()
                    news_ctx = pred.get("reasoning", "")
                    tech_ctx = (
                        f"Price: ${pred['price_at_prediction']}, "
                        f"Direction: {pred['direction']}, "
                        f"Confidence: {pred['confidence']}%, "
                        f"Targets: ${pred['target_low']}-${pred['target_high']}"
                    )
                    consensus = await run_swarm_analysis(coin_symbol, news_ctx, tech_ctx)

                    if consensus["confidence"] >= CONSENSUS_THRESHOLD and consensus["direction"] != "NEUTRAL":
                        side = "BUY" if consensus["direction"] == "BUY" else "SELL"
                        # DRY_RUN_LIVE short-circuits the gate: observe decision
                        # path without spamming Telegram with simulated approvals.
                        from shared.dry_run_live import is_active as _dry_run_active, log_simulated_gate_call
                        if _dry_run_active():
                            log_simulated_gate_call(
                                pair=f"{coin_symbol}/USDT",
                                side=side,
                                size_usd=7.0,
                                reason=f"swarm {consensus['confidence']}%",
                            )
                            continue
                        logger.info(
                            f"[SWARM] {coin_symbol} passed consensus ({consensus['confidence']}%) "
                            f"— sending to Trading Approval Gate"
                        )
                        # Trigger approval gate (30-min timeout, REJECT default)
                        try:
                            gate = TradingApprovalGate.instance()
                            await gate.require_approval(
                                pair=f"{coin_symbol}/USDT",
                                side=side,
                                size_usd=7.0,  # MAX_TRADE from rules
                                entry_price=pred["price_at_prediction"],
                                sl_price=pred["target_low"] if side == "BUY" else pred["target_high"],
                                tp_price=pred["target_high"] if side == "BUY" else pred["target_low"],
                                rsi=0,
                                paper_mode=True,
                                metadata={"consensus": consensus},
                            )
                        except Exception as gate_err:
                            logger.warning(f"[SWARM] Approval gate failed for {coin_symbol}: {gate_err}")
                    else:
                        logger.info(
                            f"[SWARM] {coin_symbol} below threshold ({consensus['confidence']}%) — no trade"
                        )
        except ImportError as _imp:
            logger.debug("Trading swarm not available: %s", _imp)
        except Exception as _swarm_err:
            logger.warning("Swarm consensus error (non-fatal): %s", _swarm_err)

        logger.info(
            "Generated %d predictions for %d coins",
            len(all_predictions), len(market_data),
        )
        return all_predictions

    finally:
        if close_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# Persist predictions to SQLite
# ---------------------------------------------------------------------------
async def _save_predictions(predictions: list[dict]) -> None:
    """Save predictions to the database."""
    if not predictions:
        return

    try:
        import aiosqlite

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA journal_mode=WAL")

            for pred in predictions:
                await db.execute(
                    """INSERT INTO predictions
                       (coin, direction, confidence, timeframe, target_low, target_high,
                        reasoning, price_at_prediction)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        pred["coin"],
                        pred["direction"],
                        pred["confidence"],
                        pred["timeframe"],
                        pred["target_low"],
                        pred["target_high"],
                        pred["reasoning"],
                        pred["price_at_prediction"],
                    ),
                )
            await db.commit()
            logger.debug("Saved %d predictions to DB", len(predictions))

    except ImportError:
        logger.warning("aiosqlite not available, predictions not persisted")
    except Exception as exc:
        logger.error("Failed to save predictions: %s", exc)
