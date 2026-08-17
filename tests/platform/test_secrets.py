# -*- coding: utf-8 -*-
"""Secret loading and sanitization tests."""

import pytest

from shared.platform.secrets import sanitize_message, credential_status


def test_sanitize_message_redacts_api_keys():
    msg = "Failed with sk-or-abcdefghijklmnopqrstuvwxyz1234567890"
    sanitized = sanitize_message(msg)
    assert "sk-or-" not in sanitized or "***REDACTED***" in sanitized
    assert "abcdefghijklmnopqrstuvwxyz1234567890" not in sanitized


def test_sanitize_message_redacts_telegram_token():
    msg = "Invalid token 1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ1234567890ab"
    sanitized = sanitize_message(msg)
    assert "ABCdefGHI" not in sanitized


def test_credential_status_never_exposes_values(monkeypatch):
    monkeypatch.setattr(
        "shared.platform.secrets.get_credential",
        lambda key: "secret-value" if key == "OPENROUTER_API_KEY" else None,
    )
    status = credential_status()
    assert status["OPENROUTER_API_KEY"] == "SET"
    for v in status.values():
        assert v in {"SET", "MISSING"}
        assert "secret" not in v.lower()


def test_require_secrets_raises_on_missing(monkeypatch):
    from shared.platform.secrets import require_secrets

    monkeypatch.setattr(
        "shared.platform.secrets.load_platform_secrets",
        lambda **kw: {"OPENROUTER_API_KEY": None},
    )
    with pytest.raises(RuntimeError, match="missing"):
        require_secrets(["OPENROUTER_API_KEY"])
