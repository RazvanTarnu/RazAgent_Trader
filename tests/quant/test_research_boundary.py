# -*- coding: utf-8 -*-
"""P2-10: trading_intelligence/ must never import execution, exchange, keyring, or approval."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUANT_ROOT = ROOT / "trading_intelligence"

FORBIDDEN_PREFIXES = (
    "ccxt",
    "keyring",
    "shared.execution",
    "shared.keyring_loader",
    "shared.approval",
    "shared.trading_approval",
    "shared.platform.secrets",
    "shared.providers.exchange",
    "legacy.trading_intelligence_v1.exchanges",
)


def _imported_modules(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _is_forbidden(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_PREFIXES)


def test_quant_package_does_not_import_execution_exchange_keyring_or_approval():
    assert QUANT_ROOT.is_dir(), "quant package missing"
    violations: list[str] = []
    for path in QUANT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _imported_modules(tree):
            if _is_forbidden(module):
                violations.append(f"{path.relative_to(ROOT)}:{module}")
    assert violations == []
