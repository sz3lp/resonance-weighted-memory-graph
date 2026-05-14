"""Utilities for injecting social feedback into an agent's memory log.

This module closes the feedback loop by taking engagement data collected for a
post and persisting it as a memory entry.  The stored memory can later be used
by other components (e.g. context injectors or ranking utilities) to influence
future behaviour of the agent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from rwmg.utils.tagger import extract_memory_tags
from rwmg.utils.timestamp_utils import get_current_iso_time


# feedback/feedback_injector.py

def inject_feedback_as_memory(agent_id: str, post_id: str, feedback_data: Dict) -> None:
    """Append a feedback summary as a memory entry for ``agent_id``.

    Parameters
    ----------
    agent_id:
        Identifier of the agent whose memory log should be updated.
    post_id:
        Event identifier of the original post.
    feedback_data:
        Dictionary containing normalised post feedback.  Expected keys include
        ``resonance_score``, ``engagement`` (with ``upvotes``, ``comments``,
        ``top_comment`` and ``sentiment``), ``post_text``, ``timestamp`` and
        ``community``.
    """

    resonance = float(feedback_data.get("resonance_score", 0.0))
    engagement = feedback_data.get("engagement", {}) or {}
    upvotes = int(engagement.get("upvotes", 0))
    community = str(feedback_data.get("community", ""))
    top_comment = str(engagement.get("top_comment", ""))

    summary = f"Post received {upvotes} upvotes"
    if community:
        summary += f" in {community}"
    summary += "."
    if top_comment:
        summary += f" Top comment: '{top_comment}'."

    content = str(feedback_data.get("post_text", ""))
    timestamp = str(feedback_data.get("timestamp") or get_current_iso_time())
    source = str(feedback_data.get("source", "reddit_post"))

    agent_dir = Path("agents") / agent_id
    profile_path = agent_dir / "profile.json"
    try:
        with profile_path.open("r", encoding="utf-8") as fh:
            profile = json.load(fh)
        archetype = profile.get("archetype_core", "")
    except (OSError, json.JSONDecodeError):
        archetype = ""

    tags = extract_memory_tags(content, archetype)
    sentiment_tag = str(engagement.get("sentiment", "")).lower()
    if sentiment_tag:
        tags.append(sentiment_tag)
    tags = sorted(set(tags))

    entry: Dict = {
        "type": "feedback",
        "source": source,
        "resonance_score": resonance,
        "summary": summary,
        "content": content,
        "tags": tags,
        "timestamp": timestamp,
        "event_id": post_id,
        "priority": resonance,
    }

    log_path = agent_dir / "memory_log.json"
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
        pass

    tags_path = agent_dir / "memory_tags.json"
    try:
        with tags_path.open("r", encoding="utf-8") as fh:
            tag_map = json.load(fh)
        if not isinstance(tag_map, dict):
            tag_map = {}
    except (json.JSONDecodeError, OSError):
        tag_map = {}

    tag_map[post_id] = tags

    try:
        with tags_path.open("w", encoding="utf-8") as fh:
            json.dump(tag_map, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass
