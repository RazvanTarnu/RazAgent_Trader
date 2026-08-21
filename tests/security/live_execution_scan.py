# -*- coding: utf-8 -*-
"""AST / source scanners for live-execution containment."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADAPTER_DIR = ROOT / "shared" / "providers" / "exchange"

# Split literals so this helper is not a self-hit if it is ever scanned.
EX_HOSTS = (
    "binance" + ".com",
    "kucoin" + ".com",
    "api" + ".binance",
    "api" + ".kucoin",
)
SIGNING_MARKERS = (
    "hmac",
    "X-MBX-" + "APIKEY",
    "KC-API-" + "SIGN",
    "KC-API-" + "PASSPHRASE",
)
ORDER_ALIASES = (
    "create" + "_order",
    "create" + "_market_buy_order",
    "create" + "_market_sell_order",
    "create" + "_market_order",
    "create" + "_limit_order",
    "create" + "_limit_buy_order",
    "create" + "_limit_sell_order",
    "create" + "_order_ws",
    "edit" + "_order",
    "cancel" + "_order",
)
FINANCIAL_ENDPOINTS = (
    "asset/" + "dust",
    "asset/" + "transfer",
    "asset/" + "convert",
    "convert" + "/",
    "margin" + "/",
    "capital/" + "withdraw",
    "sub-account" + "/",
    "universal-" + "transfer",
    "simple-earn" + "/",
    "lending" + "/",
    "staking" + "/",
    "loan" + "/",
    "futures" + "/",
)
HTTP_MUTATORS = frozenset({"post", "put", "delete", "patch"})
SKIP_PARTS = {".git", ".venv", "venv", "__pycache__", "tests"}
QUARANTINED_LEGACY_EXECUTORS = frozenset({
    Path("legacy/trading_intelligence_v1/exchanges/binance_executor.py"),
    Path("legacy/trading_intelligence_v1/exchanges/kucoin_executor.py"),
})
LIVE_BROKER_NAMES = frozenset({"livebroker", "live_broker"})


@dataclass
class SourceFindings:
    signed_exchange_client: bool = False
    state_changing_http: list[int] = field(default_factory=list)
    order_alias_calls: list[str] = field(default_factory=list)
    indirect_dispatch: list[str] = field(default_factory=list)
    financial_endpoints: list[str] = field(default_factory=list)
    live_broker_names: list[str] = field(default_factory=list)


def production_python_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        files.append(path)
    return files


def relative_to_root(path: Path) -> Path:
    return path.relative_to(ROOT)


def is_adapter(path: Path) -> bool:
    return ADAPTER_DIR in path.parents or path.parent == ADAPTER_DIR


def is_quarantined_legacy_executor(path: Path) -> bool:
    return relative_to_root(path) in QUARANTINED_LEGACY_EXECUTORS


def _has_host(text: str) -> bool:
    lowered = text.lower()
    return any(host in lowered for host in EX_HOSTS)


def _has_signing(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in SIGNING_MARKERS)


def _fold_string(node: ast.AST, env: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append(_fold_string(value.value, env) or "")
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_string(node.left, env)
        right = _fold_string(node.right, env)
        if left is not None and right is not None:
            return left + right
    return None


def _bind_strings(tree: ast.AST) -> dict[str, str]:
    env: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            folded = _fold_string(node.value, env)
            if folded is not None:
                env[node.targets[0].id] = folded
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            folded = _fold_string(node.value, env)
            if folded is not None:
                env[node.target.id] = folded
    return env


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _forbidden_assign_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and "FORBIDDEN" in target.id.upper():
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if "FORBIDDEN" in node.target.id.upper():
                names.add(node.target.id)
    return names


def _in_denylist_assignment(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    forbidden_names: set[str],
) -> bool:
    current: ast.AST | None = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.Assign):
            for target in current.targets:
                if isinstance(target, ast.Name) and target.id in forbidden_names:
                    return True
        if (
            isinstance(current, ast.AnnAssign)
            and isinstance(current.target, ast.Name)
            and current.target.id in forbidden_names
        ):
            return True
    return False


def _looks_like_api_or_url(value: str) -> bool:
    lowered = value.lower().strip()
    return (
        "http://" in lowered
        or "https://" in lowered
        or "/api/" in lowered
        or "sapi/" in lowered
        or lowered.startswith("/")
        or "://" in lowered
    )


def _is_financial_endpoint_literal(value: str) -> bool:
    lowered = value.lower()
    hits = [item for item in FINANCIAL_ENDPOINTS if item in lowered]
    if _is_order_api_path(value):
        return True
    if not hits:
        return False
    stripped = lowered.strip()
    if stripped in FINANCIAL_ENDPOINTS or stripped.strip("/") in {
        item.strip("/") for item in FINANCIAL_ENDPOINTS
    }:
        return True
    if _looks_like_api_or_url(value):
        return True
    return False


def _is_order_api_path(value: str) -> bool:
    lowered = value.lower()
    return "/api/" in lowered and any(
        token in lowered for token in ("/order", "/orders", "/oco")
    )


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def scan_source(source: str) -> SourceFindings:
    findings = SourceFindings()
    findings.signed_exchange_client = _has_host(source) and _has_signing(source)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return findings

    env = _bind_strings(tree)
    parents = _parent_map(tree)
    forbidden_names = _forbidden_assign_names(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in HTTP_MUTATORS:
                candidates = [_fold_string(arg, env) for arg in node.args]
                candidates.extend(
                    _fold_string(keyword.value, env) for keyword in node.keywords
                )
                if any(value and _has_host(value) for value in candidates if value):
                    findings.state_changing_http.append(getattr(node, "lineno", 0))
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in ORDER_ALIASES
            ):
                findings.order_alias_calls.append(
                    f"{node.func.attr}:{getattr(node, 'lineno', 0)}"
                )
            if isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
                alias = _fold_string(node.args[1], env)
                if alias in ORDER_ALIASES:
                    findings.indirect_dispatch.append(
                        f"getattr:{alias}:{getattr(node, 'lineno', 0)}"
                    )
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "partial"
                and node.args
                and isinstance(node.args[0], ast.Attribute)
                and node.args[0].attr in ORDER_ALIASES
            ):
                findings.indirect_dispatch.append(
                    f"partial:{node.args[0].attr}:{getattr(node, 'lineno', 0)}"
                )

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in ORDER_ALIASES:
                findings.indirect_dispatch.append(
                    f"literal:{node.value}:{getattr(node, 'lineno', 0)}"
                )
            if _is_financial_endpoint_literal(node.value) and not _in_denylist_assignment(
                node, parents, forbidden_names
            ):
                hits = [item for item in FINANCIAL_ENDPOINTS if item in node.value.lower()]
                if _is_order_api_path(node.value):
                    hits.append("order-api")
                findings.financial_endpoints.append(
                    f"{','.join(hits)}:{getattr(node, 'lineno', 0)}"
                )

        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.lower() in LIVE_BROKER_NAMES:
                findings.live_broker_names.append(node.name)

    return findings


def scan_production_file(path: Path) -> SourceFindings:
    return scan_source(path.read_text(encoding="utf-8"))
