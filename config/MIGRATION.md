# RazAgent_Trader migration notes (from GodClaw monorepo)

## Overview

RazAgent_Trader was extracted from `RazAgent-Enterprise` to run as an isolated
crypto trading platform on the laptop host.

## Directory mapping

| GodClaw (PC) | RazAgent_Trader (laptop) |
|--------------|--------------------------|
| `shared/keyring_loader.py` | `shared/keyring_loader.py` (same, scope may differ) |
| `skills/trading_intelligence/exchanges/` | Legacy executors — use `shared/providers/exchange/` for new code |
| `shared/config.py` | Legacy SSOT — use `shared/platform/config.py` for platform config |
| N/A | `metrics_server.py` (new, port 9100) |
| N/A | `config/default.yaml` + gitignored `config/laptop.yaml` |

## Keyring migration

1. Export credentials from PC keyring scope `AgentCeoR`
2. Import into laptop scope `RazAgentTrader` or keep `AgentCeoR` (loader supports both aliases)
3. Required keys: `OPENROUTER_API_KEY`, exchange keys, `TAILSCALE_METRIC_TOKEN`

## Configuration migration

- Move host-specific settings to `config/laptop.yaml` (gitignored)
- Set `PAPER_MODE=true` until operator explicitly activates live trading
- Do NOT copy `.env` files with secrets into the repo

## Process migration

Old: manual bot start
New: `scripts/start_trader.ps1` validates config, starts metrics, then bot

## Integration boundaries

- Agent 2 (Quant): consumes `MarketDataProvider`, `LLMProvider.recommend()`
- Agent 3 (Security): consumes `ExchangeProvider` only via approval gate, owns risk limits

## Rollback

Stop processes via `scripts/stop_trader.ps1`, revert to previous git tag.
