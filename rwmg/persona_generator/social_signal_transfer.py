"""Helpers for recording simple social connections between agents."""

from __future__ import annotations

import json
from pathlib import Path


def update_social_signal(
    agent_uuid: str, other_id: str, relationship_type: str, affinity_score: float
) -> None:
    """Add or update a social connection for ``agent_uuid``.

    Parameters
    ----------
    agent_uuid:
        The agent whose connections should be updated.
    other_id:
        Identifier of the other agent in the relationship.
    relationship_type:
        Either ``"friend"`` or ``"mentor"``.  Any other value defaults to
        ``"friend"``.
    affinity_score:
        Floating point score representing the strength of the relationship.
    """

    path = Path("agents") / agent_uuid / "connections.json"
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {"friends": [], "mentors": []}

    rel_key = "mentors" if relationship_type.lower().startswith("mentor") else "friends"
    entries = data.get(rel_key, [])
    for entry in entries:
        if entry.get("agent_id") == other_id:
            entry["affinity_score"] = affinity_score
            break
    else:
        new_entry = {"agent_id": other_id, "affinity_score": affinity_score}
        if rel_key == "friends":
            new_entry["relationship_type"] = relationship_type
        entries.append(new_entry)
    data[rel_key] = entries

    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


__all__ = ["update_social_signal"]

