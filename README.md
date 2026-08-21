# RazAgent_Trader

> Crypto trading bot — extracted from the GodClaw / RazAgent Enterprise monorepo.
> Runs exclusively on the laptop (DESKTOP-BH3MFQ9) with Moonshot Kimi 2.6 as the LLM.
> Isolated from the PC fleet to eliminate Telegram 409 conflicts, GPU contention, and coupling to the video pipeline.

## Architecture

- **Host**: laptop on LAN `192.168.1.137` (Tailscale optional, added when the laptop leaves the home network).
- **LLM runtime**: OpenRouter → `moonshotai/kimi-k2.6` (Moonshot AI's Kimi K2.6 via OpenRouter aggregator). API key in keyring `AgentCeoR::OPENROUTER_API_KEY`.
- **Moonshot direct API** (keyring `AgentCeoR::MOONSHOT_API_KEY`): reserved for future use (not currently wired into any module; regenerate at platform.moonshot.ai if needed).
- **Supervisor bridge**: PC `192.168.1.136` polls read-only metrics from `http://192.168.1.137:9100/metrics/*` via bearer token.
- **Migration plan**: `data/missions/trader_migration.md` (in the parent GodClaw repo on PC).

## Components

| Path | Purpose |
|------|---------|
| `crypto_bot/trade_crypto_bot.py` | Telegram bot (@TradeCrypto13_bot), polling mode |
| `legacy/trading_intelligence_v1/` | Quarantined v1 prediction engine, OHLCV, smart exits, executors |
| `trading_intelligence/` | Quant engine (research only — backtest, features, swarm). No execution. |
| `skills/crypto_swarm/` | 3-agent debate (Fundamentals / Technical / Risk) |
| `shared/` | Keyring loader, trade journal, approval gate, config |
| `metrics_server.py` | FastAPI :9100 — read-only metrics for supervisor (created at F2.4) |
| `config/laptop.yaml` | Host-specific config (gitignored) |
| `scripts/start_trader.ps1` | Boot: metrics_server + bot (if token present) |

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Keyring keys (scope `RazAgentTrader`)

| Key | Required for |
|-----|--------------|
| `MOONSHOT_API_KEY` | LLM reasoning |
| `MOONSHOT_ORG_ID` | LLM org attribution |
| `TAILSCALE_METRIC_TOKEN` | Bearer for PC-side metrics polling |
| `TRADE_CRYPTO_BOT_TOKEN` | Telegram bot (populated after F3.1 BotFather revoke) |
| `TRADE_CRYPTO_CHAT_ID` | Telegram admin chat |
| `BINANCE_API_KEY` + `BINANCE_API_SECRET` | Exchange trading |
| `KUCOIN_API_KEY` + `KUCOIN_API_SECRET` + `KUCOIN_API_PASSPHRASE` | Alternate exchange |

Import with:
```python
import keyring, json
from cryptography.fernet import Fernet
data = json.loads(Fernet(PASS_PHRASE.encode()).decrypt(Path("trader_keys.enc").read_bytes()))
for k, v in data.items():
    keyring.set_password("RazAgentTrader", k, v)
```

## Safety rules (inherited from GodClaw)

- MAX trade = $7 (`base_executor.py`)
- Daily loss kill-switch = $20 (`drawdown_guard.py`)
- PAPER_MODE = True by default; PIN required to flip LIVE (`patches/trading_activate.py`)
- Zero-withdrawal guard (validates no exchange withdraw/transfer endpoints)
- Approval gate 30min timeout for live trades (`trading_approval_gate.py`)

## Ops

- **Start**: `.\scripts\start_trader.ps1`
- **Stop**: `.\scripts\stop_trader.ps1`
- **Logs**: `logs/metrics_server.log`, `logs/trade_crypto_bot.log`
- **Metrics probe (from PC)**:
  ```
  curl -H "Authorization: Bearer $TOKEN" http://192.168.1.137:9100/healthz
  ```

## Lineage

Extracted from `RazAgent-Enterprise` @ V2.5.0 on 2026-04-21.
