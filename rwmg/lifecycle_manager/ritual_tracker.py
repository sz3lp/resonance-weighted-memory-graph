"""Simple helpers to track an agent's ritual status."""

from __future__ import annotations

import json
from pathlib import Path


def _load_state(agent_uuid: str) -> dict:
    path = Path("agents") / agent_uuid / "agent_state.json"
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(agent_uuid: str, state: dict) -> None:
    path = Path("agents") / agent_uuid / "agent_state.json"
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def start_ritual(agent_uuid: str, stage: str) -> None:
    state = _load_state(agent_uuid)
    state["is_in_ritual"] = True
    state["ritual_stage"] = stage
    _save_state(agent_uuid, state)


def complete_ritual(agent_uuid: str) -> None:
    state = _load_state(agent_uuid)
    state["is_in_ritual"] = False
    state["ritual_stage"] = ""
    _save_state(agent_uuid, state)


__all__ = ["start_ritual", "complete_ritual"]

