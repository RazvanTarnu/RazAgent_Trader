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


def test_dust_sweep_is_unconditionally_forbidden():
    from skills.crypto_swarm.dust_sweeper import crypto_dust_sweep

    with pytest.raises(ExecutionForbidden, match="dust conversion is a financial action"):
        import asyncio
        asyncio.run(crypto_dust_sweep(confirmed="true"))


def test_no_signed_exchange_client_outside_adapters():
    from tests.security.live_execution_scan import (
        is_adapter,
        is_quarantined_legacy_executor,
        production_python_files,
        relative_to_root,
        scan_production_file,
    )

    violations = []
    for path in production_python_files():
        if is_adapter(path) or is_quarantined_legacy_executor(path):
            continue
        findings = scan_production_file(path)
        if findings.signed_exchange_client:
            violations.append(str(relative_to_root(path)))
    assert violations == []


def test_no_state_changing_http_to_exchange_hosts():
    from tests.security.live_execution_scan import (
        is_adapter,
        is_quarantined_legacy_executor,
        production_python_files,
        relative_to_root,
        scan_production_file,
    )

    violations = []
    for path in production_python_files():
        if is_adapter(path) or is_quarantined_legacy_executor(path):
            continue
        findings = scan_production_file(path)
        for lineno in findings.state_changing_http:
            violations.append(f"{relative_to_root(path)}:{lineno}")
    assert violations == []


def test_order_aliases_are_confined_to_platform_adapters():
    from tests.security.live_execution_scan import (
        is_adapter,
        production_python_files,
        relative_to_root,
        scan_production_file,
    )

    violations = []
    for path in production_python_files():
        if is_adapter(path):
            continue
        findings = scan_production_file(path)
        for item in findings.order_alias_calls:
            violations.append(f"{relative_to_root(path)}:{item}")
    assert violations == []


def test_no_indirect_order_dispatch():
    from tests.security.live_execution_scan import (
        is_adapter,
        production_python_files,
        relative_to_root,
        scan_production_file,
    )

    violations = []
    for path in production_python_files():
        if is_adapter(path):
            continue
        findings = scan_production_file(path)
        for item in findings.indirect_dispatch:
            violations.append(f"{relative_to_root(path)}:{item}")
    assert violations == []


def test_no_financial_endpoint_literals_outside_adapters():
    from tests.security.live_execution_scan import (
        is_adapter,
        is_quarantined_legacy_executor,
        production_python_files,
        relative_to_root,
        scan_production_file,
    )

    violations = []
    for path in production_python_files():
        if is_adapter(path) or is_quarantined_legacy_executor(path):
            continue
        findings = scan_production_file(path)
        for item in findings.financial_endpoints:
            violations.append(f"{relative_to_root(path)}:{item}")
    assert violations == []


def test_live_broker_does_not_exist():
    from tests.security.live_execution_scan import (
        LIVE_BROKER_NAMES,
        production_python_files,
        relative_to_root,
        scan_production_file,
    )

    violations = []
    for path in production_python_files():
        stem = path.stem.lower()
        if stem in LIVE_BROKER_NAMES or stem.replace("-", "_") in LIVE_BROKER_NAMES:
            violations.append(str(relative_to_root(path)))
        findings = scan_production_file(path)
        for name in findings.live_broker_names:
            violations.append(f"{relative_to_root(path)}:{name}")
    assert violations == []


def test_forbidden_execution_tools_are_not_registered():
    import ast
    import inspect
    import textwrap

    from skills.crypto_swarm import register_tools

    tools = register_tools()
    assert "crypto_dust_sweep" not in tools
    assert "crypto_execute_trade" not in tools

    def _unconditional_forbidden(func) -> bool:
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
        except (OSError, TypeError, SyntaxError):
            return False
        fn = next(
            (
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ),
            None,
        )
        if fn is None:
            return False
        body = [
            node
            for node in fn.body
            if not (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
        ]
        core = [
            node for node in body if not isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        if len(core) != 1:
            return False
        stmt = core[0]
        if isinstance(stmt, ast.Raise):
            exc = stmt.exc
            if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
                return exc.func.id == "ExecutionForbidden"
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            func = stmt.value.func
            return isinstance(func, ast.Name) and func.id == "raise_execution_forbidden"
        return False

    leaking = [name for name, func in tools.items() if _unconditional_forbidden(func)]
    assert leaking == []


def test_scanner_flags_regression_b1_sample():
    from tests.security.live_execution_scan import ROOT, scan_source

    fixture = ROOT / "tests" / "security" / "fixtures" / "regression_b1_sample.py.txt"
    findings = scan_source(fixture.read_text(encoding="utf-8"))
    assert findings.signed_exchange_client
    assert findings.state_changing_http
    assert findings.financial_endpoints
