# -*- coding: utf-8 -*-
"""LLM provider initialization tests."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from shared.platform.config import LLMConfig, PlatformConfig, SafetyConfig
from shared.providers.llm.factory import create_llm_provider
from shared.providers.llm.openrouter import OpenRouterProvider, _parse_recommendation_json
from shared.providers.llm.moonshot import MoonshotProvider


def _platform_config(provider: str = "openrouter", moonshot_enabled: bool = False) -> PlatformConfig:
    return PlatformConfig(
        llm=LLMConfig(provider=provider, moonshot_enabled=moonshot_enabled),
        safety=SafetyConfig(paper_mode=True, auto_live=False),
    )


def test_create_openrouter_provider(monkeypatch):
    monkeypatch.setattr(
        "shared.providers.llm.factory.require_secrets",
        lambda keys: {"OPENROUTER_API_KEY": "test-key"},
    )
    provider = create_llm_provider(_platform_config())
    assert isinstance(provider, OpenRouterProvider)
    assert provider.name == "openrouter"


def test_moonshot_dormant_raises(monkeypatch):
    monkeypatch.setattr(
        "shared.providers.llm.factory.require_secrets",
        lambda keys: {"MOONSHOT_API_KEY": "test-key"},
    )
    with pytest.raises(RuntimeError, match="dormant"):
        create_llm_provider(_platform_config(provider="moonshot", moonshot_enabled=False))


def test_moonshot_enabled_creates_provider(monkeypatch):
    monkeypatch.setattr(
        "shared.providers.llm.factory.require_secrets",
        lambda keys: {"MOONSHOT_API_KEY": "test-key"},
    )
    provider = create_llm_provider(_platform_config(provider="moonshot", moonshot_enabled=True))
    assert isinstance(provider, MoonshotProvider)


def test_parse_recommendation_json_valid():
    raw = json.dumps({"thesis": "bullish", "signals": [], "confidence": 0.5})
    data = _parse_recommendation_json(raw)
    assert data["thesis"] == "bullish"


def test_parse_recommendation_json_invalid():
    with pytest.raises(ValueError, match="valid JSON"):
        _parse_recommendation_json("not json")


@pytest.mark.asyncio
async def test_openrouter_malformed_response():
    provider = OpenRouterProvider(api_key="test", max_retries=0)

    async def mock_post(*args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"choices": []})
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        instance = mock_client.return_value.__aenter__.return_value
        instance.post = AsyncMock(side_effect=mock_post)
        with pytest.raises(ValueError, match="Malformed"):
            await provider.complete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_openrouter_timeout_retry():
    provider = OpenRouterProvider(api_key="test", max_retries=1, timeout_seconds=1)

    with patch("httpx.AsyncClient") as mock_client:
        instance = mock_client.return_value.__aenter__.return_value
        instance.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        with pytest.raises(TimeoutError):
            await provider.complete([{"role": "user", "content": "hi"}])
