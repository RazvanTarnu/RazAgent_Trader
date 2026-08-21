# -*- coding: utf-8 -*-
"""C21 / P2-7: the bot must import crypto_swarm from this repo."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "crypto_bot" / "trade_crypto_bot.py"


def test_trade_crypto_bot_imports_in_repo_crypto_swarm():
    source = BOT.read_text(encoding="utf-8")
    assert "backend.razagent_server" not in source
    assert "from skills.crypto_swarm import register_tools" in source


def test_crypto_swarm_register_tools_is_importable():
    from skills.crypto_swarm import register_tools

    tools = register_tools()
    assert "crypto_portfolio" in tools
    assert "crypto_dust_sweep" not in tools
    assert "crypto_execute_trade" not in tools
