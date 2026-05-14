"""Logging helpers for prompt memory injection.

This module keeps track of which memories influenced a generated post so that
later analysis modules can reference them.  The information is stored within the
agent's ``memory_log.json`` entries under the ``"injected_memories"`` field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List


def log_injected_memories(agent_uuid: str, event_id: str, injected_memory_ids: List[str]) -> None:
    """Persist the list of memory identifiers used for a prompt.

    Parameters
    ----------
    agent_uuid:
        Identifier of the agent that generated the post.
    event_id:
        The unique identifier of the post event returned by
        :func:`feedback.post_action_logger.log_agent_output`.
    injected_memory_ids:
        List of memory ``event_id`` values that were inserted into the prompt
        context.
    """

    log_path = Path("agents") / agent_uuid / "memory_log.json"
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
    except (OSError, json.JSONDecodeError):
        entries = []

    # Find the log entry for the event and update its injected memories.  If no
    # entry exists we create a minimal placeholder so downstream modules can
    # still reference the association.
    found = False
    for entry in entries:
        if entry.get("event_id") == event_id:
            entry["injected_memories"] = list(injected_memory_ids)
            found = True
            break

    if not found:
        entries.append(
            {
                "event_id": event_id,
                "timestamp": "",
                "content": "",
                "platform": "",
                "resonance_score": 0.0,
                "injected_memories": list(injected_memory_ids),
                "feedback_data_ref": "",
            }
        )

    try:
        with log_path.open("w", encoding="utf-8") as fh:
            json.dump(entries, fh, ensure_ascii=False, indent=2)
    except OSError:
        # Logging failures should not be fatal for the simulation loop.
        pass


__all__ = ["log_injected_memories"]

