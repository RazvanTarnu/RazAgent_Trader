"""Negative proof that legacy and bot code cannot emit real orders."""

import ast
from pathlib import Path

import pytest

from shared.execution import ExecutionForbidden
from shared.execution.kill_switch import KillSwitchState, is_armed, persist_armed, read_kill_switch
from shared.platform.config import PlatformConfig, SafetyConfig
from shared.providers.exchange.factory import create_exchange_adapters

ROOT = Path(__file__).resolve().parents[2]
ADAPTER_DIR = ROOT / "shared" / "providers" / "exchange"


def _python_files():
    for path in ROOT.rglob("*.py"):
        if any(part in {".git", ".venv", "venv"} for part in path.parts):
            continue
        yield path


def test_ccxt_imports_are_confined_to_platform_adapters():
    violations = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name == "ccxt" or name.startswith("ccxt.") for name in names):
                if ADAPTER_DIR not in path.parents:
                    violations.append(str(path.relative_to(ROOT)))
    assert violations == []


def test_exchange_order_calls_are_confined_to_platform_adapters():
    violations = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create" + "_order"
                and ADAPTER_DIR not in path.parents
            ):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert violations == []


def test_legacy_execution_is_forbidden():
    from skills.crypto_swarm.trade_executioner import execute_trade

    with pytest.raises(ExecutionForbidden, match="paper-only"):
        import asyncio
        asyncio.run(execute_trade(1, confirmed="true"))


def test_exchange_factory_rejects_non_paper_config():
    config = PlatformConfig(safety=SafetyConfig(paper_mode=False, auto_live=False))
    with pytest.raises(ExecutionForbidden, match="paper-only"):
        create_exchange_adapters(config)


def test_trading_activate_contains_no_config_mutation():
    path = ROOT / "shared" / "patches" / "trading_activate.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {"write_text", "write_bytes", "open", "replace"}
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert calls.isdisjoint(forbidden)


def test_kill_switch_missing_corrupt_and_ambiguous_are_armed(tmp_path, monkeypatch):
    state_path = tmp_path / "kill_switch.json"
    assert read_kill_switch(state_path) is KillSwitchState.ARMED
    state_path.write_text("not-json", encoding="utf-8")
    assert is_armed(state_path)
    state_path.write_text('{"state":"UNKNOWN"}', encoding="utf-8")
    assert is_armed(state_path)
    state_path.write_text('{"state":"DISARMED"}', encoding="utf-8")
    monkeypatch.setenv("RAZAGENT_KILL_SWITCH", "unexpected")
    assert is_armed(state_path)


def test_kill_switch_can_persist_only_armed(tmp_path):
    state_path = tmp_path / "kill_switch.json"
    persist_armed(state_path)
    assert read_kill_switch(state_path) is KillSwitchState.ARMED
