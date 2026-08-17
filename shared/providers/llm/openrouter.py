# -*- coding: utf-8 -*-
"""OpenRouter LLM provider — moonshotai/kimi-k2.6 via aggregator."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Sequence

import httpx

from shared.platform.interfaces import LLMProvider, LLMRecommendation
from shared.platform.secrets import safe_exception_message

logger = logging.getLogger("platform.llm.openrouter")

_RECOMMENDATION_SCHEMA = {
    "thesis": "",
    "signals": [],
    "evidence": [],
    "confidence": 0.0,
    "invalidation_conditions": [],
    "timeframe": "",
    "risks": [],
}


class OpenRouterProvider(LLMProvider):
    """Structured recommendations via OpenRouter — never executes trades."""

    def __init__(
        self,
        api_key: str,
        model: str = "moonshotai/kimi-k2.6",
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._last_success: datetime | None = None

    @property
    def name(self) -> str:
        return "openrouter"

    @property
    def last_successful_call(self) -> datetime | None:
        return self._last_success

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/RazAgent-Trader",
            "X-Title": "RazAgent_Trader",
        }

    async def _request_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        f"{self._base_url}/chat/completions",
                        headers=self._headers(),
                        json=payload,
                    )
                    if resp.status_code == 429 and attempt < self._max_retries:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    self._last_success = datetime.now(timezone.utc)
                    return data
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise TimeoutError(
                    f"OpenRouter request timed out after {self._max_retries + 1} attempts"
                ) from exc
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code >= 500 and attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise RuntimeError(
                    safe_exception_message(exc)
                ) from exc
            except Exception as exc:
                last_error = exc
                raise RuntimeError(safe_exception_message(exc)) from exc
        raise RuntimeError(safe_exception_message(last_error or RuntimeError("unknown")))

    async def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        payload = {
            "model": self._model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = await self._request_with_retry(payload)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Malformed OpenRouter response: missing choices[0].message.content") from exc

    async def recommend(
        self,
        context: dict[str, Any],
        *,
        temperature: float = 0.2,
    ) -> LLMRecommendation:
        system = (
            "You are a crypto research advisor. Return ONLY valid JSON with keys: "
            "thesis, signals (list of objects with direction/strength/confidence/time_horizon/"
            "entry_rationale/invalidation/required_market_conditions), evidence, confidence (0-1), "
            "invalidation_conditions, timeframe, risks. "
            "You MUST NOT output order instructions or execution commands."
        )
        user_content = json.dumps(context, default=str)
        raw = await self.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            temperature=temperature,
            max_tokens=4096,
        )
        parsed = _parse_recommendation_json(raw)
        return LLMRecommendation(
            thesis=str(parsed.get("thesis", "")),
            signals=list(parsed.get("signals", [])),
            evidence=[str(e) for e in parsed.get("evidence", [])],
            confidence=float(parsed.get("confidence", 0.0)),
            invalidation_conditions=[str(i) for i in parsed.get("invalidation_conditions", [])],
            timeframe=str(parsed.get("timeframe", "")),
            risks=[str(r) for r in parsed.get("risks", [])],
            model=self._model,
            provider=self.name,
            timestamp=datetime.now(timezone.utc),
        )

    async def health_check(self) -> bool:
        try:
            await self.complete(
                [{"role": "user", "content": "Reply with OK"}],
                max_tokens=8,
            )
            return True
        except Exception as exc:
            logger.warning("OpenRouter health check failed: %s", safe_exception_message(exc))
            return False


def _parse_recommendation_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM did not return valid JSON recommendation: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("LLM recommendation must be a JSON object")
    return data
