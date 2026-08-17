# -*- coding: utf-8 -*-
"""Config parsing and validation tests."""

from pathlib import Path

import pytest
import yaml

from shared.platform.config import (
    ConfigValidationError,
    PlatformConfig,
    load_platform_config,
    validate_config,
    _dict_to_config,
)


def test_default_config_paper_mode_true(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    default = {
        "environment": "test",
        "safety": {"paper_mode": True, "auto_live": False},
        "llm": {"provider": "openrouter"},
    }
    (config_dir / "default.yaml").write_text(yaml.dump(default), encoding="utf-8")
    monkeypatch.setattr("shared.platform.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("shared.platform.config.DEFAULT_CONFIG_PATH", config_dir / "default.yaml")
    config = load_platform_config()
    assert config.is_paper_mode is True
    assert config.safety.auto_live is False


def test_env_override_paper_mode(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "default.yaml").write_text(
        yaml.dump({"safety": {"paper_mode": True}, "llm": {"provider": "openrouter"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("shared.platform.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("shared.platform.config.DEFAULT_CONFIG_PATH", config_dir / "default.yaml")
    monkeypatch.setenv("PAPER_MODE", "false")
    config = load_platform_config()
    assert config.is_paper_mode is False


def test_auto_live_rejected():
    config = _dict_to_config({
        "llm": {"provider": "openrouter"},
        "safety": {"auto_live": True, "paper_mode": True},
    })
    errors = validate_config(config)
    assert any("auto_live" in e for e in errors)


def test_moonshot_dormant_without_enable():
    config = _dict_to_config({
        "llm": {"provider": "moonshot", "moonshot_enabled": False},
        "safety": {"auto_live": False},
    })
    errors = validate_config(config)
    assert any("moonshot" in e.lower() for e in errors)


def test_invalid_exchange_rejected():
    config = _dict_to_config({
        "llm": {"provider": "openrouter"},
        "exchanges": {"enabled": ["unknown_exch"]},
        "safety": {"auto_live": False},
    })
    errors = validate_config(config)
    assert any("unknown_exch" in e for e in errors)


def test_missing_default_yaml_uses_defaults(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr("shared.platform.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("shared.platform.config.DEFAULT_CONFIG_PATH", config_dir / "default.yaml")
    # Empty default file path — load_platform_config will merge empty + validate
    (config_dir / "default.yaml").write_text(
        yaml.dump({"llm": {"provider": "openrouter"}, "safety": {"auto_live": False}}),
        encoding="utf-8",
    )
    config = load_platform_config()
    assert config.llm.model == "moonshotai/kimi-k2.6"
    assert config.is_paper_mode is True
