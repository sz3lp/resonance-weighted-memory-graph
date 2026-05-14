"""Detect long-term psychological shifts in agents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional


def check_for_evolution(agent_uuid: str) -> Optional[Dict]:
    """Inspect an agent's memory log for signs of evolution.

    The heuristic here is deliberately small: if the average resonance score of
    stored memories exceeds ``0.8`` we signal a potential evolution event and
    return a dictionary describing the trigger.  Otherwise ``None`` is returned.
    """

    log_path = Path("agents") / agent_uuid / "memory_log.json"
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None

    if not entries:
        return None

    avg = sum(float(e.get("resonance_score", 0.0)) for e in entries) / len(entries)
    if avg > 0.8:
        return {"agent_id": agent_uuid, "trigger": "high_resonance", "average": avg}
    return None


__all__ = ["check_for_evolution"]

