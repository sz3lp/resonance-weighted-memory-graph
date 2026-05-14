"""Assign behaviour templates to existing agents."""

from __future__ import annotations

import json
from pathlib import Path

from rwmg.sim_runner.sim_start import assign_behavior_profile


def assign_profiles_to_existing_agents() -> None:
    """Populate ``agent_state.json`` with a behaviour profile if missing."""

    base = Path("agents")
    if not base.exists():
        return

    for agent_dir in base.iterdir():
        if not agent_dir.is_dir():
            continue
        state_path = agent_dir / "agent_state.json"
        try:
            with state_path.open("r", encoding="utf-8") as fh:
                state = json.load(fh) or {}
        except (OSError, json.JSONDecodeError):
            state = {}
        if state.get("behavior_profile"):
            continue
        state["behavior_profile"] = assign_behavior_profile(agent_dir.name)
        try:
            with state_path.open("w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass


if __name__ == "__main__":
    assign_profiles_to_existing_agents()

