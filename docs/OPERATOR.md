# RazAgent_Trader — Developer & Operator Guide

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\scripts\start_trader.ps1
```

## Architecture

```
LLM (OpenRouter → moonshotai/kimi-k2.6)
  ↓ structured recommendation
Quant Engine (Agent 2)
  ↓
Risk Engine (Agent 3)
  ↓
Approval Gate
  ↓
ExchangeProvider adapter
```

The LLM never executes orders directly.

## Configuration

| Layer | Source | Secrets? |
|-------|--------|----------|
| Defaults | `config/default.yaml` | No |
| Environment | `config/{env}.yaml` | No |
| Host | `config/laptop.yaml` (gitignored) | No |
| Runtime | `PAPER_MODE`, `RAZAGENT_ENV` env vars | No |
| Credentials | Windows keyring (`AgentCeoR` / `RazAgentTrader`) | Yes |

## Key interfaces

Import from `shared.platform`:

- `LLMProvider` — research/recommendations only
- `ExchangeProvider` — normalized exchange API
- `MarketDataProvider` — read-only market data
- `TradeRepository` — trade persistence
- `EventLogger` — audit trail
- `MetricsProvider` — read-only metrics snapshot

## Metrics (read-only)

Supervisor polls from PC:

```
curl -H "Authorization: Bearer $TOKEN" http://192.168.1.137:9100/healthz
curl -H "Authorization: Bearer $TOKEN" http://192.168.1.137:9100/metrics
```

No write endpoints exist on the metrics server.

## Safety defaults

- `PAPER_MODE=true` by default
- `auto_live=false` — startup never enters LIVE mode automatically
- Zero-withdrawal guard in exchange adapters
- Secrets redacted from logs via `shared/log_filter.py`

## Testing

```powershell
pip install pytest pytest-asyncio
pytest tests/ -v
```

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Port 9100 not listening | `logs/metrics_server.err.log` |
| Bot not starting | Keyring `TRADE_CRYPTO_BOT_TOKEN` |
| LLM errors | Keyring `OPENROUTER_API_KEY` |
| Exchange degraded | Expected in paper mode without creds |
