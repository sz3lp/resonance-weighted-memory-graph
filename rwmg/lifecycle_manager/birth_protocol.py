"""Activate a newly generated agent by writing its persona to disk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from rwmg.sim_runner.sim_start import populate_manifest


def activate_new_agent(persona_data: Dict) -> str:
    """Persist the supplied persona data and return the agent UUID.

    The function expects ``persona_data`` to contain at least a ``profile``
    mapping following the ``profile.json`` schema and may include additional
    files such as ``agent_state`` and ``canonical_events``.  Missing files are
    created with sensible defaults so that downstream components can operate.
    """

    profile = persona_data.get("profile", {})
    agent_id = profile.get("agent_id")
    if not agent_id:
        raise ValueError("persona_data must include a profile with 'agent_id'")

    root = Path("agents")
    agent_dir = root / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "profile.json": profile,
        "agent_state.json": persona_data.get("agent_state", {}),
        "canonical_events.json": persona_data.get("canonical_events", []),
        "memory_log.json": [],
        "memory_cache_top5.json": [],
        "suppression_log.json": [],
        "memory_tags.json": {},
        "connections.json": {"friends": [], "mentors": []},
    }

    for filename, payload in files.items():
        try:
            with (agent_dir / filename).open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # Ensure subordinate directories exist
    (agent_dir / "temp").mkdir(exist_ok=True)
    (agent_dir / "memory_index.csv").touch(exist_ok=True)

    # Add entry to global manifest
    populate_manifest([agent_id])

    return agent_id


__all__ = ["activate_new_agent"]

