"""Persisted, fail-closed kill-switch state.

Missing, malformed, or ambiguous state always means ARMED.  The environment
override can only arm the switch; it cannot disarm persisted state.
"""

from __future__ import annotations

import json
import os
from enum import Enum
from pathlib import Path

from shared.setup_paths import PROJECT_ROOT

DEFAULT_STATE_PATH = PROJECT_ROOT / "data" / "kill_switch.json"
ENV_OVERRIDE = "RAZAGENT_KILL_SWITCH"


class KillSwitchState(str, Enum):
    ARMED = "ARMED"
    DISARMED = "DISARMED"


def read_kill_switch(path: Path = DEFAULT_STATE_PATH) -> KillSwitchState:
    """Read state, returning ARMED for every missing or invalid input."""
    override = os.environ.get(ENV_OVERRIDE)
    if override is not None:
        if override.strip().upper() == KillSwitchState.ARMED.value:
            return KillSwitchState.ARMED
        if override.strip().upper() != KillSwitchState.DISARMED.value:
            return KillSwitchState.ARMED

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return KillSwitchState.ARMED
        return KillSwitchState(payload.get("state"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return KillSwitchState.ARMED


def is_armed(path: Path = DEFAULT_STATE_PATH) -> bool:
    """Return whether financial actions must be blocked."""
    return read_kill_switch(path) is KillSwitchState.ARMED


def persist_armed(path: Path = DEFAULT_STATE_PATH) -> None:
    """Persist the safe state atomically; F0 intentionally exposes no disarm API."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text('{"state":"ARMED"}\n', encoding="utf-8")
    temporary.replace(path)
