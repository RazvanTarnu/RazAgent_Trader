"""
V16.0 Crypto Swarm — Exchange Connector & Key Validator
Loads API keys from environment/keyring. Validates before trading.
RULE: NO withdraw permissions ever.
"""
import os, logging, asyncio, hmac, hashlib, time as _time, threading
import httpx
import ccxt.async_support as ccxt_async

logger = logging.getLogger("godclaw.crypto")

# Exchange configs — keys from env (Docker) or keyring (Windows)
EXCHANGE_CONFIGS = {
    "binance": {
        "class": ccxt_async.binance,
        "api_key_env": "BINANCE_API_KEY",
        "secret_env": "BINANCE_SECRET",
        "options": {"defaultType": "spot"},  # SPOT ONLY, no futures/margin
    },
    "kucoin": {
        "class": ccxt_async.kucoin,
        "api_key_env": "KUCOIN_API_KEY",
        "secret_env": "KUCOIN_SECRET",
        "passphrase_env": "KUCOIN_PASSPHRASE",
        "options": {"defaultType": "spot"},
    },
}

# Singleton exchange instances (thread-safe access)
_exchanges: dict[str, ccxt_async.Exchange] = {}
_exchanges_lock = threading.Lock()

def _load_keys(exchange_name: str) -> dict | None:
    """Load API keys from environment variables."""
    config = EXCHANGE_CONFIGS.get(exchange_name)
    if not config:
        return None
    api_key = os.environ.get(config["api_key_env"], "")
    secret = os.environ.get(config["secret_env"], "")
    if not api_key or not secret:
        # Try keyring as fallback
        try:
            import keyring
            api_key = api_key or keyring.get_password("AgentCeoR", config["api_key_env"]) or ""
            secret = secret or keyring.get_password("AgentCeoR", config["secret_env"]) or ""
        except Exception:
            pass
    if not api_key or not secret:
        return None
    result = {"apiKey": api_key, "secret": secret, "options": config.get("options", {})}
    if "passphrase_env" in config:
        passphrase = os.environ.get(config["passphrase_env"], "")
        if not passphrase:
            try:
                import keyring
                passphrase = keyring.get_password("AgentCeoR", config["passphrase_env"]) or ""
            except Exception:
                pass
        result["password"] = passphrase
    return result

def get_exchange(name: str) -> ccxt_async.Exchange | None:
    """Get or create exchange instance (thread-safe)."""
    with _exchanges_lock:
        if name in _exchanges:
            return _exchanges[name]
        config = EXCHANGE_CONFIGS.get(name)
        if not config:
            return None
        keys = _load_keys(name)
        if not keys:
            return None
        exchange = config["class"](keys)
        exchange.enableRateLimit = True
        _exchanges[name] = exchange
        return exchange

async def validate_api_keys(**kwargs) -> dict:
    """Validate API keys for all configured exchanges."""
    results = {}
    for name in EXCHANGE_CONFIGS:
        keys = _load_keys(name)
        if not keys:
            results[name] = {"valid": False, "error": "No API keys found", "emoji": "⚪"}
            continue
        try:
            # V16.1.1: Direct httpx + HMAC — bypasses CCXT aiohttp issues on Windows
            import hmac, hashlib, time as _time, httpx
            api_key = keys["apiKey"]
            secret = keys["secret"]

            if name == "binance":
                ts = str(int(_time.time() * 1000))
                query = f"timestamp={ts}&recvWindow=5000"
                sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
                url = f"https://api.binance.com/api/v3/account?{query}&signature={sig}"
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(url, headers={"X-MBX-APIKEY": api_key})
                if resp.status_code != 200:
                    err = resp.json().get("msg", resp.text[:100])
                    raise Exception(f"HTTP {resp.status_code}: {err}")
                data = resp.json()
                total = {}
                for b in data.get("balances", []):
                    free = float(b.get("free", 0))
                    locked = float(b.get("locked", 0))
                    if free + locked > 0:
                        total[b["asset"]] = round(free + locked, 8)
            elif name == "kucoin":
                # V16.2.1: Direct httpx + KuCoin HMAC auth (bypasses CCXT aiohttp)
                import base64
                passphrase = keys.get("password", "")
                ts = str(int(_time.time() * 1000))
                endpoint = "/api/v1/accounts"
                str_to_sign = f"{ts}GET{endpoint}"
                sig = base64.b64encode(
                    hmac.new(secret.encode(), str_to_sign.encode(), hashlib.sha256).digest()
                ).decode()
                pass_sig = base64.b64encode(
                    hmac.new(secret.encode(), passphrase.encode(), hashlib.sha256).digest()
                ).decode()
                headers = {
                    "KC-API-KEY": api_key,
                    "KC-API-SIGN": sig,
                    "KC-API-TIMESTAMP": ts,
                    "KC-API-PASSPHRASE": pass_sig,
                    "KC-API-KEY-VERSION": "2",
                }
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(f"https://api.kucoin.com{endpoint}", headers=headers)
                if resp.status_code != 200:
                    err_data = resp.json()
                    raise Exception(f"HTTP {resp.status_code}: {err_data.get('msg', resp.text[:100])}")
                data = resp.json()
                total = {}
                for acct in data.get("data", []):
                    bal = float(acct.get("balance", 0))
                    if bal > 0 and acct.get("type") == "trade":
                        asset = acct["currency"]
                        total[asset] = total.get(asset, 0) + round(bal, 8)
            else:
                total = {}
            results[name] = {
                "valid": True,
                "assets": len(total),
                "balances": {k: round(v, 8) for k, v in sorted(total.items(), key=lambda x: -x[1])[:10]},
                "emoji": "✅",
            }
        except ccxt_async.AuthenticationError as e:
            results[name] = {"valid": False, "error": f"Auth failed: {str(e)[:100]}", "emoji": "❌"}
        except ccxt_async.ExchangeError as e:
            results[name] = {"valid": False, "error": f"Exchange error: {str(e)[:100]}", "emoji": "⚠️"}
        except Exception as e:
            results[name] = {"valid": False, "error": f"{type(e).__name__}: {str(e)[:100]}", "emoji": "❌"}
        finally:
            pass  # httpx client auto-closes; CCXT exchange closed inline for kucoin

    # Format output
    lines = ["🔐 <b>Crypto API Key Validation</b>\n"]
    for name, r in results.items():
        lines.append(f"{r['emoji']} <b>{name.upper()}</b>: ")
        if r["valid"]:
            lines[-1] += f"Connected ({r['assets']} assets)"
            if r.get("balances"):
                for asset, amount in list(r["balances"].items())[:5]:
                    lines.append(f"   • {asset}: {amount}")
        else:
            lines[-1] += r["error"]

    return {
        "output": "\n".join(lines),
        "exchanges": results,
        "any_valid": any(r["valid"] for r in results.values()),
    }

async def get_ticker(exchange_name: str, symbol: str, **kwargs) -> dict:
    """Get current ticker for a trading pair."""
    ex = get_exchange(exchange_name)
    if not ex:
        return {"error": f"Exchange {exchange_name} not connected"}
    try:
        ticker = await ex.fetch_ticker(symbol)
        return {
            "symbol": symbol,
            "exchange": exchange_name,
            "last": ticker["last"],
            "bid": ticker["bid"],
            "ask": ticker["ask"],
            "volume_24h": ticker.get("quoteVolume"),
            "change_24h": ticker.get("percentage"),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:100]}"}

async def close_all(**kwargs) -> dict:
    """Close all exchange connections gracefully."""
    with _exchanges_lock:
        for name, ex in _exchanges.items():
            try:
                await ex.close()
            except Exception:
                pass
        _exchanges.clear()
    return {"output": "All exchange connections closed."}

def register_tools() -> dict:
    return {
        "crypto_validate": validate_api_keys,
        "crypto_ticker": get_ticker,
        "crypto_close": close_all,
    }
