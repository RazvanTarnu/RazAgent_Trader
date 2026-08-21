# QUANT_ENGINE_HANDOFF.md

**Agent 2 — Quant / Trading Intelligence Engineer**  
**Model:** Composer 2.5  
**Date:** 2026-08-17  
**Branch:** `cursor/quant-engine-b60c`

---

## 1. Architecture Overview

The quant engine produces **research outputs and structured recommendations only**. It never executes exchange orders, accesses exchange credentials, or bypasses the risk/approval chain.

### Mandatory execution flow

```
Market Data (MarketDataProvider)
  ↓
Feature Engineering
  ↓
Market Regime Classification
  ↓
Technical Signal Engine
  ↓
3-Agent Crypto Swarm (LLMProvider.recommend)
  ↓
Signal Aggregator
  ↓
PortfolioRecommendation
  ↓
Risk Engine (Agent 3)
  ↓
Approval Gate
  ↓
ExchangeProvider.place_order
```

### Repository layout

```
trading_intelligence/           # NEW — platform-backed quant engine
  data/providers/               # CoinGecko + composite fallback
  features/                     # Technical feature pipeline
  regime/                       # Rule-based regime classifier
  swarm/                        # 3-agent crypto swarm
  signals/                      # QuantSignal, aggregator
  backtest/                     # Bar-by-bar + walk-forward
  pipeline/                     # ResearchCycle orchestrator
skills/trading_intelligence/    # LEGACY — migrate gradually
skills/crypto_swarm/            # LEGACY — Telegram tool wrappers
tests/quant/                    # Unit + mock contract tests
```

---

## 2. Strategies Implemented

| Strategy component | Module | Description |
|--------------------|--------|-------------|
| Technical momentum | `features/technical.py` | RSI, MA crossover, momentum, volatility |
| Regime-aware signals | `regime/classifier.py` | Trend/vol/liquidity regime labels |
| Rule-based entries | `signals/aggregator.py` | Deterministic signals from features + regime |
| 3-agent swarm | `swarm/coordinator.py` | Fundamentals + Technical + Risk Analysis |
| Research cycle | `pipeline/cycle.py` | End-to-end symbol analysis |

Initial strategy version: **1.0.0** (rule-based technical + LLM swarm overlay).

---

## 3. Features

Implemented in `trading_intelligence/features/technical.py`:

| Feature | Justification |
|---------|---------------|
| `return_1` | Short-term price change |
| `volatility_20` | Risk sizing / regime detection |
| `atr_14` | Stop placement reference |
| `rsi_14` | Mean-reversion / overbought-oversold |
| `ma_7`, `ma_25`, `ma_50`, `ma_200` | Trend structure |
| `momentum_10` | Directional bias |
| `volume_anomaly` | Unusual activity detection |
| `ma_crossover` | Trend change signal |
| `trend_regime`, `volatility_regime` | Regime inputs |
| `spread_pct`, `order_book_imbalance` | Liquidity (when ticker/book available) |

Every `MarketDataPoint` carries: timestamp, source, symbol, timeframe, quality status.

---

## 4. Data Providers

| Provider | Module | Credentials | Purpose |
|----------|--------|-------------|---------|
| Exchange (primary) | `shared/market_data/provider.py` | Via Agent 1 platform | OHLCV, ticker from Binance/KuCoin |
| CoinGecko (fallback) | `data/providers/coingecko.py` | None (public API) | Market context when exchange unavailable |
| Composite | `data/providers/composite.py` | None | Primary → secondary fallback |

**Not yet implemented:** Coinbase, Glassnode, macro/sentiment/on-chain dedicated adapters. Legacy `skills/trading_intelligence/data_collector.py` still provides CoinGecko/Fear&Greed for the Telegram bot until migrated.

---

## 5. Crypto Swarm Protocol

### Agents (logically independent)

| Agent | Weight | Role |
|-------|--------|------|
| `fundamentals` | 30% | Macro, news context, tokenomics |
| `technical` | 40% | Price action, indicators from features |
| `risk_analysis` | 30% | Downside scenarios, conflicts — **advisory only** |

### Structured agent output

Each agent returns `AgentOutput`:

```python
{
  "agent": "fundamentals|technical|risk_analysis",
  "thesis": str,
  "signals": [{"direction", "strength", "confidence", "time_horizon",
               "entry_rationale", "invalidation", "required_market_conditions"}],
  "evidence": [str],
  "confidence": float,
  "invalidation_conditions": [str],
  "timeframe": str,
  "risks": [str]
}
```

### Flow

1. Fundamentals + Technical run **in parallel** via `LLMProvider.recommend()`
2. Risk Analysis receives peer outputs as context
3. `SignalAggregator` computes weighted consensus (30/40/30)

Prompt version: `swarm-v1`

---

## 6. Signal Contract

Every `QuantSignal` specifies:

- `direction` — BUY | SELL | HOLD
- `strength` — 0.0–1.0
- `confidence` — 0.0–1.0
- `time_horizon`
- `entry_rationale`
- `invalidation`
- `required_market_conditions`

`PortfolioRecommendation` is the handoff object to Agent 3 — **not an order**.

---

## 7. Backtesting Methodology

### Bar-by-bar engine (`backtest/engine.py`)

- Uses only `bars[:i+1]` at each step — **no look-ahead**
- Rejects insufficient history (`min_bars=30`)
- Enforces minimum trade count for validity
- Reports via `BacktestMetrics`: CAGR, Sharpe, Sortino, max drawdown, win rate, profit factor, turnover, exposure, tail loss

### Walk-forward (`backtest/walk_forward.py`)

- Rolling train/test windows with explicit separation
- Minimum fold count enforcement
- Overfitting check: flags when OOS Sharpe << IS Sharpe
- `robust=False` when insufficient data or overfitting detected

### Safeguards

- No future leakage (bar window truncation)
- Minimum trade count per fold
- Out-of-sample evaluation required for `robust=True`
- Parameter constraints via fixed rule-based strategy (no optimization loop yet)

---

## 8. Known Limitations

1. **Legacy code coexists** — `skills/trading_intelligence/` and `skills/crypto_swarm/` still use direct httpx/ccxt/Ollama
2. **No live LLM integration tests** — swarm tests use `MockLLM`
3. **CoinGecko OHLCV has no volume** — volume features may be zero for fallback data
4. **Single strategy** — rule-based technical + swarm overlay; no portfolio optimization
5. **Alternative data** — Glassnode, macro, sentiment providers are interface-ready but not implemented
6. **Windows paths in legacy** — new `trading_intelligence/` uses portable paths only

---

## 9. Test Results

```
52 passed in 2.51s
```

| Category | File | Count |
|----------|------|-------|
| Feature engineering | `tests/quant/test_quant_engine.py` | 4 |
| Regime classification | included | 1 |
| Signal aggregation | included | 1 |
| Swarm (mock LLM) | included | 2 |
| Backtest no-lookahead | included | 2 |
| Walk-forward | included | 2 |
| Research cycle | included | 3 |
| Platform (Agent 1) | `tests/platform/` | 37 |

### Test separation

| Type | Requires real creds? | Location |
|------|---------------------|----------|
| Unit tests | No | `tests/quant/` |
| Mock contract tests | No | `tests/quant/` (MockLLM, MockMarketData) |
| Live integration tests | Yes | **Not included** — add `@pytest.mark.live` on laptop |

Run: `python3 -m pytest tests/ -v`

---

## 10. Files Changed / Created

### New files

| Path | Purpose |
|------|---------|
| `trading_intelligence/__init__.py` | Package exports |
| `trading_intelligence/signals/models.py` | QuantSignal, AgentOutput, PortfolioRecommendation |
| `trading_intelligence/signals/aggregator.py` | Signal aggregation |
| `trading_intelligence/features/technical.py` | Feature computation |
| `trading_intelligence/features/pipeline.py` | MarketDataPoint → FeatureVector |
| `trading_intelligence/regime/classifier.py` | Regime detection |
| `trading_intelligence/data/providers/coingecko.py` | Public CoinGecko provider |
| `trading_intelligence/data/providers/composite.py` | Fallback composite |
| `trading_intelligence/swarm/protocol.py` | Swarm message schema |
| `trading_intelligence/swarm/agents.py` | Agent runners |
| `trading_intelligence/swarm/coordinator.py` | 3-agent coordinator |
| `trading_intelligence/backtest/engine.py` | Bar-by-bar backtest |
| `trading_intelligence/backtest/walk_forward.py` | Walk-forward evaluator |
| `trading_intelligence/backtest/metrics.py` | Performance metrics |
| `trading_intelligence/pipeline/cycle.py` | ResearchCycle |
| `tests/quant/test_quant_engine.py` | 15 quant tests |
| `QUANT_ENGINE_HANDOFF.md` | This document |

### Modified files

| Path | Change |
|------|--------|
| `pyproject.toml` | Added `trading_intelligence` package |

### Unchanged (legacy)

| Path | Notes |
|------|-------|
| `skills/trading_intelligence/*` | Legacy orchestrator — delegate to new package over time |
| `skills/crypto_swarm/*` | Telegram tools — wrap `SwarmCoordinator` when ready |

---

## 11. Integration Contract with Agent 1 (Platform)

### Consume

```python
from shared.platform.config import load_platform_config
from shared.platform.interfaces import DataQuality
from shared.market_data.provider import ExchangeMarketDataProvider
from shared.providers.exchange.factory import create_exchange_adapters
from shared.providers.llm.factory import create_llm_provider
from trading_intelligence.data.providers.coingecko import CoinGeckoProvider
from trading_intelligence.data.providers.composite import CompositeMarketDataProvider
from trading_intelligence.pipeline.cycle import ResearchCycle

config = load_platform_config()
adapters = create_exchange_adapters(config)
exchange_md = ExchangeMarketDataProvider(adapters[config.exchanges.default_exchange])
market = CompositeMarketDataProvider(exchange_md, CoinGeckoProvider())
llm = create_llm_provider(config)

cycle = ResearchCycle(market, llm)
recommendation = await cycle.run("BTC/USDT")
# recommendation is PortfolioRecommendation — pass to Agent 3
```

### Must NOT

- Call `ExchangeProvider.place_order()`
- Import keyring or read exchange secrets
- Disable or bypass approval gate
- Switch `PAPER_MODE`

---

## 12. Integration Contract with Agent 3 (Security/QA)

### Handoff object

Agent 2 delivers `PortfolioRecommendation`:

```python
@dataclass
class PortfolioRecommendation:
    symbol: str
    direction: str           # BUY | SELL | HOLD
    confidence: float
    bundle: SignalBundle     # full signal trace
    thesis: str
    invalidation_conditions: list[str]
    risks: list[str]
    timeframe: str
    reproducibility: dict    # data, features, model, strategy versions
    timestamp: datetime
```

### Agent 3 responsibilities

1. Apply deterministic risk rules (`shared/drawdown_guard.py`)
2. Enforce daily loss limits, max trade size, kill switch
3. Route approved orders through `ExchangeProvider.place_order()`
4. Log via `EventLogger` and persist via `TradeRepository`
5. Respect `config.safety.paper_mode` — paper orders return synthetic IDs

### Reproducibility fields

Every recommendation includes:

- `data_source`, `data_quality`, `data_timestamp`
- `feature_version`, `strategy_version`, `prompt_version`
- `provider`, `model`

Agent 3 should persist these in audit events for post-trade review.

---

## 13. Next Steps

1. **Wire Telegram bot** — `crypto_bot/trade_crypto_bot.py` calls `ResearchCycle` instead of legacy Ollama swarm
2. **Migrate legacy skills** — thin wrappers delegating to `trading_intelligence/`
3. **Add live integration tests** — `@pytest.mark.live` with OpenRouter + paper exchange on laptop
4. **Agent 3** — accept `PortfolioRecommendation` in approval gate; wire `ExchangeProvider`
5. **Extend data providers** — Fear&Greed, sentiment, on-chain when credentials available
