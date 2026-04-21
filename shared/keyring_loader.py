# -*- coding: utf-8 -*-
"""Unified keyring credential loader for all RazAgent Enterprise services.

Loads API keys from Windows Credential Locker (keyring) into os.environ.
Single source of truth — replaces 4 duplicate implementations across:
  - backend/entrypoint.py
  - Video_Studio_Worker/video_agent_bot.py
  - Video_Studio_Worker/pipeline/auto_pipeline.py

Usage:
    from keyring_loader import load_keys
    load_keys()                                    # load all 7 keys
    load_keys(keys=["OPENAI_API_KEY"])             # load specific keys
    load_keys(required=["AGENT_VIDEO_TOKEN"])       # exit if missing
    load_keys(overwrite_env=False)                  # don't overwrite existing env vars
"""
import os
import logging

logger = logging.getLogger("godclaw.keyring_loader")

SERVICE_NAME = "AgentCeoR"  # TODO-TECHDEBT: legacy service name — consider migrating to "GodClaw" (requires keyring re-registration)

ALL_KEYS = [
    "TELEGRAM_TOKEN",
    "AGENT_VIDEO_TOKEN",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ADMIN_CHAT_ID",
    "VIRUSTOTAL_API_KEY",
    "Github",
    "FAL_API_KEY",
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "KUCOIN_API_KEY",
    "KUCOIN_API_SECRET",
    "KUCOIN_API_PASSPHRASE",
    "NEWS_API_KEY",
    "OLLAMA_CLOUD_URL",
    "OLLAMA_CLOUD_MODEL",
    "OLLAMA_CLOUD_API_KEY",
    "COINGECKO_API_KEY",
    "TRADE_CRYPTO_BOT_TOKEN",
    "TRADE_CRYPTO_CHAT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "POSTIZ_API_TOKEN",
    "POSTIZ_ADMIN_EMAIL",
    "POSTIZ_ADMIN_PASS",
    "YOUTUBE_OAUTH_TOKEN",
    "EXPO_PUSH_TOKEN",
    "FCM_SERVER_KEY",
    "TECH_LOGS_CHAT_ID",
    "SHOPIFY_ACCESS_TOKEN",
    "SHOPIFY_SHOP_DOMAIN",
    "WISE_API_TOKEN",
    "WISE_PROFILE_ID",
]

# Maps a canonical key name to alternate names that may exist in keyring.
# get_credential() tries each variant in order until one returns a value.
_KEY_ALIASES: dict[str, list[str]] = {
    "BINANCE_API_KEY":        ["BINANCEAPIKEY", "BINANCE_KEY"],
    "BINANCE_API_SECRET":     ["BINANCEAPISECRET", "BINANCE_SECRET"],
    "KUCOIN_API_KEY":         ["KUCOINAPIKEY", "KUCOIN_KEY"],
    "KUCOIN_API_SECRET":      ["KUCOINAPISECRET", "KUCOIN_SECRET"],
    "KUCOIN_API_PASSPHRASE":  ["KUCOINAPIPASSPHRASE", "KUCOIN_PASSPHRASE"],
    "OPENROUTER_API_KEY":     ["OPENROUTERAPIKEY"],
    "OPENAI_API_KEY":         ["OPENAIAPIKEY"],
    "GOOGLE_API_KEY":         ["GOOGLEAPIKEY"],
    "FAL_API_KEY":            ["FALAPIKEY"],
    "VIRUSTOTAL_API_KEY":     ["VIRUSTOTALAPIKEY"],
    "NEWS_API_KEY":           ["NEWSAPIKEY", "NEWS_KEY"],
    "TELEGRAM_TOKEN":         ["TELEGRAMTOKEN"],
    "AGENT_VIDEO_TOKEN":      ["AGENTVIDEOTOKEN"],
    "ADMIN_CHAT_ID":          ["ADMINCHATID"],
    "GITHUB_TOKEN":           ["Github"],
    "Github":                 ["GITHUB_TOKEN"],
    "OLLAMA_CLOUD_URL":       ["OLLAMACLOUDURL"],
    "OLLAMA_CLOUD_MODEL":     ["OLLAMACLOUDMODEL"],
    "OLLAMA_CLOUD_API_KEY":   ["OLLAMACLOUDAPIKEY"],
    "COINGECKO_API_KEY":      ["COINGECKOAPIKEY"],
    "SHOPIFY_ACCESS_TOKEN":   ["SHOPIFYACCESSTOKEN"],
    "SHOPIFY_SHOP_DOMAIN":    ["SHOPIFYSHOPDOMAIN"],
    "WISE_API_TOKEN":         ["WISEAPITOKEN", "WISE_TOKEN"],
    "WISE_PROFILE_ID":        ["WISEPROFILEID"],
}


def _get_variants(key: str) -> list[str]:
    """Return *[key]* followed by all known alternate names."""
    variants = [key]
    # Direct alias lookup
    if key in _KEY_ALIASES:
        variants.extend(_KEY_ALIASES[key])
    else:
        # Reverse lookup — maybe *key* is itself an alias
        for canonical, aliases in _KEY_ALIASES.items():
            if key in aliases:
                variants.append(canonical)
                variants.extend(a for a in aliases if a != key)
                break
    # Generic fallback: try with / without underscores
    if "_" in key:
        no_us = key.replace("_", "")
        if no_us not in variants:
            variants.append(no_us)
    else:
        with_us = key
        for sep in ("API_KEY", "API_SECRET", "API_PASSPHRASE", "_TOKEN", "_ID"):
            compact = sep.replace("_", "")
            if compact in key:
                with_us = key.replace(compact, sep)
                break
        if with_us != key and with_us not in variants:
            variants.append(with_us)
    return variants


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_credential(key: str) -> str | None:
    """Get a single credential from keyring, trying all known naming variants.

    This is the **recommended** way to fetch a credential.  It handles the
    mismatch between CEO-Agent (no underscores) and Claude-Code (with
    underscores) naming conventions transparently.
    """
    try:
        import keyring
    except ImportError:
        return os.environ.get(key)

    for variant in _get_variants(key):
        try:
            val = keyring.get_password(SERVICE_NAME, variant)
            if val:
                if variant != key:
                    logger.debug("Found %s as variant %s", key, variant)
                return val
        except Exception:
            pass
    return None


def load_keys(
    keys: list[str] | None = None,
    required: list[str] | None = None,
    overwrite_env: bool = True,
) -> dict[str, str | None]:
    """Load credentials from Windows Credential Locker into os.environ.

    Args:
        keys: List of key names to load. Defaults to ALL_KEYS.
        required: Keys that MUST exist — raises SystemExit if missing.
        overwrite_env: If False, skip keys already present in os.environ.

    Returns:
        Dict mapping key names to their values (or None if not found).
    """
    # V13.0: Docker mode — env vars are the source of truth, skip keyring
    if os.environ.get("RAZAGENT_DOCKER"):
        result = {}
        for key in (keys or ALL_KEYS):
            result[key] = os.environ.get(key)
        if required:
            missing = [k for k in required if not result.get(k)]
            if missing:
                logger.error("Docker: Required env vars missing: %s", missing)
        return result

    try:
        import keyring  # noqa: F811
    except ImportError:
        logger.warning("keyring package not installed — cannot load credentials")
        if required:
            raise SystemExit(
                f"FATAL: keyring not installed but required keys needed: {required}"
            )
        return {k: None for k in (keys or ALL_KEYS)}

    target_keys = keys or ALL_KEYS
    result: dict[str, str | None] = {}
    loaded = 0

    for key in target_keys:
        # Skip if already in env and overwrite not requested
        if not overwrite_env and os.environ.get(key):
            result[key] = os.environ[key]
            continue

        # Use get_credential which tries all naming variants
        val = get_credential(key)
        result[key] = val
        if val:
            os.environ[key] = val
            loaded += 1

    # Check required keys
    if required:
        missing = [k for k in required if not result.get(k)]
        if missing:
            raise SystemExit(
                f"FATAL: Required credentials missing from keyring "
                f"(service={SERVICE_NAME!r}): {missing}\n"
                f"Fix: keyring.set_password({SERVICE_NAME!r}, '<KEY>', '<VALUE>')"
            )

    logger.info("Keyring: loaded %d/%d keys from %r", loaded, len(target_keys), SERVICE_NAME)
    return result


def audit_credentials() -> dict[str, str]:
    """Return a status dict (key → 'SET' | 'MISSING') for all known keys."""
    return {
        key: ("SET" if get_credential(key) else "MISSING")
        for key in ALL_KEYS
    }
