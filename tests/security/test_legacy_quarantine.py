# -*- coding: utf-8 -*-
"""Compensatory assertions for QUARANTINED_LEGACY_EXECUTORS (C23 / F1.0).

Scanner exclusions are a blind spot unless something else proves each
excluded file is still quarantined. This module:

1. Asserts every excluded path exists (a stale exclusion is a rotten one).
2. AST-checks instruction order in each order-capable method body: a guard
   must precede any network call. File-level grep is rejected because a
   guard on a sibling method would hide an unguarded live path.
3. Calls each such method and expects ExecutionForbidden.
4. Replays tests/security/fixtures/regression_c23_unguarded_executor.py.txt
   and requires the AST checker to flag it.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
from pathlib import Path
from typing import Iterable

import pytest

from shared.execution import ExecutionForbidden
from tests.security.live_execution_scan import (
    HTTP_MUTATORS,
    QUARANTINED_LEGACY_EXECUTORS,
    ROOT,
    _bind_strings,
    _call_name,
    _fold_string,
)

ORDER_METHOD_NAMES = frozenset(
    {
        "place_market_order",
        "place_order",
        "cancel_order",
        "execute",
        "route",
    }
)
REGRESSION_FIXTURE = (
    ROOT / "tests" / "security" / "fixtures" / "regression_c23_unguarded_executor.py.txt"
)
_DUMMY_ARGS = {
    "api_key": "test-key",
    "api_secret": "test-secret",
    "passphrase": "test-pass",
    "symbol": "BTCUSDT",
    "side": "BUY",
    "amount_usd": 10.0,
    "quantity": 0.001,
    "order_id": "paper-1",
    "client_order_id": "paper-1",
}


def _relative_path(path: Path) -> Path:
    return path if not path.is_absolute() else path.relative_to(ROOT)


def _resolve_quarantined(rel: Path) -> Path:
    return ROOT / rel


def _module_name(rel: Path) -> str:
    return ".".join(rel.with_suffix("").parts)


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _enclosing_function(
    node: ast.AST, parents: dict[ast.AST, ast.AST]
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    current: ast.AST | None = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
    return None


def _is_nested_function(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, parents: dict[ast.AST, ast.AST]
) -> bool:
    parent = parents.get(fn)
    return isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))


def _name_ends_with(func: ast.AST, suffix: str) -> bool:
    if isinstance(func, ast.Name):
        return func.id == suffix
    if isinstance(func, ast.Attribute):
        return func.attr == suffix
    return False


def _is_guard_statement(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Raise):
        exc = stmt.exc
        if exc is None:
            return False
        if isinstance(exc, ast.Call):
            return _name_ends_with(exc.func, "ExecutionForbidden")
        return _name_ends_with(exc, "ExecutionForbidden")
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return _name_ends_with(stmt.value.func, "raise_execution_forbidden")
    return False


def _strip_docstring_and_imports(body: list[ast.stmt]) -> list[ast.stmt]:
    stmts = list(body)
    if (
        stmts
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and isinstance(stmts[0].value.value, str)
    ):
        stmts = stmts[1:]
    while stmts and isinstance(stmts[0], (ast.Import, ast.ImportFrom)):
        stmts = stmts[1:]
    return stmts


def _is_http_mutator_call(node: ast.Call, env: dict[str, str]) -> bool:
    name = _call_name(node.func)
    if name in HTTP_MUTATORS:
        return True
    if isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
        alias = _fold_string(node.args[1], env)
        return alias in HTTP_MUTATORS
    return False


def _function_has_direct_http_mutator(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, env: dict[str, str]
) -> bool:
    parents = _parent_map(fn)
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not _is_http_mutator_call(node, env):
            continue
        # ast.walk includes nested functions; only count calls whose
        # innermost enclosing function is *this* function.
        inner = _enclosing_function(node, parents)
        if inner is None or inner is fn:
            return True
    return False


def _self_method_calls(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
            names.add(node.func.attr)
    return names


def _class_method_map(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    methods: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[item.name] = item
    return methods


def _reaches_http_mutator(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    methods: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    env: dict[str, str],
    seen: set[str] | None = None,
) -> bool:
    seen = seen if seen is not None else set()
    if fn.name in seen:
        return False
    seen.add(fn.name)
    if _function_has_direct_http_mutator(fn, env):
        return True
    for callee in _self_method_calls(fn):
        target = methods.get(callee)
        if target is not None and _reaches_http_mutator(target, methods, env, seen):
            return True
    return False


def iter_order_capable_functions(
    tree: ast.AST, env: dict[str, str]
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Functions that can emit an order, by name or by reaching client.post."""
    parents = _parent_map(tree)
    methods = _class_method_map(tree)
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _is_nested_function(node, parents):
            continue
        named = node.name in ORDER_METHOD_NAMES
        posts = _reaches_http_mutator(node, methods, env)
        if named or posts:
            found.append(node)
    return found


def unguarded_order_methods(source: str) -> list[str]:
    """Return qualified names of order-capable methods whose body is unguarded.

    A method is guarded only if, after skipping the docstring and leading
    imports, the first statement in *that function's* body is
    ``raise_execution_forbidden(...)`` or ``raise ExecutionForbidden(...)``.
    A guard on a sibling method does not count.
    """
    tree = ast.parse(source)
    env = _bind_strings(tree)
    parents = _parent_map(tree)
    flagged: list[str] = []
    for fn in iter_order_capable_functions(tree, env):
        core = _strip_docstring_and_imports(fn.body)
        guarded = bool(core) and _is_guard_statement(core[0])
        if guarded:
            continue
        class_name = ""
        parent = parents.get(fn)
        if isinstance(parent, ast.ClassDef):
            class_name = parent.name + "."
        flagged.append(f"{class_name}{fn.name}:{fn.lineno}")
    return flagged


def _fill_params(fn) -> tuple[list, dict]:
    sig = inspect.signature(fn)
    args: list = []
    kwargs: dict = {}
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if param.default is not param.empty:
            continue
        value = _DUMMY_ARGS.get(name, "test")
        if param.kind == param.KEYWORD_ONLY:
            kwargs[name] = value
        else:
            args.append(value)
    return args, kwargs


def _instantiate(cls):
    args, kwargs = _fill_params(cls.__init__)
    return cls(*args, **kwargs)


def _load_quarantined_module(rel: Path):
    return importlib.import_module(_module_name(rel))


def _executor_classes(module) -> list[type]:
    classes: list[type] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue
        if any(name in ORDER_METHOD_NAMES and callable(getattr(obj, name, None)) for name in ORDER_METHOD_NAMES):
            classes.append(obj)
    return classes


def _order_capable_callables(cls) -> Iterable[tuple[str, object]]:
    for name, member in inspect.getmembers(cls):
        if name.startswith("_"):
            continue
        if name not in ORDER_METHOD_NAMES:
            continue
        if not callable(member):
            continue
        yield name, member


@pytest.mark.parametrize("rel", sorted(QUARANTINED_LEGACY_EXECUTORS, key=str))
def test_quarantined_legacy_executor_file_exists(rel: Path):
    path = _resolve_quarantined(rel)
    assert path.is_file(), f"stale scanner exclusion, missing {rel.as_posix()}"


def test_quarantine_exclusions_are_not_vacuous():
    assert QUARANTINED_LEGACY_EXECUTORS, "empty exclusion set would hide a missing compensatory test"


@pytest.mark.parametrize("rel", sorted(QUARANTINED_LEGACY_EXECUTORS, key=str))
def test_order_methods_are_guarded_before_network_by_ast(rel: Path):
    path = _resolve_quarantined(rel)
    source = path.read_text(encoding="utf-8")
    flagged = unguarded_order_methods(source)
    assert flagged == [], (
        f"{rel.as_posix()} has order-capable methods without a leading "
        f"ExecutionForbidden guard: {flagged}"
    )


@pytest.mark.parametrize("rel", sorted(QUARANTINED_LEGACY_EXECUTORS, key=str))
def test_order_methods_raise_execution_forbidden(rel: Path):
    module = _load_quarantined_module(rel)
    classes = _executor_classes(module)
    assert classes, f"no executor class found in {rel.as_posix()}"
    raised = []
    for cls in classes:
        instance = _instantiate(cls)
        methods = list(_order_capable_callables(cls))
        assert methods, f"{cls.__name__} has no order-capable methods to call"
        for name, _ in methods:
            bound = getattr(instance, name)
            args, kwargs = _fill_params(bound)
            with pytest.raises(ExecutionForbidden):
                result = bound(*args, **kwargs)
                if inspect.isawaitable(result):
                    asyncio.run(result)
            raised.append(f"{cls.__name__}.{name}")
    assert raised


def test_regression_c23_fixture_exists():
    assert REGRESSION_FIXTURE.is_file()


def test_regression_c23_unguarded_executor_is_flagged():
    source = REGRESSION_FIXTURE.read_text(encoding="utf-8")
    flagged = unguarded_order_methods(source)
    assert any("place_market_order" in item for item in flagged), (
        "C23 fixture must make the AST checker red; got " + repr(flagged)
    )
    assert not any(
        item.startswith("UnguardedBinanceExecutor.cancel_order") for item in flagged
    ), "cancel_order is guarded; flagging it would mean the checker greps the file"


def test_ast_checker_does_not_accept_sibling_guard():
    """A guard on another function must not green-light an unguarded poster."""
    source = (
        "import httpx\n"
        "async def cancel_order(self):\n"
        "    raise_execution_forbidden('no')\n"
        "async def place_market_order(self):\n"
        "    async with httpx.AsyncClient() as client:\n"
        "        await client.post('https://api.binance.com/api/v3/order')\n"
    )
    flagged = unguarded_order_methods(source)
    assert any("place_market_order" in item for item in flagged)
