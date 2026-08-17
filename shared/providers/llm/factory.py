# -*- coding: utf-8 -*-
"""LLM provider factory."""

from __future__ import annotations

from shared.platform.config import PlatformConfig
from shared.platform.interfaces import LLMProvider
from shared.platform.secrets import require_secrets, safe_exception_message
from shared.providers.llm.moonshot import MoonshotProvider
from shared.providers.llm.openrouter import OpenRouterProvider


def create_llm_provider(config: PlatformConfig) -> LLMProvider:
    """Instantiate the configured LLM provider."""
    if config.llm.provider == "openrouter":
        secrets = require_secrets(["OPENROUTER_API_KEY"])
        return OpenRouterProvider(
            api_key=secrets["OPENROUTER_API_KEY"],
            model=config.llm.model,
            base_url=config.llm.base_url,
            timeout_seconds=config.llm.timeout_seconds,
            max_retries=config.llm.max_retries,
        )

    if config.llm.provider == "moonshot":
        if not config.llm.moonshot_enabled:
            raise RuntimeError(
                "Direct Moonshot provider is dormant. Set llm.moonshot_enabled=true "
                "and llm.provider=moonshot explicitly to activate."
            )
        secrets = require_secrets(["MOONSHOT_API_KEY"])
        return MoonshotProvider(
            api_key=secrets["MOONSHOT_API_KEY"],
            model=config.llm.moonshot_model,
            base_url=config.llm.moonshot_base_url,
            timeout_seconds=config.llm.timeout_seconds,
            max_retries=config.llm.max_retries,
        )

    raise ValueError(f"Unknown LLM provider: {config.llm.provider}")
