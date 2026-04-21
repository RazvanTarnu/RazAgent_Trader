"""
V16.1 Crypto Swarm — Dust Sweeper & Portfolio Overview
Converts small balances ("dust") to BNB via Binance's official endpoint.
Uses direct httpx + HMAC-SHA256 signing (NO ccxt/aiohttp).
"""
import os, hmac, hashlib, time, logging, httpx

logger = logging.getLogger("godclaw.crypto.dust")
BASE_URL = "https://api.binance.com"

# Assets never considered dust
_KEEP_ASSETS = {"BNB", "USDT", "BTC", "USDC", "BUSD", "FDUSD"}
_DUST_THRESHOLD_USD = 10.0


def _get_keys():
    from shared.keyring_loader import get_credential

    api_key = os.environ.get("BINANCE_API_KEY", "") or get_credential("BINANCE_API_KEY") or ""
    secret = os.environ.get("BINANCE_API_SECRET", "") or os.environ.get("BINANCE_SECRET", "") or get_credential("BINANCE_API_SECRET") or ""
    return api_key, secret


async def _binance_request(method, endpoint, params=None, signed=True):
    """Reusable helper for all direct Binance API calls (httpx + HMAC-SHA256)."""
    api_key, secret = _get_keys()
    if not api_key or not secret:
        return {"error": "No Binance API keys configured"}

    params = params or {}
    if signed:
        params["timestamp"] = str(int(time.time() * 1000))
        params["recvWindow"] = "5000"
        query = "&".join(f"{k}={v}" for k, v in params.items())
        params["signature"] = hmac.new(
            secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()

    url = f"{BASE_URL}{endpoint}"
    headers = {"X-MBX-APIKEY": api_key}

    async with httpx.AsyncClient(timeout=15) as client:
        if method == "GET":
            resp = await client.get(url, params=params, headers=headers)
        elif method == "POST":
            resp = await client.post(url, params=params, headers=headers)
        else:
            return {"error": f"Unsupported HTTP method: {method}"}

        if resp.status_code != 200:
            try:
                err = resp.json().get("msg", resp.text[:200])
            except Exception:
                err = resp.text[:200]
            return {"error": f"HTTP {resp.status_code}: {err}"}
        return resp.json()


async def _get_usdt_price(asset: str) -> float:
    """Get approximate USD value for 1 unit of asset."""
    if asset == "USDT":
        return 1.0
    if asset == "BUSD" or asset == "FDUSD" or asset == "USDC":
        return 1.0

    # Try direct USDT pair
    resp = await _binance_request("GET", "/api/v3/ticker/price",
                                  {"symbol": f"{asset}USDT"}, signed=False)
    if not resp.get("error") and "price" in resp:
        return float(resp["price"])

    # Try BTC pair then convert BTC->USDT
    resp_btc = await _binance_request("GET", "/api/v3/ticker/price",
                                      {"symbol": f"{asset}BTC"}, signed=False)
    if not resp_btc.get("error") and "price" in resp_btc:
        btc_usdt = await _binance_request("GET", "/api/v3/ticker/price",
                                          {"symbol": "BTCUSDT"}, signed=False)
        if not btc_usdt.get("error") and "price" in btc_usdt:
            return float(resp_btc["price"]) * float(btc_usdt["price"])

    # Try BNB pair then convert BNB->USDT
    resp_bnb = await _binance_request("GET", "/api/v3/ticker/price",
                                      {"symbol": f"{asset}BNB"}, signed=False)
    if not resp_bnb.get("error") and "price" in resp_bnb:
        bnb_usdt = await _binance_request("GET", "/api/v3/ticker/price",
                                          {"symbol": "BNBUSDT"}, signed=False)
        if not bnb_usdt.get("error") and "price" in bnb_usdt:
            return float(resp_bnb["price"]) * float(bnb_usdt["price"])

    return 0.0


async def crypto_dust_check(**kwargs) -> dict:
    """Check for small balances (dust) that can be converted to BNB."""
    account = await _binance_request("GET", "/api/v3/account")
    if account.get("error"):
        return {"output": f"❌ {account['error']}", "error": account["error"]}

    balances = account.get("balances", [])
    dust_assets = []
    total_dust_usd = 0.0

    for b in balances:
        asset = b["asset"]
        free = float(b.get("free", 0))
        locked = float(b.get("locked", 0))
        total = free + locked
        if total <= 0 or asset in _KEEP_ASSETS:
            continue

        price_usd = await _get_usdt_price(asset)
        value_usd = total * price_usd

        if value_usd < _DUST_THRESHOLD_USD:
            dust_assets.append({
                "asset": asset,
                "amount": total,
                "value_usd": round(value_usd, 4),
            })
            total_dust_usd += value_usd

    if not dust_assets:
        return {"output": "🧹 No dust found — all balances are above $10 or in keep-list.", "dust": []}

    # Sort by value descending
    dust_assets.sort(key=lambda x: -x["value_usd"])

    lines = ["🧹 <b>Dust Analysis</b>\n"]
    for d in dust_assets:
        lines.append(f"  • {d['asset']}: {d['amount']:.8g} (~${d['value_usd']:.2f})")
    lines.append(f"\n<b>Total dust</b>: ~${total_dust_usd:.2f} across {len(dust_assets)} assets")
    lines.append("\nUse <code>/crypto sweep</code> to convert to BNB.")

    return {"output": "\n".join(lines), "dust": dust_assets, "total_usd": round(total_dust_usd, 4)}


async def crypto_dust_sweep(**kwargs) -> dict:
    """Convert small balances (dust) to BNB via Binance's official endpoint.

    Requires confirmed='true' for execution (human-in-the-loop safety gate).
    Without confirmation, returns a proposal with instructions.
    """
    confirmed = str(kwargs.get("confirmed", "")).lower() == "true"

    # First, get the dust list
    check = await crypto_dust_check()
    if check.get("error"):
        return check
    dust = check.get("dust", [])
    if not dust:
        return {"output": "🧹 No dust to sweep — portfolio is clean."}

    asset_list = [d["asset"] for d in dust]

    if not confirmed:
        lines = ["🧹 <b>Dust Sweep Proposal</b>\n"]
        lines.append(f"Assets to convert to BNB ({len(asset_list)}):")
        for d in dust:
            lines.append(f"  • {d['asset']}: {d['amount']:.8g} (~${d['value_usd']:.2f})")
        lines.append(f"\nEstimated total: ~${check.get('total_usd', 0):.2f}")
        lines.append("\n⚠️ To execute, reply: <code>/crypto sweep confirmed</code>")
        return {"output": "\n".join(lines), "requires_confirmation": True, "assets": asset_list}

    # Execute the dust conversion
    logger.info("Executing dust sweep for assets: %s", asset_list)
    result = await _binance_request("POST", "/sapi/v1/asset/dust", {
        "asset": ",".join(asset_list),
    })

    if result.get("error"):
        return {"output": f"❌ Sweep failed: {result['error']}", "error": result["error"]}

    # Parse Binance response
    transfer_result = result.get("totalTransferResult", result.get("results", []))
    total_bnb = 0.0
    detail_lines = []

    if isinstance(transfer_result, list):
        for r in transfer_result:
            bnb = float(r.get("transferedAmount", r.get("amount", 0)))
            total_bnb += bnb
            detail_lines.append(f"  • {r.get('fromAsset', '?')} → {bnb:.8f} BNB")

    # Also check totalTransfered at top level
    total_bnb = float(result.get("totalTransfered", total_bnb) or total_bnb)
    fee = float(result.get("totalServiceCharge", 0) or 0)

    lines = ["✅ <b>Dust Sweep Complete</b>\n"]
    if detail_lines:
        lines.extend(detail_lines)
    lines.append(f"\n<b>Total received</b>: {total_bnb:.8f} BNB")
    if fee > 0:
        lines.append(f"<b>Fee</b>: {fee:.8f} BNB")

    return {"output": "\n".join(lines), "total_bnb": total_bnb, "fee": fee}


async def crypto_portfolio(**kwargs) -> dict:
    """Get portfolio overview with USD values for all assets."""
    account = await _binance_request("GET", "/api/v3/account")
    if account.get("error"):
        return {"output": f"❌ {account['error']}", "error": account["error"]}

    balances = account.get("balances", [])
    holdings = []
    total_usd = 0.0

    for b in balances:
        asset = b["asset"]
        free = float(b.get("free", 0))
        locked = float(b.get("locked", 0))
        total = free + locked
        if total <= 0:
            continue

        price_usd = await _get_usdt_price(asset)
        value_usd = total * price_usd
        total_usd += value_usd

        holdings.append({
            "asset": asset,
            "amount": total,
            "free": free,
            "locked": locked,
            "price_usd": round(price_usd, 6),
            "value_usd": round(value_usd, 2),
        })

    # Sort by USD value descending
    holdings.sort(key=lambda x: -x["value_usd"])

    lines = ["💼 <b>Portfolio Overview</b>\n"]
    for h in holdings:
        lock_str = f" (🔒{h['locked']:.8g})" if h["locked"] > 0 else ""
        lines.append(
            f"  • <b>{h['asset']}</b>: {h['amount']:.8g}{lock_str}"
            f"  — ${h['value_usd']:.2f} (@${h['price_usd']:.6g})"
        )
    lines.append(f"\n<b>Total portfolio</b>: ${total_usd:,.2f}")

    return {
        "output": "\n".join(lines),
        "holdings": holdings,
        "total_usd": round(total_usd, 2),
    }


def register_tools() -> dict:
    return {
        "crypto_dust_check": crypto_dust_check,
        "crypto_dust_sweep": crypto_dust_sweep,
        "crypto_portfolio": crypto_portfolio,
    }
