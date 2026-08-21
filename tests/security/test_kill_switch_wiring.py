# -*- coding: utf-8 -*-
"""Kill-switch must be consulted at existing financial-action boundaries."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from shared.execution.kill_switch import (
    KillSwitchState,
    ensure_persisted_armed_if_missing_or_invalid,
    read_kill_switch,
)
from shared.platform.config import PlatformConfig, SafetyConfig
from shared.platform.interfaces import OrderRequest
from shared.providers.exchange.binance import BinanceAdapter
from shared.providers.exchange.kucoin import KuCoinAdapter


@pytest.mark.asyncio
async def test_binance_place_order_refuses_when_armed(monkeypatch):
    monkeypatch.setattr("shared.execution.kill_switch.is_armed", lambda *a, **k: True)
    adapter = BinanceAdapter(api_key="", api_secret="", paper_mode=True, max_retries=0)
    result = await adapter.place_order(
        OrderRequest(symbol="BTC/USDT", side="buy", order_type="market", quantity=0.001)
    )
    assert result.success is False
    assert result.error == "kill-switch ARMED"
    await adapter.close()


@pytest.mark.asyncio
async def test_kucoin_place_order_refuses_when_armed(monkeypatch):
    monkeypatch.setattr("shared.execution.kill_switch.is_armed", lambda *a, **k: True)
    adapter = KuCoinAdapter(
        api_key="", api_secret="", passphrase="", paper_mode=True, max_retries=0
    )
    result = await adapter.place_order(
        OrderRequest(symbol="BTC/USDT", side="buy", order_type="market", quantity=0.001)
    )
    assert result.success is False
    assert result.error == "kill-switch ARMED"
    await adapter.close()


@pytest.mark.asyncio
async def test_prepare_trade_refuses_when_armed_without_network(monkeypatch):
    monkeypatch.setattr("shared.execution.kill_switch.is_armed", lambda *a, **k: True)

    def _fail_get_exchange(*_args, **_kwargs):
        raise AssertionError("prepare_trade must not touch the exchange when ARMED")

    monkeypatch.setattr(
        "skills.crypto_swarm.trade_executioner.get_exchange",
        _fail_get_exchange,
        raising=False,
    )
    from skills.crypto_swarm.trade_executioner import prepare_trade

    result = await prepare_trade("binance", "BTC/USDT", "buy", 0.001)
    assert result["error"] == "kill-switch ARMED"


def test_lifecycle_persists_armed_when_file_missing(tmp_path):
    path = tmp_path / "data" / "kill_switch.json"
    ensure_persisted_armed_if_missing_or_invalid(path)
    assert path.exists()
    assert read_kill_switch(path) is KillSwitchState.ARMED


def test_lifecycle_persists_armed_when_file_invalid(tmp_path):
    path = tmp_path / "kill_switch.json"
    path.write_text("not-json", encoding="utf-8")
    ensure_persisted_armed_if_missing_or_invalid(path)
    assert read_kill_switch(path) is KillSwitchState.ARMED


def test_lifecycle_does_not_overwrite_valid_disarmed(tmp_path):
    path = tmp_path / "kill_switch.json"
    path.write_text('{"state":"DISARMED"}\n', encoding="utf-8")
    ensure_persisted_armed_if_missing_or_invalid(path)
    assert read_kill_switch(path) is KillSwitchState.DISARMED


def test_metrics_exposes_kill_switch(monkeypatch):
    monkeypatch.setattr(
        "metrics_server.load_platform_config",
        lambda: PlatformConfig(safety=SafetyConfig(paper_mode=True, auto_live=False)),
    )
    monkeypatch.setattr("metrics_server.get_credential", lambda key: "")
    monkeypatch.setattr("metrics_server._client_allowed", lambda req, allowed: True)
    monkeypatch.setattr(
        "metrics_server.validate_startup",
        lambda: type("R", (), {"success": True, "config": PlatformConfig(), "errors": []})(),
    )
    monkeypatch.setattr(
        "metrics_server.read_kill_switch",
        lambda: KillSwitchState.ARMED,
    )

    from metrics_server import app

    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.json()["kill_switch"] == "ARMED"
    assert client.post("/metrics", json={}).status_code == 405
