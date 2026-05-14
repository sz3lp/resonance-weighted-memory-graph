"""Utilities for logging the immediate output of an agent's action.

The wider RWMG system records every post an agent makes so that subsequent
modules – such as feedback processors or explainability tools – can reference
the original content.  This module provides a small helper function to perform
that initial logging step.  The implementation is intentionally
file‑system-based and avoids any external dependencies so that it works in the
contained execution environment used for the unit tests.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Dict

from rwmg.utils.event_bus import emit_event
from rwmg.utils.timestamp_utils import get_current_iso_time


# feedback/post_action_logger.py
def log_agent_output(agent_uuid: str, content: str, platform: str) -> str:
    """Log the raw output of an agent's action and return a unique event ID.

    Parameters
    ----------
    agent_uuid:
        Identifier of the agent that produced the content.
    content:
        The text that was generated or posted by the agent.
    platform:
        Name of the platform the content was intended for.

    Returns
    -------
    str
        The generated ``event_id`` which uniquely identifies this post.
    """

    event_id = uuid.uuid4().hex

    entry: Dict = {
        "event_id": event_id,
        "timestamp": get_current_iso_time(),
        "content": content,
        "platform": platform,
        # Resonance and feedback will be filled in by later stages of the
        # feedback pipeline.  Initial defaults allow downstream code to rely on
        # these keys being present.
        "resonance_score": 0.0,
        "injected_memories": [],
        "feedback_data_ref": "",
    }

    agent_dir = Path("agents") / agent_uuid
    log_path = agent_dir / "memory_log.json"
    agent_dir.mkdir(parents=True, exist_ok=True)

    try:
        if log_path.exists():
            with log_path.open("r", encoding="utf-8") as fh:
                log_entries = json.load(fh)
            if not isinstance(log_entries, list):
                log_entries = []
        else:
            log_entries = []
    except (json.JSONDecodeError, OSError):
        log_entries = []

    log_entries.append(entry)

    try:
        with log_path.open("w", encoding="utf-8") as fh:
            json.dump(log_entries, fh, ensure_ascii=False, indent=2)
    except OSError:
        # Logging should be best-effort; failure to write the log is swallowed so
        # that the simulation can continue.  The event ID is still returned to
        # the caller even though the entry could not be persisted.
        pass

    # Notify interested parties that a post has been logged.  Errors from
    # subscriber handlers are intentionally ignored so the logging path remains
    # robust.
    try:  # pragma: no cover - event bus errors are non-critical
        emit_event("post_logged", {"agent_uuid": agent_uuid, "event_id": event_id})
    except Exception:  # pragma: no cover
        pass

    return event_id


