# -*- coding: utf-8 -*-
"""Platform lifecycle — startup, shutdown, validation."""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from typing import Optional

from shared.execution.kill_switch import ensure_persisted_armed_if_missing_or_invalid
from shared.platform.config import ConfigValidationError, PlatformConfig, load_platform_config, validate_config
from shared.platform.interfaces import ProcessState
from shared.platform.metrics_state import MetricsState
from shared.platform.secrets import credential_status, load_platform_secrets, safe_exception_message
from shared.providers.exchange.factory import create_exchange_adapters
from shared.providers.llm.factory import create_llm_provider

logger = logging.getLogger("platform.lifecycle")


@dataclass
class StartupResult:
    success: bool
    config: Optional[PlatformConfig] = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_dependencies() -> list[str]:
    """Check required Python packages are importable."""
    errors: list[str] = []
    for module in ("httpx", "yaml", "fastapi", "uvicorn", "ccxt"):
        try:
            __import__(module)
        except ImportError:
            errors.append(f"Missing dependency: {module}")
    return errors


def validate_startup(*, require_llm: bool = False) -> StartupResult:
    """Validate configuration, dependencies, and safety defaults."""
    errors: list[str] = []
    warnings: list[str] = []

    ensure_persisted_armed_if_missing_or_invalid()

    dep_errors = validate_dependencies()
    errors.extend(dep_errors)

    try:
        config = load_platform_config()
    except ConfigValidationError as exc:
        return StartupResult(success=False, errors=[str(exc)])

    cfg_errors = validate_config(config)
    errors.extend(cfg_errors)

    if not config.is_paper_mode:
        warnings.append("LIVE mode detected — ensure operator explicitly enabled trading")

    # Default must be paper
    if config.safety.paper_mode is not True and config.safety.auto_live:
        errors.append("Cannot auto-enter LIVE mode")

    creds = credential_status()
    if require_llm and creds.get("OPENROUTER_API_KEY") == "MISSING":
        errors.append("OPENROUTER_API_KEY missing from keyring")

    if errors:
        return StartupResult(success=False, config=config, errors=errors, warnings=warnings)

    return StartupResult(success=True, config=config, warnings=warnings)


async def initialize_platform(config: PlatformConfig, metrics: MetricsState) -> None:
    """Initialize providers and update metrics state."""
    ensure_persisted_armed_if_missing_or_invalid()
    metrics.set_process_state(ProcessState.STARTING)
    metrics.set_paper_mode(config.is_paper_mode)

    load_platform_secrets()

    # LLM
    try:
        llm = create_llm_provider(config)
        metrics.set_provider_status("llm", llm.name)
        healthy = await llm.health_check()
        metrics.set_provider_status("llm", "ok" if healthy else "degraded")
        if hasattr(llm, "last_successful_call") and llm.last_successful_call:
            metrics.set_last_model_call(llm.last_successful_call)
    except Exception as exc:
        metrics.set_provider_status("llm", "error")
        logger.warning("LLM init skipped: %s", safe_exception_message(exc))

    # Exchanges
    adapters = create_exchange_adapters(config)
    for name, adapter in adapters.items():
        try:
            ok = await adapter.test_connection()
            metrics.set_exchange_status(name, "connected" if ok else "degraded")
            ticker = await adapter.get_ticker("BTC/USDT")
            metrics.set_last_market_data(ticker.timestamp)
        except Exception as exc:
            metrics.set_exchange_status(name, "error")
            logger.warning("Exchange %s init: %s", name, safe_exception_message(exc))
        finally:
            if hasattr(adapter, "close"):
                await adapter.close()

    metrics.set_process_state(ProcessState.READY)
    metrics.set_health("ok")
    metrics.set_readiness("ready")


def run_startup_validation() -> int:
    """CLI entry for startup validation. Returns exit code."""
    result = validate_startup(require_llm=False)
    for w in result.warnings:
        logger.warning(w)
    if not result.success:
        for e in result.errors:
            logger.error(e)
        return 1
    if result.config:
        logger.info(
            "Startup validation OK — env=%s paper_mode=%s",
            result.config.environment,
            result.config.is_paper_mode,
        )
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    sys.exit(run_startup_validation())


if __name__ == "__main__":
    main()
