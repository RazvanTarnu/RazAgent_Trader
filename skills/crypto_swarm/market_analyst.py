"""V16.0 Crypto Swarm — Market Analyst Agent"""
import os, logging, asyncio

logger = logging.getLogger("godclaw.crypto.analyst")

# Import exchange connector
from .exchange_connector import get_exchange, EXCHANGE_CONFIGS

async def crypto_analyze(exchange_name: str = "binance", base: str = "USDT", **kwargs) -> dict:
    """Analyze portfolio and find micro-trading opportunities."""
    ex = get_exchange(exchange_name)
    if not ex:
        return {"error": f"Exchange {exchange_name} not connected. Run /crypto init first."}

    try:
        balance = await ex.fetch_balance()
        total = {k: v for k, v in balance.get("total", {}).items() if v and v > 0}

        if not total:
            return {"error": "No assets found in portfolio."}

        # Identify dust (assets worth < $1)
        opportunities = []
        portfolio_lines = []

        for asset, amount in sorted(total.items(), key=lambda x: -x[1]):
            if asset == base:
                portfolio_lines.append(f"💰 {asset}: {amount:.2f}")
                continue

            symbol = f"{asset}/{base}"
            try:
                ticker = await ex.fetch_ticker(symbol)
                value_usd = amount * (ticker["last"] or 0)

                if value_usd < 1.0:
                    opportunities.append({
                        "type": "dust",
                        "asset": asset,
                        "amount": amount,
                        "value_usd": round(value_usd, 4),
                        "action": f"Sell {amount:.8f} {asset} (dust: ${value_usd:.4f})",
                    })
                    portfolio_lines.append(f"🔸 {asset}: {amount:.8f} (${value_usd:.4f} — dust)")
                else:
                    portfolio_lines.append(f"📊 {asset}: {amount:.8f} (${value_usd:.2f})")

                    # Check 24h momentum for swing opportunities
                    change = ticker.get("percentage", 0) or 0
                    if change < -5:
                        opportunities.append({
                            "type": "dip_buy",
                            "asset": asset,
                            "change_24h": round(change, 2),
                            "price": ticker["last"],
                            "action": f"Consider buying {asset} (24h: {change:.1f}% dip)",
                        })
                    elif change > 8:
                        opportunities.append({
                            "type": "take_profit",
                            "asset": asset,
                            "change_24h": round(change, 2),
                            "price": ticker["last"],
                            "action": f"Consider taking profit on {asset} (24h: +{change:.1f}%)",
                        })
            except Exception:
                portfolio_lines.append(f"❓ {asset}: {amount:.8f} (no {base} pair)")

        # Format output
        output_lines = [
            f"📊 <b>Crypto Analysis — {exchange_name.upper()}</b>\n",
            "<b>Portfolio:</b>",
            *portfolio_lines,
            f"\n<b>Opportunities ({len(opportunities)}):</b>",
        ]

        if opportunities:
            for i, opp in enumerate(opportunities, 1):
                emoji = {"dust": "🗑️", "dip_buy": "📉", "take_profit": "📈"}.get(opp["type"], "💡")
                output_lines.append(f"{i}. {emoji} {opp['action']}")
        else:
            output_lines.append("No opportunities detected at this time.")

        return {
            "output": "\n".join(output_lines),
            "opportunities": opportunities,
            "portfolio_assets": len(total),
        }
    except Exception as e:
        logger.error(f"[Analyst] Error: {e}", exc_info=True)
        return {"error": f"Analysis failed: {type(e).__name__}: {str(e)[:200]}"}

def register_tools() -> dict:
    return {"crypto_analyze": crypto_analyze}
