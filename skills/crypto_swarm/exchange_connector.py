"""Read-only compatibility facade over the platform exchange providers."""

from __future__ import annotations

from shared.execution import ExecutionForbidden
from shared.platform.config import load_platform_config
from shared.providers.exchange.factory import create_exchange_adapters


class ReadOnlyExchange:
    """Expose legacy market-data names without exposing order execution."""

    def __init__(self, provider):
        self._provider = provider

    async def fetch_ticker(self, symbol: str) -> dict:
        ticker = await self._provider.get_ticker(symbol)
        return {
            "last": ticker.last,
            "bid": ticker.bid,
            "ask": ticker.ask,
            "quoteVolume": ticker.volume_24h,
        }

    async def fetch_balance(self) -> dict:
        balances = await self._provider.get_balances()
        return {"total": {balance.asset: balance.total for balance in balances}}

    async def close(self) -> None:
        await self._provider.close()

    async def place_order(self, *args, **kwargs):
        raise ExecutionForbidden("orders are unavailable through read-only exchange access")


_exchanges: dict[str, ReadOnlyExchange] | None = None


def _read_only_exchanges() -> dict[str, ReadOnlyExchange]:
    global _exchanges
    if _exchanges is None:
        config = load_platform_config()
        if config.safety.paper_mode is not True:
            raise ExecutionForbidden("non-paper configuration is forbidden")
        _exchanges = {
            name: ReadOnlyExchange(provider)
            for name, provider in create_exchange_adapters(config).items()
        }
    return _exchanges


def get_exchange(name: str) -> ReadOnlyExchange | None:
    return _read_only_exchanges().get(name.lower())


async def validate_api_keys(**kwargs) -> dict:
    """Report configured read-only providers without probing private accounts."""
    names = sorted(_read_only_exchanges())
    return {
        "output": "Read-only exchange providers: " + ", ".join(names),
        "exchanges": {name: {"valid": True, "read_only": True} for name in names},
        "any_valid": bool(names),
    }


async def get_ticker(exchange_name: str, symbol: str, **kwargs) -> dict:
    exchange = get_exchange(exchange_name)
    if exchange is None:
        return {"error": f"Exchange {exchange_name} not configured"}
    ticker = await exchange.fetch_ticker(symbol)
    return {"symbol": symbol, "exchange": exchange_name, **ticker}


async def close_all(**kwargs) -> dict:
    global _exchanges
    for exchange in (_exchanges or {}).values():
        await exchange.close()
    _exchanges = None
    return {"output": "All read-only exchange connections closed."}


def register_tools() -> dict:
    return {"crypto_validate": validate_api_keys, "crypto_ticker": get_ticker, "crypto_close": close_all}
