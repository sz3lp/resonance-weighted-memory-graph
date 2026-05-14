"""Tools for explaining how a post came to be.

This module exposes :func:`trace_post_to_memories` which inspects the agents'
data directories to discover which memories were injected into the prompt that
generated a particular post.  The function is intentionally lightweight and
file‑system based; no external databases are involved in this project
blueprint.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List


# explainability/post_traceback.py
def trace_post_to_memories(event_id: str) -> Dict:
    """Trace a post back to the memories that influenced its generation.

    Parameters
    ----------
    event_id:
        Identifier of the post to trace.

    Returns
    -------
    dict
        A dictionary containing the ``agent_id`` of the posting agent, the
        ``post_event`` entry from its ``memory_log.json`` and a list of
        ``influencing_memories`` with basic details for each referenced memory.
        An empty dictionary is returned if the event cannot be located.
    """

    agents_root = Path(__file__).resolve().parents[1] / "agents"

    for agent_dir in agents_root.iterdir():
        if not agent_dir.is_dir():
            continue

        log_path = agent_dir / "memory_log.json"
        if not log_path.exists():
            continue

        try:
            with log_path.open("r", encoding="utf-8") as fh:
                log_entries: List[Dict] = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        for entry in log_entries:
            if entry.get("event_id") != event_id:
                continue

            injected_ids = entry.get("injected_memories", []) or []

            # Build a lookup table for memory entries from both the log and the
            # agent's canonical events.
            memory_lookup: Dict[str, Dict] = {
                m.get("event_id"): m for m in log_entries if m.get("event_id")
            }

            canon_path = agent_dir / "canonical_events.json"
            if canon_path.exists():
                try:
                    with canon_path.open("r", encoding="utf-8") as fh:
                        canon_entries: List[Dict] = json.load(fh)
                    memory_lookup.update(
                        {c.get("event_id"): c for c in canon_entries if c.get("event_id")}
                    )
                except (json.JSONDecodeError, OSError):
                    pass

            influencing = []
            for mem_id in injected_ids:
                mem = memory_lookup.get(mem_id)
                if not mem:
                    continue
                influencing.append(
                    {
                        "event_id": mem.get("event_id"),
                        "content": mem.get("content") or mem.get("description"),
                        "resonance_score": mem.get("resonance_score"),
                    }
                )

            return {
                "agent_id": agent_dir.name,
                "post_event": entry,
                "influencing_memories": influencing,
            }

    return {}

