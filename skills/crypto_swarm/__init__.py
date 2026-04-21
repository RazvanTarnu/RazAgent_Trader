def register_tools():
    from .exchange_connector import register_tools as _ex
    from .market_analyst import register_tools as _ma
    from .risk_manager import register_tools as _rm
    from .trade_executioner import register_tools as _te
    from .dust_sweeper import register_tools as _ds
    from .sentiment_analyzer import register_tools as _sa
    tools = {}
    tools.update(_ex())
    tools.update(_ma())
    tools.update(_rm())
    tools.update(_te())
    tools.update(_ds())
    tools.update(_sa())
    # V1.0: 3-agent trading swarm
    try:
        from .trading_swarm import SKILL_TOOLS as _swarm
        tools.update({k: v for k, v in _swarm.items()})
    except ImportError:
        pass
    return tools

__all__ = ["register_tools"]
