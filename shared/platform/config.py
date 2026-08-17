# -*- coding: utf-8 -*-
"""Platform configuration loader with environment separation.

Configuration precedence (lowest → highest):
  1. config/default.yaml
  2. config/{environment}.yaml  (e.g. laptop.yaml — gitignored)
  3. Environment variables (RAZAGENT_* / platform keys)
  4. Keyring secrets (loaded separately via secrets module)

No secrets are stored in source-controlled YAML files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from shared.setup_paths import PROJECT_ROOT

CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "default.yaml"


@dataclass
class LLMConfig:
    provider: str = "openrouter"
    model: str = "moonshotai/kimi-k2.6"
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 60.0
    max_retries: int = 2
    # Dormant direct Moonshot — not production default
    moonshot_enabled: bool = False
    moonshot_model: str = "moonshot-v1-128k"
    moonshot_base_url: str = "https://api.moonshot.cn/v1"


@dataclass
class ExchangeConfig:
    enabled: list[str] = field(default_factory=lambda: ["binance", "kucoin"])
    default_exchange: str = "binance"
    request_timeout_seconds: float = 15.0
    max_retries: int = 3


@dataclass
class MetricsConfig:
    host: str = "0.0.0.0"
    port: int = 9100
    bearer_token_key: str = "TAILSCALE_METRIC_TOKEN"
    allowed_ips: list[str] = field(default_factory=lambda: ["127.0.0.1", "192.168.1.0/24"])


@dataclass
class SafetyConfig:
    paper_mode: bool = True
    max_trade_usd: float = 7.0
    max_daily_loss_usd: float = 20.0
    auto_live: bool = False  # Must never auto-enter LIVE mode


@dataclass
class PlatformConfig:
    environment: str = "development"
    host_id: str = "laptop"
    llm: LLMConfig = field(default_factory=LLMConfig)
    exchanges: ExchangeConfig = field(default_factory=ExchangeConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    logs_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "logs")

    @property
    def is_paper_mode(self) -> bool:
        return self.safety.paper_mode

    @property
    def is_live_mode(self) -> bool:
        return not self.safety.paper_mode


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Map well-known env vars onto config keys."""
    if v := os.environ.get("RAZAGENT_ENV"):
        data["environment"] = v
    if v := os.environ.get("RAZAGENT_HOST_ID"):
        data["host_id"] = v
    if v := os.environ.get("PAPER_MODE"):
        data.setdefault("safety", {})["paper_mode"] = _parse_bool(v)
    if v := os.environ.get("METRICS_PORT"):
        data.setdefault("metrics", {})["port"] = int(v)
    if v := os.environ.get("METRICS_HOST"):
        data.setdefault("metrics", {})["host"] = v
    if v := os.environ.get("LLM_PROVIDER"):
        data.setdefault("llm", {})["provider"] = v
    if v := os.environ.get("LLM_MODEL"):
        data.setdefault("llm", {})["model"] = v
    return data


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file must be a mapping: {path}")
    return data


def _dict_to_config(data: dict[str, Any]) -> PlatformConfig:
    llm_data = data.get("llm", {})
    exch_data = data.get("exchanges", {})
    metrics_data = data.get("metrics", {})
    safety_data = data.get("safety", {})

    return PlatformConfig(
        environment=str(data.get("environment", "development")),
        host_id=str(data.get("host_id", "laptop")),
        llm=LLMConfig(
            provider=str(llm_data.get("provider", "openrouter")),
            model=str(llm_data.get("model", "moonshotai/kimi-k2.6")),
            base_url=str(llm_data.get("base_url", "https://openrouter.ai/api/v1")),
            timeout_seconds=float(llm_data.get("timeout_seconds", 60.0)),
            max_retries=int(llm_data.get("max_retries", 2)),
            moonshot_enabled=bool(llm_data.get("moonshot_enabled", False)),
            moonshot_model=str(llm_data.get("moonshot_model", "moonshot-v1-128k")),
            moonshot_base_url=str(llm_data.get("moonshot_base_url", "https://api.moonshot.cn/v1")),
        ),
        exchanges=ExchangeConfig(
            enabled=list(exch_data.get("enabled", ["binance", "kucoin"])),
            default_exchange=str(exch_data.get("default_exchange", "binance")),
            request_timeout_seconds=float(exch_data.get("request_timeout_seconds", 15.0)),
            max_retries=int(exch_data.get("max_retries", 3)),
        ),
        metrics=MetricsConfig(
            host=str(metrics_data.get("host", "0.0.0.0")),
            port=int(metrics_data.get("port", 9100)),
            bearer_token_key=str(metrics_data.get("bearer_token_key", "TAILSCALE_METRIC_TOKEN")),
            allowed_ips=list(metrics_data.get("allowed_ips", ["127.0.0.1", "192.168.1.0/24"])),
        ),
        safety=SafetyConfig(
            paper_mode=bool(safety_data.get("paper_mode", True)),
            max_trade_usd=float(safety_data.get("max_trade_usd", 7.0)),
            max_daily_loss_usd=float(safety_data.get("max_daily_loss_usd", 20.0)),
            auto_live=bool(safety_data.get("auto_live", False)),
        ),
        data_dir=Path(data.get("data_dir", str(PROJECT_ROOT / "data"))),
        logs_dir=Path(data.get("logs_dir", str(PROJECT_ROOT / "logs"))),
    )


class ConfigValidationError(Exception):
    """Raised when mandatory configuration is missing or invalid."""


def validate_config(config: PlatformConfig) -> list[str]:
    """Return list of validation errors (empty = valid)."""
    errors: list[str] = []

    if config.safety.auto_live:
        errors.append("auto_live must remain false — LIVE mode requires explicit operator action")

    if config.safety.max_trade_usd <= 0:
        errors.append("max_trade_usd must be positive")

    if config.metrics.port < 1 or config.metrics.port > 65535:
        errors.append(f"Invalid metrics port: {config.metrics.port}")

    if config.llm.provider not in {"openrouter", "moonshot"}:
        errors.append(f"Unknown LLM provider: {config.llm.provider}")

    if config.llm.provider == "moonshot" and not config.llm.moonshot_enabled:
        errors.append("Direct Moonshot provider selected but moonshot_enabled=false")

    for ex in config.exchanges.enabled:
        if ex not in {"binance", "kucoin"}:
            errors.append(f"Unknown exchange: {ex}")

    return errors


def load_platform_config(
    *,
    environment: Optional[str] = None,
    host_config: Optional[Path] = None,
) -> PlatformConfig:
    """Load and merge platform configuration."""
    merged: dict[str, Any] = _load_yaml(DEFAULT_CONFIG_PATH)

    env_name = environment or os.environ.get("RAZAGENT_ENV", merged.get("environment", "development"))
    env_path = CONFIG_DIR / f"{env_name}.yaml"
    merged = _deep_merge(merged, _load_yaml(env_path))

    # Host-specific override (gitignored laptop.yaml by convention)
    host_path = host_config or CONFIG_DIR / "laptop.yaml"
    merged = _deep_merge(merged, _load_yaml(host_path))

    merged = _apply_env_overrides(merged)
    config = _dict_to_config(merged)

    errors = validate_config(config)
    if errors:
        raise ConfigValidationError("; ".join(errors))

    return config
