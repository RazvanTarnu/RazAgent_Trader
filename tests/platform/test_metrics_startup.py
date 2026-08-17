# -*- coding: utf-8 -*-
"""Metrics read-only and startup safety tests."""

import pytest
from fastapi.testclient import TestClient

from shared.platform.config import PlatformConfig, SafetyConfig
from shared.platform.interfaces import ProcessState
from shared.platform.lifecycle import validate_startup, validate_dependencies
from shared.platform.metrics_state import MetricsState


def test_metrics_state_snapshot_readonly_fields():
    state = MetricsState()
    state.set_health("ok")
    state.set_paper_mode(True)
    state.set_process_state(ProcessState.READY)
    snap = state.snapshot()
    assert snap.health == "ok"
    assert snap.paper_mode is True
    assert snap.process_state == ProcessState.READY


def test_metrics_server_no_write_endpoints(monkeypatch):
    monkeypatch.setattr(
        "metrics_server.load_platform_config",
        lambda: PlatformConfig(safety=SafetyConfig(paper_mode=True, auto_live=False)),
    )
    monkeypatch.setattr("metrics_server.get_credential", lambda key: "")
    monkeypatch.setattr("metrics_server._client_allowed", lambda req, allowed: True)

    from metrics_server import app

    client = TestClient(app)
    assert client.post("/metrics", json={}).status_code == 405
    assert client.put("/healthz").status_code == 405
    assert client.delete("/metrics").status_code == 405


def test_metrics_server_healthz_readonly(monkeypatch):
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

    from metrics_server import app

    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert "paper_mode" in data
    assert data["paper_mode"] is True


def test_startup_validation_paper_mode_default(monkeypatch):
    monkeypatch.setattr(
        "shared.platform.lifecycle.load_platform_config",
        lambda **kw: PlatformConfig(safety=SafetyConfig(paper_mode=True, auto_live=False)),
    )
    monkeypatch.setattr("shared.platform.lifecycle.validate_dependencies", lambda: [])
    result = validate_startup()
    assert result.success is True
    assert result.config.is_paper_mode is True


def test_startup_fails_on_auto_live(monkeypatch):
    monkeypatch.setattr(
        "shared.platform.lifecycle.load_platform_config",
        lambda **kw: PlatformConfig(safety=SafetyConfig(paper_mode=True, auto_live=True)),
    )
    monkeypatch.setattr("shared.platform.lifecycle.validate_dependencies", lambda: [])
    result = validate_startup()
    assert result.success is False
    assert any("auto_live" in e for e in result.errors)


def test_validate_dependencies_detects_missing(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "ccxt":
            raise ImportError("no ccxt")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    errors = validate_dependencies()
    assert any("ccxt" in e for e in errors)
