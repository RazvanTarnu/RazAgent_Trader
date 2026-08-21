# -*- coding: utf-8 -*-
"""Isolate persisted kill-switch and audit files from the repo working tree."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_runtime_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "shared.execution.kill_switch.DEFAULT_STATE_PATH",
        tmp_path / "kill_switch.json",
    )
    monkeypatch.setattr(
        "shared.execution.AUDIT_DB_PATH",
        tmp_path / "platform_events.db",
    )
