"""Persisted, fail-closed kill-switch state.

Missing, malformed, or ambiguous state always means ARMED.  The environment
override can only arm the switch; it cannot disarm persisted state.
"""

from __future__ import annotations

import json
import logging
import os
from enum import Enum
from pathlib import Path

from shared.setup_paths import PROJECT_ROOT

DEFAULT_STATE_PATH = PROJECT_ROOT / "data" / "kill_switch.json"
ENV_OVERRIDE = "RAZAGENT_KILL_SWITCH"


class KillSwitchState(str, Enum):
    ARMED = "ARMED"
    DISARMED = "DISARMED"


def _state_path(path: Path | None = None) -> Path:
    return DEFAULT_STATE_PATH if path is None else path


def read_kill_switch(path: Path | None = None) -> KillSwitchState:
    """Read state, returning ARMED for every missing or invalid input."""
    path = _state_path(path)
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


def is_armed(path: Path | None = None) -> bool:
    """Return whether financial actions must be blocked."""
    return read_kill_switch(_state_path(path)) is KillSwitchState.ARMED


def persist_armed(path: Path | None = None) -> None:
    """Persist the safe state atomically; F0 intentionally exposes no disarm API."""
    path = _state_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text('{"state":"ARMED"}\n', encoding="utf-8")
    temporary.replace(path)


def ensure_persisted_armed_if_missing_or_invalid(path: Path | None = None) -> None:
    """Write ARMED when the state file is missing or unreadable/unknown.

    A well-formed DISARMED file is left untouched. There is still no disarm API.
    """
    path = _state_path(path)
    needs_persist = False
    reason = "missing"
    if not path.exists():
        needs_persist = True
    else:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            state = payload.get("state") if isinstance(payload, dict) else None
            if state not in {KillSwitchState.ARMED.value, KillSwitchState.DISARMED.value}:
                needs_persist = True
                reason = "invalid"
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            needs_persist = True
            reason = "invalid"
    if needs_persist:
        persist_armed(path)
        logging.getLogger("shared.execution.kill_switch").warning(
            "Kill-switch file %s; persisted ARMED at %s", reason, path
        )
