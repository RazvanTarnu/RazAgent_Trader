# PLATFORM_HANDOFF.md

**Agent 1 — Platform Architect**  
**Model:** DeepSeek V4 Flash  
**Date:** 2026-08-17  
**Branch:** `cursor/platform-foundation-b60c`

---

## 1. Architecture Overview

RazAgent_Trader platform foundation establishes stable boundaries between:

| Layer | Owner | Consumes |
|-------|-------|----------|
| Platform (Agent 1) | Config, providers, metrics, lifecycle | Keyring, YAML config |
| Quant Engine (Agent 2) | Signals, swarm, backtesting | `MarketDataProvider`, `LLMProvider.recommend()` |
| Security/QA (Agent 3) | Risk, approval, kill switch | `ExchangeProvider` via approval gate only |

### Execution flow (mandatory)

```
LLM (structured recommendation)
  ↓
Quant Engine (Agent 2)
  ↓
Risk Engine (Agent 3)
  ↓
Approval Gate
  ↓
ExchangeProvider adapter
```

The LLM **never** receives direct authority to execute exchange orders.

### Repository layout

```
crypto_bot/           # Telegram bot entry (existing)
skills/               # Legacy trading intelligence (Agent 2 territory)
shared/
  platform/           # Config, interfaces, lifecycle, metrics state
  providers/          # LLM + exchange adapters
  market_data/        # Read-only market data wrapper
  persistence/        # TradeRepository implementation
  events/             # EventLogger implementation
config/
  default.yaml        # Source-controlled defaults (no secrets)
  laptop.yaml.example # Host template
  MIGRATION.md        # Migration from GodClaw
metrics_server.py     # Read-only FastAPI :9100
scripts/
  start_trader.ps1    # Validated startup launcher
  validate_platform.py
tests/platform/       # Unit + contract tests
docs/OPERATOR.md      # Developer/operator guide
```

---

## 2. Interfaces

All defined in `shared/platform/interfaces.py`:

| Interface | Purpose | Production impl |
|-----------|---------|-----------------|
| `LLMProvider` | Research/recommendations only | `OpenRouterProvider` |
| `ExchangeProvider` | Normalized exchange API | `BinanceAdapter`, `KuCoinAdapter` |
| `MarketDataProvider` | Read-only market data | `ExchangeMarketDataProvider` |
| `TradeRepository` | Trade persistence | `SQLiteTradeRepository` |
| `EventLogger` | Audit trail | `SQLiteEventLogger` |
| `MetricsProvider` | Read-only metrics snapshot | `MetricsState` |

### ExchangeProvider contract

All adapters implement:

- `get_balances()`
- `get_ticker(symbol)`
- `get_order_book(symbol, depth=20)`
- `get_ohlcv(symbol, timeframe, limit=100)`
- `place_order(OrderRequest)` — blocked in paper mode (returns synthetic paper ID)
- `cancel_order(symbol, order_id)`
- `get_order(symbol, order_id)`
- `get_open_orders(symbol=None)`
- `test_connection()`

Exchange-specific quirks (signing, timestamps, rate limits) stay inside adapters.

### LLMProvider contract

- `complete(messages)` — raw text completion
- `recommend(context)` — returns `LLMRecommendation` (structured JSON)
- `health_check()` — connectivity probe

Production default: **OpenRouter → `moonshotai/kimi-k2.6`**

Dormant: **Direct Moonshot** (`MoonshotProvider`) — requires explicit `llm.provider=moonshot` + `llm.moonshot_enabled=true`.

---

## 3. Configuration Contract

### Precedence (low → high)

1. `config/default.yaml`
2. `config/{environment}.yaml`
3. `config/laptop.yaml` (gitignored)
4. Environment variables (`PAPER_MODE`, `RAZAGENT_ENV`, `LLM_MODEL`, etc.)
5. Keyring secrets (via `shared/platform/secrets.py`)

### Safety invariants

| Setting | Default | Rule |
|---------|---------|------|
| `safety.paper_mode` | `true` | Must default to paper |
| `safety.auto_live` | `false` | Startup validation rejects `true` |
| Secrets in YAML | forbidden | Validation + `.gitignore` |

### Keyring keys

| Key | Required for |
|-----|--------------|
| `OPENROUTER_API_KEY` | LLM (production) |
| `MOONSHOT_API_KEY` | Direct Moonshot (dormant) |
| `TAILSCALE_METRIC_TOKEN` | Metrics auth |
| `BINANCE_API_KEY/SECRET` | Binance live |
| `KUCOIN_API_KEY/SECRET/PASSPHRASE` | KuCoin live |
| `TRADE_CRYPTO_BOT_TOKEN` | Telegram bot |

Loader: `shared/keyring_loader.py` (service `AgentCeoR` with alias support)

---

## 4. Provider Contract

### OpenRouter (production)

```python
from shared.platform.config import load_platform_config
from shared.providers.llm.factory import create_llm_provider

config = load_platform_config()
llm = create_llm_provider(config)
rec = await llm.recommend({"symbol": "BTC/USDT", "features": {...}})
# rec is LLMRecommendation — NOT an order
```

### Moonshot direct (dormant)

Factory raises unless both:
- `config.llm.provider == "moonshot"`
- `config.llm.moonshot_enabled == true`

### Exchange adapters

```python
from shared.providers.exchange.factory import create_exchange_adapters

adapters = create_exchange_adapters(config)
binance = adapters["binance"]
ticker = await binance.get_ticker("BTC/USDT")
```

Paper mode: `place_order()` returns synthetic `paper-*` order IDs without hitting the exchange.

---

## 5. Exchange Contract

### Normalized types

- `Balance`, `Ticker`, `OrderBook`, `OHLCVBar`, `OrderRequest`, `OrderResult`

### Security

- Zero-withdrawal guard: `validate_url_safety()` blocks withdraw/transfer endpoints
- No secrets in logs: `shared/log_filter.py` + `safe_exception_message()`
- Invalid credentials in live mode: `place_order()` returns `success=False`, does not fake success

### Retry behavior

- Transient errors (5xx, timeout): exponential backoff, max 3 attempts
- Fatal errors (401, 403, 404, 422): fail fast, no retry

---

## 6. Startup Contract

`scripts/start_trader.ps1` steps:

1. Stop stale processes
2. Verify Python 3
3. Verify dependencies (`httpx`, `yaml`, `fastapi`, `uvicorn`, `ccxt`)
4. Run `scripts/validate_platform.py` — **fail cleanly on error**
5. Start `metrics_server.py` on port 9100
6. Start `crypto_bot/trade_crypto_bot.py` only if `TRADE_CRYPTO_BOT_TOKEN` present

**Does not auto-enter LIVE mode.** `PAPER_MODE` defaults to `true`.

### Metrics server (read-only)

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /healthz` | Bearer + IP allowlist | Health + paper_mode |
| `GET /readyz` | Bearer + IP allowlist | Readiness probe |
| `GET /metrics` | Bearer + IP allowlist | Full metrics snapshot |

No POST/PUT/PATCH/DELETE endpoints exist.

Metrics exposed: health, readiness, provider status, exchange connectivity, last market-data timestamp, last successful model call, paper/live mode, process state.

---

## 7. Test Results

```
37 passed in 2.64s
```

### Test categories

| Category | File | Count |
|----------|------|-------|
| Config parsing | `test_config.py` | 6 |
| Missing secrets | `test_secrets.py` | 4 |
| Provider initialization | `test_llm_providers.py` | 7 |
| Exchange normalization | `test_exchange_adapters.py` | 10 |
| Invalid credentials | `test_exchange_adapters.py` | included |
| Malformed responses | `test_llm_providers.py` | included |
| Timeout/retry | `test_exchange_adapters.py`, `test_llm_providers.py` | included |
| Metrics read-only | `test_metrics_startup.py` | 3 |
| Startup safety | `test_metrics_startup.py` | 3 |
| PAPER_MODE default | `test_config.py`, `test_metrics_startup.py` | included |
| Market data quality | `test_market_data.py` | 2 |
| Persistence/events | `test_persistence_events.py` | 2 |

### Test separation

| Type | Requires real creds? | Location |
|------|---------------------|----------|
| Unit tests | No | `tests/platform/` |
| Mock contract tests | No | `tests/platform/test_*` |
| Live integration tests | Yes | **Not included** — add under `tests/integration/` with `@pytest.mark.live` when creds available |

No fake success paths: live failures return `success=False` or raise explicitly.

Run: `python3 -m pytest tests/ -v`

---

## 8. Files Changed / Created

### New files

| Path | Purpose |
|------|---------|
| `shared/platform/interfaces.py` | Architectural ABCs + dataclasses |
| `shared/platform/config.py` | YAML config loader + validation |
| `shared/platform/secrets.py` | Keyring wrapper + sanitization |
| `shared/platform/lifecycle.py` | Startup validation + init |
| `shared/platform/metrics_state.py` | Internal metrics registry |
| `shared/providers/llm/openrouter.py` | OpenRouter provider |
| `shared/providers/llm/moonshot.py` | Dormant Moonshot provider |
| `shared/providers/llm/factory.py` | LLM factory |
| `shared/providers/exchange/base.py` | Parsing, retry, security |
| `shared/providers/exchange/binance.py` | Binance adapter |
| `shared/providers/exchange/kucoin.py` | KuCoin adapter |
| `shared/providers/exchange/factory.py` | Exchange factory |
| `shared/market_data/provider.py` | MarketDataProvider impl |
| `shared/persistence/trade_repository.py` | SQLite TradeRepository |
| `shared/events/event_logger.py` | SQLite EventLogger |
| `metrics_server.py` | Read-only metrics HTTP server |
| `config/default.yaml` | Default platform config |
| `config/laptop.yaml.example` | Host config template |
| `config/MIGRATION.md` | Migration documentation |
| `docs/OPERATOR.md` | Developer/operator guide |
| `scripts/validate_platform.py` | Startup validation CLI |
| `tests/platform/*.py` | 37 platform tests |
| `pytest.ini` | Pytest configuration |
| `PLATFORM_HANDOFF.md` | This document |

### Modified files

| Path | Change |
|------|--------|
| `scripts/start_trader.ps1` | Added validation, dependency check, dynamic root |
| `requirements.txt` | Added pytest, pytest-asyncio |
| `pyproject.toml` | Extended package list |

### Unchanged (legacy — Agent 2/3 integration points)

| Path | Notes |
|------|-------|
| `skills/trading_intelligence/exchanges/` | Legacy executors — migrate to new adapters |
| `shared/approval_gate.py` | Agent 3 — wire to `ExchangeProvider` |
| `shared/drawdown_guard.py` | Agent 3 — unchanged |
| `crypto_bot/trade_crypto_bot.py` | Bot entry — consume platform on init |

---

## 9. Integration Points for Agent 2 (Quant Engine)

### Consume these interfaces

```python
from shared.platform.config import load_platform_config
from shared.providers.exchange.factory import create_exchange_adapters
from shared.market_data.provider import ExchangeMarketDataProvider
from shared.providers.llm.factory import create_llm_provider

config = load_platform_config()
adapters = create_exchange_adapters(config)
market = ExchangeMarketDataProvider(adapters[config.exchanges.default_exchange])
llm = create_llm_provider(config)

# OHLCV with quality metadata
ohlcv_point = await market.fetch_ohlcv("BTC/USDT", "1h")
if ohlcv_point.quality != DataQuality.OK:
    reject_stale_data()

# Swarm agents call llm.recommend() — NOT place_order()
rec = await llm.recommend(swarm_context)
```

### Agent 2 must NOT

- Import `ccxt`, `httpx` for exchange calls directly
- Access keyring for exchange secrets
- Call `place_order()` — that flows through Agent 3 approval gate

### Deliverable expected from Agent 2

`QUANT_ENGINE_HANDOFF.md` covering swarm protocol, features, backtesting, integration with these interfaces.

---

## 10. Integration Points for Agent 3 (Security/QA)

### Consume these interfaces

```python
from shared.providers.exchange.factory import create_exchange_adapters
from shared.events.event_logger import SQLiteEventLogger
from shared.persistence.trade_repository import SQLiteTradeRepository

# Execution ONLY after approval gate
adapters = create_exchange_adapters(config)
result = await adapters["binance"].place_order(approved_request)

# Audit every action
events.log_event(AuditEvent(...))
repo.save_trade(TradeRecord(..., paper_mode=config.is_paper_mode))
```

### Agent 3 owns

- Risk thresholds, kill switch, daily loss limits
- Approval gate logic (`shared/approval_gate.py`, `shared/trading_approval_gate.py`)
- LIVE mode activation (PIN gate in `shared/patches/trading_activate.py`)

### Wire approval gate to platform

Replace direct `BinanceExecutor`/`KuCoinExecutor` calls with `ExchangeProvider` from factory. Pass `paper_mode` from `PlatformConfig.safety.paper_mode`.

### Metrics supervision

PC supervisor polls read-only endpoints — Agent 3 must not add mutation endpoints to `metrics_server.py`.

---

## 11. Known Limitations

1. **Live integration tests not included** — require real exchange/API credentials on laptop
2. **Legacy executors coexist** — `skills/trading_intelligence/exchanges/` not yet migrated
3. **Moonshot direct dormant** — OpenRouter is production path
4. **Keyring is Windows-centric** — Docker mode uses env vars (`RAZAGENT_DOCKER`)
5. **FastAPI lifespan** — uses deprecated `@app.on_event("startup")` (functional, warning only)

---

## 12. Next Steps

1. **Agent 2:** Build `trading_intelligence/` on `MarketDataProvider` + `LLMProvider.recommend()`
2. **Agent 3:** Wire approval gate to `ExchangeProvider`; enforce paper/live gate
3. **Platform:** Migrate legacy executors to adapters; add `@pytest.mark.live` integration suite on laptop
