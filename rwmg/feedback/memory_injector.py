"""Helpers for converting comment interactions into memory entries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from .resonance_collector import collect_comment_feedback


# feedback/memory_injector.py
def inject_interaction_memory(agent_id: str, comment_data: Dict) -> None:
    """Append ``comment_data`` as a structured memory for ``agent_id``.

    The function stores the memory in ``agents/<agent_id>/memory_log.json`` using
    the standard entry schema.  Failures to read or write the log are silently
    ignored so that simulations can proceed uninterrupted.
    """

    agent_dir = Path("agents") / agent_id
    log_path = agent_dir / "memory_log.json"
    agent_dir.mkdir(parents=True, exist_ok=True)

    try:
        with log_path.open("r", encoding="utf-8") as fh:
            log_entries: List[Dict] = json.load(fh)
            if not isinstance(log_entries, list):
                log_entries = []
    except (OSError, json.JSONDecodeError):
        log_entries = []

    entry = {
        "event_id": comment_data.get("comment_id"),
        "type": "comment_feedback",
        "origin": comment_data.get("origin", ""),
        "timestamp": comment_data.get("timestamp"),
        "content": comment_data.get("body", ""),
        "sentiment": comment_data.get("sentiment"),
        "tags": comment_data.get("tags", []),
        "resonance_score": float(comment_data.get("resonance_score", 0.0)),
    }

    log_entries.append(entry)

    try:
        with log_path.open("w", encoding="utf-8") as fh:
            json.dump(log_entries, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def process_and_log_interactions(agent_id: str, post_id: str, platform: str) -> None:
    """Collect and persist comment interactions for ``post_id``.

    The function orchestrates the comment feedback pipeline by first fetching and
    processing all comments for ``post_id`` and then storing each as a memory via
    :func:`inject_interaction_memory`.
    """

    comments = collect_comment_feedback(agent_id, post_id, platform)
    for comment in comments:
        comment["origin"] = f"{platform}_reply"
        inject_interaction_memory(agent_id, comment)


__all__ = ["inject_interaction_memory", "process_and_log_interactions"]
